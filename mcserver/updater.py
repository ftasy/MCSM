# -*- coding: utf-8 -*-
"""V1.6.0：软件更新检测（GitHub Releases）。

网络请求，调用方必须放进 Worker 后台线程，绝不能卡 UI。
★ V1.8.0：GITHUB_REPO 已配置为 ftasy/MCSM；
  修复 check_latest 里二次 % 格式化崩溃，并补网络超时 + 退避重试兜底。
"""
import json
import re
import time

from .core import net
from . import __version__

_RE_DIGITS_U = re.compile(r"\d+")
# ★ V2.5.0：预发布后缀不能算版本位。以前 v2.4.1-rc1 被切成
#   [2,4,1,1]、v2.4.2-beta1 被切成 [2,4,2,1]，都会误报"有更新"。
_RE_PRE = re.compile(
    r"(?i)[-_.]?(?:pre|rc|alpha|beta|snapshot|dev|exp)\d*\b.*$")
_RE_V = re.compile(r"(?i)^v")

# ★ 已配置：更新检测指向 ftasy/MCSM（V1.8.0：确认非空，且为官方仓库）
GITHUB_REPO = "ftasy/MCSM"

# URL 直接拼好，避免二次 % 格式化的坑
API = "https://api.github.com/repos/%s/releases/latest" % GITHUB_REPO

TIMEOUT = 10        # 单次请求超时（秒）
RETRIES = 3         # 最多请求次数（含首次）
RETRY_WAIT = 1.5    # 重试退避基数（1.5 / 3 / 6 秒）


def _nums(s):
    """抽出纯版本号数字段：v2.4.1-rc1 -> [2, 4, 1]（后缀不算版本位）。"""
    t = _RE_V.sub("", str(s or "").strip())
    t = _RE_PRE.sub("", t).strip()
    return [int(x) for x in _RE_DIGITS_U.findall(t)]


def is_newer(tag, cur):
    """tag 是否严格大于 cur。

    ★ V2.5.0：预发布（rc / beta / pre / snapshot...）一律不算正式更新；
      数字位补齐到等长再比，避免 2.4 与 2.4.1 靠"谁更长"来判。
    """
    if _RE_PRE.search(str(tag or "")):
        return False
    t, c = _nums(tag), _nums(cur)
    n = max(len(t), len(c))
    t += [0] * (n - len(t))
    c += [0] * (n - len(c))
    return t > c


def _request_once():
    """单次请求 GitHub releases/latest，返回原始 JSON dict。"""
    return net.get_json(API, timeout=TIMEOUT)


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
            # ★ V2.5.0：草稿/预发布不算正式更新（双保险，is_newer 也拦）
            if data.get("draft") or data.get("prerelease"):
                return tag, url, False
            return tag, url, is_newer(tag, __version__)
        except Exception as e:
            last = e
            if attempt >= RETRIES or not net.is_retryable(e):
                break
            wait = RETRY_WAIT * (2 ** (attempt - 1))
            log("    [%s] 网络不稳（%s），%.1fs 后重试..."
                % (repo, e, wait))
            time.sleep(wait)
    raise last
