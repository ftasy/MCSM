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



"""SourceDialog：下载源与 CurseForge API Key 设置。"""

class SourceDialog(BaseDialog):
    """下载源 + CurseForge API Key 设置。"""

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setWindowTitle("下载源设置")
        v = QVBoxLayout(self)
        tip = QLabel(
            "MCIM 是国内公益镜像，会时不时挂（实测有过 503）。\n"
            "『自动』= 先试镜像，不通就退回官方。\n\n"
            "CurseForge 官方 API 需要免费申请一个 key；\n"
            "走 MCIM 镜像时不需要。这里留空就只用镜像搜 CF。")
        tip.setWordWrap(True)
        v.addWidget(tip)

        f = QFormLayout()
        self.cb = QComboBox()
        self.cb.addItem("自动（推荐）", "auto")
        self.cb.addItem("MCIM 国内镜像", "mcim")
        self.cb.addItem("官方源（Modrinth / CurseForge）", "official")
        i = self.cb.findData(sources.get_mirror())
        self.cb.setCurrentIndex(i if i >= 0 else 0)
        f.addRow("下载源:", self.cb)

        self.ed_key = QLineEdit(sources.get_cf_key())
        self.ed_key.setPlaceholderText(
            "CurseForge API Key（留空 = 不启用官方 CF）")
        self.ed_key.setEchoMode(QLineEdit.Password)
        f.addRow("CF Key:", self.ed_key)
        v.addLayout(f)

        self.lbl_now = QLabel("当前：%s" % sources.describe())
        self.lbl_now.setStyleSheet(_muted())
        v.addWidget(self.lbl_now)

        btns = QDialogButtonBox(QDialogButtonBox.Ok
                                | QDialogButtonBox.Cancel)
        btns.accepted.connect(self.accept)
        btns.rejected.connect(self.reject)
        v.addWidget(btns)
        self.resize(470, 280)

    def values(self):
        return (self.cb.currentData() or "auto",
                self.ed_key.text().strip())


