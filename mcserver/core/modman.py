# -*- coding: utf-8 -*-
"""模组管理：识别已装 mod、启用/禁用（改名）、检查更新。

禁用靠改后缀名实现：xxx.jar -> xxx.jar.disabled
启用反之。这是最安全的方式，不删除不移动原文件。
"""

import os
import re
import json
import zipfile

DISABLED_SUFFIX = ".disabled"

# jar 内的元数据文件（按优先级）
META_FILES = [
    "fabric.mod.json",      # Fabric
    "META-INF/neoforge.mods.toml",  # NeoForge
    "META-INF/mods.toml",   # Forge
    "mcmod.info",           # 老 Forge
    "quilt.mod.json",       # Quilt
]


def _read_jar_meta(jar_path):
    """读 jar 里的 mod 元数据，返回 (id, name, version)。"""
    if not os.path.isfile(jar_path):
        return None, None, None
    try:
        with zipfile.ZipFile(jar_path) as z:
            names = set(z.namelist())
            for mf in META_FILES:
                if mf not in names:
                    continue
                raw = z.read(mf)
                txt = raw.decode("utf-8", errors="replace")
                try:
                    return _parse_meta(mf, txt)
                except Exception:
                    continue
    except Exception:
        pass
    return None, None, None


def _parse_meta(what, txt):
    if what.endswith(".json"):
        d = json.loads(txt)
        # fabric.mod.json
        mid = d.get("id") or d.get("schema_version") and None
        if what == "quilt.mod.json":
            mid = (d.get("quilt_loader", {}).get("id")
                   or d.get("id"))
            ver = d.get("quilt_loader", {}).get("version")
            name = d.get("quilt_loader", {}).get("metadata", {}).get("name")
            return mid, name, ver
        if what == "mcmod.info":
            if isinstance(d, list) and d:
                d = d[0]
            return d.get("modid"), d.get("name"), d.get("version")
        ver = d.get("version")
        name = d.get("name")
        if not name and isinstance(d.get("custom"), dict):
            name = d["custom"].get("modmenu", {}).get("display")
        return mid, name, ver
    # TOML（简易解析，只取我们需要的字段）
    mid = name = ver = None
    # 找 [[mods]] 块
    blocks = txt.split("[[")
    for b in blocks:
        if not b.strip().lower().startswith("mods"):
            continue
        mid = mid or _toml_get(b, "modId")
        name = name or _toml_get(b, "displayName")
        ver = ver or _toml_get(b, "version")
    return mid, name, ver


def _toml_get(block, key):
    m = re.search(r'^\s*%s\s*=\s*["\']([^"\']+)["\']' % re.escape(key),
                  block, re.M)
    return m.group(1) if m else None


_LOADER_SUFFIX_RE = re.compile(
    r"-(fabric|forge|neoforge|quilt|bukkit|spigot|paper|velocity|bungee)$")


def _strip_loader_seg(base):
    """去掉文件名尾部的加载器段（fabric/forge/...）。"""
    return _LOADER_SUFFIX_RE.sub("", base)


def _guess_from_filename(fn):
    """从文件名猜 id / 版本：lithium-mc1.21.10-0.20.1.jar"""
    base = os.path.basename(fn)
    for suf in (".jar" + DISABLED_SUFFIX, ".jar"):
        if base.lower().endswith(suf):
            base = base[: -len(suf)]
            break
    # ★ V1.7.3-1：先剥尾部加载器段（name-loader-version 格式，
    #   ferritecore-6.0.1-fabric 的版本段被 fabric 挡住剥不掉）
    base = _strip_loader_seg(base)
    m = re.search(r"-(\d+\.\d+(?:\.\d+)?)$", base)
    ver = None
    if m:
        ver = m.group(1)
        base = base[: m.start()]
    # 去掉 mc1.21.10 这类游戏版本段
    base = re.sub(r"-mc\d+\.\d+(\.\d+)?$", "", base)
    base = re.sub(r"-\d+\.\d+(\.\d+)?$", "", base)
    # 再剥一次 loader 段（name-loader-mc-version 格式，
    #   lithium-fabric-mc1.20.1-0.11.2 -> lithium）
    base = _strip_loader_seg(base)
    return base, ver


def scan_mods(server_dir):
    """扫描 <server_dir>/mods。"""
    return scan_mods_dir(os.path.join(server_dir, "mods"))


def scan_mods_dir(md):
    """扫描一个具体的 mods 目录。

    和 scan_mods(server_dir) 分开，是因为调用方有时手里只有 mods 目录
    本身（比如 loaderup.find_fabric_api），以前只能传 server_dir，
    传 mods 目录进去会去扫 mods/mods（不存在）→ 永远扫不到。
    """
    out = []
    if not md or not os.path.isdir(md):
        return out
    try:
        files = sorted(os.listdir(md))
    except Exception:
        return out
    for fn in files:
        full = os.path.join(md, fn)
        if not os.path.isfile(full):
            continue
        low = fn.lower()
        if low.endswith(".jar" + DISABLED_SUFFIX):
            enabled = False
        elif low.endswith(".jar"):
            enabled = True
        else:
            continue
        mid, name, ver = _read_jar_meta(full)
        guess = False
        if not mid and not name:
            gname, gver = _guess_from_filename(fn)
            # ★ 修复暗雷三：去掉可能残留的 .jar 或 .jar.disabled 后缀
            # 比如 lithium.jar 会变成 lithium，去 Modrinth 搜索时才准
            clean_name = gname.lower().replace(".jar.disabled", "").replace(".jar", "")
            name = clean_name
            mid = clean_name
            ver = ver or gver
            guess = True
        try:
            kb = os.path.getsize(full) / 1024.0
        except Exception:
            kb = 0
        out.append({
            "path": full,
            "filename": fn,
            "id": mid or "",
            "name": name or fn,
            "version": ver or "?",
            "enabled": enabled,
            "size_kb": round(kb, 1),
            "guess": guess,
        })
    return out


def _toggle_path(path, enable):
    """算出改名后的目标路径。"""
    d = os.path.dirname(path)
    fn = os.path.basename(path)
    low = fn.lower()
    if enable:
        if low.endswith(".jar" + DISABLED_SUFFIX):
            new = fn[: -len(DISABLED_SUFFIX)]
        else:
            return None
    else:
        if low.endswith(".jar"):
            new = fn + DISABLED_SUFFIX
        else:
            return None
    return os.path.join(d, new)


def set_enabled(path, enable):
    """启用/禁用一个 mod。返回新的文件名。"""
    dst = _toggle_path(path, enable)
    if not dst:
        return None
    if os.path.exists(dst):
        raise OSError("目标已存在: %s" % os.path.basename(dst))
    os.rename(path, dst)
    return os.path.basename(dst)


# ---------------- 更新检查 ----------------
MODRINTH = "https://api.modrinth.com/v2"


def _norm(s):
    """归一化用于比对：去空格、连字符、下划线、小写。"""
    return re.sub(r"[^a-z0-9]", "", (s or "").lower())


# =========================================================
#  更新检查（用文件哈希，不用名字猜）
# =========================================================
# 名字猜是行不通的：C2ME 显示名是
# "Concurrent Chunk Management Engine"，而 slug 是 c2me-fabric。
# PCL2 / HMCL 这类启动器都是用文件哈希查 Modrinth 的，
# 100% 准确，不依赖名字。


def file_sha1(path, block=1024 * 1024):
    """算文件的 sha1（Modrinth 用的是整个文件的 sha1）。"""
    import hashlib
    h = hashlib.sha1()
    try:
        with open(path, "rb") as f:
            while True:
                b = f.read(block)
                if not b:
                    break
                h.update(b)
        return h.hexdigest()
    except Exception:
        return None


def _api_post(path, body, timeout=30):
    """POST 到 Modrinth API（批量查文件哈希 / 查更新）。

    ★ 批量查哈希/更新是高频请求，国内直连官方容易卡死：
      auto / mcim 模式下先走 MCIM 镜像（实测同样支持 POST），
      镜像抛异常时自动回退官方源；手动锁定官方源则只走官方。
    """
    import urllib.request
    import json as _j
    from . import sources
    data = _j.dumps(body).encode("utf-8")

    def _post(base):
        req = urllib.request.Request(
            base + path, data=data, method="POST",
            headers={
                "Content-Type": "application/json",
                "User-Agent": "MCServerManager/0.1",
            })
        with urllib.request.urlopen(req, timeout=timeout) as r:
            return _j.loads(r.read().decode("utf-8"))

    if sources.get_mirror() == "official":
        return _post(MODRINTH)
    try:
        return _post(sources.MCIM_MODRINTH)
    except Exception:
        return _post(MODRINTH)


def _api_get(path, timeout=30, on_log=None):
    import urllib.request
    import json as _j
    from . import sources
    # auto 模式：交给 sources 挑源，挂了自动换另一个
    if sources.get_mirror() == "auto":
        return sources._get_json_auto(
            path, timeout,
            headers={"User-Agent": "MCServerManager/0.1"},
            log=on_log)
    # ★ 这里以前写的是 _base() —— 那是个不存在的名字，
    #   非 auto 模式（手动锁定官方源/镜像）下会 NameError 直接崩。
    #   sources.modrinth_base() 才是正确入口（会按当前镜像设置换域名）。
    req = urllib.request.Request(
        sources.modrinth_base() + path,
        headers={"User-Agent": "MCServerManager/0.1"})
    with urllib.request.urlopen(req, timeout=timeout) as r:
        return _j.loads(r.read().decode("utf-8"))



def _ver_eq(a, b):
    """宽松比对两个版本号字符串。

    Modrinth 上的写法五花八门：
      "0.2.9"                vs "0.2.9"
      "mc1.21.5-0.16.3-fabric" vs "mc1.21.5-0.16.3-fabric"
      "fabric-1.21.5-2.6.24"  vs "fabric-1.21.5-2.6.24"
    两边都一样时直接相等；不一样时归一化再比。
    保守起见：只要有一边为空，就不判相等（不误报"最新"）。
    """
    a = (a or "").strip()
    b = (b or "").strip()
    if not a or not b:
        return False
    if a == b:
        return True
    import re as _re
    def norm(x):
        x = x.lower()
        # 去掉 loader / mc 版本前缀尾缀，只留主版本号的样子
        x = _re.sub(r"\bfabric\b|\bforge\b|\bneoforge\b|"
                    r"\bquilt\b|\bmc\b|\+built\b", " ", x)
        x = x.replace("-", ".").replace("_", ".").replace("+", ".")
        x = _re.sub(r"\s+", ".", x)
        x = _re.sub(r"\.{2,}", ".", x).strip(".")
        return x
    na, nb = norm(a), norm(b)
    if na and na == nb:
        return True
    # 一方是另一方的后缀（比如 "0.16.3" 在 "mc1.21.5-0.16.3" 里）
    if na and nb and (na.endswith("." + nb) or nb.endswith("." + na)):
        return True
    return False


def lookup_by_hashes(hashes):
    """用 sha1 批量查 Modrinth，确认这些 jar 对应哪些项目。

    返回 {sha1: {"project_id", "version_number", "name", ...}}
    没找到的不在结果里。
    """
    if not hashes:
        return {}
    try:
        r = _api_post("/version_files", {
            "hashes": list(hashes),
            "algorithm": "sha1",
        })
    except Exception:
        return {}
    return r if isinstance(r, dict) else {}


def lookup_updates(hashes, mc_version, loader):
    """直接问 Modrinth：这些 jar 有没有更新版。

    返回 {sha1: {...新版本信息...}}，只含有更新的。
    """
    if not hashes:
        return {}
    body = {
        "hashes": list(hashes),
        "algorithm": "sha1",
    }
    if loader:
        body["loaders"] = [loader]
    if mc_version:
        body["game_versions"] = [mc_version]
    try:
        r = _api_post("/version_files/update", body)
    except Exception:
        return {}
    return r if isinstance(r, dict) else {}


def project_versions(project_id, mc_version=None, loader=None,
                     limit=5):
    """拿某项目的最新版本列表。"""
    import urllib.parse
    q = []
    if mc_version:
        # 这里必须编码，否则 400
        q.append("game_versions=%s"
                 % urllib.parse.quote('["%s"]' % mc_version))
    if loader:
        q.append("loaders=%s" % urllib.parse.quote('["%s"]' % loader))
    path = "/project/%s/version" % project_id
    if q:
        path += "?" + "&".join(q)
    try:
        vs = _api_get(path)
    except Exception:
        return []
    return vs if isinstance(vs, list) else []


def project_info(project_id):
    try:
        return _api_get("/project/%s" % project_id)
    except Exception:
        return None


def _cdn(url):
    """下载链接过一层镜像替换（关着镜像就原样返回）。"""
    if not url:
        return url
    try:
        from . import sources
        return sources.mirror_cdn(url)
    except Exception:
        return url


def _pick_file_url(ver):
    """从版本对象里挑下载链接（并按需换成镜像 CDN）。"""
    try:
        fs = ver.get("files") or []
        # 主文件优先
        for f in fs:
            if f.get("primary"):
                return _cdn(f.get("url"))
        if fs:
            return _cdn(fs[0].get("url"))
    except Exception:
        pass
    return None




# Modrinth 的分类 slug（mod 类常用的一部分）
CATEGORIES = [
    ("optimization", "优化"),
    ("utility", "实用工具"),
    ("library", "库 / 前置"),
    ("worldgen", "世界生成"),
    ("adventure", "冒险"),
    ("magic", "魔法"),
    ("technology", "科技"),
    ("storage", "存储"),
    ("decoration", "装饰"),
    ("food", "食物"),
    ("equipment", "装备"),
    ("transportation", "交通"),
    ("mobs", "生物"),
    ("cursed", "Cursed"),
    ("misc", "其它"),
]

# 排序方式：Modrinth 的 index 参数
SORTS = [
    ("relevance", "相关度"),
    ("downloads", "下载量"),
    ("follows", "关注数"),
    ("newest", "最新发布"),
    ("updated", "最近更新"),
]


def browse_projects(query="", mc_version=None, loader=None,
                    category=None, sort="relevance", limit=20,
                    offset=0, project_type="mod", source="modrinth"):
    """浏览/搜索项目（比 search_projects 全）。

    source: modrinth（默认）或 curseforge。
    facets 语法：外层数组是 AND，内层数组是 OR。
    返回 {"hits":[...], "total":N, "offset":N}
    """
    import urllib.parse
    # CurseForge 走另一套（经 MCIM 镜像，免 key）
    if source == "curseforge":
        from . import sources
        smap = {"relevance": 1, "downloads": 2, "updated": 3,
                "newest": 4}
        return sources.cf_search(
            query=query, mc_version=mc_version, loader=loader,
            category=category, sort=smap.get(sort, 1),
            page_size=limit, index=offset)

    # facets 语法：外层数组是 AND，内层数组是 OR。
    # ★ v0.3.0 修复：以前是手写字符串拼接 + _q() 加引号，
    #   最终 urlencode 出来带双层引号（["categories:"fabric""]），
    #   Modrinth 直接 400 Bad Request。
    #   现在改成列表嵌套，用 json.dumps 一次性生成标准 JSON：
    #   facets=[["project_type:mod"],["categories:fabric"]]
    #   —— 绝不 400。
    facets = []
    if project_type:
        facets.append(["project_type:%s" % project_type])
    if loader:
        facets.append(["categories:%s" % loader])
    if mc_version:
        facets.append(["versions:%s" % mc_version])
    if category:
        facets.append(["categories:%s" % category])

    params = {
        "limit": limit,
        "offset": offset,
        "index": sort,
    }
    if query:
        params["query"] = query
    if facets:
        # 顶部已 import json，直接序列化（urlencode 会自动转义方括号）
        params["facets"] = json.dumps(facets)

    path = "/search?" + urllib.parse.urlencode(params)
    try:
        d = _api_get(path)
    except Exception as e:
        # ★ 以前这里静默吞掉异常，结果只显示"0 条"，
        #   用户分不清是"真没有"还是"网络挂了/参数错了"。
        #   现在把原因带出去，界面上能显示。
        return {"hits": [], "total": 0, "offset": offset,
                "error": _brief_err(e), "query_url": path}
    if not isinstance(d, dict):
        return {"hits": [], "total": 0, "offset": offset,
                "error": "返回内容不是 JSON", "query_url": path}
    hits = d.get("hits") or []
    # ★ V1.6.3：过滤纯客户端模组（Sodium、Iris 之类）。
    #   服务端装 client_side=required + server_side=unsupported
    #   的模组毫无意义，直接不显示。
    hits = [h for h in hits
            if not (h.get("client_side") == "required"
                    and h.get("server_side") == "unsupported")]
    return {"hits": hits,
            "total": d.get("total_hits", len(hits)),
            "offset": d.get("offset", offset),
            "error": None, "query_url": path}


def _brief_err(e):
    """把网络异常翻译成一句人话（含 HTTP 码）。"""
    import urllib.error
    try:
        if isinstance(e, urllib.error.HTTPError):
            code = e.code
            hint = {400: "请求参数有问题（ facets 可能不被接受）",
                    403: "被拒绝（403）—— 可能缺 User-Agent，或代理/防火墙拦截",
                    404: "接口不存在（404）",
                    410: "资源已下架（410）",
                    429: "请求太频繁被限流（429），等一会儿再试",
                    500: "服务端出错（500）",
                    502: "网关错误（502）",
                    503: "服务不可用（503）"}.get(code, "HTTP %s" % code)
            return "%s —— %s" % (hint, code)
        if isinstance(e, urllib.error.URLError):
            return "连不上（%s）—— 检查网络/代理，或换个源" % (e.reason,)
        if isinstance(e, TimeoutError):
            return "超时 —— 网络太慢或被挡，建议切到国内镜像"
    except Exception:
        pass
    return "%s: %s" % (type(e).__name__, str(e)[:80])


def _q(s):
    """facet 值必须使用标准的 JSON 双引号包裹。

    ★ 修复 400 错误：以前这里加的是单引号（'mod'），
    但 Modrinth API 严格遵循 JSON 标准，只认双引号（"mod"）。
    """
    s = str(s).replace("'", "").replace('"', "")
    return '"%s"' % s


def install_from_project(slug, mc_version, loader, mods_dir,
                         on_log=None, source="modrinth"):
    """把一个项目装到 mods_dir（modrinth 或 curseforge）。

    返回 {"ok":True,"file":...,"version":...} 或
         {"ok":False,"reason":...}
    """
    def log(m):
        if on_log:
            on_log(m)
    import os
    from . import sources, install

    # ---- CurseForge ----
    if source == "curseforge":
        # ★ V1.8.6：内置推荐里的 slug（如 cupboard）不是 CF 数字 id，
        #   先解析成 project id 再取下载链接。
        cf_id = slug
        if not str(slug).isdigit():
            cf_id = sources.cf_resolve_id(
                slug, mc_version=mc_version, loader=loader)
            if not cf_id:
                return {"ok": False,
                        "reason": "CurseForge 上找不到 %s"
                                  "（可能需要 API key / MCIM 镜像）" % slug}
        r = sources.cf_download_url(cf_id, mc_version=mc_version,
                                    loader=loader)
        if r.get("err"):
            return {"ok": False, "reason": r["err"]}
        name = r.get("filename") or ("%s.jar" % slug)
        dest = os.path.join(mods_dir, name)
        try:
            install._download(r["url"], dest, on_log=on_log)
        except Exception as e:
            return {"ok": False,
                    "reason": "下载失败：%s" % (install._brief(e),)}
        return {"ok": True, "file": name, "version": "",
                "type": "release", "url": r["url"]}

    vs = project_versions(slug, mc_version=mc_version, loader=loader)
    if not vs:
        return {"ok": False,
                "reason": "没有 %s + %s 的版本" % (mc_version or "任何版本",
                                                    loader or "该加载器")}
    # 优先 release，其次 beta
    vs_sorted = sorted(vs, key=lambda v: 0 if v.get("version_type")
                       == "release" else 1)
    last_err = None
    for v in vs_sorted:
        u = _pick_file_url(v)
        if not u:
            continue
        name = None
        for f in (v.get("files") or []):
            if f.get("url") == u:
                name = f.get("filename")
        if not name:
            name = "%s-%s.jar" % (slug, v.get("version_number") or "0")
        dest = os.path.join(mods_dir, name)
        try:
            _dl(u, dest, on_log)
        except Exception as e:
            last_err = e
            continue
        return {"ok": True, "file": name,
                "version": v.get("version_number"),
                "type": v.get("version_type"), "url": u}
    # 走到这：要么没 jar，要么下载全失败。
    # 两种情况要分开说，不然网络故障会被误说成"没有这个模组"
    if last_err is not None:
        return {"ok": False,
                "reason": "下载失败：%s" % _brief_dl_err(last_err)}
    return {"ok": False, "reason": "找不到可下载的 jar"}


def _dl(url, dest, on_log=None):
    """下载，走 install 的统一实现（带 3 次重试 + 退避）。"""
    from . import install
    return install._download(url, dest, on_log=on_log)


def _brief_dl_err(e):
    """把下载异常压成人话。

    ★ 以前这个函数也叫 _brief_err，把上面那个（网络/HTTP 码版）
      整个覆盖掉了 —— 结果搜索失败时拿不到 403/429 那些具体提示，
      退化成一句泛泛的话。改名，两个各司其职。
    """
    try:
        from . import install
        return install._brief(e)
    except Exception:
        return "%s: %s" % (type(e).__name__, str(e)[:80])


def search_projects(query, mc_version=None, loader=None, limit=10):
    """按名字搜索 Modrinth 项目（哈希查不到时的退路）。"""
    import urllib.parse
    facets = []
    if loader:
        facets.append('["categories:%s"]' % loader)
    if mc_version:
        facets.append('["versions:%s"]' % mc_version)
    path = "/search?limit=%d&query=%s" % (
        limit, urllib.parse.quote(query))
    if facets:
        path += "&facets=" + urllib.parse.quote(
            "[%s]" % ",".join(facets))
    try:
        d = _api_get(path)
    except Exception:
        return []
    return (d or {}).get("hits", []) if isinstance(d, dict) else []


def check_update(m, mc_version, loader, on_log=None):
    """查 Modrinth 看有没有更新。

    优先用文件哈希（准确），查不到才退回名字搜索。
    返回 {"has","latest","url","title","status","err"}
    status: update / latest / unknown / error
    """
    log = on_log or (lambda x: None)
    path = m.get("path") or ""
    name = m.get("name") or m.get("id") or "?"

    # ---- 1) 哈希查（首选） ----
    if path and os.path.isfile(path):
        h = file_sha1(path)
        if h:
            log("  哈希 %s ... %s" % (h[:12], name))
            info = lookup_by_hashes([h])
            hit = (info or {}).get(h)
            if hit:
                pid = hit.get("project_id")
                cur_ver = (hit.get("version_number")
                           or m.get("version") or "")
                # 直接问有没有更新
                ups = lookup_updates([h], mc_version, loader)
                up = (ups or {}).get(h)
                pinfo = project_info(pid) or {}
                title = pinfo.get("title") or name
                home = "https://modrinth.com/mod/%s" % pid

                if up:
                    nv = up.get("version_number") or ""
                    # ★ 关键：Modrinth 的 /version_files/update 会返回
                    #   "该版本可用的最新版本"，它可能就是你当前装的
                    #   这个。必须比对，否则会出现 0.2.9 -> 0.2.9
                    #   这种自己等于自己还标"可更新"的假警报。
                    same_ver = _ver_eq(nv, cur_ver)
                    # 更可靠：看它返回的文件哈希是不是你本地这个
                    up_sha = ""
                    try:
                        for f in (up.get("files") or []):
                            hs = (f.get("hashes") or {}).get("sha1")
                            if hs:
                                up_sha = hs
                                if f.get("primary"):
                                    break
                    except Exception:
                        pass
                    same_file = bool(up_sha and up_sha.lower()
                                     == h.lower())

                    if same_file or same_ver:
                        note = ("（就是同一个文件）" if same_file
                                else "（版本号相同）")
                        log("  %s 已是最新 %s%s" % (name, cur_ver, note))
                        return {
                            "has": False, "latest": cur_ver,
                            "url": home, "title": title,
                            "status": "latest",
                            "cur": cur_ver, "method": "hash",
                            "same_file": same_file,
                        }

                    url = _pick_file_url(up) or home
                    return {
                        "has": True, "latest": nv, "url": url,
                        "title": title,
                        "status": "update",
                        "cur": cur_ver,
                        "method": "hash",
                    }
                # 没更新
                pinfo = project_info(pid) or {}
                return {
                    "has": False, "latest": cur_ver,
                    "url": "https://modrinth.com/mod/%s" % pid,
                    "title": pinfo.get("title") or name,
                    "status": "latest",
                    "cur": cur_ver,
                    "method": "hash",
                }
            else:
                log("  Modrinth 上没有这个文件的记录"
                    "（可能来自 CurseForge 或自己改过）: %s" % name)
        else:
            log("  算不出哈希: %s" % name)
    else:
        log("  文件不存在: %s" % path)

    # ---- 2) 退回名字搜索 ----
    q = _norm(m.get("id") or m.get("name") or "")
    if not q:
        return {"has": False, "status": "unknown",
                "err": "无法识别 mod 名"}

    try:
        hits = search_projects(q, mc_version, loader)
    except Exception as e:
        return {"has": False, "status": "error",
                "err": "查询失败: %r" % e}

    best = None
    for h_ in hits:
        sid = _norm(h_.get("slug", ""))
        title = _norm(h_.get("title", ""))
        if q == sid or q == title:
            best = h_
            break
        if q and (q in sid or sid in q):
            best = h_
            break
        if best is None and len(q) >= 6 and (
                q[:8] in sid or q[:8] in title):
            best = h_
    if not best:
        return {"has": False, "status": "unknown",
                "err": "Modrinth 上没找到同名项目"}

    slug = best.get("slug")
    title = best.get("title")
    try:
        vs = project_versions(slug, mc_version, loader)
    except Exception as e:
        return {"has": False, "status": "error",
                "err": "版本查询失败: %r" % e,
                "url": "https://modrinth.com/mod/%s" % slug}

    if not vs:
        return {"has": False, "status": "unknown",
                "err": "该版本下没有可用构建",
                "url": "https://modrinth.com/mod/%s" % slug}

    latest = vs[0]
    lname = latest.get("version_number") or latest.get("name") or ""
    cur = m.get("version") or ""
    has = _ver_newer(lname, cur)
    return {
        "has": has, "latest": lname,
        "url": _pick_file_url(latest)
               or "https://modrinth.com/mod/%s" % slug,
        "title": title,
        "status": "update" if has else "latest",
        "cur": cur,
        "method": "name",
    }


def _ver_newer(a, b):
    """粗判版本号 a 是否比 b 新。"""
    def parts(s):
        return [int(x) for x in re.findall(r"\d+", str(s))][:4]
    pa, pb = parts(a), parts(b)
    if not pa or not pb:
        return False
    n = max(len(pa), len(pb))
    pa += [0] * (n - len(pa))
    pb += [0] * (n - len(pb))
    return pa > pb
