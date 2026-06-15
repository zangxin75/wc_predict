"""scan_rho.py — Dixon-Coles RHO 扫描:数据驱动找最优平局修正强度

动机:2026 真实验证暴露平局系统性漏判(10 场 4 平全漏,平局召回 0%)。
RHO 越负 → 0-0/1-1 联合概率上调越多 → 平局概率上升。
回测 2018/2022 扫 RHO,看 Brier + 平局召回/精确,找数据驱动最优值。
不能拍脑袋调参——先量化每个 RHO 的影响。

用法:
    python scan_rho.py
"""

import os
import sys

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8")

import numpy as np

import compute_elo
import wc_predict
# 扫描 RHO 时关闭平局 boost,测纯泊松 + τ 的行为(否则 boost 污染 RHO 信号)
wc_predict.Config.DRAW_BOOST_B = None
from backtest import load_test_matches, actual_outcome

HERE = os.path.dirname(os.path.abspath(__file__))
RESULTS = os.path.join(HERE, "results.csv")
ORIG_RHO = wc_predict.Config.RHO

RUNS = [("2018-06-14", "2018-07-16", "2018"),
        ("2022-11-20", "2022-12-19", "2022")]
RHOS = [-0.08, -0.12, -0.15, -0.20, -0.25, -0.30]


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
    print("=" * 72)
    print(f"  Dixon-Coles RHO 扫描(当前基线 RHO={ORIG_RHO})")
    print("  目标:Brier 最低 + 平局召回提升,且不伤整体准确")
    print("=" * 72)
    print(f"  {'RHO':<7}{'届':<6}{'Brier':<8}{'准确%':<7}{'平局召回%':<10}"
          f"{'平局精确%':<10}{'预测平局数':<8}")
    print("  " + "-" * 68)

    for cutoff, until, name in RUNS:
        elo, _, _ = compute_elo.build_elo(RESULTS, cutoff_date=cutoff)
        matches = load_test_matches(RESULTS, cutoff, "FIFA World Cup", until)
        for rho in RHOS:
            wc_predict.Config.RHO = rho
            preds, outs = [], []
            for m in matches:
                eh, ea = elo.get(m["home"]), elo.get(m["away"])
                if eh is None or ea is None:
                    continue
                hw, dr, aw, _, _ = wc_predict.predict_from_elo(eh, ea, home_adv_elo=0)
                preds.append([hw, dr, aw])
                outs.append(actual_outcome(m["hs"], m["as_"]))
            b, a, rec, prec, nd = metrics(preds, outs)
            print(f"  {rho:<7}{name:<6}{b:<8.4f}{a*100:<6.1f}{rec*100:<9.1f}"
                  f"{prec*100:<9.1f}{nd:<8}")
        print("  " + "-" * 68)
    wc_predict.Config.RHO = ORIG_RHO
    print("=" * 72)


if __name__ == "__main__":
    main()
