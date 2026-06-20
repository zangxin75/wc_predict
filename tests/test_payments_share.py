"""test_payments_share.py — share invite code 限流 + next_available_at 单测

覆盖(handle 维度的限流 — 跨 email 共用):
  SC-01:首次生成新码(reused=False, status=new)
  SC-02:同 handle + 同 email 24h 内复用
  SC-03:同 handle + 不同 email 24h 内仍复用(新行为:handle 才是限流 key)
  SC-04:不同 handle + 同 email 可拿多码
  SC-05:redeemed 后再 verify → next_available_at = used_at + 24h
  SC-06:unused reused → next_available_at = 旧 expires_at
  SC-07:source_meta 缺 handle 字段时(向后兼容)仍能生成新码
  SC-08:跨平台(reddit vs X)不互限
  SC-09:handle 大小写不敏感(@NBA == @nba)
"""

import json
import os
import sys
import tempfile
import time
from pathlib import Path

import pytest

HERE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if HERE not in sys.path:
    sys.path.insert(0, HERE)

from webapp import payments


@pytest.fixture
def tmp_files(monkeypatch):
    """独立 JSON 文件,避免污染生产 invite_codes.json / paid_users.json。"""
    with tempfile.TemporaryDirectory() as tmpdir:
        codes_file = Path(tmpdir) / "codes.json"
        users_file = Path(tmpdir) / "users.json"
        codes_file.write_text(json.dumps({}))
        users_file.write_text(json.dumps({}))
        orig_codes = payments.INVITE_CODES_PATH
        orig_users = payments.PAID_USERS_PATH
        payments.INVITE_CODES_PATH = str(codes_file)
        payments.PAID_USERS_PATH = str(users_file)
        try:
            yield
        finally:
            payments.INVITE_CODES_PATH = orig_codes
            payments.PAID_USERS_PATH = orig_users


def _meta(handle="@nba"):
    return {"tweet_url": f"https://x.com/{handle.lstrip('@')}/status/123",
            "handle": handle, "tweet_id": "123"}


# ── SC-01:首次生成新码 ───────────────────────────────────────
def test_generate_new(tmp_files):
    r = payments.generate_share_code("x", "alice@x.com", _meta())
    assert r["reused"] is False
    assert r["status"] == "new"
    assert r["code"].startswith("SHARE-X-")
    # 新码:next_available_at = 自身 expires_at
    assert r["next_available_at"] == r["expires_at"]


# ── SC-02:同 handle + 同 email 24h 内复用 ───────────────────
def test_reuse_same_handle_same_email(tmp_files):
    r1 = payments.generate_share_code("x", "alice@x.com", _meta())
    r2 = payments.generate_share_code("x", "alice@x.com", _meta())
    assert r2["reused"] is True
    assert r2["status"] == "active"
    assert r2["code"] == r1["code"]


# ── SC-03:同 handle + 不同 email 24h 内仍复用(新行为) ──────
def test_reuse_same_handle_different_email(tmp_files):
    """换 email 不能绕开:handle 才是限流 key。"""
    r1 = payments.generate_share_code("x", "alice@x.com", _meta())
    r2 = payments.generate_share_code("x", "bob@x.com", _meta())
    assert r2["reused"] is True
    assert r2["code"] == r1["code"]
    assert r2["status"] == "active"


# ── SC-04:不同 handle + 同 email 可拿多码 ───────────────────
def test_different_handles_get_separate_codes(tmp_files):
    r1 = payments.generate_share_code("x", "alice@x.com", _meta(handle="@nba"))
    r2 = payments.generate_share_code("x", "alice@x.com", _meta(handle="@cnn"))
    assert r2["reused"] is False
    assert r2["code"] != r1["code"]


# ── SC-05:redeemed 后再 verify → next_available_at = used_at + 24h ──
def test_next_available_after_redeem(tmp_files):
    r1 = payments.generate_share_code("x", "alice@x.com", _meta())
    used_at = int(time.time())
    # 模拟 24h 内的 redeem(直接调 _save_codes 改 used_at 避免 time travel)
    codes = payments._load_codes()
    codes[r1["code"]]["used"] = True
    codes[r1["code"]]["used_by"] = "alice@x.com"
    codes[r1["code"]]["used_at"] = used_at
    payments._save_codes(codes)
    # 再 verify → reused, status=redeemed
    r2 = payments.generate_share_code("x", "alice@x.com", _meta())
    assert r2["reused"] is True
    assert r2["status"] == "redeemed"
    assert r2["code"] == r1["code"]
    # next_available_at ≈ used_at + 86400(允许 ±5s 误差)
    expected = used_at + 86400
    assert abs(r2["next_available_at"] - expected) < 5


# ── SC-06:unused reused → next_available_at = 旧 expires_at ──
def test_next_available_unused_reused(tmp_files):
    r1 = payments.generate_share_code("x", "alice@x.com", _meta())
    # 不兑换,直接再 verify
    r2 = payments.generate_share_code("x", "alice@x.com", _meta())
    assert r2["reused"] is True
    assert r2["status"] == "active"
    # next_available_at = 旧 expires_at
    assert r2["next_available_at"] == r1["expires_at"]


# ── SC-07:source_meta 缺 handle 字段(向后兼容)───────────
def test_no_handle_in_meta(tmp_files):
    """老调用 / 内部传 dict 不带 handle → 仍能生成新码。"""
    r1 = payments.generate_share_code("x", "alice@x.com", {"tweet_url": "x"})
    r2 = payments.generate_share_code("x", "alice@x.com", {"tweet_url": "x"})
    # handle="" 在两边都一致 → 视为同 handle → 复用
    assert r2["reused"] is True
    assert r2["code"] == r1["code"]


# ── SC-08:跨平台(reddit vs X)不互限 ─────────────────────
def test_different_platforms_independent(tmp_files):
    r1 = payments.generate_share_code("x", "alice@x.com", _meta())
    r2 = payments.generate_share_code("reddit", "alice@x.com", _meta())
    assert r2["reused"] is False
    assert r2["code"] != r1["code"]


# ── SC-09:handle 大小写不敏感(@NBA == @nba)──────────────────
def test_handle_case_insensitive(tmp_files):
    r1 = payments.generate_share_code("x", "alice@x.com", _meta(handle="@NBA"))
    r2 = payments.generate_share_code("x", "alice@x.com", _meta(handle="@nba"))
    assert r2["reused"] is True
    assert r2["code"] == r1["code"]
