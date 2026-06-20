"""compare_reflection.py — 反思 vs 原 Elo:2026 已打场命中率/Brier/LogLoss 对比

公平性约定:
  - cutoff = 2026-06-10(开幕前一日,不含本届任何正赛场,防 Elo 泄漏)
  - 不传 odds_map/ou_map(用户问的是 Elo 预测;市场收盘价含结果信息,不公平)
  - 这样 source 全为 elo,probs 完全由 Eh/Ea 决定,
    唯一差别 = reflection 是否调整 Eh/Ea
  - reflection 调整来自 teams/*.yaml(elo_adjustments + reusable recent_lessons)
"""
import csv
import math
import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
sys.path.insert(0, ROOT)

import compute_elo
from webapp import predictor

CUTOFF = "2026-06-10"
LIVE = os.path.join(ROOT, "wc2026_live.csv")


def brier(probs, idx):
    p_h, p_d, p_a = probs
    if idx == 0:  # home_win
        return (p_h - 1) ** 2 + p_d ** 2 + p_a ** 2
    if idx == 1:  # draw
        return p_h ** 2 + (p_d - 1) ** 2 + p_a ** 2
    return p_h ** 2 + p_d ** 2 + (p_a - 1) ** 2


def logloss(probs, idx):
    p = max(probs[idx], 1e-12)
    return -math.log(p)


def result_idx(hs, as_):
    if hs > as_:
        return 0
    if hs < as_:
        return 2
    return 1


def main():
    elo, _, _ = compute_elo.build_elo(
        os.path.join(ROOT, "results.csv"), cutoff_date=CUTOFF)

    with open(LIVE, newline="", encoding="utf-8") as f:
        rows = [r for r in csv.reader(f) if r and r[0] != "date"]

    cols = ["date", "home_zh", "away_zh", "score", "result",
            "elo_p", "refl_p", "elo_pred", "refl_pred",
            "elo_hit", "refl_hit", "elo_adj"]
    lines = []
    stats = {k: {"n": 0, "hits": 0, "brier": 0.0, "ll": 0.0,
                 "score_hits": 0, "touched": 0}
             for k in ("elo", "refl")}

    for r in rows:
        date, home, away, hs, as_ = r[0], r[1], r[2], r[3], r[4]
        if hs == "NA" or as_ == "NA":
            continue
        ihs, ias = int(hs), int(as_)
        ri = result_idx(ihs, ias)

        pe = predictor.predict_one(home, away, elo, with_reflection=False)
        pr = predictor.predict_one(home, away, elo, with_reflection=True)
        if not pe or not pr:
            continue

        zh = predictor.EN_TO_ZH.get(home, home)
        za = predictor.EN_TO_ZH.get(away, away)

        def _argmax(p):
            return p.index(max(p))

        e_pred, r_pred = _argmax(pe["probs"]), _argmax(pr["probs"])
        e_hit, r_hit = e_pred == ri, r_pred == ri
        adj = pr.get("reflection_adj") or {"home": 0, "away": 0}
        touched = (adj["home"] != 0 or adj["away"] != 0)

        for k, p, hit in (("elo", pe, e_hit), ("refl", pr, r_hit)):
            s = stats[k]
            s["n"] += 1
            s["hits"] += int(hit)
            s["brier"] += brier(p["probs"], ri)
            s["ll"] += logloss(p["probs"], ri)
            s["score_hits"] += int(p["top_score"] == [ihs, ias])
        if touched:
            stats["refl"]["touched"] += 1

        def fmt_adj(a):
            h, w = a["home"], a["away"]
            if h == 0 and w == 0:
                return "—"
            return f"主{h:+d}/客{w:+d}"

        lines.append([
            date, f"{zh} vs {za}", f"{ihs}-{ias}",
            ["主胜", "平", "客胜"][ri],
            f"{pe['probs'][0]:.2f}/{pe['probs'][1]:.2f}/{pe['probs'][2]:.2f}",
            f"{pr['probs'][0]:.2f}/{pr['probs'][1]:.2f}/{pr['probs'][2]:.2f}",
            ["主胜", "平", "客胜"][e_pred], ["主胜", "平", "客胜"][r_pred],
            "✓" if e_hit else "✗", "✓" if r_hit else "✗",
            fmt_adj(adj),
        ])

    # ── 汇总 ──────────────────────────────────────────────────────
    w = max(len(str(x)) for row in [cols] + lines for x in row)
    print("=" * 70)
    print(f"2026 世界杯已打 {stats['elo']['n']} 场 | cutoff Elo @ {CUTOFF} | 纯 Elo 模式")
    print(f"其中反思实际改动 Elo 的场: {stats['refl']['touched']} 场")
    print("=" * 70)
    print("  ".join(c.ljust(w) for c in cols))
    for row in lines:
        print("  ".join(str(x).ljust(w) for x in row))
    print("-" * 70)
    print(f"{'指标':<14}{'原 Elo':>14}{'带反思':>14}{'Δ(反思-原)':>14}")
    for label, fn in [
        ("命中率", lambda s: s["hits"] / s["n"]),
        ("Brier(↓)", lambda s: s["brier"] / s["n"]),
        ("LogLoss(↓)", lambda s: s["ll"] / s["n"]),
        ("精确比分", lambda s: s["score_hits"] / s["n"]),
    ]:
        e, rr = fn(stats["elo"]), fn(stats["refl"])
        print(f"{label:<14}{e:>14.4f}{rr:>14.4f}{rr - e:>+14.4f}")


if __name__ == "__main__":
    main()
