"""
test_ou_total.py — 对比"纯 Elo" vs "Elo + 市场 OU 总进球"在 30 场已打 WC2026 上的
比分预测准确度。

目的:验证 R3 模式(OU 赔率携带总进球信号)能否在不改 λ_h/λ_a 相对分配的前提下,
用市场总量替代常数 2.20,带来 Top-1 命中率提升。

方法:
  纯 Elo       = elo_to_lambdas(λ_total=2.20)
  Elo+OU       = lambda_from_market(oh,od,oa, ou=(over,under), ou_line) — 已含 OU 约束
  Elo+OU 但无OU = 同纯 Elo (兜底)

指标:
  Top-1 hit   = 实际比分 == 模型 argmax 概率
  Top-3 hit   = 实际比分 ∈ 模型 Top-3 概率
  P(actual)   = 模型给真实比分的概率(越高 = 越自信且对)
  Brier(score)= Σ (p_ij - y_ij)²,越小越好(y_ij 1 of 9x9 网格)

输出:打到 stdout,关键摘要末尾。
"""

import csv
import os
import sys
import math
from collections import defaultdict

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
sys.path.insert(0, ROOT)

import wc_predict
import compute_odds


PLAYED_CSV = os.path.join(ROOT, "wc2026_live.csv")
OU_CSV = os.path.join(ROOT, "ou_wc.csv")
ODDS_CSV = os.path.join(ROOT, "odds_wc.csv")


def load_played():
    out = []
    with open(PLAYED_CSV, newline="", encoding="utf-8") as f:
        for row in csv.DictReader(f):
            try:
                hs = int(row["home_score"])
                aws = int(row["away_score"])
            except (ValueError, KeyError):
                continue
            out.append({
                "date": row.get("date", ""),
                "home": row["home_team"],
                "away": row["away_team"],
                "hs": hs, "aws": aws,
            })
    return out


def predict_scores(elo_h, elo_a, mode, ohd=None, ou=None, ou_line=2.5):
    """mode: 'elo' 纯 Elo 总 2.20; 'market' 用 lambda_from_market 含 OU."""
    if mode == "elo":
        lam_h, lam_a = wc_predict.elo_to_lambdas(elo_h, elo_a)
    else:
        if ohd is None:
            return None
        lam = compute_odds.lambda_from_market(
            ohd[0], ohd[1], ohd[2], ou=ou, ou_line=ou_line
        )
        if lam is None:
            return None
        lam_h, lam_a = lam
    hw, dr, aw, matrix, top5 = wc_predict._poisson_three_way(lam_h, lam_a)
    return {"lam_h": lam_h, "lam_a": lam_a, "matrix": matrix, "top5": top5}


def top_n_indices(flat, n):
    return [kv[0] for kv in flat[:n]]


def evaluate(matches, mode):
    n_top1 = n_top3 = 0
    p_actual_sum = 0.0
    brier_sum = 0.0
    n = 0
    misses = []
    lam_h_list = []
    lam_a_list = []

    for m in matches:
        en_h = m["home"]; en_a = m["away"]
        zh_h = wc_predict.EN_TO_ZH.get(en_h)
        zh_a = wc_predict.EN_TO_ZH.get(en_a)
        if not (zh_h and zh_a):
            continue
        try:
            elo_h = wc_predict._elo_value(zh_h)
            elo_a = wc_predict._elo_value(zh_a)
        except KeyError:
            continue
        ohd = None; ou = None; ou_line = 2.5
        if mode == "market":
            odds_map = compute_odds.load_odds(ODDS_CSV)
            ou_map = compute_odds.load_ou(OU_CSV)
            year = "2026"
            ohd = compute_odds.lookup_odds(odds_map, en_h, en_a, year=year)
            ou_entry = compute_odds.lookup_ou(ou_map, en_h, en_a, year=year)
            if ou_entry is not None:
                ou = ou_entry.get("ou"); ou_line = ou_entry.get("ou_line", 2.5)
            if ohd is None:
                continue  # 无 1X2 价 → 跳过
        pred = predict_scores(elo_h, elo_a, mode, ohd=ohd, ou=ou, ou_line=ou_line)
        if pred is None:
            continue
        matrix = pred["matrix"]
        top5 = pred["top5"]
        top3 = top_n_indices(top5, 3)
        actual = (m["hs"], m["aws"])

        lam_h_list.append(pred["lam_h"])
        lam_a_list.append(pred["lam_a"])

        if top5[0][0] == actual:
            n_top1 += 1
        else:
            misses.append((m["date"], en_h, en_a, top5[0][0], actual))
        if actual in top3:
            n_top3 += 1
        p_actual_sum += matrix[actual[0]][actual[1]]
        # 单场 Brier(9x9 grid 截断到 MAX_GOALS)
        y = [[0.0] * (wc_predict.Config.MAX_GOALS + 1)
             for _ in range(wc_predict.Config.MAX_GOALS + 1)]
        y[actual[0]][actual[1]] = 1.0
        b = 0.0
        for i in range(wc_predict.Config.MAX_GOALS + 1):
            for j in range(wc_predict.Config.MAX_GOALS + 1):
                b += (matrix[i][j] - y[i][j]) ** 2
        brier_sum += b
        n += 1

    if n == 0:
        return None
    return {
        "n": n,
        "top1": n_top1 / n,
        "top3": n_top3 / n,
        "p_actual_mean": p_actual_sum / n,
        "brier_mean": brier_sum / n,
        "lam_h_mean": sum(lam_h_list) / n,
        "lam_a_mean": sum(lam_a_list) / n,
        "misses": misses[:10],
    }


def main():
    matches = load_played()
    print(f"已打 WC2026 比赛: {len(matches)} 场")

    print("\n=== A: 纯 Elo (λ_total=2.20 常数) ===")
    res_a = evaluate(matches, "elo")
    print(res_a)

    print("\n=== B: Elo + 市场 OU 总进球 (lambda_from_market) ===")
    res_b = evaluate(matches, "market")
    print(res_b)

    if res_a and res_b:
        print("\n=== 对比 ===")
        print(f"{'指标':<20} {'A 纯 Elo':>12} {'B Elo+OU':>12} {'Δ(B-A)':>10}")
        for k, name in [("top1", "Top-1 hit"), ("top3", "Top-3 hit"),
                        ("p_actual_mean", "P(actual) mean"),
                        ("brier_mean", "Brier (9x9) mean")]:
            va = res_a[k]; vb = res_b[k]
            delta = vb - va
            sign = "+" if delta >= 0 else ""
            print(f"{name:<20} {va:>12.4f} {vb:>12.4f} {sign}{delta:>10.4f}")
        print(f"{'λh mean':<20} {res_a['lam_h_mean']:>12.3f} {res_b['lam_h_mean']:>12.3f}")
        print(f"{'λa mean':<20} {res_a['lam_a_mean']:>12.3f} {res_b['lam_a_mean']:>12.3f}")


if __name__ == "__main__":
    main()