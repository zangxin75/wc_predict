# leagues/elo.py
"""统一俱乐部 Elo 池:五大联赛 + 欧冠一个池(欧冠跨联赛连接,使联赛间强度可比)。

参数(对照 clubelo.com 实践):
  · K:联赛 30,欧冠 40(更重要赛事波动更大)
  · 主场 +100 只进期望分计算,不改赛后修正方向
  · 净胜球乘数:2 球 ×1.5,3 球 ×1.75,≥4 球 ×(11+gd)/8
  · 新队(升班马/首次入池)1500 起步,前 30 场 K×1.5 加速收敛
    → 每队用自己的 K,设计上非零和(刻意,勿改成统一 K)
"""
INITIAL_ELO = 1500.0
HOME_ADV = 100.0
K_LEAGUE = 30.0
K_UCL = 40.0
PROMOTED_GAMES = 30
PROMOTED_BOOST = 1.5


def k_for(comp, games_played):
    k = K_UCL if comp == "UCL" else K_LEAGUE
    if games_played < PROMOTED_GAMES:
        k *= PROMOTED_BOOST
    return k


def goal_diff_mult(gd):
    if gd >= 4:
        return (11 + gd) / 8.0
    if gd == 3:
        return 1.75
    if gd == 2:
        return 1.5
    return 1.0


def update_elo(rh, ra, gh, ga, comp, neutral, h_games, a_games):
    """单场更新,返回 (new_rh, new_ra)。每队用自己的 K(升班马加速 → 非零和是特性)。"""
    adv = 0.0 if neutral else HOME_ADV
    exp_h = 1.0 / (1.0 + 10 ** (-((rh + adv) - ra) / 400.0))
    if gh > ga:
        sh = 1.0
    elif gh == ga:
        sh = 0.5
    else:
        sh = 0.0
    m = goal_diff_mult(abs(gh - ga))
    new_rh = rh + k_for(comp, h_games) * m * (sh - exp_h)
    new_ra = ra + k_for(comp, a_games) * m * ((1.0 - sh) - (1.0 - exp_h))
    return new_rh, new_ra


def build_pool(matches, cutoff=None):
    """按日期顺序走全部比赛建池。

    matches: [{date,home,away,gh,ga,comp,neutral}, ...](comp ∈ LEAGUE_CODES+['UCL'])
    cutoff: 'YYYY-MM-DD',只用 date < cutoff 的比赛(walk-forward 严格)。
    返回 (ratings, history):
      ratings = {team: 最新 elo}
      history = [{date,home,away,elo_h,elo_a,comp}, ...]  — elo_h/elo_a 是赛前值,
                回测/XGBoost 特征可直接用,无未来泄漏。
    """
    rows = [m for m in matches if cutoff is None or m["date"] < cutoff]
    rows.sort(key=lambda m: (m["date"], m["home"]))
    games, ratings, history = {}, {}, []
    for m in rows:
        h, a = m["home"], m["away"]
        rh = ratings.get(h, INITIAL_ELO)
        ra = ratings.get(a, INITIAL_ELO)
        history.append({"date": m["date"], "home": h, "away": a,
                        "elo_h": rh, "elo_a": ra, "comp": m["comp"]})
        ratings[h], ratings[a] = update_elo(
            rh, ra, m["gh"], m["ga"], m["comp"], bool(m.get("neutral")),
            games.get(h, 0), games.get(a, 0))
        games[h] = games.get(h, 0) + 1
        games[a] = games.get(a, 0) + 1
    return ratings, history
