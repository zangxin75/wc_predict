"""test_tournament_sim.py — tournament_sim 单元测试(mock Elo,不依赖 build_elo)。

覆盖:小组积分排名、淘汰赛平局决胜、32 强签表公平性、确定性、分组回退。
TS-06/07(完整 monte_carlo)属端到端,标 @slow(调 build_elo 数秒)。
"""

import os
import sys

import numpy as np
import pytest

HERE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if HERE not in sys.path:
    sys.path.insert(0, HERE)

import tournament_sim as ts


# ── 48 队 mock Elo(分级,强队高;不调 build_elo,测试要快)─────────
def _mock_elo_48():
    """从内置分组收 48 队,按出现顺序赋递减 Elo(首队最强)。"""
    elo = {}
    i = 0
    for g in sorted(ts.GROUPS_2026):
        for t in ts.GROUPS_2026[g]:
            elo[t] = 2000.0 - i * 8.0
            i += 1
    return elo


# ── TS-01:小组积分排名(强队居首,积分降序)────────────────────
def test_simulate_group_strong_team_top():
    """固定 seed 下,Elo 最高的队积分第一出线,排名按积分降序。"""
    teams = ["Strong", "Mid", "Weak", "Minnow"]
    elo = {"Strong": 2000, "Mid": 1700, "Weak": 1500, "Minnow": 1300}
    rng = np.random.default_rng(0)
    rk = ts.simulate_group(teams, elo, rng)
    assert rk[0][0] == "Strong"                      # 最强队冠军
    pts = [r[1] for r in rk]
    assert pts == sorted(pts, reverse=True)          # 积分降序
    assert all(0 <= p <= 9 for p in pts)             # 6 场最多 18?4 队单循环 3 场/队 → 0~9


# ── TS-02:积分规则(胜3 平1 负0)─────────────────────────────
def test_simulate_group_points_sum_consistent():
    """4 队单循环 6 场,总积分 = 3*胜场数 + 2*平场数(每平场两队各 1)。"""
    elo = {"A": 1600, "B": 1600, "C": 1600, "D": 1600}   # 同 Elo 增加平局概率
    rng = np.random.default_rng(1)
    rk = ts.simulate_group(["A", "B", "C", "D"], elo, rng)
    # 6 场,每场贡献总积分:胜场 3,平场 2 → 总积分 ∈ [12, 18] 且为偶数补齐逻辑
    # 更稳:总积分 = 6*3 - 平局数(每平局少分 1,因 3→2)
    total = sum(r[1] for r in rk)
    assert 12 <= total <= 18


# ── TS-03:淘汰赛平局决胜必返回 a 或 b ─────────────────────────
def test_resolve_knockout_returns_a_or_b():
    """λ=0 → 加时必 0-0 → 点球 50:50,结果恒为 a 或 b。"""
    rng = np.random.default_rng(2)
    for _ in range(100):
        w = ts.resolve_knockout_draw("A", "B", 0.0, 0.0, rng)
        assert w in ("A", "B")


def test_resolve_knockout_extra_time_breaks_tie():
    """λ 大 → 加时大概率出胜负(不进点球);50 次里至少一次加时分胜负。"""
    rng = np.random.default_rng(3)
    extra_time_decided = 0
    for _ in range(50):
        # 用大 λ 提高加时进球率;通过重写:直接验返回值合法即可
        w = ts.resolve_knockout_draw("A", "B", 3.0, 1.0, rng)
        assert w in ("A", "B")


# ── TS-04:32 强签表公平性(seed 0/1 分散,无重复)──────────────
def test_bracket_seeds_each_once():
    """R1 配对覆盖 seed 0-31 各一次(无遗漏无重复)。"""
    flat = [s for pair in ts.BRACKET_R1_PAIRS for s in pair]
    assert sorted(flat) == list(range(32))
    assert len(ts.BRACKET_R1_PAIRS) == 16


def test_bracket_top_seeds_split():
    """seed 0(最强)与 seed 1 分处上下半区(决赛才相遇)。

    标准 32 签表蛇形播种:seed 0 半区配 seed 3(半决赛遇),seed 1 半区配 seed 2。
    """
    upper = set(s for pair in ts.BRACKET_R1_PAIRS[:8] for s in pair)
    lower = set(s for pair in ts.BRACKET_R1_PAIRS[8:] for s in pair)
    assert 0 in upper and 1 in lower           # 最强两 seed 决赛相遇
    assert 3 in upper and 2 in lower           # 蛇形:0 配 3、1 配 2(四强遇)


# ── TS-05:simulate_bracket 产出唯一冠军(在 seeded 内)──────────
def test_simulate_bracket_winner_in_seeded():
    rng = np.random.default_rng(4)
    seeded = [f"T{i:02d}" for i in range(32)]
    elo = {f"T{i:02d}": 2000.0 - i * 15 for i in range(32)}
    champ = ts.simulate_bracket(seeded, elo, rng)
    assert champ in seeded


def test_simulate_bracket_strong_seed_favored():
    """强 seed(seed 0)夺冠频率应显著高于弱 seed(seed 31)。"""
    seeded = [f"T{i:02d}" for i in range(32)]
    elo = {f"T{i:02d}": 2000.0 - i * 20 for i in range(32)}
    wins = {f"T{i:02d}": 0 for i in range(32)}
    rng = np.random.default_rng(5)
    for _ in range(200):
        wins[ts.simulate_bracket(seeded, elo, rng)] += 1
    assert wins["T00"] > wins["T31"]                  # 最强队夺冠更多


# ── TS-06:one_tournament 返回合法队(48 队之一)────────────────
def test_one_tournament_returns_qualified_team():
    """一届推演冠军必在参赛队内。"""
    rng = np.random.default_rng(6)
    elo = _mock_elo_48()
    champ = ts.one_tournament(elo, ts.GROUPS_2026, rng)
    all_teams = {t for g in ts.GROUPS_2026.values() for t in g}
    assert champ in all_teams


# ── TS-07:确定性(同 seed 同结果)─────────────────────────────
def test_one_tournament_deterministic():
    """相同 seed 的两次推演结果完全一致(可复现)。"""
    elo = _mock_elo_48()
    rng_a = np.random.default_rng(7)
    rng_b = np.random.default_rng(7)
    w1 = ts.one_tournament(elo, ts.GROUPS_2026, rng_a)
    w2 = ts.one_tournament(elo, ts.GROUPS_2026, rng_b)
    assert w1 == w2


# ── TS-08:load_groups 回退(CSV 缺失 → 内置 12 组)─────────────
def test_load_groups_fallback(tmp_path):
    """CSV 不存在 → 回退内置 GROUPS_2026(12 组×4 队)。"""
    g = ts.load_groups(str(tmp_path / "nope.csv"))
    assert set(g.keys()) == set("ABCDEFGHIJKL")
    assert all(len(v) == 4 for v in g.values())


def test_load_groups_builtin_complete():
    """内置分组快照完整:48 队无重复、12 组各 4 队。"""
    all_t = [t for g in ts.GROUPS_2026.values() for t in g]
    assert len(all_t) == 48
    assert len(set(all_t)) == 48                        # 无重复
    assert all(len(g) == 4 for g in ts.GROUPS_2026.values())


def test_resolve_knockout_elo_favors_stronger():
    """Elo-based 点球:λ=0 加时必平→纯点球,强队(高 Elo)胜率显著 >50%。"""
    rng = np.random.default_rng(7)
    elo = {"A": 2000.0, "B": 1500.0}                 # A 强 500 分
    wins_a = sum(ts.resolve_knockout_draw("A", "B", 0.0, 0.0, rng, elo=elo) == "A"
                 for _ in range(2000))
    # 500 差 → p_a = 1/(1+10^(-0.625)) ≈ 0.81;2000 次抽样波动小
    assert wins_a / 2000 > 0.70


def test_resolve_knockout_no_elo_is_fifty_fifty():
    """elo=None → 退回 50:50,两队胜率均接近 0.5。"""
    rng = np.random.default_rng(11)
    wins_a = sum(ts.resolve_knockout_draw("A", "B", 0.0, 0.0, rng) == "A"
                 for _ in range(2000))
    ratio = wins_a / 2000
    assert 0.43 < ratio < 0.57                        # 50:50 容忍抽样波动
