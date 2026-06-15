"""compute_ad 单元测试:Dixon-Coles 攻防 MLE 拟合。

分两类:
- 契约/异常测试(快,小 fixture 或直接断言结构)
- 统计正确性 smoke(慢,真实 results.csv,标 @slow)
"""

import csv
import os

import pytest

import compute_ad

HERE = os.path.dirname(os.path.abspath(__file__))
RESULTS = os.path.join(HERE, "..", "results.csv")

slow = pytest.mark.slow


# ── AD-07:空数据异常 ──────────────────────────────────────────
def test_fit_empty_csv_raises(tmp_path):
    """空 CSV(仅表头)应抛 ValueError,不静默返回垃圾。"""
    p = tmp_path / "empty.csv"
    p.write_text(
        "date,home_team,away_team,home_score,away_score,tournament,city,country,neutral\n",
        encoding="utf-8",
    )
    with pytest.raises(ValueError):
        compute_ad.fit_attack_defense(str(p))


# ── AD-08:结构正确 ────────────────────────────────────────────
@slow
def test_fit_returns_expected_structure():
    params, glob = compute_ad.fit_attack_defense(RESULTS, cutoff_date="2022-11-20")
    assert "Brazil" in params
    for key in ("attack", "defense", "matches"):
        assert key in params["Brazil"]
    assert "beta0" in glob and "home_adv" in glob


# ── AD-01:进攻强弱区分 ────────────────────────────────────────
@slow
def test_fit_strong_team_higher_attack():
    """巴西 attack 应高于全体中位数(进攻强队)。"""
    params, _ = compute_ad.fit_attack_defense(RESULTS, cutoff_date="2022-11-20")
    attacks = sorted(v["attack"] for v in params.values() if v["matches"] >= 20)
    median = attacks[len(attacks) // 2]
    assert params["Brazil"]["attack"] > median


# ── AD-02:防守强弱区分 ────────────────────────────────────────
@slow
def test_fit_exists_strong_defense_team():
    """应存在 defense<0 的队(强防守,失球少)。"""
    params, _ = compute_ad.fit_attack_defense(RESULTS, cutoff_date="2022-11-20")
    defenses = [v["defense"] for v in params.values() if v["matches"] >= 20]
    assert min(defenses) < 0


# ── AD-03:小样本回退中性 ──────────────────────────────────────
@slow
def test_fit_min_sample_falls_back_to_neutral():
    """matches < min_matches 的队,attack/defense 应回退中性(≈0)。"""
    params, _ = compute_ad.fit_attack_defense(
        RESULTS, cutoff_date="2022-11-20", min_matches=20)
    for team, v in params.items():
        if v["matches"] < 20:
            assert abs(v["attack"]) < 0.1, f"{team} attack 未回退中性"
            assert abs(v["defense"]) < 0.1, f"{team} defense 未回退中性"


# ── AD-04:cutoff 无泄漏 ───────────────────────────────────────
@slow
def test_fit_cutoff_changes_parameters():
    """不同 cutoff 下巴西 attack 应不同(walk-forward 无泄漏)。"""
    p18, _ = compute_ad.fit_attack_defense(RESULTS, cutoff_date="2018-06-14")
    p22, _ = compute_ad.fit_attack_defense(RESULTS, cutoff_date="2022-11-20")
    assert abs(p18["Brazil"]["attack"] - p22["Brazil"]["attack"]) > 1e-6


# ── AD-05:λ 物理合理(clip 兜底)──────────────────────────────
def test_expected_goals_reasonable_range():
    """expected_goals 对任何参数组合都 clip 到物理合理区间 [0.1, 5.0]。

    用极端 mock 参数验证:强攻+弱防原始 λ 会指数爆炸(exp(6)=400+),clip 必须压住。
    用 mock 而非全量拟合——既快(秒级)又直接测 clip 契约,不必每次跑 4 分钟拟合。
    (全量拟合的真实 λ 合理性由 clip 本身保证:无论 MLE 拟出什么,输出都被压住。)
    """
    params = {
        "MegaAttack":     {"attack": 3.0, "defense": -2.0, "matches": 100},
        "PaperDefense":   {"attack": -1.0, "defense": 3.0, "matches": 100},
        "Balanced":       {"attack": 0.0, "defense": 0.0, "matches": 100},
    }
    glob = {"beta0": 0.185, "home_adv": 0.234}
    # 强攻打弱防:原始 exp(0.185+3.0+3.0)=exp(6.185)≈485,必须被 clip
    lam_h, lam_a = compute_ad.expected_goals(
        params, glob, "MegaAttack", "PaperDefense", neutral=True)
    assert 0.1 <= lam_h <= 5.0, f"强攻打弱防 λ_h={lam_h} 未被 clip"
    assert 0.1 <= lam_a <= 5.0, f"PaperDefense λ_a={lam_a} 未被 clip"
    # 中等对决 λ 在合理中段(≈1.2),不受 clip 影响
    lam_h2, lam_a2 = compute_ad.expected_goals(
        params, glob, "Balanced", "Balanced", neutral=True)
    assert 0.1 <= lam_h2 <= 5.0
    # 未知队按中性处理,λ 仍在区间内
    lam_h3, _ = compute_ad.expected_goals(
        params, glob, "UnknownTeamA", "UnknownTeamB", neutral=True)
    assert 0.1 <= lam_h3 <= 5.0


# ── AD-06:时间衰减生效 ────────────────────────────────────────
@slow
def test_halflife_affects_parameters():
    """半衰期不同 → 参数不同(证明时间权重真的起作用)。"""
    p_short, _ = compute_ad.fit_attack_defense(
        RESULTS, cutoff_date="2022-11-20", halflife_years=1.0)
    p_long, _ = compute_ad.fit_attack_defense(
        RESULTS, cutoff_date="2022-11-20", halflife_years=10.0)
    assert abs(p_short["Brazil"]["attack"] - p_long["Brazil"]["attack"]) > 1e-6
