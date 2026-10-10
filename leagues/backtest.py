# leagues/backtest.py
"""联赛回测(walk-forward 严格)。

粒度与 WC backtest.py 一致:season-level —— 预测第 S 季时,
Elo 池用 cutoff = S 季开赛日(8 月 1 日)一次性建好,DC 用同一 cutoff 拟合;
赛季内不滚动更新(滚动版留作后续增强,此处刻意与 WC 对齐,勿混用粒度)。
欧冠淘汰赛比分是 90 分钟结果(Review Focus 5),回测同样按此语义对比,不修正。

手工跑:python -m leagues.backtest  →  docs/leagues_backtest.md
"""
import math
from datetime import date

from . import build, config, dixon_coles, elo as elo_mod, predict


def _season_start(season):
    y1, _ = config.season_years(season)
    return f"{y1}-08-01"


def _logloss(probs, actual):
    return -math.log(max(probs[actual], 1e-9))


def backtest_league(code, seasons):
    """对 code 的每个 season 做赛季级 walk-forward 回测,对比 B365 去水基线。"""
    results = build.load_results(code)
    out = {}
    for season in seasons:
        cutoff = _season_start(season)
        elo, _ = elo_mod.build_pool(results, cutoff=cutoff)
        params, glob = dixon_coles.fit_league(results, cutoff=cutoff)
        played = [m for m in results if m.get("season") == season and m["gh"] is not None]
        played.sort(key=lambda m: (m["date"], m["home"]))
        n = hit_m = hit_b = 0
        ll_m = ll_b = 0.0
        for m in played:
            dv = predict.devig(m.get("b365h"), m.get("b365d"), m.get("b365a"))
            if not dv:
                continue
            pred = predict.predict_match(m["home"], m["away"], elo=elo,
                                         dc_params=params, dc_glob=glob)
            actual = "home" if m["gh"] > m["ga"] else ("draw" if m["gh"] == m["ga"] else "away")
            p_mkt = {"home": dv[0], "draw": dv[1], "away": dv[2]}
            n += 1
            hit_m += int(max(pred["probs"], key=pred["probs"].get) == actual)
            hit_b += int(max(p_mkt, key=p_mkt.get) == actual)
            ll_m += _logloss(pred["probs"], actual)
            ll_b += _logloss(p_mkt, actual)
        if n:
            out[season] = {"n": n, "acc_model": hit_m / n, "acc_b365": hit_b / n,
                           "logloss_model": ll_m / n, "logloss_b365": ll_b / n}
    return out


def _last_complete_seasons(k=3):
    """最近 k 个已完结赛季(当前赛季不算完结)。"""
    cur = config.current_season()
    y1, _ = config.season_years(cur)
    return [f"{y % 100:02d}{(y + 1) % 100:02d}" for y in range(y1 - k, y1)]


def main():
    seasons = _last_complete_seasons(3)
    lines = ["# 联赛板块回测(walk-forward,赛季粒度)",
             "",
             f"生成:{date.today().isoformat()} 赛季:{', '.join(seasons)}",
             "口径:Elo 池与 DC 均以赛季开赛日(8/1)为 cutoff;基线 = B365 收盘去水。",
             "",
             "| 联赛 | 赛季 | 场次 | 模型准确率 | B365 准确率 | 模型 logloss | B365 logloss |",
             "|---|---|---|---|---|---|---|"]
    for code in config.LEAGUE_CODES:
        try:
            res = backtest_league(code, seasons)
        except Exception as e:
            lines.append(f"| {code} | – | – | 回测失败:{e} | | | |")
            continue
        for s, r in sorted(res.items()):
            lines.append(f"| {code} | {s} | {r['n']} | {r['acc_model']:.3f} | "
                         f"{r['acc_b365']:.3f} | {r['logloss_model']:.3f} | {r['logloss_b365']:.3f} |")
    with open("docs/leagues_backtest.md", "w", encoding="utf-8") as f:
        f.write("\n".join(lines) + "\n")
    print("\n".join(lines))


if __name__ == "__main__":
    main()
