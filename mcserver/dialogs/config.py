# -*- coding: utf-8 -*-
"""服务器配置：ServerConfigDialog（server.properties / 启动参数 / 模组页签）。

从 dialogs.py 拆分而来，只保留本模块用到的导入。
"""

import os

from PySide6.QtGui import QColor
from PySide6.QtCore import Qt, QTime
from PySide6.QtWidgets import (
    QDialog, QVBoxLayout, QHBoxLayout, QFormLayout, QLabel, QLineEdit,
    QComboBox, QPushButton, QCheckBox, QSpinBox, QFileDialog, QMessageBox,
    QProgressBar, QDialogButtonBox, QGroupBox, QListWidget, QAbstractItemView,
    QTabWidget, QScrollArea, QDoubleSpinBox, QWidget, QApplication,
    QTableWidget, QTableWidgetItem, QHeaderView, QTimeEdit)
from ..core import (
    versions, java as javacore, install, builder, props, backup, modman,
    loaderup)
from ..dialog_workers import Worker, _track, _stop_threads
from .common import (
    LogView, _on_bg, _muted, _preset_matches, _themed, _open_in_explorer,
    _yaml_path_for, _yaml_get, _yaml_set, _yaml_patch_preserve, _mem_mb_of,
    _set_mem_mb, _apply_mem_unit, YAML_ITEMS)
from .create import JavaPick
from .mods import ModBrowseDialog, ModDialog
from .server_ops import LoaderUpdateDialog, RemoveCoreDialog
from .backup import BackupDialog
from ..widgets import app_icon


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
        # ★ V1.9.10：刷新 —— 重新检测当前核心/加载器
        hbf = QHBoxLayout()
        self.btn_core_refresh = QPushButton("刷新")
        self.btn_core_refresh.setToolTip(
            "重新检测服务器目录里的核心 jar 和加载器类型")
        self.btn_core_refresh.clicked.connect(self._core_refresh)
        hbf.addWidget(self.btn_core_refresh)
        hbf.addStretch(1)
        f1.addRow("", hbf)
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
        # ★ V1.9.10：浏览 —— 手动指定已下载好的核心 jar
        self.btn_core_browse = QPushButton("浏览...")
        self.btn_core_browse.setToolTip(
            "手动选择一个已下载好的核心 jar（如 fabric.jar / paper.jar）直接替换")
        self.btn_core_browse.clicked.connect(self._core_browse)
        hb.addWidget(self.btn_core_browse)
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

    # ---------- V1.9.10：核心页 加载器 刷新/浏览 ----------
    def _core_refresh(self):
        """刷新：重新检测服务器目录里的核心 jar，并推断加载器类型。"""
        sd = self.entry.get("server_dir") or self.entry["folder"]
        cj = install.find_core(sd)
        self.lbl_core.setText(
            os.path.basename(cj) if cj else "未识别到核心")
        loader = self._guess_loader_from_core(cj)
        if loader:
            self.lbl_loader.setText(loader)
            try:
                i = self.cb_loader.findText(loader)
                if i >= 0:
                    self.cb_loader.setCurrentIndex(i)
            except Exception:
                pass
        else:
            self.lbl_loader.setText("未知")

    def _guess_loader_from_core(self, cj):
        """从核心 jar 文件名推断加载器类型（fabric/paper/forge/neoforge/vanilla）。"""
        if not cj:
            return None
        n = os.path.basename(cj).lower()
        for ld in ("fabric", "paper", "forge", "neoforge"):
            if ld in n:
                return ld
        if n.startswith("minecraft_server") or n == "server.jar":
            return "vanilla"
        return None

    def _core_browse(self):
        """浏览：手动选择一个已下载好的核心 jar 直接替换。"""
        sd = self.entry.get("server_dir") or self.entry["folder"]
        filt = ("JAR 文件 (*.jar);;所有文件 (*)" if os.name == "nt"
                else "All Files (*)")
        path, _ = QFileDialog.getOpenFileName(
            self, "选择服务端核心 jar", "", filt)
        if not path:
            return
        try:
            import shutil as _sh
            cj = install.find_core(sd)
            old = os.path.basename(cj) if cj else None
            r = QMessageBox.question(
                self, "更换核心",
                "将用你选中的 jar 替换当前核心%s。\n\n"
                "选中的文件会复制进服务器目录，源文件保留。\n"
                "存档、模组、server.properties 不受影响。\n\n"
                "继续吗？"
                % ("（%s）" % old if old else ""),
                QMessageBox.Yes | QMessageBox.No, QMessageBox.No)
            if r != QMessageBox.Yes:
                return
            if cj:
                try:
                    os.remove(cj)
                except Exception:
                    _force_drop = getattr(install, "_force_drop", None)
                    if _force_drop:
                        _force_drop(cj, lambda m: None)
                    else:
                        QMessageBox.warning(
                            self, "删除旧核心失败",
                            "旧核心文件被占用无法删除，请关闭相关程序后重试。")
                        return
            dst = os.path.join(sd, os.path.basename(path))
            _sh.copy2(path, dst)
            self.lbl_core.setText(os.path.basename(dst))
            ld = self._guess_loader_from_core(dst)
            self.lbl_loader.setText(ld or "未知")
            if ld:
                try:
                    i = self.cb_loader.findText(ld)
                    if i >= 0:
                        self.cb_loader.setCurrentIndex(i)
                except Exception:
                    pass
            self._need_rescan = True
            QMessageBox.information(
                self, "核心已更换",
                "已用 %s 替换核心。\n可点『启动』测试；"
                "如启动报错请检查版本与加载器是否匹配。" % os.path.basename(dst))
        except Exception as ex:
            QMessageBox.critical(self, "更换核心失败", repr(ex))

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
                from ..core import javacore
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

        self.mod_table = QTableWidget(0, 5)
        self.mod_table.setHorizontalHeaderLabels(
            ["状态", "名称", "版本", "最新版本", "文件"])
        self.mod_table.verticalHeader().setVisible(False)
        self.mod_table.setEditTriggers(
            QAbstractItemView.NoEditTriggers)
        self.mod_table.setSelectionBehavior(
            QAbstractItemView.SelectRows)
        hh = self.mod_table.horizontalHeader()
        for i, m in enumerate((80, 200, 140, 140, 1)):
            hh.setSectionResizeMode(
                i, QHeaderView.ResizeToContents if m != 1
                else QHeaderView.Stretch)
        v.addWidget(self.mod_table, 1)

        # ★ V2.0.9：检查更新结果缓存（path -> res）
        self._mod_results = {}

        for m in self.mod_mods:
            r = self.mod_table.rowCount()
            self.mod_table.insertRow(r)
            self.mod_table.setItem(r, 0, QTableWidgetItem(
                "启用" if m.get("enabled", True) else "禁用"))
            self.mod_table.setItem(r, 1,
                                   QTableWidgetItem(m.get("name", "?")))
            self.mod_table.setItem(r, 2,
                                   QTableWidgetItem(m.get("version", "")))
            self.mod_table.setItem(r, 3, QTableWidgetItem("-"))
            self.mod_table.setItem(r, 4, QTableWidgetItem(
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
        # ★ V2.0.6：点"完整管理"直接打开浏览模组大窗，
        #   不再先弹"模组管理"小窗、再手动点浏览（用户反馈多一步）；
        #   ★ V2.0.9：改名"+添加模组"（与原 hb2 的＋添加模组合并，删旧按钮）
        b_full = QPushButton("＋ 添加模组")
        b_full.setToolTip(
            "打开模组浏览（推荐 + 全库搜索 + 勾选安装）；\n"
            "已装模组的启用/禁用在这里下方操作，检查更新看『最新版本』列")
        b_full.clicked.connect(self._add_mod_here)
        hb.addWidget(b_full)
        v.addLayout(hb)

        # ---- 第二行：更新 ----
        hb2 = QHBoxLayout()
        b_up = QPushButton("↑ 更新加载器")
        b_up.setToolTip("只换核心文件，存档/模组/配置都不动")
        b_up.clicked.connect(self._update_loader_here)
        hb2.addWidget(b_up)
        b_api = QPushButton("↑ 更新 Fabric API")
        b_api.setToolTip("几乎所有模组的前置，版本不对会导致"
                         "所有模组一起加载失败")
        b_api.clicked.connect(self._update_fapi_here)
        hb2.addWidget(b_api)
        # ★ V2.0.9：已装模组管理的"检查更新/下载更新"移到这里
        b_chk = QPushButton("检查更新（选中）")
        b_chk.setToolTip("检查表格里选中的已装模组有没有新版本（结果写『最新版本』列）")
        b_chk.clicked.connect(lambda: self._check_mod_updates_here(True))
        hb2.addWidget(b_chk)
        b_chkall = QPushButton("一键检查全部更新")
        b_chkall.clicked.connect(lambda: self._check_mod_updates_here(False))
        hb2.addWidget(b_chkall)
        b_dlupd = QPushButton("下载选中项的更新")
        b_dlupd.setToolTip("打开表格里选中模组的更新下载页（需先检查过更新）")
        b_dlupd.clicked.connect(self._download_mod_update_here)
        hb2.addWidget(b_dlupd)
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
        cfg = {
            "storage-method": "h2",
            "server": "global",
            "default-groups": ["default"],
        }
        p = self._luckperms_cfg_path()
        try:
            import yaml
            if os.path.isfile(p):
                with open(p, "r", encoding="utf-8",
                          errors="replace") as f:
                    d = yaml.safe_load(f.read()) or {}
                # ★ 修：以前读完 d 就丢掉、永远返回默认值，
                #   页签里显示的从来不是真实配置。现在合并回去。
                if isinstance(d, dict):
                    cfg.update(d)
        except Exception:
            pass
        return cfg, p

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
            # ★ V2.0.9：第 4 列显示缓存的最新版本结果（若有）
            res = self._mod_results.get(m.get("path")) or {}
            latest = res.get("latest") or res.get("cur") or "-"
            if res.get("status") == "update":
                latest = "%s ←可更新" % latest
            self.mod_table.setItem(r, 3, QTableWidgetItem(latest))
            self.mod_table.setItem(r, 4, QTableWidgetItem(
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

    # ---------- ★ V2.0.9：已装模组"检查更新/下载更新"搬到配置窗 ----------
    def _check_mod_updates_here(self, only_sel=True):
        """检查选中/全部已装模组的更新，结果写『最新版本』列。"""
        loader = (self.entry.get("loader") or "").lower()
        mc = (self.entry.get("version") or "").strip()
        if not mc or loader in ("", "unknown", "paper", "vanilla"):
            QMessageBox.information(
                self, "检查不了",
                "需要有效的 MC 版本 + Fabric/Forge/NeoForge 加载器\n"
                "才能查更新。当前：%s + %s" % (mc or "?", loader or "?"))
            return
        if only_sel:
            r = self.mod_table.currentRow()
            if r < 0 or r >= len(self.mod_mods):
                QMessageBox.information(
                    self, "提示", "先在表格里选中一个已装模组")
                return
            mods = [self.mod_mods[r]]
        else:
            mods = list(self.mod_mods)
        if not mods:
            QMessageBox.information(self, "提示", "mods 目录里没有模组")
            return
        self._log("[更新检查] 开始检查 %d 个模组（%s + %s）..."
                  % (len(mods), mc, loader))
        for m in mods:
            r = self._row_of_mod_here(m)
            if r is not None:
                self.mod_table.setItem(r, 3, QTableWidgetItem("查询中..."))
            QApplication.processEvents()
            try:
                res = modman.check_update(
                    m, mc, loader, on_log=lambda x: None)
            except Exception as e:
                res = {"status": "error", "err": repr(e)}
            self._mod_results[m.get("path")] = res
            status = res.get("status")
            latest = res.get("latest") or res.get("cur") or ""
            url = res.get("url") or ""
            if r is not None:
                if status == "update":
                    col = QTableWidgetItem("%s ←可更新" % latest)
                    col.setForeground(QColor(_on_bg("#ffd166")))
                    self.mod_table.setItem(r, 3, col)
                elif status == "latest":
                    col = QTableWidgetItem("%s [最新]" % latest)
                    col.setForeground(QColor(_on_bg("#06d6a0")))
                    self.mod_table.setItem(r, 3, col)
                else:
                    self.mod_table.setItem(
                        r, 3, QTableWidgetItem(
                            (res.get("err") or "查询失败")[:20]))
            self._log("[更新检查] %s -> %s（%s）%s"
                      % (m.get("name") or "?", latest or "?",
                         status or "?",
                         ("下载: %s" % url)
                         if url and status == "update" else ""))
        self._log("[更新检查] 完成")

    def _row_of_mod_here(self, m):
        for i in range(self.mod_table.rowCount()):
            it = self.mod_table.item(i, 4)
            if it and it.text() == os.path.basename(m.get("path", "")):
                return i
        return None

    def _download_mod_update_here(self):
        """打开表格里选中模组的更新下载页（需先检查过更新）。"""
        r = self.mod_table.currentRow()
        if r < 0 or r >= len(self.mod_mods):
            QMessageBox.information(self, "提示", "先选中一个已装模组")
            return
        m = self.mod_mods[r]
        res = self._mod_results.get(m.get("path")) or {}
        url = res.get("url") or ""
        if not url:
            QMessageBox.information(
                self, "提示",
                "这个模组还没检查过更新，先点『检查更新（选中）』。")
            return
        if res.get("status") == "latest":
            rr = QMessageBox.question(
                self, "已是最新",
                "已是 %s。\n仍打开它的下载页吗？"
                % (res.get("latest") or ""),
                QMessageBox.Yes | QMessageBox.No, QMessageBox.No)
            if rr != QMessageBox.Yes:
                return
        import webbrowser
        try:
            webbrowser.open(url)
            self._log("已打开: %s" % url)
        except Exception as e:
            QMessageBox.warning(
                self, "打不开",
                "没法自动打开浏览器：%r\n\n链接：%s" % (e, url))


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
        from ..core import modconf
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

            # ★ V2.1.0：先读回已生成配置（下拉自动选档要参考它）
            rd = None
            try:
                rd = spec["read"](sd)
            except Exception:
                rd = None

            # 预设
            cb = QComboBox()
            for k, p in spec["presets"].items():
                cb.addItem(p["label"], k)
            # ★ V1.9.6：默认选中"均衡（推荐）"（若存在），
            #   与下方初始渲染基准 balanced 保持一致；
            #   不加这个，下拉显示"保守"但数值却是均衡档，看着像没切换。
            # ★ V2.1.0：读回已生成的配置后，按数值自动匹配预设档位 ——
            #   否则服务器保存过激进档后重开页面，下拉仍显示"均衡"
            #   但数值是激进的，看起来就像"保守/激进数值反了"。
            cb.blockSignals(True)
            try:
                j = -1
                if rd:
                    for k, p in spec["presets"].items():
                        if _preset_matches(p, rd, spec["key"]):
                            j = cb.findData(k)
                            break
                if j < 0:
                    j = cb.findData("balanced")
                if j < 0:
                    j = cb.findData("default")
                if j >= 0:
                    cb.setCurrentIndex(j)
            finally:
                cb.blockSignals(False)
            f.addRow("预设:", cb)
            self._mc_widgets["preset"] = cb
            # ★ V2.1.1：ServerCore 预设旁加方向提示（数值越大越保守）
            if spec["key"] != "simple-voice-chat":
                lbl_hint = QLabel("数值越大越接近原版（保守），越小优化越狠（激进）")
                lbl_hint.setStyleSheet(_muted())
                lbl_hint.setWordWrap(True)
                f.addRow("", lbl_hint)

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
                # ★ V2.0.14：voice_host 语音主机地址
                host_edit = QLineEdit()
                host_edit.setText(str(d.get("voice_host", "0.0.0.0")))
                host_edit.setPlaceholderText("0.0.0.0")
                self._mc_widgets["svc_host"] = host_edit
                f.addRow("voice_host 语音主机地址:", host_edit)
                lbl_host = QLabel(
                    "语音服务绑定的主机地址。0.0.0.0 = 监听所有网卡（默认）；\n"
                    "填具体 IP（如 192.168.1.100）可限制只监听该网卡。\n"
                    "云服务器多网卡时按需指定，一般保持默认即可。")
                lbl_host.setStyleSheet(_muted())
                lbl_host.setWordWrap(True)
                f.addRow("", lbl_host)
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
                    if g("svc_host"):
                        g("svc_host").setText(
                            str(p.get("voice_host", "0.0.0.0")))
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
        from ..core import modconf
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
                "voice_host": str(g("svc_host").text()).strip()
                if g("svc_host") else "0.0.0.0",
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
            from ..core import modman as _mm
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
        from ..core import install as _inst
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


