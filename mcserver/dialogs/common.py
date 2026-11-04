# -*- coding: utf-8 -*-
"""通用助手：LogView 日志控件、主题配色助手、YAML / 内存单位读写。

从 dialogs.py 拆分而来，只保留本模块用到的导入。
"""

import os
import re
import sys

from PySide6.QtGui import QTextCursor
from PySide6.QtCore import Qt, QThread
from PySide6.QtWidgets import (QMessageBox, QPlainTextEdit, QTextBrowser,
                               QDialog)
from ..core import modman, net, fsutil
from ..dialog_workers import (
    _LIVE_THREADS, _StopMixin, Worker, IconWorker, LoaderVerWorker, _track,
    _stop_threads, _stop_all_threads, _install_exit_hook, _fetch_icon_img,
    _make_letter_pixmap, _make_placeholder_pixmap, _global_show_icons)
from ..core.palette import PAL


# ★ 性能优化：预编译字面量正则
_RE_YAML_KEY = re.compile(r"^(\s*)([^#\s][^:]*):")
_RE_YAML_KV = re.compile(r"^(\s*[^#][^:]*:)\s*(.*)$")
_RE_YAML_COMMENT = re.compile(r"(#.*)$")
_RE_BRACKET_TAG = re.compile(r"\[([^\[\]]+)\]")
_RE_BRACKET_STRIP = re.compile(r"\[[^\[\]]*\]")
_RE_URL = re.compile(r"https?://[^\s]+")


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
# ★ 重构：后台线程与共享工具已提取到 dialog_workers.py
from ..dialog_workers import (
    _LIVE_THREADS, _StopMixin, Worker, IconWorker, LoaderVerWorker,
    _track, _stop_threads, _stop_all_threads, _install_exit_hook,
    _fetch_icon_img, _make_letter_pixmap, _make_placeholder_pixmap,
    _global_show_icons)



def _th():
    try:
        from ..core import theme as _t
        _t.load()
        return _t
    except Exception:
        return None


def _fg():
    """当前主题字体色（取不到就用白）。"""
    t = _th()
    return (t.get("fg") if t else None) or PAL.WHITE


def _on_bg(c):
    """把写死的语义色调到在当前底色上读得清。"""
    t = _th()
    return t.on_bg(c) if t else c


def _sec_bg():
    """分组标题行底色。"""
    t = _th()
    return t.section_bg() if t else PAL.SECTION_BG


def _preset_matches(p, rd, key):
    """判断读回的配置 rd 是否就是预设 p 这一档（按该模组的关键数值）。

    ★ V2.1.0：ServerCore 用 target_mspt（保守50/均衡40/激进35 唯一区分），
      Simple Voice Chat 用 voice_distance+broadcast_range 组合。
    返回 True 时下拉应自动选中这一档，避免"下拉显示均衡、数值却是旧档"。
    """
    try:
        if key == "servercore":
            return p.get("target_mspt") == rd.get("target_mspt")
        if key == "simple-voice-chat":
            return (p.get("voice_distance") == rd.get("voice_distance")
                    and p.get("broadcast_range") == rd.get("broadcast_range"))
    except Exception:
        pass
    return False


def _muted():
    """次要文字（提示/说明）的 CSS。

    ★ 以前直接写死 color:#666 —— 那串灰色在深色底上对比度只有
      2.1~2.7，低于 4.5 可读线，表现就是"提示文字像没显示出来"。
      这里按当前主题现算，保证看得清。
    """
    try:
        from ..core import theme as _th
        return _th.muted_css()
    except Exception:
        return "color:%s;" % PAL.MUTED


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
    with open(path, "r", encoding="utf-8") as f:
        lines = f.readlines()

    stack = []            # [(indent, key), ...]
    idx = {}              # (key1, key2, ...) -> 行号
    for i, ln in enumerate(lines):
        m = _RE_YAML_KEY.match(ln)
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
        m2 = _RE_YAML_KV.match(line)
        if not m2:
            continue
        tail = m2.group(2)
        cm = _RE_YAML_COMMENT.search(tail)
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
        fsutil.atomic_write_text(path, "".join(lines), encoding="utf-8",
                                 newline="", keep_backup=True)


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
        from ..core import theme as _th
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


def _fetch_icon_bytes(url, timeout=15):
    """下载一张模组图标（后台线程调用，返回原始字节）。"""
    if not url:
        return None
    try:
        return net.request(url, timeout=timeout)
    except Exception:
        return None


# ★ V2.3.4：『下载源设置』单例
#   以前主界面、模组浏览、高级搜索三处各自 new 一个 SourceDialog，
#   连着点几下就叠出好几个"下载源设置"窗口 —— 看着就像
#   "每次操作都弹一次镜像源选择"。这里保证全局同时只有一个：
#   重复调用只把已有窗口顶到前面，不新建。
_SOURCE_DLG = {"dlg": None}


def show_source_dialog(parent=None):
    """打开『下载源设置』（单例）。返回 (dialog, values)。

    values = (mirror, cf_key)；用户取消时为 None。
    ★ 实例常驻：整个进程只创建过一个 SourceDialog，重复点按钮
      只把它顶到前面；万一它被销毁了（Qt 回收），下次自动重建。
    ★ 延迟导入 SourceDialog，避免 common ↔ mod_source 循环依赖。
    """
    from .mod_source import SourceDialog
    dlg = _SOURCE_DLG.get("dlg")
    if dlg is not None:
        try:
            if dlg.isVisible():
                dlg.raise_()
                dlg.activateWindow()
                return dlg, dlg.values()
        except Exception:
            dlg = None
    if dlg is None:
        dlg = _themed(SourceDialog(parent))
        _SOURCE_DLG["dlg"] = dlg
    if dlg.exec() != QDialog.Accepted:
        return dlg, None
    return dlg, dlg.values()


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
