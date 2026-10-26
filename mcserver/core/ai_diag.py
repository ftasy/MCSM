# -*- coding: utf-8 -*-
"""V1.0.0 —— AI 深度诊断。

纯标准库实现：从服务器日志提取报错片段，POST 给任意
OpenAI 兼容接口（OpenAI / DeepSeek / Kimi / 自定义），
返回 Markdown 诊断文本。所有网络请求由调用方放进 Worker 线程。
"""
import json
import os
import re
import urllib.request
import urllib.error

# 服务商预设（OpenAI 兼容）
PROVIDERS = [
    ("OpenAI",      "https://api.openai.com/v1",     "gpt-4o-mini"),
    ("DeepSeek",    "https://api.deepseek.com/v1",   "deepseek-chat"),
    ("Kimi(Moonshot)", "https://api.moonshot.cn/v1", "moonshot-v1-8k"),
    ("自定义",       "",                              ""),
]

SYSTEM_PROMPT = ("你是一个资深 Minecraft 服主，请分析以下日志，"
                 "告诉我崩溃原因和修复方法")

# 报错特征行（提取时按这个过滤）
ERR_RE = re.compile(
    r"(error|exception|caused by|fatal|severe|failed|unable|"
    r"at net\.minecraft|crash|invalid|out of memory|"
    r"address already in use|connect timed out)",
    re.IGNORECASE)

MAX_LINES = 200       # 只看最后 200 行
MAX_ERRORS = 80       # 过滤后最多带多少条
MAX_CHARS = 20000     # 防止日志太大把 token 烧光


def cfg_path():
    """AI 配置存 ai.json（与 servers.json 同目录）。"""
    return os.path.join(os.path.dirname(os.path.abspath(__file__)),
                        "..", "ai.json")


def load_cfg():
    try:
        with open(cfg_path(), "r", encoding="utf-8") as f:
            d = json.load(f)
        return {
            "key": d.get("key", ""),
            "base": d.get("base", PROVIDERS[0][1]),
            "model": d.get("model", PROVIDERS[0][2]),
            "provider": d.get("provider", 0),
        }
    except Exception:
        return {"key": "", "base": PROVIDERS[0][1],
                "model": PROVIDERS[0][2], "provider": 0}


def save_cfg(key, base, model, provider):
    try:
        d = {"key": key, "base": base, "model": model,
             "provider": provider}
        with open(cfg_path(), "w", encoding="utf-8") as f:
            json.dump(d, f, ensure_ascii=False, indent=2)
        return True
    except Exception:
        return False


def find_latest_log(server_dir):
    """按常见布局找 latest.log，返回路径或 None。"""
    for cand in (os.path.join(server_dir, "logs", "latest.log"),
                 os.path.join(server_dir, "server", "logs", "latest.log"),
                 os.path.join(server_dir, "latest.log")):
        if os.path.isfile(cand):
            return cand
    return None


def _read_text(path):
    """UTF-8 优先，失败回退 GBK，再不行忽略坏字节。"""
    for enc in ("utf-8", "gbk", "utf-8-sig"):
        try:
            with open(path, "r", encoding=enc) as f:
                return f.read()
        except UnicodeDecodeError:
            continue
    with open(path, "r", encoding="utf-8", errors="ignore") as f:
        return f.read()


def collect_errors(server_dir, max_lines=MAX_LINES,
                   max_errors=MAX_ERRORS, max_chars=MAX_CHARS):
    """提取 latest.log 最后 max_lines 行里的报错内容。

    返回 dict：
      path      日志文件路径（找不到为 None）
      lines     送审的行（list）
      total     日志总行数
      filtered  过滤后命中的行数（0 = 没筛到，退回全量）
      truncated 是否因超长被截断
    """
    path = find_latest_log(server_dir)
    if not path:
        return {"path": None, "lines": [], "total": 0,
                "filtered": 0, "truncated": False}
    text = _read_text(path)
    all_lines = text.splitlines()
    tail = all_lines[-max_lines:]
    hits = [ln for ln in tail if ERR_RE.search(ln)][:max_errors]
    if not hits:
        hits = tail
    # 总字符上限
    truncated = False
    out, size = [], 0
    for ln in hits:
        if size + len(ln) + 1 > max_chars:
            truncated = True
            break
        out.append(ln)
        size += len(ln) + 1
    return {"path": path, "lines": out, "total": len(all_lines),
            "filtered": len(hits), "truncated": truncated}


def call_llm(api_key, base_url, model, log_text, timeout=120,
             on_log=None, prompt=SYSTEM_PROMPT):
    """POST 到 OpenAI 兼容 chat/completions，返回 Markdown 文本。

    on_log: 可选的进度回调（Worker 的 log signal）。
    失败抛异常，由调用方捕获。
    """
    def _log(m):
        try:
            if on_log:
                on_log(m)
        except Exception:
            pass

    if not api_key:
        raise ValueError("请先填写 API Key")
    base = (base_url or "").strip().rstrip("/")
    if not base:
        raise ValueError("请填写接口地址（base url）")
    if not model:
        raise ValueError("请填写模型名称")
    if not log_text.strip():
        raise ValueError("没有可分析的日志（找不到 latest.log 或内容为空）")

    url = base + "/chat/completions"
    body = {
        "model": model,
        "messages": [
            {"role": "system", "content": prompt},
            {"role": "user", "content": log_text},
        ],
        "temperature": 0.2,
    }
    data = json.dumps(body).encode("utf-8")
    req = urllib.request.Request(
        url, data=data,
        headers={
            "Content-Type": "application/json",
            "Authorization": "Bearer %s" % api_key,
            "User-Agent": "MCServerManager/1.0 (ai-diag)",
        })
    _log("正在请求 %s ..." % base)
    with urllib.request.urlopen(req, timeout=timeout) as resp:
        raw = resp.read()
    try:
        obj = json.loads(raw.decode("utf-8"))
    except Exception:
        raise ValueError("接口返回不是合法 JSON：%s" % raw[:200])
    if isinstance(obj, dict) and obj.get("error"):
        err = obj["error"]
        raise ValueError("接口报错：%s" % (err.get("message") or err))
    try:
        return obj["choices"][0]["message"]["content"].strip()
    except Exception:
        raise ValueError("响应缺少 choices[0].message.content，"
                         "请确认是 OpenAI 兼容接口")
