# 需求表:fetch_wc2026(2026 世界杯赛程+结果抓取器)

## 模块职责
抓取 2026 世界杯赛程(未打)和已打结果,产出两个 CSV:
- `wc2026_live.csv` — 已打比赛,**9 列格式兼容 results.csv**(供 update_live append + compute_elo 复用)
- `wc2026_schedule.csv` — 未打赛程(date, home, away, group, stage, city)

## 数据源(多源容错)
1. **主源**:ESPN 非官方 JSON API `site.api.espn.com/.../soccer/fifa.world/scoreboard`(结构化,比 HTML 稳)
2. **回退**:Wikipedia "2026 FIFA World Cup" wikitable 解析
3. 双源全失败 → **raise,绝不静默写空**(脏数据比没数据更糟,会污染 Elo 回写)

## 队名标准化(关键)
ESPN/Wikipedia 队名 → results.csv 英文队名(否则 Elo 字典 KeyError):
- "USA" / "U.S." → "United States"
- "South Korea" → "Korea Republic"
- 维护 `NAME_NORMALIZE` 映射表,未覆盖的队原样保留 + 警告

## 点球大战处理
淘汰赛点球决胜:90/120 分钟比分入 live(Elo 按平局),点球结果单独存 `penalties` 列(扩展第 10 列,不影响 results.csv 9 列兼容)。

---

## 需求表(编号 | 场景 | 输入 | 预期输出 | 备注)

| 编号 | 场景 | 输入 | 预期输出 | 备注 |
|------|------|------|----------|------|
| FETCH-01 | ESPN 正常返回(混合) | mock JSON:2 场 completed(有比分)+ 1 场 scheduled | live.csv 含 2 行,schedule.csv 含 1 行 | 主路径 |
| FETCH-02 | 已打比赛字段完整 | mock completed:home="Brazil" score=2, away="Serbia" score=0 | live 行 = ["2026-06-13","Brazil","Serbia","2","0","FIFA World Cup","Group G",...,"TRUE"] | 9 列兼容 |
| FETCH-03 | ESPN schema 缺失字段 | mock JSON 缺 "competitions" 键 | 触发 Wikipedia 回退;若回退也失败则 raise ValueError | 容错链 |
| FETCH-04 | 队名标准化 | ESPN 返回 "USA" vs "South Korea" | live 写 "United States"、"Korea Republic" | 一致性 |
| FETCH-05 | 12 组分组提取 | mock 含 group="Group G" | 解析为 "G";12 组 A-L 全覆盖 | 2026 赛制 |
| FETCH-06 | 已打 vs 未打区分 | status="scheduled"(未来) | 入 schedule.csv,live.csv 不含 | 时间分流 |
| FETCH-07 | 点球决胜(淘汰赛) | mock:90分钟 1-1,点球 4-3 | live 90分钟比分 1-1;penalties 列="4-3" | Elo 按平局 |
| FETCH-08 | 缓存命中 | 同 URL 2 次调用 | 第 2 次不发起网络请求,返回缓存 | TTL 默认 300s |
| FETCH-09 | 双源全失败 | 网络断 + Wikipedia 解析空 | raise RuntimeError("抓取失败"),不写任何 CSV | 异常,不产脏数据 |
| FETCH-10 | 边界:赛事未开始(0 已打) | mock 全 scheduled | live.csv 仅列头,schedule.csv 含全部赛程 | 边界,空 live 合法 |

---

## 校验规则(复用 fetch_results.validate 思路,适配 JSON)
- ESPN JSON:必含 `events[]`,每个 event 含 `competitions[]`,每个 competition 含 `competitors[]`(2 个,score 数字)
- Wikipedia:wikitable 列数 ≥ 6(组/队/赛程),行数 ≥ 48(队)/104(场比赛)
- 产出 CSV:列头严格匹配 EXPECTED_HEADER,行数 > 0(schedule)或 ≥ 0(live,赛事未开始合法)

## 非功能
- 网络超时 30s,重试 1 次
- User-Agent 标识(复用 fetch_results 的 "MiroFishBot/1.0")
- 不依赖网络的单测:全部用 mock JSON/HTML fixture
