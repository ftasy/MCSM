# -*- coding: utf-8 -*-
"""极简 NBT 解析器（零依赖，够读 level.dat / world_gen_settings.dat）。"""

import gzip
import struct

TAG_END=0; TAG_BYTE=1; TAG_SHORT=2; TAG_INT=3; TAG_LONG=4
TAG_FLOAT=5; TAG_DOUBLE=6; TAG_BYTE_ARRAY=7; TAG_STRING=8
TAG_LIST=9; TAG_COMPOUND=10; TAG_INT_ARRAY=11; TAG_LONG_ARRAY=12


class NBTError(Exception):
    pass


class _Reader:
    def __init__(self, buf):
        self.b = buf
        self.i = 0

    def _need(self, n):
        if self.i + n > len(self.b):
            raise NBTError("数据不足")

    def _u(self, fmt, size):
        self._need(size)
        v = struct.unpack_from(fmt, self.b, self.i)[0]
        self.i += size
        return v

    def byte(self):   return self._u(">b", 1)
    def short(self):  return self._u(">h", 2)
    def int(self):    return self._u(">i", 4)
    def long(self):   return self._u(">q", 8)
    def float(self):  return self._u(">f", 4)
    def double(self): return self._u(">d", 8)

    def string(self):
        n = self._u(">H", 2)
        self._need(n)
        s = self.b[self.i:self.i + n]
        self.i += n
        return s.decode("utf-8", "replace")

    def payload(self, t):
        if t == TAG_BYTE:   return self.byte()
        if t == TAG_SHORT:  return self.short()
        if t == TAG_INT:    return self.int()
        if t == TAG_LONG:   return self.long()
        if t == TAG_FLOAT:  return self.float()
        if t == TAG_DOUBLE: return self.double()
        if t == TAG_STRING: return self.string()
        if t == TAG_BYTE_ARRAY:
            n = self.int(); self._need(n)
            v = self.b[self.i:self.i + n]; self.i += n; return v
        if t == TAG_LIST:
            et = self.byte(); n = self.int()
            if n < 0:
                raise NBTError("负长度")
            return [self.payload(et) for _ in range(n)]
        if t == TAG_COMPOUND:
            d = {}
            while True:
                tt = self.byte()
                if tt == TAG_END:
                    break
                name = self.string()
                d[name] = self.payload(tt)
            return d
        if t == TAG_INT_ARRAY:
            n = self.int(); self._need(n * 4)
            v = list(struct.unpack_from(">%di" % n, self.b, self.i))
            self.i += n * 4; return v
        if t == TAG_LONG_ARRAY:
            n = self.int(); self._need(n * 8)
            v = list(struct.unpack_from(">%dq" % n, self.b, self.i))
            self.i += n * 8; return v
        raise NBTError("未知标签 %d" % t)

    def read_root(self):
        t = self.byte()
        if t == TAG_END:
            return {}
        name = self.string()
        val = self.payload(t)
        return val if isinstance(val, dict) else {"": val}


def parse_nbt(data):
    return _Reader(data).read_root()


def load_dat(path):
    """读 .dat，自动判断 gzip / 裸 NBT。失败返回 None。"""
    try:
        with open(path, "rb") as f:
            raw = f.read()
    except Exception:
        return None
    for attempt in (lambda: gzip.decompress(raw), lambda: raw):
        try:
            root = parse_nbt(attempt())
            if isinstance(root, dict):
                return root
        except Exception:
            continue
    return None


def dig_key(obj, key, depth=0):
    """递归找指定 key（忽略大小写），返回第一个匹配值。"""
    if depth > 8:
        return None
    if isinstance(obj, dict):
        for k, v in obj.items():
            if k.lower() == key.lower():
                return v
        for v in obj.values():
            r = dig_key(v, key, depth + 1)
            if r is not None:
                return r
    elif isinstance(obj, list):
        for v in obj:
            r = dig_key(v, key, depth + 1)
            if r is not None:
                return r
    return None
