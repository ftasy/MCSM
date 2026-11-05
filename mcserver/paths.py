# -*- coding: utf-8 -*-
"""统一的路径解析 —— 换一台电脑也必须能找到自己的数据。

★ V2.4.0 为什么要有这个模块：

以前每个模块各自用 ``__file__`` 往上数几层来定位配置文件，
而且**数的层数各不相同**：

    · app_library._data_path   → 上 1 层
    · sources.cfg_path         → 上 3 层
    · theme.path               → 上 3 层
    · install._icon_map_path   → 上 2 层
    · ai_diag.cfg_path         → 上 1 层（还多拼了个 ..）

源码运行时这些路径还能凑合（都落在仓库里），但打包成 EXE 之后
``__file__`` 指向的是 ``_internal/``：

    · 所有配置全部落进 _internal/（用户根本看不到、也不会备份）
    · 用户把软件装到 Program Files / 只读 U 盘 → 写不进去，静默丢配置
    · 将来若改 --onefile，_internal 是每次运行解压的临时目录
      → 服务器列表、主题、镜像源**每次启动清零**

更直接的表现是"换台电脑 / 换个目录就全丢"。所以统一到这里：

    · 打包（frozen）→ EXE 所在目录
    · 源码运行     → 项目根目录（mcserver 的上一级）
    · 该目录不可写 → 回落 %LOCALAPPDATA%\\MCSM

老版本写在旧位置的文件会在第一次读取时自动迁移过来，不丢数据。
"""

import os
import shutil
import sys


def app_dir():
    """软件基目录（配置 / 日志都放这里）。

    ★ 不要用 os.getcwd()：从快捷方式、计划任务、别的目录启动时，
      CWD 未必是软件目录，配置就会散落到随机位置。
    """
    if getattr(sys, "frozen", False):
        return os.path.dirname(os.path.abspath(sys.executable))
    # 源码运行：mcserver/ 的上一级
    return os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


def user_dir():
    """兜底目录：软件目录不可写时（Program Files / 只读盘）用它。"""
    base = (os.environ.get("LOCALAPPDATA")
            or os.environ.get("APPDATA")
            or os.path.expanduser("~"))
    return os.path.join(base, "MCSM")


_WRITABLE = {}


def _writable(d):
    """目录能不能真的写文件（只判断 isdir 不够，只读目录照样能 isdir）。"""
    if d in _WRITABLE:
        return _WRITABLE[d]
    ok = False
    try:
        os.makedirs(d, exist_ok=True)
        probe = os.path.join(d, ".mcsm_write_probe")
        with open(probe, "w", encoding="utf-8") as f:
            f.write("ok")
        os.remove(probe)
        ok = True
    except Exception:
        ok = False
    _WRITABLE[d] = ok
    return ok


def data_dir():
    """实际使用的数据目录（可写，带兜底）。"""
    d = app_dir()
    if _writable(d):
        return d
    u = user_dir()
    try:
        os.makedirs(u, exist_ok=True)
        return u
    except Exception:
        return d


def _package_dir():
    return os.path.dirname(os.path.abspath(__file__))


def legacy_locations(name):
    """老版本可能写过的位置（按优先级），用于自动迁移。

    覆盖：项目根 / mcserver/ / _internal/ / _internal/mcserver/ / 当前目录。
    宁可多列几个，也不要让用户的服务器列表凭空消失。
    """
    pkg = _package_dir()
    root = os.path.dirname(pkg)
    try:
        cwd = os.getcwd()
    except Exception:
        cwd = ""
    bases = [root, pkg, os.path.join(pkg, "core"),
             os.path.join(cwd, "_internal"),
             os.path.join(cwd, "_internal", "mcserver") if cwd else "",
             cwd]
    out = []
    for b in bases:
        if not b:
            continue
        p = os.path.join(b, name)
        if p not in out:
            out.append(p)
    return out


def data_file(name, migrate=True):
    """数据文件全路径。

    目标不存在、而老位置有 → 自动复制过来（只复制，不删原件，
    万一判断错了用户还能自己找回去）。
    """
    target = os.path.join(data_dir(), name)
    if os.path.exists(target) or not migrate:
        return target
    for old in legacy_locations(name):
        if os.path.abspath(old) == os.path.abspath(target):
            continue
        if not os.path.isfile(old):
            continue
        try:
            os.makedirs(os.path.dirname(target), exist_ok=True)
            shutil.copy2(old, target)
            return target
        except Exception:
            continue
    return target


def log_file():
    """manager.log 的位置（软件目录，不可写则回落用户目录）。"""
    p = os.path.join(data_dir(), "manager.log")
    return p
