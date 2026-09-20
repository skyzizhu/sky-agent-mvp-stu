#!/usr/bin/env bash
# 停止研究工作台（配合 start.sh 使用）
cd "$(dirname "$0")"

if [ -f logs/workbench.pid ] && kill "$(cat logs/workbench.pid)" 2>/dev/null; then
  echo "[停止] 工作台已停止（pid $(cat logs/workbench.pid)）"
  rm -f logs/workbench.pid
else
  PID=$(lsof -ti :7870 2>/dev/null)
  if [ -n "$PID" ]; then
    kill $PID && echo "[停止] 已停止端口 7870 上的进程（pid $PID）"
  else
    echo "[停止] 工作台未在运行"
  fi
fi
