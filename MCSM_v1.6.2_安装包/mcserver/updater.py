# -*- coding: utf-8 -*-
"""V1.6.0：软件更新检测（GitHub Releases）。

网络请求，调用方必须放进 Worker 后台线程，绝不能卡 UI。
"""
import json
import re
import urllib.request

from . import __version__

# ★ 已配置：更新检测指向 ftasy/MCSM
GITHUB_REPO = "ftasy/MCSM"

API = "https://api.github.com/repos/%s/releases/latest" % GITHUB_REPO


def _nums(s):
    """把版本号字符串里的数字段抽出来：v1.6.0 -> [1, 6, 0]。"""
    return [int(x) for x in re.findall(r"\d+", str(s))]


def is_newer(tag, cur):
    """tag 是否严格大于 cur（tag 常带 v 前缀，cur 是 1.6.0）。"""
    t = _nums(tag)
    c = _nums(cur)
    for a, b in zip(t, c):
        if a != b:
            return a > b
    return len(t) > len(c)


def check_latest(on_log=None):
    """查 GitHub 最新版，返回 (tag_name, html_url, is_new)。

    GITHUB_REPO 未配置时返回 (None, None, False)，不抛异常。
    请求失败抛异常（Worker 会转成 done(False, err)）。
    """
    repo = GITHUB_REPO.strip().strip("/")
    if not repo:
        return None, None, False
    req = urllib.request.Request(
        API % repo,
        headers={"User-Agent": "MCSM/%s" % __version__})
    with urllib.request.urlopen(req, timeout=10) as r:
        data = json.loads(r.read().decode("utf-8"))
    tag = data.get("tag_name") or ""
    url = data.get("html_url") or ""
    return tag, url, is_newer(tag, __version__)
