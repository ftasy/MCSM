# -*- coding: utf-8 -*-
"""生成 server.properties / run.bat / eula.txt。"""

import os
import re

from . import versions

DIFF_KEY = {"和平": "peaceful", "简单": "easy", "普通": "normal",
            "困难": "hard"}
GM_KEY = {"生存": "survival", "创造": "creative", "冒险": "adventure",
          "旁观": "spectator"}

# ★ Aikar's Flags：PaperMC 官方推荐的 G1GC 优化参数
#   https://docs.papermc.io/paper/aikars-flags
#   前提：-Xms 和 -Xmx 必须相同（否则 G1GC 无法发挥最佳性能）。
#   注意：老代码曾把 -XX:+AlwaysPreTouch 的首字母拼错过
#   （靠 replace 兜底修正），这里直接用正确拼写，不再依赖笔误防护。
AIKAR_FLAGS = [
    "-XX:+UseG1GC",
    "-XX:+ParallelRefProcEnabled",
    "-XX:MaxGCPauseMillis=200",
    "-XX:+UnlockExperimentalVMOptions",
    "-XX:+DisableExplicitGC",
    "-XX:+AlwaysPreTouch",
    "-XX:G1NewSizePercent=30",
    "-XX:G1MaxNewSizePercent=40",
    "-XX:G1HeapRegionSize=8M",
    "-XX:G1ReservePercent=20",
    "-XX:G1HeapWastePercent=5",
    "-XX:G1MixedGCCountTarget=4",
    "-XX:InitiatingHeapOccupancyPercent=15",
    "-XX:G1MixedGCLiveThresholdPercent=90",
    "-XX:G1RSetUpdatingPauseTimePercent=5",
    "-XX:SurvivorRatio=32",
    "-XX:+PerfDisableSharedMem",
    "-XX:MaxTenuringThreshold=1",
]


def decide_view(mob_spawn, random_tick, regions):
    """按存档特征决定视距。
    
    ★ 默认给正常玩家视距（视距 10，模拟距离 10）。
    只有超大存档（region 文件超过 300 个）才稍微降一点模拟距离，
    视距绝对不降到 6/4 这种“老电脑妥协”的数值。
    """
    # 只有地图实在太大，才稍微降一点模拟距离
    if (regions or 0) > 300:
        return 10, 8
        
    # 绝大多数普通地图，给完全正常的 10 / 10
    return 10, 10


# =========================================================
#  世界名 / level-name 安全化
# =========================================================
# MC 颜色代码：§ + 一位字符
_MC_CODE = re.compile(r"\u00a7[0-9a-fk-orA-FK-OR]")

# Windows 路径非法字符（MC 存档名会直接当文件夹名用）
_BAD_CHARS = r'<>:"/\\|?*'


def strip_mc_codes(s):
    """去掉 §b §l 这类颜色/格式代码。"""
    if not s:
        return ""
    s = s.replace("&", "\u00a7")
    s = _MC_CODE.sub("", s)
    # 残留的孤立 § 也去掉
    s = s.replace("\u00a7", "")
    return s


def clean_display(name):
    """用于显示的干净名字（去颜色码，但保留空格和冒号）。"""
    s = strip_mc_codes(name or "")
    s = re.sub(r"\s+", " ", s).strip()
    return s


def safe_level_name(name, fallback="world"):
    """生成能当文件夹名用的 level-name。

    MC 会把 level-name 直接拿去拼路径，所以不能有 : 之类。
    例：'§b§lFrozen Crag 2: Frostbite Frenzy'
        -> 'Frozen Crag 2  Frostbite Frenzy'
    """
    s = strip_mc_codes(name or "")
    # 非法字符换空格
    for ch in _BAD_CHARS:
        s = s.replace(ch, " ")
    # 控制字符
    s = "".join(ch for ch in s if ord(ch) >= 32)
    s = re.sub(r"\s+", " ", s).strip()
    # 不能以点结尾（Windows 会吃掉）
    s = s.rstrip(". ")
    if not s:
        return fallback
    # 太长会出问题
    if len(s) > 40:
        s = s[:40].rstrip()
    return s


def validate_level_name(name):
    """检查 level-name 是否安全。返回 (是否安全, 问题说明)。"""
    if not name:
        return False, "为空"
    bad = [c for c in _BAD_CHARS if c in name]
    if bad:
        return False, "含非法字符 %s（MC 会用它拼路径，会崩）" % "".join(bad)
    if "\u00a7" in name or "&" in name:
        return False, "含颜色代码"
    if name.endswith("."):
        return False, "以点结尾"
    return True, ""


def build_properties(level_name, mc_version, difficulty=None,
                     gamemode=None, seed=None, mob_spawn=True,
                     random_tick=None, regions=None, view=None,
                     sim=None, motd=None):
    # 只要视距或模拟距离有一个没指定，就按存档特征推算
    if view is None or sim is None:
        _view, _sim = decide_view(mob_spawn, random_tick, regions)
        if view is None:
            view = _view
        if sim is None:
            sim = _sim

    diff = DIFF_KEY.get(difficulty, "normal")
    gm = GM_KEY.get(gamemode, "survival")
    ms = "true" if mob_spawn else "false"
    fmt = versions.pack_format(mc_version)
    need_java = versions.need_java(mc_version)

    L = []
    a = L.append
    a("#Minecraft server properties")
    a("# 由 MC服务器管理器 自动生成")
    a("# MC %s   数据包格式 %s   Java %s"
      % (mc_version, fmt if fmt else "?", need_java if need_java else "?"))
    a("")
    a("# ---- 世界 ----")
    a("level-name=%s" % safe_level_name(level_name))
    a("# 以下几项只在新建世界时生效；存档已存在则读 level.dat")
    a("level-type=minecraft:normal")
    a("level-seed=%s" % (seed if seed is not None else ""))
    a("difficulty=%s" % diff)
    a("gamemode=%s" % gm)
    a("hardcore=false")
    a("")
    a("# ---- 视距 ----")
    a("view-distance=%d" % view)
    a("simulation-distance=%d" % sim)
    a("")
    a("# ---- 维度 ----")
    a("allow-nether=true")
    a("allow-end=true")
    a("")
    a("# ---- 玩家 ----")
    a("max-players=20")
    a("online-mode=true")
    a("server-port=25565")
    a("spawn-protection=0")
    a("")
    a("# ---- 刷怪（与存档 gamerule 一致）----")
    a("spawn-monsters=%s" % ms)
    a("spawn-animals=%s" % ms)
    a("spawn-npcs=%s" % ms)
    a("")
    a("# ---- 性能 ----")
    a("sync-chunk-writes=false")
    a("network-compression-threshold=256")
    a("entity-broadcast-range-percentage=100")
    a("max-world-size=29999984")
    a("")
    a("# ---- 其它 ----")
    a("enable-status=true")
    # 显示名保留空格和冒号（只是标语，不拼路径）
    disp = clean_display(level_name)
    a("motd=%s" % (motd or disp or safe_level_name(level_name)))
    a("enable-command-block=true")
    a("enforce-secure-profile=false")
    a("max-tick-time=60000")
    a("query.port=25565")
    a("enable-query=false")
    a("enable-rcon=false")
    a("broadcast-rcon-to-ops=true")
    a("broadcast-console-to-ops=true")
    a("op-permission-level=4")
    a("player-idle-timeout=0")
    a("prevent-proxy-connections=false")
    a("rcon.password=")
    a("rcon.port=25575")
    a("resource-pack=")
    a("resource-pack-prompt=")
    a("resource-pack-sha1=")
    a("server-ip=")
    a("snooper-enabled=false")
    a("use-native-transport=true")
    a("white-list=false")
    a("enforce-whitelist=false")
    a("function-permission-level=2")
    a("initial-enabled-packs=vanilla")
    a("initial-disabled-packs=")
    a("accepts-transfers=false")
    a("enable-jmx-monitoring=false")
    a("hide-online-players=false")
    a("log-ips=true")
    a("max-chained-neighbor-updates=1000000")
    a("pause-when-empty-seconds=")
    a("rate-limit=0")
    a("region-file-compression=deflate")
    a("require-resource-pack=false")
    a("text-filtering-config=")
    a("text-filtering-version=0")
    a("unlock-all-recipes=false")
    return "\n".join(L) + "\n"


def build_run_bat(java_path, jar_name, xms="1G", xmx="4G",
                  extra_args="nogui", aikar=True,
                  launch="jar", args_rel=None,
                  pause="error"):
    """生成 run.bat。返回 ASCII 安全的文本（CRLF）。

    pause 决定结尾要不要 pause（就是那个"按任意键继续"）：
      "error"  —— 只在服务器异常退出时才停（默认）
      "always" —— 永远停（老行为，正常关闭也要按一下）
      "never"  —— 永远不停（窗口直接关，崩溃了也看不到错误）

    默认改成 "error" 是因为：正常 /stop 关闭时那个 pause
    纯属多余，每次都要手动按一下很烦；但崩溃时又必须留住
    窗口，否则错误一闪而过。
    """
    if pause not in ("error", "always", "never"):
        pause = "error"
    java_q = '"%s"' % java_path
    flags = ""
    if aikar:
        # ★ Aikar 参数要求 -Xms 和 -Xmx 必须一致
        xmx = xms
        flags = " " + " ".join(AIKAR_FLAGS)

    lines = []
    lines.append("@echo off")
    lines.append("chcp 65001 >nul")
    lines.append('cd /d "%~dp0"')
    lines.append("title Minecraft Server")
    lines.append("")

    if launch == "argsfile" and args_rel:
        # ★ 新版 Forge：用 @args 文件启动，没有启动 jar
        #   形如 java @user_jvm_args.txt
        #        @libraries\net\minecraftforge\forge\...\win_args.txt
        lines.append('if not exist "%s" (' % args_rel)
        lines.append('    echo [ERROR] %s not found.' % args_rel)
        lines.append("    pause")
        lines.append("    exit /b 1")
        lines.append(")")
        # user_jvm_args.txt 里放内存参数，installer 可能没建
        lines.append("if not exist user_jvm_args.txt (")
        lines.append("    echo -Xms%s > user_jvm_args.txt" % xms)
        lines.append("    echo -Xmx%s >> user_jvm_args.txt" % xmx)
        lines.append(")")
        lines.append("")
        lines.append("echo Starting server (Forge args-file mode) ...")
        lines.append("")
        lines.append('%s @user_jvm_args.txt @"%s" %s'
                     % (java_q, args_rel, extra_args or ""))
    else:
        lines.append('if not exist "%s" (' % jar_name)
        lines.append('    echo [ERROR] %s not found.' % jar_name)
        lines.append("    pause")
        lines.append("    exit /b 1")
        lines.append(")")
        lines.append("")
        lines.append("echo Starting server ...")
        lines.append("")
        lines.append('%s -Xms%s -Xmx%s%s -jar %s %s'
                     % (java_q, xms, xmx, flags, jar_name,
                        extra_args or ""))
    lines.append("")
    lines.append("echo.")
    lines.append("echo Server stopped.")

    # ★ 结尾 pause 的处理
    if pause == "always":
        lines.append("pause")
    elif pause == "error":
        lines.append("set EXITCODE=%ERRORLEVEL%")
        lines.append('if not "%EXITCODE%"=="0" (')
        lines.append("    echo.")
        lines.append("    echo [!] Server exited with code %EXITCODE%.")
        lines.append("    echo     Window stays open so you can read"
                     " the error above.")
        lines.append("    pause")
        lines.append(")")
    # never: 什么都不加，窗口直接关

    lines.append("")
    return "\r\n".join(lines) + "\r\n"


def write_eula(server_dir):
    p = os.path.join(server_dir, "eula.txt")
    txt = ("#By changing the setting below to TRUE you are indicating "
           "your agreement to our EULA (https://aka.ms/MinecraftEULA).\n"
           "eula=true\n")
    with open(p, "w", encoding="utf-8", newline="\n") as f:
        f.write(txt)
    return p


def write_text(path, text, crlf=True):
    os.makedirs(os.path.dirname(path), exist_ok=True)
    nl = "\r\n" if crlf else "\n"
    with open(path, "w", encoding="utf-8", newline="") as f:
        if crlf:
            f.write(text.replace("\n", "\r\n"))
        else:
            f.write(text)
    return path
