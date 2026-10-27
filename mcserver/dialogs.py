# -*- coding: utf-8 -*-
"""各种对话框：创建向导 / Java 管理 / 搬迁。"""

import os
import re
import sys

from PySide6.QtGui import (QColor, QDesktopServices, QTextCursor,
                           QIcon, QPixmap, QImage)
from PySide6.QtCore import (Qt, Signal, QThread, QTimer, QUrl, QSize,
                            QTime)
from PySide6.QtWidgets import (
    QDialog, QVBoxLayout, QHBoxLayout, QFormLayout, QLabel,
    QLineEdit, QComboBox, QPushButton, QCheckBox, QSpinBox,
    QTextEdit, QFileDialog, QMessageBox, QProgressBar,
    QDialogButtonBox, QGroupBox, QListWidget, QListWidgetItem,
    QAbstractItemView, QPlainTextEdit, QTextBrowser,
    QTabWidget, QScrollArea, QDoubleSpinBox, QFrame,  # ★ 加上 QFrame
    QWidget, QApplication, QTableWidget, QTableWidgetItem,
    QHeaderView, QInputDialog, QCompleter, QListView,
    QTimeEdit,  # ★ V1.6.3：定时重启时间框之前漏导，配置窗一点就 NameError
    QRadioButton,  # ★ V1.7.2：ModBrowseDialog 双分组用了但一直没导，
                   #   弹窗一构造就 NameError、点击毫无反应（静默失败）
    QStackedLayout,  # ★ V1.8.0：全库搜索等待态（列表中央"正在搜索..."）
)

from .core import (versions, java as javacore, install, builder,
                   migrate, props, backup, modman, sources, loaderup)


# ★ QTextBrowser 没有 appendPlainText —— 那是 QPlainTextEdit 的方法。
#   日志区为了限行数统一改成了 QTextBrowser，但代码里仍在调 appendPlainText，
#   结果就是"点一下就崩"（AttributeError）。
#   这里补一个安全实现：
#     - 直接调 appendPlainText 也能用
#     - 用 insertPlainText 而不是 append，所以 < > & 不会被当 HTML 吃掉，
#       缩进空格也不会被折叠
class LogView(QTextBrowser):
    """日志显示控件。

    为什么不是直接用 QTextBrowser：
      日志区原本是 QPlainTextEdit，限行数用 setMaximumBlockCount；
      后来为了能点链接改成了 QTextBrowser，但那个方法只有 QPlainTextEdit 有，
      于是代码里 20 多处 self.log.appendPlainText(...) 全都 AttributeError。

      之前是给 QTextBrowser 打猴子补丁解决的 —— 能用，但依赖 import 顺序，
      而且类型不明、静态检查查不出来。这里改成正式子类，一劳永逸。

    用 insertPlainText 而不是 append，所以：
      · < > & 不会被当 HTML 吃掉（日志里全是 <world> 这种）
      · 缩进空格不会被折叠
    """

    def appendPlainText(self, text):
        try:
            self.moveCursor(QTextCursor.End)
            self.insertPlainText(str(text) + "\n")
        except Exception:
            pass

    def appendHtml(self, html):
        try:
            self.moveCursor(QTextCursor.End)
            self.insertHtml(str(html) + "<br>")
        except Exception:
            pass

    def setMaximumBlockCount(self, n):
        """QPlainTextEdit 才有；这里用文档上限近似。"""
        try:
            self.document().setMaximumBlockCount(int(n))
        except Exception:
            pass


# ★ 全局存活集合：线程对象一旦被 Python GC 回收，
#   C++ 那侧的 QThread 就会被销毁。要是这时 run() 还没跑完，
#   Qt 直接 abort 整个程序 —— 报错就是
#     "QThread: Destroyed while thread is still running"
#   表现是"点了某个按钮就闪退"。这里强制保持引用，杜绝被回收。
_LIVE_THREADS = set()


def _th():
    try:
        from .core import theme as _t
        _t.load()
        return _t
    except Exception:
        return None


def _fg():
    """当前主题字体色（取不到就用白）。"""
    t = _th()
    return (t.get("fg") if t else None) or "#ffffff"


def _on_bg(c):
    """把写死的语义色调到在当前底色上读得清。"""
    t = _th()
    return t.on_bg(c) if t else c


def _sec_bg():
    """分组标题行底色。"""
    t = _th()
    return t.section_bg() if t else "#e8e8e8"


def _muted():
    """次要文字（提示/说明）的 CSS。

    ★ 以前直接写死 color:#666 —— 那串灰色在深色底上对比度只有
      2.1~2.7，低于 4.5 可读线，表现就是"提示文字像没显示出来"。
      这里按当前主题现算，保证看得清。
    """
    try:
        from .core import theme as _th
        return _th.muted_css()
    except Exception:
        return "color:#9aa4b2;"


# =========================================================
#  V0.5.0：Paper/Spigot 性能优化（YAML 可视化编辑）
# =========================================================
YAML_ITEMS = [
    # (key, yaml 路径, 类型, 显示名, 默认值, 提示)
    ("optimize-explosions",
     "world-settings.default.optimize-explosions", "bool",
     "优化爆炸计算", True,
     "降低 TNT/爆炸对服务端性能的影响（推荐开启）"),
    ("max-entity-collisions",
     "world-settings.default.entity-activation-range"
     ".max-entity-collisions", "int",
     "实体碰撞上限", 8,
     "同一位置最多同时发生碰撞的实体数，改小可防实体过多卡服"),
    ("mob-spawn-range",
     "world-settings.default.mob-spawn-range", "int",
     "刷怪范围", 8,
     "玩家周围多少个区块内会刷怪，改小更省性能"),
    ("tick-rate",
     "world-settings.default.tick-rate", "int",
     "区块 tick 倍率", 1,
     "1=原版。改大跳过区块 tick 提升性能（略影响红石/刷怪）"),
    ("animals",
     "world-settings.default.entity-activation-range.animals", "int",
     "动物激活距离", 32,
     "动物在多少格内才激活 AI，改小省性能"),
]


def _yaml_path_for(server_dir):
    """找 Paper/Spigot 的 YAML 配置文件，按优先级返回第一个存在的。"""
    cands = [
        os.path.join(server_dir, "config", "paper-world-defaults.yml"),
        os.path.join(server_dir, "config", "paper-global.yml"),
        os.path.join(server_dir, "spigot.yml"),
    ]
    for p in cands:
        if os.path.isfile(p):
            return p
    return None


def _yaml_get(data, path):
    """沿点路径取 YAML 值；缺失返回 None。"""
    cur = data
    for part in path.split("."):
        if not isinstance(cur, dict):
            return None
        cur = cur.get(part)
        if cur is None:
            return None
    return cur


def _yaml_set(data, path, value):
    """沿点路径写入 YAML 值（逐级补 dict）。"""
    cur = data
    parts = path.split(".")
    for part in parts[:-1]:
        nxt = cur.get(part)
        if not isinstance(nxt, dict):
            nxt = {}
            cur[part] = nxt
        cur = nxt
    cur[parts[-1]] = value


# ============ V1.6.1：内存单位（G/MB）智能换算 ============
def _mem_mb_of(sp, cb):
    """按当前单位取整数 MB（底层统一整数 MB 传给 Java）。

    G：值 x 1024 取整；MB：直接取整。
    """
    if cb.currentData() == "g":
        return int(round(float(sp.value()) * 1024))
    return int(round(float(sp.value())))


def _set_mem_mb(sp, cb, mb):
    """按 cb 当前单位把整数 MB 写进 sp（Aikar 同步等场景用）。"""
    if cb.currentData() == "g":
        sp.setValue(round(mb / 1024.0, 1))
    else:
        sp.setValue(int(mb))


def _apply_mem_unit(sp, cb):
    """单位切换联动：改精度/范围/步长/后缀 + 智能换算当前值。

    - 切到 G：当前值视为 MB，除以 1024；整除显示整数、
      除不尽保留一位小数（如 3584 -> 3.5）；范围 1.0~64.0，
      步长 0.5，允许小数。
    - 切到 MB：当前值视为 G，乘以 1024 取整；范围 512~65536，
      步长 512。
    """
    if cb.currentData() == "g":
        mb = int(round(float(sp.value())))
        sp.setDecimals(1)
        sp.setSingleStep(0.5)
        sp.setRange(1.0, 64.0)
        sp.setSuffix(" G")
        v = mb / 1024.0
        sp.setValue(round(v, 1) if v != int(v) else float(int(v)))
    else:
        g = float(sp.value())
        mb = int(round(g * 1024))
        sp.setDecimals(0)
        sp.setSingleStep(512)
        sp.setRange(512, 65536)
        sp.setSuffix(" MB")
        sp.setValue(mb)


def _yaml_scalar(v):
    """YAML 标量文本：bool 写 true/false（小写），其余直接转 str。"""
    if isinstance(v, bool):
        return "true" if v else "false"
    return str(v)


def _yaml_patch_preserve(path, edits):
    """★ V1.5.0：行级原位替换 YAML 值，保留原有注释。

    edits: [(path 列表, value), ...]。
    每个键沿缩进层级在原文中定位，只替换值、保留行尾注释；
    原文里没有的键（Paper 默认文件一般全都有）追加到文件末尾。
    这样不用 safe_dump 重建全文，注释全部保留。
    """
    import re
    with open(path, "r", encoding="utf-8") as f:
        lines = f.readlines()

    stack = []            # [(indent, key), ...]
    idx = {}              # (key1, key2, ...) -> 行号
    for i, ln in enumerate(lines):
        m = re.match(r"^(\s*)([^#\s][^:]*):", ln)
        if not m:
            continue
        indent = len(m.group(1))
        key = m.group(2).strip().strip("'\"")
        while stack and stack[-1][0] >= indent:
            stack.pop()
        stack.append((indent, key))
        idx[tuple(k for _, k in stack)] = i

    changed = False
    missing = []
    for plist, val in edits:
        key = tuple(plist)
        if key not in idx:
            missing.append(plist)
            continue
        i = idx[key]
        line = lines[i]
        m2 = re.match(r"^(\s*[^#][^:]*:)\s*(.*)$", line)
        if not m2:
            continue
        tail = m2.group(2)
        cm = re.search(r"(#.*)$", tail)
        comment = cm.group(1) if cm else ""
        lines[i] = (m2.group(1) + " " + _yaml_scalar(val)
                    + (" " + comment if comment else "") + "\n")
        changed = True

    if missing:
        lines.append("\n# ---- MCSM 性能优化（v1.5.0 追加）----\n")
        for plist, val in edits:
            if plist not in missing:
                continue
            indent = "  " * (len(plist) - 1)
            lines.append(indent + plist[-1] + ": " + _yaml_scalar(val) + "\n")
        changed = True

    if changed:
        with open(path, "w", encoding="utf-8", newline="") as f:
            f.writelines(lines)


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
    import urllib.request
    try:
        req = urllib.request.Request(
            url, headers={"User-Agent": "MCServerManager/0.1"})
        with urllib.request.urlopen(req, timeout=timeout) as r:
            data = r.read()
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
    """
    got = Signal(str, object)   # icon_url, QImage

    def __init__(self, urls, parent=None):
        super().__init__(None)   # ★ 不传 parent，防对话框销毁连坐
        self.urls = urls or []
        _LIVE_THREADS.add(self)

    def run(self):
        for url in self.urls:
            if self.isInterruptionRequested():
                break
            img = _fetch_icon_img(url)
            if img is not None:
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
    palette = [
        ("#5b8def", "#e8f0ff"), ("#f59e0b", "#fff4e0"),
        ("#4ade80", "#e6fbea"), ("#a78bfa", "#f2edff"),
        ("#f87171", "#ffecec"), ("#22d3ee", "#e6fbff"),
        ("#f472b6", "#ffeef6"), ("#facc15", "#fffbe6"),
    ]
    idx = sum(ord(c) for c in name) % len(palette)
    bg, _fg = palette[idx]
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
    p.setBrush(QBrush(QColor("#3a3d47")))
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


class JavaPick(QGroupBox):
    """Java 选择组件：下拉 + 浏览 + 校验提示。"""

    def __init__(self, need=None, parent=None, mc_version=None):
        super().__init__("Java", parent)
        self.need = need
        self.mc_version = mc_version
        self.javas = []

        self.combo = QComboBox()
        self.combo.setMinimumWidth(400)
        self.btn_browse = QPushButton("浏览...")
        self.btn_scan = QPushButton("重新扫描")
        self.btn_dl = QPushButton("下载 Java")
        self.btn_dl.setToolTip("没找到合适的 Java 时，一键从 Adoptium 下载"
                               "Temurin JRE 到软件目录 java/ 并自动选中")
        self.lbl = QLabel("")
        self.lbl.setWordWrap(True)

        row = QHBoxLayout()
        row.addWidget(self.combo, 1)
        row.addWidget(self.btn_browse)
        row.addWidget(self.btn_scan)
        row.addWidget(self.btn_dl)

        v = QVBoxLayout(self)
        v.addLayout(row)
        v.addWidget(self.lbl)

        self.btn_browse.clicked.connect(self.browse)
        self.btn_scan.clicked.connect(self.rescan)
        self.btn_dl.clicked.connect(self._dl_java)
        self.combo.currentIndexChanged.connect(self._check)

    def set_need(self, need, mc_version=None):
        self.need = need
        if mc_version is not None:
            self.mc_version = mc_version
        self._check()

    def rescan(self):
        # ★ V1.6.4：Java 扫描放进后台线程。
        #   javacore.find_all() 要逐个跑 `java -version`（subprocess），
        #   同步执行会卡主线程 2~3 秒 —— 正是"点初始化要等两三秒"的元凶。
        #   现在先显示"正在扫描..."占位，结果回来后再填充下拉框。
        self.combo.blockSignals(True)
        self.combo.clear()
        self.combo.addItem("正在扫描 Java...", None)
        self.combo.blockSignals(False)
        self.lbl.setText("正在扫描系统里的 Java ...")
        self.lbl.setStyleSheet("color:%s;" % _on_bg("#9aa4b2"))
        w = _track(self, Worker(self._scan_job, {}, self))
        w.done.connect(self._on_scan_done)
        w.start()

    def _scan_job(self, on_log=None):
        return javacore.find_all()

    def _on_scan_done(self, ok, res):
        if not ok or not isinstance(res, list):
            self.javas = []
            self.combo.blockSignals(True)
            self.combo.clear()
            self.combo.addItem("(扫描失败：%s)" % (res,), None)
            self.combo.blockSignals(False)
            self.lbl.setText("Java 扫描失败：%r" % (res,))
            self.lbl.setStyleSheet("color:%s;" % _on_bg("#c62828"))
            return
        self.javas = res
        self.combo.blockSignals(True)
        self.combo.clear()
        if not self.javas:
            self.combo.addItem("(未找到 Java，请点『浏览』手动指定)", None)
            self.combo.blockSignals(False)
            self.lbl.setText("没扫描到 Java。")
            self.lbl.setStyleSheet("color:%s;" % _on_bg("#c62828"))
            return
        mx = versions.java_max_ok(self.mc_version) if self.mc_version else None
        for j in self.javas:
            m = j["major"]
            if self.need is not None and m < self.need:
                mark = "   [版本太低]"
            elif mx and m > mx:
                mark = "   [偏高，可能起不来]"
            else:
                mark = ""
            self.combo.addItem("Java %d  -  %s%s"
                               % (m, j["path"], mark), j["path"])
        # 优先选"刚好合适"的，而不是最低满足的
        idx = 0
        best = None
        for i, j in enumerate(self.javas):
            m = j["major"]
            if self.need is not None and m < self.need:
                continue
            if mx and m > mx:
                continue
            if best is None or abs(m - (self.need or 0)) < abs(best[1] - (self.need or 0)):
                best = (i, m)
        if best:
            idx = best[0]
        elif self.javas:
            idx = 0
        self.combo.setCurrentIndex(idx)
        self.combo.blockSignals(False)
        self._check()

    def browse(self):
        filt = ("java (java.exe);;All Files (*)" if os.name == "nt"
                else "All Files (*)")
        path, _ = QFileDialog.getOpenFileName(self, "选择 java", "", filt)
        if not path:
            return
        ok, msg = javacore.validate(path, self.need,
                                    mc_version=self.mc_version)
        if not ok:
            QMessageBox.warning(self, "Java 不可用", msg)
            return
        self.combo.insertItem(0, "Java - %s" % path, path)
        self.combo.setCurrentIndex(0)

    def _check(self):
        if not hasattr(self, "lbl") or self.lbl is None:
            return
        path = self.combo.currentData()
        if not path:
            self.lbl.setText("请选择 Java")
            self.lbl.setStyleSheet("color:%s;" % _on_bg("#c62828"))
            return
        ok, msg = javacore.validate(path, self.need,
                                    mc_version=self.mc_version)
        if ok:
            if "偏高" in msg:
                # 黄色警告：能用但有风险
                self.lbl.setText("[!] %s" % msg)
                self.lbl.setStyleSheet("color:%s;" % _on_bg("#ef6c00"))
            else:
                self.lbl.setText("[OK] %s" % msg)
                self.lbl.setStyleSheet("color:%s;" % _on_bg("#2e7d32"))
        else:
            tail = ("（本服需要 Java %d）" % self.need) if self.need else ""
            self.lbl.setText("[!] %s%s" % (msg, tail))
            self.lbl.setStyleSheet("color:%s;" % _on_bg("#c62828"))

    def path(self):
        return self.combo.currentData()

    # ---------- V0.4.0：一键下载 Java（Worker 线程） ----------
    def _dl_java(self):
        major = self.need or 21
        if self.need:
            hint = "当前服务器需要 Java %d。" % self.need
        else:
            hint = "按 Java %d 下载（现代 MC 通用）。" % major
        r = QMessageBox.question(
            self, "下载 Java",
            "%s\n将从 Adoptium 官方下载 Temurin JRE %d（约 40-60MB）\n"
            "并解压到软件目录的 java/ 文件夹，下载完自动选中。\n\n"
            "继续吗？" % (hint, major),
            QMessageBox.Yes | QMessageBox.No, QMessageBox.Yes)
        if r != QMessageBox.Yes:
            return
        self.btn_dl.setEnabled(False)
        self.lbl.setStyleSheet("color:%s;" % _on_bg("#1565c0"))
        self.lbl.setText("正在下载 JRE %d ...（网络请求在后台）" % major)
        self.worker = _track(self, Worker(
            self._dl_job, {"major": major}, self))
        self.worker.log.connect(lambda m: self.lbl.setText(str(m)))
        self.worker.done.connect(self._dl_done)
        self.worker.start()

    def _dl_job(self, major=None, on_log=None):
        """Worker 内执行：下载 + 解压 + 校验，全部不碰主线程。"""
        exe = javacore.download_jre(int(major or 21), on_log=on_log)
        info = javacore.probe(exe)
        if not info:
            raise RuntimeError("下载完成但校验失败，请重试")
        return {"exe": exe, "major": info[0], "version": info[1]}

    def _dl_done(self, ok, res):
        self.btn_dl.setEnabled(True)
        if not ok:
            self.lbl.setText("[下载失败] %s" % str(res)[:120])
            self.lbl.setStyleSheet("color:%s;" % _on_bg("#c62828"))
            return
        exe = res.get("exe") or ""
        mj = res.get("major")
        self.combo.blockSignals(True)
        self.combo.insertItem(
            0, "Java %d（内置下载） - %s" % (mj or "?", exe), exe)
        self.combo.setCurrentIndex(0)
        self.combo.blockSignals(False)
        self._check()


class CreateDialog(QDialog):
    """创建/初始化服务端向导。"""

    def closeEvent(self, e):
        """关闭前先停掉后台线程。

        ★ 不停的话：对话框销毁了、线程还在跑，
          Qt 会直接 abort 整个程序 —— 也就是"点了就闪退"。
        """
        try:
            _stop_threads(self)
        except Exception:
            pass
        # ★ V1.6.4：JavaPick 的扫描线程挂在它自己身上，
        #   窗口关闭时也要一起停掉，避免线程残留导致崩溃。
        try:
            _stop_threads(self.java_pick)
        except Exception:
            pass
        try:
            super().closeEvent(e)
        except Exception:
            pass

    def __init__(self, entry, javas=None, parent=None):
        super().__init__(parent)
        # 窗口图标（跟主程序一致）
        try:
            from .app import app_icon
            self.setWindowIcon(app_icon())
        except Exception:
            pass
        self.entry = entry
        self.setWindowTitle("初始化服务端 - %s" % entry.get("name", ""))
        self.resize(760, 620)
        self.worker = None

        v = QVBoxLayout(self)

        # ---- ★ 把所有的设置项放进滚动区，防止高 DPI 小屏幕下窗口被撑爆 ----
        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setFrameShape(QFrame.NoFrame)
        scroll_widget = QWidget()
        scroll_layout = QVBoxLayout(scroll_widget)
        # ★ V1.7.2-3：给滚动内容留出边距，防止 QGroupBox 贴边/溢出
        scroll_layout.setContentsMargins(8, 8, 8, 8)
        scroll_layout.setSpacing(10)

        # ---- 基本信息 ----
        g1 = QGroupBox("识别结果")
        f = QFormLayout(g1)
        self.ed_name = QLineEdit(entry.get("world_name") or entry.get("name", ""))
        self.ed_name.setToolTip(
            "对应 server.properties 里的 level-name，"
            "必须与存档文件夹名一致")
        f.addRow("世界名(level-name):", self.ed_name)
        # ★ V1.7.1-3：回显前净化版本（旧 servers.json 里可能存着
        #   Paper 构建号 "26.2.push" 之类脏串）
        # ★ V1.7.3-4：条目里没版本（或版本是脏串）时，当场再跑一遍
        #   多级回退识别 —— 存档 level.dat > 核心 jar 内部
        #   version.json/install.properties > jar 文件名 > 文件夹名。
        #   兜底仍识别不出就把输入框留空、提示手动填写。
        _init_ver = entry.get("version") or ""
        _init_src = entry.get("version_source") or ""
        try:
            from .core import scanner as _sc
            if _init_ver:
                _cv = _sc.clean_mc_version(_init_ver)
                _init_ver = _cv if _cv else ""
            if not _init_ver:
                _det_v, _det_src = _sc.detect_mc_version(
                    entry.get("folder") or entry.get("server_dir") or "",
                    server_dir=entry.get("server_dir"),
                    world_dir=entry.get("world_dir"))
                if _det_v:
                    _init_ver, _init_src = _det_v, _det_src
        except Exception:
            pass
        self.ed_version = QLineEdit(_init_ver)
        self.ed_version.setPlaceholderText("例如 1.21.7 / 26.2")
        try:
            from .core import versions as _V
            self._vcompleter = QCompleter(_V.all_versions(), self)
            self._vcompleter.setCaseSensitivity(Qt.CaseInsensitive)
            self._vcompleter.setFilterMode(Qt.MatchContains)
            self._vcompleter.setCompletionMode(
                QCompleter.PopupCompletion)
            self.ed_version.setCompleter(self._vcompleter)
        except Exception:
            pass
        vrow = QHBoxLayout()
        vrow.addWidget(self.ed_version, 1)
        self.btn_refresh_v = QPushButton("刷新版本列表")
        self.btn_refresh_v.setToolTip(
            "从 Mojang 版本清单拉取最新的正式版列表并缓存到本地。\n"
            "MC 出了新版本（26.4、27.x…）后点一下，就能在这里选到，\n"
            "Java 需求和数据包格式号也会按规律推算出来。")
        self.btn_refresh_v.clicked.connect(self._refresh_versions)
        vrow.addWidget(self.btn_refresh_v)
        vw = QWidget()
        vw.setLayout(vrow)
        f.addRow("MC 版本:", vw)
        hint = QLabel("")
        hint.setWordWrap(True)
        hint.setStyleSheet(_muted())
        if _init_ver:
            hint.setText("读自 %s。若识别错了（比如存档实际是别的版本），"
                         "直接改这里。" % (_init_src or "自动识别"))
        else:
            hint.setText("未能自动识别版本，请手动填写")
        f.addRow("", hint)
        self.lbl_info = QLabel("")
        self.lbl_info.setWordWrap(True)
        f.addRow("存档:", self.lbl_info)
        scroll_layout.addWidget(g1)

        # ---- 加载器 ----
        g2 = QGroupBox("服务端核心")
        f2 = QFormLayout(g2)
        self.cb_loader = QComboBox()
        f2.addRow("加载器:", self.cb_loader)

        self.cb_lver = QComboBox()
        self.cb_lver.setEditable(False)
        self.cb_lver.setEnabled(False)
        self.cb_lver.setToolTip(
            "留空 = 最新稳定版。\n列表按编号从新到旧排列。\n"
            "想用测试版（比如 Fabric 0.20.x）就在这里选。")
        self.btn_lver = QPushButton("刷新")
        self.btn_lver.setEnabled(False)
        self.btn_lver.clicked.connect(self._load_loader_versions)
        hl = QHBoxLayout()
        hl.addWidget(self.cb_lver, 1)
        hl.addWidget(self.btn_lver)
        f2.addRow("加载器版本:", hl)
        self.lbl_lver = QLabel("")
        self.lbl_lver.setWordWrap(True)
        self.lbl_lver.setStyleSheet(_muted())
        f2.addRow("", self.lbl_lver)
        # ---- 优化模组区 ----
        # ★ V1.7.1-4：选择行包成容器，Paper 时整行隐藏，
        #   取消勾选时也隐藏 —— 不再出现"选择要装哪些..."重复两行
        self.chk_mods = QCheckBox("同时下载优化模组")
        self.chk_mods.setChecked(True)
        f2.addRow("", self.chk_mods)
        self.w_mods_sel = QWidget()
        _vm = QVBoxLayout(self.w_mods_sel)
        _vm.setContentsMargins(0, 0, 0, 0)
        _vm.setSpacing(2)
        hmod = QHBoxLayout()
        self.btn_pick_mods = QPushButton("选择要装哪些...")
        self.btn_pick_mods.clicked.connect(self.pick_mods)
        hmod.addWidget(self.btn_pick_mods)
        self.lbl_mods = QLabel("（未选择，点开始时会弹选择界面）")
        self.lbl_mods.setWordWrap(True)
        self.lbl_mods.setStyleSheet(_muted())
        hmod.addWidget(self.lbl_mods, 1)
        _vm.addLayout(hmod)
        f2.addRow("", self.w_mods_sel)
        self.chk_mods.toggled.connect(self.w_mods_sel.setVisible)

        # ---- 插件区 ----
        # ★ V1.7.1-4：默认不勾选，勾选后才出现选择行，
        #   初始界面只显示一行选择按钮，不再重复
        self.chk_plugins = QCheckBox("同时下载插件")
        self.chk_plugins.setChecked(False)
        f2.addRow("", self.chk_plugins)
        self._picked_plugin_slugs = None
        self._plugins_confirmed = False
        self.w_plugins_sel = QWidget()
        _vp = QVBoxLayout(self.w_plugins_sel)
        _vp.setContentsMargins(0, 0, 0, 0)
        _vp.setSpacing(2)
        hplg = QHBoxLayout()
        self.btn_pick_plugins = QPushButton("选择要装哪些插件...")
        self.btn_pick_plugins.clicked.connect(self.pick_plugins)
        hplg.addWidget(self.btn_pick_plugins)
        self.lbl_plugins = QLabel("（未选择，点开始时会弹选择界面）")
        self.lbl_plugins.setWordWrap(True)
        self.lbl_plugins.setStyleSheet(_muted())
        hplg.addWidget(self.lbl_plugins, 1)
        _vp.addLayout(hplg)
        f2.addRow("", self.w_plugins_sel)
        self.chk_plugins.toggled.connect(self.w_plugins_sel.setVisible)
        self.w_plugins_sel.setVisible(False)

        self._picked_slugs = None
        self._mod_fail = []
        self._plugin_fail = []  # ★ V1.2.0
        self._mods_confirmed = False
        self._last_loader = None
        self._rebuilding = False

        self.lbl_loader_hint = QLabel("")
        self.lbl_loader_hint.setWordWrap(True)
        f2.addRow("", self.lbl_loader_hint)
        scroll_layout.addWidget(g2)

        # ---- Java ----
        self.java_pick = JavaPick()
        scroll_layout.addWidget(self.java_pick)
        self.ed_version.textChanged.connect(self._sync_java_need)

        # ---- 内存（★ V1.6.1：MB/G 单位可切换，底层统一整数 MB）----
        g3 = QGroupBox("内存")
        h3 = QHBoxLayout(g3)
        h3.setContentsMargins(10, 10, 10, 10)
        h3.addWidget(QLabel("最小:"))
        # ★ QDoubleSpinBox：G 模式允许小数（0.5 步进）；MB 模式整数
        self.sp_xms = QDoubleSpinBox()
        self.cb_xms_unit = QComboBox()
        self.cb_xms_unit.addItem("G", "g")
        self.cb_xms_unit.addItem("MB", "mb")
        self.cb_xms_unit.setCurrentIndex(0)  # 默认 G
        self.sp_xms.setDecimals(1)
        self.sp_xms.setSingleStep(0.5)
        self.sp_xms.setRange(1.0, 64.0)
        self.sp_xms.setValue(1.0)  # 默认 1.0G
        self.sp_xms.setSuffix(" G")
        h3.addWidget(self.sp_xms)
        h3.addWidget(self.cb_xms_unit)
        h3.addWidget(QLabel("最大:"))
        self.sp_xmx = QDoubleSpinBox()
        self.cb_xmx_unit = QComboBox()
        self.cb_xmx_unit.addItem("G", "g")
        self.cb_xmx_unit.addItem("MB", "mb")
        self.cb_xmx_unit.setCurrentIndex(0)
        self.sp_xmx.setDecimals(1)
        self.sp_xmx.setSingleStep(0.5)
        self.sp_xmx.setRange(1.0, 64.0)
        self.sp_xmx.setValue(4.0)  # 默认 4.0G
        self.sp_xmx.setSuffix(" G")
        h3.addWidget(self.sp_xmx)
        h3.addWidget(self.cb_xmx_unit)
        h3.addStretch(1)
        # 单位切换联动：G <-> MB 智能换算
        self.cb_xms_unit.currentIndexChanged.connect(
            lambda: _apply_mem_unit(self.sp_xms, self.cb_xms_unit))
        self.cb_xmx_unit.currentIndexChanged.connect(
            lambda: _apply_mem_unit(self.sp_xmx, self.cb_xmx_unit))
        scroll_layout.addWidget(g3)

        # ---- 自动化功能（初始化默认勾选，可随时在服务器配置里改）----
        # ★ V1.7.2-3：分组框改名"性能与自动化"，把 Aikar 优化 /
        #   自动重启 / 定时备份统一收纳，界面层级更清楚
        g_auto = QGroupBox("性能与自动化")
        v_auto = QVBoxLayout(g_auto)
        v_auto.setContentsMargins(10, 10, 10, 10)
        self.chk_aikar = QCheckBox("启用 Aikar 性能优化 (G1GC)")
        self.chk_aikar.setToolTip(
            "目前公认最优的 Java 启动参数，可大幅减少 GC 卡顿。\n"
            "启用后最大内存会被强制设置为与最小内存相同。")
        self.chk_aikar.setChecked(True)  # ★ 默认勾选
        v_auto.addWidget(self.chk_aikar)
        self.chk_auto_restart = QCheckBox("崩溃时自动重启（最多 5 次）")
        self.chk_auto_restart.setChecked(True)  # ★ 默认勾选
        v_auto.addWidget(self.chk_auto_restart)
        self.chk_auto_backup = QCheckBox("开启定时备份存档")
        self.chk_auto_backup.setChecked(True)  # ★ 默认勾选
        v_auto.addWidget(self.chk_auto_backup)
        scroll_layout.addWidget(g_auto)

        self.ed_version.textChanged.connect(self._refresh_loaders)
        self.ed_version.textChanged.connect(self._note_version_changed)
        self.ed_version.textChanged.connect(self._schedule_lver_reload)
        self.cb_loader.currentIndexChanged.connect(self._refresh_hint)
        self.cb_loader.currentIndexChanged.connect(self._on_loader_changed)

        scroll_layout.addStretch(1)
        scroll.setWidget(scroll_widget)

        # 把滚动区加进主布局，占据上面的剩余空间（权重设置为 2）
        v.addWidget(scroll, 2)

        # ---- 日志 ----
        self.log = LogView()
        self.log.setReadOnly(True)
        self.log.document().setMaximumBlockCount(2000)
        v.addWidget(QLabel("进度:"))
        # 日志最小高度调小，权重为 1，避免挤压上方 UI
        self.log.setMinimumHeight(100)
        v.addWidget(self.log, 1)

        self.prog = QProgressBar()
        self.prog.setRange(0, 0)
        self.prog.setVisible(False)
        v.addWidget(self.prog)

        btns = QDialogButtonBox(QDialogButtonBox.Ok | QDialogButtonBox.Cancel)
        btns.button(QDialogButtonBox.Ok).setText("开始")
        self.btn_ok = btns.button(QDialogButtonBox.Ok)
        btns.accepted.connect(self.start)
        btns.rejected.connect(self.reject)
        v.addWidget(btns)

        # 初始化时 ed_version 已经填好了（发生在 JavaPick 创建之前，
        # 所以 textChanged 不会触发）。这里先算好 Java 需求。
        self._sync_java_need()

        wd = entry.get("world_dir")
        self.lbl_info.setText(wd or "(未发现存档)")
        self._refresh_loaders()
        # ★ V1.6.4：Java 扫描已异步化，再延迟到窗口显示之后触发，
        #   让"初始化服务端"窗口先弹出来，Java 列表随后自动填充，
        #   不再因为逐个跑 java -version 而卡住弹窗。
        QTimer.singleShot(50, self.java_pick.rescan)
        # ★ V1.7.1-2 / V1.7.3-4：强制给整个弹窗套主题样式。
        #   显式调用 self.setStyleSheet(theme.stylesheet(None)) 覆盖
        #   弹窗自身及所有子控件，恢复 QPushButton:hover / :pressed 的
        #   悬停/按下动效 —— 防止个别控件被局部样式或继承问题盖掉，
        #   出现"按钮像死木头"。再补 apply_palette 兜底：
        #   它现在会给滚动区 viewport 套【完整主题样式表】，让
        #   QScrollArea 内部（刷新版本列表/浏览/重新扫描/下载 Java…）
        #   的按钮同样拿到动效规则。
        try:
            from .core import theme as _th
            _th.load()
            self.setStyleSheet(_th.stylesheet(None))
            _th.apply_palette(self)
        except Exception:
            pass

    def showEvent(self, e):
        """★ V1.7.3-3：窗口真正显示时再套一次主题样式。

        有的环境里 __init__ 里 setStyleSheet 会被后续初始化/布局
        覆盖，导致按钮回到 Windows 默认样式（悬停/点击效果丢失）。
        showEvent 兜底再套一遍，保证按钮动效真正落到渲染。
        """
        try:
            super().showEvent(e)
        except Exception:
            pass
        try:
            _themed(self)
        except Exception:
            pass

    def _refresh_versions(self):
        """从网上拉最新正式版列表，缓存到本地并刷新补全。

        后台线程跑，不卡界面。失败也不影响使用 ——
        硬编码表里已有的版本照常能用。
        """
        from .core import versions as _V
        self.btn_refresh_v.setEnabled(False)
        self.btn_refresh_v.setText("拉取中...")
        self._append_log("正在拉取 MC 版本清单...")

        def job(on_log=None):
            return _V.refresh(timeout=8, log=on_log)

        def done(ok, res):
            self.btn_refresh_v.setEnabled(True)
            self.btn_refresh_v.setText("刷新版本列表")
            if not ok or not isinstance(res, tuple):
                self._append_log("[错误] 拉取失败: %s" % res)
                self._append_log(
                    "      不影响使用：表里已有的版本照常能选。")
                return
            added, total = res
            if total == 0:
                self._append_log("没拿到版本清单（网络不通？）。"
                                 "表里已有的版本照常可用。")
                return
            if added:
                self._append_log(
                    "版本列表已更新：新增 %d 个 —— %s"
                    % (len(added), "、".join(added[:12])
                       + ("…" if len(added) > 12 else "")))
                self._append_log(
                    "  这些新版本的 Java 需求和数据包格式号是按规律"
                    "推算的，装完如果起不来请把报错发我。")
            else:
                self._append_log(
                    "版本列表已是最新（共 %d 个正式版），没有新增。"
                    % total)
            # 刷新补全
            try:
                self._vcompleter.model().setStringList(
                    _V.all_versions())
            except Exception:
                pass

        w = _track(self, Worker(job, {}, self))
        w.log.connect(self._append_log)
        w.done.connect(done)
        w.start()

    def _append_log(self, msg):
        """往进度日志区追加一行。

        ★ 这个方法以前压根没定义 —— 类里调了 9 次 self._append_log(...)，
          但 CreateDialog 没有它。平时还好，一点『刷新版本列表』就
          AttributeError 崩掉（v5.0 加那个按钮时漏了）。
        """
        try:
            self.log.appendPlainText(str(msg))
            sb = self.log.verticalScrollBar()
            if sb is not None:
                sb.setValue(sb.maximum())
        except Exception:
            pass

    def _refresh_loaders(self):
        self._rebuilding = True
        try:
            self._do_refresh_loaders()
        finally:
            self._rebuilding = False
        # 重建期间为了保住已选模组，跳过了文案更新；
        # 重建完再刷一次，把加载器说明/模组提示刷新成当前加载器的
        self._refresh_hint()
        # ★ V1.6.2：加载器确定后立刻联动查询版本下拉框。
        #   以前只在 textChanged / 换加载器时查，初始化时 ed_version
        #   已填好（textChanged 不触发），cb_lver 就一直是空的。
        try:
            self._load_loader_versions()
        except Exception:
            pass

    def _do_refresh_loaders(self):
        v = self.ed_version.text().strip()
        # ★ 必须屏蔽信号：清空和重填都会触发 currentIndexChanged，
        #   导致瞬间发起好几次网络请求，旧结果返回时覆盖掉新结果。
        self.cb_loader.blockSignals(True)
        try:
            self.cb_loader.clear()
            if not v:
                self.cb_loader.addItem("(先填版本)", None)
                return
            need = versions.need_java(v)
            self.java_pick.set_need(need)
            sup = versions.supported_loaders(v)
            cur = self.cb_loader.currentData() or self.entry.get("loader")
            for l in sup:
                self.cb_loader.addItem(versions.LOADER_LABEL.get(l, l), l)
            pick = cur if cur in sup else (sup[0] if sup else None)
            if pick is not None:
                i = self.cb_loader.findData(pick)
                if i >= 0:
                    self.cb_loader.setCurrentIndex(i)
        finally:
            self.cb_loader.blockSignals(False)

    def _refresh_hint(self):
        l = self.cb_loader.currentData()
        v = self.ed_version.text().strip()
        if not l:
            self.lbl_loader_hint.setText("")
            return
        if l == "paper":
            self.lbl_loader_hint.setText(
                "插件服。玩家用原版客户端即可进，无需安装任何东西。\n"
                "Paper 官方只维护较新的版本；旧版本（如 1.21.3/1.21.4，"
                "甚至 1.7.10）虽被官方标为 unsupported，但 fill 站仍保留"
                "构建，本软件会自动从那里下载。\n"
                "⚠ 太老的版本官方不再修 bug / 补安全漏洞，出问题需自行承担。")
            # ★ V1.7.1-4：Paper 不装优化模组 —— 整行隐藏，
            #   不再显示一个禁用的死按钮造成"重复/没反应"
            self.chk_mods.setVisible(False)
            self.w_mods_sel.setVisible(False)
            # ★ V1.2.0：Paper -> 显示插件勾选与选择入口
            self.chk_plugins.setVisible(True)
            self.w_plugins_sel.setVisible(self.chk_plugins.isChecked())
            if self._last_loader != l and not self._rebuilding:
                self._picked_slugs = []
                self._mods_confirmed = False
                self._picked_plugin_slugs = None
                self._plugins_confirmed = False
            self._last_loader = l
        else:
            self.lbl_loader_hint.setText(
                "模组服。玩家客户端若装模组，只能装纯客户端模组"
                "（优化/光影/小地图），不能装添加内容的模组。\n"
                "也可同时勾选『下载插件』安装插件类组件"
                "（权限/语音/经济等，需该加载器支持）。")
            # ★ V1.7.1-4：模组区整行显示，插件区默认收起、
            #   勾选『同时下载插件』才展开
            self.chk_mods.setVisible(True)
            self.w_mods_sel.setVisible(self.chk_mods.isChecked())
            self.chk_plugins.setVisible(True)
            self.w_plugins_sel.setVisible(self.chk_plugins.isChecked())
            if self._last_loader != l and not self._rebuilding:
                self._picked_slugs = None
                self._mods_confirmed = False
                self._picked_plugin_slugs = None
                self._plugins_confirmed = False
            self._last_loader = l
            self._refresh_mods_label()

    def _on_loader_changed(self):
        """换加载器 -> 清空并重新拉可用版本。"""
        # 重建期间不响应，避免 _do_refresh_loaders 引发连锁拉取
        if getattr(self, "_rebuilding", False):
            return
        # ★ V1.6.2：网络未返回前保持禁用，显示"正在查询版本..."
        self.cb_lver.blockSignals(True)
        try:
            self.cb_lver.clear()
            self.cb_lver.addItem("正在查询版本...", None)
        finally:
            self.cb_lver.blockSignals(False)
        self._lver_loaded = False
        self._need_lver_async = True
        self.cb_lver.setEnabled(False)
        self.btn_lver.setEnabled(False)
        self.lbl_lver.setText("")
        self._load_loader_versions()

    def _schedule_lver_reload(self):
        """MC 版本变了 -> 稍等一下再重拉加载器版本清单（防抖）。
        
        ★ 为什么需要防抖：边打字边联网会把请求打爆，且旧请求晚回来
          会覆盖掉新结果。这里用 350ms 延迟，如果用户在 350ms 内继续
          打字，定时器会重置。
        """
        try:
            if getattr(self, "_lver_timer", None) is None:
                self._lver_timer = QTimer(self)
                self._lver_timer.setSingleShot(True)
                self._lver_timer.timeout.connect(self._load_loader_versions)
            self._lver_timer.stop()
            self._lver_timer.start(350)
        except Exception:
            pass

    def _load_loader_versions(self):
        """联网拉这个加载器在这个 MC 版本上的可用版本。"""
        v = self.ed_version.text().strip()
        loader = self.cb_loader.currentData()
        if not v or not loader:
            return
            
        # ★ 发新请求前，先彻底杀掉上一次还没跑完的旧线程！
        #   否则快速切版本时，会同时跑几十个网络请求，导致假死。
        old_w = getattr(self, "lver_worker", None)
        if old_w is not None:
            try:
                old_w.stop()  # 继承自 _StopMixin
            except Exception:
                pass

        self.btn_lver.setEnabled(False)
        # ★ V1.6.2：查询期间下拉框保持禁用，显示"正在查询版本..."
        #   （不显示"(最新稳定版)"占位，防止用户误点旧选项）
        self.cb_lver.setEnabled(False)
        self.cb_lver.blockSignals(True)
        try:
            self.cb_lver.clear()
            self.cb_lver.addItem("正在查询版本...", None)
        finally:
            self.cb_lver.blockSignals(False)
        self.lbl_lver.setText("")
        
        # ★ 引入请求 ID 防止并发乱序（旧请求晚回来直接丢弃）
        self._lver_req_id = getattr(self, "_lver_req_id", 0) + 1
        req_id = self._lver_req_id
        
        self.lver_worker = _track(self, LoaderVerWorker(v, loader, self))
        self.lver_worker.got.connect(lambda c, e: self._on_lver(c, e, req_id))
        self.lver_worker.start()

    def _on_lver(self, cands, err, req_id):
        # ★ 丢弃过期的返回结果
        if getattr(self, "_lver_req_id", 0) != req_id:
            return
        if not hasattr(self, "lver_worker") or self.lver_worker is None:
            return
        self.btn_lver.setEnabled(True)
        
        # ★ 填充时屏蔽信号，防止 currentIndexChanged 连锁触发卡顿
        self.cb_lver.blockSignals(True)
        try:
            self.cb_lver.clear()
            self.cb_lver.addItem("(最新稳定版)", None)
        finally:
            self.cb_lver.blockSignals(False)
        if err:
            self.cb_lver.setEnabled(True)
            self.lbl_lver.setText(
                "暂时查不到版本清单，留空会自动用最新稳定版"
                "（不影响安装）")
            return
        if not cands:
            self.cb_lver.setEnabled(True)  # ★ 恢复可点，别一直灰着
            self.lbl_lver.setText("这个加载器没有可选版本，留空即可")
            return
        for c in cands:
            label = c["version"]
            if not c.get("stable", True):
                label += "  (测试版)"
            self.cb_lver.addItem(label, c["version"])
        self.cb_lver.setEnabled(True)
        n_beta = sum(1 for c in cands if not c.get("stable", True))
        total = 0
        try:
            total = int(getattr(self.lver_worker, "total", 0) or 0)
        except Exception:
            total = 0
        # 每次打开都是实时查的（换 MC 版本 / 换加载器 / 点『刷新』
        # 都会重查），所以这里就是官方当前的最新情况。
        tip = "实时查到 %d 个" % (total or len(cands))
        if total and total > len(cands):
            tip += "，这里只列最新的 %d 个" % len(cands)
        if n_beta:
            tip += "，其中 %d 个是测试版" % n_beta
        tip += "（点『刷新』可重新拉取）"
        self.lbl_lver.setText(tip)

    def _note_version_changed(self):
        """版本改了 -> 提醒模组清单可能变，但不强制重选。"""
        if not self._mods_confirmed:
            return
        txt = self.lbl_mods.text()
        if "版本已改" in txt:
            return
        self.lbl_mods.setText(
            "%s\n（版本已改，模组清单可能不同 —— 想重选请点左边按钮）" % txt)

    def _refresh_mods_label(self):
        """按当前选择状态刷新模组那行提示文字。"""
        if self._mods_confirmed and self._picked_slugs is not None:
            n = len(self._picked_slugs)
            if n == 0:
                self.lbl_mods.setText("已选：0 个（只装核心）")
            else:
                shown = "、".join(self._picked_slugs[:6])
                if n > 6:
                    shown += " ..."
                self.lbl_mods.setText("已选 %d 个：%s" % (n, shown))
        else:
            self.lbl_mods.setText("（未选择，点开始时会弹选择界面）")

    def _log(self, m):
        self.log.appendPlainText(m)


    def _sync_java_need(self):
        """版本改了 -> 重算需要的 Java，并自动选一个合适的。"""
        v = self.ed_version.text().strip()
        need = versions.need_java(v)
        self.java_pick.set_need(need, mc_version=v)
        if self.java_pick.javas:
            best = javacore.pick_for(need, self.java_pick.javas,
                                     mc_version=v)
            if best:
                i = self.java_pick.combo.findData(best["path"])
                if i >= 0:
                    self.java_pick.combo.setCurrentIndex(i)

    def pick_mods(self):
        """弹出模组选择界面。

        ★ V1.7.1-1：整体包 try/except —— 弹窗构造/查询如果抛异常，
        以前会在信号槽里静默吞掉（点击无反应），现在打印到日志并
        弹窗提示具体错误。
        """
        try:
            v = self.ed_version.text().strip()
            loader = self.cb_loader.currentData()
            if not loader:
                QMessageBox.warning(self, "缺信息", "先选加载器")
                return
            if not install.mod_list_for(loader, v):
                QMessageBox.information(
                    self, "这个加载器没有模组",
                    "%s 是插件服/原版，不装模组。\n\n"
                    "插件请在服务器目录的 plugins/ 里手动放。" % loader)
                self._picked_slugs = []
                self.lbl_mods.setText("（%s 不装模组）" % loader)
                return
            dlg = _themed(self._browse_dlg(v, loader))
            if dlg.exec() != QDialog.Accepted:
                return
            self._picked_slugs = dlg.selected
            self._mods_confirmed = True
            n = len(self._picked_slugs)
            if n == 0:
                self.lbl_mods.setText("已选：0 个（只装核心）")
            else:
                self.lbl_mods.setText("已选 %d 个：%s"
                                      % (n, "、".join(self._picked_slugs[:6])
                                         + (" ..." if n > 6 else "")))
        except Exception as ex:
            import traceback as _tb
            try:
                _tb.print_exc()
            except Exception:
                pass
            QMessageBox.warning(
                self, "选择模组失败",
                "打开模组选择界面时出错：\n%r\n\n"
                "已把错误打印到日志，可反馈给作者。" % ex)

    def _browse_dlg(self, v, loader):
        """PCL2 风格模组选择视图（初始化向导共用）。"""
        e = dict(self.entry)
        e["version"] = v
        e["loader"] = loader
        return ModBrowseDialog(e, self, select_only=True)

    def _browse_plugins_dlg(self, v):
        """★ V1.2.0：插件选择视图（数据源 project_type=plugin）。

        ★ V1.7.1-1：以前写死 loader="paper"，Fabric/Forge/NeoForge
          服点插件按钮时按 Paper 过滤版本，查不到/异常 -> 弹窗无反应。
          现在用当前实际加载器，插件版本按该加载器过滤。
        """
        e = dict(self.entry)
        e["version"] = v
        e["loader"] = self.cb_loader.currentData() or "paper"
        return ModBrowseDialog(e, self, select_only=True, mode="plugins")

    def pick_plugins(self):
        """★ V1.2.0：弹出插件选择界面。

        ★ V1.7.1-1：同 pick_mods，加 try/except 防止点击无反应。
        """
        try:
            v = self.ed_version.text().strip()
            if not v:
                QMessageBox.warning(self, "缺信息", "先填 MC 版本")
                return
            dlg = _themed(self._browse_plugins_dlg(v))
            if dlg.exec() != QDialog.Accepted:
                return
            self._picked_plugin_slugs = dlg.selected
            self._plugins_confirmed = True
            n = len(self._picked_plugin_slugs)
            if n == 0:
                self.lbl_plugins.setText("已选：0 个（只装核心）")
            else:
                self.lbl_plugins.setText("已选 %d 个：%s"
                                         % (n, "、".join(
                                             self._picked_plugin_slugs[:6])
                                            + (" ..." if n > 6 else "")))
            # ★ Paper 装了插件后自动勾上"同时下载插件"
            self.chk_plugins.setChecked(True)
        except Exception as ex:
            import traceback as _tb
            try:
                _tb.print_exc()
            except Exception:
                pass
            QMessageBox.warning(
                self, "选择插件失败",
                "打开插件选择界面时出错：\n%r\n\n"
                "已把错误打印到日志，可反馈给作者。" % ex)

    def start(self):
        v = self.ed_version.text().strip()
        loader = self.cb_loader.currentData()
        jpath = self.java_pick.path()
        if not v:
            QMessageBox.warning(self, "缺信息", "请填写 MC 版本")
            return
        if not loader:
            QMessageBox.warning(self, "缺信息", "请选择加载器")
            return
        if not jpath:
            QMessageBox.warning(self, "缺信息", "请选择 Java")
            return
        ok, msg = javacore.validate(jpath, versions.need_java(v),
                                    mc_version=v)
        if not ok:
            QMessageBox.warning(self, "Java 不合适", msg)
            return
        if "偏高" in msg:
            r = QMessageBox.question(
                self, "Java 版本偏高",
                "%s\n\n"
                "继续安装可能会【装完起不来】。\n"
                "确定仍要用这个 Java 吗？" % msg,
                QMessageBox.Yes | QMessageBox.No, QMessageBox.No)
            if r != QMessageBox.Yes:
                return

        server_dir = self.entry.get("server_dir") or self.entry.get("folder", "")

        # ★ 模组选择：勾了"同时下载优化模组"就先弹选择界面。
        #   Paper 不装模组（界面已隐藏该行，但勾选状态可能残留 True），
        #   这里显式排除，避免 Paper 服点开始又弹出模组选择。
        mod_slugs = None
        if self.chk_mods.isChecked() and loader != "paper":
            # ★ 已经选过就用那份，不再弹第二次
            if self._mods_confirmed and self._picked_slugs is not None:
                mod_slugs = self._picked_slugs
            else:
                dlg = _themed(self._browse_dlg(v, loader))
                if dlg.exec() != QDialog.Accepted:
                    return
                mod_slugs = dlg.selected
                self._picked_slugs = mod_slugs
                self._mods_confirmed = True
            if not mod_slugs:
                r = QMessageBox.question(
                    self, "一个都没选",
                    "你没勾选任何模组。\n\n"
                    "确定不装模组，只装核心吗？",
                    QMessageBox.Yes | QMessageBox.No, QMessageBox.No)
                if r != QMessageBox.Yes:
                    return
        else:
            self._picked_slugs = None
            self._mods_confirmed = False

        # ★ 插件选择：勾了"同时下载插件"就先弹选择界面
        #   ★ V1.7.1-1：放开给所有加载器（v1.7.0 只改了界面显示，
        #   start() 里还写死 paper，Fabric/Forge 勾了插件也不下载）
        plugin_slugs = None
        if self.chk_plugins.isChecked():
            if self._plugins_confirmed and self._picked_plugin_slugs is not None:
                plugin_slugs = self._picked_plugin_slugs
            else:
                dlg = _themed(self._browse_plugins_dlg(v))
                if dlg.exec() != QDialog.Accepted:
                    return
                plugin_slugs = dlg.selected
                self._picked_plugin_slugs = plugin_slugs
                self._plugins_confirmed = True
            if not plugin_slugs:
                r = QMessageBox.question(
                    self, "一个都没选",
                    "你没勾选任何插件。\n\n"
                    "确定不装插件，只装核心吗？",
                    QMessageBox.Yes | QMessageBox.No, QMessageBox.No)
                if r != QMessageBox.Yes:
                    return
        else:
            self._picked_plugin_slugs = None
            self._plugins_confirmed = False

        # ★ 加载器版本：下拉里选了就用选的，没选(None)才自动用最新。
        #   以前压根没往 info 里放，_do 拿到的永远是 None，
        #   于是"明明选了 448 却下了 308"。
        lv = None
        try:
            if self.cb_lver.isEnabled():
                lv = self.cb_lver.currentData()
        except Exception:
            lv = None

        info = {
            "server_dir": server_dir,
            "version": v,
            "loader": loader,
            "loader_version": lv,
            "java": jpath,
            "xms": _mem_mb_of(self.sp_xms, self.cb_xms_unit),
            "xmx": _mem_mb_of(self.sp_xmx, self.cb_xmx_unit),
            # ★ 自动化三项（默认全勾），随初始化写进服务器配置
            "aikar": self.chk_aikar.isChecked(),
            "auto_restart": self.chk_auto_restart.isChecked(),
            "auto_backup": self.chk_auto_backup.isChecked(),
            # level-name 必须是"世界文件夹的名字"，不是显示名。
            # 显示名可能含冒号/颜色码，会导致启动崩溃。
            "level_name": self._pick_level_name(),
            "want_mods": (self.chk_mods.isChecked()
                          and loader != "paper"),
            "mod_slugs": mod_slugs,
            # ★ V1.7.1-1：插件放开给所有加载器（不再写死 paper）
            "want_plugins": self.chk_plugins.isChecked(),
            "plugin_slugs": plugin_slugs,
            "entry": self.entry,
        }
        self.btn_ok.setEnabled(False)
        self.prog.setVisible(True)
        self.worker = _track(self, Worker(self._do, {"info": info}, self))
        self.worker.log.connect(self._log)
        self.worker.done.connect(self._finished)
        self.worker.start()


    def _pick_level_name(self):
        """决定 level-name：优先用世界文件夹的实际名字。"""
        e = self.entry
        sd = e.get("server_dir") or e.get("folder", "")

        # 1) 扫描器找到的世界目录 -> 用它的 basename
        wd = e.get("world_dir")
        if wd and os.path.isdir(wd):
            base = os.path.basename(wd.rstrip("\\/"))
            if base and base.lower() != os.path.basename(
                    sd.rstrip("\\/")).lower():
                return base

        # 2) 服务器目录下唯一的子文件夹（新导入结构）
        try:
            subs = [n for n in sorted(os.listdir(sd))
                    if os.path.isdir(os.path.join(sd, n))
                    and not n.startswith(".")
                    and n.lower() not in ("mods", "plugins", "config",
                                          "logs", "backups", "libraries",
                                          "versions", "world", "data")]
            # 找出含 level.dat 的
            for n in subs:
                if os.path.isfile(os.path.join(sd, n, "level.dat")):
                    return n
        except Exception:
            pass

        # 3) 有 world/ 就用 world
        if os.path.isdir(os.path.join(sd, "world")):
            return "world"

        # 4) 最后才用显示名（会走 safe_level_name 清洗）
        return (e.get("world_name")
                or self.ed_name.text().strip() or "world")

    def _do(self, info=None, on_log=None):
        log = on_log or (lambda m: None)
        sd = info["server_dir"]
        os.makedirs(sd, exist_ok=True)

        log("目标目录: %s" % sd)
        _lv = info.get("loader_version")
        log("版本 %s / 加载器 %s%s"
            % (info["version"], info["loader"],
               ("（指定 %s）" % _lv) if _lv else "（最新稳定版）"))

        # 1) eula
        builder.write_eula(sd)
        log("[OK] eula.txt")

        # 2) 核心
        log("下载 %s 核心 ..." % info["loader"])
        r = install.install_core(info["loader"], sd, info["version"],
                                 java_path=info["java"], on_log=log,
                                 loader_version=info.get("loader_version"))
        launcher = r.get("launch") or "jar"
        args_rel = r.get("args_rel")
        if launcher == "argsfile":
            # 新版 Forge：没有启动 jar，用 args 文件
            jar_name = None
            log("[OK] 核心: args 文件模式 (%s)" % args_rel)
        else:
            jar_name = os.path.basename(r["jar"])
            log("[OK] 核心: %s" % jar_name)

        # 3) 模组（用初始化前选好的清单）
        if info["want_mods"]:
            only = info.get("mod_slugs")
            log("下载优化模组 (%s) ..."
                % ("%d 个" % len(only) if only else "全部"))
            mr = install.install_mods(sd, info["version"],
                                      info["loader"], on_log=log,
                                      only=only)
            log("模组: %d 个成功, %d 个跳过"
                % (len(mr["ok"]), len(mr["fail"])))
            for f in mr["fail"]:
                log("  失败: %s (%s)" % (f["name"], f["slug"]))
            for f in (mr.get("skipped") or []):
                log("  因缺前置跳过: %s (%s)"
                    % (f.get("name"), f.get("slug")))
            self._mod_fail = mr["fail"] + (mr.get("skipped") or [])

            # ★ 针对 Forge 1.20.1 的 FerriteCore 防崩溃配置
            if info["loader"] == "forge" and info["version"] == "1.20.1":
                try:
                    cfg_dir = os.path.join(sd, "config")
                    cfg_path = os.path.join(cfg_dir, "ferritecore.toml")
                    os.makedirs(cfg_dir, exist_ok=True)
                    # 检查是否已经存在该配置
                    need_write = True
                    if os.path.isfile(cfg_path):
                        with open(cfg_path, "r", encoding="utf-8") as f:
                            txt = f.read()
                        if "populateNeighborTable" in txt:
                            need_write = False
                    
                    if need_write:
                        with open(cfg_path, "a", encoding="utf-8") as f:
                            f.write("\n[general]\n# 防止 Forge 1.20.1 下区块加载崩溃\npopulateNeighborTable = true\n")
                        log("[OK] 已为 FerriteCore 添加防崩溃配置")
                except Exception as e:
                    log("  [警告] 自动配置 FerriteCore 失败: %r" % e)

            # （服务端的 ServerCore 配置已移至「服务器配置」中手动生成）

        # 3.5) 插件（★ V1.7.0-3：放开所有加载器，按实际加载器过滤版本）
        if info.get("want_plugins"):
            pl_slugs = info.get("plugin_slugs")
            log("下载插件 (%s) ..."
                % ("%d 个" % len(pl_slugs) if pl_slugs else "全部"))
            pr = install.install_plugins(
                sd, info["version"], loader=info.get("loader") or "paper",
                on_log=log, only=pl_slugs)
            log("插件: %d 个成功, %d 个失败"
                % (len(pr["ok"]), len(pr["fail"])))
            for f in pr["fail"]:
                log("  失败: %s (%s)" % (f["name"], f["slug"]))
            self._plugin_fail = pr["fail"]

        # 4) server.properties
        e = info["entry"]
        props = builder.build_properties(
            level_name=info["level_name"],
            mc_version=info["version"],
            difficulty=e.get("difficulty"),
            gamemode=e.get("gamemode"),
            seed=e.get("seed"),
            regions=e.get("regions"),
            motd=builder.clean_display(
                info["entry"].get("world_name") or info["level_name"]))
        builder.write_text(os.path.join(sd, "server.properties"),
                           props, crlf=False)
        log("[OK] server.properties")

        # 5) run.bat
        bat = builder.build_run_bat(
            info["java"], jar_name,
            xms="%dM" % info["xms"], xmx="%dM" % info["xmx"],
            launch=launcher, args_rel=args_rel)
        bp = os.path.join(sd, "run.bat")
        with open(bp, "w", encoding="utf-8", newline="") as f:
            f.write(bat)
        log("[OK] run.bat")

        return {"server_dir": sd, "jar": jar_name, "java": info["java"],
                "version": info["version"], "loader": info["loader"],
                "xms": info["xms"], "xmx": info["xmx"],
                "launch": launcher, "args_rel": args_rel}

    # 常见错误 -> 人话。返回"可能的原因"那段文字。
    FRIENDLY = [
        # (匹配关键字, 标题, 建议)
        (("410", "gone", "下架"),
         "Paper 这个版本没有可下载构建",
         "Paper 官方站已不再直接提供旧版本，但 fill 站通常还有。\n"
         "如果 fill 也查不到，说明这个 MC 版本确实没有 Paper 构建。\n"
         "→ 改选 Fabric（几乎所有版本都有），或换一个 Paper 有的版本。"),

        (("无法获取 paper", "paper 没有", "没有 .* 的构建"),
         "这个版本 Paper 没有构建",
         "→ 改选 Fabric，或换一个 Paper 有的版本。"),

        (("fabric 没有", "no loader", "loader"),
         "Fabric 没有对应这个版本的 loader",
         "可能是版本号填错了，或者这个 MC 版本太新/太老。\n"
         "→ 在上方『MC 版本』里确认一下。"),

        (("forge", "neoforge", "maven"),
         "Forge / NeoForge 下载失败",
         "Forge 的安装器要跑几分钟（中途要下大量依赖），"
         "这是正常的。\n"
         "如果很快就失败，多半是这个版本没有 Forge 构建，\n"
         "或者 Maven 源连不上。\n"
         "→ 换 Fabric 试试，它装得快得多。"),

        (("403", "forbidden"),
         "被服务器拒绝了（403）",
         "可能是网络出口被限制，或者对方暂时屏蔽了你的 IP。\n"
         "→ 过一会再试，或换个网络。"),

        (("404", "not found"),
         "找不到这个资源（404）",
         "多半是版本号填错了，或者该版本确实没有对应的核心。"),

        (("timed out", "timeout", "超时"),
         "网络超时",
         "下载源连不上或太慢。\n"
         "→ 检查网络，或过一会重试。"),

        (("ssl", "certificate", "证书"),
         "SSL 证书问题",
         "多半是系统时间不对，或者网络中间有代理/劫持。\n"
         "→ 检查一下电脑右下角的日期时间是否正确。"),

        (("urlerror", "connection", "name or service", "拒绝", "连接"),
         "连不上网络",
         "→ 检查网络是否通畅。\n"
         "   公司/校园网有时会拦这些下载源。"),

        (("java", "jdk"),
         "Java 有问题",
         "→ 在上方『Java』那一栏重新选一个。\n"
         "   1.20.1 要 Java 17，1.21.x / 26.x 要 Java 21，26.3 要 25。"),

        (("permission denied", "拒绝访问", "权限"),
         "没有写入权限",
         "→ 目标文件夹可能开着（资源管理器占用），"
         "或者被杀毒软件拦了。\n"
         "   关掉占用它的窗口再试。"),

        (("no space", "空间不足", "磁盘"),
         "磁盘空间不够",
         "→ 清出一些空间再试。"),
    ]

    def _friendly_error(self, res):
        """把原始异常翻译成人能看懂的原因 + 建议。"""
        raw = ""
        try:
            if isinstance(res, dict):
                raw = str(res.get("error") or res.get("msg") or res)
            else:
                raw = str(res)
        except Exception:
            raw = "未知错误"
        low = raw.lower()

        for keys, title, tip in self.FRIENDLY:
            for k in keys:
                try:
                    import re as _re
                    if _re.search(k, low):
                        return "%s\n%s" % (title, tip)
                except Exception:
                    if k in low:
                        return "%s\n%s" % (title, tip)

        # 没匹配上：给原始信息，别让用户一头雾水
        if len(raw) > 300:
            raw = raw[:300] + " ..."
        return "原始错误：\n%s\n\n" \
               "（如果看不懂，把这段发给作者。）" % raw

    def _retry_failed_mods(self, failed):
        """只重试没装上的那几个模组（核心不用重装）。"""
        from . import core
        sd = self.entry.get("server_dir") or self.entry["folder"]
        slugs = [f.get("slug") for f in failed if f.get("slug")]
        self.btn_ok.setEnabled(False)
        self.prog.setVisible(True)
        self.log.appendPlainText("")
        self.log.appendPlainText("=== 重试 %d 个失败的模组 ===" % len(slugs))
        self.worker = _track(self, Worker(
            self._do_retry, {"sd": sd, "slugs": slugs}, self))
        self.worker.log.connect(self._log)
        self.worker.done.connect(self._retry_done)
        self.worker.start()

    def _do_retry(self, sd=None, slugs=None, on_log=None):
        from . core import install
        log = on_log or (lambda m: None)
        mr = install.install_mods(sd, self.ed_version.text().strip(),
                                  self.cb_loader.currentData(),
                                  on_log=log, only=slugs)
        log("重试结果：成功 %d，仍失败 %d"
            % (len(mr["ok"]), len(mr["fail"])))
        return mr

    def _retry_done(self, ok, res):
        self.prog.setVisible(False)
        self.btn_ok.setEnabled(True)
        n_ok = len((res or {}).get("ok", [])) if ok else 0
        n_fail = len((res or {}).get("fail", [])) if ok else 0
        if ok and n_fail == 0:
            QMessageBox.information(self, "完成",
                                    "这次 %d 个模组全部装上了。" % n_ok)
            self._mod_fail = []
            self.accept()
        elif ok:
            self._mod_fail = res["fail"]
            QMessageBox.warning(
                self, "仍有失败",
                "装上 %d 个，还有 %d 个没成功。\n\n"
                "请检查这些模组在 Modrinth 上是否有对应的 MC 版本和加载器。\n"
                "如果确认有，可能是本软件网络请求受阻（如镜像源限流）。"
                % (n_ok, n_fail))
        else:
            QMessageBox.critical(self, "失败",
                                 "重试失败：%r" % (res,))

    def _finished(self, ok, res):
        self.prog.setVisible(False)
        self.btn_ok.setEnabled(True)
        if ok:
            self.result_info = res
            failed = getattr(self, "_mod_fail", None) or []
            if failed:
                # ★ 只要没死掉，统统不弹窗打扰，直接放行！
                # 失败原因可以在上方日志看，或者事后去主界面自己补。
                pass
            
            QMessageBox.information(self, "完成",
                                    "服务端已就绪。\n可直接双击 run.bat，"
                                    "或在主界面点『启动』。")
            self.accept()
        else:
            hint = self._friendly_error(res)
            box = QMessageBox(self)
            box.setIcon(QMessageBox.Critical)
            box.setWindowTitle("失败")
            box.setText("初始化失败，请看上方进度日志。\n\n"
                        "── 可能的原因 ──\n%s" % hint)
            # Paper 被官方下架时，给一个"去 fill 站手动下载"的入口 ——
            # 自动下载走不通时，手动下 jar 改名 server.jar 一样能用。
            mv = getattr(self, "ed_version", None)
            mcv = (mv.text().strip() if mv else "") or ""
            loader = (self.cb_loader.currentData()
                      if hasattr(self, "cb_loader") else None)
            b_manual = None
            if loader == "paper" and mcv:
                url = ("https://fill-ui.papermc.io/projects/paper/"
                       "versions/%s" % mcv)
                b_manual = box.addButton("打开 fill 站手动下载",
                                         QMessageBox.ActionRole)
            box.addButton(QMessageBox.Close)
            box.exec()
            if b_manual is not None and box.clickedButton() is b_manual:
                try:
                    from PySide6.QtGui import QDesktopServices
                    from PySide6.QtCore import QUrl
                    QDesktopServices.openUrl(QUrl(url))
                except Exception:
                    pass


class ServerConfigDialog(QDialog):
    """可视化编辑 server.properties + 该服专属 Java/内存设置。"""

    # 分组：(标签, [字段键...])
    GROUPS = [
        ("世界", ["level-name", "level-seed", "level-type", "difficulty",
                  "gamemode", "hardcore", "max-world-size"]),
        ("视距", ["view-distance", "simulation-distance"]),
        ("维度", ["allow-nether", "allow-end"]),
        ("玩家", ["max-players", "online-mode", "white-list",
                  "enforce-whitelist", "pvp", "allow-flight",
                  "server-port", "spawn-protection", "motd"]),
        ("刷怪", ["spawn-monsters", "spawn-animals", "spawn-npcs",
                  "spawn-structures"]),
        ("其它", ["enable-command-block", "function-permission-level",
                  "op-permission-level", "player-idle-timeout",
                  "enable-status", "hide-online-players",
                  "require-resource-pack", "resource-pack",
                  "resource-pack-sha1", "max-tick-time",
                  "enforce-secure-profile", "sync-chunk-writes",
                  "network-compression-threshold",
                  "use-native-transport", "log-ips", "snooper-enabled"]),
    ]

    def closeEvent(self, e):
        """关闭前先停掉后台线程。

        ★ 不停的话：对话框销毁了、线程还在跑，
          Qt 会直接 abort 整个程序 —— 也就是"点了就闪退"。
        """
        try:
            _stop_threads(self)
        except Exception:
            pass
        # ★ V1.6.4：JavaPick 的扫描线程挂在它自己身上，同样要停。
        try:
            _stop_threads(self.java_pick)
        except Exception:
            pass
        try:
            super().closeEvent(e)
        except Exception:
            pass

    def __init__(self, entry, saved=None, parent=None):
        super().__init__(parent)
        # 窗口图标（跟主程序一致）
        try:
            from .app import app_icon
            self.setWindowIcon(app_icon())
        except Exception:
            pass
        self.entry = entry
        self.saved = dict(saved or {})
        self.setWindowTitle("服务器配置 - %s" % entry.get("name", ""))
        self.resize(860, 720)
        self._need_rescan = False

        sd = entry.get("server_dir") or entry["folder"]
        # ★ 模组/备份/日志等页签都读 self.server_dir。
        #   这个类里以前只有局部变量 sd，没有这个属性 ——
        #   一点『更新 Fabric API』之类的按钮就 AttributeError。
        self.server_dir = sd
        self.props_path = os.path.join(sd, "server.properties")
        # ★ V1.6.2：读取失败绝不卡死/崩溃 —— 回退默认值并提示
        try:
            self.values, self.lines = props.parse(self.props_path)
        except Exception:
            self.values, self.lines = {}, []
        if not self.values:
            QMessageBox.information(
                self, "提示",
                "没找到 server.properties，将用默认值创建。\n"
                "建议先『初始化服务端』。")

        self.widgets = {}
        v = QVBoxLayout(self)

        # ---- 页签容器 ----
        self.tabs = QTabWidget()

        # ===== 页签 1：server.properties（★ V1.5.0：合并为单页滚动）=====
        # 以前按分组拆成 6 个子页签，切换麻烦；现在一个可滚动页面
        # 从上到下排完，布尔值全部用 QCheckBox，小白看得懂。
        sa = QScrollArea()
        sa.setWidgetResizable(True)
        self.props_tabs = sa  # 保留属性名，兼容其它引用
        w = QWidget()
        self._props_root = w  # 挂到 self 防止被回收
        out = QVBoxLayout(w)
        for gname, keys in self.GROUPS:
            gb = QGroupBox(gname)
            f = QFormLayout(gb)
            f.setLabelAlignment(Qt.AlignRight)
            for k in keys:
                lbl = QLabel(props.KEY_LABEL.get(k, k))
                wd = self._mk_widget(k)
                desc = props.KEY_DESC.get(k, "")
                if desc and props.KEY_TYPE.get(k) != "enum":
                    lbl.setToolTip(desc)
                    wd.setToolTip(desc)
                f.addRow(lbl, wd)
                self.widgets[k] = wd
            out.addWidget(gb)
        out.addStretch(1)
        sa.setWidget(w)
        # ===== 页签 1：登录（★ V1.8.2：移到最前）=====
        self.tabs.addTab(self._auth_tab(), "登录")

        # ===== 页签 2：server.properties =====
        self.tabs.addTab(sa, "server.properties")
        v.addWidget(self.tabs, 1)

        # ===== 页签 2：核心 =====
        self.tabs.addTab(self._core_tab(), "核心")

        # ===== 页签 3：模组 =====
        self.tabs.addTab(self._mod_tab(), "模组")

        # ===== 页签 5：插件（★ V1.7.0-3：放开所有加载器，
        #   Fabric / Forge / NeoForge 也支持装插件类组件）=====
        ldr_cfg = (entry.get("loader") or "").lower()
        if ldr_cfg not in ("", "vanilla", "unknown"):
            self.tabs.addTab(self._plugin_tab(), "插件")

        # ===== 页签 6：模组与插件配置（★ V1.8.2：原"插件配置"+
        #   "模组配置"合并为一个页签，位于插件之后）=====
        self.tabs.addTab(self._modplugin_tab(ldr_cfg), "模组与插件配置")

        # ===== 页签 7：备份 =====
        self.tabs.addTab(self._backup_tab(), "备份")

        # ===== 页签 8：白名单 =====
        self.tabs.addTab(self._whitelist_tab(), "白名单")

        # ===== 页签 9：启动设置 =====
        gj = QGroupBox("Java 与内存（仅本服）", self)
        self._w_java = gj
        gv = QVBoxLayout(gj)
        need = versions.need_java(entry.get("version"))
        self.java_pick = JavaPick(need)
        gv.addWidget(self.java_pick)
        hm = QHBoxLayout()
        hm.addWidget(QLabel("最小内存:"))
        # ★ V1.6.1：内存单位 MB/G 可切换，智能回显、底层统一整数 MB
        self.sp_xms = QDoubleSpinBox()
        self.cb_xms_unit = QComboBox()
        self.cb_xms_unit.addItem("G", "g")
        self.cb_xms_unit.addItem("MB", "mb")
        hm.addWidget(self.sp_xms)
        hm.addWidget(self.cb_xms_unit)
        hm.addWidget(QLabel("最大内存:"))
        self.sp_xmx = QDoubleSpinBox()
        self.cb_xmx_unit = QComboBox()
        self.cb_xmx_unit.addItem("G", "g")
        self.cb_xmx_unit.addItem("MB", "mb")
        hm.addWidget(self.sp_xmx)
        hm.addWidget(self.cb_xmx_unit)
        hm.addStretch(1)
        gv.addLayout(hm)
        # 智能回显：saved 里整数 MB，能被 1024 整除 → G 显示，否则 MB
        # （先回显后连接联动，避免 setCurrentIndex 触发换算重算）
        self._init_mem_spin(self.sp_xms, self.cb_xms_unit,
                            int(self.saved.get("xms", 1024)))
        self._init_mem_spin(self.sp_xmx, self.cb_xmx_unit,
                            int(self.saved.get("xmx", 4096)))
        self.cb_xms_unit.currentIndexChanged.connect(
            lambda: _apply_mem_unit(self.sp_xms, self.cb_xms_unit))
        self.cb_xmx_unit.currentIndexChanged.connect(
            lambda: _apply_mem_unit(self.sp_xmx, self.cb_xmx_unit))

        # ★ run.bat 结束时要不要 pause（那个"按任意键继续"）
        hp = QHBoxLayout()
        hp.addWidget(QLabel("run.bat 结束时:"))
        self.cb_pause = QComboBox()
        self.cb_pause.addItem("只在崩溃时暂停（推荐）", "error")
        self.cb_pause.addItem("总是暂停（正常关闭也要按一下）", "always")
        self.cb_pause.addItem("从不暂停（窗口直接关）", "never")
        cur_p = self.saved.get("pause", "error")
        ip = self.cb_pause.findData(cur_p)
        self.cb_pause.setCurrentIndex(ip if ip >= 0 else 0)
        hp.addWidget(self.cb_pause, 1)
        gv.addLayout(hp)
        self.lbl_pause = QLabel("")
        self.lbl_pause.setWordWrap(True)
        self.lbl_pause.setStyleSheet(_muted())
        gv.addWidget(self.lbl_pause)
        self.cb_pause.currentIndexChanged.connect(self._sync_pause_hint)
        self._sync_pause_hint()

        gv.addSpacing(10)
        gb_auto = QGroupBox("自动化功能")
        fg_auto = QFormLayout(gb_auto)

        # ★ 新增 Aikar 优化选项
        self.chk_aikar = QCheckBox("启用 Aikar 性能优化 (G1GC)")
        self.chk_aikar.setToolTip(
            "目前公认最优的 Java 启动参数，可大幅减少 GC 卡顿。\n"
            "启用后最大内存会被强制设为与最小内存相同。")
        self.chk_aikar.setChecked(self.saved.get("aikar", False))
        fg_auto.addRow("", self.chk_aikar)
        self.chk_aikar.toggled.connect(self._sync_aikar_mem)

        self.chk_auto_restart = QCheckBox("崩溃时自动重启（最多 5 次）")
        self.chk_auto_restart.setChecked(self.saved.get("auto_restart", False))
        fg_auto.addRow("", self.chk_auto_restart)

        # ★ V0.5.0：每日定时重启
        hb_rt = QHBoxLayout()
        self.chk_auto_restart_time = QCheckBox("每日定时重启")
        self.chk_auto_restart_time.setToolTip(
            "到点后服务器会广播『60 秒后重启』，倒计时结束自动 stop\n"
            "并重新启动，适合清空内存泄漏 / 定期保活的场景。")
        rt_cfg = str(self.saved.get("auto_restart_time") or "")
        self.chk_auto_restart_time.setChecked(bool(rt_cfg))
        hb_rt.addWidget(self.chk_auto_restart_time)
        self.time_restart = QTimeEdit()
        self.time_restart.setDisplayFormat("HH:mm")
        try:
            hh, mm = rt_cfg.split(":")
            self.time_restart.setTime(QTime(int(hh), int(mm)))
        except Exception:
            self.time_restart.setTime(QTime(4, 0))
        self.time_restart.setEnabled(self.chk_auto_restart_time.isChecked())
        self.chk_auto_restart_time.toggled.connect(
            self.time_restart.setEnabled)
        hb_rt.addWidget(self.time_restart)
        fg_auto.addRow("", hb_rt)
        
        hb_bk = QHBoxLayout()
        self.chk_auto_backup = QCheckBox("定时备份存档")
        self.chk_auto_backup.setChecked(self.saved.get("auto_backup", False))
        hb_bk.addWidget(self.chk_auto_backup)
        self.sp_bk_interval = QSpinBox()
        self.sp_bk_interval.setRange(5, 1440)
        self.sp_bk_interval.setValue(self.saved.get("backup_interval", 30))
        self.sp_bk_interval.setSuffix(" 分钟")
        self.sp_bk_interval.setEnabled(self.chk_auto_backup.isChecked())
        self.chk_auto_backup.toggled.connect(self.sp_bk_interval.setEnabled)
        hb_bk.addWidget(self.sp_bk_interval)
        fg_auto.addRow("", hb_bk)
        gv.addWidget(gb_auto)

        sw = QScrollArea()
        sw.setWidgetResizable(True)
        sw.setWidget(gj)
        self.tabs.addTab(sw, "启动设置")

        # ===== 页签 7：性能优化（Paper/Spigot YAML）=====
        self.tabs.addTab(self._yaml_tab(), "性能优化(Paper/Spigot)")

        self.java_pick.rescan()
        want = self.saved.get("java")
        if want:
            i = self.java_pick.combo.findData(want)
            if i >= 0:
                self.java_pick.combo.setCurrentIndex(i)
            else:
                self.java_pick.combo.insertItem(
                    0, "Java - %s" % want, want)
                self.java_pick.combo.setCurrentIndex(0)

        # ---- 按钮 ----
        btns = QDialogButtonBox(
            QDialogButtonBox.Save | QDialogButtonBox.Cancel)
        btns.button(QDialogButtonBox.Save).setText("保存并生成 run.bat")
        btns.accepted.connect(self.do_save)
        btns.rejected.connect(self.reject)
        v.addWidget(btns)


    # ---------------- 核心页签 ----------------
    def _sync_pause_hint(self):
        """解释当前 pause 选项的含义。"""
        try:
            d = self.cb_pause.currentData()
        except Exception:
            return
        if d == "error":
            t = ("正常关闭（/stop）时窗口直接关，不用按键；"
                 "崩溃时才停住让你看错误。")
        elif d == "always":
            t = "无论怎么退出都会停下来等按键（老行为）。"
        else:
            t = ("窗口直接关。注意：崩溃时错误会一闪而过，"
                 "看不到内容。")
        try:
            self.lbl_pause.setText(t)
        except Exception:
            pass

    def _core_tab(self):
        w = QWidget(self)   # 父对象设为对话框，避免被回收
        self._w_core = w
        v = QVBoxLayout(w)
        sd = self.entry.get("server_dir") or self.entry["folder"]

        g1 = QGroupBox("当前核心")
        f1 = QFormLayout(g1)
        cj = install.find_core(sd)
        self.lbl_core = QLabel(
            os.path.basename(cj) if cj else "未识别到核心")
        f1.addRow("核心文件:", self.lbl_core)
        self.lbl_loader = QLabel(self.entry.get("loader") or "未知")
        f1.addRow("加载器:", self.lbl_loader)
        v.addWidget(g1)

        g2 = QGroupBox("选择 / 更换核心")
        v2 = QVBoxLayout(g2)
        v2.addWidget(QLabel(
            "换核心会先删掉现有的 core jar，再下载新的。\n"
            "存档、模组、server.properties 都会保留。"))
        hb = QHBoxLayout()
        self.cb_loader = QComboBox()
        self.cb_loader.addItems(
            ["fabric", "forge", "neoforge", "paper", "vanilla"])
        cur = (self.entry.get("loader") or "").lower()
        i = self.cb_loader.findText(cur)
        if i >= 0:
            self.cb_loader.setCurrentIndex(i)
        hb.addWidget(QLabel("加载器:"))
        hb.addWidget(self.cb_loader)
        self.btn_switch = QPushButton("下载并更换")
        self.btn_switch.clicked.connect(self._switch_core)
        hb.addWidget(self.btn_switch)
        hb.addStretch(1)
        v2.addLayout(hb)

        self.core_prog = QProgressBar()
        self.core_prog.setRange(0, 0)
        self.core_prog.setVisible(False)
        v2.addWidget(self.core_prog)
        self.core_log = LogView()
        self.core_log.setReadOnly(True)
        self.core_log.setMaximumHeight(160)
        self.core_log.setVisible(False)
        v2.addWidget(self.core_log)
        v.addWidget(g2)

        g3 = QGroupBox("删除核心")
        v3 = QVBoxLayout(g3)
        v3.addWidget(QLabel(
            "只删核心 jar，存档/模组/配置全保留。\n"
            "删完可以再选一个别的加载器。"))
        hb3 = QHBoxLayout()
        self.btn_remove_core = QPushButton("删除核心")
        self.btn_remove_core.clicked.connect(self._remove_core_here)
        hb3.addWidget(self.btn_remove_core)
        hb3.addStretch(1)
        v3.addLayout(hb3)
        v.addWidget(g3)

        v.addStretch(1)
        return w

    def _switch_core(self):
        """真正换核心：删旧的 -> 下新的，带进度日志。"""
        loader = self.cb_loader.currentText().strip().lower()
        old_loader = (self.entry.get("loader") or "").lower()

        if loader == old_loader:
            r = QMessageBox.question(
                self, "已经是这个加载器",
                "当前就是 %s。\n\n仍要重新下载一遍吗？"
                % (loader or "未知"),
                QMessageBox.Yes | QMessageBox.No, QMessageBox.No)
            if r != QMessageBox.Yes:
                return

        # Java：优先用本服已存的，其次用启动设置里选的
        jpath = (self.saved.get("java")
                 or (self.java_pick.path()
                     if hasattr(self, "java_pick") else ""))
        if not jpath:
            try:
                from .core import javacore
                need = versions.need_java(self.entry.get("version"))
                cands = javacore.find_javas()
                pick = javacore.pick_for(cands, need) if cands else None
                if pick:
                    jpath = pick.get("path") or ""
            except Exception:
                jpath = ""

        if not jpath:
            r = QMessageBox.question(
                self, "没找到 Java",
                "没选 Java，Forge/NeoForge 安装必须要有。\n\n"
                "Fabric/Paper/Vanilla 可以不装。仍要继续吗？",
                QMessageBox.Yes | QMessageBox.No, QMessageBox.No)
            if r != QMessageBox.Yes:
                return

        sd = self.entry.get("server_dir") or self.entry["folder"]
        mcver = self.entry.get("version") or ""

        r = QMessageBox.warning(
            self, "确认更换核心",
            "将把 %s 换成 %s（版本 %s）。\n\n"
            "流程：\n"
            "  1. 删掉现有核心文件\n"
            "  2. 下载新的\n\n"
            "存档 / mods / plugins / server.properties 都会保留。\n"
            "Forge / NeoForge 安装可能要几分钟。\n\n继续？"
            % (old_loader or "当前核心", loader, mcver or "未知"),
            QMessageBox.Yes | QMessageBox.No, QMessageBox.No)
        if r != QMessageBox.Yes:
            return

        # 进度区
        self.core_log.setVisible(True)
        self.core_log.clear()
        self.core_prog.setVisible(True)
        self.core_prog.setRange(0, 0)
        self.btn_switch.setEnabled(False)
        self.btn_remove_core.setEnabled(False)
        QApplication.processEvents()

        self.core_worker = _track(self, Worker(
            self._do_switch,
            {"server_dir": sd, "loader": loader,
             "mc_version": mcver, "java": jpath},
            self))
        self.core_worker.log.connect(self._core_log)
        self.core_worker.done.connect(self._switch_done)
        self.core_worker.start()

    def _do_switch(self, server_dir, loader, mc_version, java,
                   on_log=None):
        log = on_log or (lambda m: None)
        # 1) 删旧的
        log("=== 1/2 删除现有核心 ===")
        try:
            install.remove_core(server_dir, also_dirs=True, on_log=log)
        except Exception as e:
            log("[警告] 删除时出问题: %r" % e)
        # 2) 下新的
        log("")
        log("=== 2/2 下载 %s ===" % loader)
        res = install.install_core(
            loader, server_dir, mc_version,
            java_path=java or None, on_log=log)
        return res

    def _core_log(self, msg):
        self.core_log.appendPlainText(msg)
        QApplication.processEvents()

    def _switch_done(self, ok, res):
        self.core_prog.setRange(0, 1)
        self.core_prog.setValue(1)
        self.btn_switch.setEnabled(True)
        self.btn_remove_core.setEnabled(True)
        self._need_rescan = True
        if ok:
            self.core_log.appendPlainText("")
            self.core_log.appendPlainText("=== 完成 ===")
            # 刷新显示
            try:
                cj = install.find_core(
                    self.entry.get("server_dir")
                    or self.entry["folder"])
                self.lbl_core.setText(
                    os.path.basename(cj) if cj else "未识别到核心")
                self.lbl_loader.setText(
                    self.cb_loader.currentText())
            except Exception:
                pass
            QMessageBox.information(
                self, "完成",
                "核心已更换为 %s。\n\n"
                "记得回主界面点『重新扫描』。"
                % self.cb_loader.currentText())
        else:
            self.core_log.appendPlainText("")
            self.core_log.appendPlainText("[失败] %s" % res)
            QMessageBox.warning(
                self, "更换失败",
                "没能完成更换。\n\n%s\n\n"
                "存档和配置都还在，可以重试或换个加载器。" % res)

    def _remove_core_here(self):
        dlg = _themed(RemoveCoreDialog(self.entry, self))
        if dlg.exec():
            self.lbl_core.setText("已删除（保存后刷新）")
            self._need_rescan = True

    # ---------------- 模组页签 ----------------
    def _mod_tab(self):
        w = QWidget(self)
        self._w_mod = w
        v = QVBoxLayout(w)
        try:
            self.mod_mods = modman.scan_mods(
                self.entry.get("server_dir")
                or self.entry["folder"])
        except Exception:
            self.mod_mods = []

        self.mod_table = QTableWidget(0, 4)
        self.mod_table.setHorizontalHeaderLabels(
            ["状态", "名称", "版本", "文件"])
        self.mod_table.verticalHeader().setVisible(False)
        self.mod_table.setEditTriggers(
            QAbstractItemView.NoEditTriggers)
        self.mod_table.setSelectionBehavior(
            QAbstractItemView.SelectRows)
        hh = self.mod_table.horizontalHeader()
        for i, m in enumerate((80, 200, 140, 1)):
            hh.setSectionResizeMode(
                i, QHeaderView.ResizeToContents if m != 1
                else QHeaderView.Stretch)
        v.addWidget(self.mod_table, 1)

        for m in self.mod_mods:
            r = self.mod_table.rowCount()
            self.mod_table.insertRow(r)
            self.mod_table.setItem(r, 0, QTableWidgetItem(
                "启用" if m.get("enabled", True) else "禁用"))
            self.mod_table.setItem(r, 1,
                                   QTableWidgetItem(m.get("name", "?")))
            self.mod_table.setItem(r, 2,
                                   QTableWidgetItem(m.get("version", "")))
            self.mod_table.setItem(r, 3, QTableWidgetItem(
                os.path.basename(m.get("path", ""))))

        hb = QHBoxLayout()
        b_en = QPushButton("启用")
        b_en.clicked.connect(lambda: self._toggle_mod(True))
        hb.addWidget(b_en)
        b_dis = QPushButton("禁用")
        b_dis.clicked.connect(lambda: self._toggle_mod(False))
        hb.addWidget(b_dis)
        b_open = QPushButton("打开 mods 文件夹")
        b_open.clicked.connect(self._open_mods_here)
        hb.addWidget(b_open)
        b_full = QPushButton("完整管理（含更新检查）")
        b_full.clicked.connect(self._open_mod_dialog)
        hb.addWidget(b_full)
        v.addLayout(hb)

        # ---- 第二行：添加 / 更新 ----
        hb2 = QHBoxLayout()
        b_add = QPushButton("＋ 添加模组")
        b_add.setToolTip("从 Modrinth / CurseForge 搜并安装")
        b_add.clicked.connect(self._add_mod_here)
        hb2.addWidget(b_add)
        b_up = QPushButton("↑ 更新加载器")
        b_up.setToolTip("只换核心文件，存档/模组/配置都不动")
        b_up.clicked.connect(self._update_loader_here)
        hb2.addWidget(b_up)
        b_api = QPushButton("↑ 更新 Fabric API")
        b_api.setToolTip("几乎所有模组的前置，版本不对会导致"
                         "所有模组一起加载失败")
        b_api.clicked.connect(self._update_fapi_here)
        hb2.addWidget(b_api)
        hb2.addStretch(1)
        v.addLayout(hb2)
        return w

    # ---------- 插件页签（★ V1.2.0，Paper 专用） ----------
    def _plugins_dir_here(self):
        return os.path.join(self.server_dir, "plugins")

    def _plugin_tab(self):
        w = QWidget(self)
        self._w_plugin = w
        v = QVBoxLayout(w)
        try:
            self.plugins = modman.scan_mods_dir(self._plugins_dir_here())
        except Exception:
            self.plugins = []

        self.plugin_table = QTableWidget(0, 4)
        self.plugin_table.setHorizontalHeaderLabels(
            ["状态", "名称", "版本", "文件"])
        self.plugin_table.verticalHeader().setVisible(False)
        self.plugin_table.setEditTriggers(
            QAbstractItemView.NoEditTriggers)
        self.plugin_table.setSelectionBehavior(
            QAbstractItemView.SelectRows)
        hh = self.plugin_table.horizontalHeader()
        for i, m in enumerate((80, 200, 140, 1)):
            hh.setSectionResizeMode(
                i, QHeaderView.ResizeToContents if m != 1
                else QHeaderView.Stretch)
        v.addWidget(self.plugin_table, 1)

        self._fill_plugin_table()

        # ---- 第一行：启用/禁用/打开目录 ----
        hb = QHBoxLayout()
        b_en = QPushButton("启用")
        b_en.clicked.connect(lambda: self._toggle_plugin(True))
        hb.addWidget(b_en)
        b_dis = QPushButton("禁用")
        b_dis.clicked.connect(lambda: self._toggle_plugin(False))
        hb.addWidget(b_dis)
        b_open = QPushButton("打开 plugins 文件夹")
        b_open.clicked.connect(self._open_plugins_here)
        hb.addWidget(b_open)
        v.addLayout(hb)

        # ---- 第二行：添加 ----
        hb2 = QHBoxLayout()
        b_add = QPushButton("＋ 添加插件")
        b_add.setToolTip("从 Modrinth 搜索并安装 Paper 插件")
        b_add.clicked.connect(self._add_plugin_here)
        hb2.addWidget(b_add)
        hb2.addStretch(1)
        v.addLayout(hb2)

        # ---- 插件配置板块（基础版）----
        v.addWidget(self._plugin_cfg_box())
        return w

    def _fill_plugin_table(self):
        self.plugin_table.setRowCount(0)
        for p in self.plugins:
            r = self.plugin_table.rowCount()
            self.plugin_table.insertRow(r)
            self.plugin_table.setItem(r, 0, QTableWidgetItem(
                "启用" if p.get("enabled", True) else "禁用"))
            self.plugin_table.setItem(r, 1,
                                      QTableWidgetItem(p.get("name", "?")))
            self.plugin_table.setItem(r, 2,
                                      QTableWidgetItem(p.get("version", "")))
            self.plugin_table.setItem(r, 3, QTableWidgetItem(
                os.path.basename(p.get("path", ""))))

    def _cur_plugin(self):
        i = self.plugin_table.currentRow()
        if 0 <= i < len(self.plugins):
            return self.plugins[i]
        return None

    def _toggle_plugin(self, enable):
        p = self._cur_plugin()
        if not p:
            QMessageBox.information(self, "提示", "先选中一个插件")
            return
        try:
            modman.set_enabled(p["path"], enable)
            self._need_rescan = True
            self._refresh_plugins_here()
            QMessageBox.information(
                self, "完成",
                "已%s: %s\n\n重启服务器后生效。"
                % ("启用" if enable else "禁用", p.get("name")))
        except Exception as e:
            QMessageBox.critical(self, "失败", repr(e))

    def _open_plugins_here(self):
        _open_in_explorer(self._plugins_dir_here())

    def _add_plugin_here(self):
        """在这里直接搜装插件（装完自动刷新列表）。

        ★ V1.7.2-2：用 self._plugin_dialog 持有引用（防止局部变量被
        GC 回收导致窗口一闪没），外层包 try/except（防止异常被事件
        循环静默吞掉、点击毫无反应），show 后 raise_+activateWindow
        保证弹窗在顶层。
        """
        try:
            self._plugin_dialog = _themed(ModBrowseDialog(
                self.entry, self, mode="plugins"))
            self._plugin_dialog.show()
            self._plugin_dialog.raise_()
            self._plugin_dialog.activateWindow()
            self._plugin_dialog.finished.connect(
                lambda _res: self._refresh_plugins_here())
        except Exception as e:
            import traceback as _tb
            try:
                _tb.print_exc()
            except Exception:
                pass
            QMessageBox.critical(
                self, "打开插件搜索失败",
                "错误详情：%r" % (e,))

    def _refresh_plugins_here(self):
        try:
            self.plugins = modman.scan_mods_dir(self._plugins_dir_here())
        except Exception:
            self.plugins = []
        self._fill_plugin_table()

    # ---------- 插件配置板块（基础版，★ V1.2.0） ----------
    # ---------- 插件配置页签（★ V1.8.0） ----------
    def _luckperms_cfg_path(self):
        """找 LuckPerms 配置文件（config/LuckPerms/config.yml）。"""
        base = os.path.join(self.server_dir, "config")
        for sub in ("LuckPerms", "luckperms", "LUCKPERMS"):
            p = os.path.join(base, sub, "config.yml")
            if os.path.isfile(p):
                return p
        return os.path.join(base, "LuckPerms", "config.yml")

    def _read_luckperms_cfg(self):
        """读 LuckPerms 配置，缺字段用默认值。返回 (dict, path)。"""
        defaults = {
            "storage-method": "h2",
            "server": "global",
            "default-groups": ["default"],
        }
        p = self._luckperms_cfg_path()
        try:
            if os.path.isfile(p):
                with open(p, "r", encoding="utf-8",
                          errors="replace") as f:
                    d = yaml.safe_load(f.read()) or {}
        except Exception:
            d = {}
        return defaults, p

    def _modplugin_tab(self, ldr_cfg):
        """★ V1.8.2：『模组与插件配置』合并页签。

        上半部 = 原模组配置（_modconf_tab），下半部 = 原插件配置
        （_plugincfg_tab，仅非 vanilla 服显示）。两个页签内容合并
        为一个页签，位于『插件』之后。
        """
        w = QWidget(self)
        v = QVBoxLayout(w)
        v.setContentsMargins(0, 0, 0, 0)
        v.setSpacing(6)
        v.addWidget(self._modconf_tab())
        if ldr_cfg not in ("", "vanilla", "unknown"):
            v.addWidget(self._plugincfg_tab())
        v.addStretch(1)
        return w

    def _plugincfg_tab(self):
        """插件配置页签：LuckPerms 简单配置界面 + EssentialsX 一键生成。"""
        import yaml  # noqa: F401
        w = QWidget(self)
        self._w_plugincfg = w
        v = QVBoxLayout(w)

        pd = self._plugins_dir_here()
        names = []
        try:
            if os.path.isdir(pd):
                names = [f.lower() for f in os.listdir(pd)
                         if f.lower().endswith(".jar")
                         or f.lower().endswith(".jar.disabled")]
        except Exception:
            names = []

        def _has(prefix):
            return any(n.startswith(prefix) for n in names)

        # ---- LuckPerms 简单配置界面 ----
        gb = QGroupBox("LuckPerms（权限管理）")
        f = QFormLayout(gb)

        if not _has("luckperms"):
            f.addRow(QLabel(
                "未检测到 LuckPerms 插件。\n"
                "在『插件』页签装上 LuckPerms 后，"
                "这里就能直接配它的存储与默认组。"))
            row = QHBoxLayout()
            b = QPushButton("生成基础配置（luckperms.yml）")
            b.clicked.connect(self._gen_luckperms_cfg)
            row.addWidget(b)
            row.addStretch(1)
            f.addRow("", row)
        else:
            defaults, path = self._read_luckperms_cfg()
            tip = QLabel("配置文件：%s" % path)
            tip.setWordWrap(True)
            tip.setStyleSheet(_muted())
            f.addRow("", tip)

            self.lp_storage = QComboBox()
            for k, label in (("h2", "h2（本地文件，推荐）"),
                             ("sqlite", "SQLite（本地文件）"),
                             ("mysql", "MySQL（远程数据库）")):
                self.lp_storage.addItem(label, k)
            cur = defaults.get("storage-method", "h2")
            i = self.lp_storage.findData(cur)
            self.lp_storage.setCurrentIndex(i if i >= 0 else 0)
            f.addRow("存储方式:", self.lp_storage)

            self.lp_server = QLineEdit(str(defaults.get("server", "global")))
            self.lp_server.setPlaceholderText("global")
            f.addRow("服务器名(server):", self.lp_server)

            dg = defaults.get("default-groups") or ["default"]
            self.lp_groups = QLineEdit(
                ", ".join(str(x) for x in dg) if dg else "default")
            self.lp_groups.setPlaceholderText("default")
            f.addRow("默认组(逗号分隔):", self.lp_groups)

            # MySQL 连接块（选 mysql 时显示）
            self.lp_mysql_box = QGroupBox("MySQL 连接")
            mf = QFormLayout(self.lp_mysql_box)
            ms = defaults.get("data-source") or {}
            my = ms.get("mysql") or {}
            self.lp_mysql_host = QLineEdit(str(my.get("host", "127.0.0.1")))
            mf.addRow("地址:", self.lp_mysql_host)
            self.lp_mysql_port = QSpinBox()
            self.lp_mysql_port.setRange(1, 65535)
            self.lp_mysql_port.setValue(int(my.get("port", 3306)))
            mf.addRow("端口:", self.lp_mysql_port)
            self.lp_mysql_db = QLineEdit(str(my.get("database", "luckperms")))
            mf.addRow("数据库:", self.lp_mysql_db)
            self.lp_mysql_user = QLineEdit(str(my.get("username", "root")))
            mf.addRow("用户名:", self.lp_mysql_user)
            self.lp_mysql_pwd = QLineEdit(str(my.get("password", "")))
            self.lp_mysql_pwd.setEchoMode(QLineEdit.Password)
            mf.addRow("密码:", self.lp_mysql_pwd)
            f.addRow("", self.lp_mysql_box)

            def _toggle_mysql(_i=None):
                self.lp_mysql_box.setVisible(
                    self.lp_storage.currentData() == "mysql")
            self.lp_storage.currentIndexChanged.connect(_toggle_mysql)
            _toggle_mysql()

            b_save = QPushButton("保存 LuckPerms 配置")
            b_save.clicked.connect(self._save_luckperms_cfg)
            f.addRow("", b_save)

        v.addWidget(gb)

        # ---- EssentialsX 一键生成 ----
        gb2 = QGroupBox("EssentialsX（基础指令/经济桥）")
        g2 = QVBoxLayout(gb2)
        if _has("essentialsx"):
            row = QHBoxLayout()
            row.addWidget(QLabel("已安装 <b>EssentialsX</b>"))
            row.addStretch(1)
            b = QPushButton("生成基础配置")
            b.clicked.connect(self._gen_essentialsx_cfg)
            row.addWidget(b)
            g2.addLayout(row)
        else:
            g2.addWidget(QLabel(
                "未检测到 EssentialsX。装好后可一键生成基础配置。"))
        row2 = QHBoxLayout()
        b_open = QPushButton("打开插件配置文件夹")
        b_open.setToolTip("打开服务器 config/ 目录（所有插件的配置都在这里）")
        b_open.clicked.connect(
            lambda: _open_in_explorer(os.path.join(
                self.server_dir, "config")))
        row2.addWidget(b_open)
        row2.addStretch(1)
        g2.addLayout(row2)
        v.addWidget(gb2)

        v.addStretch(1)
        sw = QScrollArea()
        sw.setWidgetResizable(True)
        sw.setWidget(w)
        return sw

    def _save_luckperms_cfg(self):
        """把 LuckPerms 配置界面上的值写回 config.yml。"""
        import yaml
        import shutil
        try:
            defaults, path = self._read_luckperms_cfg()
            d = {}
            try:
                if os.path.isfile(path):
                    with open(path, "r", encoding="utf-8",
                              errors="replace") as f:
                        d = yaml.safe_load(f.read()) or {}
            except Exception:
                d = {}
            d["storage-method"] = str(self.lp_storage.currentData())
            d["server"] = self.lp_server.text().strip() or "global"
            groups = [g.strip() for g in
                      self.lp_groups.text().replace("，", ",").split(",")
                      if g.strip()]
            d["default-groups"] = groups or ["default"]
            if d["storage-method"] == "mysql":
                d.setdefault("data-source", {})
                d["data-source"]["mysql"] = {
                    "host": self.lp_mysql_host.text().strip() or "127.0.0.1",
                    "port": int(self.lp_mysql_port.value()),
                    "database": self.lp_mysql_db.text().strip()
                    or "luckperms",
                    "username": self.lp_mysql_user.text().strip() or "root",
                    "password": self.lp_mysql_pwd.text(),
                }
            cfg_dir = os.path.dirname(path)
            os.makedirs(cfg_dir, exist_ok=True)
            if os.path.isfile(path):
                try:
                    shutil.copy2(path, path + ".bak")
                except Exception:
                    pass
            with open(path, "w", encoding="utf-8", newline="") as f:
                yaml.safe_dump(d, f, allow_unicode=True,
                               default_flow_style=False, sort_keys=False)
            QMessageBox.information(
                self, "已保存",
                "已写入 LuckPerms 配置：\n%s\n\n"
                "重启服务器或执行 /lp reload 后生效。"
                % path)
        except Exception as e:
            QMessageBox.critical(self, "保存失败",
                                 "写 LuckPerms 配置失败：\n%r" % (e,))

    def _plugin_cfg_box(self):
        """检测 EssentialsX / LuckPerms，提供一键生成基础配置。"""
        gb = QGroupBox("插件配置（基础版）")
        gv = QVBoxLayout(gb)

        tip = QLabel(
            "检测到常用插件时可一键生成基础配置。\n"
            "更完整的配置会在插件首次启动时自动补齐；"
            "已存在的配置文件不会被覆盖。")
        tip.setWordWrap(True)
        tip.setStyleSheet(_muted())
        gv.addWidget(tip)

        found = False
        pd = self._plugins_dir_here()
        names = []
        try:
            if os.path.isdir(pd):
                names = [f.lower() for f in os.listdir(pd)
                         if f.lower().endswith(".jar")
                         or f.lower().endswith(".jar.disabled")]
        except Exception:
            names = []

        def _has(prefix):
            for n in names:
                if n.startswith(prefix):
                    return True
            return False

        # EssentialsX
        if _has("essentialsx"):
            found = True
            row = QHBoxLayout()
            row.addWidget(QLabel("已安装 <b>EssentialsX</b>"))
            row.addStretch(1)
            b = QPushButton("生成基础配置")
            b.clicked.connect(self._gen_essentialsx_cfg)
            row.addWidget(b)
            gv.addLayout(row)

        # LuckPerms
        if _has("luckperms"):
            found = True
            row = QHBoxLayout()
            row.addWidget(QLabel("已安装 <b>LuckPerms</b>"))
            row.addStretch(1)
            b = QPushButton("生成基础配置")
            b.clicked.connect(self._gen_luckperms_cfg)
            row.addWidget(b)
            gv.addLayout(row)

        if not found:
            gv.addWidget(QLabel(
                "（没检测到 EssentialsX / LuckPerms。"
                "装好插件后回到这里可一键生成配置。）"))

        row = QHBoxLayout()
        b_open = QPushButton("打开插件配置文件夹")
        b_open.setToolTip("打开服务器 config/ 目录（所有插件的配置都在这里）")
        b_open.clicked.connect(
            lambda: _open_in_explorer(os.path.join(
                self.server_dir, "config")))
        row.addWidget(b_open)
        row.addStretch(1)
        gv.addLayout(row)
        return gb

    def _gen_plugin_cfg(self, sub, filename, content, label):
        """生成插件基础配置（不存在才写）。"""
        cfg_dir = os.path.join(self.server_dir, "config", sub)
        try:
            os.makedirs(cfg_dir, exist_ok=True)
        except Exception as e:
            QMessageBox.critical(self, "失败", repr(e))
            return
        path = os.path.join(cfg_dir, filename)
        if os.path.isfile(path):
            QMessageBox.information(
                self, "已存在",
                "%s 的配置文件已经存在：\n%s\n\n"
                "为避免覆盖你的自定义配置，本次没有写入。"
                % (label, path))
            return
        try:
            with open(path, "w", encoding="utf-8") as f:
                f.write(content)
            QMessageBox.information(
                self, "完成",
                "已生成 %s 基础配置：\n%s\n\n"
                "插件首次启动时会把其余配置项自动补齐。"
                % (label, path))
        except Exception as e:
            QMessageBox.critical(self, "失败", repr(e))

    def _gen_essentialsx_cfg(self):
        self._gen_plugin_cfg(
            "EssentialsX", "config.yml",
            "# EssentialsX 基础配置（由 MCSM 生成）\n"
            "# 更完整的字段会在插件首次启动时自动补齐。\n"
            "ops: []\n"
            "motd: '&a欢迎来到本服务器'\n"
            "hide-display-name-in-vanish: false\n",
            "EssentialsX")

    def _gen_luckperms_cfg(self):
        self._gen_plugin_cfg(
            "LuckPerms", "luckperms.yml",
            "# LuckPerms 基础配置（由 MCSM 生成）\n"
            "# 更完整的字段会在插件首次启动时自动补齐。\n"
            "storage-method: h2\n"
            "sync-min-interval: 30\n",
            "LuckPerms")

    # ---------- 模组页签的新动作 ----------
    def _mods_dir_here(self):
        d = os.path.join(self.server_dir, "mods")
        if not os.path.isdir(d):
            d2 = os.path.join(self.server_dir, "server", "mods")
            if os.path.isdir(d2):
                d = d2
        return d

    def _add_mod_here(self):
        """在这里直接搜装模组（装完自动刷新列表）。

        ★ V1.7.2-2：同 _add_plugin_here —— self._mod_dialog 持有
        引用 + try/except + raise_/activateWindow，杜绝"点击无反应"。
        """
        try:
            if (self.entry.get("loader") or "") == "paper":
                QMessageBox.information(
                    self, "这是插件服",
                    "Paper 装的是插件不是模组。\n\n"
                    "要用模组的话，得把核心换成 Fabric / Forge。")
                return
            self._mod_dialog = _themed(ModBrowseDialog(self.entry, self))
            self._mod_dialog.show()
            self._mod_dialog.raise_()
            self._mod_dialog.activateWindow()
            self._mod_dialog.finished.connect(
                lambda _res: self._refresh_mods_here())
        except Exception as e:
            import traceback as _tb
            try:
                _tb.print_exc()
            except Exception:
                pass
            QMessageBox.critical(
                self, "打开模组搜索失败",
                "错误详情：%r" % (e,))

    def _refresh_mods_here(self):
        try:
            self.mod_mods = modman.scan_mods(self.server_dir)
        except Exception:
            self.mod_mods = []
        self.mod_table.setRowCount(0)
        for m in self.mod_mods:
            r = self.mod_table.rowCount()
            self.mod_table.insertRow(r)
            self.mod_table.setItem(r, 0, QTableWidgetItem(
                "启用" if m.get("enabled", True) else "禁用"))
            self.mod_table.setItem(r, 1,
                                   QTableWidgetItem(m.get("name", "?")))
            self.mod_table.setItem(r, 2,
                                   QTableWidgetItem(m.get("version", "")))
            self.mod_table.setItem(r, 3, QTableWidgetItem(
                os.path.basename(m.get("path", ""))))

    def _update_loader_here(self):
        """更新 Fabric / Forge / NeoForge / Paper 核心。"""
        # 带上这个服保存的 Java 路径（Forge/NeoForge 安装必须有 Java）
        e = dict(self.entry)
        try:
            jp = (self.saved.get("java")
                  or (self.java_pick.path()
                      if hasattr(self, "java_pick") else ""))
            if jp:
                e["java_path"] = jp
        except Exception:
            pass
        dlg = _themed(LoaderUpdateDialog(e, self))
        if dlg.exec():
            self._log("[加载器] 已更新，重启服务端生效")

    def _update_fapi_here(self):
        """单独更新 Fabric API（旧文件留底）。"""
        loader = (self.entry.get("loader") or "").lower()
        if loader in ("paper", "vanilla", "unknown"):
            QMessageBox.information(
                self, "用不上",
                "Fabric API 只有 Fabric 服需要。\n"
                "当前是 %s。" % (loader or "未知"))
            return
        md = self._mods_dir_here()
        if not os.path.isdir(md):
            QMessageBox.information(self, "没有 mods 目录",
                                    "服里还没装过模组。")
            return
        cur = loaderup.find_fabric_api(md)
        txt = ("当前：%s\n\n" % (cur.get("version") or "未安装")
               if cur else "没找到已装的 Fabric API，将直接装最新。\n\n")
        r = QMessageBox.question(
            self, "更新 Fabric API",
            txt + "会把旧的改名成 .old 留着，不会直接删。\n继续？")
        if r != QMessageBox.Yes:
            return

        self.btn_up = getattr(self, "btn_up", None)
        w = _track(self, Worker(self._do_fapi, {"mods_dir": md}, self))
        w.log.connect(self._log)
        w.done.connect(self._on_fapi)
        self._fapi_worker = w
        w.start()

    def _do_fapi(self, mods_dir=None, on_log=None):
        return loaderup.update_fabric_api(
            mods_dir,
            (self.entry.get("version") or "").strip() or None,
            (self.entry.get("loader") or "").strip() or None,
            on_log=on_log)

    def _on_fapi(self, ok, res):
        if not ok or not (res or {}).get("ok"):
            QMessageBox.warning(
                self, "没装上",
                "%r" % ((res or {}).get("reason") or res,))
            return
        self._refresh_mods_here()
        QMessageBox.information(
            self, "完成",
            "Fabric API -> %s\n\n"
            "旧的已留成 .old，出问题可以换回来。\n"
            "重启服务端生效。" % (res.get("version") or "最新"))


    def _log(self, m):
        """ServerConfigDialog 自己的日志输出。

        之前 open_log_dir() 里调了 self._log()，但本类没这个方法
        （_log 在 CreateDialog / ModDialog / BackupDialog 里），
        一点『打开日志文件夹』就会 AttributeError。现在补上。
        """
        try:
            self.core_log.setVisible(True)
            self.core_log.appendPlainText(str(m))
        except Exception:
            pass

    def open_log_dir(self):
        """打开日志文件夹 / latest.log。"""
        for d in (os.path.join(self.server_dir, "logs"),
                  os.path.join(self.server_dir, "server", "logs")):
            if os.path.isdir(d):
                _open_in_explorer(d)
                self._log("[提示] 日志目录: %s" % d)
                return
        for d in (self.server_dir,
                  os.path.join(self.server_dir, "server")):
            lp = os.path.join(d, "latest.log")
            if os.path.isfile(lp):
                _open_in_explorer(lp)
                self._log("[提示] 日志文件: %s" % lp)
                return
        _open_in_explorer(self.server_dir)
        self._log("[提示] 没找到 logs/ 或 latest.log，"
                  "已打开服务器目录")

    def _cur_mod(self):
        r = self.mod_table.currentRow()
        if r < 0 or r >= len(self.mod_mods):
            return None
        return self.mod_mods[r]

    def _toggle_mod(self, enable):
        m = self._cur_mod()
        if not m:
            QMessageBox.information(self, "提示", "先选中一个 mod")
            return
        try:
            modman.set_enabled(m["path"], enable)
            self._need_rescan = True
            QMessageBox.information(
                self, "完成",
                "已%s: %s\n\n重启服务器后生效。"
                % ("启用" if enable else "禁用", m.get("name")))
        except Exception as e:
            QMessageBox.critical(self, "失败", repr(e))

    def _open_mods_here(self):
        sd = self.entry.get("server_dir") or self.entry["folder"]
        _open_in_explorer(os.path.join(sd, "mods"))

    def _open_mod_dialog(self):
        ModDialog(self.entry, self).exec()
        self._need_rescan = True

    # ---------------- 模组配置页签 ----------------
    def _modconf_tab(self):
        """给"装了还要配"的模组生成配置。

        为什么要这个页签：
          ServerCore 装上只是能用，动态视距/模拟距离/刷怪上限
          全写在 config 里。不配就用默认值，等于白装一半。
          用户要求能在『服务器配置』里直接配，而不是自己找文件改。
        """
        from .core import modconf
        w = QWidget(self)
        self._w_modconf = w
        v = QVBoxLayout(w)

        sd = self.entry.get("server_dir") or self.entry["folder"]
        self._modconf_dir = sd

        self._mc_widgets = {}
        found_any = False
        for spec in modconf.CONFIGURABLE_MODS:
            installed = False
            try:
                installed = bool(spec["check"](sd))
            except Exception:
                installed = False
            found_any = found_any or installed

            if not installed:
                # ★ V1.9.2：未检测到的模组也显示卡片（与插件配置页
                #   的 LuckPerms / EssentialsX 卡片风格一致）：
                #   提示文字 + 「打开模组配置文件夹」按钮。
                gb = QGroupBox("%s（未安装）" % (spec["name"]))
                g = QVBoxLayout(gb)
                g.addWidget(QLabel(
                    "未检测到 %s。\n在『模组』页签装上后，"
                    "这里就能直接配它。" % (spec["name"])))
                row = QHBoxLayout()
                b = QPushButton("打开模组配置文件夹")
                b.setToolTip("打开服务器 config/ 目录"
                             "（所有模组配置都在这里）")
                b.clicked.connect(lambda: _open_in_explorer(
                    os.path.join(sd, "config")))
                row.addWidget(b)
                row.addStretch(1)
                g.addLayout(row)
                v.addWidget(gb)
                continue

            gb = QGroupBox("%s" % (spec["name"]))
            f = QFormLayout(gb)

            f.addRow(QLabel(spec.get("help", "")))

            # 预设
            cb = QComboBox()
            for k, p in spec["presets"].items():
                cb.addItem(p["label"], k)
            # ★ V1.9.6：默认选中"均衡（推荐）"（若存在），
            #   与下方初始渲染基准 balanced 保持一致；
            #   不加这个，下拉显示"保守"但数值却是均衡档，看着像没切换。
            cb.blockSignals(True)
            try:
                j = cb.findData("balanced")
                if j < 0:
                    j = cb.findData("default")
                if j >= 0:
                    cb.setCurrentIndex(j)
            finally:
                cb.blockSignals(False)
            f.addRow("预设:", cb)
            self._mc_widgets["preset"] = cb

            # ★ V1.7.0-4：按模组找配置文件（ServerCore / SVC 路径不同）
            if spec["key"] == "simple-voice-chat":
                cur, rel = modconf.find_svc_config(sd)
            else:
                cur, rel = modconf.find_servercore_config(sd)
            self._mc_widgets["_spec"] = spec
            f.addRow("配置文件:",
                     QLabel(cur or ("%s（还没生成）" % rel)))

            def _spin(key, lo, hi, val, suffix=""):
                sp = QSpinBox()
                sp.setRange(lo, hi)
                sp.setValue(int(val))
                if suffix:
                    sp.setSuffix(suffix)
                self._mc_widgets[key] = sp
                return sp

            rd = None
            try:
                rd = spec["read"](sd)
            except Exception:
                rd = None
            # ★ V1.7.0-4：不同模组预设 key 不同（ServerCore 用 balanced，
            #   SVC 用 default），按 spec 取第一个可用预设
            d = dict(spec["presets"].get("balanced")
                     or spec["presets"].get("default") or {})
            if rd:
                d.update(rd)

            if spec["key"] == "simple-voice-chat":
                # ---- Simple Voice Chat 专用字段（★ V1.7.0-4）----
                def _svc_spin(key, lo, hi, val, suffix=""):
                    sp = QSpinBox()
                    sp.setRange(lo, hi)
                    sp.setValue(int(val))
                    if suffix:
                        sp.setSuffix(suffix)
                    self._mc_widgets[key] = sp
                    return sp

                f.addRow("语音端口（默认 24454）:",
                         _svc_spin("svc_port", 1024, 65535,
                                   d.get("port", 24454)))
                f.addRow("语音距离（格，默认 8）:",
                         _svc_spin("svc_distance", 1, 64,
                                   d.get("voice_distance", 8)))
                f.addRow("广播范围（格，默认 64）:",
                         _svc_spin("svc_broadcast", 8, 512,
                                   d.get("broadcast_range", 64)))
                self._mc_widgets["svc_enable"] = self._mk_check(
                    "启用语音聊天（关掉后进服不加载语音）",
                    bool(d.get("enable", True)))
                f.addRow("", self._mc_widgets["svc_enable"])
                # ★ V1.8.0：分组功能开关（对讲组）
                self._mc_widgets["svc_groups"] = self._mk_check(
                    "启用语音分组（对讲组 / disable_groups=false）",
                    bool(d.get("enable_groups", True)))
                f.addRow("", self._mc_widgets["svc_groups"])
            else:
                self._mc_widgets["dynamic"] = self._mk_check(
                    "卡了自动降视距/模拟距离/刷怪上限",
                    bool(d.get("dynamic", True)))
                f.addRow("", self._mc_widgets["dynamic"])
                f.addRow("目标 MSPT（越小越激进）:",
                         _spin("target_mspt", 20, 80,
                               d.get("target_mspt", 40), " ms"))
                f.addRow("模拟距离（正常/最低）:",
                         self._two(_spin("sim_default", 2, 32,
                                         d.get("sim_default", 8)),
                                   _spin("sim_min", 2, 32,
                                         d.get("sim_min", 6))))
                f.addRow("区块刻距离（正常/最低）:",
                         self._two(_spin("chunk_tick_default", 2, 32,
                                         d.get("chunk_tick_default", 8)),
                                   _spin("chunk_tick_min", 1, 32,
                                         d.get("chunk_tick_min", 5))))
                f.addRow("刷怪上限%（正常/最低）:",
                         self._two(_spin("mobcap_max", 10, 100,
                                         d.get("mobcap_max", 100)),
                                   _spin("mobcap_min", 10, 100,
                                         d.get("mobcap_min", 70))))
                def _dspin(key, lo, hi, val, step=0.1, suffix=""):
                    sp = QDoubleSpinBox()
                    sp.setRange(lo, hi)
                    sp.setSingleStep(step)
                    sp.setDecimals(1)
                    sp.setValue(float(val))
                    if suffix:
                        sp.setSuffix(suffix)
                    self._mc_widgets[key] = sp
                    return sp

                f.addRow("自动保存间隔:",
                         _spin("autosave", 60, 3600,
                               d.get("autosave", 600), " 秒"))
                f.addRow("经验球合并概率:",
                         _spin("xp_merge", 1, 100, d.get("xp_merge", 20)))
                f.addRow("经验球合并半径:",
                         _dspin("xp_radius", 0.0, 5.0,
                                d.get("xp_radius", 1.0)))
                f.addRow("掉落物合并半径:",
                         _dspin("item_radius", 0.0, 5.0,
                                d.get("item_radius", 1.0)))

                self._mc_widgets["lobotomize"] = self._mk_check(
                    "村民发呆优化（交易大厅很有效）",
                    bool(d.get("lobotomize", True)))
                f.addRow("", self._mc_widgets["lobotomize"])
                f.addRow("村民优化间隔:",
                         _spin("lobotomize_interval", 1, 200,
                               d.get("lobotomize_interval", 20), " tick"))

                self._mc_widgets["prevent_unloaded"] = self._mk_check(
                    "防止玩家走进未加载区块",
                    bool(d.get("prevent_unloaded", True)))
                f.addRow("", self._mc_widgets["prevent_unloaded"])

                # ★ 动态视距控制
                self._mc_widgets["allow_dynamic_view"] = self._mk_check(
                    "允许动态降视距（会接管 view-distance）",
                    bool(d.get("allow_dynamic_view", False)))
                f.addRow("", self._mc_widgets["allow_dynamic_view"])
                f.addRow("视距（正常/最低）:",
                         self._two(_spin("view_default", 2, 32,
                                         d.get("view_default", 10)),
                                   _spin("view_min", 2, 32,
                                         d.get("view_min", 6))))

            def _apply_preset(_i=None, _cb=cb, _spec=spec):
                # ★ V1.9.6：_spec=spec 用默认参数捕获当前循环的模组。
                #   原来直接闭包引用循环变量 spec —— for 循环结束后
                #   spec 已经变成最后一个模组（Simple Voice Chat），
                #   在 ServerCore 卡片上切换预设时查的是 SVC 的 presets，
                #   永远取不到 aggressive/light/balanced → 全部落回默认值，
                #   表现就是"保守/均衡/激进三档数值完全相同"。
                k = _cb.currentData()
                p = _spec["presets"].get(k) or {}
                g = self._mc_widgets.get
                try:
                    # ★ V1.7.0-4：SVC 预设
                    if g("svc_port"):
                        g("svc_port").setValue(int(p.get("port", 24454)))
                    if g("svc_distance"):
                        g("svc_distance").setValue(
                            int(p.get("voice_distance", 8)))
                    if g("svc_broadcast"):
                        g("svc_broadcast").setValue(
                            int(p.get("broadcast_range", 64)))
                    if g("svc_enable"):
                        g("svc_enable").setChecked(
                            bool(p.get("enable", True)))
                    if g("svc_groups"):
                        g("svc_groups").setChecked(
                            bool(p.get("enable_groups", True)))
                    if g("dynamic"):
                        g("dynamic").setChecked(bool(p.get("dynamic", True)))
                    if g("target_mspt"):
                        g("target_mspt").setValue(int(p.get("target_mspt", 40)))
                    if g("sim_default"):
                        g("sim_default").setValue(int(p.get("sim_default", 8)))
                    if g("sim_min"):
                        g("sim_min").setValue(int(p.get("sim_min", 6)))
                    if g("chunk_tick_default"):
                        g("chunk_tick_default").setValue(
                            int(p.get("chunk_tick_default", 8)))
                    if g("chunk_tick_min"):
                        g("chunk_tick_min").setValue(
                            int(p.get("chunk_tick_min", 5)))
                    if g("mobcap_max"):
                        g("mobcap_max").setValue(int(p.get("mobcap_max", 100)))
                    if g("mobcap_min"):
                        g("mobcap_min").setValue(int(p.get("mobcap_min", 70)))
                    if g("autosave"):
                        g("autosave").setValue(int(p.get("autosave", 600)))
                    if g("xp_merge"):
                        g("xp_merge").setValue(int(p.get("xp_merge", 20)))
                    if g("lobotomize"):
                        g("lobotomize").setChecked(bool(p.get("lobotomize", True)))
                    if g("prevent_unloaded"):
                        g("prevent_unloaded").setChecked(
                            bool(p.get("prevent_unloaded", True)))
                    if g("xp_radius"):
                        g("xp_radius").setValue(float(p.get("xp_radius", 1.0)))
                    if g("item_radius"):
                        g("item_radius").setValue(float(p.get("item_radius", 1.0)))
                    if g("lobotomize_interval"):
                        g("lobotomize_interval").setValue(
                            int(p.get("lobotomize_interval", 20)))
                    if g("allow_dynamic_view"):
                        g("allow_dynamic_view").setChecked(
                            bool(p.get("allow_dynamic_view", False)))
                    if g("view_default"):
                        g("view_default").setValue(int(p.get("view_default", 10)))
                    if g("view_min"):
                        g("view_min").setValue(int(p.get("view_min", 6)))
                except Exception:
                    pass

            cb.currentIndexChanged.connect(_apply_preset)

            bt = QPushButton("保存这个模组的配置")
            bt.clicked.connect(self._save_modconf)
            f.addRow("", bt)
            self._mc_lbl = QLabel("")
            self._mc_lbl.setWordWrap(True)
            self._mc_lbl.setStyleSheet(_muted())
            f.addRow("", self._mc_lbl)
            v.addWidget(gb)

        if not found_any:
            v.addWidget(QLabel(
                "尚未安装 ServerCore / Simple Voice Chat —— "
                "在『模组』页签装上后，上方卡片即可直接配置。"))
        v.addStretch(1)

        sw = QScrollArea()
        sw.setWidgetResizable(True)
        sw.setWidget(w)
        return sw

    def _mk_check(self, text, checked):
        cb = QCheckBox(text)
        cb.setChecked(bool(checked))
        return cb

    def _two(self, a, b):
        w = QWidget()
        h = QHBoxLayout(w)
        h.setContentsMargins(0, 0, 0, 0)
        h.addWidget(a)
        h.addWidget(QLabel("/"))
        h.addWidget(b)
        h.addStretch(1)
        return w

    def _save_modconf(self):
        """把界面上的值写成模组配置文件。"""
        from .core import modconf
        spec = self._mc_widgets.get("_spec")
        if not spec:
            return
        g = self._mc_widgets.get
        # ★ V1.7.0-4：按模组构造保存参数（ServerCore / SVC 字段不同）
        if spec["key"] == "simple-voice-chat":
            opts = {
                "port": int(g("svc_port").value()) if g("svc_port") else 24454,
                "voice_distance": int(g("svc_distance").value())
                if g("svc_distance") else 8,
                "broadcast_range": int(g("svc_broadcast").value())
                if g("svc_broadcast") else 64,
                "enable": bool(g("svc_enable").isChecked())
                if g("svc_enable") else True,
                # ★ V1.8.0：分组功能开关
                "enable_groups": bool(g("svc_groups").isChecked())
                if g("svc_groups") else True,
            }
        else:
            opts = {
                "dynamic": bool(g("dynamic").isChecked()) if g("dynamic") else True,
                "target_mspt": int(g("target_mspt").value()) if g("target_mspt") else 40,
                "sim_default": int(g("sim_default").value()) if g("sim_default") else 8,
                "sim_min": int(g("sim_min").value()) if g("sim_min") else 6,
                "chunk_tick_default": int(g("chunk_tick_default").value())
                if g("chunk_tick_default") else 8,
                "chunk_tick_min": int(g("chunk_tick_min").value())
                if g("chunk_tick_min") else 5,
                "mobcap_max": int(g("mobcap_max").value()) if g("mobcap_max") else 100,
                "mobcap_min": int(g("mobcap_min").value()) if g("mobcap_min") else 70,
                "autosave": int(g("autosave").value()) if g("autosave") else 600,
                "xp_merge": int(g("xp_merge").value()) if g("xp_merge") else 20,
                "item_merge": 1.0, # 兼容旧字段
                "xp_radius": float(g("xp_radius").value()) if g("xp_radius") else 1.0,
                "item_radius": float(g("item_radius").value()) if g("item_radius") else 1.0,
                "lobotomize": bool(g("lobotomize").isChecked())
                if g("lobotomize") else True,
                "lobotomize_interval": int(g("lobotomize_interval").value())
                if g("lobotomize_interval") else 20,
                "prevent_unloaded": bool(g("prevent_unloaded").isChecked())
                if g("prevent_unloaded") else True,
                "allow_dynamic_view": bool(g("allow_dynamic_view").isChecked())
                if g("allow_dynamic_view") else False,
                "view_default": int(g("view_default").value()) if g("view_default") else 10,
                "view_min": int(g("view_min").value()) if g("view_min") else 6,
            }
        # 最低值不能比正常值还大，否则模组会在两头来回跳
        opts["sim_min"] = min(opts.get("sim_min", 6), opts.get("sim_default", 8))
        opts["chunk_tick_min"] = min(opts.get("chunk_tick_min", 5),
                                     opts.get("chunk_tick_default", 8))
        opts["mobcap_min"] = min(opts.get("mobcap_min", 70),
                                 opts.get("mobcap_max", 100))
        r = spec["apply"](self._modconf_dir, opts)
        lbl = getattr(self, "_mc_lbl", None)
        if not r.get("ok"):
            if lbl:
                lbl.setText("保存失败：%s" % (r.get("err") or "未知原因"))
            QMessageBox.warning(self, "保存失败",
                                "写不进配置文件：\n%s"
                                % (r.get("err") or "未知原因"))
            return
        msg = "已写入 %s" % r.get("rel")
        if r.get("backup"):
            msg += "\n（旧配置已备份为 .bak）"
        if spec["key"] == "simple-voice-chat":
            msg += "\n重启服务器后生效（语音端口改变时，客户端语音设置里也要填同一端口）。"
        else:
            msg += "\n服务器里执行 /servercore reload 即可生效，不用重启。"
        if lbl:
            lbl.setText(msg)
        QMessageBox.information(self, "已保存", msg)

    # ---------------- 登录页签（★ V1.3.0 多源共存） ----------------
    def _auth_tab(self):
        w = QWidget(self)
        self._w_auth = w
        v = QVBoxLayout(w)

        g1 = QGroupBox("验证模式")
        f = QFormLayout(g1)
        self.cb_auth_mode = QComboBox()
        self.cb_auth_mode.addItem("正版验证 (Mojang)", "mojang")
        self.cb_auth_mode.addItem("离线模式 (无验证)", "offline")
        self.cb_auth_mode.addItem(
            "多源共存 (正版 + 第三方外置登录)", "multi")
        cur = self.saved.get("auth_type", "offline")
        i = self.cb_auth_mode.findData(cur)
        self.cb_auth_mode.setCurrentIndex(i if i >= 0 else 1)
        f.addRow("验证模式:", self.cb_auth_mode)
        v.addWidget(g1)

        g2 = QGroupBox("第三方源配置（仅『多源共存』生效，可多选）")
        self._w_auth_g2 = g2
        f2 = QFormLayout(g2)
        # ★ V1.7.0-2：单选下拉 -> 多选复选框（可同时勾 LittleSkin / MUA /
        #   自定义），勾选预设源时自动记录其 Yggdrasil API 地址。
        self.chk_auth_ls = QCheckBox("LittleSkin")
        self.chk_auth_ls.setToolTip("https://littleskin.cn/api/yggdrasil")
        f2.addRow("", self.chk_auth_ls)
        self.chk_auth_mua = QCheckBox("MUA Union")
        self.chk_auth_mua.setToolTip(
            "https://skin.mualliance.ltd/api/union/yggdrasil")
        f2.addRow("", self.chk_auth_mua)
        h_cus = QHBoxLayout()
        self.chk_auth_custom = QCheckBox("自定义")
        self.ed_auth_url = QLineEdit()
        self.ed_auth_url.setPlaceholderText(
            "https://你的皮肤站/api/yggdrasil")
        self.ed_auth_url.setEnabled(False)
        h_cus.addWidget(self.chk_auth_custom)
        h_cus.addWidget(self.ed_auth_url, 1)
        f2.addRow("", h_cus)
        # 勾选自定义 -> 输入框可用；取消 -> 置灰并清空
        self.chk_auth_custom.toggled.connect(
            lambda on: (self.ed_auth_url.setEnabled(on),
                        (self.ed_auth_url.setText("") if not on else None)))
        v.addWidget(g2)

        tip = QLabel(
            "多源共存 = 正版玩家直接进，第三方皮肤站（LittleSkin / "
            "MUA Union 等）玩家也能进。\n\n"
            "首次启动服务器时本软件会自动：\n"
            "· Paper 服：下载 MultiLogin 插件到 plugins/\n"
            "· Forge / Fabric / NeoForge 服：下载 MultiYggdrasil "
            "模组到 mods/ 并生成 multi-yggdrasil.ini\n"
            "· 把 server.properties 的 online-mode 设为 true")
        tip.setWordWrap(True)
        tip.setStyleSheet(_muted())
        v.addWidget(tip)

        v.addStretch(1)

        # 从已保存的配置初始化第三方源（★ V1.7.0-2：多选回显；
        #   兼容旧的单值 auth_url 字段）
        urls = self.saved.get("auth_urls") or []
        if not urls:
            old = (self.saved.get("auth_url") or "").strip()
            if old:
                urls = [old]
        ls = "https://littleskin.cn/api/yggdrasil"
        mua = "https://skin.mualliance.ltd/api/union/yggdrasil"
        self.chk_auth_ls.setChecked(ls in urls)
        self.chk_auth_mua.setChecked(mua in urls)
        custom_urls = [u for u in urls
                       if u.strip() and u not in (ls, mua)]
        if custom_urls:
            self.chk_auth_custom.setChecked(True)
            self.ed_auth_url.setText(custom_urls[0])

        self.cb_auth_mode.currentIndexChanged.connect(
            self._auth_mode_changed)
        self._auth_mode_changed()
        return w

    def _auth_urls(self):
        """当前勾选的第三方源地址列表。"""
        out = []
        if self.chk_auth_ls.isChecked():
            out.append("https://littleskin.cn/api/yggdrasil")
        if self.chk_auth_mua.isChecked():
            out.append("https://skin.mualliance.ltd/api/union/yggdrasil")
        if self.chk_auth_custom.isChecked():
            u = self.ed_auth_url.text().strip()
            if u:
                out.append(u)
        return out

    def _auth_mode_changed(self):
        multi = (self.cb_auth_mode.currentData() == "multi")
        g2 = getattr(self, "_w_auth_g2", None)
        if g2 is not None:
            g2.setVisible(multi)

    # ★ V1.7.0-2：多源登录防呆
    @staticmethod
    def _missing_auth_component(server_dir, loader):
        """检查多源登录组件是否已装。没装返回提示文案，装了返回 None。

        Paper -> plugins/MultiLogin；其它加载器 -> mods/ 里
        MultiYggdrasil（AkiYggdrasil 兼容）。
        """
        try:
            from .core import modman as _mm
            if loader == "paper":
                target = os.path.join(server_dir, "plugins")
                keys = ("multilogin", "multilogin-bungee")
            else:
                target = os.path.join(server_dir, "mods")
                keys = ("multiyggdrasil", "akiyggdrasil", "multi-yggdrasil")
            if not os.path.isdir(target):
                return ("未检测到 %s，且 %s 文件夹还不存在。"
                        % ("MultiLogin" if loader == "paper"
                           else "MultiYggdrasil", target))
            for m in _mm.scan_mods_dir(target):
                name = ((m.get("name") or "") + " "
                        + (m.get("filename") or "")).lower()
                if any(k in name for k in keys):
                    return None
            return ("未检测到 %s 模组/插件。"
                    % ("MultiLogin" if loader == "paper"
                       else "MultiYggdrasil"))
        except Exception:
            return None

    def _download_auth_component(self, server_dir, loader):
        """后台下载多源登录组件（Modrinth 引擎）。返回是否发起成功。"""
        from .core import install as _inst
        try:
            mc_v = self.entry.get("version") or ""
            kind = "plugin" if loader == "paper" else "mod"
            comp = _inst.AUTH_COMPONENTS.get(
                "paper" if kind == "plugin" else "mod") or {}
            slug = comp.get("slug", "multiyggdrasil")

            def job(on_log=None):
                return _inst.install_auth_component(
                    server_dir, mc_v, loader, kind, slug, on_log=on_log)

            w = Worker(job, {}, self)
            w.log.connect(lambda m: None)  # 静默，失败信息走返回值
            w.done.connect(
                lambda ok, res: self._on_auth_dl(ok, res))
            _track(self, w)
            w.start()
            return True
        except Exception:
            return False

    def _on_auth_dl(self, ok, res):
        try:
            if ok and res and res.get("ok"):
                QMessageBox.information(
                    self, "下载完成",
                    "%s 已装好，现在保存配置即可生效。"
                    % (res.get("name") or "登录组件"))
            else:
                reason = (res or {}).get("reason") or "未知错误"
                QMessageBox.warning(
                    self, "下载失败",
                    "自动下载失败：%s" % reason)
        except Exception:
            pass

    # ---------------- 备份页签 ----------------
    def _backup_tab(self):
        w = QWidget(self)
        self._w_backup = w
        v = QVBoxLayout(w)

        g1 = QGroupBox("存档（只备份这些）")
        v1 = QVBoxLayout(g1)
        self.bk_list = QListWidget()
        try:
            self.bk_worlds = backup.find_worlds(
                self.entry.get("server_dir") or self.entry["folder"])
        except Exception:
            self.bk_worlds = []
        for wi_ in self.bk_worlds:
            # find_worlds 返回的是 dict
            self.bk_list.addItem("%s    （%d 个 region，%.1f MB）"
                                 % (wi_.get("name", "?"),
                                    wi_.get("regions", 0),
                                    wi_.get("size_mb", 0)))
        if self.bk_worlds:
            self.bk_list.setCurrentRow(0)
        else:
            self.bk_list.addItem("（没有发现存档）")
        v1.addWidget(self.bk_list)
        v.addWidget(g1)

        # ★ V1.1.0：备份格式下拉（默认文件夹）
        gfmt = QGroupBox("备份格式")
        fgf = QHBoxLayout(gfmt)
        self.cb_backup_format = QComboBox()
        self.cb_backup_format.addItem(
            "文件夹（速度快，占用空间大）", "folder")
        self.cb_backup_format.addItem(
            "ZIP（速度慢，省空间）", "zip")
        cur_fmt = self.saved.get("backup_format", "folder")
        i_fmt = self.cb_backup_format.findData(cur_fmt)
        self.cb_backup_format.setCurrentIndex(i_fmt if i_fmt >= 0 else 0)
        fgf.addWidget(self.cb_backup_format, 1)
        fgf.addWidget(QLabel("备份与『立即备份』都按这个格式来"))
        v.addWidget(gfmt)

        hb = QHBoxLayout()
        b_now = QPushButton("立即备份")
        b_now.clicked.connect(self._do_backup_here)
        hb.addWidget(b_now)
        b_full = QPushButton("完整管理（含恢复）")
        b_full.clicked.connect(self._open_backup_dialog)
        hb.addWidget(b_full)
        hb.addStretch(1)
        v.addLayout(hb)

        v.addStretch(1)
        return w

    def _do_backup_here(self):
        if not self.bk_worlds:
            QMessageBox.information(self, "提示", "没有发现存档")
            return
        i = self.bk_list.currentRow()
        if i < 0 or i >= len(self.bk_worlds):
            return
        w = self.bk_worlds[i]
        sd = self.entry.get("server_dir") or self.entry["folder"]
        try:
            r = backup.backup_world(
                w["path"], sd, note="",
                compress=(self.cb_backup_format.currentData() == "zip"))
            QMessageBox.information(
                self, "完成",
                "已备份（%.1f MB）:\n%s"
                % (r.get("size_mb", 0), r.get("path", "")))
        except Exception as e:
            QMessageBox.critical(self, "失败", repr(e))

    def _open_backup_dialog(self):
        BackupDialog(self.entry, self,
                     backup_format=self.cb_backup_format.currentData()
                     or "folder").exec()
        self._need_rescan = True

    def _whitelist_tab(self):
        import json
        w = QWidget(self)
        self._w_white = w
        v = QVBoxLayout(w)
        
        self.white_path = os.path.join(self.server_dir, "whitelist.json")
        self.white_data = []
        if os.path.isfile(self.white_path):
            try:
                with open(self.white_path, "r", encoding="utf-8") as f:
                    self.white_data = json.load(f)
            except Exception:
                pass
                
        v.addWidget(QLabel("在这里添加/删除的玩家，保存时会直接写入 whitelist.json。\n如果服务器已开启白名单，需要重启服务器或发送 reload 生效。"))
        
        self.white_list = QListWidget()
        for p in self.white_data:
            self.white_list.addItem(p.get("name", p.get("uuid", "?")))
        v.addWidget(self.white_list, 1)
        
        hb = QHBoxLayout()
        self.ed_white = QLineEdit()
        self.ed_white.setPlaceholderText("输入玩家名（正版）或 UUID")
        hb.addWidget(self.ed_white, 1)
        b_add = QPushButton("添加")
        def _add():
            name = self.ed_white.text().strip()
            if not name: return
            if any(p.get("name") == name for p in self.white_data):
                QMessageBox.information(self, "已存在", "该玩家已在白名单中。")
                return
            # 如果是正版服务器，白名单需要 uuid，但离线服务器可以只填 name。
            # MC 服务器 reload 时如果缺失 uuid 会自动补全。这里先填个假的，或者不填 uuid。
            self.white_data.append({"name": name, "uuid": "00000000-0000-0000-0000-000000000000"})
            self.white_list.addItem(name)
            self.ed_white.clear()
        b_add.clicked.connect(_add)
        self.ed_white.returnPressed.connect(_add)
        hb.addWidget(b_add)
        
        b_del = QPushButton("移除选中")
        def _del():
            it = self.white_list.currentItem()
            if not it: return
            name = it.text()
            self.white_data = [p for p in self.white_data if p.get("name") != name]
            self.white_list.takeItem(self.white_list.row(it))
        b_del.clicked.connect(_del)
        hb.addWidget(b_del)
        v.addLayout(hb)

        # 保存按钮
        b_save = QPushButton("保存白名单")
        def _save():
            try:
                os.makedirs(os.path.dirname(self.white_path), exist_ok=True)
                with open(self.white_path, "w", encoding="utf-8") as f:
                    json.dump(self.white_data, f, ensure_ascii=False, indent=2)
                QMessageBox.information(self, "完成", "白名单已保存。")
            except Exception as e:
                QMessageBox.warning(self, "保存失败", repr(e))
        b_save.clicked.connect(_save)
        v.addWidget(b_save)
        
        return w

    def _mk_widget(self, key):
        t = props.KEY_TYPE.get(key, "str")
        cur = self.values.get(key, "")
        if t == "bool":
            # ★ V1.5.0：布尔值用 QCheckBox（原来下拉"启用/禁用"）
            cb = QCheckBox("启用")
            cb.setChecked(str(cur).strip().lower()
                          in ("true", "1", "yes", "on"))
            return cb
        if t == "enum":
            cb = QComboBox()
            opts = props.enum_options(key)
            for o in opts:
                cb.addItem(props.display(key, o), o)
            if cur:
                i = cb.findData(cur)
                cb.setCurrentIndex(i if i >= 0 else 0)
            cb.setEditable(False)
            return cb
        if t == "int":
            sp = QSpinBox()
            sp.setRange(-1, 2147483647)
            try:
                sp.setValue(int(cur))
            except Exception:
                sp.setValue(0)
            sp.setMinimumWidth(160)
            return sp
        ed = QLineEdit(cur or "")
        ed.setMinimumWidth(260)
        return ed

    def _read_widget(self, key):
        w = self.widgets.get(key)
        if w is None:
            return None
        t = props.KEY_TYPE.get(key, "str")
        if t == "bool":
            return "true" if w.isChecked() else "false"
        if t == "enum":
            d = w.currentData()
            return d if d is not None else w.currentText()
        if t == "int":
            return str(w.value())
        return w.text()

    def _sync_aikar_mem(self, on):
        """勾选 Aikar 时把最大内存同步为最小内存（Aikar 的前提）。

        ★ V1.6.1：两个框单位可能不同，先统一换算成整数 MB 再写入，
          防止把"G 数值"直接当"MB 数值"用。
        """
        if on:
            mb = _mem_mb_of(self.sp_xms, self.cb_xms_unit)
            _set_mem_mb(self.sp_xmx, self.cb_xmx_unit, mb)

    def _init_mem_spin(self, sp, cb, mb):
        """★ V1.6.1：智能回显 —— saved 值是整数 MB。

        能被 1024 整除（且换算后 <=64）→ 以 G 展示（如 4096 -> 4）；
        除不尽（如 3072）→ 以 MB 展示原值。
        """
        if mb > 0 and mb % 1024 == 0 and (mb // 1024) <= 64:
            cb.setCurrentIndex(cb.findData("g"))
            sp.setDecimals(1)
            sp.setSingleStep(0.5)
            sp.setRange(1.0, 64.0)
            sp.setSuffix(" G")
            sp.setValue(round(mb / 1024.0, 1))
        else:
            cb.setCurrentIndex(cb.findData("mb"))
            sp.setDecimals(0)
            sp.setSingleStep(512)
            lo = 512
            if mb and mb < lo:
                lo = mb  # 历史小值原样回显，防止被夹到 512
            sp.setRange(lo, 65536)
            sp.setSuffix(" MB")
            sp.setValue(int(mb))

    # ============ V0.5.0：性能优化（Paper/Spigot YAML）============
    def _yaml_tab(self):
        """把 YAML 核心优化项做成可视化开关。"""
        try:
            import yaml  # noqa: F401
            self._yaml_ok = True
        except Exception:
            self._yaml_ok = False

        w = QWidget()
        self._yaml_root = w  # ★ 挂到 self 防止页签被回收后控件销毁
        v = QVBoxLayout(w)

        self._yaml_path = _yaml_path_for(self.server_dir)
        self._yaml_data = {}
        if self._yaml_path:
            try:
                import yaml
                with open(self._yaml_path, "r",
                          encoding="utf-8") as f:
                    self._yaml_data = yaml.safe_load(f) or {}
            except Exception:
                self._yaml_data = {}
        self._yaml_widgets = {}
        self._yaml_dirty = False

        if not self._yaml_ok:
            v.addWidget(QLabel("未安装 pyyaml，请先运行：\n"
                               "pip install pyyaml"))
            return w
        if not self._yaml_path:
            lbl = QLabel("未找到 Paper/Spigot 配置文件。\n"
                         "仅 Paper / Spigot 服务端支持此项优化，"
                         "请先初始化对应核心。")
            lbl.setWordWrap(True)
            lbl.setStyleSheet(_muted())
            v.addWidget(lbl)
            return w

        tip = QLabel("配置文件：%s\n"
                     "改动会在点『保存』时写回 YAML，原注释全部保留。"
                     % self._yaml_path)
        tip.setWordWrap(True)
        tip.setStyleSheet(_muted())
        v.addWidget(tip)

        f = QFormLayout()
        f.setLabelAlignment(Qt.AlignRight)
        for key, path, typ, name, default, desc in YAML_ITEMS:
            # ★ V1.6.2：单项读取/类型转换异常时跳过该项，
            #   绝不让整个页签/对话框卡死或崩溃
            try:
                cur = _yaml_get(self._yaml_data, path)
                if typ == "bool":
                    ck = QCheckBox()
                    ck.setChecked(bool(cur if cur is not None else default))
                    ck.setToolTip(desc)
                    ck.toggled.connect(self._yaml_mark_dirty)
                    self._yaml_widgets[key] = ck
                    f.addRow(QLabel(name), ck)
                else:
                    sp = QSpinBox()
                    sp.setRange(0, 10000)
                    try:
                        sp.setValue(int(float(
                            cur if cur is not None else default)))
                    except Exception:
                        sp.setValue(int(default))
                    sp.setToolTip(desc)
                    sp.valueChanged.connect(self._yaml_mark_dirty)
                    self._yaml_widgets[key] = sp
                    f.addRow(QLabel(name), sp)
            except Exception:
                continue
        v.addLayout(f)
        return w

    def _yaml_mark_dirty(self):
        self._yaml_dirty = True

    def _yaml_save(self):
        """把性能优化页签的改动写回 YAML 文件（有改动才写）。

        ★ V1.5.0：行级原位替换，保留原有注释；行级替换失败
        才回退 safe_dump（极端情况注释可能丢失）。
        """
        if not getattr(self, "_yaml_ok", False):
            return
        if not getattr(self, "_yaml_path", None):
            return
        if not self._yaml_dirty:
            return
        import yaml
        edits = []
        for key, path, typ, _name, _default, _desc in YAML_ITEMS:
            wd = self._yaml_widgets.get(key)
            if wd is None:
                continue
            val = wd.isChecked() if typ == "bool" else wd.value()
            edits.append((path.split("."), val))
        try:
            _yaml_patch_preserve(self._yaml_path, edits)
            return
        except Exception:
            pass
        # 兜底：行级替换失败才整体重写
        data = dict(self._yaml_data or {})
        for key, path, typ, _name, _default, _desc in YAML_ITEMS:
            wd = self._yaml_widgets.get(key)
            if wd is None:
                continue
            val = wd.isChecked() if typ == "bool" else wd.value()
            _yaml_set(data, path, val)
        with open(self._yaml_path, "w", encoding="utf-8",
                  newline="") as f:
            yaml.safe_dump(data, f, allow_unicode=True,
                           default_flow_style=False, sort_keys=False)

    def do_save(self):
        jpath = self.java_pick.path()
        if not jpath:
            QMessageBox.warning(self, "缺 Java", "请选择 Java")
            return
        need = versions.need_java(self.entry.get("version"))
        okj, msg = javacore.validate(jpath, need)
        if not okj:
            r = QMessageBox.question(
                self, "Java 可能不合适",
                "%s\n\n仍要保存吗？" % msg)
            if r != QMessageBox.Yes:
                return

        # ★ V1.7.0-2：多源登录防呆校验（保存前）
        if (self.cb_auth_mode.currentData() == "multi"):
            urls = self._auth_urls()
            if not urls:
                QMessageBox.warning(
                    self, "未选择第三方源",
                    "『多源共存』至少要勾选一个第三方源 "
                    "（LittleSkin / MUA Union / 自定义）。")
                return
            # 检查登录组件是否真的装上了
            sd0 = self.entry.get("server_dir") or self.entry["folder"]
            ldr0 = (self.entry.get("loader") or "").lower()
            miss = self._missing_auth_component(sd0, ldr0)
            if miss:
                r = QMessageBox.question(
                    self, "未检测到登录组件",
                    "%s\n\n配置可能无法生效。是否自动为您下载？"
                    % miss)
                if r == QMessageBox.Yes:
                    if not self._download_auth_component(sd0, ldr0):
                        QMessageBox.warning(
                            self, "下载失败",
                            "登录组件下载失败，请稍后重试，"
                            "或手动放入 %s 文件夹。"
                            % ("plugins" if ldr0 == "paper" else "mods"))
                        return
                else:
                    return

        for k in self.widgets:
            val = self._read_widget(k)
            if val is not None:
                self.values[k] = val

        try:
            props.save(self.props_path, self.values, self.lines)
        except Exception as e:
            QMessageBox.critical(self, "保存失败", repr(e))
            return

        # ★ V0.5.0：把性能优化页签的改动写回 YAML
        try:
            self._yaml_save()
        except Exception as e:
            QMessageBox.warning(self, "YAML 保存失败", repr(e))

        # 生成 run.bat（★ V1.6.1：统一整数 MB，保持 -Xms4096M 格式）
        sd = self.entry.get("server_dir") or self.entry["folder"]
        L = install.find_launch(sd)
        launch_mode = L.get("launch") or "jar"
        jar = L.get("jar") or ((self.entry.get("jars") or ["server.jar"])[0])
        xms_mb = _mem_mb_of(self.sp_xms, self.cb_xms_unit)
        xmx_mb = _mem_mb_of(self.sp_xmx, self.cb_xmx_unit)
        bat = builder.build_run_bat(
            jpath, jar,
            xms="%dM" % xms_mb,
            xmx="%dM" % xmx_mb,
            launch=launch_mode, args_rel=L.get("args_rel"),
            pause=self.cb_pause.currentData() or "error",
            aikar=self.chk_aikar.isChecked())
        bp = os.path.join(sd, "run.bat")
        try:
            with open(bp, "w", encoding="utf-8", newline="") as f:
                f.write(bat)
        except Exception as e:
            QMessageBox.warning(self, "run.bat 失败", repr(e))
            bp = None

        self.result_info = {
            "java": jpath,
            "xms": xms_mb,
            "xmx": xmx_mb,
            "pause": self.cb_pause.currentData() or "error",
            "aikar": self.chk_aikar.isChecked(),  # ★ 保存 Aikar 开关
            "auto_restart": self.chk_auto_restart.isChecked(),
            "auto_restart_time": (self.time_restart.time().toString("HH:mm")
                                  if self.chk_auto_restart_time.isChecked()
                                  else ""),  # ★ V0.5.0 每日定时重启
            "auto_backup": self.chk_auto_backup.isChecked(),
            "backup_interval": self.sp_bk_interval.value(),
            "backup_format": (self.cb_backup_format.currentData()
                              or "folder"),  # ★ V1.1.0 备份格式
            # ★ V1.3.0 / V1.7.0-2：登录验证（多源共存可多选第三方源）
            "auth_type": (self.cb_auth_mode.currentData() or "offline"),
            "auth_urls": self._auth_urls(),
            "auth_url": (self._auth_urls() or [""])[0],
        }
        tail = ("\n并已生成 run.bat" if bp else "")
        QMessageBox.information(self, "已保存",
                                "server.properties 已保存。%s" % tail)
        self.accept()


class DeleteServerDialog(QDialog):
    """删除整个服务器文件夹。危险操作，多重确认。"""

    def __init__(self, entry, parent=None):
        super().__init__(parent)
        # 窗口图标（跟主程序一致）
        try:
            from .app import app_icon
            self.setWindowIcon(app_icon())
        except Exception:
            pass
        self.entry = entry
        self.setWindowTitle("删除服务器 - %s" % entry.get("name", ""))
        self.resize(600, 460)

        self.server_dir = entry.get("server_dir") or entry["folder"]
        v = QVBoxLayout(self)

        warn = QLabel(
            "⚠ 这会删掉整个服务器文件夹，包括存档、模组、配置。\n"
            "删除后无法恢复（除非你有备份）。")
        warn.setStyleSheet("color:%s; font-weight:bold;" % _on_bg("#c0392b"))
        warn.setWordWrap(True)
        v.addWidget(warn)

        # 信息
        size = install.server_size(self.server_dir)
        nf, nd = install.count_files(self.server_dir)
        box = QGroupBox("将要删除")
        f = QFormLayout(box)
        f.addRow("目录:", QLabel(self.server_dir))
        f.addRow("大小:", QLabel(install.human_size(size)))
        f.addRow("内容:", QLabel("%d 个文件，%d 个文件夹" % (nf, nd)))
        v.addWidget(box)

        # 概览
        self.table = QTableWidget(0, 2)
        self.table.setHorizontalHeaderLabels(["项目", "情况"])
        self.table.verticalHeader().setVisible(False)
        self.table.setEditTriggers(QAbstractItemView.NoEditTriggers)
        hh = self.table.horizontalHeader()
        hh.setSectionResizeMode(0, QHeaderView.ResizeToContents)
        hh.setSectionResizeMode(1, QHeaderView.Stretch)
        v.addWidget(self.table, 1)
        self._fill_overview()

        self.chk_bk = QCheckBox("保留 backups 文件夹（挪到旁边，强烈建议）")
        self.chk_bk.setChecked(True)
        v.addWidget(self.chk_bk)

        self.chk_diag = QCheckBox("删除前先导出一份诊断包（推荐）")
        self.chk_diag.setChecked(True)
        v.addWidget(self.chk_diag)

        self.log = LogView()
        self.log.setReadOnly(True)
        self.log.setMaximumHeight(90)
        v.addWidget(self.log)

        hb = QHBoxLayout()
        self.btn = QPushButton("确认删除")
        self.btn.setStyleSheet("color:%s; font-weight:bold;" % _on_bg("#c0392b"))
        self.btn.clicked.connect(self.do_delete)
        hb.addWidget(self.btn)
        b_kill = QPushButton("结束占用进程")
        b_kill.clicked.connect(self.do_kill)
        hb.addWidget(b_kill)
        hb.addStretch(1)
        v.addLayout(hb)

        bb = QDialogButtonBox(QDialogButtonBox.Close)
        bb.rejected.connect(self.reject)
        v.addWidget(bb)

    def _fill_overview(self):
        sd = self.server_dir
        rows = []
        # 存档不一定叫 world，用扫描器找
        wd = None
        try:
            from .core import scanner as _sc
            ws = _sc.find_worlds(sd, max_depth=2)
            if ws:
                wd = ws[0]
        except Exception:
            wd = None
        if wd is None and os.path.isdir(os.path.join(sd, "world")):
            wd = os.path.join(sd, "world")
        if wd and os.path.isfile(os.path.join(wd, "level.dat")):
            reg = 0
            for base in ("region",
                         os.path.join("dimensions", "minecraft",
                                      "overworld", "region")):
                rd = os.path.join(wd, base)
                if os.path.isdir(rd):
                    try:
                        reg += len([f for f in os.listdir(rd)
                                    if f.endswith(".mca")])
                    except Exception:
                        pass
            rows.append(("存档", "%s（%d 个 region）"
                         % (os.path.basename(wd), reg)))
        else:
            rows.append(("存档", "未发现存档（level.dat）"))
        for sub, label in (("mods", "模组"), ("plugins", "插件")):
            d = os.path.join(sd, sub)
            if os.path.isdir(d):
                try:
                    c = len([f for f in os.listdir(d)
                             if f.lower().endswith(".jar")])
                    rows.append((label, "%d 个" % c))
                except Exception:
                    pass
        bk = os.path.join(sd, "backups")
        if os.path.isdir(bk):
            try:
                c = len(os.listdir(bk))
                rows.append(("备份", "%d 个（勾选保留会挪走）" % c))
            except Exception:
                pass
        cj = install.find_core(sd)
        rows.append(("核心", os.path.basename(cj) if cj else "未识别"))
        for k, val in rows:
            r = self.table.rowCount()
            self.table.insertRow(r)
            self.table.setItem(r, 0, QTableWidgetItem(k))
            self.table.setItem(r, 1, QTableWidgetItem(val))

    def do_kill(self):
        r = QMessageBox.warning(
            self, "结束进程",
            "将结束占用该目录的 java 进程。继续？",
            QMessageBox.Yes | QMessageBox.No, QMessageBox.No)
        if r != QMessageBox.Yes:
            return
        n = install.kill_java_for(
            self.server_dir,
            on_log=lambda m: self.log.appendPlainText(m))
        self.log.appendPlainText("结束 %d 个" % n)

    def do_delete(self):
        name = self.entry.get("name", "")
        # 第一次确认
        r = QMessageBox.warning(
            self, "确认删除",
            "真的要删除服务器「%s」吗？\n\n"
            "目录：%s\n\n"
            "存档、模组、配置会全部消失。"
            % (name, self.server_dir),
            QMessageBox.Yes | QMessageBox.No, QMessageBox.No)
        if r != QMessageBox.Yes:
            return
        # 第二次：输入确认语
        # 以前要求输入服务器名字 —— 名字又长又带连字符，
        # 手打一遍太痛苦。改成固定短句，好打也好认。
        PHRASE = "我确认删除"
        txt, ok2 = QInputDialog.getText(
            self, "再次确认",
            "这是不可恢复的操作。\n\n"
            "请输入「%s」四个字来确认：" % PHRASE)
        if not ok2:
            return
        # 容错：去空格、全角半角、忽略大小写
        typed = (txt or "").strip().replace(" ", "")
        if typed != PHRASE:
            QMessageBox.information(
                self, "已取消",
                "输入不匹配，删除已取消。\n\n"
                "你输入的是：%s" % (txt or "(空)"))
            return

        # 先导出诊断包
        if self.chk_diag.isChecked():
            try:
                folder = install.collect_diag(self.server_dir)
                self.log.appendPlainText("诊断包: %s" % folder)
            except Exception as e:
                self.log.appendPlainText("诊断包失败: %r" % e)

        self.btn.setEnabled(False)
        QApplication.processEvents()
        res = install.delete_server(
            self.server_dir,
            keep_backups=self.chk_bk.isChecked(),
            on_log=lambda m: self.log.appendPlainText(m))
        if res:
            QMessageBox.information(self, "完成", "服务器已删除。")
            self.accept()
        else:
            self.btn.setEnabled(True)
            QMessageBox.warning(
                self, "删除失败",
                "没能完全删除，通常是还有进程占用。\n\n"
                "先点『结束占用进程』，再试一次。")


class DiagDialog(QDialog):
    """导出报错诊断包，方便发给别人排查。"""

    def __init__(self, entry, console_text=None, parent=None):
        super().__init__(parent)
        # 窗口图标（跟主程序一致）
        try:
            from .app import app_icon
            self.setWindowIcon(app_icon())
        except Exception:
            pass
        self.entry = entry
        self.console_text = console_text or ""
        self.setWindowTitle("导出报错诊断 - %s" % entry.get("name", ""))
        self.resize(620, 420)

        self.server_dir = entry.get("server_dir") or entry["folder"]
        v = QVBoxLayout(self)

        v.addWidget(QLabel(
            "会把排查需要的信息收集到一个文件夹里：\n"
            "  · 诊断报告.txt（含 server.properties、"
            "mods/plugins 清单、日志尾部）\n"
            "  · latest.log（原始日志）\n"
            "  · 控制台输出.txt（本软件控制台里的内容）\n\n"
            "把这个文件夹打包发给我，就能定位问题。"))

        self.chk_console = QCheckBox("包含本软件控制台的内容")
        self.chk_console.setChecked(True)
        v.addWidget(self.chk_console)

        self.chk_open = QCheckBox("导出后打开文件夹")
        self.chk_open.setChecked(True)
        v.addWidget(self.chk_open)

        self.log = LogView()
        self.log.setReadOnly(True)
        v.addWidget(self.log, 1)

        hb = QHBoxLayout()
        b = QPushButton("导出诊断包")
        b.clicked.connect(self.do_export)
        hb.addWidget(b)
        bz = QPushButton("打包成 zip")
        bz.clicked.connect(lambda: self.do_export(zip_it=True))
        hb.addWidget(bz)
        hb.addStretch(1)
        v.addLayout(hb)

        bb = QDialogButtonBox(QDialogButtonBox.Close)
        bb.rejected.connect(self.reject)
        v.addWidget(bb)

        self.last_folder = None

    def do_export(self, zip_it=False):
        try:
            extra = (self.console_text
                     if self.chk_console.isChecked() else None)
            folder = install.collect_diag(self.server_dir,
                                          extra_log=extra)
            self.last_folder = folder
            self.log.appendPlainText("已导出: %s" % folder)
            try:
                for f in sorted(os.listdir(folder)):
                    self.log.appendPlainText("  - %s" % f)
            except Exception:
                pass
            out = folder
            if zip_it:
                import shutil as _sh
                zp = folder + ".zip"
                try:
                    if os.path.exists(zp):
                        os.remove(zp)
                    _sh.make_archive(folder, "zip", folder)
                    self.log.appendPlainText("\n已打包: %s" % zp)
                    out = zp
                except Exception as e:
                    self.log.appendPlainText("打包失败: %r" % e)
            if self.chk_open.isChecked():
                _open_in_explorer(out if not zip_it
                                  else os.path.dirname(out))
        except Exception as e:
            self.log.appendPlainText("失败: %r" % e)


class LoaderUpdateDialog(QDialog):
    """更新 Fabric / Forge / NeoForge / Paper。

    只换核心文件 —— 存档、模组、server.properties 全都不动。
    以前要更新只能"删核心重装"，那会把配置折腾一遍。
    """

    def closeEvent(self, e):
        """关闭前先停掉后台线程。

        ★ 不停的话：对话框销毁了、线程还在跑，
          Qt 会直接 abort 整个程序 —— 也就是"点了就闪退"。
        """
        try:
            _stop_threads(self)
        except Exception:
            pass
        try:
            super().closeEvent(e)
        except Exception:
            pass

    def __init__(self, entry, parent=None):
        super().__init__(parent)
        # 窗口图标（跟主程序一致）
        try:
            from .app import app_icon
            self.setWindowIcon(app_icon())
        except Exception:
            pass
        self.entry = entry
        self.server_dir = entry.get("server_dir") or entry["folder"]
        self.setWindowTitle("更新加载器 - %s"
                            % entry.get("name", ""))
        self.resize(640, 500)
        v = QVBoxLayout(self)

        self.cur = loaderup.detect(self.server_dir)
        mc = (entry.get("version") or "").strip()
        loader = (entry.get("loader") or "").strip().lower()

        g = QGroupBox("当前")
        f = QFormLayout(g)
        f.addRow("服务器:", QLabel(entry.get("name", "")))
        f.addRow("MC 版本:", QLabel(mc or "未知"))
        f.addRow("加载器:", QLabel(loader or "未知"))
        f.addRow("当前核心版本:", QLabel(
            (self.cur or {}).get("version") or "没检测到"))
        if self.cur:
            f.addRow("检测依据:", QLabel(
                str(self.cur.get("source") or "")))
        v.addWidget(g)

        v.addWidget(QLabel(
            "只替换核心文件，存档 / 模组 / 配置都不会动。\n"
            "装完需要重启服务端。"))

        # ★ 更新 loader 时顺手把 Fabric API 也更新掉：
        #   Fabric API 是绝大多数模组的前置，loader 升级后它没跟上，
        #   模组可能一起加载失败（用户报过"只更新了 loader，
        #   Fabric API 还是旧版"）。
        self.cb_api = QCheckBox("同时更新 Fabric API 前置模组（推荐）")
        self.cb_api.setChecked(True)
        self.cb_api.setToolTip(
            "Fabric API 是大多数 Fabric 模组的前置。\n"
            "loader 升级后它没跟上，模组可能一起加载失败。\n"
            "只对 Fabric 服显示；旧的会改名 .old 留底。")
        self.cb_api.setVisible(loader == "fabric")
        v.addWidget(self.cb_api)

        self.log = LogView()
        self.log.setReadOnly(True)
        self.log.setVisible(False)
        v.addWidget(self.log, 1)
        self.pb = QProgressBar()
        self.pb.setVisible(False)
        self.pb.setRange(0, 0)
        v.addWidget(self.pb)

        self.hb = QHBoxLayout()
        self.b_up = QPushButton("更新到最新")
        self.b_up.clicked.connect(self.do_update)
        self.hb.addWidget(self.b_up)
        b_close = QPushButton("关闭")
        b_close.clicked.connect(self.reject)
        self.hb.addWidget(b_close)
        v.addLayout(self.hb)

        if loader not in ("fabric", "forge", "neoforge", "paper"):
            self.b_up.setEnabled(False)
            self.log.setVisible(True)
            self.log.setPlainText(
                "这个服的加载器是「%s」，本功能只支持\n"
                "Fabric / Forge / NeoForge / Paper。\n\n"
                "不确定的话，可以用『删除核心』再重新初始化。"
                % (loader or "未知"))

    def do_update(self):
        loader = (self.entry.get("loader") or "").strip().lower()
        mc = (self.entry.get("version") or "").strip()
        if loader in ("forge", "neoforge"):
            r = QMessageBox.question(
                self, "%s 要跑安装器" % loader,
                "更新要跑官方 installer，通常 2-5 分钟。\n"
                "装在后台跑，界面不会卡住。\n\n"
                "新版 Forge/NeoForge 装完不生成 jar，而是用\n"
                "win_args.txt 启动 —— 这是正常的，不是失败。\n\n继续？")
            if r != QMessageBox.Yes:
                return
        self.b_up.setEnabled(False)
        self.log.setVisible(True)
        self.pb.setVisible(True)
        with_api = bool(loader == "fabric"
                        and self.cb_api.isChecked())
        w = _track(self, Worker(
            self._do, {"loader": loader, "mc": mc,
                       "with_api": with_api}, self))
        w.log.connect(self._log)
        w.done.connect(self._done)
        self._w = w
        w.start()

    def _log(self, m):
        self.log.appendPlainText(str(m))

    def _mods_dir(self):
        for cand in (os.path.join(self.server_dir, "mods"),
                     os.path.join(self.server_dir, "server", "mods")):
            if os.path.isdir(cand):
                return cand
        return os.path.join(self.server_dir, "mods")

    def _do(self, loader=None, mc=None, with_api=False, on_log=None):
        log = on_log or (lambda m: None)
        if loader == "fabric":
            r = loaderup.update_fabric(
                self.server_dir, mc, on_log=on_log)
            if r.get("ok") and with_api:
                md = self._mods_dir()
                if not os.path.isdir(md):
                    r["fabric_api"] = {
                        "ok": False,
                        "reason": "没有 mods 目录（这个服还没装过模组）"}
                else:
                    log("")
                    log("=== 同时更新 Fabric API ===")
                    ra = loaderup.update_fabric_api(
                        md, mc, "fabric", on_log=on_log)
                    r["fabric_api"] = ra
                    if not ra.get("ok"):
                        log("[警告] Fabric API 没更新上：%s"
                            % (ra.get("reason") or "未知原因"))
            return r
        if loader in ("forge", "neoforge"):
            jp = None
            try:
                jp = self.entry.get("java_path") or None
            except Exception:
                jp = None
            return loaderup.update_forge(
                self.server_dir, mc, loader=loader,
                java_path=jp, on_log=on_log)
        if loader == "paper":
            return loaderup.update_paper(
                self.server_dir, mc, on_log=on_log)
        return {"ok": False, "err": "不支持的加载器 %s" % loader}

    def _done(self, ok, res):
        self.b_up.setEnabled(True)
        self.pb.setVisible(False)
        if not ok or not (res or {}).get("ok"):
            err = (res or {}).get("err") or res
            QMessageBox.warning(self, "更新失败", "%r" % (err,))
            return
        if res.get("changed") is False:
            QMessageBox.information(self, "不用更新",
                                    "已经是最新版了：%s"
                                    % res.get("version"))
            self.reject()
            return
        extra = ""
        fa = res.get("fabric_api")
        if fa:
            if fa.get("ok"):
                extra = ("\n\nFabric API -> %s"
                         % (fa.get("version") or "最新"))
            else:
                extra = ("\n\n[注意] 加载器已更新，但 Fabric API "
                         "没更新上：\n  %s"
                         % (fa.get("reason") or "未知原因"))
        QMessageBox.information(
            self, "完成",
            "%s -> %s%s\n\n重启服务端生效。"
            % (res.get("old") or "旧版本", res.get("version"), extra))
        self.accept()


class RemoveCoreDialog(QDialog):
    """删除装错的核心。绝不碰存档/模组/配置。"""

    def closeEvent(self, e):
        """关闭前先停掉后台线程。

        ★ 不停的话：对话框销毁了、线程还在跑，
          Qt 会直接 abort 整个程序 —— 也就是"点了就闪退"。
        """
        try:
            _stop_threads(self)
        except Exception:
            pass
        try:
            super().closeEvent(e)
        except Exception:
            pass

    def _open_url(self, url):
        """在系统浏览器里打开链接。

        之前日志区 anchorClicked 连到了 self._open_url()，
        但本类没有（它只在 ModDialog 里），点链接就崩。现在补上。
        """
        try:
            QDesktopServices.openUrl(str(url))
        except Exception:
            pass

    def __init__(self, entry, parent=None):
        super().__init__(parent)
        # 窗口图标（跟主程序一致）
        try:
            from .app import app_icon
            self.setWindowIcon(app_icon())
        except Exception:
            pass
        self.entry = entry
        self.setWindowTitle("删除服务端核心 - %s" % entry.get("name", ""))
        self.resize(620, 480)

        self.server_dir = entry.get("server_dir") or entry["folder"]
        v = QVBoxLayout(self)

        v.addWidget(QLabel(
            "用途：装错核心时清掉，然后重新点『初始化服务端』。\n"
            "只会删除核心 jar 和加载器目录，"
            "存档 / mods / plugins / config / server.properties "
            "全部保留。\n\n"
            "如果提示『文件夹正在使用』：服务器进程可能还没退干净，"
            "先用下面的『结束占用进程』。"))

        self.table = QTableWidget(0, 2)
        self.table.setHorizontalHeaderLabels(["类型", "名称"])
        self.table.verticalHeader().setVisible(False)
        self.table.setEditTriggers(QAbstractItemView.NoEditTriggers)
        hh = self.table.horizontalHeader()
        hh.setSectionResizeMode(0, QHeaderView.ResizeToContents)
        hh.setSectionResizeMode(1, QHeaderView.Stretch)
        v.addWidget(self.table, 1)

        self.files = install.list_core_files(self.server_dir)
        for kind, p in self.files:
            r = self.table.rowCount()
            self.table.insertRow(r)
            self.table.setItem(r, 0, QTableWidgetItem(kind))
            self.table.setItem(r, 1, QTableWidgetItem(os.path.basename(p)))
        if not self.files:
            self.table.setRowCount(1)
            self.table.setItem(0, 0, QTableWidgetItem("-"))
            self.table.setItem(0, 1, QTableWidgetItem("没找到核心文件"))

        self.chk_dirs = QCheckBox("同时删除加载器目录（.fabric / "
                                  ".paper-remapped / .neoforge）")
        self.chk_dirs.setChecked(True)
        v.addWidget(self.chk_dirs)

        self.log = LogView()
        self.log.setReadOnly(True)
        self.log.setMaximumHeight(110)
        # 日志里的 http 链接可以点。
        # ★ 这里必须用 QTextBrowser：QPlainTextEdit 既没有
        #   setOpenExternalLinks 也没有 anchorClicked（前者是 QLabel 的，
        #   后者是 QTextBrowser 的）。以前用 QPlainTextEdit 去调，
        #   一打开这个窗口就 AttributeError 崩。
        self.log.setOpenExternalLinks(False)
        self.log.anchorClicked.connect(
            lambda u: self._open_url(u.toString()))
        v.addWidget(self.log)

        hb = QHBoxLayout()
        self.btn = QPushButton("删除核心")
        self.btn.clicked.connect(self.do_remove)
        self.btn.setEnabled(bool(self.files))
        hb.addWidget(self.btn)
        b_kill = QPushButton("结束占用进程")
        b_kill.setToolTip("结束还在占用这个目录的 java 进程")
        b_kill.clicked.connect(self.do_kill)
        hb.addWidget(b_kill)
        b_rescan = QPushButton("重新检测")
        b_rescan.clicked.connect(self.re_scan)
        hb.addWidget(b_rescan)
        hb.addStretch(1)
        v.addLayout(hb)

        bb = QDialogButtonBox(QDialogButtonBox.Close)
        bb.rejected.connect(self.reject)
        v.addWidget(bb)

    def re_scan(self):
        self.files = install.list_core_files(self.server_dir)
        self.table.setRowCount(0)
        for kind, p in self.files:
            r = self.table.rowCount()
            self.table.insertRow(r)
            self.table.setItem(r, 0, QTableWidgetItem(kind))
            self.table.setItem(r, 1, QTableWidgetItem(os.path.basename(p)))
        if not self.files:
            self.table.setRowCount(1)
            self.table.setItem(0, 0, QTableWidgetItem("-"))
            self.table.setItem(0, 1, QTableWidgetItem("核心已清空"))
        self.btn.setEnabled(bool(self.files))
        pids = install.find_java_pids()
        self.log.appendPlainText(
            "重新检测：%d 个核心文件，%d 个 java 进程"
            % (len(self.files), len(pids)))

    def do_kill(self):
        r = QMessageBox.warning(
            self, "结束进程",
            "将结束还在占用这个目录的 java 进程。\n\n"
            "如果本机还开着别的 MC（客户端或其它服），"
            "也可能一起被结束。\n\n继续？",
            QMessageBox.Yes | QMessageBox.No, QMessageBox.No)
        if r != QMessageBox.Yes:
            return
        self.log.appendPlainText("正在结束占用进程 ...")
        QApplication.processEvents()
        n = install.kill_java_for(
            self.server_dir,
            on_log=lambda m: self.log.appendPlainText(m))
        self.log.appendPlainText("结束 %d 个进程" % n)
        if n:
            QMessageBox.information(
                self, "完成",
                "已结束 %d 个进程。\n\n"
                "等几秒后点『重新检测』再删。" % n)
        else:
            QMessageBox.information(
                self, "没找到",
                "没找到明确占用该目录的 java 进程。\n\n"
                "如果还是删不掉：\n"
                "  - 确认服务器已完全停止\n"
                "  - 关掉资源管理器里打开的相关文件夹\n"
                "  - 或重启电脑后再删")

    def do_remove(self):
        r = QMessageBox.question(
            self, "确认删除",
            "将删除 %d 项核心文件。\n\n"
            "存档和模组不会受影响。\n\n确定？" % len(self.files))
        if r != QMessageBox.Yes:
            return
        r2 = QMessageBox.warning(
            self, "再次确认",
            "删除后需要重新『初始化服务端』才能开服。\n确定继续？",
            QMessageBox.Yes | QMessageBox.No, QMessageBox.No)
        if r2 != QMessageBox.Yes:
            return
        try:
            res = install.remove_core(
                self.server_dir,
                also_dirs=self.chk_dirs.isChecked(),
                on_log=lambda m: self.log.appendPlainText(m))
            rm = res["removed"]
            bl = res["blocked"]
            self.log.appendPlainText(
                "\n结果：删除 %d 项，失败 %d 项" % (len(rm), len(bl)))
            if bl:
                QMessageBox.warning(
                    self, "部分失败",
                    "删掉 %d 项，但有 %d 项删不掉：\n\n%s\n\n"
                    "先点『结束占用进程』，再『重新检测』重试。"
                    % (len(rm), len(bl),
                       "\n".join("  %s - %s" % (n, w)
                                  for _k, n, w in bl)))
                self.re_scan()
            else:
                self.btn.setEnabled(False)
                QMessageBox.information(
                    self, "完成",
                    "已删除 %d 项。\n\n现在可以重新『初始化服务端』，"
                    "选一个别的核心。" % len(rm))
                self.accept()
        except Exception as e:
            QMessageBox.critical(self, "失败", repr(e))



def _esc_html(s):
    """把纯文本转成 HTML 安全字符串（卡片红字原因、标签渲染用）。"""
    return (str(s).replace("&", "&amp;").replace("<", "&lt;")
            .replace(">", "&gt;").replace('"', "&quot;"))


def _inst_hit(slug, title, installed_set):
    """★ V1.7.3-1：已装判定（归一化 contains）。

    以前用精确集合成员匹配：modman 扫不出规范名的模组
    （ferritecore/spark 名字带版本尾巴）就永远判"未装"。
    现在 slug/title 与已装文件名都做归一化（去 - _ . 空格小写），
    用子串包含判断：ferritecore in ferritecore601fabric -> True。
    """
    if not installed_set:
        return False
    n = modman._norm(slug) or modman._norm(title)
    if not n:
        return False
    return any(n in it for it in installed_set)


def _themed(dlg):
    """给对话框套当前主题（对话框内部打开的子窗口也能用）。

    和 MainWindow._style_dlg 是同一件事，只是这里不需要主窗口实例。

    ★ V1.7.3-3：以前 except 静默吞异常 —— 样式生成/应用一旦失败，
    弹窗就保持 Windows 默认样式（按钮没有悬停/按下效果），
    还完全不知道原因。现在异常打印到日志并弹窗提示。
    """
    try:
        from .core import theme as _th
        _th.load()
        dlg.setStyleSheet(_th.stylesheet(None))
        _th.apply_palette(dlg)
    except Exception as ex:
        try:
            import traceback as _tb
            _tb.print_exc()
        except Exception:
            pass
        try:
            QMessageBox.warning(
                dlg, "主题样式应用失败",
                "套用界面主题时出错：%r\n\n"
                "按钮可能显示为系统默认样式，可反馈给作者。" % (ex,))
        except Exception:
            pass
    return dlg


class ModPickDialog(QDialog):
    """初始化前让用户自己勾选要装哪些模组。

    支持：
      · 按分组显示（不至于一大坨）
      · 手动添加任意 Modrinth 模组（粘贴链接或 slug）
    """

    def closeEvent(self, e):
        """关闭前先停掉后台线程。

        ★ 不停的话：对话框销毁了、线程还在跑，
          Qt 会直接 abort 整个程序 —— 也就是"点了就闪退"。
        """
        try:
            _stop_threads(self)
        except Exception:
            pass
        try:
            super().closeEvent(e)
        except Exception:
            pass

    def __init__(self, mc_version, loader, parent=None):
        super().__init__(parent)
        # 窗口图标（跟主程序一致）
        try:
            from .app import app_icon
            self.setWindowIcon(app_icon())
        except Exception:
            pass
        self.setWindowTitle("选择要安装的模组 - %s / %s" % (mc_version, loader))
        self.resize(760, 620)
        self.picked = []
        self.loader = loader
        self.mc_version = mc_version

        v = QVBoxLayout(self)
        v.addWidget(QLabel(
            "版本 <b>%s</b> / 加载器 <b>%s</b><br>"
            "勾选要装的。装错了也能在『模组管理』里禁用。"
            % (mc_version, loader)))

        self.table = QTableWidget(0, 3)
        self.table.setHorizontalHeaderLabels(["装", "模组", "说明"])
        self.table.verticalHeader().setVisible(False)
        self.table.setEditTriggers(QAbstractItemView.NoEditTriggers)
        hh = self.table.horizontalHeader()
        hh.setSectionResizeMode(0, QHeaderView.ResizeToContents)
        hh.setSectionResizeMode(1, QHeaderView.ResizeToContents)
        hh.setSectionResizeMode(2, QHeaderView.Stretch)
        v.addWidget(self.table, 1)

        # ---- 手动添加（解决"内置清单不够"的根本办法）----
        gb = QGroupBox("清单里没有想要的？手动添加")
        hb = QHBoxLayout(gb)
        self.ed_custom = QLineEdit()
        self.ed_custom.setPlaceholderText(
            "粘贴 Modrinth 项目链接，或直接填 slug（如 sodium）")
        self.ed_custom.returnPressed.connect(self._add_custom)
        hb.addWidget(self.ed_custom, 1)
        b_add = QPushButton("添加")
        b_add.clicked.connect(self._add_custom)
        hb.addWidget(b_add)
        v.addWidget(gb)
        v.addWidget(QLabel(
            "提示：去 modrinth.com 找到模组，复制地址栏链接粘进来即可。"
            "版本对不上的会在安装时跳过，不会崩。"))

        h = QHBoxLayout()
        b_all = QPushButton("全选")
        b_all.clicked.connect(lambda: self._set_all(True))
        h.addWidget(b_all)
        b_none = QPushButton("全不选")
        b_none.clicked.connect(lambda: self._set_all(False))
        h.addWidget(b_none)
        b_def = QPushButton("恢复默认")
        b_def.clicked.connect(self._set_default)
        h.addWidget(b_def)
        self.lbl_count = QLabel("")
        self.lbl_count.setStyleSheet(_muted())
        h.addWidget(self.lbl_count)
        h.addStretch(1)
        v.addLayout(h)

        # ★ 冲突提示：两个抢同一块功能的模组同时勾上要说清楚，
        #   不然装完才发现视距被两个模组来回拉。
        self.lbl_warn = QLabel("")
        self.lbl_warn.setWordWrap(True)
        self.lbl_warn.setVisible(False)
        try:
            from .core import theme as _thm
            self.lbl_warn.setStyleSheet(
                "color: %s;" % _thm.on_bg("#d32f2f"))
        except Exception:
            pass
        v.addWidget(self.lbl_warn)

        bb = QDialogButtonBox(QDialogButtonBox.Ok | QDialogButtonBox.Cancel)
        bb.button(QDialogButtonBox.Ok).setText("完成选择") # ★ 改为更合理的文案
        bb.accepted.connect(self.accept)
        bb.rejected.connect(self.reject)
        v.addWidget(bb)

        self.items = install.mod_list_for(loader, mc_version)
        self.defaults = [bool(m["default"]) for m in self.items]
        self._fill()

    def _fill(self):
        self.table.setRowCount(0)
        self.boxes = []
        for m in self.items:
            # ★ V1.6.2：不再插入"① 前置"这类分组标题行，全部平铺
            r = self.table.rowCount()
            self.table.insertRow(r)
            cb = QCheckBox()
            cb.setChecked(bool(m["default"]))
            cb.stateChanged.connect(self._upd_count)
            w = QWidget()
            lay = QHBoxLayout(w)
            lay.setContentsMargins(0, 0, 0, 0)
            lay.setAlignment(Qt.AlignCenter)
            lay.addWidget(cb)
            self.table.setCellWidget(r, 0, w)
            self.boxes.append((m["slug"], cb))

            it1 = QTableWidgetItem(m["name"])
            if m.get("group") == "⑥ 自定义":
                f = it1.font(); f.setBold(True); it1.setFont(f)
                it1.setForeground(QColor(_on_bg("#1565c0")))
            self.table.setItem(r, 1, it1)

            desc = m["desc"]
            # 有冲突的直接写在说明里，勾的时候就能看见
            cfs = m.get("conflict") or []
            if cfs:
                names = []
                for c in cfs:
                    cm = install.mod_meta(self.loader, c)
                    names.append((cm or {}).get("name") or c)
                desc += "  ⚠ 与 %s 冲突" % "、".join(names)
            # 缺前置的也标出来（会被自动一起装上）
            reqs = m.get("req") or []
            if reqs:
                names = []
                for c in reqs:
                    cm = install.mod_meta(self.loader, c)
                    names.append((cm or {}).get("name") or c)
                desc += "  （需要 %s，会自动一起装）" % "、".join(names)
            self.table.setItem(r, 2, QTableWidgetItem(desc))
        self._upd_count()

    def _upd_count(self):
        try:
            picks = self.result_slugs()
            n = len(picks)
            plan = install.resolve_selection(self.loader, picks)
            extra = len(plan.get("added") or [])
            txt = "已选 %d 个" % n
            if extra:
                txt += "（含自动补的前置 %d 个）" % extra
            self.lbl_count.setText(txt)
            cfs = plan.get("conflicts") or []
            if cfs:
                self.lbl_warn.setText(
                    "⚠ 冲突：" + "；".join(w for _, _, w in cfs)
                    + "\n建议只保留其中一个。"
                      "（两个都装不会崩，但会互相拉扯）")
                self.lbl_warn.setVisible(True)
            else:
                self.lbl_warn.setText("")
                self.lbl_warn.setVisible(False)
        except Exception:
            pass

    def _add_custom(self):
        """手动添加一个不在内置清单里的模组。"""
        raw = self.ed_custom.text().strip()
        if not raw:
            return
        slug = install.normalize_slug(raw)
        if not slug:
            QMessageBox.warning(self, "识别不了", "看不出这是哪个模组。")
            return
        for sl, _ in self.boxes:
            if sl == slug:
                QMessageBox.information(self, "已经有了",
                                        "%s 已在清单里。" % slug)
                return
        self.items.append({"slug": slug, "name": slug,
                           "default": True, "desc": "手动添加",
                           "group": "⑥ 自定义", "side": "server",
                           "req": [], "conflict": []})
        self.defaults.append(True)
        self.ed_custom.clear()
        self._fill()
        note = ""
        why = install.CLIENT_ONLY_MODS.get(slug)
        if why:
            # ★ 手动加了个只对客户端生效的 —— 装到服务端没用，
            #   直接说清楚，别让人白等一次下载。
            note = ("\n\n⚠ %s 是客户端模组：%s\n"
                    "装到服务端不会有任何效果，建议取消勾选。"
                    % (slug, why))
        QMessageBox.information(
            self, "已添加",
            "%s 已加进清单（在最下面）。\n"
            "如果 %s 没有这个版本，安装时会跳过。%s"
            % (slug, self.mc_version, note))

    def _set_all(self, on):
        for _, cb in self.boxes:
            cb.setChecked(on)

    def _set_default(self):
        for i, (_, cb) in enumerate(self.boxes):
            if i < len(self.defaults):
                cb.setChecked(self.defaults[i])

    def result_slugs(self):
        return [slug for slug, cb in self.boxes if cb.isChecked()]


class AnalyzeDialog(QDialog):
    """离线日志诊断 —— 不联网、不需要 AI，靠规则库匹配。"""

    SEV_ICON = {"error": "[!]", "warn": "[?]", "info": "[i]"}
    SEV_NAME = {"error": "必须处理", "warn": "建议处理", "info": "提示"}

    def closeEvent(self, e):
        """关闭前先停掉后台线程。

        ★ 不停的话：对话框销毁了、线程还在跑，
          Qt 会直接 abort 整个程序 —— 也就是"点了就闪退"。
        """
        try:
            _stop_threads(self)
        except Exception:
            pass
        try:
            super().closeEvent(e)
        except Exception:
            pass

    def __init__(self, entry, all_entries=None, console_text=None,
                 parent=None):
        super().__init__(parent)
        # 窗口图标（跟主程序一致）
        try:
            from .app import app_icon
            self.setWindowIcon(app_icon())
        except Exception:
            pass
        self.entry = entry
        self.server_dir = entry.get("server_dir") or entry["folder"]
        self.setWindowTitle("日志诊断 - %s" % entry.get("name", ""))
        self.resize(860, 640)

        v = QVBoxLayout(self)

        # 顶部
        h = QHBoxLayout()
        self.lbl_top = QLabel("正在分析 ...")
        f = self.lbl_top.font(); f.setBold(True); self.lbl_top.setFont(f)
        h.addWidget(self.lbl_top, 1)
        b_re = QPushButton("重新分析")
        b_re.clicked.connect(self.run)
        h.addWidget(b_re)
        b_log = QPushButton("打开日志文件夹")
        b_log.clicked.connect(self.open_log_dir)
        h.addWidget(b_log)
        v.addLayout(h)

        # 结果列表
        self.list = QListWidget()
        self.list.currentItemChanged.connect(self.show_detail)
        v.addWidget(self.list, 1)

        # 详情
        self.detail = QTextEdit()
        self.detail.setReadOnly(True)
        self.detail.setMinimumHeight(150)
        v.addWidget(self.detail, 1)

        # ★ V1.0.0 AI 深度诊断区
        self._ai_init(v)

        v.addWidget(QLabel(
            "说明：这是离线规则匹配（不联网、不需要 AI）。"
            "没查到不代表一定没问题，只说明没命中已知模式。"))

        bb = QDialogButtonBox(QDialogButtonBox.Close)
        bb.rejected.connect(self.reject)
        v.addWidget(bb)

        self.issues = []
        self.console_text = console_text
        self.all_entries = all_entries or []
        self.run()

    def run(self):
        from .core import analyze as amod
        self.list.clear()
        try:
            rep = amod.full_report(
                self.server_dir,
                entry=self.entry,
                all_entries=self.all_entries,
                extra_text=self.console_text)
        except Exception as e:
            self.lbl_top.setText("分析失败：%s" % e)
            return

        self.issues = []
        for r in rep.get("log_issues", []):
            self.issues.append({
                "level": r["severity"], "title": r["title"],
                "cause": r["cause"], "fix": r["fix"],
                "file": r.get("file", ""),
                "line": r.get("first_line", 0),
                "count": r.get("count", 1),
                "sample": r.get("sample", ""),
            })
        # 日志里已经报过的事，配置检查别再报一遍
        # （比如日志里有 InvalidPathException，就不用再说"level-name 有冒号"了）
        seen = set(i["title"] for i in self.issues)
        SKIP_IF = {
            "level-name 含非法字符": "存档名里有 Windows 不允许的字符",
            "eula.txt 未同意": "EULA 协议未同意",
        }
        for c in rep.get("config_issues", []):
            title = c["title"]
            skip = False
            for k, prefix in SKIP_IF.items():
                if k in title:
                    for t in seen:
                        if t.startswith(prefix[:8]):
                            skip = True
            if skip:
                continue
            self.issues.append({
                "level": c["level"], "title": title,
                "cause": c.get("detail", ""), "fix": c.get("fix", []),
                "file": "server.properties / eula.txt",
                "line": 0, "count": 1, "sample": "",
            })

        order = {"error": 0, "warn": 1, "info": 2}
        self.issues.sort(key=lambda x: order.get(x["level"], 9))

        n_err = sum(1 for i in self.issues if i["level"] == "error")
        n_warn = sum(1 for i in self.issues if i["level"] == "warn")
        if not self.issues:
            self.lbl_top.setText("没查到已知问题（这不代表一定没问题）")
        else:
            txt = "找到 %d 个问题" % len(self.issues)
            if n_err:
                txt += "，其中 %d 个必须处理" % n_err
            self.lbl_top.setText(txt)

        for i in self.issues:
            icon = self.SEV_ICON.get(i["level"], "[i]")
            name = self.SEV_NAME.get(i["level"], "")
            cnt = "  (出现 %d 次)" % i["count"] if i["count"] > 1 else ""
            it = QListWidgetItem("%s [%s] %s%s"
                                 % (icon, name, i["title"], cnt))
            if i["level"] == "error":
                it.setForeground(QColor(_on_bg("#c62828")))
            elif i["level"] == "warn":
                it.setForeground(QColor(_on_bg("#e67e22")))
            else:
                it.setForeground(QColor(_on_bg("#555555")))
            self.list.addItem(it)

        if self.issues:
            self.list.setCurrentRow(0)

    def show_detail(self, cur, prev):
        if cur is None:
            return
        row = self.list.row(cur)
        if row < 0 or row >= len(self.issues):
            return
        i = self.issues[row]
        L = []
        L.append("【问题】%s" % i["title"])
        L.append("")
        if i["cause"]:
            L.append("【为什么】")
            L.append(i["cause"])
            L.append("")
        if i["fix"]:
            L.append("【怎么修】")
            for n, step in enumerate(i["fix"], 1):
                L.append("  %d. %s" % (n, step))
            L.append("")
        if i["sample"]:
            L.append("【日志原文】")
            L.append(i["sample"])
            L.append("")
        loc = i.get("file") or ""
        if i.get("line"):
            loc += "  第 %d 行" % i["line"]
        if loc:
            L.append("【位置】%s" % loc)
        self.detail.setPlainText("\n".join(L))

    def open_log_dir(self):
        import subprocess
        from . import core
        nw = getattr(subprocess, "CREATE_NO_WINDOW", 0)
        for d in (os.path.join(self.server_dir, "logs"),
                  os.path.join(self.server_dir, "server", "logs")):
            if os.path.isdir(d):
                try:
                    subprocess.Popen(["explorer", d], creationflags=nw)
                except Exception:
                    pass
                return
        try:
            subprocess.Popen(["explorer", self.server_dir],
                             creationflags=nw)
        except Exception:
            pass

    # ============ V1.0.0 AI 深度诊断 ============

    def _ai_init(self, v):
        """AI 深度诊断 UI：Key + 服务商 + 模型 + 按钮 + Markdown 输出。"""
        from .core import ai_diag
        gb = QGroupBox("AI 深度诊断（联网）")
        g = QVBoxLayout(gb)

        cfg = ai_diag.load_cfg()

        row1 = QHBoxLayout()
        self.ai_key = QLineEdit(cfg.get("key", ""))
        self.ai_key.setPlaceholderText("OpenAI / DeepSeek / Kimi 兼容 API Key")
        self.ai_key.setEchoMode(QLineEdit.Password)
        row1.addWidget(QLabel("API Key:"))
        row1.addWidget(self.ai_key, 1)

        self.ai_provider = QComboBox()
        for name, base, model in ai_diag.PROVIDERS:
            self.ai_provider.addItem(name)
        self.ai_provider.setCurrentIndex(
            min(cfg.get("provider", 0), len(ai_diag.PROVIDERS) - 1))
        self.ai_provider.currentIndexChanged.connect(self._ai_provider_changed)
        row1.addWidget(QLabel("服务商:"))
        row1.addWidget(self.ai_provider)

        self.ai_model = QLineEdit(cfg.get("model", ""))
        self.ai_model.setPlaceholderText("模型名（如 gpt-4o-mini / deepseek-chat）")
        row1.addWidget(QLabel("模型:"))
        row1.addWidget(self.ai_model)
        g.addLayout(row1)

        row2 = QHBoxLayout()
        self.ai_base = QLineEdit(cfg.get("base", ""))
        self.ai_base.setPlaceholderText("接口地址（留空用预设，自定义时填 https://.../v1）")
        row2.addWidget(QLabel("接口地址:"))
        row2.addWidget(self.ai_base, 1)

        self.btn_ai = QPushButton("AI 深度诊断")
        self.btn_ai.clicked.connect(self._ai_diag)
        row2.addWidget(self.btn_ai)
        g.addLayout(row2)

        self.ai_status = QLabel("提取 latest.log 最后 200 行报错，交给大模型分析")
        self.ai_status.setStyleSheet(_muted())
        g.addWidget(self.ai_status)

        self.ai_out = QTextEdit()
        self.ai_out.setReadOnly(True)
        self.ai_out.setMinimumHeight(140)
        self.ai_out.setPlaceholderText(
            "AI 诊断结果（Markdown）会显示在这里")
        g.addWidget(self.ai_out)

        v.addWidget(gb, 1)
        self._ai_provider_changed(self.ai_provider.currentIndex())

    def _ai_provider_changed(self, idx):
        """切换服务商时自动带出预设的接口地址与模型。"""
        try:
            from .core import ai_diag
            name, base, model = ai_diag.PROVIDERS[idx]
            if base:
                self.ai_base.setText(base)
            if model:
                self.ai_model.setText(model)
        except Exception:
            pass

    def _ai_diag(self):
        """启动后台线程做 AI 诊断（网络请求绝不卡界面）。"""
        from .core import ai_diag
        key = self.ai_key.text().strip()
        if not key:
            QMessageBox.warning(self, "缺 API Key",
                                "请先填写 API Key（OpenAI / DeepSeek / Kimi 兼容）")
            return
        base = self.ai_base.text().strip()
        model = self.ai_model.text().strip()
        # 持久化（Key 存在本机 ai.json）
        try:
            ai_diag.save_cfg(key, base, model, self.ai_provider.currentIndex())
        except Exception:
            pass

        self.btn_ai.setEnabled(False)
        self.ai_out.clear()
        self.ai_status.setText("正在提取报错日志 ...")

        def _job(on_log=None):
            rep = ai_diag.collect_errors(self.server_dir)
            if not rep["path"]:
                raise ValueError("找不到 latest.log（服务器还没启动过？）")
            body = ("以下是 Minecraft 服务器日志中提取的报错片段"
                    "（共 %d 行，取最后 %d 行）：\n\n%s"
                    % (rep["total"], len(rep["lines"]),
                       "\n".join(rep["lines"])))
            return ai_diag.call_llm(key, base, model, body,
                                    on_log=on_log)

        w = Worker(_job)
        _track(self, w)
        w.log.connect(self._ai_log)
        w.done.connect(self._on_ai_done)
        w.start()

    def _ai_log(self, m):
        try:
            self.ai_status.setText(str(m))
        except Exception:
            pass

    def _on_ai_done(self, ok, res):
        try:
            self.btn_ai.setEnabled(True)
            if ok:
                self.ai_out.setMarkdown(str(res))
                self.ai_status.setText("诊断完成（结果可复制）")
            else:
                self.ai_status.setText("诊断失败")
                self.ai_out.setPlainText("AI 诊断失败：%s" % res)
        except Exception:
            pass


class SourceDialog(QDialog):
    """下载源 + CurseForge API Key 设置。"""

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setWindowTitle("下载源设置")
        v = QVBoxLayout(self)
        tip = QLabel(
            "MCIM 是国内公益镜像，会时不时挂（实测有过 503）。\n"
            "『自动』= 先试镜像，不通就退回官方。\n\n"
            "CurseForge 官方 API 需要免费申请一个 key；\n"
            "走 MCIM 镜像时不需要。这里留空就只用镜像搜 CF。")
        tip.setWordWrap(True)
        v.addWidget(tip)

        f = QFormLayout()
        self.cb = QComboBox()
        self.cb.addItem("自动（推荐）", "auto")
        self.cb.addItem("MCIM 国内镜像", "mcim")
        self.cb.addItem("官方源（Modrinth / CurseForge）", "official")
        i = self.cb.findData(sources.get_mirror())
        self.cb.setCurrentIndex(i if i >= 0 else 0)
        f.addRow("下载源:", self.cb)

        self.ed_key = QLineEdit(sources.get_cf_key())
        self.ed_key.setPlaceholderText(
            "CurseForge API Key（留空 = 不启用官方 CF）")
        self.ed_key.setEchoMode(QLineEdit.Password)
        f.addRow("CF Key:", self.ed_key)
        v.addLayout(f)

        self.lbl_now = QLabel("当前：%s" % sources.describe())
        self.lbl_now.setStyleSheet(_muted())
        v.addWidget(self.lbl_now)

        btns = QDialogButtonBox(QDialogButtonBox.Ok
                                | QDialogButtonBox.Cancel)
        btns.accepted.connect(self.accept)
        btns.rejected.connect(self.reject)
        v.addWidget(btns)
        self.resize(470, 280)

    def values(self):
        return (self.cb.currentData() or "auto",
                self.ed_key.text().strip())


class ModrinthDialog(QDialog):
    """从 Modrinth 搜索/筛选/安装模组（像网页版那样挑）。"""

    COLS = ["名称", "简介", "分类", "下载量", "支持版本", "slug"]

    def closeEvent(self, e):
        """关闭前先停掉后台线程。

        ★ 不停的话：对话框销毁了、线程还在跑，
          Qt 会直接 abort 整个程序 —— 也就是"点了就闪退"。
        """
        try:
            _stop_threads(self)
        except Exception:
            pass
        try:
            super().closeEvent(e)
        except Exception:
            pass

    def __init__(self, entry, parent=None):
        super().__init__(parent)
        # 窗口图标（跟主程序一致）
        try:
            from .app import app_icon
            self.setWindowIcon(app_icon())
        except Exception:
            pass
        self.entry = entry
        self.setWindowTitle("Modrinth 模组库 - %s"
                            % entry.get("name", ""))
        self.resize(1000, 680)

        self.server_dir = entry.get("server_dir") or entry["folder"]
        self.mc_version = entry.get("version") or ""
        self.loader = (entry.get("loader") or "").lower()
        if self.loader == "unknown":
            self.loader = ""
        self.mods_dir = os.path.join(self.server_dir, "mods")

        self.installed = self._installed_slugs()
        self.offset = 0
        self.total = 0
        self.hits = []
        self.worker = None

        v = QVBoxLayout(self)

        # ---- 第零行：下载源 ----
        hs = QHBoxLayout()
        hs.addWidget(QLabel("源:"))
        self.cb_src = QComboBox()
        self.cb_src.addItem("Modrinth", "modrinth")
        self.cb_src.addItem("CurseForge", "curseforge")
        self.cb_src.setToolTip(
            "CurseForge 上有大量 Modrinth 没有的 Forge 模组。\n"
            "走 MCIM 镜像时不需要 API key。")
        self.cb_src.currentIndexChanged.connect(self._on_src)
        hs.addWidget(self.cb_src)
        self.lbl_mirror = QLabel("")
        self.lbl_mirror.setStyleSheet(_muted())
        hs.addWidget(self.lbl_mirror)
        self.btn_mirror = QPushButton("镜像源设置")
        self.btn_mirror.setToolTip(
            "MCIM 是公益镜像，会时不时挂掉。\n"
            "默认『自动』：先试镜像，不通就退回官方。")
        self.btn_mirror.clicked.connect(self.ask_mirror)
        hs.addWidget(self.btn_mirror)
        self.btn_probe = QPushButton("测一下哪个能用")
        self.btn_probe.setToolTip("真的去请求一次，看各源通不通")
        self.btn_probe.clicked.connect(self.do_probe)
        hs.addWidget(self.btn_probe)
        hs.addStretch(1)
        v.addLayout(hs)

        # ---- 第一行：搜索 ----
        h = QHBoxLayout()
        self.ed_q = QLineEdit()
        self.ed_q.setPlaceholderText("搜模组名，留空 = 浏览全部")
        self.ed_q.returnPressed.connect(self.search)
        h.addWidget(self.ed_q, 1)
        self.btn_go = QPushButton("搜索")
        self.btn_go.clicked.connect(self.search)
        h.addWidget(self.btn_go)
        v.addLayout(h)

        # ---- 第二行：筛选 ----
        g = QGroupBox("筛选（默认已按当前服务器填好，改了会重新搜）")
        fg = QFormLayout(g)
        hl = QHBoxLayout()
        self.ed_ver = QLineEdit(self.mc_version)
        self.ed_ver.setPlaceholderText("留空 = 不限版本")
        hl.addWidget(QLabel("MC版本:"))
        hl.addWidget(self.ed_ver)
        self.chk_ver = QCheckBox("只匹配该版本")
        self.chk_ver.setChecked(True)
        hl.addWidget(self.chk_ver)
        hl.addWidget(QLabel("加载器:"))
        self.cb_loader = QComboBox()
        self.cb_loader.addItem("(不限)", "")
        for k, lab in (("fabric", "Fabric"), ("forge", "Forge"),
                       ("neoforge", "NeoForge"), ("quilt", "Quilt")):
            self.cb_loader.addItem(lab, k)
        if self.loader in ("fabric", "forge", "neoforge", "quilt"):
            i = self.cb_loader.findData(self.loader)
            if i >= 0:
                self.cb_loader.setCurrentIndex(i)
        hl.addWidget(self.cb_loader)
        hl.addStretch(1)
        fg.addRow(hl)

        h2 = QHBoxLayout()
        h2.addWidget(QLabel("分类:"))
        self.cb_cat = QComboBox()
        self.cb_cat.addItem("(全部)", "")
        for slug, lab in modman.CATEGORIES:
            self.cb_cat.addItem(lab, slug)
        h2.addWidget(self.cb_cat)
        h2.addWidget(QLabel("排序:"))
        self.cb_sort = QComboBox()
        for k, lab in modman.SORTS:
            self.cb_sort.addItem(lab, k)
        h2.addWidget(self.cb_sort)
        self.chk_hide = QCheckBox("隐藏已安装的")
        self.chk_hide.setChecked(True)
        h2.addWidget(self.chk_hide)
        h2.addStretch(1)
        fg.addRow(h2)
        v.addWidget(g)

        # ---- 结果表格 ----
        self.table = QTableWidget(0, len(self.COLS))
        self.table.setHorizontalHeaderLabels(self.COLS)
        self.table.verticalHeader().setVisible(False)
        self.table.setSelectionBehavior(QAbstractItemView.SelectRows)
        self.table.setEditTriggers(QAbstractItemView.NoEditTriggers)
        self.table.setMouseTracking(True)
        hh = self.table.horizontalHeader()
        hh.setSectionResizeMode(0, QHeaderView.ResizeToContents)
        hh.setSectionResizeMode(2, QHeaderView.ResizeToContents)
        hh.setSectionResizeMode(3, QHeaderView.ResizeToContents)
        hh.setSectionResizeMode(4, QHeaderView.ResizeToContents)
        hh.setSectionResizeMode(1, QHeaderView.Stretch)
        self.table.setColumnHidden(5, True)
        self.table.doubleClicked.connect(self._on_double)
        v.addWidget(self.table, 1)

        # ---- 分页 ----
        hp = QHBoxLayout()
        self.btn_prev = QPushButton("上一页")
        self.btn_prev.clicked.connect(lambda: self._page(-1))
        hp.addWidget(self.btn_prev)
        self.lbl_page = QLabel("第 1 页")
        hp.addWidget(self.lbl_page)
        self.btn_next = QPushButton("下一页")
        self.btn_next.clicked.connect(lambda: self._page(1))
        hp.addWidget(self.btn_next)
        hp.addStretch(1)
        v.addLayout(hp)

        # ---- 操作按钮 ----
        hb = QHBoxLayout()
        self.btn_inst = QPushButton("安装选中")
        self.btn_inst.clicked.connect(self.install_sel)
        hb.addWidget(self.btn_inst)
        self.btn_open = QPushButton("在 Modrinth 打开")
        self.btn_open.clicked.connect(self.open_page)
        hb.addWidget(self.btn_open)
        self.btn_mcmod = QPushButton("mcmod 查中文资料")
        self.btn_mcmod.setToolTip("mcmod 没有公开 API，"
                                  "所以只做跳转（不抓取页面）")
        self.btn_mcmod.clicked.connect(self.open_mcmod)
        hb.addWidget(self.btn_mcmod)
        self.btn_gh = QPushButton("从 GitHub 装")
        self.btn_gh.setToolTip("填 owner/name 或仓库链接，"
                               "从 Releases 里挑 jar")
        self.btn_gh.clicked.connect(self.install_github)
        hb.addWidget(self.btn_gh)
        self.btn_re = QPushButton("重新搜索")
        self.btn_re.clicked.connect(self.search)
        hb.addWidget(self.btn_re)
        hb.addStretch(1)
        self.lbl_stat = QLabel("")
        self.lbl_stat.setStyleSheet(_muted())
        hb.addWidget(self.lbl_stat)
        v.addLayout(hb)

        # ---- 日志 ----
        self.log = LogView()
        self.log.setReadOnly(True)
        self.log.setMaximumHeight(110)
        v.addWidget(self.log)

        bb = QDialogButtonBox(QDialogButtonBox.Close)
        bb.rejected.connect(self.reject)
        v.addWidget(bb)

        self._sync_page()
        self._sync_src_ui()
        # 打开就先浏览一批（按当前服筛选）
        QTimer.singleShot(60, self.search)

    # ---------- 数据 ----------
    def _installed_slugs(self):
        """已装模组的名字集合（用来标记/隐藏）。

        ★ V1.7.3-1：全部归一化（去 - _ . 空格、小写），
        判定端用 _inst_hit 做子串包含，不再要求精确相等。
        """
        out = set()
        try:
            for m in modman.scan_mods(self.server_dir):
                n = (m.get("name") or "").strip().lower()
                if n:
                    out.add(modman._norm(n))
                fn = (m.get("file") or "").lower()
                if fn:
                    out.add(modman._norm(
                        fn.replace(".jar", "").replace(".disabled", "")))
        except Exception:
            pass
        return out

    def _params(self):
        return {
            "source": self.cb_src.currentData() or "modrinth",
            "query": self.ed_q.text().strip(),
            "mc_version": (self.ed_ver.text().strip()
                           if self.chk_ver.isChecked() else None),
            "loader": self.cb_loader.currentData() or None,
            "category": self.cb_cat.currentData() or None,
            "sort": self.cb_sort.currentData() or "relevance",
            "offset": self.offset,
        }

    def search(self):
        self.offset = 0
        self._run()

    def _page(self, d):
        new = self.offset + d * 20
        if new < 0 or (self.total and new >= self.total):
            return
        self.offset = new
        self._run()

    def _run(self):
        self.btn_go.setEnabled(False)
        self.btn_inst.setEnabled(False)
        self.lbl_stat.setText("搜索中 ...")
        self.worker = _track(self, Worker(
            self._do_search, self._params(), self))
        self.worker.log.connect(self._log)
        self.worker.done.connect(self._on_search)
        self.worker.start()

    def _do_search(self, source="modrinth", query="", mc_version=None,
                   loader=None, category=None, sort="relevance",
                   offset=0, on_log=None):
        r = modman.browse_projects(
            query=query, mc_version=mc_version, loader=loader,
            category=category, sort=sort, limit=20, offset=offset,
            source=source)
        if r.get("err"):
            raise RuntimeError(r["err"])
        return r

    def _on_search(self, ok, res):
        self.btn_go.setEnabled(True)
        self.btn_inst.setEnabled(True)
        if not ok:
            self.lbl_stat.setText("搜索失败")
            self._log("[错误] %r" % (res,))
            return
        self.hits = res.get("hits") or []
        self.total = res.get("total") or 0
        self._fill()
        self._sync_page()

        # ★ 以前搜到 0 条什么都不说 —— 分不清是"真没有"
        #   还是"网络挂了/筛选太严"。现在给原因 + 一键放宽。
        err = res.get("error")
        if err:
            self.lbl_stat.setText("搜索失败：%s" % err)
            self._log("[失败] %s" % err)
            if res.get("query_url"):
                self._log("       请求: %s" % res["query_url"])
            self._offer_relax("网络/接口有问题", err)
            return
        if not self.hits:
            p_ = self._params()
            why = []
            if p_.get("mc_version"):
                why.append("MC 版本筛成 %s" % p_["mc_version"])
            if p_.get("loader"):
                why.append("加载器 %s" % p_["loader"])
            if p_.get("category"):
                why.append("分类 %s" % p_["category"])
            if p_.get("query"):
                why.append("关键词「%s」" % p_["query"])
            msg = ("没搜到东西（筛选条件：%s）"
                   % ("、".join(why) if why else "无"))
            self.lbl_stat.setText(msg)
            self._log("[空] %s" % msg)
            self._offer_relax("放宽筛选", msg)

    def _offer_relax(self, title, reason):
        """空结果/失败时，问用户要不要放宽筛选再搜一次。"""
        p_ = dict(self._params())
        relaxed = []
        if p_.get("mc_version"):
            p_["mc_version"] = None
            relaxed.append("不限 MC 版本")
        if p_.get("loader"):
            p_["loader"] = None
            relaxed.append("不限加载器")
        if p_.get("category"):
            p_["category"] = None
            relaxed.append("不限分类")
        if not relaxed:
            return
        r = QMessageBox.question(
            self, title,
            "%s\n\n要不要放宽条件再搜一次？\n  ・ %s\n\n"
            "（放宽后能看到更多模组，但要自己确认版本是否匹配）"
            % (reason, "\n  ・ ".join(relaxed)),
            QMessageBox.Yes | QMessageBox.No, QMessageBox.No)
        if r != QMessageBox.Yes:
            return
        # 同步回界面控件，让用户看到筛选确实被清掉了
        try:
            # cb_loader / cb_cat 的第 0 项 data 是 ""（= 不限）
            if p_.get("mc_version") is None:
                if hasattr(self, "chk_ver"):
                    self.chk_ver.setChecked(False)
                if hasattr(self, "ed_ver"):
                    self.ed_ver.clear()
            if p_.get("loader") is None and hasattr(self, "cb_loader"):
                self.cb_loader.setCurrentIndex(0)
            if p_.get("category") is None and hasattr(self, "cb_cat"):
                self.cb_cat.setCurrentIndex(0)
        except Exception:
            pass
        self._log("已放宽筛选，重新搜索 ...")
        self.btn_go.setEnabled(False)
        self.lbl_stat.setText("重新搜索中 ...")
        self.worker = _track(self, Worker(self._do_search, p_, self))
        self.worker.log.connect(self._log)
        self.worker.done.connect(self._on_search)
        self.worker.start()

    def _fill(self):
        self.table.setRowCount(0)
        shown = 0
        for h in self.hits:
            title = h.get("title") or h.get("slug") or ""
            slug = h.get("slug") or ""
            if self.chk_hide.isChecked() and self._is_installed(title,
                                                                slug):
                continue
            r = self.table.rowCount()
            self.table.insertRow(r)
            mark = ""
            if self._is_installed(title, slug):
                mark = " ✓已装"
            self._item(r, 0, title + mark)
            desc = (h.get("description") or "").replace("\n", " ")
            self._item(r, 1, desc[:110])
            cats = h.get("categories") or []
            self._item(r, 2, self._cat_label(cats))
            self._item(r, 3, _fmt_num(h.get("downloads")))
            vs = h.get("versions") or []
            self._item(r, 4, ", ".join(vs[:3]) + (" ..." if len(vs) > 3
                                                  else ""))
            self._item(r, 5, slug)
            shown += 1
        self.lbl_stat.setText("共 %d 个结果，显示 %d 个"
                              % (self.total, shown))
        if not shown and self.total:
            self.lbl_stat.setText(
                "%d 个结果全被『隐藏已安装的』过滤了" % self.total)

    def _item(self, r, c, text):
        it = QTableWidgetItem(str(text))
        if c == 0:
            it.setToolTip(str(text))
        self.table.setItem(r, c, it)

    def _is_installed(self, title, slug):
        # ★ V1.7.3-1：归一化 contains（以前是精确成员匹配，
        #   ferritecore/spark 这类名字带尾巴的一直判"未装"）
        return _inst_hit(slug, title, self.installed)

    def _cat_label(self, cats):
        m = dict(modman.CATEGORIES)
        out = [m.get(c, c) for c in cats
               if c in m or c in ("fabric", "forge", "neoforge", "quilt")]
        out = [o for o in out if o not in
               ("Fabric", "forge", "neoforge", "quilt")]
        return "、".join(out[:3])

    def _sync_page(self):
        page = self.offset // 20 + 1
        self.lbl_page.setText("第 %d 页" % page)
        self.btn_prev.setEnabled(self.offset > 0)
        self.btn_next.setEnabled(bool(self.total)
                                 and self.offset + 20 < self.total)

    # ---------- 操作 ----------
    def _sel_slugs(self):
        rows = sorted(set(i.row() for i in
                          self.table.selectionModel().selectedRows()))
        out = []
        for r in rows:
            it = self.table.item(r, 5)
            nm = self.table.item(r, 0)
            if it:
                out.append((it.text(),
                            (nm.text() if nm else it.text())))
        return out

    def install_sel(self):
        sels = self._sel_slugs()
        if not sels:
            QMessageBox.information(self, "没选", "先在上面选中要装的模组")
            return
        names = "\n".join("· %s" % n for _, n in sels[:10])
        if len(sels) > 10:
            names += "\n· ...等 %d 个" % len(sels)
        r = QMessageBox.question(
            self, "确认安装",
            "把下面这些装到：\n%s\n\n%s\n\n"
            "装完最好重启服务端才生效。"
            % (self.mods_dir, names),
            QMessageBox.Yes | QMessageBox.No, QMessageBox.Yes)
        if r != QMessageBox.Yes:
            return
        self.btn_inst.setEnabled(False)
        self.worker = _track(self, Worker(
            self._do_install, {"sels": sels}, self))
        self.worker.log.connect(self._log)
        self.worker.done.connect(self._on_install)
        self.worker.start()

    def _do_install(self, sels=None, on_log=None):
        log = on_log or (lambda m: None)
        os.makedirs(self.mods_dir, exist_ok=True)
        ok_n, fail = 0, []
        for slug, name in (sels or []):
            log("安装 %s (%s) ..." % (name, slug))
            r = modman.install_from_project(
                slug, self.ed_ver.text().strip() or None,
                self.cb_loader.currentData() or None,
                self.mods_dir,
                source=self.cb_src.currentData() or "modrinth")
            if r.get("ok"):
                ok_n += 1
                log("  [OK] %s  %s" % (r["file"], r.get("version") or ""))
            else:
                fail.append((name, r.get("reason")))
                log("  [跳过] %s - %s" % (name, r.get("reason")))
        return {"ok": ok_n, "fail": fail}

    def _on_install(self, ok, res):
        self.btn_inst.setEnabled(True)
        if not ok:
            self._log("[错误] %r" % (res,))
            return
        n_ok = res.get("ok", 0)
        fail = res.get("fail") or []
        self.installed = self._installed_slugs()
        self._fill()
        if fail:
            why = "\n".join("· %s：%s" % (a, b) for a, b in fail[:8])
            QMessageBox.warning(
                self, "装完了（有失败）",
                "成功 %d 个，失败 %d 个：\n\n%s\n\n"
                "失败多半是该模组没有你这个 版本+加载器 的组合。"
                % (n_ok, len(fail), why))
        else:
            QMessageBox.information(self, "完成",
                                    "成功装了 %d 个模组。\n\n"
                                    "重启服务端才会生效。" % n_ok)

    def open_page(self):
        sels = self._sel_slugs()
        if not sels:
            QMessageBox.information(self, "没选", "先选中一个")
            return
        slug = sels[0][0]
        QDesktopServices.openUrl(
            QUrl("https://modrinth.com/mod/%s" % slug))

    def _on_double(self, index):
        r = index.row()
        it = self.table.item(r, 5)
        if not it:
            return
        src = self.cb_src.currentData()
        if src == "curseforge":
            QDesktopServices.openUrl(
                QUrl("https://www.curseforge.com/minecraft/mc-mods/%s"
                     % it.text()))
        else:
            QDesktopServices.openUrl(
                QUrl("https://modrinth.com/mod/%s" % it.text()))

    # ---------- 源 / 镜像 ----------
    def _on_src(self):
        src = self.cb_src.currentData()
        self._sync_src_ui()
        self.search()

    def _sync_src_ui(self):
        src = self.cb_src.currentData()
        k = sources.get_cf_key()
        self.lbl_mirror.setText(
            "当前：%s%s" % (sources.describe(),
                            "｜CF key 已填" if k else ""))
        # CurseForge 的分类是另一套（数字 id），先关掉分类筛选
        self.cb_cat.setEnabled(src == "modrinth")
        if src != "modrinth":
            self.cb_cat.setCurrentIndex(0)
        self.btn_open.setText("在 CurseForge 打开" if src == "curseforge"
                              else "在 Modrinth 打开")

    def ask_mirror(self):
        """下载源 + CurseForge API Key 设置。"""
        dlg = _themed(SourceDialog(self))
        if not dlg.exec():
            return
        mirror, key = dlg.values()
        sources.set_mirror(mirror)
        sources.set_cf_key(key)
        ok_s, err_s = sources.save()
        self._sync_src_ui()
        if not ok_s:
            # 切了源以为生效，重启又回到官方 —— 必须说出来
            self._log("[警告] 源设置没保存上：%s" % err_s)
            QMessageBox.warning(
                self, "没保存上",
                "下载源设置没能写入文件，重启后会回到之前的源。\n\n%s"
                % err_s)
        self._log("[源] 已切换到：%s" % sources.describe())
        if key:
            self._log("[源] CurseForge API Key 已保存（本地 sources.json）")
        self.search()

    def do_probe(self):
        """真的去请求一次，看哪个源通。"""
        self.btn_probe.setEnabled(False)
        self._log("正在探测各下载源 ...")
        self.worker = _track(self, Worker(self._do_probe, {}, self))
        self.worker.log.connect(self._log)
        self.worker.done.connect(self._on_probe)
        self.worker.start()

    def _do_probe(self, on_log=None):
        return sources.probe_all(timeout=8, on_log=on_log)

    def _on_probe(self, ok, res):
        self.btn_probe.setEnabled(True)
        if not ok:
            self._log("[探测] 出错：%r" % (res,))
            return
        self._sync_src_ui()
        good = [n for n, o, _ in res if o]
        if not good:
            QMessageBox.warning(
                self, "都不通",
                "镜像和官方源都请求失败了。\n\n"
                "多半是你本机网络/代理的问题，不是源挂了。")
        else:
            self._log("[探测] 最终使用：%s" % sources.describe())
            self.search()

    # ---------- mcmod ----------
    def open_mcmod(self):
        sels = self._sel_slugs()
        name = sels[0][1] if sels else self.ed_q.text().strip()
        if not name:
            QMessageBox.information(
                self, "没选",
                "先选中一个模组，或在搜索框里填个名字")
            return
        QDesktopServices.openUrl(QUrl(sources.mcmod_search_url(name)))
        self._log("[mcmod] 已打开搜索页：%s" % name)

    # ---------- GitHub ----------
    def install_github(self):
        repo, okk = QInputDialog.getText(
            self, "从 GitHub 安装",
            "填 owner/name 或完整仓库链接：\n\n"
            "  例如：FabricMC/fabric\n"
            "       https://github.com/CaffeineMC/sodium\n\n"
            "会从 Releases 里挑 jar（优先正式版、"
            "文件名含你的版本/加载器）。\n"
            "注意：GitHub 没有『支持哪个 MC 版本』的结构化数据，\n"
            "只能从文件名猜，可能挑错。")
        if not okk or not (repo or "").strip():
            return
        self.btn_gh.setEnabled(False)
        self.worker = _track(self, Worker(
            self._do_github, {"repo": repo.strip()}, self))
        self.worker.log.connect(self._log)
        self.worker.done.connect(self._on_github)
        self.worker.start()

    def _do_github(self, repo=None, on_log=None):
        log = on_log or (lambda m: None)
        log("查 %s 的 releases ..." % repo)
        r = sources.github_latest_jar(
            repo, self.ed_ver.text().strip() or None,
            self.cb_loader.currentData() or None)
        if not r.get("ok"):
            return {"ok": False, "err": r.get("err")}
        os.makedirs(self.mods_dir, exist_ok=True)
        dest = os.path.join(self.mods_dir, r["filename"])
        from .core import install
        install._download(r["url"], dest, on_log=log)
        return {"ok": True, "file": r["filename"], "tag": r.get("tag")}

    def _on_github(self, ok, res):
        self.btn_gh.setEnabled(True)
        if not ok or not (res or {}).get("ok"):
            err = (res or {}).get("err") or res
            QMessageBox.warning(self, "装不了",
                                "%r" % (err,))
            return
        self.installed = self._installed_slugs()
        self._fill()
        QMessageBox.information(
            self, "完成",
            "装了 %s（%s）\n\n重启服务端才生效。"
            % (res["file"], res.get("tag") or ""))

    def _log(self, m):
        self.log.appendPlainText(m)


def _fmt_num(n):
    """1234567 -> 123.5 万"""
    try:
        n = int(n)
    except Exception:
        return str(n or "")
    if n >= 100000000:
        return "%.1f 亿" % (n / 100000000.0)
    if n >= 10000:
        return "%.1f 万" % (n / 10000.0)
    return str(n)


# =========================================================
#  PCL2 风格模组浏览安装（v0.3.0 新增）
# =========================================================
# 不再用枯燥的表格：每个模组是一张卡片（图标 + 标题 + 简介），
# 勾选后点"安装"直接走 install.install_mods 的 4 线程并发下载
# （CDN 加速 + 断点续传 + Fractureiser 木马扫描）。
# 版本和加载器在初始化时已经定死（从服务器配置进入），
# 这里不再让用户选 —— 后台请求强制 facets：
#   versions:<mc_version> + categories:<loader>
def _fetch_icon_bytes(url, timeout=15):
    """下载一张模组图标（后台线程调用，返回原始字节）。"""
    if not url:
        return None
    try:
        import urllib.request
        req = urllib.request.Request(
            url, headers={"User-Agent": "MCServerManager/0.1"})
        with urllib.request.urlopen(req, timeout=timeout) as r:
            return r.read()
    except Exception:
        return None


class ModBrowseDialog(QDialog):
    """PCL2 风格模组浏览：卡片视图 + 勾选安装。

    两种模式：
      · select_only=False（默认，配置页用）：
        勾选后点「安装选中」，直接并发下载到服务器 mods 目录。
      · select_only=True（初始化向导用）：
        只做选择，点「确定选择」返回勾选的 slug 列表，
        实际下载由初始化流程统一执行（有进度/失败重试）。
    """

    def closeEvent(self, e):
        """关闭前先停掉后台线程（不然对话框销毁线程还在跑 = 闪退）。"""
        try:
            _stop_threads(self)
        except Exception:
            pass
        try:
            super().closeEvent(e)
        except Exception:
            pass

    def done(self, code):
        # 确定/接受时把勾选的 slug 存到 self.selected，调用方读取
        if code == QDialog.Accepted:
            try:
                self.selected = self._checked_slugs()
            except Exception:
                self.selected = []
        super().done(code)

    def __init__(self, entry, parent=None, select_only=False,
                 mode="mods"):
        super().__init__(parent)
        try:
            from .app import app_icon
            self.setWindowIcon(app_icon())
        except Exception:
            pass
        self.entry = entry
        self.select_only = bool(select_only)
        # ★ V1.2.0：mode="plugins" 时数据源切到 Modrinth
        #   project_type=plugin + categories=paper，装进 plugins/ 目录
        self.mode = mode or "mods"
        self.is_plugins = (self.mode == "plugins")
        kw = "插件" if self.is_plugins else "模组"
        self.setWindowTitle(
            ("选择%s" % kw if self.select_only else "浏览%s" % kw)
            + " - %s" % entry.get("name", ""))
        self.resize(960, 680)

        self.server_dir = entry.get("server_dir") or entry["folder"]
        self.mc_version = entry.get("version") or ""
        # ★ V1.7.0-3：插件视图不再强制 paper —— Fabric / Forge /
        #   NeoForge 服也能查 project_type=plugin 的组件，加载器用
        #   服务器实际加载器（browse_projects 会按它过滤）。
        self.loader = (entry.get("loader") or "").lower()
        if self.loader == "unknown":
            self.loader = ""
        self.target_dir = os.path.join(
            self.server_dir, "plugins" if self.is_plugins else "mods")

        self.installed = self._installed_slugs()
        # ★ V1.8.0：双视图物理分离（QTabWidget）
        #   推荐安装 Tab：纯本地渲染（零网络），内存分页每页 20
        #   全库搜索 Tab：仅在搜索/回车/切到该 Tab 时发网络请求
        # ★ V1.8.4：按批加载（100 条/批）+ 批内 5 页本地切片翻页
        self.batch_size = 100      # 每次向后端请求的条数
        self.page_size = 20        # UI 每页显示的条数
        self.offset = 0            # 下一批请求起点（= len(current_batch)）
        self.total_hits = 0        # 后端返回的总结果数
        self.current_batch = []    # 已加载的全部数据（append 只增不减）
        # ★ 全局页码模型（彻底告别 local_page/批次双状态错乱）：
        #   UI 只看一个 global_page，切片 = current_batch[(g-1)*20 : g*20]，
        #   向前翻永远内存秒切（append 不覆盖，前面数据都在），
        #   向后翻数据未到时显示"正在加载..."并发起下一批请求。
        self.global_page = 1       # 当前全局页（1-based，无上限）
        self.local_page = 1        # 兼容旧引用（= global_page）
        self.batch_idx = 1         # 已加载批数（1-based，预加载/追加后 +1）
        self._fetching = False     # ★ 在途请求锁：禁止同一时刻重复拉同一批
        self.total = 0             # 兼容旧引用（= total_hits）
        self.hits = []             # 兼容旧引用（= current_batch）
        self.worker = None
        self.selected = []
        self.builtin_mode = True   # ★ 打开默认内置推荐
        self._slug_title = {}
        self._lib_searched = False  # 全库是否已搜过（切 Tab 才首搜）
        self.builtin_hits = []      # 推荐清单全量（内存）
        self.builtin_offset = 0     # 推荐分页偏移（内存）
        self.builtin_total = 0
        # ★ V1.8.9：并发竞态锁 —— 每次请求递增，过期回调直接丢弃
        self._req_id = 0
        self._pending = "first"     # 本次请求类型 first/next
        # ★ V1.9.0：无感预加载标记（local_page==4 时后台 append 下一批）
        self._preloading = False    # 是否正在后台预取下一批
        # ★ V1.8.10：图标显隐开关 + 缓存
        self.show_icons = entry.get("show_icons")
        if self.show_icons is None:
            self.show_icons = _global_show_icons()
        self._icon_cache = {}       # icon_url -> QPixmap
        self._icon_loading = set()  # 正在下载的 icon_url（防重复）
        self._ph_pixmap = None      # 灰底占位图（懒生成）
        # ★ V1.9.2：站点存在性缓存（slug -> {kind: True/False/None}）
        #   只有确认该模组在某个站上有信息，才显示那个站点的索引按钮。
        self._site_cache = {}
        self._site_loading = set()  # 正在探测的 slug（防重复）
        # ★ V1.9.5：推荐列表"适配探测"缓存（key -> (ok, why)）
        #   落盘缓存跨会话复用（TTL 24h），探测结果驱动勾选禁用与
        #   卡片红字原因 —— 不再"声称已过滤版本"却装不上。
        self._compat = install.compat_cache_load()
        self._compat_loading = set()  # 正在探测的 slug（防重复）
        self._icons_dir = os.path.join(
            os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
            "icons")

        v = QVBoxLayout(self)

        # ---- 第一行：固定的版本 / 加载器（初始化时已定死）----
        h = QHBoxLayout()
        h.addWidget(QLabel(
            "版本 <b>%s</b>　加载器 <b>%s</b>"
            % (self.mc_version or "未知", self.loader or "未知")))
        h.addStretch(1)
        self.lbl_mirror = QLabel("")
        self.lbl_mirror.setStyleSheet(_muted())
        h.addWidget(self.lbl_mirror)
        self.btn_mirror = QPushButton("镜像源设置")
        self.btn_mirror.setToolTip(
            "MCIM 公益镜像，默认『自动』：先试镜像，不通退回官方。")
        self.btn_mirror.clicked.connect(self.ask_mirror)
        h.addWidget(self.btn_mirror)
        self.btn_probe = QPushButton("测一下哪个能用")
        self.btn_probe.clicked.connect(self.do_probe)
        h.addWidget(self.btn_probe)
        # ★ V1.9.1：不再隐藏 —— 初始化向导（select_only）同样需要
        #   切换下载源与测速（两个入口必须同一套组件，逻辑一致）。
        v.addLayout(h)

        # ★ V1.8.0：物理分离双视图（QTabWidget）
        #   【推荐安装】：只读本地清单（MOD_TARGETS / PLUGIN_TARGETS），
        #     零网络请求，内存分页每页 20，瞬间渲染。
        #   【全库搜索】：仅搜索/回车/切到该 Tab 才发网络，翻页带 offset，
        #     等待时列表中央显示"正在搜索..."。
        self.tabs = QTabWidget()
        v.addWidget(self.tabs, 1)

        # ============ Tab1 推荐安装（本地，零网络） ============
        w_rec = QWidget()
        vr = QVBoxLayout(w_rec)
        vr.setContentsMargins(2, 2, 2, 2)
        self.lbl_view_hint = QLabel("")
        self.lbl_view_hint.setStyleSheet(_muted())
        vr.addWidget(self.lbl_view_hint)
        self.list_builtin = QListWidget()
        self.list_builtin.setViewMode(QListView.ListMode)
        self.list_builtin.setSelectionMode(QAbstractItemView.NoSelection)
        self.list_builtin.setUniformItemSizes(True)
        self.list_builtin.setSpacing(4)
        vr.addWidget(self.list_builtin, 1)
        hp_b = QHBoxLayout()
        self.btn_bp_prev = QPushButton("上一页")
        self.btn_bp_prev.clicked.connect(lambda: self._bpage(-1))
        hp_b.addWidget(self.btn_bp_prev)
        self.lbl_bpage = QLabel("第 1 页")
        hp_b.addWidget(self.lbl_bpage)
        self.btn_bp_next = QPushButton("下一页")
        self.btn_bp_next.clicked.connect(lambda: self._bpage(1))
        hp_b.addWidget(self.btn_bp_next)
        hp_b.addStretch(1)
        vr.addLayout(hp_b)
        self.tabs.addTab(w_rec, "推荐安装")

        # ============ Tab2 全库搜索（按需网络） ============
        w_lib = QWidget()
        vl = QVBoxLayout(w_lib)
        vl.setContentsMargins(2, 2, 2, 2)
        h = QHBoxLayout()
        self.ed_q = QLineEdit()
        self.ed_q.setPlaceholderText(
            "输入关键词搜全库（如 %s）"
            % ("essentialsx" if self.is_plugins else "sodium"))
        self.ed_q.returnPressed.connect(self.search)
        h.addWidget(self.ed_q, 1)
        h.addWidget(QLabel("源:"))
        self.cb_src = QComboBox()
        self.cb_src.addItem("Modrinth", "modrinth")
        self.cb_src.addItem("CurseForge", "curseforge")
        self.cb_src.setToolTip(
            "CurseForge 上有大量 Modrinth 没有的模组（如 Cupboard 生态）。\n"
            "走 MCIM 镜像时不需要 API key。")
        h.addWidget(self.cb_src)
        h.addWidget(QLabel("分类:"))
        self.cb_cat = QComboBox()
        self.cb_cat.addItem("(全部)", "")
        for slug, lab in modman.CATEGORIES:
            self.cb_cat.addItem(lab, slug)
        h.addWidget(self.cb_cat)
        if self.is_plugins:
            # ★ V1.9.0：插件筛选下拉 —— 默认全部（不按 loader 过滤，
            #   避免 facets 的 categories:paper 等误伤 bukkit/paper 插件）；
            #   显式选择 Fabric/Forge 时才加 categories 过滤。
            h.addWidget(QLabel("筛选:"))
            self.cb_pfilter = QComboBox()
            self.cb_pfilter.addItem("(全部)", "")
            self.cb_pfilter.addItem("Fabric", "fabric")
            self.cb_pfilter.addItem("Forge", "forge")
            self.cb_pfilter.addItem("NeoForge", "neoforge")
            self.cb_pfilter.addItem("Paper/Spigot", "paper")
            self.cb_pfilter.setToolTip(
                "默认『全部』= 不按加载器过滤（能看到所有服插件）；\n"
                "选某个加载器后才只看对应的插件。")
            h.addWidget(self.cb_pfilter)
        h.addWidget(QLabel("排序:"))
        self.cb_sort = QComboBox()
        for k, lab in modman.SORTS:
            self.cb_sort.addItem(lab, k)
        i = self.cb_sort.findData("downloads")
        if i >= 0:
            self.cb_sort.setCurrentIndex(i)
        h.addWidget(self.cb_sort)
        self.btn_go = QPushButton("搜索")
        self.btn_go.clicked.connect(self.search)
        h.addWidget(self.btn_go)
        vl.addLayout(h)
        # ★ V1.8.0：切分类 / 切排序 = 用户显式操作，触发全库搜索
        self.cb_cat.currentIndexChanged.connect(self.search)
        self.cb_sort.currentIndexChanged.connect(self.search)
        # ★ V1.8.6：切下载源（Modrinth/CurseForge）同样触发重搜
        self.cb_src.currentIndexChanged.connect(self.search)
        # ★ V1.9.0：插件筛选下拉（全部/Fabric/Forge...）触发重搜
        if self.is_plugins:
            self.cb_pfilter.currentIndexChanged.connect(self.search)

        # 列表 + 中央"正在搜索..."占位（QStackedLayout 切换）
        self._lib_stack = QStackedLayout()
        self.lbl_searching = QLabel("正在搜索...")
        self.lbl_searching.setAlignment(Qt.AlignCenter)
        self.lbl_searching.setStyleSheet(
            "color:#9aa0b0; font-size:16px; background:transparent;")
        w_wait = QWidget()
        w_wait.setStyleSheet("background:transparent;")
        lay_wait = QHBoxLayout(w_wait)
        lay_wait.addStretch(1)
        lay_wait.addWidget(self.lbl_searching)
        lay_wait.addStretch(1)
        self.list_lib = QListWidget()
        self.list_lib.setViewMode(QListView.ListMode)
        self.list_lib.setSelectionMode(QAbstractItemView.NoSelection)
        self.list_lib.setUniformItemSizes(True)
        self.list_lib.setSpacing(4)
        self._lib_stack.addWidget(w_wait)     # index 0：等待/空态
        self._lib_stack.addWidget(self.list_lib)  # index 1：结果
        w_stack = QWidget()
        w_stack.setLayout(self._lib_stack)
        vl.addWidget(w_stack, 1)

        hp = QHBoxLayout()
        self.btn_prev = QPushButton("上一页")
        self.btn_prev.clicked.connect(lambda: self._page(-1))
        hp.addWidget(self.btn_prev)
        self.lbl_page = QLabel("第 1 页")
        hp.addWidget(self.lbl_page)
        self.btn_next = QPushButton("下一页")
        self.btn_next.clicked.connect(lambda: self._page(1))
        hp.addWidget(self.btn_next)
        hp.addStretch(1)
        vl.addLayout(hp)
        self.tabs.addTab(w_lib, "全库搜索")

        # ★ V1.8.0：切到全库 Tab 且从未搜索过 → 自动首搜一次；
        #   切回推荐 Tab 不触发任何网络。
        self.tabs.currentChanged.connect(self._on_tab_switch)

        # ---- 底部公共：已选计数 + 操作 ----
        hp2 = QHBoxLayout()
        self.btn_adv = QPushButton("高级搜索")
        self.btn_adv.setToolTip(
            "打开旧版表格搜索：可切换 CurseForge、GitHub 安装等")
        self.btn_adv.clicked.connect(self.open_advanced)
        hp2.addWidget(self.btn_adv)
        if self.select_only:
            self.btn_adv.hide()
        hp2.addStretch(1)
        v.addLayout(hp2)

        hb = QHBoxLayout()
        self.btn_inst = QPushButton(
            ("确定选择" if self.select_only else "安装选中") + " (0)")
        self.btn_inst.setToolTip(
            "4 线程并发下载 + CDN 加速 + 断点续传 + 木马扫描")
        self.btn_inst.clicked.connect(self.do_install)
        hb.addWidget(self.btn_inst)
        self.btn_all = QPushButton("全选")
        self.btn_all.clicked.connect(lambda: self._set_all(True))
        hb.addWidget(self.btn_all)
        self.btn_none = QPushButton("全不选")
        self.btn_none.clicked.connect(lambda: self._set_all(False))
        hb.addWidget(self.btn_none)
        hb.addStretch(1)
        self.lbl_stat = QLabel("")
        self.lbl_stat.setStyleSheet(_muted())
        hb.addWidget(self.lbl_stat)
        v.addLayout(hb)

        # ---- 日志 ----
        self.log = LogView()
        self.log.setReadOnly(True)
        self.log.setMaximumHeight(100)
        v.addWidget(self.log)

        bb = QDialogButtonBox(QDialogButtonBox.Close)
        bb.rejected.connect(self.reject)
        v.addWidget(bb)

        # ★ V1.8.0：打开即渲染本地推荐（同步、零网络、毫秒级）。
        #   不再 QTimer.singleShot(60, self.search) —— 那是卡 3-4 秒的根源。
        self._load_builtin_local()

    # ---------- 数据 ----------
    def _installed_slugs(self):
        """已装模组/插件的名字集合（用来打『已装』标记）。

        ★ V1.7.3-1：归一化存储，判定端 _inst_hit 子串包含。
        """
        out = set()
        try:
            for m in modman.scan_mods_dir(self.target_dir):
                n = (m.get("name") or "").strip().lower()
                if n:
                    out.add(modman._norm(n))
                fn = (m.get("filename") or "").lower()
                if fn:
                    out.add(modman._norm(
                        fn.replace(".jar", "").replace(".disabled", "")))
        except Exception:
            pass
        return out

    def _params(self):
        p = {
            "query": self.ed_q.text().strip(),
            "category": self.cb_cat.currentData() or None,
            "sort": self.cb_sort.currentData() or "downloads",
            "offset": self.offset,
            "source": self.cb_src.currentData() or "modrinth",
        }
        # ★ V1.9.0：插件筛选下拉（默认全部=None，不 categories 过滤）
        if self.is_plugins:
            p["loader_filter"] = (getattr(self, "cb_pfilter", None)
                                  and self.cb_pfilter.currentData()
                                  or None)
        return p

    # ---------- ★ V1.8.0：双视图（推荐安装 / 全库搜索） ----------
    def _on_tab_switch(self, idx):
        """切 Tab：只有切到『全库搜索』且还没搜过才发一次网络请求。

        切回『推荐安装』不触发任何网络（本地清单已渲染）。
        """
        if idx == 1 and not self._lib_searched:
            self.search()

    def _current_list(self):
        """当前活动 Tab 对应的列表（全选/全不选作用于当前视图）。"""
        return self.list_lib if self.tabs.currentIndex() == 1 \
            else self.list_builtin

    # ---- 推荐安装 Tab：纯本地、零网络、内存分页 ----
    def _load_builtin_local(self):
        """同步渲染本地推荐清单。

        ★ V1.8.0：绝对不发任何网络请求 —— 不查 Modrinth、不拉图标
          （图标统一走卡片占位）。数据只来自 MOD_TARGETS / PLUGIN_TARGETS，
          分页在内存中完成（每页 20）。
        ★ V1.8.2：插件服推荐 = PLUGIN_TARGETS + MOD_TARGETS["paper"]
          （Cupboard 生态）按清单顺序平铺去重，无任何分组标题。
        """
        hits = []
        seen = set()
        if self.is_plugins:
            # ★ V1.9.1：插件推荐 = 纯 PLUGIN_TARGETS（全部是真插件）。
            #   不再混入 MOD_TARGETS["paper"]（用户指出推荐里出现模组）。
            sources = list(install.PLUGIN_TARGETS)
            for m in sources:
                if m["slug"] in seen:
                    continue
                seen.add(m["slug"])
                hits.append({
                    "slug": m["slug"], "title": m["name"],
                    "description": m["desc"],
                    "categories": [], "downloads": None,
                    "group": m.get("group", ""),
                    "req": m.get("req") or [], "icon_url": None,
                    # ★ V1.9.2：来源站（Cupboard 生态 = curseforge，
                    #   其余默认 modrinth）→ 索引按钮按来源先显示
                    "source": m.get("source") or "modrinth",
                    # ★ V1.9.5：GitHub 来源插件（MultiLogin）→ 适配探测走
                    #   GitHub Releases 平台过滤，不进 Modrinth/CF 查询
                    "repo": m.get("repo") or "",
                })
        else:
            lst = install.mod_list_for(self.loader, self.mc_version)
            try:
                from .core import install as _inst
                _skip = set(_inst.CLIENT_ONLY_MODS)
            except Exception:
                _skip = set()
            _skip.add("cloth-config")  # 防御：cloth-config 已移出清单
            for m in lst:
                if m["slug"] in _skip:
                    continue
                hits.append({
                    "slug": m["slug"], "title": m["name"],
                    "description": m["desc"],
                    "categories": [], "downloads": None,
                    "group": m.get("group", ""),
                    "req": m.get("req") or [], "icon_url": None,
                    # ★ V1.9.2：来源站（Cupboard 生态 = curseforge，
                    #   其余默认 modrinth）→ 索引按钮按来源先显示
                    "source": m.get("source") or "modrinth",
                    # ★ V1.9.5：透传 repo（当前模组清单没有 GitHub 项，
                    #   留字段备用，插件清单已用）
                    "repo": m.get("repo") or "",
                })
        self.builtin_hits = hits
        self.builtin_total = len(hits)
        self.builtin_offset = 0
        self.builtin_mode = True
        self.lbl_view_hint.setText(
            "本地推荐：已按 %s + %s 过滤版本，剔除纯客户端组件"
            % (self.mc_version or "?", self.loader or "?"))
        self._fill_builtin()
        # ★ V1.9.5：渲染后异步逐项探测真实适配性（不进列表不阻塞，
        #   结果回来驱动勾选禁用 / 红字原因，缓存命中则零请求）
        self._probe_compat()

    def _fill_builtin(self):
        """渲染推荐清单当前页（内存分页每页 20）。"""
        self.list_builtin.blockSignals(True)
        self.list_builtin.clear()
        self._slug_title = {}
        page = self.builtin_hits[
            self.builtin_offset:self.builtin_offset + 20]
        for h in page:
            slug = h.get("slug") or ""
            title = h.get("title") or slug
            self._slug_title[slug] = title
            it = QListWidgetItem()
            it.setData(Qt.UserRole, slug)
            it.setFlags(Qt.ItemIsEnabled)
            it.setSizeHint(QSize(0, 104))
            card, chk = self._make_card(
                h, self._local_icon_for(slug), True)
            it._chk = chk
            self.list_builtin.addItem(it)
            self.list_builtin.setItemWidget(it, card)
        self.list_builtin.blockSignals(False)
        self._sync_bpage()
        self.lbl_stat.setText("本地推荐 %d 个，显示 %d 个"
                              % (self.builtin_total, len(page)))
        self._sync_count()
        self._probe_sites()

    def _bpage(self, d):
        """推荐 Tab 内存分页（不发网络）。"""
        new = self.builtin_offset + d * 20
        if new < 0 or (self.builtin_total
                       and new >= self.builtin_total):
            return
        self.builtin_offset = new
        self._fill_builtin()

    def _sync_bpage(self):
        page = self.builtin_offset // 20 + 1
        self.lbl_bpage.setText("第 %d 页" % page)
        self.btn_bp_prev.setEnabled(self.builtin_offset > 0)
        self.btn_bp_next.setEnabled(
            bool(self.builtin_total)
            and self.builtin_offset + 20 < self.builtin_total)

    # ---- 全库搜索 Tab：按批加载（★ V1.8.4 / V1.9.1 全局页码模型）----
    def search(self):
        """显式搜索（首次/换关键词/切源/切分类）：重置全部状态，offset=0 请求首批。

        ★ 全局页码模型：current_batch 只增不减（append 式），
          global_page 是唯一页码状态，向前翻永远内存切片。
        """
        self.offset = 0
        self.current_batch = []
        self.total_hits = 0
        self.global_page = 1
        self.local_page = 1
        self.batch_idx = 1
        self._fetching = False
        self._pending = "first"
        self._run()

    # ---- 安全切片（start 越界兜底，绝不越界/串数据）----
    def _slice_page(self):
        """按 global_page 切出当前 20 条。

        start 已超出已加载数据 → 返回 None（调用方显示"正在加载..."
        并触发 _load_next_batch()）；否则 start/end 用 min 截断，
        末批不足 20 条也不会越界。
        """
        start = (self.global_page - 1) * self.page_size
        if start >= len(self.current_batch):
            return None
        end = min(start + self.page_size, len(self.current_batch))
        return self.current_batch[start:end]

    def _max_local_page(self):
        """已加载数据最多能切出几页（随 append 不断增长）。"""
        return max(1, (len(self.current_batch) + self.page_size - 1)
                   // self.page_size)

    def _page(self, d):
        """翻页（★ V1.9.1：全局页码，向前纯本地，向后按需拉批）。

        - 向后：数据已在 current_batch → 零网络秒切；
          数据未到（start 越界）→ 显示"正在加载..."并发起 _load_next_batch()。
        - 向前：global_page>1 一律本地切片（前面数据早已 append 在内存），
          永不触发网络、永无"回不去"。
        - 在途请求期间禁止重复发起同一批（_fetching 锁）。
        """
        if d > 0:
            nxt = self.global_page + 1
            if (nxt - 1) * self.page_size < len(self.current_batch):
                self.global_page = nxt
                self.local_page = nxt
                self._fill_lib()
                self._sync_page()
                return
            # 数据未加载到：先走到目标页（显示"正在加载..."），
            # 再兜底触发网络请求（防越界空页/死页）
            if len(self.current_batch) < self.total_hits:
                self.global_page = nxt
                self.local_page = nxt
                self._load_next_batch()
            return
        if d < 0:
            if self.global_page > 1:
                self.global_page -= 1
                self.local_page = self.global_page
                self._fill_lib()
                self._sync_page()

    def _load_next_batch(self):
        """★ 数据不足时拉下一批（返回后 append，不覆盖）。

        _fetching 锁：预加载/翻页兜底请求在途时，绝不重复请求同一批
        （否则两个请求返回同一批 append 两次 = 数据翻倍错乱）。
        """
        if self._fetching:
            return
        if len(self.current_batch) >= self.total_hits:
            self.lbl_stat.setText("已到底")
            return
        self._fetching = True
        self.offset = len(self.current_batch)
        self._pending = "next"
        self._run()

    def _run(self):
        """发起全库搜索请求。

        ★ V1.8.9：请求 ID 竞态锁（过期回调丢弃）+ 按钮防连点 + 防白屏。
        ★ V1.9.0：等待态统一列表中央显示；跨界加载提示"正在加载..."。
        """
        self._lib_searched = True
        self._req_id += 1
        req_id = self._req_id
        self._set_nav_enabled(False)
        pend = getattr(self, "_pending", "first")
        if pend == "first":
            self.lbl_stat.setText("正在搜索...")
        else:
            self.lbl_stat.setText("正在加载...")
        self._lib_wait(True)
        self.worker = _track(self, Worker(
            self._do_search, {**self._params(), "_req_id": req_id}, self))
        self.worker.log.connect(self._log)
        self.worker.done.connect(self._on_search)
        self.worker.start()

    def _set_nav_enabled(self, on):
        """★ V1.8.9 防连点：请求期间禁用搜索/翻页按钮。"""
        for b in (self.btn_prev, self.btn_next, self.btn_go):
            try:
                b.setEnabled(on)
            except Exception:
                pass

    def _lib_wait(self, waiting):
        """全库 Tab 列表中央显示/隐藏『正在搜索...』（不白屏）。"""
        try:
            self._lib_stack.setCurrentIndex(0 if waiting else 1)
        except Exception:
            pass

    def _fill_fail(self, msg="加载失败，请重试"):
        """失败/超时占位：列表里放一条不可勾选的失败项，绝不白屏。"""
        try:
            self.list_lib.blockSignals(True)
            self.list_lib.clear()
            it = QListWidgetItem(msg)
            it.setFlags(Qt.NoItemFlags)
            it.setTextAlignment(Qt.AlignCenter)
            it.setSizeHint(QSize(0, 96))
            self.list_lib.addItem(it)
            self.list_lib.blockSignals(False)
            self.lbl_stat.setText(msg)
        except Exception:
            pass

    def _do_search(self, query="", category=None, sort="downloads",
                   offset=0, source="modrinth", loader_filter=None,
                   _req_id=0, on_log=None):
        """后台按批搜索（limit=batch_size=100，offset 为批次起点）。

        ★ V1.8.6：支持 source="curseforge"（Modrinth/CurseForge 双源）。
        CurseForge 的 category 是数字 categoryId，Modrinth 的 slug
        不通用，切到 CF 时忽略分类过滤。
        ★ V1.8.9：永不抛异常 —— 成功/失败都返回 dict（带 _req_id），
        由 _on_search 按竞态锁统一裁决。
        ★ V1.9.0：插件模式默认不过滤 loader（loader_filter=None →
        不传 loader → facets 只保留 project_type + versions），
        避免 categories:paper 等 facet 误伤 bukkit/paper 标签插件；
        用户从"筛选"下拉显式选择后才加 categories 过滤。
        模组模式保持原逻辑：默认按服务器加载器过滤。
        """
        try:
            if source == "curseforge":
                category = None
            eff_loader = loader_filter
            if not self.is_plugins and eff_loader is None:
                eff_loader = self.loader
            r = modman.browse_projects(
                query=query, mc_version=self.mc_version,
                loader=eff_loader, category=category, sort=sort,
                limit=self.batch_size, offset=offset, source=source,
                project_type="plugin" if self.is_plugins else "mod")
            if r.get("err"):
                raise RuntimeError(r["err"])
            return {"hits": r.get("hits") or [],
                    "total": r.get("total") or 0,
                    "offset": offset, "error": r.get("error"),
                    "builtin": False, "_req_id": _req_id}
        except Exception as e:
            return {"error": repr(e), "builtin": False,
                    "_req_id": _req_id}

    def _on_search(self, ok, res):
        """搜索结果回调。

        ★ V1.8.9 竞态锁：req_id 不是最新 → 过期数据直接丢弃。
        ★ V1.9.0：非首次请求的结果 append 追加（绝不覆盖 current_batch）。
        """
        if not isinstance(res, dict) \
                or res.get("_req_id") != self._req_id:
            return  # ★ 过期请求（疯狂翻页时旧结果不覆盖新状态）
        self._set_nav_enabled(True)
        self._fetching = False
        pend = getattr(self, "_pending", "first")
        if res.get("error"):
            self._log("[失败] %s" % res["error"])
            self._lib_wait(False)
            # ★ V1.9.0：列表显示具体错误（如"官方 CF 需要 API Key..."）
            self._fill_fail(res["error"])
            return
        hits = res.get("hits") or []
        if pend == "first":
            self.current_batch = list(hits)
            self.total_hits = res.get("total") or 0
            self.global_page = 1
            self.local_page = 1
            self.batch_idx = 1
        else:
            # ★ 追加（append），下一批数据并入 current_batch，绝不覆盖
            self.current_batch.extend(hits)
            self.total_hits = res.get("total") or 0
            self.batch_idx = (len(self.current_batch) + 99) // 100
            # 等待"正在加载..."的那一页数据到位：保持在当前 global_page，
            # 若该页现在可切出则下面 _fill_lib 直接渲染；否则停留等更多数据
            if (self.global_page - 1) * self.page_size \
                    >= len(self.current_batch):
                self.global_page = self._max_local_page()
                self.local_page = self.global_page
        self.total = self.total_hits
        self.hits = self.current_batch
        self.offset = len(self.current_batch)
        self._lib_wait(False)
        self._fill_lib()
        self._sync_page()
        if not self.current_batch:
            self.lbl_stat.setText("暂无数据")
            self._log("[空] 没找到匹配，试试放宽筛选或换个关键词")
            return
        self._maybe_preload()

    # ---- 无感预加载（append 式，翻页永远零等待） ----
    def _maybe_preload(self):
        """已加载数据快翻到底、且还有下一批时，后台静默拉取并 append。

        用户看到接近当前批末尾时后台偷偷拉回下一批 → 点「下一页」
        时数据已在内存，直接切片渲染，永远看不到"正在加载..."转圈。
        请求携带 _req_id 竞态锁 + _fetching 在途锁：
        预加载进行中若用户点了搜索或翻页拉批，回调不会重复 append。
        """
        if self._preloading or self._fetching:
            return
        if self.global_page < self._max_local_page() \
                and (self.global_page + 1) * self.page_size \
                < len(self.current_batch):
            return  # 后面还有已加载数据，没必要现在预取
        if len(self.current_batch) >= self.total_hits:
            return
        self._preloading = True
        self._fetching = True
        self._req_id += 1
        req_id = self._req_id
        nxt = len(self.current_batch)
        w = _track(self, Worker(
            self._do_search,
            {**self._params(), "offset": nxt, "_req_id": req_id},
            self))
        w.log.connect(self._log)
        w.done.connect(self._on_preload)
        w.start()

    def _on_preload(self, ok, res):
        """预加载回调：req_id 匹配才 append；失败静默，翻页时再正常请求。"""
        self._preloading = False
        self._fetching = False
        if not isinstance(res, dict) \
                or res.get("_req_id") != self._req_id:
            return
        if res.get("error"):
            return
        self.current_batch.extend(res.get("hits") or [])
        self.total_hits = res.get("total") or 0
        self.total = self.total_hits
        self.batch_idx = (len(self.current_batch) + 99) // 100
        self.offset = len(self.current_batch)
        self._sync_page()
        # 若用户正停在"正在加载..."等待页，数据到位后立即补渲染
        if (self.global_page - 1) * self.page_size \
                < len(self.current_batch):
            self._fill_lib()

    # ---- ★ V1.8.10：图标（异步下载 + 缓存 + 显隐开关） ----
    def _placeholder(self):
        if self._ph_pixmap is None:
            self._ph_pixmap = _make_placeholder_pixmap()
        return self._ph_pixmap

    def _icon_pm_for(self, h):
        """给一条搜索结果显示的图标 pixmap（GUI 线程）。

        开关关 → None（纯文字）；有缓存 → 真实图；
        有 icon_url → 灰底占位（并触发异步下载）；否则 None。
        """
        if not self.show_icons:
            return None
        url = h.get("icon_url") or ""
        if url and url in self._icon_cache:
            return self._icon_cache[url]
        if url:
            self._spawn_icon_load([url])
            return self._placeholder()
        return None

    def _local_icon_for(self, slug):
        """推荐安装：本地 icons/<slug>.png，没有则首字母色块。"""
        try:
            p = os.path.join(self._icons_dir, "%s.png" % slug)
            if os.path.isfile(p):
                pm = QPixmap(p)
                if not pm.isNull():
                    return pm.scaled(
                        48, 48, Qt.KeepAspectRatio,
                        Qt.SmoothTransformation)
        except Exception:
            pass
        return _make_letter_pixmap(slug, 48)

    def _spawn_icon_load(self, urls):
        """为当前页启动一个后台图标下载线程（去重 + 缓存判断）。"""
        todo = []
        for u in dict.fromkeys(urls):
            if u and u not in self._icon_cache \
                    and u not in self._icon_loading:
                self._icon_loading.add(u)
                todo.append(u)
        if not todo:
            return
        w = IconWorker(todo, self)
        w.got.connect(self._on_icon)
        _track(self, w)
        w.start()

    def _on_icon(self, url, img):
        """图标下载完成：转 QPixmap 入缓存，替换对应卡片占位图。"""
        try:
            self._icon_loading.discard(url)
            if not self.show_icons:
                return
            pm = QPixmap.fromImage(img)
            if pm.isNull():
                return
            pm = pm.scaled(48, 48, Qt.KeepAspectRatio,
                           Qt.SmoothTransformation)
            self._icon_cache[url] = pm
            self._apply_icon(url, pm)
        except Exception:
            pass

    def _apply_icon(self, url, pm):
        """遍历正在显示的卡片，把 icon_url 匹配的占位替换成真图。"""
        for i in range(self.list_lib.count()):
            it = self.list_lib.item(i)
            w = self.list_lib.itemWidget(it)
            if w is None:
                continue
            if getattr(w, "_icon_url", None) == url \
                    and getattr(w, "_icon_lbl", None) is not None:
                try:
                    w._icon_lbl.setPixmap(pm)
                except Exception:
                    pass

    def _fill_lib(self):
        """渲染当前页。

        ★ V1.8.9：精准切片 —— start/end 用 min 截断，末批不足 20 条
        也不会越界或串数据。
        ★ V1.8.10：按 show_icons 开关渲染图标（占位 → 异步替换）。
        ★ V1.9.0：start 越界 → "正在加载..."并触发拉下一批；
          空数据 → "暂无数据"，绝不空白页。
        """
        self.list_lib.blockSignals(True)
        self.list_lib.clear()
        self._slug_title = {}
        page = self._slice_page()
        if page is None:
            # 数据未加载到：兜底（显示正在加载，随后自动拉下一批）
            self.list_lib.blockSignals(False)
            self.lbl_stat.setText("正在加载...")
            if len(self.current_batch) < self.total_hits:
                self._load_next_batch()
            return
        for h in page:
            slug = h.get("slug") or ""
            title = h.get("title") or slug
            self._slug_title[slug] = title
            it = QListWidgetItem()
            it.setData(Qt.UserRole, slug)
            it.setFlags(Qt.ItemIsEnabled)
            it.setSizeHint(QSize(0, 96))
            card, chk = self._make_card(
                h, self._icon_pm_for(h), False)
            it._chk = chk
            self.list_lib.addItem(it)
            self.list_lib.setItemWidget(it, card)
        self.list_lib.blockSignals(False)
        shown = self.list_lib.count()
        if not shown and not self.current_batch:
            self.lbl_stat.setText("暂无数据")
            self._sync_count()
            return
        self.lbl_stat.setText("共 %d 个结果，显示 %d 个"
                              % (self.total_hits, shown))
        self._sync_count()
        self._probe_sites()

    # ---------- PCL2 风格卡片 ----------
    # ★ V1.8.2：desc 末尾 [标签] → 彩色小标签（第三行渲染）。
    _TAG_COLORS = {
        "第三方登录": "#5b8def", "前置库": "#f59e0b",
        "Cupboard生态": "#f59e0b", "Cupboard附属": "#f59e0b",
        "优化": "#4ade80", "语音": "#4ade80",
        "监控": "#a78bfa", "崩溃分析": "#f87171", "工具": "#22d3ee",
        "权限": "#facc15", "聊天": "#f472b6", "聊天扩展": "#f472b6",
        "基础指令": "#60a5fa", "防熊": "#f87171", "建筑": "#22d3ee",
        "区域保护": "#4ade80", "基础前置": "#f59e0b",
    }

    def _make_card(self, h, icon_pm, builtin):
        """一行卡片：左侧勾选框 +（可选）48x48 图标，右侧名称/简介/标签/百科按钮。

        ★ V1.8.4：无图标时复选框+文字顶格左对齐（纯文字紧凑排版）。
        ★ V1.8.10：icon_pm 非 None 时在勾选框右侧插入图标；
          带 icon_url 的卡片记录 _icon_url/_icon_lbl，供异步下载完成后替换。
        """
        slug = h.get("slug") or ""
        title = h.get("title") or slug
        _raw_desc = (h.get("description") or "").replace("\n", " ")
        # ★ V1.8.2：提取 desc 里的 [标签] 用于彩色渲染，简介正文去掉标签
        _tags_desc = re.findall(r"\[([^\[\]]+)\]", _raw_desc)
        desc = re.sub(r"\[[^\[\]]*\]", " ", _raw_desc).strip()
        if len(desc) > 120:
            desc = desc[:117] + "..."
        dl = h.get("downloads")
        cats = h.get("categories") or []
        cat_txt = "、".join(
            dict(modman.CATEGORIES).get(c, c) for c in cats[:3])
        group = h.get("group") or ""
        req = h.get("req") or []
        installed = _inst_hit(slug, title, self.installed)

        card = QWidget()
        # ★ V1.6.2：卡片底色跟随主题（深色主题=深卡片，浅色主题=浅卡片），
        #   标题文字用主题字体色，彻底解决"黑字黑底看不见"。
        _card_bg = "#1e1f29"
        try:
            from .core import theme as _thm
            _card_bg = _thm.field_bg() or _card_bg
        except Exception:
            pass
        card.setStyleSheet(
            "QWidget { background: %s; border-radius: 8px; }" % _card_bg)
        lay = QHBoxLayout(card)
        lay.setContentsMargins(10, 8, 10, 8)
        lay.setSpacing(10)

        # 勾选框
        chk = QCheckBox()
        chk.setToolTip("勾选后点下方「%s」"
                       % ("确定选择" if self.select_only else "安装选中"))
        if installed:
            chk.setChecked(False)
        chk.stateChanged.connect(lambda _st: self._sync_count())
        lay.addWidget(chk)

        # ★ V1.8.10：可选图标（全库=占位/缓存图；推荐=本地图标/色块）
        if icon_pm is not None:
            lbl_icon = QLabel()
            lbl_icon.setFixedSize(48, 48)
            lbl_icon.setPixmap(icon_pm)
            lay.addWidget(lbl_icon)
            card._icon_url = h.get("icon_url") or ""
            card._icon_lbl = lbl_icon
        else:
            card._icon_url = ""
            card._icon_lbl = None

        # ★ V1.8.4：彻底移除图标列与 ? 占位，复选框+文字顶格左对齐
        # 右侧：三行文字 + 链接
        rt = QVBoxLayout()
        rt.setSpacing(2)

        h1 = QHBoxLayout()
        # ★ V1.6.2：标题颜色用当前主题字体色（深/浅主题都看得清）
        lb_title = QLabel("<b style='font-size:13px; color:%s;'>%s</b>"
                          % (_fg(), title))
        if installed:
            lb_title.setText(
                "<b style='font-size:13px; color:#4ade80;'>✓已装 %s</b>"
                % title)
        h1.addWidget(lb_title)
        h1.addStretch(1)
        # 一键查百科
        # ★ V1.9.2：先按数据来源确认"必有的站"，其它站探测到有信息
        #   才显示按钮 —— 该模组在某个站上没有信息时，不显示该站索引。
        if builtin:
            _src = h.get("source") or "modrinth"
        else:
            try:
                _src = self.cb_src.currentData() or "modrinth"
            except Exception:
                _src = "modrinth"
        _known = {"modrinth": _src == "modrinth",
                  "curseforge": _src == "curseforge",
                  "mcmod": False}
        b_mod = QPushButton("[Modrinth]")
        b_mod.setCursor(Qt.PointingHandCursor)
        b_mod.setStyleSheet("QPushButton{border:none;color:#5b8def;"
                            "font-size:11px;}"
                            "QPushButton:hover{color:#8ab4ff;}")
        b_mod.clicked.connect(
            lambda _c, s=slug: self._open_modlink("modrinth", s, title))
        b_mc = QPushButton("[MC百科]")
        b_mc.setCursor(Qt.PointingHandCursor)
        b_mc.setStyleSheet("QPushButton{border:none;color:#5b8def;"
                           "font-size:11px;}"
                           "QPushButton:hover{color:#8ab4ff;}")
        b_mc.clicked.connect(
            lambda _c, s=slug, n=title: self._open_modlink("mcmod", s, n))
        b_cf = QPushButton("[CurseForge]")
        b_cf.setCursor(Qt.PointingHandCursor)
        b_cf.setStyleSheet("QPushButton{border:none;color:#5b8def;"
                           "font-size:11px;}"
                           "QPushButton:hover{color:#8ab4ff;}")
        b_cf.clicked.connect(
            lambda _c, s=slug: self._open_modlink("curseforge", s, title))
        for _b, _k in ((b_mod, "modrinth"), (b_mc, "mcmod"),
                       (b_cf, "curseforge")):
            if not _known.get(_k):
                _b.hide()  # 未确认存在 → 先隐藏，探测到信息后再显示
        h1.addWidget(b_mod)
        h1.addWidget(b_mc)
        h1.addWidget(b_cf)
        # 记录站点按钮，供后台探测完成后按结果显隐
        card._site_btns = {"modrinth": b_mod, "mcmod": b_mc,
                           "curseforge": b_cf}
        card._site_slug = slug
        card._site_name = title
        rt.addLayout(h1)

        # 简介（灰色小字，两行截断）
        lb_desc = QLabel(desc)
        lb_desc.setWordWrap(True)
        lb_desc.setStyleSheet(
            "color:#9aa0b0; font-size:11px; background:transparent;")
        lb_desc.setMaximumHeight(30)  # 最多两行
        rt.addWidget(lb_desc)

        # 标签行（★ V1.8.2：纯平铺、无分组标题；desc 的 [标签] 彩色渲染）
        tags = []
        # ★ V1.9.5：推荐项先查适配缓存，未命中显示"适配检测中…"，
        #   探测结果回来由 _apply_compat 落卡片（禁用/红字/绿字）。
        _ck = None
        if builtin:
            # 彩色小标签排最前（PCL2 风格，第三行灰字）
            for t in _tags_desc:
                tags.append('<span style="color:%s; font-weight:bold;">'
                            '[%s]</span>'
                            % (self._TAG_COLORS.get(t, "#8ab4ff"), t))
            if req:
                tags.append("前置: " + "、".join(req))
            _ck = self._compat.get(self._compat_key(slug))
            if _ck and _ck[0] is False:
                tags.append('<span style="color:#ef4444;'
                            'font-weight:bold;">⚠ %s</span>'
                            % _esc_html(_ck[1]))
            elif _ck and _ck[0] is True:
                tags.append("版本兼容已按当前服过滤")
            else:
                tags.append("适配检测中…")
        else:
            if cat_txt:
                tags.append("分类 " + cat_txt)
            if dl:
                tags.append("下载 " + _fmt_num(dl))
            tags.append("%s + %s" % (self.mc_version or "?",
                                     self.loader or "?"))
        if installed:
            tags.append("已安装（勾选可覆盖更新）")
        lb_tags = QLabel("  ".join(tags))
        lb_tags.setTextFormat(Qt.RichText)
        lb_tags.setStyleSheet(
            "color:#6b7280; font-size:10px; background:transparent;")
        rt.addWidget(lb_tags)
        # ★ V1.9.5：卡片标签引用，供探测结果回来后原地替换红字
        card._tags_lbl = lb_tags
        if builtin and _ck and _ck[0] is False:
            chk.setEnabled(False)  # 缓存已知不适配 → 直接禁勾

        lay.addLayout(rt, 1)
        return card, chk

    @staticmethod
    def _open_modlink(kind, slug, name):
        """打开百科/详情页。"""
        from PySide6.QtGui import QDesktopServices
        from PySide6.QtCore import QUrl
        import urllib.parse as _up
        try:
            if kind == "modrinth":
                url = "https://modrinth.com/mod/%s" % slug
            elif kind == "mcmod":
                url = "https://search.mcmod.cn/s?key=%s" % (
                    _up.quote(name))
            else:
                url = "https://www.curseforge.com/minecraft/mc-mods/%s" % slug
            QDesktopServices.openUrl(QUrl(url))
        except Exception:
            pass

    # ---------- ★ V1.9.2：站点索引存在性探测 ----------
    #   卡片上的 [Modrinth] / [MC百科] / [CurseForge] 按钮只显示
    #   该模组确实有信息的站点：来源站直接显示，其余站后台探测
    #   （有 → 显示按钮，没有 / 无法确认 → 保持隐藏）。
    def _probe_sites(self):
        """收集当前两列表（全库/推荐）里还没结论的 (slug, kind) 并后台探测。

        缓存命中（_site_cache）零请求；同一 slug 在探测中不重复发起。
        """
        todo = []
        for lw in (self.list_lib, self.list_builtin):
            for i in range(lw.count()):
                it = lw.item(i)
                card = lw.itemWidget(it)
                if card is None or not getattr(card, "_site_btns", None):
                    continue
                slug = getattr(card, "_site_slug", "") or ""
                if not slug or slug in self._site_loading:
                    continue
                name = getattr(card, "_site_name", "") or slug
                st = self._site_cache.get(slug) or {}
                for kind, b in card._site_btns.items():
                    if b.isVisible() or kind in st:
                        continue  # 已显示，或已有结论（保持隐藏）
                    todo.append((slug, name, kind))
                if todo and slug not in self._site_loading:
                    self._site_loading.add(slug)
        if not todo:
            return
        self._probe_worker_ = _track(self, Worker(
            self._probe_worker, {"todo": todo}, self))
        self._probe_worker_.done.connect(self._apply_site_probe)
        self._probe_worker_.start()

    @staticmethod
    def _probe_worker(todo, on_log=None):
        """后台并发探测各站，返回 {slug: {kind: True/False/None}}。"""
        from .core import sources as _s
        import concurrent.futures
        out = {}
        with concurrent.futures.ThreadPoolExecutor(max_workers=8) as ex:
            futs = [(slug, kind,
                     ex.submit(_s.probe_site_exists, slug, name, kind))
                    for slug, name, kind in todo]
            for slug, kind, fut in futs:
                try:
                    out.setdefault(slug, {})[kind] = fut.result(timeout=15)
                except Exception:
                    out.setdefault(slug, {})[kind] = None
        return out

    def _apply_site_probe(self, ok, res):
        if not isinstance(res, dict):
            return
        for slug, m in res.items():
            self._site_loading.discard(slug)
            st = self._site_cache.setdefault(slug, {})
            st.update(m)
        for lw in (self.list_lib, self.list_builtin):
            for i in range(lw.count()):
                card = lw.itemWidget(lw.item(i))
                self._update_card_sites(card)

    def _update_card_sites(self, card):
        """按探测结论更新一张卡片的站点按钮显隐。"""
        btns = getattr(card, "_site_btns", None)
        if not btns:
            return
        slug = getattr(card, "_site_slug", "") or ""
        st = self._site_cache.get(slug) or {}
        for kind, b in btns.items():
            v = st.get(kind)
            if v is None:
                continue
            if v and not b.isVisible():
                b.show()
            elif not v and b.isVisible():
                b.hide()

    # ---------- ★ V1.9.5：推荐列表适配探测 ----------
    #   顶部"已按版本过滤"只是静态文案；真正能不能装在这里逐项
    #   探测（Modrinth / CurseForge / GitHub 三来源），结果驱动
    #   勾选禁用与卡片红字原因，缓存命中则零网络请求。
    def _compat_key(self, slug):
        return "%s|%s|%s" % (self.mc_version or "?", self.loader or "?",
                             slug)

    def _probe_compat(self):
        """收集推荐列表里还没结论的项，后台并发探测适配性。"""
        todo = []
        for h in self.builtin_hits:
            slug = h.get("slug") or ""
            if not slug or slug in self._compat_loading:
                continue
            if self._compat_key(slug) in self._compat:
                continue  # 缓存命中（含跨会话落盘缓存）
            todo.append((slug, {"slug": slug,
                                "source": h.get("source") or "modrinth",
                                "repo": h.get("repo") or ""}))
        if not todo:
            return
        for slug, _m in todo:
            self._compat_loading.add(slug)
        self._compat_worker_ = _track(self, Worker(
            self._compat_worker,
            {"todo": todo, "mc_version": self.mc_version,
             "loader": self.loader}, self))
        self._compat_worker_.done.connect(self._apply_compat)
        self._compat_worker_.start()

    @staticmethod
    def _compat_worker(kwargs, on_log=None):
        """后台并发探测每个推荐项的适配性（8 线程，单项限时 20s）。"""
        from .core import install as _inst
        import concurrent.futures
        todo = kwargs.get("todo") or []
        mc_version = kwargs.get("mc_version") or ""
        loader = kwargs.get("loader") or ""
        out = {}
        with concurrent.futures.ThreadPoolExecutor(max_workers=8) as ex:
            futs = [(slug, ex.submit(_inst.probe_compat, meta,
                                     mc_version, loader))
                    for slug, meta in todo]
            for slug, fut in futs:
                try:
                    r = fut.result(timeout=20)
                except Exception:
                    r = {"ok": None,
                         "why": "适配检测超时，安装时再确认"}
                out[slug] = r if isinstance(r, dict) \
                    else {"ok": None, "why": ""}
        return out

    def _apply_compat(self, ok, res):
        """探测结果落地：不适配 → 禁用勾选 + 红字原因；并写缓存。"""
        if not isinstance(res, dict):
            return
        changed = False
        for slug, r in res.items():
            self._compat_loading.discard(slug)
            if not isinstance(r, dict):
                continue
            key = self._compat_key(slug)
            cur = self._compat.get(key)
            if cur and cur[0] is not None:
                continue  # 已有结论的不覆盖（防止过期响应互踩）
            self._compat[key] = (r.get("ok"), r.get("why") or "")
            changed = True
        for i in range(self.list_builtin.count()):
            it = self.list_builtin.item(i)
            slug = it.data(Qt.UserRole)
            if not slug:
                continue
            r = self._compat.get(self._compat_key(slug))
            if not r or r[0] is not False:
                continue
            chk = getattr(it, "_chk", None)
            if chk is not None:
                chk.blockSignals(True)
                chk.setChecked(False)
                chk.setEnabled(False)
                chk.blockSignals(False)
            card = self.list_builtin.itemWidget(it)
            if card is not None and getattr(card, "_tags_lbl", None):
                card._tags_lbl.setText(
                    "<span style='color:#ef4444;font-weight:bold;'>"
                    "⚠ %s</span>" % _esc_html(r[1]))
        self._sync_count()
        if changed:
            install.compat_cache_save(self._compat)

    def _sync_count(self):
        n = len(self._checked_slugs())
        verb = "确定选择" if self.select_only else "安装选中"
        self.btn_inst.setText("%s (%d)" % (verb, n))

    def _checked_slugs(self):
        """★ V1.8.0：合并两个 Tab 的勾选（推荐 + 全库）。
        ★ V1.9.5：跳过不适配已禁用的项（disabled 的 chk 即使被
          程序化勾上也不算，绝不把装不了的项带进安装队列）。"""
        out = []
        for lst in (self.list_builtin, self.list_lib):
            for i in range(lst.count()):
                it = lst.item(i)
                chk = getattr(it, "_chk", None)
                if chk is not None and chk.isEnabled() \
                        and chk.isChecked():
                    s = it.data(Qt.UserRole)
                    if s:
                        out.append(s)
        return out

    def _set_all(self, on):
        """★ V1.8.0：作用于当前活动 Tab 的列表。
        ★ V1.9.5：全选跳过已禁用（不适配）的项。"""
        lst = self._current_list()
        for i in range(lst.count()):
            it = lst.item(i)
            chk = getattr(it, "_chk", None)
            if chk is not None and chk.isEnabled():
                chk.setChecked(on)
        self._sync_count()

    def _sync_page(self):
        """全库 Tab 页码与按钮（★ V1.9.1 全局页码模型）。

        页码 = global_page / 总页（ceil(total_hits/20)），不再用 offset
        参与显示计算 —— 修复"开局跳第 3/5/9 页"的错乱显示。
        下一页可用 = 当前页之后还有已加载数据 或 还有下一批可请求；
        上一页可用 = global_page>1（前面数据都在内存，纯本地）。
        """
        self.lbl_page.setText("第 %d 页" % self.global_page)
        self.btn_prev.setEnabled(self.global_page > 1)
        self.btn_next.setEnabled(
            self.global_page * self.page_size < len(self.current_batch)
            or len(self.current_batch) < self.total_hits)

    # ---------- 操作 ----------
    def open_advanced(self):
        """高级搜索：旧版表格（可切 CurseForge / GitHub 安装）。"""
        dlg = _themed(ModrinthDialog(self.entry, self))
        dlg.exec()
        self.installed = self._installed_slugs()
        self.search()

    def ask_mirror(self):
        """镜像源设置：非模态（show() 而非 exec()），实例由 self._src_dialog
        持有，点确定（accepted）后立即保存并刷新全库列表。"""
        try:
            d = getattr(self, "_src_dialog", None)
            if d is None:
                # ★ V1.9.0：修复旧代码 SourceDialog(self, self) 双参
                #   TypeError（__init__ 只接受一个 parent）被静默吞掉
                d = _themed(SourceDialog(self))
                self._src_dialog = d
                d.accepted.connect(self._on_src_saved)
            d.show()
            d.raise_()
            d.activateWindow()
        except Exception as e:
            self._log("[错误] 镜像源设置：%r" % e)

    def _on_src_saved(self):
        """★ V1.9.0：源设置保存后立即生效（写入 sources.json + 刷新 + 重搜）。"""
        try:
            from .core import sources as _src
            d = getattr(self, "_src_dialog", None)
            if d is None:
                return
            mirror, key = d.values()
            _src.set_mirror(mirror)
            _src.set_cf_key(key)
            ok_s, err_s = _src.save()
            self._sync_src_ui()
            if not ok_s:
                self._log("[警告] 源设置没保存上：%s" % err_s)
                QMessageBox.warning(
                    self, "没保存上",
                    "下载源设置没能写入文件，重启后会回到之前的源。\n\n%s"
                    % err_s)
            self._log("[源] 已切换到：%s" % _src.describe())
            if key:
                self._log("[源] CurseForge API Key 已保存（本地 sources.json）")
            # 保存后立即用新源重新搜索全库列表
            self.search()
        except Exception:
            pass

    def do_probe(self):
        """探测所有下载源（后台 Worker，避免卡 UI）。

        ★ V1.9.0：修复旧代码 _src.probe() 缺 name 参数导致的 TypeError；
          镜像 503 时在 UI 提示"镜像不可用，已自动切换官方源"。
        ★ 并发测速：probe_all_latency 并行请求并返回各源延迟（ms）。
        """
        try:
            from .core import sources as _src
            self.btn_probe.setEnabled(False)
            self._log("正在探测各下载源 ...")
            self.worker = _track(self, Worker(self._probe_task, {}, self))
            self.worker.log.connect(self._log)
            self.worker.done.connect(self._on_probe_done)
            self.worker.start()
        except Exception as e:
            self._log("[错误] %r" % e)

    def _probe_task(self, on_log=None):
        from .core import sources as _src
        return _src.probe_all_latency(timeout=8)

    def _on_probe_done(self, ok, res):
        self.btn_probe.setEnabled(True)
        try:
            from .core import sources as _src
            if not ok:
                self._log("[探测] 出错：%r" % (res,))
                return
            self._sync_src_ui()
            if not isinstance(res, list) or not res:
                return
            good = [r for r in res if r[1]]
            # 逐源报告延迟（★ 并发测速结果）
            names = {"mcim": "MCIM 镜像", "official": "官方源"}
            for name, okk, ms, why in res:
                lab = names.get(name, name)
                if okk:
                    self._log("[探测] %s 可用（%d ms）" % (lab, ms))
                else:
                    self._log("[探测] %s 不可用（%s）" % (lab, why))
            if not good:
                QMessageBox.warning(
                    self, "都不通",
                    "镜像和官方源都请求失败了。\n\n已自动保持在官方源。")
                self._log("[探测] 全部不可用")
                return
            # ★ V1.9.0：镜像 503/不可用 → UI 提示已自动切换官方源
            mci = next((o for n, o, _, _ in res if n == "mcim"), True)
            if not mci and _src.get_mirror() != "mcim":
                QMessageBox.information(
                    self, "镜像不可用",
                    "MCIM 镜像不可用，已自动切换官方源。\n"
                    "可在『镜像源设置』里手动改回，或检查网络。")
                self._log("[探测] MCIM 镜像不可用，已自动切换官方源")
            self._log("[探测] 完成，当前源：%s" % _src.describe())
        except Exception as e:
            self._log("[错误] %r" % e)

    def _sync_src_ui(self):
        try:
            from .core import sources as _src
            cur = _src.get_mirror()
            lab = {"auto": "自动（镜像优先，失败回退官方）",
                   "mcim": "MCIM 镜像", "official": "官方源"}.get(
                       cur, str(cur))
            self.lbl_mirror.setText("镜像: %s" % lab)
        except Exception:
            pass

    def do_install(self):
        slugs = self._checked_slugs()
        if not slugs:
            QMessageBox.information(
                self, "没勾选",
                "先勾选要装的%s（点卡片左侧的勾选框）"
                % ("插件" if self.is_plugins else "模组"))
            return
        # ★ 仅选择模式（初始化向导）：不在这里下载，
        #   直接 Accept，由调用方用 self.selected 统一安装
        if self.select_only:
            self.accept()
            return
        kw = "插件" if self.is_plugins else "模组"
        names = "\n".join(
            "· %s" % (self._slug_title.get(s, s))
            for s in slugs)[:500]
        r = QMessageBox.question(
            self, "确认安装",
            "把选中的 %d 个%s装到：\n%s\n\n%s\n\n"
            "4 线程并发下载 + CDN 加速 + 断点续传 + 木马扫描。\n"
            "装完重启服务端才生效。"
            % (len(slugs), kw, self.target_dir, names),
            QMessageBox.Yes | QMessageBox.No, QMessageBox.Yes)
        if r != QMessageBox.Yes:
            return
        self.btn_inst.setEnabled(False)
        self.worker = _track(self, Worker(
            self._do_install, {"slugs": slugs}, self))
        self.worker.log.connect(self._log)
        self.worker.done.connect(self._on_install)
        self.worker.start()

    def _do_install(self, slugs=None, on_log=None):
        """复用 install_mods / install_plugins（V1.2.0 插件分流）。"""
        if self.is_plugins:
            return install.install_plugins(
                self.server_dir, self.mc_version,
                loader=self.loader or "paper",
                on_log=on_log, only=slugs or [])
        r = install.install_mods(
            self.server_dir, self.mc_version, self.loader,
            on_log=on_log, only=slugs or [])
        return r

    def _on_install(self, ok, res):
        self.btn_inst.setEnabled(True)
        if not ok:
            self._log("[错误] %r" % (res,))
            return
        ok_l = res.get("ok") or []
        fail_l = res.get("fail") or []
        skip_l = res.get("skipped") or []
        self._log("")
        self._log("★ 完成：成功 %d，失败 %d，跳过 %d"
                  % (len(ok_l), len(fail_l), len(skip_l)))
        for f in fail_l:
            self._log("  [失败] %s - %s" % (f.get("name"), f.get("reason")))
        self.installed = self._installed_slugs()
        self.search()

    def _log(self, m):
        self.log.appendPlainText(m)


class ModDialog(QDialog):
    """模组管理：列出已装 mod，可启用/禁用/检查更新/打开文件夹。"""

    COLS = ["状态", "名称", "当前版本", "最新版本", "文件"]

    def closeEvent(self, e):
        """关闭前先停掉后台线程。

        ★ 不停的话：对话框销毁了、线程还在跑，
          Qt 会直接 abort 整个程序 —— 也就是"点了就闪退"。
        """
        try:
            _stop_threads(self)
        except Exception:
            pass
        try:
            super().closeEvent(e)
        except Exception:
            pass

    def __init__(self, entry, parent=None):
        super().__init__(parent)
        # 窗口图标（跟主程序一致）
        try:
            from .app import app_icon
            self.setWindowIcon(app_icon())
        except Exception:
            pass
        self.entry = entry
        self.setWindowTitle("模组管理 - %s" % entry.get("name", ""))
        self.resize(900, 600)

        self.server_dir = entry.get("server_dir") or entry["folder"]
        self.mc_version = entry.get("version") or ""
        self.loader = (entry.get("loader") or "").lower()
        if self.loader == "unknown":
            self.loader = ""
        self.results = {}   # path -> 检查结果（含下载链接）

        v = QVBoxLayout(self)

        # 顶部信息
        h = QHBoxLayout()
        h.addWidget(QLabel("版本: %s" % (self.mc_version or "未知")))
        h.addWidget(QLabel("加载器: %s" % (self.loader or "未知")))
        h.addStretch(1)
        btn_dir = QPushButton("打开 mods 文件夹")
        btn_dir.clicked.connect(self.open_dir)
        h.addWidget(btn_dir)
        btn_re = QPushButton("重新扫描")
        btn_re.clicked.connect(self.refresh)
        h.addWidget(btn_re)
        self.btn_dl = QPushButton("打开选中项的下载链接")
        self.btn_dl.clicked.connect(self.do_download)
        self.btn_dl.setToolTip("选中一个检查过更新的 mod，"
                               "在浏览器里打开它的下载页面")
        h.addWidget(self.btn_dl)
        btn_log = QPushButton("打开日志文件夹")
        btn_log.clicked.connect(self.open_log_dir)
        h.addWidget(btn_log)
        btn_browse = QPushButton("浏览装模组")
        btn_browse.setToolTip("PCL2 风格卡片视图：带图标和简介，"
                              "按当前版本/加载器筛选，勾选后并发下载")
        btn_browse.clicked.connect(self.open_browse)
        h.addWidget(btn_browse)
        v.addLayout(h)

        # 表格
        self.table = QTableWidget(0, len(self.COLS))
        self.table.setHorizontalHeaderLabels(self.COLS)
        self.table.verticalHeader().setVisible(False)
        self.table.setSelectionBehavior(QAbstractItemView.SelectRows)
        self.table.setEditTriggers(QAbstractItemView.NoEditTriggers)
        hh = self.table.horizontalHeader()
        hh.setSectionResizeMode(0, QHeaderView.ResizeToContents)
        hh.setSectionResizeMode(1, QHeaderView.ResizeToContents)
        hh.setSectionResizeMode(2, QHeaderView.ResizeToContents)
        hh.setSectionResizeMode(3, QHeaderView.ResizeToContents)
        hh.setSectionResizeMode(4, QHeaderView.Stretch)
        v.addWidget(self.table, 1)

        # 按钮
        hb = QHBoxLayout()
        self.btn_toggle = QPushButton("启用 / 禁用")
        self.btn_toggle.clicked.connect(self.do_toggle)
        hb.addWidget(self.btn_toggle)
        self.btn_upd = QPushButton("检查更新（选中）")
        self.btn_upd.clicked.connect(self.do_check_sel)
        hb.addWidget(self.btn_upd)
        self.btn_upd_all = QPushButton("一键检查全部更新")
        self.btn_upd_all.clicked.connect(self.do_check_all)
        hb.addWidget(self.btn_upd_all)
        self.btn_dl = QPushButton("下载选中项的更新")
        self.btn_dl.clicked.connect(self.do_download)
        hb.addWidget(self.btn_dl)
        hb.addStretch(1)
        v.addLayout(hb)

        # 日志区：可点链接
        self.log = LogView()
        self.log.setReadOnly(True)
        self.log.setMaximumHeight(110)
        v.addWidget(self.log)
        # 双击行 = 打开该 mod 的下载页
        self.table.setMouseTracking(True)
        self.table.itemEntered.connect(self._on_hover)
        self.table.doubleClicked.connect(self._on_double)

        bb = QDialogButtonBox(QDialogButtonBox.Close)
        bb.rejected.connect(self.reject)
        v.addWidget(bb)

        self.mods = []
        self.refresh()


    def open_browse(self):
        """打开 PCL2 风格模组浏览（卡片视图，按当前服筛选）。"""
        if self.loader not in ("fabric", "forge", "neoforge"):
            QMessageBox.information(
                self, "当前加载器不支持模组",
                "只有 Fabric / Forge / NeoForge 服务器能装模组。\n\n"
                "当前加载器：%s" % (self.loader or "未知"))
            return
        dlg = _themed(ModBrowseDialog(self.entry, self))
        dlg.exec()
        # 装完回来刷新一下列表
        self.refresh()

    def _on_hover(self, item):
        pass

    def _on_double(self, index):
        """双击一行 -> 打开该 mod 的下载链接。"""
        r = index.row()
        if r < 0 or r >= len(self.mods):
            return
        m = self.mods[r]
        res = self.results.get(m["path"]) or {}
        url = res.get("url") or ""
        if not url or url.startswith("https://modrinth.com/mod/") is False:
            if not url:
                # 没查过，先查一下
                QMessageBox.information(
                    self, "提示",
                    "这个 mod 还没检查过更新，\n"
                    "先点『检查更新（选中）』。")
                return
        self._open_url(url)

    def do_download(self):
        """打开选中 mod 的下载链接。"""
        m = self._cur()
        if not m:
            QMessageBox.information(self, "提示", "先选中一个 mod")
            return
        res = self.results.get(m["path"]) or {}
        url = res.get("url") or ""
        if not url:
            QMessageBox.information(
                self, "提示",
                "还没检查过这个 mod，先点『检查更新（选中）』。")
            return
        if res.get("status") == "latest":
            r = QMessageBox.question(
                self, "已是最新",
                "这个 mod 已是最新版（%s）。\n\n"
                "仍要打开它的 Modrinth 页面吗？" % res.get("latest", ""),
                QMessageBox.Yes | QMessageBox.No, QMessageBox.No)
            if r != QMessageBox.Yes:
                return
        self._open_url(url)

    def open_log_dir(self):
        """打开日志文件夹 / latest.log。"""
        for d in (os.path.join(self.server_dir, "logs"),
                  os.path.join(self.server_dir, "server", "logs")):
            if os.path.isdir(d):
                _open_in_explorer(d)
                self._log("[提示] 日志目录: %s" % d)
                return
        for d in (self.server_dir,
                  os.path.join(self.server_dir, "server")):
            lp = os.path.join(d, "latest.log")
            if os.path.isfile(lp):
                _open_in_explorer(lp)
                self._log("[提示] 日志文件: %s" % lp)
                return
        _open_in_explorer(self.server_dir)
        self._log("[提示] 没找到 logs/ 或 latest.log，已打开服务器目录")

    def _open_url(self, url):
        import webbrowser
        try:
            webbrowser.open(url)
            self._log("已打开浏览器: %s" % url)
        except Exception as e:
            QMessageBox.warning(
                self, "打不开",
                "没法自动打开浏览器：%r\n\n链接是：\n%s" % (e, url))

    def _log(self, m):
        """写日志。里面的 http 链接渲染成可点击的蓝字。"""
        import re as _re
        txt = str(m)
        urls = _re.findall(r"https?://[^\s]+", txt)
        if urls:
            # 先转义 HTML，再把 URL 包成 <a>
            esc = (txt.replace("&", "&amp;")
                      .replace("<", "&lt;").replace(">", "&gt;"))
            for u in urls:
                esc = esc.replace(
                    u, '<a href="%s" style="color:#4da3ff;">%s</a>'
                       % (u.replace('"', "%22"), u))
            self.log.appendHtml(esc)
        else:
            self.log.appendPlainText(txt)

    def open_dir(self):
        d = os.path.join(self.server_dir, "mods")
        if not os.path.isdir(d):
            try:
                os.makedirs(d, exist_ok=True)
            except Exception as e:
                QMessageBox.warning(self, "失败", repr(e))
                return
        _open_in_explorer(d)

    def refresh(self):
        self.mods = modman.scan_mods(self.server_dir)
        self.table.setRowCount(0)
        for m in self.mods:
            r = self.table.rowCount()
            self.table.insertRow(r)
            st = "启用" if m["enabled"] else "禁用"
            self.table.setItem(r, 0, QTableWidgetItem(st))
            if not m["enabled"]:
                self.table.item(r, 0).setForeground(QColor(_on_bg("#c8c8c8")))
            nm = m["name"]
            if m["guess"]:
                nm += "  (?)"
            self.table.setItem(r, 1, QTableWidgetItem(nm))
            self.table.setItem(r, 2, QTableWidgetItem(m["version"]))
            self.table.setItem(r, 3, QTableWidgetItem("-"))
            self.table.setItem(r, 4, QTableWidgetItem(m["filename"]))
        if not self.mods:
            self._log("没找到 mod（mods 目录为空或不存在）")

    def _cur(self):
        r = self.table.currentRow()
        if r < 0 or r >= len(self.mods):
            return None
        return self.mods[r]

    def do_toggle(self):
        m = self._cur()
        if not m:
            QMessageBox.information(self, "提示", "先选中一个 mod")
            return
        want = not m["enabled"]
        word = "禁用" if not want else "启用"
        r = QMessageBox.question(
            self, "确认",
            "%s：\n  %s\n\n（通过改名实现，不会删除文件）\n\n继续？"
            % (word, m["filename"]))
        if r != QMessageBox.Yes:
            return
        try:
            new = modman.set_enabled(m["path"], want)
            self._log("[OK] %s -> %s" % (word, new))
            self.refresh()
        except Exception as e:
            QMessageBox.critical(self, "失败", repr(e))

    def _check_one(self, m):
        r = self._row_of(m)
        if r is None:
            return None
        if not self.mc_version or not self.loader:
            self.table.setItem(r, 3, QTableWidgetItem("缺版本/加载器"))
            return None
        self.table.setItem(r, 3, QTableWidgetItem("查询中..."))
        QApplication.processEvents()

        res = modman.check_update(m, self.mc_version, self.loader,
                                  on_log=lambda x: None)
        self.results[m["path"]] = res
        status = res.get("status") or (
            "error" if res.get("err") else "unknown")
        latest = res.get("latest") or ""
        url = res.get("url") or ""
        method = res.get("method") or ""

        self.results[m.get("path", "")] = res
        if status == "update":
            txt = "%s  ← 可更新" % latest
            self.table.setItem(r, 3, QTableWidgetItem(txt))
            self.table.item(r, 3).setForeground(QColor(_on_bg("#ffd166")))
            self.table.item(r, 3).setToolTip(
                "当前 %s → 最新 %s\n%s" % (res.get("cur", "?"),
                                          latest, url))
            self._log("[可更新] %s : %s -> %s"
                      % (m["name"], res.get("cur", "?"), latest))
            if url:
                self._log("         下载: %s" % url)
                self._log("         （选中该行点『下载选中项的更新』"
                          "可直接打开）")
        elif status == "latest":
            txt = "%s  [最新]" % (latest or res.get("cur", "?"))
            self.table.setItem(r, 3, QTableWidgetItem(txt))
            self.table.item(r, 3).setForeground(QColor(_on_bg("#06d6a0")))
            self._log("[最新] %s (%s)" % (m["name"], latest))
        elif status == "error":
            msg = (res.get("err") or "查询失败")[:30]
            self.table.setItem(r, 3, QTableWidgetItem(msg))
            self.table.item(r, 3).setForeground(QColor(_on_bg("#ef476f")))
            self._log("[失败] %s : %s" % (m["name"], res.get("err")))
        else:
            msg = (res.get("err") or "Modrinth 上没查到")[:30]
            self.table.setItem(r, 3, QTableWidgetItem(msg))
            self.table.item(r, 3).setForeground(QColor(_on_bg("#b5b5b5")))
            self.table.item(r, 3).setToolTip(
                "Modrinth 的文件库里没有这个 jar 的记录。\n"
                "通常是因为：\n"
                "  · 这个 mod 只发在 CurseForge\n"
                "  · 你这版是自己改过/重新打包的\n"
                "  · 版本号不在 Modrinth 上")
            self._log("[没查到] %s : %s" % (m["name"],
                                            res.get("err") or "无记录"))
        return status

    def do_check_all(self):
        if not self.mods:
            return
        if not self.mc_version or not self.loader:
            QMessageBox.warning(
                self, "无法检查",
                "需要知道版本和加载器。\n"
                "当前识别为：版本 %s，加载器 %s"
                % (self.mc_version or "未知", self.loader or "未知"))
            return
        self._log("开始检查 %d 个 mod ..." % len(self.mods))
        self._log("方式：先用文件哈希查 Modrinth（准），"
                  "查不到再按名字搜")
        QApplication.processEvents()

        tally = {"update": 0, "latest": 0, "unknown": 0, "error": 0}
        for m in self.mods:
            try:
                st = self._check_one(m)
                if st in tally:
                    tally[st] += 1
            except Exception as ex:
                tally["error"] += 1
                self._log("[异常] %s : %r" % (m.get("name"), ex))
            QApplication.processEvents()

        self._log("")
        self._log("检查完毕 —— 可更新 %d，已最新 %d，没查到 %d，失败 %d"
                  % (tally["update"], tally["latest"],
                     tally["unknown"], tally["error"]))
        if tally["update"]:
            self._log("黄色行 = 有新版本。")
            self._log("打开下载：选中该行点『下载选中项的更新』，"
                      "或直接双击该行。")
        if tally["unknown"]:
            self._log("灰色行 = Modrinth 文件库里没有记录"
                      "（多半只发在 CurseForge），不是检查失败。")

    def _row_of(self, m):
        for i, x in enumerate(self.mods):
            if x["path"] == m["path"]:
                return i
        return None

    def do_check_sel(self):
        m = self._cur()
        if not m:
            QMessageBox.information(self, "提示", "先选中一个 mod")
            return
        self._check_one(m)




def _open_in_explorer(path):
    """在文件管理器里打开一个目录。"""
    try:
        if sys.platform.startswith("win"):
            os.startfile(path)
        elif sys.platform == "darwin":
            # ★ V1.6.2：os.system 全改 subprocess.run（跨平台无黑框）
            import subprocess as _sp
            _sp.run(["open", path],
                    creationflags=getattr(_sp, "CREATE_NO_WINDOW", 0))
        else:
            import subprocess as _sp
            _sp.run(["xdg-open", path],
                    creationflags=getattr(_sp, "CREATE_NO_WINDOW", 0))
    except Exception:
        pass


class BackupDialog(QDialog):
    """备份管理：列出该服的存档和已有备份，支持备份/恢复/删除。"""

    def closeEvent(self, e):
        """关闭前先停掉后台线程。

        ★ 不停的话：对话框销毁了、线程还在跑，
          Qt 会直接 abort 整个程序 —— 也就是"点了就闪退"。
        """
        try:
            _stop_threads(self)
        except Exception:
            pass
        try:
            super().closeEvent(e)
        except Exception:
            pass

    def __init__(self, entry, parent=None, running_fn=None,
                 backup_format=None):
        super().__init__(parent)
        # 窗口图标（跟主程序一致）
        try:
            from .app import app_icon
            self.setWindowIcon(app_icon())
        except Exception:
            pass
        self.entry = entry
        # 主窗口传进来的"这个服在不在跑"查询函数
        self.running_fn = running_fn
        # ★ V1.1.0：备份格式（"folder" / "zip"），默认文件夹
        self.backup_format = backup_format or "folder"
        self.setWindowTitle("备份管理 - %s" % entry.get("name", ""))
        self.resize(760, 560)

        self.server_dir = entry.get("server_dir") or entry["folder"]
        v = QVBoxLayout(self)

        # ---- 存档列表 ----
        g1 = QGroupBox("存档（只备份这些，不会碰整个服务端）")
        v1 = QVBoxLayout(g1)
        self.world_list = QListWidget()
        v1.addWidget(self.world_list)
        v.addWidget(g1)

        self.worlds = backup.find_worlds(self.server_dir)
        for wd in self.worlds:
            it = QListWidgetItem(
                "%s      [%.1f MB, %d 个 region]\n    %s"
                % (wd["name"], wd["size_mb"], wd["regions"], wd["rel"]))
            it.setData(Qt.UserRole, wd["path"])
            self.world_list.addItem(it)
        if not self.worlds:
            self.world_list.addItem("(没找到存档，确认 level.dat 是否存在)")
        else:
            self.world_list.setCurrentRow(0)

        # ---- 选项 ----
        h = QHBoxLayout()
        self.chk_zip = QCheckBox("压缩成 zip（省空间，但慢）")
        # ★ V1.1.0：默认勾选状态跟随服务器配置的备份格式
        self.chk_zip.setChecked(self.backup_format == "zip")
        h.addWidget(self.chk_zip)
        h.addStretch(1)
        v.addLayout(h)

        # ---- 备份列表 ----
        self.g2 = QGroupBox("已有备份")
        v2 = QVBoxLayout(self.g2)
        self.lbl_bkdir = QLabel("")
        self.lbl_bkdir.setWordWrap(True)
        self.lbl_bkdir.setStyleSheet(_muted())
        v2.addWidget(self.lbl_bkdir)
        self.bk_list = QListWidget()
        v2.addWidget(self.bk_list)
        v.addWidget(self.g2, 1)
        self.refresh_backups()

        # ---- 按钮 ----
        hb = QHBoxLayout()
        self.btn_now = QPushButton("立即备份选中存档")
        self.btn_now.clicked.connect(self.do_backup)
        hb.addWidget(self.btn_now)
        self.btn_restore = QPushButton("用备份替换当前存档")
        self.btn_restore.clicked.connect(self.do_restore)
        hb.addWidget(self.btn_restore)
        self.btn_del = QPushButton("删除备份")
        self.btn_del.clicked.connect(self.do_delete)
        hb.addWidget(self.btn_del)
        hb.addStretch(1)
        v.addLayout(hb)

        self.log = LogView()
        self.log.setReadOnly(True)
        self.log.setMaximumHeight(120)
        v.addWidget(self.log)

        hb2 = QHBoxLayout()
        b1 = QPushButton("打开服务器文件夹")
        b1.clicked.connect(
            lambda: _open_in_explorer(self.server_dir))
        hb2.addWidget(b1)
        b2 = QPushButton("打开备份文件夹")
        b2.clicked.connect(self.open_backup_dir)
        hb2.addWidget(b2)
        b3 = QPushButton("打开选中存档文件夹")
        b3.clicked.connect(self.open_world_dir)
        hb2.addWidget(b3)
        b4 = QPushButton("刷新备份列表")
        b4.clicked.connect(self.refresh_backups)
        hb2.addWidget(b4)
        b5 = QPushButton("打开日志文件夹")
        b5.clicked.connect(self.open_log_dir)
        hb2.addWidget(b5)
        hb2.addStretch(1)
        v.addLayout(hb2)

        bb = QDialogButtonBox(QDialogButtonBox.Close)
        bb.rejected.connect(self.reject)
        v.addWidget(bb)


    def open_log_dir(self):
        """打开日志文件夹（找不到就打开服务器目录）。"""
        for d in (os.path.join(self.server_dir, "logs"),
                  os.path.join(self.server_dir, "server", "logs")):
            if os.path.isdir(d):
                _open_in_explorer(d)
                self._log("[提示] 日志目录: %s" % d)
                return
        for d in (self.server_dir,
                  os.path.join(self.server_dir, "server")):
            lp = os.path.join(d, "latest.log")
            if os.path.isfile(lp):
                _open_in_explorer(lp)
                self._log("[提示] 日志文件: %s" % lp)
                return
        _open_in_explorer(self.server_dir)
        self._log("[提示] 没找到 logs/ 或 latest.log，已打开服务器目录")

    def open_backup_dir(self):
        d = os.path.join(self.server_dir, backup.BACKUP_DIRNAME)
        if not os.path.isdir(d):
            r = QMessageBox.question(self, "还没有备份",
                                     "备份文件夹不存在，要创建吗？")
            if r != QMessageBox.Yes:
                return
            try:
                os.makedirs(d, exist_ok=True)
            except Exception as e:
                QMessageBox.warning(self, "失败", repr(e))
                return
        _open_in_explorer(d)

    def open_world_dir(self):
        wp = self._cur_world()
        if not wp:
            QMessageBox.information(self, "提示", "先选中一个存档")
            return
        _open_in_explorer(wp)

    def _log(self, m):
        self.log.appendPlainText(m)

    def refresh_backups(self):
        """刷新备份列表，并显示备份目录在哪。"""
        self.bk_list.clear()
        self.backups = backup.list_backups(self.server_dir)
        try:
            dg = backup.diagnose(self.server_dir)
        except Exception:
            dg = None
        if dg and dg.get("dirs_found"):
            self.lbl_bkdir.setText(
                "备份目录: %s" % "  |  ".join(dg["dirs_found"]))
        else:
            miss = (dg or {}).get("dirs_missing") or []
            self.lbl_bkdir.setText(
                "备份目录还没建（备份时会自动创建）。\n找过: %s"
                % ", ".join(miss[:2]))
        for b in self.backups:
            tag = "" if b.get("confirmed", True) else "  [未确认]"
            it = QListWidgetItem(
                "%s%s   [%s, %.1f MB]\n    %s"
                % (b["name"], tag,
                   "zip" if b["kind"] == "zip" else "文件夹",
                   b["size_mb"], b.get("time", "")))
            it.setData(Qt.UserRole, b["path"])
            it.setToolTip(b["path"])
            self.bk_list.addItem(it)
        if not self.backups:
            self.bk_list.addItem("(还没有备份)")

    def _cur_world(self):
        it = self.world_list.currentItem()
        if not it:
            return None
        return it.data(Qt.UserRole)

    def _cur_backup(self):
        it = self.bk_list.currentItem()
        if not it:
            return None
        return it.data(Qt.UserRole)

    def do_backup(self):
        wp = self._cur_world()
        if not wp:
            QMessageBox.warning(self, "提示", "先选中一个存档")
            return
        self._log("备份中，请稍候 ...")
        QApplication.processEvents()
        try:
            r = backup.backup_world(wp, self.server_dir,
                                    compress=self.chk_zip.isChecked(),
                                    on_log=self._log)
            self.refresh_backups()
            QMessageBox.information(self, "完成",
                                    "备份完成：\n%s\n%.1f MB"
                                    % (os.path.basename(r["path"]),
                                       r["size_mb"]))
        except Exception as e:
            QMessageBox.critical(self, "失败", repr(e))

    def do_restore(self):
        bp = self._cur_backup()
        wp = self._cur_world()
        if not bp:
            QMessageBox.warning(self, "提示", "先选中一个备份")
            return
        if not wp:
            QMessageBox.warning(self, "提示", "先选中要被替换的存档")
            return
        bn = os.path.basename(bp)
        wn = os.path.basename(wp.rstrip("\\/"))
        r = QMessageBox.question(
            self, "确认替换",
            "将用备份：\n  %s\n\n替换当前存档：\n  %s\n\n"
            "当前存档会被重命名保留（world_old_时间戳），确认继续？"
            % (bn, wn))
        if r != QMessageBox.Yes:
            return
        # 二次确认
        r2 = QMessageBox.warning(
            self, "再次确认",
            "这个操作会替换存档数据，确定？",
            QMessageBox.Yes | QMessageBox.No, QMessageBox.No)
        if r2 != QMessageBox.Yes:
            return
        self._log("恢复中 ...")
        QApplication.processEvents()
        try:
            res = backup.restore_backup(bp, wp, on_log=self._log)
            QMessageBox.information(
                self, "完成",
                "已恢复。\n旧存档保留为：\n%s\n\n确认没问题后可手动删除。"
                % os.path.basename(res["old"]))
        except Exception as e:
            QMessageBox.critical(self, "失败", repr(e))

    def _is_running(self):
        """这个服务器现在在不在跑。"""
        try:
            if callable(self.running_fn):
                return bool(self.running_fn())
        except Exception:
            pass
        return False

    def do_delete(self):
        bp = self._cur_backup()
        if not bp:
            QMessageBox.warning(self, "提示", "先选中一个备份")
            return

        # 服务器还在跑的话，存档文件被占用，多半删不干净
        if self._is_running():
            r = QMessageBox.warning(
                self, "服务器还在运行",
                "这个服务器正在运行，存档文件被占用。\n\n"
                "这时删除备份很可能【删不干净】，只剩下一部分残骸。\n\n"
                "建议先停止服务器再删。\n\n仍要继续吗？",
                QMessageBox.Yes | QMessageBox.No, QMessageBox.No)
            if r != QMessageBox.Yes:
                return

        r = QMessageBox.question(self, "确认",
                                 "删除备份：\n%s\n\n不可恢复，确定？"
                                 % os.path.basename(bp))
        if r != QMessageBox.Yes:
            return

        self._log("删除中 ...")
        QApplication.processEvents()
        try:
            st = backup.delete_backup(bp, on_log=self._log)
        except Exception as e:
            QMessageBox.critical(self, "失败", repr(e))
            self.refresh_backups()
            return

        self.refresh_backups()

        if st.get("gone") and not st.get("failed"):
            self._log("[OK] 已删除干净")
            return

        # 删不干净 —— 必须说清楚，不能假装成功
        if st.get("gone"):
            QMessageBox.information(
                self, "已删除",
                "已删除（过程中有 %d 项报错，但最终删干净了）。"
                % st.get("failed", 0))
            return

        left = st.get("left_mb", 0.0)
        nf = st.get("failed", 0)
        sample = ""
        if st.get("failed_paths"):
            try:
                rel = os.path.relpath(st["failed_paths"][0][0], bp)
            except Exception:
                rel = st["failed_paths"][0][0]
            sample = "\n\n例如：%s\n原因：%s" % (rel,
                                                st["failed_paths"][0][1])
        box = QMessageBox(self)
        box.setIcon(QMessageBox.Warning)
        box.setWindowTitle("没删干净")
        box.setText("没删干净，还剩 %.1f MB（%d 项删不掉）。%s"
                    % (left, nf, sample))
        box.setInformativeText(
            "常见原因：\n"
            "  1) 服务器还在运行，文件被占用 —— 先停止服务器再删\n"
            "  2) 文件只读 —— 本程序已尝试自动改权限\n"
            "  3) 资源管理器 / 压缩软件正打开着这个备份\n\n"
            "可以关掉占用的程序后重试，或打开文件夹手动删除。")
        b_retry = box.addButton("重试", QMessageBox.AcceptRole)
        b_open = box.addButton("打开文件夹手动删", QMessageBox.ActionRole)
        box.addButton("知道了", QMessageBox.RejectRole)
        box.exec()
        if box.clickedButton() is b_retry:
            self.do_delete()
        elif box.clickedButton() is b_open:
            try:
                _open_in_explorer(os.path.dirname(bp)
                                  if not os.path.isdir(bp) else bp)
            except Exception:
                pass
            self.refresh_backups()


class MigrateDialog(QDialog):
    """搬迁设置。"""

    def closeEvent(self, e):
        """关闭前先停掉后台线程。

        ★ 不停的话：对话框销毁了、线程还在跑，
          Qt 会直接 abort 整个程序 —— 也就是"点了就闪退"。
        """
        try:
            _stop_threads(self)
        except Exception:
            pass
        try:
            super().closeEvent(e)
        except Exception:
            pass

    def __init__(self, src_root, parent=None):
        super().__init__(parent)
        # 窗口图标（跟主程序一致）
        try:
            from .app import app_icon
            self.setWindowIcon(app_icon())
        except Exception:
            pass
        self.setWindowTitle("统一搬迁")
        self.resize(700, 460)
        v = QVBoxLayout(self)

        h = QHBoxLayout()
        h.addWidget(QLabel("源目录:"))
        self.ed_src = QLineEdit(src_root or "")
        h.addWidget(self.ed_src, 1)
        b = QPushButton("选择...")
        b.clicked.connect(self.pick_src)
        h.addWidget(b)
        v.addLayout(h)

        h2 = QHBoxLayout()
        h2.addWidget(QLabel("目标:"))
        self.ed_dst = QLineEdit("G:\\MCServers")
        h2.addWidget(self.ed_dst, 1)
        b2 = QPushButton("选择...")
        b2.clicked.connect(self.pick_dst)
        h2.addWidget(b2)
        v.addLayout(h2)

        self.chk_copy = QCheckBox("复制而非移动（推荐，原文件保留）")
        self.chk_copy.setChecked(True)
        v.addWidget(self.chk_copy)

        v.addWidget(QLabel("预览:"))
        self.lst = QListWidget()
        v.addWidget(self.lst, 1)

        btns = QDialogButtonBox(QDialogButtonBox.Ok | QDialogButtonBox.Cancel)
        btns.button(QDialogButtonBox.Ok).setText("执行搬迁")
        btns.accepted.connect(self.accept)
        btns.rejected.connect(self.reject)
        v.addWidget(btns)
        self.preview()

    def pick_src(self):
        d = QFileDialog.getExistingDirectory(self, "源目录", self.ed_src.text())
        if d:
            self.ed_src.setText(d)
            self.preview()

    def pick_dst(self):
        d = QFileDialog.getExistingDirectory(self, "目标目录", self.ed_dst.text())
        if d:
            self.ed_dst.setText(d)
            self.preview()

    def preview(self):
        self.lst.clear()
        plans = migrate.plan(self.ed_src.text(), self.ed_dst.text())
        for p in plans:
            # ★ 如果目标已存在，标红显示，避免用户盲目点击执行
            it = QListWidgetItem("%s\n    -> %s%s"
                                 % (p["name"], p["dst"],
                                    "   [已存在,将跳过]" if p["exists"] else ""))
            if p["exists"]:
                it.setForeground(QColor("#c62828"))
            self.lst.addItem(it)
        if not plans:
            self.lst.addItem("(源目录下没有子文件夹)")

    def values(self):
        return {"src": self.ed_src.text(), "dst": self.ed_dst.text(),
                "copy": self.chk_copy.isChecked()}


# ★ 必须真的装上这个钩子。
#   以前只写了 _install_exit_hook() 却从来没调用过它 ——
#   于是"解释器退出前停掉线程"这道兜底根本不存在。
#   后果：程序从非正常路径退出时（异常、Ctrl-C、控制台被关、
#   或者最后一个窗口直接没了），后台线程还在跑，
#   Qt 抛 "QThread: Destroyed while thread is still running" 然后 abort，
#   表现就是"没点什么就闪退了"。
#   加上这行之后，退出前一定先停线程。
_install_exit_hook()
