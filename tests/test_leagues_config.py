"""联赛配置注册表 + 赛季计算 + 队名规范化。"""
import datetime

from leagues import config


def test_current_season_august_boundary():
    assert config.current_season(datetime.date(2026, 7, 31)) == "2526"
    assert config.current_season(datetime.date(2026, 8, 1)) == "2627"
    assert config.current_season(datetime.date(2026, 10, 9)) == "2627"
    assert config.current_season(datetime.date(2027, 3, 15)) == "2627"


def test_current_season_env_override(monkeypatch):
    monkeypatch.setenv("LEAGUES_SEASON", "2021")
    assert config.current_season(datetime.date(2026, 10, 9)) == "2021"


def test_season_years():
    assert config.season_years("2627") == (2026, 2027)
    assert config.season_years("9900") == (1999, 2000)


def test_league_registry_shape():
    assert config.LEAGUE_CODES == ["E0", "SP1", "I1", "D1", "F1"]
    assert config.ALL_CODES == ["E0", "SP1", "I1", "D1", "F1", "UCL"]
    for code in config.ALL_CODES:
        lg = config.LEAGUES[code]
        assert lg["espn"] and lg["name"] and lg["name_zh"]
    assert config.LEAGUES["D1"]["relegation_n"] == 2   # 德甲 18 队降 2
    assert config.LEAGUES["F1"]["ucl_spots"] == 3
    assert config.LEAGUES["E0"]["ucl_spots"] == 4
    # TheOddsAPI UCL sport key 钉死(spec v2 勘误,写错会静默抓空)
    assert config.LEAGUES["UCL"]["odds_sport_key"] == "soccer_uefa_champs_league"


def test_paths_and_global_paths(tmp_path, monkeypatch):
    monkeypatch.setattr(config, "DATA_ROOT", str(tmp_path))
    p = config.paths("E0")
    assert set(p) == {"results", "schedule", "fixtures", "predictions", "dc_params", "sim"}
    assert p["results"].endswith("e0/results.csv".replace("/", __import__("os").sep))
    g = config.global_paths()
    assert set(g) == {"elo", "elo_history", "xgb"}


def test_aliases_canonical():
    aliases = config.load_aliases()
    assert aliases["Manchester City"] == "Man City"
    assert aliases["Atlético Madrid"] == "Ath Madrid"
    assert aliases["Nottingham Forest"] == "Nott'm Forest"
    assert aliases["Borussia Mönchengladbach"] == "M'gladbach"
    assert aliases["Paris Saint-Germain"] == "Paris SG"
    assert aliases["Internazionale"] == "Inter"
    # 未命中 → 原样返回(strip);命中前也先 strip
    assert config.canonical("Unknown FC", aliases) == "Unknown FC"
    assert config.canonical("  Man City  ", aliases) == "Man City"


def test_backfill_constants():
    assert config.BACKFILL_START == 2000
    assert config.UCL_HISTORY_START == 2015
