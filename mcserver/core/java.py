# -*- coding: utf-8 -*-
"""Java 环境探测。"""

import os
import sys
import glob
import subprocess

from . import versions


def _candidate_dirs():
    dirs = []
    if sys.platform == "win32":
        roots = []
        for env in ("ProgramFiles", "ProgramFiles(x86)", "LOCALAPPDATA"):
            v = os.environ.get(env)
            if v:
                roots.append(v)
        # ★ V1.9.8：扫描所有盘符根目录 —— 很多用户把 JDK 装在
        #   D:\JDK、E:\jdk 这类自定义位置，原来只扫 C 盘 + ProgramFiles
        #   会漏掉，导致"没找到 Java"（点启动后控制台无输出、启动中止）。
        for _c in "ABCDEFGHIJKLMNOPQRSTUVWXYZ":
            d = _c + ":\\"
            if os.path.isdir(d):
                roots.append(d)
        vendors = ["Java", "Eclipse Adoptium", "Microsoft", "BellSoft",
                   "Zulu", "azul", "GraalVM", "OpenJDK", "jdk",
                   "AdoptOpenJDK", "Temurin", "Semeru", "Liberica",
                   "JDK", "JDKs", "JAVA", "JavaSoft", "java", "jdk-*"]
        for r in roots:
            for v in vendors:
                base = os.path.join(r, v)
                if os.path.isdir(base):
                    dirs.append(base)
                    for sub in sorted(glob.glob(os.path.join(base, "*"))):
                        if os.path.isdir(sub):
                            dirs.append(sub)
            # ★ V1.9.8：盘根下形如 jdk-21 / jdk25 的目录（不在固定 vendor 名）
            for sub in sorted(glob.glob(os.path.join(r, "*"))):
                low = os.path.basename(sub).lower()
                if os.path.isdir(sub) and ("jdk" in low or "java" in low):
                    dirs.append(sub)
    else:
        dirs += ["/usr/lib/jvm", "/opt/java", "/Library/Java/JavaVirtualMachines"]
    return dirs


def _java_exe(name):
    return name + (".exe" if sys.platform == "win32" else "")


def probe(path):
    """探测一个 java 可执行文件，返回 (major, version_string) 或 None。"""
    if not path or not os.path.isfile(path):
        return None
    try:
        r = subprocess.run([path, "-version"], capture_output=True,
                           timeout=15,
                           # ★ V1.6.2：禁止 java -version 闪黑框
                           creationflags=getattr(subprocess,
                                                 "CREATE_NO_WINDOW", 0))
        out = (r.stdout or b"").decode("utf-8", "replace") + (
              r.stderr or b"").decode("utf-8", "replace")
    except Exception:
        return None
    major = versions.parse_java_major(out)
    if major is None:
        return None
    first = out.strip().splitlines()[0] if out.strip() else ""
    return (major, first[:80])


def find_all():
    """扫描系统里所有 Java，返回 [{path, major, version}]。"""
    found = {}
    seen = set()

    def add(p):
        if not p:
            return
        p = os.path.abspath(p)
        if p in seen:
            return
        seen.add(p)
        info = probe(p)
        if info:
            found[p] = {"path": p, "major": info[0], "version": info[1]}

    # 1) PATH 里的 java
    try:
        which = "where" if sys.platform == "win32" else "which"
        r = subprocess.run([which, "java"], capture_output=True, timeout=10,
                           # ★ V1.6.2：禁止 where/which 闪黑框
                           creationflags=getattr(subprocess,
                                                 "CREATE_NO_WINDOW", 0))
        for line in (r.stdout or b"").decode("utf-8", "replace").splitlines():
            line = line.strip()
            if line and os.path.isfile(line):
                add(line)
    except Exception:
        pass

    # 2) JAVA_HOME
    jh = os.environ.get("JAVA_HOME")
    if jh:
        add(os.path.join(jh, "bin", _java_exe("java")))

    # 3) 常见安装目录
    for d in _candidate_dirs():
        for rel in (os.path.join("bin", _java_exe("java")),
                    _java_exe("java")):
            try:
                for hit in sorted(glob.glob(os.path.join(d, rel))):
                    add(hit)
            except Exception:
                pass
        # 有些目录结构是 vendor/jdk-21/bin/java
        try:
            for sub in sorted(glob.glob(os.path.join(d, "*"))):
                if os.path.isdir(sub):
                    p = os.path.join(sub, "bin", _java_exe("java"))
                    if os.path.isfile(p):
                        add(p)
        except Exception:
            pass

    return sorted(found.values(), key=lambda x: (-x["major"], x["path"]))


def pick_for(need, javas=None, mc_version=None):
    """从已探测的 Java 里挑一个 >= need 的，优先最接近的。

    mc_version 给了的话，还会避开"太高"的
    （1.19.3 需要 17 但最高只建议 17，那就别选 25）。
    """
    if javas is None:
        javas = find_all()
    if not javas:
        return None

    mx = None
    if mc_version:
        try:
            from . import versions
            mx = versions.java_max_ok(mc_version)
        except Exception:
            mx = None

    if not need:
        return javas[0]

    def score(j):
        m = j["major"]
        if m < need:
            return 10000 + (need - m)
        if mx and m > mx:
            # 超过建议上限：能用但危险，排在后面
            return 500 + (m - mx)
        return abs(m - need)

    return sorted(javas, key=score)[0]


def validate(path, need, mc_version=None):
    """校验某个 java 路径是否满足需求。返回 (ok, msg)。

    mc_version 给了的话，还会检查"是不是太高"——
    老版本 MC 用太新的 Java 会起不来
    （Forge 1.16.5 + Java 17 会炸，1.19.x + Java 21/25 也容易出问题）。
    """
    info = probe(path)
    if info is None:
        return False, "无法执行，或不是有效的 Java"
    major = info[0]
    if need and major < need:
        return False, "Java %d 太低，需要 %d" % (major, need)

    if mc_version:
        try:
            from . import versions
            mx = versions.java_max_ok(mc_version)
        except Exception:
            mx = None
        if mx and major > mx:
            return True, ("Java %d —— 偏高。%s 最高建议 Java %d，"
                          "用更高的可能起不来（尤其 Forge）。\n"
                          "     建议装个 Java %d 再选它。"
                          % (major, mc_version, mx, mx))
    return True, "Java %d" % major


# =========================================================
#  V0.4.0：内置 Java 自动下载（Adoptium Temurin JRE）
# =========================================================

def app_base_dir():
    """软件根目录：EXE 版 = exe 所在目录；源码版 = 项目根。"""
    if getattr(sys, "frozen", False):
        return os.path.dirname(sys.executable)
    return os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


def java_home_dir():
    """内置 Java 存放目录：<软件根>/java。"""
    return os.path.join(app_base_dir(), "java")


def adoptium_jre(major, on_log=None):
    """用 Adoptium API 查某个大版本的 JRE 下载直链。

    返回 {"url": zip 直链, "name": 解压目录名}。
    失败抛 InstallError。
    """
    from . import install as _inst
    api = ("https://api.adoptium.net/v3/assets/latest/%d/hotspot"
           "?os=windows&arch=x64&image_type=jre" % major)
    d = _inst._get_json(api, timeout=30)
    # ★ API 返回的是 assets 数组（不是 {"assets": [...]} 对象）
    if isinstance(d, list):
        assets = d
    elif isinstance(d, dict):
        assets = (d or {}).get("assets") or []
    else:
        assets = []
    if not assets:
        raise _inst.InstallError("Adoptium 没有返回可用资产")
    b = assets[0].get("binary") or {}
    p = b.get("package") or {}
    link = p.get("link")
    name = p.get("name") or ("jdk-%d" % major)
    if not link:
        raise _inst.InstallError("Adoptium 没给下载链接")
    return {"url": link, "name": name}


def download_jre(major, on_log=None):
    """下载并解压 JRE 到 <软件根>/java/。

    返回解压后的 javaw.exe（没有就用 java.exe）绝对路径。
    这是耗时网络操作，调用方必须放进 Worker 线程。
    """
    import zipfile
    import shutil
    from . import install as _inst

    def log(m):
        if on_log:
            on_log(m)

    info = adoptium_jre(major, on_log=on_log)
    base = java_home_dir()
    os.makedirs(base, exist_ok=True)

    zip_path = os.path.join(base, info["name"] + ".zip")
    log("下载 JRE %d（%s）..." % (major, info["name"]))
    _inst._download(info["url"], zip_path, timeout=900, on_log=on_log)

    tmp = os.path.join(base, "_tmp_" + info["name"])
    if os.path.isdir(tmp):
        shutil.rmtree(tmp, ignore_errors=True)
    os.makedirs(tmp, exist_ok=True)
    log("解压中 ...")
    with zipfile.ZipFile(zip_path) as z:
        z.extractall(tmp)
    os.remove(zip_path)

    # 解压产物一般是 <tmp>/jdk-xx.y.z+7/，在它的 bin/ 里找 javaw
    exe = None
    for root, _dirs, _files in os.walk(tmp):
        for name in ("javaw.exe", "java.exe"):
            p = os.path.join(root, "bin", name)
            if os.path.isfile(p):
                exe = p
                break
        if exe:
            break
    if not exe:
        shutil.rmtree(tmp, ignore_errors=True)
        raise _inst.InstallError("解压后没找到 javaw.exe，请手动下载 Java")

    # 挪到正式目录（幂等：已存在先删旧的）
    dest = os.path.join(base, info["name"])
    if os.path.isdir(dest):
        shutil.rmtree(dest, ignore_errors=True)
    shutil.move(tmp, dest)
    # 解压产物可能多套一层目录（<dest>/jdk-xx/bin/...），递归找
    for root, _dirs, _files in os.walk(dest):
        for name in ("javaw.exe", "java.exe"):
            p = os.path.join(root, "bin", name)
            if os.path.isfile(p):
                return p
    raise _inst.InstallError("Java 已下载但 bin 下没有可执行文件")
