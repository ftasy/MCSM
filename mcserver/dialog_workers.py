# -*- coding: utf-8 -*-
"""从 dialogs.py 提取的后台线程与共享工具。

包含：
  - _StopMixin / Worker / IconWorker / LoaderVerWorker：QThread 子类
  - _track / _stop_threads / _stop_all_threads：线程生命周期管理
  - _fetch_icon_img / _make_letter_pixmap / _make_placeholder_pixmap：图标工具
  - _global_show_icons：配置读取
  - _LIVE_THREADS：全局线程注册表
"""

import os
import json

from PySide6.QtCore import (Qt, QThread, Signal)
from PySide6.QtGui import (QImage, QPixmap, QPainter, QColor,
                           QFont, QBrush, QPen)

from .core import net
from .core.palette import PAL, PLACEHOLDER_PAIRS

_LIVE_THREADS = set()


class _StopMixin:
    """给 QThread 加一个安全的 stop()。

    QThread 不能"优雅中断"一个正在阻塞（下载等）的 run()，    所以顺序是：请求中断 -> quit() -> 等一小会儿 ->
    还不停就 terminate()（总比整个程序崩掉强）。
    """

    def stop(self, soft_ms=600, hard_ms=1500):
        try:
            if not self.isRunning():
                _LIVE_THREADS.discard(self)
                return True
            self.requestInterruption()
            self.quit()
            if self.wait(soft_ms):
                _LIVE_THREADS.discard(self)
                return True
            self.terminate()
            ok = self.wait(hard_ms)
            _LIVE_THREADS.discard(self)
            return ok
        except Exception:
            _LIVE_THREADS.discard(self)
            return False


class Worker(QThread, _StopMixin):
    """后台执行任务，避免界面卡死。"""
    log = Signal(str)
    done = Signal(bool, object)

    def __init__(self, fn, kwargs=None, parent=None):
        # ★ parent 不传给 QThread：传了之后对话框一销毁，
        #   Qt 会连线程对象一起销毁，而线程可能还在跑 —— 直接崩。
        #   这里只存 Python 引用，存活由 _LIVE_THREADS 兜底。
        super().__init__(None)
        self.fn = fn
        self.kwargs = kwargs or {}
        self._owner = parent
        _LIVE_THREADS.add(self)

    def run(self):
        try:
            res = self.fn(on_log=self.log.emit, **self.kwargs)
            if not self.isInterruptionRequested():
                self.done.emit(True, res)
        except Exception as e:
            try:
                self.log.emit("[错误] %r" % e)
                self.done.emit(False, repr(e))
            except Exception:
                pass
        finally:
            _LIVE_THREADS.discard(self)


def _install_exit_hook():
    """注册 atexit：解释器退出前停掉所有后台线程。

    closeEvent 只在"正常关闭主窗口"时触发。如果程序是从别处
    退出的（异常、控制台 Ctrl-C、直接杀进程），线程可能还在跑，
    Qt 就会抛 "QThread: Destroyed while thread is still running"。
    atexit 是最后一道兜底。
    """
    import atexit
    atexit.register(_stop_all_threads)


def _stop_all_threads(soft_ms=600, hard_ms=1500):
    """停掉程序里所有还在跑的后台线程（退出前兜底）。"""
    n = 0
    for w in list(_LIVE_THREADS):
        try:
            w.stop(soft_ms, hard_ms)
            n += 1
        except Exception:
            pass
    _LIVE_THREADS.clear()
    return n


def _stop_threads(obj, soft_ms=600, hard_ms=1500):
    """停掉一个对象身上挂着的所有后台线程。

    对话框关闭时必须调：不然线程还在跑、对话框已经销毁，
    Qt 就会 abort —— 也就是用户看到的"闪退"。
    """
    n = 0
    for w in list(getattr(obj, "_threads", []) or []):
        try:
            w.stop(soft_ms, hard_ms)
            n += 1
        except Exception:
            pass
    # 兜底：扫一遍实例字典，凡是 QThread 都停
    try:
        from PySide6.QtCore import QThread as _QT
        for v in list(vars(obj).values()):
            if isinstance(v, _QT):
                try:
                    v.stop(soft_ms, hard_ms)
                    n += 1
                except Exception:
                    pass
    except Exception:
        pass
    return n


def _track(obj, w):
    """登记线程，方便对话框关闭时统一停掉。"""
    lst = getattr(obj, "_threads", None)
    if lst is None:
        lst = []
        obj._threads = lst
    lst.append(w)
    return w


# ================= ★ V1.8.10：图标异步加载 =================

def _fetch_icon_img(url, timeout=6):
    """后台线程里用 urllib 下载图标，返回 QImage（可跨线程）或 None。"""
    try:
        data = net.request(url, timeout=timeout)
        if not data:
            return None
        img = QImage()
        if not img.loadFromData(data):
            return None
        if img.width() < 8 or img.height() < 8:
            return None
        return img
    except Exception:
        return None


class IconWorker(QThread, _StopMixin):
    """后台批量下载当前页的图标（一个线程遍历一批 URL）。

    只发 QImage（线程安全），转 QPixmap 在 GUI 线程的槽里做。
    ★ V2.3.7：失败也发信号（img=None）—— 以前只在成功时发，
      下载失败的 URL 会永远留在 _icon_loading 里，既不重试、
      也会把"这一页的图标"永远卡在占位图上。
    """

    got = Signal(str, object)   # icon_url, QImage（None = 下载失败）

    def __init__(self, urls, parent=None):
        super().__init__(None)   # ★ 不传 parent，防对话框销毁连坐
        self.urls = urls or []
        _LIVE_THREADS.add(self)

    def run(self):
        for url in self.urls:
            if self.isInterruptionRequested():
                break
            img = _fetch_icon_img(url)
            try:
                self.got.emit(url, img)
            except Exception:
                pass
        _LIVE_THREADS.discard(self)


def _make_letter_pixmap(slug, size=64):
    """首字母色块图标：按 slug 稳定取色，圆角方块 + 白色首字母。

    ★ V1.8.10：推荐安装列表没有网络图标，用本地 icons/<slug>.png
    回退到色块（绝不再画问号）。
    """
    name = (slug or "?")
    letter = name[0].upper() if name else "?"
    idx = sum(ord(c) for c in name) % len(PLACEHOLDER_PAIRS)
    bg, _fg = PLACEHOLDER_PAIRS[idx]
    pm = QPixmap(size, size)
    pm.fill(Qt.transparent)
    from PySide6.QtGui import QPainter, QColor, QFont, QBrush, QPen
    p = QPainter(pm)
    p.setRenderHint(QPainter.Antialiasing)
    p.setBrush(QBrush(QColor(bg)))
    p.setPen(Qt.NoPen)
    p.drawRoundedRect(4, 4, size - 8, size - 8, 12, 12)
    p.setPen(QColor(_fg))
    f = QFont()
    f.setBold(True)
    f.setPixelSize(int(size * 0.5))
    p.setFont(f)
    p.drawText(pm.rect(), Qt.AlignCenter, letter)
    p.end()
    return pm


def _make_placeholder_pixmap(size=64):
    """灰底圆角占位图（图标下载前的默认图，绝不再画问号）。"""
    pm = QPixmap(size, size)
    pm.fill(Qt.transparent)
    from PySide6.QtGui import QPainter, QColor, QBrush
    p = QPainter(pm)
    p.setRenderHint(QPainter.Antialiasing)
    p.setBrush(QBrush(QColor(PAL.ART_BG)))
    p.setPen(Qt.NoPen)
    p.drawRoundedRect(4, 4, size - 8, size - 8, 12, 12)
    p.end()
    return pm


def _global_show_icons():
    """读 servers.json 的 display.show_icons（★ V1.9.0：默认 False=纯文字极速）。"""
    try:
        import json as _json
        cand = [os.path.join(os.getcwd(), "servers.json")]
        try:
            cand.append(os.path.join(
                os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
                "servers.json"))
        except Exception:
            pass
        for p in cand:
            if not os.path.isfile(p):
                continue
            with open(p, "r", encoding="utf-8") as f:
                d = _json.load(f)
            v = ((d.get("display") or {}).get("show_icons"))
            return False if v is None else bool(v)
    except Exception:
        pass
    return False


class LoaderVerWorker(QThread, _StopMixin):
    """后台查加载器可用版本，避免界面卡住。"""
    got = Signal(object, object)

    # ★ 下拉里最多列多少个版本。
    #   以前 fabric/forge/neoforge 是把官方返回的全部塞进去 ——
    #   Fabric loader 累积了几百个，Forge 也有几十个，
    #   下拉拉不到底、也挑不出哪个是新的。
    #   这里统一截到最新 20 个（正式版和测试版都保留，
    #   测试版会在标签上标出来），跟 Paper 的口径一致。
    MAX_VER = 20

    def __init__(self, mc_version, loader, parent=None):
        super().__init__(None)   # ★ 同上：不挂 parent，避免被 Qt 销毁
        self.mc = mc_version
        self.loader = loader
        self._owner = parent
        self.total = 0      # 官方一共返回了多少个（截断前）
        _LIVE_THREADS.add(self)

    def run(self):
        from . core import install
        try:
            if self.loader == "fabric":
                cs = install.fabric_loader_candidates(self.mc)
            elif self.loader == "forge":
                cs = [{"version": v, "stable": True}
                      for v in install.forge_candidates(self.mc)]
            elif self.loader == "neoforge":
                cs = [{"version": v, "stable": True}
                      for v in install.neoforge_candidates(self.mc)]
            elif self.loader == "paper":
                # ★ Paper 只有 fill v3 一个下载站了
                #   （官方 api.papermc.io/v2 已整体下线，实测全 410）。
                bs = install.fill_builds(self.mc)
                if not bs:
                    raise install.InstallError(
                        "fill 站没有 %s 的 Paper 构建" % self.mc)
                # ★ 统一按 build 号降序（新的在前）。
                #   不假设接口返回顺序，显式排序，别猜。
                def _bid(b):
                    v = b.get("build")
                    try:
                        return int(v)
                    except Exception:
                        return 0
                bs = sorted(bs, key=_bid, reverse=True)
                cs = [{"version": str(b.get("build")), "stable": True}
                      for b in bs[:self.MAX_VER]]
                self.total = len(bs)
                self.paper_src = "fill"
            else:
                cs = []

            # ★ 统一截到最新 MAX_VER 个。
            #   官方返回的已经是"新的在前"，这里只做截断，不重排 ——
            #   重排要靠数字而不是字符串，各加载器的版本号格式不一样，
            #   冒然排序容易把 0.9.x 排到 0.10.x 前面。
            if cs and len(cs) > self.MAX_VER:
                self.total = max(self.total, len(cs))
                cs = cs[:self.MAX_VER]
            else:
                self.total = max(self.total, len(cs))
            self.got.emit(cs, None)
        except Exception as e:
            try:
                self.got.emit([], install._brief(e) if hasattr(
                    install, "_brief") else str(e)[:80])
            except Exception:
                pass
        finally:
            _LIVE_THREADS.discard(self)


_install_exit_hook()
