---
created: 2026-06-19
last_updated: 2026-06-19
version: V1.0
status: 已发布
update_note: 2026-06-19 调研多源 odds API,结论 The Odds API 为最优方案(Pinnacle + Betfair Exchange 覆盖)
owner: 预测系统
---

# 多源 Odds API 调研（The Odds API）

## 一句话结论

**The Odds API (the-odds-api.com) 是当前最优解**——单 endpoint 覆盖 Pinnacle、Betfair Exchange、Bet365 等 10+ 家,WC2026 有专门 sport_key,免费层 500 req/月可覆盖未赛,付费 $79/月可解锁历史赔率。

**Pinnacle 旧公开 API 已废弃,Orbit/Betfair 无独立 API,均只能通过 The Odds API 间接获取。**

---

## 1. 背景:为什么需要多源?

当前项目抓赔率脚本:
- `fetch_wc2026_odds.py`:BetExplorer 一家,1X2 40 场(未赛),OU 5 场
- `fetch_wc2026_ou.py`:BetExplorer 详情页 OU,慢(playwright)
- `fetch_flashscore.py`:deprecated(SPA + 私有 WebSocket 协议)

**问题**:
1. ⚠️ BetExplorer 单源 — 无法做严格搬砖(2+ 家)
2. ⚠️ 28 场已结束比赛 1X2 全部丢失(博彩撤盘)
3. ⚠️ OU 覆盖率极低(5/68 场)
4. ⚠️ 无法做"多源报价交叉验证"

**多源能解锁**:
- ✅ 严格搬砖(类型 A,2+ 家 1/oh + 1/od + 1/oa < 1)
- ✅ 历史赔率回填(若付费)
- ✅ "哪本盘更准"的多源对比研究

---

## 2. 候选方案对比

### 2.1 Pinnacle 公开 API

| 项 | 状态 |
|---|---|
| 旧 endpoint | `https://api.pinnacle.com/v1/...` 已废弃(2023 起) |
| 现存 | ❌ 无公开 API,只向授权 affiliate 开放 |
| 替代 | The Odds API (eu 区域含 `pinnacle`) |

**结论**:独立接入不可行。

### 2.2 Betfair Exchange / Orbit

| 项 | 状态 |
|---|---|
| Betfair API | 需注册 Betfair 账户 + 申请 API key + 维持账户余额(讽刺:必须有余额才能保持 API 活跃) |
| Orbit Exchange | 2023 年品牌重命名,API 协议不变 |
| 替代 | The Odds API (uk 区域含 `betfair_ex_uk`) |

**结论**:独立接入成本高(账户维护 + 余额要求),通过聚合 API 更划算。

### 2.3 BetBurger / OddsJam

| 项 | 状态 |
|---|---|
| 类型 | 套利扫描 SaaS(非数据源) |
| 定价 | $50-200/月,按扫的场次计费 |
| 优势 | 已做好 1/oh + 1/od + 1/oa < 1 检测 + 报警 |
| 劣势 | 黑盒,无法访问原始 odds |

**结论**:不直接用,但值得作为参考(他们的扫描器很成熟)。

### 2.4 商业数据 API (Bet Practice, Sportradar, Opta)

| 项 | 状态 |
|---|---|
| Sportradar | 数千美元/月起,WC2026 数据需单独报价 |
| Opta | 数千英镑/月 |
| StatsBomb | 主攻事件数据(非赔率) |

**结论**:对个人项目太贵。

### 2.5 ⭐ The Odds API(推荐)

| 项 | 详情 |
|---|---|
| **官网** | https://the-odds-api.com |
| **API 入口** | `https://api.the-odds-api.com/v4/` |
| **WC2026 支持** | ✅ `soccer_fifa_world_cup` + winner / qualifiers / women's |
| **博彩覆盖** | Pinnacle (eu), Betfair Exchange (uk), Bet365, Betway, William Hill, Ladbrokes, BetMGM 等 10+ |
| **市场** | `h2h` (1X2) + `spreads` (亚盘) + `totals` (OU) |
| **赔率格式** | decimal / american |
| **免费层** | 500 req/月,仅 current/upcoming |
| **付费层** | $79/月起,解锁历史赔率 + 5 分钟级更新 + 10k+ req |
| **响应头** | `x-requests-remaining` + `x-requests-used` 实时显示配额 |
| **Python SDK** | 无官方 SDK,但 `requests` 直接调;非官方 `oddsapi` (PyPI) 可选 |
| **GitHub samples** | https://github.com/the-odds-api/samples-python |

---

## 3. The Odds API 实战示例

### 3.1 拿 sport 列表(免费)

```python
import requests
r = requests.get("https://api.the-odds-api.com/v4/sports",
                 params={"apiKey": "YOUR_KEY"})
# 返回: [{"key": "soccer_fifa_world_cup", ...}, ...]
```

### 3.2 拿 WC2026 1X2 报价(免费,消耗 1 req)

```python
r = requests.get(
    "https://api.the-odds-api.com/v4/sports/soccer_fifa_world_cup/odds",
    params={
        "apiKey": "YOUR_KEY",
        "regions": "eu,uk",          # 多 region → 多博彩
        "markets": "h2h",             # 1X2
        "oddsFormat": "decimal",      # 1.50 这种
        "dateFormat": "iso",
    },
)
# 返回: [{
#   "id": "abc123",
#   "sport_key": "soccer_fifa_world_cup",
#   "home_team": "Mexico",
#   "away_team": "South Korea",
#   "commence_time": "2026-06-19T...",
#   "bookmakers": [
#     {"key": "pinnacle", "title": "Pinnacle",
#      "markets": [{"key": "h2h",
#                   "outcomes": [
#                     {"name": "Mexico", "price": 2.06},
#                     {"name": "Draw",   "price": 3.21},
#                     {"name": "South Korea", "price": 4.08}
#                   ]}]},
#     {"key": "betfair_ex_uk", ...},
#     ...
#   ]
# }, ...]
```

### 3.3 拿历史赔率(付费,消耗 10x quota)

```python
# 1) 拿 events 列表(含 event id）
events = requests.get(
    "https://api.the-odds-api.com/v4/historical/sports/soccer_fifa_world_cup/events",
    params={"apiKey": KEY, "date": "2026-06-11T00:00:00Z"},
).json()

# 2) 按 event_id 拉指定比赛的当时所有博彩报价
odds = requests.get(
    f"https://api.the-odds-api.com/v4/historical/sports/soccer_fifa_world_cup/events/{event_id}/odds",
    params={"apiKey": KEY, "date": "2026-06-11T20:00:00Z",
            "regions": "eu,uk", "markets": "h2h",
            "oddsFormat": "decimal"},
).json()
```

### 3.4 严格搬砖扫描

```python
# 一场比赛拿到多本盘后,遍历 1X2 组合,找 1/oh + 1/od + 1/oa < 1
for book in match["bookmakers"]:
    h2h = next(m for m in book["markets"] if m["key"] == "h2h")
    odds = {o["name"]: o["price"] for o in h2h["outcomes"]}
    s = 1/odds["home"] + 1/odds["Draw"] + 1/odds["away"]
    if s < 1.0:
        print(f"ARBITRAGE: {match['home_team']} vs {match['away_team']} "
              f"on {book['title']}: sum = {s:.4f}")
```

---

## 4. Quota 成本估算

| 场景 | Quota | 备注 |
|---|---|---|
| WC2026 全 40 场 × h2h × eu | 40 req | 免费层足够 |
| WC2026 全 40 场 × h2h + totals × eu + uk | 40 × 2 × 2 = **160 req** | 免费层够 |
| WC2026 全 40 场 × h2h + totals + spreads × 4 region | 40 × 3 × 4 = **480 req** | 接近免费层上限 |
| 历史回填 28 场 × h2h × eu | 28 × 10 = **280 req** | **付费层专属** |
| 历史回填 28 场 × h2h + totals × 2 region | 28 × 2 × 2 × 10 = **1120 req** | 付费层也吃紧 |

**推荐免费层用法**:每日 1 次拉所有 WC2026 报价,只取 h2h + totals,eu + uk 共 2 region → 每月 ~80 req,留 420 req 余量给未来扩展。

---

## 5. 接入项目

### 5.1 推荐脚本结构

```
fetch_odds_api.py           # 主脚本,扫 WC2026 → 写 odds_wc_api.csv
├── fetch_odds_for_sport()  # GET /v4/sports/{sport}/odds
├── parse_match()           # 提取 1X2 + OU(若 markets 含 totals)
├── merge_to_csv()          # 复用 fetch_wc2026_odds.py 的保留逻辑
└── main()
```

### 5.2 与 BetExplorer 协同

- **主源**:The Odds API(Pinnacle + Betfair 报价,小数,decimal)
- **辅源**:BetExplorer(继续抓未覆盖的盘口,作为交叉验证)
- **去重**:按 (year, home, away) key,优先保留 The Odds API(Pinnacle 抽水低)
- **格式**:统一为项目标准 `odds_h/odds_d/odds_a` decimal

### 5.3 cron 配置

```bash
# update_odds.sh(伪代码)
./.venv/bin/python fetch_odds_api.py        # 主:抓 The Odds API
./.venv/bin/python fetch_wc2026_odds.py     # 辅:抓 BetExplorer
./.venv/bin/python fetch_wc2026_ou.py       # OU 详情
```

每小时一次,The Odds API 配额消耗 ~80/月,完全够用。

---

## 6. 决策矩阵

| 目标 | 方案 | 月成本 | 投入 | 推荐度 |
|---|---|---|---|---|
| **拿多源未赛报价** | The Odds API 免费层 | $0 | 2h 写脚本 | ⭐⭐⭐⭐⭐ |
| **回填 28 场历史 1X2** | The Odds API 付费层 | $79 | 0.5h 拉历史 endpoint | ⭐⭐ (看需求) |
| **做严格搬砖扫描** | 免费层已够 | $0 | 1h 写扫描器 | ⭐⭐⭐⭐ |
| **B2B 商业化** | Sportradar / Opta | $1000+ | 需合同 | ❌ 个人项目不适用 |

---

## 7. 建议下一步

1. **写 `fetch_odds_api.py`** (~2h):用免费 key 拉 Pinnacle + Betfair + Bet365 1X2,复用 `merge_to_csv()` 保留逻辑,先加到 update_odds.sh 试跑
2. **写 `scan_arbitrage.py`** (~1h):每次抓完跑一次,找 1/oh + 1/od + 1/oa < 1 的边
3. **评估 1-2 周后**:若免费层够用,继续白嫖;若需要历史赔率(回填或赛后分析),再考虑付费
4. **V1.3 文档更新**:把"抓赔率脚本"段补充 The Odds API 来源

---

## 8. 参考链接

- 主页:https://the-odds-api.com
- API V4 文档:https://the-odds-api.com/liveapi/guides/v4/
- Sport 列表:https://the-odds-api.com/sports-odds-data/sports-apis.html
- Bookmaker 区域:https://the-odds-api.com/sports-odds-data/bookmaker-apis.html
- 市场说明:https://the-odds-api.com/sports-odds-data/betting-markets.html
- 历史赔率:https://the-odds-api.com/historical-odds-data/
- Python 示例:https://github.com/the-odds-api/samples-python
- 错误码:https://the-odds-api.com/liveapi/guides/v4/api-error-codes.html

---

## 相关文件

- 套利讲解(姊妹文档):`02-市场调研/套利讲解_足球预测_20260619.md` (V1.2)
- 现有抓取脚本:`fetch_wc2026_odds.py` (BetExplorer), `fetch_wc2026_ou.py` (OU), `fetch_flashscore.py` (deprecated)
- merge 保留逻辑:`fetch_wc2026_odds.py:merge_to_csv()` 已被 `fetch_flashscore.py:merge_1x2/merge_ou` 复用
- 模型入口:`webapp/predictor.py:predict_one`
- 对比脚本:`scripts/compare_market_vs_elo.py`
