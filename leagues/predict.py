# leagues/predict.py
"""单场预测管线:DC 比分矩阵 → 三态/比分 top5/OU2.5/BTTS;Elo 回退;
赔率 devig + edge + 分数 Kelly;与 XGBoost 的分歧门控集成(ensemble.py 模式)。

付费门控前提(N-1):只有显式传 odds 时输出才含 market 子 dict;
不传则 market 键完全不存在(API 层按 Pro 状态决定是否传)。
"""
import math

from . import dixon_coles
from .dixon_coles import _tau

MAX_GOALS = 8
AVG_TOTAL_GOALS = 2.6      # 联赛场均总进球(高于 WC 的 2.20,联赛无保守淘汰赛)
ELO_SCALE = 200.0
ELO_HOME_ADV = 100.0
KELLY_FRACTION = 0.25


def poisson_pmf(k, lam):
    return math.exp(-lam) * lam ** k / math.factorial(k)


def score_matrix(lh, la, rho=0.0):
    """(MAX_GOALS+1)² 比分概率矩阵,P(i,j)=Pois(i;lh)·Pois(j;la)·τ(i,j),归一化。"""
    m = [[poisson_pmf(i, lh) * poisson_pmf(j, la) * _tau(i, j, lh, la, rho)
          for j in range(MAX_GOALS + 1)] for i in range(MAX_GOALS + 1)]
    s = sum(sum(row) for row in m)
    return [[v / s for v in row] for row in m]


def three_way(m):
    n = len(m)
    hw = sum(m[i][j] for i in range(n) for j in range(n) if i > j)
    dr = sum(m[i][i] for i in range(n))
    aw = sum(m[i][j] for i in range(n) for j in range(n) if i < j)
    return hw, dr, aw


def top_scores(m, n=5):
    cells = sorted(((m[i][j], i, j) for i in range(len(m)) for j in range(len(m))),
                   reverse=True)
    return [[f"{i}-{j}", round(p, 4)] for p, i, j in cells[:n]]


def ou25_over(m):
    n = len(m)
    return sum(m[i][j] for i in range(n) for j in range(n) if i + j >= 3)


def btts_yes(m):
    n = len(m)
    return sum(m[i][j] for i in range(1, n) for j in range(1, n))


def elo_probs(elo_h, elo_a, neutral=False):
    """纯 Elo 回退:sigmoid 分总进球份额 → 双泊松矩阵 → 三态。
    返回 ({"home","draw","away"}, lh, la)。"""
    diff = elo_h + (0.0 if neutral else ELO_HOME_ADV) - elo_a
    share = 1.0 / (1.0 + 10 ** (-diff / (2 * ELO_SCALE)))
    lh = max(AVG_TOTAL_GOALS * share, 0.1)
    la = max(AVG_TOTAL_GOALS * (1.0 - share), 0.1)
    hw, dr, aw = three_way(score_matrix(lh, la))
    return {"home": hw, "draw": dr, "away": aw}, lh, la


def devig(oh, od, oa):
    """三态按比例去水。任一缺失/≤1.0 → None。"""
    if not oh or not od or not oa or min(oh, od, oa) <= 1.0:
        return None
    inv = [1.0 / oh, 1.0 / od, 1.0 / oa]
    s = sum(inv)
    return inv[0] / s, inv[1] / s, inv[2] / s


def kelly(p, odds, fraction=KELLY_FRACTION):
    """分数凯利:f* = (p·odds − 1)/(odds − 1) × fraction;edge ≤ 0 → 0。"""
    if not odds or odds <= 1.0:
        return 0.0
    raw = (p * odds - 1.0) / (odds - 1.0)
    return max(raw, 0.0) * fraction


def combine_gate(stat, xgb, w_base=0.2):
    """分歧门控:三态首选一致 → w_base·stat + (1−w_base)·xgb;分歧 → 纯 stat。"""
    agree = max(stat, key=stat.get) == max(xgb, key=xgb.get)
    w_stat = w_base if agree else 1.0
    return {k: w_stat * stat[k] + (1.0 - w_stat) * xgb[k] for k in ("home", "draw", "away")}


def predict_match(home, away, *, elo, dc_params, dc_glob, odds=None, neutral=False,
                  xgb_probs=None, low_confidence=False):
    """单场完整预测。返回:
    {probs, lam_h, lam_a, top_scores, ou25_over, btts_yes, low_confidence [, market]}
    market = {p_mkt, edges, max_edge, pick, odds, kelly}(仅 odds 传参且 devig 成功时)。
    """
    if home in dc_params and away in dc_params:
        lh, la = dixon_coles.expected_goals(dc_params, dc_glob, home, away, neutral)
        m = score_matrix(lh, la, dc_glob.get("rho", 0.0))
        hw, dr, aw = three_way(m)
        stat = {"home": hw, "draw": dr, "away": aw}
    else:
        stat, lh, la = elo_probs(elo.get(home, 1500.0), elo.get(away, 1500.0), neutral)
        m = score_matrix(lh, la)
        low_confidence = True
    probs = combine_gate(stat, xgb_probs) if xgb_probs else stat
    out = {
        "probs": {k: round(v, 4) for k, v in probs.items()},
        "lam_h": round(lh, 3), "lam_a": round(la, 3),
        "top_scores": top_scores(m),
        "ou25_over": round(ou25_over(m), 4),
        "btts_yes": round(btts_yes(m), 4),
        "low_confidence": bool(low_confidence),
    }
    if odds:
        dv = devig(odds.get("b365h"), odds.get("b365d"), odds.get("b365a"))
        if dv:
            p_mkt = {"home": dv[0], "draw": dv[1], "away": dv[2]}
            edges = {k: probs[k] - p_mkt[k] for k in probs}
            pick = max(edges, key=edges.get)
            odd_key = {"home": "b365h", "draw": "b365d", "away": "b365a"}[pick]
            out["market"] = {
                "p_mkt": {k: round(v, 4) for k, v in p_mkt.items()},
                "edges": {k: round(v, 4) for k, v in edges.items()},
                "max_edge": round(edges[pick], 4),
                "pick": pick,
                "odds": {"home": odds.get("b365h"), "draw": odds.get("b365d"),
                         "away": odds.get("b365a")},
                "kelly": round(kelly(probs[pick], odds.get(odd_key)), 4),
            }
    return out
