# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## 项目概览

世界杯足球预测系统 + FastAPI Web 应用。两层架构:

1. **预测算法层**(根目录 `.py`)— Elo + Dixon-Coles + XGBoost + 蒙特卡洛,输出胜/平/负三态概率 + 比分分布。`README.md` 详述算法原理与调参旋钮。
2. **Web 层**(`webapp/`)— FastAPI 服务 + 前端 SPA,对外提供预测、夺冠榜、价值下注、AI 反思等。生产部署在 `http://aijialidu.ticp.vip/wc/`。

## 常用命令

### Web 服务
```bash
./webapp.sh start|stop|restart|status   # 本地启停(绑 127.0.0.1:8000)
bash deploy_webapp.sh                   # prod 一键部署(systemd + nginx)
```

**注意**:本地 WSL2 当前实际跑的 uvicorn 绑 `--host 0.0.0.0`(同时支持 localhost + docker bridge),与 `webapp.sh` 默认的 `127.0.0.1` 不一致 — 重启时按需调整。PID 文件在 `/tmp/wc_webapp.pid`,日志在 `/tmp/wc_webapp.log`。

### 测试与开发
```bash
pytest tests/                           # 全量(71 passed)
pytest tests/test_payments_is_paid.py -v  # 单文件
pytest tests/test_app_security.py -k login  # 单测试
pre-commit run --all-files             # 手动跑 hook(gitleaks 需 manual stage)
```

### 数据更新
```bash
./update_odds.sh                        # BetExplorer 1X2 + OU,有变自动 commit+push
./update_odds_api.sh                    # TheOddsAPI(配额限制,独立 cron)
python fetch_wc2026.py                  # ESPN 赛程+结果抓取
python update_live.py                   # 已打结果回写 results.csv
```

`update_odds.sh` 设计为 cron 任务(每小时),merge 语义是**只填空**(不覆盖 TheOddsAPI 优质数据)。

## 架构核心

### 数据流(后台 updater,每 15 分钟)
`webapp/updater.py` 在独立线程跑:`fetch_wc2026.py(子进程)` → `update_live.py` → 重建 Elo → 重算赛程 → atomic_replace 刷新 `webapp/cache.py` 模块级缓存 → 落盘 `.wc2026_cache.json`。

容错铁律:fetch 失败 = warning(沿用旧数据);update/build 失败 = error(终止本次,保留上一成功快照)。`/api/refresh` 可手动唤醒。

### Session 鉴权(`webapp/auth.py`)
- 用 `starlette SessionMiddleware`(HMAC 签名 cookie,30 天)
- **单 session 限制(方案 D)**:每账号同时只 1 个活跃登录。session 含 `jti`,登录时新 jti 覆盖 `active_sessions.json[email]` → 旧 session 在下次 `get_current_user` 时返回 None(被踢)
- 登录入口 3 个:`/api/redeem-invite`(邀请码,消耗码)、`/api/claim-session`(Stripe `cs_xxx`)、`/api/email-login`(已 Pro 用户跨设备恢复,**不消耗码**)
- `SESSION_SECRET` 优先环境变量,缺则从 `STRIPE_SECRET_KEY` 派生(启动警告)

### Stripe 付费(`webapp/payments.py`)
- **一次性 payment 模式**(无订阅),`price_data` 内嵌动态定价:`amount = remaining_count × unit_price`
- **全球统一 USD 收款**,$0.42/场(中文区用户信用卡网络自动按汇率换算)。CNY 收款被 Stripe live 模式最小金额限制拒绝
- Webhook endpoint:`/api/stripe-webhook`,事件 `checkout.session.completed` + `customer.subscription.deleted`

### 数据存储(纯 JSON,无数据库)
所有运行时数据存 JSON 文件,**均在 `.gitignore` 里**(含真实 PII):
- `webapp/paid_users.json` — Pro 用户(email → {active, plan, via, created})
- `webapp/invite_codes.json` — 邀请码池(INVITE-* / SHARE-* → {used, used_by, expires_at})
- `webapp/active_sessions.json` — 单 session jti 记录

`payments.is_paid(email)` 只看 `active: true`,不区分 plan 来源(邀请码 / Stripe 付费跨设备登录路径完全统一)。

### 前端
- `webapp/static/index.html` + `app.js` + `style.css` + `i18n.js`
- 6 语言:`webapp/static/i18n/{en,zh-CN,es,pt,ar,fr}.json` + `teams.json`(48 队译名)
- **静态资源版本号**:`index.html` 里 `app.js?v=N` / `i18n.js?v=N`,改 JS/HTML 必 bump;`i18n.js` 内 `I18N_VER` 改 JSON 必 bump
- Poll 30s,主循环末尾查 `/api/whoami` 同步被踢状态(`isPaid` 变化触发重渲)
- Pro 状态判断:`isPaid` 控制 schedule 赔率是否 mask 成 `*`(未付费);登录成功必须调 `renderSchedule()` 否则首页赔率不变

### prod 部署
- 目标:`/opt/wc_predict`(systemd 服务 `wc_predict` + nginx 反代)
- 流程:`git pull && sudo systemctl restart wc_predict`
- **JSON 数据文件不会被 git pull 同步**(在 .gitignore)— 必须手动 scp 或在 prod 直接改

## 关键约束

### pre-commit hooks(强制)
1. **禁止跨项目密钥回退路径** — 不允许代码引用 `/home/zx/project/词源效能` 或 `token-efficiency` 的 .env(F-04 历史 incident)
2. **禁止 .env 提交**
3. gitleaks(需 manual stage,WSL 无外网时跳过)

### .env 变量(本地 + prod 同名)
- `STRIPE_SECRET_KEY` / `STRIPE_PUBLISHABLE_KEY` / `STRIPE_WEBHOOK_SECRET`(live 模式)
- `GLM_API_KEY`(智谱,`websearch_client.py` / `i18n_translator.py`)
- `LITELLM_MASTER_KEY`(`lessons_fetcher.py` / `generate_lessons.py`)

### 算法层重要约定
- **walk-forward 严格**:回测 / XGBoost 训练不能看未来数据,见 `backtest.py` + `train_xgb.py`
- **Dixon-Coles τ 修正**:低比分(0-0/1-1)联合概率上调,补偿泊松独立假设低估平局
- ** monte_carlo 复冠榜**:10000 次模拟,赛制 12 组→32 强(2026 新赛制),Elo-based 点球给强队优势

## 重要参考文档

- `README.md` — 算法原理(Elo→λ→泊松→Dixon-Coles)、调参旋钮、准确率预期
- `docs/backtest_findings.md` — 回测结论(2018 XGBoost 增益,2022 翻车)
- `docs/share-referral-program.md` — 分享邀请系统设计
- `04-技术方案/安全审计_足球预测_20260620.md` — 安全审计
- `数据获取指南.md` — 数据源 + 各模块需求表
