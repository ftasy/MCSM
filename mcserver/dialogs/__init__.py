# -*- coding: utf-8 -*-
"""对话框集合。

原来是一个 8700 行的 dialogs.py，现按功能拆成 6 个子模块，
这里做统一门面导出 —— `from .dialogs import CreateDialog` 等旧写法继续可用。

    common      通用：LogView / 主题助手 / YAML 读写
    create      创建向导：JavaPick / CreateDialog
    config      服务器配置：ServerConfigDialog
    server_ops  运维：删除服务器 / 诊断 / 加载器更新 / 移除核心
    mods        模组：选择 / 全库浏览 / Modrinth / 模组配置
    backup      备份与搬迁：BackupDialog / MigrateDialog
"""

from ..dialog_workers import (Worker, IconWorker, LoaderVerWorker,
                              _track, _stop_threads, _stop_all_threads,
                              _LIVE_THREADS)
from .common import (LogView, _th, _fg, _on_bg, _sec_bg, _muted,
                     _preset_matches, _themed, _esc_html, _inst_hit,
                     _fmt_num, _open_in_explorer, _fetch_icon_bytes)
from .server_ops import (DeleteServerDialog, DiagDialog,
                         LoaderUpdateDialog, RemoveCoreDialog)
from .mods import (ModPickDialog, AnalyzeDialog, SourceDialog,
                   ModrinthDialog, ModBrowseDialog, ModDialog)
from .create import JavaPick, CreateDialog
from .backup import BackupDialog, MigrateDialog
from .config import ServerConfigDialog

__all__ = [
    # common
    "LogView", "_themed", "_esc_html", "_inst_hit", "_fmt_num",
    "_open_in_explorer", "_fetch_icon_bytes", "_th", "_fg", "_on_bg",
    # 后台线程（dialog_workers 转发）
    "Worker", "IconWorker", "LoaderVerWorker", "_track",
    "_stop_threads", "_stop_all_threads", "_LIVE_THREADS",
    # 对话框
    "JavaPick", "CreateDialog", "ServerConfigDialog",
    "DeleteServerDialog", "DiagDialog", "LoaderUpdateDialog",
    "RemoveCoreDialog", "ModPickDialog", "AnalyzeDialog", "SourceDialog",
    "ModrinthDialog", "ModBrowseDialog", "ModDialog",
    "BackupDialog", "MigrateDialog",
]
