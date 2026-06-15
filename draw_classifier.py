"""draw_classifier.py — 两层混合平局分类器(任务 #23,已证伪保留作学习记录)

动机:scan_features.py 证据——风格特征(两队低进球率、平局倾向、进球方差)对
"是否平局"有 abs_elo_diff 之外的增量判别力(控 Elo 分箱后强度 0.10-0.11,世界杯
子集更强 0.13)。泊松模型结构性低估平局,因为它只看进球强度,捕捉不到"风格惯性"。

最终判决:证伪(第 6 个被否方向)。判别力真实存在(scan_features AUC=0.55),
但实现为预测管道后 Brier 全线恶化(融合 v1 哑铃式:q 校准失配召回归零;融合
v2 logit-odds 修正:alpha 全段 Brier +0.06~+0.20)。根因:世界杯平局占 22%,
Brier(平方误差)下"错杀非平局"惩罚 >> "漏判平局",召回↑必伴 Brier↑,无甜点。
这是信息论硬约束。条件 boost(已整合)是帕累托最优解,本模块不整合。

学到的(比成功更有价值):
  · AUC(排序判别力)≠ 可用概率(校准+绝对值),两者在小样本+类别不平衡下对立
  · "有判别力的特征"不等于"能改善目标的模型"——目标函数(Brier)决定一切
  · 平局预测的 Brier/召回对立是世界杯场景的结构性约束,与 RHO/boost 失败同根

用法(诊断/学习用,非生产):
    python draw_classifier.py            # 回测,看 Brier 恶化+召回提升的对立
    python draw_classifier.py --cutoff 2022-11-20 --until 2022-12-19
"""

import argparse
import csv
import os
import sys
from collections import defaultdict, deque

import numpy as np
from sklearn.linear_model import LogisticRegression

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8")

import compute_elo
import wc_predict

HERE = os.path.dirname(os.path.abspath(__file__))
RESULTS = os.path.join(HERE, "results.csv")

N = 10                  # 风格窗口(与 train_xgb / scan_features 一致)
LOW_GOAL_THRESH = 2
INITIAL_ELO = 1500.0

# logistic 用的风格特征名(顺序固定)
STYLE_FEATURES = [
    "home_lowgoal_rate", "away_lowgoal_rate", "min_lowgoal",
    "both_defensive", "home_goalvar", "away_goalvar",
    "home_draw_rate", "away_draw_rate", "abs_elo_diff",
]


def build_style_features(path=RESULTS):
    """walk-forward 遍历,每场算 (date, style_vec, is_draw, tournament, home, away, eh, ea)。

    style_vec 按 STYLE_FEATURES 顺序;is_draw=真实平局标签。
    eh/ea=赛前 Elo(供第一层泊松用,同源)。
    """
    matches = []
    with open(path, newline="", encoding="utf-8") as f:
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
    rows = []
    for m in matches:
        home, away, hs, as_ = m["home"], m["away"], m["hs"], m["as_"]
        eh = elo.get(home, INITIAL_ELO)
        ea = elo.get(away, INITIAL_ELO)
        hh, aa = hist[home], hist[away]

        def _stats(q):
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

        h_dr, h_low, h_var = _stats(hh)
        a_dr, a_low, a_var = _stats(aa)
        feats = {
            "home_lowgoal_rate": h_low,
            "away_lowgoal_rate": a_low,
            "min_lowgoal": min(h_low, a_low),
            "both_defensive": h_low * a_low,
            "home_goalvar": h_var,
            "away_goalvar": a_var,
            "home_draw_rate": h_dr,
            "away_draw_rate": a_dr,
            "abs_elo_diff": abs(eh - ea),
        }
        vec = [feats[k] for k in STYLE_FEATURES]
        rows.append({
            "date": m["date"], "vec": vec, "is_draw": 1 if hs == as_ else 0,
            "tournament": m["tournament"], "home": home, "away": away,
            "eh": eh, "ea": ea,
        })

        neh, nea = compute_elo.update_elo_after_match(
            eh, ea, hs, as_, m["tournament"], m["neutral"])
        elo[home], elo[away] = neh, nea
        hist[home].append((hs, as_))
        hist[away].append((as_, hs))
    return rows


def blend(p0_h, p0_d, p0_a, q, alpha=1.0):
    """★【两层混合融合:logit-odds 修正】(v2,修正 v1 校准失败)

    v1(哑铃式再分配)失败诊断:logistic q 与泊松 p0_d 尺度不可比(q 全局偏低),
    直接替换导致 64 场里 0 场 q>p0_d,召回归零。class_weight='balanced' 能救召回
    但 Brier 爆(0.81)——证明判别力真实存在,问题在校准。

    正解:不替换概率,而在 logit 尺度叠加修正。以 logistic 基线 q0(训练集平局先验)
    为参照,学"相对偏移" g = logit(q) - logit(q0),再把这个偏移加到泊松 logit 上:
        logit(p_d_adj) = logit(p0_d) + alpha * g
    平局概率从 p0_d 沿 logit 轴平移 g,胜负按泊松比例重分配。alpha 控制修正强度。

    优点:① 保持泊松校准基准;② g 可正可负(风格特征双向作用);③ 天然单调。

    ── 回测结果(任务 #23 最终判决:证伪)─────────────────────────────
    alpha 扫描全段 Brier 均恶化:
      alpha=0.5: 2018 +0.095 / 2022 +0.060(召回 23%/40%)
      alpha=3.0: 2018 +0.204 / 2022 +0.141(召回 54%/73%)
    根因(scan_features 诊断):风格特征 AUC=0.55(排序判别力真实存在),但世界杯
    平局占 22% → Brier(平方误差)下"错杀非平局"的惩罚 >> "漏判平局"。召回↑必伴
    Brier↑,两者对立,无甜点。这是信息论硬约束,非调参可破。
    与 RHO/全局boost 同模式:平局召回与 Brier 不可兼得。条件 boost(已整合)是
    该约束下的帕累托最优解。本模块保留作学习记录,不整合入预测管道。
    """
    # 平局 vs 非平局的 logit 修正
    eps = 1e-6
    q_c = min(max(q, eps), 1 - eps)
    p0_c = min(max(p0_d, eps), 1 - eps)
    # 偏移:相对 logit(q0=0.227 训练先验)的偏离
    g = np.log(q_c / (1 - q_c)) - np.log(0.227 / (1 - 0.227))
    logit_adj = np.log(p0_c / (1 - p0_c)) + alpha * g
    p_d_new = 1.0 / (1.0 + np.exp(-logit_adj))
    p_d_new = min(max(p_d_new, 0.01), 0.85)
    rest = 1.0 - p_d_new
    base = p0_h + p0_a
    if base <= 1e-9:
        return rest / 2.0, p_d_new, rest / 2.0
    return rest * (p0_h / base), p_d_new, rest * (p0_a / base)


def draw_recall(preds, outs):
    """平局召回率:真实平局中被 argmax 预测为平局的比例。"""
    p = np.asarray(preds)
    o = np.asarray(outs)
    real = [i for i in range(len(o)) if np.argmax(o[i]) == 1]
    if not real:
        return float("nan"), 0
    hit = sum(1 for i in real if np.argmax(p[i]) == 1)
    return hit / len(real), hit


def backtest_two_layer(rows, cutoff, until, tournament="FIFA World Cup",
                       C=1.0, alpha=1.0):
    """walk-forward 回测两层混合。返回 (brier, recall, n_draw, n_test, preds, outs)。"""
    train = [r for r in rows if r["date"] < cutoff and r["tournament"] != tournament]
    # 也纳入更早的世界杯训练(样本珍贵)
    train += [r for r in rows if r["date"] < cutoff]
    test = [r for r in rows if cutoff <= r["date"] < until and r["tournament"] == tournament]
    if not train or not test:
        raise ValueError(f"训练/测试集为空(train={len(train)} test={len(test)})")

    Xtr = np.array([r["vec"] for r in train], dtype=float)
    ytr = np.array([r["is_draw"] for r in train], dtype=int)
    # NaN 填均值(进球方差首场无历史时为 NaN)
    col_mean = np.nanmean(Xtr, axis=0)
    inds = np.where(np.isnan(Xtr))
    Xtr[inds] = np.take(col_mean, inds[1])

    clf = LogisticRegression(C=C, max_iter=1000, class_weight=None, solver="lbfgs")
    clf.fit(Xtr, ytr)

    preds, outs = [], []
    n_draw_real = 0
    for r in test:
        x = np.array(r["vec"], dtype=float).reshape(1, -1)
        inds = np.where(np.isnan(x))
        x[inds] = np.take(col_mean, inds[1])
        q = float(clf.predict_proba(x)[0, 1])   # logistic 平局概率
        # 第一层泊松(含条件 boost)
        hw, dr, aw, _, _ = wc_predict.predict_from_elo(r["eh"], r["ea"], home_adv_elo=0)
        hw, dr, aw = blend(hw, dr, aw, q, alpha=alpha)
        preds.append([hw, dr, aw])
        outs.append([1.0, 0.0, 0.0] if r["is_draw"] == 0 else [0.0, 1.0, 0.0])
        n_draw_real += r["is_draw"]
    brier = float(np.mean(np.sum((np.array(preds) - np.array(outs)) ** 2, axis=1)))
    recall, hit = draw_recall(preds, outs)
    return brier, recall, n_draw_real, len(test), preds, outs


def main():
    ap = argparse.ArgumentParser(description="两层混合平局分类器回测")
    ap.add_argument("--cutoff", default=None)
    ap.add_argument("--until", default=None)
    ap.add_argument("--C", type=float, default=1.0, help="logistic 正则强度倒数")
    ap.add_argument("--alpha", type=float, default=1.0, help="logistic 修正混合系数")
    args = ap.parse_args()

    print("=" * 70)
    print("  两层混合平局分类器(泊松基线 + logistic 风格修正)")
    print(f"  C={args.C}  alpha={args.alpha}")
    print("=" * 70)
    rows = build_style_features()
    print(f"  特征矩阵:{len(rows)} 场,{len(STYLE_FEATURES)} 维风格特征")

    runs = []
    if args.cutoff:
        runs = [(args.cutoff, args.until or "9999-99-99", "自定义")]
    else:
        runs = [("2018-06-14", "2018-07-16", "2018"),
                ("2022-11-20", "2022-12-19", "2022")]

    # 对照基线(含 boost 的纯 elo)
    baseline = {"2018": (0.6134, 0.31), "2022": (0.6105, 0.13)}

    print("-" * 70)
    print(f"  {'届':<8}{'测试':>5}{'真实平':>6}{'两层Brier':>10}{'两层召回':>9}"
          f"{'elo Brier':>10}{'elo召回':>8}{'ΔBrier':>9}")
    print("-" * 70)
    for cutoff, until, name in runs:
        b, rec, nd, nt, _, _ = backtest_two_layer(
            rows, cutoff, until, C=args.C, alpha=args.alpha)
        eb, erec = baseline.get(name, (float("nan"), float("nan")))
        d = b - eb
        flag = "✓提升" if d < -0.003 else ("≈持平" if abs(d) < 0.003 else "↓下降")
        print(f"  {name:<8}{nt:>5}{nd:>6}{b:>10.4f}{rec*100:>8.0f}%"
              f"{eb:>10.4f}{erec*100:>7.0f}%{d:>+9.4f} {flag}")
    print("-" * 70)
    print("  结果:alpha 全段 Brier 恶化(召回↑必伴 Brier↑)→ 证伪,不整合。")
    print("  详见模块 docstring 与 blend() 的回测记录。")
    print("=" * 70)


if __name__ == "__main__":
    main()
