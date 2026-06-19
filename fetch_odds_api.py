"""fetch_odds_api.py — 抓 WC2026 1X2 + OU from The Odds API (the-odds-api.com)

⚠️ 2026-06-19 新增:多源覆盖第一步,主源 The Odds API(Pinnacle + Betfair
  Exchange + Bet365 等),辅源 fetch_wc2026_odds.py(BetExplorer,只填空)。

设计要点:
  1. 优先选最"sharp"的博彩(1/oh + 1/od + 1/oa 最小者,抽水最低)
  2. 每个 match 取一个 bookmaker(主 h2h),OU 同理
  3. 复用 fetch_wc2026_odds.py 的 merge_to_csv 保留逻辑(2026 已结束
     盘口不丢)
  4. API key 走环境变量 THE_ODDS_API_KEY(用户注册免费层即可)

Quota 估算(免费 500 req/月):
  - h2h + totals × 2 regions = 4 req / 全量抓一次
  - 每日 1 次 → 月 120 req,留 380 req 余量
"""
import argparse
import csv
import math
import os
import sys
import time

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8")

import requests

import compute_odds as co
import fetch_wc2026_odds as fw  # 复用 merge_to_csv

HERE = os.path.dirname(os.path.abspath(__file__))
OUT_CSV = co.OUT_CSV
OU_CSV = co.OU_CSV
ENDPOINT = "https://api.the-odds-api.com/v4/sports/soccer_fifa_world_cup/odds"
SPORT_KEY = "soccer_fifa_world_cup"

UA = {
    "User-Agent": "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 "
                  "(KHTML, like Gecko) Chrome/120.0 Safari/537.36",
    "Accept": "application/json",
}


def get_api_key():
    """从 env 读 API key,缺失则明确报错(避免 silent 失败浪费 quota)。"""
    key = os.environ.get("THE_ODDS_API_KEY", "").strip()
    if not key:
        print("=" * 60)
        print("⚠ THE_ODDS_API_KEY 未设置")
        print("  注册: https://the-odds-api.com (免费 500 req/月)")
        print("  设置: export THE_ODDS_API_KEY=your_key_here")
        print("  或:   写进 .env 文件再用 direnv/source")
        print("=" * 60)
        sys.exit(1)
    return key


def fetch_odds(api_key, regions="eu,uk", markets="h2h,totals",
               odds_format="decimal"):
    """GET v4/sports/{sport}/odds → (data, headers) 或 raise。"""
    params = {
        "apiKey": api_key,
        "regions": regions,
        "markets": markets,
        "oddsFormat": odds_format,
        "dateFormat": "iso",
    }
    r = requests.get(ENDPOINT, params=params, headers=UA, timeout=30)
    if r.status_code == 401:
        raise RuntimeError("API key 无效(401)。检查 THE_ODDS_API_KEY")
    if r.status_code == 429:
        raise RuntimeError("quota 用尽(429)。免费层 500 req/月,等下月或付费")
    r.raise_for_status()
    return r.json(), dict(r.headers)


def vig_1x2(oh, od, oa):
    """抽水率(1/oh + 1/od + 1/oa - 1),越小越 sharp。None → math.inf。"""
    if not (oh and od and oa) or oh <= 1 or od <= 1 or oa <= 1:
        return math.inf
    return 1.0 / oh + 1.0 / od + 1.0 / oa - 1.0


def pick_sharpest_h2h(bookmakers):
    """bookmakers: API 返回的 bookmakers[] → (home, draw, away, book_key) 或 None。

    策略:遍历所有 book,挑 h2h 且 vig 最小者。h2h 三态对应 home/draw/away。
    """
    best = None
    best_vig = math.inf
    for bm in bookmakers or []:
        for mkt in bm.get("markets", []):
            if mkt.get("key") != "h2h":
                continue
            outcomes = {o["name"]: o["price"] for o in mkt.get("outcomes", [])}
            # 1X2 三个 key 名称可能为 "Home/Draw/Away" 或具体队名
            # outcomes 是 {"Mexico": 1.5, "Draw": 3.2, "South Korea": 5.0}
            # 用启发式找 draw(draw 必有)
            draw = None
            home = None
            away = None
            for name, price in outcomes.items():
                ln = name.lower()
                if ln == "draw":
                    draw = price
                else:
                    # 假设非 Draw 的第一个是 home,第二个是 away
                    if home is None:
                        home = price
                    else:
                        away = price
            if not (home and draw and away):
                continue
            v = vig_1x2(home, draw, away)
            if v < best_vig:
                best_vig = v
                best = (home, draw, away, bm.get("key", ""))
    return best


def pick_sharpest_totals(bookmakers):
    """bookmakers → (over, under, line, book_key) 或 None。

    totals market 的 outcomes 通常 2 个:Over 2.5 / Under 2.5
    API 用不同格式:有的把 line 放在 outcome.description,有的放在 market 的 last_update。
    我们用 outcome["name"] 解析 "Over" / "Under",point 用 outcome["point"](API v4 支持)。
    """
    best = None
    best_vig = math.inf
    for bm in bookmakers or []:
        for mkt in bm.get("markets", []):
            if mkt.get("key") != "totals":
                continue
            outcomes = mkt.get("outcomes", [])
            over = under = line = None
            for o in outcomes:
                if o.get("name") == "Over":
                    over = o["price"]
                    line = o.get("point")
                elif o.get("name") == "Under":
                    under = o["price"]
                    if line is None:
                        line = o.get("point")
            if not (over and under and line):
                continue
            if over <= 1 or under <= 1:
                continue
            v = 1.0 / over + 1.0 / under - 1.0
            if v < best_vig:
                best_vig = v
                best = (over, under, line, bm.get("key", ""))
    return best


def parse_match(m):
    """一个 match dict → (home, away, score, [oh,od,oa], [(line,ov,uv,book)])。"""
    home_raw = m.get("home_team", "")
    away_raw = m.get("away_team", "")
    home = co.normalize_team(home_raw)
    away = co.normalize_team(away_raw)
    if not home or not away:
        return None
    commence = m.get("commence_time", "")
    date = commence[:10] if commence else ""

    h2h = pick_sharpest_h2h(m.get("bookmakers", []))
    odds3 = list(h2h[:3]) if h2h else None

    tot = pick_sharpest_totals(m.get("bookmakers", []))
    ou = [tot] if tot else []  # [(line, over, under, book)]

    return home, away, date, odds3, ou


def main():
    ap = argparse.ArgumentParser(
        description="抓 WC2026 1X2 + OU from The Odds API")
    ap.add_argument("--dry", action="store_true", help="只打印不写 CSV")
    ap.add_argument("--regions", default="eu,uk",
                    help="博彩区域(默认 eu,uk;Pinnacle 在 eu, Betfair Exchange 在 uk)")
    ap.add_argument("--markets", default="h2h,totals",
                    help="市场(默认 h2h,totals;若只关心 1X2 可改 h2h)")
    args = ap.parse_args()

    api_key = get_api_key()

    print("=" * 60)
    print(f"  抓取 WC2026 ({SPORT_KEY}) from The Odds API")
    print(f"  regions={args.regions}  markets={args.markets}")
    print("=" * 60)

    t0 = time.time()
    try:
        data, headers = fetch_odds(api_key, regions=args.regions,
                                   markets=args.markets)
    except Exception as e:
        print(f"  ✗ 抓取失败: {e}")
        sys.exit(1)
    elapsed = time.time() - t0

    remaining = headers.get("x-requests-remaining", "?")
    used = headers.get("x-requests-used", "?")
    print(f"  ✓ HTTP 200, 耗时 {elapsed:.1f}s, 配额: 已用 {used} / 剩余 {remaining}")
    print(f"  ✓ 返回 {len(data)} 场比赛")

    rows_1x2 = []  # [(home, away, score, oh, od, oa)]
    rows_ou = []   # [(home, away, oh, od, oa, ou_over, ou_under)]
    skipped = []

    for m in data:
        parsed = parse_match(m)
        if not parsed:
            skipped.append(m.get("id", "?"))
            continue
        home, away, date, odds3, ou = parsed
        if odds3:
            rows_1x2.append((home, away, "", odds3[0], odds3[1], odds3[2]))
            info = f"  ✓ {home:22s} vs {away:22s}  1X2 {odds3[0]:.2f}/{odds3[1]:.2f}/{odds3[2]:.2f}"
            if ou:
                line, ov, uv, _ = ou[0]
                rows_ou.append((home, away, odds3[0], odds3[1], odds3[2], ov, uv))
                info += f"  OU{line} {ov:.2f}/{uv:.2f}"
            print(info)
        else:
            print(f"  ✗ {home:22s} vs {away:22s}  无 1X2")

    print("-" * 60)
    print(f"  合计: 1X2 {len(rows_1x2)} 场, OU {len(rows_ou)} 场")
    if skipped:
        print(f"  跳过 {len(skipped)} 场(无法解析)")

    if args.dry:
        print("  --dry,不写 CSV")
        return

    if rows_1x2:
        # 复用 fetch_wc2026_odds.merge_to_csv(保留 2026 已结束盘口)
        added, kept = fw.merge_to_csv(rows_1x2)
        print(f"✓ odds_wc.csv: 2026 写入 {added} 场,保留 {kept} 场")

    if rows_ou:
        # OU 用 fetch_flashscore.merge_ou(同样有保留逻辑)
        import fetch_flashscore as ff
        added_o, kept_o = ff.merge_ou(rows_ou)
        print(f"✓ ou_wc.csv: 2026 写入 {added_o} 场,保留 {kept_o} 场")


if __name__ == "__main__":
    main()
