# -*- coding: utf-8 -*-
"""SourceDialog：下载源与 CurseForge API Key 设置。

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
from ..core.palette import PAL


class SourceDialog(BaseDialog):
    """下载源 + CurseForge API Key 设置。"""

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setWindowTitle("下载源设置")
        v = QVBoxLayout(self)
        tip = QLabel(
            "★ API 信息（搜索 / 版本列表）和文件下载是两条独立链路，"
            "分别选源效果最好：\n"
            "  国内常见情况：API 走 MCIM 更快，但下 jar 官方 CDN 反而更快。\n"
            "  『自动』= 分别测两条通道的延迟，各挑最快的。\n\n"
            "MCIM 是国内公益镜像，偶尔会挂（实测有过 503），"
            "不通会自动退回官方。\n"
            "CurseForge 官方 API 需要免费申请一个 key；"
            "走 MCIM 时不需要，留空即可。")
        tip.setWordWrap(True)
        v.addWidget(tip)

        f = QFormLayout()
        self.cb = QComboBox()
        self.cb.addItem("自动（推荐）", "auto")
        self.cb.addItem("MCIM 国内镜像", "mcim")
        self.cb.addItem("官方源（Modrinth / CurseForge）", "official")
        i = self.cb.findData(sources.get_mirror())
        self.cb.setCurrentIndex(i if i >= 0 else 0)
        f.addRow("API 信息源:", self.cb)

        self.cb_dl = QComboBox()
        self.cb_dl.addItem("自动（按测速挑最快的）", "auto")
        self.cb_dl.addItem("MCIM 镜像 CDN", "mcim")
        self.cb_dl.addItem("官方 CDN", "official")
        self.cb_dl.setToolTip(
            "只影响实际下载 .jar / 核心 / 安装器文件，不影响搜索。\n"
            "国内网络下官方 CDN 常常比镜像更快，建议保持『自动』。")
        j = self.cb_dl.findData(sources.get_download())
        self.cb_dl.setCurrentIndex(j if j >= 0 else 0)
        f.addRow("文件下载源:", self.cb_dl)

        self.ed_key = QLineEdit(sources.get_cf_key())
        self.ed_key.setPlaceholderText(
            "CurseForge API Key（留空 = 不启用官方 CF）")
        self.ed_key.setEchoMode(QLineEdit.Password)
        f.addRow("CF Key:", self.ed_key)
        v.addLayout(f)

        self.lbl_now = QLabel("当前：%s" % sources.describe())
        self.lbl_now.setWordWrap(True)
        self.lbl_now.setStyleSheet(_muted())
        v.addWidget(self.lbl_now)

        # ★ V2.3.8：两条通道都能**当场测**，不用先保存。
        #   挂 VPN / 直连的测速结果可能完全反过来，所以必须让用户
        #   在自己当前网络下实测，而不是按开发机的数据写死。
        self.btn_probe = QPushButton(
            "测一下两条通道（API 搜索 + 文件下载，各 2 个源）")
        self.btn_probe.setToolTip(
            "分别测 4 个源的延迟，并显示『自动』模式在当前网络下会选哪个组合。\n"
            "挂 VPN、换网络、跨运营商时结果可能完全不同，建议换网络后重测。")
        self.btn_probe.clicked.connect(self.do_probe)
        v.addWidget(self.btn_probe)
        self.lbl_probe = QLabel(
            "未测速 —— 测速结果只反映你当前的网络（VPN 开/关差别很大）。")
        self.lbl_probe.setWordWrap(True)
        self.lbl_probe.setStyleSheet(_muted())
        v.addWidget(self.lbl_probe)

        btns = QDialogButtonBox(QDialogButtonBox.Ok
                                | QDialogButtonBox.Cancel)
        btns.accepted.connect(self.accept)
        btns.rejected.connect(self.reject)
        v.addWidget(btns)
        self.resize(560, 460)

    def do_probe(self):
        """后台并发测两条通道，不卡界面。"""
        self.btn_probe.setEnabled(False)
        self.lbl_probe.setText("正在测速（4 个源，约 10 秒）...")
        self._probe_worker = _track(self, Worker(self._probe_job, {}, self))
        self._probe_worker.done.connect(self._on_probe_done)
        self._probe_worker.start()

    @staticmethod
    def _probe_job(on_log=None):
        """API 通道和下载通道分开测，返回 (api结果, cdn结果)。"""
        api = sources.probe_all_latency(timeout=8)
        cdn = sources.probe_cdn_all_latency(timeout=8)
        return (api, cdn)

    def _on_probe_done(self, ok, res):
        self.btn_probe.setEnabled(True)
        if not ok or not isinstance(res, tuple) or len(res) != 2:
            self.lbl_probe.setText("测速失败：%s" % (res,))
            return
        api, cdn = res

        def fmt(rows, mcim_lab, off_lab):
            out = []
            for name, okk, ms, why in rows:
                lab = mcim_lab if name == "mcim" else off_lab
                out.append("  · %-10s %s" % (
                    lab, ("%d ms  ✓ 可用" % ms) if okk
                    else ("不可用（%s）" % why)))
            return out

        lines = ["【API 搜索通道】"] + fmt(api, "MCIM", "官方") \
            + ["【文件下载通道】"] + fmt(cdn, "MCIM", "官方 CDN")
        # 告诉用户「自动」在当前网络下会怎么选
        good_api = [r for r in api if r[1]]
        good_cdn = [r for r in cdn if r[1]]
        pick_api = min(good_api, key=lambda r: r[2])[0] if good_api else "-"
        pick_cdn = min(good_cdn, key=lambda r: r[2])[0] if good_cdn else "-"
        lines.append("自动模式在当前网络下会选：API=%s，下载=%s" % (
            "MCIM" if pick_api == "mcim" else "官方",
            "MCIM" if pick_cdn == "mcim" else "官方 CDN"))
        lines.append("（结果只反映当前网络；换 VPN / 网络后建议重测）")
        self.lbl_probe.setText("\n".join(lines))
        # 测速会更新运行时的自动选源状态，同步刷新"当前"那一行
        try:
            self.lbl_now.setText("当前：%s" % sources.describe())
        except Exception:
            pass

    def values(self):
        return (self.cb.currentData() or "auto",
                self.ed_key.text().strip(),
                self.cb_dl.currentData() or "auto")


