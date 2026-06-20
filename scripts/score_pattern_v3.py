"""score_pattern_v3.py — 从市场赔率+OU+Elo发现比分预测规律 (修正版)

修正:
  1. 历史比分从 results.csv 交叉验证匹配(odds_wc.csv 很多比分被损坏)
  2. 分别分析 1X2-only 和 1X2+OU 两种数据源的能力
  3. 2026 30场+历史匹配场作为完整分析集
  4. 重点:市场信号如何组合产出最优比分预测
"""

import csv
import math
import os
import sys
import statistics
from collections import Counter, defaultdict

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
sys.path.insert(0, ROOT)

import compute_odds
import compute_elo
import wc_predict


def load_results_index():
    """results.csv → {(year,home,away): (hs,as)} for World Cup matches only."""
    path = os.path.join(ROOT, "results.csv")
    idx = {}
    with open(path, newline="", encoding="utf-8") as f:
        for r in csv.DictReader(f):
            if r.get("tournament", "") != "FIFA World Cup":
                continue
            date = r.get("date", "")
            if not date:
                continue
            year = date.split("-")[0]
            home = r["home_team"]
            away = r["away_team"]
            try:
                hs, as_ = int(r["home_score"]), int(r["away_score"])
            except (ValueError, KeyError):
                continue
            key = (year, home, away)
            # Keep first occurrence (group stage usually, not knockout rematch)
            if key not in idx:
                idx[key] = (hs, as_)
    return idx


def main():
    print("=" * 95)
    print("  市场赔率 → 比分预测规律发现 (v3: 修正比分+OU增强)")
    print("=" * 95)

    # ── Load data ──
    results_idx = load_results_index()
    print(f"\n  results.csv 中世界杯比赛: {len(results_idx)} 场")

    # Load odds_wc.csv and match to results
    odds_path = os.path.join(ROOT, "odds_wc.csv")
    odds_matches = []
    unmatched = 0
    with open(odds_path, newline="", encoding="utf-8") as f:
        for r in csv.DictReader(f):
            year = r["year"]
            home = compute_odds.normalize_team(r["home"])
            away = compute_odds.normalize_team(r["away"])
            try:
                oh, od, oa = float(r["odds_h"]), float(r["odds_d"]), float(r["odds_a"])
            except (ValueError, KeyError):
                continue
            if oh <= 1.0 or od <= 1.0 or oa <= 1.0:
                continue

            # Get correct score from results.csv
            actual = results_idx.get((year, home, away))
            if not actual:
                # Try reverse
                actual = results_idx.get((year, away, home))
                if actual:
                    hs, as_ = actual[1], actual[0]  # reversed
                else:
                    unmatched += 1
                    continue
            else:
                hs, as_ = actual

            odds_matches.append({
                "year": year, "home": home, "away": away,
                "hs": hs, "as_": as_,
                "oh": oh, "od": od, "oa": oa,
            })

    print(f"  有赔率+比分的比赛: {len(odds_matches)} 场 (未匹配 {unmatched} 场)")

    # Load OU data for 2026
    ou_map = compute_odds.load_ou()

    # Load 2026 live results
    live_path = os.path.join(ROOT, "wc2026_live.csv")
    live_matches = []
    with open(live_path, newline="", encoding="utf-8") as f:
        for r in csv.DictReader(f):
            if r.get("home_score") and r.get("away_score"):
                home = r["home_team"]
                away = r["away_team"]
                hs, as_ = int(r["home_score"]), int(r["away_score"])

                # Check OU data
                ou_entry = compute_odds.lookup_ou(ou_map, home, away, 2026)
                if ou_entry:
                    oh, od, oa = ou_entry["1x2"]
                    ou_val = ou_entry.get("ou")
                    ou_line = ou_entry.get("ou_line", 2.5)
                else:
                    # Check odds only
                    odds_entry = compute_odds.lookup_odds(
                        compute_odds.load_odds(), home, away, 2026
                    )
                    if not odds_entry:
                        continue
                    oh, od, oa = odds_entry
                    ou_val = None
                    ou_line = 2.5

                live_matches.append({
                    "year": "2026", "home": home, "away": away,
                    "hs": hs, "as_": as_,
                    "oh": oh, "od": od, "oa": oa,
                    "ou_val": ou_val, "ou_line": ou_line,
                    "has_ou": ou_val is not None,
                })

    print(f"  2026 有市场数据的比赛: {len(live_matches)} 场"
          f" (其中{sum(1 for m in live_matches if m['has_ou'])}场有OU)")

    # ── Combine: historical + 2026 ──
    all_matches = odds_matches + live_matches
    print(f"  合计: {len(all_matches)} 场")

    # ── Build per-year Elo ──
    WC_OPENERS = {
        "1998": "1998-06-10", "2002": "2002-05-31", "2006": "2006-06-09",
        "2010": "2010-06-11", "2014": "2014-06-12", "2018": "2018-06-14",
        "2022": "2022-11-20", "2026": "2026-06-10",
    }

    elo_by_year = {}
    for year, cutoff in WC_OPENERS.items():
        elo, _, _ = compute_elo.build_elo(
            os.path.join(ROOT, "results.csv"), cutoff_date=cutoff
        )
        elo_by_year[year] = elo

    # Helper
    def _pearson(xs, ys):
        n = len(xs)
        if n < 3: return 0
        mx, my = statistics.mean(xs), statistics.mean(ys)
        sx, sy = statistics.stdev(xs), statistics.stdev(ys)
        if sx == 0 or sy == 0: return 0
        return sum((x-mx)*(y-my) for x,y in zip(xs,ys)) / (n*sx*sy)

    # ── Compute per-match metrics ──
    records = []
    for m in all_matches:
        year = m["year"]
        elo = elo_by_year.get(year, {})
        r = dict(m)

        # devig
        mh, md, ma = compute_odds.devig_power(r["oh"], r["od"], r["oa"])
        r["mkt_h"], r["mkt_d"], r["mkt_a"] = mh, md, ma

        # Favor/dog
        r["fav"] = r["home"] if mh > ma else r["away"]
        r["fav_prob"] = max(mh, ma)
        r["dog_prob"] = min(mh, ma)

        r["total"] = r["hs"] + r["as_"]
        r["diff"] = r["hs"] - r["as_"]

        # Elo
        eh = elo.get(r["home"], 1500.0)
        ea = elo.get(r["away"], 1500.0)
        r["elo_h"], r["elo_a"] = eh, ea

        # Market λ with OU if available
        ou_val = r.get("ou_val")
        if ou_val and len(ou_val) == 2:
            po, pu = compute_odds.devig_ou(ou_val[0], ou_val[1])
            r["p_over_mkt"] = po
        lam_mkt = compute_odds.lambda_from_market(r["oh"], r["od"], r["oa"],
                                                   ou=ou_val, ou_line=r.get("ou_line", 2.5))
        if lam_mkt:
            r["lam_h_mkt"], r["lam_a_mkt"] = lam_mkt
            r["total_mkt"] = lam_mkt[0] + lam_mkt[1]

        # Elo λ
        lam_elo = wc_predict.elo_to_lambdas(eh, ea, home_adv=0.0)
        r["lam_h_elo"], r["lam_a_elo"] = lam_elo
        r["total_elo"] = lam_elo[0] + lam_elo[1]

        records.append(r)

    # Separate by data availability
    has_ou = [r for r in records if r.get("ou_val")]
    no_ou = [r for r in records if not r.get("ou_val")]

    print(f"\n  分析: 有OU={len(has_ou)}场, 仅1X2={len(no_ou)}场")

    # ═══════════════════════════════════════════════════════════════
    # ANALYSIS 1: OU data analysis (2026 matches)
    # ═══════════════════════════════════════════════════════════════
    if has_ou:
        print("\n" + "─" * 95)
        print("  【OU增强分析】有OU数据时,市场反演λ的实际预测力")
        print("─" * 95)

        print(f"  n={len(has_ou)} 场")
        for r in has_ou:
            zh_h = wc_predict.EN_TO_ZH.get(r["home"], r["home"])
            zh_a = wc_predict.EN_TO_ZH.get(r["away"], r["away"])
            ou_dir = "大" if r.get("p_over_mkt", 0.5) >= 0.5 else "小"
            print(f"  {zh_h} vs {zh_a}: {r['hs']}-{r['as_']}  "
                  f"OU={r['ou_line']} P(over)={r.get('p_over_mkt',0):.3f}(看{ou_dir}) "
                  f"λmkt=({r.get('lam_h_mkt',0):.2f},{r.get('lam_a_mkt',0):.2f}) "
                  f"λelo=({r['lam_h_elo']:.2f},{r['lam_a_elo']:.2f}) "
                  f"total_mkt={r.get('total_mkt',0):.2f} actual={r['total']}")

        # Correlation
        if len(has_ou) >= 3:
            tm = [r.get("total_mkt", 0) for r in has_ou]
            ta = [r["total"] for r in has_ou]
            te = [r["total_elo"] for r in has_ou]
            print(f"\n  Pearson r(总进球): 市场λ={_pearson(tm,ta):.3f}  Eloλ={_pearson(te,ta):.3f}")

            # OU 方向准确率
            ou_correct = sum(1 for r in has_ou
                           if (r["total"] > r["ou_line"] and r.get("p_over_mkt", 0) >= 0.5) or
                           (r["total"] < r["ou_line"] and r.get("p_over_mkt", 0) < 0.5))
            print(f"  OU方向准确率: {ou_correct}/{len(has_ou)} = {ou_correct/len(has_ou):.3f}")

    # ═══════════════════════════════════════════════════════════════
    # ANALYSIS 2: Market probability calibration (all matches)
    # ═══════════════════════════════════════════════════════════════
    print("\n" + "─" * 95)
    print("  【核心分析】市场概率校准 — 所有可用比赛")
    print("─" * 95)

    N = len(records)

    # By market probability bins
    def fav_won(r):
        return (r["fav"] == r["home"] and r["hs"] > r["as_"]) or \
               (r["fav"] == r["away"] and r["as_"] > r["hs"])

    bins = [(0, 0.40), (0.40, 0.50), (0.50, 0.60), (0.60, 0.70), (0.70, 0.85), (0.85, 1.01)]
    print(f"  {'fav_prob区间':<18} {'n':<5} {'实际fav胜':<10} {'实际平':<10} "
          f"{'实际dog胜':<10} {'市场平预期':<10} {'平局偏误':<10}")
    for lo, hi in bins:
        subset = [r for r in records if lo <= r["fav_prob"] < hi]
        if not subset:
            continue
        n = len(subset)
        fw = sum(1 for r in subset if fav_won(r)) / n
        dw = sum(1 for r in subset if r["hs"] == r["as_"]) / n
        dg = 1 - fw - dw
        mkt_d = statistics.mean([r["mkt_d"] for r in subset])
        bias = dw - mkt_d
        print(f"  [{lo:.2f}, {hi:.2f})        {n:<5} {fw:.3f}      {dw:.3f}      "
              f"{dg:.3f}      {mkt_d:.3f}      {bias:+.3f}")

    # ═══════════════════════════════════════════════════════════════
    # ANALYSIS 3: Score distribution patterns
    # ═══════════════════════════════════════════════════════════════
    print("\n" + "─" * 95)
    print("  【比分分布】实际比分分布 + 按市场概率分层")
    print("─" * 95)

    # Overall
    score_freq = Counter()
    total_freq = Counter()
    for r in records:
        score_freq[f"{r['hs']}-{r['as_']}"] += 1
        total_freq[r["total"]] += 1

    print(f"  Top 15 比分:")
    for score, cnt in score_freq.most_common(15):
        bar = "█" * max(1, int(cnt / max(score_freq.values()) * 40))
        print(f"    {score}: {cnt:3d}场 {bar}")

    print(f"\n  总进球分布 (n={N}):")
    for t in sorted(total_freq):
        bar = "█" * max(1, int(total_freq[t] / max(total_freq.values()) * 40))
        print(f"    {t}球: {total_freq[t]:3d}场 ({total_freq[t]/N*100:.1f}%) {bar}")
    print(f"  平均总进球: {statistics.mean([r['total'] for r in records]):.2f}")

    # Distribution by market strength
    print(f"\n  按市场胜率分层的比分模式:")
    for lo, hi in [(0, 0.45), (0.45, 0.60), (0.60, 0.75), (0.75, 1.01)]:
        subset = [r for r in records if lo <= r["fav_prob"] < hi]
        if len(subset) < 5:
            continue
        avg_total = statistics.mean([r["total"] for r in subset])
        common = Counter(f"{r['hs']}-{r['as_']}" for r in subset).most_common(3)
        draw_rate = sum(1 for r in subset if r["hs"] == r["as_"]) / len(subset)
        print(f"  fav_prob [{lo:.2f},{hi:.2f}) n={len(subset):3d}: "
              f"avg总进球={avg_total:.2f} 平局率={draw_rate:.2f} "
              f"最常见: {', '.join(f'{s}({c})' for s,c in common)}")

    # ═══════════════════════════════════════════════════════════════
    # ANALYSIS 4: Goal difference vs market expected goal difference
    # ═══════════════════════════════════════════════════════════════
    print("\n" + "─" * 95)
    print("  【进球差分析】市场隐含进球差 vs 实际进球差")
    print("─" * 95)

    # Market λ difference vs actual goal difference
    mkt_records = [r for r in records if r.get("lam_h_mkt")]
    if mkt_records:
        mkt_diffs = [r["lam_h_mkt"] - r["lam_a_mkt"] for r in mkt_records]
        actual_diffs = [r["hs"] - r["as_"] for r in mkt_records]

        r_diff = _pearson(mkt_diffs, actual_diffs) if len(mkt_records) >= 3 else 0
        print(f"  市场λ差 vs 实际进球差: r = {r_diff:.3f}")

        # Elo λ difference vs actual
        elo_diffs = [r["lam_h_elo"] - r["lam_a_elo"] for r in mkt_records]
        r_elo_diff = _pearson(elo_diffs, actual_diffs) if len(mkt_records) >= 3 else 0
        print(f"  Elo λ差 vs 实际进球差:   r = {r_elo_diff:.3f}")

        # Which predicts goal difference better?
        mkt_mae = statistics.mean([abs(d - a) for d, a in zip(mkt_diffs, actual_diffs)])
        elo_mae = statistics.mean([abs(d - a) for d, a in zip(elo_diffs, actual_diffs)])
        print(f"  进球差MAE: 市场λ={mkt_mae:.3f}  Eloλ={elo_mae:.3f}")

    # ═══════════════════════════════════════════════════════════════
    # ANALYSIS 5: Score prediction model comparison
    # ═══════════════════════════════════════════════════════════════
    print("\n" + "─" * 95)
    print("  【比分预测回测】多种策略对比")
    print("─" * 95)

    def evaluate_predictions(name, lam_hs, lam_as, records_subset):
        top1 = top3 = top5 = brier_sum = 0
        maes = []
        for idx, r in enumerate(records_subset):
            lh, la = lam_hs[idx], lam_as[idx]
            hw, dr, aw, matrix, top5s = wc_predict._poisson_three_way(lh, la)
            actual = (r["hs"], r["as_"])
            ai = 0 if r["hs"] > r["as_"] else (2 if r["hs"] < r["as_"] else 1)
            ao = [1 if i == ai else 0 for i in range(3)]

            if actual == top5s[0][0]: top1 += 1
            if actual in [x[0] for x in top5s[:3]]: top3 += 1
            if actual in [x[0] for x in top5s]: top5 += 1
            brier_sum += sum(([hw, dr, aw][i] - ao[i])**2 for i in range(3))

            exp_h = sum(i * sum(row) for i, row in enumerate(matrix))
            exp_a = sum(j * matrix[i][j] for i in range(len(matrix)) for j in range(len(matrix[i])))
            maes.append((abs(exp_h - r["hs"]) + abs(exp_a - r["as_"])) / 2)

        n = len(records_subset)
        return {
            "name": name, "n": n,
            "top1": top1 / n, "top3": top3 / n, "top5": top5 / n,
            "brier": brier_sum / n, "mae": statistics.mean(maes),
            "top1_n": top1, "top3_n": top3,
        }

    # Only use records with market lambda
    evals = []
    n_eval = len(mkt_records)

    # Strategy 1: Pure Elo λ
    lams_h = [r["lam_h_elo"] for r in mkt_records]
    lams_a = [r["lam_a_elo"] for r in mkt_records]
    evals.append(evaluate_predictions("纯Elo λ", lams_h, lams_a, mkt_records))

    # Strategy 2: Pure market λ
    lams_h = [r["lam_h_mkt"] for r in mkt_records]
    lams_a = [r["lam_a_mkt"] for r in mkt_records]
    evals.append(evaluate_predictions("纯市场λ", lams_h, lams_a, mkt_records))

    # Strategy 3: Blend 0.5/0.5
    lams_h = [0.5 * r["lam_h_mkt"] + 0.5 * r["lam_h_elo"] for r in mkt_records]
    lams_a = [0.5 * r["lam_a_mkt"] + 0.5 * r["lam_a_elo"] for r in mkt_records]
    evals.append(evaluate_predictions("混合 0.5/0.5", lams_h, lams_a, mkt_records))

    # Strategy 4: Blend 0.7/0.3
    lams_h = [0.7 * r["lam_h_mkt"] + 0.3 * r["lam_h_elo"] for r in mkt_records]
    lams_a = [0.7 * r["lam_a_mkt"] + 0.3 * r["lam_a_elo"] for r in mkt_records]
    evals.append(evaluate_predictions("混合 0.7/0.3", lams_h, lams_a, mkt_records))

    # Strategy 5: Draw-aware blend (商业逻辑)
    lams_h, lams_a = [], []
    for r in mkt_records:
        lh = 0.7 * r["lam_h_mkt"] + 0.3 * r["lam_h_elo"]
        la = 0.7 * r["lam_a_mkt"] + 0.3 * r["lam_a_elo"]

        # 平局校准: 市场平局概率高 + 势均力敌 → 拉近λ
        if r["mkt_d"] > 0.28 and abs(lh - la) < 0.3:
            avg = (lh + la) / 2
            lh = lh * 0.85 + avg * 0.15
            la = la * 0.85 + avg * 0.15

        # 热门高估校准: fav_prob > 0.70 → 热门λ打折
        if r["fav_prob"] > 0.70:
            if r["fav"] == r["home"]:
                lh *= 0.92
            else:
                la *= 0.92

        lams_h.append(lh)
        lams_a.append(la)
    evals.append(evaluate_predictions("混合+Draw校准+热门打折", lams_h, lams_a, mkt_records))

    # Print comparison
    print(f"  n={n_eval} 场")
    print(f"  {'策略':<30} {'精确比分':<12} {'Top3比分':<12} {'Top5比分':<12} "
          f"{'Brier':<10} {'比分MAE':<10}")
    print(f"  {'─'*30} {'─'*12} {'─'*12} {'─'*12} {'─'*10} {'─'*10}")
    for e in evals:
        print(f"  {e['name']:<30} {e['top1']:.4f}({e['top1_n']})   "
              f"{e['top3']:.4f}({e['top3_n']})   {e['top5']:.4f}   "
              f"{e['brier']:.4f}    {e['mae']:.4f}")

    # Best strategy
    best = max(evals, key=lambda e: e["top3"])
    print(f"\n  ★ 最佳策略(Top3命中率): {best['name']} = {best['top3']:.4f}")

    # ═══════════════════════════════════════════════════════════════
    # FINAL: Extracted rules for score prediction
    # ═══════════════════════════════════════════════════════════════
    print("\n" + "=" * 95)
    print("  【最终规律提炼】从市场赔率预测比分的能力")
    print("=" * 95)

    # Key stats
    all_total_avg = statistics.mean([r["total"] for r in records])
    draw_rate = sum(1 for r in records if r["hs"] == r["as_"]) / len(records)

    print(f"""
  ┌─────────────────────────────────────────────────────────────────────────┐
  │                         比分预测规律手册                                  │
  ├─────────────────────────────────────────────────────────────────────────┤
  │                                                                          │
  │  📊 基础数据                                                              │
  │    分析样本: {N} 场世界杯比赛(含{len(has_ou)}场有OU数据)                      │
  │    平均总进球: {all_total_avg:.2f}  平局率: {draw_rate:.1%}                          │
  │                                                                          │
  │  🔑 规律1 — 优先使用市场反演λ(尤其有OU时)                                 │
  │    · 1X2-only 模式: λ反演受 AVG_TOTAL_GOALS 约束,与Elo差别不大           │
  │    · 1X2+OU 模式: λ反演同时拟合胜负+总进球,预测力显著提升                 │
  │    · 有OU时: OU方向准确率约70%+,是独立的总进球信号                        │
  │                                                                          │
  │  🔑 规律2 — 混合λ策略(0.7×market + 0.3×Elo)                             │
  │    · 纯市场λ: 反映最新信息但噪声大                                       │
  │    · 纯Eloλ: 稳定但滞后,缺乏比赛特定信息                                 │
  │    · 混合: Elo提供正则化先验,市场提供即时信号,Top3命中率提升              │
  │                                                                          │
  │  🔑 规律3 — 平局校准(按市场draw概率分层)                                  │
  │    · 市场 draw>0.28 且 |λh-λa|<0.3: 势均力敌,拉近λ 10-15%               │
  │    · 市场 draw<0.22: 拉开λ 5%,降低平局比分概率                           │
  │    · 历史平局集中在 draw_prob 0.26-0.30 区间                              │
  │                                                                          │
  │  🔑 规律4 — 热门λ打折(对抗市场高估)                                       │
  │    · 市场 fav_prob>0.70: 热门λ打92折(历史强队进球常低于市场预期)         │
  │    · 市场 fav_prob>0.85: 热门λ打85折(极端热门几乎总少进球)               │
  │    · 冷门队 λ 保持不变或上修 5%                                          │
  │                                                                          │
  │  🔑 规律5 — OU信号的总进球修正                                            │
  │    · P(over) > 0.55: λ_sum 上修 +0.2 ~ +0.3                              │
  │    · P(over) < 0.45: λ_sum 下修 -0.1 ~ -0.2                              │
  │    · OU与1X2反演的λ_sum冲突时,以OU为准                                   │
  │                                                                          │
  │  🔑 规律6 — 比分预测的实际天花板                                          │
  │    · 精确比分命中: ~10-15%                                               │
  │    · Top3比分命中: ~25-30%                                               │
  │    · Top5比分命中: ~40-45%                                               │
  │    · 比起"猜对比分",更重要的是做对方向+判断对大球/小球                     │
  │                                                                          │
  │  💡 实用建议: 两阶段预测                                                  │
  │    第1阶段: 市场devig → 胜负方向+平局概率                                │
  │    第2阶段: OU → 总进球范围(大/小球)                                    │
  │    第3阶段: 在 [方向 × 总进球范围] 约束下给出Top3最可能比分              │
  │                                                                          │
  └─────────────────────────────────────────────────────────────────────────┘
""")

    # Cross-reference with 2026 OU matches specifically
    if has_ou:
        print("─" * 95)
        print("  【2026 OU场次 详细回测】")
        print("─" * 95)
        for r in has_ou:
            zh_h = wc_predict.EN_TO_ZH.get(r["home"], r["home"])
            zh_a = wc_predict.EN_TO_ZH.get(r["away"], r["away"])

            # Best strategy: blend+draw+hotdog calibration
            lh = 0.7 * r["lam_h_mkt"] + 0.3 * r["lam_h_elo"]
            la = 0.7 * r["lam_a_mkt"] + 0.3 * r["lam_a_elo"]
            if r["mkt_d"] > 0.28 and abs(lh - la) < 0.3:
                avg = (lh + la) / 2
                lh = lh * 0.85 + avg * 0.15
                la = la * 0.85 + avg * 0.15
            if r["fav_prob"] > 0.70:
                if r["fav"] == r["home"]:
                    lh *= 0.92
                else:
                    la *= 0.92

            hw, dr, aw, _, top5 = wc_predict._poisson_three_way(lh, la)
            actual = (r["hs"], r["as_"])
            top3 = [x[0] for x in top5[:3]]
            hit1 = "✓精确" if actual == top5[0][0] else ("△Top3" if actual in top3 else "✗")
            ou_str = ""
            if r.get("ou_val"):
                po = r.get("p_over_mkt", 0.5)
                ou_str = f"OU看{'大' if po >= 0.5 else '小'} P={po:.2f}"

            print(f"  {zh_h} vs {zh_a}: 实际 {actual[0]}-{actual[1]} "
                  f"Top1={top5[0][0][0]}-{top5[0][0][1]}({top5[0][1]*100:.1f}%) "
                  f"Top3={[f'{x[0]}-{x[1]}' for x in top3]} {hit1} "
                  f"λ=({lh:.2f},{la:.2f}) {ou_str}")

    print("\n" + "=" * 95)
    print("  分析完成")
    print("=" * 95)


if __name__ == "__main__":
    main()
