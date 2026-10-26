# -*- coding: utf-8 -*-
"""外观主题：字体 / 字体颜色 / 背景色 / 背景图片。

存成 JSON，主窗口启动时套 stylesheet。
背景图支持平铺或拉伸，并叠一层半透明色保证文字可读
（不然白字落在亮图上根本看不见）。
"""

import os
import json

# 默认配色：浅色系（浅灰白底 + 近黑字 + 蓝色强调）。
#   bg     #f4f6f8  浅灰白。不用纯白 #ffffff 是因为纯白在
#                   长时间看屏幕时反光更重，浅灰白更柔和；
#                   同时和白色卡片拉开一点点层次。
#   fg     #1a1a1a  近黑，与浅底对比度 ~16，最清楚
#   accent #0b62d0  偏深的蓝。浅底上必须用深一点的强调色，
#                   亮蓝 #4a9eff 在白底上对比度只有 2.6，看不清。
# ★ 深色档没有丢 —— 预设里"墨绿""深灰""纯黑""深蓝"都在，
#   想要暗色随时点一下就切回去。
DEFAULT = {
    "font_family": "",        # 空 = 用系统默认
    "font_size": 0,           # 0 = 用默认字号
    "fg": "#1a1a1a",          # 字体颜色（近黑）
    "bg": "#f4f6f8",          # 背景色（浅灰白）
    "accent": "#0b62d0",      # 强调色（深蓝）
    "bg_image": "",           # 背景图片路径
    "bg_mode": "stretch",     # stretch | tile
    "bg_opacity": 88,         # 背景图上的遮罩不透明度 0-100
    "font_bold": 0,           # 1 = 全局粗体
    # ---- PCL 风格（v5）----
    "pcl": 1,                 # 1 = 启用 PCL 风格选中/悬停
    "tab_idle": "#ffffff",    # 顶栏（标签页）常驻字体色：白
    "tab_sel_box": 1,         # 1 = 选中的标签套一圈白底
    "item_idle_mode": "dark", # 列表项目常驻字色：dark 暗 / light 亮 / fg 跟随
    "sel_bars": 1,            # 选中项竖条：0 无 / 1 只左侧 / 2 左右各一条
    "hover_tip": 1,           # 1 = 鼠标悬停时显示注释
    "v": 6,                   # 主题版本，用于迁移旧配置
}

# 常驻字色三档的含义（深色主题下"黑"会自动换成"暗但看得清"的灰，
# 直接写 #000000 在墨绿底上对比度只有 1.4，等于看不见）
ITEM_IDLE_MODES = [
    ("dark", "暗色（未选中时淡，选中才亮）"),
    ("light", "亮色"),
    ("fg", "跟随主字体色"),
]

# 预设：[名字, 前景, 背景, 强调色]
# ★ 深色档字体一律白色系（用户要求），浅色档一律近黑字 ——
#   浅底上写白字对比度 1.05，等于看不见，所以不能一套到底。
# ★ 前四个是浅色（默认推荐），后五个是深色。
PRESETS = [
    ("浅灰白（默认）", "#1a1a1a", "#f4f6f8", "#0b62d0"),
    ("纯白", "#1a1a1a", "#ffffff", "#0b62d0"),
    ("浅蓝", "#16233a", "#eef3fa", "#0b62d0"),
    ("浅绿", "#1b2a22", "#eef5ef", "#2e7d32"),
    ("墨绿", "#ffffff", "#1b2a22", "#3ec46d"),
    ("深灰", "#ffffff", "#252525", "#4a9eff"),
    ("纯黑", "#ffffff", "#101010", "#5ac8fa"),
    ("深蓝", "#ffffff", "#16233a", "#4a9eff"),
    ("暖棕", "#ffffff", "#2b231c", "#e08a3c"),
]

def is_dark_bg(bg=None):
    """这个背景算深色吗（按相对亮度判断，不靠名单硬记）。"""
    try:
        return luma(bg or _cfg.get("bg") or DEFAULT["bg"]) < 0.5
    except Exception:
        return False


def preset_apply(name):
    """按名字套预设，返回 (fg, bg, accent, item_idle_mode)。

    ★ 额外返回 item_idle_mode：深浅切换时如果不管它，
      浅色档的"黑字"用到墨绿底上就是一片消失（对比度 1.4），
      反过来深色的"亮字"到白底上同样看不见。
    """
    for nm, fg, bg, ac in PRESETS:
        if nm == name or nm.startswith(name):
            dark = is_dark_bg(bg)
            return fg, bg, ac, ("light" if dark else "dark")
    d = DEFAULT
    return d["fg"], d["bg"], d["accent"], "dark"


_cfg = dict(DEFAULT)

# ★ 主题"版本号"。
#   界面上有些东西（图标卡片的文字是自绘的）会把主题缓存几秒，
#   免得每一行都去读配置文件。缓存带来一个副作用：
#   【改完主题立刻重画的那一次，用的还是旧值】——
#   而之后如果没有新的重绘，就永远停在旧样子。
#   表现就是"取消勾选粗体却没有效果"。
#   所以每次主题真的变了就 +1，缓存方比对这个值决定是否重取。
_rev = 0


def rev():
    return _rev


def bump():
    global _rev
    _rev += 1
    return _rev


def path():
    try:
        base = os.path.dirname(os.path.dirname(
            os.path.dirname(os.path.abspath(__file__))))
    except Exception:
        base = os.getcwd()
    return os.path.join(base, "theme.json")


def load():
    before = dict(_cfg)
    p = path()
    try:
        with open(p, "r", encoding="utf-8") as f:
            d = json.load(f)
        if isinstance(d, dict):
            for k in DEFAULT:
                if k in d:
                    _cfg[k] = d[k]
    except Exception:
        pass
    # 迁移：以前默认前景色偏灰（#d8d8d8），用户反馈"太虚"。
    # 只有"还是旧默认值"时才提亮，用户自己改过的不动。
    if (_cfg.get("fg") or "").lower() in ("#d8d8d8", "#cccccc", "#c0c0c0"):
        _cfg["fg"] = DEFAULT["fg"]
    # 迁移到 v4：默认配色改墨绿。
    #   只看背景色是不是旧默认（#2c3644 蓝灰）—— 背景是"看着怪"
    #   的主要来源；用户自己挑过背景的不动，免得冲掉心头好。
    if int(_cfg.get("v") or 0) < 4:
        if (_cfg.get("bg") or "").lower() in ("#2c3644", "#252525"):
            _cfg["fg"] = DEFAULT["fg"]
            _cfg["bg"] = DEFAULT["bg"]
            _cfg["accent"] = DEFAULT["accent"]
        _cfg["v"] = DEFAULT["v"]
        try:
            save()
        except Exception:
            pass
    # 迁移到 v6：默认配色改浅色系（用户反馈"亮色看着更舒服"）。
    #   只动"还是墨绿默认值"的 —— 墨绿是 v5.4 设的默认，
    #   用户若自己挑过背景（比如纯黑），一律不动。
    if int(_cfg.get("v") or 0) < 6:
        if (_cfg.get("bg") or "").lower() == "#1b2a22":
            _cfg["fg"] = DEFAULT["fg"]
            _cfg["bg"] = DEFAULT["bg"]
            _cfg["accent"] = DEFAULT["accent"]
            _cfg["item_idle_mode"] = "dark"
        _cfg["v"] = DEFAULT["v"]
        try:
            save()
        except Exception:
            pass
    # ★ 颜色字段被写坏时（比如 theme.json 里 "fg": false）恢复默认。
    #   不修的话后面 QColor()/CSS 会拿到非字符串，轻则颜色错乱重则崩。
    fixed_color = False
    for k in ("fg", "bg", "accent"):
        v = _cfg.get(k)
        if not isinstance(v, str) or not v.strip().startswith("#"):
            _cfg[k] = DEFAULT[k]
            fixed_color = True
    if fixed_color:
        try:
            save()
        except Exception:
            pass

    # ★ 兜底：配置里若存着"字体色≈背景色"（用户自己调的、或旧版留下的），
    #   一进界面就是一片糊 —— 表现可能像"卡住"（其实只是全看不见）。
    #   这里在加载时就修正，保证无论如何界面都可读。
    fg0 = _cfg.get("fg") or DEFAULT["fg"]
    bg0 = _cfg.get("bg") or DEFAULT["bg"]
    try:
        if contrast_ratio(fg0, bg0) < 2.5:
            _cfg["fg"] = ensure_contrast(fg0, bg0, 4.5)[0]
            try:
                save()
            except Exception:
                pass
    except Exception:
        pass

    # ★ 系统里没有的字体名会让 DirectWrite 反复创建字体面失败
    #   （qt.qpa.fonts: CreateFontFaceFromHDC() failed），
    #   界面会明显发涩、极端情况下像卡死。加载时直接清掉。
    fam = (_cfg.get("font_family") or "").strip()
    if fam and not valid_font(fam):
        try:
            print("[主题] 配置里的字体 %r 本机不存在，已忽略" % fam)
        except Exception:
            pass
        _cfg["font_family"] = ""
    # ★ 只要配置真的变了就 +1，让界面上的缓存立刻失效
    if _cfg != before:
        bump()
    return dict(_cfg)


def save(d=None):
    """保存主题。

    ★ 以前失败静默吞掉（except: pass）。
      后果：你改完配色/字体，界面照常弹出"已应用"，
      但磁盘上根本没写进去 —— 重启后全部回到旧值。
      表现就是"改了没用"，而日志里什么都没有，极难排查。

    现在返回 (ok, err)，调用方必须看结果再决定说什么。
    """
    if d:
        for k in DEFAULT:
            if k in d:
                _cfg[k] = d[k]
        # ★ 让界面上自绘文字的缓存立刻失效（不然"取消粗体"看不出变化）
        bump()
    p = path()
    try:
        d0 = os.path.dirname(p)
        if d0:
            os.makedirs(d0, exist_ok=True)
        with open(p, "w", encoding="utf-8") as f:
            json.dump(_cfg, f, ensure_ascii=False, indent=2)
        return (True, p)
    except Exception as e:
        return (False, "%s  (%s)" % (repr(e), p))


def get(k=None):
    return dict(_cfg) if k is None else _cfg.get(k)


def _hex2rgb(h):
    h = (h or "").lstrip("#")
    if len(h) == 3:
        h = "".join(c * 2 for c in h)
    try:
        return tuple(int(h[i:i + 2], 16) for i in (0, 2, 4))
    except Exception:
        return (0, 0, 0)


def luma(hex_color):
    """WCAG 相对亮度 0~1。

    ★ 用来判断"字体色和背景色是不是太接近"。
      不能用简单 RGB 差值 —— 人眼对绿色最敏感、蓝色最不敏感，
      纯 RGB 差会把 (#ffffff / #fffffe) 当成差很多，也会把
      (#808080 灰 / #00ff00 亮绿) 误判成差不多。
    """
    r, g, b = _hex2rgb(hex_color)

    def ch(c):
        c = c / 255.0
        return c / 12.92 if c <= 0.03928 else ((c + 0.055) / 1.055) ** 2.4
    return 0.2126 * ch(r) + 0.7152 * ch(g) + 0.0722 * ch(b)


def contrast_ratio(a, b):
    """两色对比度 1~21。低于 3 基本看不清，4.5 是可读线。"""
    l1, l2 = luma(a), luma(b)
    if l1 < l2:
        l1, l2 = l2, l1
    return (l1 + 0.05) / (l2 + 0.05)


MIN_CONTRAST = 4.5


def ensure_contrast(fg, bg, min_ratio=MIN_CONTRAST):
    """字体色和背景色太接近时，把字体色推向黑或白。

    返回 (新字体色, 是否改过)。

    ★ 为什么是改字体色而不是改背景？
      背景往往是用户挑了半天的底色（还可能带背景图），
      动它等于把整个界面换掉；字体色改了只会影响可读性。
      推向哪边由背景亮度决定：亮底推黑、暗底推白 ——
      这样能拿到该背景下的最大对比度。

    ★ 逐档加深/提亮而不是一步到黑/白：
      用户选了个蓝色字，直接变纯白太粗暴；
      先试着按 15% 一档挪，够用就停，保留原来的色相感。
    """
    def clamp(v):
        return max(0, min(255, int(v)))
    try:
        if contrast_ratio(fg, bg) >= min_ratio:
            return fg, False
    except Exception:
        return fg, False
    # 推向哪边：【比较两端实际对比度】再决定，不能只看"背景算不算暗"。
    # 反例：背景 #7f7f7f（中灰）算暗，但配白字只有 3.95，配黑字有 5.32
    #       —— 按"暗底推白"的老写法反而推错方向，永远达不到 4.5。
    target_white = (contrast_ratio("#ffffff", bg)
                    >= contrast_ratio("#000000", bg))
    r, g, b = _hex2rgb(fg)
    for step in range(1, 21):
        t = step / 20.0
        if target_white:
            c = (clamp(r + (255 - r) * t), clamp(g + (255 - g) * t),
                 clamp(b + (255 - b) * t))
        else:
            c = (clamp(r * (1 - t)), clamp(g * (1 - t)),
                 clamp(b * (1 - t)))
        cand = "#%02x%02x%02x" % c
        if contrast_ratio(cand, bg) >= min_ratio:
            return cand, True
    # 逐档都不够（极少见）：直接用两端里对比度更高的那一端
    w, b_ = "#ffffff", "#000000"
    return (w if contrast_ratio(w, bg) >= contrast_ratio(b_, bg) else b_), True


def _mix(a, b, t):
    """把颜色 a 朝 b 混合 t（0~1）。"""
    r1, g1, b1 = _hex2rgb(a)
    r2, g2, b2 = _hex2rgb(b)
    m = lambda x, y: max(0, min(255, int(x + (y - x) * t)))
    return "#%02x%02x%02x" % (m(r1, r2), m(g1, g2), m(b1, b2))


def muted_color(fg=None, bg=None, min_ratio=MIN_CONTRAST):
    """次要文字色（提示/说明用）——比主文字淡，但仍必须看得清。

    ★ 以前各处直接写死 color:#666。那串灰色在深色底上对比度只有
      2.1~2.7（可读线是 4.5），表现就是"提示文字像没显示"。
      这里按主题现算：把字体色朝背景色方向一档档调淡，
      取【最淡但仍达标】的那一档 —— 既保留层次又不会看不见。
    """
    fg = fg or _cfg.get("fg") or DEFAULT["fg"]
    bg = bg or _cfg.get("bg") or DEFAULT["bg"]
    best = fg
    for i in range(1, 10):
        c = _mix(fg, bg, i / 10.0)
        if contrast_ratio(c, bg) >= min_ratio:
            best = c
        else:
            break
    return best


def on_bg(color, bg=None, min_ratio=MIN_CONTRAST):
    """把任意颜色调整到"在当前背景上读得清"（尽量保留原色相）。

    ★ 代码里有不少写死的语义色（蓝 #1565c0、深灰 #555、
      纯黑 #000000…），它们在浅色底上没问题，换到深色主题
      就糊成一团 —— 对比度只有 2 左右。
      这里朝字体色方向一档档提亮，够用就停，红还是红、绿还是绿。
    """
    bg = bg or _cfg.get("bg") or DEFAULT["bg"]
    fg = _cfg.get("fg") or DEFAULT["fg"]
    try:
        if contrast_ratio(color, bg) >= min_ratio:
            return color
    except Exception:
        return color
    for i in range(1, 11):
        cand = _mix(color, fg, i / 10.0)
        try:
            if contrast_ratio(cand, bg) >= min_ratio:
                return cand
        except Exception:
            break
    return fg


def pcl_enabled():
    return int(_cfg.get("pcl") or 0) == 1


def tab_idle_color():
    """顶栏（标签页）未选中时的字色 —— PCL 里是常驻白字。

    ★ 浅色主题下纯白在亮底上等于看不见（对比度 1.05），
      这里自动推到该背景上读得清为止，深底上仍保持纯白。
    """
    bg = _cfg.get("bg") or DEFAULT["bg"]
    c = (_cfg.get("tab_idle") or "").strip() or "#ffffff"
    return ensure_contrast(c, bg, MIN_CONTRAST)[0]


def tab_sel_bg():
    """选中标签的底色 —— PCL 是一圈白底。关掉时用主题淡底。

    ★ 浅色主题下"白底框"等于没有框（背景本来就接近白），
      选中看着像没选中。这时改成主题色淡底，仍然有 PCL 那种
      "选中被框起来"的观感，只是框是淡蓝/淡绿而不是白。
    """
    bg = _cfg.get("bg") or DEFAULT["bg"]
    ac = _cfg.get("accent") or DEFAULT["accent"]
    if int(_cfg.get("tab_sel_box") or 0):
        # 白底必须和背景拉得开，否则框看不见
        if contrast_ratio("#ffffff", bg) >= 1.35:
            return "#ffffff"
        return _mix(bg, ac, 0.22)
    return _mix(bg, ac, 0.30)


def tab_sel_fg():
    """选中标签的字色 = 主题色，但必须在【白底】上读得清。

    ★ 直接用 accent 会出问题：墨绿 #3ec46d 落在白底上对比度
      只有 2.1（可读线 4.5），看着就是"选中了反而看不清"。
      这里把主题色朝可读方向压深，仍然是绿色系。
    """
    ac = _cfg.get("accent") or DEFAULT["accent"]
    box = tab_sel_bg()
    return ensure_contrast(ac, box, MIN_CONTRAST)[0]


def item_idle_color():
    """列表项目未选中时的字色（PCL 里是常驻黑字）。

    ★ 深色主题下不能真的用纯黑 —— 墨绿底上对比度 1.4，整片消失。
      这里"朝背景方向调暗到最暗但仍达标"的那一档：
        深色主题 → 淡灰（比主文字暗，选中才亮起来，层次分明）
        浅色主题 → 接近黑（就是 PCL 原本的观感）
    """
    mode = (_cfg.get("item_idle_mode") or "dark").lower()
    if mode == "fg":
        return _cfg.get("fg") or DEFAULT["fg"]
    if mode == "light":
        fg = _cfg.get("fg") or DEFAULT["fg"]
        return ensure_contrast(fg, _cfg.get("bg") or DEFAULT["bg"],
                               MIN_CONTRAST)[0]
    return muted_color(min_ratio=MIN_CONTRAST)


def item_sel_color():
    """列表项目选中时的字色 = 主题色（保证在背景上读得清）。"""
    ac = _cfg.get("accent") or DEFAULT["accent"]
    bg = _cfg.get("bg") or DEFAULT["bg"]
    return ensure_contrast(ac, bg, MIN_CONTRAST)[0]


def sel_bar_mode():
    """选中项竖条：0 无 / 1 左 / 2 左右各一条（默认）。"""
    if not pcl_enabled():
        return 0
    try:
        # ★ 用 is not None 判断，而不是 `or 1`。否则用户明确选择 0（不画）会被 or 1 强制改成 1。
        sb = _cfg.get("sel_bars")
        if sb is None:
            return 1
        return max(0, min(2, int(sb)))
    except Exception:
        return 1


def hover_tip_enabled():
    return pcl_enabled() and int(_cfg.get("hover_tip") or 0) == 1


def section_bg():
    """分组标题行的底色：比背景亮一点，但绝不能是浅灰。

    ★ 以前写死 #e8e8e8，深色主题下就是"浅灰底 + 白字"——
      分组标题（① 前置、② 核心优化…）整行看不见。
    """
    return _mix(_cfg.get("bg") or DEFAULT["bg"],
                _cfg.get("accent") or DEFAULT["accent"], 0.30)


def muted_css():
    """次要文字的 CSS 片段，供各处提示标签直接用。"""
    return "color:%s;" % muted_color()


def field_bg():
    """输入框底色：比背景略亮一点点，让输入区能看出来。"""
    return _mix(_cfg.get("bg") or DEFAULT["bg"],
                _cfg.get("fg") or DEFAULT["fg"], 0.08)


def overlay_rgba(hex_color, opacity):
    """把 #rrggbb 变成 rgba(r,g,b,0.xx) —— 用作背景图上的遮罩。"""
    r, g, b = _hex2rgb(hex_color)
    a = max(0, min(100, int(opacity))) / 100.0
    return "rgba(%d,%d,%d,%.2f)" % (r, g, b, a)


def font_css():
    """生成字体 CSS。

    ★ 以前写的是 font: "字体名";  —— CSS 的 font 简写
      【必须】包含 font-size，只有 family 时整条声明无效，
      Qt 会静默丢弃，表现就是"选了字体但没变化"。

      现在拆成 font-family / font-size 两条独立声明，
      单独设任何一个都有效。
    """
    fam = (_cfg.get("font_family") or "").strip()
    size = int(_cfg.get("font_size") or 0)
    out = []
    if fam:
        out.append('font-family: "%s";' % fam)
    if size > 0:
        out.append("font-size: %dpt;" % size)
    if int(_cfg.get("font_bold") or 0):
        out.append("font-weight: bold;")
    return " ".join(out)


def valid_font(fam):
    """字体在不在系统里。

    ★ 用户机器上出现过：
        qt.qpa.fonts: DirectWrite: CreateFontFaceFromHDC() failed
        ... "MS Sans Serif"
      这是设了一个系统里没有的字体名。Qt 每次绘制都要
      重试创建字体面，界面会明显发涩、极端情况下像卡住。
      所以设之前先查 QFontDatabase。
    """
    fam = (fam or "").strip()
    if not fam:
        return True          # 空 = 系统默认，合法
    try:
        from PySide6.QtGui import QFontDatabase
        have = set(QFontDatabase.families())
        if fam in have:
            return True
        # 有些写法带样式后缀，如 "Microsoft YaHei UI Light"
        for f in have:
            if f.lower() == fam.lower():
                return True
        return False
    except Exception:
        return True          # 查不了就别拦，避免误伤


def apply_font(app=None):
    """把字体设到整个应用上（最可靠的方式）。

    ★ 为什么不能只靠 stylesheet？
      Qt stylesheet 里 QWidget{font-family:...} 对很多控件
      （尤其是已经设过 font 的、或走 palette 的）不一定生效，
      表现就是"选了字体但界面没变化"。

      QApplication.setFont() 是 Qt 最底层的字体机制：
      所有没被单独设过字体的控件都会用它 —— 这一条一定生效。
    """
    try:
        from PySide6.QtGui import QFont
        from PySide6.QtWidgets import QApplication
        app = app or QApplication.instance()
        if app is None:
            return False
        fam = (_cfg.get("font_family") or "").strip()
        # ★ 系统里没有的字体名会让 DirectWrite 反复创建字体面失败
        #   （界面发涩 / 像卡住），这里直接忽略掉，退回系统默认。
        if fam and not valid_font(fam):
            try:
                print("[主题] 字体 %r 在本机不存在，已退回系统默认" % fam)
            except Exception:
                pass
            fam = ""
        size = int(_cfg.get("font_size") or 0)

        # ★ 关键：QFont() 不是"全新的默认字体"，它继承
        #   当前 app.font()。以前只写 if bold: f.setBold(True)，
        #   于是取消勾选粗体时，f 仍从 app 继承着上一次的粗体 ——
        #   再 setFont 回去，界面还是粗的，"取消了却没效果"。
        #   现在以【系统原始字体】为基准，并且两个方向都显式设置。
        base = _sys_font(app)
        f = QFont(base)
        if fam:
            f.setFamily(fam)
        if size > 0:
            f.setPointSize(size)
        else:
            # 没设字号时用系统默认字号，别把界面缩成 0
            f.setPointSize(max(base.pointSize(), 9))
        # ★ 两个方向都要设：关掉粗体时必须显式 setBold(False)
        f.setBold(bool(int(_cfg.get("font_bold") or 0)))
        app.setFont(f)
        return True
    except Exception:
        return False


_sys_font_cache = {"f": None}


def _sys_font(app):
    """系统原始字体（第一次调用时记下来）。

    之后每次都以它为基准，这样取消字体/字号/粗体时
    能真正回到系统默认，而不是叠加上一次的设置。
    """
    try:
        from PySide6.QtGui import QFont
        if _sys_font_cache["f"] is None:
            _sys_font_cache["f"] = QFont(app.font())
        return _sys_font_cache["f"]
    except Exception:
        from PySide6.QtGui import QFont
        return QFont()


def apply_palette(root):
    """把主题色写进 Qt 【调色板】，兜住 stylesheet 盖不到的控件。

    ★ 为什么光靠 stylesheet 不够？
      Qt 在没有任何 stylesheet 指定背景的控件上，会回落到
      【系统调色板】。Windows 的 Window 色是纯白 #ffffff，
      于是任何一个漏写的容器都是白底 —— 白底 + 白字 = 一片空白，
      表现就是"这一块的字全不见了"。

      ★ 这类问题在 Linux 上根本复现不出来（系统调色板偏灰，
        QScrollArea 那几条规则看着就够了），所以之前几轮我
        测着没问题、你那边却一直白 —— 根子就在这。

      调色板是 Qt 的最后一层兜底：stylesheet 优先，
      所以已经设过的地方不会被这里改掉，只会补上漏网之鱼。
    """
    try:
        from PySide6.QtGui import QPalette, QColor
        from PySide6.QtWidgets import QWidget, QAbstractScrollArea
    except Exception:
        return 0
    fg = _cfg.get("fg") or DEFAULT["fg"]
    bg = _cfg.get("bg") or DEFAULT["bg"]
    fb = field_bg()
    n = 0
    try:
        widgets = [root] + list(root.findChildren(QWidget))
    except Exception:
        widgets = [root]
    for w in widgets:
        try:
            p = w.palette()
            # Base/Text：输入类控件的底和字
            p.setColor(QPalette.Base, QColor(fb))
            p.setColor(QPalette.Text, QColor(fg))
            # Window/WindowText：普通容器和标签
            p.setColor(QPalette.Window, QColor(bg))
            p.setColor(QPalette.WindowText, QColor(fg))
            p.setColor(QPalette.Button, QColor(bg))
            p.setColor(QPalette.ButtonText, QColor(fg))
            p.setColor(QPalette.ToolTipBase, QColor(bg))
            p.setColor(QPalette.ToolTipText, QColor(fg))
            p.setColor(QPalette.PlaceholderText, QColor(muted_color()))
            w.setPalette(p)
            n += 1
            # ★ 滚动区的 viewport：白底最顽固的一处。
            #   QTableWidget/QListWidget 设了 background 也只是给它自己，
            #   里面那层 viewport 仍是独立 QWidget，stylesheet 常漏掉，
            #   在 Windows 上就露出纯白 —— 表格内容区整片白。
            if isinstance(w, QAbstractScrollArea):
                try:
                    vp = w.viewport()
                    if vp is not None:
                        vp.setPalette(p)
                        vp.setAutoFillBackground(True)
                        # ★ V1.7.3-4：以前这里只给 viewport 写一条
                        #   background/color 的【局部样式表】—— 但 Qt 里
                        #   任何自带局部样式表的控件都会成为样式表的
                        #   “分界点”，上层（对话框/主窗口）的
                        #   QPushButton:hover / QPushButton:pressed 等规则
                        #   从此无法级联到它的子孙控件 —— 表现就是
                        #   QScrollArea 里的按钮只剩边框、没有悬停/按下动效
                        #   （真机实测：CreateDialog 里“刷新版本列表”“浏览”
                        #   等按钮全中招，只有弹窗直下的“开始/Cancel”正常）。
                        #   现在给 viewport 套【完整主题样式表】+ 一条
                        #   viewport 自身的底色/字色覆盖：
                        #     · 完整样式表把 QPushButton 动效等所有规则
                        #       送进滚动区内部（按钮恢复悬停/按下反馈）
                        #     · 底部那条 QWidget#<objname> 覆盖保住原有的
                        #       表格/滚动区白底修复（palette 会被上层
                        #       样式表的 transparent 吃掉，仍需要这一条）。
                        try:
                            _full = stylesheet(None)
                            _on = vp.objectName()
                            if _on:
                                _full += ("\nQWidget#%s { background-color: %s;"
                                          " color: %s; }" % (_on, fb, fg))
                            vp.setStyleSheet(_full)
                        except Exception:
                            pass
                        n += 1
                except Exception:
                    pass
        except Exception:
            pass
    return n


def _tab_css():
    """标签页样式。PCL 风格：常驻白字 + 选中换主题色并套一圈白底。"""
    fcss = font_css()
    if not pcl_enabled():
        bg, ac = _cfg.get("bg") or DEFAULT["bg"], _cfg.get("accent") or DEFAULT["accent"]
        # ★ 选中标签的字色以前写死 #fff。浅色主题下选中底是
        #   淡蓝（accent 30% 叠在浅底上），白字落上去对比度不到 4，
        #   表现就是"选中了反而看不清"。这里按实际底色现算。
        sel_box = _mix(bg, ac, 0.30)
        sel_fg = ensure_contrast("#ffffff", sel_box, MIN_CONTRAST)[0]
        return ("QTabBar::tab { background: %s; color: %s; padding: 6px 14px;"
                " border: none; margin-right: 2px; %s }\n"
                "QTabBar::tab:selected { background: %s; color: %s;"
                " border-bottom: 2px solid %s; }\n"
                "QTabBar::tab:hover { background: %s; }"
                % (overlay_rgba(bg, 85), _cfg.get("fg") or DEFAULT["fg"],
                   fcss, overlay_rgba(ac, 30), sel_fg, ac,
                   overlay_rgba(ac, 15)))
    return ("QTabBar::tab { background: transparent; color: %s;"
            " padding: 6px 14px; border: 1px solid transparent;"
            " border-radius: 3px; margin-right: 2px; %s }\n"
            "QTabBar::tab:selected { background: %s; color: %s;"
            " border: 1px solid %s; }\n"
            "QTabBar::tab:hover { background: %s; }\n"
            "QTabBar::tab:selected:hover { background: %s; }"
            % (tab_idle_color(), fcss, tab_sel_bg(), tab_sel_fg(),
               tab_sel_bg(),
               overlay_rgba(_cfg.get("accent") or DEFAULT["accent"], 15),
               tab_sel_bg()))


def stylesheet(target=None):
    """生成完整 stylesheet。

    target: 要套背景图的控件类名（一般是 QMainWindow）。
    背景图只给主窗口，子对话框不套 —— 否则每个弹窗都带一张图，
    又慢又难看。
    """
    fg = _cfg.get("fg") or DEFAULT["fg"]
    bg = _cfg.get("bg") or DEFAULT["bg"]
    ac = _cfg.get("accent") or DEFAULT["accent"]
    fcss = font_css()
    op = int(_cfg.get("bg_opacity") or 88)

    B = lambda o: overlay_rgba(bg, o)      # 背景色的半透明版
    # 有背景图时，主窗口背景透明，让图片透出来
    img = (_cfg.get("bg_image") or "").strip()
    has_img = bool(img) and os.path.isfile(img)
    if has_img and target:
        p2 = img.replace("\\", "/")
        # ★ url() 一定要加引号：路径里有中文/空格/括号时，
        #   不加引号 Qt 会整条 CSS 解析失败（表现就是"换了背景没反应"）
        if _cfg.get("bg_mode") == "tile":
            img_css = ('background-image: url("%s");'
                       "background-repeat: repeat;" % p2)
        else:
            img_css = ('background-image: url("%s");'
                       "background-repeat: no-repeat;"
                       "background-position: center;"
                       "background-size: cover;" % p2)
        win_bg = img_css + " background-color: transparent;"
    else:
        win_bg = "background-color: %s;" % bg
    # centralwidget 一律实底（有图时叠遮罩，无图时纯色）
    win_bg2 = ("background-color: %s;" % B(96)) if has_img \
        else ("background-color: %s;" % bg)

    A = lambda o: overlay_rgba(ac, o)      # 强调色的半透明版

    css = """
/* ---- 主窗口 ----
   ★ QMainWindow 的可见区域其实是 centralWidget，
     只写 QMainWindow{background} 会被它盖住看不见，
     必须同时给 centralwidget 和 QWidget 兜底。 */
%(TARGET)s {
    %(WIN_BG)s
}
%(TARGET)s::centralwidget, %(TARGET)s > QWidget {
    %(WIN_BG2)s
}
QWidget {
    color: %(FG)s;
    %(FONT)s
}

/* ---- 对话框：必须给实底 ----
   主窗口可以铺背景图（会变 transparent），
   但对话框只设了文字色、没设底色 —— 亮字落在系统浅底上
   就成了"字和背景融为一体"。这里强制实底。 */
QDialog, QMessageBox, QWizard {
    background-color: %(B98)s;
    color: %(FG)s;
}
QDialog QGroupBox, QMessageBox QGroupBox {
    background-color: %(B92)s;
    color: %(FG)s;
}
QDialog QLabel, QMessageBox QLabel {
    background-color: transparent;
    color: %(FG)s;
    %(FONT)s
}
QMainWindow::separator { background: %(AC)s; }

/* ---- ★ 滚动区容器：白底白字的真凶 ----
   QScrollArea 的 viewport 默认 autoFillBackground=True，
   拿的是系统调色板的 Window（Windows 上是 #efefef 近白），
   而 QWidget{...} 只设了 color 没设 background —— 盖不住它。
   结果就是『服务器配置』这类带滚动区的页面白底 + 白字。
   viewport 的内部对象名固定为 qt_scrollarea_viewport，
   用 #id 选择器才能精确命中。 */
QScrollArea, QAbstractScrollArea,
#qt_scrollarea_viewport,
#qt_scrollarea_hcontainer, #qt_scrollarea_vcontainer {
    background-color: %(B92)s;
}
QDialog QScrollArea, QDialog #qt_scrollarea_viewport {
    background-color: %(B92)s;
}
/* ★ 真正的元凶：QScrollArea.setWidget(w) 会把 w 的
   autoFillBackground 强制设成 true，于是 w 用系统调色板的
   Window 色（#efefef 近白）铺满整个内容区。
   w 是 viewport 的直接子，用这条子选择器才能命中。 */
QScrollArea > QWidget > QWidget,
QAbstractScrollArea > QWidget > QWidget {
    background-color: %(B92)s;
}
/* 同理：这些容器也可能露出系统底色 */
QSplitter, QFrame, QStackedWidget, QMdiArea {
    background-color: %(B90)s;
}
QTabWidget::pane > QWidget, QTabWidget > QWidget {
    background-color: transparent;
}

/* ---- 输入类：给实底色，不然叠在图上没法打字 ---- */
QLineEdit, QTextEdit, QPlainTextEdit, QComboBox, QSpinBox {
    background-color: %(B95)s;
    color: %(FG)s;
    border: 1px solid %(AC)s;
    border-radius: 3px;
    padding: 2px 4px;
    selection-background-color: %(AC)s;
    %(FONT)s
}
QComboBox QAbstractItemView {
    background: %(B98)s; color: %(FG)s;
    selection-background-color: %(AC)s;
}

/* ---- 表格 / 列表 ---- */
QTableWidget, QListWidget, QTreeWidget {
    %(FONT)s
    background-color: %(B80)s;
    gridline-color: %(AC)s;
    border: 1px solid %(AC)s;
    border-radius: 4px;
}
QHeaderView::section {
    background-color: %(B92)s;
    color: %(FG)s;
    %(FONT)s
    padding: 4px 6px;
    border: none;
    border-right: 1px solid %(AC)s;
}
/* ---- 选中项 ----
   PCL 风格：淡底 + 主题色字 + 左右两条竖杠（竖杠由委托画）。
   不用实心主题色块 —— 那样选中一整行太重，而且和"常驻暗色字"
   反差过大反而看不出"选中才亮起来"的层次。 */
QTableWidget::item:selected, QListWidget::item:selected {
    background-color: %(SEL_BG)s;
    color: %(ITEM_SEL)s;
}
QTableWidget::item:hover, QListWidget::item:hover {
    background-color: %(A15)s;
}

/* ---- 按钮 ---- */
QPushButton {
    background-color: %(B90)s;
    color: %(FG)s;
    border: 1px solid %(AC)s;
    border-radius: 4px;
    padding: 5px 12px;
    %(FONT)s
}
QPushButton:hover { background-color: %(A45)s; border-color: %(AC)s; }
QPushButton:pressed { background-color: %(A75)s; }
QPushButton:disabled { color: #9a9a9a; background-color: %(B80)s; }

/* ---- 标签页 ---- */
QTabWidget::pane { border: 1px solid %(AC)s; border-radius: 4px;
                   background: %(B85)s; }
%(TAB_CSS)s

/* ---- 其它 ---- */
QGroupBox { border: 1px solid %(AC)s; border-radius: 4px;
            margin-top: 8px; padding-top: 6px; %(FONT)s }
QGroupBox::title { subcontrol-origin: margin; left: 10px;
                   padding: 0 4px; color: %(AC)s; %(FONT)s }
QToolBar { background: %(B75)s; border: none; padding: 4px;
           spacing: 3px; }
QToolButton { background: transparent; border-radius: 4px;
              padding: 4px 8px; color: %(FG)s; %(FONT)s }
QToolButton:hover { background: %(A20)s; }
QMenuBar { background: %(B92)s; color: %(FG)s; %(FONT)s }
QMenuBar::item:selected { background: %(AC)s; }
QMenu { background: %(B98)s; color: %(FG)s;
        border: 1px solid %(AC)s; %(FONT)s }
QMenu::item:selected { background: %(AC)s; }
QScrollBar:vertical { background: %(B88)s; width: 10px; }
QScrollBar::handle:vertical { background: %(AC)s; border-radius: 5px; }
QScrollBar:horizontal { background: %(B88)s; height: 10px; }
QScrollBar::handle:horizontal { background: %(AC)s;
                                border-radius: 5px; }
QProgressBar { border: 1px solid %(AC)s; border-radius: 3px;
               background: %(B80)s; text-align: center; }
QProgressBar::chunk { background-color: %(AC)s; }
"""
    d = {
        "TARGET": target or "QMainWindow",
        "WIN_BG": win_bg, "WIN_BG2": win_bg2,
        "FG": fg, "AC": ac, "FONT": fcss,
        "TAB_CSS": _tab_css(),
        "SEL_BG": (overlay_rgba(ac, 26) if pcl_enabled()
                   else ac),
        "ITEM_SEL": (item_sel_color() if pcl_enabled() else "#ffffff"),
        "B75": B(75), "B80": B(80), "B85": B(85), "B88": B(88),
        "B90": B(90), "B92": B(92), "B95": B(95), "B98": B(98),
        "A15": A(15), "A20": A(20), "A30": A(30), "A45": A(45),
        "A55": A(55), "A75": A(75),
    }
    for k, v in d.items():
        css = css.replace("%%(%s)s" % k, v)
    return css



