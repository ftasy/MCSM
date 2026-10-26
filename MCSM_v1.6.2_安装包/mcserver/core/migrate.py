# -*- coding: utf-8 -*-
"""把分散的地图文件夹统一搬迁到新总目录。"""

import os
import shutil


def safe_name(name):
    """清洗成安全的文件夹名（保留中文，只剔除 Windows 非法字符）。"""
    import re
    s = str(name or "")
    # 只过滤 Windows 不支持的字符
    s = re.sub(r'[\\/:*?"<>|]+', "", s)
    # 首尾的空格和点去掉（Windows 文件夹名不能以点结尾）
    s = s.strip(". ")
    # 连续空格合并
    s = re.sub(r"\s+", " ", s)
    return s or "map"


def plan(src_root, dst_root):
    """生成搬迁计划：[{src, dst, name, exists}]，不实际执行。"""
    plans = []
    if not os.path.isdir(src_root):
        return plans
    for n in sorted(os.listdir(src_root)):
        full = os.path.join(src_root, n)
        if not os.path.isdir(full) or n.startswith("."):
            continue
        # 源文件夹里可能有 server 子目录
        src_server = os.path.join(full, "server")
        src = src_server if os.path.isdir(src_server) else full
        dst = os.path.join(dst_root, safe_name(n))
        if os.path.isdir(os.path.join(full, "server")):
            dst = os.path.join(dst, "server")
        plans.append({"src": src, "dst": dst, "name": n,
                      "exists": os.path.exists(dst)})
    return plans


def do_migrate(plans, copy=True, on_progress=None, on_log=None):
    """执行搬迁。copy=True 为复制（安全），False 为移动。"""
    log = on_log or (lambda m: None)
    results = []
    for i, p in enumerate(plans):
        if on_progress:
            on_progress(i, len(plans), p["name"])
        try:
            if p["exists"]:
                results.append((p["name"], False, "目标已存在，跳过"))
                log("  [跳过] %s（目标已存在）" % p["name"])
                continue
            
            os.makedirs(os.path.dirname(p["dst"]), exist_ok=True)
            if copy:
                log("  正在复制 %s ..." % p["name"])
                shutil.copytree(p["src"], p["dst"], dirs_exist_ok=True)
            else:
                log("  正在移动 %s ..." % p["name"])
                # 跨盘移动容易失败，加入 ignore_errors 兜底
                shutil.move(p["src"], p["dst"])
            
            results.append((p["name"], True, "复制完成" if copy else "移动完成"))
            log("  [OK] %s 完成" % p["name"])
        except Exception as ex:
            err_msg = repr(ex)
            log("  [失败] %s - %s" % (p["name"], err_msg))
            
            # ★ 关键修补：如果复制到一半失败，尝试把残缺的目标文件夹删掉
            #   避免下次搬迁时因为“目标已存在”被直接跳过，导致服务端彻底损坏
            if copy and os.path.isdir(p["dst"]):
                try:
                    shutil.rmtree(p["dst"], ignore_errors=True)
                    log("  [清理] 已删除不完整的目标文件夹")
                except Exception:
                    pass
                    
            results.append((p["name"], False, err_msg))
    return results


# =========================================================
#  新导入：创建「版本-地图名」文件夹，地图放进去
# =========================================================
def probe_world(src):
    """探查一个源文件夹，找出世界目录、版本、世界名。

    返回 {"world_dir", "version", "world_name", "kind"}
    kind: "world"  源本身含 level.dat
          "inner"  源里有个 world/ 子目录含 level.dat
          "none"   没找到
    """
    from . import scanner
    out = {"world_dir": None, "version": None,
           "world_name": None, "kind": "none"}

    if os.path.isfile(os.path.join(src, "level.dat")):
        out["kind"] = "world"
        out["world_dir"] = src
    else:
        inner = os.path.join(src, "world")
        if os.path.isfile(os.path.join(inner, "level.dat")):
            out["kind"] = "inner"
            out["world_dir"] = inner
        else:
            # 往下找一层
            try:
                for n in sorted(os.listdir(src)):
                    full = os.path.join(src, n)
                    if os.path.isdir(full) and os.path.isfile(
                            os.path.join(full, "level.dat")):
                        out["kind"] = "inner"
                        out["world_dir"] = full
                        break
            except Exception:
                pass
            if out["world_dir"] is None:
                # 再深一层（有些地图是 xxx/world/ 或 xxx/server/world/）
                try:
                    for n in sorted(os.listdir(src)):
                        sub = os.path.join(src, n)
                        if not os.path.isdir(sub):
                            continue
                        for m in sorted(os.listdir(sub)):
                            full = os.path.join(sub, m)
                            if os.path.isdir(full) and os.path.isfile(
                                    os.path.join(full, "level.dat")):
                                out["kind"] = "inner"
                                out["world_dir"] = full
                                break
                        if out["world_dir"]:
                            break
                except Exception:
                    pass
    if out["world_dir"] is None:
        return out

    try:
        wi = scanner.read_world_info(out["world_dir"])
    except Exception:
        wi = {}
    out["version"] = wi.get("version")
    out["world_name"] = wi.get("name") or os.path.basename(
        out["world_dir"])
    return out



def safe_ver_name(ver):
    """版本名保留点号：1.21.3 而不是 1-21-3。"""
    import re
    v = (ver or "").strip()
    v = re.sub(r"[^0-9A-Za-z._-]", "", v)
    if not v:
        return "unknown"
    return v


def plan_import(src, dst_root):
    """为单个源文件夹规划导入。

    目标结构：
        dst_root/{版本}-{地图名}/
            ├── {地图名}/        <- 世界内容（level.dat, region...）
            └── (核心稍后装在这层)

    返回 {"server_name", "server_dir", "world_subdir",
          "world_src", "version", "world_name",
          "exists", "kind"}
    """
    info = probe_world(src)
    ver = info["version"] or "unknown"
    wname = info["world_name"] or os.path.basename(src)
    disp = clean_display_name(wname)

    safe_ver = safe_ver_name(ver)
    safe_map = safe_name(disp) or "map"
    # 世界文件夹名：用地图名（MC 会用 level-name 拼路径，必须干净）
    world_sub = safe_map
    server_name = "%s-%s" % (safe_ver, safe_map)
    server_dir = os.path.join(dst_root, server_name)

    return {
        "src": src,
        "server_name": server_name,
        "server_dir": server_dir,
        "world_subdir": world_sub,
        "world_src": info["world_dir"],
        "world_dst": os.path.join(server_dir, world_sub),
        "version": info["version"],
        "world_name": wname,
        "kind": info["kind"],
        "exists": os.path.exists(server_dir),
        "safe_level_name": world_sub,
    }


def clean_display_name(name):
    """去掉 § 颜色代码等，用于生成文件夹名。"""
    from . import builder
    try:
        return builder.clean_display(name)
    except Exception:
        import re
        return re.sub(r"\u00a7.", "", name or "")


def do_import(plan, copy=True, on_log=None):
    """执行导入：建文件夹 -> 把世界放进去。

    返回 (成功?, 说明)
    """
    import shutil as sh
    log = on_log or (lambda m: None)

    if plan["kind"] == "none":
        return False, "没找到 level.dat，不确定这是不是一个 MC 世界"

    # 实时检查，不用 plan 里的快照
    if os.path.exists(plan["server_dir"]):
        return False, "目标已存在: %s" % plan["server_dir"]

    if not plan["world_src"] or not os.path.isdir(plan["world_src"]):
        return False, "世界目录无效: %s" % plan["world_src"]

    try:
        os.makedirs(plan["server_dir"], exist_ok=True)
        log("创建: %s" % plan["server_name"])

        dst = plan["world_dst"]
        if copy:
            sh.copytree(plan["world_src"], dst, dirs_exist_ok=True)
            log("复制世界 -> %s/" % plan["world_subdir"])
        else:
            sh.move(plan["world_src"], dst)
            log("移动世界 -> %s/" % plan["world_subdir"])

        # ★ 处理旧版本分维度的文件夹
        src_dir = os.path.dirname(plan["world_src"])
        src_base = os.path.basename(plan["world_src"])
        dst_dir = os.path.dirname(dst)
        dst_base = os.path.basename(dst)
        
        for suffix in ("_nether", "_the_end"):
            old_dim = os.path.join(src_dir, src_base + suffix)
            if os.path.isdir(old_dim):
                new_dim = os.path.join(dst_dir, dst_base + suffix)
                log("发现旧版维度文件夹: %s，一并复制" % (src_base + suffix))
                if copy:
                    sh.copytree(old_dim, new_dim, dirs_exist_ok=True)
                else:
                    sh.move(old_dim, new_dim)
                    
        # ★ 把服务端的配置文件、模组、插件一并带过去
        for f in ("server.properties", "eula.txt", "run.bat", "run.sh",
                  "banned-ips.json", "banned-players.json", "ops.json",
                  "whitelist.json", "usercache.json"):
            src_f = os.path.join(src_dir, f)
            if os.path.isfile(src_f) and copy:
                try:
                    new_f = os.path.join(plan["server_dir"], f)
                    if not os.path.exists(new_f):
                        sh.copy2(src_f, new_f)
                except Exception:
                    pass
        log("已顺带复制服务端配置文件")

        for d in ("mods", "plugins", "config", "scripts"):
            src_d = os.path.join(src_dir, d)
            if os.path.isdir(src_d) and copy:
                new_d = os.path.join(plan["server_dir"], d)
                log("一并复制目录: %s/" % d)
                try:
                    sh.copytree(src_d, new_d, dirs_exist_ok=True)
                except Exception as e:
                    log("  [警告] 复制 %s 时出错: %r" % (d, e))

        return True, "完成"

    except Exception as e:
        return False, repr(e)



