# 需求表:Dixon-Coles 攻防参数拟合 (compute_ad)

## 模块说明
从历史比赛(cutoff 前)用最大似然估计(MLE)拟合每队 `attack`/`defense` 参数,
带时间衰减(近期比赛权重高,半衰期可调)+ 小样本回退(近周期比赛不足的队回退中性)。
产出供 `wc_predict` 使用,替代静态 `AVG_TOTAL_GOALS`——让每队有自己的攻防强度。

## 参数化(Dixon-Coles 标准)
```
log λ_home = β0 + attack[home] + defense[away] + home_adv
log λ_away = β0 + attack[away] + defense[home]
进球数 ~ Poisson(λ),低比分用 Dixon-Coles τ 修正(ρ 复用 wc_predict.Config.RHO)
约束:Σ attack = 0, Σ defense = 0(可识别性)
时间权重:w(t) = exp(−ξ · (cutoff − t).days), ξ = ln2 / (halflife_years·365)
```
- `attack` 越大 → 进攻越强;`defense` 越大 → 防守越弱(让对手进更多)。
- 强防守队 `defense` 为负。

## 输入输出
- 输入:`results.csv` 路径、`cutoff_date`(ISO,默认 None=全量)、`halflife_years`(默认3)、`min_matches`(默认20)
- 输出:`({"队名": {"attack":float,"defense":float,"matches":int}}, {"beta0":float,"home_adv":float})`

## 需求项

| 编号 | 场景 | 输入 | 预期输出 | 备注 |
|---|---|---|---|---|
| AD-01 | 进攻强弱区分 | 真实数据 cutoff=2022-11-20 | Brazil.attack > 全体 attack 中位数 | 强队进攻参数应在上半 |
| AD-02 | 防守强弱区分 | 真实数据 cutoff=2022-11-20 | 存在 defense<0 的队(强防守) | defense 负=失球少 |
| AD-03 | 小样本回退中性 | 真实数据,某队 matches<min_matches | 该队 |attack| < 0.1 | 回退中性避免过拟合 |
| AD-04 | cutoff 无泄漏 | cutoff=2018 vs 2022 | Brazil.attack 两值不同 | walk-forward |
| AD-05 | λ 物理合理 | 任意两队算 λ | 0.2 < λ < 4.0 | 场均进球合理区间 |
| AD-06 | 时间衰减生效 | 同数据 cutoff 不同 | 参数不同 | 近期权重高 |
| AD-07 | 异常:空数据 | 仅表头的空 CSV | 抛 ValueError | 不静默崩溃 |
| AD-08 | 结构正确 | 真实数据拟合 | 返回 dict 含 attack/defense/matches 三键 | 契约 |
