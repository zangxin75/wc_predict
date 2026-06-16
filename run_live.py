"""run_live.py — 2026 实时预测一键闭环

串联完整数据流:
  fetch_wc2026  →  update_live(--apply)  →  重算 Elo(cutoff=今日)
                                              →  预测"下一轮"未赛

设计:调度现有模块,不改其接口。每天跑一次即可保持预测最新。
  · fetch:ESPN 抓取(带缓存),产 wc2026_live.csv + wc2026_schedule.csv
  · update:回写已打结果到 results.csv(去重 + NA 补全 + 冲突报警)
  · 重算:用今日为 cutoff 重建 Elo(含全部已回写结果)
  · 预测:从 schedule 取下一轮未赛,输出三态概率 + 首选比分

用法:
    python run_live.py                    # 全流程(抓→回→算→预测下一轮)
    python run_live.py --no-fetch         # 跳过抓取(用缓存数据)
    python run_live.py --no-apply         # 只 dry-run 回写,不改 results.csv
    python run_live.py --next 8           # 预测最近 8 场未赛(默认 8)
"""

import argparse
import csv
import datetime
import os
import subprocess
import sys

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8")

import compute_elo
import compute_odds
import wc_predict
import update_live

HERE = os.path.dirname(os.path.abspath(__file__))
RESULTS = os.path.join(HERE, "results.csv")
SCHED = os.path.join(HERE, "wc2026_schedule.csv")

# 东道主场 Elo 加成(与 verify_2026 一致)
HOST_ADV = {"Mexico": 90, "United States": 70, "Canada": 40}


def upcoming_matches(sched_path=SCHED, today=None, limit=8):
    """★【下一轮预测筛选:留作设计点】从 schedule 取"下一轮"未赛。

    已实现的基线:日期升序,跳过 < today(已开赛),取前 limit 场。
    东道主场非中立时,预测会加 HOST_ADV(见 predict_upcoming)。

    可调设计(影响输出密度与价值):
      · limit:默认 8(约 1-2 个比赛日)。太小=信息不足,太大=噪声多。
      · 是否按"比赛日"聚合(同日打包输出)而非场数?
      · 是否只输出"东道主场"(信息量最大,加成最显著)?
      · cutoff 用 today 还是 today+1(避免漏掉今日开打)?
    """
    if not os.path.exists(sched_path):
        return []
    today = today or datetime.date.today().isoformat()
    rows = []
    with open(sched_path, newline="", encoding="utf-8") as f:
        for r in csv.reader(f):
            if r[0] == "date" or len(r) < 6:
                continue
            if r[0] >= today:
                rows.append(r)
    rows.sort(key=lambda r: (r[0], r[1]))
    return rows[:limit]


def predict_upcoming(matches, elo, neutral_default=True, odds_map=None):
    """对未赛列表逐场预测,返回 [(date, home, away, [hw,dr,aw], top_score, adv, source), ...]。

    异源优先:若该场在 odds_map 有收盘报价,1X2 概率用 devig 后的市场价(backtest 证明 Δ=−0.066
    优于 Elo);无则回退 Elo。scoreline(首选比分)始终用 Elo 推断作显示(odds 不含比分)。
    source ∈ {"odds","elo","miss"}。东道主场(home 在 HOST_ADV 且非中立)加主场 Elo。
    """
    if odds_map is None:
        try:
            odds_map = compute_odds.load_odds()
        except Exception:
            odds_map = {}
    out = []
    for r in matches:
        date, home, away = r[0], r[1], r[2]
        eh, ea = elo.get(home), elo.get(away)
        if eh is None or ea is None:
            out.append((date, home, away, None, None, 0, "miss"))
            continue
        adv = HOST_ADV.get(home, 0) if not neutral_default else 0
        e_hw, e_dr, e_aw, _, top = wc_predict.predict_from_elo(eh, ea, home_adv_elo=adv)
        year = int(date[:4]) if date[:4].isdigit() else None
        oh = compute_odds.lookup_odds(odds_map, home, away, year=year) if year else None
        if oh is not None:
            hw, dr, aw = compute_odds.devig_power(*oh)   # 市场价优于 Elo,决策用 odds
            out.append((date, home, away, [hw, dr, aw], top[0][0], adv, "odds"))
        else:
            out.append((date, home, away, [e_hw, e_dr, e_aw], top[0][0], adv, "elo"))
    return out


def main():
    ap = argparse.ArgumentParser(description="2026 实时预测一键闭环")
    ap.add_argument("--no-fetch", action="store_true", help="跳过 ESPN 抓取")
    ap.add_argument("--no-apply", action="store_true", help="update 只 dry-run")
    ap.add_argument("--next", type=int, default=8, help="预测最近 N 场未赛(默认8)")
    args = ap.parse_args()

    today = datetime.date.today().isoformat()
    print("=" * 70)
    print(f"  2026 实时预测闭环  (today={today})")
    print("=" * 70)

    # 1) 抓取(子进程,保持输出隔离)
    if args.no_fetch:
        print("  [1/4] 跳过抓取(--no-fetch)")
        if not os.path.exists(SCHED):
            sys.exit("✗ 无 schedule 数据,先去掉 --no-fetch 跑一次")
    else:
        print("  [1/4] ESPN 抓取...")
        print("  " + "-" * 66)
        rc = subprocess.call([sys.executable, "fetch_wc2026.py"])
        if rc != 0:
            sys.exit(f"✗ fetch_wc2026 失败 (rc={rc})")
        print("  " + "-" * 66)

    # 2) 回写(子进程)
    print(f"\n  [2/4] 回写已打结果...  [{'DRY-RUN' if args.no_apply else 'APPLY'}]")
    print("  " + "-" * 66)
    if args.no_apply:
        new, na_upd, conf, dup = update_live.diff_live()
        print(f"  新增 {len(new)} 补NA {len(na_upd)} 冲突 {len(conf)} 幂等 {dup}")
    else:
        rc = subprocess.call([sys.executable, "update_live.py", "--apply"])
        if rc != 0:
            sys.exit(f"✗ update_live 失败 (rc={rc})")
    print("  " + "-" * 66)

    # 3) 重算 Elo(cutoff=今日,含全部已回写结果)
    print(f"\n  [3/4] 重算 Elo(cutoff={today})...")
    elo, n_match, _ = compute_elo.build_elo(RESULTS, cutoff_date=today)
    print(f"        含 {n_match:,} 场,{len(elo)} 队")

    # 4) 下一轮预测
    print(f"\n  [4/4] 下一轮未赛预测(最近 {args.next} 场):")
    ups = upcoming_matches(limit=args.next, today=today)
    if not ups:
        print("        无未赛(赛程可能已结束或数据缺失)。")
        print("=" * 70)
        return
    preds = predict_upcoming(ups, elo)
    zh = wc_predict.EN_TO_ZH
    OUT = {0: "主胜", 1: "平", 2: "客胜"}
    print(f"  {'日期':<11}{'对阵':<26}{'主/平/客':<14}{'预测':<5}{'比分':<6}{'主场':<5}{'来源'}")
    print("  " + "-" * 74)
    n_odds = n_elo = n_miss = 0
    SRC = {"odds": "报价", "elo": "Elo"}
    for date, home, away, probs, top, adv, src in preds:
        name = f"{zh.get(home, home)} vs {zh.get(away, away)}"
        if probs is None:
            n_miss += 1
            print(f"  {date:<11}{name:<26}{'Elo缺失':<14}")
            continue
        n_odds += src == "odds"
        n_elo += src == "elo"
        pi = probs.index(max(probs))
        ps = f"{probs[0]*100:>3.0f}/{probs[1]*100:>2.0f}/{probs[2]*100:>3.0f}"
        site = f"+{adv}" if adv else "中立"
        print(f"  {date:<11}{name:<24}{ps:<16}{OUT[pi]:<5}{top[0]}-{top[1]:<3}{site:<6}{SRC.get(src, src)}")
    print("=" * 70)
    print(f"  概率来源:{n_odds} 场用市场报价(devig,更准),{n_elo} 场用 Elo 回退"
          + (f",{n_miss} 场 Elo 缺失。" if n_miss else "。"))
    print("  说明:有当届收盘报价时 1X2 概率优先用市场价(backtest 108场证明 Δ=−0.066 优于 Elo);")
    print("  无报价时回退 Elo。比分为 Elo+泊松+Dixon-Coles 首选比分(显示用,odds 不含比分)。")


if __name__ == "__main__":
    main()
