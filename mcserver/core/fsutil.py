# -*- coding: utf-8 -*-
"""文件写入工具：原子替换 + 自动快照。

为什么需要这个模块
------------------
`open(path, "w")` 会**立刻把原文件截断成 0 字节**，然后才开始写。
只要中间出任何事 —— 断电、任务管理器强杀、磁盘写满、json.dump 抛异常
—— 原文件就已经没了：

  · servers.json      几十个服务器的列表、排序、图标、最近开服时间，一次清空
  · server.properties 变成空文件 → 服务器起不来，且读回来也是空的
  · 各种 YAML 配置     同上

这里统一走「写同目录临时文件 → fsync → os.replace」：

  os.replace 在同一分区上是**原子操作**，结果是「要么旧的、要么新的」，
  不存在写到一半的中间状态。断电最多丢掉这一次修改，绝不会毁掉原文件。

`keep_backup=True` 时，替换前会先把旧内容轮转成 `<file>.bak`，
再之前的两份顺延成 `.bak.2` / `.bak.3`，手滑改错能捞回来。
"""

import json
import os
import shutil
import threading

__all__ = [
    "atomic_write_text", "atomic_write_bytes", "atomic_write_json",
    "snapshot", "read_text_fallback", "read_json_fallback",
    "tmp_path_for",
]

# 快照保留份数：.bak / .bak.2 / .bak.3
DEFAULT_KEEP = 3


def tmp_path_for(path):
    """返回 path 对应的临时文件路径（同目录，保证 os.replace 不跨分区）。

    带上 进程号 + 线程号：两个线程同时写同一个文件时不会抢同一个临时名
    （固定叫 `path.tmp` 的话，后一个 open 会直接 PermissionError）。
    同一线程连续写只会复用这一个临时名，所以不会堆积垃圾文件。
    """
    return "%s.%d-%d.tmp" % (path, os.getpid(), threading.get_ident())


def _translate_newline(text, newline):
    """按 open() 的 newline 语义做换行转换。

    None → 转成系统换行（Windows 上是 \\r\\n）
    ""   → 原样不动
    指定 → 转成指定串
    """
    if newline == "":
        return text
    target = os.linesep if newline is None else newline
    return text.replace("\n", target)


def _replace_with_retry(tmp, path):
    """os.replace 的兜底。

    Windows 上替换失败有两类常见原因：
      1. 目标带只读属性（zip 解压出来常有）→ PermissionError
      2. 目标此刻正被别人打开着读（另一个线程/杀毒软件扫描）→ PermissionError
    第 2 类通常几十毫秒就释放，所以稍微等一下重试几次，
    总等待上限约 100ms，不会卡住界面。
    """
    import time

    last = None
    for i in range(6):
        try:
            os.replace(tmp, path)
            return
        except OSError as e:
            last = e
            if i == 2 and os.path.exists(path):
                try:
                    os.chmod(path, 0o666)
                except OSError:
                    pass
            if i < 5:
                time.sleep(0.015 * (i + 1))
    raise last


def atomic_write_bytes(path, data, keep_backup=False, keep=DEFAULT_KEEP):
    """原子写入二进制内容。返回实际路径。"""
    path = os.path.abspath(path)
    d = os.path.dirname(path)
    if d:
        os.makedirs(d, exist_ok=True)
    if keep_backup and os.path.isfile(path):
        try:
            snapshot(path, keep=keep)
        except Exception:
            # 快照失败不该阻止正常保存 —— 它只是保险
            pass

    tmp = tmp_path_for(path)
    try:
        with open(tmp, "wb") as f:
            f.write(data)
            f.flush()
            os.fsync(f.fileno())
        _replace_with_retry(tmp, path)
    except BaseException:
        try:
            os.unlink(tmp)
        except OSError:
            pass
        raise
    return path


def atomic_write_text(path, text, encoding="utf-8", newline=None,
                      keep_backup=False, keep=DEFAULT_KEEP):
    """原子写入文本。newline 语义与内置 open() 一致。"""
    if text is None:
        text = ""
    if isinstance(text, bytes):
        return atomic_write_bytes(path, text, keep_backup=keep_backup,
                                  keep=keep)
    payload = _translate_newline(text, newline).encode(encoding, "replace")
    return atomic_write_bytes(path, payload, keep_backup=keep_backup, keep=keep)


def atomic_write_json(path, obj, indent=2, ensure_ascii=False,
                      keep_backup=False, keep=DEFAULT_KEEP):
    """原子写入 JSON（不带换行转换，跟原来 json.dump 的行为一致）。"""
    text = json.dumps(obj, ensure_ascii=ensure_ascii, indent=indent)
    return atomic_write_text(path, text, encoding="utf-8", newline="",
                             keep_backup=keep_backup, keep=keep)


def snapshot(path, keep=DEFAULT_KEEP, suffix=".bak"):
    """把现有文件轮转成 .bak，老快照顺延。返回 .bak 路径（没有源文件则 None）。

    path.bak.2 → path.bak.3
    path.bak   → path.bak.2
    path       → path.bak
    """
    if not os.path.isfile(path):
        return None
    base = path + suffix
    for i in range(keep - 1, 0, -1):
        src = base if i == 1 else "%s.%d" % (base, i)
        dst = "%s.%d" % (base, i + 1)
        if os.path.isfile(src):
            try:
                os.replace(src, dst)
            except OSError:
                pass
    tmp = tmp_path_for(base)
    try:
        shutil.copy2(path, tmp)
        _replace_with_retry(tmp, base)
    except Exception:
        try:
            os.unlink(tmp)
        except OSError:
            pass
        raise
    return base


def read_text_fallback(path, encoding="utf-8"):
    """读文本；主文件读不出来（空/损坏）就退回 .bak。读不到返回 None。"""
    for p in (path, path + ".bak"):
        try:
            if not os.path.isfile(p):
                continue
            with open(p, "r", encoding=encoding) as f:
                data = f.read()
            if data.strip():
                return data
        except Exception:
            continue
    return None


def read_json_fallback(path, validate=None, keep=DEFAULT_KEEP):
    """读 JSON；主文件损坏时依次回退 .bak / .bak.2 / .bak.3。

    ★ V2.5.0：以前只退 .bak 一份，而 snapshot 明明留了 keep 份；
      且不校验结构 —— 合法的 `null` / `[]` 会被当成正常数据返回。
    validate 可选：func(obj) -> bool，用于拒绝"能解析但语义不对"的内容。
    全部候选都失败返回 None。
    """
    base = path + ".bak"
    candidates = [path, base] + [
        "%s.%d" % (base, i) for i in range(2, max(2, keep) + 1)]
    for p in candidates:
        try:
            if not os.path.isfile(p):
                continue
            with open(p, "r", encoding="utf-8") as f:
                obj = json.load(f)
            if validate is not None and not validate(obj):
                raise ValueError("结构不符合预期: %s" % type(obj).__name__)
            return obj
        except Exception:
            continue
    return None


def quarantine_corrupt(path):
    """把损坏的主文件复制一份留证（不删原件），返回留证路径。

    用于"读失败但绝不能让空数据写回覆盖"的场景。已有留证时不重复生成。
    """
    import glob
    import time as _t
    try:
        if glob.glob(path + ".corrupt-*"):
            return None
        if not os.path.isfile(path):
            return None
        dst = "%s.corrupt-%s" % (path, _t.strftime("%Y%m%d-%H%M%S"))
        shutil.copy2(path, dst)
        return dst
    except Exception:
        return None
