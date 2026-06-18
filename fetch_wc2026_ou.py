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
OVERVIEW = "https://www.betexplorer.com/football/world/world-cup-2026/"

# 概览页取每场详情链接 + 一行文本(含队名/比分/1X2)。复用 fetch_wc2026_odds 的锚点。
JS_LINKS = r"""()=>{
  const out=[];
  const olis = [...document.querySelectorAll('li.table-main__oddsLi')];
  for(const li of olis){
    const mi = li.parentElement;
    const txt = (mi? mi.innerText:'').replace(/\n/g,' ').replace(/\s+/g,' ').trim();
    const links = [...(mi||li).querySelectorAll('a[href]')];
    const detail = links.map(a=>a.getAttribute('href')).filter(h=>h.includes('world-championship-2026/'));
    // odds 优先在 oddsLi 内,缺失则取整个 matchInfo 的前 3 个(概览页有时 odds 在 li 外)
    let odds = [...li.querySelectorAll('[data-odd]')].map(e=>e.getAttribute('data-odd'));
    if(odds.length < 3 && mi){
      odds = [...mi.querySelectorAll('[data-odd]')].map(e=>e.getAttribute('data-odd'));
    }
    if(detail.length) out.push({text:txt, href: detail[0], odds:odds.slice(0,3)});
  }
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
    """rows: [(home,away,oh,od,oa,ou_over,ou_under)] → merge 进 ou_wc.csv(year=2026)。"""
    by_key = {}
    if os.path.exists(OU_CSV):
        with open(OU_CSV, newline="", encoding="utf-8") as f:
            for r in csv.DictReader(f):
                by_key[(r["year"], r["home"], r["away"])] = r
    merged = {k: v for k, v in by_key.items() if k[0] != "2026"}
    for home, away, oh, od, oa, oov, ou in rows:
        merged[("2026", home, away)] = {
            "year": "2026", "date": "", "home": home, "away": away,
            "odds_h": f"{oh:.2f}", "odds_d": f"{od:.2f}", "odds_a": f"{oa:.2f}",
            "ou_line": "2.5", "ou_over": f"{oov:.2f}" if oov else "",
            "ou_under": f"{ou:.2f}" if ou else "",
        }
    fields = ["year", "date", "home", "away", "odds_h", "odds_d", "odds_a",
              "ou_line", "ou_over", "ou_under"]
    all_rows = sorted(merged.values(), key=lambda m: (str(m["year"]), m["home"], m["away"]))
    with open(OU_CSV, "w", newline="", encoding="utf-8") as f:
        w = csv.DictWriter(f, fieldnames=fields)
        w.writeheader()
        for r in all_rows:
            w.writerow({fn: r.get(fn, "") for fn in fields})
    return len(rows), len(all_rows) - len(rows)


def main():
    ap = argparse.ArgumentParser(description="2026 WC Over/Under 半自动采集 → ou_wc.csv")
    ap.add_argument("--dry", action="store_true", help="只打印不写")
    args = ap.parse_args()

    from playwright.sync_api import sync_playwright
    wc = load_wc_teams()
    print("=" * 60)
    print("  2026 世界杯 Over/Under 采集(半自动)")
    print("=" * 60)
    collected = []
    manual = []
    with sync_playwright() as p:
        b = p.chromium.launch(headless=True)
        pg = b.new_page()
        links = []
        for pt in ("results/", "fixtures/"):
            try:
                pg.goto(OVERVIEW + pt, wait_until="domcontentloaded", timeout=45000)
                pg.wait_for_timeout(5000)
                links.extend(pg.evaluate(f"({JS_LINKS})()"))
            except Exception as e:
                print(f"  [{pt}] 概览抓取失败: {str(e)[:60]}")
        # 去重(同 href 取一次)
        seen_href = {}
        for r in links:
            seen_href.setdefault(r["href"], r)
        print(f"  概览取得 {len(seen_href)} 个 WC 详情链接")
        for href, rec in seen_href.items():
            parsed = parse_row(rec["text"])
            if not parsed:
                continue
            home, away, odds3 = parsed
            if wc and (home not in wc or away not in wc):
                continue
            url = "https://www.betexplorer.com" + href
            ou = None
            det_odds = None
            try:
                pg.goto(url, wait_until="domcontentloaded", timeout=45000)
                pg.wait_for_timeout(4000)
                # 1X2:详情页顶部收盘区前 3 个 data-odd(主平客)
                det_odds = pg.evaluate(
                    "(()=>{const d=[...document.querySelectorAll('[data-odd]')]"
                    ".slice(0,3).map(e=>+e.getAttribute('data-odd'));"
                    "return d.length===3?d:null;})()")
                # 点「Over/Under」锚点 → 页面自带 token 异步渲染 OU 报价表
                if pg.evaluate("(()=>!!document.querySelector('a[data-tab=\"ou\"]'))()"):
                    pg.evaluate("document.querySelector('a[data-tab=\"ou\"]').click()")
                    pg.wait_for_timeout(4000)
                    ou = pg.evaluate(f"({JS_OU25})()")
            except Exception as e:
                print(f"  详情页失败 {home}-{away}: {str(e)[:50]}")
            # 1X2 优先概览,其次详情页顶 3 个 data-odd
            use = odds3 or det_odds
            if use is None or len(use) != 3:
                manual.append((home, away, None, None, None, url))
                print(f"  ✗ {home}-{away}  无 1X2(概览+详情均缺)→ {url}")
                continue
            oh, od, oa = use
            # 有 1X2 即收集(OU 缺则 ou_over/under 空 → tournament_sim 退模式A,仍用反演λ)
            if ou:
                collected.append((home, away, oh, od, oa, ou["over"], ou["under"]))
                print(f"  ✓ {home}-{away}  1X2 {oh:.2f}/{od:.2f}/{oa:.2f}  OU2.5 {ou['over']}/{ou['under']} (n={ou['n']})")
            else:
                collected.append((home, away, oh, od, oa, None, None))
                manual.append((home, away, oh, od, oa, url))
                print(f"  △ {home}-{away}  1X2 {oh:.2f}/{od:.2f}/{oa:.2f}  OU 未提取(退模式A,可手补)→ {url}")
        b.close()

    print("-" * 60)
    print(f"  自动获取 OU: {len(collected)} 场   待手补: {len(manual)} 场")
    if manual:
        print("  手补格式(ou_wc.csv 追加,ou_over/under 留空则退模式A):")
        for home, away, oh, od, oa, url in manual:
            print(f"    2026,,{home},{away},{oh:.2f},{od:.2f},{oa:.2f},2.5,<over>,<under>  # {url}")
    if args.dry:
        print("  --dry,不写 ou_wc.csv")
        return
    if collected:
        added, kept = merge_ou(collected)
        print(f"✓ ou_wc.csv:写入 {added} 场(含 OU),文件共 {added+kept} 场")
        print("  mode B 现已可用:monte_carlo 自载 ou_wc.csv → 这几场用 OU 约束反演(更准)。")
    else:
        print("  无自动 OU 产出,ou_wc.csv 未改(模式A 仍生效)。")


if __name__ == "__main__":
    main()
