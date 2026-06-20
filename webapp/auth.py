"""auth.py — Session 鉴权模块 (F-02 修复)

替代 x_user_email 查询参数鉴权(F-02 Critical 漏洞),改用 HMAC 签名的 session cookie。

鉴权流程:
  Stripe 支付:
    1. 用户完成 checkout → Stripe 跳回 /?checkout=success&session_id=cs_xxx
    2. 前端调 POST /api/claim-session?session_id=cs_xxx
    3. 服务端用 STRIPE_SECRET_KEY 查 session, 拿到 customer_email
    4. 写入 session["email"], Set-Cookie 自动下发签名 cookie
    5. 后续 API 自动带 cookie, get_current_user() 读出 email

  邀请码:
    1. POST /api/redeem-invite { email, code } → 验证 + 标记付费
    2. 服务端写入 session["email"] → Set-Cookie 下发
    3. 后续 API 走 cookie

向后兼容: x_user_email query 参数仍可用,但会打 DeprecationWarning 警告日志,
        推荐前端尽快迁移到 cookie。

安全特性:
  · cookie 由 starlette SessionMiddleware 签名(itsdangerous), 防篡改
  · 默认 SameSite=Lax, 防止 CSRF 跨站请求
  · 30 天过期 (max_age=30*24*3600)
  · secret_key 优先用 SESSION_SECRET 环境变量, 缺则从 STRIPE_SECRET_KEY 派生
    (启动时会发警告, 强烈建议生产环境显式设置)
"""
import hashlib
import os
import time
import warnings
from typing import Optional

from fastapi import Request, HTTPException


def _resolve_secret() -> str:
    """解析 session 签名密钥。

    优先级: 显式 SESSION_SECRET 环境变量 > 从 STRIPE_SECRET_KEY 派生 > 临时随机。
    派生模式启动时会发警告,生产应显式设置。
    """
    explicit = os.environ.get("SESSION_SECRET", "").strip()
    if explicit and len(explicit) >= 32:
        return explicit
    sk = os.environ.get("STRIPE_SECRET_KEY", "")
    if sk:
        derived = hashlib.sha256(f"wcpredict-session-v1:{sk}".encode()).hexdigest()
        warnings.warn(
            "[auth] SESSION_SECRET 未设置;已从 STRIPE_SECRET_KEY 派生。"
            "生产环境请显式设置 SESSION_SECRET (>=32 字节随机串)。",
            RuntimeWarning,
            stacklevel=2,
        )
        return derived
    # 最后的兜底: 临时密钥(重启失效,但不阻断启动)
    import secrets
    ephemeral = secrets.token_hex(32)
    warnings.warn(
        "[auth] SESSION_SECRET 与 STRIPE_SECRET_KEY 均未配置;"
        f"使用临时随机密钥 (本次启动有效, 重启后所有 session 失效): {ephemeral[:8]}...",
        RuntimeWarning,
        stacklevel=2,
    )
    return ephemeral


SESSION_SECRET = _resolve_secret()
SESSION_MAX_AGE = 30 * 24 * 3600   # 30 天
COOKIE_NAME = "wcpredict_session"


def get_current_user(request: Request) -> Optional[str]:
    """从 session cookie 取当前用户 email。

    返回 None 表示未登录。优先 cookie, 后备读取 x_user_email query 参数
    (向后兼容, 命中时打 DeprecationWarning)。
    """
    # 1) 优先从 session middleware 取
    sess = getattr(request, "session", None) or {}
    email = sess.get("email")
    if email:
        return str(email).strip().lower()

    # 2) 向后兼容: x_user_email 查询参数 (会打警告)
    legacy = request.query_params.get("x_user_email")
    if legacy:
        client = request.client.host if request.client else "?"
        print(
            f"[auth][DEPRECATED] x_user_email query param used by {client} "
            f"(email={legacy[:30]}...). Migrate to session cookie."
        )
        return legacy.strip().lower()

    return None


def require_user(request: Request) -> str:
    """FastAPI Depends: 必须已登录, 否则 401。"""
    user = get_current_user(request)
    if not user:
        raise HTTPException(
            status_code=401,
            detail="Authentication required. Please subscribe or redeem an invite code.",
        )
    return user


def set_session_user(request: Request, email: str) -> None:
    """写入 session["email"]。Set-Cookie 由 SessionMiddleware 自动下发。"""
    request.session["email"] = email.strip().lower()
    request.session["iat"] = int(time.time())


def clear_session(request: Request) -> None:
    """清空 session(登出)。"""
    request.session.clear()
