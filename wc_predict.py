"""
wc_predict.py — 世界杯单场胜负预测（Elo + Dixon-Coles 泊松模型）

零依赖，仅用 Python 标准库。直接运行：
    python wc_predict.py

原理一句话：用两队 Elo 差换算出各自的"预期进球数"(泊松参数 λ)，
再用泊松分布枚举所有可能比分，叠加 Dixon-Coles 低比分修正，
最后求和得到胜/平/负三态概率与最可能比分。
"""

import math
import os
import sys
from itertools import product

# Windows 控制台默认 GBK，强制 stdout 用 UTF-8，避免中文/符号输出报错
if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8")


# =========================================================================
# ★ 决策点 1：球队 Elo 评分表
# 数据来源：由 compute_elo.py 从 49,413 场历史比赛(martj42/international_results,
#   截至 2026-06-27)自算并校准到 eloratings 尺度(均值≈1700,σ≈150)。
# 更新方法：python fetch_results.py && python compute_elo.py，把输出粘回这里。
# 原手抄值(2026-06-11,eloratings.net,含 est 估计)：西班牙2157 阿根廷2115 法国2063
#   英格兰2024 巴西1991 葡萄牙1989 荷兰1975 克罗地亚1935 德国1920 比利时1905
#   乌拉圭1895 意大利1885 瑞士1850 摩洛哥1845 哥伦比亚1830 墨西哥1800 美国1790
#   日本1795 塞内加尔1770 厄瓜多尔1755 韩国1740 澳大利亚1720 加拿大1690 库拉索1560
# =========================================================================
ELO_RATINGS = {
    "西班牙":   2107,
    "阿根廷":   2092,
    "法国":     2059,
    "英格兰":   2038,
    "巴西":     2024,
    "哥伦比亚": 2021,
    "葡萄牙":   2016,
    "厄瓜多尔": 2002,
    "荷兰":     1992,
    "德国":     1989,
    "日本":     1988,
    "摩洛哥":   1983,
    "乌拉圭":   1973,
    "克罗地亚": 1972,
    "墨西哥":   1969,
    "比利时":   1966,
    "澳大利亚": 1958,
    "意大利":   1948,  # (注意：意大利未必参赛 2026 世界杯)
    "瑞士":     1947,
    "塞内加尔": 1937,
    "韩国":     1934,
    "加拿大":   1919,  # (东道主)
    "美国":     1913,  # (东道主)
    "库拉索":   1764,
}


# =========================================================================
# ★ 队名映射：英文(results.csv / build_elo 键)↔ 中文(交互显示用)
# 覆盖 2026 世界杯全部 48 队(基于 fetch_wc2026 抓取的分组数据,非凭记忆)。
# tournament_sim 内部用英文(build_elo),显示/交互用中文经此映射。
# =========================================================================
HERE = os.path.dirname(os.path.abspath(__file__))
RESULTS = os.path.join(HERE, "results.csv")

EN_TO_ZH = {
    "Algeria": "阿尔及利亚", "Argentina": "阿根廷", "Australia": "澳大利亚",
    "Austria": "奥地利", "Belgium": "比利时", "Bosnia and Herzegovina": "波黑",
    "Brazil": "巴西", "Canada": "加拿大", "Cape Verde": "佛得角",
    "Colombia": "哥伦比亚", "Croatia": "克罗地亚", "Curaçao": "库拉索",
    "Czech Republic": "捷克", "DR Congo": "刚果(金)", "Ecuador": "厄瓜多尔",
    "Egypt": "埃及", "England": "英格兰", "France": "法国",
    "Germany": "德国", "Ghana": "加纳", "Haiti": "海地",
    "Iran": "伊朗", "Iraq": "伊拉克", "Ivory Coast": "科特迪瓦",
    "Japan": "日本", "Jordan": "约旦", "Mexico": "墨西哥",
    "Morocco": "摩洛哥", "Netherlands": "荷兰", "New Zealand": "新西兰",
    "Norway": "挪威", "Panama": "巴拿马", "Paraguay": "巴拉圭",
    "Portugal": "葡萄牙", "Qatar": "卡塔尔", "Saudi Arabia": "沙特阿拉伯",
    "Scotland": "苏格兰", "Senegal": "塞内加尔", "South Africa": "南非",
    "South Korea": "韩国", "Spain": "西班牙", "Sweden": "瑞典",
    "Switzerland": "瑞士", "Tunisia": "突尼斯", "Turkey": "土耳其",
    "United States": "美国", "Uruguay": "乌拉圭", "Uzbekistan": "乌兹别克斯坦",
}
ZH_TO_EN = {v: k for k, v in EN_TO_ZH.items()}   # 中文 → 英文(build_elo 查询键)


# =========================================================================
# ★ 决策点 2：可调参数（这些才是真正影响预测的旋钮）
# =========================================================================
class Config:
    # 单场比赛预期"总进球数"基线。
    # ★ 数据驱动取值 2.62 = 近4届世界杯加权均值：
    #   2010=2.27(权重1) + 2014=2.67(权重2) + 2018=2.64(权重2) + 2022=2.69(权重3)
    #   = (2.27×1 + 2.67×2 + 2.64×2 + 2.69×3) / 8 = 20.84/8 = 2.605 ≈ 2.62
    # 数据源：Wikipedia/FIFA 官方统计。近3届稳定在 2.64~2.69 且呈微升趋势。
    # 调大 → 预测更"开放"(更多 2-1/3-1)；调小 → 更"保守"(更多 1-0/0-0)。
    AVG_TOTAL_GOALS = 2.62

    # Elo 差异 → 进球份额 的转换敏感度。
    # Elo 每 100 分差约对应 58:42 的进球分配。调小 → 强弱更悬殊；调大 → 更平均。
    ELO_SCALE = 300.0

    # Dixon-Coles 低比分修正系数 ρ，取小负值（论文约 -0.1 ~ -0.05）。
    # 负值会让 0-0、1-1 概率上调、0-1/1-0 下调——泊松独立假设本会低估平局。
    RHO = -0.08

    # ★ 条件平局 boost（数据驱动，直击平局系统性漏判弱点）
    # 背景:2026 真实验证 10 场 4 平全漏,召回 0%。RHO 调更负已证伪(Brier 单调恶化);
    #   全局 draw boost 也证伪(对强弱悬殊场也误 boost → Brier 暴涨)。
    # 条件 boost:boost 因子随 |Elo 差| 指数衰减 —— 只对势均力敌场 boost:
    #   factor = 1 + (B-1)·exp(-|ΔElo|/SCALE);ΔElo=0 → factor=B;|ΔElo|>>SCALE → 1。
    # 物理依据:真实足球平局集中在势均力敌对决(双方互相制约),强弱悬殊场平局罕见。
    # 回测(scan_cond_boost.py):SCALE=120,B=2.0 →
    #   2018 召回 0→31%(Brier 0.5885→0.6134,+0.025);2022 召回 0→13%(Brier +0.008)。
    #   两届 Brier 代价都最小,是"准确优先"下的稳健甜点。设 B=None 关闭(测纯泊松 baseline)。
    DRAW_BOOST_B = 2.0
    DRAW_BOOST_SCALE = 120.0

    # 主场优势折算成 Elo 分。世界杯中立场默认 0；东道主用下面的 HOST_NATION_ADV。
    HOME_ADV_ELO = 0

    # ★ 三国联办东道主加成（数据驱动，非拍脑袋）
    # 锚点：常规足球主场 = +100 Elo（业界共识，opisthokonta.net/Elo 方法论，
    #       对应同级对手 ~64% 期望得分）。
    # 2026 是史上首次三国联办，比赛分散在 16 城 → 单国主场被稀释，基础取 +70。
    # 再按各自主场独特性修正：
    #   墨西哥 +90 = 70基础 + 20海拔(墨西哥城 2240m，客队需48h适应，高原实证影响)
    #   美国   +70 = 70基础（氛围强但比赛分散，无海拔）
    #   加拿大 +40 = 70基础 − 30文化(足球氛围三国中最淡)
    # 依据来源：opisthokonta.net(Elo主场方法论)、RotoWire(东道主91%出线/26%夺冠)、
    #          The Athletic(2026三国联办的海拔与旅行挑战分析)
    HOST_NATION_ADV = {"墨西哥": 90, "美国": 70, "加拿大": 40}

    # 枚举比分的最大进球数上限（单队），8 足够覆盖所有现实比分。
    MAX_GOALS = 8


# =========================================================================
# 核心数学
# =========================================================================
def elo_expected_score(elo_a, elo_b):
    """标准 Elo 期望得分（0~1，胜=1 平=0.5 负=0）。用于交叉验证泊松结果。"""
    return 1.0 / (1.0 + 10 ** ((elo_b - elo_a) / 400.0))


def elo_to_lambdas(elo_home, elo_away, total=Config.AVG_TOTAL_GOALS,
                   scale=Config.ELO_SCALE, home_adv=Config.HOME_ADV_ELO):
    """把两队 Elo（含主场优势）换算成各自的泊松预期进球 λ。

    思路：总进球池按强度 sigmoid 分配。强队分得多，弱队分得少。
    """
    eff_home = elo_home + home_adv
    # 主队分得总进球的份额（0.5 附近，随 Elo 差偏移）
    share_home = 1.0 / (1.0 + math.exp(-(eff_home - elo_away) / scale))
    lam_home = total * share_home
    lam_away = total * (1.0 - share_home)
    return lam_home, lam_away


def poisson_pmf(k, lam):
    """泊松分布 P(X=k) = λ^k · e^(-λ) / k!"""
    return (lam ** k) * math.exp(-lam) / math.factorial(k)


def dixon_coles_tau(x, y, lam_home, lam_away, rho=None):
    """Dixon-Coles 低比分相关修正因子 τ。

    只在 x,y ∈ {0,1} 时生效，用来修正泊松独立假设对平局/小比分的偏差。
    rho=None 时动态读 Config.RHO(避免 Python 默认参数在定义时锁定,
    使 Config.RHO 真正成为运行时可调旋钮)。
    """
    if rho is None:
        rho = Config.RHO
    if x == 0 and y == 0:
        return 1.0 - lam_home * lam_away * rho
    if x == 0 and y == 1:
        return 1.0 + lam_home * rho
    if x == 1 and y == 0:
        return 1.0 + lam_away * rho
    if x == 1 and y == 1:
        return 1.0 - rho
    return 1.0


def _poisson_three_way(lam_h, lam_a):
    """从两队 λ 算 (home_win, draw, away_win, score_matrix, top_scores)。

    泊松枚举 + Dixon-Coles τ 修正 + 归一化。elo 入口和 ad 入口共用此核心。
    """
    n = Config.MAX_GOALS
    matrix = [[0.0] * (n + 1) for _ in range(n + 1)]
    for i, j in product(range(n + 1), repeat=2):
        p = poisson_pmf(i, lam_h) * poisson_pmf(j, lam_a)
        p *= dixon_coles_tau(i, j, lam_h, lam_a)
        matrix[i][j] = p

    total = sum(matrix[i][j] for i in range(n + 1) for j in range(n + 1))
    for i in range(n + 1):
        for j in range(n + 1):
            matrix[i][j] /= total

    home_win = sum(matrix[i][j] for i in range(n + 1) for j in range(n + 1) if i > j)
    draw = sum(matrix[i][i] for i in range(n + 1))
    away_win = sum(matrix[i][j] for i in range(n + 1) for j in range(n + 1) if i < j)

    flat = [((i, j), matrix[i][j]) for i in range(n + 1) for j in range(n + 1)]
    flat.sort(key=lambda kv: kv[1], reverse=True)
    return home_win, draw, away_win, matrix, flat[:5]


def conditional_draw_boost(hw, dr, aw, elo_diff):
    """条件平局 boost:势均力敌场提升 draw 概率,强弱悬殊场不动。

    boost 因子随 |Elo 差| 指数衰减:factor = 1+(B-1)·exp(-|ΔElo|/SCALE)。
    由 Config.DRAW_BOOST_B / DRAW_BOOST_SCALE 控制;B 为 None 或 1.0 时直接返回(关闭)。
    物理依据:平局集中在势均力敌对决;全局 boost 会污染强弱悬殊场、伤 Brier(已证伪)。
    """
    b = Config.DRAW_BOOST_B
    if b is None or b == 1.0:
        return hw, dr, aw
    factor = 1.0 + (b - 1.0) * math.exp(-abs(elo_diff) / Config.DRAW_BOOST_SCALE)
    dr_b = dr * factor
    s = hw + dr_b + aw
    return hw / s, dr_b / s, aw / s


def predict_from_elo(elo_home, elo_away, home_adv_elo=None):
    """用两队 Elo 值(含主场加成)算预测,不查 ELO_RATINGS 字典。

    backtest / 整届推演的 elo 模式用这个:自带 Elo(build_elo 算的英文键)。
    返回 (home_win, draw, away_win, score_matrix, top_scores)。
    末尾套条件平局 boost(由 Config 开关控制,可关闭测纯泊松)。
    """
    if home_adv_elo is None:
        home_adv_elo = Config.HOME_ADV_ELO
    lam_h, lam_a = elo_to_lambdas(elo_home, elo_away, home_adv=home_adv_elo)
    hw, dr, aw, matrix, top_scores = _poisson_three_way(lam_h, lam_a)
    # 用"含主场优势的有效 Elo 差"判势均力敌:主场越强→差越大→越少 boost
    hw, dr, aw = conditional_draw_boost(hw, dr, aw, (elo_home + home_adv_elo) - elo_away)
    return hw, dr, aw, matrix, top_scores


def predict_from_elo_hedged(elo_h, rd_h, elo_a, rd_a, home_adv_elo=None,
                            max_w=0.5):
    """★【第二刀 learning 点 2 — 用户实现 w】带 Elo 不确定性的概率 hedging。

    动机:Elo 只是点估计,RD(compute_elo.estimate_rd)衡量其可信度。两队任一方 RD
    大(评分不可信)→ 应把三态概率向均匀分布收缩,避免对不可信评分过度自信。
    p_final = (1-w)·p_poisson + w·uniform,  uniform=[1/3,1/3,1/3]
    w∈[0,max_w]:w=0 完全信 Elo(高可信);w=max_w 最大收缩(低可信)。

    ── ★ TODO(你来实现 5-8 行,learning 点 2)──────────────────
    合并 rd_h/rd_a → 单个 rd_combined,再映射到 w∈[0,max_w]。设计选择(有取舍):
      · 合并方式:max(rd_h,rd_a)(两队任一不可信即整场不可信,推荐)/几何平均/求和。
      · 映射函数:线性 / sigmoid / 分段阈值。线性起点建议
            rd_combined = max(rd_h, rd_a)
            w = max_w * clamp((rd_combined - RD_LOW) / (RD_HIGH - RD_LOW), 0, 1)
        RD_LOW/RD_HIGH 可取 60/180(rd∈[40,220]的全域分位)。sigmoid 更平滑但多个旋钮。
    约束(测试锁定):w 必须随 rd_combined 单调递增(高 RD → 多 hedging)。
    ──────────────────────────────────────────────────────────
    返回 (home_win, draw, away_win, None, None)——末两位无意义(hedging 不产比分矩阵),
    保留 5 元组是为兼容现有 `hw,dr,aw,_,_ = ...` 解包。
    """
    if home_adv_elo is None:
        home_adv_elo = Config.HOME_ADV_ELO
    p_h, p_d, p_a, _, _ = predict_from_elo(elo_h, elo_a, home_adv_elo)

    # ★【learning 点 2 实现】max(rd) 合并 + 线性映射。
    # 合并:取两队 RD 的 max(短板效应——任一不可信 → 整场预测不可信)。
    # 映射:线性 rd=60→w=0(高可信不收缩),rd=180→w=max_w(最大收缩)。
    # 简洁可解释,零额外旋钮;后续回测可调 RD_LOW/RD_HIGH,或换 sigmoid 做平滑过渡。
    rd_combined = max(rd_h, rd_a)
    RD_LOW, RD_HIGH = 60.0, 180.0
    w = max_w * min(max((rd_combined - RD_LOW) / (RD_HIGH - RD_LOW), 0.0), 1.0)

    u = 1.0 / 3.0
    p_h_f = (1.0 - w) * p_h + w * u
    p_d_f = (1.0 - w) * p_d + w * u
    p_a_f = (1.0 - w) * p_a + w * u
    s = p_h_f + p_d_f + p_a_f
    return p_h_f / s, p_d_f / s, p_a_f / s, None, None


def predict_from_ad(ad_params, ad_glob, home, away, neutral=True):
    """用 Dixon-Coles 攻防参数算预测。

    home/away 是英文队名(匹配 ad_params 键)。backtest 的 ad 模式、整届推演用这个。
    返回 (home_win, draw, away_win, score_matrix, top_scores)。
    """
    import compute_ad
    lam_h, lam_a = compute_ad.expected_goals(ad_params, ad_glob, home, away, neutral)
    return _poisson_three_way(lam_h, lam_a)


# build_elo 缓存(_elo_value fallback 用,避免每次重算 49000 场)
_ELO_BUILD_CACHE = None


def _elo_value(team_zh):
    """中文队名 → Elo 值。覆盖 2026 全部 48 队。

    先查 ELO_RATINGS(手抄 24 队,eloratings 校准基准);miss 则经 ZH_TO_EN
    转英文查 build_elo(全队动态)。未知队 raise KeyError。
    """
    global _ELO_BUILD_CACHE
    if team_zh in ELO_RATINGS:
        return ELO_RATINGS[team_zh]
    en = ZH_TO_EN.get(team_zh)
    if en is None:
        raise KeyError(f"未找到球队: {team_zh}(不在 ELO_RATINGS,也非 2026 参赛队)")
    if _ELO_BUILD_CACHE is None:
        import compute_elo
        _ELO_BUILD_CACHE, _, _ = compute_elo.build_elo(RESULTS)
    val = _ELO_BUILD_CACHE.get(en)
    if val is None:
        raise KeyError(f"build_elo 无 {en}({team_zh})的历史数据")
    return val


def predict_match(team_home, team_away, home_adv_elo=None, verbose=True, odds_map=None, year=None):
    """预测一场比赛(按中文队名),返回 (home_win, draw, away_win, matrix, top_scores)。

    支持全部 48 队:ELO_RATINGS(24 队手抄)miss 时自动经 ZH_TO_EN → build_elo 查(延迟缓存)。
    内部委托给 predict_from_elo;本函数负责 队名→Elo + 报告打印。
    home_adv_elo=None 时用 Config.HOME_ADV_ELO;
    若主队是 2026 东道主,建议传 Config.HOST_NATION_ADV[队名]。

    异源优先(与 predict_upcoming/predict_one 一致):odds_map+year 命中时 1X2 概率
    用 devig 市场价(108 场回测 Δ=−0.066 优于 Elo);否则 Elo。matrix/top_scores
    始终 Elo(odds 无比分维度)。
    """
    elo_h = _elo_value(team_home)
    elo_a = _elo_value(team_away)
    adv = Config.HOME_ADV_ELO if home_adv_elo is None else home_adv_elo

    home_win, draw, away_win, matrix, top_scores = predict_from_elo(elo_h, elo_a, adv)

    src = "elo"
    if odds_map and year:
        import compute_odds   # 惰性 import,避免循环依赖(同 compute_ad 模式)
        en_h = ZH_TO_EN.get(team_home)
        en_a = ZH_TO_EN.get(team_away)
        if en_h and en_a:
            oh = compute_odds.lookup_odds(odds_map, en_h, en_a, year=year)
            if oh is not None:
                home_win, draw, away_win = compute_odds.devig_power(*oh)
                src = "odds"

    if verbose:
        lam_h, lam_a = elo_to_lambdas(elo_h, elo_a, home_adv=adv)
        elo_exp = elo_expected_score(elo_h + adv, elo_a)
        _print_report(team_home, team_away, elo_h, elo_a, adv,
                      lam_h, lam_a, home_win, draw, away_win, elo_exp, top_scores, src)

    return home_win, draw, away_win, matrix, top_scores


def _print_report(home, away, elo_h, elo_a, adv, lam_h, lam_a,
                  p_home, p_draw, p_away, elo_exp, top_scores, src="elo"):
    print("=" * 56)
    print(f"  {home}  vs  {away}")
    print("=" * 56)
    src_lbl = "市场报价 devig(108场回测 Δ=−0.066 优于 Elo)" if src == "odds" else "Elo"
    print(f"概率来源:  {src_lbl}")
    print(f"Elo:        {home} {elo_h} (+{adv}主场)   {away} {elo_a}")
    print(f"预期进球:   {home} {lam_h:.2f}   {away} {lam_a:.2f}")
    print("-" * 56)
    print(f"【三态概率】")
    print(f"  {home} 胜:  {p_home*100:5.1f}%")
    print(f"  平局:      {p_draw*100:5.1f}%")
    print(f"  {away} 胜:  {p_away*100:5.1f}%")
    print("-" * 56)
    print(f"【Elo 交叉验证】{home} 期望得分 = {elo_exp*100:.1f}%"
          f"  (≈ 胜率+半平局 = {(p_home + 0.5*p_draw)*100:.1f}%)")
    print("-" * 56)
    print(f"【最可能比分 Top5】")
    for (i, j), p in top_scores:
        print(f"  {i}-{j}   {p*100:5.2f}%   {'<-- 预测' if (i, j) == top_scores[0][0] else ''}")
    print("=" * 56)
    print()


# =========================================================================
# 演示：预测几场真实/示例对局
# =========================================================================
if __name__ == "__main__":
    print("\n⚽ 世界杯胜负预测（Elo + Dixon-Coles 泊松）")
    print(f"   参数：场均总进球 AVG={Config.AVG_TOTAL_GOALS}（近4届加权均值）")
    print(f"   东道主加成：墨{Config.HOST_NATION_ADV['墨西哥']}"
          f"/美{Config.HOST_NATION_ADV['美国']}"
          f"/加{Config.HOST_NATION_ADV['加拿大']}\n")

    # 案例 1：决赛级对决（中立场地）
    predict_match("西班牙", "阿根廷", home_adv_elo=0)

    # 案例 2：已打过的比赛（巴西 1-1 摩洛哥）—— 可对比模型给的 1-1 概率
    predict_match("巴西", "摩洛哥", home_adv_elo=0)

    # 案例 3：强弱悬殊
    predict_match("德国", "库拉索", home_adv_elo=0)

    # 案例 4：美国东道主主场（加成值取自 HOST_NATION_ADV）
    predict_match("美国", "塞内加尔", home_adv_elo=Config.HOST_NATION_ADV["美国"])

    # 案例 5：墨西哥高原主场（加成最高，因为墨西哥城海拔 2240m）
    predict_match("墨西哥", "日本", home_adv_elo=Config.HOST_NATION_ADV["墨西哥"])
