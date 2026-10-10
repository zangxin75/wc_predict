# tests/test_leagues_backtest.py
"""回测:walk-forward 严格性 + 指标合法性。标记 slow(默认跳过)。"""
import pytest

from leagues import backtest, build, config


@pytest.fixture
def sandbox(tmp_path, monkeypatch):
    monkeypatch.setattr(config, "DATA_ROOT", str(tmp_path))
    from leagues import fetch_fd, normalize
    # 两个赛季,每队每季互踢,强弱分明
    rows = []
    for s, y in (("2425", 2024), ("2526", 2025)):
        for rnd in range(10):
            rows.append({"season": s, "date": f"{y}-09-{rnd + 1:02d}",
                         "home": "Strong", "away": "Weak", "gh": 2, "ga": 0,
                         "b365h": 1.5, "b365d": 4.0, "b365a": 6.0})
            rows.append({"season": s, "date": f"{y + 1}-03-{rnd + 1:02d}",
                         "home": "Weak", "away": "Strong", "gh": 1, "ga": 2,
                         "b365h": 5.0, "b365d": 3.8, "b365a": 1.6})
    fetch_fd.write_csv(config.paths("E0")["results"], normalize.RESULT_FIELDS, rows)
    return tmp_path


@pytest.mark.slow
def test_backtest_metrics_valid_and_walk_forward(sandbox, monkeypatch):
    calls = []

    real_fit = backtest.dixon_coles.fit_league

    def spy_fit(matches, cutoff=None, **kw):
        calls.append(cutoff)
        params, glob = real_fit(matches, cutoff=cutoff, **kw)
        # 未来数据零容忍:拟合实际只消费 cutoff 前的 2425 季(10 轮 × 2 场)
        assert glob["n_matches"] == 20
        return params, glob

    monkeypatch.setattr(backtest.dixon_coles, "fit_league", spy_fit)
    out = backtest.backtest_league("E0", ["2526"])
    r = out["2526"]
    assert r["n"] == 20
    assert 0.0 <= r["acc_model"] <= 1.0 and 0.0 <= r["acc_b365"] <= 1.0
    assert r["logloss_model"] > 0 and r["logloss_b365"] > 0
    assert calls and all(c == "2025-08-01" for c in calls)   # 赛季开赛日 cutoff
    # 强弱分明的合成数据上,模型与 B365 都应有不错准确率
    assert r["acc_model"] >= 0.8 and r["acc_b365"] >= 0.8
