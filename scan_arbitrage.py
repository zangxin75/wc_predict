"""scan_arbitrage.py — 扫 WC2026 多博彩报价,找严格搬砖机会

⚠️ 2026-06-19 新增:多源覆盖(必须 Pinnacle + Betfair + Bet365 等
  多家)开启后,才能跑这个扫描。

原理:
  同一场比赛,不同博彩开出不同赔率。如果三选一(主胜/平/客胜)的
  倒数之和小于 1(1/oh + 1/od + 1/oa < 1),就存在无风险套利:
    - 用 book_H 买主胜(赔率 oh)
    - 用 book_D 买平(赔率 od)
    - 用 book_A 买客胜(赔率 oa)
    - 总注金 S = 1/oh + 1/od + 1/oa
    - 不论赛果,每 1 块注金回收 1 块(保证)
    - 利润 = (1 - S) / S × 总注金

策略:
  对每场比赛,先找"三态最佳赔率"(每个结果的最优价,可能来自不同博彩),
  若倒数之和 < 1 → 报告套利机会,含 stake 分配和保证利润(以 100 块本金计)。
"""
import argparse
import math
import os
import sys

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8")

import fetch_odds_api as foa  # 复用 fetch_odds()


def best_per_outcome(match, book_keys=None):
    """对一场比赛,聚合所有博彩的 h2h 报价 → (best_h, book_h, best_d, book_d, best_a, book_a)。

    book_keys: 可选 set,只保留这些博彩(如 {"pinnacle", "betfair_ex_uk"} 限定锐盘)。
                None 或空 = 所有博彩。

    Returns None 若任一态缺。
    """
    home_name = match.get("home_team")
    away_name = match.get("away_team")
    if not (home_name and away_name):
        return None
    best = {"h": (None, None), "d": (None, None), "a": (None, None)}
    for bm in match.get("bookmakers", []) or []:
        bk = bm.get("key", "")
        if book_keys and bk not in book_keys:
            continue
        for mkt in bm.get("markets", []):
            if mkt.get("key") != "h2h":
                continue
            for o in mkt.get("outcomes", []):
                price = o.get("price")
                if not price or price <= 1:
                    continue
                name = o.get("name", "")
                if name == "Draw":
                    key = "d"
                elif name == home_name:
                    key = "h"
                elif name == away_name:
                    key = "a"
                else:
                    continue
                cur_price, _ = best[key]
                if cur_price is None or price > cur_price:
                    best[key] = (price, bm.get("key", ""))
    if not (best["h"][0] and best["d"][0] and best["a"][0]):
        return None
    return best["h"][0], best["h"][1], best["d"][0], best["d"][1], best["a"][0], best["a"][1]


def best_totals(match, book_keys=None):
    """对一场比赛,聚合所有博彩的 totals 报价(分 line)→ {line: (best_over, book_o, best_under, book_u)}"""
    out = {}
    for bm in match.get("bookmakers", []) or []:
        bk = bm.get("key", "")
        if book_keys and bk not in book_keys:
            continue
        for mkt in bm.get("markets", []):
            if mkt.get("key") != "totals":
                continue
            for o in mkt.get("outcomes", []):
                price = o.get("price")
                point = o.get("point")
                if not (price and point) or price <= 1:
                    continue
                name = o.get("name", "")
                if name not in ("Over", "Under"):
                    continue
                if point not in out:
                    out[point] = {"o": (None, None), "u": (None, None)}
                cur, _ = out[point]["o" if name == "Over" else "u"]
                if cur is None or price > cur:
                    out[point]["o" if name == "Over" else "u"] = (price, bk)
    # 过滤完整配对
    return {l: v for l, v in out.items() if v["o"][0] and v["u"][0]}


def arb_1x2(home, away, commence, best):
    """1X2 套利检测 + stake 计算(总注金 100)。"""
    ph, bh, pd, bd, pa, ba = best
    s = 1.0 / ph + 1.0 / pd + 1.0 / pa
    if s >= 1.0:
        return None
    bank = 100.0
    # stake 分配:让每条线 payout 相等
    payout = bank / s
    stake_h = payout / ph
    stake_d = payout / pd
    stake_a = payout / pa
    profit = payout - bank
    return {
        "type": "1X2",
        "match": f"{home} vs {away}",
        "commence": commence,
        "sum": s,
        "margin_pct": (1 - s) * 100,
        "best": [
            ("H", ph, bh, stake_h),
            ("D", pd, bd, stake_d),
            ("A", pa, ba, stake_a),
        ],
        "bank": bank,
        "profit": profit,
    }


def arb_ou(home, away, commence, line, ov, ob, uv, ub):
    """OU 套利检测 + stake 计算(总注金 100)。"""
    s = 1.0 / ov + 1.0 / uv
    if s >= 1.0:
        return None
    bank = 100.0
    payout = bank / s
    stake_o = payout / ov
    stake_u = payout / uv
    profit = payout - bank
    return {
        "type": f"OU{line}",
        "match": f"{home} vs {away}",
        "commence": commence,
        "sum": s,
        "margin_pct": (1 - s) * 100,
        "best": [
            ("Over", ov, ob, stake_o),
            ("Under", uv, ub, stake_u),
        ],
        "bank": bank,
        "profit": profit,
    }


def print_opp(o):
    print(f"\n  🎯 套利机会: {o['match']} ({o['commence'][:16]})")
    print(f"     类型: {o['type']}    倒数和: {o['sum']:.4f}    利润率: {o['margin_pct']:.2f}%")
    print(f"     总注 {o['bank']:.0f} → 保证利润 {o['profit']:.2f}")
    print(f"     {'结果':<8}{'赔率':>8}  @  {'博彩':<16}{'注金':>10}")
    for label, price, book, stake in o["best"]:
        print(f"     {label:<8}{price:>8.3f}  @  {book:<16}{stake:>10.2f}")


def main():
    ap = argparse.ArgumentParser(
        description="扫 WC2026 严格搬砖机会(1X2 + OU)")
    ap.add_argument("--regions", default="eu,uk", help="博彩区域")
    ap.add_argument("--min-margin", type=float, default=0.0,
                    help="最小利润率%%过滤(默认 0,显示所有 < 1 的机会)")
    ap.add_argument("--markets", default="h2h,totals",
                    help="市场(默认 h2h,totals)")
    ap.add_argument("--books", default="",
                    help="博彩白名单,逗号分隔(如 pinnacle,betfair_ex_uk,matchbook)。空=全部")
    ap.add_argument("--output", help="可选:写 JSON 到指定文件")
    args = ap.parse_args()

    book_keys = set(k.strip() for k in args.books.split(",") if k.strip()) or None
    if book_keys:
        print(f"  ⚙ 限定博彩: {sorted(book_keys)}")

    api_key = foa.get_api_key()
    print("=" * 60)
    print(f"  扫 WC2026 严格搬砖机会(The Odds API · regions={args.regions})")
    print(f"  最小利润率过滤: {args.min_margin}%")
    print("=" * 60)

    try:
        data, headers = foa.fetch_odds(api_key, regions=args.regions,
                                       markets=args.markets)
    except Exception as e:
        print(f"  ✗ 抓取失败: {e}")
        sys.exit(1)

    remaining = headers.get("x-requests-remaining", "?")
    used = headers.get("x-requests-used", "?")
    print(f"  ✓ HTTP 200, 配额: 已用 {used} / 剩余 {remaining}")
    print(f"  ✓ 扫描 {len(data)} 场比赛...")

    opps_1x2 = []
    opps_ou = []
    no_data = 0

    for m in data:
        home = m.get("home_team", "")
        away = m.get("away_team", "")
        commence = m.get("commence_time", "")
        # 1X2
        b = best_per_outcome(m, book_keys)
        if b:
            o = arb_1x2(home, away, commence, b)
            if o and o["margin_pct"] >= args.min_margin:
                opps_1x2.append(o)
        # OU(分 line)
        for line, v in best_totals(m, book_keys).items():
            o = arb_ou(home, away, commence, line, v["o"][0], v["o"][1],
                       v["u"][0], v["u"][1])
            if o and o["margin_pct"] >= args.min_margin:
                opps_ou.append(o)
        if not b and not best_totals(m, book_keys):
            no_data += 1

    # 排序:利润率降序
    opps_1x2.sort(key=lambda x: -x["margin_pct"])
    opps_ou.sort(key=lambda x: -x["margin_pct"])

    print("-" * 60)
    print(f"  1X2 机会: {len(opps_1x2)} 个")
    print(f"  OU  机会: {len(opps_ou)} 个")
    print(f"  无数据: {no_data} 场")

    if opps_1x2:
        print("\n" + "=" * 60)
        print("  1X2 套利(按利润率降序)")
        print("=" * 60)
        for o in opps_1x2:
            print_opp(o)
    if opps_ou:
        print("\n" + "=" * 60)
        print("  OU 套利(按利润率降序)")
        print("=" * 60)
        for o in opps_ou:
            print_opp(o)
    if not opps_1x2 and not opps_ou:
        print("\n  ℹ 当前无套利机会(博彩已充分对齐)。这是常态——真套利窗口通常 < 1%。")

    # 可选 JSON 输出
    if args.output:
        import json
        with open(args.output, "w", encoding="utf-8") as f:
            json.dump({"1x2": opps_1x2, "ou": opps_ou,
                       "remaining": remaining, "used": used},
                      f, ensure_ascii=False, indent=2)
        print(f"\n✓ 结果已写到 {args.output}")


if __name__ == "__main__":
    main()
