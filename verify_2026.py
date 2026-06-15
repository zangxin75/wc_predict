"""verify_2026.py — 2026 世界杯已打比赛【赛前】预测验证(真实未来检验)

用 cutoff=2026-06-11(开赛前)的 Elo,对已打比赛做赛前预测,逐场对比真实结果。
与历史回测(2018/2022)的本质区别:这是模型第一次面对真实发生的未来——
  无调参机会、无 peeking、无 walk-forward 调整。结果即真实水平。

两部分:
  Part 1 — elo 单模型(含东道主加成)逐场预测 vs 真实,看命中率与比分。
  Part 2 — 三模型(elo 中立 / xgb / ensemble 分歧门控)Brier 对比,看集成能否救场。

用法:
    python verify_2026.py
"""

import os
import sys

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8")

import compute_elo
import wc_predict
from backtest import load_test_matches, actual_outcome, score_metric

HERE = os.path.dirname(os.path.abspath(__file__))
RESULTS = os.path.join(HERE, "results.csv")
CUTOFF = "2026-06-11"
# 动态 until:验证"开赛~今天"全部已打(实时滚动),而非硬编码日期
import datetime
UNTIL = (datetime.date.today() + datetime.timedelta(days=1)).isoformat()

# 东道主主场 Elo 加成(英文队名 → 加成,取自 wc_predict.Config.HOST_NATION_ADV)
HOST_ADV = {"Mexico": 90, "United States": 70, "Canada": 40}

OUTCOME = {0: "主胜", 1: "平", 2: "客胜"}


def part1_elo():
    """Part 1:elo + 东道主加成,逐场赛前预测 vs 真实。"""
    print("=" * 78)
    print(f"  Part 1 — elo 单模型(含东道主加成)逐场赛前预测  (cutoff={CUTOFF})")
    print("=" * 78)

    elo, _, _ = compute_elo.build_elo(RESULTS, cutoff_date=CUTOFF)
    matches = load_test_matches(RESULTS, CUTOFF, "FIFA World Cup")
    zh = wc_predict.EN_TO_ZH

    preds, outs, rows = [], [], []
    for m in matches:
        home, away = m["home"], m["away"]
        eh, ea = elo.get(home), elo.get(away)
        if eh is None or ea is None:
            print(f"  跳过(Elo 缺失):{home} vs {away}")
            continue
        adv = HOST_ADV.get(home, 0) if not m["neutral"] else 0
        hw, dr, aw, _, top = wc_predict.predict_from_elo(eh, ea, home_adv_elo=adv)
        probs = [hw, dr, aw]
        pred_idx = probs.index(max(probs))
        actual_idx = actual_outcome(m["hs"], m["as_"]).index(1.0)
        top_sc = top[0][0]
        rows.append((home, away, adv, m["hs"], m["as_"], actual_idx,
                     probs, pred_idx, pred_idx == actual_idx,
                     top_sc, top_sc == (m["hs"], m["as_"]), eh, ea))
        preds.append(probs)
        outs.append(actual_outcome(m["hs"], m["as_"]))

    print(f"\n  共 {len(rows)} 场已打比赛\n")
    print(f"  {'比赛':<24}{'主场':<6}{'真实':<5}{'真实结果':<7}"
          f"| {'预测 主/平/客':<16}{'预测':<5}{'命中':<4}| {'首选比分':<9}{'比分命中'}")
    print("  " + "-" * 76)
    for (home, away, adv, hs, as_, ai, pp, pi, hit, ts, sh, eh, ea) in rows:
        name = f"{zh.get(home, home)} vs {zh.get(away, away)}"
        site = f"+{adv}" if adv else "中立"
        probs_s = f"{pp[0]*100:>4.0f}/{pp[1]*100:>2.0f}/{pp[2]*100:>3.0f}"
        print(f"  {name:<22}{site:<7}{hs}-{as_:<3}{OUTCOME[ai]:<6}"
              f"| {probs_s:<18}{OUTCOME[pi]:<5}{'✓' if hit else '✗':<4}| "
              f"{ts[0]}-{ts[1]:<10}{'✓' if sh else '✗'}")

    sc = score_metric(preds, outs)
    tw = sum(r[8] for r in rows)
    shn = sum(r[10] for r in rows)
    n = len(rows)
    print("\n  " + "=" * 76)
    print(f"  【Part 1 汇总】{n} 场")
    print(f"    三态命中率:    {tw}/{n} = {tw/n*100:.1f}%   (README 预期 55-60%)")
    print(f"    精确比分命中:  {shn}/{n} = {shn/n*100:.1f}%  (README 预期 12-15%)")
    print(f"    Brier 分数:    {sc['brier']:.4f}   (随机≈0.667,历史回测均值≈0.595)")
    print(f"    LogLoss:       {sc['logloss']:.4f}")
    print("  " + "=" * 76)
    return n, tw, shn, sc["brier"]


def part2_models():
    """Part 2:三模型(elo 中立 / xgb / ensemble)Brier 对比。

    此处 elo 用中立场(home_adv=0),与 Part 1 东道主加成版不同——
    为与 train_xgb 特征同源(ensemble 要求 stat 与 xgb 同 Elo 源)。
    ensemble 三态命中率结构上 = elo(分歧退回 elo),故此处看 Brier 校准差异。
    """
    import train_xgb
    import ensemble

    print("\n" + "=" * 78)
    print(f"  Part 2 — 三模型 Brier 对比(elo中立 / xgb / ensemble 分歧门控)")
    print("=" * 78)
    print("  训练 xgb(cutoff 前全量)→ 预测 2026 已打 → 算三模型 Brier...\n")

    rows = train_xgb.build_dataset()
    train = [r for r in rows if r[0] < CUTOFF]
    test = [r for r in rows if CUTOFF <= r[0] < UNTIL and r[3] == "FIFA World Cup"]
    model = train_xgb.train_model([r[1] for r in train], [r[2] for r in train])
    xgb_probs = train_xgb.predict_proba(model, [r[1] for r in test])
    outs_oh = train_xgb._label_to_onehot([r[2] for r in test])

    elo_p, xgb_p, ens_p, ndis = [], [], [], 0
    for r, xp in zip(test, xgb_probs):
        eh, ea = r[1][0], r[1][1]
        hw, dr, aw, _, _ = wc_predict.predict_from_elo(eh, ea, home_adv_elo=0)
        sp = [hw, dr, aw]
        elo_p.append(sp)
        xgb_p.append(list(xp))
        if sp.index(max(sp)) != list(xp).index(max(xp)):
            ndis += 1
        ws, wx = ensemble.combine_weights(sp, list(xp))
        ens_p.append([ws * a + wx * b for a, b in zip(sp, xp)])

    b_elo = score_metric(elo_p, outs_oh)["brier"]
    b_xgb = score_metric(xgb_p, outs_oh)["brier"]
    b_ens = score_metric(ens_p, outs_oh)["brier"]
    best = min(b_elo, b_xgb, b_ens)

    def flag(b):
        return "  ← 最优" if b == best else ""

    print(f"  样本:{len(test)} 场   elo/xgb 分歧场:{ndis} 场")
    print("  " + "-" * 76)
    print(f"    elo(中立):    {b_elo:.4f}{flag(b_elo)}")
    print(f"    xgb:          {b_xgb:.4f}{flag(b_xgb)}")
    print(f"    ensemble:     {b_ens:.4f}{flag(b_ens)}")
    print("  " + "-" * 76)
    print(f"  (参考:随机 Brier≈0.667;历史回测 elo 均值≈0.595、xgb≈0.600、ensemble≈0.595)")
    print("  " + "=" * 76)
    return len(test), ndis, b_elo, b_xgb, b_ens


def main():
    part1_elo()
    part2_models()


if __name__ == "__main__":
    main()
