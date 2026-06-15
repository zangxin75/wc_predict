"""compute_ad.py — Dixon-Coles 攻防参数 MLE 拟合

从历史比赛(cutoff 前)用最大似然估计拟合每队 attack/defense,带时间衰减 +
小样本回退。替代 wc_predict 的静态 AVG_TOTAL_GOALS——让每队有自己的攻防强度。

参数化(Dixon-Coles 标准):
    log λ_home = β0 + attack[home] + defense[away] + home_adv·(非中立?1:0)
    log λ_away = β0 + attack[away] + defense[home]
    进球 ~ Poisson(λ),低比分用 Dixon-Coles τ 修正(ρ 复用 wc_predict.Config.RHO)
可识别性:弱惩罚 λ·mean(attack)² + λ·mean(defense)² 替代硬约束 sum=0(L-BFGS-B 更快)。

用法:
    params, glob = fit_attack_defense("results.csv", cutoff_date="2022-11-20")
    lam_h, lam_a = expected_goals(params, glob, "Brazil", "Argentina", neutral=True)

零外部状态;依赖 numpy/scipy。CSV 同 results.csv 9 列格式。
"""

import csv
import math
import os
import sys
from datetime import date

import numpy as np
from scipy.optimize import minimize
from scipy.special import gammaln

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8")

import wc_predict  # 复用 Config.RHO、dixon_coles_tau 的 ρ 定义


def _parse_date(s):
    return date.fromisoformat(s)


def _days_between(d_old, d_new):
    """d_new - d_old 的天数(正数表示 d_old 更早)。"""
    return (_parse_date(d_new) - _parse_date(d_old)).days


def _dc_tau_vectorized(x, y, lam_h, lam_a, rho):
    """Dixon-Coles 低比分 τ 修正,向量化版本(同 wc_predict.dixon_coles_tau 的逻辑)。"""
    tau = np.ones_like(x, dtype=float)
    m00 = (x == 0) & (y == 0)
    m01 = (x == 0) & (y == 1)
    m10 = (x == 1) & (y == 0)
    m11 = (x == 1) & (y == 1)
    tau[m00] = 1.0 - lam_h[m00] * lam_a[m00] * rho
    tau[m01] = 1.0 + lam_h[m01] * rho
    tau[m10] = 1.0 + lam_a[m10] * rho
    tau[m11] = 1.0 - rho
    return np.clip(tau, 1e-6, None)  # 防 τ≤0 导致 log 爆炸


def _load_matches(path, cutoff_date):
    """读 cutoff 前的有效比赛。返回 dict 列表,或空列表(调用方判断异常)。"""
    out = []
    with open(path, newline="", encoding="utf-8") as f:
        for r in csv.reader(f):
            if not r or r[0] == "date" or len(r) < 9:
                continue
            if not (r[3].lstrip("-").isdigit() and r[4].lstrip("-").isdigit()):
                continue
            if cutoff_date is not None and r[0] >= cutoff_date:
                continue
            out.append({
                "date": r[0], "home": r[1], "away": r[2],
                "hs": int(r[3]), "as_": int(r[4]),
                "tournament": r[5],
                "neutral": r[8].strip().upper() == "TRUE",
            })
    out.sort(key=lambda m: m["date"])
    return out


def fit_attack_defense(path, cutoff_date=None, halflife_years=3.0, min_matches=20):
    """MLE 拟合每队 attack/defense。

    返回 (params, glob):
      params = {队名: {"attack":float, "defense":float, "matches":int}}
      glob   = {"beta0":float, "home_adv":float}   # home_adv 是对数主场加成
    """
    matches = _load_matches(path, cutoff_date)
    if not matches:
        raise ValueError("无有效比赛数据(检查 cutoff_date 或 CSV 内容)")

    # 队伍索引
    teams = sorted({m["home"] for m in matches} | {m["away"] for m in matches})
    idx = {t: i for i, t in enumerate(teams)}
    N = len(teams)

    # 权重 = 纯时间衰减(以 cutoff 为参考点——walk-forward 关键)。
    # 【实验记录】曾尝试额外乘赛事重要性(tournament_k/30),但回测反而更差
    #   (2018: 0.5966→0.6198,2022: 0.6245→0.6474)。原因:升权世界杯减少有效样本量,
    #   国家队本就稀疏,过拟合加剧。赛事权重对 Elo(顺序更新、K放大单场)有效,
    #   对 Dixon-Coles 批量 MLE 反效果。故 ad 仅用时间衰减。
    ref_date = cutoff_date if cutoff_date else matches[-1]["date"]
    xi = math.log(2) / (halflife_years * 365.0)
    weights = np.array([
        math.exp(-xi * _days_between(m["date"], ref_date))
        for m in matches
    ])

    # 向量化比赛数据
    h_idx = np.array([idx[m["home"]] for m in matches])
    a_idx = np.array([idx[m["away"]] for m in matches])
    hs = np.array([m["hs"] for m in matches], dtype=float)
    as_ = np.array([m["as_"] for m in matches], dtype=float)
    home_factor = np.array([0.0 if m["neutral"] else 1.0 for m in matches])

    # 每队比赛计数(用于小样本回退)
    counts = np.zeros(N)
    for i in range(len(matches)):
        counts[h_idx[i]] += 1
        counts[a_idx[i]] += 1

    rho = wc_predict.Config.RHO
    log_fact_h = gammaln(hs + 1.0)  # 预计算 log(k!),与参数无关
    log_fact_a = gammaln(as_ + 1.0)
    PEN = 0.001  # 可识别性弱惩罚系数

    def nll(x):
        beta0 = x[0]
        log_home_adv = x[1]
        attack = x[2:2 + N]
        defense = x[2 + N:2 + 2 * N]

        log_lam_h = beta0 + attack[h_idx] + defense[a_idx] + log_home_adv * home_factor
        log_lam_a = beta0 + attack[a_idx] + defense[h_idx]
        # clip 防 exp 爆炸
        lam_h = np.exp(np.clip(log_lam_h, -8, 8))
        lam_a = np.exp(np.clip(log_lam_a, -8, 8))

        # 泊松对数似然: log P(k;λ) = k·log(λ) − λ − log(k!)
        ll_h = hs * np.log(lam_h) - lam_h - log_fact_h
        ll_a = as_ * np.log(lam_a) - lam_a - log_fact_a
        # Dixon-Coles τ 修正
        tau = _dc_tau_vectorized(hs, as_, lam_h, lam_a, rho)
        ll = weights * (ll_h + ll_a + np.log(tau))

        pen = PEN * (attack.mean() ** 2 + defense.mean() ** 2)
        return -ll.sum() + pen

    # 初值:β0 = log(场均每队进球≈1.3),其余 0
    x0 = np.zeros(2 + 2 * N)
    x0[0] = math.log(1.3)
    res = minimize(nll, x0, method="L-BFGS-B",
                   options={"maxiter": 500, "ftol": 1e-7})

    beta0 = float(res.x[0])
    log_home_adv = float(res.x[1])
    attack = res.x[2:2 + N] - res.x[2:2 + N].mean()      # 中心化 → sum≈0
    defense = res.x[2 + N:2 + 2 * N] - res.x[2 + N:2 + 2 * N].mean()

    # 小样本回退:近周期比赛不足的队回退中性(attack=defense=0)
    for i in range(N):
        if counts[i] < min_matches:
            attack[i] = 0.0
            defense[i] = 0.0

    params = {}
    for i, t in enumerate(teams):
        params[t] = {
            "attack": float(attack[i]),
            "defense": float(defense[i]),
            "matches": int(counts[i]),
        }
    glob = {"beta0": beta0, "home_adv": log_home_adv}
    return params, glob


def expected_goals(params, glob, home, away, neutral=True):
    """从攻防参数算两队预期进球 λ_home, λ_away。

    未知队(不在 params)按中性 attack=defense=0 处理。
    输出 clip 到 [0.1, 5.0]:MLE 在小样本边缘会过拟合出荒谬 λ
    (弱国对弱国刷出 attack+defense≈4 → exp→50+),工程兜底压回物理合理区间。
    真实强队打弱队 λ≈3,不受影响;>5 在足球里几乎不存在。
    """
    beta0 = glob["beta0"]
    ha = 0.0 if neutral else glob["home_adv"]
    ah = params.get(home, {}).get("attack", 0.0)
    dh = params.get(home, {}).get("defense", 0.0)
    aa = params.get(away, {}).get("attack", 0.0)
    da = params.get(away, {}).get("defense", 0.0)
    lam_h = min(max(math.exp(beta0 + ah + da + ha), 0.1), 5.0)
    lam_a = min(max(math.exp(beta0 + aa + dh), 0.1), 5.0)
    return lam_h, lam_a


def main():
    """命令行:拟合并打印 Top 攻防队,产出 params.json。"""
    here = os.path.dirname(os.path.abspath(__file__))
    results = os.path.join(here, "results.csv")
    out = os.path.join(here, "params.json")
    import json

    print("=" * 56)
    print("  Dixon-Coles 攻防参数 MLE 拟合")
    print("=" * 56)
    params, glob = fit_attack_defense(results, cutoff_date=None)
    print(f"  全局:β0={glob['beta0']:.3f}  home_adv(log)={glob['home_adv']:.3f}  "
          f"队伍数={len(params)}")

    qualified = [(t, v) for t, v in params.items() if v["matches"] >= 20]
    print(f"  有效队(≥20场):{len(qualified)}")
    print("-" * 56)
    print("  进攻 Top 10(attack 越大越强):")
    for t, v in sorted(qualified, key=lambda kv: -kv[1]["attack"])[:10]:
        print(f"    {t:<22}{v['attack']:+.3f}   ({v['matches']}场)")
    print("-" * 56)
    print("  防守 Top 10(defense 越小越强):")
    for t, v in sorted(qualified, key=lambda kv: kv[1]["defense"])[:10]:
        print(f"    {t:<22}{v['defense']:+.3f}   ({v['matches']}场)")

    with open(out, "w", encoding="utf-8") as f:
        json.dump({"params": params, "global": glob}, f, ensure_ascii=False, indent=2)
    print("=" * 56)
    print(f"✓ 已保存 {out}")


if __name__ == "__main__":
    main()
