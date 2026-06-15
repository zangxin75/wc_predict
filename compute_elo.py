"""
compute_elo.py — 从历史比赛结果(results.csv)重建各国国家队 Elo 评分

思路:把 1872 至今所有国际比赛当作一条时间序列,逐场用 Elo 公式更新两队评分。
跑完得到每队"历史积分轨迹的终点" ≈ 当前实力评分。用它替换 wc_predict.py 里
手抄 / 估计的 ELO_RATINGS(那些标 (est) 的值会被真实计算值取代)。

零依赖(仅标准库)。用法:
    python compute_elo.py
前提:先跑 fetch_results.py 生成 results.csv
产出:elo_ratings.json
"""

import csv
import json
import os
import math
import statistics
import sys
from collections import defaultdict

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8")

HERE = os.path.dirname(os.path.abspath(__file__))
RESULTS = os.path.join(HERE, "results.csv")
OUT_JSON = os.path.join(HERE, "elo_ratings.json")

# 新队第一次出现时从这个值起步。1500 是 Elo 惯例中点。
INITIAL_ELO = 1500.0

# 英文→中文队名映射(覆盖 wc_predict.py 现有 24 队)。拼写已对照 results.csv 核实。
# 需要更多队(如 2026 世界杯全部 48 队)时往这里加,对比与写回会自动纳入。
EN_TO_ZH = {
    "Spain": "西班牙", "Argentina": "阿根廷", "France": "法国",
    "England": "英格兰", "Brazil": "巴西", "Portugal": "葡萄牙",
    "Netherlands": "荷兰", "Croatia": "克罗地亚", "Germany": "德国",
    "Belgium": "比利时", "Uruguay": "乌拉圭", "Italy": "意大利",
    "Switzerland": "瑞士", "Morocco": "摩洛哥", "Colombia": "哥伦比亚",
    "Mexico": "墨西哥", "United States": "美国", "Japan": "日本",
    "Senegal": "塞内加尔", "Ecuador": "厄瓜多尔", "South Korea": "韩国",
    "Australia": "澳大利亚", "Canada": "加拿大", "Curaçao": "库拉索",
}

# 校准目标尺度:与 wc_predict.py 现有 ELO_RATINGS(eloratings.net 尺度)匹配,
# 否则数值范围不同会破坏 wc_predict 的 ELO_SCALE / AVG_TOTAL_GOALS 等参数。
TARGET_MEAN = 1700.0
TARGET_STDEV = 150.0


def parse_match(row):
    return {
        "date": row[0],
        "home": row[1],
        "away": row[2],
        "hs": int(row[3]),
        "as": int(row[4]),
        "tournament": row[5],
        "neutral": row[8].strip().upper() == "TRUE",
    }


def tournament_k(tournament):
    """赛事 → K 因子权重(eloratings 标准)。

    世界杯决赛圈 50 / 预选赛 & 洲际正赛 40 / 友谊赛 20 / 其他 30。
    compute_elo 和 compute_ad 共用此分档,保证赛事权重跨模型一致——
    这是 ad 模型追平 elo 的关键:友谊赛(练兵/非全力)降权,世界杯/洲际正赛升权。
    """
    t = (tournament or "").lower()
    if "world cup" in t and "qualif" not in t:
        return 50.0
    if ("qualif" in t or "uefa euro" in t or "copa" in t
            or "cup of nations" in t or "asian cup" in t
            or "nations league" in t or "championship" in t):
        return 40.0
    if "friendly" in t:
        return 20.0
    return 30.0


def update_elo_after_match(elo_home, elo_away, home_score, away_score,
                           tournament, neutral):
    """★【请你实现】根据一场比赛结果更新两队 Elo,返回 (new_elo_home, new_elo_away)。

    这是 Elo 系统的核心。标准公式:

        E_home = 1 / (1 + 10 ** ((elo_away - elo_home_eff) / 400))   # 期望得分(0~1)
        S_home = 1.0(主队胜) / 0.5(平) / 0.0(主队负)              # 实际得分
        new_elo_home = elo_home + K * (S_home - E_home)
        new_elo_away = elo_away + K * (S_away - E_away)     # S_away = 1 - S_home

    ── 本实现采用 eloratings.net 标准策略 ──────────────────────────
    1. K 因子按赛事分档:世界杯决赛圈 50 / 世界杯预选赛 & 洲际正赛 40 /
       友谊赛 20 / 其他默认 30。
    2. 净胜球放大 G:gd=2→1.5, gd=3→1.75, gd≥4→(11+gd)/8(官方公式)。
    3. 主场加成:中立场地 0,否则主队 +100(仅用于算期望得分,不写回存储值)。
    ──────────────────────────────────────────────────────────────
    """
    # 1) K 因子按赛事分档(抽到 tournament_k,compute_ad 也复用同一分档)
    k = tournament_k(tournament)

    # 2) 净胜球放大 G(eloratings 官方公式)
    gd = abs(home_score - away_score)
    if gd <= 1:
        g = 1.0
    elif gd == 2:
        g = 1.5
    elif gd == 3:
        g = 1.75
    else:
        g = (11.0 + gd) / 8.0
    k *= g

    # 3) 期望得分(含主场加成,不写回存储 Elo)
    eff_home = elo_home + (0.0 if neutral else 100.0)
    e_home = 1.0 / (1.0 + 10.0 ** ((elo_away - eff_home) / 400.0))

    # 4) 实际得分
    if home_score > away_score:
        s_home = 1.0
    elif home_score < away_score:
        s_home = 0.0
    else:
        s_home = 0.5

    # 5) 更新(零和:两队变化量大小相等、符号相反)
    delta = k * (s_home - e_home)
    return elo_home + delta, elo_away - delta


def build_elo(path, cutoff_date=None):
    """逐场更新 Elo,返回 ({英文队名: Elo}, 有效比赛数, 跳过数)。

    cutoff_date: ISO 日期串(如 "2018-06-14")。设了则只处理该日期【严格之前】
        的比赛——用于回测的 walk-forward 无泄漏评估(回测某届世界杯时,Elo
        只能用开赛前的历史算,否则未来信息渗入、分数虚高)。
        None(默认)= 用全部比赛,行为同旧版。
    注:ISO 日期(YYYY-MM-DD)字典序与时间序一致,可直接字符串比较。
    """
    elo = defaultdict(lambda: INITIAL_ELO)
    matches, skipped = [], 0
    with open(path, newline="", encoding="utf-8") as f:
        for r in csv.reader(f):
            if not r or r[0] == "date" or len(r) < 9:
                continue
            # 跳过无有效比分的比赛(弃权/取消/未赛,CSV 里可能记为 NA 等)
            if not (r[3].lstrip("-").isdigit() and r[4].lstrip("-").isdigit()):
                skipped += 1
                continue
            m = parse_match(r)
            # walk-forward: 跳过 cutoff 及之后的比赛
            if cutoff_date is not None and m["date"] >= cutoff_date:
                continue
            matches.append(m)
    matches.sort(key=lambda m: m["date"])

    for m in matches:
        eh, ea = elo[m["home"]], elo[m["away"]]
        nh, na = update_elo_after_match(eh, ea, m["hs"], m["as"],
                                        m["tournament"], m["neutral"])
        elo[m["home"]], elo[m["away"]] = nh, na
    return dict(elo), len(matches), skipped


def estimate_rd(games_last_365d, days_since_last,
                rd_min=40.0, rd_max=220.0,
                tau_games=12.0, tau_idle=365.0):
    """★【第二刀 — 轻量 Elo 不确定性(RD)估计】非完整 Glicko,零外部依赖。

    物理含义:RD 越大 → 该队 Elo 越不可信。两个驱动因素:
      · 样本量 games_last_365d:近期打得少 → 不确定。指数收敛(tau_games≈12 场饱和)。
      · 时效性 days_since_last:久未参赛 → 即使有历史,竞技状态也成疑。线性爬升。

    用途:predict_from_elo_hedged 用 RD 把概率向均匀分布收缩(高 RD → 多 hedging)。
    walk-forward 安全:只用 cutoff 前数据统计(build_rd 保证)。
    返回 [rd_min, rd_max] 内的 RD。
    """
    converge = math.exp(-games_last_365d / tau_games)
    base = rd_min + (rd_max - rd_min) * converge
    idle_extra = max(0.0, days_since_last - tau_idle) / tau_idle
    rd = base + (rd_max - base) * min(idle_extra, 1.0) * 0.5
    return min(rd, rd_max)


def build_rd(path, cutoff_date, window_days=365):
    """逐队统计 [cutoff-window, cutoff) 内场数 + 距 cutoff 天数 → {队名: rd}。

    walk-forward:只读 cutoff 严格之前的比赛。每队:
      · games_last_365d = [cutoff-window, cutoff) 内出场次数(近期活跃度)
      · days_since_last = cutoff − 该队最近一场比赛的天数(无历史 → 取 window,视为久远)
    覆盖所有历史出场过的队;window 内零场的队 games=0 → RD 趋 rd_max(新队/久疏战阵)。
    """
    import datetime
    cutoff_d = datetime.date.fromisoformat(cutoff_date).toordinal()
    lo = cutoff_d - window_days
    games = defaultdict(int)
    last_seen = {}
    with open(path, newline="", encoding="utf-8") as f:
        for r in csv.reader(f):
            if not r or r[0] == "date" or len(r) < 9:
                continue
            if not (r[3].lstrip("-").isdigit() and r[4].lstrip("-").isdigit()):
                continue
            d = datetime.date.fromisoformat(r[0]).toordinal()
            if d >= cutoff_d:
                continue
            home, away = r[1], r[2]
            if d >= lo:
                games[home] += 1
                games[away] += 1
            for team in (home, away):
                if team not in last_seen or d > last_seen[team]:
                    last_seen[team] = d
    rd = {}
    for team in set(last_seen) | set(games):
        g = games.get(team, 0)
        days_idle = cutoff_d - last_seen.get(team, lo)
        rd[team] = estimate_rd(g, days_idle)
    return rd


def calibrate(elo):
    """线性校准到 eloratings 尺度(匹配均值/标准差),保证数值兼容 wc_predict 参数。"""
    vals = list(elo.values())
    mu, sd = statistics.mean(vals), statistics.pstdev(vals) or 1.0
    return {k: TARGET_MEAN + (v - mu) / sd * TARGET_STDEV for k, v in elo.items()}


def to_chinese(elo):
    """英文队名 Elo → 中文键(仅保留 EN_TO_ZH 覆盖的队)。"""
    return {EN_TO_ZH[k]: v for k, v in elo.items() if k in EN_TO_ZH}


def main():
    if not os.path.exists(RESULTS):
        sys.exit("✗ 找不到 results.csv,请先运行:python fetch_results.py")

    print("=" * 56)
    print("  从历史比赛重建 Elo 评分")
    print("=" * 56)
    elo, n, skipped = build_elo(RESULTS)
    print(f"  已处理 {n:,} 场有效比赛,{len(elo)} 支队伍"
          + (f"(跳过 {skipped} 场无比分)" if skipped else ""))
    elo = calibrate(elo)
    print(f"  校准到 eloratings 尺度(均值≈{TARGET_MEAN:.0f},σ≈{TARGET_STDEV:.0f})")

    with open(OUT_JSON, "w", encoding="utf-8") as f:
        json.dump(elo, f, ensure_ascii=False, indent=2, sort_keys=True)
    print(f"  已保存 {OUT_JSON}")

    print("-" * 56)
    print("  实力 Top 15:")
    for i, (team, e) in enumerate(sorted(elo.items(), key=lambda kv: -kv[1])[:15], 1):
        print(f"    {i:>2}. {team:<22}{e:6.0f}")

    zh_elo = to_chinese(elo)
    try:
        sys.path.insert(0, HERE)
        import wc_predict
        print("-" * 56)
        print(f"  与 wc_predict.py 现有 ELO_RATINGS 对比(重建覆盖 {len(zh_elo)} 队):")
        for team, old in sorted(wc_predict.ELO_RATINGS.items(), key=lambda kv: -kv[1]):
            new = zh_elo.get(team)
            if new is not None:
                print(f"    {team:<8}现有 {old:>5}   重建 {new:6.0f}   (Δ{new - old:+.0f})")
    except Exception as e:
        print(f"  (跳过与 wc_predict 对比:{e})")

    print("-" * 56)
    print("  ↓ 可直接替换 wc_predict.py 中 ELO_RATINGS 的字典字面量(按 Elo 降序):")
    print("ELO_RATINGS = {")
    for k, v in sorted(zh_elo.items(), key=lambda kv: -kv[1]):
        print(f'    "{k}": {round(v)},')
    print("}")

    print("=" * 56)
    print("✓ 把上面的字典粘进 wc_predict.py,或让我自动写回。")


if __name__ == "__main__":
    main()
