"""fetch_oddsportal.py — 从 OddsPortal 采集世界杯历史 1X2 收盘赔率。

OddsPortal 用 Vue.js SPA 渲染比赛行,每页 ~50 行,分页显示全部比赛(含资格赛)。
需:
  1. Playwright 渲染 SPA
  2. 逐页翻页提取
  3. 按决赛圈日期窗口过滤(排除资格赛)
  4. 去重后输出 CSV

用法:
  python3 scripts/fetch_oddsportal.py 2022              # 单届
  python3 scripts/fetch_oddsportal.py 2018 2022         # 多届
  python3 scripts/fetch_oddsportal.py --all             # 所有有数据的届
  python3 scripts/fetch_oddsportal.py --all --no-write  # 只打印,不写 CSV
"""

import argparse
import csv
import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
ODDS_CSV = os.path.join(HERE, "..", "odds_wc.csv")

# OddsPortal 有赔率数据的届(1998/2002 无数据,2006 只有 2 场)
SUPPORTED_YEARS = [2010, 2014, 2018, 2022, 2026]

# 决赛圈日期窗口
WC_DATES = {
    2010: ("11 Jun 2010", "11 Jul 2010"),
    2014: ("12 Jun 2014", "13 Jul 2014"),
    2018: ("14 Jun 2018", "15 Jul 2018"),
    2022: ("20 Nov 2022", "18 Dec 2022"),
    2026: ("11 Jun 2026", "19 Jul 2026"),
}

_MONTHS = {
    "Jan": 1, "Feb": 2, "Mar": 3, "Apr": 4, "May": 5, "Jun": 6,
    "Jul": 7, "Aug": 8, "Sep": 9, "Oct": 10, "Nov": 11, "Dec": 12,
}


def _parse_date(s):
    parts = s.split()
    if len(parts) == 3:
        return (int(parts[2]), _MONTHS.get(parts[1], 0), int(parts[0]))
    return (0, 0, 0)


def _resolve_relative(s, today):
    """OddsPortal 2026 用 'Today, 20 Jun' / 'Yesterday, 19 Jun' / 'Tomorrow, 21 Jun'
    这类相对日期,把它们转成 'DD Mon 2026' 让 _in_window 能判。"""
    s = s.strip()
    if s.startswith("Today,"):
        d = today
    elif s.startswith("Yesterday,"):
        from datetime import timedelta
        d = today - timedelta(days=1)
    elif s.startswith("Tomorrow,"):
        from datetime import timedelta
        d = today + timedelta(days=1)
    else:
        return s
    # "Today, 20 Jun" → "20 Jun 2026"
    return f"{d.day} {_MONTHS_INV[d.month]} {d.year}"


_MONTHS_INV = {v: k for k, v in _MONTHS.items()}


def _in_window(datestr, year):
    if year not in WC_DATES:
        return False
    from datetime import date
    today = date.today()
    datestr = _resolve_relative(datestr, today)
    d = _parse_date(datestr)
    s = _parse_date(WC_DATES[year][0])
    e = _parse_date(WC_DATES[year][1])
    return s <= d <= e


EXTRACT_PAGE_JS = r"""() => {
  const matches = [];
  let currentDate = '';
  const allEls = document.querySelectorAll(
    '[data-testid="secondary-header"], [data-testid="game-row"]');
  for (const el of allEls) {
    const testid = el.getAttribute('data-testid');
    if (testid === 'secondary-header') {
      const dateEl = el.querySelector('[data-testid="date-header"]');
      if (dateEl) currentDate = dateEl.textContent.trim();
      continue;
    }
    if (testid === 'game-row') {
      const oddsContainers = el.querySelectorAll('[data-testid*="odd-container"]');
      if (oddsContainers.length < 3) continue;
      const participants = el.querySelector('[data-testid="event-participants"]');
      if (!participants) continue;
      const teamLinks = participants.querySelectorAll('a[title]');
      if (teamLinks.length < 2) continue;
      const home = teamLinks[0].getAttribute('title');
      const away = teamLinks[teamLinks.length - 1].getAttribute('title');
      if (!home || !away) continue;
      const boldEls = participants.querySelectorAll('.font-bold');
      const scores = [];
      boldEls.forEach(e => {
        const t = e.textContent.trim();
        if (/^\d+$/.test(t)) scores.push(t);
      });
      const score = scores.length >= 2 ? scores[0] + ':' + scores[1] : '';
      const vals = [];
      oddsContainers.forEach(e => {
        const v = parseFloat(e.textContent.trim());
        if (!isNaN(v) && v > 1 && v < 100) vals.push(v);
      });
      const unique = [];
      for (let i = 0; i < vals.length; i++) {
        if (i === 0 || vals[i] !== vals[i - 1]) unique.push(vals[i]);
      }
      const odds = unique.slice(0, 3);
      if (odds.length === 3) {
        matches.push({
          date: currentDate.replace(/\s+-\s+.*$/, '').trim(),
          home, away, score,
          odds_h: odds[0], odds_d: odds[1], odds_a: odds[2],
        });
      }
    }
  }
  return matches;
}"""


def fetch_year(year, page):
    """采集单届世界杯全部决赛圈 1X2 赔率。返回 [(year,date,home,away,score,oh,od,oa)]。"""
    url = f"https://www.oddsportal.com/football/world/{('world-championship' if year == 2026 else 'world-cup')}-{year}/results/"
    page.goto(url, wait_until="domcontentloaded", timeout=60000)
    page.wait_for_timeout(10000)

    all_matches = []
    seen = set()
    pg = 0
    from datetime import date
    today = date.today()

    while True:
        pg += 1
        matches = page.evaluate(EXTRACT_PAGE_JS)
        added = 0
        for m in matches:
            key = (m["home"], m["away"])
            if key in seen:
                continue
            raw_date = m["date"]
            canon_date = _resolve_relative(raw_date, today)
            if not _in_window(canon_date, year):
                continue
            seen.add(key)
            all_matches.append((
                str(year), canon_date, m["home"], m["away"],
                m["score"],
                f"{m['odds_h']:.2f}", f"{m['odds_d']:.2f}", f"{m['odds_a']:.2f}",
            ))
            added += 1
        if added:
            print(f"    page {pg}: +{added} 场", file=sys.stderr)

        # Click "Next" button
        has_next = page.evaluate("""() => {
            const links = document.querySelectorAll('.pagination-link');
            for (const l of links) {
                const t = l.textContent.trim().toLowerCase();
                if (t === 'next') {
                    if (l.classList.contains('active') || l.disabled) return false;
                    l.click();
                    return true;
                }
            }
            return false;
        }""")
        if not has_next:
            break
        page.wait_for_timeout(5000)

    print(f"  [{year}] 共 {len(all_matches)} 场决赛圈比赛", file=sys.stderr)
    return all_matches


def merge_csv(all_rows):
    """Merge collected rows into odds_wc.csv, preserving existing data."""
    by_key = {}
    if os.path.exists(ODDS_CSV):
        with open(ODDS_CSV, newline="", encoding="utf-8") as f:
            for r in csv.DictReader(f):
                by_key[(r["year"], r["home"], r["away"])] = r

    insert_years = {r[0] for r in all_rows}
    merged = {k: v for k, v in by_key.items() if k[0] not in insert_years}

    added = 0
    fields = ["year", "date", "home", "away", "score", "odds_h", "odds_d", "odds_a"]
    for row in all_rows:
        key = (row[0], row[2], row[3])
        if key not in merged:
            added += 1
        merged[key] = {
            "year": row[0], "date": row[1], "home": row[2], "away": row[3],
            "score": row[4], "odds_h": row[5], "odds_d": row[6], "odds_a": row[7],
        }

    all_out = sorted(merged.values(), key=lambda m: (str(m["year"]), m["home"], m["away"]))
    with open(ODDS_CSV, "w", newline="", encoding="utf-8") as f:
        w = csv.DictWriter(f, fieldnames=fields)
        w.writeheader()
        for r in all_out:
            w.writerow({fn: r.get(fn, "") for fn in fields})
    return added, len(all_out)


def main():
    ap = argparse.ArgumentParser(description="OddsPortal 世界杯历史赔率采集")
    ap.add_argument("years", nargs="*", type=int, help="世界杯年份")
    ap.add_argument("--all", action="store_true", help="采集所有有数据的届")
    ap.add_argument("--no-write", action="store_true",
                    help="只打印不写 CSV")
    args = ap.parse_args()

    years = args.years
    if args.all:
        years = SUPPORTED_YEARS
    if not years:
        years = [2022]

    from playwright.sync_api import sync_playwright

    all_new = []
    with sync_playwright() as p:
        browser = p.chromium.launch(headless=True)
        context = browser.new_context(
            viewport={"width": 1280, "height": 800},
            user_agent="Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 "
                       "(KHTML, like Gecko) Chrome/120.0 Safari/537.36",
        )

        for year in years:
            # 每届用新页面避免 SPA 状态残留
            page = context.new_page()
            rows = fetch_year(year, page)
            page.close()
            all_new.extend(rows)

        context.close()
        browser.close()

    if not all_new:
        print("无新数据", file=sys.stderr)
        return

    if args.no_write:
        print(f"\n{'year':6s} {'date':14s} {'home':20s} {'away':20s} {'score':6s} "
              f"{'oh':6s} {'od':6s} {'oa':6s}")
        print("-" * 85)
        for r in all_new[:30]:
            print(f"{r[0]:6s} {r[1]:14s} {r[2]:20s} {r[3]:20s} {r[4]:6s} "
                  f"{r[5]:6s} {r[6]:6s} {r[7]:6s}")
        if len(all_new) > 30:
            print(f"... 共 {len(all_new)} 场")
        return

    added, total = merge_csv(all_new)
    print(f"\n✓ odds_wc.csv: 新增 {added} 场, 共 {total} 场", file=sys.stderr)


if __name__ == "__main__":
    main()
