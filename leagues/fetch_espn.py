# leagues/fetch_espn.py
"""ESPN scoreboard 抓取:按月迭代(dates=YYYYMM)拿全季赛程 + 结果 + 中立场标记。

已验证事实(2026-10-09,spec §2 D3):
  · range 语法 dates=20260801-20270731 不支持(返回空),必须按月迭代
  · limit 默认 50,欧冠大月份会截断 → 必须 limit=400(Review Focus 2)
  · 空 events = 该月无数据(休赛期或未发布),按缺月记录(N-3),不视为错误
  · 欧冠淘汰赛比分是 90 分钟结果(加时/点球不计),DC/Elo 直接用这个语义,
    不做加时修正(Review Focus 5,刻意决策,勿"修复")
"""
import time

import requests

from . import config, fetch_fd, normalize

TIMEOUT = 30
RETRIES = 3


def season_months(season):
    """'2627' → ['202608', ..., '202707'](欧洲赛季 8 月开、7 月止)。"""
    y1, y2 = config.season_years(season)
    return [f"{y1}{m:02d}" for m in range(8, 13)] + [f"{y2}{m:02d}" for m in range(1, 8)]


def espn_month(espn, yyyymm):
    """单月 scoreboard,返回 events list。异常重试 RETRIES 次后抛出(调用方计缺月)。"""
    url = config.ESPN_SCOREBOARD.format(espn=espn)
    last = None
    for i in range(RETRIES):
        try:
            r = requests.get(url, params={"dates": yyyymm, "limit": 400}, timeout=TIMEOUT)
            r.raise_for_status()
            return r.json().get("events", [])
        except Exception as e:
            last = e
            if i < RETRIES - 1:
                time.sleep(2.0 ** i)
    raise last


def parse_event(ev, aliases):
    """单个 event → schedule 行 dict;结构不完整返回 None。

    status 取 ESPN 原生 state:pre(未赛)/in(进行中)/post(完赛);只有 post 记比分。
    neutral 来自 competitions[0].neutralSite(欧冠决赛/中立轮次)。
    """
    comps = ev.get("competitions") or []
    if not comps:
        return None
    comp = comps[0]
    sides = {}
    for c in comp.get("competitors", []):
        name = config.canonical((c.get("team") or {}).get("displayName", ""), aliases)
        sides[c.get("homeAway")] = (name, normalize.to_int(c.get("score")))
    if "home" not in sides or "away" not in sides:
        return None
    (home, hs), (away, as_) = sides["home"], sides["away"]
    state = ((ev.get("status") or {}).get("type") or {}).get("state", "")
    dt = ev.get("date") or ""
    if len(dt) < 16 or not home or not away or state not in ("pre", "in", "post"):
        return None
    return {
        "date": dt[:10], "time": dt[11:16], "home": home, "away": away,
        "status": state,
        "gh": hs if state == "post" and hs is not None else "",
        "ga": as_ if state == "post" and as_ is not None else "",
        "neutral": bool(comp.get("neutralSite")),
    }


def _fetch_monthly(code, season, aliases):
    """按月迭代全季。空 events 或请求异常均计为缺月(N-3)。返回 (rows, missing)。"""
    espn = config.LEAGUES[code]["espn"]
    rows, missing = [], []
    for ym in season_months(season):
        try:
            events = espn_month(espn, ym)
        except Exception:
            missing.append(ym)
            continue
        if not events:
            missing.append(ym)   # 休赛期月份固定为空 → missing 仅供日志观察,不致命
            continue
        for ev in events:
            row = parse_event(ev, aliases)
            if row:
                rows.append(row)
    rows.sort(key=lambda r: (r["date"], r["time"]))
    return rows, missing


def fetch_schedule(code, season=None):
    """抓联赛全季赛程写 schedule.csv。全空抛 RuntimeError。返回 (n_rows, missing)。"""
    season = season or config.current_season()
    rows, missing = _fetch_monthly(code, season, config.load_aliases())
    if not rows:
        raise RuntimeError(f"{code} {season}: ESPN 全季无数据")
    fetch_fd.write_csv(config.paths(code)["schedule"], normalize.SCHEDULE_FIELDS, rows)
    return len(rows), missing


def fetch_ucl_history(start_year=config.UCL_HISTORY_START, end_year=None):
    """抓欧冠历史(只留 post 完赛),写 UCL results.csv。

    返回 (n_rows, total_missing, degraded);degraded = 最长连续缺月 > 2
    (跨赛季休赛期属正常缺月,调用方只把 degraded 当警告日志)。
    """
    season = config.current_season()
    _, cur_end = config.season_years(season)
    end_year = end_year or cur_end
    aliases = config.load_aliases()
    all_rows, missing_all = [], []
    for y in range(start_year, end_year):
        s = f"{y % 100:02d}{(y + 1) % 100:02d}"
        rows, missing = _fetch_monthly("UCL", s, aliases)
        for r in rows:
            if r["status"] == "post":
                all_rows.append({"date": r["date"], "home": r["home"], "away": r["away"],
                                 "gh": r["gh"], "ga": r["ga"], "neutral": r["neutral"]})
        missing_all.extend(missing)
    if not all_rows:
        raise RuntimeError("UCL: 历史抓取为空")
    all_rows.sort(key=lambda r: (r["date"], r["home"]))
    fetch_fd.write_csv(config.paths("UCL")["results"], normalize.UCL_RESULT_FIELDS, all_rows)
    return len(all_rows), len(missing_all), _max_consecutive(missing_all) > 2


def _max_consecutive(months):
    """缺月列表(YYYYMM)中最长连续段长度。"""
    best = cur = 0
    prev = None
    for ym in sorted(set(months)):
        idx = int(ym[:4]) * 12 + int(ym[4:])
        cur = cur + 1 if prev is not None and idx == prev + 1 else 1
        best = max(best, cur)
        prev = idx
    return best
