"""fetch_flashscore.py — 从 Flashscore 抓 2026 世界杯 1X2 + Over/Under 赔率。

⚠️ 2026-06-19 标 deprecated:
  Flashscore 是纯 React SPA,概览页静态 HTML 零数据(全部由 GraphQL 异步加载)。
  原方案尝试 Playwright + wait_for_selector("a.event__match") 失败(选择器可能也变了),
  且每场详情页 SPA 渲染 ~10s,慢 5 倍以上。

  同期 fetch_wc2026_odds.py 已重写为 requests+BS4,静态 HTML 即可拿到 1X2;
  fetch_wc2026_ou.py 用 Playwright 抓详情页 OU 异步表(选择器 a[data-tab="ou"] 仍有效)。
  故本脚本当前不再走 update_odds.sh。代码保留以备 BetExplorer 全面失效时切换。

  真正要恢复 Flashscore 抓取,需要:
  1. 调研 GraphQL 端点(https://2.ds.lsapp.eu/pq_graphql?)直接 POST 拿 JSON
  2. 或用 playwright + 更激进的等待(networkidle + 长 timeout)
  3. 或换 OddsPortal(类似 BetExplorer 但 DOM 可能更稳定)

机制(2026-06-19 之前的旧版):
  1. Playwright 渲染 Flashscore WC2026 fixtures/results 页
  2. 提取比赛详情链接,同步解析队名、比分
  3. 逐场访问详情页(必须 SPA 渲染后才能提取)
  4. 写 odds_wc.csv + ou_wc.csv
"""
import argparse
import csv
import json
import os
import re
import sys
import time

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8")

import compute_odds as co

HERE = os.path.dirname(os.path.abspath(__file__))
ODDS_CSV = co.OUT_CSV
OU_CSV = co.OU_CSV
CACHE = os.path.join(HERE, "webapp", "data", "cache.json")

# Flashscore 用 world-championship (不是 world-cup)
WC_BASE = "https://www.flashscore.com/football/world/world-championship-2026/"
WC_RESULTS = WC_BASE + "results/"
WC_FIXTURES = WC_BASE + "fixtures/"

# 比赛链接选择器 (SPA 渲染后 a 内含队名)
MATCH_A_SEL = 'a[href*="/match/football/"]'

# 详情页:第一组 .odds 为主赔率
ODDS_SEL = ".odds"

# 折中等待:SPA 冷启动要 8-10s,热启动(同一 browser)可降至 4s
SPA_WAIT = 8


def normalize_team(name):
    """Flashscore 队名→项目标准队名。"""
    name = re.sub(r"\s+", " ", name).strip()
    name = name.replace("&", "and")
    m = {
        "Czech Republic": "Czech",
        "Curacao": "Curaçao",
        "DR Congo": "DR Congo",
    }
    return m.get(name, name)


def load_wc_teams():
    """参赛队白名单。"""
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


def extract_matches_from_page(pg, url, label):
    """渲染 SPA 页,提取 [(home, away, score_or_None, href)]。"""
    pg.goto(url, wait_until="domcontentloaded", timeout=60000)
    try:
        pg.wait_for_selector(MATCH_A_SEL, timeout=25000)
    except Exception:
        print(f"  [{label}] ⚠ 无比赛链接")
        return []
    pg.wait_for_timeout(5000)  # 确保 JS 完成渲染

    out = []
    links = pg.query_selector_all(MATCH_A_SEL)
    for a in links:
        href = a.get_attribute("href")
        text = a.inner_text().strip()
        if not href or not text:
            continue
        # text = "Czech Republic - South Africa" (主页显示如此,无比分数)
        parts = [p.strip() for p in text.split("-", 1)]
        if len(parts) != 2:
            continue
        home, away = normalize_team(parts[0]), normalize_team(parts[1])
        if home and away:
            out.append((home, away, None, href))
    return out


def extract_1x2_from_detail(pg, href):
    """访问比赛详情页,取第一组 .odds 作为 1X2 赔率。
    返回 (oh, od, oa) 或 None。"""
    url = "https://www.flashscore.com" + href
    try:
        pg.goto(url, wait_until="domcontentloaded", timeout=60000)
        try:
            pg.wait_for_selector(ODDS_SEL, timeout=20000)
        except Exception:
            pass
        pg.wait_for_timeout(SPA_WAIT * 1000)
    except Exception as e:
        print(f"    ⚠ 加载失败({str(e)[:40]})")
        return None

    # 第一组 .odds (主赔率,含 3 个值用 \n 分隔)
    odds_els = pg.query_selector_all(ODDS_SEL)
    if len(odds_els) >= 3:
        try:
            oh = float(odds_els[0].inner_text().strip())
            od = float(odds_els[1].inner_text().strip())
            oa = float(odds_els[2].inner_text().strip())
            if oh > 1 and od > 1 and oa > 1:
                return (oh, od, oa)
        except ValueError:
            # 有的 .odds 放进整组文本 "\n1.61\n4.21\n6.36"
            txt = odds_els[0].inner_text().strip()
            vals = txt.replace("\n", " ").strip().split()
            if len(vals) >= 3:
                try:
                    oh, od, oa = float(vals[0]), float(vals[1]), float(vals[2])
                    if oh > 1 and od > 1 and oa > 1:
                        return (oh, od, oa)
                except ValueError:
                    pass
    return None


def extract_ou_from_detail(pg):
    """从当前详情页提取 OU 赔率(需先点 ODDS→OVER/UNDER)。
    返回 (ou_over, ou_under) 或 None。"""
    # 点 ODDS 主 tab
    try:
        has_odds_tab = pg.evaluate(
            """() => [...document.querySelectorAll('[data-testid="wcl-tab"]')]
            .some(t => t.innerText.trim() === 'ODDS')"""
        )
        if not has_odds_tab:
            return None
        pg.evaluate(
            """() => [...document.querySelectorAll('[data-testid="wcl-tab"]')]
            .find(t => t.innerText.trim() === 'ODDS')?.click()"""
        )
        pg.wait_for_timeout(3000)

        # 点 OVER/UNDER 子 tab
        has_ou = pg.evaluate(
            """() => [...document.querySelectorAll('[data-testid="wcl-tab"]')]
            .some(t => t.innerText.trim() === 'OVER/UNDER')"""
        )
        if not has_ou:
            return None
        pg.evaluate(
            """() => [...document.querySelectorAll('[data-testid="wcl-tab"]')]
            .find(t => t.innerText.trim() === 'OVER/UNDER')?.click()"""
        )
        pg.wait_for_timeout(12000)  # OU 表格异步渲染
    except Exception:
        return None

    # 从正文提取:找 2.5 行之后的 over/under 值(第一组为主赔率)
    try:
        body = pg.evaluate("() => document.body.innerText")
        lines = [l.strip() for l in body.split("\n")]
        for i, line in enumerate(lines):
            if line == "2.5" and i + 2 < len(lines):
                over_val = lines[i + 1]
                under_val = lines[i + 2]
                try:
                    ov = float(over_val)
                    uv = float(under_val)
                    if ov > 1 and uv > 1:
                        return (ov, uv)
                except ValueError:
                    continue
    except Exception:
        pass
    return None


def merge_1x2(rows):
    """rows: [(home,away,score,oh,od,oa)] → merge 进 odds_wc.csv。

    保留已有 2026 行作为 fallback(博彩方比赛结束后撤盘,重跑脚本时
    无赔率返回会导致历史盘口被清空)。
    """
    by_key = {}
    if os.path.exists(ODDS_CSV):
        with open(ODDS_CSV, newline="", encoding="utf-8") as f:
            for r in csv.DictReader(f):
                by_key[(r["year"], r["home"], r["away"])] = r
    # 保留已有 2026 行(防止"全量替换"清掉已结束比赛的盘口)
    preserved = {k: v for k, v in by_key.items() if k[0] == "2026"}
    merged = {k: v for k, v in by_key.items() if k[0] != "2026"}
    added = 0
    for home, away, score, oh, od, oa in rows:
        merged[("2026", home, away)] = {
            "year": "2026", "date": "", "home": home, "away": away,
            "score": score or "", "odds_h": f"{oh:.2f}", "odds_d": f"{od:.2f}",
            "odds_a": f"{oa:.2f}",
        }
        added += 1
    # 本次未抓到但之前已有的 2026 行 → 保留(已结束比赛撤盘后仍可用作回测)
    kept = 0
    for k, v in preserved.items():
        if k not in merged:
            merged[k] = v
            kept += 1
    fields = ["year", "date", "home", "away", "score", "odds_h", "odds_d", "odds_a"]
    all_rows = sorted(merged.values(), key=lambda m: (str(m["year"]), m["home"], m["away"]))
    with open(ODDS_CSV, "w", newline="", encoding="utf-8") as f:
        w = csv.DictWriter(f, fieldnames=fields)
        w.writeheader()
        for r in all_rows:
            w.writerow({fn: r.get(fn, "") for fn in fields})
    kept_other = len(all_rows) - added
    return added, kept_other


def merge_ou(rows):
    """rows: [(home,away,oh,od,oa,ou_over,ou_under)] → merge 进 ou_wc.csv。

    保留已有 2026 行作为 fallback(已结束比赛 OU 盘会撤,重跑时
    无 OU 返回会清空历史盘口,影响回测)。
    """
    by_key = {}
    if os.path.exists(OU_CSV):
        with open(OU_CSV, newline="", encoding="utf-8") as f:
            for r in csv.DictReader(f):
                by_key[(r["year"], r["home"], r["away"])] = r
    # 保留已有 2026 行(防止"全量替换"清掉已结束比赛的盘口)
    preserved = {k: v for k, v in by_key.items() if k[0] == "2026"}
    merged = {k: v for k, v in by_key.items() if k[0] != "2026"}
    added = 0
    for home, away, oh, od, oa, oov, ou in rows:
        merged[("2026", home, away)] = {
            "year": "2026", "date": "", "home": home, "away": away,
            "odds_h": f"{oh:.2f}", "odds_d": f"{od:.2f}", "odds_a": f"{oa:.2f}",
            "ou_line": "2.5", "ou_over": f"{oov:.2f}" if oov else "",
            "ou_under": f"{ou:.2f}" if ou else "",
        }
        added += 1
    # 本次未抓到但之前已有的 2026 行 → 保留(已结束比赛撤盘后仍可用作回测)
    kept = 0
    for k, v in preserved.items():
        if k not in merged:
            merged[k] = v
            kept += 1
    fields = ["year", "date", "home", "away", "odds_h", "odds_d", "odds_a",
              "ou_line", "ou_over", "ou_under"]
    all_rows = sorted(merged.values(), key=lambda m: (str(m["year"]), m["home"], m["away"]))
    with open(OU_CSV, "w", newline="", encoding="utf-8") as f:
        w = csv.DictWriter(f, fieldnames=fields)
        w.writeheader()
        for r in all_rows:
            w.writerow({fn: r.get(fn, "") for fn in fields})
    kept_other = len(all_rows) - added
    return added, kept_other


def main():
    ap = argparse.ArgumentParser(description="从 Flashscore 抓 2026 世界杯赔率")
    ap.add_argument("--dry", action="store_true", help="只打印不写")
    ap.add_argument("--no-ou", action="store_true", help="跳过 OU")
    ap.add_argument("--max", type=int, default=0, help="限抓 N 场(0=全部)")
    args = ap.parse_args()

    from playwright.sync_api import sync_playwright

    print("=" * 60)
    print("  Flashscore 2026 世界杯赔率采集")
    print("=" * 60)
    wc_set = load_wc_teams()
    print(f"  参赛队白名单: {len(wc_set) if wc_set else 0} 队")

    collected_1x2 = []
    collected_ou = []

    with sync_playwright() as p:
        b = p.chromium.launch(headless=True)
        pg = b.new_page(viewport={"width": 1280, "height": 800})

        # 第 1 步:扫已赛+未赛概览页,收集 match 链接
        all_matches = []
        for url, label in [(WC_RESULTS, "已赛"), (WC_FIXTURES, "未赛")]:
            rows = extract_matches_from_page(pg, url, label)
            if wc_set:
                before = len(rows)
                rows = [r for r in rows if r[0] in wc_set and r[1] in wc_set]
                print(f"  [{label}] {before} 场 → 过滤后 {len(rows)} 场 WC2026")
            else:
                print(f"  [{label}] {len(rows)} 场")
            all_matches.extend(rows)

        # 去重(同对阵用后者,尽量选已赛页的)
        seen = {}
        for m in reversed(all_matches):
            seen[(m[0], m[1])] = m
        all_matches = list(reversed(list(seen.values())))

        print(f"\n  去重后 {len(all_matches)} 场,开始逐场采集赔率...")
        if args.max:
            all_matches = all_matches[:args.max]
            print(f"  (限抓前 {args.max} 场)")

        # 第 2 步:逐场访问详情页
        t0 = time.time()
        for idx, (home, away, score, href) in enumerate(all_matches, 1):
            elapsed = time.time() - t0
            print(f"  [{idx}/{len(all_matches)}] {home:20s} vs {away:20s}  "
                  f"({elapsed:.0f}s)", end="", flush=True)

            odds3 = extract_1x2_from_detail(pg, href)
            if odds3 is None:
                print(" ✗ 无赔率")
                continue
            oh, od, oa = odds3
            collected_1x2.append((home, away, score, oh, od, oa))
            info = f" ✓ 1X2 {oh:.2f}/{od:.2f}/{oa:.2f}"

            if not args.no_ou:
                ou = extract_ou_from_detail(pg)
                if ou:
                    collected_ou.append((home, away, oh, od, oa, ou[0], ou[1]))
                    info += f"  OU {ou[0]:.2f}/{ou[1]:.2f}"
            print(info)

        b.close()

    print("-" * 60)
    print(f"  总计:1X2 {len(collected_1x2)} 场, OU {len(collected_ou)} 场")
    print(f"  用时: {time.time() - t0:.0f}s")

    if args.dry:
        print("  --dry,不写 CSV")
        return

    if collected_1x2:
        a1, k1 = merge_1x2(collected_1x2)
        print(f"✓ odds_wc.csv: 新增 {a1} 场(总保留 {k1} 场)")
    if collected_ou:
        a2, k2 = merge_ou(collected_ou)
        print(f"✓ ou_wc.csv: 新增 {a2} 场(总保留 {k2} 场)")
    print("  done.")


if __name__ == "__main__":
    main()
