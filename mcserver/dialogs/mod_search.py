# -*- coding: utf-8 -*-
"""模组相关：ModPickDialog / ModBrowseDialog / ModrinthDialog / ModDialog / 分析。

从 dialogs.py 拆分而来，只保留本模块用到的导入。
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
from .mod_pick import _link_btn_css
from .server_ops import DiagDialog
from ..widgets import app_icon
from ..core.palette import PAL



"""ModrinthDialog：从 Modrinth 搜索/筛选/安装模组。"""
from .mod_source import SourceDialog
class ModrinthDialog(BaseDialog):
    """从 Modrinth 搜索/筛选/安装模组（像网页版那样挑）。"""

    COLS = ["名称", "简介", "分类", "下载量", "支持版本", "slug"]

    def __init__(self, entry, parent=None):
        super().__init__(parent)
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
        from ..core import install
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


# =========================================================
