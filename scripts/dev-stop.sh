#!/usr/bin/env bash
# dev-stop.sh — 停止开发环境
set -e

cd "$(dirname "$0")/.."

# 停止 uvicorn
pid=$(pgrep -f "uvicorn app.main:app" 2>/dev/null || true)
if [ -n "$pid" ]; then
    echo "停止 uvicorn (PID: $pid)"
    kill $pid 2>/dev/null || true
fi

# 停止 Docker 服务
docker compose down

echo "开发环境已停止"
