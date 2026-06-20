"""test_payments_is_paid.py — is_paid() 单测

覆盖:空 email / 不存在 / inactive / invite / subscription / payment /
       share 计划未过期 / share 计划已过期 / share 计划缺 pro_expires_at(防御)。
"""

import os
import sys
import time

import pytest

HERE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if HERE not in sys.path:
    sys.path.insert(0, HERE)

from webapp import payments


# ── IP-01:空 email ─────────────────────────────────────────
def test_is_paid_empty_email():
    assert payments.is_paid("") is False
    assert payments.is_paid(None) is False


# ── IP-02:邮箱不在 paid_users ──────────────────────────────
def test_is_paid_unknown_email():
    assert payments.is_paid("nobody@example.com", users={}) is False


# ── IP-03:active=false ───────────────────────────────────
def test_is_paid_inactive():
    users = {"a@b.com": {"active": False, "plan": "invite"}}
    assert payments.is_paid("a@b.com", users=users) is False


# ── IP-04:invite 计划 active → 长期 Pro ───────────────────
def test_is_paid_invite_active():
    users = {"a@b.com": {"active": True, "plan": "invite", "created": 0}}
    assert payments.is_paid("a@b.com", users=users) is True


# ── IP-05:subscription 计划 active → 长期 Pro(无 pro_expires_at) ──
def test_is_paid_subscription_active():
    users = {"a@b.com": {"active": True, "plan": "subscription", "subscription_id": "sub_xxx"}}
    assert payments.is_paid("a@b.com", users=users) is True


# ── IP-06:payment(单次 tournament)计划 active → 长期 Pro ──
def test_is_paid_payment_active():
    users = {"a@b.com": {"active": True, "plan": "payment", "session_id": "cs_xxx"}}
    assert payments.is_paid("a@b.com", users=users) is True


# ── IP-07:share 计划未过期 → Pro ──────────────────────────
def test_is_paid_share_active_future():
    users = {"a@b.com": {
        "active": True,
        "plan": "share",
        "pro_expires_at": int(time.time()) + 3600,   # 1h 后过期
    }}
    assert payments.is_paid("a@b.com", users=users) is True


# ── IP-08:share 计划已过期 → 非 Pro (这是修复的核心 bug) ──
def test_is_paid_share_expired():
    users = {"a@b.com": {
        "active": True,
        "plan": "share",
        "pro_expires_at": int(time.time()) - 1,      # 1s 前已过期
    }}
    assert payments.is_paid("a@b.com", users=users) is False


# ── IP-09:share 计划缺 pro_expires_at(防御)→ 非 Pro ──────
def test_is_paid_share_missing_expiry():
    users = {"a@b.com": {"active": True, "plan": "share"}}     # 无 pro_expires_at
    assert payments.is_paid("a@b.com", users=users) is False


# ── IP-10:share 计划 pro_expires_at=0(显式) → 非 Pro ─────
def test_is_paid_share_zero_expiry():
    users = {"a@b.com": {"active": True, "plan": "share", "pro_expires_at": 0}}
    assert payments.is_paid("a@b.com", users=users) is False


# ── IP-11:大小写不敏感(email 标准化)─────────────────────
def test_is_paid_case_insensitive():
    users = {"a@b.com": {"active": True, "plan": "invite"}}
    assert payments.is_paid("A@B.COM", users=users) is True
    assert payments.is_paid("  a@b.com  ", users=users) is True
