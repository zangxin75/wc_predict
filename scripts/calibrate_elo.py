"""calibrate_elo.py — 用历史世界杯市场赔率校准 Elo 模型的方向性偏误。

问题:Elo 固定 AVG_TOTAL_GOALS=2.62 → 系统性高估平局/冷门。
方案:
  1. 参数网格搜索:对 (ELO_SCALE, AVG_TOTAL_GOALS, RHO) 最小化 vs 市场 Brier
  2. 逐场 total_goals 回归:Elo 特征 → 最优 total_goals,替代固定常数
  3. 输出校准:Platt/Isotonic 映射 Elo probs → 市场 probs

输出:最佳参数组合 + 回测对比表 + 推荐方案。
"""
import csv
import math
import os
import sys

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8")

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
sys.path.insert(0, ROOT)

import compute_odds
import compute_elo
import wc_predict

RESULTS = os.path.join(ROOT, "results.csv")
ODDS = os.path.join(ROOT, "odds_wc.csv")

# 每届世界杯开幕日(cutoff:不含正赛数据,模拟赛前预测)
WC_OPENERS = {
    "1998": "1998-06-10",
    "2002": "2002-05-31",
    "2006": "2006-06-09",
    "2010": "2010-06-11",
    "2014": "2014-06-12",
    "2018": "2018-06-14",
    "2022": "2022-11-20",
}


def load_historical_matches():
    """加载 odds_wc.csv 中非 2026 的历史比赛(有市场赔率的地面真值)。"""
    matches = []
    if not os.path.exists(ODDS):
        return matches
    with open(ODDS, newline="", encoding="utf-8") as f:
        for r in csv.DictReader(f):
            y = str(r["year"])
            if y == "2026":
                continue
            try:
                oh, od, oa = float(r["odds_h"]), float(r["odds_d"]), float(r["odds_a"])
                if oh <= 1.0 or od <= 1.0 or oa <= 1.0:
                    continue
            except (ValueError, KeyError):
                continue
            matches.append({
                "year": y,
                "home": compute_odds.normalize_team(r["home"]),
                "away": compute_odds.normalize_team(r["away"]),
                "odds": (oh, od, oa),
            })
    return matches


def brier_score(probs, outcome_idx):
    """Brier score: (1/n)·Σ(p_i - o_i)²  where o_i=1 for actual, 0 otherwise.
    这里对单个预测返回 (p_h-o_h)²+(p_d-o_d)²+(p_a-o_a)²。"""
    o = [0.0, 0.0, 0.0]
    o[outcome_idx] = 1.0
    return sum((p - oi) ** 2 for p, oi in zip(probs, o))


def kl_divergence(p, q):
    """KL(p||q) = Σ p_i·log(p_i/q_i)。p=市场(真值), q=模型。"""
    s = 0.0
    for pi, qi in zip(p, q):
        if pi > 0 and qi > 0:
            s += pi * math.log(pi / qi)
    return s


def evaluate_params(matches, elo_cache, avg_total, elo_scale, rho,
                    draw_b=None, draw_scale=None):
    """给定参数,对所有历史比赛算 Elo 预测 vs 市场 devig,返回汇总指标。"""
    # 临时覆盖 Config
    orig_total = wc_predict.Config.AVG_TOTAL_GOALS
    orig_scale = wc_predict.Config.ELO_SCALE
    orig_rho = wc_predict.Config.RHO
    orig_db = wc_predict.Config.DRAW_BOOST_B
    orig_ds = wc_predict.Config.DRAW_BOOST_SCALE

    wc_predict.Config.AVG_TOTAL_GOALS = avg_total
    wc_predict.Config.ELO_SCALE = elo_scale
    wc_predict.Config.RHO = rho
    if draw_b is not None:
        wc_predict.Config.DRAW_BOOST_B = draw_b
    if draw_scale is not None:
        wc_predict.Config.DRAW_BOOST_SCALE = draw_scale

    try:
        total_brier = 0.0
        total_kl = 0.0
        n = 0
        draw_elo_list = []
        draw_market_list = []
        for m in matches:
            y = m["year"]
            elo = elo_cache.get(y, {})
            eh = elo.get(m["home"])
            ea = elo.get(m["away"])
            if eh is None or ea is None:
                continue
            hw, dr, aw, _, _ = wc_predict.predict_from_elo(eh, ea, home_adv_elo=0)
            mh, md, ma = compute_odds.devig_power(*m["odds"])
            total_brier += ((hw - mh) ** 2 + (dr - md) ** 2 + (aw - ma) ** 2)
            if md > 0 and dr > 0:
                total_kl += kl_divergence([mh, md, ma], [hw, dr, aw])
            draw_elo_list.append(dr)
            draw_market_list.append(md)
            n += 1
    finally:
        wc_predict.Config.AVG_TOTAL_GOALS = orig_total
        wc_predict.Config.ELO_SCALE = orig_scale
        wc_predict.Config.RHO = orig_rho
        wc_predict.Config.DRAW_BOOST_B = orig_db
        wc_predict.Config.DRAW_BOOST_SCALE = orig_ds

    if n == 0:
        return None
    avg_draw_elo = sum(draw_elo_list) / n
    avg_draw_market = sum(draw_market_list) / n
    return {
        "n": n, "avg_brier": total_brier / n, "avg_kl": total_kl / n,
        "avg_draw_elo": avg_draw_elo, "avg_draw_market": avg_draw_market,
        "draw_bias": avg_draw_elo - avg_draw_market,
    }


def grid_search(matches, elo_cache):
    """二维网格搜索 (AVG_TOTAL_GOALS × ELO_SCALE),固定 RHO=当前值。"""
    print("\n" + "=" * 70)
    print("  网格搜索: AVG_TOTAL_GOALS × ELO_SCALE")
    print("=" * 70)
    print(f"  历史比赛: {len(matches)} 场 (含市场赔率)")
    print(f"  当前默认: total={wc_predict.Config.AVG_TOTAL_GOALS}, "
          f"scale={wc_predict.Config.ELO_SCALE}, rho={wc_predict.Config.RHO}")

    # 先跑 baseline
    base = evaluate_params(matches, elo_cache,
                          wc_predict.Config.AVG_TOTAL_GOALS,
                          wc_predict.Config.ELO_SCALE,
                          wc_predict.Config.RHO)
    if base:
        print(f"\n  Baseline: Brier={base['avg_brier']:.4f}  KL={base['avg_kl']:.4f}  "
              f"draw_bias={base['draw_bias']:+.3f} "
              f"(Elo {base['avg_draw_elo']:.3f} vs Mkt {base['avg_draw_market']:.3f})")

    best = None
    results = []
    for total in [2.0, 2.2, 2.4, 2.6, 2.62, 2.8, 3.0, 3.2, 3.4, 3.6, 3.8, 4.0]:
        for scale in [200, 250, 300, 350, 400, 450, 500, 600]:
            r = evaluate_params(matches, elo_cache, total, scale, wc_predict.Config.RHO)
            if r is None:
                continue
            results.append((total, scale, r))
            if best is None or r["avg_brier"] < best["avg_brier"]:
                best = r
                best["total"] = total
                best["scale"] = scale

    # 按 Brier 排序打印 top 10
    results.sort(key=lambda x: x[2]["avg_brier"])
    print(f"\n  {'Rank':<5} {'Total':<8} {'Scale':<8} {'Brier':<10} {'KL':<10} {'DrawBias':<10}")
    print(f"  {'-'*50}")
    for i, (total, scale, r) in enumerate(results[:15]):
        marker = " <-- best" if i == 0 else ""
        print(f"  {i+1:<5} {total:<8.2f} {scale:<8.0f} "
              f"{r['avg_brier']:<10.4f} {r['avg_kl']:<10.4f} "
              f"{r['draw_bias']:+.4f}{marker}")

    if best:
        print(f"\n  ★ 最优: total={best['total']:.2f}  scale={best['scale']:.0f}  "
              f"Brier={best['avg_brier']:.4f}  draw_bias={best['draw_bias']:+.4f}")
        if base:
            delta = base["avg_brier"] - best["avg_brier"]
            print(f"     vs baseline ΔBrier = {delta:+.4f} "
                  f"({'改善' if delta > 0 else '恶化'})")
    return best, results


def grid_search_with_db(matches, elo_cache, rho, draw_b, draw_scale):
    """用指定 RHO+DrawBoost 做二维网格搜索 (AVG_TOTAL_GOALS × ELO_SCALE)。"""
    best = None
    for total in [2.0, 2.2, 2.4, 2.6, 2.62, 2.8, 3.0, 3.2, 3.4, 3.6, 3.8, 4.0]:
        for scale in [200, 250, 300, 350, 400, 450, 500, 600]:
            r = evaluate_params(matches, elo_cache, total, scale, rho,
                              draw_b=draw_b, draw_scale=draw_scale)
            if r is None:
                continue
            if best is None or r["avg_brier"] < best["avg_brier"]:
                best = r
                best["total"] = total
                best["scale"] = scale

    if best:
        print(f"  最优: total={best['total']:.2f}  scale={best['scale']:.0f}  "
              f"Brier={best['avg_brier']:.4f}  draw_bias={best['draw_bias']:+.4f}")
    return best


def scan_rho(matches, elo_cache, best_total, best_scale):
    """固定 total/scale,扫 RHO 找最优。"""
    print("\n" + "=" * 70)
    print(f"  扫描 RHO (fixed total={best_total:.2f} scale={best_scale:.0f})")
    print("=" * 70)

    best = None
    results = []
    for rho in [-0.20, -0.15, -0.12, -0.10, -0.08, -0.06, -0.04, -0.02, 0.0]:
        r = evaluate_params(matches, elo_cache, best_total, best_scale, rho)
        if r is None:
            continue
        results.append((rho, r))
        if best is None or r["avg_brier"] < best["avg_brier"]:
            best = r
            best["rho"] = rho

    results.sort(key=lambda x: x[1]["avg_brier"])
    for rho, r in results:
        marker = " <-- best" if r is best else ""
        print(f"  ρ={rho:+.2f}  Brier={r['avg_brier']:.4f}  KL={r['avg_kl']:.4f}  "
              f"draw_bias={r['draw_bias']:+.4f}{marker}")
    return best


def scan_draw_boost(matches, elo_cache, best_total, best_scale, best_rho):
    """扫描条件平局 boost 参数 (B, SCALE),同时测试完全关闭 boost 的效果。"""
    print("\n" + "=" * 70)
    print(f"  扫描 Draw Boost (total={best_total:.2f} scale={best_scale:.0f} rho={best_rho:+.2f})")
    print("=" * 70)

    # 先测关闭 draw boost 的 baseline
    r_off = evaluate_params(matches, elo_cache, best_total, best_scale, best_rho,
                           draw_b=1.0, draw_scale=120)
    if r_off:
        print(f"\n  Draw Boost OFF (B=1.0): Brier={r_off['avg_brier']:.4f}  "
              f"KL={r_off['avg_kl']:.4f}  draw_bias={r_off['draw_bias']:+.4f}")

    # 扫描 B × SCALE
    best = None
    results = []
    for b_val in [1.0, 1.2, 1.4, 1.6, 1.8, 2.0, 2.5, 3.0]:
        for ds in [60, 80, 100, 120, 150, 200, 300]:
            r = evaluate_params(matches, elo_cache, best_total, best_scale, best_rho,
                              draw_b=b_val, draw_scale=ds)
            if r is None:
                continue
            results.append((b_val, ds, r))
            if best is None or r["avg_brier"] < best["avg_brier"]:
                best = r
                best["b"] = b_val
                best["ds"] = ds

    results.sort(key=lambda x: x[2]["avg_brier"])
    print(f"\n  {'Rank':<5} {'B':<8} {'Scale':<8} {'Brier':<10} {'KL':<10} {'DrawBias':<10}")
    print(f"  {'-'*50}")
    for i, (b_val, ds, r) in enumerate(results[:15]):
        marker = " <-- best" if i == 0 else ""
        print(f"  {i+1:<5} {b_val:<8.2f} {ds:<8.0f} "
              f"{r['avg_brier']:<10.4f} {r['avg_kl']:<10.4f} "
              f"{r['draw_bias']:+.4f}{marker}")

    if best:
        print(f"\n  ★ 最优 draw_boost: B={best['b']:.1f}  SCALE={best['ds']:.0f}  "
              f"Brier={best['avg_brier']:.4f}")
        if r_off:
            delta = r_off["avg_brier"] - best["avg_brier"]
            print(f"     vs OFF ΔBrier = {delta:+.4f}")
    return best


def per_match_total_analysis(matches, elo_cache):
    """分析:关闭 draw boost + Dixon-Coles 后,对每场找令 Elo draw prob = market draw prob 的 total_goals。
    然后回归 total* ~ Elo 特征,建立逐场 total 预测器。"""
    print("\n" + "=" * 70)
    print("  逐场 total_goals 分析 (draw boost OFF, RHO=0)")
    print("=" * 70)

    orig_total = wc_predict.Config.AVG_TOTAL_GOALS
    orig_scale = wc_predict.Config.ELO_SCALE
    orig_rho = wc_predict.Config.RHO
    orig_db = wc_predict.Config.DRAW_BOOST_B
    orig_ds = wc_predict.Config.DRAW_BOOST_SCALE

    # 关闭 draw boost 和 RHO,让 total_goals 成为唯一自由度
    wc_predict.Config.RHO = 0.0
    wc_predict.Config.DRAW_BOOST_B = 1.0

    samples = []
    for m in matches:
        y = m["year"]
        elo = elo_cache.get(y, {})
        eh = elo.get(m["home"])
        ea = elo.get(m["away"])
        if eh is None or ea is None:
            continue
        mh, md, ma = compute_odds.devig_power(*m["odds"])

        # 二分搜索:找 total* 使 Elo draw prob = market draw prob
        lo, hi = 1.2, 5.5
        best_total = None
        best_err = float("inf")
        for _ in range(60):
            mid = 0.5 * (lo + hi)
            wc_predict.Config.AVG_TOTAL_GOALS = mid
            _, dr, _, _, _ = wc_predict.predict_from_elo(eh, ea, home_adv_elo=0)
            err = dr - md
            if abs(err) < best_err:
                best_err = abs(err)
                best_total = mid
            if err > 0:
                lo = mid  # Elo draw too high → increase total (more goals → fewer draws)
            else:
                hi = mid
            if hi - lo < 0.005:
                break

        elo_diff = eh - ea
        avg_elo = 0.5 * (eh + ea)
        samples.append({
            "home": m["home"], "away": m["away"], "year": y,
            "elo_h": eh, "elo_a": ea, "elo_diff": elo_diff, "avg_elo": avg_elo,
            "total_star": best_total,
            "mkt_draw": md, "mkt_home": mh, "mkt_away": ma,
            "odds": m["odds"],
        })

    wc_predict.Config.AVG_TOTAL_GOALS = orig_total
    wc_predict.Config.ELO_SCALE = orig_scale
    wc_predict.Config.RHO = orig_rho
    wc_predict.Config.DRAW_BOOST_B = orig_db
    wc_predict.Config.DRAW_BOOST_SCALE = orig_ds

    if not samples:
        print("  无有效样本")
        return None

    # 统计 total* 分布
    totals = [s["total_star"] for s in samples]
    n = len(totals)
    mean_t = sum(totals) / n
    var_t = sum((t - mean_t) ** 2 for t in totals) / n
    print(f"  n={n}  total* mean={mean_t:.3f}  std={math.sqrt(var_t):.3f}  "
          f"min={min(totals):.2f}  max={max(totals):.2f}")
    print(f"  (当前固定 total={wc_predict.Config.AVG_TOTAL_GOALS})")

    # 简单线性回归: total* ~ β0 + β1·|elo_diff| + β2·avg_elo
    # H0: total* 与 elo 特征无关(常数模型就是最优)
    n_float = float(n)
    x1 = [abs(s["elo_diff"]) for s in samples]
    x2 = [s["avg_elo"] for s in samples]
    y = totals

    mx1 = sum(x1) / n_float
    mx2 = sum(x2) / n_float
    my = sum(y) / n_float

    # 协方差矩阵
    c11 = sum((a - mx1) ** 2 for a in x1) / n_float
    c22 = sum((a - mx2) ** 2 for a in x2) / n_float
    c12 = sum((a - mx1) * (b - mx2) for a, b in zip(x1, x2)) / n_float
    cy1 = sum((a - mx1) * (b - my) for a, b in zip(x1, y)) / n_float
    cy2 = sum((a - mx2) * (b - my) for a, b in zip(x2, y)) / n_float

    # 解 2x2 线性方程组
    det = c11 * c22 - c12 * c12
    if abs(det) > 1e-10:
        beta1 = (cy1 * c22 - cy2 * c12) / det
        beta2 = (cy2 * c11 - cy1 * c12) / det
    else:
        beta1 = beta2 = 0.0
    beta0 = my - beta1 * mx1 - beta2 * mx2

    # R²
    y_pred = [beta0 + beta1 * abs(a) + beta2 * b for a, b in zip(x1, x2)]
    ss_res = sum((yi - yp) ** 2 for yi, yp in zip(y, y_pred))
    ss_tot = sum((yi - my) ** 2 for yi in y)
    r2 = 1.0 - ss_res / ss_tot if ss_tot > 0 else 0.0

    print(f"\n  回归: total* = {beta0:.3f} + {beta1:.4f}·|ΔElo| + {beta2:.6f}·avgElo")
    print(f"  R² = {r2:.4f}  (常数模型 R²=0)")

    # 相关系数
    r_diff = sum((a - mx1) * (b - my) for a, b in zip(x1, y)) / (n_float * math.sqrt(c11 * var_t)) if c11 > 0 and var_t > 0 else 0
    r_avg = sum((a - mx2) * (b - my) for a, b in zip(x2, y)) / (n_float * math.sqrt(c22 * var_t)) if c22 > 0 and var_t > 0 else 0
    print(f"  corr(total*, |ΔElo|) = {r_diff:+.3f}")
    print(f"  corr(total*, avgElo)  = {r_avg:+.3f}")

    # 交叉验证:留一法评估逐场 total 预测器的 Brier
    cv_brier = 0.0
    cv_n = 0
    const_brier = 0.0
    for i, s in enumerate(samples):
        # 留一法:用其他样本拟合,预测当前
        lo_x1 = [abs(s2["elo_diff"]) for j, s2 in enumerate(samples) if j != i]
        lo_x2 = [s2["avg_elo"] for j, s2 in enumerate(samples) if j != i]
        lo_y = [s2["total_star"] for j, s2 in enumerate(samples) if j != i]
        lo_n = float(len(lo_x1))
        if lo_n < 3:
            continue
        lm1 = sum(lo_x1) / lo_n
        lm2 = sum(lo_x2) / lo_n
        ly = sum(lo_y) / lo_n
        lc11 = sum((a - lm1) ** 2 for a in lo_x1) / lo_n
        lc22 = sum((a - lm2) ** 2 for a in lo_x2) / lo_n
        lc12 = sum((a - lm1) * (b - lm2) for a, b in zip(lo_x1, lo_x2)) / lo_n
        lcy1 = sum((a - lm1) * (b - ly) for a, b in zip(lo_x1, lo_y)) / lo_n
        lcy2 = sum((a - lm2) * (b - ly) for a, b in zip(lo_x2, lo_y)) / lo_n
        ldet = lc11 * lc22 - lc12 * lc12
        if abs(ldet) > 1e-10:
            lb1 = (lcy1 * lc22 - lcy2 * lc12) / ldet
            lb2 = (lcy2 * lc11 - lcy1 * lc12) / ldet
        else:
            lb1 = lb2 = 0.0
        lb0 = ly - lb1 * lm1 - lb2 * lm2
        pred_total = lb0 + lb1 * abs(s["elo_diff"]) + lb2 * s["avg_elo"]
        pred_total = max(1.5, min(5.0, pred_total))

        # 用 pred_total 算 Elo probs vs market
        wc_predict.Config.AVG_TOTAL_GOALS = pred_total
        hw, dr, aw, _, _ = wc_predict.predict_from_elo(s["elo_h"], s["elo_a"], home_adv_elo=0)
        mh, md, ma = compute_odds.devig_power(*s["odds"])
        cv_brier += ((hw - mh) ** 2 + (dr - md) ** 2 + (aw - ma) ** 2)
        cv_n += 1

        # 常数模型 baseline
        wc_predict.Config.AVG_TOTAL_GOALS = orig_total
        hw_c, dr_c, aw_c, _, _ = wc_predict.predict_from_elo(s["elo_h"], s["elo_a"], home_adv_elo=0)
        const_brier += ((hw_c - mh) ** 2 + (dr_c - md) ** 2 + (aw_c - ma) ** 2)

    wc_predict.Config.AVG_TOTAL_GOALS = orig_total

    if cv_n > 0:
        print(f"\n  留一法 CV (n={cv_n}):")
        print(f"    常数 total={orig_total}:  Brier={const_brier/cv_n:.4f}")
        print(f"    逐场 total 预测器:      Brier={cv_brier/cv_n:.4f}")
        delta = const_brier / cv_n - cv_brier / cv_n
        print(f"    Δ = {delta:+.4f} {'✓ 改善' if delta > 0 else '✗ 恶化'}")

    return {
        "samples": samples,
        "mean_total": mean_t,
        "std_total": math.sqrt(var_t),
        "beta0": beta0, "beta1": beta1, "beta2": beta2,
        "r2": r2, "r_diff": r_diff, "r_avg": r_avg,
        "cv_brier_const": const_brier / cv_n if cv_n else None,
        "cv_brier_per_match": cv_brier / cv_n if cv_n else None,
    }


def output_calibration_analysis(matches, elo_cache):
    """分析 Elo 输出的逐方向偏差,评估 Platt scaling 的必要性。"""
    print("\n" + "=" * 70)
    print("  输出校准分析 (Platt / Isotonic 必要性评估)")
    print("=" * 70)

    all_elo = {"h": [], "d": [], "a": []}
    all_mkt = {"h": [], "d": [], "a": []}
    for m in matches:
        y = m["year"]
        elo = elo_cache.get(y, {})
        eh = elo.get(m["home"])
        ea = elo.get(m["away"])
        if eh is None or ea is None:
            continue
        hw, dr, aw, _, _ = wc_predict.predict_from_elo(eh, ea, home_adv_elo=0)
        mh, md, ma = compute_odds.devig_power(*m["odds"])
        all_elo["h"].append(hw)
        all_elo["d"].append(dr)
        all_elo["a"].append(aw)
        all_mkt["h"].append(mh)
        all_mkt["d"].append(md)
        all_mkt["a"].append(ma)

    n = len(all_elo["h"])
    if n == 0:
        return

    # 按方向统计偏差
    for label, elo_key in [("主胜", "h"), ("平局", "d"), ("客胜", "a")]:
        elo_vals = all_elo[elo_key]
        mkt_vals = all_mkt[elo_key]
        mean_elo = sum(elo_vals) / n
        mean_mkt = sum(mkt_vals) / n
        bias = mean_elo - mean_mkt
        rmse = math.sqrt(sum((e - m) ** 2 for e, m in zip(elo_vals, mkt_vals)) / n)
        corr = (sum((e - mean_elo) * (m - mean_mkt) for e, m in zip(elo_vals, mkt_vals)) /
                (n * math.sqrt(sum((e - mean_elo) ** 2 for e in elo_vals) / n) *
                 math.sqrt(sum((m - mean_mkt) ** 2 for m in mkt_vals) / n))) if n > 1 else 0
        print(f"  {label}: Elo均值={mean_elo:.3f}  Mkt均值={mean_mkt:.3f}  "
              f"Bias={bias:+.3f}  RMSE={rmse:.3f}  Corr={corr:+.3f}")

    # 可靠性图:按 Elo 预测概率分桶,看实际市场概率
    print(f"\n  可靠性图 (Elo draw prob → Mkt draw prob):")
    for bin_lo in [0.15, 0.20, 0.25, 0.30, 0.35]:
        bin_hi = bin_lo + 0.05
        in_bin = [(e, m) for e, m in zip(all_elo["d"], all_mkt["d"])
                  if bin_lo <= e < bin_hi]
        if in_bin:
            avg_e = sum(p[0] for p in in_bin) / len(in_bin)
            avg_m = sum(p[1] for p in in_bin) / len(in_bin)
            print(f"    Elo∈[{bin_lo:.2f},{bin_hi:.2f}): n={len(in_bin):2d}  "
                  f"Elo={avg_e:.3f} → Mkt={avg_m:.3f}  bias={avg_e-avg_m:+.3f}")


def main():
    print("=" * 70)
    print("  Elo 模型方向性偏误校准")
    print("=" * 70)

    # 1. 加载历史比赛(含市场赔率)
    matches = load_historical_matches()
    print(f"\n  加载历史比赛: {len(matches)} 场 (1998-2022, 含 BetExplorer 收盘赔率)")

    # 2. 为每届世界杯构建赛前 Elo
    print("\n  构建各届赛前 Elo...")
    elo_cache = {}
    for year, cutoff in sorted(WC_OPENERS.items()):
        elo, _, _ = compute_elo.build_elo(RESULTS, cutoff_date=cutoff)
        elo_cache[year] = elo
        # 统计该届有 Elo 的比赛
        n_covered = sum(1 for m in matches if m["year"] == year
                        and m["home"] in elo and m["away"] in elo)
        print(f"    {year} (cutoff={cutoff}): {len(elo)} 队 Elo, 覆盖 {n_covered} 场")

    # 3. 网格搜索最优 (total, scale) — 保持当前 draw boost
    print("\n  --- 第1轮: 保持当前 Draw Boost 扫 (total, scale) ---")
    best_gs, all_gs = grid_search(matches, elo_cache)

    # 4. 扫描 RHO
    best_rho = None
    if best_gs:
        best_rho = scan_rho(matches, elo_cache, best_gs["total"], best_gs["scale"])

    # 5. 扫描 Draw Boost (最重要:这是主导 draw prob 的参数)
    best_db = None
    use_total = best_gs["total"] if best_gs else wc_predict.Config.AVG_TOTAL_GOALS
    use_scale = best_gs["scale"] if best_gs else wc_predict.Config.ELO_SCALE
    use_rho = best_rho["rho"] if best_rho else 0.0
    if best_rho:
        best_db = scan_draw_boost(matches, elo_cache, use_total, use_scale, use_rho)

    # 6. 用最优 RHO+DB 重新扫 (total, scale) — 因为交互效应
    if best_rho and best_db:
        print("\n  --- 第2轮: 用最优 RHO+DrawBoost 重扫 (total, scale) ---")
        best_gs2 = grid_search_with_db(matches, elo_cache,
                                         best_rho["rho"],
                                         best_db["b"],
                                         best_db["ds"])
        if best_gs2:
            use_total = best_gs2["total"]
            use_scale = best_gs2["scale"]

    # 7. 逐场 total 回归分析
    per_match = per_match_total_analysis(matches, elo_cache)

    # 8. 输出校准分析
    output_calibration_analysis(matches, elo_cache)

    # 9. 汇总推荐
    print("\n" + "=" * 70)
    print("  推荐方案")
    print("=" * 70)

    # 计算最优组合
    best_total = use_total
    best_scale = use_scale
    best_rho_val = best_rho["rho"] if best_rho else 0.0
    best_b = best_db["b"] if best_db else 1.0
    best_ds = best_db["ds"] if best_db else 120.0

    # 最终评估
    final = evaluate_params(matches, elo_cache, best_total, best_scale,
                           best_rho_val, draw_b=best_b, draw_scale=best_ds)
    base = evaluate_params(matches, elo_cache,
                          wc_predict.Config.AVG_TOTAL_GOALS,
                          wc_predict.Config.ELO_SCALE,
                          wc_predict.Config.RHO)

    print(f"\n  当前配置: total={wc_predict.Config.AVG_TOTAL_GOALS}  "
          f"scale={wc_predict.Config.ELO_SCALE}  rho={wc_predict.Config.RHO}  "
          f"B={wc_predict.Config.DRAW_BOOST_B}  DScale={wc_predict.Config.DRAW_BOOST_SCALE}")
    if base:
        print(f"     Brier={base['avg_brier']:.4f}  draw_bias={base['draw_bias']:+.4f}")

    print(f"\n  ★ 推荐配置: total={best_total:.2f}  scale={best_scale:.0f}  "
          f"rho={best_rho_val:+.2f}  B={best_b:.1f}  DScale={best_ds:.0f}")
    if final:
        print(f"     Brier={final['avg_brier']:.4f}  draw_bias={final['draw_bias']:+.4f}")
        if base:
            delta_brier = base["avg_brier"] - final["avg_brier"]
            delta_bias = abs(base["draw_bias"]) - abs(final["draw_bias"])
            print(f"     ΔBrier = {delta_brier:+.4f}  Δ|draw_bias| = {delta_bias:+.4f}")
            print(f"     {'✓ 改善' if delta_brier > 0 else '✗ 无改善或恶化'}")
    print(f"\n  需更新 wc_predict.py Config:")
    print(f"    AVG_TOTAL_GOALS = {best_total:.2f}")
    print(f"    ELO_SCALE = {best_scale:.0f}")
    print(f"    RHO = {best_rho_val:+.2f}")
    print(f"    DRAW_BOOST_B = {best_b:.1f}")
    print(f"    DRAW_BOOST_SCALE = {best_ds:.0f}")


if __name__ == "__main__":
    main()
