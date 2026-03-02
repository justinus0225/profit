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

## Stage 1: CI 검증 + 백테스트 (로컬, 오프라인)

LLM API 키 없이 로컬에서 전략 성과를 검증한다. Docker, 거래소 API, LLM 모두 불필요.

### 필요 항목

| 항목 | 필수 여부 | 용도 |
|------|----------|------|
| Python 3.12+ | 필수 | 런타임 |
| 인터넷 연결 | 데이터 다운로드 시 1회만 | ccxt로 과거 OHLCV 다운로드 |
| 거래소 API 키 | **불필요** | ccxt 공개 API 사용 |
| LLM API 키 | **불필요** | 규칙 기반 전략 사용 |
| Docker | **불필요** | DB/Redis 미사용 |

### 실행 방법

```bash
# 1) 가상환경 생성 및 의존성 설치 (최초 1회)
python3 -m venv .venv
source .venv/bin/activate
pip install -e .

# 2) 과거 OHLCV 데이터 다운로드 (네트워크 필요, 1회만)
#    data/ohlcv/ 디렉토리에 CSV 파일로 저장된다
python scripts/download_ohlcv.py --symbol BTC/USDT --timeframe 1h --days 180
python scripts/download_ohlcv.py --symbol ETH/USDT --timeframe 1h --days 180

# 3) 백테스트 실행 (오프라인 가능)
#    단일 전략 테스트
python scripts/run_backtest.py --data data/ohlcv/BTC_USDT_1h.csv --strategy combined

#    전체 5개 전략 비교
python scripts/run_backtest.py --data data/ohlcv/BTC_USDT_1h.csv --all-strategies

#    옵션 조정
python scripts/run_backtest.py \
  --data data/ohlcv/BTC_USDT_1h.csv \
  --strategy trend_following \
  --balance 50000 \
  --commission 0.001 \
  --position-pct 0.30

# 4) 단위 테스트
pip install -e ".[dev]"
pytest tests/ -v
```

### 백테스트 전략 종류

| 전략 | 설명 | 핵심 지표 |
|------|------|----------|
| `mean_reversion` | RSI 과매도/과매수 반전 | RSI(14) |
| `trend_following` | MA 골든크로스/데드크로스 | SMA(20), SMA(50) |
| `momentum` | 가격 변화율 추종 | ROC(12) |
| `breakout` | 20-bar 고점 돌파 + ATR 손절 | High(20), ATR(14) |
| `combined` | RSI + MA + Volume 복합 신호 | RSI + SMA + Volume ratio |

### Stage Gate 통과 기준 (자동 판정)

| 지표 | 기준 | 설명 |
|------|------|------|
| Sharpe Ratio | > 1.0 | 위험 대비 수익률 |
| MDD (최대 낙폭) | < 20% | 최대 자산 하락 폭 |
| Win Rate (승률) | > 50% | 수익 거래 비율 |
| Profit Factor (손익비) | > 1.5 | 총 이익 / 총 손실 |

백테스트 실행 후 4개 지표에 대한 PASS/FAIL이 자동 출력된다. 모두 PASS 시 Stage 2 진행 가능.

---

## Stage 2: Paper Trading (로컬 디버깅)

실시간 데이터 + 가상 체결로 에이전트 전체를 가동하여 동작을 확인한다. **실제 자금은 사용되지 않는다.**

### 필요 항목

| 항목 | 필수 여부 | 용도 |
|------|----------|------|
| Python 3.12+ | 필수 | 런타임 |
| Docker & Docker Compose | 필수 | TimescaleDB, Redis 실행 |
| 거래소 API 키 | 필수 | 실시간 시세 조회 (읽기 전용 가능) |
| LLM API 키 | 필수 (최소 1개) | 에이전트 분석/판단 (Claude 권장) |
| 인터넷 연결 | 필수 | 실시간 데이터 + LLM API 호출 |

### 설정 방법

```bash
# 1) .env 파일 생성
cp .env.example .env
```

`.env` 파일을 편집하여 아래 항목을 설정한다:

```bash
# ── 필수: 거래소 API (읽기 전용 권한이면 충분) ──
EXCHANGE_API_KEY=your_binance_api_key
EXCHANGE_API_SECRET=your_binance_api_secret

# ── 필수: LLM API (최소 1개) ──
CLAUDE_API_KEY=your_claude_api_key

# ── 시스템 제어: Paper Trading 모드 ──
SYSTEM_PAPER_TRADING_MODE=true    # 가상 체결 (실제 주문 X)
SYSTEM_TRADING_ENABLED=true       # 매매 로직 활성화

# ── 인프라 비밀번호 (기본값 사용 가능) ──
POSTGRES_PASSWORD=profit_dev_password
REDIS_PASSWORD=profit_dev_password
```

> `SYSTEM_PAPER_TRADING_MODE=true` 설정 시 ccxt sandbox 모드가 활성화되어 거래소에 실제 주문이 전송되지 않는다. `.env` 환경 변수가 `config/default.yml`보다 우선 적용된다.

### 실행 방법

```bash
# 가상환경 활성화
source .venv/bin/activate
pip install -e ".[dev]"

# 시스템 시작 (Docker 인프라 + uvicorn 자동 실행)
./scripts/start.sh dev
```

`start.sh dev`는 다음을 순서대로 수행한다:
1. `.env` 파일의 환경 변수를 로드
2. Docker로 TimescaleDB, Redis, Prometheus, Grafana 시작
3. 서비스 헬스체크 (Redis PONG, TimescaleDB pg_isready)
4. `uvicorn src.main:app --reload` 로 애플리케이션 시작

수동으로 실행하려면:

```bash
# 인프라만 Docker로
docker compose up -d timescaledb redis prometheus grafana

# .env 변수 로드 후 애플리케이션 시작
set -a && source .env && set +a
uvicorn src.main:app --host 0.0.0.0 --port 8000 --reload
```

### 동작 확인

시스템 시작 후 아래 항목을 확인한다:

```bash
# 헬스체크
curl http://localhost:8000/health

# 기대 응답:
# {"status":"ok","paper_trading":true,"trading_enabled":true,"agents":9,...}
```

| 확인 항목 | 방법 |
|----------|------|
| 시스템 상태 | `GET /health` — `paper_trading: true` 확인 |
| API 문서 | http://localhost:8000/docs (Swagger UI) |
| 에이전트 동작 | 로그에서 `Agents started: 9` 확인 |
| Redis 통신 | Grafana 또는 로그에서 Pub/Sub 이벤트 확인 |
| Grafana 대시보드 | http://localhost:3001 (admin / `GRAFANA_PASSWORD`) |
| Prometheus 메트릭 | http://localhost:8000/metrics |

### 시스템 중지

```bash
./scripts/stop.sh
# 또는
docker compose down
```

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
