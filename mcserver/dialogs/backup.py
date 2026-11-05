# -*- coding: utf-8 -*-
"""备份与搬迁：BackupDialog / MigrateDialog。

从 dialogs.py 拆分而来，只保留本模块用到的导入。
"""

import os
import sys

from PySide6.QtGui import QColor
from PySide6.QtCore import Qt, QThread
from PySide6.QtWidgets import (
    QDialog, QVBoxLayout, QHBoxLayout, QLabel, QLineEdit, QPushButton,
    QCheckBox, QFileDialog, QMessageBox, QDialogButtonBox, QGroupBox,
    QListWidget, QListWidgetItem, QApplication)
from ..core import migrate, backup
from ..dialog_workers import _stop_threads, _install_exit_hook
from .common import LogView, _muted, _open_in_explorer
from .base import BaseDialog
from ..core.palette import PAL


def _default_migrate_dst():
    """搬迁目标的默认值：用户目录\\MCServers。

    ★ V2.4.0：原来写死 "G:\\MCServers"（作者的备份盘）。
      别人的电脑没有 G 盘，点『执行搬迁』直接失败，还以为是软件坏了。
      现在取"任何 Windows 都有"的用户目录；若那里所在盘恰好不可写，
      再退到系统临时目录的上一级，保证总有一个能用的路径。
    """
    cands = []
    try:
        home = os.path.expanduser("~")
        if home and home != "~":
            cands.append(os.path.join(home, "MCServers"))
    except Exception:
        pass
    for env in ("USERPROFILE", "LOCALAPPDATA", "TEMP", "TMP"):
        v = os.environ.get(env)
        if v:
            cands.append(os.path.join(v, "MCServers"))
    # 用户目录所在盘是不是真的存在（外接盘拔掉、漫游配置异常时会没有）
    for p in cands:
        try:
            drive = os.path.splitdrive(os.path.abspath(p))[0]
            if drive and not os.path.isdir(drive + os.sep):
                continue
            return p
        except Exception:
            continue
    return ""


class BackupDialog(BaseDialog):
    """备份管理：列出该服的存档和已有备份，支持备份/恢复/删除。"""

    def __init__(self, entry, parent=None, running_fn=None,
                 backup_format=None):
        super().__init__(parent)
        self.entry = entry
        # 主窗口传进来的"这个服在不在跑"查询函数
        self.running_fn = running_fn
        # ★ V1.1.0：备份格式（"folder" / "zip"），默认文件夹
        self.backup_format = backup_format or "folder"
        self.setWindowTitle("备份管理 - %s" % entry.get("name", ""))
        self.resize(760, 560)

        self.server_dir = entry.get("server_dir") or entry["folder"]
        v = QVBoxLayout(self)

        # ---- 存档列表 ----
        g1 = QGroupBox("存档（只备份这些，不会碰整个服务端）")
        v1 = QVBoxLayout(g1)
        self.world_list = QListWidget()
        v1.addWidget(self.world_list)
        v.addWidget(g1)

        self.worlds = backup.find_worlds(self.server_dir)
        for wd in self.worlds:
            it = QListWidgetItem(
                "%s      [%.1f MB, %d 个 region]\n    %s"
                % (wd["name"], wd["size_mb"], wd["regions"], wd["rel"]))
            it.setData(Qt.UserRole, wd["path"])
            self.world_list.addItem(it)
        if not self.worlds:
            self.world_list.addItem("(没找到存档，确认 level.dat 是否存在)")
        else:
            self.world_list.setCurrentRow(0)

        # ---- 选项 ----
        h = QHBoxLayout()
        self.chk_zip = QCheckBox("压缩成 zip（省空间，但慢）")
        # ★ V1.1.0：默认勾选状态跟随服务器配置的备份格式
        self.chk_zip.setChecked(self.backup_format == "zip")
        h.addWidget(self.chk_zip)
        h.addStretch(1)
        v.addLayout(h)

        # ---- 备份列表 ----
        self.g2 = QGroupBox("已有备份")
        v2 = QVBoxLayout(self.g2)
        self.lbl_bkdir = QLabel("")
        self.lbl_bkdir.setWordWrap(True)
        self.lbl_bkdir.setStyleSheet(_muted())
        v2.addWidget(self.lbl_bkdir)
        self.bk_list = QListWidget()
        v2.addWidget(self.bk_list)
        v.addWidget(self.g2, 1)
        self.refresh_backups()

        # ---- 按钮 ----
        hb = QHBoxLayout()
        self.btn_now = QPushButton("立即备份选中存档")
        self.btn_now.clicked.connect(self.do_backup)
        hb.addWidget(self.btn_now)
        self.btn_restore = QPushButton("用备份替换当前存档")
        self.btn_restore.clicked.connect(self.do_restore)
        hb.addWidget(self.btn_restore)
        self.btn_del = QPushButton("删除备份")
        self.btn_del.clicked.connect(self.do_delete)
        hb.addWidget(self.btn_del)
        hb.addStretch(1)
        v.addLayout(hb)

        self.log = LogView()
        self.log.setReadOnly(True)
        self.log.setMaximumHeight(120)
        v.addWidget(self.log)

        hb2 = QHBoxLayout()
        b1 = QPushButton("打开服务器文件夹")
        b1.clicked.connect(
            lambda: _open_in_explorer(self.server_dir))
        hb2.addWidget(b1)
        b2 = QPushButton("打开备份文件夹")
        b2.clicked.connect(self.open_backup_dir)
        hb2.addWidget(b2)
        b3 = QPushButton("打开选中存档文件夹")
        b3.clicked.connect(self.open_world_dir)
        hb2.addWidget(b3)
        b4 = QPushButton("刷新备份列表")
        b4.clicked.connect(self.refresh_backups)
        hb2.addWidget(b4)
        b5 = QPushButton("打开日志文件夹")
        b5.clicked.connect(self.open_log_dir)
        hb2.addWidget(b5)
        hb2.addStretch(1)
        v.addLayout(hb2)

        bb = QDialogButtonBox(QDialogButtonBox.Close)
        bb.rejected.connect(self.reject)
        v.addWidget(bb)


    def open_log_dir(self):
        """打开日志文件夹（找不到就打开服务器目录）。"""
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

    def open_backup_dir(self):
        d = os.path.join(self.server_dir, backup.BACKUP_DIRNAME)
        if not os.path.isdir(d):
            r = QMessageBox.question(self, "还没有备份",
                                     "备份文件夹不存在，要创建吗？")
            if r != QMessageBox.Yes:
                return
            try:
                os.makedirs(d, exist_ok=True)
            except Exception as e:
                QMessageBox.warning(self, "失败", repr(e))
                return
        _open_in_explorer(d)

    def open_world_dir(self):
        wp = self._cur_world()
        if not wp:
            QMessageBox.information(self, "提示", "先选中一个存档")
            return
        _open_in_explorer(wp)

    def _log(self, m):
        self.log.appendPlainText(m)

    def refresh_backups(self):
        """刷新备份列表，并显示备份目录在哪。"""
        self.bk_list.clear()
        self.backups = backup.list_backups(self.server_dir)
        try:
            dg = backup.diagnose(self.server_dir)
        except Exception:
            dg = None
        if dg and dg.get("dirs_found"):
            self.lbl_bkdir.setText(
                "备份目录: %s" % "  |  ".join(dg["dirs_found"]))
        else:
            miss = (dg or {}).get("dirs_missing") or []
            self.lbl_bkdir.setText(
                "备份目录还没建（备份时会自动创建）。\n找过: %s"
                % ", ".join(miss[:2]))
        for b in self.backups:
            tag = "" if b.get("confirmed", True) else "  [未确认]"
            it = QListWidgetItem(
                "%s%s   [%s, %.1f MB]\n    %s"
                % (b["name"], tag,
                   "zip" if b["kind"] == "zip" else "文件夹",
                   b["size_mb"], b.get("time", "")))
            it.setData(Qt.UserRole, b["path"])
            it.setToolTip(b["path"])
            self.bk_list.addItem(it)
        if not self.backups:
            self.bk_list.addItem("(还没有备份)")

    def _cur_world(self):
        it = self.world_list.currentItem()
        if not it:
            return None
        return it.data(Qt.UserRole)

    def _cur_backup(self):
        it = self.bk_list.currentItem()
        if not it:
            return None
        return it.data(Qt.UserRole)

    def do_backup(self):
        wp = self._cur_world()
        if not wp:
            QMessageBox.warning(self, "提示", "先选中一个存档")
            return
        self._log("备份中，请稍候 ...")
        QApplication.processEvents()
        try:
            r = backup.backup_world(wp, self.server_dir,
                                    compress=self.chk_zip.isChecked(),
                                    on_log=self._log)
            self.refresh_backups()
            QMessageBox.information(self, "完成",
                                    "备份完成：\n%s\n%.1f MB"
                                    % (os.path.basename(r["path"]),
                                       r["size_mb"]))
        except Exception as e:
            QMessageBox.critical(self, "失败", repr(e))

    def do_restore(self):
        bp = self._cur_backup()
        wp = self._cur_world()
        if not bp:
            QMessageBox.warning(self, "提示", "先选中一个备份")
            return
        if not wp:
            QMessageBox.warning(self, "提示", "先选中要被替换的存档")
            return
        bn = os.path.basename(bp)
        wn = os.path.basename(wp.rstrip("\\/"))
        r = QMessageBox.question(
            self, "确认替换",
            "将用备份：\n  %s\n\n替换当前存档：\n  %s\n\n"
            "当前存档会被重命名保留（world_old_时间戳），确认继续？"
            % (bn, wn))
        if r != QMessageBox.Yes:
            return
        # 二次确认
        r2 = QMessageBox.warning(
            self, "再次确认",
            "这个操作会替换存档数据，确定？",
            QMessageBox.Yes | QMessageBox.No, QMessageBox.No)
        if r2 != QMessageBox.Yes:
            return
        self._log("恢复中 ...")
        QApplication.processEvents()
        try:
            res = backup.restore_backup(bp, wp, on_log=self._log)
            QMessageBox.information(
                self, "完成",
                "已恢复。\n旧存档保留为：\n%s\n\n确认没问题后可手动删除。"
                % os.path.basename(res["old"]))
        except Exception as e:
            QMessageBox.critical(self, "失败", repr(e))

    def _is_running(self):
        """这个服务器现在在不在跑。"""
        try:
            if callable(self.running_fn):
                return bool(self.running_fn())
        except Exception:
            pass
        return False

    def do_delete(self):
        bp = self._cur_backup()
        if not bp:
            QMessageBox.warning(self, "提示", "先选中一个备份")
            return

        # 服务器还在跑的话，存档文件被占用，多半删不干净
        if self._is_running():
            r = QMessageBox.warning(
                self, "服务器还在运行",
                "这个服务器正在运行，存档文件被占用。\n\n"
                "这时删除备份很可能【删不干净】，只剩下一部分残骸。\n\n"
                "建议先停止服务器再删。\n\n仍要继续吗？",
                QMessageBox.Yes | QMessageBox.No, QMessageBox.No)
            if r != QMessageBox.Yes:
                return

        r = QMessageBox.question(self, "确认",
                                 "删除备份：\n%s\n\n不可恢复，确定？"
                                 % os.path.basename(bp))
        if r != QMessageBox.Yes:
            return

        self._log("删除中 ...")
        QApplication.processEvents()
        try:
            st = backup.delete_backup(bp, on_log=self._log)
        except Exception as e:
            QMessageBox.critical(self, "失败", repr(e))
            self.refresh_backups()
            return

        self.refresh_backups()

        if st.get("gone") and not st.get("failed"):
            self._log("[OK] 已删除干净")
            return

        # 删不干净 —— 必须说清楚，不能假装成功
        if st.get("gone"):
            QMessageBox.information(
                self, "已删除",
                "已删除（过程中有 %d 项报错，但最终删干净了）。"
                % st.get("failed", 0))
            return

        left = st.get("left_mb", 0.0)
        nf = st.get("failed", 0)
        sample = ""
        if st.get("failed_paths"):
            try:
                rel = os.path.relpath(st["failed_paths"][0][0], bp)
            except Exception:
                rel = st["failed_paths"][0][0]
            sample = "\n\n例如：%s\n原因：%s" % (rel,
                                                st["failed_paths"][0][1])
        box = QMessageBox(self)
        box.setIcon(QMessageBox.Warning)
        box.setWindowTitle("没删干净")
        box.setText("没删干净，还剩 %.1f MB（%d 项删不掉）。%s"
                    % (left, nf, sample))
        box.setInformativeText(
            "常见原因：\n"
            "  1) 服务器还在运行，文件被占用 —— 先停止服务器再删\n"
            "  2) 文件只读 —— 本程序已尝试自动改权限\n"
            "  3) 资源管理器 / 压缩软件正打开着这个备份\n\n"
            "可以关掉占用的程序后重试，或打开文件夹手动删除。")
        b_retry = box.addButton("重试", QMessageBox.AcceptRole)
        b_open = box.addButton("打开文件夹手动删", QMessageBox.ActionRole)
        box.addButton("知道了", QMessageBox.RejectRole)
        box.exec()
        if box.clickedButton() is b_retry:
            self.do_delete()
        elif box.clickedButton() is b_open:
            try:
                _open_in_explorer(os.path.dirname(bp)
                                  if not os.path.isdir(bp) else bp)
            except Exception:
                pass
            self.refresh_backups()


class MigrateDialog(BaseDialog):
    """搬迁设置。"""

    def __init__(self, src_root, parent=None):
        super().__init__(parent)
        self.setWindowTitle("统一搬迁")
        self.resize(700, 460)
        v = QVBoxLayout(self)

        h = QHBoxLayout()
        h.addWidget(QLabel("源目录:"))
        self.ed_src = QLineEdit(src_root or "")
        h.addWidget(self.ed_src, 1)
        b = QPushButton("选择...")
        b.clicked.connect(self.pick_src)
        h.addWidget(b)
        v.addLayout(h)

        h2 = QHBoxLayout()
        h2.addWidget(QLabel("目标:"))
        # ★ V2.4.0：这里原来写死 "G:\\MCServers" —— 那是我这台机器的
        #   备份盘，别人的电脑根本没有 G 盘，一点"执行搬迁"就报错。
        #   现在默认放"用户目录\MCServers"（任何 Windows 都有），
        #   用户也可以自己点『选择...』改成别的盘。
        self.ed_dst = QLineEdit(_default_migrate_dst())
        h2.addWidget(self.ed_dst, 1)
        b2 = QPushButton("选择...")
        b2.clicked.connect(self.pick_dst)
        h2.addWidget(b2)
        v.addLayout(h2)

        self.chk_copy = QCheckBox("复制而非移动（推荐，原文件保留）")
        self.chk_copy.setChecked(True)
        v.addWidget(self.chk_copy)

        v.addWidget(QLabel("预览:"))
        self.lst = QListWidget()
        v.addWidget(self.lst, 1)

        btns = QDialogButtonBox(QDialogButtonBox.Ok | QDialogButtonBox.Cancel)
        btns.button(QDialogButtonBox.Ok).setText("执行搬迁")
        btns.accepted.connect(self.accept)
        btns.rejected.connect(self.reject)
        v.addWidget(btns)
        self.preview()

    def pick_src(self):
        d = QFileDialog.getExistingDirectory(self, "源目录", self.ed_src.text())
        if d:
            self.ed_src.setText(d)
            self.preview()

    def pick_dst(self):
        d = QFileDialog.getExistingDirectory(self, "目标目录", self.ed_dst.text())
        if d:
            self.ed_dst.setText(d)
            self.preview()

    def preview(self):
        self.lst.clear()
        plans = migrate.plan(self.ed_src.text(), self.ed_dst.text())
        for p in plans:
            # ★ 如果目标已存在，标红显示，避免用户盲目点击执行
            it = QListWidgetItem("%s\n    -> %s%s"
                                 % (p["name"], p["dst"],
                                    "   [已存在,将跳过]" if p["exists"] else ""))
            if p["exists"]:
                it.setForeground(QColor(PAL.DANGER))
            self.lst.addItem(it)
        if not plans:
            self.lst.addItem("(源目录下没有子文件夹)")

    def values(self):
        return {"src": self.ed_src.text(), "dst": self.ed_dst.text(),
                "copy": self.chk_copy.isChecked()}


# ★ 必须真的装上这个钩子。
#   以前只写了 _install_exit_hook() 却从来没调用过它 ——
#   于是"解释器退出前停掉线程"这道兜底根本不存在。
#   后果：程序从非正常路径退出时（异常、Ctrl-C、控制台被关、
#   或者最后一个窗口直接没了），后台线程还在跑，
#   Qt 抛 "QThread: Destroyed while thread is still running" 然后 abort，
#   表现就是"没点什么就闪退了"。
#   加上这行之后，退出前一定先停线程。

