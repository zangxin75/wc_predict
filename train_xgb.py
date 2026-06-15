"""train_xgb.py — XGBoost 三态预测模型(突破模型)

动机:回测证明统计模型(elo/ad)同源,加权集成收益微(+0.0009 噪声级)。
XGBoost 从非线性特征组合出发,提供异源视角——这是集成增益的必要条件。

特征(9 维,严格 walk-forward —— 每场用赛前数据,赛后才更新):
  elo_home, elo_away, elo_diff, 近10场进球/失球均值(主客各2),
  赛事权重 tournament_k, 中立场标记
标签:0=主胜, 1=平, 2=客胜(multi:softprob)

用法:
    python train_xgb.py                    # 训练 + 回测 2018/2022 + 保存模型
    python train_xgb.py --cutoff 2022-11-20 --until 2022-12-19
"""

import argparse
import csv
import os
import sys
from collections import deque

import numpy as np
import xgboost as xgb

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8")

import compute_elo

HERE = os.path.dirname(os.path.abspath(__file__))
RESULTS = os.path.join(HERE, "results.csv")
MODEL_OUT = os.path.join(HERE, "xgb_wc.json")

# 无历史时的 form 先验(半个平均进球;XGBoost 会从 Elo 主导学)
FORM_PRIOR = 1.31

FEATURE_NAMES = [
    "elo_home", "elo_away", "elo_diff",
    "gf_avg_home", "ga_avg_home", "gf_avg_away", "ga_avg_away",
    "tournament_k", "neutral",
]


def build_dataset(path=RESULTS):
    """单次遍历所有比赛(按日期),增量维护 Elo + form,每场产 (date, features, label, tournament)。

    walk-forward:每场特征用赛前 Elo/form(当前值),赛后才更新。无泄漏。
    返回 [(date_str, [9 特征], label, tournament_str), ...] 按日期升序。
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
                "tournament": r[5], "neutral": r[8].strip().upper() == "TRUE",
            })
    matches.sort(key=lambda m: m["date"])

    elo = {}                                   # team -> Elo(初始 1500,差抵消无需校准)
    form = {}                                  # team -> deque([(gf, ga), ...], maxlen=10)
    rows = []
    for m in matches:
        home, away = m["home"], m["away"]
        hs, as_ = m["hs"], m["as_"]
        eh = elo.get(home, 1500.0)
        ea = elo.get(away, 1500.0)
        fh = form.get(home)
        fa = form.get(away)

        def _avg(q, idx):
            if not q:
                return FORM_PRIOR
            return sum(x[idx] for x in q) / len(q)

        features = [
            eh, ea, eh - ea,
            _avg(fh, 0), _avg(fh, 1), _avg(fa, 0), _avg(fa, 1),
            compute_elo.tournament_k(m["tournament"]),
            1.0 if m["neutral"] else 0.0,
        ]
        label = 0 if hs > as_ else (2 if hs < as_ else 1)
        rows.append((m["date"], features, label, m["tournament"], home, away))

        # 赛后更新(walk-forward 关键:特征已固定用赛前值)
        neh, nea = compute_elo.update_elo_after_match(
            eh, ea, hs, as_, m["tournament"], m["neutral"])
        elo[home] = neh
        elo[away] = nea
        form.setdefault(home, deque(maxlen=10)).append((hs, as_))
        form.setdefault(away, deque(maxlen=10)).append((as_, hs))
    return rows


def _label_to_onehot(y, n_classes=3):
    oh = np.zeros((len(y), n_classes))
    oh[np.arange(len(y)), y] = 1.0
    return oh


def train_model(X, y, num_boost_round=300, seed=42):
    """XGBoost 多分类训练。返回 model。"""
    dtrain = xgb.DMatrix(np.asarray(X), label=np.asarray(y),
                         feature_names=FEATURE_NAMES)
    params = {
        "objective": "multi:softprob", "num_class": 3,
        "eval_metric": "mlogloss",
        "max_depth": 4, "eta": 0.05, "subsample": 0.8,
        "colsample_bytree": 0.8, "seed": seed, "verbosity": 0,
    }
    return xgb.train(params, dtrain, num_boost_round=num_boost_round)


def predict_proba(model, X):
    """返回 (n, 3) softprob。"""
    return model.predict(xgb.DMatrix(np.asarray(X), feature_names=FEATURE_NAMES))


def brier_score(preds, outcomes):
    """三态 Brier:mean(Σ_k (p_k − o_k)²)。越低越好,随机≈0.667。"""
    preds = np.asarray(preds)
    outcomes = np.asarray(outcomes)
    return float(np.mean(np.sum((preds - outcomes) ** 2, axis=1)))


def backtest_xgb(rows, cutoff, until, tournament="FIFA World Cup"):
    """walk-forward 回测:train=date<cutoff,test=[cutoff,until) 且指定赛事。

    返回 (brier, n_test, model)。
    """
    train = [r for r in rows if r[0] < cutoff]
    test = [r for r in rows if cutoff <= r[0] < until and r[3] == tournament]
    if not train or not test:
        raise ValueError(f"训练集/测试集为空(train={len(train)} test={len(test)})")

    Xtr = [r[1] for r in train]
    ytr = [r[2] for r in train]
    model = train_model(Xtr, ytr)
    preds = predict_proba(model, [r[1] for r in test])
    outs = _label_to_onehot([r[2] for r in test])
    return brier_score(preds, outs), len(test), model


def main():
    ap = argparse.ArgumentParser(description="XGBoost 三态预测训练 + 回测")
    ap.add_argument("--cutoff", default=None, help="单届回测 ISO 日期;不传则跑 2018+2022")
    ap.add_argument("--until", default=None)
    ap.add_argument("--rounds", type=int, default=300)
    args = ap.parse_args()

    print("=" * 56)
    print("  XGBoost 三态预测模型(突破模型)")
    print("=" * 56)
    rows = build_dataset()
    print(f"  特征矩阵:{len(rows)} 场,{len(FEATURE_NAMES)} 维特征")

    # Elo baseline 对照(backtest_findings 记录值)
    elo_baseline = {"2018": 0.5885, "2022": 0.6020}

    runs = []
    if args.cutoff:
        runs = [(args.cutoff, args.until or "9999-99-99", "自定义")]
    else:
        runs = [("2018-06-14", "2018-07-16", "2018"),
                ("2022-11-20", "2022-12-19", "2022")]

    best_model = None
    print("-" * 56)
    print(f"  {'届':<8} {'测试场':>6} {'XGB Brier':>10} {'Elo baseline':>13} {'Δ':>8}")
    print("-" * 56)
    for cutoff, until, name in runs:
        brier, n, model = backtest_xgb(rows, cutoff, until)
        elo_b = elo_baseline.get(name, float("nan"))
        delta = brier - elo_b
        flag = "✓突破" if delta < -0.005 else ("≈持平" if abs(delta) < 0.005 else "↓更差")
        print(f"  {name:<8} {n:>6} {brier:>10.4f} {elo_b:>13.4f} {delta:>+8.4f} {flag}")
        if best_model is None:
            best_model = model

    # 用全量数据训练最终模型并保存
    final = train_model([r[1] for r in rows], [r[2] for r in rows],
                        num_boost_round=args.rounds)
    final.save_model(MODEL_OUT)
    print("-" * 56)
    print(f"  最终模型(全量 {len(rows)} 场)已保存:{MODEL_OUT}")
    print("=" * 56)


if __name__ == "__main__":
    main()
