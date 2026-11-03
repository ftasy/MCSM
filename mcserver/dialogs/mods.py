# -*- coding: utf-8 -*-
"""模组对话框集合门面。

按职责拆成 mod_pick / mod_analyze / mod_source / mod_search / mod_browse 五个
子模块，这里统一 re-export，保证 `from .mods import ModBrowseDialog` 等旧写法
继续可用（config.py / create.py 仍这么写）。
"""

from .mod_pick import _link_btn_css, ModPickDialog
from .mod_analyze import AnalyzeDialog
from .mod_source import SourceDialog
from .mod_search import ModrinthDialog
from .mod_browse import ModBrowseDialog, ModDialog

__all__ = [
    "_link_btn_css", "ModPickDialog", "AnalyzeDialog", "SourceDialog",
    "ModrinthDialog", "ModBrowseDialog", "ModDialog",
]
