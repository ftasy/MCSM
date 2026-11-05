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

        btns = QDialogButtonBox(QDialogButtonBox.Ok
                                | QDialogButtonBox.Cancel)
        btns.accepted.connect(self.accept)
        btns.rejected.connect(self.reject)
        v.addWidget(btns)
        self.resize(520, 330)

    def values(self):
        return (self.cb.currentData() or "auto",
                self.ed_key.text().strip(),
                self.cb_dl.currentData() or "auto")


