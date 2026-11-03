# -*- coding: utf-8 -*-
"""AnalyzeDialog：服务器日志 / 配置健康诊断。

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


class AnalyzeDialog(BaseDialog):
    """离线日志诊断 —— 不联网、不需要 AI，靠规则库匹配。"""

    SEV_ICON = {"error": "[!]", "warn": "[?]", "info": "[i]"}
    SEV_NAME = {"error": "必须处理", "warn": "建议处理", "info": "提示"}

    def __init__(self, entry, all_entries=None, console_text=None,
                 parent=None):
        super().__init__(parent)
        self.entry = entry
        self.server_dir = entry.get("server_dir") or entry["folder"]
        self.setWindowTitle("日志诊断 - %s" % entry.get("name", ""))
        self.resize(860, 640)

        v = QVBoxLayout(self)

        # 顶部
        h = QHBoxLayout()
        self.lbl_top = QLabel("正在分析 ...")
        f = self.lbl_top.font(); f.setBold(True); self.lbl_top.setFont(f)
        h.addWidget(self.lbl_top, 1)
        b_re = QPushButton("重新分析")
        b_re.clicked.connect(self.run)
        h.addWidget(b_re)
        b_log = QPushButton("打开日志文件夹")
        b_log.clicked.connect(self.open_log_dir)
        h.addWidget(b_log)
        # ★ V2.0.1：导出报错诊断并入分析日志（原工具栏独立按钮删除）
        b_diag = QPushButton("导出诊断包")
        b_diag.setToolTip(
            "收集日志与配置到一个文件夹/zip，方便发给别人排查")
        b_diag.clicked.connect(self.export_diag)
        h.addWidget(b_diag)
        v.addLayout(h)

        # 结果列表
        self.list = QListWidget()
        self.list.currentItemChanged.connect(self.show_detail)
        v.addWidget(self.list, 1)

        # 详情
        self.detail = QTextEdit()
        self.detail.setReadOnly(True)
        self.detail.setMinimumHeight(150)
        v.addWidget(self.detail, 1)

        # ★ V1.0.0 AI 深度诊断区
        self._ai_init(v)

        v.addWidget(QLabel(
            "说明：这是离线规则匹配（不联网、不需要 AI）。"
            "没查到不代表一定没问题，只说明没命中已知模式。"))

        bb = QDialogButtonBox(QDialogButtonBox.Close)
        bb.rejected.connect(self.reject)
        v.addWidget(bb)

        self.issues = []
        self.console_text = console_text
        self.all_entries = all_entries or []
        self.run()

    def run(self):
        from ..core import analyze as amod
        self.list.clear()
        try:
            rep = amod.full_report(
                self.server_dir,
                entry=self.entry,
                all_entries=self.all_entries,
                extra_text=self.console_text)
        except Exception as e:
            self.lbl_top.setText("分析失败：%s" % e)
            return

        self.issues = []
        for r in rep.get("log_issues", []):
            self.issues.append({
                "level": r["severity"], "title": r["title"],
                "cause": r["cause"], "fix": r["fix"],
                "file": r.get("file", ""),
                "line": r.get("first_line", 0),
                "count": r.get("count", 1),
                "sample": r.get("sample", ""),
            })
        # 日志里已经报过的事，配置检查别再报一遍
        # （比如日志里有 InvalidPathException，就不用再说"level-name 有冒号"了）
        seen = set(i["title"] for i in self.issues)
        SKIP_IF = {
            "level-name 含非法字符": "存档名里有 Windows 不允许的字符",
            "eula.txt 未同意": "EULA 协议未同意",
        }
        for c in rep.get("config_issues", []):
            title = c["title"]
            skip = False
            for k, prefix in SKIP_IF.items():
                if k in title:
                    for t in seen:
                        if t.startswith(prefix[:8]):
                            skip = True
            if skip:
                continue
            self.issues.append({
                "level": c["level"], "title": title,
                "cause": c.get("detail", ""), "fix": c.get("fix", []),
                "file": "server.properties / eula.txt",
                "line": 0, "count": 1, "sample": "",
            })

        order = {"error": 0, "warn": 1, "info": 2}
        self.issues.sort(key=lambda x: order.get(x["level"], 9))

        n_err = sum(1 for i in self.issues if i["level"] == "error")
        n_warn = sum(1 for i in self.issues if i["level"] == "warn")
        if not self.issues:
            self.lbl_top.setText("没查到已知问题（这不代表一定没问题）")
        else:
            txt = "找到 %d 个问题" % len(self.issues)
            if n_err:
                txt += "，其中 %d 个必须处理" % n_err
            self.lbl_top.setText(txt)

        for i in self.issues:
            icon = self.SEV_ICON.get(i["level"], "[i]")
            name = self.SEV_NAME.get(i["level"], "")
            cnt = "  (出现 %d 次)" % i["count"] if i["count"] > 1 else ""
            it = QListWidgetItem("%s [%s] %s%s"
                                 % (icon, name, i["title"], cnt))
            if i["level"] == "error":
                it.setForeground(QColor(_on_bg(PAL.DANGER)))
            elif i["level"] == "warn":
                it.setForeground(QColor(_on_bg(PAL.WARN)))
            else:
                it.setForeground(QColor(_on_bg(PAL.MUTED_DARK)))
            self.list.addItem(it)

        if self.issues:
            self.list.setCurrentRow(0)

    def show_detail(self, cur, prev):
        if cur is None:
            return
        row = self.list.row(cur)
        if row < 0 or row >= len(self.issues):
            return
        i = self.issues[row]
        L = []
        L.append("【问题】%s" % i["title"])
        L.append("")
        if i["cause"]:
            L.append("【为什么】")
            L.append(i["cause"])
            L.append("")
        if i["fix"]:
            L.append("【怎么修】")
            for n, step in enumerate(i["fix"], 1):
                L.append("  %d. %s" % (n, step))
            L.append("")
        if i["sample"]:
            L.append("【日志原文】")
            L.append(i["sample"])
            L.append("")
        loc = i.get("file") or ""
        if i.get("line"):
            loc += "  第 %d 行" % i["line"]
        if loc:
            L.append("【位置】%s" % loc)
        self.detail.setPlainText("\n".join(L))

    def open_log_dir(self):
        import subprocess
        from .. import core
        nw = getattr(subprocess, "CREATE_NO_WINDOW", 0)
        for d in (os.path.join(self.server_dir, "logs"),
                  os.path.join(self.server_dir, "server", "logs")):
            if os.path.isdir(d):
                try:
                    subprocess.Popen(["explorer", d], creationflags=nw)
                except Exception:
                    pass
                return
        try:
            subprocess.Popen(["explorer", self.server_dir],
                             creationflags=nw)
        except Exception:
            pass

    # ★ V2.0.1：导出报错诊断并入分析日志（原工具栏独立入口删除）
    def export_diag(self):
        """打开导出诊断包对话框（复用 DiagDialog）。"""
        try:
            dlg = DiagDialog(self.entry,
                             console_text=self.console_text,
                             parent=self)
            dlg.exec()
        except Exception:
            import traceback
            traceback.print_exc()

    # ============ V1.0.0 AI 深度诊断 ============

    def _ai_init(self, v):
        """AI 深度诊断 UI：Key + 服务商 + 模型 + 按钮 + Markdown 输出。"""
        from ..core import ai_diag
        gb = QGroupBox("AI 深度诊断（联网）")
        g = QVBoxLayout(gb)

        cfg = ai_diag.load_cfg()

        row1 = QHBoxLayout()
        self.ai_key = QLineEdit(cfg.get("key", ""))
        self.ai_key.setPlaceholderText("OpenAI / DeepSeek / Kimi 兼容 API Key")
        self.ai_key.setEchoMode(QLineEdit.Password)
        row1.addWidget(QLabel("API Key:"))
        row1.addWidget(self.ai_key, 1)

        self.ai_provider = QComboBox()
        for name, base, model in ai_diag.PROVIDERS:
            self.ai_provider.addItem(name)
        self.ai_provider.setCurrentIndex(
            min(cfg.get("provider", 0), len(ai_diag.PROVIDERS) - 1))
        self.ai_provider.currentIndexChanged.connect(self._ai_provider_changed)
        row1.addWidget(QLabel("服务商:"))
        row1.addWidget(self.ai_provider)

        self.ai_model = QLineEdit(cfg.get("model", ""))
        self.ai_model.setPlaceholderText("模型名（如 gpt-4o-mini / deepseek-chat）")
        row1.addWidget(QLabel("模型:"))
        row1.addWidget(self.ai_model)
        g.addLayout(row1)

        row2 = QHBoxLayout()
        self.ai_base = QLineEdit(cfg.get("base", ""))
        self.ai_base.setPlaceholderText("接口地址（留空用预设，自定义时填 https://.../v1）")
        row2.addWidget(QLabel("接口地址:"))
        row2.addWidget(self.ai_base, 1)

        self.btn_ai = QPushButton("AI 深度诊断")
        self.btn_ai.clicked.connect(self._ai_diag)
        row2.addWidget(self.btn_ai)
        g.addLayout(row2)

        self.ai_status = QLabel("提取 latest.log 最后 200 行报错，交给大模型分析")
        self.ai_status.setStyleSheet(_muted())
        g.addWidget(self.ai_status)

        self.ai_out = QTextEdit()
        self.ai_out.setReadOnly(True)
        self.ai_out.setMinimumHeight(140)
        self.ai_out.setPlaceholderText(
            "AI 诊断结果（Markdown）会显示在这里")
        g.addWidget(self.ai_out)

        v.addWidget(gb, 1)
        self._ai_provider_changed(self.ai_provider.currentIndex())

    def _ai_provider_changed(self, idx):
        """切换服务商时自动带出预设的接口地址与模型。"""
        try:
            from ..core import ai_diag
            name, base, model = ai_diag.PROVIDERS[idx]
            if base:
                self.ai_base.setText(base)
            if model:
                self.ai_model.setText(model)
        except Exception:
            pass

    def _ai_diag(self):
        """启动后台线程做 AI 诊断（网络请求绝不卡界面）。"""
        from ..core import ai_diag
        key = self.ai_key.text().strip()
        if not key:
            QMessageBox.warning(self, "缺 API Key",
                                "请先填写 API Key（OpenAI / DeepSeek / Kimi 兼容）")
            return
        base = self.ai_base.text().strip()
        model = self.ai_model.text().strip()
        # 持久化（Key 存在本机 ai.json）
        try:
            ai_diag.save_cfg(key, base, model, self.ai_provider.currentIndex())
        except Exception:
            pass

        self.btn_ai.setEnabled(False)
        self.ai_out.clear()
        self.ai_status.setText("正在提取报错日志 ...")

        def _job(on_log=None):
            rep = ai_diag.collect_errors(self.server_dir)
            if not rep["path"]:
                raise ValueError("找不到 latest.log（服务器还没启动过？）")
            body = ("以下是 Minecraft 服务器日志中提取的报错片段"
                    "（共 %d 行，取最后 %d 行）：\n\n%s"
                    % (rep["total"], len(rep["lines"]),
                       "\n".join(rep["lines"])))
            return ai_diag.call_llm(key, base, model, body,
                                    on_log=on_log)

        w = Worker(_job)
        _track(self, w)
        w.log.connect(self._ai_log)
        w.done.connect(self._on_ai_done)
        w.start()

    def _ai_log(self, m):
        try:
            self.ai_status.setText(str(m))
        except Exception:
            pass

    def _on_ai_done(self, ok, res):
        try:
            self.btn_ai.setEnabled(True)
            if ok:
                self.ai_out.setMarkdown(str(res))
                self.ai_status.setText("诊断完成（结果可复制）")
            else:
                self.ai_status.setText("诊断失败")
                self.ai_out.setPlainText("AI 诊断失败：%s" % res)
        except Exception:
            pass

