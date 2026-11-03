# -*- coding: utf-8 -*-
"""从 app.py 提取的可复用 UI 组件。

包含：
  - Console / FloatConsole：服务器控制台
  - NavButton：带动画的导航按钮
  - ReattachedProcess：重新挂接已运行的服务器进程
  - app_icon()：程序图标加载
  - _th() / _fg() / _on_bg()：主题色助手
"""

import os
import re
import sys

from PySide6.QtCore import (Qt, QProcess, Signal, QEvent,
                            QEasingCurve, QVariantAnimation)
from PySide6.QtGui import (QFont, QColor, QTextCursor,
                           QPainter, QIcon)
from PySide6.QtWidgets import (
    QWidget, QDialog, QTextEdit, QLineEdit, QPushButton,
    QVBoxLayout, QHBoxLayout)

from .core import theme

# 控制台配色默认跟随主题（和信息面板一致）。
# 旧默认/历史测试值视为"未自定义"，自动迁移到当前主题色。
_CONSOLE_STALE_BG = {"#e2e4e6", "#f0f0f0", "#101010", "#f4f6f8"}
_CONSOLE_STALE_INFO = {"#e6e6e6", "#cfcfcf"}


class Console(QWidget):
    """单个服务器的控制台。"""

    @staticmethod
    def _theme_colors():
        """当前主题 (fg, bg)——控制台默认配色跟随它，和信息面板一致。"""
        try:
            t = theme.get()
            fg = t.get("fg") or "#1a1a1a"
            bg = t.get("bg") or "#f4f6f8"
            return fg, bg
        except Exception:
            return "#1a1a1a", "#f4f6f8"

    def __init__(self, parent=None):
        super().__init__(parent)
        v = QVBoxLayout(self)
        v.setContentsMargins(0, 0, 0, 0)
        self.out = QTextEdit()
        self.out.setReadOnly(True)
        # QTextEdit 没有 setMaximumBlockCount，用文档上限代替
        self.out.document().setMaximumBlockCount(5000)
        f = QFont("Consolas", 10)
        self.out.setFont(f)
        # ★ V2.0.1.1：用 objectName 精确选择器 —— 主窗口主题样式表
        #   的 QTextEdit{} 规则无法覆盖 QTextEdit#console_out，
        #   保存显示设置后输出区不再被主题刷成白底。
        #   background-image:none 必需：主题 centralwidget 若挂了背景图，
        #   会级联到输入类控件，浅色图会盖在深色上。
        #   ★ 注意：滚动条样式不要写进同一份 QSS —— 完整 QSS 在主题
        #   级联下会整个失效（背景被主题输入类规则盖掉）；滚动条改由
        #   palette 控制（见下方 setPalette）。
        self.out.setObjectName("console_out")
        # ★ V2.0.2：背景/文字色由 _apply_console_bg() 按 self._bg 统一设置
        #   （不再硬编码 #101010；颜色相近时文字自动调整）
        self.out.setLineWrapMode(QTextEdit.NoWrap)
        v.addWidget(self.out, 1)

        h = QHBoxLayout()
        self.ed = QLineEdit()
        self.ed.setPlaceholderText("输入服务器指令后回车（如 op 玩家名 / spark tps）")
        self.ed.returnPressed.connect(self.send)
        h.addWidget(self.ed, 1)
        self.btn_send = QPushButton("发送")
        self.btn_send.clicked.connect(self.send)
        h.addWidget(self.btn_send)
        v.addLayout(h)

        self.proc = None
        self.proc_name = ""
        self.proc_key = None

        # ★ V2.0.3：控制台背景/文字默认跟随主题（和信息面板一致）
        th_fg, th_bg = Console._theme_colors()
        self._bg = th_bg
        self._fit_cache = {}
        # ★ V1.1.0：控制台配色（可被「显示设置 → 控制台配色」覆盖）
        self._colors = dict(self.DEFAULT_COLORS)
        self._colors["info"] = th_fg
        self._apply_console_bg()

    def _apply_console_bg(self):
        """按 self._bg 刷新输出区背景与默认文字色（viewport 锁）。

        QTextEdit 的可见区域是内部 viewport，主题样式表的输入类 QSS
        会作用到 viewport；必须对 viewport 直接 setStyleSheet + setPalette
        才锁得住背景。文字色与背景太相近时自动调整保证可读。
        """
        try:
            from PySide6.QtGui import QPalette as _QP
            from PySide6.QtGui import QColor as _QC
            txt = self._fit(self._colors.get("info") or "#cfcfcf")
            self.out.setStyleSheet(
                "QTextEdit#console_out{background:%s !important;"
                "background-image:none !important;"
                "color:%s !important;}" % (self._bg, txt))
            # ★ 滚动条亮色：直接设 palette（Button=轨道、Highlight=滑块）
            _pal = self.out.palette()
            _pal.setColor(_QP.Button, _QC("#1e1e1e"))
            _pal.setColor(_QP.Base, _QC(self._bg))
            _pal.setColor(_QP.Text, _QC(txt))
            _pal.setColor(_QP.Highlight, _QC("#4a4a4a"))
            self.out.setPalette(_pal)
        except Exception:
            pass

    def _fit(self, color):
        """文字色与背景太相近时自动调整（复用 theme.ensure_contrast）。

        背景亮就把字往黑推、背景暗就往白推，保留原有色相感；
        结果按 (背景,文字) 缓存，日志刷屏时不重复计算。
        """
        try:
            key = (self._bg, color)
            v = self._fit_cache.get(key)
            if v is None:
                from .core import theme as _th
                v, _ = _th.ensure_contrast(color, self._bg, 4.5)
                self._fit_cache[key] = v
            return v
        except Exception:
            return color

    def set_colors(self, colors):
        """应用自定义控制台配色（None / 空 dict = 恢复默认）。

        ★ V2.0.3：未自定义（或仍是旧默认值）的 bg/info 跟随主题，
          与控制台信息面板配色一致；用户真正挑过的颜色保留。
        """
        try:
            th_fg, th_bg = Console._theme_colors()
            if colors:
                for k in self.DEFAULT_COLORS:
                    v = colors.get(k)
                    if v:
                        # ★ V2.0.3：旧默认普通信息色视为"未自定义"→ 主题前景
                        if k == "info" and v.lower() in _CONSOLE_STALE_INFO:
                            v = th_fg
                        self._colors[k] = v
                bg = colors.get("bg")
                if bg and bg.lower() not in _CONSOLE_STALE_BG:
                    self._bg = bg
                else:
                    self._bg = th_bg
            else:
                self._colors = dict(self.DEFAULT_COLORS)
                self._colors["info"] = th_fg
                self._bg = th_bg
        except Exception:
            try:
                _, th_bg = Console._theme_colors()
            except Exception:
                th_bg = "#f4f6f8"
            self._colors = dict(self.DEFAULT_COLORS)
            self._bg = th_bg
        self._fit_cache.clear()
        self._apply_console_bg()
        
        # ★ 指令历史与补全
        self.history = []
        self.history_idx = -1
        
        # 给输入框加补全（大幅扩充常用词库，贴合实际开服习惯）
        from PySide6.QtWidgets import QCompleter
        common_cmds = [
            "stop", "save-all", "reload", "restart",
            "op", "deop", "whitelist", "ban", "ban-ip", "pardon", "kick", "list",
            "spark tps", "spark profiler", "spark health",
            "time set day", "time set night", "weather clear", "weather rain",
            "gamemode survival", "gamemode creative", "gamemode spectator",
            "difficulty peaceful", "difficulty hard",
            "tp", "give", "clear", "effect", "enchant",
            "gamerule keepInventory true", "gamerule mobGriefing false",
            "difficulty", "seed", "forge tps", "fabric tps"
        ]
        self.completer = QCompleter(common_cmds, self)
        self.completer.setCaseSensitivity(Qt.CaseInsensitive)
        self.completer.setFilterMode(Qt.MatchContains)
        self.ed.setCompleter(self.completer)
        
        # 绑定键盘事件
        self.ed.installEventFilter(self)

    def eventFilter(self, obj, event):
        """拦截输入框的上下键，实现历史记录。

        ★ V2.0.1.1：Tab 补全已按用户要求移除（会和 Tab 本身行为冲突）。
          补全仍由 QCompleter 自动弹出，上下键选择、回车确认。
        """
        if obj is self.ed and event.type() == QEvent.KeyPress:
            # ★ 如果补全弹窗正在显示，把上下键交给 QCompleter 处理
            if self.completer and self.completer.popup() and self.completer.popup().isVisible():
                return super().eventFilter(obj, event)

            if event.key() == Qt.Key_Up:
                if self.history:
                    self.history_idx = max(0, self.history_idx - 1)
                    self.ed.setText(self.history[self.history_idx])
                return True
            elif event.key() == Qt.Key_Down:
                if self.history:
                    self.history_idx = min(len(self.history), self.history_idx + 1)
                    if self.history_idx == len(self.history):
                        self.ed.clear()
                    else:
                        self.ed.setText(self.history[self.history_idx])
                return True
        return super().eventFilter(obj, event)

    # 日志级别 -> 颜色（★ V1.1.0：可用「显示设置 → 控制台配色」覆盖）
    DEFAULT_COLORS = {
        # ★ V2.0.3：info/bg 默认跟随主题（和信息面板一致）——
        #   这里 info 仅是兜底；实际由 _theme_colors() 决定。
        "info":       "#cfcfcf",  # 普通信息（未自定义时=主题前景色）
        "warn":       "#ffd166",  # 警告（黄）
        "error":      "#ff6b6b",  # 普通错误（红）
        "error_bold": "#ff5252",  # 报错堆栈：亮红 + 加粗
        "ok":         "#4caf50",  # 成功 / 启动完成（亮绿）
        "chat":       "#4fc3f7",  # 玩家聊天（青蓝）
    }

    # 报错堆栈特征：整行亮红加粗
    ERR_STACK_RE = re.compile(
        r"(exception|caused by|\bat net\.minecraft\b|fatal|severe)",
        re.IGNORECASE)
    # 玩家聊天：<玩家名> 内容
    # ★ V2.0.2.1：原只匹配 [A-Za-z0-9_]，中文/符号玩家名
    #   （如 <[记者] CYT233>）不命中 → 聊天被当成普通信息灰字。
    #   放宽为尖括号内任意字符；Java 堆栈的 <init>/<clinit>
    #   由 _level() 里的 "at "/"caused by" 前缀排除。
    CHAT_RE = re.compile(r"<[^<>]{1,40}>")
    # 启动完成
    DONE_RE = re.compile(r"\bDone\b|启动完成|server started", re.IGNORECASE)
    # ★ 性能优化：预编译 _level() 内联正则（每行日志都会调用）
    _LEVEL_PREFIX_RE = re.compile(r"\[\d{2}:\d{2}:\d{2}\s+([A-Z]+)/?\w*\]")
    _ERR_KW_RE = re.compile(r"\b(error|failed|unable)\b")
    _WARN_KW_RE = re.compile(r"\b(warn|warning|deprecated)\b")

    def append(self, text):
        self._append_colored(text.rstrip("\n"))

    def toPlainText(self):
        """★ 以前没有这个方法。

        主界面 do_diag / do_analyze 调 self.console.toPlainText() 取控制台文本，
        但 Console 内部是 self.out，自己没转发 —— AttributeError。
        更要命的是调用处用 try/except: pass 包着，于是不崩，
        只是 txt 永远是空字符串：
          诊断时少了一份最重要的现场数据（刚发生的报错），
          而界面完全没有任何提示。
        """
        try:
            return self.out.toPlainText()
        except Exception:
            return ""

    def clear(self):
        try:
            self.out.clear()
        except Exception:
            pass

    def _level(self, line):
        """判断一行日志的级别。"""
        low = line.lower()
        # 带 [HH:MM:SS LEVEL] 前缀的
        m = self._LEVEL_PREFIX_RE.search(line)
        if m:
            lv = m.group(1).upper()
            if lv in ("ERROR", "FATAL", "SEVERE"):
                return "error"
            if lv in ("WARN", "WARNING"):
                return "warn"
            if lv == "INFO":
                # 玩家聊天优先（服务端聊天是 INFO 级别）
                if (self.CHAT_RE.search(line)
                        and not low.startswith(("at ", "caused by"))):
                    return "chat"
                if any(k in low for k in ("error", "exception", "failed",
                                          "failed to", "unable")):
                    return "error"
                if any(k in low for k in ("done (", "started",
                                          "listening", "启动完成",
                                          "server started")):
                    return "ok"
                return "info"
        # 无前缀的兜底（V1.0.0：聊天优先于错误关键词，
        # 否则玩家聊天里带个 "error" 会被误标成红色）
        if (self.CHAT_RE.search(line)
                and not low.startswith(("at ", "caused by"))):
            return "chat"
        if self.ERR_STACK_RE.search(low):
            return "error"
        if self._ERR_KW_RE.search(low):
            return "error"
        if self._WARN_KW_RE.search(low):
            return "warn"
        if ("done (" in low or "started" in low
                or "listening" in low or "就绪" in low):
            return "ok"
        return "info"

    def _append_colored(self, text):
        # ★ 先判断用户是否在底部，再决定要不要自动滚到底 ——
        #   正在往上翻崩溃日志时，新日志不该把他拽走。
        sb = self.out.verticalScrollBar()
        was_at_bottom = (sb.value() >= sb.maximum() - 4)
        cur = self.out.textCursor()
        cur.movePosition(QTextCursor.End)
        for line in text.split("\n"):
            if not line.strip():
                continue
            lv = self._level(line)
            color = self._colors.get(lv) or self._colors["info"]
            bold = False
            # ★ 报错堆栈整行亮红加粗
            if lv == "error" and self.ERR_STACK_RE.search(line):
                color = self._colors.get("error_bold") or self._colors["error"]
                bold = True
            # ★ V2.0.2：与背景太相近的颜色自动调整（保证可读）
            color = self._fit(color)
            safe = (line.replace("&", "&amp;")
                        .replace("<", "&lt;").replace(">", "&gt;"))
            if bold:
                cur.insertHtml(
                    '<span style="color:%s;font-weight:bold;">'
                    '%s</span><br>' % (color, safe))
            else:
                cur.insertHtml(
                    '<span style="color:%s;">%s</span><br>'
                    % (color, safe))
        self.out.setTextCursor(cur)
        if was_at_bottom:
            sb.setValue(sb.maximum())

    def set_running(self, running):
        self.ed.setEnabled(running)
        self.btn_send.setEnabled(running)

    def send(self):
        cmd = self.ed.text().strip()
        if not cmd or not self.proc:
            return
        self.ed.clear()
        
        # ★ 记录历史
        if cmd not in self.history:
            self.history.append(cmd)
        self.history_idx = len(self.history)
        
        try:
            self.proc.write(("%s\n" % cmd).encode("utf-8"))
        except Exception:
            pass


class FloatConsole(QDialog):
    """★ V2.0.13：独立调出的服务器控制台窗口。

    从主窗口『调出控制台』打开：只读输出 + 指令输入（复用 Console），
    关闭该窗口只销毁视图，不向服务器发任何指令、不停止服务器；
    主窗口关闭后服务器照常后台运行（进程解耦，见 closeEvent）。
    """
    closed = Signal(str)  # proc_key

    def __init__(self, parent, proc, proc_key, name):
        super().__init__(parent)
        self.proc_key = proc_key
        self.setWindowTitle(
            "%s —— 服务器控制台（关闭窗口不会停止服务器）" % name)
        self.resize(900, 560)
        v = QVBoxLayout(self)
        v.setContentsMargins(8, 8, 8, 8)
        self.console = Console()
        self.console.proc = proc
        self.console.proc_name = name
        self.console.proc_key = proc_key
        self.console.set_running(True)
        v.addWidget(self.console, 1)
        tip = QLabel(
            "提示：关闭此窗口仅关闭视图，服务器继续在后台运行；"
            "下方输入指令直接发给服务器。")
        tip.setStyleSheet("color:#888888;")
        v.addWidget(tip)

    def append(self, text):
        try:
            self.console.append(text)
        except Exception:
            pass

    def closeEvent(self, ev):
        # 只关视图，不碰服务器进程
        ev.accept()
        try:
            self.closed.emit(self.proc_key)
        except Exception:
            pass


class NavButton(QWidget):
    """顶栏文字按钮：悬停淡入 + 按下回弹。

    ★ 为什么不用 QToolButton + 样式表：
      Qt 样式表没有 transition/animation，点了只有瞬间变色、没有过渡。
      这里用两个 QVariantAnimation 分别推进 hover / press 的 0~1 进度，
      paintEvent 里按进度画背景，才有"淡入 + 压感 + 回弹"。
      文字直接用 self.font() 画，所以全局粗体 / 字号对它同样生效。
    """
    clicked = Signal()

    def __init__(self, text, parent=None):
        super().__init__(parent)
        self._text = text
        self._hover = 0.0
        self._press = 0.0
        self._hover_anim = QVariantAnimation(self)
        self._hover_anim.valueChanged.connect(self._set_hover)
        self._press_anim = QVariantAnimation(self)
        self._press_anim.valueChanged.connect(self._set_press)
        self.setCursor(Qt.PointingHandCursor)
        self.setFocusPolicy(Qt.NoFocus)
        self.setSizePolicy(QSizePolicy.Fixed, QSizePolicy.Fixed)
        self.setAttribute(Qt.WA_Hover, True)

    # ---------- 动画 ----------
    def _animate(self, anim, start, end, ms, curve):
        anim.stop()
        anim.setStartValue(float(start))
        anim.setEndValue(float(end))
        anim.setDuration(int(ms))
        anim.setEasingCurve(curve)
        anim.start()

    def _set_hover(self, v):
        self._hover = float(v)
        self.update()

    def _set_press(self, v):
        self._press = float(v)
        self.update()

    def _animate_hover(self, target):
        self._animate(self._hover_anim, self._hover, target, 140,
                      QEasingCurve.OutCubic)

    def _animate_press(self, target, bounce=False):
        self._animate(self._press_anim, self._press, target,
                      170 if bounce else 70,
                      QEasingCurve.OutBack if bounce
                      else QEasingCurve.OutCubic)

    # ---------- 事件 ----------
    def enterEvent(self, ev):
        if self.isEnabled():
            self._animate_hover(1.0)
        super().enterEvent(ev)

    def leaveEvent(self, ev):
        self._animate_hover(0.0)
        if self._press > 0:
            self._animate_press(0.0)
        super().leaveEvent(ev)

    def mousePressEvent(self, ev):
        if ev.button() == Qt.LeftButton and self.isEnabled():
            self._animate_press(1.0)
            ev.accept()
        else:
            ev.ignore()

    def mouseReleaseEvent(self, ev):
        if ev.button() != Qt.LeftButton:
            return
        inside = self.rect().contains(ev.position().toPoint())
        self._animate_press(0.0, bounce=True)
        if inside and self.isEnabled():
            self.clicked.emit()
        ev.accept()

    def changeEvent(self, ev):
        if ev.type() in (QEvent.EnabledChange, QEvent.FontChange):
            if not self.isEnabled():
                self._hover_anim.stop()
                self._press_anim.stop()
                self._hover = 0.0
                self._press = 0.0
            self.setCursor(Qt.PointingHandCursor if self.isEnabled()
                           else Qt.ArrowCursor)
            self.updateGeometry()
            self.update()
        super().changeEvent(ev)

    # ---------- 尺寸 / 绘制 ----------
    def sizeHint(self):
        fm = QFontMetrics(self.font())
        return QSize(fm.horizontalAdvance(self._text) + 20,
                     fm.height() + 10)

    def minimumSizeHint(self):
        return self.sizeHint()

    def paintEvent(self, ev):
        p = QPainter(self)
        p.setRenderHint(QPainter.Antialiasing, True)
        cfg = theme.get()
        fg = QColor(cfg.get("fg") or "#1a1a1a")
        ac = QColor(cfg.get("accent") or "#0b62d0")
        if not self.isEnabled():
            fg = QColor(theme.muted_color())
        hover = max(0.0, min(1.0, self._hover))
        press = max(0.0, min(1.0, self._press))
        # 按下时整体轻微下沉，松开时 OutBack 回弹
        dy = int(round(press))
        r = self.rect().adjusted(1, 1 + dy, -1, -1 + dy)
        # ★ V1.7.0-5：悬停/按下高亮加强（原来 alpha 26/42 太淡，
        #   深色主题下几乎看不出反馈），hover 60 + press 110
        alpha = int(hover * 60 + press * 110)
        if alpha > 0:
            c = QColor(ac)
            c.setAlpha(min(255, alpha))
            p.setPen(Qt.NoPen)
            p.setBrush(c)
            p.drawRoundedRect(r, 4, 4)
            # 悬停时文字也稍微提亮（用 accent 色，跟随 hover 强度）
            if hover > 0.3:
                txt = QColor(fg).lighter(112)
                p.setPen(txt)
                p.setFont(self.font())
                p.drawText(r, Qt.AlignCenter, self._text)
                return
        p.setPen(fg)
        p.setFont(self.font())
        p.drawText(r, Qt.AlignCenter, self._text)


def app_icon():
    """程序图标（草方块 + 扳手）。

    多路径查找：包内 mcserver/、包根、再上一级
    —— 不管用户把压缩包解到哪一层都能找到。
    ★ V1.6.5：PyInstaller 打包后 __file__ 指向 _internal/mcserver 里，
      icon.ico 由 --add-data 放进 _internal 根（sys._MEIPASS）。
      不加这个路径，打包版窗口/任务栏图标会退回默认图标。
    """
    from PySide6.QtGui import QIcon
    here = os.path.dirname(os.path.abspath(__file__))
    cands = [
        os.path.join(here, "icon.ico"),            # mcserver/icon.ico
        os.path.join(here, "icon.png"),
        os.path.join(os.path.dirname(here), "icon.ico"),   # 包根
        os.path.join(os.path.dirname(here), "icon.png"),
        os.path.join(os.path.dirname(os.path.dirname(here)), "icon.ico"),
    ]
    if getattr(sys, "frozen", False):
        try:
            meipass = getattr(sys, "_MEIPASS", "") or ""
            if meipass:
                cands.insert(0, os.path.join(meipass, "icon.ico"))
                cands.insert(1, os.path.join(meipass, "icon.png"))
        except Exception:
            pass
    for f in cands:
        try:
            if os.path.isfile(f):
                ic = QIcon(f)
                if not ic.isNull():
                    return ic
        except Exception:
            pass
    return QIcon()


def _th():
    try:
        from .core import theme as _t
        _t.load()
        return _t
    except Exception:
        return None


def _fg():
    t = _th()
    return (t.get("fg") if t else None) or "#ffffff"


def _on_bg(c):
    t = _th()
    return t.on_bg(c) if t else c


class ReattachedProcess:
    """V2.1.0: Wrapper for a server process from a previous manager instance."""
    def __init__(self, pid, server_dir, name=""):
        self._pid = pid
        self._server_dir = server_dir
        self._name = name
        self._alive = self._check_alive()

    def _check_alive(self):
        try:
            import ctypes
            h = ctypes.windll.kernel32.OpenProcess(0x1000, False, self._pid)
            if h:
                ctypes.windll.kernel32.CloseHandle(h)
                return True
            return False
        except Exception:
            return False
    def write(self, data):
        cmd = data.decode("utf-8", "replace").strip().lower()
        if cmd == "stop":
            try:
                import ctypes
                h = ctypes.windll.kernel32.OpenProcess(0x0001, False, self._pid)
                if h:
                    ctypes.windll.kernel32.TerminateProcess(h, 0)
                    ctypes.windll.kernel32.CloseHandle(h)
            except Exception:
                pass

    def state(self):
        if self._alive:
            self._alive = self._check_alive()
        return QProcess.Running if self._alive else QProcess.NotRunning

    def processId(self):
        return self._pid

    def kill(self):
        try:
            import ctypes
            h = ctypes.windll.kernel32.OpenProcess(0x0001, False, self._pid)
            if h:
                ctypes.windll.kernel32.TerminateProcess(h, 1)
                ctypes.windll.kernel32.CloseHandle(h)
        except Exception:
            pass
        self._alive = False


