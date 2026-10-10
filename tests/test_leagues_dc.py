# tests/test_leagues_dc.py
"""Dixon-Coles MLE:合成数据参数恢复、小样本回退、UCL 借用制、λ 裁剪。"""
import random

from leagues import dixon_coles as dc


def _synth_matches(rng, teams, atk, dfn, n_rounds=30, start_year=2023):
    """用已知真值参数生成泊松比分(β0=log(1.35), home_adv=0.25)。"""
    import math
    matches = []
    for r in range(n_rounds):
        for i, h in enumerate(teams):
            for j, a in enumerate(teams):
                if i <= j:
                    continue
                lh = math.exp(math.log(1.35) + 0.25 + atk[h] - dfn[a])
                la = math.exp(math.log(1.35) + atk[a] - dfn[h])
                gh = _pois(rng, lh)
                ga = _pois(rng, la)
                month = (r % 12) + 1
                matches.append({"date": f"{start_year}-{month:02d}-15",
                                "home": h, "away": a, "gh": gh, "ga": ga})
    return matches


def _pois(rng, lam):
    """Knuth 泊松采样(测试用,避免依赖 numpy 随机)。"""
    import math
    L, k, p = math.exp(-lam), 0, 1.0
    while p > L:
        k += 1
        p *= rng.random()
    return k - 1


def test_fit_league_recovers_attack_ordering():
    rng = random.Random(42)
    teams = ["Strong", "MidA", "MidB", "Weak"]
    atk = {"Strong": 0.5, "MidA": 0.1, "MidB": -0.1, "Weak": -0.5}
    dfn = {"Strong": -0.3, "MidA": 0.0, "MidB": 0.0, "Weak": 0.3}  # defense 越高越漏
    matches = _synth_matches(rng, teams, atk, dfn)
    params, glob = dc.fit_league(matches)
    assert params["Strong"]["attack"] > params["Weak"]["attack"]
    assert params["Strong"]["defense"] < params["Weak"]["defense"]
    assert glob["home_adv"] > 0 and glob["n_matches"] == len(matches)
    # 攻防各自零均值化(可识别性)
    s_atk = sum(p["attack"] for p in params.values())
    assert abs(s_atk) < 1e-6


def test_fit_league_small_sample_falls_back_to_neutral():
    rng = random.Random(7)
    # 25 轮:A/B 各踢 25 场(≥ min_matches),正常拟合
    matches = _synth_matches(rng, ["A", "B"], {"A": 0.3, "B": -0.3},
                             {"A": 0.0, "B": 0.0}, n_rounds=25)
    # 加一支只踢了 2 场的新队
    matches += [{"date": "2023-02-15", "home": "NewBoys", "away": "A", "gh": 0, "ga": 4},
                {"date": "2023-02-16", "home": "B", "away": "NewBoys", "gh": 3, "ga": 0}]
    params, _ = dc.fit_league(matches, min_matches=20)
    assert params["NewBoys"]["attack"] == 0.0 and params["NewBoys"]["defense"] == 0.0
    assert params["NewBoys"]["n"] == 2
    assert params["A"]["attack"] != 0.0            # 老队正常拟合


def test_fit_league_empty_matches():
    params, glob = dc.fit_league([])
    assert params == {} and glob["n_matches"] == 0


def test_fit_ucl_global_borrows_and_flags_outsiders():
    """N-2:非五大联赛队(如 Ajax)→ 中性参数 + low_confidence=True。"""
    league_params = {
        "Real Madrid": {"attack": 0.4, "defense": -0.2, "n": 100},
        "Man City": {"attack": 0.5, "defense": -0.3, "n": 100},
    }
    ucl_matches = [
        {"date": "2025-09-16", "home": "Real Madrid", "away": "Ajax", "gh": 3, "ga": 1, "neutral": False},
        {"date": "2025-09-17", "home": "Man City", "away": "Real Madrid", "gh": 2, "ga": 2, "neutral": False},
        {"date": "2025-10-01", "home": "Ajax", "away": "Man City", "gh": 0, "ga": 4, "neutral": False},
        {"date": "2026-05-30", "home": "Real Madrid", "away": "Man City", "gh": 1, "ga": 0, "neutral": True},
    ]
    glob, borrow = dc.fit_ucl_global(ucl_matches, league_params)
    assert borrow["Real Madrid"]["attack"] == 0.4 and borrow["Real Madrid"]["low_confidence"] is False
    assert borrow["Ajax"]["attack"] == 0.0 and borrow["Ajax"]["low_confidence"] is True
    assert set(glob) >= {"beta0", "home_adv", "rho", "n_matches"}
    assert glob["n_matches"] == 4 and glob["rho"] == 0.0
    # 借用参数可直接进 expected_goals 出 λ
    lh, la = dc.expected_goals(borrow, glob, "Real Madrid", "Ajax")
    assert lh > la


def test_expected_goals_clip_and_unknown_team():
    params = {"X": {"attack": 10.0, "defense": -10.0, "n": 50}}
    glob = {"beta0": 0.3, "home_adv": 0.25, "rho": 0.0}
    lh, la = dc.expected_goals(params, glob, "X", "Ghost")
    assert lh == 5.0                                # 上限裁剪
    params2 = {"X": {"attack": -10.0, "defense": 10.0, "n": 50}}
    lh2, la2 = dc.expected_goals(params2, glob, "Ghost", "X")
    assert la2 == 0.1                               # 下限裁剪
    # 中立场:home_adv 不计(用中等强度参数,避免 λ 触底裁剪后失去区分度)
    params3 = {"X": {"attack": 0.5, "defense": 0.0, "n": 50}}
    lh3, _ = dc.expected_goals(params3, glob, "X", "Ghost", neutral=True)
    lh4, _ = dc.expected_goals(params3, glob, "X", "Ghost", neutral=False)
    assert lh4 > lh3
