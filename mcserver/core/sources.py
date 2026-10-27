# -*- coding: utf-8 -*-
"""模组下载源：Modrinth 官方 / MCIM 国内镜像 / CurseForge。

为什么需要这个模块：
  1. 国内连 api.modrinth.com 经常超时或被拒（10061），
     MCIM 把 Modrinth 和 CurseForge 都镜像了，只要换域名。
  2. 很多 Forge 模组只在 CurseForge 上发，
     Modrinth 上搜不到（比如 cupboard、Connectivity）。
     MCIM 镜像了 CurseForge，走它【不需要申请 API key】。

镜像的字段结构和官方一致，所以解析代码完全复用。
"""

import os
import re
import json
import urllib.request
import urllib.parse

# ---- 源地址 ----
MODRINTH_OFFICIAL = "https://api.modrinth.com/v2"
MODRINTH_CDN = "https://cdn.modrinth.com"

# MCIM：国内镜像，同时镜像 Modrinth 和 CurseForge
# ★ 实测：Modrinth API 必须带 /modrinth/v2 两个后缀，
#   只带 /modrinth 会 404（例如 /modrinth/project/sodium -> 404，
#   /modrinth/v2/project/sodium -> 200）。
MCIM_MODRINTH = "https://mod.mcimirror.top/modrinth/v2"
MCIM_CDN = "https://mod.mcimirror.top"
MCIM_CURSEFORGE = "https://mod.mcimirror.top/curseforge"

CURSEFORGE_OFFICIAL = "https://api.curseforge.com"

# CurseForge 的 mod classId = 6
CF_MOD_CLASS = 6

# ---- 当前配置（运行时可改） ----
_cfg = {
    # auto = 自动挑（先试 MCIM，挂了退回官方）
    # official / mcim = 手动锁死
    "mirror": "auto",
    "cf_key": "",
    "health": {},       # {"mcim": True/False, "official": ...}
    "_active": None,    # auto 模式下实际生效的那个
}

# auto 模式的尝试顺序
AUTO_ORDER = ["mcim", "official"]


# ---- 持久化 ----
def cfg_path():
    """配置文件路径（和 servers.json 同目录）。"""
    try:
        base = os.path.dirname(os.path.dirname(os.path.dirname(
            os.path.abspath(__file__))))
    except Exception:
        base = os.getcwd()
    return os.path.join(base, "sources.json")


def load():
    """读入保存的源设置。"""
    p = cfg_path()
    try:
        with open(p, "r", encoding="utf-8") as f:
            d = json.load(f)
        if isinstance(d, dict):
            set_mirror(d.get("mirror", "official"))
            set_cf_key(d.get("cf_key", ""))
    except Exception:
        pass


def save():
    """保存源设置。返回 (ok, err)。

    ★ 以前失败静默：切了镜像源以为生效，重启后又回到官方源。
    """
    p = cfg_path()
    try:
        d0 = os.path.dirname(p)
        if d0:
            os.makedirs(d0, exist_ok=True)
        with open(p, "w", encoding="utf-8") as f:
            json.dump({"mirror": _cfg["mirror"],
                       "cf_key": _cfg["cf_key"]},
                      f, ensure_ascii=False, indent=2)
        return (True, p)
    except Exception as e:
        return (False, "%s  (%s)" % (repr(e), p))


def set_mirror(name):
    """auto（自动，推荐）/ mcim（锁镜像）/ official（锁官方）。"""
    n = (name or "").strip().lower()
    if n not in ("auto", "mcim", "official"):
        n = "auto"
    _cfg["mirror"] = n
    if n in ("mcim", "official"):
        _cfg["_active"] = n


def get_mirror():
    return _cfg["mirror"]


def set_cf_key(key):
    _cfg["cf_key"] = (key or "").strip()


def get_cf_key():
    return _cfg["cf_key"]


def _resolve():
    """当前实际该用哪个源（auto 会看探测结果）。"""
    m = _cfg["mirror"]
    if m in ("official", "mcim"):
        return m
    for name in AUTO_ORDER:
        if _cfg["health"].get(name) is True:
            return name
    return _cfg.get("_active") or AUTO_ORDER[0]


def modrinth_base():
    """当前该用的 Modrinth API 前缀。"""
    if _resolve() == "mcim":
        return MCIM_MODRINTH
    return MODRINTH_OFFICIAL


def curseforge_base():
    """当前该用的 CurseForge API 前缀。"""
    if _resolve() == "mcim":
        return MCIM_CURSEFORGE
    return CURSEFORGE_OFFICIAL


def mirror_cdn(url):
    """把 Modrinth CDN 链接换到镜像（下载加速）。"""
    if not url:
        return url
    if _resolve() != "mcim":
        return url
    if url.startswith(MODRINTH_CDN):
        return MCIM_CDN + url[len(MODRINTH_CDN):]
    return url


def describe():
    """给用户看的一句话状态。"""
    m = _cfg["mirror"]
    if m == "auto":
        a = _resolve()
        h = _cfg["health"]
        tail = ""
        if h.get("mcim") is False:
            tail = "（MCIM 不通，已退回官方）"
        elif h.get("mcim") is True:
            tail = "（MCIM 可用）"
        return "自动 —— 当前 %s%s" % (
            "MCIM 国内镜像" if a == "mcim" else "官方源", tail)
    if m == "mcim":
        return "MCIM 国内镜像（Modrinth + CurseForge）"
    return "官方源（Modrinth）"


def mark_bad(name, err=None):
    """某个源请求失败记一笔；auto 模式下自动换另一个。"""
    _cfg["health"][name] = False
    if _cfg["mirror"] == "auto" and _cfg.get("_active") == name:
        for other in AUTO_ORDER:
            if other != name and _cfg["health"].get(other) is not False:
                _cfg["_active"] = other
                return other
    return _cfg.get("_active")


def mark_good(name):
    _cfg["health"][name] = True
    _cfg["_active"] = name


def probe(name, timeout=8):
    """探测某个源通不通。返回 (ok, 说明)。"""
    base = MCIM_MODRINTH if name == "mcim" else MODRINTH_OFFICIAL
    try:
        _get_json(base, "/project/sodium", timeout=timeout)
        mark_good(name)
        return True, "通"
    except Exception as e:
        mark_bad(name, e)
        return False, _brief(e)


def probe_all(timeout=8, on_log=None):
    """依次探测所有源；auto 模式锁定第一个通的。"""
    out = []
    for name in AUTO_ORDER:
        okk, why = probe(name, timeout)
        out.append((name, okk, why))
        if on_log:
            on_log("[探测] %s -> %s%s" % (
                "MCIM 镜像" if name == "mcim" else "官方源",
                "可用" if okk else "不可用",
                "" if okk else "（%s）" % why))
    if _cfg["mirror"] == "auto":
        for name, okk, _ in out:
            if okk:
                _cfg["_active"] = name
                break
    return out


def probe_one_latency(name, timeout=8):
    """并发探测单个源：返回 (name, ok, delay_ms, why)。"""
    import time
    base = MCIM_MODRINTH if name == "mcim" else MODRINTH_OFFICIAL
    t0 = time.time()
    try:
        _get_json(base, "/project/sodium", timeout=timeout)
        mark_good(name)
        return (name, True, int((time.time() - t0) * 1000), "通")
    except Exception as e:
        mark_bad(name, e)
        return (name, False, -1, _brief(e))


def probe_all_latency(timeout=8):
    """★ 并发探测所有源，返回 [(name, ok, delay_ms, why)]（毫秒）。

    与 probe_all 的区别：两个源同时发请求，界面不串行等待；
    auto 模式下同样锁定第一个通的为 _active。
    """
    import concurrent.futures
    out = []
    with concurrent.futures.ThreadPoolExecutor(max_workers=2) as ex:
        futs = [ex.submit(probe_one_latency, n, timeout)
                for n in AUTO_ORDER]
        for f in concurrent.futures.as_completed(futs):
            out.append(f.result())
    out.sort(key=lambda r: AUTO_ORDER.index(r[0]))
    if _cfg["mirror"] == "auto":
        for name, okk, _, _ in out:
            if okk:
                _cfg["_active"] = name
                break
    return out


# =========================================================
#  统一 HTTP
# =========================================================
def _get_json(base, path, timeout=30, headers=None):
    req = urllib.request.Request(
        base + path,
        headers=headers or {
            "User-Agent": "MCServerManager/0.1",
            "Accept": "application/json",
        })
    with urllib.request.urlopen(req, timeout=timeout) as r:
        return json.loads(r.read().decode("utf-8"))


def _get_json_auto(path, timeout=30, headers=None, log=None):
    """auto 模式下：MCIM 挂了自动退回官方（反过来也一样）。

    手动锁死某个源时不搞这套，失败就直接抛。
    """
    if _cfg["mirror"] != "auto":
        return _get_json(modrinth_base(), path, timeout, headers)

    order = list(AUTO_ORDER)
    # 把已探测可用的排前面
    order.sort(key=lambda n: 0 if _cfg["health"].get(n) is True else 1)
    last = None
    for name in order:
        base = MCIM_MODRINTH if name == "mcim" else MODRINTH_OFFICIAL
        try:
            d = _get_json(base, path, timeout, headers)
            mark_good(name)
            return d
        except Exception as e:
            last = e
            mark_bad(name, e)
            if log:
                log("[源] %s 失败：%s，换下一个"
                    % ("MCIM" if name == "mcim" else "官方", _brief(e)))
    raise last


# =========================================================
#  CurseForge（经 MCIM 镜像，免 key）
# =========================================================
def _cf_headers():
    h = {"User-Agent": "MCServerManager/0.1",
         "Accept": "application/json"}
    k = get_cf_key()
    if k:
        h["x-api-key"] = k
    return h


def cf_search(query="", mc_version=None, loader=None,
              category=None, sort=1, page_size=20, index=0,
              project_type="mod"):
    """搜 CurseForge 模组。

    镜像状态下不需要 key；官方 API 需要 key（否则 401）。
    返回 {"hits":[...], "total":N}

    ★ V1.9.1：classId 按 project_type 硬映射 —— 插件搜索必须
      classId=5（CF 插件类），否则把模组当插件搜出来（"插件里混入模组"）。
      统一成和 Modrinth 一样的字段，UI 不用分两套：
      title / slug / description / categories /
      downloads / versions / source / icon
    """
    if get_mirror() != "mcim" and not get_cf_key():
        return {"hits": [], "total": 0,
                "err": "官方 CF 需要 API Key，建议切换 MCIM 镜像"}
    # CurseForge classId：6=模组，5=Bukkit/Spigot 插件
    _cid = 5 if project_type == "plugin" else CF_MOD_CLASS
    params = {
        "gameId": 432,          # Minecraft
        "classId": _cid,
        "pageSize": page_size,
        "index": index,         # ★ 页码（1-based），调用方负责从 offset 换算
        "sortField": sort,      # 1=热门 2=下载量 ...
    }
    if query:
        params["searchFilter"] = query
    if mc_version:
        params["gameVersion"] = mc_version
    if loader:
        # CurseForge 用 modLoaderType：1=Forge 4=Fabric 5=Quilt 6=NeoForge
        # ★ V1.8.6：补上 neoforge=6（之前漏了，NeoForge 服搜 CF 会退化）
        m = {"forge": 1, "fabric": 4, "quilt": 5, "neoforge": 6}
        if loader in m:
            params["modLoaderType"] = m[loader]
    if category:
        params["categoryId"] = category

    path = "/v1/mods/search?" + urllib.parse.urlencode(params)
    try:
        d = _get_json(curseforge_base(), path, headers=_cf_headers())
    except Exception as e:
        return {"hits": [], "total": 0, "err": _brief(e)}
    raw = (d or {}).get("data") or []
    hits = []
    for m in raw:
        vs = [gv.get("name") for gv in (m.get("latestFiles") or [])
              if gv.get("name")]
        hits.append({
            "title": m.get("name") or "",
            "slug": str(m.get("id") or ""),
            "description": m.get("summary") or "",
            "categories": _cf_cats(m),
            "downloads": m.get("downloadCount") or 0,
            "versions": list(dict.fromkeys(
                [gv.get("gameVersion") for gv in
                 (m.get("gameVersionLatestFiles") or [])
                 if gv.get("gameVersion")])) or [],
            "source": "curseforge",
            "icon": (m.get("logo") or {}).get("thumbnailUrl") or "",
            "cf_id": m.get("id"),
        })
    total = ((d or {}).get("pagination") or {}).get("totalCount")
    return {"hits": hits, "total": total if total is not None
            else len(hits)}


def _cf_cats(m):
    """CurseForge 的分类名取出来（只取主分类）。"""
    out = []
    for c in (m.get("categories") or [])[:3]:
        n = c.get("name")
        if n:
            out.append(n)
    return out


def cf_resolve_id(slug, mc_version=None, loader=None):
    """把项目 slug（如 cupboard）解析成 CurseForge project id。

    CurseForge API 只认数字 id，不认 slug 路径；内置推荐清单里
    Cupboard 生态用的是 slug 形式，下载前先按名称搜索定位。
    返回 int id 或 None。
    """
    try:
        r = cf_search(query=slug, mc_version=mc_version, loader=loader,
                      page_size=10)
    except Exception:
        return None
    hits = r.get("hits") or []
    s = (slug or "").lower().replace(" ", "")
    for h in hits:
        t = (h.get("title") or "").lower().replace(" ", "")
        if s and (s in t or t == s):
            return h.get("cf_id")
    if hits:
        return hits[0].get("cf_id")  # 兜底取第一个搜索结果
    return None


def cf_download_url(cf_id, mc_version=None, loader=None):
    """拿一个 CurseForge 模组可下载的 jar 链接。

    返回 {"url","filename"} 或 {"err":...}
    """
    path = "/v1/mods/%s/files" % cf_id
    try:
        d = _get_json(curseforge_base(), path, headers=_cf_headers())
    except Exception as e:
        return {"err": _brief(e)}
    files = (d or {}).get("data") or []
    if not files:
        return {"err": "这个模组没有可下载的文件"}

    # 按 MC 版本 + 加载器挑，挑不到就退到最新一个
    def match(f):
        if mc_version:
            gvs = f.get("gameVersions") or []
            if mc_version not in gvs:
                return False
        if loader:
            gvs = [g.lower() for g in (f.get("gameVersions") or [])]
            if loader.lower() not in gvs:
                return False
        return True

    cand = [f for f in files if match(f)]
    pool = cand or files
    # releaseType: 1=release 2=beta 3=alpha，小的优先；
    # 同类型里日期新的优先
    def _key(f):
        rt = f.get("releaseType") or 9
        # 日期降序：用字符串取反倒序（安全，不碰数字）
        d = f.get("fileDate") or ""
        return (rt, _desc(d))

    def _desc(d):
        # 把日期字符串映射成"越小越新"，避免 -str 报错
        return tuple(-ord(c) for c in d[:19])

    pool = sorted(pool, key=_key)
    for f in pool:
        u = f.get("downloadUrl")
        if u:
            return {"url": u, "filename": f.get("fileName") or "",
                    "date": f.get("fileDate")}
    return {"err": "文件列表里没有直链（CurseForge 有些文件要跳转）"}


def cf_file_url(project_id, file_id):
    """按精确 projectID + fileID 拿 CurseForge 文件直链（整合包清单用）。"""
    path = "/v1/mods/%s/files/%s" % (project_id, file_id)
    try:
        d = _get_json(curseforge_base(), path, headers=_cf_headers())
    except Exception as e:
        return {"err": _brief(e)}
    f = (d or {}).get("data") or {}
    u = f.get("downloadUrl")
    if u:
        return {"url": u, "filename": f.get("fileName") or ""}
    return {"err": "该文件没有直链"}


def _brief(e):
    try:
        from . import install
        return install._brief(e)
    except Exception:
        return "%s: %s" % (type(e).__name__, str(e)[:60])


# =========================================================
#  mcmod（中文百科）—— 只做跳转，不抓取
# =========================================================
def mcmod_search_url(name):
    """生成 mcmod 的搜索页链接。

     mcmod 没有公开 API，抓取是灰色地带且页面一改就崩，
     所以只做"点一下打开搜索页"。
    """
    return "https://search.mcmod.cn/s?key=" + urllib.parse.quote(
        name or "")


# =========================================================
#  GitHub Releases（手动填仓库，不做搜索）
# =========================================================
def github_releases(repo):
    """列一个仓库的 release。repo 形如 owner/name。

    公开 API 无需 key，但限速 60 次/小时。
    返回 {"ok":True,"releases":[...]} 或 {"ok":False,"err":...}
    """
    repo = (repo or "").strip().strip("/")
    repo = re.sub(r"^https?://github\.com/", "", repo)
    repo = re.sub(r"\.git$", "", repo)
    parts = [p for p in repo.split("/") if p]
    if len(parts) < 2:
        return {"ok": False, "err": "格式应是 owner/name 或完整链接"}
    owner, name = parts[0], parts[1]
    path = "/repos/%s/%s/releases?per_page=30" % (owner, name)
    try:
        d = _get_json("https://api.github.com", path,
                      headers={"User-Agent": "MCServerManager/0.1",
                               "Accept": "application/vnd.github+json"})
    except Exception as e:
        return {"ok": False, "err": _brief(e)}
    out = []
    for r in (d or []):
        assets = []
        for a in (r.get("assets") or []):
            n = a.get("name") or ""
            if n.lower().endswith(".jar"):
                assets.append({
                    "name": n,
                    "url": a.get("browser_download_url"),
                    "size": a.get("size") or 0,
                })
        out.append({
            "tag": r.get("tag_name") or "",
            "name": r.get("name") or r.get("tag_name") or "",
            "date": (r.get("published_at") or "")[:10],
            "prerelease": bool(r.get("prerelease")),
            "assets": assets,
        })
    return {"ok": True, "releases": out,
            "repo": "%s/%s" % (owner, name)}


def github_latest_jar(repo, mc_version=None, loader=None):
    """从仓库里挑一个最像能用的 jar。

    GitHub 没有"支持哪个 MC 版本"的结构化字段，
    只能从文件名猜。挑不到就返回最新的 release 型 jar。
    """
    r = github_releases(repo)
    if not r.get("ok"):
        return r
    rels = [x for x in r["releases"] if not x["prerelease"]] \
        or r["releases"]
    for rel in rels:
        assets = rel["assets"]
        if not assets:
            continue
        # 优先文件名里同时含版本和加载器
        def score(a):
            n = a["name"].lower()
            s = 0
            if mc_version and mc_version in n:
                s += 2
            if loader and loader.lower() in n:
                s += 1
            # 排除源码包 / forge-installer 之类
            if any(k in n for k in ("sources", "api-", "installer",
                                    "javadoc", "dev", "slim")):
                s -= 3
            return s
        best = max(assets, key=score)
        if score(best) >= 0:
            return {"ok": True, "url": best["url"],
                    "filename": best["name"], "tag": rel["tag"]}
    return {"ok": False, "err": "没找到 jar（可能只在 CurseForge 发布）"}
