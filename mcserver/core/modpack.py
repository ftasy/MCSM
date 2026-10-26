# -*- coding: utf-8 -*-
"""V0.4.0：整合包 (Modpack) 一键导入。

支持两种格式：
  · Modrinth   —— zip 内带 modrinth.index.json（files 有直链 + sha1）
  · CurseForge —— zip 内带 manifest.json（files 是 projectID+fileID，
                  下载链接走 MCIM 镜像逐条解析）

流程：识别类型 → 解析 MC 版本/加载器/模组清单 → 自动创建服务器目录
      → install_core 装 Forge/Fabric 核心 → 4 线程并发下载全部模组
      → 拷贝 overrides 覆盖文件 → 生成基础 server.properties。

所有网络请求都是耗时操作，调用方必须放进 Worker 线程。
"""

import os
import re
import zipfile
import json
import hashlib
import shutil
import tempfile
import threading
from concurrent.futures import ThreadPoolExecutor, as_completed

from . import install
from . import sources


def _clean_name(name):
    """文件名 -> 安全目录名。"""
    name = re.sub(r"[\\/:*?\"<>|\s]+", "_", str(name or "")).strip("_")
    return name or "Modpack"


def detect_kind(zip_path):
    """判断 zip 是不是整合包。返回 "modrinth" / "curse" / None。"""
    try:
        with zipfile.ZipFile(zip_path) as z:
            names = z.namelist()
    except Exception:
        return None
    if any(n.endswith("modrinth.index.json") for n in names):
        return "modrinth"
    if any(n.endswith("manifest.json") for n in names):
        return "curse"
    return None


def _read_json_in_zip(zp, suffix):
    for n in zp.namelist():
        if n.endswith(suffix):
            with zp.open(n) as f:
                return json.loads(f.read().decode("utf-8", "replace"))
    return None


def parse(zip_path):
    """解析整合包元信息（本地 zip 读取，很快）。

    返回 dict：
      {kind, name, mc_version, loader, loader_version, mods, overrides}
        mods:
          Modrinth -> {path, url, sha1, size}
          Curse    -> {projectID, fileID, required}
    """
    kind = detect_kind(zip_path)
    if not kind:
        raise install.InstallError(
            "不是整合包：zip 里没有 modrinth.index.json 或 manifest.json")
    if kind == "modrinth":
        return _parse_modrinth(zip_path)
    return _parse_curse(zip_path)


def _parse_modrinth(zip_path):
    with zipfile.ZipFile(zip_path) as z:
        idx = _read_json_in_zip(z, "modrinth.index.json")
        if not idx:
            raise install.InstallError("modrinth.index.json 解析失败")
    deps = idx.get("dependencies") or {}
    mc = str(deps.get("minecraft") or "")
    loader, lv = None, None
    # 优先级：neoforge > forge > fabric-loader > quilt-loader
    for key in ("neoforge", "forge", "fabric-loader", "quilt-loader"):
        if deps.get(key):
            loader = key.replace("-loader", "")
            lv = str(deps[key])
            break
    if not loader:
        raise install.InstallError("整合包没声明加载器（forge/fabric 等）")
    mods = []
    for f in idx.get("files") or []:
        dl = f.get("downloads") or []
        if not dl:
            continue
        hashes = f.get("hashes") or {}
        mods.append({"path": f.get("path") or "",
                     "url": dl[0],
                     "sha1": hashes.get("sha1") or "",
                     "size": f.get("fileSize") or 0})
    return {"kind": "modrinth", "name": idx.get("name") or "",
            "mc_version": mc, "loader": loader, "loader_version": lv,
            "mods": mods, "overrides": bool(idx.get("overrides"))}


def _parse_curse(zip_path):
    with zipfile.ZipFile(zip_path) as z:
        mf = _read_json_in_zip(z, "manifest.json")
        if not mf:
            raise install.InstallError("manifest.json 解析失败")
    mc = str((mf.get("minecraft") or {}).get("version") or "")
    mods = []
    for f in mf.get("files") or []:
        mods.append({"projectID": f.get("projectID"),
                     "fileID": f.get("fileID"),
                     "required": f.get("required", True)})
    loader, lv = None, None
    mls = (mf.get("minecraft") or {}).get("modLoaders") or []
    for ml in mls:
        lid = str(ml.get("id") or "")
        for pref in ("neoforge", "forge", "fabric", "quilt"):
            if lid.startswith(pref + "-"):
                loader, lv = pref, lid[len(pref) + 1:]
                break
        if loader:
            break
    if not loader:
        raise install.InstallError("整合包没声明加载器")
    return {"kind": "curse", "name": mf.get("name") or "",
            "mc_version": mc, "loader": loader, "loader_version": lv,
            "mods": mods, "overrides": bool(mf.get("overrides"))}


def _copy_tree(src, dst):
    for root, dirs, files in os.walk(src):
        rel = os.path.relpath(root, src)
        t = dst if rel == "." else os.path.join(dst, rel)
        os.makedirs(t, exist_ok=True)
        for f in files:
            shutil.copy2(os.path.join(root, f), os.path.join(t, f))


def _ensure_props(server_dir):
    p = os.path.join(server_dir, "server.properties")
    if os.path.isfile(p):
        return
    with open(p, "w", encoding="utf-8", newline="") as f:
        f.write("server-port=25565\nmotd=MCSM Modpack Server\n")


def _download_mods(server_dir, meta, log):
    mods_dir = os.path.join(server_dir, "mods")
    os.makedirs(mods_dir, exist_ok=True)
    ok, fail = [], []

    if meta["kind"] == "modrinth":
        tasks = [m for m in meta["mods"] if m.get("url")]

        def work(m):
            rel = m.get("path") or ""
            if not rel:
                rel = os.path.join("mods", "file.jar")
            dest = os.path.join(server_dir, rel)
            try:
                install._download(m["url"], dest, timeout=300)
                if m.get("sha1"):
                    h = hashlib.sha1()
                    with open(dest, "rb") as f:
                        for chunk in iter(lambda: f.read(65536), b""):
                            h.update(chunk)
                    if h.hexdigest().lower() != m["sha1"].lower():
                        os.remove(dest)
                        return False, rel, "校验失败 (sha1)"
                return True, rel, ""
            except Exception as e:
                return False, rel, install._brief(e)
    else:
        tasks = [m for m in meta["mods"]
                 if m.get("projectID") and m.get("fileID")]

        def work(m):
            pid, fid = m["projectID"], m["fileID"]
            r = sources.cf_file_url(pid, fid)
            if r.get("err"):
                return False, "%s/%s" % (pid, fid), r["err"]
            fname = r.get("filename") or ("%s-%s.jar" % (pid, fid))
            dest = os.path.join(mods_dir, os.path.basename(fname))
            try:
                install._download(r["url"], dest, timeout=300)
                return True, os.path.basename(fname), ""
            except Exception as e:
                return False, fname, install._brief(e)

    if not tasks:
        log("  清单里没有可下载的模组文件")
        return ok, fail
    with ThreadPoolExecutor(max_workers=4) as ex:
        futs = [ex.submit(work, m) for m in tasks]
        for fu in as_completed(futs):
            try:
                okb, name, why = fu.result()
            except Exception as e:
                okb, name, why = False, "?", install._brief(e)
            if okb:
                ok.append(name)
                log("  [OK] %s" % name)
            else:
                fail.append(name)
                log("  [失败] %s - %s" % (name, why))
    return ok, fail


def _fast_extract(zip_path, dest):
    """★ V1.4.0：大整合包解压提速。

    zipfile.extractall 受 GIL 限制，解压大型整合包（几百 MB /
    几万个小文件）极慢。Windows 10+ 自带 tar.exe（原生多线程 C
    实现，支持 zip 格式），通常快几倍到十几倍。

    调用方必须保证 dest 目录已存在。tar 不可用 / 失败时自动回退
    zipfile.extractall，保证功能不丢。
    """
    try:
        import subprocess as _sp
        r = _sp.run(
            ["tar", "-xf", zip_path, "-C", dest],
            capture_output=True, timeout=1800)
        if r.returncode == 0:
            return
    except Exception:
        pass
    with zipfile.ZipFile(zip_path) as z:
        z.extractall(dest)


def install_modpack(zip_path, root, name=None, on_log=None):
    """整包导入主流程（Worker 线程内调用）。

    返回 {server_dir, name, mc_version, loader, mods_ok, mods_fail}
    """
    def log(m):
        if on_log:
            on_log(m)

    tmp = tempfile.mkdtemp(prefix="mcs_modpack_")
    try:
        _fast_extract(zip_path, tmp)

        meta = parse(zip_path)
        server_name = _clean_name(
            name or meta.get("name")
            or os.path.splitext(os.path.basename(zip_path))[0])
        base_dir = os.path.join(root, server_name)
        server_dir = base_dir
        n = 1
        while os.path.exists(server_dir):
            server_dir = "%s_%d" % (base_dir, n)
            n += 1
        os.makedirs(server_dir, exist_ok=True)

        mc = meta["mc_version"]
        loader = meta["loader"]
        lv = meta.get("loader_version") or None
        log("== 导入整合包：%s ==" % server_name)
        if not mc:
            raise install.InstallError("整合包没写 MC 版本")
        log("MC %s · %s%s" % (mc, loader, (" " + str(lv)) if lv else ""))

        # 1) 装核心（forge/fabric/neoforge；quilt 暂按 fabric 处理）
        core_loader = loader
        if loader == "quilt":
            core_loader = "fabric"
            log("  [!] Quilt 整合包暂按 Fabric 安装核心")
        install.install_core(core_loader, server_dir, mc,
                             loader_version=lv, on_log=log)

        # 2) 并发下载全部模组
        mods = meta["mods"] or []
        if mods:
            log("下载 %d 个模组（4 线程并发 + 断点续传）..." % len(mods))
        ok, fail = _download_mods(server_dir, meta, log)

        # 3) overrides 覆盖（配置/数据文件）
        ov = os.path.join(tmp, "overrides")
        if os.path.isdir(ov):
            log("拷贝 overrides 覆盖文件 ...")
            _copy_tree(ov, server_dir)

        # 4) 基础 server.properties（保证扫描能识别）
        _ensure_props(server_dir)

        log("== 导入完成：成功 %d 个模组，失败 %d 个 =="
            % (len(ok), len(fail)))
        return {"server_dir": server_dir, "name": server_name,
                "mc_version": mc, "loader": core_loader,
                "mods_ok": ok, "mods_fail": fail}
    finally:
        shutil.rmtree(tmp, ignore_errors=True)
