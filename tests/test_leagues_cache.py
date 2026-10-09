# tests/test_leagues_cache.py
"""联赛独立缓存:global 声明正确性(cache.py:64 bug 不复刻)、原子持久化。"""
import json
import os

import pytest

from webapp import leagues_cache


@pytest.fixture(autouse=True)
def tmp_cache(tmp_path, monkeypatch):
    monkeypatch.setattr(leagues_cache, "CACHE_FILE", str(tmp_path / "leagues_cache.json"))
    # 每个测试重置模块状态
    monkeypatch.setattr(leagues_cache, "_payload", {})
    monkeypatch.setattr(leagues_cache, "_status",
                        {"running": False, "last_success": None,
                         "last_error": None, "updated_at": None})
    return tmp_path


def test_set_get_roundtrip():
    assert leagues_cache.get_league("E0") is None
    leagues_cache.set_league("E0", predictions={"matches": [1, 2]}, standings=[{"team": "A"}])
    entry = leagues_cache.get_league("E0")
    assert entry["predictions"]["matches"] == [1, 2] and entry["standings"] == [{"team": "A"}]
    leagues_cache.set_league("E0", sim={"teams": {}})          # 局部更新不清其他字段
    entry = leagues_cache.get_league("E0")
    assert entry["predictions"]["matches"] == [1, 2] and entry["sim"] == {"teams": {}}
    assert leagues_cache.get_codes() == ["E0"]


def test_save_then_load_reads_back():
    """钉死 global 声明:load() 里对 _payload/_status 的赋值必须对模块可见。"""
    leagues_cache.set_league("SP1", predictions={"matches": ["x"]})
    leagues_cache.set_status(running=False, last_success="ok")
    leagues_cache.save()
    # 模拟进程重启:清掉内存再 load
    leagues_cache._payload = {}
    leagues_cache._status = {"running": None}
    assert leagues_cache.load() is True
    assert leagues_cache.get_league("SP1")["predictions"]["matches"] == ["x"]
    assert leagues_cache.get_status()["last_success"] == "ok"


def test_load_missing_file_returns_false():
    assert leagues_cache.load() is False


def test_load_corrupt_file_returns_false():
    with open(leagues_cache.CACHE_FILE, "w") as f:
        f.write("{not json")
    assert leagues_cache.load() is False


def test_save_is_atomic():
    leagues_cache.set_league("E0", predictions={"matches": []})
    leagues_cache.save()
    assert os.path.exists(leagues_cache.CACHE_FILE)
    assert not os.path.exists(leagues_cache.CACHE_FILE + ".tmp")
    with open(leagues_cache.CACHE_FILE, encoding="utf-8") as f:
        json.load(f)                                   # 合法 JSON
