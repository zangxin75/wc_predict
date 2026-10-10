"""UCL:TheOddsAPI 三方命名 canonical 匹配、借用制 rebuild、降级行为。"""
import json
import os
import time

import pytest

from leagues import build, config, fetch_odds_ucl


class FakeResp:
    def __init__(self, payload):
        self._payload = payload

    def raise_for_status(self):
        pass

    def json(self):
        return self._payload


ODDS_PAYLOAD = [
    {"home_team": "Internazionale", "away_team": "AC Milan",
     "bookmakers": [
         {"markets": [{"key": "h2h", "outcomes": [
             {"name": "Internazionale", "price": 2.40},
             {"name": "Draw", "price": 3.20},
             {"name": "AC Milan", "price": 3.10}]}]},
         {"markets": [{"key": "h2h", "outcomes": [
             {"name": "Internazionale", "price": 2.50},
             {"name": "Draw", "price": 3.30},
             {"name": "AC Milan", "price": 3.00}]}]},
     ]},
    {"home_team": "Qarabag FK", "away_team": "Unknown United",
     "bookmakers": [{"markets": [{"key": "h2h", "outcomes": [
         {"name": "Qarabag FK", "price": 1.80},
         {"name": "Draw", "price": 3.60},
         {"name": "Unknown United", "price": 4.50}]}]}]},
]


def test_fetch_ucl_odds_canonical_names(monkeypatch):
    """Review Focus 4:TheOddsAPI 'Internazionale' → canonical 'Inter' 才能匹配赛程。"""
    monkeypatch.setattr(fetch_odds_ucl.requests, "get",
                        lambda url, params=None, timeout=None: FakeResp(ODDS_PAYLOAD))
    out = fetch_odds_ucl.fetch_ucl_odds(api_key="fake")
    assert ("Inter", "Milan") in out                      # 三方命名已归一
    assert out[("Inter", "Milan")]["b365h"] == pytest.approx(2.45)   # 两家均价
    assert ("Qarabag FK", "Unknown United") in out        # 未命中别名 → 原名透传
    # sport key 钉死 soccer_uefa_champs_league(spec v2 勘误)
    captured = {}

    def cap_get(url, params=None, timeout=None):
        captured["url"] = url
        return FakeResp([])

    monkeypatch.setattr(fetch_odds_ucl.requests, "get", cap_get)
    fetch_odds_ucl.fetch_ucl_odds(api_key="fake")
    assert "soccer_uefa_champs_league" in captured["url"]


def test_fetch_ucl_odds_no_key_returns_empty(monkeypatch):
    monkeypatch.delenv("THE_ODDS_API_KEY", raising=False)
    assert fetch_odds_ucl.fetch_ucl_odds() == {}


@pytest.fixture
def sandbox(tmp_path, monkeypatch):
    monkeypatch.setattr(config, "DATA_ROOT", str(tmp_path))
    monkeypatch.setenv("LEAGUES_SEASON", "2627")
    from leagues import fetch_fd, normalize
    # I1 联赛 DC 参数(供借用)
    build._atomic_json(config.paths("I1")["dc_params"], {
        "league": "I1",
        "params": {"Inter": {"attack": 0.4, "defense": -0.2, "n": 100},
                   "Milan": {"attack": 0.2, "defense": 0.0, "n": 100}},
        "global": {"beta0": 0.3, "home_adv": 0.25, "rho": 0.0, "n_matches": 380},
    })
    for c in ("E0", "SP1", "D1", "F1"):                  # 其他联赛空参数
        build._atomic_json(config.paths(c)["dc_params"],
                           {"league": c, "params": {}, "global": {"beta0": 0.3, "home_adv": 0.2, "rho": 0.0}})
        fetch_fd.write_csv(config.paths(c)["results"], normalize.RESULT_FIELDS, [])
    fetch_fd.write_csv(config.paths("UCL")["results"], normalize.UCL_RESULT_FIELDS, [
        {"date": "2026-09-16", "home": "Inter", "away": "Milan", "gh": 2, "ga": 1, "neutral": ""},
        {"date": "2026-09-17", "home": "Inter", "away": "Ajax", "gh": 3, "ga": 0, "neutral": ""},
    ])
    fetch_fd.write_csv(config.paths("UCL")["schedule"], normalize.SCHEDULE_FIELDS, [
        {"date": "2026-09-16", "time": "20:00", "home": "Inter", "away": "Milan",
         "status": "post", "gh": 2, "ga": 1, "neutral": ""},
        {"date": "2026-10-21", "time": "20:00", "home": "Inter", "away": "Ajax",
         "status": "pre", "gh": "", "ga": "", "neutral": ""},
        {"date": "2026-10-22", "time": "20:00", "home": "Milan", "away": "Ajax",
         "status": "pre", "gh": "", "ga": "", "neutral": ""},
    ])
    # UCL results 视为过期(fetch_ucl_history 的补抓门槛)
    stale = time.time() - 25 * 3600
    os.utime(config.paths("UCL")["results"], (stale, stale))
    return tmp_path


def test_rebuild_ucl_borrow_and_low_confidence(sandbox):
    elo = {"Inter": 1700.0, "Milan": 1600.0, "Ajax": 1550.0}
    p = build.rebuild_ucl(elo=elo, ucl_odds={("Inter", "Ajax"):
                                             {"b365h": 1.7, "b365d": 3.8, "b365a": 4.6}})
    assert p["code"] == "UCL" and len(p["matches"]) == 3
    post = [m for m in p["matches"] if m["status"] == "post"][0]
    assert post["gh"] == 2
    m1 = [m for m in p["matches"] if m["home"] == "Inter" and m["status"] == "pre"][0]
    assert m1["low_confidence"] is True                    # Ajax 无五大联赛归属(N-2)
    assert m1["probs"]["home"] > m1["probs"]["away"]       # 借用 Inter 强势参数
    assert "market" in m1                                  # ucl_odds 匹配成功
    m2 = [m for m in p["matches"] if m["home"] == "Milan"][0]
    assert "market" not in m2                              # 无赔率 → 无 market 键
    # dc_params 落盘含 low_confidence 标记
    with open(config.paths("UCL")["dc_params"], encoding="utf-8") as f:
        dc = json.load(f)
    assert dc["params"]["Ajax"]["low_confidence"] is True
    assert dc["params"]["Inter"]["low_confidence"] is False


def test_fetch_all_if_stale_includes_ucl(sandbox, monkeypatch):
    from leagues import fetch_espn, fetch_fd
    monkeypatch.setattr(fetch_fd, "fetch_league_history", lambda code, **kw: (5, []))
    monkeypatch.setattr(fetch_fd, "fetch_fixtures", lambda codes=None: {})
    monkeypatch.setattr(fetch_espn, "fetch_schedule", lambda code, **kw: (10, []))
    called = []
    monkeypatch.setattr(fetch_espn, "fetch_ucl_history",
                        lambda **kw: called.append(1) or (100, 5, False))
    report = build.fetch_all_if_stale()
    assert called == [1]                                   # UCL 历史被补抓
    assert report["ucl"]["rows"] == 100
