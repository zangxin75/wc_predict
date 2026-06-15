"""compute_elo 单元测试:聚焦 build_elo 的 cutoff_date 行为(walk-forward 回测的地基)。"""

import csv
import os

import pytest

import compute_elo

HERE = os.path.dirname(os.path.abspath(__file__))
RESULTS = os.path.join(HERE, "..", "results.csv")

# 极小 fixture:4 场跨 2018 世界杯开赛前后的比赛,用来精确验证 cutoff 过滤
SAMPLE_ROWS = [
    ["date", "home_team", "away_team", "home_score", "away_score",
     "tournament", "city", "country", "neutral"],
    ["2017-01-01", "TeamA", "TeamB", 2, 1, "Friendly", "X", "Y", "TRUE"],
    ["2018-06-13", "TeamA", "TeamB", 1, 1, "Friendly", "X", "Y", "TRUE"],
    ["2018-06-15", "TeamA", "TeamB", 3, 0, "FIFA World Cup", "X", "Y", "TRUE"],
    ["2020-01-01", "TeamA", "TeamB", 0, 2, "Friendly", "X", "Y", "TRUE"],
]


@pytest.fixture
def sample_csv(tmp_path):
    p = tmp_path / "sample.csv"
    with open(p, "w", newline="", encoding="utf-8") as f:
        csv.writer(f).writerows(SAMPLE_ROWS)
    return str(p)


def test_build_elo_cutoff_filters_future_matches(sample_csv):
    """cutoff=2018-06-14 应只保留该日期前的 2 场(2017-01-01, 2018-06-13)。"""
    _, n_full, _ = compute_elo.build_elo(sample_csv)
    _, n_cut, _ = compute_elo.build_elo(sample_csv, cutoff_date="2018-06-14")
    assert n_full == 4
    assert n_cut == 2


def test_build_elo_cutoff_none_equals_no_cutoff(sample_csv):
    """cutoff_date=None 必须等价于不传(向后兼容)。"""
    _, n1, _ = compute_elo.build_elo(sample_csv)
    _, n2, _ = compute_elo.build_elo(sample_csv, cutoff_date=None)
    assert n1 == n2 == 4


def test_build_elo_cutoff_changes_ratings(sample_csv):
    """不同 cutoff 下同一队 Elo 应不同(证明 cutoff 真的影响了更新序列)。"""
    elo_full, _, _ = compute_elo.build_elo(sample_csv)
    elo_early, _, _ = compute_elo.build_elo(sample_csv, cutoff_date="2018-06-14")
    # full 多含 2018 世界杯 3-0 大胜(K=50×1.75 高权重)和 2020 输球两场;
    # 净方向取决于具体比分与赛事权重,这里只断言"与 early 不同"。
    assert elo_full["TeamA"] != elo_early["TeamA"]


def test_update_elo_after_match_zero_sum():
    """Elo 更新零和:两队变化量大小相等、符号相反。"""
    h0, a0 = 1600.0, 1500.0
    h, a = compute_elo.update_elo_after_match(h0, a0, 2, 1, "FIFA World Cup", True)
    assert abs((h - h0) + (a - a0)) < 1e-9


@pytest.mark.parametrize("tournament,expected_k_base", [
    ("FIFA World Cup", 50.0),              # 决赛圈最高权重
    ("FIFA World Cup qualification", 40.0),  # 预选赛
    ("Friendly", 20.0),                    # 友谊赛最低
    ("UEFA Nations League", 40.0),         # 洲际正赛
])
def test_k_factor_distinguishes_tournaments(tournament, expected_k_base):
    """同比分下,重要赛事的 Elo 变动幅度应大于友谊赛(K 因子分档生效)。"""
    h_wc, _ = compute_elo.update_elo_after_match(1500, 1500, 1, 0, tournament, True)
    # 友谊赛 K=20,对应赛事应更大 → 同样 1-0 下变动更剧烈
    delta = abs(h_wc - 1500)
    friendly_delta = abs(
        compute_elo.update_elo_after_match(1500, 1500, 1, 0, "Friendly", True)[0] - 1500)
    if expected_k_base > 20.0:
        assert delta > friendly_delta
