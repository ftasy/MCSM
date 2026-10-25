# -*- coding: utf-8 -*-
"""加载器（Fabric / Forge / NeoForge / Paper）更新，以及 Fabric API 更新。

为什么要单独一个模块：
  装核心是一次性的（初始化时），但加载器是会长版本的
  —— Fabric loader 隔三差五出新版本修 bug，
  Forge 更是几乎每周一个 build。
  以前要更新只能"删核心重装"，那会把存档配置折腾一遍。

  现在：只换核心文件，存档 / 模组 / 配置一个都不碰。
"""

import os
import re
import json
import zipfile

from . import install, modman, scanner

# Fabric 把当前版本写在这几个地方（不同安装方式不一样）
_FABRIC_MARKERS = [
    "fabric-server-launch.properties",
    "fabric.properties",
]


def detect(server_dir):
    """猜这个服当前用的是什么加载器 + 版本。

    返回 {"loader":..., "version":...}，猜不出来返回 None。
    """
    if not server_dir or not os.path.isdir(server_dir):
        return None

    # ---- Fabric ----
    # 启动时会在 server_dir 留下 launch properties
    for name in _FABRIC_MARKERS:
        p = os.path.join(server_dir, name)
        if os.path.isfile(p):
            try:
                txt = open(p, "r", encoding="utf-8",
                           errors="ignore").read()
                m = re.search(r"loader\s*=\s*(\S+)", txt) \
                    or re.search(r"fabric\.loader\.version\s*=\s*(\S+)",
                                 txt)
                if m:
                    return {"loader": "fabric",
                            "version": m.group(1),
                            "source": name}
            except Exception:
                pass
    # ★ 新版下载的 fabric.jar 是个启动器，文件名不带版本；
    #   但 jar 内有 install.properties：
    #     fabric-loader-version=0.19.5
    #     game-version=26.3
    #   不读它的话 detect() 永远返回 None → 每次点"更新"都会
    #   重新下载同一个版本，界面显示"更新成功"但其实没变。
    for f in os.listdir(server_dir):
        if not f.lower().endswith(".jar") or "fabric" not in f.lower():
            continue
        try:
            with zipfile.ZipFile(os.path.join(server_dir, f)) as z:
                names = z.namelist()
                if "install.properties" in names:
                    txt = z.read("install.properties").decode(
                        "utf-8", "ignore")
                    m = re.search(
                        r"fabric-loader-version\s*=\s*(\S+)", txt)
                    if m:
                        return {"loader": "fabric",
                                "version": m.group(1), "source": f}
        except Exception:
            pass
    # jar 名里常带版本：fabric-server-mc.1.20.1-loader.0.19.5-launcher.x.jar
    for f in os.listdir(server_dir):
        if f.lower().endswith(".jar"):
            m = re.search(r"loader[.-](\d+\.\d+\.\d+)", f, re.I)
            if m and "fabric" in f.lower():
                return {"loader": "fabric", "version": m.group(1),
                        "source": f}

    # ---- Forge / NeoForge ----
    for f in os.listdir(server_dir):
        low = f.lower()
        if not low.endswith(".jar"):
            continue
        # 匹配 Forge: forge-1.20.1-47.2.0.jar
        m = re.search(r"^forge-(\d[\w.\-]+?)-(\d[\w.\-]+?)"
                      r"(?:-shim|-server|-installer)?\.jar$", low)
        if m:
            return {"loader": "forge", "version": m.group(2),
                    "mc_version": m.group(1), "source": f}
        # 匹配 NeoForge: neoforge-21.1.1.jar (只有一段版本号)
        m = re.search(r"^neoforge-(\d[\w.\-]+?)"
                      r"(?:-shim|-server|-installer)?\.jar$", low)
        if m:
            return {"loader": "neoforge", "version": m.group(1),
                    "source": f}
    # 有 win_args.txt 但没 jar -> 新版 Forge（bootstrap 启动）
    win = _find_win_args(server_dir)
    if win:
        m = re.search(r"forge[\\\\/](\d[\w.\-]+?)[\\\\/]", win)
        ver = m.group(1) if m else ""
        loader = "neoforge" if "neoforge" in win.lower() else "forge"
        return {"loader": loader, "version": ver, "source": "win_args"}

    # ---- Paper / Purpur ----
    # ★ 必须按 jar 内部 paperclip 标记判断：
    #   vanilla 的 server.jar 里也有 version.json，只认 version.json
    #   会把原版误判成 paper（老代码就是这么错的）。
    for cand in ("paper.jar", "purpur.jar", "server.jar"):
        p = os.path.join(server_dir, cand)
        if not os.path.isfile(p):
            continue
        kind = scanner.detect_jar_loader(p)
        if kind not in ("paper", "purpur"):
            continue
        ver = ""
        try:
            with zipfile.ZipFile(p) as z:
                for n in ("version.json", "META-INF/version.json"):
                    if n in z.namelist():
                        d = json.loads(z.read(n))
                        ver = d.get("version") or d.get("id") or ""
                        break
        except Exception:
            pass
        return {"loader": kind, "version": ver, "source": cand}
    return None


def _find_win_args(server_dir):
    """找新版 Forge 的 win_args.txt。"""
    root = os.path.join(server_dir, "libraries", "net",
                        "minecraftforge")
    if not os.path.isdir(root):
        root2 = os.path.join(server_dir, "libraries", "net",
                             "neoforged")
        root = root2 if os.path.isdir(root2) else root
    if not os.path.isdir(root):
        return None
    for dp, dn, fn in os.walk(root):
        for f in fn:
            if f == "win_args.txt":
                return os.path.join(dp, f)
    return None


# =========================================================
#  Fabric
# =========================================================
def _vtuple(s):
    """把版本号变成可比较的元组（0.20.1 > 0.19.5）。"""
    import re as _re
    return tuple(int(x) for x in _re.findall(r"\d+", str(s)))


def update_fabric(server_dir, mc_version, want=None, on_log=None):
    """换掉 fabric.jar（存档/模组/配置都不动）。"""
    def log(m):
        if on_log:
            on_log(m)

    old = detect(server_dir)
    if old:
        log("当前 loader：%s" % old.get("version"))

    cur = install.fabric_loader_version(mc_version, want=want)
    if not want:
        # 提醒一下"有更新的测试版但没自动用"——不然用户会以为没更新到最新
        try:
            cands = install.fabric_loader_candidates(mc_version)
            betas = [c["version"] for c in cands
                     if not c["stable"]
                     and _vtuple(c["version"]) > _vtuple(cur)]
            if betas:
                log("提示：%s 是测试版（beta），已跳过；稳定版最新 = %s"
                    % (betas[0], cur))
        except Exception:
            pass
    if old and old.get("version") == cur:
        log("已经是最新：%s" % cur)
        return {"ok": True, "version": cur, "changed": False}

    # 先删旧的 fabric 启动 jar（只删核心，不碰别的）
    removed = []
    stuck = []
    for f in os.listdir(server_dir):
        if f.lower().endswith(".jar") and (
                f.lower().startswith("fabric") or f == "fabric.jar"):
            try:
                os.remove(os.path.join(server_dir, f))
                removed.append(f)
            except Exception as e:
                # ★ 以前静默。但删不掉意味着新旧两个 jar 同时躺在目录里，
                #   启动时用哪个全看文件名排序 —— 会出现"更新了却还是旧版本"。
                #   必须说出来。
                stuck.append((f, repr(e)))
    if removed:
        log("已移除旧核心：%s" % "、".join(removed))
    if stuck:
        log("[警告] 有 %d 个旧核心删不掉，新旧会同时存在：")
        for f, e in stuck[:5]:
            log("       %s  (%s)" % (f, e))
        log("       常见原因：服务器还在运行，文件被占用。请先停止服务器。")

    r = install.install_fabric(server_dir, mc_version,
                               loader_version=want, on_log=log)
    log("✓ loader 已更新到 %s" % r["loader"])
    return {"ok": True, "version": r["loader"], "changed": True,
            "old": (old or {}).get("version")}


# =========================================================
#  Forge / NeoForge
# =========================================================
def forge_latest(mc_version, loader="forge"):
    """该 MC 版本上最新的 Forge / NeoForge。"""
    try:
        if loader == "neoforge":
            return install.neoforge_candidates(mc_version)[0]
        return install.forge_latest_version(mc_version)
    except Exception:
        return None


def update_forge(server_dir, mc_version, loader="forge", want=None,
                 java_path=None, on_log=None):
    """跑 installer 更新 Forge / NeoForge。

    注意：新版 Forge 装完【不生成 jar】，而是 win_args.txt，
    这是正常的，别当成失败。
    """
    def log(m):
        if on_log:
            on_log(m)

    old = detect(server_dir)
    # Forge/NeoForge 的 installer 必须有 Java；调用方没给就自动找一个
    if not java_path:
        try:
            from . import versions as _versions
            from . import java as _java
            need = _versions.need_java(mc_version)
            cands = _java.find_javas()
            pick = _java.pick_for(cands, need) if cands else None
            if pick:
                java_path = pick.get("path")
                if java_path:
                    log("自动使用 Java: %s" % java_path)
        except Exception:
            pass

    tgt = want or forge_latest(mc_version, loader)
    if not tgt:
        return {"ok": False, "err": "查不到 %s 的 %s 版本"
                % (loader, mc_version)}
    log("目标版本：%s%s" % (tgt,
                           "" if not old else "（当前 %s）"
                           % old.get("version")))

    # 移除旧的核心 jar
    removed = []
    stuck = []
    for f in os.listdir(server_dir):
        low = f.lower()
        if not low.endswith(".jar"):
            continue
        if low.startswith(loader) or low.startswith("forge"):
            try:
                os.remove(os.path.join(server_dir, f))
                removed.append(f)
            except Exception as e:
                # ★ 以前静默，和 update_fabric 一样的问题：
                #   删不掉 = 新旧两个 jar 同时躺着，启动时用哪个看文件名排序，
                #   于是"更新了却还是旧版本"。必须说出来。
                stuck.append((f, repr(e)))
    if removed:
        log("已移除旧核心：%s" % "、".join(removed))
    if stuck:
        log("[警告] 有 %d 个旧核心删不掉，新旧会同时存在："
            % len(stuck))
        for f, r in stuck:
            log("    %s -> %s" % (f, r))
        log("    装完后请手动确认启动用的是哪个 jar（多半是文件被占用，"
            "先停服再更新）。")

    if loader == "neoforge":
        r = install.install_neoforge(server_dir, mc_version,
                                     java_path=java_path,
                                     loader_version=tgt, on_log=log)
    else:
        r = install.install_forge(server_dir, mc_version,
                                  java_path=java_path,
                                  loader_version=tgt, on_log=log)
    log("✓ %s 已更新" % loader)
    return {"ok": True, "version": tgt, "changed": True,
            "old": (old or {}).get("version")}


# =========================================================
#  Paper
# =========================================================
def update_paper(server_dir, mc_version, want=None, on_log=None):
    def log(m):
        if on_log:
            on_log(m)
    old = detect(server_dir)
    for f in ("paper.jar", "server.jar"):
        p = os.path.join(server_dir, f)
        if os.path.isfile(p):
            try:
                os.remove(p)
                log("已移除旧核心：%s" % f)
            except Exception as e:
                log("[警告] 旧核心 %s 删不掉（%s），新旧会同时存在"
                    % (f, repr(e)))
    r = install.install_paper(server_dir, mc_version,
                              loader_version=want, on_log=log)
    return {"ok": True, "version": r.get("build") or want or "最新",
            "changed": True, "old": (old or {}).get("version")}


# =========================================================
#  Fabric API 单独更新
# =========================================================
FABRIC_API_SLUG = "fabric-api"


def find_fabric_api(mods_dir):
    """在 mods 目录里找已装的 Fabric API。

    ★ modman.scan_mods() 的参数是【服务器目录】（里面自己拼 mods/），
      这里以前直接把 mods 目录传进去 —— 实际扫的是 mods/mods（不存在），
      永远返回空。结果：
        · 界面上永远显示"没找到已装的 Fabric API"；
        · 更新时旧 jar 不会被改名留底，新旧两个 Fabric API 同时躺在
          mods 里，模组加载直接报重复/版本冲突。
    """
    if not mods_dir or not os.path.isdir(mods_dir):
        return None
    mods = modman.scan_mods_dir(mods_dir)
    for m in mods:
        name = (m.get("name") or "").lower()
        slug = (m.get("slug") or "").lower()
        base = os.path.basename(m.get("path") or "").lower()
        if ("fabric api" in name or "fabric-api" in slug
                or base.startswith("fabric-api")):
            return m
    return None


def update_fabric_api(mods_dir, mc_version, loader="fabric",
                      on_log=None):
    """把 Fabric API 更新到最新（旧文件改名保留，不直接删）。

    为什么单独做：Fabric API 是几乎所有模组的前置，
    它版本不对会导致所有模组一起加载失败。
    而它更新极频繁，手动下很容易下错。
    """
    def log(m):
        if on_log:
            on_log(m)

    cur = find_fabric_api(mods_dir)
    if cur:
        log("已装：%s（%s）" % (cur.get("name"),
                               cur.get("version") or "未知版本"))
    else:
        log("没找到已装的 Fabric API，直接装最新的")

    r = modman.install_from_project(
        FABRIC_API_SLUG, mc_version or None, loader or None,
        mods_dir, on_log=log)
    if not r.get("ok"):
        return {"ok": False, "reason": r.get("reason")}

    # 旧的改名留着（万一新版有问题能换回来）
    if cur and cur.get("path"):
        old_p = cur["path"]
        new_p = os.path.join(mods_dir, r["file"])
        if os.path.abspath(old_p) != os.path.abspath(new_p) \
                and os.path.isfile(old_p):
            bak = old_p + ".old"
            try:
                if os.path.exists(bak):
                    os.remove(bak)
                os.rename(old_p, bak)
                log("旧的已留底：%s" % os.path.basename(bak))
            except Exception:
                pass
    log("✓ Fabric API -> %s" % (r.get("version") or "最新版"))
    return {"ok": True, "file": r["file"],
            "version": r.get("version") or "",
            "old": (cur or {}).get("version") or ""}



