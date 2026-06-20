"""compare_market_vs_elo.py — 市场驱动预测 vs 纯 Elo:2026 已打场对比

公平性:
  - cutoff Elo @ 2026-06-10(赛前,不含本届正赛场)
  - "市场驱动" = predict_one 传 odds_map+ou_map+year=2026(predictor 内部异源优先)
  - 2026 当前仅有 ou_wc.csv 4 场(Argentina/Algeria、Austria/Jordan、
    France/Senegal、Iraq/Norway),odds_wc.csv 无 2026 行
  - 无市场数据的场会自动回退到 Elo(故全 20 场对比意义有限)
"""
import csv
import math
import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
sys.path.insert(0, ROOT)

import compute_elo
import compute_odds
from webapp import predictor

CUTOFF = "2026-06-10"
LIVE = os.path.join(ROOT, "wc2026_live.csv")


def brier(probs, idx):
    ph, pd, pa = probs
    o = [0, 0, 0]; o[idx] = 1
    return (ph - o[0]) ** 2 + (pd - o[1]) ** 2 + (pa - o[2]) ** 2


def logloss(probs, idx):
    return -math.log(max(probs[idx], 1e-12))


def result_idx(hs, as_):
    return 0 if hs > as_ else (2 if hs < as_ else 1)


def main():
    elo, _, _ = compute_elo.build_elo(os.path.join(ROOT, "results.csv"),
                                       cutoff_date=CUTOFF)
    try:
        odds_map = compute_odds.load_odds()
    except Exception:
        odds_map = {}
    try:
        ou_map = compute_odds.load_ou()
    except Exception:
        ou_map = {}

    with open(LIVE, newline="", encoding="utf-8") as f:
        rows = [r for r in csv.reader(f) if r and r[0] != "date"]

    def _argmax(p):
        return p.index(max(p))

    ZH = ["主胜", "平", "客胜"]
    lines = []
    # 全集 / 有市场子集 两套统计
    agg = {
        "all":   {k: {"n": 0, "hits": 0, "brier": 0.0, "ll": 0.0, "score": 0}
                  for k in ("elo", "mkt")},
        "mkt_h": {k: {"n": 0, "hits": 0, "brier": 0.0, "ll": 0.0, "score": 0}
                  for k in ("elo", "mkt")},
    }

    for r in rows:
        date, home, away, hs, as_ = r[0], r[1], r[2], r[3], r[4]
        if hs == "NA" or as_ == "NA":
            continue
        ihs, ias = int(hs), int(as_)
        ri = result_idx(ihs, ias)

        pe = predictor.predict_one(home, away, elo, with_reflection=False)
        pm = predictor.predict_one(home, away, elo, odds_map=odds_map,
                                    ou_map=ou_map, year=2026,
                                    with_reflection=False)
        if not pe or not pm:
            continue

        zh = predictor.EN_TO_ZH.get(home, home)
        za = predictor.EN_TO_ZH.get(away, away)
        has_market = pm.get("source") == "odds"
        e_pred, m_pred = _argmax(pe["probs"]), _argmax(pm["probs"])
        e_hit, m_hit = e_pred == ri, m_pred == ri

        for bucket, in_bucket in (("all", True), ("mkt_h", has_market)):
            if not in_bucket:
                continue
            for k, p, hit in (("elo", pe, e_hit), ("mkt", pm, m_hit)):
                s = agg[bucket][k]
                s["n"] += 1
                s["hits"] += int(hit)
                s["brier"] += brier(p["probs"], ri)
                s["ll"] += logloss(p["probs"], ri)
                s["score"] += int(p["top_score"] == [ihs, ias])

        lines.append({
            "match": f"{zh} vs {za}", "score": f"{ihs}-{ias}",
            "result": ZH[ri], "mkt": "市" if has_market else "Elo",
            "elo_p": pe["probs"], "mkt_p": pm["probs"],
            "elo_pred": ZH[e_pred], "mkt_pred": ZH[m_pred],
            "e_hit": e_hit, "m_hit": m_hit,
            "lam_inv": pm.get("lam_inverted"),
        })

    # ── 明细 ─────────────────────────────────────────────────────
    print("=" * 80)
    print(f"2026 已打 {agg['all']['elo']['n']} 场 | cutoff Elo @ {CUTOFF} | "
          f"其中 {agg['mkt_h']['elo']['n']} 场有市场数据(模式 A/B)")
    print("=" * 80)
    print(f"{'比赛':<22}{'比分':<6}{'实际':<6}{'数据':<5}"
          f"{'Elo三态':<18}{'市场三态':<18}{'Elo':<4}{'市':<4}")
    for ln in lines:
        ep = "/".join(f"{x:.2f}" for x in ln["elo_p"])
        mp = "/".join(f"{x:.2f}" for x in ln["mkt_p"])
        print(f"{ln['match']:<20}{ln['score']:<6}{ln['result']:<6}{ln['mkt']:<5}"
              f"{ep:<18}{mp:<18}"
              f"{'✓' if ln['e_hit'] else '✗':<4}{'✓' if ln['m_hit'] else '✗':<4}")

    # ── 汇总 ─────────────────────────────────────────────────────
    def show(label, bucket):
        e, m = agg[bucket]["elo"], agg[bucket]["mkt"]
        if e["n"] == 0:
            print(f"\n[{label}] 无样本")
            return
        print(f"\n[{label}] n={e['n']}")
        print(f"  {'指标':<12}{'纯 Elo':>14}{'市场驱动':>14}{'Δ(市-Elo)':>14}")
        for name, fn in [("命中率", lambda s: s["hits"]/s["n"]),
                         ("Brier↓",  lambda s: s["brier"]/s["n"]),
                         ("LogLoss↓", lambda s: s["ll"]/s["n"]),
                         ("精确比分", lambda s: s["score"]/s["n"])]:
            ev, mv = fn(e), fn(m)
            print(f"  {name:<12}{ev:>14.4f}{mv:>14.4f}{mv-ev:>+14.4f}")

    show("全 20 场(含市场回退 Elo 的 16 场)", "all")
    show("仅有市场数据的子集(对照)", "mkt_h")


if __name__ == "__main__":
    main()
