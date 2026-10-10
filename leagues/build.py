# leagues/build.py
"""构建管线:CSV → Elo 池 / DC 参数 / 积分榜 / 单场预测。

核心不变式:rebuild 每次从最新 schedule **全量重建** predictions 字典
(fixtures.csv 是滚动 4 天窗口,改期后旧键必须消失,绝不增量 patch —— Review Focus 3)。
"""
import csv
import json
import os
from datetime import date

from . import config, dixon_coles, elo as elo_mod, predict as predict_mod


def read_csv(path):
    if not os.path.exists(path):
        return []
    with open(path, encoding="utf-8") as f:
        return list(csv.DictReader(f))


def _num(row, keys):
    out = dict(row)
    for k in keys:
        v = out.get(k)
        if v in (None, ""):
            out[k] = None
        else:
            try:
                out[k] = float(v)
            except (TypeError, ValueError):
                out[k] = None
    return out


def _goals_int(row):
    for k in ("gh", "ga"):
        row[k] = int(row[k]) if row.get(k) is not None else None
    return row


def _bool_neutral(row):
    row["neutral"] = str(row.get("neutral", "")).strip().lower() in ("true", "1")
    return row


def load_results(code):
    out = []
    for r in read_csv(config.paths(code)["results"]):
        r = _num(r, ("gh", "ga", "b365h", "b365d", "b365a", "ps_h", "ps_d", "ps_a",
                     "ou_over", "ou_under", "ah_line", "ah_h", "ah_a"))
        out.append(_bool_neutral(_goals_int(r)) | {"comp": code})
    return out


def load_schedule(code):
    out = []
    for r in read_csv(config.paths(code)["schedule"]):
        out.append(_bool_neutral(_goals_int(_num(r, ("gh", "ga")))))
    return out


def load_fixtures(code):
    return [_num(r, ("b365h", "b365d", "b365a", "max_h", "max_d", "max_a",
                     "ps_h", "ps_d", "ps_a", "ou_over", "ou_under",
                     "ah_line", "ah_h", "ah_a"))
            for r in read_csv(config.paths(code)["fixtures"])]


def compute_standings(results, season):
    """3/1/0 积分,排序 pts → gd → gf → 队名。只算指定 season 的已完赛。"""
    table = {}
    for r in results:
        if r.get("season") != season or r["gh"] is None or r["ga"] is None:
            continue
        h, a = r["home"], r["away"]
        for t in (h, a):
            table.setdefault(t, {"team": t, "p": 0, "w": 0, "d": 0, "l": 0,
                                 "gf": 0, "ga": 0, "gd": 0, "pts": 0})
        th, ta = table[h], table[a]
        th["p"] += 1
        ta["p"] += 1
        th["gf"] += r["gh"]
        th["ga"] += r["ga"]
        ta["gf"] += r["ga"]
        ta["ga"] += r["gh"]
        if r["gh"] > r["ga"]:
            th["w"] += 1
            th["pts"] += 3
            ta["l"] += 1
        elif r["gh"] == r["ga"]:
            th["d"] += 1
            ta["d"] += 1
            th["pts"] += 1
            ta["pts"] += 1
        else:
            ta["w"] += 1
            ta["pts"] += 3
            th["l"] += 1
    for t in table.values():
        t["gd"] = t["gf"] - t["ga"]
    return sorted(table.values(), key=lambda t: (-t["pts"], -t["gd"], -t["gf"], t["team"]))


def build_elo_pool(cutoff=None):
    """全 ALL_CODES 历史 → 统一 Elo 池,落盘 elo.json + elo_history.csv。返回 (ratings, history)。"""
    from . import fetch_fd
    matches = []
    for code in config.ALL_CODES:
        matches.extend(load_results(code))
    ratings, history = elo_mod.build_pool(matches, cutoff=cutoff)
    gp = config.global_paths()
    with open(gp["elo"] + ".tmp", "w", encoding="utf-8") as f:
        json.dump(ratings, f, ensure_ascii=False)
    os.replace(gp["elo"] + ".tmp", gp["elo"])
    fetch_fd.write_csv(gp["elo_history"],
                       ["date", "home", "away", "elo_h", "elo_a", "comp"], history)
    return ratings, history


def rebuild_dc(code, cutoff=None):
    """拟合并落盘 dc_params.json。返回 {"league","params","global"}。"""
    params, glob = dixon_coles.fit_league(load_results(code), cutoff=cutoff)
    payload = {"league": code, "params": params, "global": glob}
    _atomic_json(config.paths(code)["dc_params"], payload)
    return payload


def _load_dc(code):
    p = config.paths(code)["dc_params"]
    if os.path.exists(p):
        with open(p, encoding="utf-8") as f:
            return json.load(f)
    return rebuild_dc(code)


def _atomic_json(path, payload):
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path + ".tmp", "w", encoding="utf-8") as f:
        json.dump(payload, f, ensure_ascii=False)
    os.replace(path + ".tmp", path)


def _xgb_probs(xgb, code, home, away):
    """xgb 参数是 {(code, home, away): probs} 查找表(Task 17 构建);缺省 None。"""
    if not xgb:
        return None
    return xgb.get((code, home, away))


def rebuild(code, elo=None, xgb=None):
    """从最新 schedule 全量重建 predictions.json。返回 payload dict。

    schedule.csv 就是当前赛季(Task 4 按赛季抓取),无需再过滤 season。
    status=='post' 行只带比分;'pre'/'in' 行出预测;fixtures 滚动窗口里的
    赔率按 (date,home,away) 精确匹配,匹配不上就无赔率(下周再抓自然补上)。
    """
    season = config.current_season()
    schedule = load_schedule(code)
    fixtures = {(f["date"], f["home"], f["away"]): f for f in load_fixtures(code)}
    if elo is None:
        elo = build_elo_pool()[0]
    dc = _load_dc(code)
    dc_params, dc_glob = dc["params"], dc["global"]
    matches = []
    for m in schedule:
        base = {"date": m["date"], "time": m.get("time", ""), "home": m["home"],
                "away": m["away"], "status": m["status"], "neutral": bool(m.get("neutral"))}
        if m["status"] == "post":
            base["gh"], base["ga"] = m["gh"], m["ga"]
        else:
            odds = fixtures.get((m["date"], m["home"], m["away"]))
            pred = predict_mod.predict_match(
                m["home"], m["away"], elo=elo, dc_params=dc_params, dc_glob=dc_glob,
                odds=odds, neutral=base["neutral"],
                xgb_probs=_xgb_probs(xgb, code, m["home"], m["away"]))
            base.update(pred)
            if odds:
                base["odds"] = {k: odds.get(k) for k in
                                ("b365h", "b365d", "b365a", "ou_over", "ou_under",
                                 "ah_line", "ah_h", "ah_a")}
        matches.append(base)
    payload = {"code": code, "season": season,
               "generated_at": date.today().isoformat(), "matches": matches}
    _atomic_json(config.paths(code)["predictions"], payload)
    return payload


def rebuild_all(elo=None, xgb=None):
    if elo is None:
        elo = build_elo_pool()[0]
    return {code: rebuild(code, elo=elo, xgb=xgb) for code in config.LEAGUE_CODES}


def fetch_all_if_stale(fixtures_h=20, results_h=20):
    """按文件新鲜度补抓(updater 用)。单联赛失败记 errors,不阻塞其他联赛。"""
    from . import fetch_espn, fetch_fd
    report = {"fd_history": {}, "fixtures": None, "schedules": {}, "ucl": None,
              "errors": []}
    for code in config.LEAGUE_CODES:
        if not fetch_fd.is_fresh(config.paths(code)["results"], results_h):
            try:
                n, failed = fetch_fd.fetch_league_history(code)
                report["fd_history"][code] = {"rows": n, "failed_seasons": failed}
            except Exception as e:
                report["errors"].append(f"fd_history {code}: {e}")
    # fixtures 一次抓全量拆分;用首个联赛的文件做新鲜度代理(同一次写入)
    try:
        probe = config.paths(config.LEAGUE_CODES[0])["fixtures"]
        if not fetch_fd.is_fresh(probe, fixtures_h):
            report["fixtures"] = fetch_fd.fetch_fixtures()
    except Exception as e:
        report["errors"].append(f"fixtures: {e}")
    for code in config.LEAGUE_CODES:
        if not fetch_fd.is_fresh(config.paths(code)["schedule"], fixtures_h):
            try:
                n, missing = fetch_espn.fetch_schedule(code)
                report["schedules"][code] = {"rows": n, "missing_months": missing}
            except Exception as e:
                report["errors"].append(f"schedule {code}: {e}")
    # UCL:赛程同新鲜度;历史回填重(11+ 赛季 × 12 月),results_h 阈值控制频率
    if not fetch_fd.is_fresh(config.paths("UCL")["schedule"], fixtures_h):
        try:
            n, missing = fetch_espn.fetch_schedule("UCL")
            report["schedules"]["UCL"] = {"rows": n, "missing_months": missing}
        except Exception as e:
            report["errors"].append(f"schedule UCL: {e}")
    if not fetch_fd.is_fresh(config.paths("UCL")["results"], results_h):
        try:
            n, total_missing, degraded = fetch_espn.fetch_ucl_history()
            report["ucl"] = {"rows": n, "missing": total_missing, "degraded": degraded}
        except Exception as e:
            report["errors"].append(f"ucl_history: {e}")
    return report


def rebuild_ucl(elo=None, ucl_odds=None):
    """UCL 预测全量重建:借用制 DC(fit_ucl_global)+ TheOddsAPI 赔率。

    low_confidence = 任一方 borrow.low_confidence(非五大联赛队,N-2)。
    新进欧冠、历史里没有的队不在 borrow_map → predict_match 自动回退纯 Elo
    并自带 low_confidence=True,两条路径一致。
    """
    season = config.current_season()
    schedule = load_schedule("UCL")
    if elo is None:
        elo = build_elo_pool()[0]
    league_params = {}
    for code in config.LEAGUE_CODES:
        for t, p in _load_dc(code)["params"].items():
            league_params.setdefault(t, p)
    glob, borrow = dixon_coles.fit_ucl_global(load_results("UCL"), league_params)
    _atomic_json(config.paths("UCL")["dc_params"],
                 {"league": "UCL", "params": borrow, "global": glob})
    matches = []
    for m in schedule:
        base = {"date": m["date"], "time": m.get("time", ""), "home": m["home"],
                "away": m["away"], "status": m["status"], "neutral": bool(m.get("neutral"))}
        if m["status"] == "post":
            base["gh"], base["ga"] = m["gh"], m["ga"]
        else:
            odds = (ucl_odds or {}).get((m["home"], m["away"]))
            low = (borrow.get(m["home"], {}).get("low_confidence", True)
                   or borrow.get(m["away"], {}).get("low_confidence", True))
            pred = predict_mod.predict_match(
                m["home"], m["away"], elo=elo, dc_params=borrow, dc_glob=glob,
                odds=odds, neutral=base["neutral"], low_confidence=low)
            base.update(pred)
            if odds:
                base["odds"] = {k: odds.get(k) for k in ("b365h", "b365d", "b365a")}
        matches.append(base)
    payload = {"code": "UCL", "season": season,
               "generated_at": date.today().isoformat(), "matches": matches}
    _atomic_json(config.paths("UCL")["predictions"], payload)
    return payload
