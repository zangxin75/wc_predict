# tests/test_leagues_api.py
"""联赛 API:付费门控(N-1 字段白名单)、402、404/503、限流。"""
import pytest
from fastapi.testclient import TestClient

from webapp import leagues_cache
from webapp.app import app
from webapp.rate_limit import limiter

PAID_EMAIL = "pro@example.com"

MATCH_WITH_MARKET = {
    "date": "2026-10-11", "time": "15:00", "home": "A", "away": "B",
    "status": "pre", "neutral": False, "probs": {"home": 0.5, "draw": 0.3, "away": 0.2},
    "lam_h": 1.6, "lam_a": 1.1, "top_scores": [["1-0", 0.12]],
    "ou25_over": 0.55, "btts_yes": 0.52, "low_confidence": False,
    "odds": {"b365h": 1.9, "b365d": 3.4, "b365a": 4.2},
    "market": {"p_mkt": {"home": 0.5, "draw": 0.28, "away": 0.22},
               "edges": {"home": 0.0, "draw": 0.02, "away": -0.02},
               "max_edge": 0.05, "pick": "draw",
               "odds": {"home": 1.9, "draw": 3.4, "away": 4.2}, "kelly": 0.012},
}


@pytest.fixture(autouse=True)
def seed_cache(monkeypatch):
    limiter.reset()
    monkeypatch.setattr(leagues_cache, "_payload", {})
    monkeypatch.setattr(leagues_cache, "_status", {"running": False, "last_error": None})
    leagues_cache.set_league(
        "E0",
        predictions={"code": "E0", "season": "2627", "generated_at": "2026-10-09",
                     "matches": [MATCH_WITH_MARKET]},
        standings=[{"team": "A", "pts": 3}],
        sim={"teams": {"A": {"title": 0.4, "ucl": 0.7, "releg": 0.0}}},
    )
    yield


def _all_keys(obj):
    """递归收集 payload 里所有 dict 键。"""
    keys = set()
    if isinstance(obj, dict):
        for k, v in obj.items():
            keys.add(k)
            keys |= _all_keys(v)
    elif isinstance(obj, list):
        for v in obj:
            keys |= _all_keys(v)
    return keys


# 注意:Depends(auth.get_current_user) 在路由注册时已绑定函数对象,
# monkeypatch 属性无效 —— 必须用 app.dependency_overrides 覆盖。
@pytest.fixture
def pro_client(monkeypatch):
    from webapp import auth as auth_mod
    app.dependency_overrides[auth_mod.get_current_user] = lambda: PAID_EMAIL
    monkeypatch.setattr("webapp.leagues.payments.is_paid",
                        lambda email: email == PAID_EMAIL)
    yield TestClient(app)
    app.dependency_overrides.pop(auth_mod.get_current_user, None)


@pytest.fixture
def free_client(monkeypatch):
    from webapp import auth as auth_mod
    app.dependency_overrides[auth_mod.get_current_user] = lambda: None
    monkeypatch.setattr("webapp.leagues.payments.is_paid", lambda email: False)
    yield TestClient(app)
    app.dependency_overrides.pop(auth_mod.get_current_user, None)


def test_matches_free_user_sees_all_in_free_phase(free_client):
    """第一阶段 LEAGUES_FREE=True:所有人看完整字段(含 market)。"""
    r = free_client.get("/api/leagues/E0/matches")
    assert r.status_code == 200
    m = r.json()["matches"][0]
    assert m["odds"]["b365h"] == 1.9
    assert m["market"]["pick"] == "draw"


def test_matches_gating_when_paid_phase(free_client, monkeypatch):
    """第二阶段 LEAGUES_FREE=False 时:N-1 字段白名单恢复生效。"""
    from webapp import leagues as leagues_api
    monkeypatch.setattr(leagues_api, "LEAGUES_FREE", False)
    r = free_client.get("/api/leagues/E0/matches")
    assert r.status_code == 200
    body = r.json()
    assert body["matches"][0]["odds"] is None
    assert body["matches"][0]["probs"]["home"] == 0.5    # 预测免费展示
    leaked = _all_keys(body) & {"market", "edges", "kelly", "max_edge", "p_mkt"}
    assert leaked == set(), f"付费字段泄漏: {leaked}"


def test_matches_pro_user_sees_odds_and_market(pro_client):
    r = pro_client.get("/api/leagues/E0/matches")
    assert r.status_code == 200
    m = r.json()["matches"][0]
    assert m["odds"]["b365h"] == 1.9
    assert m["market"]["pick"] == "draw" and m["market"]["kelly"] == 0.012


def test_value_free_phase_returns_200(free_client):
    """第一阶段 LEAGUES_FREE=True:价值下注免费开放。"""
    r = free_client.get("/api/leagues/E0/value")
    assert r.status_code == 200
    assert len(r.json()["values"]) == 1


def test_value_non_pro_returns_402_in_paid_phase(free_client, monkeypatch):
    from webapp import leagues as leagues_api
    monkeypatch.setattr(leagues_api, "LEAGUES_FREE", False)
    r = free_client.get("/api/leagues/E0/value")
    assert r.status_code == 402


def test_value_pro_returns_sorted_edges(pro_client):
    r = pro_client.get("/api/leagues/E0/value")
    assert r.status_code == 200
    values = r.json()["values"]
    assert len(values) == 1 and values[0]["max_edge"] == 0.05
    assert values[0]["home"] == "A" and values[0]["pick"] == "draw"


def test_standings_and_sim_free(free_client):
    r = free_client.get("/api/leagues/E0/standings")
    assert r.status_code == 200 and r.json()["standings"][0]["team"] == "A"
    r = free_client.get("/api/leagues/E0/sim")
    assert r.status_code == 200 and r.json()["sim"]["teams"]["A"]["title"] == 0.4


def test_unknown_league_404_and_not_ready_503(free_client):
    assert free_client.get("/api/leagues/XX/matches").status_code == 404
    assert free_client.get("/api/leagues/D1/matches").status_code == 503   # 未构建


def test_list_leagues(free_client):
    r = free_client.get("/api/leagues")
    assert r.status_code == 200
    leagues = {l["code"]: l for l in r.json()["leagues"]}
    assert leagues["E0"]["available"] is True
    assert leagues["D1"]["available"] is False
    assert leagues["UCL"]["name_zh"] == "欧冠"


def test_rate_limit_30_per_minute(free_client):
    codes = [free_client.get("/api/leagues/E0/standings").status_code for _ in range(31)]
    assert 429 in codes
