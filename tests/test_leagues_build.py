# tests/test_leagues_build.py
"""构建管线:积分榜、Elo 池落盘、rebuild 无过期键、数字转换。"""
import json
import os

import pytest

from leagues import build, config


@pytest.fixture
def sandbox(tmp_path, monkeypatch):
    """把 DATA_ROOT 指到 tmp,写读互不影响真实数据。"""
    monkeypatch.setattr(config, "DATA_ROOT", str(tmp_path))
    return tmp_path


def _write_results(code, rows):
    from leagues import fetch_fd, normalize
    fetch_fd.write_csv(config.paths(code)["results"], normalize.RESULT_FIELDS, rows)


def _write_schedule(code, rows):
    from leagues import fetch_fd, normalize
    fetch_fd.write_csv(config.paths(code)["schedule"], normalize.SCHEDULE_FIELDS, rows)


def _r(season, date, home, away, gh, ga, **kw):
    row = {"season": season, "date": date, "home": home, "away": away, "gh": gh, "ga": ga}
    row.update(kw)
    return row


def test_compute_standings_math(sandbox):
    _write_results("E0", [
        _r("2627", "2026-08-15", "LIV", "ARS", 2, 1),
        _r("2627", "2026-08-22", "ARS", "CHE", 1, 0),
        _r("2627", "2026-08-29", "LIV", "CHE", 1, 1),
        _r("2526", "2026-05-01", "CHE", "LIV", 5, 0),   # 旧赛季,不计入
    ])
    table = build.compute_standings(build.load_results("E0"), "2627")
    assert [t["team"] for t in table] == ["LIV", "ARS", "CHE"]
    assert table[0]["pts"] == 4 and table[0]["gd"] == 1
    assert table[1]["pts"] == 3
    assert table[2]["pts"] == 1 and table[2]["p"] == 2


def test_load_results_type_conversion_and_neutral(sandbox):
    _write_results("UCL", [
        {"date": "2026-05-30", "home": "A", "away": "B", "gh": "1", "ga": "0",
         "neutral": "True"},
        {"date": "2026-05-31", "home": "C", "away": "D", "gh": "2", "ga": "2",
         "neutral": ""},
    ])
    rows = build.load_results("UCL")
    assert rows[0]["gh"] == 1 and rows[0]["neutral"] is True and rows[0]["comp"] == "UCL"
    assert rows[1]["neutral"] is False
    assert build.load_results("SP1") == []          # 文件不存在 → []


def test_build_elo_pool_writes_files(sandbox):
    _write_results("E0", [_r("2627", "2026-08-15", "A", "B", 2, 0)])
    _write_results("SP1", [_r("2627", "2026-08-16", "C", "D", 0, 1)])
    ratings, history = build.build_elo_pool()
    assert ratings["A"] > ratings["B"] and ratings["D"] > ratings["C"]
    gp = config.global_paths()
    assert os.path.exists(gp["elo"]) and os.path.exists(gp["elo_history"])
    with open(gp["elo"], encoding="utf-8") as f:
        assert json.load(f)["A"] == ratings["A"]


def test_rebuild_predictions_no_stale_keys(sandbox, monkeypatch):
    """Review Focus 3:改期后 fixtures 键过期 → rebuild 从最新 schedule 全量重建。"""
    monkeypatch.setenv("LEAGUES_SEASON", "2627")
    _write_results("E0", [_r("2627", "2026-08-15", "A", "B", 2, 0)] * 25)  # 让 DC 有样本
    _write_schedule("E0", [
        {"date": "2026-10-11", "time": "15:00", "home": "A", "away": "B",
         "status": "pre", "gh": "", "ga": "", "neutral": ""},
        {"date": "2026-10-11", "time": "17:00", "home": "C", "away": "D",
         "status": "pre", "gh": "", "ga": "", "neutral": ""},
        {"date": "2026-10-04", "time": "15:00", "home": "A", "away": "C",
         "status": "post", "gh": "2", "ga": "1", "neutral": ""},
    ])
    elo = {"A": 1600.0, "B": 1500.0, "C": 1550.0, "D": 1450.0}
    p1 = build.rebuild("E0", elo=elo)
    assert len(p1["matches"]) == 3
    # 改期:第 2 场被移走 → schedule 只剩 2 行
    _write_schedule("E0", [
        {"date": "2026-10-11", "time": "15:00", "home": "A", "away": "B",
         "status": "pre", "gh": "", "ga": "", "neutral": ""},
        {"date": "2026-10-04", "time": "15:00", "home": "A", "away": "C",
         "status": "post", "gh": "2", "ga": "1", "neutral": ""},
    ])
    p2 = build.rebuild("E0", elo=elo)
    assert len(p2["matches"]) == 2
    assert all(m["away"] != "D" for m in p2["matches"])     # 过期键不残留
    pre = [m for m in p2["matches"] if m["status"] == "pre"][0]
    assert "probs" in pre and "lam_h" in pre
    post = [m for m in p2["matches"] if m["status"] == "post"][0]
    assert post["gh"] == 2 and "probs" not in post          # 完赛场只展示比分
    # 落盘文件同步
    with open(config.paths("E0")["predictions"], encoding="utf-8") as f:
        assert len(json.load(f)["matches"]) == 2


def test_rebuild_attaches_fixture_odds(sandbox, monkeypatch):
    monkeypatch.setenv("LEAGUES_SEASON", "2627")
    from leagues import fetch_fd, normalize
    _write_results("E0", [_r("2627", "2026-08-15", "A", "B", 2, 0)] * 25)
    _write_schedule("E0", [
        {"date": "2026-10-11", "time": "15:00", "home": "A", "away": "B",
         "status": "pre", "gh": "", "ga": "", "neutral": ""},
    ])
    fetch_fd.write_csv(config.paths("E0")["fixtures"], normalize.FIXTURE_FIELDS, [
        {"date": "2026-10-11", "time": "15:00", "home": "A", "away": "B",
         "b365h": 1.9, "b365d": 3.4, "b365a": 4.2},
    ])
    p = build.rebuild("E0", elo={"A": 1600.0, "B": 1500.0})
    m = p["matches"][0]
    assert m["odds"]["b365h"] == 1.9
    assert "market" in m                            # 有赔率 → 出 market(Pro 字段,API 层再过滤)


def test_fetch_all_if_stale_isolates_failures(sandbox, monkeypatch):
    monkeypatch.setenv("LEAGUES_SEASON", "2627")
    from leagues import fetch_espn, fetch_fd

    monkeypatch.setattr(fetch_fd, "fetch_league_history",
                        lambda code, **kw: (_ for _ in ()).throw(RuntimeError("boom"))
                        if code == "D1" else (10, []))
    monkeypatch.setattr(fetch_fd, "fetch_fixtures", lambda codes=None: {c: 1 for c in codes})
    monkeypatch.setattr(fetch_espn, "fetch_schedule", lambda code, **kw: (30, []))
    report = build.fetch_all_if_stale()
    assert any("D1" in e for e in report["errors"])
    assert report["fd_history"]["E0"]["rows"] == 10  # 其他联赛不受影响
