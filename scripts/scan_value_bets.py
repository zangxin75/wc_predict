"""scan_value_bets.py — 系统 Elo vs 市场 devig:价值下注扫描(B 方案)

⚠️ 2026-06-19 新增:
  配 A 方案(scan_arbitrage.py)对比。B 方案是 value betting:
    对每场找 Elo 概率 > 市场 devig 概率最大的那个结果,
    若 edge ≥ 阈值,即为"价值下注"。

⚠️ 重要发现(校准后):
  calibrate_elo.py 跑 295 场历史 (1998-2022) 后:
    Elo vs 市场 相关系数 +0.87(主胜/客胜),+0.67(平)
    Brier=0.0237(已最优),Draw bias 仅 +0.004
  → Elo 模型已被最优校准,理论上不会显著跑赢市场。
  → 扫描出的大 edge 多在"冷门队"(Elo 数据稀疏区),实际不可信。

用法:
  ./.venv/bin/python scripts/scan_value_bets.py                    # 阈值 3%
  ./.venv/bin/python scripts/scan_value_bets.py --min-edge 0.05    # 阈值 5%
  ./.venv/bin/python scripts/scan_value_bets.py --output v.json    # 写 JSON

输出:
  1. Edge 分布统计(已赛 + 未赛)
  2. 未赛 Top 20 价值机会(按 Kelly 排序)
  3. 已赛回测(若 edge≥X 下注 1 单位,理论 ROI)
"""
import argparse
import csv
import json
import os
import sys

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8")

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
sys.path.insert(0, ROOT)

import compute_elo
import compute_odds as co
from webapp import predictor
from datetime import date

LIVE = os.path.join(ROOT, "wc2026_live.csv")
SCHED = os.path.join(ROOT, "wc2026_schedule.csv")
ZH = ["主胜", "平", "客胜"]


def devig(oh, od, oa):
    """市场赔率 → 三态去抽水概率(归一化)"""
    if not (oh and od and oa) or oh <= 1 or od <= 1 or oa <= 1:
        return None
    s = 1.0 / oh + 1.0 / od + 1.0 / oa
    return (1.0 / oh / s, 1.0 / od / s, 1.0 / oa / s)


def result_idx(hs, as_):
    return 0 if hs > as_ else (2 if hs < as_ else 1)


def load_played():
    out = []
    if not os.path.exists(LIVE):
        return out
    with open(LIVE, newline="", encoding="utf-8") as f:
        for r in csv.DictReader(f):
            try:
                out.append({
                    "date": r["date"],
                    "home": r["home_team"],
                    "away": r["away_team"],
                    "hs": int(r["home_score"]),
                    "as_": int(r["away_score"]),
                })
            except (ValueError, KeyError):
                continue
    return out


def load_upcoming():
    today = date.today().isoformat()
    out = []
    if not os.path.exists(SCHED):
        return out
    played_keys = {(p["home"], p["away"]) for p in load_played()}
    with open(SCHED, newline="", encoding="utf-8") as f:
        for r in csv.DictReader(f):
            try:
                hs = int(r.get("home_score") or 0)
                as_ = int(r.get("away_score") or 0)
            except ValueError:
                hs = as_ = 0
            if r["date"] >= today and hs == 0 and as_ == 0:
                key = (r["home_team"], r["away_team"])
                if key in played_keys:
                    continue
                out.append({
                    "date": r["date"],
                    "home": r["home_team"],
                    "away": r["away_team"],
                    "stage": r.get("stage", ""),
                })
    return out


def main():
    ap = argparse.ArgumentParser(
        description="系统 Elo vs 市场:价值下注扫描(B 方案)")
    ap.add_argument("--min-edge", type=float, default=0.03,
                    help="最小 edge 阈值(默认 0.03 = 3%%)")
    ap.add_argument("--output", help="可选:写 JSON")
    ap.add_argument("--show-top", type=int, default=20,
                    help="未赛显示前 N 个机会(默认 20)")
    args = ap.parse_args()

    elo, _, _ = compute_elo.build_elo(os.path.join(ROOT, "results.csv"))
    odds_map = co.load_odds()

    played = load_played()
    upcoming = load_upcoming()

    print("=" * 90)
    print(f"  价值下注扫描(纯 Elo vs 市场 devig)")
    print(f"  已赛 {len(played)} 场 | 未赛 {len(upcoming)} 场 | 阈值 edge ≥ {args.min_edge*100:.1f}%")
    print("=" * 90)

    # ── 收集所有 edge 数据 ────────────────────────────────────────
    all_edges = []
    for src, lst in [("已赛", played), ("未赛", upcoming)]:
        for m in lst:
            home, away = m["home"], m["away"]
            # 关键:不传 odds_map,强制纯 Elo
            pe = predictor.predict_one(home, away, elo, with_reflection=False)
            if not pe:
                continue
            ot = co.lookup_odds(odds_map, home, away, 2026)
            if not ot:
                continue
            oh, od, oa = ot
            pm = devig(oh, od, oa)
            if not pm:
                continue
            edges = [pe["probs"][i] - pm[i] for i in range(3)]
            all_edges.append({
                "src": src,
                "date": m["date"],
                "home": home,
                "away": away,
                "stage": m.get("stage", ""),
                "p_elo": list(pe["probs"]),
                "p_mkt": list(pm),
                "edges": edges,
                "max_edge": max(edges),
                "max_idx": max(range(3), key=lambda i: edges[i]),
                "odds": [oh, od, oa],
                "hs": m.get("hs"),
                "as_": m.get("as_"),
            })

    # ── Edge 分布 ────────────────────────────────────────────────
    if all_edges:
        evs = [e["max_edge"] for e in all_edges]
        print(f"\n[1] Edge 分布(共 {len(evs)} 场有 odds):")
        print(f"  平均: {sum(evs)/len(evs)*100:+.2f}%  |  中位: {sorted(evs)[len(evs)//2]*100:+.2f}%")
        print(f"  最大: {max(evs)*100:+.2f}%  |  最小: {min(evs)*100:+.2f}%")
        for thr in [0.01, 0.02, 0.03, 0.05, 0.10]:
            print(f"  ≥{thr*100:.0f}%: {sum(1 for e in evs if e >= thr)} 场")

    # ── 未赛 Top N 价值机会(按 Kelly 排序) ──────────────────────
    value_bets = []
    for e in all_edges:
        if e["src"] != "未赛" or e["max_edge"] < args.min_edge:
            continue
        odds = e["odds"][e["max_idx"]]
        p = e["p_elo"][e["max_idx"]]
        b = odds - 1
        kelly = max(0, (b * p - (1 - p)) / b) if b > 0 else 0
        e["kelly"] = kelly
        e["picked_odds"] = odds
        value_bets.append(e)

    value_bets.sort(key=lambda x: -x["kelly"])

    print(f"\n[2] 未赛价值机会(共 {len(value_bets)} 场 ≥ {args.min_edge*100:.1f}%, "
          f"按 Kelly 降序,显示前 {args.show_top}):")
    print("-" * 95)
    print(f"{'日期':<12}{'比赛':<24}{'Elo':<14}{'市场':<14}{'maxEdge':<8}"
          f"{'下注':<6}{'赔率':<6}{'Kelly':<8}")
    for v in value_bets[:args.show_top]:
        elo_s = "/".join(f"{x:.2f}" for x in v["p_elo"])
        mkt_s = "/".join(f"{x:.2f}" for x in v["p_mkt"])
        zh = predictor.EN_TO_ZH.get(v["home"], v["home"])
        za = predictor.EN_TO_ZH.get(v["away"], v["away"])
        print(f"{v['date']:<12}{zh+' vs '+za:<22}{elo_s:<14}{mkt_s:<14}"
              f"{v['max_edge']*100:+5.1f}%  {ZH[v['max_idx']]:<4}"
              f"{v['picked_odds']:>5.2f}  {v['kelly']*100:>5.1f}%")

    # ── 已赛回测(纯 Elo edge 在实际结果上) ──────────────────────
    played_with_data = [e for e in all_edges if e["src"] == "已赛"
                        and e.get("hs") is not None]
    print(f"\n[3] 已赛回测({len(played_with_data)} 场有 odds + 比分):")
    if played_with_data:
        # edge on actual result
        edge_on_actual = []
        for e in played_with_data:
            ri = result_idx(e["hs"], e["as_"])
            edge_on_actual.append(e["edges"][ri])
        n_pos = sum(1 for e in edge_on_actual if e > 0)
        n_neg = sum(1 for e in edge_on_actual if e < 0)
        avg = sum(edge_on_actual) / len(edge_on_actual)
        print(f"  Elo 看好实际结果的 edge 分布:")
        print(f"    平均: {avg*100:+.2f}%  | 正 edge: {n_pos} 场  |  负 edge: {n_neg} 场")
        print(f"  ⚠ 注意:fetch 时比赛已开赛/结束,赔率均已撤盘 → 这部分回测数据缺失,")
        print(f"           当前 0 场已赛能完整跑回测。等小组赛打完才能做真实 ROI 验证。")

    # ── JSON 输出 ────────────────────────────────────────────────
    if args.output:
        with open(args.output, "w", encoding="utf-8") as f:
            json.dump({
                "scanned_at": date.today().isoformat(),
                "min_edge": args.min_edge,
                "n_played": len(played),
                "n_upcoming": len(upcoming),
                "n_value_bets": len(value_bets),
                "top": value_bets[:args.show_top],
            }, f, ensure_ascii=False, indent=2, default=str)
        print(f"\n✓ 结果已写到 {args.output}")


if __name__ == "__main__":
    main()