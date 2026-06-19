#!/usr/bin/env bash
# update_odds_api.sh — The Odds API 抓取(独立 cron,避免和 update_odds.sh 撞频率)
#
# ⚠️ 2026-06-19 新增:从 update_odds.sh 拆出。
#
# 为什么独立? — 免费层 quota 限制(500 req/月)。
#   fetch_odds_api.py 每次消耗 credits = markets × regions
#   默认 h2h + totals × eu + uk = 2 × 2 = 4 credits/call
#
# 推荐 cron 频率(避免 500 quota 超限):
#   • 每 6 小时(4 次/天)  → 4 × 4 × 30 = 480 credits/月 ✓ 在限内
#   • 每 8 小时(3 次/天)  → 4 × 3 × 30 = 360 credits/月 ✓ 更安全
#   • 每 12 小时(2 次/天) → 4 × 2 × 30 = 240 credits/月 ✓ 留余量给 scan
#
# 错误 cron 频率:
#   • 每小时  → 4 × 24 × 30 = 2880 credits/月 ✗ 超限 5.7x
#   • 每 4 小时 → 4 × 6 × 30 = 720 credits/月 ✗ 超限
#
# scan_arbitrage.py 也会消耗 quota,建议手动跑(每周 1-2 次),不挂 cron。
#
# 用法:
#   ./update_odds_api.sh                 # 默认 python
#   PYTHON=python3 ./update_odds_api.sh  # 指定解释器
#   ./update_odds_api.sh --dry            # 只抓不写
#
# crontab 配置示例(每 6 小时):
#   7 */6 * * * /home/zx/project/足球预测/update_odds_api.sh
#             ^ 偏移到 :07 避开整点(:00/:30)的高峰
set -u

cd "$(dirname "$0")"
# 默认用本地 venv python(cron 环境 PATH 可能不含 python),可被环境变量覆盖
if [ -z "${PYTHON:-}" ] && [ -x "./.venv/bin/python" ]; then
  PY="./.venv/bin/python"
else
  PY="${PYTHON:-python}"
fi
LOG="update_odds_api.log"
TS() { date '+%Y-%m-%d %H:%M:%S'; }

echo "[$(TS)] === The Odds API 增量采集(主源) ===" | tee -a "$LOG"

if ! "$PY" fetch_odds_api.py "$@" >>"$LOG" 2>&1; then
  echo "[$(TS)] ⚠ fetch_odds_api 失败(check $LOG)" | tee -a "$LOG"
  exit 1
fi

# 检查配额(响应头 x-requests-remaining)
REMAINING=$(grep -oP '剩余 \K\d+' "$LOG" | tail -1)
if [ -n "$REMAINING" ] && [ "$REMAINING" -lt 50 ] 2>/dev/null; then
  echo "[$(TS)] ⚠ 配额仅剩 $REMAINING(本月快用完),考虑付费或降低频率" | tee -a "$LOG"
fi

# 提交(若有变)
if git diff --quiet -- odds_wc.csv ou_wc.csv; then
  echo "[$(TS)] 无新报价,跳过提交" | tee -a "$LOG"
  exit 0
fi

git add odds_wc.csv ou_wc.csv
git commit -q -m "Data: The Odds API 增量更新 $(date +%F)"
if git push origin master >>"$LOG" 2>&1; then
  echo "[$(TS)] ✓ 已提交并推送" | tee -a "$LOG"
else
  echo "[$(TS)] △ 已提交本地,推送失败(检查凭据/网络)" | tee -a "$LOG"
fi
