# 联赛板块扩展设计 spec（v2 · critic 审计修订版）

- 日期：2026-10-09（v2 修订，吸收 critic 审计 1 BLOCKER + 6 MAJOR + 6 MINOR + 3 NIT 全部 findings）
- 状态：待复审
- 范围：wc_predict 从「2026 世界杯单一赛事」扩展为「WC 旗舰 + 五大联赛/欧冠联赛板块」

---

## 1. 背景与目标

现有系统只覆盖 FIFA World Cup 2026（48 队，杯赛赛制）。扩展决策（用户已确认）：

| 决策点 | 结论 |
|---|---|
| 首期联赛 | 英超(E0)、西甲(SP1)、意甲(I1)、德甲(D1)、法甲(F1)、欧冠(UCL) |
| 预测深度 | 完整引擎逐联赛复刻：Elo + Dixon-Coles + XGBoost + 比分矩阵 + 价值下注 + 赛季推演 |
| 站点结构 | WC 保持旗舰，新增 `/leagues/` 顶级板块 |
| 技术路线 | A. 并行管道（新增 `leagues/` 独立模块；WC 侧改动限 §3 白名单） |
| 付费门控 | 预测免费展示；**赔率与价值下注需 Pro**（与 WC 门控强度对齐，见 §4.8） |

成功标准：
1. `/leagues/` 对 6 个赛事展示：upcoming 赛程 + 三态预测 + 比分预测 + 实时积分榜 + 夺冠/前四/降级概率（5 联赛，P2 达成）+ 欧冠预测（P3 达成）。
2. 非 Pro 用户可见预测、不可见赔率与价值下注（402/mask，与 WC 一致）；Pro 用户全量可见。
3. 既有 71 个测试全绿不退化，WC 页面行为无变化。
4. 回测报告（近 3 赛季 × 5 联赛，模型 vs B365 收盘价基线，Brier/LogLoss/Accuracy）落盘 `docs/leagues_backtest.md`。

## 2. 数据源（逐项实测，2026-10-09）

| # | 源 | 用途 | 成本 | 实测证据 |
|---|---|---|---|---|
| D1 | football-data 赛季 CSV `mmz4281/{season}/{code}.csv` | 1993 至今历史比分 + Bet365/Pinnacle 收盘赔率 + OU2.5 + 亚盘 | 免费 | `curl -sL .../2526/E0.csv` → 200，80+ 列（B365H…PSH…AHh…B365CAHA） |
| D2 | football-data `fixtures.csv` | 近期 upcoming 赔率（**约 4 天滚动窗口**，199 行实测仅覆盖 10-09~10-12） | 免费 | `curl -sL .../fixtures.csv` → 199 场；Pinnacle 列名为 **PPH/PPD/PPA**（与赛季 CSV 的 PSH/PSD/PSA 不同） |
| D3 | ESPN scoreboard **按月迭代** `soccer/{league}/scoreboard?dates=YYYYMM&limit=400` | **全季赛程（已赛+未赛）**、UCL 历史赛果回填 | 免费 | `eng.1?dates=202705` → 50 场全 'pre'（05-01→05-30，赛季末已发布）；`uefa.champions?dates=202611` → 36 场；`uefa.champions?dates=20180526` → 2018 决赛（历史深度 ≥2018 已验证） |
| D4 | TheOddsAPI `soccer_uefa_champs_league` | 欧冠周赔率补充（可选 sharpen 层） | 免费 500 req/月 | 月耗 < 20 req |

**关键实测结论**：
- **B-1 修复**：fixtures.csv 只是 ~4 天滚动窗口，不能支撑赛季推演；**全季赛程走 D3（ESPN 按月迭代）**，已验证赛季末月份（2027-05）的未赛场次完整发布。6 赛事 × 12 月 = 72 次请求/季，免费无 key。
- **M-1 修复**：UCL 历史经 ESPN 按月回填，2018 决赛实测可取；回填起点定为 2015-16 赛季（≥10 季跨联赛连接样本）。若个别月份缺数据 → 回退：Elo 池仅 5 联赛，UCL 场次用联赛内 Elo 直接映射（§4.3）。
- **M-4 修复**：fixtures.csv 与赛季 CSV 列名不一致（PPH vs PSH 等），归一化层必须按 **DictReader 列名映射**（§4.2 附对照表），禁止按列位置。
- **已知限制**：OU 仅 2.5 一档、亚盘为主线一档；fixtures.csv 不含欧冠；clubelo.com 实测 502 不自依赖。

## 3. 架构总览与 WC 改动白名单

```
D1 赛季 CSV ──┐
D2 fixtures ──┼─→ leagues/fetch_*.py ─→ data/leagues/{CODE}/*.csv|json
D3 ESPN 按月 ──┤        (clubs_aliases.json 归一)
D4 OddsAPI ───┘                  │
                                 ▼
   leagues/elo.py（统一俱乐部 Elo 池，walk-forward）
   leagues/dixon_coles.py（每赛事攻防 MLE；UCL 借用联赛参数）
   leagues/train_xgb.py（跨联赛单模型）
   leagues/predict.py（三态 + 比分矩阵 + 集成门控）
   leagues/season_sim.py（D3 全季赛程 × 蒙特卡洛）
                                 │
                                 ▼
   webapp/leagues.py（APIRouter）→ /api/leagues/*
   webapp/leagues_cache.py（独立缓存实现，global 声明完整）
   webapp/leagues_updater.py（独立线程独立循环）
   webapp/static/leagues.html + leagues.js
```

**M-6 修复 · WC 侧允许改动白名单（此外一律禁改）**：
| 文件 | 允许的改动 |
|---|---|
| `webapp/app.py` | import + `include_router(leagues.router)` + lifespan 启动 leagues_updater 线程 |
| `webapp/static/index.html` | 导航栏加「联赛」入口 + 版本号 script 标签 |
| `.gitignore` | **新增** `data/leagues/`、`.leagues_cache.json` 条目（注意：现有 `results.csv`/`elo_ratings.json` 是入库的，本条目是新纪律而非沿用先例） |
| `webapp/static/i18n/*.json` + `i18n.js` | 新增 key + `I18N_VER` bump |
| `tests/`、`docs/`、CHANGELOG | 新增 |

leagues updater = **独立线程、独立循环**（在 lifespan 中与现有 updater 并行启动），两者共享的只有进程，互不调用、互不惊动。回归边界：71 个既有测试全绿；新 import 不得影响 conftest 收集。

## 4. 组件设计

### 4.1 `leagues/config.py` — 赛事注册表

```python
LEAGUES = {
  "E0":  {"name": "Premier League", "name_zh": "英超", "fd_code": "E0",  "espn": "eng.1"},
  "SP1": {"name": "La Liga",        "name_zh": "西甲", "fd_code": "SP1", "espn": "esp.1"},
  "I1":  {"name": "Serie A",        "name_zh": "意甲", "fd_code": "I1",  "espn": "ita.1"},
  "D1":  {"name": "Bundesliga",     "name_zh": "德甲", "fd_code": "D1",  "espn": "ger.1"},
  "F1":  {"name": "Ligue 1",        "name_zh": "法甲", "fd_code": "F1",  "espn": "fra.1"},
  "UCL": {"name": "Champions League","name_zh": "欧冠", "fd_code": None,  "espn": "uefa.champions",
          "sport_key": "soccer_uefa_champions_league"},
}
def current_season(today=None) -> str: ...  # 8月1日边界推导 "2526" 等，可配置覆盖
```

**赛季滚动**：`CURRENT_SEASON` 不写死——按日期推导（8 月 1 日起为新赛季），env 变量可覆盖；fetch_history 按已知赛季区间逐季下载，404 即跳过；ESPN 按月拉取时**空 events 或请求异常均计为缺月**（N-3：7 月等休赛空月返回空 events 而非 404，措辞统一，缺月计数供 §4.3 降级判断）。新增联赛 = 加一行配置 + 跑 fetch。

### 4.2 数据抓取与归一化

| 模块 | 职责 | 输出 |
|---|---|---|
| `leagues/fetch_history.py` | D1 逐季下载 → 归一化合并（首次全量回填，之后增量） | `data/leagues/{CODE}/results.csv` |
| `leagues/fetch_schedule.py` | **D3 按月迭代**拉全季赛程（已赛带比分 + 未赛） → 每赛事 schedule | `data/leagues/{CODE}/schedule.csv` |
| `leagues/fetch_fixtures.py` | D2 近期赔率快照（按列名映射） | `data/leagues/{CODE}/fixtures.csv` |
| `leagues/fetch_ucl.py` | D3 回填 UCL 历史（2015-16 起） + 当月赛程；欧冠周可选 D4 补赔率 | `data/leagues/UCL/results.csv`、`schedule.csv` |
| `leagues/clubs_aliases.json` | **跨源队名归一**（ESPN "Manchester City" ↔ football-data "Man City"），含校验：UCL 每队必须映射到联赛池俱乐部或显式登记 | 被抓取层强制引用 |

**列名对照（M-4）**：归一化层按 DictReader 列名取值——

| 归一化列 | fixtures.csv (D2) | 赛季 CSV (D1) |
|---|---|---|
| b365h/d/a | B365H/B365D/B365A | 同左 |
| ps_h/d/a | **PPH/PPD/PPA** | **PSH/PSD/PSA** |
| ou_over/under (2.5) | Avg>2.5/Avg<2.5 | Avg>2.5/Avg<2.5 |
| ah_line/ah_h/ah_a | AHh + B365AHH/B365AHA | AHh + B365AHH/B365AHA |

**日期归一化（MINOR-3）**：D1/D2 日期为 `DD/MM/YYYY`，入库一律转 **ISO `YYYY-MM-DD`**——现有 walk-forward 依赖字符串字典序排序（compute_elo.py:139 同款约定），转错则排序静默错误，归一化层必须有单测锁死。

**schema 校验（§7 风险细化）**：抓取后校验必需列存在性 + 数值域（odds > 1.0、0 ≤ goals ≤ 15、ah_line ∈ [-4, 4]），失败 = warning 沿用旧数据（容错铁律同现有约定）。

归一化 `results.csv`：`season,date,home,away,gh,ga,b365h,b365d,b365a,ps_h,ps_d,ps_a,ou_over,ou_under,ah_line,ah_h,ah_a`
归一化 `schedule.csv`：`date,time,home,away,status,gh,ga`（status: pre/post，未赛 gh/ga 空）
归一化 `fixtures.csv`：`date,time,home,away,b365h,b365d,b365a,ps_h,ps_d,ps_a,ou_over,ou_under,ah_line,ah_h,ah_a`

### 4.3 `leagues/elo.py` — 统一俱乐部 Elo 池

- 一个评分池覆盖 5 联赛 + UCL 全部俱乐部；UCL 交锋天然连接跨联赛评分（clubelo 同款设计，数据自建）。
- walk-forward 顺序更新；K 因子分档：UCL > 联赛；进球差乘数沿用 eloratings 惯例。
- 输入：5 联赛 results（D1，2000-01 起热身）+ UCL results（D3，2015-16 起）按日期合并排序；**任一场赛前评分只由该场之前历史决定**。
- **升班马冷启动（critic 缺口）**：新入池俱乐部初始 1500，前 30 场池内比赛 K 值 ×1.5 加速收敛。
- **UCL 主客场语义**：欧冠双回合一主一客按实际主客处理；决赛为中立场，home_adv = 0（schedule/results 标记 neutral）。
- **M-1 回退**：若 UCL 历史回填残缺（连续缺月 > 2），elo.py 自动降级为「仅 5 联赛池」，UCL 预测直接取双方联赛内 Elo，并在构建日志 warning。
- 输出：`data/leagues/elo_global.json` + `elo_history.csv`（每场赛后评分，供 XGB/回测）。
- 计算量估算（critic MINOR-4）：~4.7 万场顺序更新为秒级；不构成成本。

### 4.4 `leagues/dixon_coles.py` — 每赛事攻防参数

- 5 联赛：各自独立 MLE（attack/defense/home_adv/rho，τ 修正低比分），时间衰减权重（半衰期为 params 旋钮），窗口 = 近 5 完整赛季 + 当前赛季。
- **UCL 特例（M-5）**：UCL 单队样本太少，独立 MLE 统计上不成立——攻防参数**借用该队所属联赛的 DC 参数**，仅在 UCL 样本上拟合全局 β0（进球率校准）+ home_adv + rho。样本 < min_matches 的队伍沿用联赛参数不罚。
- **N-2 补充**：无五大联赛归属的 UCL 队（Ajax/Porto/Benfica/Celtic/PSV 等常客）无参数可借——attack=defense=0（中性）+ 输出标记 `low_confidence: true`；回测 UCL 切片按「双方均五大联赛 / 含非五大联赛」分组报告指标。
- 输出：`data/leagues/{CODE}/dc_params.json`（UCL 文件只含全局三参数 + 借用规则版本号）。
- 计算量：每赛事 MLE 数秒级。

### 4.5 `leagues/predict.py` — 单场预测

每场输出（对齐 WC 单场能力）：
1. Elo 基础三态（含平局模型）。
2. DC 比分矩阵（λ_home/λ_away + rho）→ 三态、最可能比分 top5、OU2.5 概率、BTTS 概率。
3. XGB 三态（4.6）。
4. 集成：统计(DC) 与 ML(XGB) 加权 + 分歧门控（模式复刻 `ensemble.py`）。
5. 市场对比：fixtures 赔率 devig → edge；edge > 阈值（params 旋钮）输出 Kelly 仓位（f/4）。
- 输出：`data/leagues/{CODE}/predictions.json`（updater 重建，atomic replace）。

### 4.6 `leagues/train_xgb.py` — 跨联赛 XGBoost

- 单一俱乐部模型，league one-hot 为特征；5 联赛近 10 季约 2 万场。
- 特征（全部赛前可得）：elo_diff、elo_home/away、近 5 场战绩差、近 5 场进/失球差、DC attack/defense 差、home、league one-hot。
- **walk-forward 切分（MINOR-6 明确）**：按赛季日期边界——train = 赛季 S 开始日期前的全部比赛，test = 赛季 S；5 联赛赛季同为 8 月-5 月按日历对齐；UCL 切片单独报告指标，不混入 train/test 主表。
- 输出：`data/leagues/xgb_club.json`（NIT-1 补路径）。
- 计算量：分钟级。

### 4.7 `leagues/season_sim.py` — 赛季推演（B-1 修复后可交付）

- 输入：当前真实积分（results 现算）+ **D3 全季赛程中 status=pre 的剩余场次** × 10,000 次蒙特卡洛（DC 比分矩阵抽样；联赛无点球）。
- 输出每队：夺冠 / 前四 / 降级概率 → `data/leagues/{CODE}/sim.json`。
- UCL 不做赛季推演（联赛阶段+淘汰赛混合赛制，超范围）。

### 4.8 Web 层

**后端** `webapp/leagues.py`（APIRouter）：

| Endpoint | 内容 | 门控（M-3 修复，与 WC 对齐） | 限流 |
|---|---|---|---|
| `GET /api/leagues` | 赛事列表 + 元信息 | 无 | 30/min |
| `GET /api/leagues/{code}/matches` | upcoming + 三态/比分预测 + 赔率 | 预测免费；**非 Pro 赔率字段 mask 为 null**（同 WC `*` 模式） | 30/min |
| `GET /api/leagues/{code}/standings` | 积分榜（**updater 预构建入缓存**，非请求时现算） | 无 | 30/min |
| `GET /api/leagues/{code}/sim` | 夺冠/前四/降级概率 | 无 | 30/min |
| `GET /api/leagues/{code}/value` | edge + Kelly | **非 Pro → 402**（复用 WC `/api/value-bets` 模式，杜绝用免费输入客户端重算 edge） | 30/min |

**M-3 原理**：WC 端对非 Pro 同时 mask 赔率输入 + 402 价值接口；联赛板块若给免费概率+免费赔率，edge 可在浏览器 10 行重算——故门控必须对齐：**免费=预测，Pro=赔率+价值**。

**N-1 补充**：`/matches` 响应字段白名单——仅三态/比分 top5/OU2.5/BTTS 与赛程信息；`edge`、`kelly` 字段**一律不出现**在该 payload（不得直接序列化 predictions.json），test_leagues_api.py 断言响应不含这两个 key。

**缓存** `webapp/leagues_cache.py`：独立实现（模式参考 cache.py，但 **global 声明写全**——cache.py:64 存在 `_market` 未入 global 列表的既有 bug，不得复刻；该 bug 另案处理，不在本 spec 范围）。落盘 `.leagues_cache.json`，TTL = updater 重建周期。

**前端**：
- 新页面 `webapp/static/leagues.html` + `leagues.js`，复用 style.css LED 风格。
- 联赛切换 tab（6）→ 赛程预测卡 / 积分榜 / 夺冠概率条形图；赔率列与价值入口对非 Pro mask/置灰（复用 isPaid + 登录后重渲模式）。
- `index.html` 导航加「联赛」入口。
- i18n：6 语言 × ~40 key；**翻译流程**：zh/en 人工撰写，es/pt/ar/fr 复用 `webapp/i18n_translator.py`（GLM）生成后人工抽查；`I18N_VER` + script 版本号必 bump。

### 4.9 运维

- `update_leagues.sh`（cron）：每日 fixtures + schedule 增量；周一/赛后日 results 增量 + Elo/DC/预测/推演重建；每月初抓新月赛程页。
- prod：沿用 deploy_webapp.sh；首次历史回填在 prod 本地执行一次（~5 联赛 × 25 季 + UCL × 10 季，网络 IO 为主，预计 < 30 min）。
- `.gitignore` **新增** `data/leagues/`、`.leagues_cache.json`。
- TheOddsAPI 月配额 < 20 req。

## 5. 测试策略

| 测试 | 内容 |
|---|---|
| `tests/test_leagues_normalize.py` | **列名映射（PPH vs PSH）、DD/MM/YYYY→ISO、schema 校验拒绝坏行**、clubs_aliases 完整性（UCL 队必映射） |
| `tests/test_leagues_elo.py` | Elo 数学、主客对称、walk-forward 无泄漏、升班马加速 K、中立场地 home_adv=0 |
| `tests/test_leagues_dc.py` | 合成数据 MLE 收敛；**UCL 借用参数路径**（联赛攻防 + UCL 全局参数） |
| `tests/test_leagues_predict.py` | 三态和=1、集成门控分支、devig/Kelly 数学 |
| `tests/test_leagues_api.py` | 5 endpoint 冒烟；**非 Pro：matches 赔率字段为 null、/value 返回 402**；Pro 全量；错误 code 404；限流生效 |
| `leagues/backtest.py` | 近 3 赛季 × 5 联赛；**同时报告 B365 收盘价 devig 基线**的 Brier/LogLoss/Accuracy，模型须接近或优于基线 → `docs/leagues_backtest.md` |
| 回归 | 既有 71 测试全绿；WC 页面手动冒烟 |

## 6. 分阶段实施

| 阶段 | 内容 | 验收 |
|---|---|---|
| P1 垂直切片 | 归一化层 + 别名表 + Elo + DC + predict + ESPN schedule 管道 + 英超页面 E2E | `/api/leagues/E0/matches` 返回预测；非 Pro 赔率 null |
| P2 联赛铺开 | SP1/I1/D1/F1 配置接入 + 积分榜 + season_sim + 回测报告（含基线） | 5 联赛页面齐；`docs/leagues_backtest.md` |
| P3 欧冠 | UCL 历史回填 + 借用参数 DC + UCL 预测 + D4 赔率 | UCL 页面可查预测；Elo 池跨联赛连接生效 |
| P4 ML + 收尾 | XGB 集成 + 402/mask 门控全量 + i18n 6 语言 + 全量测试 | Kelly Pro 解锁；无裸 i18n key；71+N 测试绿 |

每阶段独立可交付、可回滚（新模块，删除即回滚）。

## 7. 风险与缓解

| 风险 | 缓解 |
|---|---|
| football-data 改格式/断更 | 必需列存在性 + 数值域校验，失败沿用旧数据；D3 可兜底赛程/结果（无赔率） |
| fixtures.csv ~4 天窗口变化 | 赔率仅用于近场价值判断；推演赛程不依赖它（B-1 已修复走 D3） |
| 跨联赛 Elo 被 UCL 样本噪声污染 | UCL K 保守；回测单列 UCL 切片；历史回填残缺自动降级仅联赛池（§4.3） |
| UCL DC 参数样本不足 | 借用联赛攻防 + 仅拟合全局参数（§4.4）；切片指标差则 UCL 降级纯 Elo 引擎 |
| XGB 跨联赛泛化翻车（2022 教训） | 分歧门控；回测不达标则 XGB 权重置 0 纯统计上线 |
| 升班马冷启动拉低预测 | 初始 1500 + 前 30 场 K×1.5；回测报告单列升班马场次准确率 |
| 队名跨源不一致静默分叉 | clubs_aliases.json 强制校验 + 单测；未映射队抓取即报错（不静默） |
| 非五大联赛 UCL 队预测质量（N-2） | 中性攻防参数 + low_confidence 标记；回测分组报告，差则前端降权展示 |
| 赛季滚动写死 "2526" | current_season() 按日期推导 + env 覆盖（§4.1） |

## 8. 明确不做（YAGNI）

- 多档 OU/亚盘、波胆、半全场盘口。
- UCL 赛季推演、次级联赛、其他大洲联赛。
- 统一抽象重构现有 WC 引擎；修复 cache.py `_market` 既有 bug（另案）。
- 分钟级盘口监控与走势 sparkline。
- 数据库引入（沿用 JSON/CSV）。

---

## 附：v2 修订记录（对应 critic 审计 findings）

| Finding | 处理 |
|---|---|
| B-1 剩余赛程无来源 | §2 D3 + §4.2 fetch_schedule + §4.7，ESPN 按月迭代已实测 |
| M-1 UCL 历史可行性 | §2 实测 2018 决赛；§4.3 回填起点 2015-16 + 自动降级回退 |
| M-2 队名归一 | §4.2 clubs_aliases.json + 强制校验 + §5 单测 |
| M-3 门控可绕过 | §4.8 改为 WC 对齐：非 Pro 赔率 mask + /value 402 |
| M-4 列名假设错误 | §2/§4.2 列名对照表 + DictReader 映射 |
| M-5 UCL DC 不成立 | §4.4 借用联赛攻防 + 全局参数拟合 |
| M-6 零改动矛盾 | §3 精确白名单 + updater 独立线程定义 |
| MINOR 1-6 | .gitignore 新条目措辞；30/min 限流 + standings 预构建；ISO 日期锁死；计算量估算；leagues_cache 独立实现不复刻 bug；XGB 切分定义 |
| NIT 1-3 | xgb_club.json 路径；mask 措辞同步；成功标准注明 P2/P3 分期 |
| 缺口：升班马/中立场地/赛季滚动/i18n 流程/回测基线 | §4.3、§4.4、§4.1、§4.8、§5 分别补入 |
