"""fetch_wc2026_odds.py — 抓取 2026 世界杯小组赛(及进行中淘汰赛)实时报价,写入 odds_wc.csv。

背景:BetExplorer 2026 页是 JS 渲染的 div 版"通用直播/赛程"页,静态 HTML 无赔率。
本脚本用 Playwright 渲染 results(已赛)+ fixtures(未赛)页,定位 "WORLD CHAMPIONSHIP 2026"
区块,逐行解析 (主,客,比分,oh,od,oa),merge 进 odds_wc.csv(year=2026)。

为什么 Playwright 而非 requests:2026 数据 JS 后渲染,requests 拿到的是 no-js 空壳(零 table/data-odd)。
为什么 merge:保护历史 108 场(1998-2022),只更新 2026 行。

⚠️ 最佳努力(best-effort):BetExplorer 反爬/页面结构可能变动;抓不到的场 odds_wc.csv 不写,
webapp 自动回退 Elo(predict_one 的 None→elo 兜底)。可反复跑(每日),随小组赛推进累积场次。
手动补数据:直接在 odds_wc.csv 追加 `2026,YYYY-MM-DD,Home,Away,score,oh,od,oa` 行即可,无需改代码。
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
OUT_CSV = co.OUT_CSV
BASE = "https://www.betexplorer.com/football/world/world-cup-2026/"   # 概览页:抓1X2+取详情链接
CACHE = os.path.join(HERE, "webapp", "data", "cache.json")   # 含 groups→2026 参赛队白名单

# 锚点:li.table-main__oddsLi 的父级 = UL.table-main__matchInfo,文本含完整一行
# "FIN Saudi Arabia 1 : 1 Uruguay 7.55 4.09 1.49"(状态前缀 + 队名 + 比分 + 队名 + 赔率)。
# 该 URL 是 WC2026 专属页,所有行均属 WC,无需联赛过滤。
EXTRACT_WC = r"""(pageType)=>{
  const out = [];
  const olis = [...document.querySelectorAll('li.table-main__oddsLi')];
  for(const li of olis){
    const odds = [...li.querySelectorAll('[data-odd]')].map(e=>e.getAttribute('data-odd'));
    if(odds.length < 3) continue;
    const mi = li.parentElement;   // UL.table-main__matchInfo(含队名)
    const txt = mi ? (mi.innerText||'').replace(/\n/g,' ').replace(/\s+/g,' ').trim() : '';
    out.push({page:pageType, text:txt, odds:odds.slice(0,3)});
  }
  return out;
}"""

# 行解析(去掉尾部 3 赔率后):
#   已赛/进行:  [状态前缀] HOME sH : sA AWAY   例 "FIN Saudi Arabia 1 : 1 Uruguay"
#   未赛:       [时间] HOME - AWAY              例 "12:30 Spain - Brazil"
# 状态前缀:FIN/FT/HT/POSTP/CANC/AWARDED/AWD/18'/12:30(进行/未赛时分)
PREFIX_RE = re.compile(r"""^(?:(?:FIN|FT|HT|POSTP|CANC|AWARDED|AWD|RET)\s+|\d+\s*'\s+|\d{1,2}:\d{2}\s+)""", re.I)
ROW_RE_SCORE = re.compile(r"""^(?P<home>.+?)\s+(?P<sh>\d+)\s*:\s*(?P<sa>\d+)\s+(?P<away>.+?)\s*$""")
ROW_RE_DASH = re.compile(r"""^(?P<home>.+?)\s+-\s+(?P<away>.+?)\s*$""")


def load_wc_teams():
    """从 webapp cache.json 的 groups 提取 2026 参赛队集合(经 normalize),用于过滤掉
    BetExplorer 通用概览页混入的周边联赛(巴西 Serie B 等)。失败返回 None=不过滤。"""
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


def parse_row(rec):
    """raw {text, odds} → (home, away, score, oh, od, oa) or None(队名/赔率不全)。"""
    text, odds = rec["text"], rec["odds"]
    if not text or len(odds) != 3:
        return None
    # 剥离行尾已知 3 赔率(DOM 的 data-odd 比 text 里的数字更可靠)
    body = text
    for o in reversed(odds):
        i = body.rfind(o)
        if i >= 0:
            body = body[:i].rstrip()
    body = PREFIX_RE.sub("", body, count=1).strip()   # 去 FIN/HT/18'/时分 前缀
    score = None
    m = ROW_RE_SCORE.match(body)
    if m:
        home, away = m["home"], m["away"]
        score = f"{m['sh']}:{m['sa']}"
    else:
        m = ROW_RE_DASH.match(body)
        if m:
            home, away = m["home"], m["away"]
        else:
            return None
    home = co.normalize_team(home.strip())
    away = co.normalize_team(away.strip())
    if len(home) < 2 or len(away) < 2:
        return None
    try:
        oh, od, oa = float(odds[0]), float(odds[1]), float(odds[2])
    except ValueError:
        return None
    return home, away, score, oh, od, oa


def harvest_page(pg, page_type):
    """渲染一页,返回 [(home,away,score,oh,od,oa)]。"""
    path = "results/" if page_type == "results" else "fixtures/"
    pg.goto(BASE + path, wait_until="domcontentloaded", timeout=45000)
    pg.wait_for_timeout(5000)   # 给 JS 渲染;不用 networkidle(长轮询会卡)
    raw = pg.evaluate(f"({EXTRACT_WC})({page_type!r})")
    out = []
    for rec in raw:
        parsed = parse_row(rec)
        if parsed:
            out.append(parsed)
    return out


def merge_to_csv(rows_2026):
    """rows_2026: [(home,away,score,oh,od,oa)] → merge 进 odds_wc.csv(只动 2026)。"""
    by_key = {}
    if os.path.exists(OUT_CSV):
        with open(OUT_CSV, newline="", encoding="utf-8") as f:
            for r in csv.DictReader(f):
                by_key[(r["year"], r["home"], r["away"])] = r
    # 清掉旧 2026(本次全量替换 2026),保留其他届
    merged = {k: v for k, v in by_key.items() if k[0] != "2026"}
    added = 0
    for home, away, score, oh, od, oa in rows_2026:
        merged[("2026", home, away)] = {
            "year": "2026", "date": "", "home": home, "away": away,
            "score": score or "", "odds_h": f"{oh:.2f}", "odds_d": f"{od:.2f}", "odds_a": f"{oa:.2f}",
        }
        added += 1
    fields = ["year", "date", "home", "away", "score", "odds_h", "odds_d", "odds_a"]
    all_rows = sorted(merged.values(), key=lambda m: (str(m["year"]), m["home"], m["away"]))
    with open(OUT_CSV, "w", newline="", encoding="utf-8") as f:
        w = csv.DictWriter(f, fieldnames=fields)
        w.writeheader()
        for r in all_rows:
            w.writerow({fn: r.get(fn, "") for fn in fields})
    kept_other = len(all_rows) - added
    return added, kept_other


def main():
    ap = argparse.ArgumentParser(description="抓取 2026 世界杯小组赛实时报价 → odds_wc.csv")
    ap.add_argument("--dry", action="store_true", help="只打印抓到的,不写 CSV")
    args = ap.parse_args()

    from playwright.sync_api import sync_playwright
    print("=" * 60)
    print("  抓取 2026 世界杯小组赛报价(BetExplorer, Playwright 渲染)")
    print("=" * 60)
    wc_set = load_wc_teams()
    all_rows = []
    with sync_playwright() as p:
        b = p.chromium.launch(headless=True)
        pg = b.new_page()
        for pt in ("results", "fixtures"):
            try:
                rows = harvest_page(pg, pt)
                if wc_set:
                    before = len(rows)
                    rows = [r for r in rows if r[0] in wc_set and r[1] in wc_set]
                    print(f"  [{pt}] 解析 {before} 场 → 白名单过滤后 {len(rows)} 场 WC2026")
                else:
                    print(f"  [{pt}] 解析出 {len(rows)} 场(无白名单,未过滤)")
                for home, away, score, oh, od, oa in rows[:8]:
                    sc = score or "-"
                    print(f"    {home} {sc} {away}  {oh:.2f}/{od:.2f}/{oa:.2f}")
                all_rows.extend(rows)
            except Exception as e:
                print(f"  [{pt}] 抓取失败({str(e)[:80]})——该页跳过,不影响已抓")
        b.close()

    # 去重(同场在 results+fixtures 都出现时取 fixtures 的未赛或 results 的已赛)
    seen = {}
    for home, away, score, oh, od, oa in all_rows:
        seen[(home, away)] = (home, away, score, oh, od, oa)   # 后者覆盖(results 优先)

    print("-" * 60)
    print(f"  去重后 {len(seen)} 场")
    if args.dry:
        print("  --dry,不写 CSV")
        return
    added, kept = merge_to_csv(list(seen.values()))
    print(f"✓ odds_wc.csv:2026 写入 {added} 场,保留历史 {kept} 场")
    print(f"  现在 webapp 2026 小组赛卡片中这些场将显示「📊报价」(自动生效)。")


if __name__ == "__main__":
    main()
