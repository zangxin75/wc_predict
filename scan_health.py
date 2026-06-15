"""scan_health.py — 探索:留出验证集 xgb 健康度信号能否指导 ensemble 降权

任务 #22 动机:分歧门控盲区在"一致场仍借力 0.3 xgb",xgb 系统性差时拖累 ensemble。
假设:用 cutoff 前【留出验证集】(不参与训练)评估 xgb vs elo 实际 Brier 差,
  xgb 近期差 → 全局升 w_base(降 xgb 权)。

与已证伪信号的差异:
  · recent_density 是输入特征(强队密度恒高,无区分力);
  · 验证窗口模型选择是"二选一硬切"(分布偏移,常规赛事 xgb 恒赢但世界杯翻车);
  · 本信号是【连续健康度 + 兜底降权】:不预测翻车(不可能),只提供"xgb 连验证集
    都差时少借力"的保守兜底。

两步验证:
  Step 1 — 信号有效性:验证集 health 符号能否区分 2018(xgb 突破)/2022(xgb 翻车)?
           2018 health 应<=0(xgb 好),2022 health 应>0(xgb 差)。
  Step 2 — adaptive 回测:用 health 调 w_base,看 ensemble Brier 是否改善。

用法:
    python scan_health.py
"""

import os
import sys

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8")

import numpy as np

import compute_elo
import wc_predict
import train_xgb
import ensemble

HERE = os.path.dirname(os.path.abspath(__file__))
RESULTS = os.path.join(HERE, "results.csv")

RUNS = [("2018-06-14", "2018-07-16", "2018"),
        ("2022-11-20", "2022-12-19", "2022")]
KS = [50, 100, 200]
# 已知单模型 Brier(含条件 boost 后的 elo;scan_cond_boost scale=120 值)
ELO_BASE = {"2018": 0.6134, "2022": 0.6105}
XGB_BASE = {"2018": 0.5765, "2022": 0.6234}   # train_xgb 历史值(boost 不影响 xgb)


def _row_key(r):
    return (r[0], r[4], r[5])    # (date, home, away)


def eval_split(model, eval_rows):
    """算 (xgb_brier, elo_brier) 在给定场次上。elo 用 predict_from_elo(含 boost)。"""
    if not eval_rows:
        return float("nan"), float("nan")
    X = [r[1] for r in eval_rows]
    xp = train_xgb.predict_proba(model, X)
    ep = []
    for r in eval_rows:
        hw, dr, aw, _, _ = wc_predict.predict_from_elo(r[1][0], r[1][1], home_adv_elo=0)
        ep.append([hw, dr, aw])
    outs = train_xgb._label_to_onehot([r[2] for r in eval_rows])
    return train_xgb.brier_score(list(xp), outs), train_xgb.brier_score(ep, outs)


def health_to_wbase(health, base=0.3, threshold=0.03):
    """验证集 health(xgb_brier - elo_brier)→ 一致场 w_base。
    health<=0(xgb 健康)→base;health>=threshold(xgb 崩)→1.0(纯 elo);中间线性。"""
    if health <= 0:
        return base
    if health >= threshold:
        return 1.0
    return base + (1.0 - base) * (health / threshold)


def main():
    rows = train_xgb.build_dataset()

    print("=" * 78)
    print("  Step 1 — 信号有效性:验证集 health 能否区分 xgb 好坏年?")
    print("  health = 验证集 xgb_brier - elo_brier  (>0 = xgb 差)")
    print("  期待:2018 health<=0(xgb 突破年)/ 2022 health>0(xgb 翻车年)")
    print("=" * 78)
    print(f"  {'届':<6}{'K':<5}{'验证xgb':<8}{'验证elo':<8}{'health':<8}"
          f"| {'测试xgb':<8}{'测试elo':<8}{'test差':<8}{'信号对?'}")
    print("  " + "-" * 74)
    healths = {}
    for cutoff, until, name in RUNS:
        pre = [r for r in rows if r[0] < cutoff and r[3] != "FIFA World Cup"]
        test = [r for r in rows if cutoff <= r[0] < until and r[3] == "FIFA World Cup"]
        for k in KS:
            validation = pre[-k:]
            val_keys = set(_row_key(r) for r in validation)
            train = [r for r in rows if r[0] < cutoff and _row_key(r) not in val_keys]
            model = train_xgb.train_model([r[1] for r in train], [r[2] for r in train])
            xg_v, el_v = eval_split(model, validation)
            xg_t, el_t = eval_split(model, test)
            health = xg_v - el_v
            test_diff = xg_t - el_t
            # 信号正确 = health 与 test_diff 同号(都能区分 xgb 好坏)
            ok = "✓" if health * test_diff > 0 or abs(health) < 0.005 else "✗"
            healths[(name, k)] = (health, test_diff)
            print(f"  {name:<6}{k:<5}{xg_v:<8.4f}{el_v:<8.4f}{health:<+8.4f}"
                  f"| {xg_t:<8.4f}{el_t:<8.4f}{test_diff:<+8.4f}{ok:>6}")
        print("  " + "-" * 74)

    print("\n" + "=" * 78)
    print("  Step 2 — adaptive ensemble 回测:health 调 w_base,看 ensemble 改善")
    print("  规则:分歧场→w_base=1.0(纯 elo);一致场→w_base=health_to_wbase(health)")
    print("=" * 78)
    print(f"  {'届':<6}{'K':<5}{'adaptive':<10}{'原ensemble':<11}{'elo':<9}"
          f"{'xgb':<9}{'vs 最优':<9}{'改善?'}")
    print("  " + "-" * 74)
    for cutoff, until, name in RUNS:
        e_b, x_b = ELO_BASE[name], XGB_BASE[name]
        for k in KS:
            # adaptive 回测:验证集 health 定 w_base,再跑 test
            pre = [r for r in rows if r[0] < cutoff and r[3] != "FIFA World Cup"]
            test = [r for r in rows if cutoff <= r[0] < until and r[3] == "FIFA World Cup"]
            validation = pre[-k:]
            val_keys = set(_row_key(r) for r in validation)
            train = [r for r in rows if r[0] < cutoff and _row_key(r) not in val_keys]
            model = train_xgb.train_model([r[1] for r in train], [r[2] for r in train])
            xg_v, el_v = eval_split(model, validation)
            health = xg_v - el_v
            w_base = health_to_wbase(health)
            # test 集逐场集成
            xp_test = train_xgb.predict_proba(model, [r[1] for r in test])
            preds = []
            for r, xp in zip(test, xp_test):
                hw, dr, aw, _, _ = wc_predict.predict_from_elo(
                    r[1][0], r[1][1], home_adv_elo=0)
                sp = [hw, dr, aw]
                ws, wx = ensemble.combine_weights(sp, list(xp), ctx={"w_base": w_base})
                preds.append([ws * a + wx * b for a, b in zip(sp, xp)])
            outs = train_xgb._label_to_onehot([r[2] for r in test])
            b_adapt = train_xgb.brier_score(preds, outs)
            # 原 ensemble(固定 w_base=0.3)参考
            b_orig = ensemble.backtest_ensemble(cutoff, until)[0]
            best = min(e_b, x_b)
            flag = "✓超" if b_adapt < best else ""
            improve = "✓改善" if b_adapt < b_orig else "✗未改善"
            print(f"  {name:<6}{k:<5}{b_adapt:<10.4f}{b_orig:<11.4f}{e_b:<9.4f}"
                  f"{x_b:<9.4f}{b_adapt-best:<+9.4f}{improve:>8}  w_base={w_base:.2f}")
        print("  " + "-" * 74)
    print("=" * 78)


if __name__ == "__main__":
    main()
