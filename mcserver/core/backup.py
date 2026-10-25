# -*- coding: utf-8 -*-
"""存档备份 / 恢复。只备份真正的存档文件夹，不碰整个服务端。"""

import os
import re
import shutil
import time
import zipfile

BACKUP_DIRNAME = "backups"


# 恢复时旧存档的前缀（world_old_20261018_174225），
# 不应被当成可备份的存档
OLD_PREFIX = re.compile(r"^.+_old_\d{8}_\d{6}$")

def is_world_dir(d):
    """判断一个目录是不是 MC 存档。"""
    if not os.path.isdir(d):
        return False
    return os.path.isfile(os.path.join(d, "level.dat"))


def find_worlds(server_dir, max_depth=3):
    """在服务端目录里找所有存档。返回 [{path, name, rel, size_mb, regions}]。"""
    out = []
    server_dir = os.path.abspath(server_dir)
    if not os.path.isdir(server_dir):
        return out
    for dirpath, dirnames, filenames in os.walk(server_dir):
        depth = dirpath[len(server_dir):].count(os.sep)
        if depth >= max_depth:
            dirnames[:] = []
            continue
        # 跳过备份目录自身
        parts = dirpath[len(server_dir):].split(os.sep)
        if BACKUP_DIRNAME in parts:
            dirnames[:] = []
            continue
        if "level.dat" in filenames:
            dirnames[:] = []
            base = os.path.basename(dirpath)
            # 跳过恢复时留下的旧存档（world_old_时间戳）
            if OLD_PREFIX.match(base):
                continue
            out.append(_world_stat(dirpath, server_dir))
    return out


def _dir_size(d, limit_mb=4096):
    total = 0
    try:
        for dp, dn, fn in os.walk(d):
            for f in fn:
                try:
                    total += os.path.getsize(os.path.join(dp, f))
                except Exception:
                    pass
            if total > limit_mb * 1048576:
                break
    except Exception:
        pass
    return total


def _count_region(d):
    for cand in (os.path.join(d, "region"),
                 os.path.join(d, "dimensions", "minecraft",
                              "overworld", "region")):
        if os.path.isdir(cand):
            try:
                return len([f for f in os.listdir(cand)
                            if f.endswith(".mca")])
            except Exception:
                pass
    return 0


def _world_stat(path, root):
    rel = os.path.relpath(path, root)
    return {
        "path": path,
        "rel": rel,
        "name": os.path.basename(path) or rel,
        "size_mb": round(_dir_size(path) / 1048576.0, 1),
        "regions": _count_region(path),
    }


def safe_stamp():
    return time.strftime("%Y%m%d_%H%M%S")


def _safe(s):
    """清洗成安全文件名。"""
    s = re.sub(r'[\\/:*?"<>|]+', "_", str(s)).strip()
    return s or "world"


def _cleanup_auto_backups(bdir, keep=5, on_log=None):
    """自动清理最旧的 auto 备份，防止挂机把硬盘塞满。"""
    log = on_log or (lambda m: None)
    try:
        # 只找带有 _auto 标识的
        names = sorted([n for n in os.listdir(bdir) if "_auto" in n])
    except Exception:
        return
    
    if len(names) <= keep:
        return
        
    to_delete = names[:-keep]
    log("[清理] 自动备份超过 %d 个，正在清理最旧的 %d 个..." % (keep, len(to_delete)))
    for n in to_delete:
        full = os.path.join(bdir, n)
        try:
            if os.path.isdir(full):
                shutil.rmtree(full)
            else:
                os.remove(full)
            log("  已删除旧自动备份: %s" % n)
        except Exception as e:
            log("  [跳过] 删不掉 %s: %r" % (n, e))

def backup_world(world_path, server_dir, note="", compress=False,
                 on_log=None):
    """备份一个存档。
    compress=False -> 直接复制文件夹（快，占空间）
    compress=True  -> 打成 zip（慢，省空间）
    """
    log = on_log or (lambda m: None)
    if not is_world_dir(world_path):
        raise ValueError("不是有效存档: %s" % world_path)

    bdir = os.path.join(server_dir, BACKUP_DIRNAME)
    os.makedirs(bdir, exist_ok=True)
    stamp = safe_stamp()
    wname = _safe(os.path.basename(world_path.rstrip("\\/")))
    tag = ("_" + _safe(note)) if note else ""
    base = "%s_%s%s" % (wname, stamp, tag)

    if compress:
        dest = os.path.join(bdir, base + ".zip")
        log("压缩备份 %s ..." % world_path)
        n = 0
        with zipfile.ZipFile(dest, "w", zipfile.ZIP_DEFLATED,
                             compresslevel=6) as z:
            for dp, dn, fn in os.walk(world_path):
                dn[:] = [x for x in dn
                         if x not in ("session.lock",)]
                for f in fn:
                    if f == "session.lock":
                        continue
                    full = os.path.join(dp, f)
                    try:
                        z.write(full, os.path.relpath(full, world_path))
                        n += 1
                    except Exception:
                        pass
        log("已写入 %d 个文件" % n)
        size = os.path.getsize(dest)
    else:
        dest = os.path.join(bdir, base)
        log("复制 %s ..." % world_path)
        shutil.copytree(world_path, dest,
                        ignore=shutil.ignore_patterns("session.lock"))
        size = _dir_size(dest)

    mb = round(size / 1048576.0, 1)
    log("[OK] 备份完成: %s (%.1f MB)" % (os.path.basename(dest), mb))
    
    # ★ 如果是自动备份，则触发清理，只保留最新 5 个
    if note == "auto":
        _cleanup_auto_backups(bdir, keep=5, on_log=log)
        
    return {"path": dest, "size_mb": mb, "stamp": stamp,
            "world": wname, "kind": "zip" if compress else "dir"}



def diagnose(server_dir):
    """诊断：备份目录在哪、里面有什么。用于排查"识别不到"。"""
    info = {
        "server_dir": server_dir,
        "dirs_found": [],
        "dirs_missing": [],
        "entries": [],
    }
    for cand in (os.path.join(server_dir, BACKUP_DIRNAME),
                 os.path.join(server_dir, "server", BACKUP_DIRNAME)):
        if os.path.isdir(cand):
            info["dirs_found"].append(cand)
        else:
            info["dirs_missing"].append(cand)
    for d in info["dirs_found"]:
        try:
            for n in sorted(os.listdir(d), reverse=True)[:20]:
                full = os.path.join(d, n)
                kind = ("zip" if n.endswith(".zip")
                        else "dir" if os.path.isdir(full) else "file")
                info["entries"].append({"name": n, "kind": kind})
        except Exception:
            pass
    return info


def backup_dirs(server_dir):
    """备份目录的所有可能位置。

    有些服务器的实际目录是 xxx/server/，而 entry 记的是 xxx，
    所以两处都要找。
    """
    cands = [os.path.join(server_dir, BACKUP_DIRNAME)]
    sub = os.path.join(server_dir, "server", BACKUP_DIRNAME)
    if os.path.abspath(sub) != os.path.abspath(cands[0]):
        cands.append(sub)
    return [d for d in cands if os.path.isdir(d)]


def list_backups(server_dir):
    """列出所有备份（文件夹和 zip 都认）。"""
    out = []
    bdirs = backup_dirs(server_dir)
    if not bdirs:
        return out
    for bdir in bdirs:
        out.extend(_list_one(bdir))
    # 按时间倒序
    out.sort(key=lambda b: b.get("path", ""), reverse=True)
    return out


def _list_one(bdir):
    """列出一个备份目录里的备份。

    原则：宁可多显示，也不漏。
    认不出来的（没找到 level.dat）也会列出来，
    标注为 "unknown"，让用户自己判断，
    而不是静默隐藏 —— 之前"识别不到"就是被静默过滤掉了。
    """
    out = []
    try:
        names = sorted(os.listdir(bdir), reverse=True)
    except Exception:
        return out
    for n in names:
        full = os.path.join(bdir, n)
        if n.startswith("."):
            continue
        confirmed = True
        if n.endswith(".zip"):
            kind = "zip"
            try:
                size = os.path.getsize(full)
            except Exception:
                continue
            # 看看里面有没有 level.dat（可能多一层）
            k, _pfx = zip_world_root(full)
            if k is None:
                confirmed = False
        elif os.path.isdir(full):
            kind = "dir"
            if not _looks_like_backup(full):
                confirmed = False
            size = _dir_size(full)
        else:
            # 其它文件一律忽略（比如 .log 之类）
            continue
        try:
            mtime = time.strftime(
                "%Y-%m-%d %H:%M:%S",
                time.localtime(os.path.getmtime(full)))
        except Exception:
            mtime = ""
        out.append({"name": n, "path": full, "kind": kind,
                    "size_mb": round(size / 1048576.0, 1),
                    "time": mtime,
                    "confirmed": confirmed,
                    "world": n.rsplit("_", 2)[0] if "_" in n else n})
    return out



def _looks_like_backup(d):
    """判断一个文件夹像不像备份（含 level.dat，可能嵌一层）。"""
    try:
        if os.path.isfile(os.path.join(d, "level.dat")):
            return True
        for n in sorted(os.listdir(d)):
            sub = os.path.join(d, n)
            if os.path.isdir(sub) and os.path.isfile(
                    os.path.join(sub, "level.dat")):
                return True
    except Exception:
        pass
    return False


def world_root_in(d):
    """找出文件夹备份里真正的世界根目录。

    兼容两种：
      A. 直接是 world 内容  -> level.dat 就在 d 下
      B. 多嵌了一层         -> d/world/level.dat

    返回 (kind, path)
      "dir"       直接就是根
      "dir_inner" 多了一层，path 是真正的根
      None        没找到
    """
    if os.path.isfile(os.path.join(d, "level.dat")):
        return ("dir", d)
    try:
        subs = [n for n in sorted(os.listdir(d))
                if os.path.isdir(os.path.join(d, n))]
        for n in subs:
            sub = os.path.join(d, n)
            if os.path.isfile(os.path.join(sub, "level.dat")):
                return ("dir_inner", sub)
    except Exception:
        pass
    return (None, d)


def zip_world_root(zp):
    """看 zip 里的世界根目录在哪一层。

    返回 (kind, prefix)
      "zip"       level.dat 在 zip 根
      "zip_inner" 多了一层目录，prefix 是要剥掉的目录名
      None        没找到
    """
    import zipfile as _zf
    try:
        with _zf.ZipFile(zp, "r") as z:
            names = z.namelist()
    except Exception:
        return (None, "")
    tops = set()
    for n in names:
        if not n:
            continue
        tops.add(n.split("/")[0] if "/" in n else n)
    if "level.dat" in names:
        return ("zip", "")
    for t in sorted(tops):
        if ("%s/level.dat" % t) in names:
            return ("zip_inner", t)
    return (None, "")


def _extract_zip_smart(zp, dest, log):
    """解压 zip 到 dest，自动剥掉多余的一层目录。

    兼容两种 zip：
      A. 直接压 world 内容  -> level.dat 在根
      B. 压了 world 文件夹   -> world/level.dat（要剥掉 world/）
    """
    import zipfile as _zf
    kind, prefix = zip_world_root(zp)
    if kind == "zip_inner":
        log("检测到压缩包里多了一层『%s/』，会自动剥掉" % prefix)
    cut = len(prefix) + 1 if prefix else 0
    os.makedirs(dest, exist_ok=True)
    n = 0
    with _zf.ZipFile(zp, "r") as z:
        for info in z.infolist():
            name = info.filename
            if cut:
                if not name.startswith(prefix + "/"):
                    continue
                name = name[cut:]
            if not name:
                continue
            if name.endswith("/"):
                os.makedirs(os.path.join(dest, name), exist_ok=True)
                continue
            if os.path.basename(name) == "session.lock":
                continue
            out = os.path.join(dest, name)
            os.makedirs(os.path.dirname(out), exist_ok=True)
            try:
                with z.open(info) as src, open(out, "wb") as dst:
                    shutil.copyfileobj(src, dst)
                n += 1
            except Exception:
                pass
    log("已解压 %d 个文件" % n)
    return n


def restore_backup(backup_path, target_world, on_log=None,
                   archive_old=True):
    """用备份替换当前存档。

    archive_old=True 时，被替换下来的旧存档会挪进 backups/，
    不再留在根目录。

    会先把当前存档挪走，成功后再归档；失败自动回滚。
    """
    log = on_log or (lambda m: None)
    if not os.path.exists(backup_path):
        raise ValueError("备份不存在: %s" % backup_path)
    if not os.path.isdir(target_world):
        raise ValueError("目标存档不存在: %s" % target_world)

    tmp = target_world + "_old_%s" % safe_stamp()
    log("先把当前存档挪到: %s" % os.path.basename(tmp))
    try:
        os.rename(target_world, tmp)
    except Exception as e:
        raise RuntimeError("无法重命名当前存档（可能文件正被服务器或杀毒软件占用）: %r" % e)

    try:
        if backup_path.endswith(".zip"):
            log("解压 %s ..." % os.path.basename(backup_path))
            _extract_zip_smart(backup_path, target_world, log)
        else:
            # 文件夹备份：可能直接是根，也可能嵌了一层
            kind, root = world_root_in(backup_path)
            if kind == "dir_inner":
                log("检测到备份里多了一层『%s/』，会自动剥掉"
                    % os.path.basename(root))
                src = root
            elif kind == "dir":
                src = backup_path
            else:
                log("[警告] 备份里没找到 level.dat，原样复制")
                src = backup_path
            log("复制 %s ..." % os.path.basename(backup_path))
            
            # ★ 修复 FileNotFoundError：确保父目录存在，且用 dirs_exist_ok
            try:
                os.makedirs(os.path.dirname(os.path.abspath(target_world)), exist_ok=True)
                shutil.copytree(src, target_world, dirs_exist_ok=True,
                                ignore=shutil.ignore_patterns("session.lock"))
            except Exception as e:
                raise RuntimeError("复制存档时出错（可能是文件占用或路径过长）: %r" % e)
        # 校验一下
        if not os.path.isfile(os.path.join(target_world, "level.dat")):
            raise RuntimeError(
                "恢复后 %s 里没有 level.dat，可能备份有问题"
                % os.path.basename(target_world))
    except Exception as e:
        log("恢复失败，正在回滚 ...")
        if os.path.isdir(target_world):
            shutil.rmtree(target_world, ignore_errors=True)
        os.rename(tmp, target_world)
        raise RuntimeError("恢复失败已回滚: %r" % e)

    # 把旧存档挪进 backups/，不留在根目录
    old_final = tmp
    if archive_old:
        bn = os.path.basename(target_world.rstrip("\\/")) or "world"
        server_dir = os.path.dirname(
            os.path.abspath(target_world.rstrip("\\/")))
        try:
            dest_dir = os.path.join(server_dir, BACKUP_DIRNAME)
            os.makedirs(dest_dir, exist_ok=True)
            dst = os.path.join(dest_dir, os.path.basename(tmp))
            # 保险：目标已存在就加后缀
            if os.path.exists(dst):
                dst = dst + "_2"
            shutil.move(tmp, dst)
            old_final = dst
            log("[OK] 已恢复。旧存档已归档到 backups/: %s"
                % os.path.basename(dst))
        except Exception as e:
            # 兜底：先试复制到 backups，再删原处
            try:
                dst2 = os.path.join(dest_dir, os.path.basename(tmp))
                if os.path.exists(dst2):
                    dst2 = dst2 + "_2"
                shutil.copytree(tmp, dst2,
                                ignore=shutil.ignore_patterns(
                                    "session.lock"))
                shutil.rmtree(tmp, ignore_errors=True)
                old_final = dst2
                log("[OK] 旧存档已（复制方式）归档到 backups/: %s"
                    % os.path.basename(dst2))
            except Exception as e2:
                old_final = tmp
                log("[警告] 旧存档没能挪进 backups/，留在原处: %s"
                    % os.path.basename(tmp))
                log("       原因: %r / %r" % (e, e2))
                log("       你可以手动把它拖进 backups/ 文件夹")
    else:
        log("[OK] 已恢复。旧存档保留在: %s" % os.path.basename(tmp))

    log("      确认没问题后可在备份列表里删掉它。")
    return {"restored": target_world, "old": old_final}


def _long(p):
    """Windows 长路径前缀。

    MC 存档嵌套深（dimensions/minecraft/the_end/region/...），
    很容易超过 Windows 的 260 字符上限，那样连删都删不掉。
    """
    if os.name == "nt":
        p = os.path.abspath(p)
        if not p.startswith("\\\\?\\"):
            return "\\\\?\\" + p
    return p


def _force_unlink(fp):
    """删一个文件，失败先修权限再试（Windows 上只读文件删不掉）。"""
    try:
        os.chmod(os.path.dirname(fp) or ".", 0o755)
    except Exception:
        pass
    try:
        os.chmod(fp, 0o755)
    except Exception:
        pass
    try:
        os.unlink(fp)
        return
    except Exception:
        pass
    # 长路径再试一次
    try:
        os.unlink(_long(fp))
    except Exception:
        raise


def delete_backup(path, on_log=None):
    """删除一个备份，并如实汇报结果。

    ★ 以前是 shutil.rmtree(path, ignore_errors=True)。
      ignore_errors 会把所有失败静默吞掉：
      删到一半卡住（文件被占用 / 只读 / 路径过长）也照样"成功"返回，
      UI 什么都不说，于是你看到"删完还剩下一个 1MB 的文件夹"。

    现在尽力删（正常删 → 逐个修权限删 → 长路径重试），
    并返回 {gone, ok, deleted, failed, failed_paths}，
    删不干净一定让界面知道。
    """
    log = on_log or (lambda m: None)
    st = {"gone": False, "ok": False, "deleted": 0, "failed": 0,
          "failed_paths": [], "left_mb": 0.0}
    if not path:
        raise ValueError("没指定备份")

    # ---------- zip ----------
    if str(path).endswith(".zip"):
        if not os.path.exists(path):
            st.update(gone=True, ok=True)
            return st
        try:
            _force_unlink(path)
            st.update(deleted=1, gone=True, ok=True)
        except Exception as e:
            st.update(failed=1, gone=os.path.exists(path))
            st["failed_paths"].append((path, repr(e)))
        return _finish_delete(st, path, log)

    # ---------- 目录 ----------
    if not os.path.exists(path):
        st.update(gone=True, ok=True)
        return st
    if not os.path.isdir(path):
        try:
            _force_unlink(path)
            st.update(deleted=1, gone=True, ok=True)
        except Exception as e:
            st.update(failed=1, gone=True, ok=False)
            st["failed_paths"].append((path, repr(e)))
        return _finish_delete(st, path, log)

    # 第一遍：整棵树一次删
    try:
        shutil.rmtree(path)
    except Exception:
        pass
    if not os.path.exists(path):
        st.update(gone=True, ok=True)
        return _finish_delete(st, path, log)

    # 第二遍：逐个来，能救一个是一个
    #   常见卡住的原因：
    #     1) 只读（从压缩包 / git 复制出来的常是只读）
    #     2) 被占用（服务器还在跑，session.lock 之类）
    #     3) 路径太长（见 _long）
    for dp, dn, fn in os.walk(path, topdown=False):
        for f in fn:
            fp = os.path.join(dp, f)
            if not os.path.lexists(fp):
                continue
            try:
                _force_unlink(fp)
                st["deleted"] += 1
            except Exception as e:
                st["failed"] += 1
                if len(st["failed_paths"]) < 20:
                    st["failed_paths"].append((fp, repr(e)))
        for d in dn:
            dd = os.path.join(dp, d)
            if not os.path.lexists(dd):
                continue
            try:
                os.chmod(dd, 0o755)
                os.rmdir(dd)
                st["deleted"] += 1
            except Exception as e:
                st["failed"] += 1
                if len(st["failed_paths"]) < 20:
                    st["failed_paths"].append((dd, repr(e)))
    try:
        os.chmod(path, 0o755)
        os.rmdir(path)
        st["deleted"] += 1
    except Exception as e:
        st["failed"] += 1
        st["failed_paths"].append((path, repr(e)))

    # 第三遍：Windows 长路径兜底
    if os.name == "nt" and os.path.exists(path):
        try:
            shutil.rmtree(_long(path))
            st["deleted"] += 1
        except Exception:
            pass

    st["gone"] = not os.path.exists(path)
    st["ok"] = st["gone"] and st["failed"] == 0
    return _finish_delete(st, path, log)


def _finish_delete(st, path, log):
    if st["gone"]:
        log("[OK] 已删除: %s" % os.path.basename(str(path)))
        if st["failed"]:
            log("      过程中有 %d 项报错，但最终删干净了"
                % st["failed"])
    else:
        st["left_mb"] = round(_dir_size(path) / 1048576.0, 1)
        log("[警告] 没删干净，还剩 %.1f MB" % st["left_mb"])
        for fp, err in st["failed_paths"][:3]:
            try:
                rel = os.path.relpath(fp, path)
            except Exception:
                rel = fp
            log("      删不掉: %s  (%s)" % (rel, err))
        if st["failed"] > 3:
            log("      ...还有 %d 项" % (st["failed"] - 3))
    return st
