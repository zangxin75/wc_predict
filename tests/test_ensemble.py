"""test_ensemble.py — ensemble 单元测试。

combine_weights(learning 点 1,分歧门控)纯函数,快速单测。
backtest_ensemble 端到端(调 train_xgb 全量训练),标 @slow。
"""

import os
import sys

import pytest

HERE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if HERE not in sys.path:
    sys.path.insert(0, HERE)

import ensemble


# ── ENS-01:一致场(argmax 同)→ w_stat=base ───────────────────
def test_combine_weights_agree():
    """两模型都预测 index 0 赢(一致)→ 借力 xgb,w_stat=base。
    显式传 w_base=0.3 隔离默认值变化(函数当前默认 0.2)。"""
    ws, wx = ensemble.combine_weights([0.5, 0.3, 0.2], [0.4, 0.4, 0.2],
                                      {"w_base": 0.3})
    assert abs(ws - 0.3) < 1e-9
    assert abs(wx - 0.7) < 1e-9


# ── ENS-02:分歧场(argmax 不同)→ w_stat=1.0(纯 elo)─────────
def test_combine_weights_disagree():
    """elo 预测 index 0 赢、xgb 预测 index 1 赢(分歧)→ 纯 elo。"""
    ws, wx = ensemble.combine_weights([0.5, 0.3, 0.2], [0.2, 0.5, 0.3])
    assert ws == 1.0 and wx == 0.0


# ── ENS-03:w_base 覆盖一致场权重 ─────────────────────────────
def test_combine_weights_w_base_override():
    ws, wx = ensemble.combine_weights([0.5, 0.3, 0.2], [0.4, 0.4, 0.2],
                                      {"w_base": 0.45})
    assert abs(ws - 0.45) < 1e-9
    assert abs(wx - 0.55) < 1e-9


# ── ENS-04:权重和恒为 1(一致 + 分歧)─────────────────────────
def test_combine_weights_sums_to_one():
    for sp, xp in [([0.6, 0.3, 0.1], [0.5, 0.3, 0.2]),   # 一致
                   ([0.6, 0.3, 0.1], [0.1, 0.6, 0.3])]:  # 分歧
        for base in (0.0, 0.25, 0.5, 0.75, 1.0):
            ws, wx = ensemble.combine_weights(sp, xp, {"w_base": base})
            assert abs(ws + wx - 1.0) < 1e-9


# ── ENS-05:clamp 到 [0, 1] ──────────────────────────────────
def test_combine_weights_clamps():
    ws, wx = ensemble.combine_weights([.5, .3, .2], [.4, .4, .2], {"w_base": 1.5})
    assert ws == 1.0 and wx == 0.0
    ws, wx = ensemble.combine_weights([.5, .3, .2], [.4, .4, .2], {"w_base": -0.3})
    assert ws == 0.0 and wx == 1.0


# ── ENS-06:加权集成结果正确性(一致场)────────────────────────
def test_combine_weights_weighted_average():
    """一致场 w_base=0.3 → 集成[0]=0.3*0.6+0.7*0.4=0.46。
    显式传 w_base=0.3 隔离默认值变化。"""
    sp = [0.6, 0.2, 0.2]
    xp = [0.4, 0.4, 0.2]
    ws, wx = ensemble.combine_weights(sp, xp, {"w_base": 0.3})
    mixed = [ws * a + wx * b for a, b in zip(sp, xp)]
    assert abs(mixed[0] - 0.46) < 1e-9
    assert abs(mixed[1] - 0.34) < 1e-9


# ── ENS-07:端到端回测(全量训练,@slow)─────────────────────
@pytest.mark.slow
def test_backtest_ensemble_runs():
    brier, n, nd = ensemble.backtest_ensemble("2018-06-14", "2018-07-16")
    assert n == 64
    assert 0 <= nd <= 10                                  # 分歧场少数(实测 3)
    assert 0.0 <= brier <= 0.767                          # 不差于随机太多
