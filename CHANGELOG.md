# Changelog

本项目遵循 [Keep a Changelog](https://keepachangelog.com/zh-CN/1.1.0/) 风格。

## [Unreleased]

### 工程化
- 新增 `LICENSE`(PROPRIETARY)、`.env.example`、`.dockerignore`
- 新增 `.github/workflows/ci.yml`(Python 3.11 / 3.12 matrix)
- 新增 `.github/ISSUE_TEMPLATE/*`、`.github/PULL_REQUEST_TEMPLATE.md`
- 新增 `CONTRIBUTING.md`、`CHANGELOG.md`
- 重写 `README.md` 为对外文档
- 移动 `数据获取指南.md` → `docs/`
- `.gitignore` 增补 `scan_arbitrage_*.json` 规则
- 清理 `scan_arbitrage_*.json` × 3(git rm --cached,保留本地)

## [0.3.0] — 2026-06-22

### 支付
- **Stripe 切换 LIVE 模式**(真钱收款),单价 ¥2 → ¥3 CNY → 统一 $0.42 USD
- 动态定价:`price_data` 按剩余场次 × 单价生成,移除月度订阅 dead code
- 新增 live webhook endpoint(`/api/stripe-webhook`)

### 鉴权
- **单 session 限制(方案 D)**:每账号同时只允许 1 个活跃 session,新登录自动踢掉旧设备
- session 由 HMAC 签名 cookie 承载,弃用旧 `x_user_email` query 参数(F-02 漏洞修复)
- 新增 `/api/email-login`:已 Pro 用户跨设备邮箱登录(不消耗邀请码)
- 前端 30s 轮询 `/api/whoami` 检测 session 被踢

### 前端
- Pro 登录后立即重渲 schedule,赔率不再等 30s poll
- 已结束赛事写入 schedule CSV,前端历史折叠区可见
- i18n 新增 5 个登录相关 key,版本 → v5

## [0.2.0] — 2026-06-15

### 预测引擎
- 市场赔率反演 λ(`compute_odds.py`),B/A/Elo 三档质量分级
- **价值下注面板**:系统 Elo vs 市场 devig 对比 + Kelly 仓位
- 整届蒙特卡洛推演 10,000 次,2026 新赛制(12 组 → 32 强)适配
- 反思(Elo)模块上线

### 前端
- 6 语言切换(中/英/西/法/日/韩)
- 比赛详情抽屉、价值下注、数据来源三页签上线
- LED 指挥中心风格 UI、状态条、倒计时刷新

### 数据
- 接入 ESPN API + Wikipedia 回退 + 多家赔率源
- 邀请码分享裂变(发 X 推文 → 验证 → 解锁)
- pre-commit 钩子(gitleaks + 跨项目密钥路径扫描)

## [0.1.0] — 2026-06-10

### 基线
- Elo + Dixon-Coles + 泊松三态预测
- walk-forward 回测(`backtest.py`),Brier / LogLoss / Accuracy 三指标
- XGBoost 三态模型(`train_xgb.py`),9 维特征
- 统计 + ML 集成(`ensemble.py`),分歧门控
- FastAPI 骨架 + 赛程时刻表 + 夺冠概率 + 小组积分

[Unreleased]: https://github.com/zangxin75/足球预测/compare/v0.3.0...HEAD
[0.3.0]: https://github.com/zangxin75/足球预测/releases/tag/v0.3.0
[0.2.0]: https://github.com/zangxin75/足球预测/releases/tag/v0.2.0
[0.1.0]: https://github.com/zangxin75/足球预测/releases/tag/v0.1.0
