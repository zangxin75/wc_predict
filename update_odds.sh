#!/usr/bin/env bash
# update_odds.sh — WC2026 市场报价增量采集(手动 / 计划任务通用)
#
# 同时抓取 1X2(odds_wc.csv) + OU(ou_wc.csv),任一有变则 git commit + push。
# 两个脚本独立运行,单个失败不影响另一个(best-effort)。
#
# 用法:
#   ./update_odds.sh                 # 默认 python
#   PYTHON=python3 ./update_odds.sh  # 指定解释器
#
# 退出码:0=正常(有或无更新);1=抓取失败。
set -u

cd "$(dirname "$0")"
# 默认用本地 venv python(cron 环境 PATH 可能不含 python),可被环境变量覆盖
if [ -z "${PYTHON:-}" ] && [ -x "./.venv/bin/python" ]; then
  PY="./.venv/bin/python"
else
  PY="${PYTHON:-python}"
fi
LOG="update_odds.log"
TS() { date '+%Y-%m-%d %H:%M:%S'; }

echo "[$(TS)] === 开始增量采集(1X2 + OU) ===" | tee -a "$LOG"

# 注意(2026-06-19):The Odds API 已拆到 update_odds_api.sh 独立 cron 跑(quota 限制)。
# 本脚本只跑 BetExplorer(免费),可放心挂每小时 cron。
# fetch_wc2026_odds.py 的 merge 语义已改为"只填空",不会覆盖 The Odds API 优质数据。

# ---------- 1X2(BetExplorer)----------
echo "[$(TS)] --- 1X2(fetch_wc2026_odds,只填空) ---" | tee -a "$LOG"
if ! "$PY" fetch_wc2026_odds.py >>"$LOG" 2>&1; then
  echo "[$(TS)] ⚠ 1X2 抓取失败,继续 OU" | tee -a "$LOG"
fi

# ---------- OU ----------
echo "[$(TS)] --- OU(fetch_wc2026_ou) ---" | tee -a "$LOG"
if ! "$PY" fetch_wc2026_ou.py >>"$LOG" 2>&1; then
  echo "[$(TS)] ⚠ OU 抓取失败,继续检查提交" | tee -a "$LOG"
fi

# ---------- 提交(任一有变) ----------
CHANGED=""
if ! git diff --quiet -- odds_wc.csv; then
  CHANGED="$CHANGED odds_wc.csv(1X2)"
fi
if ! git diff --quiet -- ou_wc.csv; then
  CHANGED="$CHANGED ou_wc.csv(OU)"
fi

if [ -z "$CHANGED" ]; then
  echo "[$(TS)] 无新报价,跳过提交" | tee -a "$LOG"
  exit 0
fi

git add odds_wc.csv ou_wc.csv
git commit -q -m "Data: WC2026 市场报价增量更新 $(date +%F)"
if git push origin master >>"$LOG" 2>&1; then
  echo "[$(TS)] ✓ 已提交并推送新报价:${CHANGED}" | tee -a "$LOG"
else
  echo "[$(TS)] △ 已提交本地,推送失败(检查凭据/网络):${CHANGED}" | tee -a "$LOG"
fi
