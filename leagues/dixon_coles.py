# leagues/dixon_coles.py
"""Dixon-Coles 攻防参数 MLE(思路复刻 compute_ad.py,接口按联赛隔离)。

  log λ_home = β0 + home_adv·(非中立) + attack[h] − defense[a]
  log λ_away = β0              + attack[a] − defense[h]

  · 时间衰减权重:exp(−ln2·Δdays/(halflife·365.25)),相对最近一场
  · 可识别性:弱 L2 惩罚 + 拟合后攻防各自零均值化
  · 小样本:球队总场次 < min_matches → 中性参数(attack=defense=0),不抛错
  · RHO 默认 0.00(WC 校准结论:τ 修正无增益);lambda clip [0.1, 5.0]
  · UCL 借用制(M-5/N-2):attack/defense 固定为球队本国联赛值(联赛间强度差
    已由统一 Elo 池体现,DC 层面近似可迁移),只拟合全局 β0 + home_adv
"""
import math
from datetime import date

import numpy as np
from scipy.optimize import minimize

RHO_DEFAULT = 0.0


def _tau(x, y, lh, la, rho):
    """Dixon-Coles 低比分联合修正(rho=0 时恒 1)。"""
    if x == 0 and y == 0:
        return 1.0 - lh * la * rho
    if x == 0 and y == 1:
        return 1.0 + lh * rho
    if x == 1 and y == 0:
        return 1.0 + la * rho
    if x == 1 and y == 1:
        return 1.0 - rho
    return 1.0


def _prep(matches, cutoff=None):
    rows = [m for m in matches
            if m.get("gh") is not None and m.get("ga") is not None
            and (cutoff is None or m["date"] < cutoff)]
    rows.sort(key=lambda m: (m["date"], m["home"]))
    teams = sorted({m["home"] for m in rows} | {m["away"] for m in rows})
    idx = {t: i for i, t in enumerate(teams)}
    hi = np.array([idx[m["home"]] for m in rows])
    ai = np.array([idx[m["away"]] for m in rows])
    gh = np.array([m["gh"] for m in rows], dtype=float)
    ga = np.array([m["ga"] for m in rows], dtype=float)
    return teams, hi, ai, gh, ga, rows


def _time_weights(rows, halflife_years):
    dmax = date.fromisoformat(rows[-1]["date"])
    xi = math.log(2) / (halflife_years * 365.25)
    return np.array([math.exp(-xi * (dmax - date.fromisoformat(m["date"])).days)
                     for m in rows])


def fit_league(matches, cutoff=None, halflife_years=3.0, min_matches=20, rho=RHO_DEFAULT):
    """联赛 DC 拟合。返回 (params, glob),签名见 Task 头 Interfaces。"""
    teams, hi, ai, gh, ga, rows = _prep(matches, cutoff)
    if not rows:
        return {}, {"home_adv": 0.0, "beta0": math.log(1.35), "rho": rho, "n_matches": 0}
    n = len(teams)
    w = _time_weights(rows, halflife_years)
    neutral = np.array([bool(m.get("neutral")) for m in rows], dtype=float)

    def unpack(x):
        return x[0], x[1], x[2:2 + n], x[2 + n:2 + 2 * n]

    def nll(x):
        beta0, hadv, atk, dfn = unpack(x)
        lh = np.clip(np.exp(beta0 + hadv * (1.0 - neutral) + atk[hi] - dfn[ai]), 0.1, 5.0)
        la = np.clip(np.exp(beta0 + atk[ai] - dfn[hi]), 0.1, 5.0)
        ll = -lh + gh * np.log(lh) - la + ga * np.log(la)
        if rho != 0.0:
            tau = np.array([_tau(int(g1), int(g2), l1, l2, rho)
                            for g1, g2, l1, l2 in zip(gh, ga, lh, la)])
            ll = ll + np.log(np.maximum(tau, 1e-9))
        pen = 1e-4 * float(np.sum(atk ** 2) + np.sum(dfn ** 2))
        return -float(np.sum(w * ll)) + pen

    x0 = np.zeros(2 + 2 * n)
    x0[0] = math.log(1.35)
    x0[1] = 0.25
    res = minimize(nll, x0, method="L-BFGS-B", options={"maxiter": 500})
    beta0, hadv, atk, dfn = unpack(res.x)
    atk = atk - atk.mean()      # 零均值化(可识别性)
    dfn = dfn - dfn.mean()
    played = {}
    for m in rows:
        played[m["home"]] = played.get(m["home"], 0) + 1
        played[m["away"]] = played.get(m["away"], 0) + 1
    params = {}
    for i, t in enumerate(teams):
        if played.get(t, 0) < min_matches:
            params[t] = {"attack": 0.0, "defense": 0.0, "n": played.get(t, 0)}
        else:
            params[t] = {"attack": float(atk[i]), "defense": float(dfn[i]), "n": played[t]}
    glob = {"home_adv": float(hadv), "beta0": float(beta0), "rho": rho, "n_matches": len(rows)}
    return params, glob


def fit_ucl_global(ucl_matches, league_params_by_club, halflife_years=2.0):
    """欧冠 DC(借用制):attack/defense 固定借用本国联赛值,只拟合 β0 + home_adv。

    返回 (glob, borrow_map);无五大联赛归属的队 → 0.0 + low_confidence=True(N-2)。
    """
    rows = sorted((m for m in ucl_matches if m.get("gh") is not None),
                  key=lambda m: (m["date"], m["home"]))
    borrow_map = {}
    for m in rows:
        for t in (m["home"], m["away"]):
            if t in borrow_map:
                continue
            p = league_params_by_club.get(t)
            if p is None:
                borrow_map[t] = {"attack": 0.0, "defense": 0.0, "low_confidence": True}
            else:
                borrow_map[t] = {"attack": p["attack"], "defense": p["defense"],
                                 "low_confidence": False}
    if not rows:
        return {"beta0": math.log(1.35), "home_adv": 0.0, "rho": RHO_DEFAULT,
                "n_matches": 0}, borrow_map
    w = _time_weights(rows, halflife_years)
    gh = np.array([m["gh"] for m in rows], dtype=float)
    ga = np.array([m["ga"] for m in rows], dtype=float)
    ah = np.array([borrow_map[m["home"]]["attack"] - borrow_map[m["away"]]["defense"]
                   for m in rows])
    aa = np.array([borrow_map[m["away"]]["attack"] - borrow_map[m["home"]]["defense"]
                   for m in rows])
    neutral = np.array([bool(m.get("neutral")) for m in rows], dtype=float)

    def nll(x):
        beta0, hadv = x
        lh = np.clip(np.exp(beta0 + hadv * (1.0 - neutral) + ah), 0.1, 5.0)
        la = np.clip(np.exp(beta0 + aa), 0.1, 5.0)
        ll = -lh + gh * np.log(lh) - la + ga * np.log(la)
        return -float(np.sum(w * ll))

    res = minimize(nll, np.array([math.log(1.35), 0.2]), method="L-BFGS-B")
    glob = {"beta0": float(res.x[0]), "home_adv": float(res.x[1]),
            "rho": RHO_DEFAULT, "n_matches": len(rows)}
    return glob, borrow_map


def expected_goals(params, glob, home, away, neutral=False):
    """λ_h/λ_a,clip 到 [0.1, 5.0];球队缺参数按中性 0;中立场不计 home_adv。"""
    ph = params.get(home, {"attack": 0.0, "defense": 0.0})
    pa = params.get(away, {"attack": 0.0, "defense": 0.0})
    hadv = 0.0 if neutral else glob.get("home_adv", 0.0)
    lh = math.exp(glob["beta0"] + hadv + ph["attack"] - pa["defense"])
    la = math.exp(glob["beta0"] + pa["attack"] - ph["defense"])
    return min(max(lh, 0.1), 5.0), min(max(la, 0.1), 5.0)
