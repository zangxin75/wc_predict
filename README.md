# 世界杯胜负预测系统（Elo + Dixon-Coles + XGBoost + 蒙特卡洛）

多模型预测系统：从单文件 Elo+泊松基准，扩展到 **Dixon-Coles 攻防 MLE、XGBoost 三态模型、
统计+ML 集成、整届蒙特卡洛推演、实时结果回写**。支持 walk-forward 回测验证、2026 全部 48 队。
原 `wc_predict.py` 单文件接口保留（覆盖 48 队：手抄 24 队 miss 时自动 build_elo fallback）。

科学栈：numpy / scipy / pandas / scikit-learn / xgboost（见 `requirements.txt`）。

## 文件清单

| 文件 | 作用 |
|---|---|
| `wc_predict.py` | 核心预测（Elo→λ→泊松→Dixon-Coles），覆盖 48 队 |
| `compute_elo.py` | Elo 顺序更新（eloratings K 因子分档 + cutoff walk-forward） |
| `compute_ad.py` | Dixon-Coles 个体攻防 MLE（scipy.optimize） |
| `backtest.py` | walk-forward 回测（elo/ad/ensemble，Brier 评分，★learning 点2） |
| `fetch_wc2026.py` | 2026 赛程+结果抓取（ESPN API，缓存+去重+Wikipedia 回退） |
| `tournament_sim.py` | 整届蒙特卡洛推演（2026 新赛制 12 组→32 强，★learning 点3） |
| `train_xgb.py` | XGBoost 三态模型（9 维特征，严格 walk-forward） |
| `ensemble.py` | 统计+XGBoost 加权集成（★learning 点1 combine_weights） |
| `update_live.py` | 2026 已打结果回写 results.csv（幂等三元组去重） |
| `数据获取指南.md` / `docs/` | 数据源 + `backtest_findings.md` 回测结论 + 各模块需求表 |
| `tests/` | 各模块参数化测试（`pytest tests/`，71 passed） |

## 快速开始

```bash
cd E:\wsldisk\MiroFish\wc_predict
python wc_predict.py
```

会输出 4 场示例预测（西班牙vs阿根廷、巴西vs摩洛哥、德国vs库拉索、美国vs塞内加尔）。

## 预测任意一场比赛

打开 `wc_predict.py`，在底部 `if __name__ == "__main__":` 区加一行即可：

```python
predict_match("法国", "荷兰", home_adv_elo=0)
```

注意：两队名称必须在 `ELO_RATINGS` 字典里。新球队就往字典里加一行 `"队名": Elo分数`。

## 方案原理（三步）

```
两队 Elo 差
    │  (① sigmoid 分配总进球池)
    ▼
各队预期进球 λ_home, λ_away
    │  (② 泊松分布枚举所有比分)
    ▼
每个比分的联合概率 = P(主队进i球) × P(客队进j球) × τ
    │  (③ Dixon-Coles 修正低比分偏差)
    ▼
归一化 → 求和 → 胜/平/负三态概率 + 最可能比分
```

`★ 原理要点`
- **为什么用泊松？** 足球进球是低频独立事件，泊松分布是建模"单位时间内随机事件发生次数"的经典工具，是足球比分预测的事实标准。
- **Dixon-Coles 的 τ 修正什么？** 纯泊松假设两队进球独立，会低估 0-0、1-1 这类平局（实际足球里弱队摆大巴、强队相互制约，平局比独立假设多）。τ 把低比分的联合概率上调。
- **Elo 在这里的作用：** Elo 把"两队谁强"浓缩成一个可比较的数字，用来决定进球池怎么分。没有 Elo，你就没法量化强弱。

## 调参旋钮（真正影响预测的地方）

`wc_predict.py` 顶部 `Config` 类里有 4 个旋钮：

| 参数 | 默认 | 调节效果 |
|---|---|---|
| `AVG_TOTAL_GOALS` | 2.6 | 调大→比赛更"开放"(2-1/3-1变多)；调小→更"保守"(1-0/0-0变多) |
| `ELO_SCALE` | 300 | 调小→强弱更悬殊；调大→更平均 |
| `RHO` | -0.08 | Dixon-Coles 修正强度，越负平局越多 |
| `HOME_ADV_ELO` | 0 | 中立场默认0；给东道主设 +65 左右 |

`★ 这些才是值得你思考的决策点`，不是填空题：
- 这届世界杯是美/加/墨联合主办，**东道主有几支、各给多少主场分**是个真问题。给多了高估东道主，给少了忽略球迷助威。
- `AVG_TOTAL_GOALS` 用 2.6（历史均值）还是更高？现代足球进球率在上升。

## 准确率预期（必须诚实）

- 单场**三态预测**（胜/平/负取最高概率）：世界杯小组赛阶段约 **55%~60%** 命中率，淘汰赛因冷门多会下降。
- **精确比分**命中率很低（最高概率的比分通常也就 12%~15%），不要指望精确比分——看的是三态概率和"最可能几个比分"的分布。
- 这个模型**会稳定输给博彩公司赔率隐含的概率**（赔率是市场真金白银校准过的，包含了大量你我没有的信息）。

## 局限与下一步

这个 Elo+泊松方案的**优点**：数据要求极低（只要 Elo 就能跑）、可解释、零依赖。
**缺点**：忽略了球员伤病、战术相克、临场状态、红牌伤病等。

**已实现 XGBoost**（`train_xgb.py`）：9 维特征（Elo + 近场进球状态 + 赛事权重），严格 walk-forward。
回测结果：**2018 突破 elo baseline**（Brier 0.5765 vs 0.5885，−1.2pp，异源增益验证），
但 2022 翻车（form 特征对疫情年敏感，0.6234 vs 0.6020）。

**learning 点 1 已实现（分歧门控）**：`ensemble.py:combine_weights` 已实现动态加权
—— xgb 与 elo 预测一致时借力 xgb，分歧时退回纯 elo 规避风险。回测均值 **0.5951**，
是首个低于"全程用 elo"（0.5953）的集成策略。两届都超最优在当前数据下不可达
（2022 xgb 翻车源于疫情，不可事前预测），详见 `docs/backtest_findings.md` 第 5 节
（含 recent_density / 验证窗口 / 分歧门控 三种信号的完整对比）。

**3 处 learning 点**（均已实现核心设计 + 取舍注释）：
1. `ensemble.combine_weights` — ✓ 分歧门控（均值 0.5951，首个超纯 elo）
2. `backtest.score_metric` — ✓ 三指标 Brier+LogLoss+Accuracy（交叉验证排名一致）
3. `tournament_sim.resolve_knockout_draw` — ✓ Elo-based 点球（强队夺冠概率↑）

## 一个现实验证点

模型对 **巴西 vs 摩洛哥** 给出的最高概率比分是 **1-1**，而 2026 世界杯这场真实赛果**正是 1-1**。当然这是 n=1 的巧合，不是模型可靠性的证据——但它说明模型方向是对的。
