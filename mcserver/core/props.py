# -*- coding: utf-8 -*-
"""server.properties 读写：保留注释与顺序，支持可视化编辑。"""

import os

# 字段元信息：(键, 中文名, 类型, 选项/说明)
# 类型: enum / bool / int / str
FIELDS = [
    # ---- 世界 ----
    ("level-name",        "世界文件夹名", "str",
     "必须与存档文件夹名一致，否则会新建一个空世界"),
    ("level-seed",        "种子", "str", "留空则随机；只在新建世界时生效"),
    ("level-type",        "地图类型", "enum",
     "minecraft:normal|minecraft:flat|minecraft:large_biomes|"
     "minecraft:amplified|minecraft:single_biome_surface"),
    ("difficulty",        "难度", "enum", "peaceful|easy|normal|hard"),
    ("gamemode",          "游戏模式", "enum",
     "survival|creative|adventure|spectator"),
    ("hardcore",          "硬核模式", "bool", "开启后死亡即封禁，且不可关闭"),
    ("max-world-size",    "世界边界", "int", "半径格数，最大 29999984"),

    # ---- 视距 ----
    ("view-distance",     "视距", "int",
     "发给玩家的区块半径，直接决定能看到多远（建议 6~12）"),
    ("simulation-distance", "模拟距离", "int",
     "实体/红石/作物的运算范围（建议 4~8，太高浪费性能）"),

    # ---- 维度 ----
    ("allow-nether",      "开启下界", "bool", "关闭后无法进入下界"),
    ("allow-end",         "开启末地", "bool", "关闭后无法进入末地"),

    # ---- 玩家 ----
    ("max-players",       "最大人数", "int", "同时在线上限"),
    ("online-mode",       "正版验证", "bool",
     "关闭后任何人可用任意 ID 进入（仅内网/离线环境才关）"),
    ("white-list",        "白名单", "bool", "开启后只有白名单内的人能进"),
    ("enforce-whitelist", "强制白名单", "bool",
     "开启后踢出不在白名单的在线玩家"),
    ("server-port",       "端口", "int", "默认 25565"),
    ("spawn-protection",  "出生点保护", "int",
     "出生点周围多少格禁止非 OP 破坏，0 为关闭"),
    ("pvp",               "PVP", "bool", "玩家之间能否互相伤害"),
    ("allow-flight",      "允许飞行", "bool",
     "非创造模式能否飞行（需客户端装模组才生效）"),
    ("motd",              "服务器标语", "str", "在客户端服务器列表显示"),

    # ---- 刷怪 ----
    ("spawn-monsters",    "生成怪物", "bool", ""),
    ("spawn-animals",     "生成动物", "bool", ""),
    ("spawn-npcs",        "生成村民", "bool", ""),
    ("spawn-structures",  "生成建筑", "bool", "村庄/神殿/要塞等"),

    # ---- 其它 ----
    ("enable-command-block",  "命令方块", "bool", "冒险地图通常需要开启"),
    ("function-permission-level", "函数权限等级", "int", "1~4"),
    ("op-permission-level",   "OP 权限等级", "int", "1~4"),
    ("player-idle-timeout",   "挂机踢出", "int", "挂机多少分钟踢出，0 为不限"),
    ("enable-status",         "显示在线状态", "bool", ""),
    ("hide-online-players",   "隐藏玩家列表", "bool", ""),
    ("require-resource-pack", "强制资源包", "bool", ""),
    ("resource-pack",         "资源包地址", "str", "URL，玩家进服时下载"),
    ("resource-pack-sha1",    "资源包校验值", "str", "留空可跳过校验"),
    ("max-tick-time",         "单 tick 超时", "int",
     "毫秒，超时自动关闭服务器；-1 为禁用"),
    ("sync-chunk-writes",     "同步区块写入", "bool", "关闭可提升性能"),
    ("network-compression-threshold", "网络压缩阈值", "int",
     "-1 为关闭，256 为默认"),
    ("entity-broadcast-range-percentage", "实体广播范围", "int", "百分比"),
    ("max-chained-neighbor-updates", "连锁更新上限", "int", ""),
    ("enforce-secure-profile", "强制聊天签名", "bool",
     "外置登录/皮肤站环境常需关闭"),
    ("prevent-proxy-connections", "阻止代理连接", "bool", ""),
    ("use-native-transport",   "使用原生传输", "bool", "Linux 上性能更好"),
    ("snooper-enabled",        "发送统计数据", "bool", ""),
    ("enable-query",           "启用 Query", "bool", ""),
    ("enable-rcon",            "启用 RCON", "bool", ""),
    ("rcon.port",              "RCON 端口", "int", ""),
    ("rcon.password",          "RCON 密码", "str", ""),
    ("query.port",             "Query 端口", "int", ""),
    ("server-ip",              "绑定 IP", "str", "留空为全部网卡"),
    ("broadcast-console-to-ops", "控制台广播给 OP", "bool", ""),
    ("broadcast-rcon-to-ops",  "RCON 广播给 OP", "bool", ""),
    ("log-ips",                "记录 IP", "bool", ""),
    ("rate-limit",             "限速", "int", "0 为不限"),
    ("text-filtering-config",  "文本过滤配置", "str", ""),
    ("text-filtering-version", "文本过滤版本", "int", ""),
    ("pause-when-empty-seconds", "无人时暂停", "str", "留空为不暂停"),
    ("region-file-compression", "区块压缩", "enum", "deflate|gzip|none"),
    ("accepts-transfers",     "接受转移", "bool", ""),
    ("enable-jmx-monitoring", "JMX 监控", "bool", ""),
    ("initial-enabled-packs", "初始启用数据包", "str", ""),
    ("initial-disabled-packs", "初始禁用数据包", "str", ""),
    ("unlock-all-recipes",    "解锁全部配方", "bool", ""),
]

KEY_LABEL = {k: lbl for k, lbl, t, d in FIELDS}
KEY_TYPE = {k: t for k, lbl, t, d in FIELDS}
KEY_DESC = {k: d for k, lbl, t, d in FIELDS}


def parse(path):
    """读 server.properties，返回 (values, lines)。
    lines 保留原始每一行（含注释与空行），用于写回时保序。"""
    values = {}
    lines = []
    if not os.path.isfile(path):
        return values, lines
    with open(path, "r", encoding="utf-8", errors="replace") as f:
        raw = f.read()
    for line in raw.splitlines():
        lines.append(line)
        st = line.strip()
        if not st or st.startswith("#"):
            continue
        if "=" not in st:
            continue
        k, v = st.split("=", 1)
        values[k.strip()] = v
    return values, lines


def save(path, values, lines=None):
    """写回。已有键就地改值，新增键追加到末尾，保留注释。"""
    os.makedirs(os.path.dirname(path) or ".", exist_ok=True)
    if lines is None:
        lines = []
    out = []
    seen = set()
    for line in lines:
        st = line.strip()
        if st and not st.startswith("#") and "=" in st:
            k = st.split("=", 1)[0].strip()
            if k in values:
                out.append("%s=%s" % (k, values[k]))
                seen.add(k)
                continue
        out.append(line)
    for k in values:
        if k not in seen:
            out.append("%s=%s" % (k, values[k]))
    text = "\n".join(out)
    if not text.endswith("\n"):
        text += "\n"
    with open(path, "w", encoding="utf-8", newline="\n") as f:
        f.write(text)
    return path


def to_bool(v):
    return str(v).strip().lower() in ("true", "1", "yes", "on")


def from_bool(b):
    return "true" if b else "false"


def enum_options(key):
    d = KEY_DESC.get(key, "")
    if "|" in d:
        return [x for x in d.split("|") if x]
    return []


def display(key, v):
    """把值转成中文显示。"""
    t = KEY_TYPE.get(key, "str")
    if t == "bool":
        return "启用" if to_bool(v) else "禁用"
    if key == "difficulty":
        return {"peaceful": "和平", "easy": "简单",
                "normal": "普通", "hard": "困难"}.get(v, v)
    if key == "gamemode":
        return {"survival": "生存", "creative": "创造",
                "adventure": "冒险", "spectator": "旁观"}.get(v, v)
    return v


def from_display(key, shown):
    """中文显示值转回实际值。"""
    t = KEY_TYPE.get(key, "str")
    if t == "bool":
        return from_bool(shown in ("启用", "true", "True", True))
    if key == "difficulty":
        return {"和平": "peaceful", "简单": "easy",
                "普通": "normal", "困难": "hard"}.get(shown, shown)
    if key == "gamemode":
        return {"生存": "survival", "创造": "creative",
                "冒险": "adventure", "旁观": "spectator"}.get(shown, shown)
    return shown
