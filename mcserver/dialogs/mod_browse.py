# -*- coding: utf-8 -*-
"""ModBrowseDialog + ModDialog：PCL2 风格卡片浏览 / 已装模组管理。

从 dialogs.py 拆分而来。
"""

import os

from PySide6.QtGui import QColor, QDesktopServices, QPixmap
from PySide6.QtCore import Qt, QTimer, QUrl, QSize
from PySide6.QtWidgets import (
    QDialog, QVBoxLayout, QHBoxLayout, QFormLayout, QLabel, QLineEdit,
    QComboBox, QPushButton, QCheckBox, QTextEdit, QMessageBox,
    QDialogButtonBox, QGroupBox, QListWidget, QListWidgetItem,
    QAbstractItemView, QTabWidget, QWidget, QApplication, QTableWidget,
    QTableWidgetItem, QHeaderView, QInputDialog, QListView, QStackedLayout)
from ..core import versions, install, modman, sources
from ..core import net
from ..dialog_workers import (
    Worker, IconWorker, _track, _stop_threads, _make_letter_pixmap,
    _make_placeholder_pixmap, _global_show_icons)
from .common import (
    LogView, _fg, _on_bg, _muted, _themed, _inst_hit, _fmt_num,
    _open_in_explorer, _fetch_icon_bytes, _RE_BRACKET_TAG, _RE_BRACKET_STRIP,
    _RE_URL)
from .base import BaseDialog
from .server_ops import DiagDialog
from ..core.palette import PAL, TAG_COLORS
from .mod_source import SourceDialog
from .mod_search import ModrinthDialog
from .mod_pick import _link_btn_css


class ModBrowseDialog(BaseDialog):
    """PCL2 风格模组浏览：卡片视图 + 勾选安装。

    两种模式：
      · select_only=False（默认，配置页用）：
        勾选后点「安装选中」，直接并发下载到服务器 mods 目录。
      · select_only=True（初始化向导用）：
        只做选择，点「确定选择」返回勾选的 slug 列表，
        实际下载由初始化流程统一执行（有进度/失败重试）。
    """

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
        # ★ V2.0.1：清掉历史写坏的 None 结论（无结论不缓存，
        #   下次打开重新探测，避免"适配检测中…"被固化 24 小时）
        self._compat = {k: v for k, v in self._compat.items()
                        if v and v[0] is not None}
        self._compat_loading = set()  # 正在探测的 slug（防重复）
        self._compat_fail = {}        # ★ V2.0.1: slug -> why（探测失败/无结论，内存）
        # ★ V2.0.8：推荐页跨页勾选状态（slug -> bool）——翻页不丢勾选，
        #   安装时合并全部页的勾选
        self._builtin_checked = {}
        # ★ V2.0.9：全库搜索页跨页勾选状态（slug -> bool）
        self._lib_checked = {}
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
        hh = QHBoxLayout()
        self.lbl_view_hint = QLabel("")
        self.lbl_view_hint.setStyleSheet(_muted())
        hh.addWidget(self.lbl_view_hint, 1)
        # ★ V2.3.4：版本隔离开关。
        #   后台会按当前 MC 版本逐个探测推荐模组是否适配，
        #   默认只显示适配本版本的 —— 以前不适配的只是标红字，
        #   照样占着列表（1.16 的服也满屏 1.21 才有的模组）。
        self.chk_only_fit = QCheckBox("只显示适配本版本的")
        self.chk_only_fit.setChecked(True)
        self.chk_only_fit.setToolTip(
            "后台按当前 MC 版本逐个探测推荐模组是否适配；\n"
            "不适配的直接从列表去掉（取消勾选可看全部）。")
        self.chk_only_fit.toggled.connect(self._on_only_fit_toggled)
        hh.addWidget(self.chk_only_fit)
        vr.addLayout(hh)
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
            "color:%s; font-size:16px; background:transparent;" % PAL.MUTED)
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
        # ★ V2.0.9：已装模组管理入口删除 —— 启用/禁用在配置窗模组页，
        #   检查更新/下载更新也搬到配置窗模组页按钮行
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
    def _icon_url_for(self, slug, source="modrinth"):
        """内置清单条目的图标地址。

        ★ V2.3.4：以前内置清单的 icon_url 恒为 None，
          卡片只能画首字母色块（mcserver/icons 目录是空的，
          本地图几乎永远命中不了）—— 用户看到的全是首字母图标。
          Modrinth 的 CDN 支持按 slug 取图，这里拼规范地址，
          由 IconWorker 异步下载，回来替换首字母占位。
          ★ CurseForge 发布的项目也照样给 Modrinth 地址试一下：
            大多 CF 项目在 Modrinth 有镜像页（能出图），
            没有的下载失败就自动保留首字母色块，不报错。
        """
        if not slug:
            return ""
        return "https://cdn.modrinth.com/data/%s/icon.png" % slug

    def _is_incompatible(self, slug):
        """该 slug 是否有"明确不适配"的结论。"""
        if not slug:
            return False
        ck = self._compat.get(self._compat_key(slug))
        return bool(ck and ck[0] is False)

    def _filter_builtin_hits(self):
        """★ V2.3.4：按适配结论过滤内置清单（= 版本隔离）。

        以前不适配的模组只是标红字、照样占着列表，
        1.16 的服也能看到一屏只支持 1.21 的模组 —— 版本隔离形同虚设。
        现在默认（勾着"只显示适配本版本的"）不适配的直接从列表去掉。
        网络失败（无结论）的保留 —— 不能因为一次探测失败就藏起来。
        """
        all_hits = getattr(self, "_builtin_all", None)
        if all_hits is None:
            return
        if not self.chk_only_fit.isChecked():
            self.builtin_hits = list(all_hits)
        else:
            self.builtin_hits = [
                h for h in all_hits
                if not self._is_incompatible(h.get("slug") or "")]
        self.builtin_total = len(self.builtin_hits)
        if self.builtin_offset > max(0, self.builtin_total - 1):
            self.builtin_offset = 0

    def _on_only_fit_toggled(self, on):
        """切换"只显示适配本版本的"开关 → 重新过滤并刷新列表。"""
        try:
            self._filter_builtin_hits()
            self._fill_builtin()
        except Exception:
            pass

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
                    "req": m.get("req") or [],
                    # ★ V2.3.4：内置清单也带图标地址（原来恒为 None，
                    #   卡片只能画首字母色块，用户看到的全是首字母图标）
                    "icon_url": self._icon_url_for(
                        m["slug"], m.get("source") or "modrinth"),
                    # ★ V1.9.2：来源站（Cupboard 生态 = curseforge，
                    #   其余默认 modrinth）→ 索引按钮按来源先显示
                    "source": m.get("source") or "modrinth",
                    # ★ V1.9.5：GitHub 来源插件（MultiLogin）→ 适配探测走
                    #   GitHub Releases 平台过滤，不进 Modrinth/CF 查询
                    "repo": m.get("repo") or "",
                    # ★ V2.0.5：透传分支配置（ModernFix 1.21.5+ 社区版）
                    "branch": m.get("branch") or (),
                })
        else:
            lst = install.mod_list_for(self.loader, self.mc_version)
            try:
                from ..core import install as _inst
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
                    "req": m.get("req") or [],
                    # ★ V2.3.4：内置清单也带图标地址（原来恒为 None，
                    #   卡片只能画首字母色块，用户看到的全是首字母图标）
                    "icon_url": self._icon_url_for(
                        m["slug"], m.get("source") or "modrinth"),
                    # ★ V1.9.2：来源站（Cupboard 生态 = curseforge，
                    #   其余默认 modrinth）→ 索引按钮按来源先显示
                    "source": m.get("source") or "modrinth",
                    # ★ V1.9.5：透传 repo（当前模组清单没有 GitHub 项，
                    #   留字段备用，插件清单已用）
                    "repo": m.get("repo") or "",
                    # ★ V2.0.5：透传分支配置（ModernFix 1.21.5+ 社区版）
                    "branch": m.get("branch") or (),
                })
        self.builtin_hits = hits
        self.builtin_total = len(hits)
        self.builtin_offset = 0
        self.builtin_mode = True
        # ★ V2.3.4：留一份全量，版本隔离过滤时随时能恢复
        self._builtin_all = list(hits)
        self._filter_builtin_hits()
        self.lbl_view_hint.setText(
            "本地推荐：已按 %s + %s 过滤版本，剔除纯客户端组件；"
            "标注『无适配』的模组 = 作者暂未支持该版本"
            % (self.mc_version or "?", self.loader or "?"))
        self._fill_builtin()
        # ★ V2.0.7：适配探测延迟到窗口显示后再启动（QTimer 单次 0ms），
        #   避免构造期后台线程与首帧绘制争抢导致"空白小窗闪现"。
        from PySide6.QtCore import QTimer as _QT
        _QT.singleShot(0, self._probe_compat)

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
            # ★ V2.3.4：内置清单也异步拉真图标（首字母色块只是占位）
            _u = h.get("icon_url") or ""
            if _u:
                self._spawn_icon_load([_u])
            it._chk = chk
            # ★ V2.0.8：跨页勾选 —— 翻页重建时恢复勾选，并写回状态字典
            chk.blockSignals(True)
            if slug in self._builtin_checked:
                chk.setChecked(bool(self._builtin_checked[slug]))
            chk.blockSignals(False)
            chk.toggled.connect(
                lambda on, s=slug: self._on_builtin_toggled(s, on))
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

    def _on_builtin_toggled(self, slug, on):
        """★ V2.0.8：推荐页勾选状态写回字典（跨页保留）。"""
        try:
            self._builtin_checked[slug] = bool(on)
        except Exception:
            pass
        self._sync_count()

    def _on_lib_toggled(self, slug, on):
        """★ V2.0.9：全库搜索页勾选状态写回字典（跨页保留）。"""
        try:
            self._lib_checked[slug] = bool(on)
        except Exception:
            pass
        self._sync_count()

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
        # ★ V2.0.9：换关键词/换源后旧勾选失效，清空跨页勾选
        self._lib_checked.clear()
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
        """失败/超时占位：列表里放一条不可勾选的失败项，绝不白屏。

        ★ V2.3.4：搜索失败绝不弹窗（弹窗会挡住列表、还容易被当成
          "每次搜索都弹镜像源选择"）。失败原因直接写在列表里；
          如果是源的问题（缺 CF Key / 镜像不通），额外提示一句
          "点右上角『镜像源设置』换源"，给用户明确出口。
        """
        try:
            text = str(msg or "")
            low = text.lower()
            src_hint = ("key" in low or "镜像" in text or "源" in text
                        or "源不可用" in text or "mirror" in low)
            if src_hint:
                text += ("\n（如果和下载源有关，点右上角『镜像源设置』"
                         "换源后重试）")
            self.list_lib.blockSignals(True)
            self.list_lib.clear()
            it = QListWidgetItem(text)
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
        """遍历正在显示的卡片，把 icon_url 匹配的占位替换成真图。

        ★ V2.3.4：推荐安装 Tab 也要扫 —— 以前只扫全库 Tab，
          内置清单下载到的图没人接收，卡片一直停在首字母色块。
        """
        for lst in (self.list_lib, self.list_builtin):
            for i in range(lst.count()):
                it = lst.item(i)
                w = lst.itemWidget(it)
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
            # ★ V2.0.9：全库搜索跨页勾选 —— 翻页重建时恢复，并写回字典
            chk.blockSignals(True)
            if slug in self._lib_checked:
                chk.setChecked(bool(self._lib_checked[slug]))
            chk.blockSignals(False)
            chk.toggled.connect(
                lambda on, s=slug: self._on_lib_toggled(s, on))
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
    #   配色统一放在 core/palette.py 的 TAG_COLORS（避免两处维护）。
    _TAG_COLORS = TAG_COLORS

    def _make_card(self, h, icon_pm, builtin):
        """一行卡片：左侧勾选框 +（可选）48x48 图标，右侧名称/简介/标签/百科按钮。

        ★ V1.8.4：无图标时复选框+文字顶格左对齐（纯文字紧凑排版）。
        ★ V1.8.10：icon_pm 非 None 时在勾选框右侧插入图标；
          带 icon_url 的卡片记录 _icon_url/_icon_lbl，供异步下载完成后替换。
        """
        slug = h.get("slug") or ""
        title = h.get("title") or slug
        # ★ V2.0.4：分支项目（ModernFix 1.21.5+ 社区版）——
        #   Modrinth 链接/存在性探测指向实际安装的项目
        #   （原版在 1.21.5+ 没有适配版本，链接过去看不到东西）。
        _eff = slug
        try:
            from ..core import install as _inst
            _eff = _inst.effective_slug(h, self.mc_version) or slug
        except Exception:
            pass
        _raw_desc = (h.get("description") or "").replace("\n", " ")
        # ★ V1.8.2：提取 desc 里的 [标签] 用于彩色渲染，简介正文去掉标签
        _tags_desc = _RE_BRACKET_TAG.findall(_raw_desc)
        desc = _RE_BRACKET_STRIP.sub(" ", _raw_desc).strip()
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
        # ★ V2.0.1：卡片存原始数据，供适配探测落地后原地重建标签行
        card._meta = h
        card._builtin = builtin
        # ★ V1.6.2：卡片底色跟随主题（深色主题=深卡片，浅色主题=浅卡片），
        #   标题文字用主题字体色，彻底解决"黑字黑底看不见"。
        _card_bg = PAL.CARD_DARK
        try:
            from ..core import theme as _thm
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
                "<b style='font-size:13px; color:%s;'>✓已装 %s</b>"
                % (PAL.SUCCESS_LIGHT, title))
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
        b_mod.setStyleSheet(_link_btn_css())
        b_mod.clicked.connect(
            lambda _c, s=_eff: self._open_modlink("modrinth", s, title))
        b_mc = QPushButton("[MC百科]")
        b_mc.setCursor(Qt.PointingHandCursor)
        b_mc.setStyleSheet(_link_btn_css())
        b_mc.clicked.connect(
            lambda _c, s=slug, n=title: self._open_modlink("mcmod", s, n))
        b_cf = QPushButton("[CurseForge]")
        b_cf.setCursor(Qt.PointingHandCursor)
        b_cf.setStyleSheet(_link_btn_css())
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
        card._site_mod_slug = _eff  # ★ V2.0.4：Modrinth 探测用实际分支 slug
        card._site_name = title
        rt.addLayout(h1)

        # 简介（灰色小字，两行截断）
        lb_desc = QLabel(desc)
        lb_desc.setWordWrap(True)
        lb_desc.setStyleSheet(
            "color:%s; font-size:11px; background:transparent;"
            % PAL.MUTED)
        lb_desc.setMaximumHeight(30)  # 最多两行
        rt.addWidget(lb_desc)

        # 标签行（★ V1.8.2：纯平铺、无分组标题；desc 的 [标签] 彩色渲染）
        # ★ V1.9.5 / V2.0.1：统一走 _build_tags —— 探测结果回来由
        #   _rebuild_tags 原地重建同一行标签（含失败文案），
        #   不再只写"适配检测中…"挂一辈子。
        _tags_html, _ck = self._build_tags(
            slug, title, _tags_desc, req, builtin, cat_txt, dl)
        lb_tags = QLabel(_tags_html)
        lb_tags.setTextFormat(Qt.RichText)
        lb_tags.setStyleSheet(
            "color:%s; font-size:10px; background:transparent;"
            % PAL.MUTED_MID)
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
                if not slug:
                    continue
                name = getattr(card, "_site_name", "") or slug
                st = self._site_cache.get(slug) or {}
                for kind, b in card._site_btns.items():
                    if b.isVisible() or kind in st:
                        continue  # 已显示，或已有结论（保持隐藏）
                    # ★ V2.0.4：Modrinth 站点用实际分支 slug 探测
                    #   （ModernFix 1.21.5+ 原版没有适配版本）
                    s = slug
                    if kind == "modrinth":
                        s = getattr(card, "_site_mod_slug", None) or slug
                    if not s or s in self._site_loading:
                        continue
                    todo.append((s, name, kind))
                    self._site_loading.add(s)
        if not todo:
            return
        self._probe_worker_ = _track(self, Worker(
            self._probe_worker, {"todo": todo}, self))
        self._probe_worker_.done.connect(self._apply_site_probe)
        self._probe_worker_.start()

    @staticmethod
    def _probe_worker(todo, on_log=None):
        """后台并发探测各站，返回 {slug: {kind: True/False/None}}。"""
        from ..core import sources as _s
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
            # ★ V2.0.4：Modrinth 用实际分支 slug 查缓存（与探测 key 一致）
            s = slug
            if kind == "modrinth":
                s = getattr(card, "_site_mod_slug", None) or slug
            v = (self._site_cache.get(s) or {}).get(kind)
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

    def _build_tags(self, slug, title, _tags_desc, req, builtin,
                    cat_txt, dl):
        """构建一张卡片的标签行 HTML（含适配结论）。

        ★ V2.0.1：探测结论（False 红字 / True 兼容 / 失败灰字 /
          未命中"适配检测中…"）在这里统一决定，_make_card 渲染与
          _rebuild_tags 原地更新共用同一逻辑，绝不出现
          "适配检测中…"挂一辈子。
        """
        tags = []
        _ck = None
        installed = _inst_hit(slug, title, self.installed)
        if builtin:
            # 彩色小标签排最前（PCL2 风格，第三行灰字）
            for t in _tags_desc:
                tags.append('<span style="color:%s; font-weight:bold;">'
                            '[%s]</span>'
                            % (self._TAG_COLORS.get(t, PAL.ACCENT_HOVER), t))
            if req:
                tags.append("前置: " + "、".join(req))
            # ★ V2.0.8：适配检测结论提示全部隐藏（不再显示
            #   "适配检测中… / 适配检测失败 / ⚠无适配"），
            #   但 _ck 仍返回 —— 已知不适配的项照常禁勾选（防乱装）。
            _ck = self._compat.get(self._compat_key(slug))
        else:
            if cat_txt:
                tags.append("分类 " + cat_txt)
            if dl:
                tags.append("下载 " + _fmt_num(dl))
            tags.append("%s + %s" % (self.mc_version or "?",
                                     self.loader or "?"))
        if installed:
            tags.append("已安装（勾选可覆盖更新）")
        return "  ".join(tags), _ck

    def _rebuild_tags(self, card):
        """★ V2.0.1：按卡片存的原始数据原地重建标签行（适配结果落地）。"""
        h = getattr(card, "_meta", None)
        if not h:
            return
        builtin = getattr(card, "_builtin", False)
        slug = h.get("slug") or ""
        title = h.get("title") or slug
        _raw = (h.get("description") or "").replace("\n", " ")
        _tags_desc = _RE_BRACKET_TAG.findall(_raw)
        req = h.get("req") or []
        dl = h.get("downloads")
        cats = h.get("categories") or []
        cat_txt = "、".join(
            dict(modman.CATEGORIES).get(c, c) for c in cats[:3])
        html, _ = self._build_tags(slug, title, _tags_desc, req,
                                   builtin, cat_txt, dl)
        lbl = getattr(card, "_tags_lbl", None)
        if lbl is not None:
            lbl.setText(html)
            # ★ V2.0.8：适配检测提示已隐藏，不再挂说明 tooltip
            lbl.setToolTip("")

    def _apply_compat_text(self, slug, ok, why):
        """★ V2.0.1：按探测结论更新推荐列表里一张卡片的勾选与标签。"""
        for i in range(self.list_builtin.count()):
            it = self.list_builtin.item(i)
            if it.data(Qt.UserRole) != slug:
                continue
            if ok is False:
                chk = getattr(it, "_chk", None)
                if chk is not None:
                    chk.blockSignals(True)
                    chk.setChecked(False)
                    chk.setEnabled(False)
                    chk.blockSignals(False)
            elif ok is None:
                chk = getattr(it, "_chk", None)
                if chk is not None:
                    # 失败只解禁（避免之前残留禁用），保持可勾选安装时再确认
                    chk.setEnabled(True)
            card = self.list_builtin.itemWidget(it)
            if card is not None:
                self._rebuild_tags(card)
            break

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
                                "repo": h.get("repo") or "",
                                # ★ V2.0.5：透传分支（ModernFix 探测走社区版）
                                "branch": h.get("branch") or ()}))
        if not todo:
            return
        for slug, _m in todo:
            self._compat_loading.add(slug)
        # ★ V2.0.8：检测提示已全部隐藏（用户要求）—— 只静默后台探测
        self._compat_worker_ = _track(self, Worker(
            self._compat_worker,
            {"todo": todo, "mc_version": self.mc_version,
             "loader": self.loader}, self))
        # ★ V2.0.6：探测进度实时显示到状态栏（不再干等"检测中…"）
        try:
            self._compat_worker_.log.connect(self._compat_progress)
        except Exception:
            pass
        self._compat_worker_.done.connect(self._apply_compat)
        self._compat_worker_.start()

    def _compat_progress(self, msg):
        """★ V2.0.8：适配检测进度提示已隐藏（用户要求），保留空实现。"""
        pass

    @staticmethod
    def _compat_worker(kwargs, on_log=None):
        """后台并发探测每个推荐项的适配性（8 线程，单项限时 18s）。

        ★ V2.0.6：网络抖动自动重试一轮失败项（ok=None）——
          首次超时/网络异常不代表真不适配，重试后大多能出结论，
          显著减少"适配检测失败"的误报。
        """
        from ..core import install as _inst
        import concurrent.futures
        import time as _t
        todo = kwargs.get("todo") or []
        mc_version = kwargs.get("mc_version") or ""
        loader = kwargs.get("loader") or ""

        def run_once(items):
            res = {}
            total = len(items)
            with concurrent.futures.ThreadPoolExecutor(max_workers=8) as ex:
                futs = [(slug, ex.submit(_inst.probe_compat, meta,
                                         mc_version, loader))
                        for slug, meta in items]
                for i, (slug, fut) in enumerate(futs, 1):
                    try:
                        r = fut.result(timeout=18)
                    except Exception:
                        r = {"ok": None,
                             "why": "适配检测超时，安装时再确认"}
                    res[slug] = r if isinstance(r, dict) \
                        else {"ok": None, "why": ""}
                    # ★ V2.0.6：实时进度（状态栏）
                    if on_log:
                        try:
                            on_log("[适配检测] %d/%d" % (i, total))
                        except Exception:
                            pass
            return res

        out = run_once(todo)
        # 只重试"无结论"（网络/超时）的项；ok=False 是真不适配，不重试
        retry = [it for it in todo
                 if (out.get(it[0]) or {}).get("ok") is None]
        if retry:
            _t.sleep(1)
            again = run_once(retry)
            for slug, r in again.items():
                if r.get("ok") is not None:
                    out[slug] = r
        return out

    def _apply_compat(self, ok, res):
        """探测结果落地：不适配 → 禁用勾选 + 红字原因；并写缓存。

        ★ V2.0.1：无结论（网络失败/超时/未设置版本）只刷卡片灰字文案，
          不写缓存 —— 避免"适配检测中…"被固化 24 小时；
          下次打开会重新探测。
        """
        if not isinstance(res, dict):
            # ★ V2.0.5：Worker 异常/崩溃兜底 —— 不再残留"适配检测中…"
            #   挂一辈子（之前会跳过这些 slug 永不重探）。全部标为失败，
            #   卡片灰字说明，下次打开可重新探测。
            why = "适配检测失败（网络异常）"
            for slug in list(self._compat_loading):
                self._compat_fail[slug] = why
                self._apply_compat_text(slug, None, why)
            self._compat_loading.clear()
            try:
                self.lbl_stat.setText("")
            except Exception:
                pass
            self._sync_count()
            return
        changed = False
        for slug, r in res.items():
            self._compat_loading.discard(slug)
            if not isinstance(r, dict):
                continue
            key = self._compat_key(slug)
            if r.get("ok") is None:
                self._compat_fail[slug] = r.get("why") or "网络异常"
                self._apply_compat_text(slug, None, r.get("why") or "")
                continue
            cur = self._compat.get(key)
            if cur and cur[0] is not None:
                continue  # 已有结论的不覆盖（防止过期响应互踩）
            self._compat[key] = (r.get("ok"), r.get("why") or "")
            self._compat_fail.pop(slug, None)
            changed = True
            self._apply_compat_text(slug, r.get("ok"), r.get("why") or "")
        # ★ V2.3.4：探测出结论后按"只显示适配本版本的"重新过滤 ——
        #   明确不适配的模组直接从推荐列表里去掉（这才是版本隔离）。
        try:
            if self.chk_only_fit.isChecked():
                before = len(self.builtin_hits)
                self._filter_builtin_hits()
                if len(self.builtin_hits) != before:
                    self._fill_builtin()
        except Exception:
            pass
        self._sync_count()
        # ★ V2.0.8：完成提示已隐藏（适配检测提示整体隐藏）
        try:
            if not self._compat_loading:
                self.lbl_stat.setText("")
        except Exception:
            pass
        if changed:
            install.compat_cache_save(self._compat)

    def _sync_count(self):
        n = len(self._checked_slugs())
        verb = "确定选择" if self.select_only else "安装选中"
        self.btn_inst.setText("%s (%d)" % (verb, n))

    def _checked_slugs(self):
        """★ V1.8.0：合并两个 Tab 的勾选（推荐 + 全库）。
        ★ V1.9.5：跳过不适配已禁用的项（disabled 的 chk 即使被
          程序化勾上也不算，绝不把装不了的项带进安装队列）。
        ★ V2.0.8：推荐页翻页后勾选不丢 —— 从跨页状态字典取全部页勾选，
          全库 Tab 仍实时遍历当前渲染列表；已知不适配（缓存 False）
          的项无论在哪一页都不带进安装队列。"""
        out = []
        seen = set()
        # 推荐 Tab：跨页保留的勾选
        for slug, on in self._builtin_checked.items():
            if not on or slug in seen:
                continue
            seen.add(slug)
            ck = self._compat.get(self._compat_key(slug))
            if ck and ck[0] is False:
                continue  # 已知不适配不装
            out.append(slug)
        # 全库 Tab：跨页保留的勾选（★ V2.0.9 支持跨页）
        for slug, on in self._lib_checked.items():
            if not on or slug in seen:
                continue
            seen.add(slug)
            out.append(slug)
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
        """镜像源设置：★ V2.0.4 改回模态 exec()。

        旧版非模态 show() 的 SourceDialog 会一直挂在屏幕上不消失，
        用户每次操作（搜索/翻页）都看到它，误以为"每次搜索都弹窗"。
        模态 exec() 关掉才返回，杜绝残留；保存后立即刷新并重搜。
        """
        try:
            from .common import show_source_dialog
            from ..core import sources as _src
            d, vals = show_source_dialog(self)
            if vals is None:
                return
            mirror, key = vals
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
        except Exception as e:
            self._log("[错误] 镜像源设置：%r" % e)

    def do_probe(self):
        """探测所有下载源（后台 Worker，避免卡 UI）。

        ★ V1.9.0：修复旧代码 _src.probe() 缺 name 参数导致的 TypeError；
          镜像 503 时在 UI 提示"镜像不可用，已自动切换官方源"。
        ★ 并发测速：probe_all_latency 并行请求并返回各源延迟（ms）。
        """
        try:
            from ..core import sources as _src
            self.btn_probe.setEnabled(False)
            self._log("正在探测各下载源 ...")
            self.worker = _track(self, Worker(self._probe_task, {}, self))
            self.worker.log.connect(self._log)
            self.worker.done.connect(self._on_probe_done)
            self.worker.start()
        except Exception as e:
            self._log("[错误] %r" % e)

    def _probe_task(self, on_log=None):
        from ..core import sources as _src
        return _src.probe_all_latency(timeout=8)

    def _on_probe_done(self, ok, res):
        self.btn_probe.setEnabled(True)
        try:
            from ..core import sources as _src
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
            from ..core import sources as _src
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
        # ★ V2.0.8：安装完成后清空跨页勾选状态（避免已装项残留勾选重装）
        self._builtin_checked.clear()
        # ★ V2.0.9：全库搜索勾选同步清空
        self._lib_checked.clear()
        self.search()

    def _log(self, m):
        self.log.appendPlainText(m)

    def open_mod_manager(self):
        """★ V2.0.6：浏览大窗内的"已装模组管理"入口。

        打开旧"模组管理"小窗（启用/禁用/检查更新/打开文件夹），
        原配置页"完整管理"不再先弹这个小窗挡路。
        """
        try:
            d = _themed(ModDialog(self.entry, self))
            d.exec()
            self.installed = self._installed_slugs()
            self._load_builtin_local()
        except Exception as e:
            self._log("[错误] 已装模组管理：%r" % e)


class ModDialog(BaseDialog):
    """模组管理：列出已装 mod，可启用/禁用/检查更新/打开文件夹。"""

    COLS = ["状态", "名称", "当前版本", "最新版本", "文件"]

    def __init__(self, entry, parent=None):
        super().__init__(parent)
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
        txt = str(m)
        urls = _RE_URL.findall(txt)
        if urls:
            # 先转义 HTML，再把 URL 包成 <a>
            esc = (txt.replace("&", "&amp;")
                      .replace("<", "&lt;").replace(">", "&gt;"))
            for u in urls:
                esc = esc.replace(
                    u, '<a href="%s" style="color:%s;">%s</a>'
                       % (u.replace('"', "%22"), PAL.ACCENT_LINK, u))
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
                self.table.item(r, 0).setForeground(QColor(_on_bg(PAL.DIM)))
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
            self.table.item(r, 3).setForeground(QColor(_on_bg(PAL.WARN_LIGHT)))
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
            self.table.item(r, 3).setForeground(QColor(_on_bg(PAL.SUCCESS)))
            self._log("[最新] %s (%s)" % (m["name"], latest))
        elif status == "error":
            msg = (res.get("err") or "查询失败")[:30]
            self.table.setItem(r, 3, QTableWidgetItem(msg))
            self.table.item(r, 3).setForeground(QColor(_on_bg(PAL.DANGER)))
            self._log("[失败] %s : %s" % (m["name"], res.get("err")))
        else:
            msg = (res.get("err") or "Modrinth 上没查到")[:30]
            self.table.setItem(r, 3, QTableWidgetItem(msg))
            self.table.item(r, 3).setForeground(QColor(_on_bg(PAL.DIM)))
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


