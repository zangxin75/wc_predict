# tests/test_leagues_fetch_fd.py
"""football-data 抓取:重试、原子写、单季容错、新鲜度。"""
import os
import time

import pytest

from leagues import config, fetch_fd

CSV_A = "Div,Date,HomeTeam,AwayTeam,FTHG,FTAG,B365H,B365D,B365A\nE0,15/08/2026,A,B,1,0,2.0,3.4,3.6\n"
CSV_B = "Div,Date,HomeTeam,AwayTeam,FTHG,FTAG,B365H,B365D,B365A\nE0,16/08/2025,C,D,0,0,2.1,3.3,3.5\n"


class FakeResp:
    def __init__(self, text, status=200):
        self.content = text.encode("utf-8-sig")   # 模拟 BOM
        self.status_code = status

    def raise_for_status(self):
        if self.status_code != 200:
            raise RuntimeError(f"HTTP {self.status_code}")


def _patch_get(monkeypatch, handler):
    monkeypatch.setattr(fetch_fd.requests, "get", handler)
    monkeypatch.setattr(fetch_fd.time, "sleep", lambda s: None)   # 重试不真睡


def test_get_retries_then_success(monkeypatch):
    calls = []

    def handler(url, timeout):
        calls.append(url)
        if len(calls) < 3:
            raise RuntimeError("boom")
        return FakeResp("ok")

    _patch_get(monkeypatch, handler)
    assert fetch_fd._get("http://x") == "ok" and len(calls) == 3


def test_get_raises_after_3_failures(monkeypatch):
    def handler(url, timeout):
        raise RuntimeError("boom")

    _patch_get(monkeypatch, handler)
    with pytest.raises(RuntimeError):
        fetch_fd._get("http://x")


def test_write_csv_atomic_and_bom_decode(tmp_path):
    p = str(tmp_path / "out.csv")
    fetch_fd.write_csv(p, ["a", "b"], [{"a": 1, "b": "中文"}])
    with open(p, encoding="utf-8") as f:
        content = f.read()
    assert "中文" in content and not os.path.exists(p + ".tmp")


def test_is_fresh(tmp_path):
    p = tmp_path / "f.txt"
    assert not fetch_fd.is_fresh(str(p), 1)          # 不存在
    p.write_text("x")
    assert fetch_fd.is_fresh(str(p), 1)
    old = time.time() - 7200
    os.utime(str(p), (old, old))
    assert not fetch_fd.is_fresh(str(p), 1)          # 2 小时前,阈值 1 小时


def test_fetch_league_history_skips_failed_seasons(tmp_path, monkeypatch):
    monkeypatch.setattr(config, "DATA_ROOT", str(tmp_path))
    monkeypatch.setenv("LEAGUES_SEASON", "2627")
    seen = []

    def handler(url, timeout):
        season = url.split("/")[-2]
        seen.append(season)
        if season == "2425":
            raise RuntimeError("boom")               # 单季失败
        return FakeResp(CSV_A)

    _patch_get(monkeypatch, handler)
    n, failed = fetch_fd.fetch_league_history("E0", start=2024)
    assert seen == ["2425", "2526", "2627"]
    assert failed == ["2425"] and n == 2             # 两季成功各 1 行
    rows = fetch_fd.build_read_back(config.paths("E0")["results"])
    assert len(rows) == 2


def test_fetch_league_history_all_failed_raises(tmp_path, monkeypatch):
    monkeypatch.setattr(config, "DATA_ROOT", str(tmp_path))

    def handler(url, timeout):
        raise RuntimeError("boom")

    _patch_get(monkeypatch, handler)
    with pytest.raises(RuntimeError):
        fetch_fd.fetch_league_history("E0", start=2025, end_year=2026)


def test_fetch_fixtures_splits_by_div(tmp_path, monkeypatch):
    monkeypatch.setattr(config, "DATA_ROOT", str(tmp_path))
    text = (
        "Div,Date,Time,HomeTeam,AwayTeam,B365H,B365D,B365A,MaxH,MaxD,MaxA,PPH,PPD,PPA,"
        "Avg>2.5,Avg<2.5,AHh,B365AHH,B365AHA\n"
        "E0,11/10/2026,15:00,Arsenal,Tottenham,2.1,3.4,3.5,2.2,3.5,3.6,2.12,3.45,3.55,2.0,1.8,-0.25,1.88,1.98\n"
        "I1,11/10/2026,18:00,Inter,Juventus,2.4,3.1,3.2,2.5,3.2,3.3,2.45,3.15,3.25,1.9,1.9,0,1.9,1.9\n"
        "D2,11/10/2026,12:00,X,Y,2.0,3.0,4.0,2.1,3.1,4.1,2.0,3.0,4.0,2.0,1.8,0,1.9,1.9\n"
    )
    _patch_get(monkeypatch, lambda url, timeout: FakeResp(text))
    out = fetch_fd.fetch_fixtures(codes=["E0", "I1"])
    assert out == {"E0": 1, "I1": 1}                 # D2(英冠)被忽略
    assert os.path.exists(config.paths("I1")["fixtures"])
