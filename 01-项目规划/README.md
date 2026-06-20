# 项目规划 — 足球预测(WC2026)

| 项 | 值 |
|----|-----|
| 项目名 | WC2026 足球预测 |
| 创建 | 2025 |
| 状态 | P0/P1 安全整改完成 · 公网已通(花生壳穿透) |
| 部署 | `http://aijialidu.ticp.vip/wc/`(经花生壳 + token-efficiency-nginx 容器) |
| 技术栈 | Python 3.12 + FastAPI + uvicorn + slowapi + 智谱 GLM-4 + Stripe |
| 最近大版本 | V1.1 安全审计报告(2026-06-20) |

## 快速链接

| 类别 | 路径 |
|------|------|
| 安全审计 | `04-技术方案/安全审计_足球预测_20260620.md` |
| 部署运维 | `docs/` 下的 backtest / reflection / pattern 系列 |
| 分享邀请 | `docs/share-referral-program.md` |
| 测试 | `tests/`(109 passed, 7 skipped, 0 failed) |

## 当前运行架构

```
用户浏览器
  ↓
aijialidu.ticp.vip/wc/  (Oray DNS)
  ↓
Oray 边缘节点 119.188.71.47
  ↓
花生壳内网穿透 (Windows)
  ↓
localhost:8080 → Docker container token-efficiency-nginx-proxy :80
  ↓
location /wc/ → proxy_pass http://host.docker.internal:8000/
  ↓
docker0 bridge 172.17.0.1 → 我们的 webapp (uvicorn, 绑 172.17.0.1:8000)
```

## 近期 Git 提交

| Commit | 主题 |
|--------|------|
| `7331a70` | Feat: upgrade-modal 移动端布局 |
| `5d65d5e` | Fix: 8 个 pre-existing 测试失败 + fetch_wc2026 真实 bug |
| `12cd804` | Fix: webapp 改绑 172.17.0.1 让容器能反代 /wc |
| `d8b6c95` | Chore: pre-commit + keepalive 安全回归 |
| `d9571b2` | Feat: P0 鉴权基线 + 分享邀请码 + 审计报告 |
| `ad36c91` | Security: P1 回归测试 + pre-commit 钩子 |
| `9ab043f` | Security: P1 加固(速率限制 / 注入防护 / 密钥隔离 / 原子写 / 响应头) |

## 维护联系人

- Owner: zx
- 文档维护规范:遵循 `~/.claude/CLAUDE.md`「Document Management Standards」