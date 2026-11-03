# -*- coding: utf-8 -*-
"""全局语义色板：把散落在各处的硬编码色值收拢到这里。

为什么要有这个文件
    V2.3.2 之前，界面里散着 140 多处 "#c62828" / "#9aa4b2" 这样的裸色值。
    同一个意思（错误红）在不同文件写了 4 个略微不同的值，改一次配色要全局搜索替换，
    还容易漏。现在统一按【用途】命名，调用点只写 PAL.DANGER，不写色值。

      1. 要调色，只改这一个文件；
      2. 深浅底成对给出（DANGER 用于浅底，DANGER_LIGHT 用于深底/深色卡片）；
      3. 跟随主题的颜色不在这里 —— 那些仍走 core/theme.py（背景/前景/选中色）。

用法
    from ..core.palette import PAL

    lbl.setStyleSheet("color:%s;" % PAL.DANGER)
    it.setForeground(QColor(_on_bg(PAL.DANGER)))     # 会按当前底色自动调对比度
"""


class _Palette(object):
    """语义色常量集合（实例化一次给 PAL 用，兼作命名空间）。"""

    # ---- 危险 / 错误 / 删除 ----
    DANGER = "#c62828"          # 浅色底上的错误文字、删除按钮
    DANGER_LIGHT = "#f87171"    # 深色卡片上的错误标签、失败徽标
    DANGER_BRIGHT = "#ff5252"   # 控制台报错堆栈（亮红，深底）
    DANGER_CONSOLE = "#ff6b6b"  # 控制台普通错误

    # ---- 警告 / 需要注意 ----
    WARN = "#e67e22"            # 橙：警告文字
    WARN_STRONG = "#ef6c00"     # 深橙：需要处理的提示
    WARN_SOFT = "#f59e0b"       # 琥珀：标签底色
    WARN_LIGHT = "#ffd166"      # 深底上的警告（控制台 warn）
    YELLOW = "#facc15"          # 纯黄标签

    # ---- 成功 / 已完成 ----
    SUCCESS = "#2e7d32"         # 浅色底上的成功文字
    SUCCESS_LIGHT = "#4ade80"   # 深色卡片上的"已装"
    SUCCESS_CONSOLE = "#4caf50"  # 控制台启动完成

    # ---- 主色 / 链接 / 高亮 ----
    ACCENT = "#5b8def"          # 深色卡片上的按钮/链接
    ACCENT_HOVER = "#8ab4ff"    # 上一档的悬停色
    ACCENT_LINK = "#4da3ff"     # 富文本 <a> 的颜色
    ACCENT_DEFAULT = "#0b62d0"  # 主界面强调色（未配置时）
    INFO = "#1565c0"            # 浅色底上的提示/链接
    BLUE_SOFT = "#60a5fa"       # 浅蓝标签

    # ---- 辅助色（标签、分类） ----
    PURPLE = "#8e24aa"          # 浅色底上的紫
    PURPLE_SOFT = "#a78bfa"     # 深色卡片上的紫
    CYAN = "#22d3ee"            # 青
    TEAL = "#00897b"            # 深青
    PINK = "#f472b6"            # 粉

    # ---- 中性 ----
    MUTED = "#9aa4b2"           # 次要说明文字（提示、计数、单位）
    MUTED_DARK = "#555555"      # 浅色底上的次要文字
    MUTED_MID = "#6b7280"       # 更弱的说明（卡片脚注）
    GRAY = "#8a8a8a"            # 未知/中性徽标
    DIM = "#b5b5b5"             # 深色卡片上的次要文字

    # ---- 文本 / 背景（固定值，不跟主题） ----
    WHITE = "#ffffff"
    BLACK = "#000000"
    TEXT = "#1a1a1a"            # 浅色主题默认前景
    BG = "#f4f6f8"              # 浅色主题默认背景
    TEXT_DIM = "#d0d0d0"        # 深色底默认文字

    # ---- 深色区域（控制台 / 卡片） ----
    CONSOLE_BG = "#101010"      # 控制台默认底色
    CONSOLE_BTN = "#1e1e1e"     # 控制台内按钮底
    CONSOLE_HIGHLIGHT = "#4a4a4a"  # 控制台选区
    CONSOLE_INFO = "#cfcfcf"    # 控制台普通信息（未自定义时）
    CONSOLE_CHAT = "#4fc3f7"    # 玩家聊天（青蓝）
    CONSOLE_WARN_DARK = "#7a5c00"  # 浅底上的警告文字
    CONSOLE_WARN_TINT = "#ffe082"  # 深底上的警告文字
    CARD_DARK = "#1e1f29"       # 深色卡片底
    ART_BG = "#3a3d47"          # 占位图底色
    SECTION_BG = "#e8e8e8"      # 分组标题行底色（主题不可用时的兜底）

    # ---- 图标卡片 ----
    ICON_ACCENT = "#4a9eff"     # 图标卡片的强调色（主题 sel 取不到时）
    ICON_FG = "#f2f2f2"         # 图标卡片默认字色（深底）
    ICON_IDLE = "#b0b0b0"       # 图标卡片次要字色


#: 全局唯一的色板实例
PAL = _Palette()


#: 加载器品牌色（图标底 / 列表圆点），按加载器名小写索引
LOADER_COLORS = {
    "fabric": "#4a9eff",
    "forge": "#e08a3c",
    "neoforge": "#a96ce0",
    "paper": "#3ec46d",
    "vanilla": "#9a7b5a",
    "unknown": PAL.GRAY,
}

#: 加载器圆点色（列表里的小圆点，比图标底色更深一档）
LOADER_DOT_COLORS = {
    "fabric": "#1976d2",
    "forge": "#e67e22",
    "neoforge": "#8e24aa",
    "paper": "#00897b",
    "vanilla": "#5d4037",
    "unknown": "#607d8b",
}

#: 无图标模组的占位方块：(前景色, 背景色) 轮换
PLACEHOLDER_PAIRS = (
    (PAL.ACCENT, "#e8f0ff"),
    (PAL.WARN_SOFT, "#fff4e0"),
    (PAL.SUCCESS_LIGHT, "#e6fbea"),
    (PAL.PURPLE_SOFT, "#f2edff"),
    (PAL.DANGER_LIGHT, "#ffecec"),
    (PAL.CYAN, "#e6fbff"),
    (PAL.PINK, "#ffeef6"),
    (PAL.YELLOW, "#fffbe6"),
)

#: 模组分类标签配色
TAG_COLORS = {
    "第三方登录": PAL.ACCENT,
    "前置库": PAL.WARN_SOFT,
    "Cupboard生态": PAL.WARN_SOFT,
    "Cupboard附属": PAL.WARN_SOFT,
    "优化": PAL.SUCCESS_LIGHT,
    "语音": PAL.SUCCESS_LIGHT,
    "监控": PAL.PURPLE_SOFT,
    "崩溃分析": PAL.DANGER_LIGHT,
    "工具": PAL.CYAN,
    "权限": PAL.YELLOW,
    "聊天": PAL.PINK,
    "聊天扩展": PAL.PINK,
    "基础指令": PAL.BLUE_SOFT,
    "防熊": PAL.DANGER_LIGHT,
    "建筑": PAL.CYAN,
    "区域保护": PAL.SUCCESS_LIGHT,
    "基础前置": PAL.WARN_SOFT,
}
