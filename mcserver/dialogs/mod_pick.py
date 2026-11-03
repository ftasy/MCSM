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
from .server_ops import DiagDialog
from ..widgets import app_icon
from ..core.palette import PAL


def _link_btn_css():
    """卡片上的"查百科"小按钮样式（深色卡片上的链接蓝 + 悬停提亮）。"""
    return ("QPushButton{border:none;color:%s;font-size:11px;}"

"""ModPickDialog：初始化前手动勾选要装的模组（含 _link_btn_css 辅助）。"""

            "QPushButton:hover{color:%s;}" % (PAL.ACCENT, PAL.ACCENT_HOVER))


class ModPickDialog(BaseDialog):
    """初始化前让用户自己勾选要装哪些模组。

    支持：
      · 按分组显示（不至于一大坨）
      · 手动添加任意 Modrinth 模组（粘贴链接或 slug）
    """

    def __init__(self, mc_version, loader, parent=None):
        super().__init__(parent)
        self.setWindowTitle("选择要安装的模组 - %s / %s" % (mc_version, loader))
        self.resize(760, 620)
        self.picked = []
        self.loader = loader
        self.mc_version = mc_version

        v = QVBoxLayout(self)
        v.addWidget(QLabel(
            "版本 <b>%s</b> / 加载器 <b>%s</b><br>"
            "勾选要装的。装错了也能在『模组管理』里禁用。"
            % (mc_version, loader)))

        self.table = QTableWidget(0, 3)
        self.table.setHorizontalHeaderLabels(["装", "模组", "说明"])
        self.table.verticalHeader().setVisible(False)
        self.table.setEditTriggers(QAbstractItemView.NoEditTriggers)
        hh = self.table.horizontalHeader()
        hh.setSectionResizeMode(0, QHeaderView.ResizeToContents)
        hh.setSectionResizeMode(1, QHeaderView.ResizeToContents)
        hh.setSectionResizeMode(2, QHeaderView.Stretch)
        v.addWidget(self.table, 1)

        # ---- 手动添加（解决"内置清单不够"的根本办法）----
        gb = QGroupBox("清单里没有想要的？手动添加")
        hb = QHBoxLayout(gb)
        self.ed_custom = QLineEdit()
        self.ed_custom.setPlaceholderText(
            "粘贴 Modrinth 项目链接，或直接填 slug（如 sodium）")
        self.ed_custom.returnPressed.connect(self._add_custom)
        hb.addWidget(self.ed_custom, 1)
        b_add = QPushButton("添加")
        b_add.clicked.connect(self._add_custom)
        hb.addWidget(b_add)
        v.addWidget(gb)
        v.addWidget(QLabel(
            "提示：去 modrinth.com 找到模组，复制地址栏链接粘进来即可。"
            "版本对不上的会在安装时跳过，不会崩。"))

        h = QHBoxLayout()
        b_all = QPushButton("全选")
        b_all.clicked.connect(lambda: self._set_all(True))
        h.addWidget(b_all)
        b_none = QPushButton("全不选")
        b_none.clicked.connect(lambda: self._set_all(False))
        h.addWidget(b_none)
        b_def = QPushButton("恢复默认")
        b_def.clicked.connect(self._set_default)
        h.addWidget(b_def)
        self.lbl_count = QLabel("")
        self.lbl_count.setStyleSheet(_muted())
        h.addWidget(self.lbl_count)
        h.addStretch(1)
        v.addLayout(h)

        # ★ 冲突提示：两个抢同一块功能的模组同时勾上要说清楚，
        #   不然装完才发现视距被两个模组来回拉。
        self.lbl_warn = QLabel("")
        self.lbl_warn.setWordWrap(True)
        self.lbl_warn.setVisible(False)
        try:
            from ..core import theme as _thm
            self.lbl_warn.setStyleSheet(
                "color: %s;" % _thm.on_bg(PAL.DANGER))
        except Exception:
            pass
        v.addWidget(self.lbl_warn)

        bb = QDialogButtonBox(QDialogButtonBox.Ok | QDialogButtonBox.Cancel)
        bb.button(QDialogButtonBox.Ok).setText("完成选择") # ★ 改为更合理的文案
        bb.accepted.connect(self.accept)
        bb.rejected.connect(self.reject)
        v.addWidget(bb)

        self.items = install.mod_list_for(loader, mc_version)
        # ★ 修：mod_list_for 从 v0.3.0 起不再返回 default 字段
        #   （改由用户自己勾选），这里硬取下标必然 KeyError，
        #   选模组对话框根本打不开。缺省视为"不预勾"。
        self.defaults = [bool(m.get("default", False)) for m in self.items]
        self._fill()

    def _fill(self):
        self.table.setRowCount(0)
        self.boxes = []
        for m in self.items:
            # ★ V1.6.2：不再插入"① 前置"这类分组标题行，全部平铺
            r = self.table.rowCount()
            self.table.insertRow(r)
            cb = QCheckBox()
            cb.setChecked(bool(m.get("default", False)))
            cb.stateChanged.connect(self._upd_count)
            w = QWidget()
            lay = QHBoxLayout(w)
            lay.setContentsMargins(0, 0, 0, 0)
            lay.setAlignment(Qt.AlignCenter)
            lay.addWidget(cb)
            self.table.setCellWidget(r, 0, w)
            self.boxes.append((m["slug"], cb))

            it1 = QTableWidgetItem(m["name"])
            if m.get("group") == "⑥ 自定义":
                f = it1.font(); f.setBold(True); it1.setFont(f)
                it1.setForeground(QColor(_on_bg(PAL.INFO)))
            self.table.setItem(r, 1, it1)

            desc = m["desc"]
            # 有冲突的直接写在说明里，勾的时候就能看见
            cfs = m.get("conflict") or []
            if cfs:
                names = []
                for c in cfs:
                    cm = install.mod_meta(self.loader, c)
                    names.append((cm or {}).get("name") or c)
                desc += "  ⚠ 与 %s 冲突" % "、".join(names)
            # 缺前置的也标出来（会被自动一起装上）
            reqs = m.get("req") or []
            if reqs:
                names = []
                for c in reqs:
                    cm = install.mod_meta(self.loader, c)
                    names.append((cm or {}).get("name") or c)
                desc += "  （需要 %s，会自动一起装）" % "、".join(names)
            self.table.setItem(r, 2, QTableWidgetItem(desc))
        self._upd_count()

    def _upd_count(self):
        try:
            picks = self.result_slugs()
            n = len(picks)
            plan = install.resolve_selection(self.loader, picks)
            extra = len(plan.get("added") or [])
            txt = "已选 %d 个" % n
            if extra:
                txt += "（含自动补的前置 %d 个）" % extra
            self.lbl_count.setText(txt)
            cfs = plan.get("conflicts") or []
            if cfs:
                self.lbl_warn.setText(
                    "⚠ 冲突：" + "；".join(w for _, _, w in cfs)
                    + "\n建议只保留其中一个。"
                      "（两个都装不会崩，但会互相拉扯）")
                self.lbl_warn.setVisible(True)
            else:
                self.lbl_warn.setText("")
                self.lbl_warn.setVisible(False)
        except Exception:
            pass

    def _add_custom(self):
        """手动添加一个不在内置清单里的模组。"""
        raw = self.ed_custom.text().strip()
        if not raw:
            return
        slug = install.normalize_slug(raw)
        if not slug:
            QMessageBox.warning(self, "识别不了", "看不出这是哪个模组。")
            return
        for sl, _ in self.boxes:
            if sl == slug:
                QMessageBox.information(self, "已经有了",
                                        "%s 已在清单里。" % slug)
                return
        self.items.append({"slug": slug, "name": slug,
                           "default": True, "desc": "手动添加",
                           "group": "⑥ 自定义", "side": "server",
                           "req": [], "conflict": []})
        self.defaults.append(True)
        self.ed_custom.clear()
        self._fill()
        note = ""
        why = install.CLIENT_ONLY_MODS.get(slug)
        if why:
            # ★ 手动加了个只对客户端生效的 —— 装到服务端没用，
            #   直接说清楚，别让人白等一次下载。
            note = ("\n\n⚠ %s 是客户端模组：%s\n"
                    "装到服务端不会有任何效果，建议取消勾选。"
                    % (slug, why))
        QMessageBox.information(
            self, "已添加",
            "%s 已加进清单（在最下面）。\n"
            "如果 %s 没有这个版本，安装时会跳过。%s"
            % (slug, self.mc_version, note))

    def _set_all(self, on):
        for _, cb in self.boxes:
            cb.setChecked(on)

    def _set_default(self):
        for i, (_, cb) in enumerate(self.boxes):
            if i < len(self.defaults):
                cb.setChecked(self.defaults[i])

    def result_slugs(self):
        return [slug for slug, cb in self.boxes if cb.isChecked()]


