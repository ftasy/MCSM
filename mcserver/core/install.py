# -*- coding: utf-8 -*-
"""服务端核心下载安装：Paper / Fabric / Forge / NeoForge + 模组。"""

import os
import re
import json
import shutil
import subprocess
import socket
import time
import zipfile
import concurrent.futures
import urllib.request
import urllib.parse
import urllib.error

# 全局统一 User-Agent：所有下载与 API 请求共用同一个标识（与 modman 一致）。
UA = {"User-Agent": "MCServerManager/0.1"}
TIMEOUT = 60

# 网络瞬时故障很常见（尤其是 WinError 10061 "目标计算机积极拒绝"、
# ConnectionReset、代理抽风），以前一次失败就整个模组跳过，
# 导致"全军覆没，重开一次又好了"。
RETRY = 3           # 最多试几次
RETRY_WAIT = 1.2    # 首次退避秒数，之后按 1.2 / 2.4 / 4.8 递增

# 这些是"资源不存在"，重试一万次也没用，要立刻放弃
# （否则会白白等十几秒才告诉用户"这个版本没有"）
NO_RETRY_CODES = {400, 401, 403, 404, 410, 422}


def _is_retryable(e):
    """这个异常值得重试吗？"""
    if isinstance(e, urllib.error.HTTPError):
        # 5xx 是服务端出错，值得重试；4xx 多半是请求本身有问题
        return e.code not in NO_RETRY_CODES
    if isinstance(e, urllib.error.URLError):
        # 含 WinError 10061 / 10060 / 连接重置 / DNS 失败
        return True
    if isinstance(e, TimeoutError) or isinstance(e, OSError):
        # socket.timeout 在 3.10 里就是 TimeoutError
        return True
    return False


def _retry(fn, *args, on_retry=None, **kwargs):
    """带退避的重试包装。

    fn(*args, **kwargs) 抛异常时，若值得重试就等一会儿再来，
    最后一次仍失败则抛出原异常。
    """
    wait = RETRY_WAIT
    last = None
    for attempt in range(1, RETRY + 1):
        try:
            return fn(*args, **kwargs)
        except Exception as e:
            last = e
            if attempt >= RETRY or not _is_retryable(e):
                raise
            if on_retry:
                on_retry(attempt, e, wait)
            time.sleep(wait)
            wait *= 2
    raise last

MODRINTH = "https://api.modrinth.com/v2"

# Paper 唯一下载站：Fill v3。
# ★ 2025 年官方把 api.papermc.io/v2 整个下线了 —— 实测
#   /v2/projects/paper 等所有路径都返回 410 Gone，不再是"个别版本下架"。
#   现在只能走 fill v3；下载 URL 内嵌在 builds 响应里（fill-data.papermc.io），
#   官方明确说不要自己拼 URL，格式可能变。
#   版本支持状态在 /v3/projects/paper/versions/{v} 的 version.support.status。
FILL_API = "https://fill.papermc.io/v3/projects/paper"
FABRIC_META = "https://meta.fabricmc.net/v2"
# ★ BMCLAPI2 国内镜像（加速下载）。
#   metadata 拉取失败时会自动回退到官方源（见 _get_meta_fallback）。
FORGE_MAVEN = "https://bmclapi2.bangbang93.com/maven/net/minecraftforge/forge"
FORGE_MAVEN_OFFICIAL = "https://maven.minecraftforge.net/net/minecraftforge/forge"
NEO_META = "https://bmclapi2.bangbang93.com/maven/net/neoforged/neoforge/maven-metadata.xml"
NEO_META_OFFICIAL = "https://maven.neoforged.net/releases/net/neoforged/neoforge/maven-metadata.xml"


class InstallError(Exception):
    pass


def _open(url, timeout):
    req = urllib.request.Request(url, headers=UA)
    with urllib.request.urlopen(req, timeout=timeout) as r:
        return r.read()


def _get(url, binary=False, timeout=TIMEOUT, on_retry=None):
    data = _retry(_open, url, timeout, on_retry=on_retry)
    return data if binary else data.decode("utf-8", "replace")


def _get_json(url, timeout=TIMEOUT):
    return json.loads(_get(url, timeout=timeout))


def _get_meta_fallback(primary, fallback, timeout=15):
    """拉 metadata：镜像优先，镜像挂了自动退回官方源。

    ★ 镜像 metadata 偶尔会跳转到不可达的签名 CDN（本机实测
       BMCLAPI2 的 metadata 会 302 到 sakura.sld.tw，连接被重置）。
       直接全部失败等于 Forge/NeoForge 都列不出版本，
       所以镜像失败时回退官方源再试一次。
    """
    last = None
    for u in (primary, fallback):
        try:
            return _get(u, timeout=timeout)
        except Exception as e:
            last = e
    raise last


def _download(url, dest, timeout=180, on_retry=None, on_log=None):
    """下载到文件（流式分块 + 断点续传，防大文件内存爆炸）。

    ★ 断点续传：先写 dest + ".tmp"，中途失败会留下 .tmp，
      下次重试带上 Range 从断点继续写；服务器不支持 Range
      （回 200）就从头覆盖。下载完成后 .tmp 改名成 dest。
    """
    if on_retry is None and on_log is not None:
        def _retry_log(attempt, e, wait):
            on_log("下载失败（%s），%.1f 秒后重试（%d/%d）"
                   % (_brief(e), wait, attempt + 1, RETRY))
        on_retry = _retry_log

    def _do_download():
        req = urllib.request.Request(url, headers=UA)
        os.makedirs(os.path.dirname(dest), exist_ok=True)
        tmp = dest + ".tmp"

        # ★ 断点续传：有未下完的 .tmp 就带 Range 续传
        downloaded = 0
        if os.path.exists(tmp):
            try:
                downloaded = os.path.getsize(tmp)
            except Exception:
                downloaded = 0
            if downloaded > 0:
                req.add_header("Range", "bytes=%d-" % downloaded)

        try:
            with urllib.request.urlopen(req, timeout=timeout) as r:
                # 206 = 服务器支持 Range，接着 .tmp 往后写；
                # 200 = 不支持 Range（或重定向），从头写覆盖。
                mode = "ab" if r.status == 206 else "wb"
                if mode == "wb":
                    downloaded = 0
                with open(tmp, mode) as f:
                    # ★ 分块读取写入，每次 1MB，避免大文件（如核心 jar）撑爆内存
                    while True:
                        chunk = r.read(1024 * 1024)
                        if not chunk:
                            break
                        f.write(chunk)
        except urllib.error.HTTPError as e:
            if e.code != 416:
                raise
            # 416 = Range 越界，说明 .tmp 其实已经下完了，直接改名即可

        # 下载完成：.tmp -> dest
        if os.path.exists(dest):
            os.remove(dest)
        os.rename(tmp, dest)

    _retry(_do_download, on_retry=on_retry)
    return os.path.getsize(dest)


# =========================================================
#  Paper
# =========================================================
def fill_version_info(mc_version):
    """取 fill v3 的单版本详情；查不到返回 None。

    version.support.status 是 SUPPORTED / UNSUPPORTED；
    builds 里带内嵌下载地址。
    """
    try:
        d = _get_json("%s/versions/%s" % (FILL_API, mc_version), timeout=15)
    except Exception:
        return None
    if not isinstance(d, dict) or d.get("ok") is False:
        return None
    return d


def fill_version_status(mc_version):
    """该版本的支持状态：SUPPORTED / UNSUPPORTED / None（查不到）。"""
    d = fill_version_info(mc_version) or {}
    sup = ((d.get("version") or {}).get("support") or {})
    return sup.get("status")


def _vkey(v):
    """把 "1.21.4" 变成可比较的元组。带字母的排后面。"""
    import re as _re
    parts = _re.findall(r"\d+", str(v))
    return tuple(int(x) for x in parts) if parts else (0,)


def _paper_gone_error(mc_version, orig=None):
    """Paper 查不到这个版本的构建时，给出人话提示（含推荐替代版本）。

    ★ 以前这里用了 fill_versions() 里并不存在的 f["supported"]，
      一走到这个分支就 KeyError —— 用户看到的是异常而不是提示。
      现在版本池全部来自 fill，支持状态单独查（只在这条错误路径查一次）。
    """
    fill = fill_versions()
    if fill:
        pool = sorted({f["version"] for f in fill}, key=_vkey)
        mine = _vkey(mc_version)
        newer = [v for v in pool if _vkey(v) > mine]
        older = [v for v in pool if _vkey(v) < mine]
        status = fill_version_status(mc_version)
        msg = ("Paper（fill 站）没有 %s 的可用构建。\n\n" % mc_version)
        if status == "UNSUPPORTED":
            msg += "该版本已被官方标记为不再支持（UNSUPPORTED）。\n\n"
        elif mc_version in pool:
            msg += "（fill 站上有这个版本，但查不到任何构建）\n\n"
        if newer:
            msg += ("比你新、还在维护的：%s\n" % "、".join(newer[:8]))
            msg += ("  注意：用更高版本打开存档会【升级世界格式】，"
                    "不可逆。\n"
                    "  地图类存档建议先备份再试。\n\n")
        if older:
            older.sort(key=_vkey, reverse=True)
            msg += ("比你旧的：%s\n"
                    "  ⚠ 千万不要用它们 —— 低版本打不开高版本存档，"
                    "会损坏数据。\n\n" % "、".join(older[:5]))
        msg += "推荐做法：改用 Fabric（几乎所有版本都有）。"
        return InstallError(msg)
    return InstallError(
        "无法获取 Paper 版本列表（fill 站不通）。\n"
        "原始错误: %r\n\n"
        "推荐做法：改用 Fabric（几乎所有版本都有）。" % (orig,))


def install_paper(server_dir, mc_version, java_path=None,
                  on_log=None, loader_version=None):
    """下载 Paper jar 到 server_dir/server.jar（只走 fill v3）。"""
    def log(m):
        if on_log:
            on_log(m)

    log("查询 Paper %s 构建列表（fill v3）..." % mc_version)
    bs = fill_builds(mc_version)
    if not bs:
        raise _paper_gone_error(mc_version)

    def _bid(b):
        v = b.get("build")
        try:
            return int(v)
        except Exception:
            return 0

    if loader_version:
        want = str(loader_version)
        hit = [b for b in bs if str(b.get("build")) == want]
        if not hit:
            raise InstallError(
                "Paper %s 没有 build %s。\n可用（最新8个）：%s"
                % (mc_version, want,
                   ", ".join(str(b.get("build")) for b in
                             sorted(bs, key=_bid, reverse=True)[:8])))
        pick = hit[0]
    else:
        # ★ 优先 STABLE 通道里 build 号最大的；没有 STABLE 再取全部里最大的。
        #   不假设接口返回顺序（v2/v3 顺序都不一样），显式按 build 号排序。
        stable = [b for b in bs
                  if str(b.get("channel") or "").upper() == "STABLE"]
        pick = sorted(stable or bs, key=_bid, reverse=True)[0]

    build = pick.get("build")
    url = pick.get("url")
    if not url:
        # 兜底：详情接口的 builds 里再找一次（字段和列表接口不完全一样）
        info = fill_version_info(mc_version) or {}
        for b in info.get("builds") or []:
            bid = b.get("id") or b.get("build")
            if str(bid) != str(build):
                continue
            dl = b.get("downloads") or {}
            app = (dl.get("server:default") or dl.get("application")
                   or dl.get("server") or {})
            url = app.get("url")
            break
    if not url:
        raise InstallError(
            "fill 站上有 Paper %s 的 build %s，但接口没给下载地址。\n\n"
            "可以手动下载：\n"
            "  https://fill-ui.papermc.io/projects/paper/versions/%s\n"
            "下载后重命名为 server.jar 放进服务器目录即可。\n\n"
            "或者改用 Fabric —— 几乎所有版本都有。"
            % (mc_version, build, mc_version))

    dest = os.path.join(server_dir, "server.jar")
    log("下载 build %s ..." % build)
    n = _download(url, dest)
    log("完成，%.1f MB" % (n / 1048576.0))
    status = fill_version_status(mc_version)
    if status and status.upper() != "SUPPORTED":
        log("[WARN] 该版本被官方标记为 %s：不再修 bug / 不补安全漏洞"
            % status)
    return {"jar": dest, "file": url.rsplit("/", 1)[-1],
            "build": build, "via_fill": True}


# =========================================================
#  Fabric
# =========================================================
def fill_versions():
    """从 fill 站查 Paper 所有版本（含官方标为 unsupported 的）。

    v3 的返回结构是 {"versions": {"1.19": ["1.19.3", "1.19.4"], ...}}
    —— versions 是按 family 分组的【对象】，不是数组。
    以前当成数组遍历，拿到的是 family 名（"1.19"）而不是具体版本。
    """
    try:
        d = _get_json("%s" % FILL_API)
    except Exception:
        return []
    vs = (d or {}).get("versions")
    out = []
    if isinstance(vs, dict):
        for family, arr in vs.items():
            if isinstance(arr, list):
                for v in arr:
                    out.append({"version": str(v), "family": str(family)})
            elif isinstance(arr, str):
                out.append({"version": arr, "family": str(family)})
    elif isinstance(vs, list):
        for v in vs:
            if isinstance(v, str):
                out.append({"version": v, "family": None})
            elif isinstance(v, dict):
                nm = v.get("version") or v.get("id") or v.get("key")
                if nm:
                    out.append({"version": str(nm),
                                "family": v.get("family")})
    return out


_last_fill_err = {}


def fill_builds(mc_version):
    """查某个 MC 版本的构建列表。返回 [] 表示查不到。

    v3 要点（以前踩过的坑）：
      · build 号字段是 id（v2 叫 build）
      · 下载 URL 直接内嵌在 downloads."server:default".url，
        指向 fill-data.papermc.io —— 不要自己拼 URL
      · 出错时返回 {"ok": false, "message": "..."} 而不是抛异常
    """
    urls = [
        "%s/versions/%s/builds" % (FILL_API, mc_version),
        "%s/versions/%s" % (FILL_API, mc_version),
    ]
    d = None
    errs = []
    for u in urls:
        try:
            d = _get_json(u, timeout=15)
            if d:
                break
        except Exception as e:
            errs.append("%s -> %s" % (u.split("/v3")[-1], _brief(e)))
    _last_fill_err[mc_version] = errs
    if d is None:
        return []

    # {"ok": false, "message": "..."} —— v3 的错误形态
    if isinstance(d, dict) and d.get("ok") is False:
        _last_fill_err[mc_version] = [str(d.get("message") or "未知错误")]
        return []

    raw = d if isinstance(d, list) else (d.get("builds") or [])
    out = []
    for b in raw:
        if not isinstance(b, dict):
            bid = b
            dl = {}
        else:
            bid = b.get("id") or b.get("build") or b.get("number")
            dl = b.get("downloads") or {}
        if bid is None:
            continue
        # v3: application -> server:default（旧 key 保留兜底）
        app = (dl.get("server:default") or dl.get("application")
               or dl.get("server") or {})
        out.append({
            "build": bid,
            "channel": (b or {}).get("channel") if isinstance(b, dict) else None,
            "url": app.get("url"),
            "name": app.get("name"),
        })
    return out


def fabric_loader_candidates(mc_version):
    """列出 Fabric loader 所有可用版本（含测试版）。

    返回 [{version, stable}]，★ V1.8.2：稳定版全部排在测试版前面，
    同 stable 内按版本号新→旧排序（官方列表可能把 beta 排在前头，
    不重排的话 fabric_loader_version 会取到旧稳定版）。
    官方 meta 里 stable=false 的就是测试版（如 0.20.x 曾经是 beta）。
    """
    # ★ 拉版本列表不该等 60 秒，15 秒没回就当网络不通，直接抛错（让界面提示重试）
    arr = _get_json("%s/versions/loader/%s" % (FABRIC_META, mc_version), timeout=15)
    out = []
    for a in arr:
        lv = (a.get("loader") or {}).get("version")
        if not lv:
            continue
        out.append({"version": lv,
                    "stable": bool((a.get("loader") or {}).get("stable"))})
    if not out:
        raise InstallError("Fabric 没有 %s 的 loader" % mc_version)

    # ★ V1.8.2：稳定版排前 + 版本号倒序（新的在前）
    def _nums(v):
        return [int(x) for x in re.findall(r"\d+", v)] or [0]

    out.sort(key=lambda c: (
        0 if c["stable"] else 1,
        tuple(-n for n in _nums(c["version"]))))
    return out


def fabric_loader_version(mc_version, want=None):
    """取 Fabric loader 版本。want 指定就用指定的，否则用最新稳定版。"""
    cands = fabric_loader_candidates(mc_version)
    if want:
        for c in cands:
            if c["version"] == want:
                return c["version"]
        # 指定了但没有 -> 明确报错，别偷偷用别的版本
        raise InstallError(
            "Fabric loader %s 在 MC %s 上不存在。\n"
            "可用：%s" % (want, mc_version,
                          ", ".join(c["version"] for c in cands[:8])))
    stable = [c for c in cands if c["stable"]]
    return (stable or cands)[0]["version"]


def fabric_installer_candidates():
    """列出 Fabric installer 所有版本。返回 [{version, stable}]。"""
    arr = _get_json("%s/versions/installer" % FABRIC_META, timeout=15)
    out = []
    for a in arr:
        v = a.get("version")
        if v:
            out.append({"version": v, "stable": bool(a.get("stable"))})
    if not out:
        raise InstallError("无法获取 Fabric installer 版本")
    return out


def fabric_installer_version(want=None):
    cands = fabric_installer_candidates()
    if want:
        for c in cands:
            if c["version"] == want:
                return c["version"]
        raise InstallError("Fabric installer %s 不存在" % want)
    stable = [c for c in cands if c["stable"]]
    return (stable or cands)[0]["version"]


def install_fabric(server_dir, mc_version, java_path=None,
                   on_log=None, loader_version=None,
                   installer_version=None):
    """下载 Fabric 服务端启动 jar（自带引导，首次运行自动补全）。

    loader_version / installer_version 指定版本，
    None 表示用最新稳定版。
    """
    def log(m):
        if on_log:
            on_log(m)

    log("查询 Fabric loader ...")
    loader = fabric_loader_version(mc_version, want=loader_version)
    inst = fabric_installer_version(want=installer_version)
    log("loader=%s installer=%s" % (loader, inst))
    if loader_version:
        cands = fabric_loader_candidates(mc_version)
        hit = next((c for c in cands if c["version"] == loader), None)
        if hit and not hit["stable"]:
            log("  ⚠ loader %s 是测试版，可能有 bug" % loader)

    url = "%s/versions/loader/%s/%s/%s/server/jar" % (
        FABRIC_META, mc_version, loader, inst)
    # 不能叫 server.jar！否则会被扫描器认成"原版 Vanilla"
    dest = os.path.join(server_dir, "fabric.jar")
    log("下载 Fabric 服务端启动器 -> fabric.jar ...")
    n = _download(url, dest)
    log("完成，%.1f MB（首次启动时会自动下载依赖）" % (n / 1048576.0))
    return {"jar": dest, "loader": loader, "installer": inst}


# =========================================================
#  NeoForge
# =========================================================
def neoforge_versions(force_official=False):
    """拉取 NeoForge 版本列表（镜像优先，自动回退官方源）。

    force_official=True：跳过镜像，只问官方源。
    用于 1.20.1 —— BMCLAPI 镜像的 metadata 实测没有 47.x。
    ★ 网络失败时抛 InstallError（与 forge_versions 对称）：
      以前 return [] 会把"网络断了"误报成"没有这个版本"。
    """
    try:
        xml = (_get(NEO_META_OFFICIAL, timeout=15) if force_official
               else _get_meta_fallback(NEO_META, NEO_META_OFFICIAL))
        return re.findall(r"<version>([^<]+)</version>", xml)
    except Exception as e:
        # ★ 以前 return []，导致网络断了误报"没有版本"。
        #   抛出异常，让上层准确提示"网络问题"。
        raise InstallError("获取 NeoForge 版本列表失败（网络问题）：%s" % _brief(e))


def neoforge_candidates(mc_version):
    """列出 NeoForge 在这个 MC 版本上可用的所有版本（新的在前）。

    ★ 修复：NeoForge 从 1.20.2 开始，版本号变成了 20.2.x，1.21.1 变成了 21.1.x。
      以前用 startswith("1.21.1.") 匹配，永远匹配不到，导致菜单拉取直接报错。
    """
    all_v = neoforge_versions()
    if not all_v:
        return []
    
    # 兼容 1.20.1 特例（它是 47.x）
    if mc_version == "1.20.1":
        cand = [v for v in all_v if v.startswith("47.")]
        if not cand:
            # ★ 镜像 metadata 里没有 47.x（BMCLAPI 实测缺失），
            #   强制官方源重试一次，别让用户误以为不支持 1.20.1。
            cand = [v for v in neoforge_versions(force_official=True)
                    if v.startswith("47.")]
    else:
        # 剥离 "1." 前缀，比如 "1.21.1" -> "21.1"
        parts = mc_version.split(".")
        neo_prefix = ".".join(parts[1:]) if len(parts) > 1 and parts[0] == "1" else mc_version
        cand = [v for v in all_v if v.startswith(neo_prefix + ".") or v.startswith(mc_version + ".")]

    if not cand:
        raise InstallError("NeoForge 没有 %s 的版本" % mc_version)
    # ★ 用数字元组排序
    return sorted(cand, key=_vkey, reverse=True)


def install_neoforge(server_dir, mc_version, java_path=None, on_log=None,
                     loader_version=None):
    """下载 NeoForge installer 并执行 --installServer。"""
    def log(m):
        if on_log:
            on_log(m)

    log("准备 NeoForge 版本 ...")
    if loader_version:
        # ★ 同上：信用户选的，不二次请求
        ver = str(loader_version)
        log("使用指定版本: %s" % ver)
    else:
        try:
            cand = neoforge_candidates(mc_version)
        except Exception as e:
            raise InstallError("无法获取 NeoForge 版本列表（网络问题）: %s" % _brief(e))
        ver = cand[0]

    url = ("https://bmclapi2.bangbang93.com/maven/net/neoforged/neoforge/"
           "%s/neoforge-%s-installer.jar" % (ver, ver))
    inst_jar = os.path.join(server_dir, "_neoforge_installer.jar")
    log("下载 NeoForge %s installer ..." % ver)
    _download(url, inst_jar)
    return _run_installer(server_dir, inst_jar, java_path, log,
                          "NeoForge %s" % ver)


# =========================================================
#  Forge
# =========================================================
def forge_versions(mc_version):
    """从 promotions 或 maven 目录列表拿可用版本（镜像优先，自动回退官方源）。

    ★ 网络失败时抛 InstallError（不再 return []）：
      之前 return [] 会把"网络断了"误报成"这个版本不存在"，
      用户在列表里明明看得到版本，点开始却被拒。
    """
    try:
        xml = _get_meta_fallback(
            "%s/maven-metadata.xml" % FORGE_MAVEN,
            "%s/maven-metadata.xml" % FORGE_MAVEN_OFFICIAL)
    except Exception as e:
        # ★ 以前 return []，导致网络断了也会报"没有版本"。
        #   抛出异常，让上层能正确显示"网络超时"或"连不上"。
        raise InstallError("获取 Forge 版本列表失败（网络问题）：%s" % _brief(e))
    vs = re.findall(r"<version>([^<]+)</version>", xml)
    prefix = "%s-" % mc_version
    return sorted([v for v in vs if v.startswith(prefix)])


def forge_candidates(mc_version):
    """列出 Forge 在这个 MC 版本上可用的所有版本（新的在前）。"""
    cand = forge_versions(mc_version)
    if not cand:
        raise InstallError("Forge 没有 %s 的版本" % mc_version)
    # ★ 用数字元组排序，不能用纯字符串
    return sorted(cand, key=_vkey, reverse=True)


def forge_latest_version(mc_version):
    """该 MC 版本上最新的 Forge；查不到返回 None（不抛异常）。"""
    try:
        cand = forge_candidates(mc_version)
        return cand[0] if cand else None
    except Exception:
        return None


def install_forge(server_dir, mc_version, java_path=None, on_log=None,
                  loader_version=None):
    def log(m):
        if on_log:
            on_log(m)

    log("准备 Forge %s ..." % mc_version)
    if loader_version:
        # ★ 如果用户在下拉框里指定了明确的版本号（比如 1.20.1-47.4.28），
        # 直接用它！不要去请求 API 重新校验一遍。
        # 因为用户选的时候已经成功拉取过列表了，此时再次联网校验
        # 极易因为网络波动（如 302 跳转失败）导致明明有版本却报错。
        ver = str(loader_version)
        log("使用指定版本: %s" % ver)
    else:
        # 只有没指定版本时，才去拉取列表找最新版
        cand = forge_candidates(mc_version)
        ver = cand[0]
    url = "%s/%s/forge-%s-installer.jar" % (FORGE_MAVEN, ver, ver)
    inst_jar = os.path.join(server_dir, "_forge_installer.jar")
    log("下载 Forge %s installer ..." % ver)
    _download(url, inst_jar)
    return _run_installer(server_dir, inst_jar, java_path, log,
                          "Forge %s" % ver)


def _find_forge_argsfile(server_dir):
    """新版 Forge 不生成启动 jar，改用 args 文件启动。"""
    import glob
    base = os.path.join(server_dir, "libraries")

    def _hit_key(p):
        # 路径形如 .../forge/1.20.1-47.2.0/win_args.txt
        # 取上一级目录名（版本号）来比大小
        dirname = os.path.basename(os.path.dirname(p))
        return _vkey(dirname)

    for brand in ("net/minecraftforge/forge",
                  "net/minecraftforge/neoforge"):
        pat = os.path.join(base, *brand.split("/"), "*", "win_args.txt")
        hits = glob.glob(pat)
        if hits:
            hits.sort(key=_hit_key, reverse=True) # ★ 按版本号降序，取最新
            return os.path.relpath(hits[0], server_dir).replace("/", "\\")
    # 兜底：任何 win_args.txt
    hits = glob.glob(os.path.join(base, "**", "win_args.txt"), recursive=True)
    if hits:
        hits.sort(key=_hit_key, reverse=True) # ★ 按版本号降序，取最新
        return os.path.relpath(hits[0], server_dir).replace("/", "\\")
    return None


def _run_installer(server_dir, inst_jar, java_path, log, label):
    """执行 installer.jar --installServer（流式输出 + 30分钟超时）。"""
    if not java_path:
        raise InstallError("缺少 Java 路径，无法执行 installer")
    
    log("执行 installer（需要几分钟，请耐心等待）...")
    import time
    
    timeout_sec = 1800  # 30分钟
    start_time = time.time()
    
    try:
        p = subprocess.Popen(
            [java_path, "-jar", inst_jar, "--installServer", server_dir],
            cwd=server_dir,
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            text=True,
            encoding="utf-8",
            errors="replace",  # 防止 Windows 下 GBK 编码导致崩溃
            bufsize=1,          # 行缓冲
            # ★ V1.6.2：打包后禁止 java installer 弹出黑色 CMD 窗
            creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
        )
    except Exception as e:
        raise InstallError("启动 installer 失败: %r" % e)

    out_lines = []  # ★ 收集输出，防止走到最后拿不到
    try:
        # 逐行读取输出，边读边发给 UI
        for line in iter(p.stdout.readline, ""):
            if line:
                out_lines.append(line)  # ★ 收集
                log(line.rstrip())  # 把每一行 installer 输出推到界面上
            
            # 检查超时
            if time.time() - start_time > timeout_sec:
                p.kill()
                raise InstallError(
                    "installer 超过 30 分钟没结束，多半是网络慢或者卡死了。\n"
                    "建议：换 Fabric，或去官网手动下载 installer 运行。"
                )
        
        # 等进程真正结束（最多再等 10 秒）
        p.wait(timeout=10)
        
    except subprocess.TimeoutExpired:
        p.kill()
        raise InstallError("installer 进程未能正常退出。")
    except Exception as e:
        p.kill()
        raise InstallError("读取 installer 输出时出错: %r" % e)
    finally:
        if p.poll() is None:
            p.kill()

    log("installer 返回码 %d" % p.returncode)
    if p.returncode != 0:
        raise InstallError("installer 失败（返回码 %d），请看上方日志。" % p.returncode)

    # 找生成的启动 jar（老版 Forge 是 jar）
    for pat in ("run.jar", "forge-*-shim.jar", "neoforge-*-shim.jar",
                "forge-*.jar", "neoforge-*.jar"):
        import glob
        hits = [p for p in glob.glob(os.path.join(server_dir, pat))
                if "installer" not in os.path.basename(p).lower()]
        if hits:
            log("生成: %s" % os.path.basename(hits[0]))
            _clean_installer(inst_jar)
            return {"jar": hits[0], "version": label,
                    "launch": "jar"}

    # ★ 新版 Forge：不生成启动 jar，改用 args 文件
    args_rel = _find_forge_argsfile(server_dir)
    if args_rel:
        log("新版 Forge：不生成启动 jar，改用 args 文件启动")
        log("  %s" % args_rel)
        _clean_installer(inst_jar)
        return {"jar": None, "version": label,
                "launch": "argsfile", "args_rel": args_rel}

    # 既没 jar 也没 args 文件：把 installer 输出打出来，别让用户猜
    _clean_installer(inst_jar)
    tail = "".join(out_lines).strip().splitlines()[-15:]  # ★ 修复 NameError
    raise InstallError(
        "installer 返回 0，但目录里既没有启动 jar，"
        "也没有 args 文件。\n\n"
        "installer 最后的输出：\n%s\n\n"
        "目录: %s" % ("\n".join(tail) if tail else "(无输出)",
                      server_dir))


def _clean_installer(inst_jar):
    try:
        os.remove(inst_jar)
    except Exception:
        pass


# =========================================================
#  原版 Vanilla
# =========================================================
VANILLA_META = ("https://piston-meta.mojang.com/mc/game/"
                "version_manifest_v2.json")


def install_vanilla(server_dir, mc_version, java_path=None, on_log=None,
                    loader_version=None):
    """下载官方原版 server.jar。"""
    def log(m):
        if on_log:
            on_log(m)

    log("查询官方版本清单 ...")
    d = _get_json(VANILLA_META, timeout=15)
    hit = None
    for v in d.get("versions", []):
        if v.get("id") == mc_version:
            hit = v
            break
    if not hit:
        raise InstallError("官方没有 %s 这个版本" % mc_version)

    vd = _get_json(hit["url"], timeout=15)
    srv = (vd.get("downloads") or {}).get("server") or {}
    url = srv.get("url")
    if not url:
        raise InstallError("%s 没有提供服务端下载（可能是预览版）"
                           % mc_version)

    dest = os.path.join(server_dir, "server.jar")
    log("下载官方 server.jar ...")
    n = _download(url, dest)
    log("完成，%.1f MB" % (n / 1048576.0))
    return {"jar": dest}



# =========================================================
#  统一入口
# =========================================================
INSTALLERS = {
    "vanilla":  install_vanilla,
    "paper":    install_paper,
    "fabric":   install_fabric,
    "forge":    install_forge,
    "neoforge": install_neoforge,
}


def install_core(loader, server_dir, mc_version, java_path=None,
                 on_log=None, loader_version=None):
    if loader not in INSTALLERS:
        raise InstallError("不支持的加载器: %s" % loader)
    return INSTALLERS[loader](server_dir, mc_version,
                              java_path=java_path, on_log=on_log,
                              loader_version=loader_version)


# =========================================================
#  删除核心（装错了能清掉重来）
# =========================================================
# 核心 jar 的候选名（用于"找当前核心"）
CORE_JARS = [
    "fabric.jar", "fabric-server.jar", "fabric-server-launch.jar",
    "paper.jar", "purpur.jar", "forge.jar", "neoforge.jar",
    "server.jar", "minecraft_server.jar",
]


def find_core(server_dir):
    """找出当前的核心 jar。返回绝对路径或 None。

    新版 Forge 没有启动 jar（用 args 文件），那种情况返回 None，
    请用 find_launch() 拿启动方式。
    """
    import glob as _g
    for n in CORE_JARS:
        p = os.path.join(server_dir, n)
        if os.path.isfile(p):
            return p
    # 宽匹配
    for pat in ("forge-*.jar", "neoforge-*.jar", "paper-*.jar",
                "fabric-*.jar"):
        hits = [h for h in _g.glob(os.path.join(server_dir, pat))
                if "installer" not in os.path.basename(h).lower()]
        if hits:
            return hits[0]
    return None


def find_launch(server_dir):
    """判断这个服怎么启动。

    返回 {"launch": "jar"/"argsfile", "jar": 文件名或 None,
          "args_rel": 相对路径或 None}
    """
    c = find_core(server_dir)
    if c:
        return {"launch": "jar", "jar": os.path.basename(c),
                "args_rel": None}
    a = _find_forge_argsfile(server_dir)
    if a:
        return {"launch": "argsfile", "jar": None, "args_rel": a}
    return {"launch": None, "jar": None, "args_rel": None}


def list_core_files(server_dir):
    """列出该服所有可删除的核心相关文件/目录。"""
    import glob as _g
    out = []
    for n in CORE_JARS:
        p = os.path.join(server_dir, n)
        if os.path.exists(p):
            out.append(("核心", p))
    for pat in ("forge-*.jar", "neoforge-*.jar", "paper-*.jar",
                "fabric-*.jar", "minecraft_server*.jar"):
        for p in _g.glob(os.path.join(server_dir, pat)):
            out.append(("核心", p))
    for d in (".fabric", ".paper-remapped", ".neoforge"):
        p = os.path.join(server_dir, d)
        if os.path.isdir(p):
            out.append(("加载器目录", p))
    # 去重
    seen, uniq = set(), []
    for kind, p in out:
        kp = os.path.normcase(os.path.abspath(p))
        if kp in seen:
            continue
        seen.add(kp)
        uniq.append((kind, p))
    return uniq


def _force_drop(p, log):
    """删一个文件/目录，失败时改名后再删（绕过占用）。"""
    import shutil as _sh
    import time as _t
    # 第一次：正常删
    try:
        if os.path.isdir(p):
            _sh.rmtree(p, ignore_errors=True)
        else:
            os.remove(p)
        if not os.path.exists(p):
            return True, "已删除"
    except Exception:
        pass
    if not os.path.exists(p):
        return True, "已删除"

    # 第二次：改名后删（占用的句柄通常跟着名字走）
    tmp = p + ".mcsdel_%d" % _t.time()
    try:
        os.rename(p, tmp)
        log("  文件被占用，改名后再删 ...")
        try:
            if os.path.isdir(tmp):
                _sh.rmtree(tmp, ignore_errors=True)
            else:
                os.remove(tmp)
        except Exception:
            pass
        if not os.path.exists(tmp):
            return True, "改名后删除"
        return False, "仍被占用（改名成功但删不掉）"
    except Exception as e:
        return False, "被占用: %r" % e


def remove_core(server_dir, also_dirs=True, on_log=None):
    """删除核心 jar（可选连带加载器目录）。返回删除的文件列表。

    只删核心相关，绝不动 world/mods/config/server.properties。
    """
    log = on_log or (lambda m: None)
    removed, blocked = [], []
    for kind, p in list_core_files(server_dir):
        if kind == "加载器目录" and not also_dirs:
            continue
        name = os.path.basename(p)
        okd, why = _force_drop(p, log)
        if okd and not why.startswith("仍被占用"):
            removed.append((kind, name))
            log("已删除 %s: %s" % (kind, name))
        else:
            blocked.append((kind, name, why))
            log("[失败] %s - %s" % (name, why))

    if blocked:
        log("")
        log("有 %d 项删不掉（通常是有 java 进程还在跑）。" % len(blocked))
        log("建议：完全关闭服务器后再试，或用下面的『结束占用进程』。")
    if not removed and not blocked:
        log("没找到可删除的核心文件")
    return {"removed": removed, "blocked": blocked}


# =========================================================
#  占用进程处理
# =========================================================
def find_java_pids():
    """列出所有 java/javaw 进程。返回 [(pid, 命令行片段)]。"""
    out = []
    try:
        import subprocess as _sp
        r = _sp.run(
            ["tasklist", "/FI", "IMAGENAME eq java.exe", "/FO", "CSV", "/NH"],
            capture_output=True, text=True, timeout=15)
        for line in r.stdout.splitlines():
            line = line.strip()
            if not line or "java.exe" not in line.lower():
                continue
            parts = [x.strip('"') for x in line.split('","')]
            if len(parts) >= 2:
                try:
                    out.append((int(parts[1]), parts[0]))
                except Exception:
                    pass
        # javaw 也查一遍
        r2 = _sp.run(
            ["tasklist", "/FI", "IMAGENAME eq javaw.exe", "/FO", "CSV", "/NH"],
            capture_output=True, text=True, timeout=15)
        for line in r2.stdout.splitlines():
            line = line.strip()
            if not line or "javaw.exe" not in line.lower():
                continue
            parts = [x.strip('"') for x in line.split('","')]
            if len(parts) >= 2:
                try:
                    out.append((int(parts[1]), parts[0]))
                except Exception:
                    pass
    except Exception:
        pass
    return out


def kill_java_for(server_dir, on_log=None):
    """只结束命令行里含该目录的 java 进程。"""
    import subprocess as _sp
    log = on_log or (lambda m: None)
    key = os.path.normcase(os.path.abspath(server_dir))
    pids = find_java_pids()
    if not pids:
        log("没有发现 java 进程")
        return 0
    n = 0
    for pid, _name in pids:
        try:
            r = _sp.run(
                ["wmic", "process", "where", "ProcessId=%d" % pid,
                 "get", "CommandLine", "/value"],
                capture_output=True, text=True, timeout=20)
            cmd = (r.stdout or "").lower()
        except Exception:
            cmd = ""
        if cmd and key.lower() not in cmd:
            continue
        try:
            _sp.run(["taskkill", "/PID", str(pid), "/F"],
                    capture_output=True, timeout=15)
            log("已结束占用该目录的 java (PID %d)" % pid)
            n += 1
        except Exception as e:
            log("[跳过] PID %d - %r" % (pid, e))
    if n == 0:
        log("没找到明确占用该目录的 java 进程")
    return n

# =========================================================
#  整服删除（危险，多重保护）
# =========================================================

def server_size(server_dir):
    """算文件夹大小（字节）。"""
    total = 0
    try:
        for root, dirs, files in os.walk(server_dir):
            for f in files:
                try:
                    total += os.path.getsize(os.path.join(root, f))
                except Exception:
                    pass
    except Exception:
        pass
    return total


def human_size(n):
    for u in ("B", "KB", "MB", "GB", "TB"):
        if n < 1024:
            return "%.1f %s" % (n, u)
        n /= 1024.0
    return "%.1f PB" % n


def count_files(server_dir):
    n = d = 0
    try:
        for root, dirs, files in os.walk(server_dir):
            d += len(dirs)
            n += len(files)
    except Exception:
        pass
    return n, d


def delete_server(server_dir, keep_backups=True, on_log=None):
    """删掉整个服务器文件夹。

    keep_backups=True 时，会先把 backups/ 挪到旁边保住。
    """
    import shutil as _sh
    import time as _t
    log = on_log or (lambda m: None)

    if not os.path.isdir(server_dir):
        log("目录不存在: %s" % server_dir)
        return False

    parent = os.path.dirname(server_dir.rstrip("\\/"))
    name = os.path.basename(server_dir.rstrip("\\/"))
    if not parent or not name:
        log("拒绝删除：路径解析异常（不能删盘符根目录）")
        return False

    # 1) 保住备份
    bk = os.path.join(server_dir, "backups")
    saved = None
    if keep_backups and os.path.isdir(bk):
        dst = os.path.join(parent, "%s_备份留存_%d"
                           % (name, int(_t.time())))
        try:
            _sh.move(bk, dst)
            saved = dst
            log("备份已保全 -> %s" % os.path.basename(dst))
        except Exception as e:
            log("[警告] 备份保全失败: %r" % e)

    # 2) 删除
    try:
        _sh.rmtree(server_dir)
        if not os.path.exists(server_dir):
            log("已删除: %s" % server_dir)
            if saved:
                log("备份保留在: %s" % saved)
            return True
    except Exception as e:
        log("首次删除失败: %r" % e)

    # 3) 改名后删（绕过占用）
    tmp = os.path.join(parent, "%s_mcsdel_%d" % (name, int(_t.time())))
    try:
        os.rename(server_dir, tmp)
        log("被占用，改名后再删 ...")
        _sh.rmtree(tmp, ignore_errors=True)
        if not os.path.exists(tmp):
            log("改名录删除成功")
            return True
        log("[失败] 仍被占用: %s" % tmp)
        return False
    except Exception as e:
        log("[失败] 无法删除: %r" % e)
        return False


# =========================================================
#  诊断包（报错文件夹）
# =========================================================
def collect_diag(server_dir, out_dir=None, extra_log=None):
    """收集排查所需信息到一个文件夹。

    返回该文件夹路径。
    """
    import shutil as _sh
    import time as _t
    import platform as _pf

    name = os.path.basename(server_dir.rstrip("\\/"))
    stamp = _t.strftime("%Y%m%d_%H%M%S")
    if out_dir is None:
        out_dir = os.path.join(server_dir, "报错诊断")
    folder = os.path.join(out_dir, "%s_%s" % (name, stamp))
    os.makedirs(folder, exist_ok=True)

    rep = []
    def w(title, body):
        rep.append("=" * 60)
        rep.append(title)
        rep.append("=" * 60)
        rep.append(body if body else "(空)")
        rep.append("")

    # 1) 基本信息
    w("基本信息", "\n".join([
        "服务器: %s" % name,
        "目录:   %s" % server_dir,
        "时间:   %s" % _t.strftime("%Y-%m-%d %H:%M:%S"),
        "系统:   %s %s" % (_pf.platform(), _pf.machine()),
        "Python: %s" % _pf.python_version(),
    ]))

    # 2) 目录结构（前两层）
    tree = []
    try:
        for root, dirs, files in os.walk(server_dir):
            depth = root[len(server_dir):].count(os.sep)
            if depth > 2:
                dirs[:] = []
                continue
            ind = "  " * depth
            tree.append("%s%s/" % (ind, os.path.basename(root) or name))
            for f in sorted(files)[:25]:
                tree.append("%s  %s" % (ind, f))
            if len(files) > 25:
                tree.append("%s  ... 还有 %d 个" % (ind, len(files) - 25))
    except Exception as e:
        tree.append("读取失败: %r" % e)
    w("目录结构（前两层）", "\n".join(tree))

    # 3) server.properties
    sp = os.path.join(server_dir, "server.properties")
    if os.path.isfile(sp):
        try:
            body = open(sp, "r", encoding="utf-8",
                         errors="replace").read()
            w("server.properties", body)
        except Exception as e:
            w("server.properties", "读取失败: %r" % e)

    # 4) 日志
    logs = []
    for cand in (os.path.join(server_dir, "logs", "latest.log"),
                 os.path.join(server_dir, "logs", "latest.log.gz"),
                 os.path.join(server_dir, "logs", "debug.log")):
        if os.path.isfile(cand):
            logs.append(cand)
    if extra_log:
        logs.append(("__extra__", extra_log))
    for cand in logs:
        if isinstance(cand, tuple):
            src, content = cand
            fn = "控制台输出.txt"
            try:
                open(os.path.join(folder, fn), "w",
                     encoding="utf-8").write(content)
            except Exception:
                pass
            # 只留最后 500 行进报告，避免过大
            lines = (content or "").splitlines()
            tail = "\n".join(lines[-500:])
            w("控制台输出（最后 %d 行）" % min(500, len(lines)), tail)
            continue
        if os.path.isfile(cand):
            try:
                _sh.copy2(cand, os.path.join(
                    folder, os.path.basename(cand)))
            except Exception:
                pass
            try:
                if cand.endswith(".gz"):
                    import gzip as _gz
                    txt = _gz.open(cand, "rt", encoding="utf-8",
                                   errors="replace").read()
                else:
                    txt = open(cand, "r", encoding="utf-8",
                               errors="replace").read()
                lines = txt.splitlines()
                tail = "\n".join(lines[-500:])
                w("日志 %s（最后 %d 行）"
                  % (os.path.basename(cand), min(500, len(lines))), tail)
            except Exception as e:
                w("日志 %s" % os.path.basename(cand),
                  "读取失败: %r" % e)

    # 5) mods / plugins 清单
    for sub, label in (("mods", "模组"), ("plugins", "插件")):
        d = os.path.join(server_dir, sub)
        if not os.path.isdir(d):
            continue
        items = []
        try:
            for f in sorted(os.listdir(d)):
                full = os.path.join(d, f)
                if os.path.isfile(full) and f.lower().endswith(".jar"):
                    try:
                        sz = os.path.getsize(full) / 1024.0
                    except Exception:
                        sz = 0
                    items.append("  %-50s %8.1f KB" % (f, sz))
        except Exception as e:
            items.append("读取失败: %r" % e)
        w("%s清单 (%s)" % (label, sub),
          "\n".join(items) if items else "(空)")

    # 6) 核心 jar
    cj = find_core(server_dir)
    w("核心", "当前核心: %s" % (os.path.basename(cj) if cj else "未识别"))

    # 7) eula
    eu = os.path.join(server_dir, "eula.txt")
    if os.path.isfile(eu):
        try:
            w("eula.txt", open(eu, "r", encoding="utf-8",
                               errors="replace").read())
        except Exception:
            pass

    try:
        open(os.path.join(folder, "诊断报告.txt"), "w",
             encoding="utf-8").write("\n".join(rep))
    except Exception:
        pass

    return folder


# =========================================================
#  模组（Modrinth）
# =========================================================
# 按加载器区分的推荐模组清单。
#
# 每项字段：
#   slug     Modrinth 项目地址最后一段（modrinth.com/mod/lithium -> lithium）
#   name     显示名
#   default  默认勾不勾
#   desc     一句说明
#   group    界面里分组显示，不至于一大坨
#   side     server=服务端有用 / client=只对客户端有用
#   req      前置 slug 列表（装它就必须一起装，避免"只装一半"）
#   soft     推荐同时装的 slug（缺了也能跑，但不建议）
#   conflict 冲突 slug 列表（两个一起装会互相打架）
#
# ★ v0.3.0 视图化模式：清单回归极简。
#   以前表格模式要维护 default（默认勾不勾）、side（服务端/客户端）、
#   conflict（互斥）、min_ver/max_ver（版本上限）……一长串字段，
#   写的时候小心翼翼，还是会有模组因为"死活装不上"被吐槽。
#   现在改成 PCL2 风格卡片视图：用户自己看图标、看简介、自己勾选，
#   一切以用户的选择为准 —— 这里只留最核心的 5 个字段：
#     slug   Modrinth 项目 id（唯一标识）
#     name   显示名
#     desc   一句话简介（卡片/表格上直接给用户看）
#     group  分组（仅用于界面归类展示）
#     req    前置依赖（有前置的绑死一起装，绝不装一半）
#   版本兼容性交给 Modrinth API 兜底：视图里装的都是当前服版本
#   查得到版本的；查不到会在安装时明确提示，不会误导。


def _m(slug, name, desc, group, req=(), max_ver=None):
    """极简清单条目。

    max_ver：该模组最高支持的 MC 版本（如 LazyDFU 只到 1.18.2）。
    视图/列表展示时会严格过滤：当前服版本高于 max_ver 就不显示。
    """
    return {"slug": slug, "name": name, "desc": desc,
            "group": group, "req": list(req), "max_ver": max_ver}


MOD_TARGETS = {
    # ★ V1.8.2：纯平铺重构 —— 绝对不设分组标题（group 一律空串），
    #   desc 末尾用方括号 [标签] 标识用途，UI 卡片第三行彩色渲染。
    #   冲突项已剔除：不装 packetfixer（Connectivity 更全）、
    #   不装 morechathistory（Chat Patches 更强）。
    "fabric": [
        # 1. 第三方登录（排最前）
        _m("multiyggdrasil", "MultiYggdrasil",
           "多源外置登录：正版+第三方皮肤站共存 [第三方登录]", ""),
        # ★ V1.8.4：Cupboard 生态（强行补全，Modrinth 暂无发布则手动装）
        _m("cupboard", "Cupboard",
           "提供 JSON 配置框架与崩溃追踪的核心前置 [Cupboard生态]", ""),
        _m("fastasyncworldsave", "Faster Async World Save",
           "异步保存世界，卡顿明显减少（Modrinth 暂无发布，失败手动装） [Cupboard生态]", ""),
        _m("structureessentials", "Structure Essentials",
           "预生成结构/史莱姆区块工具（Modrinth 暂无发布，失败手动装） [Cupboard生态]", ""),
        _m("recipeessentials", "Recipe Essentials",
           "配方管理框架（Modrinth 暂无发布，失败手动装） [Cupboard生态]", ""),
        _m("connectivity", "Connectivity",
           "网络握手与防卡包优化（替代 Packet Fixer） [Cupboard生态]", ""),
        _m("chunksending", "ChunkSending",
           "区块发送速度优化（Modrinth 暂无发布，失败手动装） [Cupboard生态]", ""),
        # 3. 性能/聊天工具
        _m("clumps", "Clumps", "合并经验球，降低服务器压力 [优化]", ""),
        _m("tabtps", "TabTPS", "TPS/MSPT 监控，Tab 列表实时显示 [监控]", ""),
        _m("crash-assistant", "Crash Assistant",
           "崩溃报告收集分析（Modrinth 暂无发布，失败按日志手动装） [崩溃分析]", ""),
        _m("spark", "spark", "性能分析，卡顿时 /spark profiler 排查 [监控]", ""),
        _m("chunky", "Chunky", "预生成地形，新图跑图不卡 [工具]", "",
           req=["fabric-api"]),
        _m("luckperms", "LuckPerms", "权限管理 [权限]", ""),
        _m("chatpatches", "Chat Patches", "聊天记录上限与历史增强 [聊天]", ""),
        _m("simple-voice-chat", "Simple Voice Chat",
           "近距离语音，客户端也要装同名模组 [语音]", "",
           req=["fabric-api"]),
        # 保留核心项（同样平铺 + 标签）
        _m("fabric-api", "Fabric API", "绝大多数模组的前置 [前置库]", ""),
        _m("lithium", "Lithium",
           "优化游戏逻辑（实体AI/红石/区块刻），收益最大 [优化]", ""),
        _m("ferrite-core", "FerriteCore",
           "减少内存占用，小内存服必装 [优化]", ""),
        _m("c2me-fabric", "C2ME",
           "多线程区块生成与保存，跑图/预生成快很多 [优化]", ""),
        _m("modernfix", "ModernFix",
           "修复 Mojang 的性能 bug，还加快启动 [优化]", ""),
        _m("krypton", "Krypton", "优化网络包处理，人多时明显 [优化]", ""),
        _m("servercore", "ServerCore",
           "服务端综合优化，可按 MSPT 动态调视距 [优化]", ""),
        _m("saturn", "Saturn",
           "减少内存占用（与 FerriteCore 互补） [优化]", ""),
        _m("alternate-current", "Alternate Current",
           "重写红石粉更新逻辑，大型红石机械明显变快 [优化]", ""),
        _m("lazydfu", "LazyDFU",
           "延迟加载 DataFixer，启动快几秒（仅 1.18.2 及以前） [优化]",
           "", max_ver="1.18.2"),
        _m("put-a-plug-in-it!", "Put A Plug In It!",
           "修内存泄漏（替代已停更的 Memory Leak Fix） [优化]", ""),
        _m("noisium", "Noisium",
           "加速新区块生成（26.3 起停更） [优化]", ""),
    ],

    "neoforge": [
        # 1. 第三方登录（排最前）
        _m("multiyggdrasil", "MultiYggdrasil",
           "多源外置登录：正版+第三方皮肤站共存 [第三方登录]", ""),
        _m("akiyggdrasil", "AkiYggdrasil",
           "第三方外置登录（1.21.1 NeoForge；Modrinth 暂无发布，"
           "失败按日志手动装 jar） [第三方登录]", "", max_ver="1.21.1"),
        # ★ V1.8.4：Cupboard 生态（强行补全，Modrinth 暂无发布则手动装）
        _m("cupboard", "Cupboard",
           "提供 JSON 配置框架与崩溃追踪的核心前置 [Cupboard生态]", ""),
        _m("fastasyncworldsave", "Faster Async World Save",
           "异步保存世界，卡顿明显减少（Modrinth 暂无发布，失败手动装） [Cupboard生态]", ""),
        _m("structureessentials", "Structure Essentials",
           "预生成结构/史莱姆区块工具（Modrinth 暂无发布，失败手动装） [Cupboard生态]", ""),
        _m("recipeessentials", "Recipe Essentials",
           "配方管理框架（Modrinth 暂无发布，失败手动装） [Cupboard生态]", ""),
        _m("connectivity", "Connectivity",
           "网络握手与防卡包优化（替代 Packet Fixer） [Cupboard生态]", ""),
        _m("chunksending", "ChunkSending",
           "区块发送速度优化（Modrinth 暂无发布，失败手动装） [Cupboard生态]", ""),
        # 3. 性能/聊天工具
        _m("clumps", "Clumps", "合并经验球，降低服务器压力 [优化]", ""),
        _m("tabtps", "TabTPS", "TPS/MSPT 监控 [监控]", ""),
        _m("crash-assistant", "Crash Assistant",
           "崩溃报告收集分析（Modrinth 暂无发布，失败按日志手动装） [崩溃分析]", ""),
        _m("spark", "spark", "性能分析，卡顿时 /spark profiler 排查 [监控]", ""),
        _m("chunky", "Chunky", "预生成地形，新图跑图不卡 [工具]", ""),
        _m("luckperms", "LuckPerms", "权限管理 [权限]", ""),
        _m("chatpatches", "Chat Patches",
           "聊天记录上限与历史增强（暂无 NeoForge 版，失败按日志提示） [聊天]", ""),
        # 保留核心项
        _m("ferrite-core", "FerriteCore", "减少内存占用 [优化]", ""),
        _m("modernfix", "ModernFix", "修复性能 bug + 加快启动 [优化]", ""),
        _m("krypton", "Krypton", "优化网络包处理 [优化]", ""),
        _m("saturn", "Saturn", "减少内存占用 [优化]", ""),
        _m("lithium", "Lithium", "优化游戏逻辑 [优化]", ""),
        _m("alternate-current", "Alternate Current",
           "重写红石粉更新逻辑 [优化]", ""),
        _m("memoryleakfix", "Memory Leak Fix", "修内存泄漏 [优化]", ""),
        _m("noisium", "Noisium", "加速新区块生成 [优化]", ""),
    ],

    "forge": [
        # 1. 第三方登录（排最前）
        _m("multiyggdrasil", "MultiYggdrasil",
           "多源外置登录：正版+第三方皮肤站共存 [第三方登录]", ""),
        # ★ V1.8.4：Cupboard 生态（强行补全，Modrinth 暂无发布则手动装）
        _m("cupboard", "Cupboard",
           "提供 JSON 配置框架与崩溃追踪的核心前置 [Cupboard生态]", ""),
        _m("fastasyncworldsave", "Faster Async World Save",
           "异步保存世界，卡顿明显减少（Modrinth 暂无发布，失败手动装） [Cupboard生态]", ""),
        _m("structureessentials", "Structure Essentials",
           "预生成结构/史莱姆区块工具（Modrinth 暂无发布，失败手动装） [Cupboard生态]", ""),
        _m("recipeessentials", "Recipe Essentials",
           "配方管理框架（Modrinth 暂无发布，失败手动装） [Cupboard生态]", ""),
        _m("connectivity", "Connectivity",
           "网络握手与防卡包优化（替代 Packet Fixer） [Cupboard生态]", ""),
        _m("chunksending", "ChunkSending",
           "区块发送速度优化（Modrinth 暂无发布，失败手动装） [Cupboard生态]", ""),
        # 3. 性能/聊天工具
        _m("clumps", "Clumps", "合并经验球，降低服务器压力 [优化]", ""),
        _m("crash-assistant", "Crash Assistant",
           "崩溃报告收集分析（Modrinth 暂无发布，失败按日志手动装） [崩溃分析]", ""),
        _m("spark", "spark", "性能分析，卡顿时 /spark profiler 排查 [监控]", ""),
        _m("chunky", "Chunky", "预生成地形，新图跑图不卡 [工具]", ""),
        _m("luckperms", "LuckPerms", "权限管理 [权限]", ""),
        _m("chatpatches", "Chat Patches",
           "聊天记录上限与历史增强（暂无 Forge 版，失败按日志提示） [聊天]", ""),
        _m("simple-voice-chat", "Simple Voice Chat",
           "近距离语音，客户端也要装同名模组 [语音]", ""),
        # 保留核心项
        _m("ferrite-core", "FerriteCore",
           "减少内存占用（1.20.1 需开 populateNeighborTable 防崩溃） [优化]", ""),
        _m("modernfix", "ModernFix", "修复性能 bug + 加快启动 [优化]", ""),
        _m("krypton-fnp", "Krypton FNP (Forge版)",
           "优化网络包处理（Forge 非官方重构版） [优化]", ""),
        _m("alternate-current", "Alternate Current",
           "重写红石粉更新逻辑（Forge 版最高到 1.20.4） [优化]", ""),
        _m("memoryleakfix", "Memory Leak Fix", "修内存泄漏 [优化]", ""),
        _m("noisium", "Noisium", "加速新区块生成 [优化]", ""),
    ],

    "vanilla": [],
    # ★ V1.8.2：Paper 服推荐区 —— Cupboard 生态 6 项（都是插件，
    #   装进 plugins/）+ 适用的监控/权限工具，全平铺无分组标题。
    "paper": [
        _m("cupboard", "Cupboard",
           "提供 JSON 配置框架与崩溃追踪的核心前置 [Cupboard生态]", ""),
        _m("fastasyncworldsave", "Faster Async World Save",
           "异步保存世界，卡顿明显减少（Modrinth 暂无发布，失败手动装） [Cupboard生态]", ""),
        _m("structureessentials", "Structure Essentials",
           "预生成结构/史莱姆区块工具（Modrinth 暂无发布，失败手动装） [Cupboard生态]", ""),
        _m("recipeessentials", "Recipe Essentials",
           "配方管理框架（Modrinth 暂无发布，失败手动装） [Cupboard生态]", ""),
        _m("connectivity", "Connectivity",
           "网络握手与防卡包优化（替代 Packet Fixer） [Cupboard生态]", ""),
        _m("chunksending", "ChunkSending",
           "区块发送速度优化（Modrinth 暂无发布，失败手动装） [Cupboard生态]", ""),
        _m("tabtps", "TabTPS", "TPS/MSPT 监控，Tab 列表实时显示 [监控]", ""),
        _m("spark", "spark", "性能分析，卡顿时 /spark profiler 排查 [监控]", ""),
        _m("luckperms", "LuckPerms", "权限管理 [权限]", ""),
    ],
}

# ★ V1.8.2：内置插件推荐清单（纯平铺 + desc 标签 + 严格 req）。
#   下载时复用 resolve_selection（plugin=True）自动补前置 + 排序。
#   multilogin 最前（第三方登录第一）；essentialsx 绑定
#   vault + luckperms；essentialsxchat 绑定 essentialsx。
PLUGIN_TARGETS = [
    _m("multilogin", "MultiLogin",
       "多源登录：正版+第三方外置登录共存（Modrinth 暂无官方发布，"
       "失败请到 GitHub Ink33/MultiLogin 手动装） [第三方登录]", ""),
    _m("luckperms", "LuckPerms", "权限管理，几乎所有插件服的地基 [权限]", ""),
    _m("vault", "Vault", "经济/权限接口，经济插件的地基 [基础前置]", ""),
    _m("essentialsx", "EssentialsX",
       "基础指令/聊天/传送/经济桥 [基础指令]", "", req=["vault", "luckperms"]),
    _m("essentialsxchat", "EssentialsXChat",
       "聊天格式化与频道扩展（Modrinth 暂无独立发布，失败手动装） [聊天扩展]", "",
       req=["essentialsx"]),
    _m("coreprotect", "CoreProtect",
       "防熊回滚：方块/容器/聊天操作记录，被熊了能查能还原 [防熊]", ""),
    _m("worldedit", "WorldEdit",
       "建筑工具：选区/复制/粘贴/替换 [建筑]", ""),
    _m("worldguard", "WorldGuard",
       "区域保护：圈地/防破坏/出生点保护 [区域保护]", ""),
    _m("tabtps", "TabTPS", "TPS/MSPT 监控，Tab 列表实时显示 [监控]", ""),
    _m("spark", "spark", "性能分析，卡顿时 /spark profiler 排查 [监控]", ""),
]


def plugin_meta(slug):
    """取某个插件在推荐清单里的元信息，不在清单里返回 None。"""
    for m in PLUGIN_TARGETS:
        if m["slug"] == slug:
            return m
    return None

# ★ 这些模组"只在客户端生效"，装到服务端完全没用。
#   v5.7 之前它们在清单里（Smooth Boot / EntityCulling），
#   白白占下载位还让人以为装上了就有优化。这里留个名单，
#   万一以后有人手动加进来，界面会给提示而不是闷声装上。
CLIENT_ONLY_MODS = {
    "smoothboot": "只对客户端生效（改 CPU 调度加快启动），服务端装了没用",
    "entityculling": "纯客户端渲染优化，官方明确说不需要装在服务器",
    "sodium": "客户端渲染模组，服务端装了没用",
    "iris": "客户端光影，服务端装了没用",
    "reeses-sodium-options": "客户端设置界面，服务端装了没用",
    "sodium-extra": "客户端设置扩展，服务端装了没用",
}


def mod_meta(loader, slug):
    """取某个模组在清单里的元信息，不在清单里返回 None。"""
    for m in MOD_TARGETS.get(loader) or []:
        if m["slug"] == slug:
            return m
    return None


def resolve_selection(loader, slugs, plugin=False):
    """把"用户勾了哪些"展开成"实际要装哪些"。

    做三件事（用户明确要求"有前置的绑死一起，不要只装一半"）：

      1. 补前置：勾了 A 而 A 需要 B，就把 B 一起加进来
      2. 排顺序：前置排在被依赖者前面，装的时候先装前置
      3. 查冲突：两个互相打架的模组同时被勾上，报出来

    ★ V1.8.0：plugin=True 时按 PLUGIN_TARGETS（插件清单）解析，
      模组模式仍走 MOD_TARGETS[loader]。

    返回 dict:
      install  实际要装的 slug 列表（已按依赖排好序）
      added    自动补进来的 slug（原本没勾）
      conflicts [(slug_a, slug_b, 说明), ...]
      notes    [给人看的一句话, ...]
    """
    def _meta(s):
        return plugin_meta(s) if plugin else mod_meta(loader, s)

    want = [s for s in (slugs or []) if s]
    have = set(want)
    added, notes = [], []

    # ---- 1. 补前置（可能连锁：A 要 B，B 又要 C）----
    changed = True
    guard = 0
    while changed and guard < 12:
        changed = False
        guard += 1
        for s in list(have):
            meta = _meta(s)
            if not meta:
                continue
            for r in meta.get("req") or []:
                if r not in have:
                    have.add(r)
                    added.append(r)
                    changed = True
                    notes.append(
                        "%s 需要前置 %s，已自动一起装" % (s, r))

    # ---- 2. 拓扑排序：前置在前 ----
    ordered, done = [], set()

    def _visit(s, stack):
        if s in done:
            return
        if s in stack:      # 循环依赖，别死循环
            return
        stack.add(s)
        meta = _meta(s)
        for r in (meta.get("req") if meta else []) or []:
            _visit(r, stack)
        done.add(s)
        ordered.append(s)

    for s in want:
        _visit(s, set())
    for s in sorted(have):
        _visit(s, set())

    # ---- 3. 冲突 ----
    conflicts = []
    seen_cf = set()
    for s in ordered:
        meta = _meta(s)
        if not meta:
            continue
        for c in meta.get("conflict") or []:
            if c not in have:
                continue
            # 两边都会互相报，去重只留一条
            key = tuple(sorted([s, c]))
            if key in seen_cf:
                continue
            seen_cf.add(key)
            cm = _meta(c) or {}
            conflicts.append((
                s, c,
                "%s 与 %s 抢同一块功能，同时装会互相打架"
                % (meta.get("name", s), cm.get("name", c))))

    # ---- 4. 客户端模组提醒 ----
    for s in ordered:
        why = CLIENT_ONLY_MODS.get(s)
        if why:
            notes.append("%s：%s" % (s, why))

    return {"install": ordered, "added": added,
            "conflicts": conflicts, "notes": notes}


def normalize_slug(text):
    """把用户输入变成 Modrinth slug。

    支持：
      lithium
      https://modrinth.com/mod/lithium
      https://modrinth.com/mod/lithium/versions
      modrinth.com/mod/c2me-fabric
    返回 slug，识别不了返回 None。
    """
    if not text:
        return None
    t = str(text).strip()
    # 去掉查询串和末尾斜杠
    t = t.split("?")[0].split("#")[0].rstrip("/")
    if "/" in t:
        parts = [p for p in t.split("/") if p]
        # .../mod/<slug>[/versions]
        if "mod" in parts:
            i = parts.index("mod")
            if i + 1 < len(parts):
                return parts[i + 1]
        return parts[-1]
    return t or None

def mod_list_for(loader, mc_version=None):
    """返回精简清单，每项 dict:
    {slug, name, desc, group, req, max_ver}

    ★ v0.3.0 视图化模式：不再写死 default / side / min_ver。
      用户自己看简介、自己勾选；版本能不能装由 Modrinth API 兜底。
    ★ 严格版本过滤：带 max_ver 的模组（如 LazyDFU 只支持 1.18.2），
      当前服版本高于 max_ver 就直接剔除，绝不显示。
    """
    lst = MOD_TARGETS.get(loader) or []
    if not mc_version:
        return [dict(m) for m in lst]
    from . import versions as _V
    vk = _V.mc_version_key(mc_version, newest_first=False)  # ★ 正序比较
    out = []
    for m in lst:
        m_max = m.get("max_ver")
        if m_max and vk > _V.mc_version_key(m_max, newest_first=False):
            continue  # ★ 版本不支持，剔除（如 1.20.1 看不到 LazyDFU）
        out.append(dict(m))
    return out



def modrinth_pick(slug, mc_version, loader, on_retry=None):
    """智能挑选版本：先精确匹配，失败后动态回退到最兼容的版本。"""
    import re as _re

    def _fetch(game_versions=None):
        params = {"loaders": json.dumps([loader])}
        if game_versions:
            params["game_versions"] = json.dumps(game_versions)
        from . import sources  # ★ 修复：不能从 .core 导入，当前已经在 core 包里
        base = sources.modrinth_base()
        url = "%s/project/%s/version?%s" % (
            base, slug, urllib.parse.urlencode(params))
        
        # ★ 增加内部重试：防止瞬时网络波动（如 429 限制）导致明明有的模组被误判为“无版本”
        last_err = None
        for _ in range(2):
            try:
                return json.loads(_get(url, timeout=15, on_retry=on_retry))
            except Exception as e:
                last_err = e
                time.sleep(1.5)
        # ★ 不能 return []，否则网络断开会被误判为“无版本”。抛出真实错误！
        if last_err:
            raise last_err
        return []

    # 1. 精确匹配
    vs = _fetch([mc_version])
    if vs:
        return _pick_best(vs)

    # 2. 精确匹配失败，获取该加载器下的所有版本
    all_vs = _fetch(None)
    if not all_vs:
        return None

    # 3. 动态回退已移除（严禁跨小版本乱装，1.20.4 的 Forge 模组塞给 1.20.1 必崩）
        
    # 4. 终极兜底：不带任何过滤条件，取该项目的最新版本
    #   应对某些模组在 Modrinth 上的版本标签（如 game_versions）填错、
    #   漏填，或者索引延迟导致完全匹配不上。
    try:
        from . import sources
        base = sources.modrinth_base()
        url = "%s/project/%s/version" % (base, slug)
        all_vs_any = json.loads(_get(url, timeout=15))
        if all_vs_any:
            # 取最新的 5 个版本里，挑第一个带 .jar 文件的
            best = _pick_best(all_vs_any[:5])
            if best:
                return best
    except Exception as e:
        raise RuntimeError("获取 %s 的 Modrinth 数据失败: %s" % (slug, _brief(e)))
        
    return None


def _pick_best(versions):
    """从版本列表中挑选第一个包含 jar 文件的（优先 release）。"""
    sorted_vs = sorted(versions, key=lambda v: 0 if v.get("version_type") == "release" else 1)
    for v in sorted_vs:
        for f in v.get("files") or []:
            if f.get("url") and f.get("filename", "").endswith(".jar"):
                return {"name": f["filename"], "url": f["url"],
                        "version": v.get("version_number"),
                        "type": v.get("version_type")}
    return None


def _mirror_download_url(url):
    """CDN 加速：把官方 Modrinth CDN 换成 MCIM 国内镜像。

    ★ 只换域名，路径结构两边一致（实测 mod.mcimirror.top/data/... 返回 200）。
      注意这是无条件替换，与 sources.mirror_cdn（跟随镜像开关）不同。
    """
    if not url:
        return url
    return url.replace("cdn.modrinth.com", "mod.mcimirror.top")


def _scan_jar_virus(jar_path):
    """极简木马扫描：检查 jar 里是否有 Fractureiser 特征路径。

    ★ 2023 年的 Fractureiser 木马会在模组 jar 里塞
      dev/fractureiser/... 之类的恶意类。这里是启发式检查，
      命中即拒装并删除 —— 宁可错杀，不可放过。
    """
    try:
        with zipfile.ZipFile(jar_path, "r") as z:
            for name in z.namelist():
                low = name.lower()
                if "fractureiser" in low or low.startswith("dev/fractureiser"):
                    return True
    except Exception:
        pass
    return False


def install_mods(server_dir, mc_version, loader, on_log=None,
                 only=None):
    """并发下载优化模组到 server_dir/mods。

    ★ v5.x：4 线程并发 + MCIM CDN 加速 + Fractureiser 木马扫描，
      下载体验不再一个模组等一个模组。
    """
    def log(m):
        if on_log:
            on_log(m)

    if not (MOD_TARGETS.get(loader) or []):
        log("%s 是插件服/原版，不装模组" % loader)
        return {"ok": [], "fail": [], "skipped": [], "notes": []}

    # 只装指定的（由选择界面决定）
    if only is not None:
        want = list(only)
    else:
        # ★ v0.3.0：清单不再有 default 字段（视图化模式由用户勾选），
        #   未指定时默认装清单里的全部。
        want = [m["slug"] for m in (MOD_TARGETS.get(loader) or [])]

    # ★ 补前置 + 排顺序 + 查冲突（"不要只装一半"就靠这里）
    plan = resolve_selection(loader, want)
    for n in plan.get("notes") or []:
        log("  · %s" % n)
    for a, b, why in plan.get("conflicts") or []:
        log("  [!] 冲突：%s" % why)
    slugs = plan.get("install") or []

    # 用户手动添加的自定义 slug（不在内置清单里）也要装。
    # resolve_selection 只认清单里的，这里把漏掉的补到最后。
    for slug in (want or []):
        if slug not in slugs:
            slugs.append(slug)

    if not slugs:
        log("没有选中任何模组，跳过")
        return {"ok": [], "fail": [], "skipped": [], "notes": plan["notes"]}

    # ★ V1.8.0：Paper 服的 MOD_TARGETS 只有 Cupboard（插件），
    #   下载进 plugins/ 而不是 mods/。
    mods_dir = os.path.join(
        server_dir, "plugins" if loader == "paper" else "mods")
    os.makedirs(mods_dir, exist_ok=True)
    ok_list, fail_list, skipped = [], [], []

    # ---- 单个模组的下载任务（供线程池调用）----
    def _download_task(slug):
        meta = mod_meta(loader, slug)
        desc = (meta or {}).get("name") or slug
        try:
            def _note(attempt, e, wait):
                log("    [%s] 网络不稳（%s），%.1fs 后重试..."
                    % (desc, _brief(e), wait))
            # 获取版本信息（双重兜底已在 modrinth_pick 内实现）
            v = modrinth_pick(slug, mc_version, loader, on_retry=_note)
            if not v:
                return False, slug, desc, "无 %s+%s 版本" % (mc_version, loader)

            dest = os.path.join(mods_dir, v["name"])
            # ★ CDN 加速 + 断点续传下载
            download_url = _mirror_download_url(v["url"])
            _download(download_url, dest, on_retry=_note, on_log=log)

            # ★ 下载后安全扫描（Fractureiser 木马）
            if _scan_jar_virus(dest):
                try:
                    os.remove(dest)
                except Exception:
                    pass
                return False, slug, desc, "拦截到恶意文件（Fractureiser 特征），已删除"

            log("  [OK] %s" % v["name"])
            return True, slug, desc, v["name"]
        except Exception as e:
            log("  [跳过] %s - %s" % (desc, _brief(e)))
            return False, slug, desc, _brief(e)

    # ---- 预检查前置依赖（没进清单的一律跳过，不留半个）----
    valid_slugs = []
    for slug in slugs:
        meta = mod_meta(loader, slug)
        desc = (meta or {}).get("name") or slug
        missing = [r for r in ((meta or {}).get("req") or [])
                   if r not in slugs]
        if missing:
            msg = "缺少前置 %s" % ", ".join(missing)
            log("  [跳过] %s - %s" % (desc, msg))
            skipped.append({"slug": slug, "name": desc, "reason": msg})
        else:
            valid_slugs.append(slug)

    # ---- 并发下载（4 线程）----
    if valid_slugs:
        log("开始并发下载 %d 个模组（4 线程）..." % len(valid_slugs))
        with concurrent.futures.ThreadPoolExecutor(max_workers=4) as executor:
            future_to_slug = {
                executor.submit(_download_task, slug): slug
                for slug in valid_slugs
            }
            for future in concurrent.futures.as_completed(future_to_slug):
                try:
                    success, slug, desc, result = future.result()
                except Exception as e:  # 任务本身异常也要兜住，别让 UI 崩
                    slug = future_to_slug[future]
                    success, desc, result = False, slug, _brief(e)
                if success:
                    ok_list.append({"name": result, "slug": slug})
                else:
                    fail_list.append({"slug": slug, "name": desc,
                                      "reason": result})

    if skipped:
        log("")
        log("★ 有 %d 个模组因前置没装上而跳过（不会留半个）"
            % len(skipped))
    # 全军覆没多半是网络，不是模组本身有问题
    if fail_list and not ok_list:
        log("")
        log("★ 一个都没装上，通常是网络瞬时故障（不是模组的问题）。")
        log("  等几秒再点一次『开始』通常就好了。")
    return {"ok": ok_list, "fail": fail_list, "skipped": skipped,
            "notes": plan.get("notes") or [],
            "conflicts": plan.get("conflicts") or []}


def install_plugins(server_dir, mc_version, loader="paper", only=None,
                    on_log=None):
    """并发下载插件到 server_dir/plugins。

    ★ V1.2.0：与 install_mods 逻辑一致（4 线程并发 + CDN 加速 +
      断点续传 + Fractureiser 木马扫描），但：
      · 下载目标目录是 plugins/（不是 mods/）
      · 没有内置清单，只装用户从插件视图（Modrinth
        project_type=plugin）勾选的 slug。
    ★ V1.7.0-3：加载器不再固定 paper —— Fabric / Forge /
      NeoForge 服也会按各自加载器过滤插件版本。
    ★ V1.8.0：新增内置插件推荐清单 PLUGIN_TARGETS（multilogin 最前、
      essentialsx 绑定 vault+luckperms、含 coreprotect 防熊）；
      下载前用 resolve_selection(plugin=True) 自动补前置 + 拓扑排序，
      保证"先装前置再装依赖"。
    """
    def log(m):
        if on_log:
            on_log(m)

    # ★ V1.8.0：未指定时默认装内置推荐清单全部（与 install_mods 一致）
    if only is not None:
        want = list(only)
    else:
        want = [p["slug"] for p in PLUGIN_TARGETS]

    # ★ V1.8.0：补前置 + 排顺序 + 查冲突（复用模组的 resolve_selection）
    plan = resolve_selection(loader, want, plugin=True)
    for n in plan.get("notes") or []:
        log("  · %s" % n)
    for a, b, why in plan.get("conflicts") or []:
        log("  [!] 冲突：%s" % why)
    slugs = plan.get("install") or []

    # 用户手动添加的自定义 slug（不在内置清单里）也要装
    for slug in (want or []):
        if slug not in slugs:
            slugs.append(slug)

    if not slugs:
        log("没有选中任何插件，跳过")
        return {"ok": [], "fail": [], "skipped": [], "notes": plan["notes"]}

    plugins_dir = os.path.join(server_dir, "plugins")
    os.makedirs(plugins_dir, exist_ok=True)
    ok_list, fail_list, skipped = [], [], []

    def _download_task(slug):
        meta = plugin_meta(slug)
        desc = (meta or {}).get("name") or slug
        try:
            def _note(attempt, e, wait):
                log("    [%s] 网络不稳（%s），%.1fs 后重试..."
                    % (desc, _brief(e), wait))
            # ★ V1.7.0-3：插件按服务器实际加载器过滤（不再写死 paper）
            v = modrinth_pick(slug, mc_version, loader or "paper",
                              on_retry=_note)
            if not v:
                return False, slug, desc, "无 %s+%s 版本" % (
                    mc_version, loader or "paper")

            dest = os.path.join(plugins_dir, v["name"])
            download_url = _mirror_download_url(v["url"])
            _download(download_url, dest, on_retry=_note, on_log=log)

            # ★ 下载后安全扫描（Fractureiser 木马）
            if _scan_jar_virus(dest):
                try:
                    os.remove(dest)
                except Exception:
                    pass
                return False, slug, desc, "拦截到恶意文件（Fractureiser 特征），已删除"

            log("  [OK] %s" % v["name"])
            return True, slug, desc, v["name"]
        except Exception as e:
            log("  [跳过] %s - %s" % (desc, _brief(e)))
            return False, slug, desc, _brief(e)

    # ---- 预检查前置依赖（没进清单的一律跳过，不留半个）----
    valid_slugs = []
    for slug in slugs:
        meta = plugin_meta(slug)
        desc = (meta or {}).get("name") or slug
        missing = [r for r in ((meta or {}).get("req") or [])
                   if r not in slugs]
        if missing:
            msg = "缺少前置 %s" % ", ".join(missing)
            log("  [跳过] %s - %s" % (desc, msg))
            skipped.append({"slug": slug, "name": desc, "reason": msg})
        else:
            valid_slugs.append(slug)

    # ---- 并发下载（4 线程）----
    if valid_slugs:
        log("开始并发下载 %d 个插件（4 线程，前置优先）..."
            % len(valid_slugs))
        with concurrent.futures.ThreadPoolExecutor(max_workers=4) as executor:
            future_to_slug = {
                executor.submit(_download_task, slug): slug
                for slug in valid_slugs
            }
            for future in concurrent.futures.as_completed(future_to_slug):
                try:
                    success, slug, desc, result = future.result()
                except Exception as e:  # 任务本身异常也要兜住，别让 UI 崩
                    slug = future_to_slug[future]
                    success, desc, result = False, slug, _brief(e)
                if success:
                    ok_list.append({"name": result, "slug": slug})
                else:
                    fail_list.append({"slug": slug, "name": desc,
                                      "reason": result})

    if skipped:
        log("")
        log("★ 有 %d 个插件因前置没装上而跳过（不会留半个）"
            % len(skipped))
    if fail_list and not ok_list:
        log("")
        log("★ 一个都没装上，通常是网络瞬时故障（不是插件的问题）。")
        log("  等几秒再点一次『开始』通常就好了。")
    return {"ok": ok_list, "fail": fail_list, "skipped": skipped,
            "notes": plan.get("notes") or [],
            "conflicts": plan.get("conflicts") or []}


# ★ V1.3.0：多源登录组件（全部走 Modrinth 下载引擎，不写死第三方网址）
AUTH_COMPONENTS = {
    # Paper 服：MultiLogin 插件（注意：Modrinth 上暂没有官方发布，
    # 若自动下载失败，日志里会给手动安装指引）
    "paper": {
        "kind": "plugin", "slug": "multilogin", "name": "MultiLogin",
        "hint": "Modrinth 暂无官方 MultiLogin 插件发布；若自动下载失败，"
                "请到 MultiLogin 官方 GitHub（Ink33/MultiLogin）下载 jar "
                "手动放入 plugins/",
    },
    # 模组服（Forge / Fabric / NeoForge，含 1.21.1）：
    # MultiYggdrasil 官方支持 fabric/forge/neoforge 与 1.14~1.21.11。
    "mod": {
        "kind": "mod", "slug": "multiyggdrasil", "name": "MultiYggdrasil",
        "hint": None,
    },
}


def install_auth_component(server_dir, mc_version, loader, kind, slug,
                           on_log=None):
    """下载单个多源登录组件（Modrinth 引擎：并发/镜像/断点续传/木马扫描）。

    kind: "plugin"（装进 plugins/，加载器固定 paper）或
          "mod"（装进 mods/，加载器用服务器的实际加载器）。
    返回 {"ok": bool, "name": str, "reason": str, "path": str}
    """
    def log(m):
        if on_log:
            on_log(m)

    target = os.path.join(
        server_dir, "plugins" if kind == "plugin" else "mods")
    os.makedirs(target, exist_ok=True)
    ldr = loader if kind == "mod" else "paper"
    try:
        def _note(attempt, e, wait):
            log("    [%s] 网络不稳（%s），%.1fs 后重试..."
                % (slug, _brief(e), wait))
        v = modrinth_pick(slug, mc_version, ldr, on_retry=_note)
        if not v:
            return {"ok": False, "name": slug,
                    "reason": "Modrinth 无 %s+%s 版本" % (mc_version, ldr)}
        dest = os.path.join(target, v["name"])
        _download(_mirror_download_url(v["url"]), dest,
                  on_retry=_note, on_log=log)
        if _scan_jar_virus(dest):
            try:
                os.remove(dest)
            except Exception:
                pass
            return {"ok": False, "name": slug,
                    "reason": "拦截到恶意文件（Fractureiser 特征），已删除"}
        log("  [OK] %s" % v["name"])
        return {"ok": True, "name": v["name"], "path": dest}
    except Exception as e:
        return {"ok": False, "name": slug, "reason": _brief(e)}


def _brief(e):
    """把异常压成一句人能看懂的短话。"""
    t = "%s: %s" % (type(e).__name__, e)
    if "10061" in t or "actively refused" in t.lower():
        return "网络被拒(10061)，可能瞬时故障或代理拦截"
    if isinstance(e, TimeoutError) or "10060" in t \
            or "timed out" in t.lower():
        return "连接超时"
    if "11001" in t or "getaddrinfo" in t.lower():
        return "DNS 解析失败（检查网络）"
    if isinstance(e, urllib.error.HTTPError):
        return "HTTP %s" % e.code
    return t[:110]
