# -*- coding: utf-8 -*-
"""跨模块共享的常量。

原来这些直接写在 app.py 顶部，MainWindow 拆成多个 mixin 之后，
mixin 不能再 import app（会循环），所以统一挪到这里。
"""

import re

# 服务器清单文件名（存放在扫描总目录下）
DATA_FILE = "servers.json"

# ★ 性能优化：预编译模块级正则（启动参数里的 -Xmx / -Xms）
_XMX_RE = re.compile(r"-Xmx(\S+)")
