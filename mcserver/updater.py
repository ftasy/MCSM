# -*- coding: utf-8 -*-
"""V1.6.0：软件更新检测（GitHub Releases）。

网络请求，调用方必须放进 Worker 后台线程，绝不能卡 UI。
★ V1.8.0：GITHUB_REPO 已配置为 ftasy/MCSM；
  修复 check_latest 里二次 % 格式化崩溃，并补网络超时 + 退避重试兜底。
"""
import json
import re
import time
import urllib.request
import urllib.error

from . import __version__

# ★ 已配置：更新检测指向 ftasy/MCSM（V1.8.0：确认非空，且为官方仓库）
GITHUB_REPO = "ftasy/MCSM"

# URL 直接拼好，避免二次 % 格式化的坑
API = "https://api.github.com/repos/%s/releases/latest" % GITHUB_REPO

TIMEOUT = 10        # 单次请求超时（秒）
RETRIES = 3         # 最多请求次数（含首次）
RETRY_WAIT = 1.5    # 重试退避基数（1.5 / 3 / 6 秒）


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


def _request_once():
    """单次请求 GitHub releases/latest，返回原始 JSON dict。"""
    req = urllib.request.Request(
        API, headers={"User-Agent": "MCSM/%s" % __version__})
    with urllib.request.urlopen(req, timeout=TIMEOUT) as r:
        return json.loads(r.read().decode("utf-8"))


def _is_retryable(e):
    """该异常值得重试吗？5xx / 超时 / 连接类都值得，4xx 不重试。"""
    if isinstance(e, urllib.error.HTTPError):
        return e.code >= 500
    if isinstance(e, urllib.error.URLError):
        return True
    if isinstance(e, TimeoutError) or isinstance(e, OSError):
        return True
    return False


def check_latest(on_log=None):
    """查 GitHub 最新版，返回 (tag_name, html_url, is_new)。

    GITHUB_REPO 未配置时返回 (None, None, False)，不抛异常。
    网络抖动自动重试（最多 RETRIES 次，退避递增）；
    全部失败抛异常（Worker 会转成 done(False, err)）。
    """
    repo = GITHUB_REPO.strip().strip("/")
    if not repo:
        return None, None, False

    def log(m):
        if on_log:
            on_log(m)

    last = None
    for attempt in range(1, RETRIES + 1):
        try:
            data = _request_once()
            tag = data.get("tag_name") or ""
            url = data.get("html_url") or ""
            return tag, url, is_newer(tag, __version__)
        except Exception as e:
            last = e
            if attempt >= RETRIES or not _is_retryable(e):
                break
            wait = RETRY_WAIT * (2 ** (attempt - 1))
            log("    [%s] 网络不稳（%s），%.1fs 后重试..."
                % (repo, e, wait))
            time.sleep(wait)
    raise last
