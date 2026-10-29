# -*- coding: utf-8 -*-
"""已装模组的配置生成。

为什么要有这个模块：
  有些优化模组装上只是"能用"，真正出效果要靠配置，
  尤其是 ServerCore —— 它的动态视距/模拟距离/刷怪上限
  全都写在 config 里，不配就是默认值，等于白装一半。
  用户明确要求"要配置的模组能在『服务器配置』里配"。

这里只做文本生成 + 落盘，不引入 pyyaml 之类的依赖：
  手写 YAML 文本最稳，格式和官方示例保持一致，
  也避免不同 pyyaml 版本缩进风格不一样导致模组解析失败。

已知的坑（写在代码里免得以后又踩）：
  不同 ServerCore 版本配置文件位置不一样 ——
    新版: config/servercore/config.yml
    旧版: config/servercore-config.yml
  所以这里先找已存在的文件，找不到才用新版路径，
  并且把实际写入路径返回给界面显示出来。
"""

import os
import re
import shutil

# ServerCore 配置文件可能的位置，按顺序找
SERVERCORE_PATHS = [
    os.path.join("config", "servercore", "config.yml"),
    os.path.join("config", "servercore-config.yml"),
    os.path.join("config", "servercore.yml"),
    os.path.join("config", "servercore", "optimizations.yml"),
]

# 三档预设。数值参考官方示例 + Bloom/Ember 的优化指南。
# 口径说明：
#   target-mspt 越低越激进（40ms≈25TPS，50ms≈20TPS）
#   SIMULATION_DISTANCE/SCHUNK_TICK_DISTANCE 是"卡了之后最低降到多少"
#   MOBCAP_PERCENTAGE 是最低保留多少刷怪上限（100 = 不限制）
SERVERCORE_PRESETS = {
    "light": {
        "label": "保守（几乎不改变玩法）",
        "dynamic": True,
        "target_mspt": 50,
        "sim_default": 10, "sim_min": 8,
        "chunk_tick_default": 8, "chunk_tick_min": 6,
        "mobcap_max": 100, "mobcap_min": 85,
        "autosave": 600,
        "lobotomize": True,
        "prevent_unloaded": True,
        "xp_merge": 20, "item_merge": 1.0,
        "xp_radius": 1.0, "item_radius": 1.0,
        "lobotomize_interval": 20,
        "allow_dynamic_view": False,
        "view_default": 10, "view_min": 8,
    },
    "balanced": {
        "label": "均衡（推荐）",
        "dynamic": True,
        "target_mspt": 40,
        "sim_default": 8, "sim_min": 6,
        "chunk_tick_default": 8, "chunk_tick_min": 5,
        "mobcap_max": 100, "mobcap_min": 70,
        "autosave": 600,
        "lobotomize": True,
        "prevent_unloaded": True,
        "xp_merge": 20, "item_merge": 1.0,
        "xp_radius": 1.0, "item_radius": 1.0,
        "lobotomize_interval": 20,
        "allow_dynamic_view": False,
        "view_default": 10, "view_min": 6,
    },
    "aggressive": {
        "label": "激进（人多 / 机器弱）",
        "dynamic": True,
        "target_mspt": 35,
        "sim_default": 6, "sim_min": 4,
        "chunk_tick_default": 6, "chunk_tick_min": 4,
        "mobcap_max": 100, "mobcap_min": 50,
        "autosave": 900,
        "lobotomize": True,
        "prevent_unloaded": True,
        "xp_merge": 10, "item_merge": 1.5,
        "xp_radius": 0.8, "item_radius": 1.5,
        "lobotomize_interval": 30,
        "allow_dynamic_view": False,
        "view_default": 10, "view_min": 6,
    },
}


def find_servercore_config(server_dir):
    """找已存在的 ServerCore 配置。返回 (绝对路径, 相对路径)，
    没有返回 (None, 相对默认路径)。"""
    if not server_dir:
        return None, SERVERCORE_PATHS[0]
    for rel in SERVERCORE_PATHS:
        p = os.path.join(server_dir, rel)
        if os.path.isfile(p):
            return p, rel
    return None, SERVERCORE_PATHS[0]


def servercore_installed(server_dir):
    """这个服有没有装 ServerCore（看 mods 目录里的 jar）。"""
    md = os.path.join(server_dir or "", "mods")
    if not os.path.isdir(md):
        return False
    try:
        for fn in os.listdir(md):
            low = fn.lower()
            if low.endswith(".jar") and "servercore" in low:
                return True
    except Exception:
        pass
    return False


def _num(v, default):
    try:
        return float(v)
    except Exception:
        return float(default)


def servercore_yaml(o):
    """按选项生成 ServerCore 配置文本。

    o 里的键见 SERVERCORE_PRESETS["balanced"]。
    缺的键用 balanced 补上，免得调用方漏传就写出半个配置。
    """
    base = dict(SERVERCORE_PRESETS["balanced"])
    for k, v in (o or {}).items():
        if v is not None:
            base[k] = v
    o = base

    dyn = bool(o.get("dynamic", True))
    sim_d = int(_num(o.get("sim_default"), 8))
    sim_m = int(_num(o.get("sim_min"), 6))
    ct_d = int(_num(o.get("chunk_tick_default"), 8))
    ct_m = int(_num(o.get("chunk_tick_min"), 5))
    mc_max = int(_num(o.get("mobcap_max"), 100))
    mc_min = int(_num(o.get("mobcap_min"), 70))
    # 最小值不能大于默认值，否则模组会在两头来回跳
    sim_m = min(sim_m, sim_d)
    ct_m = min(ct_m, ct_d)
    mc_min = min(mc_min, mc_max)

    def dyn_block(setting, mx, mn, inc, interval):
        return (
            "    - setting: '%s'\n"
            "      max: %d\n"
            "      min: %d\n"
            "      increment: %d\n"
            "      interval: %d\n" % (setting, mx, mn, inc, interval))

    txt = (
        "# ServerCore configuration\n"
        "# 由 MC 服务器管理器生成 —— 可以手改，改完 /servercore reload 生效。\n"
        "#\n"
        "# 视距默认交给 server.properties 的 view-distance 控制，\n"
        "# 如果你开启了允许动态降视距，ServerCore 卡时会接管该值。\n"
        "\n"
        "features:\n"
        "  # 卡顿时防止玩家走进未加载的区块造成尖峰\n"
        "  prevent-moving-into-unloaded-chunks: %s\n"
        "  # 自动保存间隔（秒）。装了异步保存类模组可以拉长\n"
        "  autosave-interval-seconds: %d\n"
        "  # 经验球合并概率（1=100%%，20=5%%）\n"
        "  xp-merge-fraction: %d\n"
        "  # 经验球合并半径\n"
        "  xp-merge-radius: %.1f\n"
        "  # 掉落物合并半径\n"
        "  item-merge-radius: %.1f\n"
        "  lobotomize-villagers:\n"
        "    # 村民卡在 1x1 空间时降低它的 tick 频率（交易大厅很有效）\n"
        "    enabled: %s\n"
        "    tick-interval: %d\n"
        "\n"
        "# 按服务器性能自动调整下面这些动态值\n"
        "dynamic:\n"
        "  # 关掉就是完全不做动态调整\n"
        "  enabled: %s\n"
        "  # 目标 MSPT（每 tick 毫秒）。越小越激进\n"
        "  target-mspt: %d\n"
        "  # 正常时的取值\n"
        "  default-values:\n"
        "    CHUNK_TICK_DISTANCE: %d\n"
        "    MOBCAP_PERCENTAGE: %d\n"
        "    SIMULATION_DISTANCE: %d\n"
        "  # 卡了之后按顺序往下压\n"
        "  dynamic-settings:\n"
        % (
            "true" if o.get("prevent_unloaded", True) else "false",
            int(_num(o.get("autosave"), 600)),
            int(_num(o.get("xp_merge"), 20)),
            _num(o.get("xp_radius"), 1.0),
            _num(o.get("item_radius"), 1.0),
            "true" if o.get("lobotomize", True) else "false",
            int(_num(o.get("lobotomize_interval"), 20)),
            "true" if dyn else "false",
            int(_num(o.get("target_mspt"), 40)),
            ct_d, mc_max, sim_d,
        )
    )
    if dyn:
        txt += dyn_block("CHUNK_TICK_DISTANCE", ct_d, ct_m, 1, 15)
        txt += "\n"
        txt += dyn_block("MOBCAP_PERCENTAGE", mc_max, mc_min, 10, 15)
        txt += "\n"
        txt += dyn_block("SIMULATION_DISTANCE", sim_d, sim_m, 1, 15)
        
        # ★ 新增：允许动态降视距
        if o.get("allow_dynamic_view", False):
            v_d = int(_num(o.get("view_default"), 10))
            v_m = int(_num(o.get("view_min"), 6))
            v_m = min(v_m, v_d)
            txt += "\n"
            txt += dyn_block("VIEW_DISTANCE", v_d, v_m, 1, 15)
    else:
        txt += "    []\n"
    return txt


def apply_servercore(server_dir, opts):
    """把配置写进这个服。

    返回 dict:
      ok      是否写入成功
      path    实际写入的绝对路径
      rel     相对路径（给界面显示）
      created 是不是新建的文件
      backup  被覆盖前备份到了哪（没有则 None）
      err     失败原因
    """
    res = {"ok": False, "path": "", "rel": "", "created": True,
           "backup": None, "err": ""}
    if not server_dir or not os.path.isdir(server_dir):
        res["err"] = "服务器目录不存在"
        return res
    cur, rel = find_servercore_config(server_dir)
    target = cur or os.path.join(server_dir, rel)
    try:
        os.makedirs(os.path.dirname(target), exist_ok=True)
    except Exception as e:
        res["err"] = "建目录失败：%r" % e
        return res
    # 覆盖前先备份一份，别把用户手改的配置弄没了
    if cur and os.path.isfile(cur):
        try:
            bk = cur + ".bak"
            with open(cur, "r", encoding="utf-8", errors="replace") as f:
                old = f.read()
            with open(bk, "w", encoding="utf-8") as f:
                f.write(old)
            res["backup"] = bk
        except Exception:
            pass
    try:
        with open(target, "w", encoding="utf-8") as f:
            f.write(servercore_yaml(opts))
    except Exception as e:
        res["err"] = "写入失败：%r" % e
        return res
    res.update(ok=True, path=target, rel=rel, created=not bool(cur))
    return res


def read_servercore(server_dir):
    """粗略读回当前配置，给界面填默认值。读不出来就返回 None。

    只认几个关键值，不做完整 YAML 解析 —— 完整解析要引依赖，
    而这里只需要"界面打开时显示成现在的样子"。
    """
    cur, rel = find_servercore_config(server_dir)
    if not cur:
        return None
    try:
        with open(cur, "r", encoding="utf-8", errors="replace") as f:
            t = f.read()
    except Exception:
        return None
    out = {"rel": rel}

    def _b(key, default=True):
        m = re.search(r"^\s*%s:\s*(true|false)" % re.escape(key),
                      t, re.M)
        return (m.group(1) == "true") if m else default

    def _i(key, default):
        m = re.search(r"^\s*%s:\s*(-?\d+)" % re.escape(key), t, re.M)
        return int(m.group(1)) if m else default

    # ★ V2.1.0：dynamic 的 enabled 必须匹配 dynamic: 块下的那个，
    #   否则 _b("enabled") 先匹配到 features 里 lobotomize-villagers 的
    #   enabled（4 空格缩进），dynamic 开关读错（lobotomize 关了
    #   却显示动态调整也关了）。
    m = re.search(r"^dynamic:\s*\n\s*enabled:\s*(true|false)", t, re.M)
    out["dynamic"] = (m.group(1) == "true") if m else True
    out["target_mspt"] = _i("target-mspt", 40)
    out["autosave"] = _i("autosave-interval-seconds", 600)
    out["xp_merge"] = _i("xp-merge-fraction", 20)
    out["lobotomize"] = _b("enabled", True)
    out["prevent_unloaded"] = _b("prevent-moving-into-unloaded-chunks", True)
    m = re.search(r"SIMULATION_DISTANCE:\s*(-?\d+)", t)
    out["sim_default"] = int(m.group(1)) if m else 8
    m = re.search(r"CHUNK_TICK_DISTANCE:\s*(-?\d+)", t)
    out["chunk_tick_default"] = int(m.group(1)) if m else 8
    m = re.search(r"MOBCAP_PERCENTAGE:\s*(-?\d+)", t)
    out["mobcap_max"] = int(m.group(1)) if m else 100
    # min 取 dynamic-settings 里最后一个同名项
    mins = re.findall(r"SIMULATION_DISTANCE'\s*\n\s*max:\s*\d+\s*\n"
                      r"\s*min:\s*(-?\d+)", t)
    out["sim_min"] = int(mins[-1]) if mins else max(4, out["sim_default"] - 2)
    mins = re.findall(r"CHUNK_TICK_DISTANCE'\s*\n\s*max:\s*\d+\s*\n"
                      r"\s*min:\s*(-?\d+)", t)
    out["chunk_tick_min"] = int(mins[-1]) if mins else max(
        3, out["chunk_tick_default"] - 3)
    mins = re.findall(r"MOBCAP_PERCENTAGE'\s*\n\s*max:\s*\d+\s*\n"
                      r"\s*min:\s*(-?\d+)", t)
    out["mobcap_min"] = int(mins[-1]) if mins else 70
    
    # ★ 新增字段读取
    m = re.search(r"xp-merge-radius:\s*([\d.]+)", t)
    out["xp_radius"] = float(m.group(1)) if m else 1.0
    m = re.search(r"item-merge-radius:\s*([\d.]+)", t)
    out["item_radius"] = float(m.group(1)) if m else 1.0
    m = re.search(r"tick-interval:\s*(\d+)", t)
    out["lobotomize_interval"] = int(m.group(1)) if m else 20
    
    m = re.search(r"VIEW_DISTANCE'\s*\n\s*max:\s*(\d+)\s*\n\s*min:\s*(\d+)", t)
    if m:
        out["allow_dynamic_view"] = True
        out["view_default"] = int(m.group(1))
        out["view_min"] = int(m.group(2))
    else:
        out["allow_dynamic_view"] = False
        out["view_default"] = 10
        out["view_min"] = 6
    return out


# ★ V1.7.0-4：Simple Voice Chat（语音聊天）可配置
# ★ V1.8.0：新版 SVC 的配置路径是 config/voicechat/voicechat-server.properties
#   （老版本是 config/simple-voice-chat/server.properties）。
#   SVC_PATHS 顺序即查找顺序：新路径优先，找不到再回退旧路径。
SVC_PATHS = [
    os.path.join("config", "voicechat", "voicechat-server.properties"),
    os.path.join("config", "simple-voice-chat", "server.properties"),
]

SVC_DEFAULT_PORT = 24454
SVC_DEFAULT_DISTANCE = 8
SVC_DEFAULT_BROADCAST = 64

SVC_PRESETS = {
    "default": {
        "label": "默认（官方推荐）",
        "port": SVC_DEFAULT_PORT,
        "voice_distance": SVC_DEFAULT_DISTANCE,
        "broadcast_range": SVC_DEFAULT_BROADCAST,
        "enable": True,
        "enable_groups": True,
    },
    "small": {
        "label": "近距离（8 格内才听到）",
        "port": SVC_DEFAULT_PORT,
        "voice_distance": 8,
        "broadcast_range": 16,
        "enable": True,
        "enable_groups": True,
    },
    "large": {
        "label": "远距离（适合大地图）",
        "port": SVC_DEFAULT_PORT,
        "voice_distance": 16,
        "broadcast_range": 128,
        "enable": True,
        "enable_groups": True,
    },
}

_SVC_TEMPLATE = """#Server properties for Simple Voice Chat
port={port}
voice_distance={voice_distance}
broadcast_range={broadcast_range}
keep_voice_connection=false
udp_thread_count=2
show_voice_chat_settings=true
enable_voice_chat={enable}
voice_chat_icon=true
disable_groups={disable_groups}
"""


def find_svc_config(server_dir):
    """找已存在的 SVC 配置。返回 (绝对路径, 相对路径)。

    ★ V1.8.0：按 SVC_PATHS 顺序找，新路径优先，旧路径兜底。
    """
    if not server_dir:
        return None, SVC_PATHS[0]
    for rel in SVC_PATHS:
        p = os.path.join(server_dir, rel)
        if os.path.isfile(p):
            return p, rel
    return None, SVC_PATHS[0]


def svc_installed(server_dir):
    """这个服有没有装 Simple Voice Chat（看 mods 目录里的 jar）。"""
    md = os.path.join(server_dir or "", "mods")
    if not os.path.isdir(md):
        return False
    try:
        for fn in os.listdir(md):
            low = fn.lower()
            if low.endswith(".jar") and not low.endswith(".disabled"):
                if "voicechat" in low or "simple-voice-chat" in low:
                    return True
    except Exception:
        pass
    return False


def read_svc(server_dir):
    """读 SVC 配置，返回
    {port, voice_distance, broadcast_range, enable, enable_groups}。"""
    out = {
        "port": SVC_DEFAULT_PORT,
        "voice_distance": SVC_DEFAULT_DISTANCE,
        "broadcast_range": SVC_DEFAULT_BROADCAST,
        "enable": True,
        # ★ V1.8.0：分组功能开关（disable_groups=false => 开启）
        "enable_groups": True,
    }
    try:
        p, _ = find_svc_config(server_dir)
        if not p or not os.path.isfile(p):
            return out
        with open(p, "r", encoding="utf-8", errors="replace") as f:
            t = f.read()
        m = re.search(r"^\s*port\s*=\s*(\d+)", t, re.M)
        if m:
            out["port"] = int(m.group(1))
        m = re.search(r"^\s*voice_distance\s*=\s*(\d+)", t, re.M)
        if m:
            out["voice_distance"] = int(m.group(1))
        m = re.search(r"^\s*broadcast_range\s*=\s*(\d+)", t, re.M)
        if m:
            out["broadcast_range"] = int(m.group(1))
        m = re.search(r"^\s*enable_voice_chat\s*=\s*(\w+)", t, re.M)
        if m:
            out["enable"] = m.group(1).lower() in ("true", "1", "yes")
        m = re.search(r"^\s*disable_groups\s*=\s*(\w+)", t, re.M)
        if m:
            out["enable_groups"] = \
                m.group(1).lower() not in ("true", "1", "yes")
    except Exception:
        pass
    return out


def apply_svc(server_dir, opts):
    """把 SVC 配置写回配置文件（新路径优先，旧路径兜底）。

    文件不存在则生成完整模板；存在则按 key=value 逐项替换，
    保留其它行与注释。返回 {"ok", "rel", "backup", "err"}。
    """
    p = None
    rel = SVC_PATHS[0]
    try:
        d = dict(SVC_PRESETS["default"])
        for k, v in (opts or {}).items():
            if v is not None:
                d[k] = v
        # enable_groups（UI 语义）-> disable_groups（配置文件语义）
        groups = d.get("enable_groups", True)
        disable_groups = "false" if groups else "true"
        p, rel = find_svc_config(server_dir)
        backup = False
        if p and os.path.isfile(p):
            with open(p, "r", encoding="utf-8", errors="replace") as f:
                old = f.read()
            # 已存在：按行替换 key=value，保留未知行
            def _sub(m):
                key = m.group(1)
                val = {
                    "port": str(int(d.get("port", SVC_DEFAULT_PORT))),
                    "voice_distance": str(int(d.get(
                        "voice_distance", SVC_DEFAULT_DISTANCE))),
                    "broadcast_range": str(int(d.get(
                        "broadcast_range", SVC_DEFAULT_BROADCAST))),
                    "enable_voice_chat": (
                        "true" if d.get("enable", True) else "false"),
                    "disable_groups": disable_groups,
                }.get(key)
                return ("%s = %s" % (key, val)) if val is not None \
                    else m.group(0)
            body = re.sub(r"^\s*(port|voice_distance|broadcast_range|"
                          r"enable_voice_chat|disable_groups)"
                          r"\s*=\s*\S+.*$",
                          _sub, old, flags=re.M)
            if body == old:
                return {"ok": True, "rel": rel, "backup": False}
            try:
                shutil.copy2(p, p + ".bak")
                backup = True
            except Exception:
                pass
        else:
            body = _SVC_TEMPLATE.format(
                port=int(d.get("port", SVC_DEFAULT_PORT)),
                voice_distance=int(d.get(
                    "voice_distance", SVC_DEFAULT_DISTANCE)),
                broadcast_range=int(d.get(
                    "broadcast_range", SVC_DEFAULT_BROADCAST)),
                enable="true" if d.get("enable", True) else "false",
                disable_groups=disable_groups)
            # 按找到/目标路径建目录：优先新路径
            cfg_dir = os.path.join(server_dir,
                                   "config", "voicechat")
            p = os.path.join(cfg_dir, "voicechat-server.properties")
            os.makedirs(cfg_dir, exist_ok=True)
        with open(p, "w", encoding="utf-8", newline="") as f:
            f.write(body)
        return {"ok": True, "rel": rel, "backup": backup}
    except Exception as e:
        return {"ok": False, "rel": rel,
                "backup": False, "err": repr(e)}


# 以后要加别的模组配置，往这里登记就行。
# 每项：{key, name, check(有没有装), apply(写配置), read(读回来), presets}
CONFIGURABLE_MODS = [
    {"key": "servercore", "name": "ServerCore",
     "check": servercore_installed,
     "apply": apply_servercore,
     "read": read_servercore,
     "presets": SERVERCORE_PRESETS,
     "paths": SERVERCORE_PATHS,
     "help": ("ServerCore 装上只是『能用』，动态视距/模拟距离/刷怪上限"
              "都在配置文件里。不配就用默认值，等于白装一半。")},
    # ★ V1.7.0-4：Simple Voice Chat（语音聊天，可配置端口/距离）
    {"key": "simple-voice-chat", "name": "Simple Voice Chat",
     "check": svc_installed,
     "apply": apply_svc,
     "read": read_svc,
     "presets": SVC_PRESETS,
     "paths": SVC_PATHS,
     "help": ("Simple Voice Chat 近距离语音。默认端口 24454，"
              "自定义端口时客户端也要在语音设置里填同一个端口。"
              "改配置后需重启服务器生效。")},
]
