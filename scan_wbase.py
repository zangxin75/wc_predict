"""scan_wbase.py — 扫描固定 w_base:补门控盲区的务实方向

health 动态信号已证伪(scan_health.py:验证集是常规赛事,xgb 恒优,无法预判世界杯翻车)。
第 3 种动态信号失败,共同根因=分布偏移。退而求其次:承认"动态预测翻车不可能",
  扫描固定 w_base 找最稳健默认值。
  w_base=0.0 → 纯 elo(一致场也不借力,最稳健);
  w_base 越大 → 一致场借力 xgb 越多(2018 增益,2022 风险)。

目标:找 w_base 使两届都 ≤ 纯 elo,且优于当前默认 0.3。

用法:
    python scan_wbase.py
"""

import os
import sys

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8")

import ensemble

RUNS = [("2018-06-14", "2018-07-16", "2018"),
        ("2022-11-20", "2022-12-19", "2022")]
WB = [0.0, 0.1, 0.15, 0.2, 0.3, 0.5]
# 训练一次 xgb/届,复用(同届训练数据不随 w_base 变)
import train_xgb
import compute_elo
import wc_predict

HERE = os.path.dirname(os.path.abspath(__file__))
RESULTS = os.path.join(HERE, "results.csv")


def main():
    rows = train_xgb.build_dataset()
    print("=" * 72)
    print("  固定 w_base 扫描(一致场 stat 权重;分歧场恒 1.0 纯 elo)")
    print("  w_base=0.0 = 纯 elo(不借力 xgb);w_base 越大借力越多")
    print("=" * 72)
    print(f"  {'w_base':<8}{'届':<6}{'集成Brier':<11}{'vs w=0(elo)':<13}{'vs w=0.3':<11}")
    print("  " + "-" * 68)

    b_w03 = {}     # 每届 w_base=0.3 的 Brier(对照)
    b_w00 = {}     # 每届 w_base=0.0 的 Brier(=纯 elo 基准)
    results = {}
    for cutoff, until, name in RUNS:
        train = [r for r in rows if r[0] < cutoff]
        test = [(i, r) for i, r in enumerate(rows)
                if cutoff <= r[0] < until and r[3] == "FIFA World Cup"]
        model = train_xgb.train_model([r[1] for r in train], [r[2] for r in train])
        xgb_probs = train_xgb.predict_proba(model, [r[1] for _, r in test])
        outs = train_xgb._label_to_onehot([r[2] for _, r in test])
        # 预算各场 stat 与 xgb 概率
        for wb in WB:
            ctx = {"w_base": wb}
            preds = []
            for (i, r), xp in zip(test, xgb_probs):
                hw, dr, aw, _, _ = wc_predict.predict_from_elo(
                    r[1][0], r[1][1], home_adv_elo=0)
                sp = [hw, dr, aw]
                ws, wx = ensemble.combine_weights(sp, list(xp), ctx)
                preds.append([ws * a + wx * b for a, b in zip(sp, xp)])
            b = train_xgb.brier_score(preds, outs)
            results[(name, wb)] = b
            if wb == 0.0:
                b_w00[name] = b
            if wb == 0.3:
                b_w03[name] = b

    for cutoff, until, name in RUNS:
        for wb in WB:
            b = results[(name, wb)]
            d00 = b - b_w00[name]
            d03 = b - b_w03[name]
            mark = "  ← 当前默认" if wb == 0.3 else ""
            print(f"  {wb:<8}{name:<6}{b:<11.4f}{d00:<+13.4f}{d03:<+11.4f}{mark}")
        print("  " + "-" * 68)

    # 均值
    print(f"  {'均值':<8}{'':<6}", end="")
    best_wb, best_mean = None, 9
    for wb in WB:
        m = sum(results[(n, wb)] for _, _, n in RUNS) / len(RUNS)
        if m < best_mean:
            best_wb, best_mean = wb, m
    for wb in WB:
        m = sum(results[(n, wb)] for _, _, n in RUNS) / len(RUNS)
        tag = "  ← 最低均值" if wb == best_wb else ""
        if wb == WB[0]:
            print(f"{m:<11.4f}")
        else:
            print(f"  {wb:<8}{'':<6}{m:<11.4f}")
    print("=" * 72)
    print(f"  最低均值 w_base = {best_wb} (均值 {best_mean:.4f})")


if __name__ == "__main__":
    main()
