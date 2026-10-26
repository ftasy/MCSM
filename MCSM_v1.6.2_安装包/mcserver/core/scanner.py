# -*- coding: utf-8 -*-
"""扫描地图文件夹，识别版本 / 现有服务端 / 存档位置。"""

import os
import zipfile

from . import nbt, versions

# 从文件名判断加载器
JAR_HINTS = [
    ("paper",     ("paper",)),
    ("purpur",    ("purpur",)),
    ("fabric",    ("fabric-server", "fabric-loader", "server-launch",
                   "fabric-server-launch")),
    ("forge",     ("forge-", "forge_server", "minecraftforge")),
    ("neoforge",  ("neoforge",)),
    ("vanilla",   ("server.jar", "minecraft_server")),
]

# 精确文件名（小写）匹配，用于 fabric.jar / paper.jar 这类短名
JAR_EXACT = {
    "fabric.jar":   "fabric",
    "fabric-server.jar": "fabric",
    "paper.jar":    "paper",
    "purpur.jar":   "purpur",
    "forge.jar":    "forge",
    "neoforge.jar": "neoforge",
    "server.jar":   "vanilla",
}

def guess_loader_from_jar(name):
    low = name.lower()
    # 1) 精确名优先
    if low in JAR_EXACT:
        return JAR_EXACT[low]
    # 2) 包含匹配
    for loader, hints in JAR_HINTS:
        for h in hints:
            if h in low:
                return loader
    return None


def guess_loader_from_dir(d):
    """靠目录特征辅助判断（fabric 有 .fabric 目录）。"""
    try:
        names = set(os.listdir(d))
    except Exception:
        return None
    if ".fabric" in names:
        return "fabric"
    for sub in ("versions", "libraries"):
        if sub in names:
            # fabric/forge 都可能有，看看有没有专属文件
            for f in names:
                if f.lower().startswith("fabric"):
                    return "fabric"
    # ★ 新版 Forge：没有启动 jar，靠 libraries 里的 args 文件认
    import glob as _g
    for brand in ("net/minecraftforge/forge",
                  "net/minecraftforge/neoforge",
                  "net/neoforged/neoforge"):
        pat = os.path.join(d, "libraries", *brand.split("/"),
                           "*", "win_args.txt")
        if _g.glob(pat):
            return ("neoforge" if "neoforge" in brand or
                    "neoforged" in brand else "forge")
    if _g.glob(os.path.join(d, "libraries", "**", "win_args.txt"),
               recursive=True):
        return "forge"
    return None


def detect_jar_loader(path):
    """读 jar 内部特征判断加载器；判断不出返回 None。

    ★ 为什么不能只看文件名：
      Paper 的 jar 下载后叫 server.jar，而文件名规则会把
      server.jar 当成 vanilla；反过来 vanilla 的 server.jar 里
      也有 version.json。必须看 jar 里真正的启动类/标记：
        · Paper/Purpur：paperclip 引导（io/papermc/paperclip 等）
        · Fabric：install.properties 里有 fabric-loader-version
        · 原版 1.18+：Main-Class 是 net.minecraft.bundler.Main
    """
    try:
        with zipfile.ZipFile(path) as z:
            names = z.namelist()
            name_set = set(names)
            low_set = {n.lower() for n in names}
            manifest = ""
            if "META-INF/MANIFEST.MF" in name_set:
                manifest = z.read("META-INF/MANIFEST.MF").decode(
                    "utf-8", "ignore")
            mlow = manifest.lower()
            # Paper / Purpur（paperclip 引导）
            if ("paperclip" in mlow
                    or any(n.startswith(("io/papermc/paperclip/",
                                         "org/purpurmc/purpurclip/"))
                           for n in names)):
                return "purpur" if "purpur" in mlow else "paper"
            # Fabric 启动器 jar
            if "install.properties" in name_set:
                txt = z.read("install.properties").decode(
                    "utf-8", "ignore")
                if "fabric-loader-version" in txt:
                    return "fabric"
            # Forge / NeoForge 自带 mods 清单的启动 jar
            if "meta-inf/neoforge.mods.toml" in low_set:
                return "neoforge"
            if "meta-inf/mods.toml" in low_set:
                return "forge"
            # 原版：1.18+ 的 bundler，或旧版 server 主类
            if ("net.minecraft.bundler" in mlow
                    or "net.minecraft.server" in mlow):
                return "vanilla"
            # 兜底：bundler 形态的 versions.list（无 paperclip 标记）
            if "META-INF/versions.list" in name_set:
                return "vanilla"
    except Exception:
        return None
    return None


def find_server_jars(d):
    """列出目录里可能是服务端核心的 jar。"""
    if not os.path.isdir(d):
        return []
    out = []
    try:
        for f in sorted(os.listdir(d)):
            if not f.lower().endswith(".jar"):
                continue
            # 明显不是核心的跳过
            low = f.lower()
            if any(k in low for k in ("installer", "libraries",
                                      "worldedit", "spark")):
                continue
            out.append(f)
    except Exception:
        pass
    return out


def _region_dirs(world_dir, dim):
    cands = []
    if dim == "overworld":
        cands = [os.path.join(world_dir, "region"),
                 os.path.join(world_dir, "dimensions", "minecraft",
                              "overworld", "region")]
    elif dim == "nether":
        cands = [os.path.join(world_dir, "DIM-1", "region"),
                 os.path.join(world_dir, "dimensions", "minecraft",
                              "the_nether", "region")]
    else:
        cands = [os.path.join(world_dir, "DIM1", "region"),
                 os.path.join(world_dir, "dimensions", "minecraft",
                              "the_end", "region")]
                 
    existing = [c for c in cands if os.path.isdir(c)]
    
    # 兜底：如果上面找不到，往下搜索一层（兼容部分服务端自定义的维度文件夹）
    if not existing and dim == "overworld":
        for dp, dn, fn in os.walk(world_dir):
            if "region" in dn and world_dir != dp:
                cands.append(os.path.join(dp, "region"))
                break
                
    return existing or [c for c in cands if os.path.isdir(c)]


def count_region(world_dir, dim="overworld"):
    total = 0
    for rd in _region_dirs(world_dir, dim):
        try:
            total += len([f for f in os.listdir(rd)
                          if f.endswith(".mca")])
        except Exception:
            pass
    return total


def find_worlds(root, max_depth=3):
    """在 root 下找含 level.dat 的文件夹。"""
    worlds = []
    root = os.path.abspath(root)
    if not os.path.isdir(root):
        return worlds
    for dirpath, dirnames, filenames in os.walk(root):
        depth = dirpath[len(root):].count(os.sep)
        if depth >= max_depth:
            dirnames[:] = []
            continue
        if "level.dat" in filenames:
            worlds.append(dirpath)
            dirnames[:] = []   # 不往存档内部再找
    return worlds


def read_world_info(world_dir):
    """读 level.dat，返回世界信息字典。"""
    info = {"name": os.path.basename(world_dir) or world_dir,
            "version": None, "seed": None, "difficulty": None,
            "gamemode": None, "hardcore": False, "layout": "legacy"}
    ldp = os.path.join(world_dir, "level.dat")
    if not os.path.exists(ldp):
        return info
    root = nbt.load_dat(ldp)
    if not root:
        return info
    d = root.get("Data")
    if not isinstance(d, dict):
        d = root

    lv = d.get("LevelName")
    if isinstance(lv, str):
        # ★ 去除 MC 颜色代码（§b 等），防止界面显示乱码
        import re
        lv_clean = re.sub(r"\u00a7[0-9a-fk-orA-FK-OR]", "", lv)
        info["name"] = lv_clean.strip()
    
    # ★ 版本读取增强：兼容新版结构
    ver = d.get("Version")
    if isinstance(ver, dict):
        info["version"] = ver.get("Name") or ver.get("Id")
    elif isinstance(ver, str):
        info["version"] = ver
        
    info["data_version"] = d.get("DataVersion")
    
    # 如果读不到 Name，用 DataVersion 兜底
    if not info.get("version") and isinstance(info["data_version"], int):
        dv = info["data_version"]
        # 精确映射常见的 DataVersion，避免把 1.21.4 错认成 26.x
        dv_map = {
            19133: "1.12.2", 2586: "1.16.5", 2724: "1.17.1",
            2860: "1.18", 2865: "1.18.1", 2975: "1.18.2",
            3105: "1.19", 3117: "1.19.1", 3120: "1.19.2", 3218: "1.19.3",
            3337: "1.19.4", 3463: "1.20", 3465: "1.20.1", 3578: "1.20.2",
            3698: "1.20.3", 3700: "1.20.4", 3837: "1.20.5", 3839: "1.20.6",
            3953: "1.21", 3955: "1.21.1", 4082: "1.21.2/3", 4189: "1.21.4",
            4325: "1.21.5", 4435: "1.21.6", 4438: "1.21.7", 4440: "1.21.8",
            4556: "1.21.10", 4671: "1.21.11", 4786: "26.1", 4901: "26.2",
            5012: "26.3"
        }
        best_v = "未知"
        for k in sorted(dv_map.keys()):
            if dv >= k:
                best_v = dv_map[k]
        info["version"] = best_v

    diff = d.get("Difficulty")
    info["difficulty"] = {0: "和平", 1: "简单", 2: "普通",
                          3: "困难"}.get(diff, None)
    gm = d.get("GameType")
    info["gamemode"] = {0: "生存", 1: "创造", 2: "冒险",
                        3: "旁观"}.get(gm, None)
    info["hardcore"] = bool(d.get("hardcore"))

    seed = d.get("Seed")
    if isinstance(seed, int):
        info["seed"] = seed
    else:
        wgs = d.get("WorldGenSettings")
        if isinstance(wgs, dict) and isinstance(wgs.get("seed"), int):
            info["seed"] = wgs["seed"]
    if info["seed"] is None:
        info["seed"] = nbt.dig_key(root, "seed")

    if info["seed"] is None:
        # 新布局种子常在 world_gen_settings.dat
        for c in (os.path.join(world_dir, "data", "minecraft",
                               "world_gen_settings.dat"),
                  os.path.join(world_dir, "dimensions", "minecraft",
                               "overworld", "data", "minecraft",
                               "world_gen_settings.dat")):
            if os.path.exists(c):
                r2 = nbt.load_dat(c)
                v = nbt.dig_key(r2, "seed") if r2 else None
                if isinstance(v, int):
                    info["seed"] = v
                    break

    if os.path.isdir(os.path.join(world_dir, "dimensions", "minecraft")):
        info["layout"] = "new(26.1+)"

    return info


def scan_entry(folder):
    """扫描一个地图文件夹，返回条目 dict。"""
    folder = os.path.abspath(folder)
    e = {
        "folder": folder,
        "name": os.path.basename(folder.rstrip("\\/")) or folder,
        "server_dir": None,
        "world_dir": None,
        "world_name": None,
        "version": None,
        "loader": None,
        "jars": [],
        "has_run_bat": False,
        "props": None,
        "mods": 0,
        "plugins": 0,
        "seed": None,
        "difficulty": None,
        "gamemode": None,
        "regions": 0,
        "layout": "legacy",
        "notes": [],
    }
    if not os.path.isdir(folder):
        e["notes"].append("目录不存在")
        return e

    # server 子目录优先
    server_dir = folder
    sub = os.path.join(folder, "server")
    if os.path.isdir(sub):
        server_dir = sub
    e["server_dir"] = server_dir

    jars = find_server_jars(server_dir)
    e["jars"] = jars
    if jars:
        for j in jars:
            # 先看 jar 内部（能区分"Paper 装成了 server.jar"这种），
            # 认不出来再退回文件名规则
            l = detect_jar_loader(os.path.join(server_dir, j)) \
                or guess_loader_from_jar(j)
            if l:
                e["loader"] = l
                break
    if e["loader"] is None:
        # jar 名判断不出，靠目录特征
        dl = guess_loader_from_dir(server_dir)
        if dl:
            e["loader"] = dl
    if not jars and e["loader"] is None:
        e["notes"].append("未发现服务端核心")
    elif e["loader"] is None:
        e["loader"] = "unknown"

    e["has_run_bat"] = any(
        os.path.isfile(os.path.join(server_dir, n))
        for n in ("run.bat", "start.bat", "start.sh", "run.sh",
                  "启动.bat", "launch.bat", "server.bat"))

    p = os.path.join(server_dir, "server.properties")
    if os.path.isfile(p):
        try:
            d = {}
            for line in open(p, "r", encoding="utf-8", errors="replace"):
                line = line.strip()
                if "=" in line and not line.startswith("#"):
                    k, v = line.split("=", 1)
                    d[k] = v
            e["props"] = d
        except Exception:
            pass

    for subd, cnt in (("mods", "mods"), ("plugins", "plugins")):
        full = os.path.join(server_dir, subd)
        if os.path.isdir(full):
            try:
                e[cnt] = len([f for f in os.listdir(full)
                              if f.lower().endswith(".jar")])
            except Exception:
                pass

    worlds_all = find_worlds(server_dir, max_depth=3)
    if not worlds_all:
        e["notes"].append("未发现存档(level.dat)")
        return e
    # ★ 不能把 backups/ 里备份的存档当成本服主存档：
    #   以前 os.walk 先走到 backups，信息面板显示的"存档"就变成了
    #   ...\backups\xxx_20261021_231932，region/版本也跟着读错。
    worlds = [w for w in worlds_all
              if "backups" not in [p.lower() for p in os.path.relpath(
                  w, server_dir).split(os.sep)]]
    if not worlds:
        worlds = worlds_all
        e["notes"].append("主存档没找到，只找到备份里的存档")

    # 选主世界：优先 server.properties 的 level-name，
    # 其次叫 world 的，最后第一个（都已排除 backups）
    main = None
    lname = ((e.get("props") or {}).get("level-name") or "").strip()
    if lname:
        for w in worlds:
            if os.path.basename(w).lower() == lname.lower():
                main = w
                break
    if main is None:
        for w in worlds:
            if os.path.basename(w).lower() == "world":
                main = w
                break
    if main is None:
        main = worlds[0]
    e["world_dir"] = main

    wi = read_world_info(main)
    e["version_source"] = "level.dat"
    e["world_name"] = wi["name"]
    e["version"] = wi["version"]
    e["seed"] = wi["seed"]
    e["difficulty"] = wi["difficulty"]
    e["gamemode"] = wi["gamemode"]
    e["layout"] = wi["layout"]
    e["regions"] = count_region(main, "overworld")

    return e


def scan_root(root, limit=60):
    """扫描总目录下每个子文件夹。"""
    root = os.path.abspath(root)
    out = []
    if not os.path.isdir(root):
        return out
    try:
        names = sorted(os.listdir(root))
    except Exception:
        return out
    for n in names:
        if n.startswith("."):
            continue
        full = os.path.join(root, n)
        if not os.path.isdir(full):
            continue
        try:
            out.append(scan_entry(full))
        except Exception as ex:
            out.append({"folder": full, "name": n,
                        "notes": ["扫描失败: %r" % ex]})
        if len(out) >= limit:
            break
    return out
