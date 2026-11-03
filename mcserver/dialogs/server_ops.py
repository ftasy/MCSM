# -*- coding: utf-8 -*-
"""服务器运维：删除服务器、导出诊断、加载器更新、移除核心。

从 dialogs.py 拆分而来，只保留本模块用到的导入。
"""

import os

from PySide6.QtGui import QDesktopServices
from PySide6.QtCore import Qt
from PySide6.QtWidgets import (
    QDialog, QVBoxLayout, QHBoxLayout, QFormLayout, QLabel, QPushButton,
    QCheckBox, QMessageBox, QProgressBar, QDialogButtonBox, QGroupBox,
    QAbstractItemView, QPlainTextEdit, QTextBrowser, QApplication,
    QTableWidget, QTableWidgetItem, QHeaderView, QInputDialog)
from ..core import install, loaderup
from ..core.palette import PAL
from ..dialog_workers import Worker, _track, _stop_threads
from .common import LogView, _on_bg, _open_in_explorer
from .base import BaseDialog


class DeleteServerDialog(BaseDialog):
    """删除整个服务器文件夹。危险操作，多重确认。"""

    def __init__(self, entry, parent=None):
        super().__init__(parent)
        self.entry = entry
        self.setWindowTitle("删除服务器 - %s" % entry.get("name", ""))
        self.resize(600, 460)

        self.server_dir = entry.get("server_dir") or entry["folder"]
        v = QVBoxLayout(self)

        warn = QLabel(
            "⚠ 这会删掉整个服务器文件夹，包括存档、模组、配置。\n"
            "删除后无法恢复（除非你有备份）。")
        warn.setStyleSheet("color:%s; font-weight:bold;" % _on_bg(PAL.DANGER))
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
        self.btn.setStyleSheet("color:%s; font-weight:bold;" % _on_bg(PAL.DANGER))
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
            from ..core import scanner as _sc
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
                        reg += sum(1 for f in os.scandir(rd)
                                   if f.name.endswith(".mca"))
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
                    c = sum(1 for f in os.scandir(d)
                            if f.name.lower().endswith(".jar"))
                    rows.append((label, "%d 个" % c))
                except Exception:
                    pass
        bk = os.path.join(sd, "backups")
        if os.path.isdir(bk):
            try:
                c = sum(1 for _ in os.scandir(bk))
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


class DiagDialog(BaseDialog):
    """导出报错诊断包，方便发给别人排查。"""

    def __init__(self, entry, console_text=None, parent=None):
        super().__init__(parent)
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


class LoaderUpdateDialog(BaseDialog):
    """更新 Fabric / Forge / NeoForge / Paper。

    只换核心文件 —— 存档、模组、server.properties 全都不动。
    以前要更新只能"删核心重装"，那会把配置折腾一遍。
    """

    def __init__(self, entry, parent=None):
        super().__init__(parent)
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


class RemoveCoreDialog(BaseDialog):
    """删除装错的核心。绝不碰存档/模组/配置。"""

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



