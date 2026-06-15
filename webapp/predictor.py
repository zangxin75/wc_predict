"""predictor.py — 预测封装层(复用现有引擎,零重写)

把 compute_elo / wc_predict / tournament_sim 的能力包装成 web 层可直接用的
纯函数接口。本模块不读全局状态、不持有缓存(缓存归 cache.py),只做"给定
Elo 和数据文件,产出前端要的视图"。

核心产出:
  build_all(elo)        → (schedule_view, groups_view)  全量赛程富化 + 小组榜
  predict_one(home, away, elo) → dict  单场预测详情
  run_monte_carlo(n)    → list   夺冠榜(中文队名)
  enrich_match(...)     → dict   ★ learning 点:赛程→前端卡片数据契约

数据源(只读):
  wc2026_schedule.csv: date,home_team,away_team,group,stage,city
  results.csv:         date,home_team,away_team,home_score,away_score,tournament,city,country,neutral
"""

import csv
import datetime
import os
import sys

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8")

import wc_predict
import tournament_sim
import compute_elo

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
RESULTS = os.path.join(ROOT, "results.csv")
SCHEDULE = os.path.join(ROOT, "wc2026_schedule.csv")

EN_TO_ZH = wc_predict.EN_TO_ZH

# 东道主主场 Elo 加成 —— 统一用英文键(与 Elo dict / schedule 一致)。
# 注意:wc_predict.Config.HOST_NATION_ADV 用中文键,verify_2026/run_live 用英文键,
# web 层统一用英文键,消除不一致。
HOST_ADV_EN = {"Mexico": 90, "United States": 70, "Canada": 40}


def _result_of(hs, as_):
    """比分 → 三态字符串(home_win/draw/away_win)。"""
    if hs > as_:
        return "home_win"
    if hs < as_:
        return "away_win"
    return "draw"


def predict_one(home, away, elo):
    """单场预测。调 wc_predict.predict_from_elo,返回前端友好 dict。

    home/away 为英文队名(匹配 elo 键)。东道主 home 加 HOST_ADV_EN[home]。
    返回 None 表示 Elo 缺失(无法预测)。
    """
    eh, ea = elo.get(home), elo.get(away)
    if eh is None or ea is None:
        return None
    adv = HOST_ADV_EN.get(home, 0)
    hw, dr, aw, _matrix, top = wc_predict.predict_from_elo(eh, ea, home_adv_elo=adv)
    lam_h, lam_a = wc_predict.elo_to_lambdas(eh, ea, home_adv=adv)
    return {
        "probs": [round(hw, 4), round(dr, 4), round(aw, 4)],
        "top_score": [top[0][0][0], top[0][0][1]],
        "top5": [[s[0][0], s[0][1], round(s[1], 4)] for s in top],
        "lam_h": round(lam_h, 3), "lam_a": round(lam_a, 3),
        "elo_h": round(eh), "elo_a": round(ea),
        "host_adv": adv, "neutral": adv == 0,
    }


def load_results_map():
    """读 results.csv → {(date, home, away): {hs, as_, is_na}}。

    is_na=True 表示该场预填了 NA 占位(未打)。用于 enrich_match 判 status。
    """
    m = {}
    if not os.path.exists(RESULTS):
        return m
    with open(RESULTS, newline="", encoding="utf-8") as f:
        for r in csv.reader(f):
            if not r or r[0] == "date" or len(r) < 5:
                continue
            hs, as_ = r[3], r[4]
            is_na = (hs == "NA" or as_ == "NA"
                     or not hs.lstrip("-").isdigit()
                     or not as_.lstrip("-").isdigit())
            m[(r[0], r[1], r[2])] = {
                "hs": None if is_na else int(hs),
                "as_": None if is_na else int(as_),
                "is_na": is_na,
            }
    return m


def read_schedule():
    """读 wc2026_schedule.csv → 行列表(已按 date 升序)。"""
    rows = []
    if not os.path.exists(SCHEDULE):
        return rows
    with open(SCHEDULE, newline="", encoding="utf-8") as f:
        for r in csv.reader(f):
            if not r or r[0] == "date" or len(r) < 6:
                continue
            rows.append(r)
    rows.sort(key=lambda r: r[0])
    return rows


def enrich_match(sched_row, elo, results_map):
    """★【learning 点】把一行赛程 + Elo + 已打结果融合成前端卡片数据契约。

    这是"数据 → 视觉"的关键翻译层:本函数返回的字段直接决定前端每张卡片
    长什么样、渲染什么。当前实现是一个**可用的默认版**,你可以在三个有真实
    取舍的地方调整它来改变面板行为:

    ── 取舍 1:status 分级(当前三级)──────────────────────────────
      finished : results.csv 里有该场且比分非 NA(已打完)
      today    : date < 今天但仍无比分(已开赛/进行中/待补录)
      upcoming : date >= 今天(未开赛)
      想要更细?可加 'live'(需 ESPN scoreboard 的 status 字段:in/final)。
      分级越细前端越精细,但判定逻辑越复杂、越依赖外部状态。

    ── 取舍 2:已打场是否带预测(当前:带)────────────────────────
      当前 finished 场也返回 probs,前端可选展示"赛前预测 vs 实际结果"
      (让用户直观看到模型准不准)。若只想看结果,可在此 return 前删掉
      finished 场的 probs,payload 更小(轮询 30s × 104 场要考虑体积)。

    ── 取舍 3:字段取舍(当前:全集)──────────────────────────────
      返回 {date, home, away, home_zh, away_zh, group, stage, city,
            status, score, result, probs, top_score, top5, elo_h, elo_a,
            lam_h, lam_a, host_adv, neutral}。精简可减轮询体积,但前端灵活性降。

    签名固定(sched_row/elo/results_map),改内部逻辑不影响调用方。
    """
    date, home, away, group, stage, city = sched_row[0], sched_row[1], sched_row[2], sched_row[3], sched_row[4], sched_row[5]
    today = datetime.date.today().isoformat()

    m = {
        "date": date, "home": home, "away": away,
        "home_zh": EN_TO_ZH.get(home, home),
        "away_zh": EN_TO_ZH.get(away, away),
        "group": group, "stage": stage, "city": city,
    }

    # 取舍 1:status 分级
    res = results_map.get((date, home, away))
    if res and not res["is_na"]:
        m["status"] = "finished"
        m["score"] = [res["hs"], res["as_"]]
        m["result"] = _result_of(res["hs"], res["as_"])
    elif date < today:
        m["status"] = "today"
    else:
        m["status"] = "upcoming"

    # 取舍 2:已打场也带预测(供前端可选展示"预测 vs 实际")
    pred = predict_one(home, away, elo)
    if pred:
        m.update(pred)
    else:
        m["probs"] = None
    return m


def build_all(elo):
    """遍历全部赛程 → 富化每场 + 算小组榜。返回 (schedule_view, groups_view)。

    给定 Elo(后台线程已用最新 results 重建),产出 API 直接返回的视图。
    """
    results_map = load_results_map()
    sched = [enrich_match(row, elo, results_map) for row in read_schedule()]
    groups = compute_groups(sched)
    return sched, groups


def compute_groups(schedule_view):
    """12 组积分榜:已打场真实积分 + 未打场预测期望积分。

    排序键:(期望积分 exp_pts 降序, 净胜球 gd 降序, 已得积分 pts 降序)。
    exp_pts = 已得真实积分 + Σ未打场(3·P胜 + 1·P平)。
    """
    groups = tournament_sim.load_groups()   # {组字母: [4 队英文]}
    out = {}
    for g, teams in groups.items():
        g_matches = [m for m in schedule_view
                     if m.get("group") == g and m.get("stage") == "Group"]
        rows = []
        for t in teams:
            pts = gf = ga = played = 0
            exp_extra = 0.0
            for m in g_matches:
                if t not in (m["home"], m["away"]):
                    continue
                is_home = m["home"] == t
                if m["status"] == "finished":
                    played += 1
                    hs, as_ = m["score"]
                    our, opp = (hs, as_) if is_home else (as_, hs)
                    gf += our
                    ga += opp
                    pts += 3 if our > opp else (1 if our == opp else 0)
                elif m.get("probs"):
                    p = m["probs"]
                    p_win = p[0] if is_home else p[2]
                    p_draw = p[1]
                    exp_extra += 3.0 * p_win + 1.0 * p_draw
            rows.append({
                "team": t, "team_zh": EN_TO_ZH.get(t, t),
                "pts": pts, "exp_pts": round(pts + exp_extra, 2),
                "gd": gf - ga, "gf": gf, "ga": ga, "played": played,
            })
        rows.sort(key=lambda r: (-r["exp_pts"], -r["gd"], -r["pts"]))
        out[g] = rows
    return out


def run_monte_carlo(n=10000, seed=None):
    """夺冠榜。调 tournament_sim.monte_carlo(内部自建 Elo + n 次模拟)。

    成本高(数秒~分钟),cache 层只在启动/每日/手动时调用,绝不进 15 分钟循环。
    返回 [{team, team_zh, count, prob}, ...] 降序。
    """
    ranking = tournament_sim.monte_carlo(n, seed)
    return [{"team": t, "team_zh": EN_TO_ZH.get(t, t),
             "count": c, "prob": round(p, 4)} for t, c, p in ranking]


def build_elo_for_today():
    """用今天为 cutoff 重建 Elo(后台更新器每轮调用)。返回 {英文队名: Elo}。"""
    today = datetime.date.today().isoformat()
    elo, _, _ = compute_elo.build_elo(RESULTS, cutoff_date=today)
    return elo
