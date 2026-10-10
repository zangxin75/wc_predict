# tests/test_leagues_fetch_espn.py
"""ESPN 按月迭代抓取:limit=400、缺月统计、中立场、UCL 降级检测。"""
import pytest

from leagues import config, fetch_espn


def _event(home, away, state="post", hs="2", as_="1", date="2026-10-11T15:00Z", neutral=False):
    return {
        "date": date,
        "status": {"type": {"state": state}},
        "competitions": [{
            "neutralSite": neutral,
            "competitors": [
                {"homeAway": "home", "team": {"displayName": home}, "score": hs},
                {"homeAway": "away", "team": {"displayName": away}, "score": as_},
            ],
        }],
    }


class FakeResp:
    def __init__(self, payload):
        self._payload = payload

    def raise_for_status(self):
        pass

    def json(self):
        return self._payload


def test_season_months():
    ms = fetch_espn.season_months("2627")
    assert ms == ["202608", "202609", "202610", "202611", "202612",
                  "202701", "202702", "202703", "202704", "202705", "202706", "202707"]


def test_espn_month_sends_limit_400(monkeypatch):
    """Review Focus 2:不带 limit 默认 50 会静默截断欧冠大月份。"""
    captured = {}

    def handler(url, params=None, timeout=None):
        captured.update(params or {})
        return FakeResp({"events": []})

    monkeypatch.setattr(fetch_espn.requests, "get", handler)
    monkeypatch.setattr(fetch_espn.time, "sleep", lambda s: None)
    fetch_espn.espn_month("eng.1", "202610")
    assert captured["dates"] == "202610" and captured["limit"] == 400


def test_parse_event_post_and_neutral():
    aliases = config.load_aliases()
    row = fetch_espn.parse_event(_event("Manchester City", "Arsenal"), aliases)
    assert row == {"date": "2026-10-11", "time": "15:00", "home": "Man City", "away": "Arsenal",
                   "status": "post", "gh": 2, "ga": 1, "neutral": False}
    n = fetch_espn.parse_event(_event("Real Madrid", "FC Barcelona", neutral=True), aliases)
    assert n["neutral"] is True and n["away"] == "Barcelona"
    pre = fetch_espn.parse_event(_event("Arsenal", "Chelsea", state="pre"), aliases)
    assert pre["status"] == "pre" and pre["gh"] == "" and pre["ga"] == ""


def test_parse_event_rejects_malformed():
    assert fetch_espn.parse_event({"date": "2026-10-11T15:00Z"}, {}) is None
    ev = _event("A", "B")
    ev["competitions"][0]["competitors"] = [ev["competitions"][0]["competitors"][0]]
    assert fetch_espn.parse_event(ev, {}) is None


def test_fetch_schedule_counts_missing_months(tmp_path, monkeypatch):
    """N-3:空 events 或请求异常均计为缺月。"""
    monkeypatch.setattr(config, "DATA_ROOT", str(tmp_path))
    monkeypatch.setenv("LEAGUES_SEASON", "2627")

    def fake_month(espn, ym):
        if ym == "202610":
            return [_event("Arsenal", "Chelsea")]
        if ym == "202611":
            raise RuntimeError("boom")              # 请求异常 = 缺月
        return []                                   # 空 events = 缺月

    monkeypatch.setattr(fetch_espn, "espn_month", fake_month)
    n, missing = fetch_espn.fetch_schedule("E0")
    assert n == 1
    assert set(missing) == set(fetch_espn.season_months("2627")) - {"202610"}


def test_fetch_schedule_all_empty_raises(tmp_path, monkeypatch):
    monkeypatch.setattr(config, "DATA_ROOT", str(tmp_path))
    monkeypatch.setattr(fetch_espn, "espn_month", lambda e, ym: [])
    with pytest.raises(RuntimeError):
        fetch_espn.fetch_schedule("E0", season="2627")


def test_fetch_ucl_history_degraded_flag(tmp_path, monkeypatch):
    monkeypatch.setattr(config, "DATA_ROOT", str(tmp_path))
    monkeypatch.setenv("LEAGUES_SEASON", "2627")

    def fake_month(espn, ym):
        if ym in ("202609", "202610"):               # 只有这两个月有完赛
            return [_event("Real Madrid", "Manchester City"),
                    _event("Arsenal", "Chelsea", state="pre")]
        return []                                   # 其余全缺(休赛期也算缺,只影响统计)

    monkeypatch.setattr(fetch_espn, "espn_month", fake_month)
    n, total_missing, degraded = fetch_espn.fetch_ucl_history(start_year=2025, end_year=2027)
    assert n == 2                                    # pre 那场被过滤
    assert total_missing == 22                       # 2 个赛季 × 11 个缺月
    assert degraded is True                          # 202611..202608 跨年连续缺 > 2


def test_max_consecutive():
    assert fetch_espn._max_consecutive([]) == 0
    assert fetch_espn._max_consecutive(["202610", "202611", "202612", "202701"]) == 4
    assert fetch_espn._max_consecutive(["202610", "202612"]) == 1
