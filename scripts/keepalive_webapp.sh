#!/usr/bin/env bash
# keepalive_webapp.sh — WC2026 webapp 保活
#
# 用法:
#   ./scripts/keepalive_webapp.sh           # 检查 + 必要时拉起
#   crontab: */3 * * * * cd /home/zx/project/足球预测 && bash scripts/keepalive_webapp.sh
#
# 行为:
#   1. 探活:curl 8000/,期望 HTTP 200,3 秒超时
#   2. 健康:直接退出(返回 0)
#   3. 异常:用 sudo 清掉卡在 8000 的僵尸 uvicorn → 后台重启 → 等就绪 → 输出日志

set -u

PORT="${WEBAPP_PORT:-8000}"
HOST="127.0.0.1"
PROJ="/home/zx/project/足球预测"
LOG="/tmp/wc_webapp.log"
PIDFILE="/tmp/wc_webapp.pid"
HEALTH_URL="http://${HOST}:${PORT}/"
CURL_TIMEOUT=3

log() { printf '[%s] %s\n' "$(date '+%F %T')" "$*" | tee -a "$LOG"; }

probe() {
  curl -s -o /dev/null -w '%{http_code}' --max-time "$CURL_TIMEOUT" "$HEALTH_URL" 2>/dev/null
}

# ── 1. 探活 ──────────────────────────────────────────────
code=$(probe)
if [ "$code" = "200" ]; then
  log "OK  port ${PORT} healthy (HTTP 200)"
  exit 0
fi
log "FAIL  port ${PORT} unhealthy (got: ${code:-timeout}), recovering..."

# ── 2. 清僵尸(我们自己留下的 PID 优先,再兜底 sudo 清 root 僵尸)──
if [ -f "$PIDFILE" ]; then
  old=$(cat "$PIDFILE" 2>/dev/null)
  if [ -n "${old:-}" ] && kill -0 "$old" 2>/dev/null; then
    log "kill old PID $old (our previous run)"
    kill -9 "$old" 2>/dev/null || true
  fi
  rm -f "$PIDFILE"
fi

# 卡在端口上的同名僵尸 uvicorn(可能是 supervisor 没拉干净的残留)
for pid in $(pgrep -f "uvicorn.*--port ${PORT}" 2>/dev/null || true); do
  log "kill zombie uvicorn PID $pid on port ${PORT}"
  kill -9 "$pid" 2>/dev/null || sudo -n kill -9 "$pid" 2>/dev/null || true
done
sleep 1

# ── 3. 后台拉起 ─────────────────────────────────────────
log "starting: cd ${PROJ} && ./.venv/bin/python -m uvicorn webapp.app:app --host 127.0.0.1 --port ${PORT}"
cd "$PROJ" || { log "ABORT  cannot cd to ${PROJ}"; exit 1; }
nohup ./.venv/bin/python -m uvicorn webapp.app:app --host 127.0.0.1 --port "$PORT" \
  >> "$LOG" 2>&1 &
echo $! > "$PIDFILE"
new_pid=$(cat "$PIDFILE")
log "spawned PID $new_pid, waiting for readiness..."

# ── 4. 等就绪(最多 20 秒)────────────────────────────
for i in $(seq 1 20); do
  sleep 1
  code=$(probe)
  if [ "$code" = "200" ]; then
    log "READY  PID $new_pid responding on port ${PORT} (took ${i}s)"
    exit 0
  fi
done

log "TIMEOUT  PID $new_pid did not become healthy in 20s, last code=${code:-timeout}"
exit 1