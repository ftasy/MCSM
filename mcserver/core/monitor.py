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
    t0 = time.time()
    online = False
    try:
        s = socket.create_connection(("127.0.0.1", int(port)),
                                     timeout=timeout)
        s.close()
        online = True
    except Exception:
        pass
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
        st = JavaServer("127.0.0.1", int(port), timeout=timeout).status()
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
