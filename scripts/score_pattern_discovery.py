"""score_pattern_discovery.py — 从市场赔率+实力预期进球+真实比分中提炼预测规律

输入:
  - wc2026_live.csv: 30 场真实比分
  - ou_wc.csv: 市场 1X2 + 大小球赔率
  - odds_wc.csv: 市场 1X2 赔率
  - results.csv: 历史比赛(算 Elo)

分析维度:
  1. 市场 devig 胜率 → 实际胜率校准曲线
  2. OU 隐含总进球 vs 实际总进球
  3. 市场反演 λ(预期进球) vs 实际比分残差
  4. Elo λ vs 市场 λ 的差值 → 方向性偏误
  5. 泊松比分概率 vs 实际比分命中率
  6. 提炼: 如何组合市场信号 + Elo 信号提升比分预测精度
"""

import csv
import math
import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
sys.path.insert(0, ROOT)

import compute_odds
import compute_elo
import wc_predict


# ═══════════════════════════════════════════════════════════════════
# 1. 数据加载
# ═══════════════════════════════════════════════════════════════════

def load_live_results():
    """wc2026_live.csv → [(home_en, away_en, hs, as_, date)]"""
    out = []
    path = os.path.join(ROOT, "wc2026_live.csv")
    with open(path, newline="", encoding="utf-8") as f:
        for r in csv.DictReader(f):
            if r.get("home_score") and r.get("away_score"):
                out.append((
                    r["home_team"], r["away_team"],
                    int(r["home_score"]), int(r["away_score"]),
                    r["date"]
                ))
    return out


def market_expected_goals(oh, od, oa, ou=None, ou_line=2.5):
    """从市场赔率反演预期进球 (λh, λa).

    模式 B(有OU): 同时拟合 1X2 + OU → 最准
    模式 A(仅1X2): 用 AVG_TOTAL_GOALS 先验约束
    """
    return compute_odds.lambda_from_market(oh, od, oa, ou=ou, ou_line=ou_line)


def elo_expected_goals(elo, home, away):
    """Elo → (λh, λa)"""
    eh = elo.get(home, 1500.0)
    ea = elo.get(away, 1500.0)
    return wc_predict.elo_to_lambdas(eh, ea, home_adv=0.0)


def devig_1x2(oh, od, oa):
    """市场赔率 → devig 概率"""
    return compute_odds.devig_power(oh, od, oa)


def devig_ou_price(over, under):
    """OU 赔率 → P(over)"""
    return compute_odds.devig_ou(over, under)


# ═══════════════════════════════════════════════════════════════════
# 2. 核心分析
# ═══════════════════════════════════════════════════════════════════

def analyze():
    matches = load_live_results()
    ou_map = compute_odds.load_ou()
    odds_map = compute_odds.load_odds()
    elo, _, _ = compute_elo.build_elo(
        os.path.join(ROOT, "results.csv"), cutoff_date="2026-06-10"
    )

    print("=" * 90)
    print("  市场赔率+实力预期进球 → 比分预测规律发现")
    print("=" * 90)
    print(f"  真实比分样本: {len(matches)} 场")

    # 为每场匹配市场数据
    records = []
    for home, away, hs, as_, date in matches:
        rec = {
            "home": home, "away": away, "hs": hs, "as_": as_,
            "total": hs + as_, "diff": hs - as_,
            "date": date,
        }

        # 查 OU 数据(模式 B: 1X2+OU)
        ou_entry = compute_odds.lookup_ou(ou_map, home, away, 2026)
        # 查 odds 数据(模式 A: 仅1X2)
        odds_entry = compute_odds.lookup_odds(odds_map, home, away, 2026)

        # 优先 OU(含1X2+OU), 其次 odds(仅1X2)
        if ou_entry:
            oh, od, oa = ou_entry["1x2"]
            ou_val = ou_entry.get("ou")
            ou_line = ou_entry.get("ou_line", 2.5)
            rec["source"] = "OU"
        elif odds_entry:
            oh, od, oa = odds_entry
            ou_val = None
            ou_line = 2.5
            rec["source"] = "1X2"
        else:
            continue  # 无市场数据,跳过

        rec["oh"], rec["od"], rec["oa"] = oh, od, oa
        rec["ou_val"] = ou_val
        rec["ou_line"] = ou_line

        # devig 市场概率
        mh, md, ma = devig_1x2(oh, od, oa)
        rec["mkt_h"], rec["mkt_d"], rec["mkt_a"] = mh, md, ma

        # 市场隐含的 favor/dog
        if mh > ma:
            rec["fav"] = home
            rec["dog"] = away
            rec["fav_prob"] = mh
            rec["dog_prob"] = ma
        else:
            rec["fav"] = away
            rec["dog"] = home
            rec["fav_prob"] = ma
            rec["dog_prob"] = mh

        # OU 隐含总进球
        if ou_val:
            po, pu = devig_ou_price(ou_val[0], ou_val[1])
            rec["p_over_mkt"] = po
            rec["p_under_mkt"] = pu

        # 市场反演 λ(预期进球)
        lam_mkt = market_expected_goals(oh, od, oa, ou=ou_val, ou_line=ou_line)
        if lam_mkt:
            rec["lam_h_mkt"], rec["lam_a_mkt"] = lam_mkt
            rec["total_mkt"] = lam_mkt[0] + lam_mkt[1]

        # Elo λ(实力预期进球)
        lam_elo = elo_expected_goals(elo, home, away)
        rec["lam_h_elo"], rec["lam_a_elo"] = lam_elo
        rec["total_elo"] = lam_elo[0] + lam_elo[1]

        # Elo 评分
        rec["elo_h"] = elo.get(home, 1500)
        rec["elo_a"] = elo.get(away, 1500)

        records.append(rec)

    print(f"  有市场数据的场次: {len(records)}")

    if len(records) == 0:
        print("  无可用数据,退出")
        return

    # ═══════════════════════════════════════════════════════════════
    # 分析 1: 市场 devig 胜率 vs 实际胜率
    # ═══════════════════════════════════════════════════════════════
    print("\n" + "─" * 90)
    print("【分析1】市场胜率 vs 实际结果 校准曲线")
    print("─" * 90)

    # 按市场胜率分 bin
    bins = [(0, 0.30), (0.30, 0.40), (0.40, 0.50), (0.50, 0.60),
            (0.60, 0.70), (0.70, 1.01)]

    for lo, hi in bins:
        subset = [r for r in records if lo <= r["fav_prob"] < hi]
        if not subset:
            continue
        n = len(subset)
        # fav 赢 = 热门队实际赢了
        fav_wins = sum(
            1 for r in subset
            if (r["fav"] == r["home"] and r["hs"] > r["as_"]) or
               (r["fav"] == r["away"] and r["as_"] > r["hs"])
        )
        draw = sum(1 for r in subset if r["hs"] == r["as_"])
        dog_wins = n - fav_wins - draw
        avg_prob = sum(r["fav_prob"] for r in subset) / n
        print(f"  fav_prob [{lo:.2f}-{hi:.2f}) n={n:2d}  "
              f"avg_mkt={avg_prob:.3f}  "
              f"实际: fav={fav_wins/n:.3f} draw={draw/n:.3f} dog={dog_wins/n:.3f}")

    # ═══════════════════════════════════════════════════════════════
    # 分析 2: OU 隐含总进球 vs 实际总进球
    # ═══════════════════════════════════════════════════════════════
    print("\n" + "─" * 90)
    print("【分析2】OU 隐含总进球 vs 实际总进球")
    print("─" * 90)

    ou_records = [r for r in records if r.get("ou_val")]
    print(f"  有 OU 数据: {len(ou_records)} 场")

    if ou_records:
        ou_over_hit = 0
        total_errors = []
        for r in ou_records:
            actual_total = r["hs"] + r["as_"]
            ou_line = r["ou_line"]
            is_over = actual_total > ou_line
            is_push = actual_total == ou_line
            p_over = r.get("p_over_mkt", 0.5)

            if not is_push:
                ou_over_hit += 1 if (is_over and p_over >= 0.5) or (not is_over and p_over < 0.5) else 0

            if r.get("total_mkt"):
                total_errors.append(actual_total - r["total_mkt"])

        print(f"  OU 方向命中率: {ou_over_hit}/{len(ou_records)} = {ou_over_hit/len(ou_records):.3f}")
        if total_errors:
            import statistics
            print(f"  实际总进球 - 市场隐含总进球: mean={statistics.mean(total_errors):+.3f} "
                  f"std={statistics.stdev(total_errors):.3f}")
            print(f"  (正值=实际进球多于市场预期, 负值=实际进球少于市场预期)")

        # 详细列出
        print(f"\n  {'比赛':<35} {'实际':<6} {'市场λ总和':<10} {'OU线':<6} {'P(over)':<8} {'实际over?':<10}")
        for r in sorted(ou_records, key=lambda x: x["total"], reverse=True):
            zh_h = wc_predict.EN_TO_ZH.get(r["home"], r["home"])
            zh_a = wc_predict.EN_TO_ZH.get(r["away"], r["away"])
            t_mkt = f"{r.get('total_mkt', 'N/A'):.2f}" if r.get("total_mkt") else "N/A"
            p_over = r.get("p_over_mkt", 0)
            actual_over = "OVER" if r["total"] > r["ou_line"] else ("PUSH" if r["total"] == r["ou_line"] else "UNDER")
            print(f"  {zh_h} vs {zh_a:<35} {r['hs']}-{r['as_']:<6} {t_mkt:<10} "
                  f"{r['ou_line']:<6} {p_over:.3f}   {actual_over}")

    # ═══════════════════════════════════════════════════════════════
    # 分析 3: 市场 λ vs Elo λ 差值分析
    # ═══════════════════════════════════════════════════════════════
    print("\n" + "─" * 90)
    print("【分析3】市场 λ vs Elo λ -> 市场'智慧'在哪里")
    print("─" * 90)

    mkt_records = [r for r in records if r.get("total_mkt")]
    print(f"  有市场 λ 的场次: {len(mkt_records)}")

    if mkt_records:
        diffs_h = []
        diffs_a = []
        diffs_total = []
        for r in mkt_records:
            dh = r["lam_h_mkt"] - r["lam_h_elo"]
            da = r["lam_a_mkt"] - r["lam_a_elo"]
            dt = r["total_mkt"] - r["total_elo"]
            diffs_h.append(dh)
            diffs_a.append(da)
            diffs_total.append(dt)

        import statistics
        print(f"  λh 差值(市场-Elo): mean={statistics.mean(diffs_h):+.3f} std={statistics.stdev(diffs_h):.3f}")
        print(f"  λa 差值(市场-Elo): mean={statistics.mean(diffs_a):+.3f} std={statistics.stdev(diffs_a):.3f}")
        print(f"  总进球差值(市场-Elo): mean={statistics.mean(diffs_total):+.3f} std={statistics.stdev(diffs_total):.3f}")

        # 市场更看好谁? (市场 λ 差值 vs Elo λ 差值)
        print(f"\n  市场 vs Elo 对热门队的看好程度差异:")
        fav_bias = []
        for r in mkt_records:
            fav_is_home = r["fav"] == r["home"]
            mkt_diff = r["lam_h_mkt"] - r["lam_a_mkt"] if fav_is_home else r["lam_a_mkt"] - r["lam_h_mkt"]
            elo_diff = r["lam_h_elo"] - r["lam_a_elo"] if fav_is_home else r["lam_a_elo"] - r["lam_h_elo"]
            fav_bias.append(mkt_diff - elo_diff)
        print(f"  热门队 λ差 偏差(市场-Elo): mean={statistics.mean(fav_bias):+.3f} "
              f"(+值=市场比Elo更看好热门)")

    # ═══════════════════════════════════════════════════════════════
    # 分析 4: 实际比分残差 vs 预期
    # ═══════════════════════════════════════════════════════════════
    print("\n" + "─" * 90)
    print("【分析4】实际比分 vs 预期(泊松)残差模式")
    print("─" * 90)

    if mkt_records:
        home_residuals = []
        away_residuals = []
        for r in mkt_records:
            home_residuals.append(r["hs"] - r["lam_h_mkt"])
            away_residuals.append(r["as_"] - r["lam_a_mkt"])

        print(f"  主队进球残差(实际-市场λ): mean={statistics.mean(home_residuals):+.3f} "
              f"std={statistics.stdev(home_residuals):.3f}")
        print(f"  客队进球残差(实际-市场λ): mean={statistics.mean(away_residuals):+.3f} "
              f"std={statistics.stdev(away_residuals):.3f}")

        # 残差分布
        print(f"\n  主队进球残差分布:")
        for bound, label in [(-2, "远低于预期"), (-1, "低于预期"), (0, "略低于预期"),
                              (1, "略高于预期"), (2, "高于预期"), (99, "远超预期")]:
            lo = -99 if bound == -2 else bound - 1
            if bound == -2:
                lo = -99
            else:
                lo = bound - 1 if bound > 0 else bound
            count = sum(1 for x in home_residuals if lo <= x < bound)
            print(f"    [{lo:>3}, {bound:>3}): {count} 场 ({count/len(home_residuals)*100:.0f}%)")

    # ═══════════════════════════════════════════════════════════════
    # 分析 5: 比分预测——泊松 vs 实际
    # ═══════════════════════════════════════════════════════════════
    print("\n" + "─" * 90)
    print("【分析5】泊松比分概率 vs 实际比分命中率")
    print("─" * 90)

    # 用市场 λ 做泊松预测
    poisson_hits_mkt = 0
    poisson_hits_elo = 0
    poisson_top3_mkt = 0
    poisson_top3_elo = 0
    brier_mkt = 0
    brier_elo = 0

    for r in records:
        actual = (r["hs"], r["as_"])
        actual_idx = 0 if r["hs"] > r["as_"] else (2 if r["hs"] < r["as_"] else 1)
        actual_onehot = [1 if i == actual_idx else 0 for i in range(3)]

        # 市场 λ 预测
        if r.get("lam_h_mkt"):
            hw, dr, aw, matrix, top5 = wc_predict._poisson_three_way(r["lam_h_mkt"], r["lam_a_mkt"])
            top3 = [x[0] for x in top5[:3]]
            if actual in top3:
                poisson_top3_mkt += 1
            if actual == top5[0][0]:
                poisson_hits_mkt += 1
            probs_mkt = [hw, dr, aw]
            brier_mkt += sum((probs_mkt[i] - actual_onehot[i])**2 for i in range(3))

        # Elo λ 预测
        hw_e, dr_e, aw_e, matrix_e, top5_e = wc_predict._poisson_three_way(r["lam_h_elo"], r["lam_a_elo"])
        top3_e = [x[0] for x in top5_e[:3]]
        if actual in top3_e:
            poisson_top3_elo += 1
        if actual == top5_e[0][0]:
            poisson_hits_elo += 1
        probs_elo = [hw_e, dr_e, aw_e]
        brier_elo += sum((probs_elo[i] - actual_onehot[i])**2 for i in range(3))

    n_mkt = len([r for r in records if r.get("lam_h_mkt")])
    n_all = len(records)

    print(f"  {'指标':<20} {'市场λ (n=' + str(n_mkt) + ')':<20} {'Elo λ (n=' + str(n_all) + ')':<20}")
    print(f"  {'精确比分命中':<20} {poisson_hits_mkt/n_mkt:.3f} ({poisson_hits_mkt}/{n_mkt}){'':<8} "
          f"{poisson_hits_elo/n_all:.3f} ({poisson_hits_elo}/{n_all})")
    print(f"  {'Top3比分命中':<20} {poisson_top3_mkt/n_mkt:.3f} ({poisson_top3_mkt}/{n_mkt}){'':<8} "
          f"{poisson_top3_elo/n_all:.3f} ({poisson_top3_elo}/{n_all})")
    print(f"  {'Brier(三态)':<20} {brier_mkt/n_mkt:.4f}{'':<8} {brier_elo/n_all:.4f}")

    # ═══════════════════════════════════════════════════════════════
    # 分析 6: 发现规律——比分预测关键因子
    # ═══════════════════════════════════════════════════════════════
    print("\n" + "=" * 90)
    print("【规律提炼】比分预测的关键可操作规律")
    print("=" * 90)

    # 6a: 市场 λ 总和 vs 实际总进球的相关性
    if mkt_records:
        import statistics
        totals_mkt = [r["total_mkt"] for r in mkt_records]
        totals_actual = [r["total"] for r in mkt_records]
        totals_elo = [r["total_elo"] for r in mkt_records]

        # Pearson r
        def pearson_r(xs, ys):
            n = len(xs)
            mx, my = statistics.mean(xs), statistics.mean(ys)
            sx = statistics.stdev(xs)
            sy = statistics.stdev(ys)
            return sum((x - mx) * (y - my) for x, y in zip(xs, ys)) / (n * sx * sy) if sx > 0 and sy > 0 else 0

        r_mkt = pearson_r(totals_mkt, totals_actual)
        r_elo = pearson_r(totals_elo, totals_actual)
        print(f"\n  【规律1】预期总进球 vs 实际总进球 相关性:")
        print(f"    市场 λ 总和 vs 实际总进球: r = {r_mkt:.4f}")
        print(f"    Elo λ 总和 vs 实际总进球:   r = {r_elo:.4f}")

        # 哪个更准?
        mkt_err = [abs(tm - ta) for tm, ta in zip(totals_mkt, totals_actual)]
        elo_err = [abs(te - ta) for te, ta in zip(totals_elo, totals_actual)]
        print(f"    市场 λ MAE: {statistics.mean(mkt_err):.3f}")
        print(f"    Elo λ MAE:   {statistics.mean(elo_err):.3f}")

    # 6b: 市场概率 → 结果偏差模式
    print(f"\n  【规律2】市场 devig 概率偏差模式:")
    # 分胜负 vs 平局
    fav_wins_all = 0
    draws_all = 0
    dogs_win_all = 0
    for r in records:
        if r["hs"] > r["as_"]:
            actual_winner = r["home"]
        elif r["hs"] < r["as_"]:
            actual_winner = r["away"]
        else:
            actual_winner = "draw"

        if actual_winner == "draw":
            draws_all += 1
        elif actual_winner == r["fav"]:
            fav_wins_all += 1
        else:
            dogs_win_all += 1

    n = len(records)
    print(f"    全部 {n} 场: 热门胜 {fav_wins_all/n:.3f}  平 {draws_all/n:.3f}  冷门胜 {dogs_win_all/n:.3f}")

    # 按强弱差距分层
    strong_fav = [r for r in records if r["fav_prob"] >= 0.60]
    weak_fav = [r for r in records if r["fav_prob"] < 0.60]

    for label, subset in [("强热门(≥60%)", strong_fav), ("弱热门(<60%)", weak_fav)]:
        if not subset:
            continue
        n_s = len(subset)
        fw = sum(1 for r in subset if (r["fav"] == r["home"] and r["hs"] > r["as_"]) or
                 (r["fav"] == r["away"] and r["as_"] > r["hs"]))
        dw = sum(1 for r in subset if r["hs"] == r["as_"])
        dw_expected = sum(r["mkt_d"] for r in subset) / n_s
        print(f"    {label} n={n_s}: 热门胜={fw/n_s:.3f}(市场预期{(1-dw_expected)/2+0.5:.3f}) "
              f"平={dw/n_s:.3f}(市场预期{dw_expected:.3f})")

    # 6c: 比分分布规律
    print(f"\n  【规律3】实际比分分布:")
    score_freq = {}
    for r in records:
        key = f"{r['hs']}-{r['as_']}"
        score_freq[key] = score_freq.get(key, 0) + 1
    for score, count in sorted(score_freq.items(), key=lambda x: -x[1]):
        bar = "█" * count
        print(f"    {score}: {count}场 {bar}")

    # 6d: 总进球分布
    print(f"\n  【规律4】实际总进球分布:")
    total_freq = {}
    for r in records:
        t = r["total"]
        total_freq[t] = total_freq.get(t, 0) + 1
    for t in sorted(total_freq):
        bar = "█" * total_freq[t]
        print(f"    {t}球: {total_freq[t]}场 {bar}")
    print(f"    平均总进球: {sum(r['total'] for r in records)/len(records):.2f}")

    # 6e: OU 信号的有效性
    if ou_records:
        print(f"\n  【规律5】OU 信号与 Elo 信号的互补性:")
        # 按 OU 市场看好大球的比赛
        over_favored = [r for r in ou_records if r.get("p_over_mkt", 0) >= 0.5]
        under_favored = [r for r in ou_records if r.get("p_over_mkt", 0) < 0.5]

        for label, subset in [("市场看大球", over_favored), ("市场看小球", under_favored)]:
            if not subset:
                continue
            avg_total = sum(r["total"] for r in subset) / len(subset)
            avg_mkt = sum(r.get("total_mkt", 0) for r in subset) / len(subset)
            avg_elo = sum(r["total_elo"] for r in subset) / len(subset)
            over_actual = sum(1 for r in subset if r["total"] > r["ou_line"]) / len(subset)
            print(f"    {label} n={len(subset)}: 实际平均总进球={avg_total:.2f} "
                  f"市场预期={avg_mkt:.2f} Elo预期={avg_elo:.2f} "
                  f"实际大球率={over_actual:.3f}")

    # ═══════════════════════════════════════════════════════════════
    # 7: 构建混合比分预测模型
    # ═══════════════════════════════════════════════════════════════
    print("\n" + "=" * 90)
    print("【规律应用】混合比分预测模型")
    print("=" * 90)

    print("""
    模型设计:
    ┌─────────────────────────────────────────────────────────────┐
    │ 输入层                                                        │
    │  市场 1X2 赔率 → devig → [P(h), P(d), P(a)]                   │
    │  市场 OU 赔率 → devig → P(over) + 隐含总进球                   │
    │  Elo 评分    → Elo 预期进球 λh, λa                            │
    │                                                               │
    │ 融合层 (关键规律):                                             │
    │  1. 若 OU 数据存在: λ = market_inverted(1X2+OU)  ← 最准        │
    │  2. 若仅 1X2: λ = market_inverted(1X2) × 校准因子              │
    │  3. 混合 λ = 0.7×λ_market + 0.3×λ_elo  ← 降低方差             │
    │  4. 比分抽样 = Poisson(λh, λa) + 平局校准                      │
    │                                                               │
    │ 平局校准:                                                      │
    │  若 P(market draw) > 0.28 且 |λh-λa| < 0.3:                   │
    │    比分向对角线 (x,x) 加权 1.15x ← 对抗泊松对平局的低估        │
    │                                                               │
    │ 输出: Top5 最可能比分 + 概率                                   │
    └─────────────────────────────────────────────────────────────┘
    """)

    # 实际回测:混合模型 vs 纯 Elo
    print("  混合模型回测(30场):")

    blend_top1 = 0
    blend_top3 = 0
    blend_brier = 0
    blend_score_mae = 0
    blend_total_mae = 0

    for r in records:
        # 混合 λ
        if r.get("lam_h_mkt"):
            lam_h_blend = 0.70 * r["lam_h_mkt"] + 0.30 * r["lam_h_elo"]
            lam_a_blend = 0.70 * r["lam_a_mkt"] + 0.30 * r["lam_a_elo"]
        else:
            lam_h_blend = r["lam_h_elo"]
            lam_a_blend = r["lam_a_elo"]

        # 平局校准
        if r.get("mkt_d", 0) > 0.28 and abs(lam_h_blend - lam_a_blend) < 0.3:
            # 微调:增加平局比分概率(通过略微拉近 λ)
            avg_lam = (lam_h_blend + lam_a_blend) / 2
            lam_h_blend = lam_h_blend * 0.9 + avg_lam * 0.1
            lam_a_blend = lam_a_blend * 0.9 + avg_lam * 0.1

        hw, dr, aw, matrix, top5 = wc_predict._poisson_three_way(lam_h_blend, lam_a_blend)
        actual = (r["hs"], r["as_"])
        actual_idx = 0 if r["hs"] > r["as_"] else (2 if r["hs"] < r["as_"] else 1)
        actual_onehot = [1 if i == actual_idx else 0 for i in range(3)]

        top3 = [x[0] for x in top5[:3]]
        if actual in top3:
            blend_top3 += 1
        if actual == top5[0][0]:
            blend_top1 += 1

        probs = [hw, dr, aw]
        blend_brier += sum((probs[i] - actual_onehot[i])**2 for i in range(3))

        # 预期比分 MAE
        exp_home = sum(i * sum(row) for i, row in enumerate(matrix))
        exp_away = sum(j * matrix[i][j] for i in range(len(matrix)) for j in range(len(matrix[i])))
        blend_score_mae += (abs(exp_home - r["hs"]) + abs(exp_away - r["as_"]))
        blend_total_mae += abs(lam_h_blend + lam_a_blend - r["total"])

    n = len(records)
    print(f"    精确比分命中: {blend_top1/n:.3f} ({blend_top1}/{n})")
    print(f"    Top3比分命中: {blend_top3/n:.3f} ({blend_top3}/{n})")
    print(f"    Brier(三态):   {blend_brier/n:.4f}")
    print(f"    比分MAE(预期):  {blend_score_mae/n:.3f}")
    print(f"    总进球MAE:      {blend_total_mae/n:.3f}")

    # 对比纯 Elo
    print(f"\n  对比(同{n}场):")
    print(f"    {'指标':<20} {'混合模型':<15} {'纯Elo':<15} {'纯市场':<15}")
    print(f"    {'精确比分':<20} {blend_top1/n:.3f}           {poisson_hits_elo/n_all:.3f}           "
          f"{poisson_hits_mkt/n_mkt:.3f}")
    print(f"    {'Top3比分':<20} {blend_top3/n:.3f}           {poisson_top3_elo/n_all:.3f}           "
          f"{poisson_top3_mkt/n_mkt:.3f}")

    # ═══════════════════════════════════════════════════════════════
    # 8: 详细场次分析
    # ═══════════════════════════════════════════════════════════════
    print("\n" + "=" * 90)
    print("【附录】逐场分析——混合模型预测分解")
    print("=" * 90)

    for r in sorted(records, key=lambda x: x["date"]):
        zh_h = wc_predict.EN_TO_ZH.get(r["home"], r["home"])
        zh_a = wc_predict.EN_TO_ZH.get(r["away"], r["away"])

        # 计算混合预测
        if r.get("lam_h_mkt"):
            lh_b = 0.70 * r["lam_h_mkt"] + 0.30 * r["lam_h_elo"]
            la_b = 0.70 * r["lam_a_mkt"] + 0.30 * r["lam_a_elo"]
        else:
            lh_b = r["lam_h_elo"]
            la_b = r["lam_a_elo"]

        if r.get("mkt_d", 0) > 0.28 and abs(lh_b - la_b) < 0.3:
            avg_lam = (lh_b + la_b) / 2
            lh_b = lh_b * 0.9 + avg_lam * 0.1
            la_b = la_b * 0.9 + avg_lam * 0.1

        hw, dr, aw, matrix, top5 = wc_predict._poisson_three_way(lh_b, la_b)

        actual = (r["hs"], r["as_"])
        top1 = top5[0][0]
        top1_prob = top5[0][1]

        hit = "✓" if actual == top1 else ("△" if actual in [x[0] for x in top5[:3]] else "✗")
        ou_info = ""
        if r.get("ou_val"):
            po = r.get("p_over_mkt", 0)
            ou_info = f"OU{po:.2f}"

        print(f"  {r['date']} {zh_h} vs {zh_a}: 实际{actual[0]}-{actual[1]} "
              f"预测Top1={top1[0]}-{top1[1]}({top1_prob*100:.1f}%) {hit}"
              f"  λ=({lh_b:.2f},{la_b:.2f}) {ou_info}")

    print("\n" + "=" * 90)
    print("  分析完成")
    print("=" * 90)


if __name__ == "__main__":
    analyze()
