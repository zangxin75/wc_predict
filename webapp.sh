#!/usr/bin/env bash
# webapp.sh — WC2026 预测系统启动/停止/状态 (WSL2 兼容)
# 用法: ./webapp.sh {start|stop|restart|status}

set -euo pipefail
cd "$(dirname "$0")"

APP_DIR="/home/zx/project/足球预测"
LOG="$APP_DIR/webapp.log"
PIDFILE="$APP_DIR/webapp.pid"
UVICORN="$APP_DIR/.venv/bin/uvicorn"

start() {
  if [ -f "$PIDFILE" ] && kill -0 "$(cat "$PIDFILE")" 2>/dev/null; then
    echo "webapp 已在运行中 (PID $(cat "$PIDFILE"))"
    return 0
  fi

  echo "启动 webapp..."
  nohup "$UVICORN" webapp.app:app --host 0.0.0.0 --port 8000 >> "$LOG" 2>&1 &
  PID=$!
  echo $PID > "$PIDFILE"

  # 等待启动
  for i in $(seq 1 10); do
    sleep 1
    if curl -s http://127.0.0.1:8000/api/config >/dev/null 2>&1; then
      echo "webapp 启动成功 (PID $PID)"
      return 0
    fi
  done
  echo "webapp 启动超时,检查日志: tail -20 $LOG"
  return 1
}

stop() {
  if [ -f "$PIDFILE" ]; then
    PID=$(cat "$PIDFILE")
    kill "$PID" 2>/dev/null && echo "webapp 已停止 (PID $PID)" || echo "webapp 未运行"
    rm -f "$PIDFILE"
  else
    # 尝试从进程查找
    PID=$(pgrep -f "uvicorn webapp.app:app" 2>/dev/null || true)
    if [ -n "$PID" ]; then
      kill "$PID" 2>/dev/null || true
      echo "webapp 已停止 (PID $PID)"
    else
      echo "webapp 未运行"
    fi
  fi
}

status() {
  if [ -f "$PIDFILE" ] && kill -0 "$(cat "$PIDFILE")" 2>/dev/null; then
    echo "运行中 (PID $(cat "$PIDFILE"))"
    curl -s http://127.0.0.1:8000/api/status | python3 -m json.tool 2>/dev/null || echo "API 未响应"
  else
    PID=$(pgrep -f "uvicorn webapp.app:app" 2>/dev/null || true)
    if [ -n "$PID" ]; then
      echo "运行中 (PID $PID, 无 pidfile)"
      curl -s http://127.0.0.1:8000/api/status | python3 -m json.tool 2>/dev/null || echo "API 未响应"
    else
      echo "未运行"
    fi
  fi
}

case "${1:-status}" in
  start)   start ;;
  stop)    stop ;;
  restart) stop; sleep 1; start ;;
  status)  status ;;
  *)       echo "用法: $0 {start|stop|restart|status}" ;;
esac
