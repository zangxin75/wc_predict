"""scan_cond_boost.py — 条件平局 boost 扫描

全局 boost 已证伪(Brier 恶化,因对强弱悬殊场也误 boost)。
条件 boost:boost 因子随 |Elo 差| 指数衰减 —— 势均力敌才 boost,强弱悬殊不 boost。
  factor = 1 + (B-1) · exp(-|elo_diff|/scale)
  elo_diff=0 → factor=B(最大);|diff|>>scale → factor→1(不 boost)

物理依据:真实足球里平局集中在势均力敌的对决(双方互相制约),
        强弱悬殊场平局罕见 → 不该 boost。
固定 B=2.0(全局扫描中召回较好的值),扫 scale 找 Elo 差阈值。
用法:
    python scan_cond_boost.py
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
B_FIX = 2.0
SCALES = [40, 60, 80, 120, 200, 99999]      # 99999 ≈ 全局 boost(对照)


def cond_boost(hw, dr, aw, elo_diff, b, scale):
    factor = 1.0 + (b - 1.0) * np.exp(-abs(elo_diff) / scale)
    dr_b = dr * factor
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
    print("=" * 76)
    print(f"  条件平局 boost 扫描(固定 B={B_FIX},扫 scale= Elo 差衰减尺度)")
    print("  scale 越小 → 只对极接近的场 boost;scale=99999 ≈ 全局 boost(对照)")
    print("=" * 76)
    print(f"  {'scale':<8}{'届':<6}{'Brier':<8}{'准确%':<7}{'平局召回%':<10}"
          f"{'平局精确%':<10}{'预测平局数':<8}")
    print("  " + "-" * 72)
    # 基线(无 boost)对照
    for cutoff, until, name in RUNS:
        elo, _, _ = compute_elo.build_elo(RESULTS, cutoff_date=cutoff)
        matches = load_test_matches(RESULTS, cutoff, "FIFA World Cup", until)
        # 基线 B=1.0
        preds, outs = [], []
        for m in matches:
            eh, ea = elo.get(m["home"]), elo.get(m["away"])
            if eh is None or ea is None:
                continue
            hw, dr, aw, _, _ = wc_predict.predict_from_elo(eh, ea, home_adv_elo=0)
            preds.append([hw, dr, aw]); outs.append(actual_outcome(m["hs"], m["as_"]))
        br, a, rec, prec, nd = metrics(preds, outs)
        print(f"  {'基线':<8}{name:<6}{br:<8.4f}{a*100:<6.1f}{rec*100:<9.1f}"
              f"{prec*100:<9.1f}{nd:<8}")
        for scale in SCALES:
            preds, outs = [], []
            for m in matches:
                eh, ea = elo.get(m["home"]), elo.get(m["away"])
                if eh is None or ea is None:
                    continue
                hw, dr, aw, _, _ = wc_predict.predict_from_elo(eh, ea, home_adv_elo=0)
                hw, dr, aw = cond_boost(hw, dr, aw, eh - ea, B_FIX, scale)
                preds.append([hw, dr, aw]); outs.append(actual_outcome(m["hs"], m["as_"]))
            br, a, rec, prec, nd = metrics(preds, outs)
            tag = f"{scale}" if scale < 9999 else "全局"
            print(f"  {tag:<8}{name:<6}{br:<8.4f}{a*100:<6.1f}{rec*100:<9.1f}"
                  f"{prec*100:<9.1f}{nd:<8}")
        print("  " + "-" * 72)
    print("=" * 76)


if __name__ == "__main__":
    main()
