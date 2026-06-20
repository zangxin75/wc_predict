"""payments.py — Stripe 订阅集成

环境变量:
  STRIPE_SECRET_KEY          sk_live_xxx
  STRIPE_PUBLISHABLE_KEY     pk_live_xxx
  STRIPE_PRICE_MONTHLY       price_xxx ($5/month)
  STRIPE_PRICE_TOURNAMENT    price_xxx ($12 full tournament)
  STRIPE_WEBHOOK_SECRET      whsec_xxx

数据存储: paid_users.json (email → {active, subscription_id, plan, created})
"""

import os
import json
import secrets
import threading
import time

import stripe

HERE = os.path.dirname(os.path.abspath(__file__))
PAID_USERS_PATH = os.path.join(HERE, "paid_users.json")
INVITE_CODES_PATH = os.path.join(HERE, "invite_codes.json")

# 按场次计费:基础单价 ¥2 人民币/场(可由环境变量覆盖)。
# 非中文区按 CNY_TO_USD 汇率折算为 USD 显示和收款。
PRICE_PER_MATCH_CNY = float(os.environ.get("PRICE_PER_MATCH_CNY", "2.0"))
CNY_TO_USD = float(os.environ.get("CNY_TO_USD", "0.14"))
PRICE_PER_MATCH_USD = round(PRICE_PER_MATCH_CNY * CNY_TO_USD, 2)


def unit_price_for_locale(locale: str | None) -> tuple[float, str]:
    """根据 locale 返回 (unit_price, currency)。
    中文区(以 zh 开头)用 CNY,其他用 USD。
    """
    if locale and locale.lower().startswith("zh"):
        return (PRICE_PER_MATCH_CNY, "CNY")
    return (PRICE_PER_MATCH_USD, "USD")


def currency_symbol(currency: str) -> str:
    return {"CNY": "¥", "USD": "$"}.get(currency, currency + " ")

_invite_lock = threading.Lock()  # 并发安全:同一时刻只有一个 redeem 在改 JSON

STRIPE_SECRET_KEY = os.environ.get("STRIPE_SECRET_KEY", "")
STRIPE_PUBLISHABLE_KEY = os.environ.get("STRIPE_PUBLISHABLE_KEY", "")
PRICE_MONTHLY = os.environ.get("STRIPE_PRICE_MONTHLY", "")
PRICE_TOURNAMENT = os.environ.get("STRIPE_PRICE_TOURNAMENT", "")
WEBHOOK_SECRET = os.environ.get("STRIPE_WEBHOOK_SECRET", "")

# 前端 locale → Stripe Checkout locale 映射
# Stripe 支持列表: auto, bg, cs, da, de, el, en, en-GB, es, es-419, et,
# fi, fil, fr, fr-CA, hr, hu, id, it, ja, ko, lt, lv, ms, mt, nb, nl,
# pl, pt, pt-BR, ro, ru, sk, sl, sv, th, tr, vi, zh, zh-HK, zh-TW
# 注意: ar(阿拉伯语)不受支持,回退 auto
LOCALE_MAP = {
    "zh-CN": "zh",
    "zh-TW": "zh-HK",
    "es": "es",
    "pt": "pt-BR",
    "fr": "fr",
    "ar": "auto",
}

# 前端 placeholder → (price_id, mode) 映射
# monthly: subscription 模式; tournament: 一次性 payment 模式
PRICE_MAP = {
    "price_monthly": (PRICE_MONTHLY, "subscription"),
    "price_tournament": (PRICE_TOURNAMENT, "payment"),
}

stripe.api_key = STRIPE_SECRET_KEY


def resolve_price(key: str) -> tuple[str, str]:
    """解析前端 placeholder → (price_id, mode)。"""
    v = PRICE_MAP.get(key)
    if v:
        return v
    return (key, "subscription")


def _load_users() -> dict:
    try:
        with open(PAID_USERS_PATH) as f:
            return json.load(f)
    except (FileNotFoundError, json.JSONDecodeError):
        return {}


def _atomic_write_json(path: str, data) -> None:
    """原子写 JSON:tmp + os.replace,防半写损坏。"""
    tmp = path + ".tmp"
    with open(tmp, "w") as f:
        json.dump(data, f, indent=2, ensure_ascii=False)
    os.replace(tmp, path)


def _save_users(users: dict) -> None:
    _atomic_write_json(PAID_USERS_PATH, users)


def create_checkout_session(email: str, price_id: str, origin: str, locale: str = "auto",
                            remaining_count: int | None = None) -> str:
    """创建 Stripe Checkout Session,返回支付链接。

    price_id 为前端 placeholder(price_monthly/price_tournament)。
    mode 自动选择: monthly→subscription, tournament→payment(一次性)。
    locale: 前端语言代码(如 zh-CN),映射为 Stripe 支持的语言。

    remaining_count: 按场次计费模式下,当前剩余未赛场次。后端会用 remaining × PRICE_PER_MATCH
    动态构造 line_items(price_data 内嵌,不依赖预设 price)。传 None 时回退到原 PRICE_TOURNAMENT。
    """
    stripe_locale = LOCALE_MAP.get(locale, "auto")
    # F-02: success_url 带上 CHECKOUT_SESSION_ID 占位符,Stripe 会在跳转时
    # 自动替换为真实的 cs_xxx,前端拿这个 id 调 /api/claim-session 兑换 session cookie
    success_url = origin.rstrip("/") + "/?checkout=success&session_id={CHECKOUT_SESSION_ID}"
    cancel_url = origin.rstrip("/") + "/"
    common = {"customer_email": email, "success_url": success_url, "cancel_url": cancel_url,
              "metadata": {"email": email}, "locale": stripe_locale}
    if price_id == "price_tournament" and remaining_count is not None and remaining_count > 0:
        # 按场次动态定价:price_data 内嵌,mode=payment(一次性)。
        unit, currency = unit_price_for_locale(locale)
        amount_minor = max(50, int(round(remaining_count * unit * 100)))
        return stripe.checkout.Session.create(
            mode="payment",
            line_items=[{
                "price_data": {
                    "currency": currency.lower(),
                    "unit_amount": amount_minor,
                    "product_data": {
                        "name": f"WC2026 Pro · {remaining_count} matches × {currency_symbol(currency)}{unit:.2f}/match",
                    },
                },
                "quantity": 1,
            }],
            **common,
        ).url
    # 回退路径:用预设 price_id(monthly 或 tournament 默认价)
    resolved, mode = resolve_price(price_id)
    session = stripe.checkout.Session.create(
        mode=mode,
        line_items=[{"price": resolved, "quantity": 1}],
        **common,
    )
    return session.url


def is_paid(email: str | None, users: dict | None = None) -> bool:
    """查询邮箱是否已付费订阅。

    share 计划按 pro_expires_at 判断过期(默认 24h,见 SHARE_CODE_TTL);
    invite / subscription / payment 计划只要 active:true 即视为长期 Pro。
    users:可选注入,默认从 paid_users.json 加载(测试用)。
    """
    if not email:
        return False
    if users is None:
        users = _load_users()
    rec = users.get(email.strip().lower())
    if not (rec and rec.get("active")):
        return False
    if rec.get("plan") == "share":
        return time.time() < (rec.get("pro_expires_at") or 0)
    return True


# ── 一次性邀请码 ────────────────────────────────────────────
def _load_codes() -> dict:
    try:
        with open(INVITE_CODES_PATH) as f:
            return json.load(f)
    except (FileNotFoundError, json.JSONDecodeError):
        return {}


def _save_codes(codes: dict) -> None:
    """原子写邀请码池(避免半写)。"""
    _atomic_write_json(INVITE_CODES_PATH, codes)


def redeem_invite_code(email: str, code: str) -> dict:
    """一次性邀请码验证并消耗,成功则把 email 标记为付费用户。

    码格式约定:INVITE-XXXX-XXXX(大小写不敏感,自动转大写比对)。
    一次性:首次 redeem 即把 used=true,后续同码返回 already_used。

    返回 {"ok": True, "email": ..., "plan": "invite"} 或 {"ok": False, "error": ...}。
    error ∈ {"invalid_email","not_found","already_used"}。
    """
    email = (email or "").strip().lower()
    code = (code or "").strip().upper()
    if not email or "@" not in email:
        return {"ok": False, "error": "invalid_email"}
    with _invite_lock:
        codes = _load_codes()
        rec = codes.get(code)
        if not rec:
            return {"ok": False, "error": "not_found"}
        if rec.get("used"):
            return {"ok": False, "error": "already_used"}
        # 消耗码 + 解锁用户(单锁内一次性完成两个写)
        rec["used"] = True
        rec["used_by"] = email
        rec["used_at"] = int(time.time())
        _save_codes(codes)
        users = _load_users()
        users[email] = {
            "active": True,
            "plan": "invite",
            "via": code,
            "created": int(time.time()),
        }
        _save_users(users)
    return {"ok": True, "email": email, "plan": "invite"}


def handle_webhook(payload: bytes, sig_header: str) -> dict:
    """处理 Stripe Webhook 事件。"""
    if not WEBHOOK_SECRET:
        return {"received": True, "note": "webhook_secret_not_configured"}
    event = stripe.Webhook.construct_event(payload, sig_header, WEBHOOK_SECRET)
    if event["type"] == "checkout.session.completed":
        session = event["data"]["object"]
        email = (
            session.get("customer_email")
            or session.get("metadata", {}).get("email", "")
        )
        if email:
            users = _load_users()
            users[email.strip().lower()] = {
                "active": True,
                "subscription_id": session.get("subscription"),
                "plan": session.get("mode", "subscription"),
                "created": int(time.time()),
                "session_id": session.get("id"),
            }
            _save_users(users)
    elif event["type"] == "customer.subscription.deleted":
        sub = event["data"]["object"]
        cus = stripe.Customer.retrieve(sub.get("customer"))
        email = cus.get("email", "")
        if email:
            users = _load_users()
            key = email.strip().lower()
            if key in users:
                users[key]["active"] = False
                _save_users(users)
    return {"received": True}


# ── 分享邀请码(社交媒体推广) ──────────────────────────────
SHARE_CODE_TTL = 86400  # 24h
SHARE_PLATFORM_PREFIX = {"x": "X", "reddit": "RD", "telegram": "TG", "weibo": "WB"}


def _existing_recent_code(platform: str, handle: str, codes: dict, now: int) -> dict | None:
    """同 (platform, handle) 24h 内是否已有未 redeem 或已 redeem 的码。

    handle 来自 tweet URL 的 @handle 字段(同 X 账号 24h 内只允许 1 个有效码,
    跨 email 共用限流 — 防止换邮箱绕开)。handle 大小写不敏感、自动去 `@` 前缀。
    返回该码记录(或 None)。调用方据此决定 reused 或生成新码。
    """
    cutoff = now - SHARE_CODE_TTL
    target_handle = (handle or "").lstrip("@").lower()
    for rec in codes.values():
        meta = rec.get("source_meta") or {}
        if meta.get("platform") != platform:
            continue
        rec_handle = (meta.get("handle", "") or "").lstrip("@").lower()
        if rec_handle != target_handle:
            continue
        # 未用且未过期
        if not rec.get("used") and rec.get("expires_at", 0) > now:
            return rec
        # 已用且 redeem 在 24h 内(Pro 窗口内)
        if rec.get("used") and (rec.get("used_at", 0) or rec.get("redeemed_at", 0)) >= cutoff:
            return rec
    return None


def _next_available_at(rec: dict, now: int) -> int:
    """基于现有码,推算下次可生成新码的时间戳(unix 秒)。

    - 已 redeem → used_at + 24h
    - 未用未过期 → 自身 expires_at(到那时自然过期,可领新码)
    """
    if rec.get("used"):
        return int(rec.get("used_at", now)) + SHARE_CODE_TTL
    return int(rec.get("expires_at", now))


def generate_share_code(platform: str, email: str, source_meta: dict,
                       expected_email: str | None = None) -> dict:
    """生成或复用分享邀请码。

    同 (platform, X-handle) 24h 内只允许 1 个有效码(handle 从 source_meta 提取,
    跨 email 共用限流 — 防止换邮箱绕开):
      - 已有未用未过期 → 返回旧码(reused=True, status="active")
      - 已有已用且 24h 内 → 返回旧码(reused=True, status="redeemed")
      - 否则 → 生成新码(reused=False, status="new")

    expected_email: F-08 绑定。若非空,redeem 时必须由同 email 兑换,否则返回
      email_mismatch。None 表示不绑定(向后兼容)。

    返回 {"code", "expires_at", "reused", "status": "new|active|redeemed",
          "next_available_at": ts(秒)}。
    """
    email = (email or "").strip().lower()
    platform = (platform or "").strip().lower()
    expected_email = (expected_email or "").strip().lower() or None
    handle = ((source_meta or {}).get("handle") or "").lstrip("@").lower()
    now = int(time.time())
    with _invite_lock:
        codes = _load_codes()
        existing = _existing_recent_code(platform, handle, codes, now)
        if existing is not None:
            if existing.get("used"):
                status = "redeemed"
            else:
                status = "active"
            return {
                "code": existing["code"],
                "expires_at": existing.get("expires_at"),
                "reused": True,
                "status": status,
                "next_available_at": _next_available_at(existing, now),
            }
        prefix = SHARE_PLATFORM_PREFIX.get(platform, platform.upper()[:2])
        # 8 字符 base32: token_urlsafe(6) → ~8 字符 url-safe
        suffix = secrets.token_urlsafe(6)[:8].upper().replace("-", "X").replace("_", "Y")
        code = f"SHARE-{prefix}-{suffix}"
        expires = now + SHARE_CODE_TTL
        meta = dict(source_meta or {})
        meta["platform"] = platform
        meta["email"] = email
        if expected_email:
            meta["expected_email"] = expected_email
        codes[code] = {
            "code": code,
            "used": False,
            "expires_at": expires,
            "source": f"share:{platform}",
            "source_meta": meta,
            "created_at": now,
            "used_by": None,
            "used_at": None,
        }
        _save_codes(codes)
    return {
        "code": code,
        "expires_at": expires,
        "reused": False,
        "status": "new",
        "next_available_at": expires,  # 新码:下次可领 = 自身过期时间
    }


def redeem_share_code(email: str, code: str) -> dict:
    """分享邀请码 redeem。成功 → email 标记 24h Pro(由前端用 expires_at 倒计时)。

    检查顺序:email 格式 → 存在 → !used → now < expires_at → expected_email 匹配 → 写回。
    错误码 ∈ {"invalid_email","not_found","already_used","expired","email_mismatch"}。
    F-08: 若码带 expected_email 绑定,redeem email 必须一致。
    """
    email = (email or "").strip().lower()
    code = (code or "").strip().upper()
    if not email or "@" not in email:
        return {"ok": False, "error": "invalid_email"}
    now = int(time.time())
    with _invite_lock:
        codes = _load_codes()
        rec = codes.get(code)
        if not rec:
            return {"ok": False, "error": "not_found"}
        if rec.get("used"):
            return {"ok": False, "error": "already_used"}
        if (rec.get("expires_at") or 0) <= now:
            return {"ok": False, "error": "expired"}
        # F-08: expected_email 绑定校验
        expected = (rec.get("source_meta") or {}).get("expected_email")
        if expected and expected != email:
            return {"ok": False, "error": "email_mismatch"}
        # 消耗码 + 解锁用户(单锁内一次性完成)
        rec["used"] = True
        rec["used_by"] = email
        rec["used_at"] = now
        _save_codes(codes)
        users = _load_users()
        users[email] = {
            "active": True,
            "plan": "share",
            "via": code,
            "source": rec.get("source", ""),
            "pro_expires_at": now + SHARE_CODE_TTL,
            "created": now,
        }
        _save_users(users)
    return {
        "ok": True,
        "email": email,
        "plan": "share",
        "pro_expires_at": now + SHARE_CODE_TTL,
    }
