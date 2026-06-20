"""fetch_wc2026_ou.py — 2026 世界杯 Over/Under(总进球高低)市场报价半自动采集 → ou_wc.csv。

机制(2026-06-16 侦察确认):
  BetExplorer 详情页的 Over/Under 按「Over/Under」锚点 a[data-tab="ou"] 异步加载——点击
  后页面自带 token 请求 match-content.php,渲染逐报价方的 OU 表(每行 = 总进球线 + Over
  + Under 两个 data-odd;线值单元格带 table-main__doubleparameter 类名)。早先误以为
  「无稳定锚点」,实则是没点对元素(点到了内层 span 而非锚点 a[data-tab="ou"])。
  本工具:机器批量取详情链接 → 开页 → 点 OU 锚点 → 解析 line==2.5 行的 over/under 最优值
  → 自动写 ou_wc.csv(模式B)。极少数无 OU 报价的场,打印链接 + 1X2 供手补。

降级保证(已在 compute_odds 实现):ou_wc.csv 缺 OU 行时 lambda_from_market 退模式A
(仅1X2,总量先验),odds_wc.csv 的 1X2 立即可用。模式B(本工具产出)只是增强。

ou_wc.csv 格式:year,date,home,away,odds_h,odds_d,odds_a,ou_line,ou_over,ou_under
  (ou_over/ou_under 缺时留空 → 退模式A)。

用法:
  python fetch_wc2026_ou.py            # 采集并写入 ou_wc.csv
  python fetch_wc2026_ou.py --dry      # 只打印,不写
"""
import argparse
import csv
import json
import os
import re
import sys

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8")

import compute_odds as co

HERE = os.path.dirname(os.path.abspath(__file__))
OU_CSV = co.OU_CSV
CACHE = os.path.join(HERE, "webapp", "data", "cache.json")
OVERVIEW = "https://www.betexplorer.com/football/world/world-championship-2026/"

# 概览页取每场详情链接 + 一行文本(含队名/比分/1X2)。
# 2026-06-19 改:旧 li.table-main__oddsLi 已弃用,改用 td.table-main__odds > button[data-odd]。
JS_LINKS = r"""()=>{
  const out=[];
  document.querySelectorAll('tr').forEach(tr => {
    const a = tr.querySelector('a.in-match');
    if(!a) return;
    const href = a.getAttribute('href');
    if(!href || !href.includes('world-championship-2026/')) return;
    const spans = [...a.querySelectorAll('span')];
    if(spans.length < 2) return;
    const txt = tr.innerText.replace(/\s+/g, ' ').trim();
    const odds = [...tr.querySelectorAll('button[data-odd]')]
      .slice(0, 3).map(b => b.getAttribute('data-odd'));
    out.push({text: txt, href: href, odds: odds});
  });
  return out;
}"""

# 详情页 OU2.5 提取(在 Python 端点击 a[data-tab="ou"] 异步加载后执行)。
# OU 表每行:<td class="table-main__doubleparameter">线值</td> + Over data-odd + Under data-odd。
# 筛 line==2.5 的行,over/under 各取跨报价方最大值(最优 = 最小 overround,去 vig 后最干净)。
JS_OU25 = r"""()=>{
  const rows=[];
  document.querySelectorAll('tr').forEach(tr=>{
    const cells=[...tr.querySelectorAll('td')];
    const lineCell = cells.find(td => (td.className||'').includes('doubleparameter'));
    if(!lineCell) return;
    if(!/^2\.50?$/.test(lineCell.innerText.trim())) return;
    const dd=[...tr.querySelectorAll('[data-odd]')].map(e=>parseFloat(e.getAttribute('data-odd'))).filter(x=>!isNaN(x)&&x>1);
    if(dd.length>=2) rows.push([dd[0], dd[1]]);
  });
  if(!rows.length) return null;
  return {over: Math.max(...rows.map(r=>r[0])), under: Math.max(...rows.map(r=>r[1])), n: rows.length};
}"""

PREFIX_RE = re.compile(r"""^(?:(?:FIN|FT|HT|POSTP|CANC|AWARDED|AWD|RET)\s+|\d+\s*'\s+|\d{1,2}:\d{2}\s+)""", re.I)
ROW_SCORE = re.compile(r"""^(?P<h>.+?)\s+(?P<x>\d+)\s*:\s*(?P<y>\d+)\s+(?P<a>.+?)\s*$""")
ROW_DASH = re.compile(r"""^(?P<h>.+?)\s+-\s+(?P<a>.+?)\s*$""")


def load_wc_teams():
    if not os.path.exists(CACHE):
        return None
    with open(CACHE, encoding="utf-8") as f:
        d = json.load(f)
    s = set()
    for grp, tl in (d.get("groups") or {}).items():
        for r in tl:
            if isinstance(r, dict) and "team" in r:
                s.add(co.normalize_team(r["team"]))
    return s or None


def parse_row(text):
    r"""概览行文本 → (home, away, [oh,od,oa] or None)。纯文本正则,不依赖 DOM odds 位置。

    已赛:[前缀] HOME sH : sA AWAY OH OD OA  例 "FIN Saudi Arabia 1 : 1 Uruguay 7.55 4.09 1.49"
    未赛:[时分] HOME - AWAY OH OD OA        例 "03:00 Iran - New Zealand"(概览未赛常无赔率)
    队名不含数字→用 \D+ 隔开比分;尾赔率 \d+\.\d+ 单独捕获。"""
    body = PREFIX_RE.sub("", text, count=1).strip()
    # 已赛:队名 比分 队名 [3 赔率]
    m = re.match(
        r"^(?P<h>\D+?)\s+(?P<sh>\d+)\s*:\s*(?P<sa>\d+)\s+(?P<a>\D+?)\s*"
        r"(?:(?P<o1>\d+\.\d+)\s+(?P<o2>\d+\.\d+)\s+(?P<o3>\d+\.\d+))?\s*$", body)
    if m:
        h = co.normalize_team(m["h"].strip()); a = co.normalize_team(m["a"].strip())
        if len(h) >= 2 and len(a) >= 2:
            odds = None
            if m.group("o1"):
                odds = [float(m["o1"]), float(m["o2"]), float(m["o3"])]
            return h, a, odds
    # 未赛:队名 - 队名 [3 赔率]
    m = re.match(
        r"^(?P<h>\D+?)\s+-\s+(?P<a>\D+?)\s*"
        r"(?:(?P<o1>\d+\.\d+)\s+(?P<o2>\d+\.\d+)\s+(?P<o3>\d+\.\d+))?\s*$", body)
    if m:
        h = co.normalize_team(m["h"].strip()); a = co.normalize_team(m["a"].strip())
        if len(h) >= 2 and len(a) >= 2:
            odds = None
            if m.group("o1"):
                odds = [float(m["o1"]), float(m["o2"]), float(m["o3"])]
            return h, a, odds
    return None


def merge_ou(rows):
    """rows: [(home,away,oh,od,oa,ou_over,ou_under)] → merge 进 ou_wc.csv(year=2026)。

    ⚠️ 2026-06-19 改 merge 模式:保留所有已存在的 2026 行(含手补 OU 的 5 场),
    新数据只覆盖存在的字段,空字段保留旧值。这样反复跑不会丢手补数据。
    """
    by_key = {}
    if os.path.exists(OU_CSV):
        with open(OU_CSV, newline="", encoding="utf-8") as f:
            for r in csv.DictReader(f):
                by_key[(r["year"], r["home"], r["away"])] = r
    added = 0
    updated = 0
    for home, away, oh, od, oa, oov, ou in rows:
        key = ("2026", home, away)
        old = by_key.get(key)
        new_row = {
            "year": "2026", "date": "", "home": home, "away": away,
            "odds_h": f"{oh:.2f}",
            "odds_d": f"{od:.2f}",
            "odds_a": f"{oa:.2f}",
            "ou_line": (old or {}).get("ou_line") or "2.5",
            "ou_over": f"{oov:.2f}" if oov else (old or {}).get("ou_over", ""),
            "ou_under": f"{ou:.2f}" if ou else (old or {}).get("ou_under", ""),
        }
        if old is None:
            added += 1
        elif (new_row["ou_over"] != old.get("ou_over")
              or new_row["ou_under"] != old.get("ou_under")
              or new_row["odds_h"] != old.get("odds_h")):
            updated += 1
        by_key[key] = new_row
    fields = ["year", "date", "home", "away", "odds_h", "odds_d", "odds_a",
              "ou_line", "ou_over", "ou_under"]
    all_rows = sorted(by_key.values(),
                      key=lambda m: (str(m["year"]), m["home"], m["away"]))
    with open(OU_CSV, "w", newline="", encoding="utf-8") as f:
        w = csv.DictWriter(f, fieldnames=fields)
        w.writeheader()
        for r in all_rows:
            w.writerow({fn: r.get(fn, "") for fn in fields})
    return added, updated, len(all_rows)


def main():
    ap = argparse.ArgumentParser(description="2026 WC OU/1X2 半自动采集 → ou_wc.csv")
    ap.add_argument("--dry", action="store_true", help="只打印不写")
    ap.add_argument("--with-ou", action="store_true",
                    help="启用 Playwright 详情页 OU 异步抓取(慢,~8s/场,默认关)")
    args = ap.parse_args()

    wc = load_wc_teams()
    print("=" * 60)
    print(f"  2026 WC OU/1X2 采集(概览{'+OU异步' if args.with_ou else '仅1X2'})")
    print("=" * 60)

    # ── 阶段 1:概览抓 1X2(requests+BS4,无需 Playwright)──
    import requests as _req
    from bs4 import BeautifulSoup as _BS
    headers = {"User-Agent": "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 "
                             "(KHTML, like Gecko) Chrome/120.0 Safari/537.36",
               "Accept-Language": "en-US,en;q=0.9"}
    one_x_two = {}   # (home,away) → (oh,od,oa)
    seen = set()
    for pt in ("results/", "fixtures/"):
        try:
            r = _req.get(OVERVIEW + pt, headers=headers, timeout=30)
            r.raise_for_status()
        except Exception as e:
            print(f"  [{pt}] 拉取失败: {str(e)[:60]}")
            continue
        soup = _BS(r.text, "html.parser")
        for tr in soup.find_all("tr"):
            a = tr.find("a", class_="in-match")
            if not a:
                continue
            href = a.get("href") or ""
            if "world-championship-2026/" not in href:
                continue
            spans = a.find_all("span")
            if len(spans) < 2:
                continue
            home = co.normalize_team(spans[0].get_text(strip=True))
            away = co.normalize_team(spans[-1].get_text(strip=True))
            if wc and (home not in wc or away not in wc):
                continue
            key = (home, away)
            if key in seen:
                continue
            odds = []
            for btn in tr.find_all("button", attrs={"data-odd": True}):
                try:
                    odds.append(float(btn["data-odd"]))
                except (ValueError, KeyError):
                    continue
                if len(odds) >= 3:
                    break
            if len(odds) >= 3:
                one_x_two[key] = (odds[0], odds[1], odds[2])
            seen.add(key)
    print(f"  概览取得 {len(one_x_two)} 场 1X2")

    # ── 阶段 2 (可选):Playwright 详情页 OU 异步抓 ──
    ou_data = {}   # (home,away) → {over,under,n}
    if args.with_ou:
        print(f"  启 Playwright 抓 OU 详情(每场 ~8s,{len(one_x_two)} 场预计 {len(one_x_two)*8//60} 分钟)...")
        from playwright.sync_api import sync_playwright
        with sync_playwright() as p:
            b = p.chromium.launch(headless=True)
            pg = b.new_page()
            for (home, away), _ in one_x_two.items():
                href = None
                # 反查 href(重新解析一遍 results/fixtures 找该场)
                for pt in ("results/", "fixtures/"):
                    try:
                        r = _req.get(OVERVIEW + pt, headers=headers, timeout=30)
                        soup = _BS(r.text, "html.parser")
                        for tr in soup.find_all("tr"):
                            a = tr.find("a", class_="in-match")
                            if not a:
                                continue
                            spans = a.find_all("span")
                            if len(spans) < 2:
                                continue
                            h2 = co.normalize_team(spans[0].get_text(strip=True))
                            a2 = co.normalize_team(spans[-1].get_text(strip=True))
                            if (h2, a2) == (home, away):
                                href = a.get("href")
                                break
                        if href:
                            break
                    except Exception:
                        continue
                if not href:
                    continue
                url = "https://www.betexplorer.com" + href
                try:
                    pg.goto(url, wait_until="domcontentloaded", timeout=45000)
                    pg.wait_for_timeout(4000)
                    if pg.evaluate("(()=>!!document.querySelector('a[data-tab=\"ou\"]'))()"):
                        pg.evaluate("document.querySelector('a[data-tab=\"ou\"]').click()")
                        pg.wait_for_timeout(4000)
                        ou_data[(home, away)] = pg.evaluate(f"({JS_OU25})()")
                except Exception as e:
                    print(f"    ⚠ {home}-{away}: {str(e)[:50]}")
            b.close()
        print(f"  OU 抓到 {len(ou_data)} 场")

    # ── 阶段 3:合并 1X2 + OU → merge 写 ou_wc.csv ──
    collected = []
    for (home, away), (oh, od, oa) in one_x_two.items():
        ou = ou_data.get((home, away))
        if ou:
            collected.append((home, away, oh, od, oa, ou["over"], ou["under"]))
        else:
            collected.append((home, away, oh, od, oa, None, None))
    print(f"  待写入 {len(collected)} 场 (OU 字段留空 {sum(1 for c in collected if c[5] is None)} 场 → 模式A)")

    if args.dry:
        for home, away, oh, od, oa, oov, ou in collected[:5]:
            print(f"    [示例] {home} vs {away}  {oh:.2f}/{od:.2f}/{oa:.2f}  "
                  f"OU={oov}/{ou}")
        print(f"  --dry,不写 ou_wc.csv")
        return

    if collected:
        added, updated, total = merge_ou(collected)
        print(f"✓ ou_wc.csv:新增 {added} 场,更新 {updated} 场,文件共 {total} 场")
    else:
        print("  无数据可写,ou_wc.csv 未改")


if __name__ == "__main__":
    main()
