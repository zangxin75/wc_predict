"""test_fetch_wc2026.py — fetch_wc2026 单元测试(mock JSON fixture,不依赖网络)。

覆盖需求表 FETCH-01/02/04/05/06/07/10(ESPN 主路径)。
FETCH-03(Wikipedia 回退)+ FETCH-09(双源失败)见 test_double_source_failure。
"""

import json
import os
import sys
import time

import pytest

HERE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if HERE not in sys.path:
    sys.path.insert(0, HERE)

import fetch_wc2026


# ── 真实结构的 mock fixture(基于 ESPN 探索结果构造)─────────────
def _mock_event(team_h="Australia", team_a="Türkiye", hs="2", as_="0",
                state="post", completed=True, note="FIFA World Cup, Group D",
                city="Vancouver", country="Canada", date="2026-06-14T04:00Z"):
    """构造一个 ESPN event JSON(结构同真实 API)。"""
    return {
        "id": "760421",
        "date": date,
        "name": f"{team_a} at {team_h}",
        "competitions": [{
            "date": date,
            "status": {"type": {"state": state, "completed": completed}},
            "venue": {"fullName": "BC Place",
                      "address": {"city": city, "country": country}},
            "altGameNote": note,
            "competitors": [
                {"homeAway": "home", "score": hs,
                 "team": {"displayName": team_h}},
                {"homeAway": "away", "score": as_,
                 "team": {"displayName": team_a}},
            ],
        }],
    }


def _mock_scoreboard(events):
    return {"leagues": [], "season": {}, "day": {}, "events": events, "provider": {}}


# ── FETCH-02 / FETCH-07:已打比赛解析 ──────────────────────────
def test_parse_event_completed():
    """已打(post)event → 提取比分 + 分组 + 场地。"""
    ev = _mock_event(hs="2", as_="0", state="post", completed=True,
                     note="FIFA World Cup, Group D")
    r = fetch_wc2026.parse_event(ev)
    assert r is not None
    assert r["home"] == "Australia"
    assert r["away"] == "Turkey"               # Türkiye → results.csv 名
    assert r["hs"] == "2" and r["as_"] == "0"
    assert r["group"] == "D"
    assert r["stage"] == "Group"
    assert r["city"] == "Vancouver"
    assert r["country"] == "Canada"
    assert r["date"] == "2026-06-14"
    assert r["completed"] is True


def test_parse_event_scheduled():
    """未打(pre)event → state=pre, score=None, completed=False。"""
    ev = _mock_event(hs=None, as_=None, state="pre", completed=False)
    r = fetch_wc2026.parse_event(ev)
    assert r["state"] == "pre"
    assert r["hs"] is None and r["as_"] is None
    assert r["completed"] is False


# ── FETCH-05:分组 + 淘汰赛阶段提取 ──────────────────────────
def test_extract_group_and_stage_group():
    g, s = fetch_wc2026._extract_group_and_stage("FIFA World Cup, Group D")
    assert (g, s) == ("D", "Group")


def test_extract_group_and_stage_knockout():
    g, s = fetch_wc2026._extract_group_and_stage("FIFA World Cup, Round of 16")
    assert g == ""
    assert s == "Round of 16"


# ── FETCH-04:队名标准化(ESPN→results.csv)──────────────────
def test_normalize_team_known_mappings():
    assert fetch_wc2026.normalize_team("Korea Republic") == "South Korea"
    assert fetch_wc2026.normalize_team("IR Iran") == "Iran"
    assert fetch_wc2026.normalize_team("Türkiye") == "Turkey"
    assert fetch_wc2026.normalize_team("USA") == "United States"


def test_normalize_team_passthrough():
    """未覆盖的队名原样保留(Australia 等两边一致)。"""
    assert fetch_wc2026.normalize_team("Australia") == "Australia"
    assert fetch_wc2026.normalize_team("Brazil") == "Brazil"


# ── FETCH-06:已打 vs 未打分流 ───────────────────────────────
def test_split_live_schedule():
    records = [
        {"home": "A", "away": "B", "hs": "1", "as_": "0", "completed": True},
        {"home": "C", "away": "D", "hs": None, "as_": None, "completed": False},
        {"home": "E", "away": "F", "hs": "2", "as_": "2", "completed": True},
    ]
    live, sched = fetch_wc2026.split_live_schedule(records)
    assert len(live) == 2 and len(sched) == 1
    assert sched[0]["home"] == "C"


# ── FETCH-01:混合 events → live + schedule CSV ──────────────
def test_full_pipeline_mixed(tmp_path, monkeypatch):
    """2 已打 + 1 未打 → live.csv 2 行,schedule.csv 3 行(未打+已打,前端历史折叠区依赖)。"""
    events = [
        _mock_event("Brazil", "Serbia", "2", "0", note="FIFA World Cup, Group G"),
        _mock_event("Spain", "Costa Rica", "7", "0", note="FIFA World Cup, Group E"),
        _mock_event("France", "Peru", None, None, state="pre", completed=False,
                    note="FIFA World Cup, Group C"),
    ]
    data = _mock_scoreboard(events)
    monkeypatch.setattr(fetch_wc2026, "LIVE_OUT", str(tmp_path / "live.csv"))
    monkeypatch.setattr(fetch_wc2026, "SCHED_OUT", str(tmp_path / "sched.csv"))
    records = [r for r in map(fetch_wc2026.parse_event, data["events"]) if r]
    live, sched = fetch_wc2026.split_live_schedule(records)
    fetch_wc2026.write_csvs(live, sched)

    import csv
    with open(tmp_path / "live.csv", encoding="utf-8") as f:
        rows = list(csv.reader(f))
    assert rows[0] == fetch_wc2026.LIVE_HEADER     # 列头兼容 results.csv
    assert len(rows) == 3                            # 列头 + 2 已打
    assert rows[1][1] == "Brazil" and rows[1][3] == "2"
    assert rows[1][8] == "TRUE"                      # neutral 标记

    with open(tmp_path / "sched.csv", encoding="utf-8") as f:
        rows = list(csv.reader(f))
    # sched 写入顺序:未打在前,已打紧随(供前端历史折叠区渲染比分)
    assert len(rows) == 4                            # 列头 + 1 未打 + 2 已打
    assert rows[1][1] == "France" and rows[1][7] == ""   # 未打场比分空
    assert rows[2][1] == "Brazil" and rows[2][7] == "2" and rows[2][8] == "0"
    assert rows[3][1] == "Spain" and rows[3][7] == "7" and rows[3][8] == "0"
    assert rows[2][3] == "G"                         # group 列(Brazil 所在 G 组)


# ── FETCH-03:结构不全 → None(容错跳过脏数据)──────────────
def test_parse_event_missing_field_returns_none():
    """缺 competitions/competitors → None,不抛异常。"""
    assert fetch_wc2026.parse_event({"id": "x", "date": "d"}) is None
    broken = {"date": "d", "competitions": [{"competitors": []}]}
    assert fetch_wc2026.parse_event(broken) is None


# ── FETCH-08:缓存命中不发请求 ───────────────────────────────
def test_fetch_espn_cache_hit(tmp_path, monkeypatch):
    """cache 文件新鲜(< ttl)→ 直接读,不调 urlopen。"""
    cache = tmp_path / "cache.json"
    cache.write_text(json.dumps({"events": [{"cached": True}]}), encoding="utf-8")
    # mtime 设为现在(新鲜)
    os.utime(cache, (time.time(), time.time()))
    monkeypatch.setattr(fetch_wc2026, "CACHE_PATH", str(cache))

    def _boom(*a, **k):
        raise AssertionError("不应发网络请求(缓存应命中)")
    monkeypatch.setattr(fetch_wc2026.urllib.request, "urlopen", _boom)

    data = fetch_wc2026.fetch_espn(use_cache=True, ttl=300)
    assert data["events"][0]["cached"] is True


# ── FETCH-10:边界(赛事未开始,0 已打)─────────────────────
def test_split_all_scheduled():
    """全未打 → live 空,schedule 全。"""
    records = [
        {"home": "A", "away": "B", "hs": None, "as_": None, "completed": False},
    ]
    live, sched = fetch_wc2026.split_live_schedule(records)
    assert live == [] and len(sched) == 1


# ── FETCH-09:双源失败 → raise(不写空 CSV)──────────────────
def test_double_source_failure_raises(monkeypatch):
    """ESPN raise + Wikipedia NotImplementedError → main 抛 RuntimeError。"""
    monkeypatch.setattr(fetch_wc2026, "fetch_espn",
                        lambda *a, **k: (_ for _ in ()).throw(RuntimeError("net down")))
    with pytest.raises(RuntimeError, match="双源失败"):
        fetch_wc2026.main()


# ── FETCH-11:后期轮次(SF/Final)补录 ─────────────────────────
# ESPN scoreboard 对【对阵未确定】的淘汰赛轮次不返回 event,需从已打
# 上一轮结果推导补录。覆盖:推导配对 / stage 去重 / QF 不足 / Final 填充。
def _rec(date, home, away, hs, as_, stage, completed=True):
    """构造已解析 record(seed_late_rounds 的输入单元)。"""
    return {"date": date, "home": home, "away": away, "hs": hs, "as_": as_,
            "completed": completed, "stage": stage, "group": "", "city": "",
            "kickoff_utc": "", "state": "post" if completed else "pre",
            "tournament": "FIFA World Cup", "country": ""}


def test_seed_semifinals_from_completed_qf():
    """4 场 QF 完成 → 按 date 相邻配对补 2 场 SF + Final 占位。"""
    records = [
        _rec("2026-07-09", "France", "Morocco", 2, 0, "Quarterfinals"),
        _rec("2026-07-10", "Spain", "Belgium", 2, 1, "Quarterfinals"),
        _rec("2026-07-11", "Norway", "England", 1, 2, "Quarterfinals"),
        _rec("2026-07-12", "Argentina", "Switzerland", 3, 1, "Quarterfinals"),
    ]
    seeded = fetch_wc2026.seed_late_rounds(records)
    sf = [s for s in seeded if s["stage"] == "Semifinals"]
    assert len(sf) == 2
    assert (sf[0]["home"], sf[0]["away"]) == ("France", "Spain")       # QF[0]-QF[1]
    assert (sf[1]["home"], sf[1]["away"]) == ("England", "Argentina")  # QF[2]-QF[3]
    final = [s for s in seeded if s["stage"] == "Final"]
    assert len(final) == 1 and final[0]["home"] == ""                  # SF 未打,决赛待定


def test_seed_skipped_when_espn_has_round():
    """ESPN 已返回 Semifinals → 不补 SF(按 stage 去重)。"""
    records = [
        _rec("2026-07-09", "France", "Morocco", 2, 0, "Quarterfinals"),
        _rec("2026-07-10", "Spain", "Belgium", 2, 1, "Quarterfinals"),
        _rec("2026-07-11", "Norway", "England", 1, 2, "Quarterfinals"),
        _rec("2026-07-12", "Argentina", "Switzerland", 3, 1, "Quarterfinals"),
        _rec("2026-07-14", "France", "Spain", None, None, "Semifinals", completed=False),
    ]
    seeded = fetch_wc2026.seed_late_rounds(records)
    assert not any(s["stage"] == "Semifinals" for s in seeded)


def test_seed_no_semifinals_when_qf_incomplete():
    """QF 未打满 4 场 → 不补 SF(对阵无法确定),但仍补 Final 占位。"""
    records = [_rec("2026-07-09", "France", "Morocco", 2, 0, "Quarterfinals")]
    seeded = fetch_wc2026.seed_late_rounds(records)
    assert not any(s["stage"] == "Semifinals" for s in seeded)
    assert any(s["stage"] == "Final" and s["home"] == "" for s in seeded)


def test_seed_final_filled_when_semifinals_done():
    """2 场 SF 完成 → Final 补录并填对阵(SF 胜者)。"""
    records = [
        _rec("2026-07-14", "France", "Spain", 2, 1, "Semifinals"),
        _rec("2026-07-15", "England", "Argentina", 0, 1, "Semifinals"),
    ]
    seeded = fetch_wc2026.seed_late_rounds(records)
    final = [s for s in seeded if s["stage"] == "Final"]
    assert len(final) == 1
    assert (final[0]["home"], final[0]["away"]) == ("France", "Argentina")


# ── FETCH-12:Final 对阵从 results.csv 推导(生产主路径)─────────────
# ESPN 不返回已打 SF → SF 占位行比分恒 None → Final 无法从 records 推。
# 真实赛果只存在于 results.csv,seed 必须查它才能推出决赛对阵。
def test_seed_final_from_results_idx_when_sf_unplayed():
    """生产主路径:4 QF 完成 + results.csv 有两场 SF 结果(ESPN 没返回)
    → SF 占位保持无比分,Final 对阵从 results 胜者推导。"""
    records = [
        _rec("2026-07-09", "France", "Morocco", 2, 0, "Quarterfinals"),
        _rec("2026-07-10", "Spain", "Belgium", 2, 1, "Quarterfinals"),
        _rec("2026-07-11", "Norway", "England", 1, 2, "Quarterfinals"),
        _rec("2026-07-12", "Argentina", "Switzerland", 3, 1, "Quarterfinals"),
    ]
    results_idx = {
        ("2026-07-14", "France", "Spain"): (0, 1),      # Spain 胜
        ("2026-07-15", "England", "Argentina"): (1, 2),  # Argentina 胜
    }
    seeded = fetch_wc2026.seed_late_rounds(records, results_idx=results_idx)
    sf = [s for s in seeded if s["stage"] == "Semifinals"]
    assert len(sf) == 2
    # SF 占位必须保持无比分 —— 否则 split_live_schedule 会误分进 live.csv
    assert all(s["hs"] is None and s["as_"] is None for s in sf)
    final = [s for s in seeded if s["stage"] == "Final"][0]
    assert (final["home"], final["away"]) == ("Spain", "Argentina")


def test_seed_final_partial_sf_in_results():
    """results.csv 只有一场 SF → Final 只填一边,另一边待定。"""
    records = [
        _rec("2026-07-09", "France", "Morocco", 2, 0, "Quarterfinals"),
        _rec("2026-07-10", "Spain", "Belgium", 2, 1, "Quarterfinals"),
        _rec("2026-07-11", "Norway", "England", 1, 2, "Quarterfinals"),
        _rec("2026-07-12", "Argentina", "Switzerland", 3, 1, "Quarterfinals"),
    ]
    results_idx = {("2026-07-14", "France", "Spain"): (0, 1)}  # 只 SF1 已打
    seeded = fetch_wc2026.seed_late_rounds(records, results_idx=results_idx)
    final = [s for s in seeded if s["stage"] == "Final"][0]
    assert final["home"] == "Spain" and final["away"] == ""   # SF2 未打 → 客队待定


def test_load_results_index_parses_and_skips_bad_rows(tmp_path):
    """_load_results_index 解析比分,跳过 NA/残缺行,文件缺时返回 {}。"""
    p = tmp_path / "results.csv"
    p.write_text(
        "date,home_team,away_team,home_score,away_score,city\n"
        '2026-07-14,France,Spain,0,1,"Arlington"\n'
        "2026-07-15,England,Argentina,NA,NA,X\n"
        "2026-07-16,Bad,Row\n",
        encoding="utf-8",
    )
    idx = fetch_wc2026._load_results_index(str(p))
    assert idx == {("2026-07-14", "France", "Spain"): (0, 1)}
    assert fetch_wc2026._load_results_index(str(tmp_path / "nope.csv")) == {}


# ── FETCH-13:Third Place(季军赛)补录 ──────────────────────────
# 三四名 = 两场 SF 的输家对阵,需 SF 已打(查 results.csv,ESPN 同样不返回)。
def test_seed_third_place_from_sf_losers():
    """4 QF 完成 + results.csv 有两场 SF 结果 → 补 Third Place(SF 输家对阵)。"""
    records = [
        _rec("2026-07-09", "France", "Morocco", 2, 0, "Quarterfinals"),
        _rec("2026-07-10", "Spain", "Belgium", 2, 1, "Quarterfinals"),
        _rec("2026-07-11", "Norway", "England", 1, 2, "Quarterfinals"),
        _rec("2026-07-12", "Argentina", "Switzerland", 3, 1, "Quarterfinals"),
    ]
    results_idx = {
        ("2026-07-14", "France", "Spain"): (0, 1),      # Spain 胜 → France 输
        ("2026-07-15", "England", "Argentina"): (1, 2),  # Argentina 胜 → England 输
    }
    seeded = fetch_wc2026.seed_late_rounds(records, results_idx=results_idx)
    tp = [s for s in seeded if s["stage"] == "Third Place"]
    assert len(tp) == 1
    assert (tp[0]["home"], tp[0]["away"]) == ("France", "England")  # SF 两边输家


def test_seed_no_third_place_when_sf_unplayed():
    """SF 未打(results 无 SF 比分)→ 不补 Third Place(对阵推不出)。"""
    records = [
        _rec("2026-07-09", "France", "Morocco", 2, 0, "Quarterfinals"),
        _rec("2026-07-10", "Spain", "Belgium", 2, 1, "Quarterfinals"),
        _rec("2026-07-11", "Norway", "England", 1, 2, "Quarterfinals"),
        _rec("2026-07-12", "Argentina", "Switzerland", 3, 1, "Quarterfinals"),
    ]
    seeded = fetch_wc2026.seed_late_rounds(records)  # 无 results_idx
    assert not any(s["stage"] == "Third Place" for s in seeded)
