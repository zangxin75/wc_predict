"""fetch_wc2026_ou.py — 2026 世界杯 Over/Under(总进球高低)市场报价半自动采集 → ou_wc.csv。

为什么「半自动」而非全自动:
  BetExplorer 详情页的 Over/Under 数据按 tab 异步加载,无稳定 DOM 锚点(实测:URL hash
  无效,点 tab 不切子表,DOM 里 121 个 data-odd 但 OU 行结构不稳定)。全自动会变脆弱的
  模板匹配。故本工具:机器批量取详情链接 + 开页尽力提取 1X2+OU2.5,提取到 OU 则自动写
  ou_wc.csv(模式B);提取不到则打印该场链接 + 1X2,用户手动补 OU 一行即可。

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

# 详情页尽力提取 OU2.5:页面里所有「数字.5 + 两赔率」片段,优先 2.5 线。
# 兼容多种容器;提取后由 Python 端筛选最可能是 OU2.5 的配对。
JS_DETAIL = r"""()=>{
  const txt = document.body.innerText.replace(/\s+/g,' ');
  // 抓 "X.5 a b" 片段(X∈0-8,a/b 是 1.x-9.x 赔率)
  const re = /([0-8]\.5)\s+([1-9]\.\d{2})\s+([1-9]\.\d{2})/g;
  const hits=[]; let m;
  while((m=re.exec(txt))) hits.push({line:+m[1], over:+m[2], under:+m[3]});
  // 1X2 三态:详情页顶部收盘区,取前 3 个 data-odd(1X2 主平客)
  const odds = [...document.querySelectorAll('[data-odd]')].slice(0,3).map(e=>+e.getAttribute('data-odd'));
  return {dataodd: document.querySelectorAll('[data-odd]').length, odds, ou: hits};
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
    """概览行文本 → (home, away, [oh,od,oa] or None)。纯文本正则,不依赖 DOM odds 位置。

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


def pick_ou25(ou_hits, target=2.5):
    """从详情页抓的 {line,over,under} 列表选 OU2.5。优先精确 line=2.5;否则取最接近且
    over/under 在合理区间(1.3-3.5)的首个。无则 None。"""
    exact = [h for h in ou_hits if abs(h["line"] - target) < 1e-9
             and 1.3 <= h["over"] <= 3.5 and 1.3 <= h["under"] <= 3.5]
    if exact:
        return exact[0]["over"], exact[0]["under"]
    near = [h for h in ou_hits if 1.3 <= h["over"] <= 3.5 and 1.3 <= h["under"] <= 3.5]
    near.sort(key=lambda h: abs(h["line"] - target))
    if near:
        return near[0]["over"], near[0]["under"]
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
                pg.wait_for_timeout(5000)
                det = pg.evaluate(f"({JS_DETAIL})()")
                ou = pick_ou25(det["ou"])
                if len(det.get("odds") or []) == 3:
                    det_odds = det["odds"]
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
                collected.append((home, away, oh, od, oa, ou[0], ou[1]))
                print(f"  ✓ {home}-{away}  1X2 {oh:.2f}/{od:.2f}/{oa:.2f}  OU2.5 {ou[0]}/{ou[1]}")
            else:
                collected.append((home, away, oh, od, oa, None, None))
                manual.append((home, away, oh, od, oa, url))
                print(f"  △ {home}-{away}  1X2 {oh:.2f}/{od:.2f}/{oa:.2f}  OU 未提取(入 ou_wc.csv 退模式A,可手补 OU 升模式B)→ {url}")
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
