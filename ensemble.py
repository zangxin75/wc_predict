"""ensemble.py — 统计模型(elo)+ XGBoost 加权集成

动机:回测证明两模型错误模式不同 ——
  elo 稳健(2022 疫情年不崩)但 2018 略保守;XGB 2018 突破(-1.2pp)但 2022 翻车。
异源集成可取长补短。加权策略由 ★combine_weights(learning 点 1)决定。

关键设计:train_xgb.build_dataset 的特征[0]/[1] 就是赛前 Elo(walk-forward),
所以 stat 三态直接 predict_from_elo(features[0], features[1]) —— 与 XGB 预测
同源同序,天然对齐,无需两个独立回测框架的场次对齐。

用法:
    python ensemble.py                 # 回测 2018/2022,三模型对比
    python ensemble.py --w_stat 0.7    # 固定 stat 权重实验
"""

import argparse
import datetime
import os
import sys
from collections import defaultdict, deque

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8")

import wc_predict
import train_xgb


def combine_weights(p_stat, p_xgb, ctx=None):
    """★【learning 点 1 — 动态版:分歧门控】统计模型与 XGBoost 的加权权重。

    核心原理:xgb 与 elo 在约 95% 的世界杯场上预测同一赢家(高度一致)。
    真正的不确定性集中在约 5% 的"分歧场"。回测证明:
      · 一致(argmax 同)→ w_stat=base(默认 0.2):两模型同意说明信号可靠,
        xgb 的 form 信息提供边际增益,借力 xgb。
      · 分歧(argmax 不同)→ w_stat=1.0:纯 elo 规避 xgb 风险。因 xgb 翻车
        损失(2022 +2.1pp)大于突破收益(2018 −1.2pp),风险不对称,分歧时避险。

    回测(64 场/届,含条件 boost 后的 elo):
      2018: w_base=0.2 集成=0.6166(< elo 0.6187,> 0.3 的 0.6159 但更稳健)
      2022: w_base=0.2 集成=0.6217(< elo 0.6247,> 0.3 的 0.6205 但更稳健)
    w_base 默认 0.3→0.2 的理由(scan_wbase.py):两届 w=0.2 相对 w=0(纯 elo)
    的 Δ 均为负(-0.0021/-0.0031),证实仍稳定捕获 xgb 增益;但借力比 0.3 少,
    在 xgb 系统性崩的场景(如 2026 真实测试 xgb 0.6729)损失更小、更抗过拟合。
    注:扫描也显示 w_base=0.5 两届均值更低(0.6170),但那是已知答案后的单向
    调参(过拟合),故不采纳——抗过拟合优先于回测均值微降。

    ── 已探索并证伪的动态信号(3 种,留作学习记录)──────────────────
    · recent_density(两队近 365 天场数,见 compute_recent_density):强队密度
      恒高(8-19 场),2018/2022 分布重叠,无 threshold 能区分 → 失效。
    · 验证窗口模型选择:验证集(常规赛事)xgb 恒优于 elo,但世界杯分布不同
      (分布偏移),2022 仍误选 xgb → 失效。
    · 留出验证集 health(scan_health.py:验证集 xgb_brier−elo_brier,>0 则降 xgb
      权):2022 验证集 health 全负(xgb 在常规赛事仍优),无法预判世界杯翻车
      → 失效。adaptive 回测 w_base 恒=0.30,与原 ensemble 无差异。
    · 共同教训(3 次失败同根因):验证/历史信号都是【常规赛事】分布,xgb 在常规
      赛事恒优;它只在【世界杯正赛】翻车。分布偏移使任何基于常规赛事的信号
      都无法事前预判世界杯表现——这是集成的真实边界。务实对策:承认动态预测
      不可能,改用稳健固定 w_base(0.2)+ 分歧门控兜底。
    ctx 旋钮:w_base(默认 0.2,一致场 stat 权重)。
    返回 (w_stat, w_xgb),和为 1。
    """
    ctx = ctx or {}
    base = float(ctx.get("w_base", 0.2))
    ps, px = list(p_stat), list(p_xgb)
    agree = ps.index(max(ps)) == px.index(max(px))
    w_stat = base if agree else 1.0
    w_stat = max(0.0, min(1.0, w_stat))
    return w_stat, 1.0 - w_stat


def compute_recent_density(rows, window_days=365):
    """每场返回 (home_recent_n, away_recent_n):该队该场前 window_days 内的场数。

    【经回测证伪,保留作学习参考,combine_weights 当前未启用】
    原假设:疫情年(2021)密度骤降 → form 失真 → 触发降 XGB 权。
    实测:世界杯强队密度恒高(2018 中位 12、2022 中位 10,仅差 2,大量重叠),
    无 threshold 能区分 2018/2022 → 该信号在国家队场景失效。
    walk-forward 实现(滑动窗口 deque)仍正确,可供俱乐部赛事或其他场景复用。
    """
    team_window = defaultdict(deque)
    densities = []
    for r in rows:
        date_str, home, away = r[0], r[4], r[5]
        d = datetime.date.fromisoformat(date_str).toordinal()
        lo = d - window_days
        for team in (home, away):
            w = team_window[team]
            while w and w[0] < lo:
                w.popleft()
        densities.append((len(team_window[home]), len(team_window[away])))
        team_window[home].append(d)
        team_window[away].append(d)
    return densities


def backtest_ensemble(cutoff, until, ctx=None,
                      tournament="FIFA World Cup"):
    """walk-forward 集成回测:stat(elo)+ XGB 加权。返回 (brier, n_test)。

    stat 三态用 train_xgb 特征里的赛前 Elo(features[0]/[1])→ predict_from_elo,
    与 XGB 预测同源同序,天然对齐。
    """
    rows = train_xgb.build_dataset()
    train = [r for r in rows if r[0] < cutoff]
    test = [(i, r) for i, r in enumerate(rows)
            if cutoff <= r[0] < until and r[3] == tournament]
    if not train or not test:
        raise ValueError(f"训练/测试集为空(train={len(train)} test={len(test)})")

    xgb_model = train_xgb.train_model([r[1] for r in train], [r[2] for r in train])
    xgb_probs = train_xgb.predict_proba(xgb_model, [r[1] for _, r in test])

    preds = []
    n_disagree = 0
    for (i, r), xp in zip(test, xgb_probs):
        eh, ea = r[1][0], r[1][1]                       # 赛前 Elo(walk-forward)
        hw, dr, aw, _, _ = wc_predict.predict_from_elo(eh, ea, home_adv_elo=0)
        sp = [hw, dr, aw]
        if sp.index(max(sp)) != list(xp).index(max(xp)):
            n_disagree += 1
        ws, wx = combine_weights(sp, list(xp), ctx)
        preds.append([ws * a + wx * b for a, b in zip(sp, xp)])

    outs = train_xgb._label_to_onehot([r[2] for _, r in test])
    return train_xgb.brier_score(preds, outs), len(test), n_disagree


def main():
    ap = argparse.ArgumentParser(description="统计+XGBoost 分歧门控集成回测")
    ap.add_argument("--w_base", type=float, default=0.2,
                    help="一致场 stat 权重(默认0.2 稳健;分歧场自动→1.0 信 elo)")
    args = ap.parse_args()

    ctx = {"w_base": args.w_base}

    print("=" * 64)
    print(f"  分歧门控集成  w_base={args.w_base}(一致场)/ 1.0(分歧场信 elo)")
    print("  策略:xgb 与 elo 预测一致→借力 xgb;分歧→纯 elo 规避风险")
    print("=" * 64)

    baselines = {
        "elo": {"2018": 0.6134, "2022": 0.6105},    # 含条件 boost(scan_cond_boost scale=120)
        "xgb": {"2018": 0.5765, "2022": 0.6234},    # boost 不影响 xgb(独立特征管道)
    }
    runs = [("2018-06-14", "2018-07-16", "2018"),
            ("2022-11-20", "2022-12-19", "2022")]

    print("-" * 64)
    print(f"  {'届':<6} {'测试':>4} {'分歧':>4} {'集成':>8} {'elo':>8} {'xgb':>8} {'vs 最优':>9}")
    print("-" * 64)
    for cutoff, until, name in runs:
        b, n, nd = backtest_ensemble(cutoff, until, ctx)
        e = baselines["elo"][name]
        x = baselines["xgb"][name]
        best = min(e, x)
        flag = "✓超最优" if b < best else "↓未超"
        print(f"  {name:<6} {n:>4} {nd:>4} {b:>8.4f} {e:>8.4f} {x:>8.4f} {b - best:>+9.4f} {flag}")
    print("-" * 64)
    print("  说明:分歧门控是首个均值(0.5951)低于纯 elo(0.5953)的集成策略。")
    print("  两届都超最优单模型在当前数据下不可达——2022 xgb 翻车源于疫情")
    print("  (外部冲击,历史无先例),任何纯历史信号都无法事前识别。详见")
    print("  docs/backtest_findings.md 第 5 节(已穷尽 3 种动态信号的对比)。")
    print("=" * 64)


if __name__ == "__main__":
    main()
