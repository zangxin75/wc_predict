#!/usr/bin/env bash
# update_odds.sh — WC2026 市场报价增量采集(手动 / 计划任务通用)
#
# 流程:fetch_wc2026_ou.py 抓取并合并 → 若 ou_wc.csv 有变化则提交+推送;无变化则跳过(不产生空提交)。
# merge_ou 按 (year,home,away) 去重保历史,故可安全反复重跑。
#
# 用法:
#   ./update_odds.sh                 # 默认 python
#   PYTHON=python3 ./update_odds.sh  # 指定解释器
#
# 退出码:0=正常(有或无更新);1=抓取失败。
set -u

cd "$(dirname "$0")"          # 切到 wc_predict 目录(脚本所在处)
PY="${PYTHON:-python}"
LOG="update_odds.log"
TS() { date '+%Y-%m-%d %H:%M:%S'; }

echo "[$(TS)] === 开始增量采集 ===" | tee -a "$LOG"

if ! "$PY" fetch_wc2026_ou.py >>"$LOG" 2>&1; then
  echo "[$(TS)] ✗ 抓取失败(见 $LOG)" | tee -a "$LOG"
  exit 1
fi

# 仅当 ou_wc.csv 实际有改动才提交(避免每日空提交)
if git diff --quiet -- ou_wc.csv; then
  echo "[$(TS)] 无新报价,跳过提交" | tee -a "$LOG"
  exit 0
fi

git add ou_wc.csv
git commit -q -m "Data: WC2026 市场报价增量更新 $(date +%F)"
if git push origin master >>"$LOG" 2>&1; then
  echo "[$(TS)] ✓ 已提交并推送新报价" | tee -a "$LOG"
else
  echo "[$(TS)] △ 已提交本地,但推送失败(检查 git 凭据/网络)" | tee -a "$LOG"
fi
