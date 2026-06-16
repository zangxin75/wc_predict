"""
compute_odds.py — 抓取 BetExplorer 世界杯收盘赔率 + Power method 去vig

数据源:betexplorer.com 世界杯归档。每届 results 页用 ?stage=<加密ID> 分阶段
(小组赛在 "First stage",淘汰赛在 "Final tournament"),stage ID 每届不同,
必须先抓主页动态提取再逐个抓取合并 → 拿全 64 场收盘 1X2 赔率。

输出:odds_wc.csv  列 = year,date,home,away,score,odds_h,odds_d,odds_a
     (英文队名,对齐 results.csv 键)。

用法:
    python compute_odds.py          # 抓 2018+2022 写 odds_wc.csv
    python compute_odds.py 2014     # 抓单届

依赖:requests, beautifulsoup4(soccerdata 已带)。
"""

import csv
import math
import os
import re
import sys
import time

import requests
from bs4 import BeautifulSoup

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8")

HERE = os.path.dirname(os.path.abspath(__file__))
OUT_CSV = os.path.join(HERE, "odds_wc.csv")
# /soccer/ 已 301 → /football/;直击 /football/ 省一次跳转、降低限流敏感度
RESULTS_URL = "https://www.betexplorer.com/football/world/world-cup-{year}/results/"

UA = {"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
                    "(KHTML, like Gecko) Chrome/120.0 Safari/537.36",
      "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
      "Accept-Language": "en-US,en;q=0.9",
      "Accept-Encoding": "gzip, deflate, br",
      "Connection": "keep-alive",
      "Upgrade-Insecure-Requests": "1"}

# BetExplorer 队名 → results.csv 英文键的差异修正
TEAM_NORMALIZE = {
    "Korea Republic": "South Korea",
    "IR Iran": "Iran",
    "Iran IR Iran": "Iran",
    "Bosnia & Herzegovina": "Bosnia and Herzegovina",
    "Côte d'Ivoire": "Ivory Coast",
    "Cape Verde Islands": "Cape Verde",
    "DR Congo (Kinshasa)": "DR Congo",
    "USA": "United States",
    "Ireland": "Republic of Ireland",
}


def normalize_team(name):
    """队名规范化:合并空白 + 已知差异映射,对齐 results.csv 英文键。"""
    name = re.sub(r"\s+", " ", name).strip()
    return TEAM_NORMALIZE.get(name, name)


# =========================================================================
# 抓取层
# =========================================================================
def _session():
    s = requests.Session()
    s.headers.update(UA)
    return s


def fetch_stage_ids(sess, year):
    """抓 results 主页,提取 {stage 显示名: 加密 stage_id}。

    主页默认显示 "Final tournament"(淘汰赛);其余 stage(小组赛等)用
    ?stage=<id> 访问。stage_id 每届不同,必须动态提取。
    """
    r = sess.get(RESULTS_URL.format(year=year), timeout=30)
    soup = BeautifulSoup(r.text, "html.parser")
    stages = {}
    for a in soup.find_all("a", href=True):
        m = re.search(r"\?stage=([A-Za-z0-9]+)", a["href"])
        if m:
            txt = a.get_text(strip=True)
            if txt and txt not in stages:
                stages[txt] = m.group(1)
    return stages


def _cell_odd(td):
    """从 odds 单元格取 data-odd 值。

    BetExplorer 反爬细节:主胜列(带 colored 类)的 data-odd 藏在最内层嵌套 span,
    平/客列的 data-odd 直接在 td 上。两种位置都要兼容。
    """
    v = td.get("data-odd")
    if v:
        return v
    inner = td.find(attrs={"data-odd": True})
    return inner["data-odd"] if inner else None


def parse_matches(html):
    """解析一个 stage 页的 table-main,返回 [dict]。

    每场:date,home,away,score(含 AET/AfP 后缀),odds_h,odds_d,odds_a。
    日期:BetExplorer 按日期分组,日期行(无 td、含日期文本)更新 cur_date。
    """
    soup = BeautifulSoup(html, "html.parser")
    main = soup.find("table", class_=re.compile("table-main"))
    out = []
    if not main:
        return out
    cur_date = None
    for tr in main.find_all("tr"):
        text = tr.get_text(" ", strip=True)
        # 日期分组行:无比赛链接、文本里含日期模式
        if not tr.find("a", class_="in-match") and re.search(
                r"\b\d{1,2}[./ ]\d{1,2}[./ ]\d{2,4}\b|\b\d{1,2} \w+ \d{4}\b", text):
            cur_date = text
            continue
        a = tr.find("a", class_="in-match")
        if not a:
            continue
        spans = [s for s in a.find_all("span") if s.get_text(strip=True)]
        if len(spans) < 2:
            continue
        home = normalize_team(spans[0].get_text(strip=True))
        away = normalize_team(spans[-1].get_text(strip=True))
        score_td = tr.find("td", class_="h-text-center")
        score = score_td.get_text(strip=True) if score_td else ""
        odds_tds = tr.find_all("td", class_=re.compile("table-main__odds"))
        odds = [_cell_odd(td) for td in odds_tds]
        if len(odds) < 3 or any(o is None for o in odds):
            continue
        try:
            oh, od, oa = float(odds[0]), float(odds[1]), float(odds[2])
        except ValueError:
            continue
        if oh <= 1.0 or od <= 1.0 or oa <= 1.0:   # 赔率异常(退市/无效)跳过
            continue
        out.append({"date": cur_date, "home": home, "away": away, "score": score,
                    "odds_h": oh, "odds_d": od, "odds_a": oa})
    return out


def collect_tournament(sess, year, sleep=1.5):
    """抓一届世界杯【决赛圈淘汰赛】(默认页 Final tournament,16 场)。

    ⚠️ 已知限制(已验证):BetExplorer 的 world-cup-{year} league 实际是
    【洲际预选赛(~1100 场) + 决赛圈淘汰赛(16 场)】。决赛圈小组赛 48 场的
    赔率不在批量 results 页(只在逐场详情页)——遍历 ?stage= 全部 15 个 tab
    连揭幕战对手 Saudi Arabia/Ecuador 都搜不到。继续遍历只会拿到预选赛
    污染(Africa/Europe/South Am/Main 等洲际预选赛),且短间隔触发反爬断连。
    故这里只抓默认页 16 场淘汰赛;小组赛待后续 OddsPortal/半自动补全。
    """
    r = sess.get(RESULTS_URL.format(year=year), timeout=30)
    matches = {}
    for m in parse_matches(r.text):
        matches.setdefault((m["home"], m["away"]), m)
    print(f"  [{year}] 决赛圈淘汰赛(Final tournament): {len(matches)} 场")
    return list(matches.values())


# =========================================================================
# 去 vig 层(Power method)
# =========================================================================
def implied_probs(odds_h, odds_d, odds_a):
    """decimal odds → 原始隐含概率 1/o。三态和 >1,多出的是 overround(vig)。"""
    return 1.0 / odds_h, 1.0 / odds_d, 1.0 / odds_a


def devig_power(odds_h, odds_d, odds_a, tol=1e-10, max_iter=100):
    """Power method 去 vig(已实现)。

    找指数 z>1 使 Σ(1/o_i)^z = 1,然后 p_i = (1/o_i)^z(天然和=1,无需再归一化)。
    相比 basic normalization(直接除以和),power 纠正 favorite-longshot bias:
    博彩市场系统性高估冷门(赔率越高抽水越多),指数 z>1 对小的 q(冷门)压缩更多、
    对大的 q(热门)压缩更少 —— 净效果是热门概率↑、冷门概率↓,抵消 FLB。

    数值求解:二分法。隐含概率 q_i=1/o_i<1,故 q_i^z 随 z 单调递减 → 和也递减。
    z=1 时和 = 1+overround > 1;z 充分大时和 < 1。区间 [1, 10] 必有根,二分到 tol。
    """
    ih, idd, ia = implied_probs(odds_h, odds_d, odds_a)
    lo, hi = 1.0, 10.0   # lo: 和>1; hi=10 足够大使和<1(极端赔率也覆盖)
    for _ in range(max_iter):
        mid = 0.5 * (lo + hi)
        s = ih ** mid + idd ** mid + ia ** mid
        if s > 1.0:
            lo = mid        # 和>1 → z 偏小,向上逼近
        else:
            hi = mid
        if hi - lo < tol:
            break
    z = 0.5 * (lo + hi)
    return ih ** z, idd ** z, ia ** z


# =========================================================================
# 装载 / 查表(供 train_xgb / ensemble / backtest 用)
# =========================================================================
def load_odds(path=OUT_CSV):
    """加载 odds_wc.csv → {(year,home,away): (oh,od,oa)}。
    键含 year:跨届同对阵(如 Germany-Argentina 2006 QF / 2014 决赛)不再互相覆盖。
    队名为 results.csv 英文队名。"""
    out = {}
    if not os.path.exists(path):
        return out
    with open(path, newline="", encoding="utf-8") as f:
        for row in csv.DictReader(f):
            key = (str(row["year"]), normalize_team(row["home"]), normalize_team(row["away"]))
            out[key] = (float(row["odds_h"]), float(row["odds_d"]), float(row["odds_a"]))
    return out


def lookup_odds(odds_map, home, away, year):
    """查表;缺失返回 None(调用方决定回退策略)。
    year 必填:消除跨届同对阵(如 Germany-Argentina 2006/2014)的歧义。
    队名经 normalize_team 归一化,容忍 BetExplorer/results.csv 拼写差异
    (如 Ireland ↔ Republic of Ireland)。同年主客颠倒兜底。"""
    home = normalize_team(home)
    away = normalize_team(away)
    y = str(year)
    if (y, home, away) in odds_map:
        return odds_map[(y, home, away)]
    if (y, away, home) in odds_map:
        oh, od, oa = odds_map[(y, away, home)]
        return oa, od, oh
    return None


# =========================================================================
# main:抓 2018+2022 写 odds_wc.csv
# =========================================================================
def main():
    years = [int(x) for x in sys.argv[1:]] or [2018, 2022]
    sess = _session()
    rows = []
    for i, year in enumerate(years):
        print(f"\n=== 抓取 {year} 世界杯 ===")
        ms = collect_tournament(sess, year)
        for m in ms:
            m["year"] = year
            rows.append(m)
        if i < len(years) - 1:
            time.sleep(3)  # 年间停顿,避免 BetExplorer 软限流
    # 合并写入:保留已抓届(如精心整理的 1998-2022 共 108 场),
    # 只用本次抓取的年份覆盖对应年份;其余届原样保留。避免 `2026` 单独跑时清空历史。
    by_key = {}
    if os.path.exists(OUT_CSV):
        with open(OUT_CSV, newline="", encoding="utf-8") as f:
            for r in csv.DictReader(f):
                by_key[(r["year"], r["home"], r["away"])] = r
    new_years = set(str(y) for y in years)
    merged = {k: v for k, v in by_key.items() if k[0] not in new_years}
    for m in rows:
        merged[(str(m["year"]), m["home"], m["away"])] = m
    all_rows = sorted(merged.values(),
                      key=lambda m: (str(m["year"]), m["home"], m["away"]))
    fields = ["year", "date", "home", "away", "score", "odds_h", "odds_d", "odds_a"]
    with open(OUT_CSV, "w", newline="", encoding="utf-8") as f:
        w = csv.DictWriter(f, fieldnames=fields)
        w.writeheader()
        for r in all_rows:
            w.writerow({fn: r.get(fn, "") for fn in fields})

    print(f"\n✓ 写入 {OUT_CSV}: {len(all_rows)} 场"
          f"(本次抓 {len(rows)} + 保留 {len(all_rows) - len(rows)})")
    # 按届统计 + overround 抽样
    for year in years:
        ys = [m for m in rows if m["year"] == year]
        if ys:
            avg_or = sum(1/m["odds_h"] + 1/m["odds_d"] + 1/m["odds_a"] for m in ys) / len(ys)
            print(f"  {year}: {len(ys)} 场, 平均 overround = {avg_or:.3f} (健康值 1.04~1.08)")
    print("\n抽样校验(前 6 场):")
    for m in rows[:6]:
        print(f"  {m['year']} {m['home']:16} vs {m['away']:16} {m['score']:8} "
              f"{m['odds_h']:4.2f}/{m['odds_d']:4.2f}/{m['odds_a']:4.2f}")


if __name__ == "__main__":
    main()
