# -*- coding: utf-8 -*-
"""创建向导：JavaPick（Java 选择）与 CreateDialog（初始化服务端）。

从 dialogs.py 拆分而来，只保留本模块用到的导入。
"""

import os
import re

from PySide6.QtGui import QDesktopServices
from PySide6.QtCore import Qt, QTimer, QUrl
from PySide6.QtWidgets import (
    QDialog, QVBoxLayout, QHBoxLayout, QFormLayout, QLabel, QLineEdit,
    QComboBox, QPushButton, QCheckBox, QFileDialog, QMessageBox, QProgressBar,
    QDialogButtonBox, QGroupBox, QScrollArea, QDoubleSpinBox, QFrame, QWidget,
    QCompleter)
from ..core import versions, java as javacore, install, builder, props
from ..dialog_workers import (
    _StopMixin, Worker, LoaderVerWorker, _track, _stop_threads)
from .common import (
    LogView, _th, _on_bg, _muted, _themed, _mem_mb_of, _apply_mem_unit)
from .mods import ModBrowseDialog
from ..widgets import app_icon


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
        # ★ V1.9.10：手动点『重新扫描』才强制全盘重扫；平时用缓存
        return javacore.find_all(force=True)

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
            from ..core import scanner as _sc
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
            from ..core import versions as _V
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
            from ..core import theme as _th
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
        from ..core import versions as _V
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
                    if re.search(k, low):
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
        from .. import core
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
        from ..core import install
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


