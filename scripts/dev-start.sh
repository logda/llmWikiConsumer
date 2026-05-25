#!/usr/bin/env bash
# dev-start.sh — 启动开发环境
set -e

cd "$(dirname "$0")/.."

# 检查 Docker
if ! docker info &>/dev/null; then
    echo "ERROR: Docker 未运行，请先启动 Docker Desktop" >&2
    exit 1
fi

# 启动依赖服务
docker compose up -d

# 等待服务就绪（简单轮询 postgres）
echo "等待 PostgreSQL 就绪..."
until docker compose exec -T postgres pg_isready -U wiki -d llm_wiki &>/dev/null; do
    sleep 1
done
echo "服务已就绪"

# 激活 venv
source .venv/bin/activate

# 数据库迁移
alembic upgrade head

# 启动 API
echo "uvicorn 启动: http://localhost:8000  (Ctrl+C 停止)"
uvicorn app.main:app --reload
