"""share_verifier.py — 社交平台分享验证(Phase 1 仅支持 X)

校验"链接真实可访问",不验证推文内容、动作类型或原创性。
"""

import json
import re
import urllib.parse
import urllib.request
import urllib.error


OEMBED_TIMEOUT = 8  # 秒
PLATFORM_PREFIX = {"x": "X", "reddit": "RD", "telegram": "TG", "weibo": "WB"}

# 平台 → URL host 白名单(防 SSRF/重定向到非预期域)
PLATFORM_HOSTS = {
    "x": ("x.com", "twitter.com"),
}

# 平台 → URL 路径正则(只接受合法形态的链接)
PLATFORM_URL_RE = {
    "x": re.compile(r"^/([\w]{1,15})/status/(\d{10,25})/?$"),
}


def detect_platform(url: str) -> str | None:
    """从 URL 推断平台。返回 "x" 或 None。"""
    try:
        u = urllib.parse.urlparse(url.strip())
    except Exception:
        return None
    host = u.netloc.lower().lstrip("www.")
    for plat, hosts in PLATFORM_HOSTS.items():
        if host in hosts:
            return plat
    return None


def _http_get_json(url: str, timeout: int = OEMBED_TIMEOUT) -> dict:
    """无依赖 GET JSON。超时/非 2xx/解析失败抛 RuntimeError。"""
    req = urllib.request.Request(url, headers={"User-Agent": "wc-predict/1.0 (+share-verify)"})
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            body = resp.read()
    except urllib.error.HTTPError as e:
        raise RuntimeError(f"http_{e.code}") from e
    except urllib.error.URLError as e:
        raise RuntimeError("network_error") from e
    except TimeoutError as e:
        raise RuntimeError("timeout") from e
    try:
        return json.loads(body.decode("utf-8", errors="replace"))
    except (ValueError, UnicodeDecodeError) as e:
        raise RuntimeError("invalid_response") from e


def verify_share_url(url: str, platform: str | None = None) -> dict:
    """验证社交平台分享链接真实可访问。

    返回:
      {"ok": True, "platform": "x", "author_name": ..., "author_url": ..., "tweet_id": ...}
      或抛 RuntimeError(由调用方转 error 字符串)。

    Phase 1 仅支持 x;其他平台抛 unsupported_platform。
    """
    url = (url or "").strip()
    if not url:
        raise RuntimeError("empty_url")
    detected = detect_platform(url)
    if detected is None:
        raise RuntimeError("unrecognized_url")
    if platform and platform != detected:
        raise RuntimeError("platform_mismatch")
    platform = detected

    if platform == "x":
        return _verify_x(url)
    raise RuntimeError("unsupported_platform")


def _verify_x(url: str) -> dict:
    """X 平台:调 publish.twitter.com/oembed,200 + author_name 即视为真实。"""
    parsed = urllib.parse.urlparse(url)
    path_re = PLATFORM_URL_RE["x"]
    m = path_re.match(parsed.path)
    if not m:
        raise RuntimeError("invalid_x_url")
    handle, tweet_id = m.group(1), m.group(2)

    oembed_url = "https://publish.twitter.com/oembed?" + urllib.parse.urlencode({"url": url})
    data = _http_get_json(oembed_url)

    author_name = data.get("author_name", "").strip()
    author_url = data.get("author_url", "").strip()
    provider = data.get("provider_name", "").strip().lower()
    if not author_name or not author_url:
        raise RuntimeError("oembed_incomplete")
    if provider and provider not in ("twitter", "x"):
        raise RuntimeError("oembed_wrong_provider")

    return {
        "ok": True,
        "platform": "x",
        "handle": "@" + handle,
        "tweet_id": tweet_id,
        "author_name": author_name,
        "author_url": author_url,
        "html_snippet": (data.get("html") or "")[:500],
    }
