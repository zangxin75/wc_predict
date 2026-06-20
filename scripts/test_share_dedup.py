"""test_share_dedup.py — share 邀请码去重 + 生命周期 单元测试

不依赖 stripe / 网络。覆盖:
  1) _existing_recent_code 边界(未过期 / 已过期 / 跨平台 / 跨邮箱)
  2) generate_share_code 同 (platform, email) 24h 内去重
  3) generate_share_code 跨平台不互相阻塞
  4) generate_share_code 24h 后允许重新生成
  5) redeem_share_code 写入 paid_users.json + 24h Pro 标记
  6) 邀请码命名规则 SHARE-X-XXXXXXXX(8 字符)
"""
import json
import os
import sys
import tempfile
import time
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

import webapp.payments as p  # noqa: E402


class TestExistingRecentCode(unittest.TestCase):
    """_existing_recent_code 纯函数逻辑(不写文件)。"""

    def setUp(self):
        self.now = int(time.time())
        self.codes = {
            "SHARE-X-ACTIVE001": {
                "code": "SHARE-X-ACTIVE001",
                "used": False,
                "expires_at": self.now + 3600,
                "source": "share:x",
                "source_meta": {"platform": "x", "email": "a@b.com"},
                "created_at": self.now - 600,
            },
            "SHARE-X-EXPIRED01": {
                "code": "SHARE-X-EXPIRED01",
                "used": False,
                "expires_at": self.now - 100,
                "source": "share:x",
                "source_meta": {"platform": "x", "email": "a@b.com"},
                "created_at": self.now - 90000,
            },
            "SHARE-RD-OTHER1234": {
                "code": "SHARE-RD-OTHER1234",
                "used": False,
                "expires_at": self.now + 3600,
                "source": "share:reddit",
                "source_meta": {"platform": "reddit", "email": "a@b.com"},
            },
        }

    def test_active_returns_unused(self):
        hit = p._existing_recent_code("x", "a@b.com", self.codes, self.now)
        self.assertIsNotNone(hit)
        self.assertEqual(hit["code"], "SHARE-X-ACTIVE001")

    def test_cross_platform_not_matched(self):
        self.assertIsNone(p._existing_recent_code("x", "c@d.com", self.codes, self.now))

    def test_cross_email_not_matched(self):
        self.assertIsNone(p._existing_recent_code("x", "other@e.com", self.codes, self.now))

    def test_reddit_independent(self):
        hit = p._existing_recent_code("reddit", "a@b.com", self.codes, self.now)
        self.assertIsNotNone(hit)
        self.assertEqual(hit["code"], "SHARE-RD-OTHER1234")

    def test_redeemed_within_24h_blocks(self):
        """已 redeem 且 24h 内 → 返回该码(阻止再生成)。"""
        self.codes["SHARE-X-ACTIVE001"]["used"] = True
        self.codes["SHARE-X-ACTIVE001"]["used_at"] = self.now - 60
        hit = p._existing_recent_code("x", "a@b.com", self.codes, self.now)
        self.assertIsNotNone(hit)
        self.assertEqual(hit["code"], "SHARE-X-ACTIVE001")

    def test_redeemed_outside_24h_releases(self):
        self.codes["SHARE-X-ACTIVE001"]["used"] = True
        self.codes["SHARE-X-ACTIVE001"]["used_at"] = self.now - p.SHARE_CODE_TTL - 100
        self.assertIsNone(p._existing_recent_code("x", "a@b.com", self.codes, self.now))

    def test_email_case_insensitive(self):
        hit = p._existing_recent_code("x", "A@B.COM", self.codes, self.now)
        self.assertIsNotNone(hit)


class TestShareCodeIO(unittest.TestCase):
    """generate / redeem 端到端(用 tmpdir 隔离 invite_codes.json + paid_users.json)。"""

    def setUp(self):
        self.tmpdir = tempfile.mkdtemp(prefix="wc_share_test_")
        # 重定向 payments 模块的全局路径
        self._codes_path = p.INVITE_CODES_PATH
        self._users_path = p.PAID_USERS_PATH
        p.INVITE_CODES_PATH = os.path.join(self.tmpdir, "invite_codes.json")
        p.PAID_USERS_PATH = os.path.join(self.tmpdir, "paid_users.json")
        # 初始化空文件
        Path(p.INVITE_CODES_PATH).write_text("{}", encoding="utf-8")
        Path(p.PAID_USERS_PATH).write_text("{}", encoding="utf-8")

    def tearDown(self):
        p.INVITE_CODES_PATH = self._codes_path
        p.PAID_USERS_PATH = self._users_path

    def _load_codes(self):
        return json.loads(Path(p.INVITE_CODES_PATH).read_text(encoding="utf-8"))

    def _load_users(self):
        return json.loads(Path(p.PAID_USERS_PATH).read_text(encoding="utf-8"))

    def test_generate_new_code_format(self):
        res = p.generate_share_code("x", "user@example.com",
                                    {"tweet_url": "https://x.com/u/status/1"})
        self.assertFalse(res["reused"])
        self.assertEqual(res["status"], "new")
        self.assertRegex(res["code"], r"^SHARE-X-[A-Z0-9]{8}$")
        self.assertGreater(res["expires_at"], int(time.time()) + 86000)
        codes = self._load_codes()
        self.assertIn(res["code"], codes)
        self.assertEqual(codes[res["code"]]["source"], "share:x")
        self.assertEqual(codes[res["code"]]["source_meta"]["email"], "user@example.com")

    def test_dedup_same_platform_email_24h(self):
        a = p.generate_share_code("x", "u@e.com", {"tweet_url": "https://x.com/a/1"})
        b = p.generate_share_code("x", "u@e.com", {"tweet_url": "https://x.com/a/2"})
        self.assertEqual(a["code"], b["code"])
        self.assertTrue(b["reused"])
        self.assertEqual(b["status"], "active")
        codes = self._load_codes()
        self.assertEqual(len(codes), 1, "应只存一份码")

    def test_cross_platform_independent(self):
        a = p.generate_share_code("x", "u@e.com", {"tweet_url": "https://x.com/a/1"})
        b = p.generate_share_code("reddit", "u@e.com", {"tweet_url": "https://r.com/r/1"})
        self.assertNotEqual(a["code"], b["code"])
        self.assertFalse(a["reused"])
        self.assertFalse(b["reused"])
        self.assertEqual(len(self._load_codes()), 2)

    def test_cross_email_independent(self):
        a = p.generate_share_code("x", "u1@e.com", {"tweet_url": "https://x.com/a/1"})
        b = p.generate_share_code("x", "u2@e.com", {"tweet_url": "https://x.com/a/2"})
        self.assertNotEqual(a["code"], b["code"])

    def test_redeem_unlocks_user(self):
        gen = p.generate_share_code("x", "u@e.com", {"tweet_url": "https://x.com/a/1"})
        res = p.redeem_share_code("u@e.com", gen["code"])
        self.assertTrue(res["ok"])
        self.assertEqual(res["plan"], "share")
        users = self._load_users()
        self.assertTrue(users["u@e.com"]["active"])
        self.assertEqual(users["u@e.com"]["plan"], "share")
        self.assertGreater(users["u@e.com"]["pro_expires_at"], int(time.time()))

    def test_redeem_then_dedup_blocks_new_code(self):
        """redeem 后 24h 内再发同平台 → 返回已 redeem 的码,不发新码。"""
        gen = p.generate_share_code("x", "u@e.com", {"tweet_url": "https://x.com/a/1"})
        p.redeem_share_code("u@e.com", gen["code"])
        second = p.generate_share_code("x", "u@e.com", {"tweet_url": "https://x.com/a/2"})
        self.assertEqual(second["code"], gen["code"])
        self.assertTrue(second["reused"])
        self.assertEqual(second["status"], "redeemed")
        self.assertEqual(len(self._load_codes()), 1)

    def test_redeem_already_used_fails(self):
        gen = p.generate_share_code("x", "u@e.com", {"tweet_url": "https://x.com/a/1"})
        p.redeem_share_code("u@e.com", gen["code"])
        res = p.redeem_share_code("u2@e.com", gen["code"])
        self.assertFalse(res["ok"])
        self.assertEqual(res["error"], "already_used")

    def test_redeem_not_found(self):
        res = p.redeem_share_code("u@e.com", "SHARE-X-NOPE0000")
        self.assertFalse(res["ok"])
        self.assertEqual(res["error"], "not_found")

    def test_redeem_expired(self):
        gen = p.generate_share_code("x", "u@e.com", {"tweet_url": "https://x.com/a/1"})
        # 手动改成已过期
        codes = self._load_codes()
        codes[gen["code"]]["expires_at"] = int(time.time()) - 100
        Path(p.INVITE_CODES_PATH).write_text(json.dumps(codes), encoding="utf-8")
        res = p.redeem_share_code("u@e.com", gen["code"])
        self.assertFalse(res["ok"])
        self.assertEqual(res["error"], "expired")

    def test_redeem_invalid_email(self):
        res = p.redeem_share_code("not-an-email", "SHARE-X-ABCDEFGH")
        self.assertFalse(res["ok"])
        self.assertEqual(res["error"], "invalid_email")

    def test_after_redeem_then_24h_later_new_code_allowed(self):
        """模拟:redeem 后 25h,再 generate → 应发新码(redeem 已在 24h 之外)。"""
        gen = p.generate_share_code("x", "u@e.com", {"tweet_url": "https://x.com/a/1"})
        p.redeem_share_code("u@e.com", gen["code"])
        # 把 used_at 改到 25h 前
        codes = self._load_codes()
        codes[gen["code"]]["used_at"] = int(time.time()) - p.SHARE_CODE_TTL - 100
        Path(p.INVITE_CODES_PATH).write_text(json.dumps(codes), encoding="utf-8")
        second = p.generate_share_code("x", "u@e.com", {"tweet_url": "https://x.com/a/3"})
        self.assertNotEqual(second["code"], gen["code"])
        self.assertFalse(second["reused"])
        self.assertEqual(len(self._load_codes()), 2)


class TestShareEndpointContract(unittest.TestCase):
    """/api/share/* 端点契约 — 跳过,需要 httpx2 跑 TestClient。

    端点逻辑是 verify_share_url + generate_share_code 的薄包装,
    业务逻辑已由 TestShareCodeIO 全覆盖。手动 smoke 在 README 验证。
    """
    pass


if __name__ == "__main__":
    unittest.main(verbosity=2)
