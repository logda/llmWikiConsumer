#!/usr/bin/env bash
# dev.sh - 开发环境管理脚本
# 用法: ./scripts/dev.sh {start|stop|restart|reset|status|logs}

set -e

# ── 项目路径 ──────────────────────────────────────────────
SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
PROJECT_DIR="$(cd "$SCRIPT_DIR/.." && pwd)"
cd "$PROJECT_DIR"

# ── 颜色输出 ──────────────────────────────────────────────
RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[0;33m'
CYAN='\033[0;36m'
BOLD='\033[1m'
NC='\033[0m' # No Color

info()    { echo -e "${CYAN}[INFO]${NC}  $*"; }
success() { echo -e "${GREEN}[OK]${NC}    $*"; }
warn()    { echo -e "${YELLOW}[WARN]${NC}  $*"; }
error()   { echo -e "${RED}[ERROR]${NC} $*"; }

# ── 工具函数 ──────────────────────────────────────────────

check_docker() {
    if ! docker info &>/dev/null; then
        error "Docker 未运行！请先启动 Docker Desktop。"
        exit 1
    fi
    success "Docker 正在运行"
}

wait_for_healthy() {
    local max_wait=60
    local elapsed=0
    info "等待服务健康检查通过..."
    while [ $elapsed -lt $max_wait ]; do
        local all_healthy=true
        for svc in postgres redis qdrant; do
            local status
            status=$(docker compose ps --format json 2>/dev/null \
                | python3 -c "
import sys, json
for line in sys.stdin:
    obj = json.loads(line)
    if obj.get('Service') == '$svc':
        print(obj.get('Health', obj.get('Status', 'unknown')))
        break
" 2>/dev/null || echo "unknown")
            if [ "$status" != "healthy" ]; then
                all_healthy=false
                break
            fi
        done
        if $all_healthy; then
            success "所有服务健康检查通过"
            return 0
        fi
        sleep 2
        elapsed=$((elapsed + 2))
        printf "\r${CYAN}[INFO]${NC}  等待中... ${elapsed}/${max_wait}s"
    done
    echo ""
    warn "等待超时 (${max_wait}s)，服务可能尚未就绪"
    docker compose ps
    return 1
}

find_uvicorn_pid() {
    pgrep -f "uvicorn app.main:app" 2>/dev/null || true
}

# ── 子命令 ────────────────────────────────────────────────

cmd_start() {
    echo -e "${BOLD}═══ 启动开发环境 ═══${NC}"

    # 1. 检查 Docker
    check_docker

    # 2. 启动 docker-compose
    info "启动 Docker 服务 (PostgreSQL + Redis + Qdrant)..."
    docker compose up -d
    success "Docker 服务已启动"

    # 3. 等待健康检查
    wait_for_healthy

    # 4. 检查 .venv
    if [ ! -d ".venv" ]; then
        info "未找到 .venv，创建虚拟环境并安装依赖..."
        python3 -m venv .venv
        source .venv/bin/activate
        pip install -e ".[dev]"
        success "虚拟环境创建并依赖安装完成"
    else
        success ".venv 已存在"
    fi

    # 5. 检查 .env
    if [ ! -f ".env" ]; then
        warn "未找到 .env，从 .env.example 复制..."
        cp .env.example .env
        error "请编辑 .env 文件，填写 LLM_API_KEY 后重新运行！"
        echo ""
        echo "  ${YELLOW}vi .env${NC}  或  ${YELLOW}open -e .env${NC}"
        echo ""
        exit 1
    else
        # 检查 LLM_API_KEY 是否已设置
        if grep -q "^LLM_API_KEY=$" .env 2>/dev/null || grep -q "^LLM_API_KEY=$" .env 2>/dev/null; then
            warn "LLM_API_KEY 为空，部分功能可能无法使用"
        fi
        success ".env 已存在"
    fi

    # 6. 执行 alembic 迁移
    info "执行数据库迁移 (alembic upgrade head)..."
    source .venv/bin/activate
    alembic upgrade head
    success "数据库迁移完成"

    # 7. 启动 uvicorn
    echo ""
    echo -e "${BOLD}═══ 启动 API 服务 ═══${NC}"
    info "uvicorn --reload 模式启动中..."
    echo "  地址: http://localhost:8000"
    echo "  按 Ctrl+C 停止"
    echo ""
    uvicorn app.main:app --reload
}

cmd_stop() {
    echo -e "${BOLD}═══ 关闭开发环境 ═══${NC}"

    # 1. 停止 uvicorn
    local pid
    pid=$(find_uvicorn_pid)
    if [ -n "$pid" ]; then
        info "停止 uvicorn (PID: $pid)..."
        kill $pid 2>/dev/null || true
        # 等待进程退出
        local wait=0
        while kill -0 $pid 2>/dev/null && [ $wait -lt 5 ]; do
            sleep 1
            wait=$((wait + 1))
        done
        # 如果还在运行，强制终止
        if kill -0 $pid 2>/dev/null; then
            kill -9 $pid 2>/dev/null || true
        fi
        success "uvicorn 已停止"
    else
        info "uvicorn 未在运行"
    fi

    # 2. docker-compose down
    info "停止 Docker 服务..."
    docker compose down
    success "Docker 服务已停止"
}

cmd_restart() {
    echo -e "${BOLD}═══ 重启开发环境 ═══${NC}"
    cmd_stop
    echo ""
    cmd_start
}

cmd_reset() {
    echo -e "${BOLD}═══ 重置开发环境（清除所有数据）═══${NC}"

    # 1. 停止 uvicorn
    local pid
    pid=$(find_uvicorn_pid)
    if [ -n "$pid" ]; then
        info "停止 uvicorn (PID: $pid)..."
        kill $pid 2>/dev/null || true
    fi

    # 2. docker-compose down -v (删除 volumes)
    warn "这将删除所有数据库数据（PostgreSQL、Redis、Qdrant）！"
    read -p "确认重置？[y/N] " confirm
    if [ "$confirm" != "y" ] && [ "$confirm" != "Y" ]; then
        info "已取消重置"
        exit 0
    fi

    info "删除 Docker 容器和数据卷..."
    docker compose down -v
    success "容器和数据卷已删除"

    # 3. 重新启动
    check_docker
    info "重新启动 Docker 服务..."
    docker compose up -d

    # 4. 等待健康检查
    wait_for_healthy

    # 5. 数据库迁移
    info "执行数据库迁移..."
    source .venv/bin/activate
    alembic upgrade head
    success "数据库迁移完成"

    echo ""
    success "环境已重置！"
    warn "数据已清除，请重新上传知识库"
}

cmd_status() {
    echo -e "${BOLD}═══ 开发环境状态 ═══${NC}"

    # 1. Docker 服务状态
    echo -e "\n${CYAN}▸ Docker 服务${NC}"
    if docker info &>/dev/null; then
        docker compose ps
    else
        error "Docker 未运行"
    fi

    # 2. uvicorn 状态
    echo -e "\n${CYAN}▸ API 服务 (uvicorn)${NC}"
    local pid
    pid=$(find_uvicorn_pid)
    if [ -n "$pid" ]; then
        success "uvicorn 运行中 (PID: $pid)"
    else
        info "uvicorn 未运行"
    fi

    # 3. .env 关键配置（隐藏 API Key）
    echo -e "\n${CYAN}▸ 环境配置 (.env)${NC}"
    if [ -f ".env" ]; then
        while IFS='=' read -r key value; do
            # 跳过注释和空行
            [[ "$key" =~ ^#.*$ ]] && continue
            [[ -z "$key" ]] && continue
            # 隐藏包含 KEY、PASSWORD、SECRET 的值
            if [[ "$key" =~ (KEY|PASSWORD|SECRET) ]]; then
                if [ -n "$value" ]; then
                    echo "  $key=****"
                else
                    echo "  $key=${RED}(未设置)${NC}"
                fi
            else
                echo "  $key=$value"
            fi
        done < <(grep -v '^\s*#' .env | grep -v '^\s*$')
    else
        warn ".env 文件不存在"
    fi

    echo ""
}

cmd_logs() {
    echo -e "${BOLD}═══ Docker 日志 (Ctrl+C 退出) ═══${NC}"
    docker compose logs -f
}

# ── 主入口 ────────────────────────────────────────────────

usage() {
    echo "用法: $0 {start|stop|restart|reset|status|logs}"
    echo ""
    echo "子命令:"
    echo "  start    启动开发环境 (Docker + venv + uvicorn)"
    echo "  stop     关闭开发环境"
    echo "  restart  重启开发环境 (stop + start)"
    echo "  reset    重置环境（清除所有数据重来）"
    echo "  status   查看环境状态"
    echo "  logs     查看 Docker 日志"
}

case "${1:-}" in
    start)   cmd_start   ;;
    stop)    cmd_stop    ;;
    restart) cmd_restart ;;
    reset)   cmd_reset   ;;
    status)  cmd_status  ;;
    logs)    cmd_logs    ;;
    *)
        usage
        exit 1
        ;;
esac
