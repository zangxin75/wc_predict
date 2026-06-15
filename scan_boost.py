"""scan_boost.py — 平局 boost 后处理扫描

RHO 扫描已证伪(调 τ 救不了平局 argmax,且伤 Brier)。
改用 draw boost:泊松算出三态后,给 draw 概率乘因子 B(>1),重新归一化。
当 dr×B > hw 且 > aw 时,平局成 argmax。

扫 B 找平衡点:平局召回↑ vs 平局精确/整体 Brier↓。
用法:
    python scan_boost.py
"""

import os
import sys

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8")

import numpy as np

import compute_elo
import wc_predict
# 扫描手动 boost 时关闭 predict_from_elo 内部 boost,避免双重 boost
wc_predict.Config.DRAW_BOOST_B = None
from backtest import load_test_matches, actual_outcome

HERE = os.path.dirname(os.path.abspath(__file__))
RESULTS = os.path.join(HERE, "results.csv")

RUNS = [("2018-06-14", "2018-07-16", "2018"),
        ("2022-11-20", "2022-12-19", "2022")]
BOOSTS = [1.0, 1.3, 1.5, 1.8, 2.0, 2.5, 3.0]


def apply_boost(hw, dr, aw, b):
    """draw 概率乘 b,三态重新归一化。"""
    dr_b = dr * b
    s = hw + dr_b + aw
    return hw / s, dr_b / s, aw / s


def metrics(preds, outs):
    p = np.asarray(preds)
    o = np.asarray(outs)
    brier = float(np.mean(np.sum((p - o) ** 2, axis=1)))
    acc = float(np.mean(np.argmax(p, axis=1) == np.argmax(o, axis=1)))
    real_draw = [i for i in range(len(o)) if np.argmax(o[i]) == 1]
    pred_draw = [i for i in range(len(p)) if np.argmax(p[i]) == 1]
    recall = (sum(1 for i in real_draw if np.argmax(p[i]) == 1) / len(real_draw)
              if real_draw else float("nan"))
    prec = (sum(1 for i in pred_draw if np.argmax(o[i]) == 1) / len(pred_draw)
            if pred_draw else 0.0)
    return brier, acc, recall, prec, len(pred_draw)


def main():
    print("=" * 74)
    print("  平局 boost 扫描(RHO=-0.08 基线上,后处理 draw 概率乘 B)")
    print("  目标:平局召回↑ 且整体 Brier 不恶化、平局精确可接受")
    print("=" * 74)
    print(f"  {'B':<6}{'届':<6}{'Brier':<8}{'准确%':<7}{'平局召回%':<10}"
          f"{'平局精确%':<10}{'预测平局数':<8}")
    print("  " + "-" * 70)

    for cutoff, until, name in RUNS:
        elo, _, _ = compute_elo.build_elo(RESULTS, cutoff_date=cutoff)
        matches = load_test_matches(RESULTS, cutoff, "FIFA World Cup", until)
        for b in BOOSTS:
            preds, outs = [], []
            for m in matches:
                eh, ea = elo.get(m["home"]), elo.get(m["away"])
                if eh is None or ea is None:
                    continue
                hw, dr, aw, _, _ = wc_predict.predict_from_elo(eh, ea, home_adv_elo=0)
                hw, dr, aw = apply_boost(hw, dr, aw, b)
                preds.append([hw, dr, aw])
                outs.append(actual_outcome(m["hs"], m["as_"]))
            br, a, rec, prec, nd = metrics(preds, outs)
            print(f"  {b:<6}{name:<6}{br:<8.4f}{a*100:<6.1f}{rec*100:<9.1f}"
                  f"{prec*100:<9.1f}{nd:<8}")
        print("  " + "-" * 70)
    print("=" * 74)


if __name__ == "__main__":
    main()
