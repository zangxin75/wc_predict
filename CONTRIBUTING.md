# 贡献指南

感谢为本项目贡献代码 / 数据 / 反馈。本仓库为单人维护的私有项目(PROPRIETARY License),外部协作以 Issue 反馈为主;若你拿到了开发权限,请遵循以下规约。

## 开发环境

```bash
git clone <repo-url>
cd 足球预测
python3 -m venv .venv
source .venv/bin/activate              # Windows: .venv\Scripts\activate
pip install -r requirements.txt
cp .env.example .env                   # 填入真实密钥(绝对不要 commit)
```

启动开发服务器:

```bash
.venv/bin/python -m uvicorn webapp.app:app --host 0.0.0.0 --port 8000 --reload
```

浏览器打开 http://localhost:8000/

## 提交前自检

```bash
# 1. 测试全绿(必需)
.venv/bin/python -m pytest tests/ -q

# 2. 模块导入冒烟(必需)
.venv/bin/python -c "from webapp.app import app; print(app.title)"
```

## 严禁提交的内容

以下文件含真实密钥 / 用户 PII,**永远不要** `git add`:

| 文件 | 原因 |
|---|---|
| `.env` | Stripe LIVE 密钥、GLM API key |
| `webapp/paid_users.json` | 付费用户邮箱(PII) |
| `webapp/invite_codes.json` | 邀请码池(含 used_by 邮箱) |
| `webapp/active_sessions.json` | 活跃 session 记录(含 email + jti) |

`.gitignore` 已覆盖,但每次 `git status` 仍要扫一眼,避免 `-A` / `.` 误加。

## Commit 规约

参考现有提交风格:

| 前缀 | 用途 |
|---|---|
| `feat(scope):` | 新功能 |
| `fix(scope):` | Bug 修复 |
| `refactor(scope):` | 不改行为的重构 |
| `docs:` | 文档 |
| `Data:` | WC2026 市场报价增量更新(自动脚本) |
| `chore:` | 杂项(依赖升级、配置调整) |

`scope` 通常是模块名:`auth` / `stripe` / `schedule` / `predict` / `i18n` 等。

示例:
```
feat(auth): 已 Pro 用户跨设备邮箱登录(/api/email-login)
fix(stripe): 切换 live 模式 — 统一 USD 收款 + 单价 ¥2→¥3
```

## 分支策略

`master` 单分支直接提交。改动较大时可建 `feature/<name>` 分支,合并后删除。

## Stripe 改动注意

- 默认就是 LIVE 模式(`sk_live_*`),任何金额会真扣钱,自测用 $0.42 起测
- 修改 `payments.py` 前先读 `webapp/auth.py` 理解 session 流程
- 不要在测试里发真请求,用 mock;现有测试见 `tests/test_payments_*.py`

## 鉴权改动注意

- 单 session 限制:每 email 同时只 1 个活跃 session,详见 `webapp/auth.py` 注释
- 修改 session 逻辑后必跑 `tests/test_app_security.py`

## 数据改动注意

- `results.csv` 是训练数据,不要手工编辑,用 `fetch_results.py` / `update_live.py`
- `elo_ratings.json` / `params.json` / `xgb_wc.json` 是模型产物,用 `compute_*.py` / `train_xgb.py` 重新生成
- `wc2026_live.csv` / `wc2026_schedule.csv` 是抓取缓存,`.gitignore` 已排除

## 添加新语言

1. 在 `webapp/static/i18n/` 加 `<locale>.json`(参考 `zh.json` / `en.json`)
2. 在 `webapp/static/i18n.js` 的 `LOCALES` 列表注册
3. 前端会自动识别浏览器语言

## 发布流程

1. 更新 `CHANGELOG.md`
2. 跑全套测试 + 冒烟
3. 提交 + 推送 `master`
4. GitHub Actions CI 通过后部署
