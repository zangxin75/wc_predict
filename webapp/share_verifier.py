"""share_verifier.py — 社交平台分享验证(Phase 1 仅支持 X)

校验"链接形态合法",不验证推文真实存在/可访问。
Phase 1.1 改:跳过 oembed HTTP 探测(Prod 出网限制导致用户体验问题)。
"""

import re
import urllib.parse


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


def verify_share_url(url: str, platform: str | None = None) -> dict:
    """验证社交平台分享链接。

    Phase 1.1 策略调整:只校验 URL 格式(host 白名单 + 路径正则)。
    不再调 publish.twitter.com/oembed — Prod 服务器出网限制/超时导致用户报
    "网络错误,请重试",但其实问题在第三方验证服务不在用户网络。代价:
    任何人粘合法 X 链接都能拿码 — Phase 1 MVP 阶段可接受(目标:广撒网邀请)。

    返回:
      {"ok": True, "platform": "x", "handle": ..., "tweet_id": ..., "verified": False}
      或抛 RuntimeError(empty_url / unrecognized_url / platform_mismatch / invalid_x_url)。
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
    """X 平台:仅校验 URL 格式。解析 path → (handle, tweet_id),无需 HTTP。"""
    parsed = urllib.parse.urlparse(url)
    path_re = PLATFORM_URL_RE["x"]
    m = path_re.match(parsed.path)
    if not m:
        raise RuntimeError("invalid_x_url")
    handle, tweet_id = m.group(1), m.group(2)
    return {
        "ok": True,
        "platform": "x",
        "handle": "@" + handle,
        "tweet_id": tweet_id,
        "verified": False,  # 格式校验,未做真实存在性验证
        "verify_mode": "format_only",
    }
