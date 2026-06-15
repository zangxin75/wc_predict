"""backtest 单元测试:评分函数数学正确性 + 测试集筛选逻辑。"""

import csv
import os

import pytest

import backtest

HERE = os.path.dirname(os.path.abspath(__file__))


@pytest.mark.parametrize("hs, as_, expected", [
    (2, 1, [1.0, 0.0, 0.0]),   # 主胜
    (1, 1, [0.0, 1.0, 0.0]),   # 平
    (0, 2, [0.0, 0.0, 1.0]),   # 客胜
    (3, 3, [0.0, 1.0, 0.0]),   # 高比分平
])
def test_actual_outcome(hs, as_, expected):
    assert backtest.actual_outcome(hs, as_) == expected


def test_score_metric_perfect_prediction():
    """预测概率 == 实际 → Brier = 0。"""
    preds = [[1.0, 0.0, 0.0], [0.0, 1.0, 0.0], [0.0, 0.0, 1.0]]
    outs = [[1.0, 0.0, 0.0], [0.0, 1.0, 0.0], [0.0, 0.0, 1.0]]
    s = backtest.score_metric(preds, outs)
    assert s["brier"] == pytest.approx(0.0, abs=1e-9)


def test_score_metric_empty():
    s = backtest.score_metric([], [])
    assert s["n"] == 0


def test_score_metric_confident_wrong_is_heavily_penalized():
    """自信(99%)且全错 → Brier 应接近 2(三态每态 (p-o)² 之和)。"""
    preds = [[0.99, 0.005, 0.005]]
    outs = [[0.0, 0.0, 1.0]]
    s = backtest.score_metric(preds, outs)
    assert s["brier"] > 1.0
    assert s["brier"] < 2.0


def test_score_metric_random_guess_baseline():
    """全预测 [1/3,1/3,1/3](随机猜)→ Brier 应≈0.667(2/3)。"""
    preds = [[1 / 3, 1 / 3, 1 / 3]] * 6
    outs = [
        [1.0, 0.0, 0.0], [0.0, 1.0, 0.0], [0.0, 0.0, 1.0],
        [1.0, 0.0, 0.0], [0.0, 1.0, 0.0], [0.0, 0.0, 1.0],
    ]
    s = backtest.score_metric(preds, outs)
    # 每场 Σ(1/3 − o_k)² = (1/3−1)² + (1/3)² + (1/3)² = 4/9+1/9+1/9 = 6/9 = 0.667
    assert s["brier"] == pytest.approx(2 / 3, abs=1e-9)


def test_score_metric_accuracy_and_logloss_perfect():
    """完美预测:accuracy=1.0、logloss≈0;全错:accuracy=0、logloss 大。"""
    s = backtest.score_metric([[1.0, 0.0, 0.0], [0.0, 1.0, 0.0]],
                              [[1, 0, 0], [0, 1, 0]])
    assert s["accuracy"] == pytest.approx(1.0)
    assert s["logloss"] == pytest.approx(0.0, abs=1e-9)
    # 全错(class 预测反了)→ logloss 因 clip(eps) 很大
    s2 = backtest.score_metric([[0.0, 1.0, 0.0], [1.0, 0.0, 0.0]],
                               [[1, 0, 0], [0, 1, 0]])
    assert s2["accuracy"] == pytest.approx(0.0)
    assert s2["logloss"] > 10.0


def test_score_metric_accuracy_argmax():
    """命中率:argmax 对的比例。三场错一场 → accuracy=2/3。"""
    preds = [[0.6, 0.3, 0.1], [0.2, 0.7, 0.1], [0.3, 0.3, 0.4]]
    outs = [[1, 0, 0], [0, 1, 0], [1, 0, 0]]          # 第三场实际 class0,预测 class1 → 错
    s = backtest.score_metric(preds, outs)
    assert s["accuracy"] == pytest.approx(2 / 3)


@pytest.fixture
def wc_csv(tmp_path):
    """构造含 cutoff 前后世界杯比赛的迷你 CSV,验证筛选逻辑。"""
    rows = [
        ["date", "home_team", "away_team", "home_score", "away_score",
         "tournament", "city", "country", "neutral"],
        ["2017-01-01", "A", "B", 1, 0, "Friendly", "X", "Y", "TRUE"],
        ["2018-06-13", "A", "B", 1, 1, "Friendly", "X", "Y", "TRUE"],
        ["2018-06-15", "A", "B", 2, 1, "FIFA World Cup", "X", "Y", "TRUE"],  # cutoff 后
        ["2018-06-20", "C", "D", 0, 0, "FIFA World Cup", "X", "Y", "TRUE"],  # cutoff 后
        ["2018-07-01", "E", "F", 3, 2, "Friendly", "X", "Y", "TRUE"],       # 非世界杯
    ]
    p = tmp_path / "wc.csv"
    with open(p, "w", newline="", encoding="utf-8") as f:
        csv.writer(f).writerows(rows)
    return str(p)


def test_load_test_matches_filters_correctly(wc_csv):
    """cutoff=2018-06-14 后的世界杯决赛圈比赛应只返回 2 场(A-B 2-1 和 C-D 0-0)。"""
    matches = backtest.load_test_matches(wc_csv, "2018-06-14", "FIFA World Cup")
    assert len(matches) == 2
    assert matches[0]["home"] == "A" and matches[0]["hs"] == 2
    assert matches[1]["home"] == "C" and matches[1]["as_"] == 0
