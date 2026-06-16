"""tournament_sim.py — 2026 世界杯整届蒙特卡洛推演

赛制(2026 新赛制):
  12 组 × 4 队单循环 → 每组前 2 名(24 队)+ 8 个最佳第 3 名(8 队) = 32 强
  → 5 轮单淘汰(R32 → R16 → QF → SF → Final)决出冠军。

单场预测链:Elo 差 → λ(泊松率)→ 抽具体比分 → 算积分/晋级。
  小组赛允许平局;淘汰赛 90 分钟平局用 ★resolve_knockout_draw(learning 点 3)决出胜者。

分组数据:优先读 wc2026_schedule.csv(动态),不完整回退内置 GROUPS_2026 快照。

用法:
    python tournament_sim.py --sims 10000
    python tournament_sim.py --sims 5000 --seed 42 --top 20
"""

import argparse
import csv
import itertools
import os
import sys

import numpy as np

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8")

import wc_predict
import compute_elo

HERE = os.path.dirname(os.path.abspath(__file__))
RESULTS = os.path.join(HERE, "results.csv")
SCHEDULE = os.path.join(HERE, "wc2026_schedule.csv")

# 内置 2026 分组快照(基于 2026-06-15 抓取的 schedule)。load_groups 在 CSV
# 缺失/不全时回退到此,保证离线可跑。CSV 完整(12 组×4 队)时以 CSV 为准。
GROUPS_2026 = {
    "A": ["Czech Republic", "Mexico", "South Africa", "South Korea"],
    "B": ["Bosnia and Herzegovina", "Canada", "Qatar", "Switzerland"],
    "C": ["Brazil", "Haiti", "Morocco", "Scotland"],
    "D": ["Australia", "Paraguay", "Turkey", "United States"],
    "E": ["Curaçao", "Ecuador", "Germany", "Ivory Coast"],
    "F": ["Japan", "Netherlands", "Sweden", "Tunisia"],
    "G": ["Belgium", "Egypt", "Iran", "New Zealand"],
    "H": ["Cape Verde", "Saudi Arabia", "Spain", "Uruguay"],
    "I": ["France", "Iraq", "Norway", "Senegal"],
    "J": ["Algeria", "Argentina", "Austria", "Jordan"],
    "K": ["Colombia", "DR Congo", "Portugal", "Uzbekistan"],
    "L": ["Croatia", "England", "Ghana", "Panama"],
}

# 32 强首轮标准签表(seed 0-indexed,0 = 最强)。保证 seed0/seed1 分处上下
# 半区决赛才相遇(签表公平性)。R1 胜者按 pair 顺序进 list,R2+ 相邻配对。
BRACKET_R1_PAIRS = [
    (0, 31), (15, 16), (7, 24), (8, 23), (4, 27), (11, 20), (3, 28), (12, 19),
    (1, 30), (14, 17), (6, 25), (9, 22), (5, 26), (10, 21), (2, 29), (13, 18),
]


def load_groups(path=SCHEDULE):
    """从 wc2026_schedule.csv 提取 12 组×4 队;缺失/不完整回退内置快照。

    只取 stage=='Group' 的行,按 group 列聚合。任一组 ≠ 4 队则整体回退。
    """
    groups = {}
    if os.path.exists(path):
        with open(path, newline="", encoding="utf-8") as f:
            r = csv.reader(f)
            next(r, None)
            for row in r:
                if len(row) < 6 or row[4] != "Group":
                    continue
                g = row[3]
                if not g:
                    continue
                groups.setdefault(g, set()).add(row[1])
                groups[g].add(row[2])
        groups = {g: sorted(s) for g, s in groups.items() if len(s) == 4}
    if len(groups) != 12:
        return {k: list(v) for k, v in GROUPS_2026.items()}
    return groups


def _lambdas(elo, a, b, ou_map=None, year=2026):
    """两队 → (λ_a, λ_b),中立场(home_adv=0)。
    异源优先:若 ou_map 命中且能从市场报价反演 (λh,λa),用反演值(含总进球 OU 约束,
    比纯 Elo 更贴市场);否则 Elo→λ。Elo 缺失回退 1500。"""
    if ou_map:
        import compute_odds
        v = compute_odds.lookup_ou(ou_map, a, b, year)
        if v is not None:
            oh, od, oa = v["1x2"]
            lam = compute_odds.lambda_from_market(oh, od, oa, ou=v.get("ou"),
                                                  ou_line=v.get("ou_line", 2.5))
            if lam is not None:
                return lam
    return wc_predict.elo_to_lambdas(elo.get(a, 1500.0), elo.get(b, 1500.0),
                                     home_adv=0.0)


def simulate_group(teams, elo, rng, ou_map=None):
    """单循环 6 场 → 按 (积分, 净胜球, 进球) 排名。返回 [(队, 积分, 净胜球, 进球)]。"""
    pts = {t: 0 for t in teams}
    gf = {t: 0 for t in teams}
    ga = {t: 0 for t in teams}
    for a, b in itertools.combinations(teams, 2):
        lam_a, lam_b = _lambdas(elo, a, b, ou_map)
        sa, sb = rng.poisson(lam_a), rng.poisson(lam_b)
        gf[a] += sa; ga[a] += sb; gf[b] += sb; ga[b] += sa
        if sa > sb:
            pts[a] += 3
        elif sa < sb:
            pts[b] += 3
        else:
            pts[a] += 1; pts[b] += 1
    order = sorted(teams, key=lambda t: (pts[t], gf[t] - ga[t], gf[t], t),
                   reverse=True)
    return [(t, pts[t], gf[t] - ga[t], gf[t]) for t in order]


def resolve_knockout_draw(team_a, team_b, lam_a, lam_b, rng, elo=None):
    """★【learning 点 3 — 改进版】淘汰赛 90 分钟平局后决出胜者。

    已实现:
    A) 加时 30 分钟:λ_ot = λ × 0.30。加时进球率实测低于时间比 1/3(0.33)
       —— 球队加时保守 + 疲劳,0.30 更贴近历史(纯时间比会高估加时进球)。
    B) 点球:Elo-based 弱化胜率 p_a = 1/(1+10^((eb−ea)/800))。
       点球非纯运气,强队胜率约 52-57%(历史);用 Elo 差 + 大分母(800)
       弱化建模(标准 Elo 用 400,对点球太激进)。100 Elo 差 → 点球胜率 57%。
       elo=None 时退回 50:50(向后兼容,无 Elo 数据时)。

    设计依据:让强队淘汰赛存活率更接近真实(纯 50:50 会高估弱队爆冷)。
    返回 team_a 或 team_b。
    """
    ot_a, ot_b = rng.poisson(lam_a * 0.30), rng.poisson(lam_b * 0.30)
    if ot_a != ot_b:
        return team_a if ot_a > ot_b else team_b
    if elo is not None:
        ea = float(elo.get(team_a, 1500.0))
        eb = float(elo.get(team_b, 1500.0))
        p_a = 1.0 / (1.0 + 10 ** ((eb - ea) / 800.0))
        return team_a if rng.random() < p_a else team_b
    return team_a if rng.random() < 0.5 else team_b


def sim_knockout_match(a, b, elo, rng, ou_map=None):
    """淘汰赛单场:抽比分,平局用 resolve_knockout_draw。返回胜者队名。"""
    lam_a, lam_b = _lambdas(elo, a, b, ou_map)
    sa, sb = rng.poisson(lam_a), rng.poisson(lam_b)
    if sa != sb:
        return a if sa > sb else b
    return resolve_knockout_draw(a, b, lam_a, lam_b, rng, elo=elo)


def simulate_bracket(seeded_32, elo, rng, ou_map=None):
    """32 队单淘汰(seed 0 最强)。R1 按 BRACKET_R1_PAIRS,R2+ 相邻配对。返回冠军。"""
    current = [sim_knockout_match(seeded_32[a], seeded_32[b], elo, rng, ou_map)
               for a, b in BRACKET_R1_PAIRS]
    while len(current) > 1:
        current = [sim_knockout_match(current[i], current[i + 1], elo, rng, ou_map)
                   for i in range(0, len(current), 2)]
    return current[0]


def one_tournament(elo, groups, rng, ou_map=None):
    """完整一届:小组赛 → 选 32 强 → seed → 淘汰赛。返回冠军队名。"""
    rankings = {g: simulate_group(teams, elo, rng, ou_map) for g, teams in groups.items()}
    # 出线:每组前 2(24)+ 8 最佳第 3
    top2 = []
    thirds = []
    for g in sorted(rankings):
        rk = rankings[g]
        top2.append(rk[0][0]); top2.append(rk[1][0])
        thirds.append(rk[2])                       # (队, 积分, 净胜球, 进球)
    best_8 = sorted(thirds, key=lambda x: (-x[1], -x[2], -x[3]))[:8]
    qualified = top2 + [t[0] for t in best_8]      # 32 队
    # 按 Elo 降序 seed(组冠军不打组冠军的真实规则简化为纯 Elo seed)
    seeded = sorted(qualified, key=lambda t: (-elo.get(t, 1500.0), t))
    return simulate_bracket(seeded, elo, rng, ou_map)


def monte_carlo(n_sims, seed=None, ou_map=None):
    """跑 n_sims 次整届推演,返回 [(队名, 夺冠次数, 概率)] 降序。
    ou_map 非空时,有 OU+1X2 报价的场用市场反演 λ(异源优先),其余 Elo λ。"""
    elo, _, _ = compute_elo.build_elo(RESULTS)
    if ou_map is None:   # 调用方未传则自载合并双源(失败兜底空→全 Elo,向后兼容)
        try:
            import compute_odds
            ou_map = dict(compute_odds.load_ou())            # 模式B:1X2+OU(ou_wc.csv)
            for k, (oh, od, oa) in compute_odds.load_odds().items():   # 模式A:仅1X2(odds_wc.csv)
                if k not in ou_map:
                    ou_map[k] = {"1x2": (oh, od, oa), "ou": None, "ou_line": 2.5}
        except Exception:
            ou_map = {}
    groups = load_groups()
    rng = np.random.default_rng(seed)
    champs = {}
    for _ in range(n_sims):
        w = one_tournament(elo, groups, rng, ou_map)
        champs[w] = champs.get(w, 0) + 1
    ranking = sorted(champs.items(), key=lambda kv: (-kv[1], kv[0]))
    return [(t, c, c / n_sims) for t, c in ranking]


def main():
    ap = argparse.ArgumentParser(description="2026 世界杯整届蒙特卡洛推演")
    ap.add_argument("--sims", type=int, default=10000, help="模拟次数(默认 10000)")
    ap.add_argument("--seed", type=int, default=None, help="随机种子(可复现)")
    ap.add_argument("--top", type=int, default=15, help="显示夺冠概率前 N")
    args = ap.parse_args()

    print("=" * 56)
    print(f"  2026 世界杯蒙特卡洛推演  模拟 {args.sims} 次" +
          (f"  seed={args.seed}" if args.seed is not None else ""))
    print("=" * 56)
    ranking = monte_carlo(args.sims, args.seed)
    print(f"  夺冠概率 Top {args.top}(共 {len(ranking)} 队曾夺冠):")
    for i, (t, c, p) in enumerate(ranking[:args.top], 1):
        zh = wc_predict.EN_TO_ZH.get(t, t)
        bar = "█" * int(p * 100)
        print(f"  {i:2}. {zh:<6} ({t:<24}) {p * 100:5.2f}%  {c:>5}/{args.sims}  {bar}")
    print("=" * 56)


if __name__ == "__main__":
    main()
