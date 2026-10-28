# -*- coding: utf-8 -*-
"""V0.5.0：服务器实时状态探测（socket ping + mcstatus）。

live_status(port) 会做两件事：
  1. socket 连 127.0.0.1:port，测通不通 + 连接耗时（延迟）；
  2. 通了且装了 mcstatus 库，再拿在线人数 / 最大人数 / MOTD。

返回 dict：{online, latency_ms, players, motd, note}
  - online=False  -> 没连上（服务没开或端口没监听）
  - online=True   -> 端口通；players=(当前, 最大) 可能为 None
                      （mcstatus 未安装或 status 协议失败）
  - note          用于提示"装了 mcstatus 才有在线人数"。
"""

import socket
import time


def mcstatus_available():
    try:
        import mcstatus  # noqa: F401
        return True
    except Exception:
        return False


def live_status(port, timeout=3):
    """探测本机 MC 服状态。耗时网络操作，调用方放 Worker 线程。"""
    # 1) socket 连通性 + 延迟（连接耗时即延迟）
    # ★ V2.0.1.1：服务器可能只监听 IPv6（::），先试 127.0.0.1 再试 ::1，
    #   否则监听 :: 的服永远报"无响应"。
    t0 = time.time()
    online = False
    for host in ("127.0.0.1", "::1"):
        try:
            s = socket.create_connection((host, int(port)),
                                         timeout=timeout)
            s.close()
            online = True
            break
        except Exception:
            continue
    latency = int((time.time() - t0) * 1000) if online else None
    if not online:
        return {"online": False, "latency_ms": None,
                "players": None, "motd": None, "note": None}

    # 2) mcstatus 拿在线人数 / MOTD（库没装就只报连通）
    if not mcstatus_available():
        return {"online": True, "latency_ms": latency,
                "players": None, "motd": None,
                "note": "未安装 mcstatus（pip install mcstatus）"}
    try:
        from mcstatus import JavaServer
        try:
            st = JavaServer("127.0.0.1", int(port), timeout=timeout).status()
        except Exception:
            st = JavaServer("::1", int(port), timeout=timeout).status()
        raw = getattr(st, "_raw", None) or st
        pl = getattr(raw, "players", None) or {}
        cur = getattr(pl, "online", None) or pl.get("online")
        mx = getattr(pl, "max", None) or pl.get("max")
        # 有些版本人数在 st.players（响应对象直接挂）
        if cur is None:
            pp = getattr(st, "players", None)
            if pp is not None:
                cur = getattr(pp, "online", None) or pp.get("online")
                mx = getattr(pp, "max", None) or pp.get("max")
        motd = getattr(st, "description", None)
        if hasattr(motd, "to_plain"):
            motd = motd.to_plain()
        else:
            motd = str(motd or "")
        motd = (motd or "").strip()[:80] or None
        return {"online": True, "latency_ms": latency,
                "players": (cur, mx) if cur is not None else None,
                "motd": motd, "note": None}
    except Exception:
        # 端口通但 status 协议失败（刚启动 / 非 Java 协议）
        return {"online": True, "latency_ms": latency,
                "players": None, "motd": None, "note": None}


def ping_text(res):
    """把 live_status 结果格式化成界面一行字：
    "在线: 3/20 | 延迟: 2ms"  /  "在线 | 延迟: 2ms"。
    """
    if not res or not res.get("online"):
        return None
    bits = []
    pl = res.get("players")
    if pl and pl[0] is not None:
        bits.append("在线: %s/%s" % (pl[0], pl[1] if pl[1] is not None else "?"))
    else:
        bits.append("在线")
    lat = res.get("latency_ms")
    bits.append("延迟: %sms" % (lat if lat is not None else "-"))
    return " | ".join(bits)


# ---- ★ V2.0.1：进程级 CPU / 内存（ctypes，无 psutil 依赖） ----
import ctypes
from ctypes import wintypes

_cpu_snap = {}  # pid -> (采样时间, 累计 CPU 秒)


def _open_proc(pid):
    """以最低权限打开进程句柄，失败返回 None（进程已退出/权限不足）。"""
    PROCESS_QUERY_LIMITED_INFORMATION = 0x1000
    try:
        h = ctypes.windll.kernel32.OpenProcess(
            PROCESS_QUERY_LIMITED_INFORMATION, False, int(pid))
        return h if h else None
    except Exception:
        return None


def _proc_cpu_sec(pid):
    """进程累计 CPU 秒（kernel+user），失败返回 None。"""
    h = _open_proc(pid)
    if not h:
        return None
    try:
        class _FT8(ctypes.Structure):
            _fields_ = [("a", wintypes.FILETIME),
                        ("b", wintypes.FILETIME),
                        ("c", wintypes.FILETIME),
                        ("d", wintypes.FILETIME)]
        ft = _FT8()
        if not ctypes.windll.kernel32.GetProcessTimes(
                h, ctypes.byref(ft.a), ctypes.byref(ft.b),
                ctypes.byref(ft.c), ctypes.byref(ft.d)):
            return None

        def _sec(f):
            return ((f.dwHighDateTime << 32) | f.dwLowDateTime) / 1e7
        return _sec(ft.c) + _sec(ft.d)  # kernel + user
    except Exception:
        return None
    finally:
        try:
            ctypes.windll.kernel32.CloseHandle(h)
        except Exception:
            pass


def _proc_mem_mb(pid):
    """进程工作集内存 MB，失败返回 None。"""
    h = _open_proc(pid)
    if not h:
        return None
    try:
        class _PMC(ctypes.Structure):
            _fields_ = [
                ("cb", wintypes.DWORD),
                ("PageFaultCount", wintypes.DWORD),
                ("PeakWorkingSetSize", ctypes.c_size_t),
                ("WorkingSetSize", ctypes.c_size_t),
                ("QuotaPeakPagedPoolUsage", ctypes.c_size_t),
                ("QuotaPagedPoolUsage", ctypes.c_size_t),
                ("QuotaPeakNonPagedPoolUsage", ctypes.c_size_t),
                ("QuotaNonPagedPoolUsage", ctypes.c_size_t),
                ("PagefileUsage", ctypes.c_size_t),
                ("PeakPagefileUsage", ctypes.c_size_t),
            ]
        pmc = _PMC()
        pmc.cb = ctypes.sizeof(_PMC)
        if not ctypes.windll.psapi.GetProcessMemoryInfo(
                h, ctypes.byref(pmc), pmc.cb):
            return None
        return pmc.WorkingSetSize / (1024.0 * 1024.0)
    except Exception:
        return None
    finally:
        try:
            ctypes.windll.kernel32.CloseHandle(h)
        except Exception:
            pass


def proc_stats(pid):
    """取 java 进程 CPU 占用 % 与内存 MB。

    CPU% 需要两次采样差值（间隔 >0.2s），首次调用返回 None；
    进程已退出时清掉快照并返回 None。
    """
    try:
        pid = int(pid)
    except Exception:
        return {"cpu_pct": None, "mem_mb": None}
    if pid <= 0:
        return {"cpu_pct": None, "mem_mb": None}
    t = time.time()
    ct = _proc_cpu_sec(pid)
    mem = _proc_mem_mb(pid)
    if ct is None:
        _cpu_snap.pop(pid, None)
        return {"cpu_pct": None, "mem_mb": mem}
    cpu = None
    prev = _cpu_snap.get(pid)
    if prev:
        dt = t - prev[0]
        dc = ct - prev[1]
        if dt > 0.2 and dc >= 0:
            cpu = max(0.0, min(99.9, dc / dt * 100.0))
    _cpu_snap[pid] = (t, ct)
    return {"cpu_pct": cpu, "mem_mb": mem}


def monitor_text(res):
    """状态 + CPU/内存 拼成窗口标题用的一行：
    "在线: 3/20 | 延迟: 2ms | CPU 12% | 内存 1560 MB"。
    """
    base = ping_text(res)
    if not base:
        return None
    bits = [base]
    cpu = res.get("cpu_pct")
    if cpu is not None:
        bits.append("CPU %.0f%%" % cpu)
    mem = res.get("mem_mb")
    if mem is not None:
        bits.append("内存 %.0f MB" % mem)
    return " | ".join(bits)
