"""backtest.py — walk-forward 回测:对比 elo / ad(攻防)两种模型的预测准确度

核心思想(无泄漏):回测某届世界杯时,Elo 和攻防参数都只用该届【开赛前】的历史算,
否则未来信息渗入、分数虚高。cutoff_date 作下界、until 作上界,让每届独立回测。

用法:
    # elo 模型(原 baseline)
    python backtest.py --cutoff 2018-06-14 --until 2018-07-16 --model elo
    # ad 模型(Dixon-Coles 攻防)
    python backtest.py --cutoff 2018-06-14 --until 2018-07-16 --model ad
    # 多届混合(样本大)
    python backtest.py --cutoff 2018-06-14
"""

import argparse
import csv
import os
import sys

import numpy as np

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8")

HERE = os.path.dirname(os.path.abspath(__file__))
RESULTS = os.path.join(HERE, "results.csv")

import compute_elo
import wc_predict


def load_test_matches(path, cutoff, tournament="FIFA World Cup", until=None):
    """筛 [cutoff, until) 区间、指定赛事的比赛作测试集。返回 dict 列表。"""
    out = []
    with open(path, newline="", encoding="utf-8") as f:
        for r in csv.reader(f):
            if not r or r[0] == "date" or len(r) < 9:
                continue
            if not (r[3].lstrip("-").isdigit() and r[4].lstrip("-").isdigit()):
                continue
            if r[0] < cutoff:
                continue
            if until is not None and r[0] >= until:
                continue
            if r[5] != tournament:
                continue
            out.append({
                "date": r[0], "home": r[1], "away": r[2],
                "hs": int(r[3]), "as_": int(r[4]),
                "tournament": r[5], "neutral": r[8].strip().upper() == "TRUE",
            })
    out.sort(key=lambda m: m["date"])
    return out


def actual_outcome(hs, as_):
    """比分 → 实际三态 one-hot [home_win, draw, away_win]。"""
    if hs > as_:
        return [1.0, 0.0, 0.0]
    if hs < as_:
        return [0.0, 0.0, 1.0]
    return [0.0, 1.0, 0.0]


def backtest_predictions(cutoff, tournament="FIFA World Cup", until=None, model="elo",
                         halflife_years=3.0, ensemble_weight=0.5):
    """跑 walk-forward 回测,返回 (predictions, outcomes, skipped, total)。

    model="elo":      build_elo(cutoff) + predict_from_elo
    model="ad":       fit_attack_defense(cutoff, halflife) + predict_from_ad
    model="ensemble": 两者加权 w*elo + (1-w)*ad,验证集成能否超单模型
                      (正式的动态集成 combine_weights 留给任务14 ensemble.py)
    """
    matches = load_test_matches(RESULTS, cutoff, tournament, until)
    preds, outs, skipped = [], [], 0

    # 按需建模型(ensemble 两个都建,复用单次 fit)
    elo = None
    ad_params = ad_glob = None
    odds_map = None
    rd_map = None
    if model in ("elo", "ensemble", "ensemble_eo", "elo_hedged"):
        elo, _, _ = compute_elo.build_elo(RESULTS, cutoff_date=cutoff)
    if model in ("ad", "ensemble"):
        import compute_ad
        ad_params, ad_glob = compute_ad.fit_attack_defense(
            RESULTS, cutoff_date=cutoff, halflife_years=halflife_years)
    if model in ("odds", "ensemble_eo"):
        import compute_odds
        odds_map = compute_odds.load_odds()
    if model == "elo_hedged":
        rd_map = compute_elo.build_rd(RESULTS, cutoff_date=cutoff)
    if model not in ("elo", "ad", "ensemble", "odds", "ensemble_eo", "elo_hedged"):
        raise ValueError(f"未知 model: {model}(可选 elo/ad/ensemble/odds/ensemble_eo/elo_hedged)")

    for m in matches:
        if model == "elo":
            eh, ea = elo.get(m["home"]), elo.get(m["away"])
            if eh is None or ea is None:
                skipped += 1
                continue
            # baseline 假设中立场;东道主加成留作后续改进
            hw, dr, aw, _, _ = wc_predict.predict_from_elo(eh, ea, home_adv_elo=0)
        elif model == "elo_hedged":
            eh, ea = elo.get(m["home"]), elo.get(m["away"])
            if eh is None or ea is None:
                skipped += 1
                continue
            # RD 缺失 → 取 rd_max=220(最不可信,最大 hedging)
            rd_h = rd_map.get(m["home"], 220.0)
            rd_a = rd_map.get(m["away"], 220.0)
            hw, dr, aw, _, _ = wc_predict.predict_from_elo_hedged(
                eh, rd_h, ea, rd_a, home_adv_elo=0)
        elif model == "ad":
            if m["home"] not in ad_params or m["away"] not in ad_params:
                skipped += 1
                continue
            hw, dr, aw, _, _ = wc_predict.predict_from_ad(
                ad_params, ad_glob, m["home"], m["away"], neutral=True)
        elif model == "odds":
            oh = compute_odds.lookup_odds(odds_map, m["home"], m["away"], year=int(m["date"][:4]))
            if oh is None:
                skipped += 1
                continue
            hw, dr, aw = compute_odds.devig_power(*oh)
        elif model == "ensemble_eo":
            eh, ea = elo.get(m["home"]), elo.get(m["away"])
            oh = compute_odds.lookup_odds(odds_map, m["home"], m["away"], year=int(m["date"][:4]))
            if eh is None or ea is None or oh is None:
                skipped += 1
                continue
            e_hw, e_dr, e_aw, _, _ = wc_predict.predict_from_elo(eh, ea, home_adv_elo=0)
            o_hw, o_dr, o_aw = compute_odds.devig_power(*oh)
            w = ensemble_weight
            hw = w * e_hw + (1 - w) * o_hw
            dr = w * e_dr + (1 - w) * o_dr
            aw = w * e_aw + (1 - w) * o_aw
        else:  # ensemble:elo + ad 加权
            eh, ea = elo.get(m["home"]), elo.get(m["away"])
            ad_ok = m["home"] in ad_params and m["away"] in ad_params
            if eh is None or ea is None or not ad_ok:
                skipped += 1
                continue
            e_hw, e_dr, e_aw, _, _ = wc_predict.predict_from_elo(eh, ea, home_adv_elo=0)
            a_hw, a_dr, a_aw, _, _ = wc_predict.predict_from_ad(
                ad_params, ad_glob, m["home"], m["away"], neutral=True)
            w = ensemble_weight
            hw = w * e_hw + (1 - w) * a_hw
            dr = w * e_dr + (1 - w) * a_dr
            aw = w * e_aw + (1 - w) * a_aw
        preds.append([hw, dr, aw])
        outs.append(actual_outcome(m["hs"], m["as_"]))
    return preds, outs, skipped, len(matches)


def score_metric(predictions, outcomes):
    """★【learning 点 2 — 多指标版】评判预测质量。

    已实现三指标(不同维度互补,单一指标有盲区):
      · brier = mean(Σ_k (p_k − o_k)²):看概率校准,平局友好。(默认主标尺)
      · logloss = −mean(log p_actual):严厉惩罚"过度自信且错",越低越好。
      · accuracy = argmax(p)==actual 的比例:粗暴直观,越高越好。

    为什么并列三个:Brier 校准好不代表命中高;logloss 抓过度自信;accuracy
    忽略概率质量。三者能暴露"Brier 下 elo 最优、但 accuracy 下 xgb 更优"这类
    分歧——这对"该信哪个模型"的判断至关重要。

    返回 dict:{brier, logloss, accuracy, n, main_metric}。
    main_metric = brier(向后兼容下游;改主标尺就改这个字段)。
    """
    p = np.array(predictions, dtype=float)
    o = np.array(outcomes, dtype=float)
    n = len(p)
    if n == 0:
        return {"brier": float("nan"), "logloss": float("nan"),
                "accuracy": float("nan"), "n": 0, "main_metric": float("nan")}

    brier = float(np.mean(np.sum((p - o) ** 2, axis=1)))
    actual = np.argmax(o, axis=1) if o.ndim > 1 else np.asarray(o, dtype=int)
    accuracy = float(np.mean(np.argmax(p, axis=1) == actual))
    eps = 1e-15
    p_act = np.clip(p[np.arange(n), actual], eps, 1.0)
    logloss = float(-np.mean(np.log(p_act)))
    return {"brier": brier, "logloss": logloss, "accuracy": accuracy,
            "n": n, "main_metric": brier}


def main():
    ap = argparse.ArgumentParser(description="walk-forward 回测世界杯预测准确度")
    ap.add_argument("--cutoff", required=True, help="ISO 日期,如 2018-06-14(该日之前算参数)")
    ap.add_argument("--until", default=None, help="测试集上界(ISO日期);不传则测 cutoff 后全部")
    ap.add_argument("--tournament", default="FIFA World Cup")
    ap.add_argument("--model",
                    choices=["elo", "ad", "ensemble", "odds", "ensemble_eo",
                             "elo_hedged"],
                    default="elo",
                    help="elo=Elo+泊松; ad=Dixon-Coles攻防; ensemble=elo+ad加权; "
                         "odds=博彩赔率去vig(异源信号); ensemble_eo=elo+odds加权; "
                         "elo_hedged=Elo+RD不确定性hedging(概率向均匀收缩)")
    ap.add_argument("--halflife", type=float, default=3.0,
                    help="ad/ensemble 的 Dixon-Coles 时间衰减半衰期(年);默认3.0")
    ap.add_argument("--ensemble_weight", type=float, default=0.5,
                    help="ensemble 模式 elo 权重(0~1,ad 占 1-w);默认0.5")
    args = ap.parse_args()

    print("=" * 56)
    rng = f"{args.cutoff} ~ {args.until}" if args.until else f"{args.cutoff} ~ 末尾"
    extra = ""
    if args.model in ("ad", "ensemble"):
        extra += f"  半衰期={args.halflife}年"
    if args.model == "ensemble":
        extra += f"  elo权重={args.ensemble_weight}"
    print(f"  walk-forward 回测  模型={args.model}  区间={rng}{extra}")
    print("=" * 56)

    preds, outs, skipped, total = backtest_predictions(
        args.cutoff, args.tournament, args.until, args.model,
        args.halflife, args.ensemble_weight)
    scores = score_metric(preds, outs)
    print(f"  测试集: {total} 场(跳过 {skipped} 场参数缺失),有效 {scores['n']} 场")
    print("-" * 56)
    print(f"  Brier 分数:   {scores['brier']:.4f}   (越低越好,0=完美,随机猜≈0.667)")
    print(f"  对数损失:     {scores['logloss']:.4f}   (越低越好,惩罚过度自信)")
    print(f"  命中率:       {scores['accuracy']*100:.1f}%   (argmax 对的比例,随机≈33%)")
    print("=" * 56)
    return scores


if __name__ == "__main__":
    main()
