# -*- coding: utf-8 -*-
"""离线日志诊断 —— 不联网、不需要 AI，靠规则库匹配。

规则来源：实际排查 MC 服务器时反复遇到的固定模式。
每条规则 = 症状(正则) + 原因 + 修法。

设计原则：
  · 匹配要准，宁可漏报不可误报 —— 误报会让人再也不信这个工具
  · 修法要具体到"点哪个按钮/改哪个文件"，不说空话
  · 同一规则只报一次，但记录出现次数和首次行号
"""

import os
import re
import gzip
import glob

from . import props as propsmod
from . import versions as versionsmod


# =========================================================
#  规则库
# =========================================================
# severity: error(必崩/用不了) / warn(有问题但能跑) / info(提示)
#
# patterns 里用 (?i) 或直接写小写 —— 匹配时会统一 lower

RULES = [
    # ---------- 路径 / 文件名 ----------
    {
        "id": "level_name_illegal",
        "title": "存档名里有 Windows 不允许的字符（冒号等）",
        "patterns": [
            r"invalidpathexception.*illegal char",
            r"illegal char\s*<:>",
        ],
        "severity": "error",
        "cause": "MC 会把 level-name 直接拿去拼文件夹路径。"
                 "世界名里带 ':' （还可能带 §b§l 颜色码）时，"
                 "Windows 无法创建该路径，服务端启动即崩。",
        "fix": [
            "把 server.properties 里的 level-name 改成纯英文、无冒号的名字",
            "（在本软件里改：选中服务器 → 服务器配置 → 世界页签）",
            "想要漂亮的名字，改 motd 而不是 level-name",
            "  motd 只是标语，不参与拼路径，可以保留冒号",
        ],
    },
    {
        "id": "level_name_non_ascii",
        "title": "存档名含中文，Java 读路径有编码风险",
        "patterns": [
            # 注意：不要写 invalidpathexception ——
            # 会和上一条"冒号"规则重复命中，报两次没意义
            r"malformed\s*escape",
            r"path.*(?:[\u4e00-\u9fff]).*not found",
        ],
        "severity": "warn",
        "cause": "中文目录名在部分 Java 版本 / 启动方式下会读成乱码，"
                 "导致找不到存档并新建一个空世界。",
        "fix": [
            "建议把存档文件夹重命名成英文（如 hook）",
            "然后 level-name 同步改成同一个英文名",
        ],
    },

    # ---------- 核心下载 ----------
    {
        "id": "paper_version_gone",
        "title": "Paper 已下架这个 MC 版本（HTTP 410）",
        "patterns": [
            r"httperror\s*410",
            r"410:\s*'gone'",
            r"无法获取\s*paper\s*版本列表",
        ],
        "severity": "error",
        "cause": "Paper 只维护较新的几个版本，旧的会从服务器移除。"
                 "这是官方下架，不是网络问题、也不是本软件的 bug。",
        "fix": [
            "改用 Fabric —— 几乎所有版本都有，且优化模组生态更好",
            "（选中服务器 → 服务器配置 → 核心页签 → 换加载器）",
            "如果必须用 Paper 插件，只能升到官方还在提供的版本，",
            "  但要注意：升版本会升级世界格式，不可逆，务必先备份",
        ],
    },
    {
        "id": "forge_no_launch_jar",
        "title": "新版 Forge 不生成启动 jar（用 args 文件启动）",
        "patterns": [
            r"installer\s*完成但没找到启动\s*jar",
            r"win_args\.txt",
        ],
        "severity": "warn",
        "cause": "新版 Forge（1.20.1 后期 / 1.21.x）改用 bootstrap，"
                 "产物是 libraries 下的 win_args.txt，"
                 "不再是 forge-xxx.jar。这是正常的，不是装失败。",
        "fix": [
            "不用重装 —— Forge 其实已经装好了",
            "重新扫描一次，加载器应该显示 forge",
            "然后『服务器配置』里点一次保存，重新生成 run.bat 即可启动",
        ],
    },

    # ---------- 数据损坏 ----------
    {
        "id": "entity_data_broken",
        "title": "实体数据损坏（区域文件对不上）",
        "patterns": [
            r"no key id in maplike",
            r"failed to load block entity",
            r"skipping block entity",
        ],
        "severity": "error",
        "cause": "存档里实体（村民/展示框等）的数据读不出来。"
                 "常见于：存档经历过版本迁移，或 entities 文件与区域文件对不上，"
                 "或被更低版本的服务端打开过一次。",
        "fix": [
            "先备份整个存档文件夹（本软件：备份管理 → 立即备份）",
            "确认目录结构：26.1+ 实体应在",
            "  world/dimensions/minecraft/overworld/entities/",
            "如果之前被别的版本打开过，用原来的版本开一次再关掉",
            "最坏情况只能丢弃 entities 数据（村民和展示框会没）",
        ],
    },
    {
        "id": "block_attached_entity",
        "title": "挂在方块上的实体位置丢失（物品展示框/画）",
        "patterns": [
            r"block-attached entity at invalid position",
        ],
        "severity": "warn",
        "cause": "物品展示框 / 荧光展示框 / 画的位置数据丢了。"
                 "和上面的实体损坏是同一类问题。",
        "fix": [
            "如果是小地图/派对图，这些展示框多半是装饰，影响不大",
            "想彻底解决同上：备份后检查 entities 数据",
        ],
    },
    {
        "id": "level_dat_broken",
        "title": "level.dat 读取失败",
        "patterns": [
            r"failed to read level",
            r"exception reading .*level\.dat",
            r"invalid level\.dat",
        ],
        "severity": "error",
        "cause": "存档的 level.dat 损坏，或用了比存档更旧的版本去打开。",
        "fix": [
            "先备份",
            "试试用 level.dat_old 覆盖 level.dat（会丢最后一次的进度）",
            "确认服务端版本 >= 存档版本，用低版本打开会损坏数据",
        ],
    },
    {
        "id": "world_newer_version",
        "title": "存档版本高于服务端版本",
        "patterns": [
            r"created by newer version",
            r"world was created in newer",
            r"unsupported version",
            r"please use the latest version",
        ],
        "severity": "error",
        "cause": "用旧版本服务端打开了新版本存档。这会损坏数据，必须避免。",
        "fix": [
            "把服务端升到 >= 存档版本",
            "存档版本在 level.dat 里，本软件扫描时会显示",
            "注意文件夹名写的版本不一定是真实版本，以扫描结果为准",
        ],
    },

    # ---------- 端口 / 网络 ----------
    {
        "id": "port_in_use",
        "title": "端口被占用（多开时最常见）",
        "patterns": [
            r"address already in use",
            r"failed to bind to port",
            r"java\.net\.bindexception",
        ],
        "severity": "error",
        "cause": "两个服务器都用了 25565。也可能是别的软件占了。",
        "fix": [
            "改 server-port（服务器配置 → 玩家页签）",
            "本软件多开时会自动检测并提示改端口",
            "查出是谁占用：netstat -ano | findstr :25565",
        ],
    },

    # ---------- Java ----------
    {
        "id": "java_version_mismatch",
        "title": "Java 版本不匹配",
        "patterns": [
            r"unsupportedclassversionerror",
            r"class file has wrong version",
            r"unsupported class file major version",
            r"java\.lang\.unsupportedclassversionerror",
        ],
        "severity": "error",
        "cause": "Java 版本太低，跑不动这个 MC 版本 / 模组。"
                 "（反过来 Java 太高，老版本 Forge 也会炸）",
        "fix": [
            "1.20.5 及以后 → Java 21",
            "1.20.4 及以前 → Java 17",
            "1.17~1.18  → Java 17；1.16 及以前 → Java 8",
            "本软件：服务器配置 → 启动设置，选对应 JDK",
        ],
    },
    {
        "id": "out_of_memory",
        "title": "内存不足（OutOfMemory）",
        "patterns": [
            r"java\.lang\.outofmemoryerror",
            r"java heap space",
            r"out of memory",
            r"there is insufficient memory",
        ],
        "severity": "error",
        "cause": "分配的内存不够。多开时尤其容易（每个服都要吃一份）。",
        "fix": [
            "调小最大内存：服务器配置 → 启动设置",
            "多开时建议每个服 2G，别都给 4G",
            "装 FerriteCore / Saturn 减少内存占用",
            "实体太多的话考虑装清理类优化模组",
        ],
    },

    # ---------- 模组 ----------
    {
        "id": "mod_missing_dep",
        "title": "模组缺前置",
        "patterns": [
            r"requires\s+.*fabric[- ]?api",
            r"unable to load mod",
            r"modresolutionexception",
            r"could not find required mod",
            r"missing dependency",
        ],
        "severity": "error",
        "cause": "某个模组需要的前置没装（最常见是缺 Fabric API）。",
        "fix": [
            "看日志里说的是哪个模组需要什么",
            "补装前置：服务器配置 → 模组页签，或初始化时装",
            "Fabric API 是最常见的前置，默认就该勾上",
        ],
    },
    {
        "id": "mod_version_mismatch",
        "title": "模组与游戏版本不匹配",
        "patterns": [
            r"java\.lang\.nosuchmethoderror",
            r"java\.lang\.noclassdeffounderror",
            r"java\.lang\.classnotfoundexception",
            r"mixin apply failed",
            r"mixinapplyfailed",
        ],
        "severity": "error",
        "cause": "模组是针对别的 MC 版本编译的。"
                 "比如把 1.21.7 的模组放到 1.21.10 上。",
        "fix": [
            "确认每个模组都支持当前版本（Modrinth 页面会写）",
            "用本软件模组管理的『检查更新』看有没有匹配版本",
            "版本太新的话（如 26.x）很多模组还没跟上，属于正常",
        ],
    },
    {
        "id": "mod_duplicate",
        "title": "装了重复/冲突的模组",
        "patterns": [
            r"duplicate mod",
            r"found duplicate mod",
            r"mods? .* conflicts?",
        ],
        "severity": "warn",
        "cause": "同一个模组装了两份，或两个模组功能冲突。",
        "fix": [
            "打开 mods 文件夹看有没有同名不同版本的 jar",
            "禁用其中一个：模组管理 → 选中 → 禁用",
        ],
    },

    # ---------- 启动 / 配置 ----------
    {
        "id": "eula_not_accepted",
        "title": "EULA 协议未同意",
        "patterns": [
            r"you need to agree to the eula",
            r"eula\s*\(\s*https://aka\.ms",
        ],
        "severity": "error",
        "cause": "eula.txt 里 eula 不是 true。MC 要求必须同意才启动。",
        "fix": [
            "把 eula.txt 里的 eula=false 改成 eula=true",
            "本软件初始化时会自动生成已同意的 eula.txt",
        ],
    },
    {
        "id": "file_locked",
        "title": "文件被其它程序占用",
        "patterns": [
            r"file is locked",
            r"another program",
            r"the process cannot access the file",
            r"being used by another process",
        ],
        "severity": "error",
        "cause": "服务器还在运行，或资源管理器/压缩软件正开着这个目录。",
        "fix": [
            "先在本软件里停止服务器",
            "删除核心时会占用 → 点『结束占用进程』再试",
            "关掉打开着该目录的资源管理器窗口",
        ],
    },
    {
        "id": "disk_full",
        "title": "磁盘空间不足",
        "patterns": [
            r"no space left on device",
            r"disk full",
            r"insufficient disk space",
        ],
        "severity": "error",
        "cause": "盘满了。备份、日志、存档都会撑爆磁盘。",
        "fix": [
            "清理旧备份（备份管理 → 删除备份）",
            "删 logs 下的旧 .gz 日志",
            "本软件备份默认用 zip 压缩，能省不少空间",
        ],
    },
    {
        "id": "auth_verify_failed",
        "title": "登录验证失败 / 正版验证配置问题",
        "patterns": [
            r"failed to verify username",
            r"invalid session",
            r"authentication unsuccessful",
            r"you must set online-mode to true",
            r"online-mode.*must be true",
            # 真实日志里带引号且大小写不固定，上面两条经常漏：
            #   To change this, set "online-mode" to "true" in ...
            r"server is running in offline",
            r"offline/insecure mode",
            r"set [\"']?online-mode[\"']? to",
        ],
        "severity": "warn",
        "cause": "外置登录（LittleSkin 等）要生效，online-mode 必须是 true。"
                 "另外 AuthLibInjector 会把验证服务整个替换掉，"
                 "导致正版账号反而进不去。",
        "fix": [
            "确认 server.properties 里 online-mode=true",
            "要正版和皮肤站都能进，用 MultiYggdrasil（不是原版 AuthLibInjector）",
            "  javaagent 版：加 -Dmultiyggdrasil.mojangYggdrasilService=enabled",
            "  注意别同时保留 authlib-injector 的 javaagent，两者会打架",
        ],
    },
    {
        "id": "datapack_function_failed",
        "title": "数据包里的函数加载失败（地图自带的）",
        "patterns": [
            r"failed to load function",
            r"incorrect argument for command",
        ],
        "severity": "warn",
        "cause": "地图自带的数据包（这里看起来是 mkmc 那几个）用了旧版指令语法，"
                 "新版本 MC 改了这条指令的参数格式，于是解析失败。"
                 "注意：这类报错【不会导致服务器起不来】，"
                 "只是那几个函数失效——通常是季节/计分板之类的附加功能。",
        "fix": [
            "服务器能正常进就先不管，只影响出错的那几个函数",
            "想修的话：找到 datapacks 下对应的 .mcfunction，"
            "按新版语法改那条指令（一般是 gamerule 的参数名变了）",
            "或者干脆把那个数据包移除，只当普通地图玩",
            "别为了它去降级服务端——降级会损坏存档",
        ],
    },
    {
        "id": "root_warning",
        "title": "以管理员/root 身份运行",
        "patterns": [
            r"running this server as an administrative or root user",
        ],
        "severity": "warn",
        "cause": "Paper 提示你当前是管理员权限。主要风险是：万一插件有恶意代码，"
                 "它能动你整台机器而不只是服务器文件夹。",
        "fix": [
            "个人自用、只装可信插件的话，可以无视",
            "要公开开服，建议新建一个普通用户专门跑服务器",
        ],
    },
    {
        "id": "datapack_format",
        "title": "数据包格式号对不上",
        "patterns": [
            r"unknown pack format",
            r"pack format.*not supported",
            r"requires min_format",
            r"invalid pack\.mcmeta",
        ],
        "severity": "warn",
        "cause": "1.21.9+ 用 min_format/max_format，老版本用 pack_format。"
                 "两边规则不同，不能混用。",
        "fix": [
            "1.21.9 之前写 pack_format",
            "1.21.9 之后写 min_format / max_format",
            "把旧版本修过的数据包搬到新版本服上就会报这个",
        ],
    },

    # ---------- 性能 ----------
    {
        "id": "tps_behind",
        "title": "服务端跑不动（TPS 落后）",
        "patterns": [
            r"can't keep up!",
            r"running behind",
            r"is \d+ms behind",
        ],
        "severity": "warn",
        "cause": "服务端算不过来。可能是实体太多、红石机械、视距太大，"
                 "或者内存分配不合理。",
        "fix": [
            "先定位：控制台输 /spark profiler start --timeout 300",
            "  它告诉你到底是实体、红石还是别的在吃 CPU",
            "调小视距（服务器配置 → 视距页签）",
            "装 Lithium / C2ME 这类优化模组",
            "注意：Aikar 参数下 -Xms 和 -Xmx 要一样大",
        ],
    },
    {
        "id": "chunk_gen_slow",
        "title": "跑图时区块生成跟不上",
        "patterns": [
            r"preparing start region",
        ],
        "severity": "info",
        "cause": "这是启动时的正常输出，不是报错。"
                 "但如果这里卡很久，说明地形在边跑边生成。",
        "fix": [
            "想提前生成好：装 Chunky，跑 /chunky radius 1000 /chunky start",
            "地形已生成完的图不需要 Noisium 这类加速模组",
        ],
    },
]


# =========================================================
#  日志文件收集
# =========================================================

def find_log_files(server_dir):
    """找出这个服务器所有值得看的日志。

    返回 [(显示名, 绝对路径)]，按重要性排序。
    """
    out = []
    cands = [
        ("latest.log", os.path.join(server_dir, "logs", "latest.log")),
        ("logs\\latest.log", os.path.join(server_dir, "server", "logs",
                                         "latest.log")),
        ("latest.log(根目录)", os.path.join(server_dir, "latest.log")),
        ("debug.log", os.path.join(server_dir, "logs", "debug.log")),
    ]
    for name, p in cands:
        if os.path.isfile(p):
            out.append((name, p))

    # Forge installer 的日志（装核心失败时特别有用）
    for p in sorted(glob.glob(os.path.join(server_dir, "*installer*.log"))):
        out.append((os.path.basename(p), p))

    # 压缩过的旧日志（.gz），最多取 2 个
    gzs = sorted(glob.glob(os.path.join(server_dir, "logs", "*.log.gz")),
                 key=os.path.getmtime, reverse=True)[:2]
    for p in gzs:
        out.append((os.path.basename(p), p))

    return out


def read_log_tail(path, max_lines=3000):
    """读日志尾部（.gz 也支持）。返回行列表。"""
    try:
        if path.lower().endswith(".gz"):
            with gzip.open(path, "rt", encoding="utf-8",
                           errors="replace") as f:
                lines = f.readlines()
        else:
            with open(path, "r", encoding="utf-8", errors="replace") as f:
                lines = f.readlines()
    except Exception:
        return []
    return lines[-max_lines:]


# =========================================================
#  分析
# =========================================================

def _compile(rule):
    pats = []
    for p in rule["patterns"]:
        try:
            pats.append(re.compile(p, re.IGNORECASE))
        except Exception:
            pass
    return pats


def analyze_logs(server_dir, extra_text=None, max_lines=3000):
    """扫描日志，返回命中的问题列表。

    返回 [ {id, title, severity, cause, fix, count, first_line,
            sample, file} ]，按严重程度排序。
    """
    found = {}   # id -> 记录

    files = find_log_files(server_dir)
    if extra_text:
        files = files + [("控制台输出", None)]

    for name, path in files:
        if path is None:
            lines = (extra_text or "").splitlines()
        else:
            lines = read_log_tail(path, max_lines)
        if not lines:
            continue

        for i, raw in enumerate(lines, 1):
            low = raw.lower()
            for rule in RULES:
                for pat in _compile(rule):
                    if pat.search(low):
                        rid = rule["id"]
                        rec = found.get(rid)
                        if rec is None:
                            rec = {
                                "id": rid,
                                "title": rule["title"],
                                "severity": rule["severity"],
                                "cause": rule["cause"],
                                "fix": list(rule["fix"]),
                                "count": 0,
                                "first_line": i,
                                "sample": raw.strip()[:300],
                                "file": name,
                            }
                            found[rid] = rec
                        rec["count"] += 1
                        break   # 同一行命中一条规则一次就够

    order = {"error": 0, "warn": 1, "info": 2}
    return sorted(found.values(),
                  key=lambda r: (order.get(r["severity"], 9),
                                 -r["count"]))


# =========================================================
#  静态检查（不用日志也能查）
# =========================================================

def check_config(server_dir, entry=None, all_entries=None):
    """检查配置层面的问题。返回 [{level, title, detail, fix}]。"""
    issues = []

    # --- server.properties ---
    sp_path = None
    for c in (os.path.join(server_dir, "server.properties"),
              os.path.join(server_dir, "server", "server.properties")):
        if os.path.isfile(c):
            sp_path = c
            break
    sp = {}
    if sp_path:
        try:
            sp = propsmod.read(sp_path) if hasattr(
                propsmod, "read") else {}
        except Exception:
            sp = {}
        # 兜底：自己解析一遍
        try:
            with open(sp_path, "r", encoding="utf-8",
                      errors="replace") as f:
                for line in f:
                    line = line.strip()
                    if line and not line.startswith("#") and "=" in line:
                        k, v = line.split("=", 1)
                        sp[k.strip()] = v.strip()
        except Exception:
            pass

    # 1) level-name 非法字符
    ln = sp.get("level-name", "")
    if ln:
        bad = [c for c in ln if c in ':*?"<>|\\/']
        if bad:
            issues.append({
                "level": "error",
                "title": "level-name 含非法字符：%s" % " ".join(bad),
                "detail": "当前 level-name = %s" % ln,
                "fix": ["改成纯英文无符号的名字（服务器配置 → 世界页签）",
                        "漂亮的名字放 motd，那个不参与拼路径"],
            })
        elif not ln.isascii():
            issues.append({
                "level": "warn",
                "title": "level-name 含中文",
                "detail": "当前 level-name = %s" % ln,
                "fix": ["建议改成英文名，Java 读中文路径有编码风险"],
            })

    # 2) online-mode
    if sp.get("online-mode", "true").lower() == "false":
        issues.append({
            "level": "warn",
            "title": "online-mode=false（任何人都能随便进）",
            "detail": "外置登录（LittleSkin）也不会生效",
            "fix": ["要正版/皮肤站登录，必须设为 true",
                    "服务器配置 → 玩家页签 → 正版验证"],
        })

    # 3) Java 版本 vs MC 版本
    if entry and entry.get("version"):
        try:
            # ★ 修复：versions.py 里没有 java_for 函数，只有 need_java
            need = versionsmod.need_java(entry["version"])
        except Exception:
            need = None
        if need and os.path.isdir(server_dir):
            # 只看软件记下来的 Java 选择，不探测系统
            pass

    # 4) 端口冲突
    if all_entries and sp.get("server-port"):
        try:
            port = int(sp.get("server-port"))
        except Exception:
            port = None
        if port:
            dup = [e for e in all_entries
                   if e is not entry and _port_of(e) == port]
            if dup:
                issues.append({
                    "level": "error",
                    "title": "端口号和别的服务器冲突（都是 %d）" % port,
                    "detail": "同时开会导致后启动的那个失败",
                    "fix": ["改 server-port（服务器配置 → 玩家页签）",
                            "本软件多开时会自动检测并提示改端口"],
                })

    # 5) eula
    eula = None
    for c in (os.path.join(server_dir, "eula.txt"),
              os.path.join(server_dir, "server", "eula.txt")):
        if os.path.isfile(c):
            eula = c
            break
    if eula:
        try:
            with open(eula, "r", encoding="utf-8", errors="replace") as f:
                txt = f.read()
            if "eula=true" not in txt.lower():
                issues.append({
                    "level": "error",
                    "title": "eula.txt 未同意，服务器起不来",
                    "detail": "需要 eula=true",
                    "fix": ["把 eula.txt 里改成 eula=true"],
                })
        except Exception:
            pass

    return issues


def _port_of(entry):
    """从别的服务读 server-port。"""
    d = entry.get("server_dir") or entry.get("folder")
    if not d:
        return None
    for c in (os.path.join(d, "server.properties"),
              os.path.join(d, "server", "server.properties")):
        if os.path.isfile(c):
            try:
                with open(c, "r", encoding="utf-8", errors="replace") as f:
                    for line in f:
                        line = line.strip()
                        if line.startswith("server-port="):
                            return int(line.split("=", 1)[1])
            except Exception:
                pass
    return None


def full_report(server_dir, entry=None, all_entries=None, extra_text=None):
    """综合报告：日志命中 + 配置检查。"""
    return {
        "log_issues": analyze_logs(server_dir, extra_text),
        "config_issues": check_config(server_dir, entry, all_entries),
        "log_files": [n for n, _ in find_log_files(server_dir)],
    }
