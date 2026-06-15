"""test_train_xgb.py — train_xgb 单元测试(mock CSV / 小样本,不依赖全量训练)。

覆盖:特征构建维度、标签编码、walk-forward 首场初始值、模型预测形状、
Brier 指标正确性。TX-06(全量回测)属端到端,标 @slow。
"""

import os
import sys

import numpy as np
import pytest

HERE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if HERE not in sys.path:
    sys.path.insert(0, HERE)

import train_xgb

HEADER = ("date,home_team,away_team,home_score,away_score,"
          "tournament,city,country,neutral\n")


def _write_csv(path, *matches):
    """matches: (date, home, away, hs, as, tournament) 元组序列。"""
    lines = [HEADER]
    for d, h, a, hs, as_, t in matches:
        lines.append(f"{d},{h},{a},{hs},{as_},{t},X,Y,TRUE\n")
    path.write_text("".join(lines), encoding="utf-8")
    return str(path)


# ── TX-01:特征维度 + 标签编码 ────────────────────────────────
def test_build_dataset_feature_dim_and_labels(tmp_path):
    """每行 9 维特征,标签 ∈ {0,1,2}(主胜/平/客胜)。"""
    p = _write_csv(tmp_path / "r.csv",
                   ("2020-01-01", "A", "B", 2, 1, "Friendly"),   # 主胜 → 0
                   ("2020-01-02", "C", "D", 1, 1, "Friendly"),   # 平 → 1
                   ("2020-01-03", "E", "F", 0, 3, "Friendly"))   # 客胜 → 2
    rows = train_xgb.build_dataset(p)
    assert len(rows) == 3
    assert all(len(r[1]) == 9 for r in rows)            # 9 维特征
    assert [r[2] for r in rows] == [0, 1, 2]            # 标签编码正确


# ── TX-02:walk-forward 首场初始值 ────────────────────────────
def test_build_dataset_first_match_initial(tmp_path):
    """首场两队 Elo=1500(初始),form=FORM_PRIOR(无历史)。"""
    p = _write_csv(tmp_path / "r.csv",
                   ("2020-01-01", "A", "B", 1, 0, "Friendly"))
    rows = train_xgb.build_dataset(p)
    f = rows[0][1]
    assert f[0] == 1500.0                                # elo_home 初始
    assert f[1] == 1500.0                                # elo_away 初始
    assert f[2] == 0.0                                   # elo_diff
    assert f[3] == train_xgb.FORM_PRIOR                  # gf_home 无历史


# ── TX-03:walk-forward 赛后更新(无泄漏)──────────────────────
def test_build_dataset_elo_updates_between_matches(tmp_path):
    """A 首场赢 → 第二场 A 的 Elo 应高于初始 1500(特征用赛前=更新后值)。"""
    p = _write_csv(tmp_path / "r.csv",
                   ("2020-01-01", "A", "B", 3, 0, "FIFA World Cup"),   # A 大胜
                   ("2020-01-02", "A", "C", 1, 1, "FIFA World Cup"))   # A 再战
    rows = train_xgb.build_dataset(p)
    # 第二场 A 作主队,elo_home 应反映首场胜利(Elo 升 >1500)
    assert rows[1][1][0] > 1500.0
    # 首场 A 的 elo_home 仍是 1500(赛前未更新)
    assert rows[0][1][0] == 1500.0


# ── TX-04:按日期升序 ────────────────────────────────────────
def test_build_dataset_sorted_by_date(tmp_path):
    p = _write_csv(tmp_path / "r.csv",
                   ("2020-03-01", "A", "B", 1, 0, "Friendly"),
                   ("2020-01-01", "C", "D", 1, 0, "Friendly"))
    rows = train_xgb.build_dataset(p)
    assert rows[0][0] < rows[1][0]                       # 升序


# ── TX-05:模型预测形状 + 概率和为 1 ──────────────────────────
def test_train_model_predict_shape():
    """小样本训练 → predict 返回 (n, 3),行和≈1(softprob)。"""
    rng = np.random.default_rng(0)
    X = rng.uniform(1300, 1900, size=(80, 9)).tolist()
    y = (rng.integers(0, 3, size=80)).tolist()
    model = train_xgb.train_model(X, y, num_boost_round=10)
    preds = train_xgb.predict_proba(model, X[:5])
    assert preds.shape == (5, 3)
    assert np.allclose(preds.sum(axis=1), 1.0, atol=1e-4)
    assert np.all(preds >= 0)                            # 概率非负


# ── TX-06:Brier 指标正确性 ───────────────────────────────────
def test_brier_perfect_is_zero():
    """完美确定预测 → Brier=0。"""
    preds = [[1.0, 0.0, 0.0], [0.0, 0.0, 1.0]]
    outs = [[1.0, 0.0, 0.0], [0.0, 0.0, 1.0]]
    assert train_xgb.brier_score(preds, outs) == 0.0


def test_brier_random_approx_two_thirds():
    """均匀预测 → Brier≈2/3(随机基线)。"""
    preds = [[1 / 3, 1 / 3, 1 / 3]] * 300
    outs = [[1.0, 0.0, 0.0]] * 300
    assert abs(train_xgb.brier_score(preds, outs) - 2 / 3) < 0.01


# ── TX-07:label → onehot ─────────────────────────────────────
def test_label_to_onehot():
    oh = train_xgb._label_to_onehot([0, 1, 2])
    assert oh.shape == (3, 3)
    assert np.allclose(oh, np.eye(3))


# ── TX-08:回测流程跑通(mock 数据)────────────────────────────
def test_backtest_xgb_runs(tmp_path):
    """mock 足量数据 → backtest_xgb 返回 (brier, n, model),brier 合理。"""
    matches = []
    # 训练集:2017 友谊赛(主队略强构造可学模式)
    for i in range(100):
        matches.append((f"2017-06-{(i % 28) + 1:02d}", "Strong", "Weak", 2, 0, "Friendly"))
    # 测试集:2018 世界杯(全 >= cutoff 2018-06-14,固定日保证 40 行都进窗口)
    for i in range(40):
        matches.append(("2018-06-20", "Strong", "Weak", 2, 0, "FIFA World Cup"))
    p = _write_csv(tmp_path / "r.csv", *matches)
    rows = train_xgb.build_dataset(p)
    brier, n, model = train_xgb.backtest_xgb(rows, "2018-06-14", "2018-07-16")
    assert n == 40
    assert 0.0 <= brier <= 0.667 + 0.1                   # 不差于随机太多
