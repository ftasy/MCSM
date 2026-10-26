# -*- coding: utf-8 -*-
"""
=============================================================
  MC 服务器管理器
=============================================================
  一键扫描 / 搬迁 / 初始化服务端 / 启动并查看控制台
=============================================================
"""

import os
import sys
import json
import re
import shutil
import traceback
import urllib.request
import logging

from PySide6.QtCore import (Qt, QProcess, QTimer, Signal, QSize, QPoint,
                            QEvent, QEasingCurve, QVariantAnimation)
from PySide6.QtGui import (QFont, QColor, QTextCursor,
                           QPixmap, QPainter, QFontMetrics)
from PySide6.QtWidgets import (
    QAbstractItemView,
    QApplication,
    QCheckBox,
    QColorDialog,
    QComboBox,
    QDialog,
    QDialogButtonBox,
    QFileDialog,
    QFontComboBox,
    QFormLayout,
    QFrame,
    QGroupBox,
    QHBoxLayout,
    QHeaderView,
    QLabel,
    QLineEdit,
    QListWidget,
    QListWidgetItem,
    QMainWindow,
    QMessageBox,
    QPushButton,
    QSizePolicy,
    QSlider,
    QSpinBox,
    QSplitter,
    QStatusBar,
    QTabBar,
    QTabWidget,
    QTableWidget,
    QTableWidgetItem,
    QTextEdit,
    QVBoxLayout,
    QWidget)

from .core import (scanner, versions, java as javacore, builder,
                   migrate, props, backup, install, theme, monitor, modman)
from .dialogs import (CreateDialog, MigrateDialog, ServerConfigDialog,
                      BackupDialog, ModDialog, DeleteServerDialog,
                      DiagDialog, AnalyzeDialog, Worker, _track)
from . import updater   # ★ V1.6.0：软件更新检测

DATA_FILE = "servers.json"


class Console(QWidget):
    """单个服务器的控制台。"""

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
        self.out.setStyleSheet("background:#101010; color:#d0d0d0;")
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

        # ★ V1.1.0：控制台配色（可被「显示设置 → 控制台配色」覆盖）
        self._colors = dict(self.DEFAULT_COLORS)

    def set_colors(self, colors):
        """应用自定义控制台配色（None / 空 dict = 恢复默认）。"""
        try:
            if colors:
                for k in self.DEFAULT_COLORS:
                    v = colors.get(k)
                    if v:
                        self._colors[k] = v
            else:
                self._colors = dict(self.DEFAULT_COLORS)
        except Exception:
            self._colors = dict(self.DEFAULT_COLORS)
        
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
        """拦截输入框的上下键，实现历史记录"""
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
        "info":       "#d0d0d0",  # 普通信息（灰）
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
    CHAT_RE = re.compile(r"<[A-Za-z0-9_]{1,16}>")
    # 启动完成
    DONE_RE = re.compile(r"\bDone\b|启动完成|server started", re.IGNORECASE)

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
        m = re.search(r"\[\d{2}:\d{2}:\d{2}\s+([A-Z]+)/?\w*\]", line)
        if m:
            lv = m.group(1).upper()
            if lv in ("ERROR", "FATAL", "SEVERE"):
                return "error"
            if lv in ("WARN", "WARNING"):
                return "warn"
            if lv == "INFO":
                # 玩家聊天优先（服务端聊天是 INFO 级别）
                if self.CHAT_RE.search(line):
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
        if self.CHAT_RE.search(line):
            return "chat"
        if self.ERR_STACK_RE.search(low):
            return "error"
        if re.search(r"\b(error|failed|unable)\b", low):
            return "error"
        if re.search(r"\b(warn|warning|deprecated)\b", low):
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


class MainWindow(QMainWindow):
    def __init__(self):
        super().__init__()
        from . import __version__
        self.setWindowTitle("MC 服务器管理器 v%s" % __version__)
        self.resize(1180, 760)
        self.root = ""
        self.entries = []
        self.procs = {}
        self.data = {"root": "", "servers": {}, "display": {}}
        self.disp = {"font": 13, "row": 30}
        
        # ★ 第一处修改：必须是空字典，不能有 k 和 e
        self.pending_backups = {}  
        self._is_stopping = {}  # 记录是否是用户主动停止
        self.auto_restart_count = {}  # 记录自动重启次数
        self.auto_backup_timers = {}  # 定时备份定时器  
        self._size_cache = {}  # ★ 缓存文件夹大小，不要写进 servers.json  
        self._port_cache = {}  # ★ 缓存端口，防止悬停时疯狂读盘
        self._proc_buffers = {}  # ★ 日志缓冲区，防止多字节中文截断
        # ★ V0.5.0：实时监控 + 每日定时重启的状态
        self._ping_text = {}      # key -> "在线: 3/20 | 延迟: 2ms"
        self._ping_busy = set()   # 正在探测的 key（防止 10s 轮次堆积）
        self._restart_timers = {}  # 每分钟检查定时重启的 QTimer
        self._restart_countdown = {}  # 已进入 60 秒倒计时的 key
        self._plan_restart = {}   # 标记"计划重启"（_finished 据此自动再启动）
        
        # 后台任务句柄（启动时可能还不存在，先置空，外部/测试可安全查询）
        self._scan_worker = None
        self._import_worker = None
        self._modpack_worker = None
        self._migrate_worker = None
        self._scan_then = None
        self._scan_again = False
        self._scan_root_used = None

        try:
            self.setWindowIcon(app_icon())
        except Exception:
            pass
        self._build_ui()
        self._load_data()
        if self.data.get("root"):
            self.root = self.data["root"]
            self.ed_root.setText(self.root)
            # ★ 启动时如果上次的总目录已经不在了（换了机器/盘符），
            #   别弹模态框卡在启动阶段，状态栏提示一下就行。
            self.do_scan(quiet=True)
        # ★ V0.5.0：实时状态监控定时器（每 10 秒 ping 一次运行中的服）
        self._ping_timer = QTimer(self)
        self._ping_timer.timeout.connect(self._ping_round)
        self._ping_timer.start(10000)

    # ---------------- UI ----------------
    def _build_ui(self):
        tb = self.addToolBar("主")
        tb.setMovable(False)

        def act(text, fn, tip=""):
            # ★ 用自绘的 NavButton，悬停/按下才有动画（QToolButton 靠
            #   样式表只能瞬间变色）。动作对象仍是 QWidget，外部
            #   照旧 setEnabled() 控制可用状态。
            b = NavButton(text)
            if tip:
                b.setToolTip(tip)
            b.clicked.connect(fn)
            tb.addWidget(b)
            return b

        act("选择目录", self.pick_and_scan, "选择总目录并扫描")
        act("重新扫描", self.do_scan)
        act("统一搬迁", self.do_migrate)
        tb.addSeparator()
        self.act_start = act("启动", self.do_start, "启动选中服务器")
        self.act_stop = act("停止", self.do_stop, "停止选中服务器")
        self.act_stop.setEnabled(False)
        tb.addSeparator()
        act("初始化服务端", self.do_create, "为选中项下载核心/模组/生成配置")
        act("服务器配置", self.do_config,
            "server.properties / 核心 / 模组 / 备份 / 启动设置，都在这里")
        act("分析日志", self.do_analyze,
            "自动分析日志里的报错，离线规则匹配，不联网")
        act("导出报错诊断", self.do_diag,
            "收集日志和配置，方便发给别人排查")
        act("删除服务器", self.do_delete_server,
            "删掉整个服务器文件夹（含存档）")
        tb.addSeparator()
        act("显示设置", self.do_display, "调整列表字号与行高")
        act("检查更新", self.do_check_update,
            "检查 GitHub 上有没有新版本（后台线程，不卡界面）")
        tb.addSeparator()
        act("导入地图", self.do_import, "把一个地图文件夹加入管理")
        act("导入整合包", self.do_import_modpack,
            "导入 Modrinth/CurseForge 整合包 zip：\n"
            "自动识别类型、装对应版本核心、并发下载全部模组")
        act("打开文件夹", self.do_open_dir)

        # 顶部：根目录（放进 centralWidget，不用 setMenuWidget）
        # 允许往窗口里拖文件夹/压缩包导入地图
        try:
            self.setAcceptDrops(True)
        except Exception:
            pass

        self.top = QFrame()
        self.top.setFrameShape(QFrame.StyledPanel)
        th = QHBoxLayout(self.top)
        th.setContentsMargins(8, 6, 8, 6)
        th.addWidget(QLabel("总目录:"))
        self.ed_root = QLineEdit()
        self.ed_root.setReadOnly(True)
        th.addWidget(self.ed_root, 1)
        b = QPushButton("选择...")
        b.clicked.connect(self.pick_and_scan)
        th.addWidget(b)

        # 左：列表（图标视图 / 表格视图，可在显示设置里切换）
        self.view_mode = self.disp.get("view", "icon")
        self.sort_mode = self.disp.get("sort", "custom")

        # --- 图标视图 ---
        self.icon_view = QListWidget()
        self.icon_view.setViewMode(QListWidget.IconMode)
        self.icon_view.setMovement(QListWidget.Static)
        self.icon_view.setResizeMode(QListWidget.Adjust)
        self.icon_view.setWrapping(True)
        self.icon_view.setSpacing(10)
        self.icon_view.setSelectionMode(
            QAbstractItemView.SingleSelection)
        # ★ 拖动排序改成自己接管鼠标（见 _drag_* 系列）。
        # 以前用 Qt 内置的 InternalMove，实际拖不动，两个原因：
        #   1) 上面设了 setMovement(Static)，而 Static 的语义就是
        #      "项目不可被用户移动"，和"想拖动"本身就矛盾；
        #   2) 内置 drag 一旦启动会阻塞在 QDrag.exec()，
        #      viewport 上的鼠标 move 事件根本收不到，
        #      表现就是"按住拖，什么都没发生"。
        # 所以显式关掉内置拖放，改由 eventFilter 自己重排。
        self.icon_view.setDragEnabled(False)
        # 不接受外部 drop：外部文件拖进来要交给主窗口的导入逻辑，
        # 这里关掉后事件会往上传给 MainWindow.dragEnterEvent。
        self.icon_view.setAcceptDrops(False)
        self.icon_view.setDragDropMode(QAbstractItemView.NoDragDrop)
        # 拖到边缘自动滚动（以前是 False，列表长了就拖不到底）
        self.icon_view.setAutoScroll(True)
        self.icon_view.itemSelectionChanged.connect(self.on_sel)
        # ★ V1.6.0：双击卡片 = 未运行就启动 / 运行中就停止
        #   （服务器配置从右键菜单进）
        self.icon_view.doubleClicked.connect(self._double_click_icon)
        # 右键菜单：图标视图不能因为换了样式就比表格少功能
        self.icon_view.setContextMenuPolicy(Qt.CustomContextMenu)
        self.icon_view.customContextMenuRequested.connect(
            self._icon_menu)
        # 自己画图标，才有悬停放大 / 点击回弹
        from .core.icons import IconDelegate
        self.icon_delegate = IconDelegate(self.icon_view, self)
        self.icon_view.setItemDelegate(self.icon_delegate)
        try:
            self.icon_view.viewport().installEventFilter(self)
        except Exception:
            pass
        self.icon_view.pressed.connect(self._icon_pressed)
        self.icon_view.setUniformItemSizes(False)
        self.icon_view.setAutoScroll(False)
        # 拖动排序后保存自定义顺序
        try:
            self.icon_view.model().rowsMoved.connect(self._save_order)
        except Exception:
            pass

        # --- 表格视图（原来的）---
        self.table = QTableWidget(0, 6)
        self.table.setHorizontalHeaderLabels(
            ["名称", "版本", "加载器", "存档", "Region", "状态"])
        self.table.verticalHeader().setVisible(False)
        self.table.setSelectionBehavior(QAbstractItemView.SelectRows)
        self.table.setEditTriggers(QAbstractItemView.NoEditTriggers)
        self.table.horizontalHeader().setSectionResizeMode(
            0, QHeaderView.Stretch)
        for c in (1, 2, 3, 4, 5):
            self.table.horizontalHeader().setSectionResizeMode(
                c, QHeaderView.ResizeToContents)
        self.table.itemSelectionChanged.connect(self.on_sel)
        # ★ V1.6.0：双击行 = 未运行就启动 / 运行中就停止
        self.table.doubleClicked.connect(self._double_click_row)
        self.table.setContextMenuPolicy(Qt.CustomContextMenu)
        self.table.customContextMenuRequested.connect(self._icon_menu)
        # 表格也要画选中竖杠 / 悬停注释（stylesheet 的 item border 不稳）
        try:
            from .core.icons import RowDelegate
            self.row_delegate = RowDelegate(self.table, self)
            self.table.setItemDelegate(self.row_delegate)
        except Exception:
            pass
        # 表格也能拖动行排序（和图标视图一致），事件走 eventFilter
        try:
            self.table.viewport().installEventFilter(self)
            self.table.setMouseTracking(True)
            self.table.viewport().setMouseTracking(True)
        except Exception:
            pass

        self.left = QWidget()
        lv = QVBoxLayout(self.left)
        lv.setContentsMargins(0, 0, 0, 0)
        # 排序栏
        sh = QHBoxLayout()
        sh.addWidget(QLabel("排序:"))
        self.cb_sort = QComboBox()
        for t, d in (("自定义顺序（可拖动）", "custom"),
                     ("按版本", "version"),
                     ("按最近开服", "recent"),
                     ("按名称", "name"),
                     ("按文件夹大小", "size"),
                     ("按最近修改", "mtime")):
            self.cb_sort.addItem(t, d)
        i = self.cb_sort.findData(self.sort_mode)
        self.cb_sort.setCurrentIndex(i if i >= 0 else 0)
        self.cb_sort.currentIndexChanged.connect(self._on_sort_changed)
        # 拖动排序是没有图标提示的隐藏操作，写一行说明，免得发现不了
        sh.addStretch(1)
        self.lb_draghint = QLabel("")
        self.lb_draghint.setStyleSheet("color:#9aa4b2; font-size:9pt;")
        sh.addWidget(self.lb_draghint)
        sh.addWidget(self.cb_sort, 1)
        lv.addLayout(sh)
        lv.addWidget(self.icon_view, 1)
        lv.addWidget(self.table, 1)
        self.icon_view.setVisible(self.view_mode == "icon")
        self.table.setVisible(self.view_mode == "table")

        # 右：信息 + 控制台
        self.info = QTextEdit()
        self.info.setReadOnly(True)

        # 总输出：软件自己的日志（扫描/备份/搬迁提示），始终存在
        self.console = Console()
        self.console.set_colors(self.disp.get("console_colors") or None)
        # 每个运行中的服务器一个独立控制台，互不干扰 -> 支持多开
        self.consoles = {}

        right = QSplitter(Qt.Vertical)
        g = QGroupBox("信息")
        gl = QVBoxLayout(g)
        gl.addWidget(self.info)
        g.setMaximumHeight(260)
        right.addWidget(g)
        gc = QGroupBox("控制台")
        gcl = QVBoxLayout(gc)
        self.console_tabs = QTabWidget()
        self.console_tabs.setTabsClosable(True)
        self.console_tabs.setMovable(True)
        self.console_tabs.tabCloseRequested.connect(self._close_console_tab)
        self.console_tabs.addTab(self.console, "输出")
        # 第 0 页（总输出）不给关闭按钮
        try:
            self.console_tabs.tabBar().setTabButton(
                0, QTabBar.ButtonPosition.RightSide, None)
        except Exception:
            pass
        gcl.addWidget(self.console_tabs)
        right.addWidget(gc)
        right.setSizes([240, 500])

        sp = QSplitter(Qt.Horizontal)
        sp.addWidget(self.left)
        sp.addWidget(right)
        sp.setSizes([620, 540])

        central = QWidget()
        cv = QVBoxLayout(central)
        cv.setContentsMargins(4, 4, 4, 4)
        cv.setSpacing(4)
        cv.addWidget(self.top)
        cv.addWidget(sp, 1)
        self.setCentralWidget(central)

        self.setStatusBar(QStatusBar())
        self.statusBar().showMessage("就绪")
        # ★ 显式给主窗口套一次主题。
        #   以前只在『显示设置』保存后才调用，直接启动时全靠
        #   QApplication 级样式兜着 —— 一旦那条路径没走（比如
        #   被别的代码重置了 app 级样式），主窗口就会掉回系统色。
        try:
            self._apply_theme()
        except Exception:
            pass
        self._apply_display()


    # ---------------- 列表视图：图标 / 表格 / 排序 ----------------
    def _icon_for(self, e, px=64):
        """给服务器做图标：优先用存档里的 icon.png。

        没有就用加载器首字母画一个彩色圆角方块。
        """
        from PySide6.QtGui import QPixmap, QPainter, QBrush
        # 0) 用户自己指定的图标（右键『设置图标』存进 servers.json）
        #    优先级最高 —— 自己选的当然要盖过自动识别的
        custom = (self._saved(e).get("icon") or "").strip()
        if custom and os.path.isfile(custom):
            pm = QPixmap(custom)
            if not pm.isNull():
                return pm.scaled(px, px, Qt.KeepAspectRatio,
                                Qt.SmoothTransformation)
        # 1) 服务器根目录的 server-icon.png
        for base in (e.get("server_dir"), e.get("folder")):
            if not base:
                continue
            for nm in ("server-icon.png", "icon.png"):
                p = os.path.join(base, nm)
                if os.path.isfile(p):
                    pm = QPixmap(p)
                    if not pm.isNull():
                        return pm.scaled(px, px, Qt.KeepAspectRatio,
                                        Qt.SmoothTransformation)
        # 2) 存档目录里的 icon.png
        wd = e.get("world_dir")
        if wd:
            p = os.path.join(wd, "icon.png")
            if os.path.isfile(p):
                pm = QPixmap(p)
                if not pm.isNull():
                    return pm.scaled(px, px, Qt.KeepAspectRatio,
                                    Qt.SmoothTransformation)
        # 3) 画一个
        col = {"fabric": "#1976d2", "forge": "#e67e22",
               "neoforge": "#8e24aa", "paper": "#00897b",
               "vanilla": "#5d4037"}.get(
                   (e.get("loader") or "").lower(), "#607d8b")
        pm = QPixmap(px, px)
        pm.fill(Qt.transparent)
        from PySide6.QtGui import QColor
        ptr = QPainter(pm)
        ptr.setRenderHint(QPainter.Antialiasing)
        ptr.setBrush(QBrush(QColor(col)))
        ptr.setPen(Qt.NoPen)
        # ★ 原来写死 (0, 0, 64, 64) —— QPixmap 是 px x px，
        #   px != 64 时圆角矩形要么缩在左上角（px > 64），
        #   要么被裁掉右下角（px < 64）。改成按 px 铺满整张位图。
        # ★ 圆角半径也按 px 缩放：原来写死 12，px=24 时圆角占 50%
        #   看着像圆形，px=136 时占 9% 看着像方形。改成 px/8、
        #   夹在 4~16 之间，各尺寸视觉一致。
        rad = max(4, min(16, px // 8))
        ptr.drawRoundedRect(0, 0, px, px, rad, rad)
        ptr.setPen(QColor("#ffffff"))
        f = QFont()
        f.setPointSize(max(9, int(px * 0.34)))
        f.setBold(True)
        ptr.setFont(f)
        txt = (e.get("loader") or "?")[:1].upper()
        ptr.drawText(pm.rect(), Qt.AlignCenter, txt)
        ptr.end()
        return pm

    def _clean_ver(self, e):
        """★ V1.7.1-3：显示用版本净化 —— Paper 构建号等
        不像 MC 版本的值显示层再兜底清洗一次。"""
        try:
            v = e.get("version")
            if not v:
                return None
            cv = scanner.clean_mc_version(v)
            return cv or v
        except Exception:
            return e.get("version")

    def _tip_for(self, e):
        """鼠标悬停时弹出的注释（PCL 风格）。

        ★ 图标下面是精简的一两行，完整信息放这里 ——
          目录、Java、端口、存档名这些不占版面，但很常用。
        """
        L = []
        name = self._display_name(e) or e.get("world_name") or "?"
        L.append(name)
        bits = []
        if e.get("version"):
            bits.append("MC %s" % self._clean_ver(e))
        if e.get("loader"):
            bits.append(versions.LOADER_LABEL.get(e["loader"],
                                                  e["loader"]))
        if bits:
            L.append(" / ".join(bits))
        if e.get("world_name") and e.get("world_name") != name:
            L.append("存档名：%s" % e["world_name"])
        if e.get("dir"):
            L.append("目录：%s" % e["dir"])
        jv = e.get("java") or e.get("java_path")
        if jv:
            L.append("Java：%s" % (jv if len(str(jv)) < 48
                                   else "..." + str(jv)[-45:]))
        # ★ 改用带有缓存的函数，防止悬停时疯狂读盘卡死界面
        port = self._port_of_cached(e)
        if port:
            L.append("端口：%s" % port)
        if e.get("regions"):
            L.append("Region：%s 个" % e["regions"])
        if self._key(e) in self.procs:
            L.append("● 运行中")
        elif e.get("notes"):
            L.append("● 未初始化：%s" % (e.get("notes") or "")[:60])
        return "\n".join(L)

    def _item_text(self, e, as_lines=False):
        """按显示设置拼出图标下面的文字。

        as_lines=True 时返回纯文本列表（给 delegate 用）；
        False 时返回富文本 HTML（保留兼容，实际已无人调用）。
        """
        show = self.disp.get("show", None)
        if show is None:
            show = ["name", "version", "loader", "status"]
        lines = []
        # 用文件夹名（导入时已清洗过），不用 level.dat 里的世界名 ——
        # 后者可能带 §b§l 颜色码和冒号，显示出来是乱码
        name = self._display_name(e) or e.get("world_name") or "?"
        if "name" in show:
            lines.append(name)
        sub = []
        if "version" in show and e.get("version"):
            sub.append(self._clean_ver(e))
        if "loader" in show and e.get("loader"):
            sub.append(versions.LOADER_LABEL.get(
                e["loader"], e["loader"]))
        if "world" in show and e.get("world_name"):
            sub.append(e["world_name"])
        if "regions" in show:
            sub.append("%d region" % (e.get("regions") or 0))
        if sub:
            lines.append(" · ".join(sub))
        if "status" in show:
            if self._key(e) in self.procs:
                # ★ V0.5.0：运行中有监控文本就显示"在线: 3/20 | 延迟: 2ms"
                ping = self._ping_text.get(self._key(e))
                if ping:
                    lines.append(ping)
                else:
                    lines.append("● 运行中")
            elif e.get("notes"):
                lines.append("● 未初始化")
        # ★ 一个都没勾时不能崩（IndexError），至少显示名称
        if not lines:
            lines = [str(self._display_name(e)
                         or e.get("world_name") or "?")]
        if as_lines:
            return lines
        out = "<b>%s</b>" % lines[0]
        for ln in lines[1:]:
            out += "<br><span style='font-size:9pt'>%s</span>" % ln
        return out

    def _sorted_entries(self):
        """按当前排序方式返回 entries（不改 self.entries 本身）。"""
        mode = self.sort_mode
        if mode == "custom":
            order = self.disp.get("order", {})
            def k(e):
                kk = self._key(e)
                return (order.get(kk, 9999),
                        str(self._display_name(e) or ""))
            return sorted(self.entries, key=k)
        if mode == "version":
            from .core import versions as _V
            def vk(e):
                # ★ 用 versions.mc_version_key（只取版本主体、补齐长度、
                #   预发布排后面），不再 findall 抓所有数字。
                #   旧写法在版本号带后缀时长度不一致，
                #   会排出"1.21.10 夹着 1.21.11"这种乱序。
                # 同版本时用名称兜底，保证顺序稳定。
                return (_V.mc_version_key(e.get("version")),
                        str(self._display_name(e) or "").lower())
            return sorted(self.entries, key=vk)
        if mode == "recent":
            def rk(e):
                t = self._saved(e).get("last_start")
                return -(float(t) if t else 0)
            return sorted(self.entries, key=rk)
        if mode == "size":
            # 按文件夹大小排序，大的在前（缓存 60 秒）。
            # ★ 未缓存时先给占位值，绝不在这里同步扫盘卡主线程 ——
            #   真正的大小由 _on_sort_changed 触发的后台 Worker
            #   （_prefetch_sizes）算完写缓存后自动刷新。
            import time as _t
            cache = self._size_cache
            now = _t.time()

            def sk(e):
                key = self._key(e)
                cached = cache.get(key)
                if cached and now - cached.get("t", 0) < 60:
                    return -cached["s"]
                return -1  # 未缓存：占位，等后台算完 _fill_views 刷新
            return sorted(self.entries, key=sk)
        if mode == "mtime":
            # 按文件夹最后修改时间，新的在前
            def mk(e):
                d = e.get("server_dir") or e.get("folder") or ""
                try:
                    return -os.path.getmtime(d)
                except Exception:
                    return 0
            return sorted(self.entries, key=mk)
        # name
        return sorted(self.entries,
                      key=lambda e: str(self._display_name(e) or "").lower())

    def _fill_icons(self):
        """填充图标视图。

        文字不再用 setItemWidget(QLabel)，而是塞进 UserRole+3
        交给 IconDelegate 画 —— 这样才能做悬停/点击动画
        （QListWidget 自带的 IconMode 画不了动画）。
        """
        self.icon_view.clear()
        sz = int(self.disp.get("icon", 110))
        # 图标本体随"图标大小"一起放大（以前写死 64，
        # 于是调大小只把卡片撑大、文字变多，图没跟着长）
        px = max(24, int(sz * 0.62))
        self.icon_view._icon_px = px
        for e in self._sorted_entries():
            it = QListWidgetItem()
            pm = self._icon_for(e, px)
            if isinstance(pm, QPixmap) and not pm.isNull():
                it.setData(Qt.DecorationRole, pm)
            it.setData(Qt.UserRole, self._key(e))
            it.setData(Qt.UserRole + 2, e.get("loader") or "unknown")
            # ★ 以前走 _item_text 拿富文本，再用 re.sub 剥标签、
            #   split("\n") —— 但 _item_text 的分隔符是 <br> 不是 \n，
            #   re.sub 把 <br> 一起删了，split 找不到换行，
            #   多行信息全合并成 1 行，图标下面就显示一整串。
            #   现在直接让 _item_text 返回 list，delegate 拿到真正的行列表。
            lines = self._item_text(e, as_lines=True)
            it.setData(Qt.UserRole + 3, lines)
            it.setToolTip(self._tip_for(e))   # 悬停注释（PCL 风格）
            self.icon_view.addItem(it)
            it.setSizeHint(QSize(sz + 16,
                                 px + 34 + 17 * max(1, len(lines))))
        # 网格对齐，拖动时落点才跟视觉一致
        try:
            self.icon_view.setGridSize(
                QSize(sz + 18, px + 38 + 17 * 3))
        except Exception:
            pass

    def _icon_pressed(self, index):
        """按下时通知 delegate 做缩小动画。"""
        try:
            self.icon_delegate.set_press(index.row())
        except Exception:
            pass

    def eventFilter(self, obj, event):
        """图标区的动画回弹 + 自己实现的拖动排序。"""
        try:
            from PySide6.QtCore import QEvent
            # ★ 这里以前写 getattr(x, "viewport", None) —— 那拿到的是
            #   "方法对象"，不是真正的 viewport 控件，于是下面的
            #   "obj is vp_icon" 永远是 False，整个 eventFilter
            #   一次都没生效过（连点击回弹动画也是假的）。必须调用它。
            try:
                vp_icon = self.icon_view.viewport()
            except Exception:
                vp_icon = None
            try:
                vp_tab = self.table.viewport()
            except Exception:
                vp_tab = None
            t = event.type()
            # ★ 这里以前写的是 QEvent.MouseButtonMove —— 那是 Qt3 的
            #   老名字，PySide6 里根本没有，一取就 AttributeError，
            #   而下面又有 try/except 把异常吞了，于是拖动逻辑
            #   从来没执行过（表现就是"完全拖不动"）。Qt6 里按住
            #   移动的鼠标事件就是 MouseMove。
            MOVE = (QEvent.MouseButtonPress, QEvent.MouseMove,
                    QEvent.MouseButtonRelease)
            if t in MOVE:
                pos = self._ev_pos(event)
                if obj is vp_icon and self.view_mode == "icon":
                    if t == QEvent.MouseButtonPress:
                        self._drag_begin(pos)
                    elif t == QEvent.MouseMove:
                        self._drag_move(pos)
                    else:
                        self._drag_end()
                elif obj is vp_tab and self.view_mode == "table":
                    if t == QEvent.MouseButtonPress:
                        self._tdrag_begin(pos)
                    elif t == QEvent.MouseMove:
                        self._tdrag_move(pos)
                    else:
                        self._tdrag_end()
            if obj is vp_icon:
                if t == QEvent.MouseButtonRelease:
                    self.icon_delegate.clear_press()
                elif t == QEvent.Leave:
                    self.icon_delegate.leave()
                    self._drag_end()
        except Exception:
            # ★ 以前这里直接 pass，于是"取了不存在的枚举"这类错误
            #   永远看不见，拖动功能静悄悄失效。现在第一次出错时
            #   打出来（不重复刷屏）。
            if not getattr(self, "_ef_warned", False):
                self._ef_warned = True
                try:
                    import traceback
                    sys.stderr.write("[eventFilter] %s\n"
                                     % traceback.format_exc())
                except Exception:
                    pass
        return super().eventFilter(obj, event)

    # ---------------- 拖动排序（自己实现） ----------------
    # 为什么不用 Qt 内置的 InternalMove：见上面 icon_view 初始化处的
    # 注释 —— 内置那套在这会"按住拖没反应"。这里直接接管鼠标，
    # 拖到哪就插到哪，松手立刻保存。
    DRAG_SLOP2 = 36          # 位移超过 ~6px 才算拖动，避免点一下就乱动

    def _ev_pos(self, event):
        """Qt6 用 position()，老写法 pos() 兜底。"""
        try:
            return event.position().toPoint()
        except Exception:
            try:
                return event.pos()
            except Exception:
                return QPoint(0, 0)

    def _drag_begin(self, pos):
        self._drag = None
        it = self.icon_view.itemAt(pos)
        if it is None:
            return
        self._drag = {"item": it, "start": QPoint(pos), "on": False}

    def _drag_move(self, pos):
        d = getattr(self, "_drag", None)
        if not d:
            return
        if not d["on"]:
            dx = pos.x() - d["start"].x()
            dy = pos.y() - d["start"].y()
            if dx * dx + dy * dy < self.DRAG_SLOP2:
                return
            d["on"] = True
            try:
                d["item"].setData(Qt.UserRole + 4, True)
                self.icon_view.setCursor(Qt.ClosedHandCursor)
            except Exception:
                pass
        cur = self.icon_view.row(d["item"])
        tgt = self.icon_view.itemAt(pos)
        if tgt is None or tgt is d["item"]:
            return
        tr = self.icon_view.row(tgt)
        if cur < 0 or tr < 0 or cur == tr:
            return
        # takeItem 拿出来的还是同一个 item 对象，不会丢数据
        taken = self.icon_view.takeItem(cur)
        self.icon_view.insertItem(tr, taken)
        self.icon_view.setCurrentItem(taken)
        # 动画进度按行号存的，拖动后行号变了，清一下免得乱跳
        self.icon_delegate.hover_row = tr
        self.icon_view.viewport().update()

    def _drag_end(self):
        d = getattr(self, "_drag", None)
        self._drag = None
        if not d:
            return
        try:
            d["item"].setData(Qt.UserRole + 4, False)
            self.icon_view.unsetCursor()
        except Exception:
            pass
        if not d["on"]:
            return
        self._save_order()
        try:
            self.statusBar().showMessage("顺序已保存", 2500)
        except Exception:
            pass

    # ---- 表格视图：拖动行排序 ----
    def _tdrag_begin(self, pos):
        self._tdrag = None
        idx = self.table.indexAt(pos)
        if not idx.isValid():
            return
        self._tdrag = {"item": self.table.item(idx.row(), 0),
                       "start": QPoint(pos), "on": False}

    def _table_row_of(self, it):
        if it is None:
            return -1
        for r in range(self.table.rowCount()):
            if self.table.item(r, 0) is it:
                return r
        return -1

    def _tdrag_move(self, pos):
        d = getattr(self, "_tdrag", None)
        if not d:
            return
        if not d["on"]:
            dx = pos.x() - d["start"].x()
            dy = pos.y() - d["start"].y()
            if dx * dx + dy * dy < self.DRAG_SLOP2:
                return
            d["on"] = True
            self.table.setCursor(Qt.ClosedHandCursor)
        cur = self._table_row_of(d["item"])
        idx = self.table.indexAt(pos)
        if cur < 0 or not idx.isValid():
            return
        tr = idx.row()
        if tr == cur:
            return
        self._table_move_row(cur, tr)

    def _table_move_row(self, frm, to):
        n = self.table.columnCount()
        items = [self.table.takeItem(frm, c) for c in range(n)]
        self.table.removeRow(frm)
        t = to if to <= frm else to - 1
        t = max(0, min(t, self.table.rowCount()))
        self.table.insertRow(t)
        for c in range(n):
            self.table.setItem(t, c, items[c] if items[c]
                               is not None else QTableWidgetItem(""))
        self.table.setRowHeight(t, 26)
        self.table.selectRow(t)

    def _tdrag_end(self):
        d = getattr(self, "_tdrag", None)
        self._tdrag = None
        if not d:
            return
        try:
            self.table.unsetCursor()
        except Exception:
            pass
        if d["on"]:
            self._save_order()
            try:
                self.statusBar().showMessage("顺序已保存", 2500)
            except Exception:
                pass

    def _icon_menu(self, pos):
        """列表右键菜单 —— 图标视图和表格视图共用。

        为什么要加：
          换成图标视图以后，很多操作只能回顶部工具栏找，
          感觉"功能变少了"。右键把最常用的几个放在手边。
        """
        # 不用 sender()：直接按当前视图取，逻辑更明确
        if getattr(self, "view_mode", "table") == "icon":
            view = self.icon_view
            it = view.itemAt(pos)
            if it is not None:
                view.setCurrentItem(it)
            e = self._icon_entry(it) if it is not None else None
        else:
            view = self.table
            idx = view.indexAt(pos)
            if not idx.isValid():
                return
            view.selectRow(idx.row())
            # ★ B2：selectRow 之后 selectionModel 已经更新，不必再查一遍；
            #   而且 _sorted_entries() 的顺序就是 _fill_table 的填充顺序。
            sorted_e = self._sorted_entries()
            e = (sorted_e[idx.row()]
                 if idx.row() < len(sorted_e) else None)
        if e is None:
            e = self._cur_entry()
        if not e:
            return
        from PySide6.QtWidgets import QMenu
        m = QMenu(self)
        running = self._key(e) in self.procs
        a_start = m.addAction("启动") if not running else None
        a_stop = m.addAction("停止") if running else None
        m.addSeparator()
        a_cfg = m.addAction("服务器配置")
        a_init = m.addAction("初始化服务端")
        m.addSeparator()
        a_bk = m.addAction("备份管理")
        a_mod = m.addAction("模组管理")
        a_log = m.addAction("分析日志")
        m.addSeparator()
        a_rename = m.addAction("重命名服务器")
        a_icon = m.addAction("设置服务器图标...")
        a_clricon = m.addAction("清除自定义图标")
        a_bat = m.addAction("重新生成 run.bat")
        a_open = m.addAction("打开文件夹")
        a_del = m.addAction("删除服务器")
        act = m.exec(view.viewport().mapToGlobal(pos))
        if not act:
            return
        if act is a_start:
            self.do_start()
        elif act is a_stop:
            self.do_stop()
        elif act is a_cfg:
            self.do_config()
        elif act is a_init:
            self.do_create()
        elif act is a_bk:
            self.do_backup()
        elif act is a_mod:
            self.do_mods()
        elif act is a_log:
            self.do_analyze()
        elif act is a_icon:
            self.do_edit_icon()
        elif act is a_rename:
            self.do_rename_server()
        elif act is a_clricon:
            self.do_clear_icon()
        elif act is a_bat:
            self.do_gen_bat()
        elif act is a_open:
            self.do_open_dir()
        elif act is a_del:
            self.do_delete_server()

    def _cur_entry(self):
        """当前选中的那个服务器（不管现在是图标视图还是表格视图）。"""
        entry = None
        if getattr(self, "view_mode", "table") == "icon":
            it = self.icon_view.currentItem()
            if it is not None:
                entry = self._icon_entry(it)
            if entry is None:
                for it2 in self.icon_view.selectedItems():
                    entry = self._icon_entry(it2)
                    if entry:
                        break
        else:
            # ★ 表格行号是"按 _sorted_entries() 填的"，
            #   这里必须同样按排序后的顺序取，否则排序非 custom
            #   时会拿到错的服务器。
            rows = self.table.selectionModel().selectedRows()
            sorted_e = self._sorted_entries()
            if rows and rows[0].row() < len(sorted_e):
                entry = sorted_e[rows[0].row()]
        return entry

    def _icon_entry(self, it):
        """图标项 -> entry"""
        if it is None:
            return None
        k = it.data(Qt.UserRole)
        for e in self.entries:
            if self._key(e) == k:
                return e
        return None

    def _save_order(self, *a):
        """拖动后保存自定义顺序，并自动切到『自定义顺序』。

        为什么自动切：按版本/名称排序时拖动不会生效
        （下次刷新按排序规则又排回去了），
        用户会以为"拖动坏了"。这里检测到拖动就直接切成自定义，
        让拖的结果真的保留下来。
        """
        order = {}
        if getattr(self, "view_mode", "table") == "table":
            for r in range(self.table.rowCount()):
                it = self.table.item(r, 0)
                k = it.data(Qt.UserRole) if it is not None else None
                if k:
                    order[k] = r
        else:
            for i in range(self.icon_view.count()):
                it = self.icon_view.item(i)
                k = it.data(Qt.UserRole)
                if k:
                    order[k] = i
        if not order:
            return
        self.disp["order"] = order
        # 拖动了 -> 排序方式必须是自定义，否则白拖
        if (self.cb_sort.currentData() or "custom") != "custom":
            idx = self.cb_sort.findData("custom")
            if idx >= 0:
                self.cb_sort.blockSignals(True)
                self.cb_sort.setCurrentIndex(idx)
                self.cb_sort.blockSignals(False)
                self.sort_mode = "custom"
                self.disp["sort"] = "custom"
        self._save_data()
        try:
            self.statusBar().showMessage(
                "顺序已保存（排序方式已切为『自定义顺序』）", 3000)
        except Exception:
            pass

    def _on_sort_changed(self):
        self.sort_mode = self.cb_sort.currentData() or "custom"
        self.disp["sort"] = self.sort_mode
        self._fill_views()
        self._save_data()
        if self.sort_mode == "custom":
            self.statusBar().showMessage(
                "自定义顺序：拖动图标即可调整，会自动保存", 4000)
        elif self.sort_mode == "size":
            # ★ 按大小排序：后台线程算目录大小，绝不卡死主线程
            self._prefetch_sizes()

    def _prefetch_sizes(self):
        """按大小排序时，后台计算所有服务器目录大小（写进 _size_cache）。"""
        if getattr(self, "_size_worker_running", False):
            return
        self._size_worker_running = True
        self.statusBar().showMessage("正在计算各服务器大小 ...")
        entries = list(self.entries)
        import time as _t

        def job(on_log=None):
            from .core import install as _inst
            now = _t.time()
            out = {}
            for e in entries:
                d = e.get("server_dir") or e.get("folder") or ""
                key = self._key(e)
                try:
                    out[key] = _inst.server_size(d)
                except Exception:
                    out[key] = 0
            return {"sizes": out, "t": now}

        w = _track(self, Worker(job, {}, self))
        self._size_worker = w
        w.done.connect(self._on_sizes_done)
        w.start()

    def _on_sizes_done(self, ok, res):
        self._size_worker_running = False
        try:
            w, self._size_worker = getattr(self, "_size_worker", None), None
            if w is not None:
                self._forget_worker(w)
        except Exception:
            pass
        if ok and res:
            sizes = res.get("sizes") or {}
            t = res.get("t", 0)
            for k, s in sizes.items():
                self._size_cache[k] = {"s": s, "t": t}
            self._fill_views()
            self.statusBar().showMessage(
                "大小计算完成，已按大小排序", 4000)
        else:
            self.statusBar().showMessage(
                "大小计算失败，保持当前顺序", 4000)

    def _switch_view(self, mode):
        self.view_mode = mode
        self.disp["view"] = mode
        self.icon_view.setVisible(mode == "icon")
        self.table.setVisible(mode == "table")
        self._save_data()
        self._update_draghint()

    # ---------------- 显示设置 ----------------
    def _apply_display(self):
        f = QFont()
        f.setPointSize(int(self.disp.get("font", 13)))
        self.table.setFont(f)
        hh = self.table.horizontalHeader()
        hf = QFont()
        hf.setPointSize(int(self.disp.get("font", 13)))
        hf.setBold(True)
        hh.setFont(hf)
        try:
            self.table.verticalHeader().setDefaultSectionSize(
                int(self.disp.get("row", 30)))
        except Exception:
            pass
        for r in range(self.table.rowCount()):
            self.table.setRowHeight(r, int(self.disp.get("row", 30)))

    def _wrap(self, w):
        """把一个已带布局的 widget 变成页签页。"""
        return w

    def _console_colors_tab(self):
        """V1.1.0：控制台配色页签（info / warn / error / chat）。

        返回 {"widget": 页签widget, "result": 取色函数}。
        """
        cur = self.disp.get("console_colors") or {}
        w = QWidget()
        vv = QVBoxLayout(w)

        tip = QLabel(
            "自定义控制台各类型日志的颜色，不设置就恢复默认。\n"
            "报错堆栈（Exception / Caused by / at net.minecraft...）\n"
            "会使用『错误』的颜色并加粗。")
        tip.setWordWrap(True)
        tip.setStyleSheet("color:#9aa4b2;")
        vv.addWidget(tip)

        f = QFormLayout()
        items = [
            ("info", "普通信息"),
            ("warn", "警告"),
            ("error", "错误"),
            ("chat", "玩家聊天"),
        ]
        btns = {}
        from PySide6.QtWidgets import QColorDialog
        from PySide6.QtGui import QColor

        def _txt_on(hx):
            """色块上的文字色：按亮度自动黑/白，保证可读。"""
            try:
                from .core import theme as _th
                return _th.ensure_contrast("#ffffff", hx, 4.5)
            except Exception:
                return "#ffffff"

        def _mk(key, label):
            default = Console.DEFAULT_COLORS.get(key, "#d0d0d0")
            hexs = cur.get(key) or default
            b = QPushButton(hexs)
            b.setStyleSheet(
                "QPushButton{background:%s; color:%s;"
                "border:1px solid rgba(128,128,128,90);"
                "border-radius:4px; padding:4px 10px;}"
                "QPushButton:hover{font-weight:bold;}"
                % (hexs, _txt_on(hexs)))
            b.setToolTip("点击打开颜色选择器，选中后按钮显示该颜色色块")
            b.setMinimumHeight(28)

            def pick():
                col = QColorDialog.getColor(
                    QColor(hexs), self,
                    "选择『%s』的颜色" % label)
                if col.isValid():
                    hx = col.name()
                    b.setText(hx)
                    b.setStyleSheet(
                        "QPushButton{background:%s; color:%s;"
                        "border:1px solid rgba(128,128,128,90);"
                        "border-radius:4px; padding:4px 10px;}"
                        "QPushButton:hover{font-weight:bold;}"
                        % (hx, _txt_on(hx)))

            b.clicked.connect(pick)
            return b

        for key, label in items:
            btns[key] = _mk(key, label)
            f.addRow("%s:" % label, btns[key])

        b_reset = QPushButton("恢复默认配色")
        b_reset.clicked.connect(
            lambda: _reset(btns, items))
        vv.addLayout(f)

        def _reset(bts, its):
            for key, label in its:
                d = Console.DEFAULT_COLORS.get(key, "#d0d0d0")
                bts[key].setText(d)
                bts[key].setStyleSheet(
                    "QPushButton{background:%s; color:%s;"
                    "border:1px solid rgba(128,128,128,90);"
                    "border-radius:4px; padding:4px 10px;}"
                    "QPushButton:hover{font-weight:bold;}"
                    % (d, _txt_on(d)))

        vv.addWidget(b_reset)
        vv.addStretch(1)

        def _result():
            out = {}
            for key, label in items:
                out[key] = btns[key].text().strip() or \
                    Console.DEFAULT_COLORS.get(key, "#d0d0d0")
            return out

        return {"widget": w, "result": _result}

    def _appearance_tab(self):
        """外观/主题设置页。"""
        from .core import theme as _th
        cur = dict(_th.get())

        w = QWidget()
        v = QVBoxLayout(w)

        # ---- 字体 ----
        gf = QGroupBox("字体")
        ff = QFormLayout(gf)
        cb_fam = QFontComboBox()
        if cur.get("font_family"):
            cb_fam.setCurrentFont(QFont(cur["font_family"]))
        # ★ QFontComboBox 默认给的是 "Sans Serif"。以前不管用户动没动过，
        #   点 OK 都把这个默认值写进配置 —— 中文 Windows 上会把界面字体
        #   换成不适配中文的族，表现就是"改了颜色字体反而变丑/像没生效"。
        #   只有用户真的选过才写入。
        _touched = {"font": False}
        cb_fam.currentFontChanged.connect(
            lambda f: _touched.__setitem__("font", True))
        ff.addRow("字体:", cb_fam)
        sp_fs = QSpinBox()
        sp_fs.setRange(0, 24)
        sp_fs.setSpecialValueText("默认")
        sp_fs.setValue(int(cur.get("font_size") or 0))
        ff.addRow("字号:", sp_fs)

        # ---- 字体效果：粗体 ----
        he = QHBoxLayout()
        cb_bold = QCheckBox("粗体")
        cb_bold.setChecked(bool(int(cur.get("font_bold") or 0)))
        cb_bold.setToolTip("全局字体加粗（按钮、标签、表格都会变粗）")
        he.addWidget(cb_bold)
        he.addStretch(1)
        ff.addRow("效果:", he)
        v.addWidget(gf)

        # ---- 颜色 ----
        gc = QGroupBox("颜色")
        fc = QFormLayout(gc)
        picks = {}

        def mk(key, label):
            b = QPushButton("")
            b.setFixedWidth(70)

            def paint():
                b.setStyleSheet(
                    "background:%s; border:1px solid #888;"
                    % cur[key])
            paint()

            def pick():
                c = QColorDialog.getColor(QColor(cur[key]), w,
                                          label)
                if c.isValid():
                    cur[key] = c.name()
                    paint()
            b.clicked.connect(pick)
            picks[key] = (b, paint)
            fc.addRow(label + ":", b)

        mk("fg", "字体颜色")
        mk("bg", "背景颜色")
        mk("accent", "强调色")
        v.addWidget(gc)

        # ---- 背景图 ----
        gi = QGroupBox("背景图片（只套在主窗口上）")
        vi = QVBoxLayout(gi)
        hb = QHBoxLayout()
        ed_img = QLineEdit(cur.get("bg_image") or "")
        ed_img.setPlaceholderText("不选就是纯色背景")
        hb.addWidget(ed_img, 1)
        b_img = QPushButton("选择...")

        def pick_img():
            f, _ = QFileDialog.getOpenFileName(
                w, "选背景图", "", "图片 (*.png *.jpg *.jpeg *.bmp)")
            if f:
                ed_img.setText(f)
        b_img.clicked.connect(pick_img)
        hb.addWidget(b_img)
        b_clr = QPushButton("清除")

        def clr_img():
            ed_img.setText("")
        b_clr.clicked.connect(clr_img)
        hb.addWidget(b_clr)
        vi.addLayout(hb)

        cb_mode = QComboBox()
        cb_mode.addItem("拉伸铺满（常用）", "stretch")
        cb_mode.addItem("平铺重复", "tile")
        i = cb_mode.findData(cur.get("bg_mode") or "stretch")
        cb_mode.setCurrentIndex(i if i >= 0 else 0)
        vi.addWidget(cb_mode)

        sl = QSlider(Qt.Horizontal)
        sl.setRange(0, 100)
        sl.setValue(int(cur.get("bg_opacity") or 88))
        lbl_op = QLabel("")

        def show_op(n):
            lbl_op.setText("遮罩浓度 %d%%（越高文字越清楚、图越暗）"
                           % n)
        sl.valueChanged.connect(show_op)
        show_op(sl.value())
        vi.addWidget(sl)
        vi.addWidget(lbl_op)
        v.addWidget(gi)

        # ---- 预设 ----
        gp = QGroupBox("预设")
        hp = QHBoxLayout(gp)
        PRESETS = list(_th.PRESETS)
        for name, fg, bg, ac in PRESETS:
            b = QPushButton(name)

            def go(fg=fg, bg=bg, ac=ac, name=name):
                cur["fg"], cur["bg"], cur["accent"] = fg, bg, ac
                # ★ 深浅切换时"项目常驻字色"要跟着换：
                #   浅色档是黑字，直接用到墨绿底上等于消失。
                try:
                    mode = _th.preset_apply(name)[3]
                    cur["item_idle_mode"] = mode
                    j = cb_idle.findData(mode)
                    if j >= 0:
                        cb_idle.setCurrentIndex(j)
                except Exception:
                    pass
                for k in ("fg", "bg", "accent"):
                    if k in picks:
                        picks[k][1]()
            b.clicked.connect(go)
            hp.addWidget(b)

        def reset_def():
            for k in ("fg", "bg", "accent"):
                cur[k] = _th.DEFAULT[k]
                if k in picks:
                    picks[k][1]()
            cur["item_idle_mode"] = _th.DEFAULT["item_idle_mode"]
            try:
                j = cb_idle.findData(_th.DEFAULT["item_idle_mode"])
                if j >= 0:
                    cb_idle.setCurrentIndex(j)
            except Exception:
                pass
            cb_fam.setCurrentFont(QFont(""))
            sp_fs.setValue(int(_th.DEFAULT["font_size"]))
            # setCurrentFont 会触发 currentFontChanged，这里要明确表示
            # "恢复系统默认"而不是"用户选了 Sans Serif"
            cur["font_family"] = ""
            _touched["font"] = False
        b_def = QPushButton("恢复默认")
        b_def.setToolTip("恢复成默认浅灰白配色 + 系统默认字体")
        b_def.clicked.connect(reset_def)
        hp.addWidget(b_def)
        v.addWidget(gp)

        # ---- 选中与悬停效果 ----
        gp2 = QGroupBox("选中与悬停效果")
        fp2 = QFormLayout(gp2)

        cb_pcl = QCheckBox("启用选中 / 悬停样式")
        cb_pcl.setChecked(int(cur.get("pcl") or 0) == 1)
        cb_pcl.setToolTip(
            "启用后：\n"
            " · 页签常驻白字，选中换成主题色并套一圈底\n"
            " · 列表项目常驻暗色字，选中换成主题色 + 左右两条竖杠\n"
            " · 鼠标悬停弹出注释")
        fp2.addRow("", cb_pcl)

        cb_tab_idle = QComboBox()
        cb_tab_idle.addItem("白色", "#ffffff")
        cb_tab_idle.addItem("黑色", "#000000")
        cb_tab_idle.addItem("跟随主字体色", "fg")
        ti = (cur.get("tab_idle") or "#ffffff")
        ix = cb_tab_idle.findData(ti)
        cb_tab_idle.setCurrentIndex(ix if ix >= 0 else 0)
        cb_tab_idle.setToolTip("页签【未选中】时的字色。\n"
                               "浅色主题下会自动压深，不会白底白字。")
        fp2.addRow("页签常驻字色:", cb_tab_idle)

        cb_tab_box = QCheckBox("选中的页签套一圈底")
        cb_tab_box.setChecked(int(cur.get("tab_sel_box") or 0) == 1)
        cb_tab_box.setToolTip("勾选后选中的页签套一圈底；不勾则用主题色淡底。")
        fp2.addRow("", cb_tab_box)

        cb_idle = QComboBox()
        for k, lbl in _th.ITEM_IDLE_MODES:
            cb_idle.addItem(lbl, k)
        ii = cb_idle.findData(cur.get("item_idle_mode") or "dark")
        cb_idle.setCurrentIndex(ii if ii >= 0 else 0)
        cb_idle.setToolTip(
            "列表项目【未选中】时的字色。\n"
            "深色主题下不会用纯黑 —— 那样对比度只有 1.4，等于看不见，\n"
            "会自动换成'暗一些但仍读得清'的灰。")
        fp2.addRow("列表常驻:", cb_idle)

        cb_bars = QComboBox()
        cb_bars.addItem("不画", 0)
        cb_bars.addItem("只在左侧", 1)
        cb_bars.addItem("左右各一条（推荐）", 2)
        # ★ 用 is not None 判断，否则用户选 0 会被 or 2 强制变成 2
        sb_val = cur.get("sel_bars")
        ib = cb_bars.findData(int(sb_val) if sb_val is not None else 1)
        cb_bars.setCurrentIndex(ib if ib >= 0 else 1)
        cb_bars.setToolTip("选中项两侧的竖杠。\n"
                           "竖杠是委托画上去的：Qt 样式表里给 item 写\n"
                           "border-left/right 渲染不稳定，容易被忽略。")
        fp2.addRow("选中竖杠:", cb_bars)

        cb_tip = QCheckBox("鼠标悬停时显示注释")
        cb_tip.setChecked(int(cur.get("hover_tip") or 0) == 1)
        cb_tip.setToolTip("悬停在服务器图标/行上，弹出目录、Java、\n"
                          "端口、存档名等完整信息。")
        fp2.addRow("", cb_tip)
        v.addWidget(gp2)
        v.addStretch(1)

        def result():
            fg = cur["fg"]
            bg = cur["bg"]
            # ★ 对比度保护：字体色和背景色太接近就自动把字体色推远。
            #   "白色"不单指 #ffffff，#f5f5f5 / #eeeeee 这类近白
            #   配浅底一样看不清 —— 所以按 WCAG 相对亮度算，
            #   不是简单比字符串。
            fg2, changed = _th.ensure_contrast(fg, bg)
            if changed:
                cur["fg"] = fg2
                if "fg" in picks:
                    picks["fg"][1]()
                result.adjusted = (fg, fg2)
            else:
                result.adjusted = None
            fam = (cb_fam.currentFont().family()
                   if _touched["font"]
                   else (cur.get("font_family") or ""))
            # ★ 系统里没有的字体名会让 DirectWrite 反复创建字体面失败
            #   （界面发涩、像卡住），这里直接不写。
            if fam and not _th.valid_font(fam):
                result.bad_font = fam
                fam = ""
            else:
                result.bad_font = None
            return {
                # 没动过字体下拉框就保持原值（空 = 系统默认）
                "font_family": fam,
                "font_size": sp_fs.value(),
                "fg": fg2, "bg": bg,
                "accent": cur["accent"],
                "bg_image": ed_img.text().strip(),
                "bg_mode": cb_mode.currentData() or "stretch",
                "bg_opacity": sl.value(),
                "font_bold": 1 if cb_bold.isChecked() else 0,
                # ---- 选中与悬停效果 ----
                "pcl": 1 if cb_pcl.isChecked() else 0,
                "tab_idle": (cb_tab_idle.currentData()
                             if cb_tab_idle.currentData() != "fg"
                             else (cur.get("fg") or "#ffffff")),
                "tab_sel_box": 1 if cb_tab_box.isChecked() else 0,
                "item_idle_mode": cb_idle.currentData() or "dark",
                "sel_bars": cb_bars.currentData() or 0,
                "hover_tip": 1 if cb_tip.isChecked() else 0,
            }
        result.adjusted = None
        result.bad_font = None
        w.result = result
        return w

    def _style_dlg(self, dlg):
        """给对话框套当前主题。

        ★ 为什么要显式套一层？
          1) app 级样式只在启动时设过一次，改完主题后
             新弹出来的窗口还是旧配色（甚至系统默认色）。
          2) Qt 的样式表类型选择器（QDialog{...}）对
             Python 里自定义的子类未必匹配得上，
             显式 setStyleSheet 是唯一稳的做法。
        """
        try:
            from .core import theme as _th
            _th.load()
            dlg.setStyleSheet(_th.stylesheet(None))
            # ★ 再兜一层调色板：Windows 的系统调色板 Window 是纯白，
            #   stylesheet 没覆盖到的容器会露出白底 + 白字。
            _th.apply_palette(dlg)
        except Exception:
            pass
        return dlg

    def _apply_theme(self):
        """把主题套到整个程序上。"""
        from .core import theme as _th
        _th.load()
        # ★ 字体走 QApplication.setFont —— stylesheet 里的
        #   font-family 对不少控件不生效，这一条一定生效。
        # ★ 顺序很重要：先定 QApplication 字体，再套 stylesheet。
        #   反过来（先 stylesheet 后 apply_font）时，取消粗体会失效：
        #   setStyleSheet 那一刻 app.font() 还是上一次的粗体，Qt 会按
        #   这个旧字体重新 polish，把粗体“钉”到控件上；之后再
        #   app.setFont(非粗体) 也改不动这些已被样式表覆盖的控件 ——
        #   表现就是“取消勾选粗体没反应”，要再点一次 OK 才恢复。
        _th.apply_font()
        css = _th.stylesheet("QMainWindow")
        self.setStyleSheet(css)
        # 同时刷新 app 级样式，让【之后】新建的窗口也用新主题
        try:
            QApplication.instance().setStyleSheet(_th.stylesheet(None))
        except Exception:
            pass
        _th.apply_palette(self)
        for w in QApplication.topLevelWidgets():
            if isinstance(w, QDialog) and w is not self:
                try:
                    w.setStyleSheet(_th.stylesheet(None))
                except Exception:
                    pass
        # ★ 顶栏按钮是自绘的（悬停/按下动画），换主题后要重画
        for _b in self.findChildren(NavButton):
            try:
                _b.update()
            except Exception:
                pass
        # ★ 强制重画图标/表格。
        #   图标卡片上的文字（粗体）是委托自绘的，
        #   不主动 update 的话可能一直停在旧样子 ——
        #   表现就是"取消勾选粗体后没变化"。
        
        # ★ 清空委托里的主题缓存，让竖杠、颜色等立刻生效（不用切页面）
        try:
            if hasattr(self, "icon_delegate"):
                self.icon_delegate._th_cache["t"] = 0
            if hasattr(self, "row_delegate"):
                self.row_delegate._th_cache["t"] = 0
        except Exception:
            pass
            
        for _v in (getattr(self, "icon_view", None),
                   getattr(self, "table", None)):
            try:
                if _v is not None:
                    _v.viewport().update()
            except Exception:
                pass

    def do_display(self):
        # ★ 去掉以前那个保底的 dlg = QDialog(self) ——
        #   它从来没被 exec/show 过，也不传给任何控件，纯泄漏。
        #   真正显示的是下面的 dlg2。
        v = QWidget()
        vv = QVBoxLayout(v)

        # ---- 视图模式 ----
        gv = QGroupBox("视图模式")
        fv = QFormLayout(gv)
        cb_view = QComboBox()
        cb_view.addItem("图标视图（一个个方块，可拖动排序）", "icon")
        cb_view.addItem("表格视图（原来的列表）", "table")
        iv = cb_view.findData(self.disp.get("view", "icon"))
        cb_view.setCurrentIndex(iv if iv >= 0 else 0)
        fv.addRow("", cb_view)
        vv.addWidget(gv)

        # ---- 图标上显示什么 ----
        gs = QGroupBox("图标上显示哪些信息（勾选的才显示）")
        vs = QVBoxLayout(gs)
        default_show = ["name", "version", "loader", "status"]
        cur_show = self.disp.get("show") or default_show
        SHOW_ITEMS = [
            ("name", "服务器名称", "文件夹/世界名，建议一直开着"),
            ("version", "MC 版本", "如 1.21.10"),
            ("loader", "加载器", "Fabric / Forge / Paper ..."),
            ("world", "存档名", "世界里那个名字"),
            ("regions", "Region 数", "地图大小"),
            ("status", "状态", "运行中 / 未初始化"),
        ]
        show_boxes = {}
        for key, label, desc in SHOW_ITEMS:
            cb = QCheckBox(label)
            cb.setChecked(key in cur_show)
            cb.setToolTip(desc)
            show_boxes[key] = cb
            vs.addWidget(cb)
        # 名称关掉的话图标就认不出了，给个提示
        lbl_warn = QLabel("")
        lbl_warn.setStyleSheet("color:#c62828;")
        lbl_warn.setWordWrap(True)
        vs.addWidget(lbl_warn)

        def _chk():
            lbl_warn.setText(
                "⚠ 全都不勾的话图标上什么都没有，认不出是哪个服。"
                if not any(c.isChecked() for c in show_boxes.values())
                else "")
        for cb in show_boxes.values():
            cb.stateChanged.connect(_chk)
        _chk()
        vv.addWidget(gs)

        f = QFormLayout()
        sp_font = QSpinBox()
        sp_font.setRange(8, 28)
        sp_font.setValue(int(self.disp.get("font", 13)))
        sp_font.setSuffix(" pt")
        f.addRow("列表字号:", sp_font)
        sz = QSpinBox()
        sz.setRange(70, 220)
        sz.setValue(int(self.disp.get("icon", 110)))
        sz.setSuffix(" px")
        f.addRow("图标大小:", sz)
        sp_row = QSpinBox()
        sp_row.setRange(20, 64)
        sp_row.setValue(int(self.disp.get("row", 30)))
        sp_row.setSuffix(" px")
        f.addRow("表格行高:", sp_row)
        vv.addLayout(f)          # ★ v 是 QWidget，不是布局 —— 以前写成
                                 #   v.addLayout 一点『显示设置』就崩

        vv.addWidget(QLabel(
            "提示：排序在列表上方的下拉框里选；"
            "选『自定义顺序』后可以直接拖动图标调整。"))
        vv.addStretch(1)

        # ---- 外观页签 ----
        tabs = QTabWidget()
        tabs.addTab(self._wrap(v), "列表")
        ap = self._appearance_tab()
        tabs.addTab(ap, "外观 / 主题")

        # ★ V1.1.0：控制台配色页签
        cc = self._console_colors_tab()
        tabs.addTab(cc["widget"], "控制台配色")

        vb = QVBoxLayout()
        vb.addWidget(tabs, 1)
        btns = QDialogButtonBox(
            QDialogButtonBox.Ok | QDialogButtonBox.Cancel)
        vb.addWidget(btns)

        dlg2 = QDialog(self)
        dlg2.setWindowTitle("显示设置")
        dlg2.resize(520, 620)
        dlg2.setLayout(vb)
        # ★ 以前把 accepted/rejected 连到了上面那个"保底"的 dlg，
        #   而 exec() 的是 dlg2 —— 于是点 OK / Cancel 都等于
        #   accept 了一个从没显示过的窗口，dlg2.exec() 永远返回
        #   Rejected，所有设置都保存不了。必须连到 dlg2。
        btns.accepted.connect(dlg2.accept)
        btns.rejected.connect(dlg2.reject)

        # ★ V1.6.3：非模态打开 —— 主界面不再被"显示设置"死锁
        dlg2.setWindowModality(Qt.NonModal)

        def _apply_display_ok():
            self.disp["font"] = sp_font.value()
            self.disp["row"] = sp_row.value()
            self.disp["icon"] = sz.value()
            self.disp["view"] = cb_view.currentData() or "icon"
            self.disp["show"] = [k for k, cb in show_boxes.items()
                                 if cb.isChecked()]
            # ★ V1.1.0：保存控制台配色
            self.disp["console_colors"] = cc["result"]()
            self.data["display"] = self.disp
            # ★ 主题保存失败不能连累其它设置 —— 以前这里 theme 未导入，
            #   一保存就 NameError，导致字体/图标大小/视图全都存不下来。
            # ★ 保存失败必须让用户知道。
            #   以前 theme.save 静默吞错 + 这里不看返回值：
            #   界面照样弹"已应用"，但磁盘上没写进去，
            #   重启后全部回到旧值 —— 就是"改了没用"。
            try:
                ok_t, err_t = theme.save(ap.result())
            except Exception as e:
                ok_t, err_t = (False, repr(e))
            if not ok_t:
                print("[主题] 保存失败: %s" % err_t)
                QMessageBox.warning(
                    self, "主题没保存上",
                    "外观设置没能写入文件，重启后会回到旧的样子。\n\n"
                    "原因：%s\n\n"
                    "其它设置（字体大小、图标、显示项）不受影响。"
                    % err_t)
            # 对比度/字体无效的提示要放在这里，不能闷声改掉
            adj = getattr(ap.result, "adjusted", None)
            badf = getattr(ap.result, "bad_font", None)
            if adj or badf:
                msg = []
                if adj:
                    msg.append(
                        "字体色 %s 和背景色太接近（看不清），"
                        "已自动调成 %s" % (adj[0], adj[1]))
                if badf:
                    msg.append("字体 %s 本机没有，已忽略" % badf)
                QMessageBox.information(self, "已自动调整", "\n".join(msg))
            self._save_data()
            self._apply_theme()
            self._switch_view(self.disp["view"])
            self._apply_display()
            self._fill_views()
            # ★ V1.1.0：把新配色广播给所有控制台（含每服控制台）
            try:
                cc_colors = self.disp.get("console_colors") or None
                for _c in [self.console] + list(self.consoles.values()):
                    try:
                        _c.set_colors(cc_colors)
                    except Exception:
                        pass
            except Exception:
                pass
            from .core import theme as _t2
            _t2.load()
            self.statusBar().showMessage(
                "已应用 — 字体 %s / %s，前景 %s，背景 %s，强调 %s"
                % (_t2.get("font_family") or "系统默认",
                   ("%dpt" % int(_t2.get("font_size") or 0))
                   if int(_t2.get("font_size") or 0) > 0 else "默认字号",
                   _t2.get("fg"), _t2.get("bg"), _t2.get("accent")),
                8000)
            print("[主题] 字体=%r 字号=%s 前景=%s 背景=%s 强调=%s"
                  % (_t2.get("font_family"), _t2.get("font_size"),
                     _t2.get("fg"), _t2.get("bg"), _t2.get("accent")))
            print("[主题] 配置文件: %s" % _t2.path())

        dlg2.accepted.connect(_apply_display_ok)
        # ★ V1.6.3：持有引用防 GC 回收（非模态窗口没人持有会被回收）
        self._dlg_refs = getattr(self, "_dlg_refs", [])
        self._dlg_refs.append(dlg2)

        def _drop_display(_r=0):
            try:
                self._dlg_refs.remove(dlg2)
            except ValueError:
                pass

        dlg2.finished.connect(_drop_display)
        dlg2.show()

    # ---------------- 每服配置 ----------------
    def _saved(self, e):
        return self.data.get("servers", {}).get(self._key(e), {})

    def do_edit_icon(self):
        """给单个服务器换图标（右键『设置服务器图标』）。

        图片会复制一份进服务器目录，存成 manager_icon.png：
          · 不叫 server-icon.png，避免把服务器在游戏里的图标也改了
          · 复制而不是只记路径 —— 原图删了/移动了图标不会失效
        """
        e = self._cur_entry()
        if not e:
            QMessageBox.information(self, "提示", "先选中一个服务器")
            return
        from PySide6.QtWidgets import QFileDialog
        p, _ = QFileDialog.getOpenFileName(
            self, "选一张图片做图标", "",
            "图片 (*.png *.jpg *.jpeg *.bmp *.gif *.webp *.ico)")
        if not p:
            return
        from PySide6.QtGui import QPixmap
        pm = QPixmap(p)
        if pm.isNull():
            QMessageBox.warning(self, "失败", "这个文件不是能识别的图片")
            return
        base = e.get("server_dir") or e.get("folder") or ""
        if not base or not os.path.isdir(base):
            QMessageBox.warning(self, "失败", "服务器目录不存在")
            return
        dst = os.path.join(base, "manager_icon.png")
        try:
            pm.scaled(128, 128, Qt.KeepAspectRatio,
                      Qt.SmoothTransformation).save(dst, "PNG")
        except Exception as ex:
            QMessageBox.warning(self, "失败", "保存图标失败: %r" % ex)
            return
        k = self._key(e)
        self.data.setdefault("servers", {}).setdefault(k, {})["icon"] = dst
        self._save_data()
        self._fill_views()
        self.statusBar().showMessage(
            "已设置 %s 的图标" % (e.get("name") or ""))

    def do_rename_server(self):
        """右键重命名：给服务器起个好记的名字（不碰文件夹名）。

        存进 servers.json 的 display_name；图标视图和表格视图
        扫描后都会用这个字段覆盖显示（见 _display_name）。
        """
        e = self._cur_entry()
        if not e:
            QMessageBox.information(self, "提示", "先选中一个服务器")
            return
        old = e.get("display_name") or e.get("name") or ""
        new, ok = QInputDialog.getText(
            self, "重命名服务器",
            "给这个服务器起个好记的名字（不会改文件夹名）：",
            text=old)
        if not ok:
            return
        new = (new or "").strip()
        if not new:
            QMessageBox.warning(self, "名字不能为空", "请输入一个名字。")
            return
        e["display_name"] = new
        k = self._key(e)
        self.data.setdefault("servers", {}).setdefault(k, {})[
            "display_name"] = new
        self._save_data()
        self._fill_views()
        self.statusBar().showMessage("已重命名为：%s" % new, 4000)

    def do_clear_icon(self):
        """清掉自定义图标，回到自动识别（存档 icon.png 或首字母块）。"""
        e = self._cur_entry()
        if not e:
            QMessageBox.information(self, "提示", "先选中一个服务器")
            return
        k = self._key(e)
        sv = self.data.get("servers", {}).get(k)
        if not sv or not sv.get("icon"):
            QMessageBox.information(self, "提示", "这个服务器没有自定义图标")
            return
        sv.pop("icon", None)
        try:
            old = sv.get("_icon_path")
        except Exception:
            old = None
        self._save_data()
        self._fill_views()
        self.statusBar().showMessage("已清除自定义图标，恢复自动识别")

    # ★ V1.6.0：启动流程提前中止时恢复按钮/状态栏
    def _restore_start_btn(self, e=None):
        try:
            self.act_start.setEnabled(True)
        except Exception:
            pass
        if e is not None:
            self.statusBar().showMessage("就绪", 3000)

    # ★ V1.6.0：双击卡片/行 = 未运行就启动，运行中就停止
    #   （服务器配置改从右键菜单进）
    def _double_click_icon(self, item):
        key = (item.data(Qt.UserRole) or "") if item is not None else ""
        for en in self.entries:
            if self._key(en) == key:
                self._toggle_run(en)
                return

    def _double_click_row(self, idx):
        try:
            row = idx.row()
            en = self._sorted_entries()[row]
        except Exception:
            return
        self._toggle_run(en)

    def _toggle_run(self, e):
        k = self._key(e)
        if k in self.procs:
            self.do_stop()
        else:
            self.do_start()

    # ★ V1.6.0：崩溃/启动失败后的非模态智能引导
    def _ask_crash_diag(self, key=None):
        """弹非模态 QMessageBox：要不要打开『日志分析』找原因。"""
        try:
            en = None
            if key is not None:
                for x in self.entries:
                    if self._key(x) == key:
                        en = x
                        break
            box = QMessageBox(self)
            box.setIcon(QMessageBox.Warning)
            box.setWindowTitle("服务器异常")
            box.setText("检测到服务器异常退出，是否为您自动打开"
                        "『日志分析』寻找原因？")
            yes = box.addButton("是，打开日志分析", QMessageBox.AcceptRole)
            no = box.addButton("否", QMessageBox.RejectRole)
            box.setAttribute(Qt.WA_DeleteOnClose)

            def _on(btn):
                try:
                    if btn is yes:
                        self.do_analyze(en)
                finally:
                    try:
                        box.close()
                    except Exception:
                        pass

            box.buttonClicked.connect(_on)
            box.show()  # 非模态：不阻塞主界面，其它服务器还能操作
        except Exception:
            pass

    def do_config(self):
        e = self._cur()
        if not e:
            QMessageBox.information(self, "提示", "先选中一个服务器")
            return
        # ★ V1.6.2：外层 try/except —— 防止后台静默崩溃导致窗体出不来
        try:
            saved = self._saved(e)
            dlg = self._style_dlg(
                ServerConfigDialog(e, saved=saved, parent=self))
        except Exception as ex:
            import traceback
            traceback.print_exc()
            QMessageBox.critical(
                self, "打开服务端配置失败",
                "程序在创建配置窗口时遇到了错误：\n\n%s\n\n"
                "详细追踪：\n%s" % (ex, traceback.format_exc()[-500:]))
            return

        # ★ V1.6.2：非模态打开 —— 用户改配置时主界面仍可操作
        dlg.setWindowModality(Qt.NonModal)
        self._dlg_refs = getattr(self, "_dlg_refs", [])
        self._dlg_refs.append(dlg)

        def _on_config_done():
            k = self._key(e)
            # ★ Bug 3 修复：merge，不再整条覆盖（保留 icon、last_start 等）
            sv = self.data.setdefault("servers", {}).setdefault(k, {})
            sv.update(dlg.result_info)
            self._save_data()
            # ★ V1.7.0-2：保存后刷新并按 server_dir 精准回显选中，
            #   绝不再按行号 setCurrentRow（排序变化会导致选中串台）
            self.do_scan(then=lambda: self._select_entry_by_dir(e))
            self.statusBar().showMessage("已保存 %s" % e.get("name"))
            try:
                self._dlg_refs.remove(dlg)
            except ValueError:
                pass

        dlg.finished.connect(lambda _r: _on_config_done())
        dlg.show()

    def _select_entry_by_dir(self, e):
        """★ V1.7.0-2：按服务器绝对路径在刷新后的列表里精准定位并选中。

        兼容图标视图和表格视图；找不到就保持现状（不强制跳到第一行）。
        """
        try:
            target = os.path.abspath(
                e.get("server_dir") or e.get("folder") or "")
            for i, en in enumerate(self._sorted_entries()):
                if os.path.abspath(en.get("folder") or "") == target:
                    self.table.selectRow(i)
                    if self.view_mode == "icon" \
                            and self.icon_view.count() > i:
                        self.icon_view.setCurrentRow(i)
                    self.on_sel()
                    return
        except Exception:
            pass


    # ---------------- 备份 ----------------
    def do_stop_backup(self, k=None, e=None, proc=None):
        """停止服务器，等它退出后自动备份存档。

        ★ V1.1.0：支持从 do_stop 传入 (k, e, proc)，
          不传则按原来的方式取当前选中服务器。
        """
        if e is None:
            e = self._cur()
        if not e:
            return
        if k is None:
            k = self._key(e)
        if proc is None:
            proc = self.procs.get(k)
        if not proc:
            QMessageBox.information(self, "提示", "这个服没在运行")
            return
        
        # ★ 标记为主动停止，防止退出时触发自动重启
        self._is_stopping[k] = True
        # ★ 第二处修改：按服的 key 记录备份任务
        self.pending_backups[k] = e
        
        self._log_console("=== 正在停止，停止后将自动备份 ===")
        try:
            proc.write(b"stop\n")
        except Exception:
            pass

        def safe_kill():
            try:
                # ★ 多开修复：进程已经在超时前正常退出了（对象被销毁/
                #   已 NotRunning），就别再 9 秒后强杀一次，否则会报错
                if proc.state() == QProcess.NotRunning:
                    return
                self._kill(proc)
            except RuntimeError:
                pass  # 对象已销毁，安全忽略
        QTimer.singleShot(9000, safe_kill)

    def _after_stop_backup(self, e):
        """服务器退出后执行备份（放入后台线程，防止卡死UI）。"""
        sd = e.get("server_dir") or e["folder"]
        worlds = backup.find_worlds(sd)
        if not worlds:
            self._log_console("[备份] 没找到存档，跳过")
            return
        wd = worlds[0]
        self._log_console("[备份] 开始后台备份存档: %s，请稍候..." % wd["rel"])
        # ★ V1.1.0：备份格式跟随服务器配置
        sv = self._saved(e)
        compress = (sv.get("backup_format", "folder") == "zip")
        
        # 把备份操作放到后台 Worker 线程
        def job(on_log=None):
            return backup.backup_world(wd["path"], sd,
                                       compress=compress, on_log=on_log)

        w = _track(self, Worker(job, {}, self))
        w.log.connect(lambda m: self._log_console("[备份] " + m))
        
        # 备份完成后的回调
        def on_done(ok, res):
            if not ok:
                self._log_console("[备份] 失败: %r" % res)
                self._log_console("[备份] 备份未完成 —— 但服务器已经关闭。")
                self.statusBar().showMessage("备份失败（服务器已关闭）", 6000)
                return
            
            r = res
            self._log_console("[备份] 完成: %s (%.1f MB)" % (os.path.basename(r["path"]), r["size_mb"]))
            self._log_console("[备份] 备份已完成。")
            self._log_console("=== 服务器已关闭 ===")
            self.statusBar().showMessage("备份已完成，服务器已关闭", 6000)
            
        def on_done_and_cleanup(ok, res):
            self._forget_worker(w)  # ★ 清理线程对象
            on_done(ok, res)
            
        w.done.connect(on_done_and_cleanup)
        w.start()

    def _do_auto_backup(self, k, e):
        """定时备份的执行逻辑。"""
        if k not in self.procs:
            return
        sd = e.get("server_dir") or e["folder"]
        worlds = backup.find_worlds(sd)
        if not worlds:
            return
        wd = worlds[0]
        self._log_console(f"[备份] 触发定时备份: {wd['rel']} ...")
        
        # 把备份放到后台，且使用 5 个循环覆盖的命名规则以防占满空间
        def job(on_log=None):
            return backup.backup_world(wd["path"], sd, note="auto", 
                                       compress=True, on_log=on_log)
                                       
        w = _track(self, Worker(job, {}, self))
        w.log.connect(lambda m: self._log_console("[备份] " + m))
        
        def _on_auto_bk_done(ok, res):
            self._forget_worker(w)  # ★ 清理线程对象，防止长年挂机内存泄漏
            self._log_console(
                f"[备份] 定时备份完成" if ok else f"[备份] 定时备份失败: {res}")
                
        w.done.connect(_on_auto_bk_done)
        w.start()

    def _try_restart(self, key, entry):
        """10 秒后尝试自动重启。

        ★ 不碰 UI：以前为了"选中正确行"会强制 selectRow，
          用户正在操作 B 服时 A 崩溃 10 秒后自动重启，
          会把焦点从 B 抢到 A。现在直接把 entry 传给 do_start。
        """
        if key in self.procs:
            return  # 已经有人手动开了
        self._log_console(f"=== 正在自动重启 {entry.get('name')} ===")
        self.do_start(auto_restart=True, entry=entry)

    def _log_console(self, msg):
        self.console.append(msg)

    # ---------------- 导入 ----------------
    # ---------------- 拖放导入 ----------------
    def dragEnterEvent(self, e):
        """外部拖东西进来：文件夹 / zip 就接，其它不接。"""
        try:
            if e.mimeData().hasUrls():
                e.acceptProposedAction()
                return
        except Exception:
            pass
        return super().dragEnterEvent(e)

    def dragMoveEvent(self, e):
        try:
            if e.mimeData().hasUrls():
                e.acceptProposedAction()
                return
        except Exception:
            pass
        return super().dragMoveEvent(e)

    def dropEvent(self, e):
        """把拖进来的地图文件夹 / 压缩包交给导入。"""
        try:
            urls = e.mimeData().urls()
        except Exception:
            return super().dropEvent(e)
        paths = [u.toLocalFile() for u in urls if u.isLocalFile()]
        paths = [p for p in paths if p]
        if not paths:
            return super().dropEvent(e)
        e.acceptProposedAction()
        self._import_dropped(paths)

    def _import_dropped(self, paths):
        """处理拖进来的东西（统一走 migrate.plan_import，避免结构错乱）。

        ★ V0.4.0：zip 会先检测是不是整合包（Modrinth/CurseForge），
          是就走整合包导入；剩下的才当地图导入。
        """
        if not self.root:
            QMessageBox.information(self, "提示", "请先选择总目录")
            return
        root = self.root
        self.statusBar().showMessage("导入中 ...")

        # 整合包分流（本地 zip 读取，很快）
        rest = []
        for p in paths:
            if os.path.isfile(p) and p.lower().endswith(".zip"):
                try:
                    from .core import modpack as _mp
                    if _mp.detect_kind(p):
                        self._import_modpack_path(p)
                        continue
                except Exception:
                    pass
            rest.append(p)
        if not rest:
            return
        paths = rest

        def job(on_log=None):
            import tempfile
            ok_n = 0
            msgs = []
            for p in paths:
                base = os.path.basename(os.path.normpath(p)) or p
                src_dir = p
                
                # 如果是 zip，先解压到临时目录（★ V1.4.0：用系统 tar 提速）
                is_tmp = False
                if os.path.isfile(p) and p.lower().endswith(".zip"):
                    try:
                        src_dir = tempfile.mkdtemp(prefix="mcs_import_")
                        is_tmp = True
                        from .core import modpack as _mp
                        _mp._fast_extract(p, src_dir)
                    except Exception as ex:
                        msgs.append("[失败] %s（解压失败：%s）" % (base, str(ex)[:40]))
                        continue
                elif not os.path.isdir(p):
                    msgs.append("[跳过] %s（不是文件夹也不是 zip）" % base)
                    continue
                
                try:
                    plan = migrate.plan_import(src_dir, root)
                    if plan["kind"] == "none":
                        msgs.append("[跳过] %s（没找到 level.dat）" % base)
                        continue
                    if os.path.exists(plan["server_dir"]):
                        msgs.append("[跳过] %s（目标已存在）" % plan["server_name"])
                        continue
                    
                    ok, msg = migrate.do_import(plan, copy=True, on_log=on_log)
                    if ok:
                        ok_n += 1
                        msgs.append("[OK] %s -> %s" % (base, plan["server_name"]))
                    else:
                        msgs.append("[失败] %s（%s）" % (base, msg))
                finally:
                    if is_tmp:
                        import shutil
                        shutil.rmtree(src_dir, ignore_errors=True)
            return ok_n, msgs

        w = _track(self, Worker(job, {}, self))
        self._import_worker = w
        w.log.connect(self._log_console)
        w.done.connect(self._after_drop_import)
        w.start()

    # (废弃的 _import_one 已删除)

    def _after_drop_import(self, ok, res):
        w, self._import_worker = getattr(self, "_import_worker", None), None
        if w is not None:
            self._forget_worker(w)
        if not ok:
            self.statusBar().showMessage("导入失败")
            QMessageBox.warning(self, "拖放导入", "导入失败：%s" % res)
            return
        ok_n, msgs = res
        txt = "\n".join(msgs)
        self._log_console("=== 拖放导入 ===\n" + txt)
        
        # 不管成不成功，只要有提示信息就刷新一下面板
        if ok_n:
            self.do_scan()
        else:
            QMessageBox.warning(self, "拖放导入失败", "没有成功导入任何地图，请检查：\n" + txt)
        QMessageBox.information(
            self, "拖放导入",
            txt + ("\n\n已导入 %d 个。" % ok_n if ok_n else
                   "\n\n没导入成功。可以改用工具栏『导入地图』。"))

    def do_import(self):
        """导入地图：建「版本-地图名」文件夹，地图放进去。

        目标结构：
            总目录/1.21.3-Frozen-Crag/
                ├── Frozen-Crag/     <- 世界内容
                └── (核心稍后装在这层)
        """
        if not self.root:
            QMessageBox.information(self, "提示", "请先选择总目录")
            return
        d = QFileDialog.getExistingDirectory(
            self, "选择要导入的地图文件夹", self.root)
        if not d:
            return

        name = os.path.basename(d.rstrip("\\/"))
        try:
            plan = migrate.plan_import(d, self.root)
        except Exception as ex:
            QMessageBox.critical(self, "识别失败", repr(ex))
            return

        if plan["kind"] == "none":
            QMessageBox.warning(
                self, "不像 MC 世界",
                "在这个文件夹里没找到 level.dat：\n  %s\n\n"
                "请选包含 level.dat 的世界文件夹，"
                "或包含它的父文件夹。" % d)
            return

        if os.path.exists(plan["server_dir"]):
            QMessageBox.warning(
                self, "已存在",
                "总目录下已经有：\n  %s" % plan["server_name"])
            return

        r = QMessageBox.question(
            self, "确认导入",
            "源文件夹: %s\n\n"
            "识别结果：\n"
            "  版本: %s\n"
            "  地图: %s\n"
            "  世界: %s 个 region\n\n"
            "将创建：\n"
            "  %s/\n"
            "    └── %s/   <- 地图内容放这里\n\n"
            "（核心和配置文件之后会装在外层，"
            "不再和地图文件混在一起）\n\n继续？"
            % (name,
               plan["version"] or "未知",
               plan["world_name"] or "-",
               self._region_count(plan["world_src"]),
               plan["server_name"], plan["world_subdir"]))
        if r != QMessageBox.Yes:
            return

        # ★ 复制大世界可能要几十秒，放后台线程，别冻住界面
        self.statusBar().showMessage("导入中 ...")

        def job(on_log=None):
            return migrate.do_import(plan, copy=True, on_log=on_log)

        w = _track(self, Worker(job, {}, self))
        self._import_worker = w
        w.log.connect(self._log_console)
        w.done.connect(lambda ok, res: self._after_import(ok, res, plan))
        w.start()

    def _after_import(self, ok, res, plan):
        w, self._import_worker = getattr(self, "_import_worker", None), None
        if w is not None:
            self._forget_worker(w)
        if not ok:
            self.statusBar().showMessage("导入失败")
            QMessageBox.critical(self, "失败", str(res))
            return
        okk, msg = res
        if not okk:
            self.statusBar().showMessage("导入失败")
            QMessageBox.warning(self, "导入失败", msg)
            return
        self.statusBar().showMessage("导入完成")

        def select_new():
            # 选中新导入的那个（兼容图标视图和表格视图）
            # ★ 必须按视图实际顺序（_sorted_entries）定位行，
            #   否则排序不是"自定义顺序"时 self.entries 的索引
            #   和视图行号对不上，会选中错的服务器。
            target = os.path.abspath(plan["server_dir"])
            for i, e in enumerate(self._sorted_entries()):
                if os.path.abspath(e.get("folder") or "") == target:
                    self.table.selectRow(i)
                    if self.view_mode == "icon" and self.icon_view.count() > i:
                        self.icon_view.setCurrentRow(i)
                    # ★ 强制刷新右侧信息面板，防止导入完了但面板不更新
                    self.on_sel()
                    break
            QMessageBox.information(
                self, "完成",
                "已导入为：%s\n\n"
                "结构：\n"
                "  %s/\n"
                "    └── %s/\n\n"
                "接下来选中它，点『初始化服务端』装核心。\n"
                "核心会装在外层，level-name 会自动指向 %s。"
                % (plan["server_name"], plan["server_name"],
                   plan["world_subdir"], plan["world_subdir"]))

    # ---------------- V0.4.0：整合包一键导入 ----------------
    def do_import_modpack(self):
        """选一个整合包 zip（Modrinth / CurseForge）导入。"""
        if not self.root:
            QMessageBox.information(self, "提示", "请先选择总目录")
            return
        path, _ = QFileDialog.getOpenFileName(
            self, "选择整合包 zip",
            "", "整合包 (*.zip);;所有文件 (*)")
        if not path:
            return
        self._import_modpack_path(path)

    def _import_modpack_path(self, path):
        """把整合包 zip 交给后台 Worker 导入（网络请求不进主线程）。"""
        root = self.root
        self.statusBar().showMessage("正在导入整合包 ...")
        self._forget_worker(getattr(self, "_modpack_worker", None))

        def job(on_log=None):
            from .core import modpack as _mp
            return _mp.install_modpack(path, root, on_log=on_log)

        w = _track(self, Worker(job, {}, self))
        self._modpack_worker = w
        w.log.connect(lambda m: self.statusBar().showMessage(str(m)))
        w.done.connect(self._modpack_done)
        w.start()

    def _modpack_done(self, ok, res):
        w, self._modpack_worker = (
            getattr(self, "_modpack_worker", None), None)
        if w is not None:
            self._forget_worker(w)
        if not ok:
            self.statusBar().showMessage("整合包导入失败")
            QMessageBox.critical(self, "导入整合包失败",
                                 "导入失败：\n%s" % str(res)[:300])
            return
        sd = res.get("server_dir") or ""
        fail = res.get("mods_fail") or []
        ok_n = len(res.get("mods_ok") or [])
        msg = ("整合包已导入：%s\n\n"
               "目录: %s\n"
               "MC %s · %s\n"
               "模组：成功 %d 个" % (
                   res.get("name"), sd,
                   res.get("mc_version"), res.get("loader"), ok_n))
        if fail:
            msg += "\n\n失败 %d 个：\n%s" % (
                len(fail), "\n".join("· %s" % f for f in fail[:15]))
        msg += "\n\n接下来选中它，点『初始化服务端』完善配置，然后就能启动。"
        QMessageBox.information(self, "整合包导入完成", msg)
        self.statusBar().showMessage("整合包导入完成")

        target = os.path.abspath(sd)

        def select_me():
            for i, e in enumerate(self._sorted_entries()):
                if os.path.abspath(e.get("folder") or "") == target:
                    self.table.selectRow(i)
                    if self.view_mode == "icon" \
                            and self.icon_view.count() > i:
                        self.icon_view.setCurrentRow(i)
                    self.on_sel()
                    break

        self.do_scan(then=select_me)

        self.do_scan(then=select_new)

    def _region_count(self, world_dir):
        if not world_dir or not os.path.isdir(world_dir):
            return "-"
        n = 0
        for base in ("region",
                     os.path.join("dimensions", "minecraft",
                                  "overworld", "region")):
            rd = os.path.join(world_dir, base)
            if os.path.isdir(rd):
                try:
                    n += len([f for f in os.listdir(rd)
                              if f.endswith(".mca")])
                except Exception:
                    pass
        if n == 0:
            # 深一层找
            try:
                for root, dirs, files in os.walk(world_dir):
                    if os.path.basename(root) == "region":
                        n += len([f for f in files
                                  if f.endswith(".mca")])
                    if root.count(os.sep) - world_dir.count(os.sep) > 3:
                        dirs[:] = []
            except Exception:
                pass
        return n


    # ---------------- 模组 ----------------
    def do_mods(self):
        e = self._cur()
        if not e:
            QMessageBox.information(self, "提示", "先选中一个服务器")
            return
        self._style_dlg(ModDialog(e, self)).exec()


    # ---------------- 核心 ----------------
    def _warn_bad_levelname(self, e):
        """level-name 含非法字符会导致服务端启动崩溃，提前预警。"""
        sd = e.get("server_dir") or e["folder"]
        p = os.path.join(sd, "server.properties")
        if not os.path.isfile(p):
            return
        try:
            d = {}
            for line in open(p, "r", encoding="utf-8", errors="replace"):
                line = line.strip()
                if "=" in line and not line.startswith("#"):
                    k, v = line.split("=", 1)
                    d[k.strip()] = v
        except Exception:
            return
        ln = d.get("level-name")
        if not ln:
            return
        okd, why = builder.validate_level_name(ln)
        if okd:
            return
        fixed = builder.safe_level_name(ln)
        self.console.append(
            "[警告] level-name 有问题：%s" % why)
        self.console.append(
            "        当前值: %s" % ln)
        r = QMessageBox.warning(
            self, "level-name 会导致启动崩溃",
            "server.properties 里的 level-name 有问题：\n\n"
            "  %s\n\n"
            "当前值：%s\n\n"
            "MC 会拿它拼文件夹路径，含 : 这类字符会直接崩。\n\n"
            "是否改成安全的名字？\n  %s"
            % (why, ln, fixed),
            QMessageBox.Yes | QMessageBox.No, QMessageBox.Yes)
        if r != QMessageBox.Yes:
            return
        try:
            lines = open(p, "r", encoding="utf-8",
                         errors="replace").read().splitlines()
            out = []
            for line in lines:
                if line.strip().startswith("level-name="):
                    out.append("level-name=%s" % fixed)
                else:
                    out.append(line)
            open(p, "w", encoding="utf-8", newline="\n").write(
                "\n".join(out) + "\n")
            self.console.append(
                "[OK] level-name 已改为: %s" % fixed)
            self.do_scan()
        except Exception as ex:
            self.console.append("[失败] 改写 level-name: %r" % ex)


    # ---------------- 诊断 / 删除 ----------------
    def do_analyze(self, entry=None):
        """离线日志诊断。

        ★ V1.6.0：可选 entry —— 崩溃智能引导时直接对准崩掉的那台服，
          不依赖用户当前选中。
        """
        e = entry if entry is not None else self._cur()
        if not e:
            QMessageBox.information(self, "提示", "先选中一个服务器")
            return
        # 把本软件这个服的控制台输出也一起喂进去
        txt = self._console_text(e)
        dlg = self._style_dlg(AnalyzeDialog(
            e, all_entries=self.entries, console_text=txt, parent=self))
        # ★ V1.6.3：非模态 —— 分析日志时主界面不再死锁
        dlg.setWindowModality(Qt.NonModal)
        self._dlg_refs = getattr(self, "_dlg_refs", [])
        self._dlg_refs.append(dlg)

        def _drop_analyze(_r=0):
            try:
                self._dlg_refs.remove(dlg)
            except ValueError:
                pass

        dlg.finished.connect(_drop_analyze)
        dlg.show()

    # ---------------- V1.6.0：检查更新 ----------------
    def do_check_update(self):
        """后台检查 GitHub 最新版（Worker 线程，不卡 UI）。"""
        if getattr(self, "_checking_update", False):
            return
        self._checking_update = True
        self.statusBar().showMessage("正在检查更新...", 0)
        w = _track(self, Worker(updater.check_latest, {}, self))
        w.done.connect(self._on_update_done)
        w.start()

    def _on_update_done(self, ok, res):
        self._checking_update = False
        self.statusBar().showMessage("就绪", 3000)
        if not ok:
            self.statusBar().showMessage(
                "检查更新失败（网络异常或未配置仓库）", 5000)
            return
        tag, url, is_new = res
        if not is_new or not tag:
            QMessageBox.information(self, "检查更新",
                                    "当前已是最新版本")
            return
        from PySide6.QtGui import QDesktopServices
        from PySide6.QtCore import QUrl
        box = QMessageBox(self)
        box.setIcon(QMessageBox.Information)
        box.setWindowTitle("发现新版本")
        box.setText("发现新版本 %s，是否前往下载？" % tag)
        dl = box.addButton("前往下载", QMessageBox.AcceptRole)
        box.addButton("取消", QMessageBox.RejectRole)

        def _go(btn):
            if btn is dl and url:
                QDesktopServices.openUrl(QUrl(url))

        box.buttonClicked.connect(_go)
        box.exec()

    def _console_text(self, e):
        """取某个服务器自己的控制台全文。

        ★ 以前 do_diag 直接 self.console.toPlainText() ——
          那个是第 0 页「总输出」，装的是本软件自己的日志，
          不是服务器日志。于是规则诊断一直在拿软件日志去匹配
          「Failed to load function」「Address already in use」
          这类服务端报错 —— 永远匹配不上，等于白喂。
          do_analyze 反而取对了（用 self.consoles[key]）。
          这里抽出来统一，两处都走同一条路。
        """
        try:
            c = self.consoles.get(self._key(e))
            if c is None:
                return ""
            w = getattr(c, "out", None) or getattr(c, "text", None)
            if w is not None and hasattr(w, "toPlainText"):
                return w.toPlainText() or ""
        except Exception:
            pass
        return ""

    def do_diag(self):
        e = self._cur()
        if not e:
            QMessageBox.information(self, "提示", "先选中一个服务器")
            return
        txt = self._console_text(e)
        self._style_dlg(
            DiagDialog(e, console_text=txt, parent=self)).exec()

    def do_delete_server(self):
        e = self._cur()
        if not e:
            QMessageBox.information(self, "提示", "先选中一个服务器")
            return
        k = self._key(e)
        if k in self.procs:
            QMessageBox.warning(self, "还在运行",
                                "请先停止这个服务器再删除")
            return
        dlg = self._style_dlg(DeleteServerDialog(e, self))
        if dlg.exec():
            # 从列表移除
            self.root = self.ed_root.text().strip()
            self.do_scan()
            self.statusBar().showMessage("服务器已删除")

    # ---------------- 数据 ----------------
    def _data_path(self):
        return os.path.join(os.path.dirname(os.path.abspath(__file__)),
                            "..", DATA_FILE)

    def _load_data(self):
        try:
            p = self._data_path()
            if os.path.isfile(p):
                self.data = json.load(open(p, "r", encoding="utf-8"))
                if "display" in self.data:
                    self.disp.update(self.data["display"])
                if "servers" not in self.data:
                    self.data["servers"] = {}
        except Exception:
            pass

    def _save_data(self):
        """保存 servers.json。返回 (ok, err)。

        ★ 以前这里 except: pass —— 全项目最不该静默的地方。
          servers.json 装的是服务器列表、自定义排序、显示设置、
          最近开服时间。写不进去（目录只读、文件被占用、盘满）
          界面却毫无反应，你会以为排好的顺序已经记住了，
          重启一看全没了。
          现在写失败会在状态栏红字提示，并弹一次窗说明原因。
        """
        p = self._data_path()
        try:
            d0 = os.path.dirname(p)
            if d0:
                os.makedirs(d0, exist_ok=True)
            with open(p, "w", encoding="utf-8") as f:
                json.dump(self.data, f, ensure_ascii=False, indent=2)
            if getattr(self, "_save_failed", False):
                self._save_failed = False
            return (True, p)
        except Exception as e:
            err = "%s  (%s)" % (repr(e), p)
            print("[数据] 保存失败: %s" % err)
            try:
                self.statusBar().showMessage(
                    "⚠ 设置没保存上（%s），重启后会丢失" % repr(e), 15000)
            except Exception:
                pass
            # 只弹一次：_save_data 调用点很多，每次都弹会疯
            if not getattr(self, "_save_failed", False):
                self._save_failed = True
                try:
                    QMessageBox.warning(
                        self, "设置没保存上",
                        "服务器列表设置没能写入文件，重启后会丢失"
                        "（比如刚排好的顺序、显示设置）。\n\n"
                        "原因：%s\n\n"
                        "常见情况：文件夹只读、文件被别的程序占用、磁盘已满。"
                        % err)
                except Exception:
                    pass
            return (False, err)

    # ---------------- 扫描 ----------------
    def pick_and_scan(self):
        d = QFileDialog.getExistingDirectory(self, "选择总目录",
                                             self.root or "")
        if not d:
            return
        self.root = d
        self.ed_root.setText(d)
        self.data["root"] = d
        self._save_data()
        self.do_scan()

    def do_scan(self, then=None, quiet=False):
        """扫描总目录。放在后台线程跑，几十个服也不卡界面。

        then:  扫描完成后的回调（导入/删除后要选中新条目时用）。
        quiet: 不弹框（启动时自动扫描用；总目录不在了也不该卡启动）。
        """
        if not self.root or not os.path.isdir(self.root):
            if quiet:
                self.statusBar().showMessage(
                    "总目录不存在或未选择，请点『扫描』重新选择")
            else:
                QMessageBox.information(self, "提示", "请先选择总目录")
            return
        old = getattr(self, "_scan_worker", None)
        if old is not None and old.isRunning():
            # 已经在扫了：不重复开线程；等这次结束后再按【最新的 root】扫一次
            self._scan_then = then
            self._scan_again = True
            return
        self._scan_then = then
        self._scan_again = False
        root = self.root
        self._scan_root_used = root
        self.statusBar().showMessage("扫描中...")

        def job(on_log=None):
            return scanner.scan_root(root)

        w = _track(self, Worker(job, {}, self))
        self._scan_worker = w
        w.done.connect(self._on_scan_done)
        w.start()

    def _forget_worker(self, w):
        """线程跑完后从 self._threads 里摘掉，别越攒越多。"""
        lst = getattr(self, "_threads", None) or []
        try:
            lst.remove(w)
        except ValueError:
            pass

    def _on_scan_done(self, ok, res):
        w, self._scan_worker = getattr(self, "_scan_worker", None), None
        if w is not None:
            self._forget_worker(w)
        # 扫描期间 root 变过 / 又点了扫描：拿旧结果没意义，按新 root 再扫一次
        used = getattr(self, "_scan_root_used", None)
        stale = bool(self._scan_again) or bool(
            used and self.root
            and os.path.abspath(used) != os.path.abspath(self.root))
        if stale:
            self._scan_again = False
            cb = getattr(self, "_scan_then", None)
            self.do_scan(then=cb)
            return
        if not ok:
            self.statusBar().showMessage("扫描失败：%s" % res)
            self._log_console("扫描失败：%s" % res)
            return
        self.entries = res or []
        # ★ 右键重命名支持：扫描后把 servers.json 里的 display_name
        #   回填到条目上，图标和表格都显示用户改的名字
        for en in self.entries:
            saved = self.data.get("servers", {}).get(self._key(en), {})
            if saved.get("display_name"):
                en["display_name"] = saved["display_name"]
        self._fill_views()
        self.statusBar().showMessage(
            "扫描完成，共 %d 个" % len(self.entries))
        if self.sort_mode == "size":
            self._prefetch_sizes()  # ★ 扫描完若在按大小排序，后台补算
        cb, self._scan_then = getattr(self, "_scan_then", None), None
        if cb:
            try:
                cb()
            except Exception:
                pass

    def _update_draghint(self):
        """排序栏右侧的一行小字，告诉用户现在能不能拖。"""
        try:
            if getattr(self, "sort_mode", "custom") == "custom":
                what = "图标" if getattr(self, "view_mode", "icon") == "icon" \
                    else "行"
                self.lb_draghint.setText(
                    "自定义顺序：直接拖动%s就能调整" % what)
            else:
                self.lb_draghint.setText("拖一下会自动切到自定义顺序")
        except Exception:
            pass

    def _fill_views(self):
        """同时刷新图标视图和表格视图。

        ★ 为什么要有这个函数：
          以前各处分别调 _fill_icons() / _fill_table()，
          漏掉其中一个就成了"扫完一片空白"。
          do_scan() 就只调了 _fill_table()，
          而默认视图是图标 -> 用户看到的就是一个空面板。
          现在统一走这里，不会再漏。
        """
        self._fill_icons()
        self._fill_table()
        self._update_draghint()
        # 兜底：万一图标视图一个都没画出来（委托异常/尺寸算错），
        # 自动退回表格视图 —— 宁可样式朴素，也不能给用户一个空面板。
        try:
            if (getattr(self, "view_mode", "table") == "icon"
                    and self.icon_view.count() == 0
                    and self.table.rowCount() > 0):
                self._switch_view("table")
                self.statusBar().showMessage(
                    "图标视图渲染异常，已自动退回表格视图")
        except Exception:
            pass

    def _fill_table(self):
        self.table.setRowCount(0)
        for i, e in enumerate(self._sorted_entries()):
            r = self.table.rowCount()
            self.table.insertRow(r)
            # ★ 一次性算好这一行的悬停注释，所有 6 个单元格共享同一个
            #   字符串。以前内层循环每格都调一次 _tip_for(e) ——
            #   一行 6 次，几十行就几百次重复拼接；碰上端口缓存过期
            #   还会连带 6 次读盘。现在每行只算一次。
            tip = self._tip_for(e)
            vals = [
                e.get("name", ""),
                e.get("version") or "?",
                versions.LOADER_LABEL.get(e.get("loader"),
                                          e.get("loader") or "-"),
                e.get("world_name") or "-",
                str(e.get("regions") or 0),
                "未初始化" if e.get("notes") else "就绪",
            ]
            for c, v in enumerate(vals):
                it = QTableWidgetItem(v)
                if c == 5 and v == "未初始化":
                    it.setForeground(QColor("#c62828"))
                if c == 0:
                    # 拖动排序后要按这个 key 存顺序
                    it.setData(Qt.UserRole, self._key(e))
                it.setToolTip(tip)   # 整行共享同一份，不再每格重算
                self.table.setItem(r, c, it)
            
            # ★ 把行高设置挪到列循环外面，这一行填完了统一设一次就行
            self.table.setRowHeight(r, int(self.disp.get("row", 30)))
        # 运行中的服要显示"运行中"（扫描不会清掉运行列表）
        self._refresh_status()
        self.table.selectRow(0)
        # 图标视图同步选中第一行
        try:
            if self.icon_view.count():
                self.icon_view.setCurrentRow(0)
        except Exception:
            pass

    def on_sel(self):
        """选中变了 -> 刷新右侧信息。

        ★ 以前的 bug：这里只读【表格】的 selectionModel，
          图标视图点一下 rows 是空的，直接 return，
          于是"选图标时右上角信息不跟着动"。
          现在按当前可见的视图取值。
        """
        e = None
        if self.view_mode == "icon":
            it = self.icon_view.currentItem()
            if it is not None:
                e = self._icon_entry(it)
            if e is None:
                for it2 in self.icon_view.selectedItems():
                    e = self._icon_entry(it2)
                    if e:
                        break
        else:
            rows = self.table.selectionModel().selectedRows()
            # ★ 同样按 _sorted_entries() 取，与 _fill_table 的填充顺序一致
            sorted_e = self._sorted_entries()
            if rows and rows[0].row() < len(sorted_e):
                e = sorted_e[rows[0].row()]
        if not e:
            return
        # 选中某个服时，自动切到它的控制台页签
        try:
            c = self.consoles.get(self._key(e))
            if c is not None:
                self.console_tabs.setCurrentIndex(
                    self.console_tabs.indexOf(c))
            self._refresh_actions()
        except Exception:
            pass
        need = versions.need_java(e.get("version")) if e.get("version") else None
        fmt = versions.pack_format(e.get("version")) if e.get("version") else None
        newmeta = versions.uses_new_packmeta(e.get("version")) \
            if e.get("version") else None
        lines = [
            "<b>%s</b>" % (e.get("world_name")
                           or self._display_name(e)),
            "目录: %s" % e.get("folder"),
            "服务端目录: %s" % (e.get("server_dir") or "-"),
            "存档: %s" % (e.get("world_dir") or "-"),
            "版本: %s" % (e.get("version") or "未知"),
            "加载器: %s" % (e.get("loader") or "未安装"),
            "目录布局: %s" % e.get("layout"),
        ]
        if need:
            lines.append("需要 Java: <b>%d</b>" % need)
        if fmt:
            lines.append("数据包格式: %d（%s）" % (
                fmt,
                "新写法 min/max_format" if newmeta else "旧写法 pack_format"))
        if e.get("seed") is not None:
            lines.append("种子: %s" % e["seed"])
        if e.get("difficulty"):
            lines.append("难度: %s / 模式: %s" % (e["difficulty"],
                                                  e.get("gamemode")))
        if e.get("notes"):
            lines.append("<font color='#c62828'>提示: %s</font>"
                         % "；".join(e["notes"]))
        self.info.setHtml("<br>".join(lines))

        running = self._key(e) in self.procs
        self.act_stop.setEnabled(running)
        self.act_start.setEnabled(not running)

    def _display_name(self, e):
        """界面显示名：优先用户自定义重命名（display_name），否则原名。"""
        return e.get("display_name") or e.get("name") or ""

    def _key(self, e):
        return e.get("folder", "")

    def _cur(self):
        # 图标视图优先（当前可见的那个）
        try:
            if self.icon_view.isVisible():
                its = self.icon_view.selectedItems()
                if its:
                    e = self._icon_entry(its[0])
                    if e is not None:
                        return e
                # ★ 加一层兜底：如果由于某种原因 selectedItems 为空，
                #   但焦点在某个图标上（currentItem），也把它算作选中。
                it = self.icon_view.currentItem()
                if it is not None:
                    e = self._icon_entry(it)
                    if e is not None:
                        return e
        except Exception:
            pass
        rows = self.table.selectionModel().selectedRows()
        if not rows:
            return None
        # ★ 必须按 _sorted_entries() 取：表格行号是排序后填的，
        #   直接用 self.entries 会在排序非 custom 时拿错服务器 ——
        #   启动/停止/配置/删除都会作用到别的服。
        sorted_e = self._sorted_entries()
        if rows[0].row() < len(sorted_e):
            return sorted_e[rows[0].row()]
        return None

    # ---------------- 操作 ----------------
    def do_create(self):
        e = self._cur()
        if not e:
            QMessageBox.information(self, "提示", "请先在列表里选中一个服务器")
            return
            
        try:
            dlg = self._style_dlg(CreateDialog(e, parent=self))
        except Exception as ex:
            import traceback
            traceback.print_exc()
            QMessageBox.critical(
                self, "打开初始化窗口失败",
                "程序在创建初始化窗口时遇到了错误：\n\n%s\n\n"
                "详细追踪：\n%s" % (ex, traceback.format_exc()[-500:]))
            return

        # ★ V1.6.2：非模态打开 —— 用户在下载/初始化时仍可操作主界面
        dlg.setWindowModality(Qt.NonModal)
        # 保持引用，防止对话框被 GC 回收导致闪退
        self._dlg_refs = getattr(self, "_dlg_refs", [])
        self._dlg_refs.append(dlg)

        def _on_create_done():
            info = getattr(dlg, "result_info", None) or {}
            if info:
                # ★ 只更新 java / xms / xmx 三个字段。
                #   以前整条覆盖会清掉用户之前保存的
                #   last_start / auto_restart / auto_backup /
                #   backup_interval / icon 等设置 ——
                #   表现就是"我开了自动重启，初始化服务端后没了"。
                sv = self.data.setdefault("servers", {}).setdefault(
                    self._key(e), {})
                sv["java"] = info.get("java")
                sv["xms"] = info.get("xms", 1024)
                sv["xmx"] = info.get("xmx", 4096)
                # ★ 自动化三项（初始化对话框里默认全勾，可在此覆盖）
                sv["aikar"] = bool(info.get("aikar", True))
                sv["auto_restart"] = bool(info.get("auto_restart", True))
                sv["auto_backup"] = bool(info.get("auto_backup", True))
                self._save_data()

            # ★ 扫描完成后自动重新选中该服务器，刷新右侧信息面板
            target = os.path.abspath(e.get("folder") or "")
            def select_me():
                for i, en in enumerate(self._sorted_entries()):
                    if os.path.abspath(en.get("folder") or "") == target:
                        self.table.selectRow(i)
                        if self.view_mode == "icon" and self.icon_view.count() > i:
                            self.icon_view.setCurrentRow(i)
                        self.on_sel()  # 强制刷新信息面板
                        break

            self.do_scan(then=select_me)
            try:
                self._dlg_refs.remove(dlg)
            except ValueError:
                pass

        dlg.finished.connect(lambda _r: _on_create_done())
        dlg.show()

    def do_gen_bat(self):
        e = self._cur()
        if not e:
            return
        sd = e.get("server_dir") or e["folder"]
        v = e.get("version")
        if not v:
            QMessageBox.warning(self, "缺版本", "该存档读不出版本，"
                                                "请先用『初始化服务端』")
            return
        need = versions.need_java(v)
        sv = self._saved(e)
        jpath = sv.get("java")
        if not jpath:
            j = javacore.pick_for(need)
            if not j:
                QMessageBox.warning(self, "缺 Java",
                                    "没找到 Java %s（本服需要 %s）" % (need, need))
                return
            jpath = j["path"]
        xms = "%dM" % sv.get("xms", 1024)
        xmx = "%dM" % sv.get("xmx", 4096)
        # ★ 不能只看 jars，新版 Forge 没有启动 jar（用 args 文件）。
        #   必须走 install.find_launch 判断真实的启动方式。
        L = install.find_launch(sd)
        launch_mode = L.get("launch") or "jar"
        jar = L.get("jar") or ((e.get("jars") or ["server.jar"])[0])
        args_rel = L.get("args_rel")

        # ★ 防呆：如果是 jar 模式，校验文件到底存不存在
        if launch_mode == "jar":
            jar_path = os.path.join(sd, jar)
            if not os.path.isfile(jar_path):
                QMessageBox.warning(
                    self, "核心缺失", 
                    "没找到核心文件：\n%s\n\n请先点击『初始化服务端』下载核心。" % jar_path
                )
                return

        bat = builder.build_run_bat(
            jpath, jar, xms, xmx,
            launch=launch_mode, args_rel=args_rel,
            pause=sv.get("pause", "error"),
            aikar=sv.get("aikar", False))
        p = os.path.join(sd, "run.bat")
        with open(p, "w", encoding="utf-8", newline="") as f:
            f.write(bat)
        self.statusBar().showMessage("已生成 %s" % p)
        QMessageBox.information(self, "完成", "已生成:\n%s" % p)

    def do_migrate(self):
        dlg = self._style_dlg(MigrateDialog(self.root, self))
        if not dlg.exec():
            return
        v = dlg.values()
        plans = migrate.plan(v["src"], v["dst"])
        if not plans:
            QMessageBox.information(self, "提示", "没有可搬迁的文件夹")
            return
        # ★ 搬迁可能是几十 GB 的复制，放后台线程，别冻住界面
        self.statusBar().showMessage(
            "搬迁中 ...（%d 个文件夹）" % len(plans))

        def job(on_log=None):
            def prog(i, n, name):
                if on_log:
                    on_log("(%d/%d) %s" % (i + 1, n, name))
            # ★ 把 on_log 一并传进去，方便在复制大文件时看到动态
            return migrate.do_migrate(plans, copy=v["copy"],
                                      on_progress=prog, on_log=on_log)

        w = _track(self, Worker(job, {}, self))
        self._migrate_worker = w
        w.log.connect(self._log_console)
        w.done.connect(lambda ok, res: self._after_migrate(ok, res, v))
        w.start()

    def _after_migrate(self, ok, res, v):
        w, self._migrate_worker = getattr(
            self, "_migrate_worker", None), None
        if w is not None:
            self._forget_worker(w)
        if not ok:
            self.statusBar().showMessage("搬迁失败")
            QMessageBox.critical(self, "搬迁失败", str(res))
            return
        fails = ["%s: %s" % (name, msg)
                 for name, good, msg in res if not good]
        self.root = v["dst"]
        self.ed_root.setText(self.root)
        self.data["root"] = self.root
        self._save_data()
        self.do_scan()
        if fails:
            QMessageBox.warning(self, "部分失败", "\n".join(fails))
        else:
            self.statusBar().showMessage("搬迁完成")
            QMessageBox.information(self, "完成", "搬迁完成")

    def do_open_dir(self):
        e = self._cur()
        if not e:
            return
        d = e.get("server_dir") or e["folder"]
        try:
            if sys.platform == "win32":
                os.startfile(d)
            else:
                import subprocess
                subprocess.Popen(["xdg-open", d])
        except Exception as ex:
            QMessageBox.warning(self, "打不开", repr(ex))

    # ---------------- V1.3.0 多源登录准备 ----------------
    def _prepare_auth(self, e, then):
        """按 auth_type 准备登录环境；全部就绪后回调 then() 继续启动。"""
        sd = e.get("server_dir") or e["folder"]
        sv = self._saved(e)
        auth_type = sv.get("auth_type") or "offline"
        # ★ V1.7.0-2：多源共存支持多选第三方源（auth_urls 列表优先，
        #   兼容旧版单值 auth_url）
        auth_urls = sv.get("auth_urls") or []
        if not auth_urls:
            old = (sv.get("auth_url") or "").strip()
            if old:
                auth_urls = [old]
        auth_url = (auth_urls[0] if auth_urls else "").strip()
        loader = (e.get("loader") or "").lower()
        v = e.get("version") or ""
        online = auth_type in ("mojang", "multi")
        self._set_online_mode(sd, online)

        if auth_type != "multi":
            # 正版 / 离线：清理可能残留的登录模组/插件（改名保留）
            self._cleanup_auth_components(sd)
            then()
            return

        # ===== 多源共存 =====
        if loader == "paper":
            comp = install.AUTH_COMPONENTS["paper"]
            need = not self._has_auth_jar(
                sd, "plugins", ["multilogin"])
        else:
            comp = install.AUTH_COMPONENTS["mod"]
            need = not self._has_auth_jar(
                sd, "mods", ["multiyggdrasil", "akiyggdrasil"])
        if not need:
            self._write_auth_cfg(e, auth_url, auth_urls[1:])
            self._auth_multi_hint(e)
            then()
            return

        # 早期 Forge 兼容性提示
        if loader == "forge" and self._is_early_forge(v):
            QMessageBox.information(
                self, "早期版本可能不兼容",
                "当前是较早期的 Forge 版本，多源登录模组"
                "（MultiYggdrasil）可能不兼容。\n\n"
                "若启动后第三方玩家无法登录，请改用正版或离线模式。")

        r = QMessageBox.question(
            self, "安装多源登录组件",
            "启用多源共存需要安装 %s。\n\n"
            "将自动从 Modrinth 下载并配置（后台进行，不卡界面）。\n"
            "继续吗？\n\n（选『否』则直接启动，第三方玩家将无法登录）"
            % comp["name"],
            QMessageBox.Yes | QMessageBox.No, QMessageBox.Yes)
        if r != QMessageBox.Yes:
            then()
            return
        self._log_console("=== 正在下载多源登录组件 %s ===" % comp["name"])
        w = _track(self, Worker(self._dl_auth_worker, {
            "sd": sd, "v": v, "loader": loader, "comp": comp}, self))
        w.log.connect(lambda m: self._log_console(m))
        w.done.connect(
            lambda ok, res: self._on_auth_dl(ok, res, e, auth_url,
                                             auth_urls[1:], then))
        w.start()

    def _dl_auth_worker(self, sd="", v="", loader="", comp=None, on_log=None):
        return install.install_auth_component(
            sd, v, loader, comp["kind"], comp["slug"], on_log=on_log)

    def _on_auth_dl(self, ok, res, e, auth_url, extra_urls, then):
        if ok and (res or {}).get("ok"):
            self._log_console("[登录组件] 安装完成: %s" % (res or {}).get("name"))
            self._write_auth_cfg(e, auth_url, extra_urls)
        else:
            self._log_console("[登录组件] 安装失败: %s"
                              % ((res or {}).get("reason") or "?"))
            loader = (e.get("loader") or "").lower()
            comp = (install.AUTH_COMPONENTS["paper"] if loader == "paper"
                    else install.AUTH_COMPONENTS["mod"])
            if comp.get("hint"):
                QMessageBox.information(
                    self, "需要手动安装 %s" % comp["name"],
                    "%s\n\n%s" % (comp["name"], comp["hint"]))
        self._auth_multi_hint(e)
        then()

    def _auth_multi_hint(self, e):
        """Paper 多源共存：建议把 enforce-secure-profile 设为 false。"""
        if (e.get("loader") or "").lower() != "paper":
            return
        try:
            p = os.path.join(e.get("server_dir") or e["folder"],
                             "server.properties")
            if os.path.isfile(p):
                with open(p, "r", encoding="utf-8") as f:
                    for ln in f:
                        if ln.strip().startswith(
                                "enforce-secure-profile=false"):
                            return  # 已设置过就不再提示
        except Exception:
            pass
        QMessageBox.information(
            self, "建议关闭强制安全档案",
            "Paper 多源共存建议把 server.properties 里的\n"
            "『enforce-secure-profile』设为 false，\n"
            "否则部分外置登录玩家可能被拒绝。\n\n"
            "（可在『服务器配置』里修改后重启）")

    def _has_auth_jar(self, sd, folder, prefixes):
        d = os.path.join(sd, folder)
        try:
            if not os.path.isdir(d):
                return False
            for fn in os.listdir(d):
                low = fn.lower()
                if low.endswith(".jar") and not low.endswith(".disabled"):
                    for p in prefixes:
                        if low.startswith(p):
                            return True
        except Exception:
            pass
        return False

    def _cleanup_auth_components(self, sd):
        """正版/离线模式下，把残留的登录模组/插件改名 .disabled（不删除）。"""
        for folder in ("mods", "plugins"):
            try:
                for m in modman.scan_mods_dir(os.path.join(sd, folder)):
                    n = (m.get("filename") or "").lower()
                    if (n.startswith("multiyggdrasil")
                            or n.startswith("akiyggdrasil")
                            or n.startswith("multilogin")):
                        if m.get("enabled"):
                            try:
                                modman.set_enabled(m["path"], False)
                            except Exception:
                                pass
            except Exception:
                pass

    def _set_online_mode(self, sd, online):
        """改 server.properties 的 online-mode（无该行则追加）。"""
        p = os.path.join(sd, "server.properties")
        try:
            if not os.path.isfile(p):
                return
            with open(p, "r", encoding="utf-8") as f:
                lines = f.readlines()
            val = "true" if online else "false"
            hit = False
            for i, ln in enumerate(lines):
                if ln.strip().startswith("online-mode="):
                    lines[i] = "online-mode=%s\n" % val
                    hit = True
                    break
            if not hit:
                lines.append("online-mode=%s\n" % val)
            with open(p, "w", encoding="utf-8") as f:
                f.writelines(lines)
        except Exception:
            pass

    def _write_auth_cfg(self, e, auth_url, extra_urls=None):
        """写多源登录配置：mods 类 -> config/multi-yggdrasil.ini。

        ★ V1.7.0-2：多选第三方源时，第一个源作为 yggdrasilApiRoot
          （MultiYggdrasil 单 root 格式），其余源以注释写入提示，
          方便服主手动扩展（Paper 的 MultiLogin 由插件自身管理多服务）。
        """
        loader = (e.get("loader") or "").lower()
        if loader == "paper":
            return  # MultiLogin 配置由插件首次启动自动生成
        if not auth_url:
            return
        sd = e.get("server_dir") or e["folder"]
        cfg_dir = os.path.join(sd, "config")
        try:
            os.makedirs(cfg_dir, exist_ok=True)
        except Exception:
            return
        path = os.path.join(cfg_dir, "multi-yggdrasil.ini")
        extra = [u.strip().replace('"', "") for u in (extra_urls or [])
                 if u and u.strip() != auth_url]
        body = (
            "allowOfficial = true\n"
            "allowYggdrasil = true\n"
            'yggdrasilApiRoot = "%s"\n' % auth_url.replace('"', "")
        )
        if extra:
            body += ("\n; 其余第三方源（MultiYggdrasil 单 root，"
                     "如需多源请手动扩展）：\n"
                     + "".join("; yggdrasilApiRoot = \"%s\"\n" % u
                               for u in extra))
        try:
            if os.path.isfile(path):
                with open(path, "r", encoding="utf-8") as f:
                    old = f.read()
                if auth_url in old:
                    return
                QMessageBox.information(
                    self, "配置文件已存在",
                    "config/multi-yggdrasil.ini 已存在且 API 地址与当前设置"
                    "不同，为避免覆盖你的自定义配置，本次没有写入。\n\n"
                    "如需更换皮肤站，请手动修改该文件。")
                return
            with open(path, "w", encoding="utf-8") as f:
                f.write(body)
            self._log_console("[登录] 已生成 multi-yggdrasil.ini")
        except Exception:
            pass

    def _is_early_forge(self, v):
        """极早期 Forge（MultiYggdrasil 支持 1.14+，更早不兼容）。"""
        try:
            from .core import versions as _v
            return (_v.mc_version_key(v, newest_first=False)
                    < _v.mc_version_key("1.14", newest_first=False))
        except Exception:
            return False

    # ---------------- 启动/停止 ----------------
    # ---------------- 控制台页签（支持多开） ----------------
    def _console_tab(self, key, name, create=True):
        """取某个服务器的控制台页签，没有就新建一个。"""
        c = self.consoles.get(key)
        if c is not None:
            return c
        if not create:
            return None
        c = Console()
        c.proc_key = key
        c.set_colors(self.disp.get("console_colors") or None)  # ★ V1.1.0
        self.consoles[key] = c
        idx = self.console_tabs.addTab(c, name or "服务器")
        self.console_tabs.setCurrentIndex(idx)
        return c

    def _console_of(self, proc):
        """按 QProcess 反查它的控制台。"""
        for c in self.consoles.values():
            if c.proc is proc:
                return c
        return None

    def _close_console_tab(self, idx):
        if idx <= 0:
            return  # 总输出页不关
        w = self.console_tabs.widget(idx)
        for k, c in list(self.consoles.items()):
            if c is w:
                if k in self.procs:
                    QMessageBox.information(
                        self, "还在运行",
                        "这个服务器还在运行，先停止再关闭页签。")
                    return
                del self.consoles[k]
                break
        self.console_tabs.removeTab(idx)

    def _refresh_actions(self):
        """按【当前选中】的服务器决定按钮可用性。

        以前启动后直接 act_start.setEnabled(False)，
        那是全局的 -> 一旦开了任何一个服，就再也开不了第二个。
        """
        e = self._cur()
        if not e:
            self.act_start.setEnabled(False)
            self.act_stop.setEnabled(False)
            return
        running = self._key(e) in self.procs
        self.act_start.setEnabled(not running)
        self.act_stop.setEnabled(running)

    # ---------------- V0.5.0：实时状态监控 ----------------
    def _ping_round(self):
        """每 10 秒：对所有运行中的服务器做一次状态探测（Worker 线程）。

        ★ 探测是网络 IO，绝不能卡主线程；busy 集合防止 10 秒轮次
          没跑完就堆积新一轮。
        """
        if not self.procs:
            return
        by_key = {}
        for en in self.entries:
            by_key[self._key(en)] = en
        for k, e in list(by_key.items()):
            if k not in self.procs or k in self._ping_busy:
                continue
            self._ping_busy.add(k)
            port = self._port_of(e)

            def job(port=port):
                return monitor.live_status(port)

            w = _track(self, Worker(job, {}, self))
            w.done.connect(
                lambda ok, res, kk=k: self._on_ping_done(kk, ok, res))
            w.start()

    def _on_ping_done(self, key, ok, res):
        self._ping_busy.discard(key)
        if key not in self.procs:
            self._ping_text.pop(key, None)
            return
        # ★ V1.5.0：成功显示"运行中 (在线: X/Y | 延迟: Zms)"，
        #   失败显示"运行中 (无响应)"，状态列一眼看懂
        text = monitor.ping_text(res) if ok else None
        if text:
            self._ping_text[key] = "运行中 (" + text + ")"
        else:
            self._ping_text[key] = "运行中 (无响应)"
        self._apply_ping_ui(key)

    def _apply_ping_ui(self, key):
        """把监控文本刷到表格状态列 + 图标卡片文字上。"""
        en = None
        for x in self.entries:
            if self._key(x) == key:
                en = x
                break
        if en is None:
            return
        for i, e2 in enumerate(self._sorted_entries()):
            if self._key(e2) == key:
                self._set_row_status(i, e2)
                break
        try:
            for r in range(self.icon_view.count()):
                it = self.icon_view.item(r)
                if it is None or (it.data(Qt.UserRole) or "") != key:
                    continue
                lines = self._item_text(en, as_lines=True)
                it.setData(Qt.UserRole + 3, lines)
                sz = int(self.disp.get("icon", 110))
                px = max(24, int(sz * 0.62))
                it.setSizeHint(QSize(sz + 16,
                                     px + 34 + 17 * max(1, len(lines))))
                self.icon_view.viewport().update()
                break
        except Exception:
            pass

    def _refresh_status(self, e=None):
        """刷新"状态"显示：表格刷新那一列，图标视图重画文字。

        ★ 必须按 _sorted_entries() 的视图顺序定位行：
          _fill_table 是按排序后的顺序填的，如果这里用 self.entries
          的原始顺序，排序非 custom 时"运行中/未初始化"会标到
          别的服务器那一行上。
        """
        sorted_entries = self._sorted_entries()
        if e is None:
            for i, en in enumerate(sorted_entries):
                self._set_row_status(i, en)
            self._refresh_icon_labels()
            return
        target = self._key(e)
        for i, en in enumerate(sorted_entries):
            if self._key(en) == target:
                self._set_row_status(i, en)
                break
        self._refresh_icon_labels()

    def _refresh_icon_labels(self):
        """图标视图下状态由 IconDelegate 画，让 viewport 重画即可。

        ★ 以前这里用 itemWidget 找 QLabel，但 _fill_icons 早就改成
          UserRole+3 + 委托自绘、从不 setItemWidget —— itemWidget
          永远 None，等于什么都没刷新。改成直接 update viewport。
        """
        try:
            if self.icon_view.isVisible():
                self.icon_view.viewport().update()
        except Exception:
            pass

    def _set_row_status(self, row, e):
        if row >= self.table.rowCount():
            return
        running = self._key(e) in self.procs
        if running:
            ping = self._ping_text.get(self._key(e))
            if ping:
                txt, color = ping, "#2e7d32"
            else:
                txt, color = "运行中", "#2e7d32"
        elif e.get("notes"):
            txt, color = "未初始化", "#c62828"
        else:
            txt, color = "就绪", None
        it = self.table.item(row, 5)
        if it is None:
            it = QTableWidgetItem()
            self.table.setItem(row, 5, it)
        it.setText(txt)
        if color:
            it.setForeground(QColor(_on_bg(color)))
        else:
            it.setForeground(QColor(_fg()))

    def _port_of_cached(self, e):
        import time
        k = self._key(e)
        now = time.time()
        cached = self._port_cache.get(k)
        if cached and now - cached["t"] < 10:  # 缓存 10 秒
            return cached["port"]
        p = self._port_of(e)
        self._port_cache[k] = {"port": p, "t": now}
        return p

    def _port_of(self, e):
        """读这个服的 server-port（默认 25565）。"""
        sd = e.get("server_dir") or e.get("folder") or ""
        p = os.path.join(sd, "server.properties")
        try:
            with open(p, encoding="utf-8", errors="replace") as f:
                for line in f:
                    line = line.strip()
                    if line.startswith("server-port="):
                        v = line.split("=", 1)[1].strip()
                        return int(v) if v.isdigit() else 25565
        except Exception:
            pass
        return 25565

    def _check_port_conflict(self, e):
        """多开时端口不能撞。撞了 MC 会起不来（Address already in use）。"""
        # ★ B1：走端口缓存，避免每次启动把全部服务器目录的
        #   server.properties 重新读一遍（十几个服时是真的会拖慢）
        port = self._port_of_cached(e)
        for en in self.entries:
            if self._key(en) not in self.procs:
                continue
            if self._port_of_cached(en) != port:
                continue
            r = QMessageBox.warning(
                self, "端口冲突",
                "『%s』正在运行，占用的也是 %d 端口。\n"
                "『%s』用同一个端口会启动失败"
                "（Address already in use）。\n\n"
                "要先改掉其中一个的 server-port 吗？"
                % (en.get("name"), port, e.get("name")),
                QMessageBox.Yes | QMessageBox.No,
                QMessageBox.Yes)
            if r == QMessageBox.Yes:
                self._suggest_port(e)
                return False
            return True  # 用户坚持，让他试
        return True

    def _firewall_allow(self, e):
        """★ V0.4.0：放行 server-port 的 TCP 入站（Windows 防火墙）。

        防止朋友联机时被防火墙拦截。没有管理员权限 / 不是 Windows
        就静默跳过，绝不让防火墙操作打断服务器启动。
        """
        if os.name != "nt":
            return
        try:
            port = self._port_of(e)
        except Exception:
            port = None
        if not port:
            return
        try:
            import threading
            import subprocess as _sp
            cmd = (
                ["powershell", "-NoProfile", "-Command",
                 "New-NetFirewallRule -DisplayName 'MCSM_Server_%d'"
                 " -Direction Inbound -Protocol TCP -LocalPort %d"
                 " -Action Allow -ErrorAction SilentlyContinue"
                 % (port, port)])

            def _run():
                # ★ V1.4.0：防火墙命令挪到后台线程，主线程绝不同步等待
                # ★ V1.6.2：加 CREATE_NO_WINDOW，禁止弹出黑色 CMD 窗
                try:
                    _sp.run(cmd, capture_output=True, timeout=30,
                            creationflags=getattr(_sp, "CREATE_NO_WINDOW", 0))
                except Exception:
                    pass  # 没权限就静默，别影响启动

            threading.Thread(target=_run, daemon=True).start()
            self._log_console("[防火墙] 正在后台放行 TCP %d（MCSM_Server_%d）"
                              % (port, port))
        except Exception:
            pass

    def _suggest_port(self, e):
        """帮忙改成一个没被占用的端口并提示。"""
        used = set()
        for en in self.entries:
            if self._key(en) in self.procs:
                used.add(self._port_of(en))
        port = self._port_of(e)
        while port in used:
            port += 1
        sd = e.get("server_dir") or e.get("folder") or ""
        p = os.path.join(sd, "server.properties")
        try:
            lines = []
            if os.path.isfile(p):
                with open(p, encoding="utf-8", errors="replace") as f:
                    lines = f.read().splitlines()
            out, hit = [], False
            for ln in lines:
                if ln.strip().startswith("server-port="):
                    out.append("server-port=%d" % port); hit = True
                else:
                    out.append(ln)
            if not hit:
                out.append("server-port=%d" % port)
            with open(p, "w", encoding="utf-8", newline="\n") as f:
                f.write("\n".join(out) + "\n")
            self._log_console("[端口] 已改为 %d" % port)
            QMessageBox.information(
                self, "已改端口",
                "『%s』的 server-port 已改成 %d。\n"
                "再点一次『启动』即可。" % (e.get("name"), port))
        except Exception as ex:
            QMessageBox.warning(self, "改端口失败", repr(ex))

    def do_start(self, auto_restart=False, entry=None):
        # ★ A2：自动重启时传 entry，不再靠 selectRow 抢用户当前选中的服
        e = entry if entry is not None else self._cur()
        if not e:
            return
        k = self._key(e)
        if k in self.procs:
            return
        # ★ V1.6.0：点击启动立即给反馈并禁用按钮，防止狂点。
        #   真正启动成功后 _refresh_actions() 会重设按钮；
        #   提前中止的路径各自调用 _restore_start_btn 恢复。
        self.statusBar().showMessage(
            "正在启动: %s ..." % (e.get("name") or e.get("folder")), 0)
        try:
            self.act_start.setEnabled(False)
        except Exception:
            pass
        sd = e.get("server_dir") or e["folder"]
        bat = os.path.join(sd, "run.bat")
        jar = None
        if e.get("jars"):
            jar = os.path.join(sd, e["jars"][0])
        if not os.path.isfile(bat) and not (jar and os.path.isfile(jar)):
            QMessageBox.warning(self, "无法启动",
                                "没有 run.bat 也没有服务端 jar，"
                                "请先『初始化服务端』")
            self._restore_start_btn(e)
            return

        if not self._check_port_conflict(e):
            self._restore_start_btn(e)
            return

        # ★ V0.4.0：防火墙自动放行 server-port（静默，失败不打断启动）
        self._firewall_allow(e)

        # ★ V1.3.0：登录环境准备（正版/离线 即时处理；
        #   多源共存 需下载登录组件时先走 Worker，完成后自动继续启动）
        self._prepare_auth(e, lambda: self._go_start_now(e, auto_restart))

    def _go_start_now(self, e, auto_restart=False):
        k = self._key(e)
        if k in self.procs:
            return
        sd = e.get("server_dir") or e["folder"]
        v = e.get("version")
        need = versions.need_java(v) if v else None
        sv = self._saved(e)
        jpath = sv.get("java")
        if not jpath:
            j = javacore.pick_for(need)
            if not j:
                QMessageBox.warning(self, "缺 Java",
                                    "没找到 Java %s" % need)
                self._restore_start_btn(e)
                return
            jpath = j["path"]
        # ★ 读取 Aikar 优化开关
        use_aikar = sv.get("aikar", False)
        raw_xms = sv.get("xms", 1024)
        raw_xmx = sv.get("xmx", 4096)
        # Aikar 官方强烈建议 Xms 和 Xmx 保持一致
        if use_aikar:
            raw_xmx = raw_xms
        xms = "%dM" % raw_xms
        xmx = "%dM" % raw_xmx

        proc = QProcess(self)
        proc.setWorkingDirectory(sd)
        proc.setProcessChannelMode(QProcess.MergedChannels)
        proc.readyRead.connect(lambda p=proc: self._read(p))
        proc.finished.connect(lambda code, st, p=proc: self._finished(p, code))
        # ★ A1：FailedToStart 不走 finished，不接这个信号 UI 会卡死在"运行中"
        proc.errorOccurred.connect(
            lambda err, p=proc: self._start_failed(p, err))

        # ★ 不走 run.bat，直接启动 java。
        #   原因：run.bat 结尾有 pause（"按任意键继续"），
        #   在管理器里启动的话，那个 pause 会卡住子进程 ——
        #   服务器已经关了，但进程不退出，你得跑到 cmd 窗口
        #   按一下键才能结束。这就是"关闭很久"的原因。
        #   直接调 java 还能拿到准确退出码。
        launch_mode = "jar"
        jar_name = jar
        args_rel = None
        try:
            L = install.find_launch(sd)
            launch_mode = L.get("launch") or "jar"
            args_rel = L.get("args_rel")
            jar_name = (os.path.join(sd, L["jar"])
                        if L.get("jar") else None)
        except Exception:
            pass

        if launch_mode == "argsfile" and args_rel:
            # 新版 Forge：java @user_jvm_args.txt @...\win_args.txt
            uj = os.path.join(sd, "user_jvm_args.txt")
            aikar_args = builder.AIKAR_FLAGS if use_aikar else []
            try:
                if not os.path.isfile(uj):
                    with open(uj, "w", encoding="utf-8") as f:
                        f.write("-Xms%s\n-Xmx%s\n" % (xms, xmx))
                        if aikar_args:
                            f.write("\n".join(aikar_args) + "\n")
                elif aikar_args:
                    # 已存在：补上 Aikar 参数（防重复），并保证 Xmx 与 Xms 一致
                    with open(uj, "r", encoding="utf-8") as f:
                        c = f.read()
                    need = False
                    if "-XX:+UseG1GC" not in c:
                        c = c.rstrip("\n") + "\n" + "\n".join(aikar_args) + "\n"
                        need = True
                    m = re.search(r"-Xmx(\S+)", c)
                    if m and m.group(1) != ("%s" % xms):
                        c = re.sub(r"-Xmx\S+", "-Xmx%s" % xms, c)
                        need = True
                    if need:
                        with open(uj, "w", encoding="utf-8") as f:
                            f.write(c)
            except Exception:
                pass
            # ★ 绝对路径即可。QProcess 会自动为带空格的参数加双引号，
            #   如果这里手动加了双引号，反而会被转义成 "\"@...\"" 导致 Java 认不出。
            proc.start(jpath, ["@" + uj,
                               "@" + os.path.join(sd, args_rel),
                               "nogui"])
        elif launch_mode == "jar" and jar_name and os.path.isfile(jar_name):
            # ★ 直接用 java 启动，绕开 run.bat 里的 pause
            java_args = ["-Xms%s" % xms, "-Xmx%s" % xmx]
            if use_aikar:
                java_args.extend(builder.AIKAR_FLAGS)
            java_args.extend(["-jar", jar_name, "nogui"])
            proc.start(jpath, java_args)
        elif os.path.isfile(bat):
            # 兜底：实在判断不出来才走 bat
            proc.start(bat, [])
        else:
            QMessageBox.warning(self, "无法启动",
                                "没找到可启动的核心，"
                                "请先『初始化服务端』")
            self._restore_start_btn(e)
            return

        # ★ 隐患 3：把"是不是自动重启"记在 proc 上，
        #   _start_failed 据此决定是否弹窗（自动重启失败不弹）
        try:
            proc._auto_restart = bool(auto_restart)
        except Exception:
            pass
        self.procs[k] = proc
        self._is_stopping[k] = False
        # ★ 只有手动启动才重置崩溃计数，自动重启不能重置，否则会无限循环
        if not auto_restart:
            self.auto_restart_count[k] = 0
        
        # 记录最近开服时间（"按最近开服"排序要用）
        try:
            import time as _t
            sv = self.data.setdefault("servers", {}).setdefault(k, {})
            sv["last_start"] = _t.time()
            self._save_data()
            
            # ★ 开启定时备份
            if sv.get("auto_backup"):
                interval = sv.get("backup_interval", 30) * 60 * 1000
                if k in self.auto_backup_timers:
                    self.auto_backup_timers[k].stop()
                timer = QTimer(self)
                timer.timeout.connect(lambda: self._do_auto_backup(k, e))
                timer.start(interval)
                self.auto_backup_timers[k] = timer
                self._log_console(f"[备份] 已开启定时备份，每 {sv.get('backup_interval')} 分钟一次")

            # ★ V0.5.0：每日定时重启（每分钟检查一次，命中就倒计时重启）
            rt = sv.get("auto_restart_time") or ""
            if rt:
                if k in self._restart_timers:
                    self._restart_timers[k].stop()
                rt_timer = QTimer(self)
                rt_timer.timeout.connect(lambda: self._check_restart_time(k, e))
                rt_timer.start(60000)
                self._restart_timers[k] = rt_timer
                self._log_console("[定时重启] 已开启，每天 %s 自动重启"
                                  % rt)
                # 启动后立即检查一次（配置时间刚好到点的场景）
                self._check_restart_time(k, e)
        except Exception:
            pass

        # ★ 每个服务器一个独立控制台页签。
        #   以前共用一个 console + 一个 proc 指针：
        #   开第二个会把 proc 覆盖掉，输入框就打到错的服上，
        #   而且第一个服退出时 set_running(False) 会把输入框锁死。
        c = self._console_tab(k, e.get("name") or "服务器")
        c.proc = proc
        c.set_running(True)
        c.clear()
        c.append("=== 启动 %s ===" % (e.get("name")))
        self.console_tabs.setCurrentIndex(self.console_tabs.indexOf(c))

        self._refresh_actions()
        self._refresh_status(e)
        self._warn_bad_levelname(e)
        self.statusBar().showMessage(
            "运行中: %s（共 %d 个在运行）"
            % (e.get("name"), len(self.procs)))

    def _read(self, proc):
        # ★ 把 readAll 和 state() 一起放进 try ——
        #   当前调用链上 proc 一定活着，但这是"靠时机"兜底，
        #   不是"代码"兜底。把 state() 也包进来，将来新增调用点
        #   不会因为 proc 已被 deleteLater 而抛 RuntimeError。
        try:
            data = proc.readAll().data()
            state = proc.state()
        except Exception:
            return
        # ★ 隐患 1：进程已退出时 readAll() 通常返回空，
        #   此时不能早退 —— 否则下面"残留 buf 吐出来"那段永远走不到，
        #   _finished 里调 self._read(proc) 的兜底就是空转。
        #   进程还在跑、本轮又没数据，那才是真没事可做。
        if not data and state != QProcess.NotRunning:
            return
            
        # 获取或创建这个进程专属的缓冲区
        buf = self._proc_buffers.setdefault(proc, bytearray())
        buf.extend(data)
        
        # 按 \n 切分，只取完整的行（防止 QProcess 截断中文字符）
        # ★ C2：用 find + del 原地删除，替代 bytearray.split(b"\n", 1)。
        #   以前每次 split 都会把剩余内容整体复制一遍 ——
        #   一次 readyRead 如果带进来几 MB（log-ips=true / debug 日志），
        #   要按行数做几万次 O(n) 复制，整体就是 O(n²)，控制台会明显卡。
        #   现在 buf 只在原位切片，剩余内容不复制。
        #   顺带把 self._console_of(proc) 提到循环外，每行不再查一次。
        c = self._console_of(proc) or self.console
        while True:
            pos = buf.find(b"\n")
            if pos < 0:
                break
            # ★ bytearray 切片本身就是新对象，.decode() 直接支持
            #   bytearray；再包一层 bytes() 是多一次 O(pos) 复制。
            line_bytes = buf[:pos]
            del buf[:pos + 1]

            # 针对这一行完整的数据进行解码
            line_str = ""
            for enc in ("utf-8", "gbk"):
                try:
                    line_str = line_bytes.decode(enc) + "\n"
                    break
                except Exception:
                    continue
            else:
                line_str = line_bytes.decode("utf-8", "replace") + "\n"

            c.append(line_str)
            
        # 如果进程关闭，缓冲区还有残留且没换行的内容，也要吐出来
        if state == QProcess.NotRunning and buf:
            # ★ C1：跟主循环保持一致，先 utf-8 再 gbk，
            #   避免最后半行 GBK 中文被切在 \n 前打成 �
            line_str = ""
            for enc in ("utf-8", "gbk"):
                try:
                    line_str = buf.decode(enc) + "\n"
                    break
                except Exception:
                    continue
            else:
                line_str = buf.decode("utf-8", "replace") + "\n"
            # c 在主循环之前已经算过（proc 没变，_console_of 结果一样）
            c.append(line_str)
            self._proc_buffers.pop(proc, None)

    def _check_restart_time(self, k, e):
        """每分钟检查一次，到点就触发 60 秒倒计时重启。"""
        try:
            sv = self.data.get("servers", {}).get(k, {})
            rt = sv.get("auto_restart_time") or ""
        except Exception:
            return
        if not rt or k not in self.procs:
            return
        if k in self._restart_countdown or k in self._plan_restart:
            return
        import time as _t
        try:
            now = _t.localtime()
            hhmm = "%02d:%02d" % (now.tm_hour, now.tm_min)
        except Exception:
            return
        if hhmm == rt:
            self._start_planned_restart(k, e)

    def _start_planned_restart(self, k, e):
        """写公告 + 60 秒后 stop；_finished 里检测 _plan_restart 自动再启动。"""
        proc = self.procs.get(k)
        if proc is None:
            return
        self._plan_restart[k] = True
        self._restart_countdown[k] = True
        self._log_console("[定时重启] 『%s』将在 60 秒后重启！"
                          % (e.get("name") or ""))
        try:
            proc.write(("say [系统] 服务器将在 60 秒后重启！\r\n")
                       .encode("utf-8"))
        except Exception:
            pass

        def _do_stop():
            self._restart_countdown.pop(k, None)
            p = self.procs.get(k)
            if p is None:
                return
            try:
                p.write(b"stop\r\n")
            except Exception:
                pass

        QTimer.singleShot(60000, _do_stop)

    def _finished(self, proc, code):
        # ★ Bug 4 修复：进程退出前把残留的缓冲区吐完（可能包含崩溃的最后一行）
        try:
            self._read(proc)
        except Exception:
            pass

        key = None
        for k, p in list(self.procs.items()):
            if p is proc:
                key = k
                del self.procs[k]
                break

        # ★ 多开修复：进程结束第一时间清掉状态标记，
        #   防止残留的 _is_stopping 把崩溃误判成"主动停止"、
        #   残留的 _port_cache 让悬停显示旧端口。
        #   （is_stopping 提前取出来，供下面自动重启判断使用）
        is_stopping = self._is_stopping.pop(key, None)
        self._port_cache.pop(key, None)
        # ★ V0.5.0：计划重启标记提前取出；顺带清掉监控/倒计时状态
        is_plan = self._plan_restart.pop(key, False)
        self._ping_text.pop(key, None)
        self._restart_countdown.pop(key, None)
        if key in self._restart_timers:
            self._restart_timers[key].stop()
            del self._restart_timers[key]

        c = self._console_of(proc) or self.console
        c.append("=== 已退出，代码 %d ===" % code)
        if code != 0:
            c.append("[!] 退出码不是 0，说明是崩溃/异常退出。"
                     "往上翻看红色报错。")
        # 只锁【这一个】控制台的输入框，不影响其它还在跑的服
        c.set_running(False)
        c.proc = None

        self._refresh_actions()
        if key:
            for en in self.entries:
                if self._key(en) == key:
                    self._refresh_status(en)
                    break
        if self.procs:
            self.statusBar().showMessage(
                "已停止（还有 %d 个在运行）" % len(self.procs), 4000)
        else:
            self.statusBar().showMessage("已停止", 3000)
        # ★ 删除下面 4 行暴力覆盖，信任 _refresh_actions() 
        # self.act_stop.setEnabled(False)
        # self.act_start.setEnabled(True)
        # self.statusBar().showMessage("已停止")

        # ★ 停止定时备份
        if key in self.auto_backup_timers:
            self.auto_backup_timers[key].stop()
            del self.auto_backup_timers[key]

        # ★ V0.5.0：计划重启 —— 不弹窗、不计数，3 秒后自动再启动
        if is_plan:
            for en in self.entries:
                if self._key(en) == key:
                    self._log_console(
                        "[定时重启] 正在重新启动『%s』..." % en.get("name"))
                    QTimer.singleShot(
                        3000, lambda: self._try_restart(key, en))
                    break

        # ★ 自动重启逻辑
        elif code != 0 and not is_stopping:
            sv = self.data.get("servers", {}).get(key, {})
            if sv.get("auto_restart"):
                cnt = self.auto_restart_count.get(key, 0)
                if cnt < 5:
                    self.auto_restart_count[key] = cnt + 1
                    self._log_console(f"[!] 服务器异常退出，10 秒后自动重启（第 {cnt+1} 次）")
                    for en in self.entries:
                        if self._key(en) == key:
                            QTimer.singleShot(10000, lambda: self._try_restart(key, en))
                            break
                else:
                    self._log_console("[!] 自动重启次数已达 5 次，暂停重启。请检查日志。")
                    # ★ V1.6.0：自动重启已放弃 → 智能引导打开日志分析
                    self._ask_crash_diag(key)
            else:
                # ★ V1.6.0：没开自动重启 → 崩溃后智能引导
                self._log_console("[!] 服务器异常退出（未开启自动重启）。")
                self._ask_crash_diag(key)
        else:
            if key:  # ★ 建议 6 修复：防止 None 写入脏数据
                self.auto_restart_count[key] = 0

        # ★ 第三处修改：精准提取当前结束的服务器进行备份
        e = getattr(self, "pending_backups", {}).pop(key, None)
        if e is not None:
            self._after_stop_backup(e)

        # ★ 清理 QProcess 对象和日志缓冲区，防止内存泄漏
        self._proc_buffers.pop(proc, None)
        try:
            proc.deleteLater()
        except Exception:
            pass

        # ★ 多开修复：进程结束强制全量重绘，彻底刷新
        #   "运行中/已停止" 状态，避免界面残留旧状态
        try:
            self._fill_views()
        except Exception:
            pass

    def _start_failed(self, proc, err):
        """QProcess 启动失败（java 路径失效 / 权限不足 / 被安全软件拦）。

        FailedToStart 不会发出 finished 信号，所以 _finished 里的清理
        逻辑不会走。不接这个信号的话，procs 里会留一条永远不退出的
        记录，界面卡在"运行中"，按钮也全错乱。
        """
        try:
            from PySide6.QtCore import QProcess as _QP
            if err != _QP.ProcessError.FailedToStart:
                return
        except Exception:
            return

        # ★ 隐患 3 前置：先读完 _auto_restart 再 deleteLater ——
        #   虽然 PySide6 现在允许 deleteLater 后读 Python 属性，
        #   但这是隐含行为，提前读保证不依赖它。
        is_auto = bool(getattr(proc, "_auto_restart", False))

        key = None
        for k, p in list(self.procs.items()):
            if p is proc:
                key = k
                del self.procs[k]
                break

        # ★ 隐患 2：do_start 在 procs[k] = proc 之后、errorOccurred
        #   之前就建了 auto_backup timer，这里不清，timer 会挂到下次
        #   触发（30 分钟）才被 _do_auto_backup 的 k not in procs 挡住。
        if key and key in self.auto_backup_timers:
            try:
                self.auto_backup_timers[key].stop()
                del self.auto_backup_timers[key]
            except Exception:
                pass

        c = self._console_of(proc) or self.console
        c.append("[!] 服务器未能启动：java 找不到 / 权限不足 / 被拦截")
        c.set_running(False)
        c.proc = None

        self._proc_buffers.pop(proc, None)
        try:
            proc.deleteLater()
        except Exception:
            pass

        self._refresh_actions()
        if key:
            for en in self.entries:
                if self._key(en) == key:
                    self._refresh_status(en)
                    break

        # ★ 隐患 3：自动重启失败不弹窗，避免抢用户操作别的服的焦点。
        #   手动启动失败照旧弹，用户能马上看到并去修 Java。
        if not is_auto:
            QMessageBox.warning(
                self, "启动失败",
                "服务器进程没能起来。\n\n"
                "多半是 Java 路径失效（重装或移动过 Java），\n"
                "或者安全软件拦了 java.exe。\n\n"
                "可以到『服务器配置 → 启动设置』里重新选 Java。")
            # ★ V1.6.0：启动直接失败 → 智能引导打开日志分析
            self._ask_crash_diag(key)
        else:
            self._log_console(
                "[!] 自动重启失败：java 找不到 / 权限不足 / 被拦截。"
                "已停止自动重启。")

    def do_stop(self):
        e = self._cur()
        if not e:
            return
        k = self._key(e)
        proc = self.procs.get(k)
        if not proc:
            # ★ V1.1.0：没在运行直接提示，不弹备份确认框
            QMessageBox.information(self, "提示", "这个服没在运行")
            return
        # ★ V1.1.0：停止时询问是否在停止后备份存档
        r = QMessageBox.question(
            self, "停止服务器",
            "是否在停止后备份存档？\n\n"
            "· 是：服务器退出后自动备份存档\n"
            "  （格式按『服务器配置 → 备份』里的备份格式）\n"
            "· 否：只停止，不备份",
            QMessageBox.Yes | QMessageBox.No, QMessageBox.No)
        if r == QMessageBox.Yes:
            self.do_stop_backup(k, e, proc)
            return
        self._is_stopping[k] = True
        # ★ 立刻给反馈。以前点了停止什么提示都没有，
        #   服务器其实在慢慢保存世界，用户会以为卡住了。
        self._log_console("=== 正在停止，等待世界保存完毕 ===")
        self.statusBar().showMessage("正在停止 %s ..." % e.get("name"))
        self.act_stop.setEnabled(False)
        try:
            proc.write(b"stop\n")
        except Exception:
            pass
        # 8 秒还没退出就强杀（防止卡死时永远不退出）
        QTimer.singleShot(8000, lambda: self._kill(proc, e))

    def _kill(self, proc, e=None):
        """强杀。走到这里说明 8 秒了还没自己退出。"""
        try:
            if proc.state() != QProcess.NotRunning:
                self._log_console(
                    "[!] 8 秒未退出，强制结束。"
                    "（如果每次都这样，可能是插件/模组卡住退出流程）")
                proc.kill()
        except Exception:
            pass

    def closeEvent(self, ev):
        # ★ 1. 先检查后台任务（线程：搬迁/导入/扫描）
        busy = []
        for attr, label in (("_migrate_worker", "统一搬迁"),
                            ("_import_worker", "导入地图"),
                            ("_scan_worker", "扫描")):
            w = getattr(self, attr, None)
            if w is not None and w.isRunning():
                busy.append(label)
        if busy:
            r = QMessageBox.question(
                self, "后台任务还在跑",
                "正在后台执行：%s。\n\n"
                "强行退出可能留下复制了一半的文件夹"
                "（之后可以手动删掉重来）。\n确定退出？"
                % "、".join(busy))
            if r != QMessageBox.Yes:
                ev.ignore()
                return

        # ★ 2. 再检查运行中的服务器（进程：MC 服务端）
        if self.procs:
            r = QMessageBox.question(
                self, "确认", "还有 %d 个服务器在运行，确定退出？"
                % len(self.procs))
            if r != QMessageBox.Yes:
                ev.ignore()
                return
            # ★ 标记为主动停止，防止退出时触发自动重启
            for k in list(self.procs.keys()):
                self._is_stopping[k] = True
                
            # 发送 stop 命令（让服务器优雅关闭保存存档）
            for p in list(self.procs.values()):
                try:
                    p.write(b"stop\n")
                except Exception:
                    pass

            # ★ 用 QEventLoop 丝滑等待，不会卡死界面
            from PySide6.QtCore import QEventLoop
            loop = QEventLoop()

            def check_procs():
                if not self.procs:
                    loop.quit()

            # 轮询：进程退光了就退出
            check_timer = QTimer(self)
            check_timer.timeout.connect(check_procs)
            check_timer.start(100)

            # 超时守卫：用独立 QTimer 实例，而不是 singleShot 匿名定时器
            # 因为 loop 提前 quit 后，匿名定时器还会在事件队列里活着，
            # 5 秒后触发时 loop 已经销毁 -> RuntimeError: already deleted
            timeout_timer = QTimer(self)
            timeout_timer.setSingleShot(True)
            timeout_timer.timeout.connect(loop.quit)
            timeout_timer.start(5000)

            loop.exec()  # 不会阻塞 UI

            # ★ 退出后立刻停掉所有定时器，防止它们在 loop 销毁后触发
            check_timer.stop()
            timeout_timer.stop()

            # 剩余没退出的，强杀
            for p in list(self.procs.values()):
                try:
                    if p.state() != QProcess.NotRunning:
                        p.kill()
                        # 强杀是异步的，等最多 500ms 让 QProcess 走完 finished 信号
                        # 否则主程序一关，java 进程可能变成孤儿进程
                        p.waitForFinished(500)
                except Exception:
                    pass

        # ★ 3. 停掉所有还在跑的后台线程
        try:
            from .dialogs import _stop_all_threads
            _stop_all_threads()
        except Exception:
            pass
            
        # ★ 停掉所有的定时备份定时器
        for timer in self.auto_backup_timers.values():
            try:
                timer.stop()
            except Exception:
                pass
        self.auto_backup_timers.clear()
        
        self._save_data()
        ev.accept()


def _force_utf8():
    """强制 stdout / stderr / 默认编码用 UTF-8。

    为什么会需要这个：
      Windows 上 Python 的默认编码跟"进程怎么被启动"有关。
      从某些启动器 / 计划任务 / 重定向管道启动时，locale 会退化成
      ascii，于是任何中文（路径、世界名、日志）一 print 就
      UnicodeEncodeError。
      这里显式改成 utf-8 并 errors='replace'，
      保证最坏情况也只是显示成 ?，不会整个崩掉。

      另外：--noconsole 打包（EXE）后，sys.stdin / stdout / stderr
      可能是 None。此时任何 print() 或 sys.stderr.write() 都会抛
      RuntimeError: lost sys.stdin —— 引发二次崩溃闪退。
      所以先给 None 的流挂上空设备替身，保证无控制台也能安全运行。
    """
    # ★ --noconsole 保护：None 的流换成空设备，绝不二次崩溃
    if sys.stdin is None:
        try:
            sys.stdin = open(os.devnull, "r")
        except Exception:
            pass
    if sys.stdout is None:
        try:
            sys.stdout = open(os.devnull, "w")
        except Exception:
            pass
    if sys.stderr is None:
        try:
            sys.stderr = open(os.devnull, "w")
        except Exception:
            pass
    for name in ("stdout", "stderr"):
        try:
            getattr(sys, name).reconfigure(
                encoding="utf-8", errors="replace")
        except Exception:
            pass
    try:
        import locale
        locale.getpreferredencoding(False)
    except Exception:
        pass


def _set_app_user_model_id():
    """告诉 Windows：这个进程是独立应用，别用 python.exe 的图标。

    不设的话任务栏显示的是 Python 那个图标，
    而且会和所有 python 程序挤在同一组里。
    """
    if sys.platform != "win32":
        return
    try:
        import ctypes
        ctypes.windll.shell32.SetCurrentProcessExplicitAppUserModelID(
            "Yuanbao.MCServerManager.v4")
    except Exception:
        pass


def check_environment():
    """检查当前路径是否对 Java 安全（防 Java 启动失败）。

    Java 的 -cp 参数对路径里的 ! / # 极其敏感，
    尤其是生成/加载 jar 包时会被 Windows 命令行破坏，
    轻则启动失败，重则执行到一半崩。
    """
    current_dir = os.path.abspath(os.getcwd())
    if "!" in current_dir or "#" in current_dir:
        msg = (
            "警告：当前软件所在路径包含特殊字符（如 ! 或 #）。\n\n"
            "这极有可能导致 Minecraft 服务端核心（Java）无法正常启动或报错。\n"
            "当前路径：%s\n\n"
            "强烈建议将软件移动到纯英文、无特殊符号的路径下运行！" % current_dir
        )
        QMessageBox.warning(None, "路径警告", msg)


def get_base_dir():
    """获取软件基目录（日志 / 用户数据都落在这里）。

    - 打包环境（PyInstaller --noconsole EXE）：
      sys.executable 是 EXE 本身，__file__ 则指向只读的 _internal，
      所以返回 EXE 所在的最外层目录。
    - 开发环境：返回 app.py 所在目录。
    """
    if getattr(sys, "frozen", False):
        return os.path.dirname(sys.executable)
    return os.path.dirname(os.path.abspath(__file__))


def main():
    _force_utf8()
    # ★ 1. 强制系统代理支持：
    #   用户挂加速器/代理时，Windows 系统代理通常已设好，
    #   这里显式装一个用系统代理的 opener，防止 urllib 直连被卡死。
    urllib.request.install_opener(
        urllib.request.build_opener(urllib.request.ProxyHandler()))
    # ★ 2. 全局文件日志：写入软件基目录下的 manager.log
    #   （用 get_base_dir() 定位，而不是 CWD / __file__ ——
    #     打包后 __file__ 指向 _internal 只读目录，CWD 也不可靠。）
    base_dir = get_base_dir()
    log_file = os.path.join(base_dir, "manager.log")
    try:
        logging.basicConfig(
            filename=log_file,
            level=logging.INFO,
            format="%(asctime)s - %(levelname)s - %(message)s",
            encoding="utf-8",
        )
    except Exception:
        # ★ 兜底：软件可能被放在 C 盘等无写权限的位置，
        #   此时绝不因"写不了日志"而崩溃，回退到用户目录。
        try:
            local_log_dir = os.path.join(
                os.environ.get("LOCALAPPDATA", os.path.expanduser("~")),
                "MCSM")
            os.makedirs(local_log_dir, exist_ok=True)
            log_file = os.path.join(local_log_dir, "manager.log")
            logging.basicConfig(
                filename=log_file,
                level=logging.INFO,
                format="%(asctime)s - %(levelname)s - %(message)s",
                encoding="utf-8",
            )
        except Exception:
            # 最后兜底：连用户目录都写不了，就只保留内存日志，
            #   程序照常运行。
            logging.basicConfig(
                level=logging.INFO,
                format="%(asctime)s - %(levelname)s - %(message)s",
            )
    logging.info("=== MCServerManager 启动 ===")
    # 先读源设置（镜像 / CF key），后面所有联网都用它
    try:
        from .core import sources
        sources.load()
    except Exception:
        pass
    # ★ Windows 任务栏图标
    #   用 python.exe 启动时，任务栏默认显示 Python 的图标，
    #   而且会和别的 python 程序合并成一组。
    #   设了 AppUserModelID 之后，任务栏就用自己的图标、独立分组。
    _set_app_user_model_id()
    try:
        app = QApplication(sys.argv)
        # ★ 3. 路径合法性检测（QMessageBox 需要 QApplication 已存在）
        check_environment()
        # ★ Qt 退出前停掉所有后台线程。
        #   用 aboutToQuit 而不是 atexit：Qt 清理自己对象是在
        #   aboutToQuit 之后，atexit 则晚得多 —— 那时线程对象可能
        #   已经被回收，再 stop 就晚了，会抛
        #   "QThread: Destroyed while thread is still running"。
        try:
            from .dialogs import _stop_all_threads
            app.aboutToQuit.connect(_stop_all_threads)
        except Exception:
            pass
        try:
            app.setWindowIcon(app_icon())
        except Exception:
            pass
        # ★ 先 setStyle 再 setStyleSheet：
        #   Fusion 是跨平台一致外观的基础，后设样式表才不会被它冲掉。
        app.setStyle("Fusion")
        # 套主题（字体 / 颜色 / 背景图）
        try:
            from .core import theme as _th
            _th.load()
            app.setStyleSheet(_th.stylesheet(None))
            _th.apply_font(app)
            # ★ app 级调色板：兜住所有窗口里 stylesheet 没覆盖的控件
            #   （Windows 系统调色板的 Window 是纯白，不兜就是白底白字）
            _th.apply_palette(app)
        except Exception:
            pass
        w = MainWindow()
        w.show()
        sys.exit(app.exec())
    except Exception as e:
        logging.exception("程序发生未捕获的致命异常：")
        raise


if __name__ == "__main__":
    main()
