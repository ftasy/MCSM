# -*- coding: utf-8 -*-
"""MC 版本 -> Java 版本 / 数据包格式 / 加载器支持 的映射表。"""

import os
import json

# (版本前缀, 需要Java, 数据包格式号)
# 说明：数据包格式号 82 是分界线
#   <82  -> 必须写 pack_format，不能写 min/max_format
#   >=82 -> 必须写 min_format / max_format
VERSION_TABLE = [
    # 老版本（Java 8 那档；用更高的 Java 可能起不来，见 JAVA_MAX_OK）
    ("1.12.2",   8, 3),
    ("1.14.4",   8, 4),
    ("1.15.2",   8, 5),
    ("1.16.5",   8, 6),
    ("1.17.1",  16, 7),
    ("1.18",    17, 8),
    ("1.18.1",  17, 8),
    ("1.18.2",  17, 9),
    ("1.19",    17, 10),
    ("1.19.1",  17, 10),
    ("1.19.2",  17, 10),
    ("1.19.3",  17, 12),
    ("1.19.4",  17, 12),
    ("1.20",    17, 15),
    ("1.20.1",  17, 15),
    ("1.20.2",  17, 18),
    ("1.20.3",  17, 26),
    ("1.20.4",  17, 26),
    ("1.20.5",  21, 32),
    ("1.20.6",  21, 32),
    ("1.21",    21, 48),
    ("1.21.1",  21, 48),
    ("1.21.2",  21, 57),
    ("1.21.3",  21, 57),
    ("1.21.4",  21, 61),
    ("1.21.5",  21, 71),
    ("1.21.6",  21, 80),
    ("1.21.7",  21, 81),
    ("1.21.8",  21, 81),
    ("1.21.9",  21, 88),
    ("1.21.10", 21, 88),
    ("1.21.11", 21, 94),
    # 26.x 起官方要求 Java 25（26.x 的 jar 在 Java 21 上启动不了）
    ("26.1",    25, 101),
    ("26.2",    25, 107),
    ("26.3",    25, None),
]

# 各加载器是否支持某版本（用前缀判断）
LOADER_SUPPORT = {
    #        最低版本    不支持的版本前缀
    "paper":    {"min": "1.14.4", "block": []},
    "fabric":   {"min": "1.14.4", "block": []},
    "forge":    {"min": "1.14.4", "block": []},
    "neoforge": {"min": "1.20.2", "block": ["1.20.1"]},
}

LOADER_LABEL = {
    "paper":    "Paper（插件服，推荐）",
    "fabric":   "Fabric（模组服，轻量化）",
    "forge":    "Forge（模组服，老牌）",
    "neoforge": "NeoForge（模组服，新版）",
    "vanilla":  "原版 Vanilla",
}


def _vkey(ver):
    """把版本字符串拆成可比较的元组。"""
    if not ver:
        return None
    parts = str(ver).strip().split(".")
    try:
        nums = tuple(int(p) for p in parts)
    except ValueError:
        return None
    while len(nums) < 3:
        nums = nums + (0,)
    return nums[:3]


# 预发布/快照的标记词：出现这些说明"还没正式发布"，
# 排序时应该排在【同版本号正式版之后】（也就是更旧）
_PRERELEASE_RE = None


def _prerelease_rank(ver):
    """返回预发布等级：0=正式版，越大越"还在开发中"（排序越靠后）。"""
    global _PRERELEASE_RE
    if _PRERELEASE_RE is None:
        import re as _re
        _PRERELEASE_RE = _re.compile(
            r"(pre|rc|snapshot|alpha|beta|exp|w\d)", _re.I)
    s = str(ver or "")
    m = _PRERELEASE_RE.search(s)
    return 1 if m else 0


def mc_version_key(ver, newest_first=True):
    """★ 排序专用的版本键（比 _vkey 稳健得多）。

    为什么不能直接 _vkey / findall(r"\\d+")：
      1) 抓【所有数字】会把后缀也抓进来，
         "1.21.10" -> (1,21,10) 长度 3
         "1.21.10-rc1" -> (1,21,10,1) 长度 4
         元组长度不一致时比较结果很反直觉
         （短元组是长元组前缀 -> 短的小），
         于是出现"1.21.10 夹着 1.21.11"这类乱序。
      2) 快照/预发布会被当成比正式版更新。

    统一做法：
      · 只取开头的 数字(.数字)* 作为版本主体
      · 补齐到 4 段再截断，长度永远一致
      · 预发布排在正式版之后（更旧）
      · 解析不出就排最后
    """
    import re as _re
    s = str(ver or "").strip()
    m = _re.match(r"(\d+(?:\.\d+)*)", s)
    if not m:
        return (9999, 9999, 9999, 9999, 9)
    nums = [int(x) for x in m.group(1).split(".")]
    while len(nums) < 4:
        nums.append(0)
    nums = nums[:4]
    pre = _prerelease_rank(s)
    key = tuple(nums) + (pre,)
    if newest_first:
        # 倒序（新的在前）：数字取负，预发布则再加 1（更靠后）
        return tuple(-x for x in nums[:3]) + (-nums[3], pre)
    return key


def lookup(ver):
    """返回 (java, data_pack_format)。识别不了返回 (None, None)。"""
    if not ver:
        return None, None
    v = str(ver).strip()
    # 精确匹配优先
    for prefix, java, fmt in VERSION_TABLE:
        if v == prefix:
            return java, fmt
    # ★ 比表里【同体系最新】还新的版本，不能走字符串前缀匹配：
    #   "1.21.12".startswith("1.21.1") 是 True，会误取成 1.21.1 的值 48
    #   （1.21.11 才是 94）。这类直接推算。
    k = mc_version_key(v, newest_first=False)
    sames = [mc_version_key(p, newest_first=False)
             for p, _j, _f in VERSION_TABLE]
    sames = [x for x in sames if x[0] == k[0]]
    # 比较时去掉最后一位（预发布标记），
    # 否则 "1.21.11-rc1" 会被当成比 1.21.11 还新，误走推算取到 95
    n4 = lambda t: tuple(t[:4])
    if sames and n4(k) > max(n4(x) for x in sames):
        return guess_java(v), guess_pack_format(v)[0]
    # 前缀匹配，从最长开始，避免 1.21 命中 1.21.1
    for prefix, java, fmt in sorted(VERSION_TABLE, key=lambda x: -len(x[0])):
        if v.startswith(prefix):
            return java, fmt
    # ★ 表里没有（MC 出了新版本）-> 用缓存里联网拿到的推算值
    load_cache()
    ex = _extra_versions.get(v)
    if isinstance(ex, dict):
        return ex.get("java"), ex.get("pack")
    # 连缓存也没有：现算一个，至少不会"查不到就用默认 Java"
    return guess_java(v), guess_pack_format(v)[0]


def need_java(ver):
    return lookup(ver)[0]


# 老版本用太高版本的 Java 会起不来
# （Forge 1.16.5 用 Java 17 会炸，1.18.x 用 Java 21 也容易出问题）
JAVA_MAX_OK = [
    ("1.12.2",  8),
    ("1.14.4",  11),
    ("1.15.2",  11),
    ("1.16.5",  11),
    ("1.17.1",  17),
    ("1.18",    17),
    ("1.18.1",  17),
    ("1.18.2",  17),
    ("1.19",    17),
    ("1.19.1",  17),
    ("1.19.2",  17),
    ("1.19.3",  17),
    ("1.19.4",  17),
    ("1.20",    17),
    ("1.20.1",  17),
]


def java_max_ok(ver):
    """这个版本最高建议用多少的 Java。超过会有风险。

    返回 None 表示没有上限（1.20.2+ 用 21/25 都还好）。
    """
    if not ver:
        return None
    v = str(ver).strip()
    for prefix, mx in sorted(JAVA_MAX_OK, key=lambda x: -len(x[0])):
        if v.startswith(prefix):
            return mx
    return None


def pack_format(ver):
    return lookup(ver)[1]


def uses_new_packmeta(ver):
    """True = 必须写 min_format/max_format（>=82）。"""
    fmt = pack_format(ver)
    return fmt is not None and fmt >= 82


def loader_ok(loader, ver):
    """该加载器是否支持这个 MC 版本。"""
    if loader == "vanilla":
        return True
    rule = LOADER_SUPPORT.get(loader)
    if not rule or not ver:
        return False
    k = _vkey(ver)
    if k is None:
        return False
    if _vkey(rule["min"]) and k < _vkey(rule["min"]):
        return False
    for b in rule["block"]:
        if str(ver).startswith(b):
            return False
    return True


def supported_loaders(ver):
    """该版本可选的加载器。

    ★ 一个都匹配不上时（比如版本填了个很怪的值，
      或者比表里最低的还老），不要返回空列表 ——
      那会让下拉框空着，用户完全没法选。
      兜底给 fabric + vanilla。
    """
    out = [l for l in ("paper", "fabric", "forge", "neoforge")
           if loader_ok(l, ver)]
    if out:
        return out
    return ["fabric", "vanilla"]


def parse_java_major(text):
    """从 `java -version` 输出里解析主版本号。"""
    if not text:
        return None
    t = str(text)
    # openjdk version "21.0.1" / java version "1.8.0_382"
    import re
    m = re.search(r'"(\d+)(?:\.(\d+))?', t)
    if not m:
        return None
    major = int(m.group(1))
    if major == 1:
        # 1.8.0_xxx -> 8
        m2 = re.search(r'1\.(\d+)\.', t)
        return int(m2.group(1)) if m2 else None
    return major


# ==========================================================================
# 版本列表动态更新
# --------------------------------------------------------------------------
# VERSION_TABLE 是硬编码的，MC 出新版本（26.4、26.5…）后表里没有：
#   · Java 需求查不到 -> 可能选错 Java，起不来
#   · 加载器支持判断不出来 -> 只能走兜底
# 所以这里从 Mojang 的版本清单拉一次，缓存到本地，以后直接用。
# 网络不通时静默用硬编码表，不会比现在更差。
# ==========================================================================

MANIFEST_URLS = [
    ("bmclapi", "https://bmclapi2.bangbang93.com/mc/game/version_manifest.json"),
    ("official", "https://piston-meta.mojang.com/mc/game/version_manifest_v2.json"),
]

_CACHE_PATH = os.path.join(
    os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
    "mc_versions_cache.json")

_extra_versions = {}      # 版本 -> {"java":.., "pack":.., "guess":True}


def guess_java(ver):
    """表里没有的版本，按 Mojang 的 Java 要求规律推算。

    规律（官方要求）：
      1.17 及以前   -> Java 8（1.17 例外要 16）
      1.18 ~ 1.20.4 -> Java 17
      1.20.5 ~ 1.21.11 -> Java 21
      26.x 起       -> Java 25
    """
    k = mc_version_key(ver, newest_first=False)
    if k[0] >= 9999:
        return None
    nums = k[:4]
    if nums[0] >= 26:
        # 26.x 一律 Java 25：官方明说 26.x 的 jar 在 Java 21 上启动不了。
        # 以前写成「26.3 起才要 25」，26.1 / 26.2 会被推成 21，起不来。
        return 25
    if nums[0] == 1:
        # ★ 1.20.5 起就要 Java 21（不只是 1.21，
        #   以前写成 nums[1]>=21 会漏掉 1.20.5/1.20.6，推成 17）
        if nums[1] >= 21:
            return 21
        if nums[1] == 20 and nums[2] >= 5:
            return 21
        if nums[1] >= 18:
            return 17
        if nums[1] == 17:
            return 16
        return 8
    return 21


def _known_pack_versions():
    """表里已经有 pack_format 的 (版本key, fmt)。"""
    out = []
    for ver, _j, fmt in VERSION_TABLE:
        if fmt is None:
            continue
        out.append((mc_version_key(ver, newest_first=False), fmt, ver))
    return sorted(out)


def guess_pack_format(ver):
    """表里没有的版本，推算数据包格式号。

    返回 (fmt, is_guess)。推算不了返回 (None, True)。
    做法：拿表内【相邻】的已知版本做外推 ——
    比最高的还新就往上加，比最低的还旧就取最低值。
    """
    known = _known_pack_versions()
    if not known:
        return None, True
    k = mc_version_key(ver, newest_first=False)
    if k[0] >= 9999:
        return None, True
    # ★ 只在【同一版本体系】里比较（1.x 和 26.x 是两套编号）。
    #   以前不区分，1.21.12 会被夹在 1.21.11 和 26.1 之间，
    #   于是取成 1.21.11 的值 94，永远推不上去。
    same = [x for x in known if x[0][0] == k[0]]
    lo = [x for x in same if x[0] <= k]
    hi = [x for x in same if x[0] > k]
    if lo and hi:
        # 夹在两个已知版本之间：取较低那个（保守）
        return lo[-1][1], True
    if lo:
        # 比表里最新的还新：往上外推（每个小版本 +1）
        base_k, base_f, base_v = lo[-1]
        # ★ 差值要取【第一个不同的段】：
        #   1.21.x 递增的是第三段(1.21.10->1.21.11)，
        #   26.x 递增的是第二段(26.2->26.3)。
        #   以前固定比第三段，26.3/26.4 全算成和 26.2 一样的 107。
        d = 1
        for i in range(4):
            if k[i] != base_k[i]:
                d = k[i] - base_k[i]
                break
        return base_f + max(1, d), True
    if hi:
        # 比同体系里最旧的还旧：取最旧那个
        return hi[0][1], True
    # 一个同体系的都没有：给已知最大值
    return max(x[1] for x in known), True


def fetch_release_versions(timeout=8, log=None):
    """从版本清单拉【正式版】列表，返回版本号列表（新 -> 旧）。

    先试 BMCLAPI（国内快），不通再试 Mojang 官方。
    全都失败返回 []（调用方应当接着用硬编码表）。
    """
    import urllib.request
    last = None
    for name, url in MANIFEST_URLS:
        try:
            req = urllib.request.Request(
                url, headers={"User-Agent": "mcservermgr/1.0"})
            with urllib.request.urlopen(req, timeout=timeout) as r:
                data = json.loads(r.read().decode("utf-8", "replace"))
            vs = [x.get("id") for x in (data.get("versions") or [])
                  if x.get("type") == "release" and x.get("id")]
            if vs:
                if log:
                    log("版本清单：%s 拿到 %d 个正式版" % (name, len(vs)))
                return vs
        except Exception as ex:
            last = ex
            if log:
                log("版本清单 %s 拉取失败: %r" % (name, ex))
    return []


def load_cache():
    """读本地缓存，合并进 _extra_versions。"""
    global _extra_versions
    try:
        if os.path.isfile(_CACHE_PATH):
            with open(_CACHE_PATH, "r", encoding="utf-8") as f:
                d = json.load(f)
            if isinstance(d, dict):
                _extra_versions = d
    except Exception:
        pass
    return _extra_versions


def save_cache(data=None):
    global _extra_versions
    if data is not None:
        _extra_versions = data
    try:
        d0 = os.path.dirname(_CACHE_PATH)
        if d0:
            os.makedirs(d0, exist_ok=True)
        with open(_CACHE_PATH, "w", encoding="utf-8") as f:
            json.dump(_extra_versions, f, ensure_ascii=False, indent=1)
        return (True, _CACHE_PATH)
    except Exception as e:
        # 缓存写不进去不算致命（下次还能重新拉），但要如实返回
        return (False, repr(e))


def refresh(timeout=8, log=None):
    """拉取最新版本清单并合并进缓存。

    返回 (新增版本列表, 全部正式版数量)。
    表里已存在的版本【不覆盖】—— 硬编码值是确认过的，
    比推算值可靠。只补表里没有的。
    """
    vs = fetch_release_versions(timeout=timeout, log=log)
    if not vs:
        return [], 0
    load_cache()
    known = {v for v, _j, _f in VERSION_TABLE}
    added = []
    for v in vs:
        if v in known or v in _extra_versions:
            continue
        j = guess_java(v)
        pf, _g = guess_pack_format(v)
        _extra_versions[v] = {"java": j, "pack": pf, "guess": True}
        added.append(v)
    if added:
        save_cache()
    return added, len(vs)


def all_versions():
    """硬编码表 + 缓存里的新版本，按新 -> 旧排序。"""
    load_cache()
    seen, out = set(), []
    for v, _j, _f in VERSION_TABLE:
        if v not in seen:
            seen.add(v); out.append(v)
    for v in _extra_versions:
        if v not in seen:
            seen.add(v); out.append(v)
    return sorted(out, key=lambda x: mc_version_key(x))
