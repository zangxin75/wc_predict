# tests/test_leagues_elo.py
"""统一 Elo 池:零和性、升班马非零和、主场修正、净胜球乘数、cutoff 严格性。"""
from leagues import elo


def _m(date, home, away, gh, ga, comp="E0", neutral=False):
    return {"date": date, "home": home, "away": away, "gh": gh, "ga": ga,
            "comp": comp, "neutral": neutral}


def test_zero_sum_when_both_established_equal_k():
    rh, ra = elo.update_elo(1600, 1600, 2, 1, "E0", False, 100, 100)
    assert abs((rh + ra) - 3200) < 1e-9          # K 相同 → 零和
    assert rh > 1600 > ra


def test_promoted_team_non_zero_sum():
    """新队(games<30)K×1.5,老队 K 不变 → 总和不守恒(刻意设计,加速收敛)。"""
    rh, ra = elo.update_elo(1500, 1700, 1, 0, "E0", False, 0, 100)
    gained = rh - 1500
    lost = 1700 - ra
    assert gained > lost                          # 爆冷 + 加速 K → 得大于失


def test_home_adv_affects_expectation_only():
    """主场赢球加的分数应少于中立场同比分(期望分更高 → 超额更少)。"""
    rh_home, _ = elo.update_elo(1600, 1600, 1, 0, "E0", False, 100, 100)
    rh_neut, _ = elo.update_elo(1600, 1600, 1, 0, "E0", True, 100, 100)
    assert rh_neut > rh_home > 1600


def test_ucl_k_higher_than_league():
    d_ucl = elo.update_elo(1600, 1600, 1, 0, "UCL", False, 100, 100)[0] - 1600
    d_lg = elo.update_elo(1600, 1600, 1, 0, "E0", False, 100, 100)[0] - 1600
    assert d_ucl > d_lg


def test_goal_diff_mult_ladder():
    assert elo.goal_diff_mult(0) == 1.0 and elo.goal_diff_mult(1) == 1.0
    assert elo.goal_diff_mult(2) == 1.5
    assert elo.goal_diff_mult(3) == 1.75
    assert elo.goal_diff_mult(4) == (11 + 4) / 8.0
    assert elo.goal_diff_mult(7) == (11 + 7) / 8.0


def test_build_pool_cutoff_strict_and_pre_match_history():
    matches = [
        _m("2026-08-15", "A", "B", 1, 0),
        _m("2026-08-22", "B", "A", 0, 2),
        _m("2026-08-29", "A", "C", 3, 1),
    ]
    ratings, history = elo.build_pool(matches, cutoff="2026-08-22")
    # cutoff 严格 <:只剩第 1 场
    assert len(history) == 1 and "C" not in ratings
    # history 存赛前 Elo:第 1 场双方都是初始分
    assert history[0]["elo_h"] == elo.INITIAL_ELO and history[0]["elo_a"] == elo.INITIAL_ELO
    # 无 cutoff:3 场全算,A 赢 2 场应最高
    ratings, history = elo.build_pool(matches)
    assert ratings["A"] > ratings["B"] and ratings["A"] > ratings["C"]
    # 第 3 场的赛前 Elo 已反映前 2 场结果
    assert history[2]["elo_h"] != elo.INITIAL_ELO


def test_build_pool_sorts_by_date():
    m1 = [_m("2026-08-22", "B", "A", 0, 2), _m("2026-08-15", "A", "B", 1, 0)]
    m2 = list(reversed(m1))
    assert elo.build_pool(m1)[0] == elo.build_pool(m2)[0]
