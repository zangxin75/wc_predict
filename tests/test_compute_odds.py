"""compute_odds 单元测试:去vig 数学正确性 + 装载查表。

devig_power 是 learning 贡献点 1(用户实现 Power method)。
需求表 ODS-02/03(FLB 纠正)在 placeholder(basic normalization)下为红,
用户实现 Power method 的 z 求解后转绿 —— 这是"完成标准"。

需求表(devig_power):
  编号 | 场景        | 输入(oh,od,oa) | 预期                  | 备注
  ------|------------|----------------|-----------------------|-------------------
  ODS-01| 归一化      | (1.5,3.8,6.0)  | 三态和=1(误差<1e-9)    | 边界
  ODS-02| FLB纠正-热门| (1.5,3.8,6.0)  | p_h > basic_norm_p_h  | 热门被低估→上调
  ODS-03| FLB纠正-冷门| (1.5,3.8,6.0)  | p_a < basic_norm_p_a  | 冷门被高估→下调
  ODS-04| 极端赔率    | (1.05,9.0,21.0)| 和=1,均∈(0,1)         | 大冷门不崩
  ODS-05| 已公平赔率  | (3.0,3.0,3.0)  | 各≈1/3                | 无vig时退化
"""

import pytest

import compute_odds as co


def _basic_norm(oh, od, oa):
    """basic normalization(z=1):直接除以隐含概率和。作 FLB 对比基准。"""
    ih, idd, ia = 1.0 / oh, 1.0 / od, 1.0 / oa
    s = ih + idd + ia
    return ih / s, idd / s, ia / s


# ── ODS-01:三态归一(placeholder 即过)─────────────────────
def test_devig_sums_to_one():
    ph, pd, pa = co.devig_power(1.5, 3.8, 6.0)
    assert abs(ph + pd + pa - 1.0) < 1e-9


# ── ODS-02:FLB 纠正——热门概率上调(★ learning 点,placeholder 红)──
def test_devig_corrects_favorite_longshot_bias_home():
    """热门(低赔率→高隐含概率)去vig后概率应 > basic normalization。
    博彩市场系统性低估热门(赔率压得不够低),power method 的指数>1 把热门概率往上调。"""
    ph, _pd, _pa = co.devig_power(1.5, 3.8, 6.0)
    bh, _bd, _ba = _basic_norm(1.5, 3.8, 6.0)
    assert ph > bh


# ── ODS-03:FLB 纠正——冷门概率下调(★ learning 点,placeholder 红)──
def test_devig_corrects_favorite_longshot_bias_away():
    """冷门(高赔率→低隐含概率)去vig后概率应 < basic normalization。
    市场系统性高估冷门(赔率抽水更多),power method 把冷门概率往下调。"""
    _ph, _pd, pa = co.devig_power(1.5, 3.8, 6.0)
    _bh, _bd, ba = _basic_norm(1.5, 3.8, 6.0)
    assert pa < ba


# ── ODS-04:极端赔率不崩 ──────────────────────────────────
def test_devig_extreme_odds_valid():
    ph, pd, pa = co.devig_power(1.05, 9.0, 21.0)
    assert abs(ph + pd + pa - 1.0) < 1e-9
    assert all(0.0 < p < 1.0 for p in (ph, pd, pa))


# ── ODS-05:无 vig 时退化到均匀 ────────────────────────────
def test_devig_fair_odds_near_uniform():
    ph, pd, pa = co.devig_power(3.0, 3.0, 3.0)
    assert abs(ph - 1.0 / 3) < 1e-6
    assert abs(pd - 1.0 / 3) < 1e-6
    assert abs(pa - 1.0 / 3) < 1e-6


# ── 装载 / 查表 ──────────────────────────────────────────
def test_lookup_returns_none_on_missing():
    assert co.lookup_odds({}, "Brazil", "Germany", year="2014") is None


def test_lookup_handles_reverse_fixture():
    """主客颠倒查表 → 返回颠倒后的赔率(回测对齐容错)。"""
    m = {("2014", "Brazil", "Germany"): (1.5, 4.0, 6.0)}
    assert co.lookup_odds(m, "Germany", "Brazil", year="2014") == (6.0, 4.0, 1.5)


def test_implied_probs_sum_above_one_with_vig():
    ih, idd, ia = co.implied_probs(1.5, 3.8, 6.0)
    assert ih + idd + ia > 1.0   # overround 存在


def test_normalize_team_known_mappings():
    assert co.normalize_team("Korea Republic") == "South Korea"
    assert co.normalize_team("IR Iran") == "Iran"
    assert co.normalize_team("  Brazil ") == "Brazil"   # 合并空白
