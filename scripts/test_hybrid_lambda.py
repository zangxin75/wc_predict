"""test_hybrid_lambda.py — DEEPSEEK 提议的"有 OU 走 Mode B,无 OU 走 blend"实测。

⚠️ 2026-06-20 修正:之前版本没传 ou= 参数 → 错把 Mode A(1X2-only,total 强制 2.20)
当成"纯市场"测,导致 hybrid 在 30 场反而看起来差。DEEPSEEK 指出这是测错对象。

DEEPSEEK 实际提议:
  有 OU 时: λ = lambda_from_market(1X2, ou=ou_val)    ← Mode B(total 不约束 2.20)
  无 OU 时: λ = 0.7 × market_inverted(1X2) + 0.3 × Elo  ← Mode A blend

DEEPSEEK 在 305 场(1998-2026)上的结论:
  纯 Elo:       Top1=14.1%  Top3=34.4%
  纯市场:       Top1=15.7%  Top3=35.7%
  0.7/0.3 混合: Top1=16.1%  Top3=36.7%  ← 标的最优
"""

import csv
import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
sys.path.insert(0, ROOT)

import wc_predict
import compute_odds


PLAYED_CSV = os.path.join(ROOT, "wc2026_live.csv")
ODDS_CSV = os.path.join(ROOT, "odds_wc.csv")
OU_CSV = os.path.join(ROOT, "ou_wc.csv")


def load_played():
    out = []
    with open(PLAYED_CSV, newline="", encoding="utf-8") as f:
        for row in csv.DictReader(f):
            try:
                out.append({
                    "home": row["home_team"],
                    "away": row["away_team"],
                    "hs": int(row["home_score"]),
                    "aws": int(row["away_score"]),
                })
            except (ValueError, KeyError):
                continue
    return out


def predict_one(home_zh, away_zh, mode, weight_market=0.7):
    """mode: 'elo' / 'mkt_B' / 'mkt_A_blend' / 'mkt_B_blend'。

    mkt_A_blend = DEEPSEEK 无 OU 路径:0.7×Mode A + 0.3×Elo
    mkt_B       = DEEPSEEK 有 OU 路径:Mode B(1X2 + OU)
    mkt_B_blend = 在 mkt_B 基础上再 0.7×market + 0.3×Elo(谨慎 hedge)
    """
    elo_h = wc_predict._elo_value(home_zh)
    elo_a = wc_predict._elo_value(away_zh)
    elo_lam_h, elo_lam_a = wc_predict.elo_to_lambdas(elo_h, elo_a)

    if mode == "elo":
        lam_h, lam_a = elo_lam_h, elo_lam_a
        ou_val = None
    else:
        odds_map = compute_odds.load_odds(ODDS_CSV)
        ou_map = compute_odds.load_ou(OU_CSV)
        en_h = wc_predict.ZH_TO_EN[home_zh]
        en_a = wc_predict.ZH_TO_EN[away_zh]
        ohd = compute_odds.lookup_odds(odds_map, en_h, en_a, year="2026")
        if ohd is None:
            return None
        ou_entry = compute_odds.lookup_ou(ou_map, en_h, en_a, year="2026")
        ou_val = ou_entry.get("ou") if ou_entry else None
        ou_line = ou_entry.get("ou_line", 2.5) if ou_entry else 2.5

        # Mode A:仅 1X2(total 强制 ≈ AVG_TOTAL_GOALS = 2.20)
        mlam_A = compute_odds.lambda_from_market(ohd[0], ohd[1], ohd[2])
        # Mode B:1X2 + OU(total 由市场决定)
        mlam_B = None
        if ou_val is not None:
            mlam_B = compute_odds.lambda_from_market(
                ohd[0], ohd[1], ohd[2], ou=ou_val, ou_line=ou_line
            )

        if mode == "mkt_A_blend":
            if mlam_A is None:
                return None
            lam_h = weight_market * mlam_A[0] + (1 - weight_market) * elo_lam_h
            lam_a = weight_market * mlam_A[1] + (1 - weight_market) * elo_lam_a
        elif mode == "mkt_B":
            # 有 OU 用 Mode B,无 OU 退到 Mode A
            if mlam_B is not None:
                lam_h, lam_a = mlam_B
            elif mlam_A is not None:
                lam_h, lam_a = mlam_A
            else:
                return None
        elif mode == "mkt_B_blend":
            base = mlam_B if mlam_B is not None else mlam_A
            if base is None:
                return None
            lam_h = weight_market * base[0] + (1 - weight_market) * elo_lam_h
            lam_a = weight_market * base[1] + (1 - weight_market) * elo_lam_a
        else:
            raise ValueError(mode)

    hw, dr, aw, matrix, top5 = wc_predict._poisson_three_way(lam_h, lam_a)
    return {"lam_h": lam_h, "lam_a": lam_a, "matrix": matrix, "top5": top5,
            "has_ou": ou_val is not None}


def evaluate(matches, mode, **kwargs):
    n_top1 = n_top3 = 0
    p_actual_sum = 0.0
    n_with_ou = n_no_ou = 0
    n = 0
    for m in matches:
        zh_h = wc_predict.EN_TO_ZH.get(m["home"])
        zh_a = wc_predict.EN_TO_ZH.get(m["away"])
        if not (zh_h and zh_a):
            continue
        pred = predict_one(zh_h, zh_a, mode, **kwargs)
        if pred is None:
            continue
        matrix = pred["matrix"]
        top5 = pred["top5"]
        top3 = [kv[0] for kv in top5[:3]]
        actual = (m["hs"], m["aws"])
        if top5[0][0] == actual:
            n_top1 += 1
        if actual in top3:
            n_top3 += 1
        p_actual_sum += matrix[actual[0]][actual[1]]
        if pred["has_ou"]:
            n_with_ou += 1
        else:
            n_no_ou += 1
        n += 1
    return {
        "n": n, "n_with_ou": n_with_ou, "n_no_ou": n_no_ou,
        "top1": n_top1 / n if n else 0,
        "top3": n_top3 / n if n else 0,
        "p_actual": p_actual_sum / n if n else 0,
    }


def main():
    matches = load_played()
    print(f"已打 WC2026 比赛: {len(matches)} 场\n")

    modes = [
        ("elo", "A. 纯 Elo (λ_total=2.20)"),
        ("mkt_A_blend", "B. 0.7×Mode A + 0.3×Elo (无 OU 时用)"),
        ("mkt_B", "C. Mode B(有 OU 用,无 OU 退 Mode A)"),
        ("mkt_B_blend", "D. Mode B + 0.7/0.3 blend (hedge)"),
    ]
    results = {}
    for mode, label in modes:
        r = evaluate(matches, mode)
        results[label] = r
        print(f"{label}:")
        print(f"  n={r['n']} (OU={r['n_with_ou']}, 无 OU={r['n_no_ou']})")
        print(f"  Top1: {r['top1']*100:5.1f}%  Top3: {r['top3']*100:5.1f}%  P(actual): {r['p_actual']:.4f}")
        print()

    print("--- DEEPSEEK 提议权重扫描(对 Mode B blend) ---")
    for w in [0.0, 0.3, 0.5, 0.6, 0.7, 0.8, 0.9, 1.0]:
        r = evaluate(matches, "mkt_B_blend", weight_market=w)
        print(f"  w_market={w:.1f}: Top1={r['top1']*100:5.1f}%  Top3={r['top3']*100:5.1f}%  (OU={r['n_with_ou']})")

    print("\n--- Mode A blend 权重扫描(无 OU 时的回退) ---")
    for w in [0.0, 0.3, 0.5, 0.7, 1.0]:
        r = evaluate(matches, "mkt_A_blend", weight_market=w)
        print(f"  w_market={w:.1f}: Top1={r['top1']*100:5.1f}%  Top3={r['top3']*100:5.1f}%")


if __name__ == "__main__":
    main()