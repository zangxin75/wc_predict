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
PY="${PYTHON:-python}"
LOG="update_odds.log"
TS() { date '+%Y-%m-%d %H:%M:%S'; }

echo "[$(TS)] === 开始增量采集(1X2 + OU) ===" | tee -a "$LOG"

# ---------- 1X2 ----------
echo "[$(TS)] --- 1X2(fetch_wc2026_odds) ---" | tee -a "$LOG"
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
