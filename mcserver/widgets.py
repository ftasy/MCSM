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

from PySide6.QtCore import (Qt, QProcess, Signal, QEvent, QSize,
                            QEasingCurve, QVariantAnimation)
from PySide6.QtGui import (QFont, QColor, QTextCursor, QFontMetrics,
                           QPainter, QIcon)
from PySide6.QtWidgets import (
    QWidget, QDialog, QTextEdit, QLineEdit, QPushButton,
    QLabel, QSizePolicy,
    QVBoxLayout, QHBoxLayout)

from .core import theme
from .core.palette import PAL

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
            fg = t.get("fg") or PAL.TEXT
            bg = t.get("bg") or PAL.BG
            return fg, bg
        except Exception:
            return PAL.TEXT, PAL.BG

    def __init__(self, parent=None):
        super().__init__(parent)
        v = QVBoxLayout(self)
        v.setContentsMargins(0, 0, 0, 0)
        self.out = QTextEdit()
        self.out.setReadOnly(True)
        # QTextEdit 没有 setMaximumBlockCount，用文档上限代替
        # ★ v2.3.2：+1 是为了给"最后那个空块"留位 —— 重建 5000 行时
        #   会插入 5000 个块 + 1 个空块，上限正好 5000 会把第一行挤掉。
        self.out.document().setMaximumBlockCount(self.MAX_BUF + 1)
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
        # ★ 日志搜索 / 过滤条：默认隐藏，Ctrl+F 唤出
        self._search = self._build_search_bar()
        v.addWidget(self._search)
        v.addWidget(self.out, 1)

        h = QHBoxLayout()
        self.ed = QLineEdit()
        self.ed.setPlaceholderText("输入服务器指令后回车（如 op 玩家名 / spark tps）")
        self.ed.returnPressed.connect(self.send)
        h.addWidget(self.ed, 1)
        self.btn_send = QPushButton("发送")
        self.btn_send.clicked.connect(self.send)
        h.addWidget(self.btn_send)
        # ★ v2.3.2：日志搜索入口（Ctrl+F 的可见版，不然没人知道能搜）
        self.btn_find = QPushButton("查找")
        self.btn_find.setToolTip("搜索日志 / 只看错误（Ctrl+F）")
        self.btn_find.setAutoDefault(False)
        self.btn_find.clicked.connect(self.show_search)
        h.addWidget(self.btn_find)
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

        # ★ v2.3.2：原始行缓冲。
        #   过滤（只看警告/错误）要能"关掉过滤后把之前的行补回来"，
        #   只靠 QTextEdit 里已渲染的富文本做不到（级别信息已经丢了），
        #   所以额外留一份 (级别, 原文) 的滚动缓冲。
        self._buf = []
        self._filter_issue = False
        self._hits = 0

        # ★ 指令历史与补全
        #   （以前这段被误缩进进了 set_colors()，于是只有调过配色的
        #    Console 才有 history —— FloatConsole 造出来的控制台
        #    一按发送就 AttributeError: 'Console' object has no attribute
        #    'history'。现在放回 __init__，同时改配色也不会再清空历史。）
        self.history = []
        self.history_idx = -1

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
        self.ed.installEventFilter(self)

        # ★ Ctrl+F 搜索日志（仅在控制台及其子控件有焦点时生效，
        #   不影响主窗口里的 Ctrl+F —— 那是列表筛选）
        try:
            from PySide6.QtGui import QShortcut, QKeySequence
            self._sc_find = QShortcut(QKeySequence.Find, self)
            self._sc_find.setContext(Qt.WidgetWithChildrenShortcut)
            self._sc_find.activated.connect(self.show_search)
        except Exception:
            self._sc_find = None

    # ---------------- 搜索 / 过滤 ----------------
    MAX_BUF = 5000

    def _build_search_bar(self):
        """输出区上方的搜索条（默认隐藏）。"""
        w = QWidget()
        h = QHBoxLayout(w)
        h.setContentsMargins(0, 0, 0, 2)
        h.setSpacing(4)
        self.ed_find = QLineEdit()
        self.ed_find.setPlaceholderText(
            "搜索日志（回车下一个 / Shift+回车上一个 / Esc 关闭）")
        self.ed_find.setClearButtonEnabled(True)
        try:
            self.ed_find.setMaximumHeight(24)
        except Exception:
            pass
        self.ed_find.textChanged.connect(self._on_find_text)
        self.ed_find.returnPressed.connect(self.find_next)
        self.ed_find.installEventFilter(self)
        h.addWidget(self.ed_find, 1)

        self.lb_find = QLabel("")
        self.lb_find.setStyleSheet("color:%s; font-size:9pt;" % PAL.MUTED)
        self.lb_find.setMinimumWidth(52)
        h.addWidget(self.lb_find)

        def mk(text, tip, fn):
            b = QPushButton(text)
            b.setToolTip(tip)
            b.setMaximumHeight(24)
            b.setFixedWidth(28 if text in ("↑", "↓") else 54)
            b.setAutoDefault(False)   # 别抢回车的默认按钮
            b.clicked.connect(fn)
            h.addWidget(b)
            return b

        self.btn_prev = mk("↑", "上一个匹配（Shift+回车）", self.find_prev)
        self.btn_next = mk("↓", "下一个匹配（回车）", self.find_next)
        self.btn_issue = QPushButton("只看问题")
        self.btn_issue.setToolTip(
            "只显示 WARN / ERROR 级别的日志（含报错堆栈）\n"
            "排查崩溃时不用在几千行信息里翻")
        self.btn_issue.setCheckable(True)
        self.btn_issue.setMaximumHeight(24)
        self.btn_issue.setFixedWidth(74)
        self.btn_issue.setAutoDefault(False)
        self.btn_issue.toggled.connect(self.set_issue_only)
        h.addWidget(self.btn_issue)
        self.btn_close = mk("关闭", "关闭搜索并恢复全部日志（Esc）",
                            self.hide_search)
        w.setVisible(False)
        return w

    def show_search(self):
        """显示搜索条并聚焦输入框（Ctrl+F）。"""
        try:
            self._search.setVisible(True)
            self.ed_find.setFocus()
            self.ed_find.selectAll()
            q = self.ed_find.text().strip()
            if q:
                self._on_find_text(q)
        except Exception:
            pass

    def hide_search(self):
        """关闭搜索条：清高亮、关过滤、恢复全部日志。"""
        try:
            self._search.setVisible(False)
            if self.ed_find.text():
                self.ed_find.clear()
            self.out.setExtraSelections([])
            self.lb_find.setText("")
            self._hits = 0
            if self._filter_issue:
                self.btn_issue.blockSignals(True)
                self.btn_issue.setChecked(False)
                self.btn_issue.blockSignals(False)
                self.set_issue_only(False)
        except Exception:
            pass

    def set_issue_only(self, on):
        """只看 WARN / ERROR。"""
        on = bool(on)
        if on == self._filter_issue:
            return
        self._filter_issue = on
        self._rebuild_view()

    def _pass_filter(self, lv):
        return (not self._filter_issue) or lv in ("warn", "error")

    def _hl_color(self):
        """搜索命中高亮色：跟着控制台背景深浅走。"""
        try:
            from .core import theme as _th
            dark = _th.is_dark_bg(self._bg)
        except Exception:
            dark = True
        return PAL.CONSOLE_WARN_DARK if dark else PAL.CONSOLE_WARN_TINT

    def _highlight_all(self, q):
        """把所有命中处标底色，返回命中数量（最多 500 处）。"""
        from PySide6.QtGui import QTextCharFormat
        sels = []
        try:
            if q:
                doc = self.out.document()
                color = QColor(self._hl_color())
                cur = QTextCursor(doc)
                cur.movePosition(QTextCursor.Start)
                while len(sels) < 500:
                    cur = doc.find(q, cur)
                    if cur.isNull():
                        break
                    sel = QTextEdit.ExtraSelection()
                    fmt = QTextCharFormat()
                    fmt.setBackground(color)
                    sel.format = fmt
                    sel.cursor = cur
                    sels.append(sel)
        except Exception:
            pass
        try:
            self.out.setExtraSelections(sels)
        except Exception:
            pass
        return len(sels)

    def _on_find_text(self, text):
        q = (text or "").strip()
        if not q:
            self._hits = 0
            self.lb_find.setText("")
            try:
                self.out.setExtraSelections([])
            except Exception:
                pass
            return
        self._hits = self._highlight_all(q)
        self.lb_find.setText(("%d 处" % self._hits) if self._hits else "无匹配")

    def _find(self, backward=False):
        q = self.ed_find.text().strip()
        if not q:
            return
        from PySide6.QtGui import QTextDocument
        flags = QTextDocument.FindBackward if backward else \
            QTextDocument.FindFlags()
        if not self.out.find(q, flags):
            # 到头了：绕回另一端再找一次
            cur = self.out.textCursor()
            cur.movePosition(QTextCursor.End if backward
                             else QTextCursor.Start)
            self.out.setTextCursor(cur)
            if not self.out.find(q, flags):
                self.lb_find.setText("无匹配")
                return
        self.lb_find.setText(
            ("%d 处" % self._hits) if self._hits else "1 处")
        try:
            self.ed_find.setFocus()
        except Exception:
            pass

    def find_next(self):
        self._find(False)

    def find_prev(self):
        self._find(True)

    def _apply_console_bg(self):
        """按 self._bg 刷新输出区背景与默认文字色（viewport 锁）。

        QTextEdit 的可见区域是内部 viewport，主题样式表的输入类 QSS
        会作用到 viewport；必须对 viewport 直接 setStyleSheet + setPalette
        才锁得住背景。文字色与背景太相近时自动调整保证可读。
        """
        try:
            from PySide6.QtGui import QPalette as _QP
            from PySide6.QtGui import QColor as _QC
            txt = self._fit(self._colors.get("info") or PAL.CONSOLE_INFO)
            self.out.setStyleSheet(
                "QTextEdit#console_out{background:%s !important;"
                "background-image:none !important;"
                "color:%s !important;}" % (self._bg, txt))
            # ★ 滚动条亮色：直接设 palette（Button=轨道、Highlight=滑块）
            _pal = self.out.palette()
            _pal.setColor(_QP.Button, _QC(PAL.CONSOLE_BTN))
            _pal.setColor(_QP.Base, _QC(self._bg))
            _pal.setColor(_QP.Text, _QC(txt))
            _pal.setColor(_QP.Highlight, _QC(PAL.CONSOLE_HIGHLIGHT))
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
                th_bg = PAL.BG
            self._colors = dict(self.DEFAULT_COLORS)
            self._bg = th_bg
        self._fit_cache.clear()
        self._apply_console_bg()
        # 背景变了，搜索高亮的对比色也要跟着重算
        try:
            if self._search.isVisible() and self.ed_find.text().strip():
                self._on_find_text(self.ed_find.text())
        except Exception:
            pass

    def eventFilter(self, obj, event):
        """拦截输入框的上下键，实现历史记录。

        ★ V2.0.1.1：Tab 补全已按用户要求移除（会和 Tab 本身行为冲突）。
          补全仍由 QCompleter 自动弹出，上下键选择、回车确认。
        """
        if obj is getattr(self, "ed_find", None) and \
                event.type() == QEvent.KeyPress:
            # ★ 搜索框：Esc 关闭、回车下一个、Shift+回车上一个
            if event.key() == Qt.Key_Escape:
                self.hide_search()
                return True
            if event.key() in (Qt.Key_Return, Qt.Key_Enter):
                if event.modifiers() & Qt.ShiftModifier:
                    self.find_prev()
                else:
                    self.find_next()
                return True
        if obj is getattr(self, "ed", None) and event.type() == QEvent.KeyPress:
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
        "info":       PAL.CONSOLE_INFO,  # 普通信息（未自定义时=主题前景色）
        "warn":       PAL.WARN_LIGHT,  # 警告（黄）
        "error":      PAL.DANGER_CONSOLE,  # 普通错误（红）
        "error_bold": PAL.DANGER_BRIGHT,  # 报错堆栈：亮红 + 加粗
        "ok":         PAL.SUCCESS_CONSOLE,  # 成功 / 启动完成（亮绿）
        "chat":       PAL.CONSOLE_CHAT,  # 玩家聊天（青蓝）
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
            # ★ v2.3.2：优先返回原始缓冲 —— 开了"只看问题"时输出区
            #   只有警告/错误，诊断/分析需要的是完整现场，不能被过滤掉。
            if self._buf:
                return "\n".join(t for _, t in self._buf)
            return self.out.toPlainText()
        except Exception:
            return ""

    def clear(self):
        try:
            self.out.clear()
            self._buf = []
            if self._search.isVisible():
                self._on_find_text(self.ed_find.text())
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
        old = sb.value()
        cur = self.out.textCursor()
        cur.movePosition(QTextCursor.End)
        for line in text.split("\n"):
            if not line.strip():
                continue
            lv = self._level(line)
            self._buf.append((lv, line))
            if len(self._buf) > self.MAX_BUF:
                del self._buf[:len(self._buf) - self.MAX_BUF]
            # 只看问题时，非警告/错误只进缓冲、不上屏
            if not self._pass_filter(lv):
                continue
            self._insert_line(cur, lv, line)
        self.out.setTextCursor(cur)
        # ★ 以前这里只在"本来就在底部"时滚到底，其余情况会因为
        #   setTextCursor(末尾) 被 Qt 自动 ensureCursorVisible 拽到底，
        #   注释写的"不打断上翻"其实一直没生效。现在显式还原位置。
        sb.setValue(sb.maximum() if was_at_bottom else old)

    def _insert_line(self, cur, lv, line):
        """把一行按级别配色插进输出区（不负责滚动）。

        ★ v2.3.2：从 insertHtml 改成 insertText + QTextCharFormat。
          原因：`<br>` 在 Qt 里只是行分隔符（U+2028），不产生新块，
          于是 document().setMaximumBlockCount(5000) 从来没生效过 ——
          挂机一整天，控制台里几万行文本全在内存里。改成真正的
          insertBlock 之后上限才真的起作用；顺带也不用再做 HTML 转义，
          每行少一次字符串解析，日志刷屏更快。
        """
        cur.insertText(line, self._line_fmt(lv, line))
        cur.insertBlock()

    def _line_fmt(self, lv, line):
        """一行 -> 字符格式（颜色 + 报错堆栈加粗）。"""
        from PySide6.QtGui import QTextCharFormat
        color = self._colors.get(lv) or self._colors["info"]
        bold = False
        # ★ 报错堆栈整行亮红加粗
        if lv == "error" and self.ERR_STACK_RE.search(line):
            color = self._colors.get("error_bold") or self._colors["error"]
            bold = True
        # ★ V2.0.2：与背景太相近的颜色自动调整（保证可读）
        fmt = QTextCharFormat()
        fmt.setForeground(QColor(self._fit(color)))
        if bold:
            fmt.setFontWeight(QFont.Bold)
        return fmt

    def _rebuild_view(self):
        """按当前过滤条件重画整个输出区（数据来自 self._buf）。

        切换"只看问题"时调用：过滤关掉要能把之前跳过的行补回来，
        所以只能从原始缓冲重建。
        """
        try:
            sb = self.out.verticalScrollBar()
            was_at_bottom = (sb.value() >= sb.maximum() - 4)
            old = sb.value()
            self.out.setUpdatesEnabled(False)
            try:
                cur = QTextCursor(self.out.document())
                cur.select(QTextCursor.Document)
                cur.removeSelectedText()
                cur.movePosition(QTextCursor.End)
                for lv, line in self._buf:
                    if not self._pass_filter(lv):
                        continue
                    self._insert_line(cur, lv, line)
                self.out.setTextCursor(cur)
            finally:
                self.out.setUpdatesEnabled(True)
            sb.setValue(sb.maximum() if was_at_bottom else min(old, sb.maximum()))
            if self._search.isVisible() and self.ed_find.text().strip():
                self._on_find_text(self.ed_find.text())
        except Exception:
            pass

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
        tip.setStyleSheet("color:%s;" % PAL.GRAY)
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
        fg = QColor(cfg.get("fg") or PAL.TEXT)
        ac = QColor(cfg.get("accent") or PAL.ACCENT_DEFAULT)
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
    return (t.get("fg") if t else None) or PAL.WHITE


def _on_bg(c):
    t = _th()
    return t.on_bg(c) if t else c


def java_pid_alive(pid):
    """★ V2.4.1：判断 pid 是不是一个**还活着的 java 进程**。

    旧检查只看 OpenProcess 能不能拿到句柄 —— 但 Windows 对
    "已退出但句柄还被别人握着"的僵尸进程照样返回句柄；PID 被
    别的程序复用时更是直接误报"服务器在运行"（用户实测踩坑：
    退出保留后 java 已死，重启却显示运行中，还停止不掉）。
    这里三重确认：
      1. OpenProcess(PROCESS_QUERY_LIMITED_INFORMATION) 拿到句柄
      2. GetExitCodeProcess != STILL_ACTIVE(259)
      3. 进程可执行文件名含 "java"（PID 复用时绝不误报/误杀）
    """
    try:
        import ctypes
        from ctypes import wintypes
        k32 = ctypes.windll.kernel32
        h = k32.OpenProcess(0x1000, False, int(pid))
        if not h:
            return False
        try:
            code = wintypes.DWORD(0)
            if not k32.GetExitCodeProcess(h, ctypes.byref(code)):
                return False
            if code.value != 259:      # 259 = STILL_ACTIVE
                return False
            # 活着 → 再确认是 java（防 PID 复用）
            buf = ctypes.create_unicode_buffer(1024)
            size = wintypes.DWORD(1024)
            if k32.QueryFullProcessImageNameW(h, 0, buf,
                                              ctypes.byref(size)):
                name = os.path.basename(buf.value).lower()
                return "java" in name
            return True
        finally:
            k32.CloseHandle(h)
    except Exception:
        return False


class ReattachedProcess:
    """V2.1.0: Wrapper for a server process from a previous manager instance."""
    def __init__(self, pid, server_dir, name=""):
        self._pid = pid
        self._server_dir = server_dir
        self._name = name
        self._alive = self._check_alive()

    def _check_alive(self):
        # ★ V2.4.1：改用 java_pid_alive（退出码 + 映像名双重校验）
        return java_pid_alive(self._pid)
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


