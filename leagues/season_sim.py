"""赛季推演:当前积分 + 剩余赛程(status=='pre')× N 次蒙特卡洛。

每场按 DC 比分矩阵采样比分,累计积分/净胜球/进球,排最终名次 →
夺冠 / 欧冠席位(ucl_spots)/ 降级(relegation_n)概率。
剩余球队若不在当前积分榜(升班马赛程先于结果入库)按 0 分并入。
"""
import random

from . import build, config
from .dixon_coles import expected_goals
from .predict import score_matrix


def _build_dists(remaining, params, glob):
    """每场预生成累积分布,采样时一次 rng.random() 定位。"""
    dists = []
    for m in remaining:
        lh, la = expected_goals(params, glob, m["home"], m["away"], bool(m.get("neutral")))
        mat = score_matrix(lh, la, glob.get("rho", 0.0))
        cum, s = [], 0.0
        for i, row in enumerate(mat):
            for j, p in enumerate(row):
                s += p
                cum.append((s, i, j))
        cum[-1] = (1.0, cum[-1][1], cum[-1][2])   # 浮点尾巴兜底
        dists.append((m["home"], m["away"], cum))
    return dists


def simulate_season(code, n_sims=10000, seed=None):
    """返回 {code, season, n_sims, teams:{team:{title,ucl,releg}}},并落盘 sim.json。"""
    season = config.current_season()
    results = build.load_results(code)
    standings = build.compute_standings(results, season)
    remaining = [m for m in build.load_schedule(code) if m["status"] == "pre"]
    dc = build._load_dc(code)
    params, glob = dc["params"], dc["global"]
    lg = config.LEAGUES[code]
    rng = random.Random(seed)

    base = {t["team"]: (t["pts"], t["gd"], t["gf"]) for t in standings}
    for m in remaining:   # 赛程里有但积分榜没有的队伍按 0 分并入
        for t in (m["home"], m["away"]):
            base.setdefault(t, (0, 0, 0))
    teams = sorted(base)
    counts = {t: {"title": 0, "ucl": 0, "releg": 0} for t in teams}
    dists = _build_dists(remaining, params, glob)
    ucl_n = min(lg["ucl_spots"], len(teams))
    rel_n = min(lg["relegation_n"], len(teams))

    for _ in range(n_sims):
        pts = {t: base[t][0] for t in teams}
        gd = {t: base[t][1] for t in teams}
        gf = {t: base[t][2] for t in teams}
        for h, a, cum in dists:
            r = rng.random()
            gh, ga = 0, 0
            for s, i, j in cum:
                if r <= s:
                    gh, ga = i, j
                    break
            if gh > ga:
                pts[h] += 3
            elif gh == ga:
                pts[h] += 1
                pts[a] += 1
            else:
                pts[a] += 3
            gd[h] += gh - ga
            gd[a] += ga - gh
            gf[h] += gh
            gf[a] += ga
        order = sorted(teams, key=lambda t: (-pts[t], -gd[t], -gf[t], t))
        counts[order[0]]["title"] += 1
        for t in order[:ucl_n]:
            counts[t]["ucl"] += 1
        for t in (order[-rel_n:] if rel_n else []):
            counts[t]["releg"] += 1

    out = {"code": code, "season": season, "n_sims": n_sims,
           "teams": {t: {k: round(v / n_sims, 4) for k, v in c.items()}
                     for t, c in counts.items()}}
    build._atomic_json(config.paths(code)["sim"], out)
    return out
