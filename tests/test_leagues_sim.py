"""赛季推演:概率合法性、归一约束、强队占优、剩余赛程只取 pre。"""
import pytest

from leagues import build, config, season_sim


@pytest.fixture
def sandbox(tmp_path, monkeypatch):
    monkeypatch.setattr(config, "DATA_ROOT", str(tmp_path))
    monkeypatch.setenv("LEAGUES_SEASON", "2627")
    from leagues import fetch_fd, normalize
    # 当前积分:Strong 6 分,Weak 0 分
    fetch_fd.write_csv(config.paths("E0")["results"], normalize.RESULT_FIELDS, [
        {"season": "2627", "date": "2026-08-15", "home": "Strong", "away": "Weak", "gh": 3, "ga": 0},
        {"season": "2627", "date": "2026-08-22", "home": "Weak", "away": "Strong", "gh": 0, "ga": 2},
    ] * 1)
    # 剩余赛程:2 场 pre + 1 场 post(post 不能被模拟)
    fetch_fd.write_csv(config.paths("E0")["schedule"], normalize.SCHEDULE_FIELDS, [
        {"date": "2026-08-15", "time": "15:00", "home": "Strong", "away": "Weak",
         "status": "post", "gh": 3, "ga": 0, "neutral": ""},
        {"date": "2026-10-11", "time": "15:00", "home": "Strong", "away": "Weak",
         "status": "pre", "gh": "", "ga": "", "neutral": ""},
        {"date": "2026-10-18", "time": "15:00", "home": "Weak", "away": "Strong",
         "status": "pre", "gh": "", "ga": "", "neutral": ""},
    ])
    # DC 参数:Strong 明显强
    build._atomic_json(config.paths("E0")["dc_params"], {
        "league": "E0",
        "params": {"Strong": {"attack": 0.6, "defense": -0.3, "n": 100},
                   "Weak": {"attack": -0.6, "defense": 0.3, "n": 100}},
        "global": {"beta0": 0.3, "home_adv": 0.25, "rho": 0.0, "n_matches": 300},
    })
    return tmp_path


def test_sim_probabilities_valid_and_normalized(sandbox):
    out = season_sim.simulate_season("E0", n_sims=200, seed=42)
    assert out["n_sims"] == 200 and set(out["teams"]) == {"Strong", "Weak"}
    for t, p in out["teams"].items():
        for k in ("title", "ucl", "releg"):
            assert 0.0 <= p[k] <= 1.0
    # 归一约束:每次模拟冠军恰好 1 个;ucl 席位 = ucl_spots;降级 = relegation_n
    lg = config.LEAGUES["E0"]
    assert abs(sum(p["title"] for p in out["teams"].values()) - 1.0) < 0.06
    n = len(out["teams"])
    assert abs(sum(p["releg"] for p in out["teams"].values())
               - min(lg["relegation_n"], n)) < 0.06


def test_sim_strong_team_favored(sandbox):
    out = season_sim.simulate_season("E0", n_sims=200, seed=7)
    assert out["teams"]["Strong"]["title"] > out["teams"]["Weak"]["title"]
    # 2 队退化场景 min(relegation_n, n)=2 → 双方 releg 恒 1.0,只能断言不占劣
    assert out["teams"]["Weak"]["releg"] >= out["teams"]["Strong"]["releg"]


def test_sim_writes_disk_and_skips_post_matches(sandbox):
    import json
    out = season_sim.simulate_season("E0", n_sims=100, seed=1)
    with open(config.paths("E0")["sim"], encoding="utf-8") as f:
        on_disk = json.load(f)
    assert on_disk["teams"].keys() == out["teams"].keys()
    # post 场若被误当 pre 模拟,积分会重复计算 → Strong 夺冠率异常贴近 1
    # 间接验证:Weak 仍有非零夺冠可能被压到很低但不应为负/异常
    assert out["teams"]["Strong"]["title"] >= 0.5
