# -*- coding: utf-8 -*-
"""统一网络请求助手。

以前每个模块各写一套 urllib.request.Request + urlopen + 重试 + JSON 解析，
UA 不统一、重试逻辑不一致、超时各设各的。这里抽出公共基础设施：
  - UA：统一 User-Agent
  - is_retryable()：判断异常是否值得重试
  - retry()：带指数退避的重试包装
  - get() / get_text() / get_json()：常用 GET 请求快捷方法
  - request()：底层 POST/GET 请求（供 modman._api_post 等使用）

专用逻辑（镜像切换、CF key、下载进度条）仍留在各自模块，
它们调用本模块的底层函数即可。
"""

import json
import time
import urllib.request
import urllib.error

# 统一 User-Agent（所有请求共用）
UA = {"User-Agent": "MCServerManager/2.1"}

# 默认超时（秒）
TIMEOUT = 60

# 重试参数
RETRY = 3
RETRY_WAIT = 1.2    # 首次退避秒数，之后按 1.2 / 2.4 / 4.8 递增

# 这些是"资源不存在"，重试一万次也没用，要立刻放弃
NO_RETRY_CODES = {400, 401, 403, 404, 410, 422}


def is_retryable(e):
    """这个异常值得重试吗？

    5xx 服务端出错、超时、连接重置、DNS 失败都值得重试；
    4xx 多半是请求本身有问题，立刻放弃。
    """
    if isinstance(e, urllib.error.HTTPError):
        return e.code not in NO_RETRY_CODES
    if isinstance(e, urllib.error.URLError):
        # 含 WinError 10061 / 10060 / 连接重置 / DNS 失败
        return True
    if isinstance(e, (TimeoutError, OSError)):
        # socket.timeout 在 3.10+ 就是 TimeoutError
        return True
    return False


def retry(fn, *args, retries=RETRY, wait=RETRY_WAIT,
          on_retry=None, **kwargs):
    """带指数退避的重试包装。

    fn(*args, **kwargs) 抛异常时，若值得重试就等一会儿再来，
    最后一次仍失败则抛出原异常。
    """
    cur_wait = wait
    last = None
    for attempt in range(1, retries + 1):
        try:
            return fn(*args, **kwargs)
        except Exception as e:
            last = e
            if attempt >= retries or not is_retryable(e):
                raise
            if on_retry:
                on_retry(attempt, e, cur_wait)
            time.sleep(cur_wait)
            cur_wait *= 2
    raise last


def request(url, data=None, headers=None, timeout=TIMEOUT):
    """底层 HTTP 请求，返回原始 bytes。

    headers 会合并到 UA 上（调用方传入的 headers 优先）。
    data 不为 None 时发 POST。
    """
    h = dict(UA)
    if headers:
        h.update(headers)
    req = urllib.request.Request(url, data=data, headers=h)
    with urllib.request.urlopen(req, timeout=timeout) as r:
        return r.read()


def get(url, headers=None, timeout=TIMEOUT, on_retry=None):
    """GET 请求，返回原始 bytes（带重试）。"""
    return retry(request, url, headers=headers, timeout=timeout,
                 on_retry=on_retry)


def get_text(url, headers=None, timeout=TIMEOUT, on_retry=None):
    """GET 请求，返回解码后的文本。"""
    return get(url, headers=headers, timeout=timeout,
               on_retry=on_retry).decode("utf-8", "replace")


def get_json(url, headers=None, timeout=TIMEOUT, on_retry=None):
    """GET 请求，返回解析后的 JSON 对象。"""
    return json.loads(get_text(url, headers=headers, timeout=timeout,
                               on_retry=on_retry))
