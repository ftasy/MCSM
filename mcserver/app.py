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
import urllib.request
import logging

from PySide6.QtCore import Qt, QTimer
from PySide6.QtWidgets import (
    QAbstractItemView, QApplication, QComboBox, QFrame, QGroupBox,
    QHBoxLayout, QHeaderView, QLabel, QLineEdit, QListWidget, QMessageBox,
    QPushButton, QSplitter, QStatusBar, QTabBar, QTabWidget, QTableWidget,
    QTextEdit, QVBoxLayout, QWidget, QMainWindow)
from .core import theme
from .core.palette import PAL
from .widgets import Console, FloatConsole, NavButton, app_icon, _th

# ★ 重构：MainWindow 按职责拆成 7 个 mixin（见 app_*.py）
from .app_views import ViewsMixin
from .app_settings import SettingsMixin
from .app_servers import ServerOpsMixin
from .app_library import LibraryMixin
from .app_actions import ActionsMixin
from .app_console import ConsoleMixin
from .app_process import ProcessMixin


class MainWindow(
    ViewsMixin,
    SettingsMixin,
    ServerOpsMixin,
    LibraryMixin,
    ActionsMixin,
    ConsoleMixin,
    ProcessMixin,
    QMainWindow):
    """主窗口。

    实际方法分散在 app_views / app_settings / app_servers /
    app_library / app_actions / app_console / app_process 七个
    mixin 里，这里只做组装 + 构造界面骨架。
    """

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
        self._log_tailers = {}  # V2.1.0: reattached server log tail timers
        self.auto_restart_count = {}  # 记录自动重启次数
        self.auto_backup_timers = {}  # 定时备份定时器  
        self._size_cache = {}  # ★ 缓存文件夹大小，不要写进 servers.json  
        self._port_cache = {}  # ★ 缓存端口，防止悬停时疯狂读盘
        self._proc_buffers = {}  # ★ 日志缓冲区，防止多字节中文截断
        # ★ V0.5.0：实时监控 + 每日定时重启的状态
        self._ping_text = {}      # key -> "在线: 3/20 | 延迟: 2ms"
        self._mon_extra = {}      # ★ V2.0.1: key -> (cpu_pct, mem_mb) 标题监测
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
        # ★ V2.3.6：清一下上次退出时留下的"存活 QProcess"引用 ——
        #   那些进程早就没了，只是别让旧对象一直占着内存。
        try:
            from .app_process import ProcessMixin
            del ProcessMixin._LIVE_PROCS[:]
        except Exception:
            pass
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
        # ★ V2.3.6：自动接管上次仍在运行的服务器。
        #   以前固定在启动后 500ms 调 —— 那时目录扫描（异步线程）
        #   往往还没跑完，self.entries 还是空的，一个服都接管不了。
        #   现在主路径改成"扫描完成后再接管"（见 _on_scan_done），
        #   这里只留一个兜底定时器。
        QTimer.singleShot(2500, self._detect_running_servers)
        # ★ V2.3.8：自动模式下，启动后后台测一次两条通道。
        #   挂 VPN 和直连的最优源可能完全相反，不能拿一次开发机
        #   测速写死；也不能沿用上次会话的旧结果。
        QTimer.singleShot(3500, self._auto_probe_sources)

    # ---------------- UI ----------------
    def _auto_probe_sources(self):
        """★ V2.3.8：后台按当前网络测两条通道，让「自动」名副其实。

        只发 4 个很小的请求（API 查一个项目 + CDN 下一张 35KB 图标），
        结果只留在内存里，换网络 / 重开程序都会重新测。
        手动锁死某个源的用户不做这一步，避免无谓请求。
        """
        try:
            from .core import sources as _src
            if _src.get_mirror() != "auto" and _src.get_download() != "auto":
                return
            from .dialogs import Worker
            from .dialog_workers import _track

            def job(on_log=None):
                a = _src.probe_all_latency(timeout=6)
                d = _src.probe_cdn_all_latency(timeout=6)
                return (a, d)

            w = _track(self, Worker(job, {}, self))
            w.start()
        except Exception:
            pass

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

        # ★ V2.0.1：『选择目录 / 重新扫描 / 统一搬迁』挪到顶部总目录行，
        #   工具栏不再重复；导入地图/整合包/统一搬迁合并为「导入」。
        self.act_start = act("启动", self.do_start, "启动选中服务器")
        self.act_stop = act("停止", self.do_stop, "停止选中服务器")
        self.act_stop.setEnabled(False)
        self.act_stop_backup = act(
            "停止并备份", self.do_stop_backup,
            "停止选中服务器，退出后自动备份存档")
        self.act_stop_backup.setEnabled(False)
        # ★ V2.0.13：为正在运行的服务器调出独立控制台窗口
        self.act_float_console = act(
            "调出控制台", self.do_open_float_console,
            "为正在运行的服务器打开独立控制台窗口\n"
            "关闭窗口不会停止服务器，输入指令直接发给服务器")
        self.act_float_console.setEnabled(False)
        tb.addSeparator()
        act("初始化服务端", self.do_create, "为选中项下载核心/模组/生成配置")
        act("服务器配置", self.do_config,
            "server.properties / 核心 / 模组 / 备份 / 启动设置，都在这里")
        act("分析日志", self.do_analyze,
            "自动分析日志里的报错，离线规则匹配，不联网\n"
            "（导出报错诊断已并入此功能）")
        act("删除服务器", self.do_delete_server,
            "删掉整个服务器文件夹（含存档）")
        tb.addSeparator()
        act("下载源", self.do_sources,
            "镜像 / 官方源 / CF Key / 测速（全局生效，模组与插件下载都用它）")
        act("显示设置", self.do_display, "调整列表字号与行高")
        act("检查更新", self.do_check_update,
            "检查 GitHub 上有没有新版本（后台线程，不卡界面）")
        tb.addSeparator()
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
        # ★ V2.0.1：总目录行 = 选择... / 刷新 / 导入（原重新扫描改名，
        #   统一搬迁+导入地图+导入整合包合并为「导入」下拉）
        b_ref = QPushButton("刷新")
        b_ref.setToolTip("重新扫描总目录，识别服务器")
        b_ref.clicked.connect(self.do_scan)
        th.addWidget(b_ref)
        b_imp = QPushButton("导入")
        b_imp.setToolTip(
            "导入地图 / 导入整合包 / 统一搬迁（三个入口合并）")
        from PySide6.QtWidgets import QMenu
        m_imp = QMenu(self)
        a_map = m_imp.addAction("导入地图...")
        a_map.setToolTip("把一个地图文件夹加入管理")
        a_map.triggered.connect(self.do_import)
        a_pack = m_imp.addAction("导入整合包...")
        a_pack.setToolTip(
            "导入 Modrinth/CurseForge 整合包 zip：\n"
            "自动识别类型、装对应版本核心、并发下载全部模组")
        a_pack.triggered.connect(self.do_import_modpack)
        a_mig = m_imp.addAction("统一搬迁...")
        a_mig.setToolTip("把分散的地图文件夹统一搬迁到新总目录")
        a_mig.triggered.connect(self.do_migrate)
        b_imp.setMenu(m_imp)
        th.addWidget(b_imp)

        # 左：列表（图标视图 / 表格视图，可在显示设置里切换）
        self.view_mode = self.disp.get("view", "icon")
        self.sort_mode = self.disp.get("sort", "version")

        # --- 图标视图 ---
        self.icon_view = QListWidget()
        self.icon_view.setViewMode(QListWidget.IconMode)
        self.icon_view.setMovement(QListWidget.Static)
        self.icon_view.setResizeMode(QListWidget.Adjust)
        self.icon_view.setWrapping(True)
        self.icon_view.setSpacing(10)
        self.icon_view.setSelectionMode(
            QAbstractItemView.ExtendedSelection)
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
        # 排序栏（★ v2.3.2：左边加了服务器搜索框）
        sh = QHBoxLayout()
        sh.setSpacing(8)
        self.filter_text = ""
        sh.addWidget(QLabel("搜索:"))
        self.ed_filter = QLineEdit()
        self.ed_filter.setPlaceholderText("名称 / 版本 / 加载器（Ctrl+F）")
        self.ed_filter.setClearButtonEnabled(True)
        self.ed_filter.setMaximumWidth(230)
        self.ed_filter.textChanged.connect(self._on_filter_changed)
        self.ed_filter.installEventFilter(self)
        sh.addWidget(self.ed_filter)
        # ★ V2.3.4：排序控件紧跟搜索框。
        #   以前这里先 addStretch(1) 再 addWidget(cb_sort, 1)，
        #   弹簧把排序下拉框顶到了最右边，"排序:"文字和下拉框之间
        #   空出一大片，看着像"排序跟搜索框离得很远"。现在弹簧挪到最后。
        sh.addSpacing(12)
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
        sh.addWidget(self.cb_sort)
        # 拖动排序是没有图标提示的隐藏操作，写一行说明，免得发现不了
        sh.addStretch(1)
        self.lb_draghint = QLabel("")
        self.lb_draghint.setStyleSheet("color:%s; font-size:9pt;"
                                  % PAL.MUTED)
        sh.addWidget(self.lb_draghint)
        lv.addLayout(sh)
        # ★ Ctrl+F 聚焦搜索框（控制台里按 Ctrl+F 是搜日志，见 Console）
        try:
            from PySide6.QtGui import QShortcut, QKeySequence
            self._sc_filter = QShortcut(QKeySequence.Find, self)
            self._sc_filter.activated.connect(self._focus_filter)
            # ★ F5 = 重新扫描（刷新列表的习惯键）
            self._sc_rescan = QShortcut(QKeySequence.Refresh, self)
            self._sc_rescan.activated.connect(self.do_scan)
        except Exception:
            pass
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
        # ★ V2.0.13：独立调出的控制台窗口（key → FloatConsole）
        self.float_consoles = {}

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
        # ★ V2.0.1.1：窗口真正显示后再强制一次输出区深底 ——
        #   viewport 的深色样式只有在控件已 show 后重设才绝对生效。
        try:
            w._force_console_dark()
            app.processEvents()
        except Exception:
            pass
        sys.exit(app.exec())
    except Exception as e:
        logging.exception("程序发生未捕获的致命异常：")
        raise


if __name__ == "__main__":
    main()
