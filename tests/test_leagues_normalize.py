"""CSV 规范化:列名映射、日期/数字转换、脏行剔除。"""
import pytest

from leagues import normalize

FD_2627 = (
    "Div,Date,Time,HomeTeam,AwayTeam,FTHG,FTAG,B365H,B365D,B365A,PSH,PSD,PSA,"
    "Avg>2.5,Avg<2.5,AHh,B365AHH,B365AHA\n"
    "E0,15/08/2026,20:00,Liverpool,Arsenal,2,1,1.80,3.60,4.50,1.85,3.70,4.60,1.95,1.85,-0.5,1.90,2.00\n"
    "E0,16/08/2026,14:00,Man City,Chelsea,0,0,1.50,4.20,6.00,1.55,4.30,6.20,2.10,1.75,0,1.85,1.95\n"
)

# 老赛季(2005):只有 B365 三态,无 Pinnacle/OU/AH 列
FD_0506 = (
    "Div,Date,HomeTeam,AwayTeam,FTHG,FTAG,B365H,B365D,B365A\n"
    "SP1,20/08/2005,Barcelona,Real Madrid,3,0,1.90,3.30,3.80\n"
)

FIXTURES = (
    "Div,Date,Time,HomeTeam,AwayTeam,B365H,B365D,B365A,MaxH,MaxD,MaxA,PPH,PPD,PPA,"
    "Avg>2.5,Avg<2.5,AHh,B365AHH,B365AHA\n"
    "E0,11/10/2026,15:00,Arsenal,Tottenham,2.10,3.40,3.50,2.15,3.50,3.60,2.12,3.45,3.55,2.00,1.80,-0.25,1.88,1.98\n"
    "I1,11/10/2026,18:00,Inter,Juventus,2.40,3.10,3.20,2.50,3.20,3.30,2.45,3.15,3.25,1.90,1.90,0,1.90,1.90\n"
)


def test_parse_fd_results_full_columns():
    rows, skipped = normalize.parse_fd_results(FD_2627, "2627")
    assert skipped == 0 and len(rows) == 2
    r = rows[0]
    assert r["date"] == "2026-08-15" and r["home"] == "Liverpool" and r["away"] == "Arsenal"
    assert r["gh"] == 2 and r["ga"] == 1
    assert r["ps_h"] == 1.85 and r["ou_over"] == 1.95 and r["ah_line"] == -0.5
    assert r["season"] == "2627"


def test_parse_fd_results_old_season_missing_columns():
    """Review Focus 1:老赛季缺列 → 字段 None,行不丢、不报错。"""
    rows, skipped = normalize.parse_fd_results(FD_0506, "0506")
    assert skipped == 0 and len(rows) == 1
    r = rows[0]
    assert r["date"] == "2005-08-20" and r["b365h"] == 1.90
    assert r["ps_h"] is None and r["ou_over"] is None and r["ah_line"] is None


def test_parse_fd_results_skips_bad_rows():
    bad = FD_2627 + (
        "E0,,,NoDate,Team,1,0,2.0,3.0,3.0,,,,,,,,\n"      # 空日期
        "E0,17/08/2026,20:00,A,B,1,0,0.50,3.0,3.0,,,,,,,\n"  # 赔率 ≤1.0
        "E0,18/08/2026,20:00,A,B,99,0,2.0,3.0,3.0,,,,,,,\n"  # 比分越界
    )
    rows, skipped = normalize.parse_fd_results(bad, "2627")
    assert len(rows) == 2 and skipped == 3


def test_parse_fd_fixtures_pinnacle_open_names():
    """fixtures.csv 的 Pinnacle 列是 PPH/PPD/PPA(开盘),不是 PSH/PSD/PSA(收盘)。"""
    by_div = normalize.parse_fd_fixtures(FIXTURES)
    assert set(by_div) == {"E0", "I1"}
    r = by_div["E0"][0]
    assert r["ps_h"] == 2.12 and r["ps_d"] == 3.45 and r["ps_a"] == 3.55
    assert r["max_h"] == 2.15 and r["time"] == "15:00"
    assert by_div["I1"][0]["home"] == "Inter"


def test_normalize_date_formats():
    assert normalize.normalize_date("05/08/26") == "2026-08-05"
    assert normalize.normalize_date("15/08/2026") == "2026-08-15"
    assert normalize.normalize_date("2026-10-11") == "2026-10-11"
    with pytest.raises(ValueError):
        normalize.normalize_date("")
    with pytest.raises(ValueError):
        normalize.normalize_date(None)
