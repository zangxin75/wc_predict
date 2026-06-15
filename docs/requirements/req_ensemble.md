# 需求表:ensemble(统计 + XGBoost 集成)

## 模块职责
把统计模型(elo 三态)与 XGBoost 三态按 ★combine_weights(learning 点 1)加权,
产出集成预测。动机:两模型错误模式不同(elo 稳健 / XGB 突破),异源集成取长补短。

## 关键设计:同源对齐
train_xgb.build_dataset 的特征 [0]/[1] 是赛前 Elo(walk-forward 增量更新),
所以 stat 三态直接 `predict_from_elo(features[0], features[1])` —— 与 XGB 预测
同源同序,天然对齐,无需两个独立回测框架的场次匹配。

校准无关:train_xgb Elo 未校准(均值 1500),backtest baseline 校准(1700),
但 `elo_to_lambdas` 只用 Elo 差,offset 抵消 → 两 stat 三态完全一致。

## learning 点 1:combine_weights(p_stat, p_xgb, ctx)
默认固定 0.5/0.5(最简基线)。扩展方向见函数注释:
A) 固定权重  B) 按队伍数据量动态  C) 按 XGB 置信度(熵)  D) 按赛事阶段

---

## 需求表(编号 | 场景 | 输入 | 预期输出 | 备注)

| 编号 | 场景 | 输入 | 预期输出 | 备注 |
|------|------|------|----------|------|
| ENS-01 | 默认权重 | 任意 p_stat/p_xgb | (0.5, 0.5) | learning 点基线 |
| ENS-02 | ctx 覆盖 | ctx={'w_stat':0.7} | (0.7, 0.3) | 实验旋钮 |
| ENS-03 | 权重和=1 | w_stat ∈ {0,.25,.5,.75,1} | ws+wx=1 | 归一化 |
| ENS-04 | clamp | w_stat=1.5 / -0.3 | ws∈[0,1] | 防越界 |
| ENS-05 | 加权正确 | sp=[.6,.2,.2] xp=[.4,.4,.2] w=0.5 | mixed=[.5,.3,.2] | 中点 |
| ENS-06 | 端到端回测 | 2018 世界杯 | (brier, 64),brier∈[0,0.767] | @slow |

---

## 回测结果(实测 2026-06-15,combine_weights 默认 0.5)
| 届 | 集成 | elo | xgb | vs 最优单模型 |
|----|------|-----|-----|--------------|
| 2018 | (跑 main 查看) | 0.5885 | 0.5765 | — |
| 2022 | (跑 main 查看) | 0.6020 | 0.6234 | — |

预期:固定 0.5 集成介于两模型之间,难超最优。**动态 combine_weights**(按置信度/数据量)
是超最优的钥匙 —— 这是 learning 点 1 的设计价值。
