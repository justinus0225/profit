#!/usr/bin/env bash
# P.R.O.F.I.T. 시스템 시작 스크립트
# Usage: ./scripts/start.sh [dev|prod]

set -euo pipefail

MODE="${1:-dev}"
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PROJECT_DIR="$(dirname "$SCRIPT_DIR")"

cd "$PROJECT_DIR"

echo "=========================================="
echo "  P.R.O.F.I.T. System Launcher"
echo "  Mode: $MODE"
echo "=========================================="

# .env 파일 확인
if [ ! -f ".env" ]; then
    echo "[WARN] .env file not found. Creating from template..."
    cp .env.example .env
    echo "[INFO] .env created. Please edit it with your API keys."
    echo "       Required: EXCHANGE_API_KEY, EXCHANGE_API_SECRET"
    echo "       Required: At least one LLM API key (CLAUDE_API_KEY recommended)"
    exit 1
fi

# Docker Compose 확인
if ! command -v docker &>/dev/null; then
    echo "[ERROR] Docker is not installed."
    exit 1
fi

if [ "$MODE" = "dev" ]; then
    # .env 파일의 환경 변수를 현재 셸에 export (uvicorn 프로세스에 전달)
    echo "[0/4] Loading .env variables..."
    set -a
    # shellcheck disable=SC1091
    source .env
    set +a

    echo "[1/4] Starting infrastructure (TimescaleDB, Redis, Prometheus, Grafana)..."
    docker compose up -d timescaledb redis prometheus grafana

    echo "[2/4] Waiting for services..."
    sleep 5

    # Redis 연결 확인
    REDIS_PASS=$(grep REDIS_PASSWORD .env | cut -d= -f2)
    if docker compose exec redis redis-cli -a "${REDIS_PASS:-profit_dev_password}" ping | grep -q PONG; then
        echo "       Redis: OK"
    else
        echo "       Redis: WAITING..."
        sleep 5
    fi

    # TimescaleDB 연결 확인
    if docker compose exec timescaledb pg_isready -U profit -d profit_db -q; then
        echo "       TimescaleDB: OK"
    else
        echo "       TimescaleDB: WAITING..."
        sleep 10
    fi

    echo "[3/4] Starting P.R.O.F.I.T. core (local development)..."
    echo "       Use: uvicorn src.main:app --host 0.0.0.0 --port 8000 --reload"
    echo ""
    echo "[4/4] System URLs:"
    echo "       API:        http://localhost:8000"
    echo "       Health:     http://localhost:8000/health"
    echo "       Metrics:    http://localhost:8000/metrics"
    echo "       Swagger:    http://localhost:8000/docs"
    echo "       Grafana:    http://localhost:3001 (admin / \${GRAFANA_PASSWORD})"
    echo "       Prometheus: http://localhost:9090"
    echo ""
    uvicorn src.main:app --host 0.0.0.0 --port 8000 --reload

elif [ "$MODE" = "prod" ]; then
    echo "[1/3] Building and starting all services..."
    docker compose up -d --build

    echo "[2/3] Waiting for services..."
    sleep 10

    echo "[3/3] Checking health..."
    for i in {1..30}; do
        if curl -sf http://localhost:8000/health > /dev/null 2>&1; then
            echo "       Health check: OK"
            break
        fi
        if [ "$i" -eq 30 ]; then
            echo "       Health check: TIMEOUT (check logs with 'docker compose logs profit-core')"
            exit 1
        fi
        sleep 2
    done

    echo ""
    echo "=========================================="
    echo "  P.R.O.F.I.T. is running!"
    echo "=========================================="
    echo "  API:        http://localhost:8000"
    echo "  Health:     http://localhost:8000/health"
    echo "  Metrics:    http://localhost:8000/metrics"
    echo "  Swagger:    http://localhost:8000/docs"
    echo "  Grafana:    http://localhost:3001"
    echo "  Prometheus: http://localhost:9090"
    echo ""
    echo "  Logs:       docker compose logs -f profit-core"
    echo "  Stop:       docker compose down"
    echo "=========================================="
else
    echo "Usage: $0 [dev|prod]"
    exit 1
fi
