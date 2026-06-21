## Summary
<!-- 1-3 句话说明这次改动做了什么、为什么 -->

## Changes
- 
- 
- 

## Test plan
<!-- 勾选完成的验证项 -->
- [ ] `python -m pytest tests/ -q` 全绿
- [ ] `python -c "from webapp.app import app"` 导入成功
- [ ] 本机 uvicorn 启动后关键页面手测:
  - [ ] 赛程时刻表正常渲染
  - [ ] 夺冠概率正常渲染
  - [ ] 价值下注(若 Pro)正常渲染
  - [ ] 中英文切换正常
- [ ] Stripe LIVE 流程未受影响(若涉及支付)
- [ ] 单 session 鉴权未受影响(若涉及 auth)

## Screenshots
<!-- UI 改动附截图 -->

## Checklist
- [ ] 未提交 `.env` / `paid_users.json` / `invite_codes.json` / `active_sessions.json`
- [ ] commit message 已遵循现有风格(`feat(scope): ...` / `fix(scope): ...` / `Data: ...`)
- [ ] CHANGELOG.md 已更新(若为用户可见变更)
