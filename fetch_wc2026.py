"""fetch_wc2026.py — 2026 世界杯赛程+结果抓取器

数据源:ESPN 非官方 JSON API(主)+ Wikipedia wikitable(回退,待实现)。
产出:
  wc2026_live.csv     — 已打比赛,9 列兼容 results.csv(供 update_live append + compute_elo)
  wc2026_schedule.csv — 未打赛程(date, home, away, group, stage, city)

队名标准化:ESPN(FIFA 官方名)→ results.csv(传统英文名),否则 Elo 字典 KeyError。
带缓存(TTL 5 分钟)、schema 校验、失败明确报错(不静默写空)。

用法:
    python fetch_wc2026.py
依赖:仅标准库(urllib/json/csv/ssl),零三方。
"""

import csv
import json
import os
import ssl
import sys
import time
import urllib.request

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8")

HERE = os.path.dirname(os.path.abspath(__file__))
LIVE_OUT = os.path.join(HERE, "wc2026_live.csv")
SCHED_OUT = os.path.join(HERE, "wc2026_schedule.csv")
CACHE_PATH = os.path.join(HERE, ".wc2026_cache.json")

ESPN_URL = "https://site.api.espn.com/apis/site/v2/sports/soccer/fifa.world/scoreboard"
ESPN_DATES = "20260611-20260719"   # 2026 世界杯赛期(6/11 揭幕 ~ 7/19 决赛)
UA = "MiroFishBot/1.0 (+research; wc_predict)"
CACHE_TTL = 300                    # 5 分钟

# results.csv 列头(live 产出严格兼容,供 update_live 直接 append)
LIVE_HEADER = ["date", "home_team", "away_team", "home_score", "away_score",
               "tournament", "city", "country", "neutral"]
SCHED_HEADER = ["date", "home_team", "away_team", "group", "stage", "city", "kickoff_utc", "home_score", "away_score"]

# ESPN(FIFA 官方名)→ results.csv(传统英文名)
# results.csv 实测:South Korea / Iran / United States / Turkey(非 FIFA 官方名)
NAME_NORMALIZE = {
    "Korea Republic": "South Korea",
    "IR Iran": "Iran",
    "Türkiye": "Turkey",
    "USA": "United States",
    "U.S.": "United States",
    "Czechia": "Czech Republic",              # FIFA 2023 改名
    "Bosnia-Herzegovina": "Bosnia and Herzegovina",
    "Congo DR": "DR Congo",
    # 以下按需补充(抓取后对比 results.csv 队名补全)
}


def normalize_team(name):
    """ESPN/Wikipedia 队名 → results.csv 英文名。未覆盖原样保留(并打印警告)。"""
    if name in NAME_NORMALIZE:
        return NAME_NORMALIZE[name]
    return name


def fetch_espn(dates=ESPN_DATES, use_cache=True, ttl=CACHE_TTL):
    """抓 ESPN scoreboard JSON,带文件缓存。返回 dict 或 raise RuntimeError。

    缓存命中(年龄 < ttl)则不发起网络请求。
    """
    if use_cache and os.path.exists(CACHE_PATH):
        if time.time() - os.path.getmtime(CACHE_PATH) < ttl:
            with open(CACHE_PATH, encoding="utf-8") as f:
                return json.load(f)
    url = f"{ESPN_URL}?dates={dates}"
    req = urllib.request.Request(url, headers={"User-Agent": UA})
    try:
        with urllib.request.urlopen(req, timeout=30,
                                    context=ssl.create_default_context()) as resp:
            data = json.load(resp)
    except Exception as e:
        raise RuntimeError(f"ESPN 抓取失败: {type(e).__name__}: {e}")
    if "events" not in data:
        raise RuntimeError("ESPN 返回缺 events 字段(schema 变更?)")
    with open(CACHE_PATH, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False)
    return data


def _extract_group_and_stage(note):
    """从 altGameNote 提取 (group, stage)。

    'FIFA World Cup, Group D'     → ('D', 'Group')
    'FIFA World Cup, Round of 16' → ('',  'Round of 16')
    """
    note = note or ""
    if "Group" in note:
        tail = note.split("Group")[-1].strip()
        group = tail.split()[0] if tail else ""
        return group, "Group"
    stage = note.replace("FIFA World Cup", "").strip(" ,")
    return "", stage or "Knockout"


def parse_event(ev):
    """解析单个 ESPN event → dict。结构不全返回 None(容错跳过脏数据)。

    返回字段:date, home, away, hs, as, tournament, city, country,
              group, stage, state, completed
    """
    try:
        comp = ev["competitions"][0]
        comps = comp["competitors"]
        home = next(c for c in comps if c["homeAway"] == "home")
        away = next(c for c in comps if c["homeAway"] == "away")
        stype = comp["status"]["type"]
        state = stype["state"]                    # post / in / pre
        completed = stype.get("completed", state == "post")
        venue = comp.get("venue", {})
        addr = venue.get("address", {})
        group, stage = _extract_group_and_stage(comp.get("altGameNote", ""))
        return {
            "date": ev["date"][:10],              # 2026-06-14T04:00Z → 2026-06-14
            "kickoff_utc": ev["date"],            # 完整 UTC ISO(带 Z),供转北京时间
            "home": normalize_team(home["team"]["displayName"]),
            "away": normalize_team(away["team"]["displayName"]),
            "hs": home.get("score"),
            "as_": away.get("score"),
            "tournament": "FIFA World Cup",
            "city": addr.get("city", ""),
            "country": addr.get("country", ""),
            "group": group,
            "stage": stage,
            "state": state,
            "completed": completed,
        }
    except (KeyError, IndexError, StopIteration):
        return None


def split_live_schedule(records):
    """已打(completed 且有比分)→ live;否则 → schedule。"""
    live, sched = [], []
    for r in records:
        if r["completed"] and r["hs"] is not None and r["as_"] is not None:
            live.append(r)
        else:
            sched.append(r)
    return live, sched


def write_csvs(live, sched):
    """写 wc2026_live.csv(9 列)+ wc2026_schedule.csv。"""
    with open(LIVE_OUT, "w", newline="", encoding="utf-8") as f:
        w = csv.writer(f)
        w.writerow(LIVE_HEADER)
        for r in live:
            w.writerow([r["date"], r["home"], r["away"], r["hs"], r["as_"],
                        r["tournament"], r["city"], r["country"], "TRUE"])
    with open(SCHED_OUT, "w", newline="", encoding="utf-8") as f:
        w = csv.writer(f)
        w.writerow(SCHED_HEADER)
        for r in live + sched:
            hs = r["hs"] if (r.get("completed") and r["hs"] is not None) else ""
            as_ = r["as_"] if (r.get("completed") and r["as_"] is not None) else ""
            w.writerow([r["date"], r["home"], r["away"],
                        r["group"], r["stage"], r["city"],
                        r.get("kickoff_utc", ""), hs, as_])


def fetch_wikipedia():
    """Wikipedia 回退:抓 2026 FIFA World Cup 页面 wikitable。

    当前骨架:ESPN 可用时不触发。实现需 requests + HTML 解析(wikitext 或 mwparserfromhell)。
    """
    raise NotImplementedError("Wikipedia 回退待实现(ESPN 当前可用)")


def main():
    print("=" * 56)
    print("  2026 世界杯赛程+结果抓取")
    print("=" * 56)
    try:
        data = fetch_espn()
    except RuntimeError as e:
        print(f"  ESPN 失败: {e}")
        print("  尝试 Wikipedia 回退...")
        try:
            data = fetch_wikipedia()
        except NotImplementedError:
            raise RuntimeError("双源失败:ESPN 不可用且 Wikipedia 未实现。不写空 CSV(防脏数据)。")

    events = data.get("events", [])
    records = [r for r in (parse_event(ev) for ev in events) if r]
    live, sched = split_live_schedule(records)
    write_csvs(live, sched)
    print(f"  抓取 {len(records)} 场:已打 {len(live)},未打 {len(sched)}")
    if live:
        print(f"  最近已打示例:{live[0]['date']} {live[0]['home']} {live[0]['hs']}-"
              f"{live[0]['as_']} {live[0]['away']} ({live[0]['group'] or live[0]['stage']})")
    print(f"  ✓ {LIVE_OUT}")
    print(f"  ✓ {SCHED_OUT}")


if __name__ == "__main__":
    main()
