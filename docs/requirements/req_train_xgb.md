# 需求表:train_xgb(XGBoost 三态预测模型)

## 模块职责
用 XGBoost 学"比赛特征 → 胜/平/负三态概率",作统计模型(elo/ad)的异源对照。
动机:回测证明统计模型同源,加权集成收益微;XGBoost 从非线性特征组合出发提供新视角。

## 特征(9 维,严格 walk-forward)
| 特征 | 含义 | 来源 |
|------|------|------|
| elo_home / elo_away | 两队 Elo(初始 1500,差抵消无需校准) | 增量 update_elo_after_match |
| elo_diff | 主客 Elo 差 | 衍生 |
| gf_avg_home / ga_avg_home | 主队近 10 场进球/失球均值 | deque(maxlen=10) |
| gf_avg_away / ga_avg_away | 客队近 10 场进球/失球均值 | deque(maxlen=10) |
| tournament_k | 赛事权重(世界杯 50 / 预选 40 / 友谊 20) | compute_elo.tournament_k |
| neutral | 中立场标记(0/1) | results.csv 第 9 列 |

**标签**:0=主胜,1=平,2=客胜(multi:softprob 输出三态概率)。

## walk-forward(无泄漏)
单次遍历所有比赛(按日期),每场特征用**赛前** Elo/form(当前值),赛后才更新。
O(N) 高效,无需逐场 rebuild。

---

## 需求表(编号 | 场景 | 输入 | 预期输出 | 备注)

| 编号 | 场景 | 输入 | 预期输出 | 备注 |
|------|------|------|----------|------|
| TX-01 | 特征维度+标签 | mock 3 场(主胜/平/客胜) | 9 维特征,label=[0,1,2] | 编码正确 |
| TX-02 | 首场初始值 | mock 1 场 | elo_home=elo_away=1500, form=FORM_PRIOR | 无历史先验 |
| TX-03 | walk-forward 更新 | mock 2 场(A 首赢再战) | 第2场 A 的 elo>1500,首场仍=1500 | 赛前快照 |
| TX-04 | 日期升序 | mock 乱序 2 场 | rows 按日期升序 | 遍历前提 |
| TX-05 | 预测形状 | 80 样本训练 | predict (5,3),行和≈1,非负 | softprob |
| TX-06 | Brier 完美=0 | 确定且对预测 | 0.0 | 指标下界 |
| TX-07 | Brier 随机≈2/3 | 均匀预测 | ≈0.667 | 随机基线 |
| TX-08 | onehot 编码 | [0,1,2] | 3×3 单位阵 | 标签转换 |
| TX-09 | 回测跑通 | mock 训练+2018测试 | (brier, 40, model),brier≤0.767 | 端到端 |

---

## 回测结果(实测 2026-06-15)
| 届 | 测试场 | XGB Brier | Elo baseline | Δ | 判定 |
|----|--------|-----------|--------------|------|------|
| 2018 | 64 | 0.5765 | 0.5885 | −0.0120 | ✓突破 |
| 2022 | 64 | 0.6234 | 0.6020 | +0.0214 | ↓更差 |
| 均值 | 128 | 0.6000 | 0.5953 | +0.0047 | 略输 |

**发现**:XGB 2018 突破(捕捉 Elo 错过的非线性模式),2022 翻车(form 特征对疫情年敏感)。
两模型错误模式不同 → 集成沃土(任务14 combine_weights 动态加权)。

## XGBoost 参数
max_depth=4, eta=0.05, subsample=0.8, colsample_bytree=0.8, 300 round, seed=42。
适度正则防过拟合(49413 样本,9 特征,深度 4 足够)。

## 非功能
- 单次遍历构建特征 ~1s(49413 场)
- 训练 300 round ~2s
- 模型保存 xgb_wc.json(XGBoost 原生格式,可 ensemble.py 加载)
