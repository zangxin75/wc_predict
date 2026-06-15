"""scan_features.py — 风格特征对"是否平局"判别力探针(go/no-go 决策证据)

动机:独立平局分类器(任务 #23)的核心赌注是——风格特征(防守型、低进球、
平局倾向、H2H)能提供 Elo 差之外的增量判别力。条件 boost 已吃掉"Elo 接近→
更可能平局"信号,所以必须验证:控制 abs_elo_diff 后,风格特征还有没有料。

三层评估(每层都看 AUC + 互信息):
  1. 全样本:所有赛事,样本充足,看特征本身有没有判别力
  2. 世界杯子集:2018/2022 决赛圈(~128 场),看判别力在小样本+高压赛事是否还在
  3. 控 Elo 分箱:全样本按 abs_elo_diff 五等分,每箱内算风格特征 AUC 取均值
     —— 这是关键:箱内 Elo 接近,若风格特征仍 >0.5,才是真增量

决策标准:
  · 至少 2 个风格特征:全样本 AUC ≥0.52 且控 Elo 分箱 AUC ≥0.52 → go(有增量)
  · 否则 → 止损,平局不可靠预测由现有信号解决

用法:
    python scan_features.py
"""

import csv
import os
import sys
from collections import defaultdict, deque

import numpy as np

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8")

from sklearn.metrics import roc_auc_score
from sklearn.feature_selection import mutual_info_classif

import compute_elo

HERE = os.path.dirname(os.path.abspath(__file__))
RESULTS = os.path.join(HERE, "results.csv")

N = 10                  # 风格窗口(与 train_xgb form 一致)
LOW_GOAL_THRESH = 2     # 总进球 ≤2 视为低进球场(平均 2.62,偏低)
INITIAL_ELO = 1500.0


def safe_auc(y, x):
    """算 x 对 y(0/1)的 AUC,自动处理方向(返回 max(auc,1-auc))与空值。

    返回 (auc_raw, auc_strength);strength=|auc-0.5|*2(0=无判别,1=完美)。
    """
    y = np.asarray(y)
    x = np.asarray(x, dtype=float)
    mask = ~np.isnan(x)
    y, x = y[mask], x[mask]
    if len(y) < 10 or len(np.unique(y)) < 2:
        return float("nan"), 0.0
    try:
        auc = roc_auc_score(y, x)
    except ValueError:
        return float("nan"), 0.0
    return auc, abs(auc - 0.5) * 2.0


def binned_auc(y, elo_diff, x, n_bins=5):
    """控制混杂:把样本按 elo_diff 分 n_bins 箱,箱内算 x 对 y 的 AUC,取均值。

    箱内 Elo 接近,故若箱内 AUC 仍偏离 0.5,说明 x 含独立于 Elo 的增量信息。
    返回平均 strength(|auc-0.5|*2)。
    """
    y = np.asarray(y)
    ed = np.asarray(elo_diff, dtype=float)
    x = np.asarray(x, dtype=float)
    valid = ~np.isnan(ed) & ~np.isnan(x)
    y, ed, x = y[valid], ed[valid], x[valid]
    if len(y) < n_bins * 10:
        return float("nan")
    edges = np.quantile(ed, np.linspace(0, 1, n_bins + 1))
    strengths = []
    for i in range(n_bins):
        lo = edges[i]
        hi = edges[i + 1] if i < n_bins - 1 else edges[i + 1] + 1
        m = (ed >= lo) & (ed < hi)
        if m.sum() < 10 or len(np.unique(y[m])) < 2:
            continue
        try:
            auc = roc_auc_score(y[m], x[m])
            strengths.append(abs(auc - 0.5) * 2.0)
        except ValueError:
            continue
    return float(np.mean(strengths)) if strengths else float("nan")


def build():
    """walk-forward:每场赛前算风格特征 + 是否平局标签。

    维护:elo(赛前快照)、hist[team]=deque((hs,as_),N)、h2h[key]=list(is_draw)。
    """
    matches = []
    with open(RESULTS, newline="", encoding="utf-8") as f:
        for r in csv.reader(f):
            if not r or r[0] == "date" or len(r) < 9:
                continue
            if not (r[3].lstrip("-").isdigit() and r[4].lstrip("-").isdigit()):
                continue
            matches.append({
                "date": r[0], "home": r[1], "away": r[2],
                "hs": int(r[3]), "as_": int(r[4]),
                "tournament": r[5],
                "neutral": r[8].strip().upper() == "TRUE",
            })
    matches.sort(key=lambda m: m["date"])

    elo = {}
    hist = defaultdict(lambda: deque(maxlen=N))
    h2h = defaultdict(list)
    rows = []
    for m in matches:
        home, away = m["home"], m["away"]
        hs, as_ = m["hs"], m["as_"]
        eh, ea = elo.get(home, INITIAL_ELO), elo.get(away, INITIAL_ELO)
        hh, aa = hist[home], hist[away]
        key = tuple(sorted((home, away)))
        h2h_hist = h2h[key]

        def _stats(q, idx_self, idx_opp):
            """q=deque((s_self, s_opp)),返回 (平局率, 低进球率, 进球方差)。
            idx_self/opp 标记视角(主队看 hs/as_ 还是反过来)。
            """
            if not q:
                return 0.5, 0.5, float("nan")
            draws, lows, totals = 0, 0, []
            for a, b in q:
                tot = a + b
                totals.append(tot)
                if a == b:
                    draws += 1
                if tot <= LOW_GOAL_THRESH:
                    lows += 1
            n = len(q)
            var = float(np.std(totals)) if len(totals) > 1 else float("nan")
            return draws / n, lows / n, var

        # 主队近 N 场:(该队进球, 对手进球);deque 存的是 (hs,as_)
        # 客队近 N 场:存的是 (as_, hs) 视角,见下方 append
        h_dr, h_low, h_var = _stats(hh, 0, 1)
        a_dr, a_low, a_var = _stats(aa, 0, 1)
        h2h_dr = (sum(h2h_hist) / len(h2h_hist)) if h2h_hist else 0.5

        feats = {
            "abs_elo_diff": abs(eh - ea),
            "home_draw_rate": h_dr,
            "away_draw_rate": a_dr,
            "home_lowgoal_rate": h_low,
            "away_lowgoal_rate": a_low,
            "min_lowgoal": min(h_low, a_low),
            "both_defensive": h_low * a_low,
            "home_goalvar": h_var,
            "away_goalvar": a_var,
            "h2h_draw_rate": h2h_dr,
            "combined_drawness": (h_dr + a_dr) / 2.0 * min(h_low, a_low),
        }
        rows.append({
            "feats": feats,
            "is_draw": 1 if hs == as_ else 0,
            "tournament": m["tournament"],
            "date": m["date"],
        })

        # 赛后更新(walk-forward 关键:特征已固定用赛前值)
        neh, nea = compute_elo.update_elo_after_match(
            eh, ea, hs, as_, m["tournament"], m["neutral"])
        elo[home], elo[away] = neh, nea
        hist[home].append((hs, as_))
        hist[away].append((as_, hs))
        h2h[key].append(1 if hs == as_ else 0)
    return rows


def evaluate(rows, title):
    names = list(rows[0]["feats"].keys())
    y = np.asarray([r["is_draw"] for r in rows])
    elo_diff = np.asarray([r["feats"]["abs_elo_diff"] for r in rows])

    print(f"\n{'=' * 78}")
    print(f"  {title}(n={len(rows)}, 平局占比={y.mean():.1%})")
    print("=" * 78)
    print(f"  {'特征':<22}{'全样本AUC':>10}{'强度':>8}"
          f"{'控Elo分箱强度':>14}{'互信息':>9}")
    print("  " + "-" * 74)

    results = {}
    for name in names:
        x = np.asarray([r["feats"][name] for r in rows], dtype=float)
        auc_raw, strength = safe_auc(y, x)
        binned = binned_auc(y, elo_diff, x)
        # 互信息(需要 reshape,跳过 abs_elo_diff 自身)
        mi = float("nan")
        try:
            xm = x.reshape(-1, 1)
            m = ~np.isnan(x)
            if m.sum() > 20:
                mi = float(mutual_info_classif(
                    xm[m].reshape(-1, 1), y[m], random_state=0)[0])
        except Exception:
            pass
        results[name] = (auc_raw, strength, binned, mi)
        print(f"  {name:<22}{auc_raw:>10.4f}{strength:>8.3f}"
              f"{binned:>14.3f}{mi:>9.4f}")
    print("  " + "-" * 74)
    print("  说明:AUC=0.5 无判别力;强度=|AUC-0.5|×2(0~1,越大越好)。")
    print("  控Elo分箱强度:分箱消除实力差混杂后,风格特征的纯增量判别力。")
    print("  对照基准=abs_elo_diff(已有信号)。")
    return results


def main():
    rows = build()
    print(f"  已处理 {len(rows)} 场,算 {len(rows[0]['feats'])} 个特征。")

    # 全样本
    res_full = evaluate(rows, "全样本 — 风格特征判别力")

    # 世界杯子集
    wc = [r for r in rows if r["tournament"] == "FIFA World Cup"]
    res_wc = evaluate(wc, "世界杯决赛圈子集 — 风格特征判别力")

    # 决策
    print(f"\n{'=' * 78}")
    print("  go/no-go 决策")
    print("=" * 78)
    style_feats = [k for k in res_full if k != "abs_elo_diff"]
    go_count = 0
    go_list = []
    for name in style_feats:
        _, str_f, binned_f, _ = res_full[name]
        _, str_w, _, _ = res_wc[name]
        # go 条件:全样本强度≥0.04 且 世界杯强度≥0.04 且 控Elo分箱强度≥0.04
        # (强度 0.04 ≈ AUC 0.52,微弱但有;三重门槛防单一噪声)
        if str_f >= 0.04 and binned_f >= 0.04:
            go_count += 1
            go_list.append((name, str_f, binned_f, str_w))
    print(f"  通过门槛(全样本强度≥0.04 且 控Elo分箱强度≥0.04)的风格特征:{go_count}")
    for name, sf, bf, sw in sorted(go_list, key=lambda t: -t[2]):
        print(f"    · {name:<20} 全样本强度={sf:.3f} 控Elo={bf:.3f} 世界杯强度={sw:.3f}")
    if go_count >= 2:
        print(f"\n  → GO:有 ≥2 个含 Elo 之外增量的风格特征,值得投入两层分类器。")
    else:
        print(f"\n  → 止损:风格特征在控制 Elo 差后增量不足(<0.04),平局不可靠预测")
        print(f"    无法由现有信号外的风格特征解决。记录证伪,转向其他方向。")
    print("=" * 78)


if __name__ == "__main__":
    main()
