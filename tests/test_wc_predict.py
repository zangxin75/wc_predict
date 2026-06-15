"""wc_predict 单元测试:predict_from_elo 的数学正确性 + predict_match 重构不破坏行为。"""

import pytest

import wc_predict


def test_predict_from_elo_three_outcomes_sum_to_one():
    """三态概率归一化后应和为 1。"""
    hw, draw, aw, _, _ = wc_predict.predict_from_elo(1900, 1700, home_adv_elo=0)
    assert abs(hw + draw + aw - 1.0) < 1e-9


def test_predict_from_elo_symmetric_at_equal_elo():
    """同 Elo、中立场:主客胜率应近似相等(对称性)。"""
    hw, draw, aw, _, _ = wc_predict.predict_from_elo(1700, 1700, home_adv_elo=0)
    assert abs(hw - aw) < 1e-9


def test_predict_from_elo_stronger_team_higher_win_prob():
    """Elo 更高的队胜率应更高(中立场)。"""
    hw, _, aw, _, _ = wc_predict.predict_from_elo(2000, 1600, home_adv_elo=0)
    assert hw > aw


def test_predict_from_elo_home_advantage_boosts_home():
    """同等 Elo 下,有主场加成时主队胜率应高于无主场。"""
    hw_no, _, _, _, _ = wc_predict.predict_from_elo(1700, 1700, home_adv_elo=0)
    hw_yes, _, _, _, _ = wc_predict.predict_from_elo(1700, 1700, home_adv_elo=100)
    assert hw_yes > hw_no


def test_predict_from_elo_uses_default_home_adv_when_none():
    """home_adv_elo=None 时用 Config.HOME_ADV_ELO(默认0)。"""
    hw_none, draw_none, aw_none, _, _ = wc_predict.predict_from_elo(1800, 1700)
    hw_zero, draw_zero, aw_zero, _, _ = wc_predict.predict_from_elo(1800, 1700, home_adv_elo=0)
    # 默认 HOME_ADV_ELO=0,两者应一致
    assert abs(hw_none - hw_zero) < 1e-12


def test_predict_match_consistent_with_predict_from_elo():
    """predict_match 查字典后应与直接传同 Elo 的 predict_from_elo 结果一致。"""
    team = "巴西"
    elo = wc_predict.ELO_RATINGS[team]
    hw_m, draw_m, aw_m, _, _ = wc_predict.predict_match(team, "巴西", home_adv_elo=0, verbose=False)
    hw_f, draw_f, aw_f, _, _ = wc_predict.predict_from_elo(elo, elo, home_adv_elo=0)
    assert abs(hw_m - hw_f) < 1e-12
    assert abs(draw_m - draw_f) < 1e-12


def test_predict_match_raises_on_unknown_team():
    """未知队名应抛 KeyError(而非静默用默认 Elo)。"""
    with pytest.raises(KeyError):
        wc_predict.predict_match("不存在的队伍", "巴西", verbose=False)


# ═══ learning 点 2:predict_from_elo_hedged(RD 不确定性 hedging)═══

def _shrinkage(elo_h, elo_a, rd_h, rd_a):
    """hedged 相对原泊松的 L1 收缩量 = Σ|hedged_i − orig_i|(越大=收缩越多)。"""
    ph, pd, pa, _, _ = wc_predict.predict_from_elo(elo_h, elo_a, 0)
    hh, hd, ha, _, _ = wc_predict.predict_from_elo_hedged(elo_h, rd_h, elo_a, rd_a, 0)
    return abs(hh - ph) + abs(hd - pd) + abs(ha - pa)


# ── HEDGE-01:hedged 三态和严格归一 ─────────────────────────
def test_hedged_sums_to_one():
    """无论 RD 多少,收缩后三态概率和必须为 1(归一化)。"""
    hh, hd, ha, _, _ = wc_predict.predict_from_elo_hedged(2000, 80, 1800, 80, 0)
    assert abs(hh + hd + ha - 1.0) < 1e-9


# ── HEDGE-02:高可信(RD小)收缩量 < 低可信(RD大)收缩量(单调)──
def test_hedged_monotonic_in_rd():
    """w 必须是 RD 的单调递增函数:高 RD → 偏离原概率更多(严格小于)。"""
    shrink_hi = _shrinkage(2000, 1800, 40, 40)     # 两队高可信
    shrink_lo = _shrinkage(2000, 1800, 220, 220)   # 两队低可信
    assert shrink_hi < shrink_lo


# ── HEDGE-03:低可信场最大概率被压低(确实收缩了)──────────────
def test_hedged_reduces_peak():
    """两队 RD 都大 → 最大概率被向均匀拉低(严格 < 原 max)。"""
    ph, pd, pa, _, _ = wc_predict.predict_from_elo(2000, 1800, 0)
    hh, hd, ha, _, _ = wc_predict.predict_from_elo_hedged(2000, 220, 1800, 220, 0)
    assert max(hh, hd, ha) < max(ph, pd, pa)


# ── HEDGE-04:hedged 概率落在 [0,1] ─────────────────────────
def test_hedged_in_range():
    """各 RD 下 hedged 概率都合法(∈[0,1])。"""
    for rd in (40, 100, 160, 220):
        hh, hd, ha, _, _ = wc_predict.predict_from_elo_hedged(2000, rd, 1800, rd, 0)
        assert 0.0 <= hh <= 1.0 and 0.0 <= hd <= 1.0 and 0.0 <= ha <= 1.0
