# 需求表:update_live(2026 已打结果回写)

## 模块职责
把 fetch_wc2026 抓的 `wc2026_live.csv`(已打比赛)回写到 `results.csv`,
供 compute_elo / compute_ad 重算参数,实现"实时数据 → 参数刷新 → 预测更新"闭环。

## 数据流
```
fetch_wc2026 → wc2026_live.csv → update_live(--apply) → append results.csv
                                                          ↓
                                            重跑 compute_elo / compute_ad → 新 Elo/攻防
```

## 去重(关键,幂等保证)
- key = `(date, home_team, away_team)` 三元组
- fetch 多次跑拉到同样已打比赛 → 三元组去重,重复跑不重复 append
- live 文件内部自身重复也去重
- 队名一致:wc2026_live 经 NAME_NORMALIZE 后与 results.csv 同(传统英文名)

## 安全
默认 **dry-run**(只报告新增数 + 示例),`--apply` 才真写入 results.csv。
修改共享数据文件,先看后写。

---

## 需求表(编号 | 场景 | 输入 | 预期输出 | 备注)

| 编号 | 场景 | 输入 | 预期输出 | 备注 |
|------|------|------|----------|------|
| UL-01 | diff 新场 vs 已存在 | results 有(A,B),live 有(A,B)+(C,D) | new=1(C,D), dup=1 | 三元组去重 |
| UL-02 | append 写入 | 空文件 + 1 新行 | 写入 1 行,读回 home=C | append 模式 |
| UL-03 | 幂等 | 回写后再 diff | new=[] | 重复跑安全 |
| UL-04 | live 内部去重 | live 含 2 相同行 | new=1 | 自身重复 |
| UL-05 | 空输入 | new_rows=[] | 返回 0,不写 | 边界 |
| UL-06 | live 不存在 | 无 live 文件 | new=[],dup=0,不抛 | 容错 |
| UL-07 | results 不存在 | 无 results | live 全部视为新 | 首次初始化 |

---

## 验收(计划 Verification #4)
抓一场 2026 已打比赛 → update_live --apply → results.csv 新增该场 →
重跑 build_elo → 同一对决预测变化方向合理(赢队 Elo↑→胜率↑)。

## 后续(任务16 端到端)
update_live --apply 后自动触发 build_elo 重算 + params 刷新的"一键更新"脚本
(当前需手动重跑 compute_elo,任务16 打包)。
