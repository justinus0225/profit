# P.R.O.F.I.T. 배포 가이드

## 시스템 배포 및 프로덕션 전환 절차

---

# 1. Stage별 실행 환경 매핑

4-Stage Pipeline(ARCHITECTURE.md Section 13.3.3 참조)의 각 단계별 실행 환경을 정의한다.

## 1.1. 환경 매핑

| Stage | 환경 | 근거 |
|-------|------|------|
| **Stage 1**: CI 검증 + 백테스트 | **로컬** | CPU 집중 연산, 네트워크 불필요, 빠른 반복 실험 |
| **Stage 2**: Paper Trading (초기 디버깅) | **로컬** | 빠른 디버깅, 설정 조정, 에이전트 동작 확인 |
| **Stage 2**: Paper Trading (최종 검증) | **GCP** | 24/7 연속 2주+ 운영 필수, 프로덕션과 동일 환경 |
| **Stage 3**: 부분 실거래 (5-10%) | **GCP** | 실자금 운용, 24/7 안정성 필수 |
| **Stage 4**: 완전 가동 (100%) | **GCP** | 프로덕션 환경 |

## 1.2. 로컬 → GCP 전환 시점

Paper Trading 초기 디버깅이 완료된 후, 최종 2주 검증부터 GCP로 이전한다.

**로컬 환경의 한계**:
- 절전/재시작으로 인한 24/7 연속 운영 불가
- 네트워크 불안정 (가정용 ISP)
- Docker 메모리/디스크 I/O 특성이 프로덕션과 상이
- 로그/메트릭 수집 인프라 부재

**GCP 전환 이유**:
- 프로덕션과 동일한 VM 사양, 네트워크, Docker 환경
- SLA 99.95% 보장 업타임
- Prometheus/Grafana 모니터링 완전 가동
- 메모리 누수, 디스크 I/O 병목 등 프로덕션 전용 이슈 사전 발견

---

# 2. 로컬 개발 환경 설정

## 2.1. 사전 요구사항

| 항목 | 최소 사양 | 권장 사양 |
|------|-----------|-----------|
| OS | Ubuntu 22.04+ / macOS 13+ / WSL2 | Ubuntu 22.04 LTS |
| Python | 3.12+ | 3.12+ |
| Docker | 24.0+ | 최신 |
| Docker Compose | v2.20+ | 최신 |
| RAM | 8GB | 16GB+ |
| 디스크 | 20GB 여유 | 50GB+ SSD |

## 2.2. 로컬 실행

```bash
# 1. 저장소 클론
git clone git@github.com:justinus0225/profit.git
cd profit

# 2. 환경 변수 설정
cp .env.example .env.local
# .env.local 편집: API 키, DB 패스워드 등 입력

# 3. 컨테이너 빌드 및 실행
docker compose --env-file .env.local up -d --build

# 4. 서비스 상태 확인
docker compose ps
docker compose logs -f profit-core
```

## 2.3. 로컬 Paper Trading 모드

```bash
# .env.local 설정
SYSTEM_PAPER_TRADING_MODE=true
SYSTEM_TRADING_ENABLED=true
```

초기 Paper Trading은 로컬에서 1-3일간 실행하여:
- 에이전트 간 통신 정상 확인
- 합의 프로토콜 동작 확인
- 데이터 수집 파이프라인 안정성 확인
- 설정값 조정 반복

---

# 3. GCP 배포 가이드

## 3.1. VM 사양

| 항목 | 사양 | 근거 |
|------|------|------|
| 머신 유형 | `e2-standard-4` | vCPU 4, RAM 16GB. 에이전트 8개 + DB + Redis 동시 운영 |
| OS | Ubuntu 22.04 LTS | 장기 지원, Docker 공식 지원 |
| 부팅 디스크 | 100GB SSD (pd-balanced) | TimescaleDB 시계열 데이터 + 로그 저장 |
| 리전 | asia-northeast3 (서울) | 한국 거래소 접속 시 낮은 지연 |
| 네트워크 | Premium Tier | 안정적인 외부 API 통신 |

## 3.2. 월간 예상 비용

| 항목 | 월 비용 (USD) |
|------|---------------|
| e2-standard-4 (24/7) | ~$97 |
| 100GB SSD (pd-balanced) | ~$10 |
| 네트워크 이그레스 | ~$5 |
| 고정 외부 IP | ~$3 |
| **합계** | **~$115/월** |

> LLM API 비용은 별도. `llm.cost.daily_limit_usd` 기본값 $50/일 기준 월 ~$1,500 추가.

## 3.3. VM 인스턴스 생성

### 3.3.1. gcloud CLI 사용

```bash
# VM 인스턴스 생성
gcloud compute instances create profit-trading \
  --machine-type=e2-standard-4 \
  --image-family=ubuntu-2204-lts \
  --image-project=ubuntu-os-cloud \
  --boot-disk-size=100GB \
  --boot-disk-type=pd-balanced \
  --zone=asia-northeast3-a \
  --tags=profit-server

# 고정 외부 IP 할당
gcloud compute addresses create profit-ip --region=asia-northeast3
gcloud compute instances add-access-config profit-trading \
  --access-config-name="profit-static-ip" \
  --address=$(gcloud compute addresses describe profit-ip --region=asia-northeast3 --format='get(address)')
```

### 3.3.2. GCP 콘솔 사용

1. **Compute Engine > VM 인스턴스** 메뉴로 이동
2. **인스턴스 만들기** 클릭
3. 사양:
   - 이름: `profit-trading`
   - 리전: `asia-northeast3-a` (서울)
   - 머신 유형: `e2-standard-4`
   - 부팅 디스크: Ubuntu 22.04 LTS, SSD 100GB
   - 네트워킹: 고정 외부 IP 할당

## 3.4. VPC 방화벽 규칙 설정

**원칙**: 관리자 IP에서만 접근 허용, DB 포트는 내부망 전용.

```bash
# 관리자 SSH 접근 (포트 22)
gcloud compute firewall-rules create profit-allow-ssh \
  --direction=INGRESS \
  --action=ALLOW \
  --rules=tcp:22 \
  --source-ranges=<ADMIN_IP>/32 \
  --target-tags=profit-server

# Admin UI 접근 (포트 80/443)
gcloud compute firewall-rules create profit-allow-admin-ui \
  --direction=INGRESS \
  --action=ALLOW \
  --rules=tcp:80,tcp:443 \
  --source-ranges=<ADMIN_IP>/32 \
  --target-tags=profit-server

# Grafana 접근 (포트 3001)
gcloud compute firewall-rules create profit-allow-grafana \
  --direction=INGRESS \
  --action=ALLOW \
  --rules=tcp:3001 \
  --source-ranges=<ADMIN_IP>/32 \
  --target-tags=profit-server

# OpenClaw 접근 (포트 3000)
gcloud compute firewall-rules create profit-allow-openclaw \
  --direction=INGRESS \
  --action=ALLOW \
  --rules=tcp:3000 \
  --source-ranges=<ADMIN_IP>/32 \
  --target-tags=profit-server
```

**차단 확인** (DB/Redis 포트는 외부 비노출):

| 포트 | 서비스 | 외부 접근 |
|------|--------|-----------|
| 22 | SSH | 관리자 IP만 |
| 80/443 | Admin UI | 관리자 IP만 |
| 3000 | OpenClaw | 관리자 IP만 |
| 3001 | Grafana | 관리자 IP만 |
| 5432 | TimescaleDB | **차단** (내부 Docker 네트워크만) |
| 6379 | Redis | **차단** (내부 Docker 네트워크만) |
| 8000 | PROFIT Core API | **차단** (내부 Docker 네트워크만) |
| 9090 | Prometheus | **차단** (내부 Docker 네트워크만) |

## 3.5. 의존성 설치 및 Docker 구성

```bash
# SSH 접속
gcloud compute ssh profit-trading --zone=asia-northeast3-a

# 시스템 업데이트
sudo apt-get update && sudo apt-get upgrade -y

# 필수 패키지 설치
sudo apt-get install -y \
  apt-transport-https \
  ca-certificates \
  curl \
  software-properties-common \
  git \
  htop

# Docker Engine 설치
curl -fsSL https://get.docker.com -o get-docker.sh
sudo sh get-docker.sh
sudo usermod -aG docker $USER

# Docker Compose 확인 (Docker Engine에 포함)
docker compose version

# 재로그인 (docker 그룹 적용)
exit
gcloud compute ssh profit-trading --zone=asia-northeast3-a
```

## 3.6. 시스템 배포

```bash
# 1. 저장소 클론
git clone git@github.com:justinus0225/profit.git
cd profit

# 2. 환경 변수 설정
cp .env.example .env.production
# .env.production 편집: API 키, DB 패스워드, LLM API 키 입력

# 3. Paper Trading 모드로 최초 배포 (Stage 2 최종 검증)
# .env.production에서 SYSTEM_PAPER_TRADING_MODE=true 확인

# 4. 컨테이너 빌드 및 실행
docker compose --env-file .env.production up -d --build

# 5. 서비스 상태 확인
docker compose ps
docker compose logs --tail=50 profit-core

# 6. Grafana 대시보드 접속 확인
# 브라우저: http://<VM_EXTERNAL_IP>:3001

# 7. Admin UI 접속 확인
# 브라우저: http://<VM_EXTERNAL_IP>
```

---

# 4. 환경별 설정 파일

## 4.1. `.env.local` (로컬 개발/Paper Trading)

```env
# ── 시스템 ──
SYSTEM_PAPER_TRADING_MODE=true
SYSTEM_TRADING_ENABLED=true
SYSTEM_MAINTENANCE_MODE=false

# ── 거래소 API ──
EXCHANGE_API_KEY=<your_binance_api_key>
EXCHANGE_API_SECRET=<your_binance_api_secret>

# ── LLM API ──
CLAUDE_API_KEY=<your_anthropic_api_key>
GEMINI_API_KEY=<your_google_api_key>

# ── 데이터베이스 ──
POSTGRES_PASSWORD=<local_db_password>
REDIS_PASSWORD=<local_redis_password>

# ── 로깅 ──
LOG_LEVEL=debug

# ── 전략 진화 ──
EVOLUTION_GENERATION_ENABLED=false
EVOLUTION_MAX_STRATEGIES=50
```

## 4.2. `.env.paper` (GCP Paper Trading)

```env
# ── 시스템 ──
SYSTEM_PAPER_TRADING_MODE=true
SYSTEM_TRADING_ENABLED=true
SYSTEM_MAINTENANCE_MODE=false

# ── 거래소 API ──
EXCHANGE_API_KEY=<your_binance_api_key>
EXCHANGE_API_SECRET=<your_binance_api_secret>

# ── LLM API ──
CLAUDE_API_KEY=<your_anthropic_api_key>
GEMINI_API_KEY=<your_google_api_key>

# ── 데이터베이스 ──
POSTGRES_PASSWORD=<strong_db_password>
REDIS_PASSWORD=<strong_redis_password>

# ── 로깅 ──
LOG_LEVEL=warning

# ── 알림 ──
NOTIFICATION_CHANNEL=openclaw
NOTIFICATION_MIN_LEVEL=warning

# ── 전략 진화 ──
EVOLUTION_GENERATION_ENABLED=false
EVOLUTION_MAX_STRATEGIES=50
```

## 4.3. `.env.production` (GCP 실거래)

```env
# ── 시스템 ──
SYSTEM_PAPER_TRADING_MODE=false
SYSTEM_TRADING_ENABLED=true
SYSTEM_MAINTENANCE_MODE=false

# ── 거래소 API ──
EXCHANGE_API_KEY=<your_binance_api_key>
EXCHANGE_API_SECRET=<your_binance_api_secret>

# ── LLM API ──
CLAUDE_API_KEY=<your_anthropic_api_key>
GEMINI_API_KEY=<your_google_api_key>

# ── 데이터베이스 ──
POSTGRES_PASSWORD=<strong_db_password>
REDIS_PASSWORD=<strong_redis_password>

# ── 로깅 ──
LOG_LEVEL=warning

# ── 알림 ──
NOTIFICATION_CHANNEL=openclaw
NOTIFICATION_MIN_LEVEL=warning

# ── 전략 진화 ──
EVOLUTION_GENERATION_ENABLED=false
EVOLUTION_MAX_STRATEGIES=50
```

---

# 5. 보안 경화 (Security Hardening)

## 5.1. SSH 보안

```bash
# 패스워드 인증 비활성화 (키 기반 인증만 허용)
sudo sed -i 's/#PasswordAuthentication yes/PasswordAuthentication no/' /etc/ssh/sshd_config
sudo sed -i 's/PasswordAuthentication yes/PasswordAuthentication no/' /etc/ssh/sshd_config
sudo systemctl restart sshd

# fail2ban 설치 (SSH 브루트포스 방어)
sudo apt-get install -y fail2ban
sudo systemctl enable fail2ban
sudo systemctl start fail2ban
```

## 5.2. Docker 보안

| 항목 | 설정 |
|------|------|
| 사용자 네임스페이스 | `userns-remap` 활성화 (root 격리) |
| 리소스 제한 | 컨테이너별 CPU/메모리 limits 설정 |
| 네트워크 격리 | DB/Redis는 내부 Docker 네트워크에만 연결 |
| 이미지 서명 | Docker Content Trust 활성화 |

## 5.3. 크리덴셜 관리

ARCHITECTURE.md Section 13.3.7 [P7]에 정의된 3단계 보안 강화 적용:

| 단계 | 시점 | 방법 |
|------|------|------|
| **1단계** (즉시) | 초기 배포 | Docker Secrets. `.env` 대신 `docker-compose.yml`의 `secrets` 사용 |
| **2단계** (MVP 이후) | Paper Trading 안정화 후 | SOPS + Age 암호화. 설정 파일 Git 커밋 가능 |
| **3단계** (규모 확대) | 실거래 안정화 후 | HashiCorp Vault. 동적 시크릿, API 키 자동 로테이션 |

---

# 6. 운영 및 유지보수

## 6.1. 로그 관리

```bash
# Docker 로그 로테이션 설정 (/etc/docker/daemon.json)
{
  "log-driver": "json-file",
  "log-opts": {
    "max-size": "50m",
    "max-file": "5"
  }
}
```

## 6.2. 데이터베이스 백업

```bash
# TimescaleDB 일일 백업 cron job
# /etc/cron.d/profit-backup
0 3 * * * root docker exec timescaledb pg_dump -U profit profit_db | gzip > /backup/profit_$(date +\%Y\%m\%d).sql.gz

# 백업 보존: 최근 30일
0 4 * * * root find /backup -name "profit_*.sql.gz" -mtime +30 -delete
```

## 6.3. 모니터링 알림 설정

Grafana에서 다음 조건에 대한 알림 규칙을 설정:

| 조건 | 임계값 | 알림 |
|------|--------|------|
| CPU 사용률 | > 80% (5분 지속) | OpenClaw 경고 |
| 메모리 사용률 | > 85% | OpenClaw 경고 |
| 디스크 사용률 | > 80% | OpenClaw 경고 |
| 컨테이너 다운 | 1개 이상 | OpenClaw 긴급 |
| API 429 에러율 | > 1% | OpenClaw 경고 |
| 에이전트 미응답 | 60초 이상 | OpenClaw 긴급 |

---

# 7. 실거래 전환 체크리스트

## 7.1. Stage 2 → Stage 3 전환 (Paper Trading → 부분 실거래)

- [ ] Paper Trading 2주+ 연속 운영 완료
- [ ] Sharpe Ratio > 1.0, MDD < -20% 충족
- [ ] API 에러율 < 0.1%
- [ ] 메모리 누수 미발생
- [ ] 서킷 브레이커 비정상 발동 0회
- [ ] 에이전트 응답 지연 P99 < 500ms
- [ ] 모든 합의 프로토콜 정상 동작 확인
- [ ] Grafana 모니터링 대시보드 정상
- [ ] OpenClaw 알림 정상 수신
- [ ] 전략 레지스트리 빌트인 전략 4종 LIVE 등록 확인
- [ ] 시장 국면 분류 (RegimeClassifier) 정상 동작 확인
- [ ] 주간 WFO 최적화 실행 정상 완료 확인 (1회 이상)
- [ ] LLM 전략 생성 비활성화 확인 (`evolution.generation_enabled=false`)
- [ ] 관리자 수동 승인

## 7.2. Stage 3 → Stage 4 전환 (부분 실거래 → 완전 가동)

- [ ] 부분 실거래 2주+ 연속 운영 완료
- [ ] 서킷 브레이커 연속 발동 0회
- [ ] 슬리피지: 백테스트 대비 오차 < 0.3%
- [ ] TCA Implementation Shortfall 허용 범위 내
- [ ] 일일 LLM 비용 한도 내 운영 확인
- [ ] 전체 자금 대비 손실률 허용 범위 내
- [ ] SHADOW 전략 승격/강등 메커니즘 정상 동작 (해당 시)
- [ ] 관리자 수동 승인

---

# 8. 장애 대응 절차

## 8.1. 긴급 정지

```bash
# OpenClaw 명령
"매매 전체 중단"

# 또는 직접 실행
docker compose exec profit-core python -c "
import redis
r = redis.Redis()
r.set('config:system.trading_enabled', 'false')
r.publish('config:change', 'system.trading_enabled')
"

# 또는 컨테이너 중지
docker compose stop profit-core
```

## 8.2. 롤백

```bash
# 이전 버전으로 롤백
git checkout <previous-stable-tag>
docker compose --env-file .env.production up -d --build

# 설정값 롤백
# Admin UI > Settings > Change Log > 되돌리기
# 또는 OpenClaw: "설정 마지막 변경 되돌려줘"
```
