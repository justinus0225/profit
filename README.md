# P.R.O.F.I.T.

**Predictive Routing & Orchestration Framework for Intelligent Trading**

암호화폐 현물 자동매매를 위한 멀티 에이전트 시스템. 8개의 전문 에이전트가 계층적 합의 메커니즘을 통해 매매 의사결정을 수행한다.

## Architecture

```
Level 3: Orchestrator (합의 조율, 최종 의사결정)
Level 2: Risk Manager | Portfolio Manager | Execution Agent
Level 1: Analyst (Macro/Micro/Sentiment) | Quant Agent | Data Engineer | SW Engineer | QA Agent
```

- **2-out-of-3 Quorum 합의** + Risk Manager 거부권
- **2단계 코인 스크리닝**: 펀더멘탈 필터 (Analyst) -> 기술적 평가 (Quant)
- **현물 거래 전용**: 스테이블코인 비중 조절 + 분산 투자 + 손절

상세 설계: [docs/ARCHITECTURE.md](docs/ARCHITECTURE.md)

## Tech Stack

| 구성 요소 | 기술 |
|-----------|------|
| Backend | Python 3.12, FastAPI, asyncio |
| Database | TimescaleDB (시계열), PgBouncer (풀링) |
| Message Broker | Redis (Pub/Sub + 캐시) |
| AI/LLM | Claude / Gemini / OpenAI (멀티 프로바이더) |
| Exchange | ccxt (Binance 기본) |
| Monitoring | Prometheus + Grafana |
| Container | Docker Compose |
| CI/CD | GitHub Actions |

## Quick Start

### Prerequisites

- Docker & Docker Compose
- Python 3.12+ (로컬 개발 시)
- 거래소 API 키 (Binance 등)
- LLM API 키 (Claude 권장)

### 1. 환경 설정

```bash
# 저장소 클론
git clone <repository-url>
cd profit

# 환경 변수 설정
cp .env.example .env
# .env 파일을 편집하여 API 키 입력
```

**필수 환경 변수:**

| 변수 | 설명 |
|------|------|
| `EXCHANGE_API_KEY` | 거래소 API 키 |
| `EXCHANGE_API_SECRET` | 거래소 API 시크릿 |
| `CLAUDE_API_KEY` | Claude API 키 (LLM 분석용) |
| `POSTGRES_PASSWORD` | TimescaleDB 비밀번호 |
| `REDIS_PASSWORD` | Redis 비밀번호 |

**선택 환경 변수:**

| 변수 | 설명 |
|------|------|
| `TELEGRAM_BOT_TOKEN` | Telegram 알림 봇 토큰 |
| `TELEGRAM_CHAT_ID` | Telegram 채팅 ID |
| `DISCORD_WEBHOOK_URL` | Discord 웹훅 URL |
| `GEMINI_API_KEY` | Gemini API 키 (폴백) |
| `OPENAI_API_KEY` | OpenAI API 키 (폴백) |

### 2. 시스템 시작

#### Production 모드 (Docker Compose 전체)

```bash
./scripts/start.sh prod
```

전체 서비스(TimescaleDB, Redis, profit-core, Prometheus, Grafana)가 Docker로 실행된다.

#### Development 모드 (로컬 Python + Docker 인프라)

```bash
# 인프라만 Docker로 실행
./scripts/start.sh dev

# 또는 수동으로:
docker compose up -d timescaledb redis prometheus grafana
pip install -e ".[dev]"
uvicorn src.main:app --host 0.0.0.0 --port 8000 --reload
```

### 3. 시스템 확인

| 서비스 | URL |
|--------|-----|
| API | http://localhost:8000 |
| Health Check | http://localhost:8000/health |
| Swagger Docs | http://localhost:8000/docs |
| Prometheus Metrics | http://localhost:8000/metrics |
| Grafana Dashboard | http://localhost:3001 |
| Prometheus UI | http://localhost:9090 |

### 4. 시스템 중지

```bash
./scripts/stop.sh
# 또는
docker compose down
# 데이터 볼륨까지 삭제:
docker compose down -v
```

## Paper Trading (모의 매매)

기본적으로 **Paper Trading 모드**가 활성화되어 있다. 실제 자금 없이 시스템 동작을 확인할 수 있다.

```bash
# .env
SYSTEM_PAPER_TRADING_MODE=true   # 모의 매매 모드
SYSTEM_TRADING_ENABLED=false     # 매매 비활성화 (true로 변경 시 활성화)
```

실제 매매를 시작하려면:
1. `.env`에서 `SYSTEM_PAPER_TRADING_MODE=false` 설정
2. `SYSTEM_TRADING_ENABLED=true` 설정
3. 거래소 API 키에 매매 권한이 있는지 확인

## Project Structure

```
profit/
├── src/
│   ├── main.py                    # FastAPI 진입점 + 에이전트 라이프사이클
│   ├── agents/                    # 8개 에이전트
│   │   ├── base.py                # BaseAgent 추상 클래스
│   │   ├── orchestrator.py        # 오케스트레이터 (합의 조율)
│   │   ├── analyst/               # 경제 분석 에이전트
│   │   │   ├── macro.py           #   거시경제 (Fear&Greed, BTC dominance)
│   │   │   ├── micro.py           #   개별 코인 펀더멘탈
│   │   │   ├── screener.py        #   코인 스크리너 (1차 필터)
│   │   │   └── sentiment.py       #   뉴스/소셜 감성 분석
│   │   ├── quant/                 # 퀀트 에이전트
│   │   │   ├── indicators.py      #   기술적 지표 (RSI, MACD, BB, ADX)
│   │   │   ├── scoring.py         #   시그널 스코어링
│   │   │   └── backtest.py        #   전략 백테스팅
│   │   ├── risk/                  # 리스크 관리 에이전트
│   │   ├── portfolio/             # 포트폴리오 관리 에이전트
│   │   ├── executor/              # 매매 실행 에이전트 (OMS, TWAP)
│   │   ├── engineer/              # 데이터 엔지니어 에이전트
│   │   ├── developer/             # 소프트웨어 엔지니어 에이전트
│   │   └── qa/                    # QA 에이전트
│   ├── api/                       # REST API + WebSocket
│   ├── core/                      # 설정, 부팅, LLM 라우터, 메트릭
│   ├── data/                      # DB 모델, 마이그레이션, 데이터 수집
│   ├── exchange/                  # 거래소 클라이언트 (ccxt 래퍼)
│   └── integrations/              # OpenClaw, 알림 (Telegram/Discord)
├── config/                        # 기본 설정 YAML, Prometheus 설정
├── grafana/                       # Grafana 프로비저닝 + 대시보드
├── scripts/                       # 시작/중지 스크립트
├── tests/                         # 단위/통합 테스트
├── docker-compose.yml             # 6개 서비스 정의
├── Dockerfile                     # Python 3.12 프로덕션 이미지
└── pyproject.toml                 # 의존성 + 빌드 설정
```

## Monitoring

### Grafana Dashboard

Grafana (`http://localhost:3001`)에 프로비저닝된 "P.R.O.F.I.T. Overview" 대시보드:

- Open Positions / Portfolio PnL
- Risk Level (Gauge 0-100)
- Order Rate / Latency (p50, p95)
- LLM Call Rate / Latency
- Exchange API Calls
- Circuit Breaker Trips
- Data Quality Anomalies

### Prometheus Metrics

`/metrics` 엔드포인트에서 수집되는 주요 메트릭:

- `profit_orders_total` — 주문 건수 (side, status)
- `profit_risk_level` — 현재 리스크 점수
- `profit_signals_generated_total` — 생성된 시그널 수
- `profit_llm_calls_total` — LLM API 호출 수
- `profit_agent_errors_total` — 에이전트 에러 수

## OpenClaw Commands

OpenClaw 메시징을 통해 관리자가 사용할 수 있는 명령어:

| 명령어 | 설명 |
|--------|------|
| `/status` | 시스템 상태 요약 |
| `/agents` | 에이전트 상태 |
| `/pause` | 매매 일시 중단 |
| `/resume` | 매매 재개 |
| `/risk` | 현재 리스크 레벨 |
| `/balance` | 자산 잔고 요약 |
| `/help` | 명령어 도움말 |

## Testing

```bash
# 의존성 설치
pip install -e ".[dev]"

# 전체 테스트
pytest tests/ -v

# 커버리지 포함
pytest tests/ --cov=src --cov-report=term-missing

# 린트
ruff check src/ tests/

# 타입 체크
mypy src/ --ignore-missing-imports
```

## License

MIT
