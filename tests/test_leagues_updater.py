"""联赛更新器:全联赛写入、单联赛失败隔离、容错铁律(异常不出线程)。"""
import pytest

from leagues import config
from webapp import leagues_cache, leagues_updater


@pytest.fixture(autouse=True)
def tmp_cache(tmp_path, monkeypatch):
    monkeypatch.setattr(leagues_cache, "CACHE_FILE", str(tmp_path / "leagues_cache.json"))
    monkeypatch.setattr(leagues_cache, "_payload", {})
    monkeypatch.setattr(leagues_cache, "_status",
                        {"running": False, "last_success": None,
                         "last_error": None, "updated_at": None})
    monkeypatch.setattr(config, "DATA_ROOT", str(tmp_path / "data"))
    # fetch 一律打桩,测试不触网
    monkeypatch.setattr(leagues_updater.build, "fetch_all_if_stale",
                        lambda: {"errors": []})
    monkeypatch.setattr(leagues_updater.build, "build_elo_pool",
                        lambda: ({"A": 1600.0, "B": 1500.0}, []))
    monkeypatch.setattr(leagues_updater.build, "rebuild_dc", lambda code: {})
    monkeypatch.setattr(leagues_updater.build, "compute_standings",
                        lambda results, season: [{"team": "A", "pts": 3}])
    monkeypatch.setattr(leagues_updater.build, "load_results", lambda code: [])
    monkeypatch.setattr("leagues.season_sim.simulate_season", lambda code: None)
    monkeypatch.setattr(leagues_updater.build, "rebuild_ucl",
                        lambda elo=None, ucl_odds=None: {"code": "UCL", "season": "2627", "matches": []})
    monkeypatch.setattr("leagues.fetch_odds_ucl.fetch_ucl_odds", lambda: {})
    monkeypatch.setattr("leagues.train_xgb.build_upcoming_lookup", lambda elo=None: {})
    return tmp_path


def _stub_rebuild(monkeypatch, fail_codes=()):
    def fake_rebuild(code, elo=None, xgb=None):
        if code in fail_codes:
            raise RuntimeError(f"{code} boom")
        return {"code": code, "season": "2627", "matches": []}
    monkeypatch.setattr(leagues_updater.build, "rebuild", fake_rebuild)


def test_update_once_populates_all_leagues(monkeypatch):
    _stub_rebuild(monkeypatch)
    leagues_updater.update_once()
    assert leagues_cache.get_codes() == ["D1", "E0", "F1", "I1", "SP1", "UCL"]
    assert leagues_cache.get_league("E0")["standings"] == [{"team": "A", "pts": 3}]
    assert leagues_cache.get_status()["last_error"] is None
    assert leagues_cache.get_status()["running"] is False


def test_update_once_isolates_single_league_failure(monkeypatch):
    _stub_rebuild(monkeypatch, fail_codes={"D1"})
    leagues_updater.update_once()                   # 不抛
    assert "D1" not in leagues_cache.get_codes()
    assert len(leagues_cache.get_codes()) == 5      # 4 联赛 + UCL(D1 失败被隔离)
    assert "D1" in leagues_cache.get_status()["last_error"]


def test_update_once_elo_failure_keeps_old_snapshot(monkeypatch):
    _stub_rebuild(monkeypatch)
    leagues_updater.update_once()
    before = leagues_cache.get_league("E0")
    monkeypatch.setattr(leagues_updater.build, "build_elo_pool",
                        lambda: (_ for _ in ()).throw(RuntimeError("elo boom")))
    leagues_updater.update_once()                   # 不抛
    assert leagues_cache.get_league("E0") == before  # 旧快照保留(容错铁律)
    assert "elo boom" in leagues_cache.get_status()["last_error"]


def test_trigger_and_stop_events():
    leagues_updater.trigger_now()                   # 仅置位,不阻塞
    leagues_updater.stop()
    assert leagues_updater._stop.is_set()
    leagues_updater._stop.clear()  # 还原,避免影响其他测试
