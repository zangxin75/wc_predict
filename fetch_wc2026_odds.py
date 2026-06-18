"""fetch_wc2026_odds.py — 抓 WC2026 1X2 → odds_wc.csv

⚠️ 2026-06-19 大改:
  1. BetExplorer URL 路径从 world-cup-2026 → world-championship-2026
     (原路径被 301 → 首页,抓取静默拿到 0 场)
  2. 赔率容器选择器从 li.table-main__oddsLi → td.table-main__odds > button[data-odd]
  3. 赔率直接在静态 HTML,无需 Playwright,改用 requests + BS4
     (快 ~10x,反爬风险低,失败模式更可诊断)

保持原函数签名(main / --dry / merge_to_csv 不变),update_odds.sh 不用动。
对 OU 抓取仍需 Playwright(详情页 OU 是异步加载),见 fetch_wc2026_ou.py。
"""
import argparse
import csv
import json
import os
import re
import sys

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8")

import requests
from bs4 import BeautifulSoup

import compute_odds as co

HERE = os.path.dirname(os.path.abspath(__file__))
OUT_CSV = co.OUT_CSV
CACHE = os.path.join(HERE, "webapp", "data", "cache.json")
OVERVIEW = "https://www.betexplorer.com/football/world/world-championship-2026/"

UA = {
    "User-Agent": "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 "
                  "(KHTML, like Gecko) Chrome/120.0 Safari/537.36",
    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
    "Accept-Language": "en-US,en;q=0.9",
    "Accept-Encoding": "gzip, deflate, br",
    "Connection": "keep-alive",
}


def load_wc_teams():
    """参赛队白名单:从 webapp cache.json 提(用于过滤 BetExplorer 混入的非 WC 比赛)。"""
    if not os.path.exists(CACHE):
        return None
    try:
        with open(CACHE, encoding="utf-8") as f:
            d = json.load(f)
    except Exception:
        return None
    teams = set()
    for grp, tl in (d.get("groups") or {}).items():
        for row in tl:
            if isinstance(row, dict) and "team" in row:
                teams.add(co.normalize_team(row["team"]))
    return teams or None


def parse_row(tr):
    """一个 <tr> → (home, away, score or None, [oh,od,oa] or None)。

    新版 DOM 结构(2026-06-19 确认):
      <tr>
        <td class="table-main__datetime">...</td>
        <td><a class="in-match"><span>HOME</span> - <span>AWAY</span></a></td>
        <td class="h-text-center">  ← 已赛有比分 "1 : 2",未赛为空
        <td class="table-main__odds"><button data-odd="..."></button></td>  ×3
      </tr>
    前 3 个 data-odd 严格按 H/D/A 顺序。
    """
    a = tr.find("a", class_="in-match")
    if not a:
        return None
    spans = a.find_all("span")
    if len(spans) < 2:
        return None
    home = co.normalize_team(spans[0].get_text(strip=True))
    away = co.normalize_team(spans[-1].get_text(strip=True))
    if len(home) < 2 or len(away) < 2:
        return None

    # 比分:已赛 td.table-main__score 含 "1 : 2";未赛 td.h-text-center 空
    score = None
    score_td = tr.find("td", class_=re.compile(r"table-main__score"))
    if score_td:
        s = score_td.get_text(strip=True).replace(" ", "")
        if re.match(r"^\d+:\d+$", s):
            score = s
    # 若无 score td 但有 h-text-center,试提取(部分老 DOM 残留)
    if score is None:
        center_td = tr.find("td", class_="h-text-center")
        if center_td:
            s = center_td.get_text(strip=True).replace(" ", "")
            if re.match(r"^\d+:\d+$", s):
                score = s

    # 1X2:tr 内前 3 个 button[data-odd],严格 H/D/A 顺序
    odds = []
    for btn in tr.find_all("button", attrs={"data-odd": True}):
        try:
            odds.append(float(btn["data-odd"]))
        except (ValueError, KeyError):
            continue
        if len(odds) >= 3:
            break

    return home, away, score, odds[:3] if len(odds) >= 3 else None


def fetch_page(path):
    """拉概览页(results / fixtures) → BS4 soup,失败抛。"""
    url = OVERVIEW + path
    r = requests.get(url, headers=UA, timeout=30)
    r.raise_for_status()
    return BeautifulSoup(r.text, "html.parser")


def harvest(path):
    """拉一页 → [(home, away, score or None, odds3 or None)]。
    不做白名单过滤(留给 main),便于调试输出原始行数。"""
    soup = fetch_page(path)
    rows = []
    for tr in soup.find_all("tr"):
        result = parse_row(tr)
        if result is None:
            continue
        home, away, score, odds3 = result
        rows.append((home, away, score, odds3))
    return rows


def merge_to_csv(rows_2026):
    """rows_2026: [(home,away,score,oh,od,oa)] → merge 进 odds_wc.csv(只动 2026)。"""
    by_key = {}
    if os.path.exists(OUT_CSV):
        with open(OUT_CSV, newline="", encoding="utf-8") as f:
            for r in csv.DictReader(f):
                by_key[(r["year"], r["home"], r["away"])] = r
    # 清掉旧 2026(本次全量替换),保留其他届
    merged = {k: v for k, v in by_key.items() if k[0] != "2026"}
    added = 0
    for home, away, score, oh, od, oa in rows_2026:
        merged[("2026", home, away)] = {
            "year": "2026", "date": "", "home": home, "away": away,
            "score": score or "", "odds_h": f"{oh:.2f}",
            "odds_d": f"{od:.2f}", "odds_a": f"{oa:.2f}",
        }
        added += 1
    fields = ["year", "date", "home", "away", "score", "odds_h", "odds_d", "odds_a"]
    all_rows = sorted(merged.values(),
                      key=lambda m: (str(m["year"]), m["home"], m["away"]))
    with open(OUT_CSV, "w", newline="", encoding="utf-8") as f:
        w = csv.DictWriter(f, fieldnames=fields)
        w.writeheader()
        for r in all_rows:
            w.writerow({fn: r.get(fn, "") for fn in fields})
    kept_other = len(all_rows) - added
    return added, kept_other


def main():
    ap = argparse.ArgumentParser(
        description="抓 2026 世界杯 1X2 报价(BetExplorer, requests+BS4)")
    ap.add_argument("--dry", action="store_true", help="只打印,不写 CSV")
    args = ap.parse_args()

    print("=" * 60)
    print("  抓取 WC2026 1X2 报价(BetExplorer · world-championship-2026)")
    print("=" * 60)
    wc_set = load_wc_teams()
    print(f"  参赛队白名单: {len(wc_set) if wc_set else 0} 队")

    collected = {}  # (home, away) → (home, away, score, oh, od, oa)

    for pt in ("results/", "fixtures/"):
        try:
            rows = harvest(pt)
        except Exception as e:
            print(f"  [{pt}] 拉取失败: {str(e)[:80]}")
            continue
        before = len(rows)
        if wc_set:
            rows = [r for r in rows
                    if r[0] in wc_set and r[1] in wc_set]
        print(f"  [{pt}] 解析 {before} 行 → 白名单 {len(rows)} 场")
        for home, away, score, odds3 in rows:
            if odds3:
                collected[(home, away)] = (
                    home, away, score, odds3[0], odds3[1], odds3[2])
                sc = f"({score})" if score else ""
                print(f"    ✓ {home:22s} vs {away:22s} {sc:8s}  "
                      f"{odds3[0]:.2f}/{odds3[1]:.2f}/{odds3[2]:.2f}")
            else:
                print(f"    ✗ {home:22s} vs {away:22s}  无赔率")

    print("-" * 60)
    print(f"  合计 {len(collected)} 场有 1X2")
    if args.dry:
        print("  --dry,不写 CSV")
        return

    if not collected:
        print("  ⚠ 无任何 1X2 产出,odds_wc.csv 未改")
        return

    added, kept = merge_to_csv([v for v in collected.values()])
    print(f"✓ odds_wc.csv: 2026 写入 {added} 场,保留历史 {kept} 场")


if __name__ == "__main__":
    main()