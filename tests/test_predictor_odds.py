"""odds-devig 模型接入三入口的单测(predict_one / predict_match / predict_upcoming)。

验证异源优先契约:有当届收盘报价→source="odds"、1X2 用 devig;无→source="elo"。
比分/lambda 始终 Elo(odds 无比分维度)。odds_map=None 时行为同改造前(回归安全)。
"""
import os
import sys

import pytest

# 确保项目根在 sys.path(从 tests/ 运行时)
ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)

from webapp import predictor  # noqa: E402
import compute_odds  # noqa: E402


def _odds_map():
    """2014 决赛 Germany-Argentina 的收盘报价(主=Argentina? 这里用Germany主)。"""
    return {("2014", "Germany", "Argentina"): (2.0, 3.2, 3.8)}


def _elo():
    return {"Germany": 2080.0, "Argentina": 1990.0, "Brazil": 2060.0}


# ── predict_one:odds 路径 ──────────────────────────────────────────
def test_predict_one_uses_odds_when_available():
    r = predictor.predict_one("Germany", "Argentina", _elo(),
                              odds_map=_odds_map(), year="2014")
    assert r["source"] == "odds"
    expected = compute_odds.devig_power(2.0, 3.2, 3.8)
    assert [round(p, 4) for p in r["probs"]] == [round(p, 4) for p in expected]
    # 比分/lambda 仍 Elo(odds 无比分)
    assert r["top_score"] is not None and r["lam_h"] > 0


def test_predict_one_falls_back_to_elo_when_year_missing():
    r = predictor.predict_one("Germany", "Argentina", _elo(),
                              odds_map=_odds_map(), year="2026")
    assert r["source"] == "elo"


def test_predict_one_regression_when_odds_map_none():
    """odds_map=None:行为同改造前,source=elo,无报错。"""
    r = predictor.predict_one("Germany", "Argentina", _elo())
    assert r["source"] == "elo"
    assert sum(r["probs"]) == pytest.approx(1.0, abs=1e-3)


def test_predict_one_reverse_fixture_lookup():
    """主客颠倒应自动兜底(lookup_odds 已支持 reverse)。"""
    r = predictor.predict_one("Argentina", "Germany", _elo(),
                              odds_map=_odds_map(), year="2014")
    assert r["source"] == "odds"


def test_predict_one_returns_none_on_elo_missing():
    assert predictor.predict_one("Atlantis", "Lemuria", _elo(),
                                 odds_map=_odds_map(), year="2014") is None
