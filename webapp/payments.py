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
import time

import stripe

HERE = os.path.dirname(os.path.abspath(__file__))
PAID_USERS_PATH = os.path.join(HERE, "paid_users.json")

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

# 前端 placeholder → 真实 price_id 映射
PRICE_MAP = {
    "price_monthly": PRICE_MONTHLY,
    "price_tournament": PRICE_TOURNAMENT,
}

stripe.api_key = STRIPE_SECRET_KEY


def resolve_price_id(key: str) -> str:
    """解析前端传入的 placeholder price_id 为 Stripe 真实 price_id。"""
    return PRICE_MAP.get(key) or key


def _load_users() -> dict:
    try:
        with open(PAID_USERS_PATH) as f:
            return json.load(f)
    except (FileNotFoundError, json.JSONDecodeError):
        return {}


def _save_users(users: dict) -> None:
    with open(PAID_USERS_PATH, "w") as f:
        json.dump(users, f, indent=2, ensure_ascii=False)


def create_checkout_session(email: str, price_id: str, origin: str, locale: str = "auto") -> str:
    """创建 Stripe Checkout Session,返回支付链接。

    locale: 前端语言代码(如 zh-CN),映射为 Stripe 支持的语言。
    """
    resolved = resolve_price_id(price_id)
    stripe_locale = LOCALE_MAP.get(locale, "auto")
    session = stripe.checkout.Session.create(
        customer_email=email,
        mode="subscription",
        line_items=[{"price": resolved, "quantity": 1}],
        success_url=origin.rstrip("/") + "/?checkout=success",
        cancel_url=origin.rstrip("/") + "/",
        metadata={"email": email},
        locale=stripe_locale,
    )
    return session.url


def is_paid(email: str | None) -> bool:
    """查询邮箱是否已付费订阅。"""
    if not email:
        return False
    users = _load_users()
    rec = users.get(email.strip().lower())
    return bool(rec and rec.get("active"))


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
