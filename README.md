# WORLD CUP 2026 · 预测指挥中心

> Elo + Dixon-Coles + XGBoost + 蒙特卡洛 + 市场赔率反演,带 FastAPI 前后端、Stripe LIVE 支付、单 session 鉴权的完整 WC2026 单场预测系统。

![status](https://img.shields.io/badge/status-LIVE-brightgreen)
![license](https://img.shields.io/badge/license-PROPRIETARY-red)
![python](https://img.shields.io/badge/python-3.11%2B-blue)

线上访问(赛事期间):`https://aijialidu.ticp.vip/wc/`

---

## 目录

- [功能一览](#功能一览)
- [Quick Start](#quick-start)
- [架构图](#架构图)
- [关键文件导航](#关键文件导航)
- [预测引擎原理](#预测引擎原理)
- [Stripe 支付](#stripe-支付)
- [Session 鉴权](#session-鉴权)
- [数据流](#数据流)
- [测试](#测试)
- [部署](#部署)
- [License](#license)

---

## 功能一览

**预测引擎**(根目录 `*.py`)
- Elo 顺序更新(`compute_elo.py`,eloratings.net K 因子分档 + cutoff walk-forward)
- Dixon-Coles 个体攻防 MLE(`compute_ad.py`)
- XGBoost 三态模型(`train_xgb.py`,9 维特征,严格 walk-forward)
- 统计 + ML 集成(`ensemble.py`,分歧门控)
- 整届蒙特卡洛 10,000 次推演(`tournament_sim.py`,2026 新赛制 12 组 → 32 强)
- 市场赔率反演 λ(`compute_odds.py`,B/A/Elo 三档质量分级)

**Web 应用**(`webapp/`)
- 赛程时刻表(按阶段过滤:小组 / R32 / R16 / QF / SF / Final)
- 夺冠概率(蒙特卡洛 10,000 次)
- 小组积分(已打真实 + 未打预测期望)
- 历史回测(赛前 Elo vs 真实结果)
- 数据来源(单场预测的驱动源 + 反演 λ 对比)
- 价值下注(系统 Elo vs 市场 devig + Kelly 仓位,纯对比不下注)
- 比赛详情抽屉(每场完整数据)
- 6 语言切换(中 / 英 / 西 / 法 / 日 / 韩)

**支付 / 鉴权**
- Stripe LIVE 模式真钱收款,按场次动态定价
- HMAC 签名 cookie session,单设备限制(每账号同时只 1 个活跃 session)
- 已 Pro 用户跨设备邮箱登录(`/api/email-login`)
- 邀请码分享裂变(发 X 推文 → 验证 → 解锁)

---

## Quick Start

```bash
# 1. 克隆
git clone <repo-url>
cd 足球预测

# 2. 虚拟环境
python3 -m venv .venv
source .venv/bin/activate              # Windows: .venv\Scripts\activate

# 3. 装依赖
pip install -r requirements.txt

# 4. 配置密钥
cp .env.example .env
# 编辑 .env 填入真实的 STRIPE / GLM / SESSION_SECRET
chmod 600 .env

# 5. 启动
.venv/bin/python -m uvicorn webapp.app:app --host 0.0.0.0 --port 8000 --reload
```

浏览器打开 http://localhost:8000/

首次启动会:
- 自动从项目根加载 `.env`
- 重建 `webapp/data/cache.json`(Elo + 赛程 + 小组)
- 首次赛前 Elo 重建约 10 秒(回测页签首次切换时触发)

---

## 架构图

```
                  ┌─────────────────────────────────────┐
                  │      外部数据源(ESPN/Wikipedia/      │
                  │      flashscore/odds API)            │
                  └──────────────┬──────────────────────┘
                                 │ fetch_wc2026.py / fetch_*.py
                                 ▼
            ┌──────────────────────────────────────────────┐
            │   wc2026_schedule.csv  wc2026_live.csv        │
            │   odds_wc.csv         ou_wc.csv              │
            │   (.gitignore 排除,缓存可重建)               │
            └──────────────┬───────────────────────────────┘
                           │
        ┌──────────────────┼────────────────────┐
        ▼                  ▼                    ▼
  compute_elo.py     compute_ad.py        compute_odds.py
  (Elo 顺序更新)     (攻防 MLE)            (市场反演 λ)
        │                  │                    │
        ▼                  ▼                    ▼
  elo_ratings.json   params.json          反演 λ 直供
        │                  │                    │
        └──────────┬───────┴────────────────────┘
                   ▼
            wc_predict.py   ← 核心:Elo → λ → 泊松 → Dixon-Coles → 三态
                   │
       ┌───────────┼──────────────┐
       ▼           ▼              ▼
  tournament_sim  ensemble    train_xgb
  (蒙特卡洛)      (+ XGBoost)  (训练模型)
       │           │              │
       └───────────┴──────────────┘
                   │
                   ▼
        ┌──────────────────────────────────┐
        │        webapp/app.py (FastAPI)    │
        │  /api/schedule  /api/champions    │
        │  /api/groups    /api/backtest     │
        │  /api/market    /api/value        │
        │  /api/whoami    /api/email-login  │
        │  /api/create-checkout-session     │
        │  /api/claim-session               │
        │  /api/redeem-invite               │
        └──────────────┬───────────────────┘
                       │ JSON
                       ▼
        ┌──────────────────────────────────┐
        │   webapp/static/ (前端 SPA)      │
        │   index.html + app.js + i18n.js  │
        │   LED 指挥中心风格                │
        └──────────────────────────────────┘
```

---

## 关键文件导航

### 引擎(根目录 `*.py`)

| 文件 | 作用 |
|---|---|
| `wc_predict.py` | **核心** — Elo → λ → 泊松 → Dixon-Coles 三态预测,覆盖 48 队 |
| `compute_elo.py` | Elo 顺序更新(eloratings K 因子分档 + cutoff walk-forward) |
| `compute_ad.py` | Dixon-Coles 个体攻防 MLE(scipy.optimize) |
| `compute_odds.py` | 市场赔率反演 λ(B/A/Elo 三档质量分级) |
| `backtest.py` | walk-forward 回测(Brier / LogLoss / Accuracy) |
| `tournament_sim.py` | 整届蒙特卡洛 10,000 次(2026 新赛制适配) |
| `train_xgb.py` | XGBoost 三态模型(9 维特征) |
| `ensemble.py` | 统计 + ML 加权集成(分歧门控) |
| `draw_classifier.py` | 平局分类器 |
| `verify_2026.py` | 2026 赛程数据校验 |

### 数据抓取(根目录 `fetch_*.py`)

| 文件 | 作用 |
|---|---|
| `fetch_wc2026.py` | ESPN API 抓 2026 赛程 + 结果(缓存 + 去重 + Wikipedia 回退) |
| `fetch_wc2026_odds.py` | 抓 1X2 赔率 |
| `fetch_wc2026_ou.py` | 抓大小球赔率 |
| `fetch_flashscore.py` | flashscore 备选源 |
| `fetch_odds_api.py` | odds-api 备选源 |
| `fetch_results.py` | 历史比分训练集 |
| `update_live.py` | 已打结果回写 results.csv(幂等) |
| `dedupe_2026.py` | 去重工具 |

### 扫描 / 监控(根目录 `scan_*.py`)

| 文件 | 作用 |
|---|---|
| `scan_arbitrage.py` | 套利扫描 |
| `scan_features.py` | 特征分布扫描 |
| `scan_health.py` | 数据健康度检查 |
| `scan_boost.py` / `scan_cond_boost.py` | boosting 参数扫描 |
| `scan_rho.py` / `scan_wbase.py` | Dixon-Coles ρ / w_base 调参 |

### 启动入口(根目录)

| 文件 | 作用 |
|---|---|
| `run_live.py` | 生产启动入口(uvicorn + 后台抓取调度) |

### Web 应用(`webapp/`)

| 文件 | 作用 |
|---|---|
| `app.py` | FastAPI 应用,所有 `/api/*` 路由 |
| `auth.py` | session cookie 鉴权 + 单 session 限制 |
| `payments.py` | Stripe LIVE 支付 + 动态定价 |
| `i18n_translator.py` | GLM-4 翻译后端 |
| `cache.py` | Elo + 赛程 + 小组缓存 |
| `paid_users.json` | 付费用户记录(**gitignored**,含 PII) |
| `invite_codes.json` | 邀请码池(**gitignored**) |
| `active_sessions.json` | 活跃 session(**gitignored**) |
| `static/index.html` | 入口 HTML |
| `static/app.js` | 前端主逻辑 |
| `static/i18n.js` + `i18n/*.json` | 6 语言 |

### 数据 / 模型(git 跟踪的合法数据)

| 文件 | 作用 |
|---|---|
| `results.csv` (3.7M) | 1872-至今国家队历史比分训练集 |
| `elo_ratings.json` | 当前 48 队 Elo 评分 |
| `params.json` | Dixon-Coles 攻防参数 |
| `xgb_wc.json` (1.8M) | 训练好的 XGBoost 模型 |
| `odds_wc.csv` | WC2026 1X2 赔率快照 |
| `ou_wc.csv` | WC2026 大小球赔率快照 |
| `teams/i18n/*.json` | 48 队 × 6 语言翻译 |
| `teams/*.yaml` | 各队配置(阵容 / 战术等) |

### 文档(`docs/`)

| 文件 | 作用 |
|---|---|
| `数据获取指南.md` | Elo / 历史比分 / 赛程三类数据来源说明 |
| `backtest_findings.md` | walk-forward 回测完整结论 |
| `plan-b-value-betting-conclusion.md` | 价值下注方案设计 |
| `reflection-vs-elo-log.md` | 反思 Elo vs 原 Elo 命中率追踪 |
| `score-pattern-recap.md` / `score-pattern-vs-deepseek.md` | 比分模式分析 |
| `share-referral-program.md` | 邀请码分享裂变设计 |

---

## 预测引擎原理

```
两队 Elo 差
    │  (① sigmoid 分配总进球池)
    ▼
各队预期进球 λ_home, λ_away
    │  (② 泊松分布枚举所有比分)
    ▼
每个比分的联合概率 = P(主队进 i 球) × P(客队进 j 球) × τ
    │  (③ Dixon-Coles 修正低比分偏差)
    ▼
归一化 → 求和 → 胜/平/负三态概率 + 最可能比分
```

**为什么用泊松?** 足球进球是低频独立事件,泊松是建模"单位时间内随机事件发生次数"的经典工具,足球比分预测的事实标准。

**Dixon-Coles 的 τ 修正什么?** 纯泊松假设两队进球独立,会低估 0-0、1-1 这类平局(实际足球里弱队摆大巴、强队相互制约,平局比独立假设多)。τ 把低比分的联合概率上调。

**调参旋钮**(`wc_predict.py:Config`):

| 参数 | 默认 | 调节效果 |
|---|---|---|
| `AVG_TOTAL_GOALS` | 2.6 | 调大 → 更开放(2-1/3-1);调小 → 更保守(1-0/0-0) |
| `ELO_SCALE` | 300 | 调小 → 强弱更悬殊;调大 → 更平均 |
| `RHO` | -0.08 | Dixon-Coles 修正强度,越负平局越多 |
| `HOME_ADV_ELO` | 0 | 中立场默认 0;东道主 +65 左右 |

**准确率预期**(诚实):小组赛三态约 **55%-60%**,淘汰赛因冷门下降。精确比分最高概率通常 12%-15%。模型会稳定输给博彩公司赔率隐含概率(市场是真金白银校准过的)。

---

## Stripe 支付

- **LIVE 模式**:`STRIPE_SECRET_KEY=sk_live_*`,真钱收款,自测最小金额 $0.40 USD
- **按场次动态定价**:`price_data` 按剩余场次 × 单价生成(默认 $0.42 USD/场),移除了月度订阅
- **流程**:
  1. 用户点 "Upgrade" → `POST /api/create-checkout-session`(传剩余场次数)
  2. Stripe 跳转支付 → 回跳 `/?checkout=success&session_id=cs_xxx`
  3. 前端 `POST /api/claim-session?session_id=cs_xxx` → 后端用 secret 查 Stripe 拿 email
  4. `set_session_user()` 写入 cookie + jti
- **Webhook**:`/api/stripe-webhook`,用 `STRIPE_WEBHOOK_SECRET` 验签(主流程不依赖 webhook,只影响事件同步)

详见 `webapp/payments.py`。

---

## Session 鉴权

**单 session 限制(方案 D)**:每账号同时只允许 1 个活跃 session。

实现:
- 登录时生成 `jti = secrets.token_hex(16)`,存入 session + `active_sessions.json[email]`
- `get_current_user()` 比对 `session.jti` vs `active_sessions[email]`,不匹配视为被踢(返回 None)
- 新登录覆盖旧 jti → 旧 session 30s 内被前端 `/api/whoami` 轮询检测到 → 自动登出

**安全特性**:
- cookie 由 starlette `SessionMiddleware` 签名(itsdangerous),防篡改
- `SameSite=Lax`,防 CSRF
- 30 天过期
- `SESSION_SECRET` 优先用环境变量,缺则从 `STRIPE_SECRET_KEY` 派生(启动警告)

**已 Pro 用户跨设备登录**:`POST /api/email-login?email=...` — 任何已付费邮箱可在新设备登录(不消耗邀请码)。

详见 `webapp/auth.py`。

---

## 数据流

```
[赛前]                    [赛中]                  [赛后]
ESPN API ─────────────► 更新 schedule/live ──► update_live.py 回写 results.csv
   │                              │                       │
   ▼                              ▼                       ▼
fetch_wc2026.py            前端 30s poll           下一轮 compute_elo.py
   │                              │                       │
   ▼                              ▼                       ▼
compute_elo.py ──────► cache.json ──────► 前端渲染 ◄───── params.json 更新
compute_odds.py               │
   │                          │
   ▼                          ▼
反演 λ ──────────────► wc_predict.py 三态 + 比分 + 蒙特卡洛
```

---

## 测试

```bash
.venv/bin/python -m pytest tests/ -q
```

覆盖:鉴权安全、回测、攻防、Elo、赔率反演、集成、抓取、支付、预测器、蒙特卡洛、XGBoost、live 更新。当前 71 passed。

单文件:
```bash
.venv/bin/python -m pytest tests/test_app_security.py -v
```

CI 自动跑(GitHub Actions,Python 3.11 / 3.12 matrix),失败挡 PR。详见 `.github/workflows/ci.yml`。

---

## 部署

### 本机(开发 / 单机生产)

```bash
nohup .venv/bin/python -m uvicorn webapp.app:app \
    --host 0.0.0.0 --port 8000 \
    > server.log 2>&1 &
```

`0.0.0.0` 同时绑定 localhost 和 docker bridge(172.17.0.1),WSL2 用户在 Windows 浏览器可直接访问 `http://localhost:8000/`。

### 反向代理(公网)

通过 [aijialidu.ticp.vip](https://aijialidu.ticp.vip/wc/) 内网穿透暴露,无需自建 Nginx。

### 生产清单

- [ ] `.env` 权限 600,真实 Stripe LIVE key
- [ ] `SESSION_SECRET` 显式设置(≥ 32 字节随机串)
- [ ] `webapp/paid_users.json` / `invite_codes.json` / `active_sessions.json` 已异地备份
- [ ] `*.log` 加入 logrotate
- [ ] cron 定时跑 `fetch_wc2026.py`(5-15 分钟一次)

---

## License

**PROPRIETARY** — All rights reserved. 详见 [LICENSE](LICENSE)。

含 Stripe LIVE 真实交易逻辑、付费用户 PII 处理、专有预测模型与训练参数,不开源。
