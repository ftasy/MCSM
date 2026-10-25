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
        roots.append("C:\\")
        vendors = ["Java", "Eclipse Adoptium", "Microsoft", "BellSoft",
                   "Zulu", "azul", "GraalVM", "OpenJDK", "jdk",
                   "AdoptOpenJDK", "Temurin", "Semeru", "Liberica"]
        for r in roots:
            for v in vendors:
                base = os.path.join(r, v)
                if os.path.isdir(base):
                    dirs.append(base)
                    for sub in sorted(glob.glob(os.path.join(base, "*"))):
                        if os.path.isdir(sub):
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
                           timeout=15)
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
        r = subprocess.run([which, "java"], capture_output=True, timeout=10)
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
