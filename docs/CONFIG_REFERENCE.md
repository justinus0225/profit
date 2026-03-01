# P.R.O.F.I.T. 설정 관리 레퍼런스

## 시스템 운영 및 거래에 사용되는 모든 설정값 정의

모든 설정값은 **Admin UI** 또는 **OpenClaw 메시지**를 통해 실시간 변경 가능하다.
변경 즉시 적용되며, 변경 이력은 감사 로그에 기록된다.

---

# 1. 설정 관리 아키텍처

## 1.1. 설정 저장 및 동기화

```
┌─────────────────────────────────────────────────────────────┐
│                    설정 관리 흐름                              │
│                                                              │
│  ┌──────────────┐     ┌──────────────┐                      │
│  │  Admin UI     │     │  OpenClaw    │                      │
│  │  (Web Form)   │     │  (자연어)     │                      │
│  └──────┬───────┘     └──────┬───────┘                      │
│         │                     │                              │
│         │  REST API           │  메시지 파싱                  │
│         ▼                     ▼                              │
│  ┌──────────────────────────────────────┐                    │
│  │       Orchestrator (설정 관리자)       │                    │
│  │                                       │                    │
│  │  1. 설정값 유효성 검증 (범위, 타입)     │                    │
│  │  2. 위험 설정 변경 시 확인 요청         │                    │
│  │  3. 변경 이벤트 발행                   │                    │
│  └──────────────┬────────────────────────┘                    │
│                 │                                             │
│         ┌───────▼───────┐                                    │
│         │     Redis      │  ← 설정값 캐시 (실시간 조회)        │
│         │   (Pub/Sub)    │  ← 변경 이벤트 브로드캐스트         │
│         └───────┬───────┘                                    │
│                 │                                             │
│    ┌────────────┼────────────┐                                │
│    ▼            ▼            ▼                                │
│ 퀀트 Agent  리스크 Agent  실행 Agent  ...                     │
│ (설정 갱신)  (설정 갱신)  (설정 갱신)                           │
│                                                              │
│         ┌───────────────┐                                    │
│         │  TimescaleDB   │  ← 설정 변경 이력 영구 저장         │
│         │  (Audit Log)   │  ← 누가, 언제, 무엇을, 왜 변경했는지│
│         └───────────────┘                                    │
└─────────────────────────────────────────────────────────────┘
```

## 1.2. 설정 변경 원칙

| 원칙 | 설명 |
|------|------|
| **즉시 반영** | 변경 즉시 Redis에 저장, Pub/Sub로 전 에이전트에 브로드캐스트 |
| **유효성 검증** | 범위 밖 값, 타입 오류, 논리적 모순(손절 > 목표가 등) 자동 거부 |
| **위험 변경 확인** | 보유금 비율 축소, 손실 한도 확대 등 위험한 변경은 2차 확인 요청 |
| **감사 로그** | 모든 변경은 변경자(Admin UI/OpenClaw), 시각, 이전값→새값 기록 |
| **롤백 가능** | 최근 50건의 설정 변경을 되돌릴 수 있음 |
| **기본값 복원** | 개별 항목 또는 카테고리 전체를 기본값으로 복원 가능 |

## 1.3. 위험 등급 분류

설정 변경의 위험도에 따라 3단계로 분류한다.

| 등급 | 변경 방식 | 예시 |
|------|-----------|------|
| **일반 (Normal)** | 즉시 적용, 알림 없음 | 스캔 주기 변경, 화이트리스트 추가 |
| **주의 (Caution)** | 적용 전 변경 영향 요약 표시 + 확인 | 손절 비율 변경, 시그널 임계값 변경 |
| **위험 (Critical)** | 2차 확인 + OpenClaw로 변경 알림 발송 | 보유금 비율 축소, 손실 한도 확대, 매매 전체 ON/OFF |

---

# 2. 전체 설정값 목록

## 2.1. 자금 관리 (Fund Management)

리스크 관리 에이전트가 참조하는 설정값.

| 키 | 설명 | 기본값 | 범위 | 위험등급 | 단위 |
|----|------|--------|------|----------|------|
| `fund.reserve_ratio` | 최소 보유금 비율. 어떤 상황에서도 매매에 사용하지 않는 안전 자금 | `0.30` | 0.10~0.50 | Critical | 비율 |
| `fund.max_single_position` | 단일 코인 최대 투자 비율 (가용 투자금 대비) | `0.20` | 0.05~0.40 | Caution | 비율 |
| `fund.max_concurrent_coins` | 동시 보유 가능 최대 코인 수 | `10` | 3~20 | Normal | 개 |
| `fund.dca_phases` | 분할 매수 횟수 | `3` | 1~5 | Normal | 회 |
| `fund.dca_phase1_ratio` | 1차 매수 비율 | `0.40` | 0.30~1.00 | Normal | 비율 |
| `fund.dca_phase2_trigger` | 2차 매수 트리거 (1차 대비 하락률) | `-0.02` | -0.01~-0.10 | Normal | 비율 |
| `fund.dca_phase3_trigger` | 3차 매수 트리거 (1차 대비 하락률) | `-0.05` | -0.02~-0.15 | Normal | 비율 |

### OpenClaw 변경 예시

```
사용자: "보유금 비율 25%로 변경해줘"

시스템: ⚠️ [Critical 설정 변경]
  fund.reserve_ratio: 0.30 → 0.25
  영향: 가용 투자금이 전체 자산의 70% → 75%로 증가합니다.
        최소 보유금이 줄어 위기 상황 시 안전 자금이 감소합니다.
  정말 변경하시겠습니까? (예/아니오)

사용자: "예"

시스템: ✅ fund.reserve_ratio 변경 완료
  0.30 → 0.25 (적용 시각: 2026-03-01 14:30:00 UTC)
  총 자산 1,000,000 USDT 기준:
  - 최소 보유금: 300,000 → 250,000 USDT
  - 가용 투자금: 700,000 → 750,000 USDT
```

---

## 2.2. 리스크 관리 (Risk Management)

리스크 관리 에이전트 및 매매 실행 에이전트가 참조하는 설정값.

| 키 | 설명 | 기본값 | 범위 | 위험등급 | 단위 |
|----|------|--------|------|----------|------|
| `risk.daily_loss_limit` | 일일 최대 손실률. 도달 시 당일 신규 매매 중단 | `-0.03` | -0.01~-0.10 | Critical | 비율 |
| `risk.total_loss_limit` | 총 최대 손실률. 도달 시 전체 매매 중단 + 관리자 알림 | `-0.10` | -0.05~-0.30 | Critical | 비율 |
| `risk.default_stop_loss` | 코인별 기본 손절률 | `-0.05` | -0.02~-0.15 | Caution | 비율 |
| `risk.trailing_stop` | 트레일링 스탑 비율 (고점 대비) | `0.03` | 0.01~0.10 | Caution | 비율 |
| `risk.max_consecutive_losses` | 연속 손실 허용 횟수. 초과 시 매매 일시 중단 | `5` | 3~10 | Caution | 회 |
| `risk.slippage_tolerance` | 최대 허용 슬리피지 | `0.005` | 0.001~0.02 | Normal | 비율 |
| `risk.circuit_breaker_price_spike` | 급변 감지 기준 (1분 내) | `0.10` | 0.05~0.20 | Caution | 비율 |
| `risk.circuit_breaker_api_failures` | API 연속 실패 허용 횟수 | `3` | 2~10 | Normal | 회 |

### 리스크 레벨 경계값

| 키 | 설명 | 기본값 | 범위 | 위험등급 |
|----|------|--------|------|----------|
| `risk.level_low_max` | Low 리스크 상한 | `30` | 20~40 | Caution |
| `risk.level_medium_max` | Medium 리스크 상한 | `60` | 40~70 | Caution |
| `risk.level_high_max` | High 리스크 상한 | `80` | 60~90 | Caution |
| `risk.utilization_low` | Low 리스크 시 가용금 사용 비율 | `1.00` | 0.80~1.00 | Normal |
| `risk.utilization_medium` | Medium 리스크 시 가용금 사용 비율 | `0.70` | 0.50~0.90 | Normal |
| `risk.utilization_high` | High 리스크 시 가용금 사용 비율 | `0.40` | 0.20~0.60 | Caution |

---

## 2.3. 코인 선별 (Universe Screening)

경제 분석 에이전트가 Watchlist 생성 시 참조하는 설정값.

| 키 | 설명 | 기본값 | 범위 | 위험등급 | 단위 |
|----|------|--------|------|----------|------|
| `screening.market_cap_rank` | 시가총액 순위 기준 (Top N) | `100` | 20~500 | Normal | 순위 |
| `screening.min_daily_volume` | 최소 일평균 거래량 | `10000000` | 1000000~100000000 | Normal | USD |
| `screening.min_fundamental_score` | Watchlist 편입 최소 펀더멘탈 스코어 | `40` | 20~60 | Normal | 점 |
| `screening.unlock_warning_days` | 토큰 언락 경고 기간 | `30` | 7~90 | Normal | 일 |
| `screening.unlock_warning_ratio` | 토큰 언락 경고 비율 (유통량 대비) | `0.05` | 0.02~0.15 | Normal | 비율 |
| `screening.blacklist` | 매매 제외 코인 목록 | `[]` | - | Normal | 목록 |
| `screening.whitelist` | 우선 감시 코인 목록 (Watchlist 갱신과 무관하게 항상 포함) | `[]` | - | Normal | 목록 |
| `screening.exchange` | 타겟 거래소 | `"binance"` | 지원 거래소 | Caution | 문자열 |

### OpenClaw 변경 예시

```
사용자: "블랙리스트에 LUNA 추가해"

시스템: ✅ screening.blacklist 변경 완료
  [] → ["LUNA/USDT"]
  LUNA/USDT가 Watchlist에서 제외됩니다.
  현재 LUNA 보유 포지션이 없습니다.

사용자: "화이트리스트에 BTC, ETH 추가"

시스템: ✅ screening.whitelist 변경 완료
  [] → ["BTC/USDT", "ETH/USDT"]
  이 코인들은 펀더멘탈 스코어와 무관하게 항상 Watchlist에 포함됩니다.
```

---

## 2.4. 시그널 및 전략 (Signal & Strategy)

퀀트 에이전트가 시그널 생성 시 참조하는 설정값.

| 키 | 설명 | 기본값 | 범위 | 위험등급 | 단위 |
|----|------|--------|------|----------|------|
| `signal.buy_threshold` | 매수 시그널 발동 기준 스코어 | `50` | 30~80 | Caution | 점 |
| `signal.sell_threshold` | 매도 시그널 발동 기준 스코어 | `-50` | -80~-30 | Caution | 점 |
| `signal.consensus_similarity_min` | 합의 시 최소 코사인 유사도 | `0.60` | 0.40~0.80 | Caution | 값 |
| `signal.consensus_quorum` | 합의 최소 찬성 수 (N-out-of-3) | `2` | 2~3 | Critical | 수 |

### 전략별 활성화

| 키 | 설명 | 기본값 | 위험등급 |
|----|------|--------|----------|
| `strategy.mean_reversion.enabled` | 평균 회귀 전략 활성화 | `true` | Caution |
| `strategy.trend_following.enabled` | 추세 추종 전략 활성화 | `true` | Caution |
| `strategy.momentum.enabled` | 모멘텀 전략 활성화 | `true` | Caution |
| `strategy.breakout.enabled` | 브레이크아웃 전략 활성화 | `true` | Caution |

### 전략별 세부 파라미터

| 키 | 설명 | 기본값 | 범위 | 위험등급 |
|----|------|--------|------|----------|
| `strategy.mean_reversion.rsi_oversold` | RSI 과매도 기준 | `30` | 15~40 | Normal |
| `strategy.mean_reversion.rsi_overbought` | RSI 과매수 기준 | `70` | 60~85 | Normal |
| `strategy.trend_following.ma_short` | 단기 이동평균 기간 | `20` | 5~50 | Normal |
| `strategy.trend_following.ma_long` | 장기 이동평균 기간 | `50` | 20~200 | Normal |
| `strategy.trend_following.adx_min` | ADX 최소 추세 강도 | `25` | 15~40 | Normal |
| `strategy.momentum.price_spike_threshold` | 급등 감지 기준 (5분) | `0.03` | 0.02~0.10 | Normal |
| `strategy.momentum.volume_spike_multiplier` | 거래량 급증 배수 | `5` | 3~10 | Normal |
| `strategy.breakout.lookback_days` | 채널 돌파 기준 기간 | `20` | 10~60 | Normal |

---

## 2.5. 포트폴리오 관리 (Portfolio Management)

포트폴리오 관리 에이전트가 참조하는 설정값.

| 키 | 설명 | 기본값 | 범위 | 위험등급 | 단위 |
|----|------|--------|------|----------|------|
| `portfolio.short_term_ratio` | 단기 포지션 목표 비중 | `0.25` | 0.10~0.40 | Normal | 비율 |
| `portfolio.mid_term_ratio` | 중기 포지션 목표 비중 | `0.45` | 0.30~0.60 | Normal | 비율 |
| `portfolio.long_term_ratio` | 장기 포지션 목표 비중 | `0.30` | 0.10~0.40 | Normal | 비율 |
| `portfolio.max_correlation` | 포트폴리오 내 코인 간 최대 상관계수 | `0.80` | 0.50~0.95 | Normal | 값 |
| `portfolio.short_term_max_days` | 단기 포지션 최대 보유일 | `7` | 1~14 | Normal | 일 |
| `portfolio.mid_term_max_days` | 중기 포지션 최대 보유일 | `28` | 7~60 | Normal | 일 |
| `portfolio.rebalance_time` | 일일 리밸런싱 시각 (UTC) | `"00:00"` | - | Normal | 시각 |

### 보유 기간 연장 조건

| 키 | 설명 | 기본값 | 범위 | 위험등급 |
|----|------|--------|------|----------|
| `portfolio.extend_min_pnl` | 연장 시 최소 수익률 (손실 중이면 연장 불가) | `0.00` | -0.02~0.05 | Normal |
| `portfolio.extend_min_fundamental` | 연장 시 최소 펀더멘탈 스코어 | `70` | 40~90 | Normal |
| `portfolio.extend_max_risk_level` | 연장 허용 최대 리스크 레벨 | `60` | 30~80 | Normal |

---

## 2.6. 스캔 주기 (Scan Schedule)

각 에이전트의 동작 주기를 제어하는 설정값.

| 키 | 설명 | 기본값 | 범위 | 위험등급 | 단위 |
|----|------|--------|------|----------|------|
| `schedule.quant_fast_scan` | 퀀트 빠른 스캔 주기 | `15` | 5~60 | Normal | 분 |
| `schedule.quant_deep_scan` | 퀀트 깊은 분석 주기 | `60` | 30~240 | Normal | 분 |
| `schedule.quant_strategy_eval` | 퀀트 전략 평가 주기 | `240` | 60~480 | Normal | 분 |
| `schedule.analyst_news` | 뉴스/SNS 크롤링 주기 | `60` | 15~240 | Normal | 분 |
| `schedule.analyst_macro` | 거시 환경 업데이트 주기 | `240` | 60~480 | Normal | 분 |
| `schedule.analyst_universe` | Watchlist 갱신 시각 (UTC) | `"00:00"` | - | Normal | 시각 |
| `schedule.risk_full_eval` | 리스크 스코어 전체 재산출 시각 (UTC) | `"00:00"` | - | Normal | 시각 |
| `schedule.portfolio_report` | 성과 리포트 발송 시각 (UTC) | `"09:00"` | - | Normal | 시각 |
| `schedule.risk_position_poll` | 포지션 손익 감시 간격 | `10` | 5~60 | Caution | 초 |
| `schedule.oms_reconciliation` | OMS 거래소 상태 동기화 주기 | `300` | 60~600 | Normal | 초 |
| `schedule.execution_order_poll` | 미체결 주문 상태 폴링 간격 | `30` | 10~120 | Normal | 초 |

---

## 2.7. 이벤트 트리거 (Event Triggers)

이벤트 기반 동작의 임계값을 제어하는 설정값.

| 키 | 설명 | 기본값 | 범위 | 위험등급 | 단위 |
|----|------|--------|------|----------|------|
| `event.price_spike_window` | 급등/급락 감지 시간 창 | `5` | 1~15 | Normal | 분 |
| `event.price_spike_threshold` | 급등/급락 감지 기준 | `0.03` | 0.02~0.10 | Normal | 비율 |
| `event.volume_spike_multiplier` | 대량 거래 감지 배수 (평균 대비) | `5` | 3~20 | Normal | 배 |
| `event.fear_greed_critical` | Fear & Greed 극단 공포 기준 | `20` | 10~30 | Caution | 점 |
| `event.fear_greed_extreme_greed` | Fear & Greed 극단 탐욕 기준 | `80` | 70~90 | Caution | 점 |

---

## 2.8. 실행 (Execution)

매매 실행 에이전트가 참조하는 설정값.

| 키 | 설명 | 기본값 | 범위 | 위험등급 | 단위 |
|----|------|--------|------|----------|------|
| `execution.default_order_type` | 기본 주문 유형 | `"limit"` | limit, market | Normal | 문자열 |
| `execution.limit_order_timeout` | 지정가 미체결 시 취소 시간 | `300` | 60~3600 | Normal | 초 |
| `execution.large_order_threshold` | 대량 주문 기준 (TWAP/VWAP 적용) | `50000` | 10000~500000 | Normal | USD |
| `execution.twap_intervals` | TWAP 분할 횟수 | `5` | 3~20 | Normal | 회 |
| `execution.twap_interval_seconds` | TWAP 분할 간격 | `60` | 30~300 | Normal | 초 |

---

## 2.9. 알림 (Notifications)

관리자 알림 관련 설정값.

| 키 | 설명 | 기본값 | 범위 | 위험등급 | 단위 |
|----|------|--------|------|----------|------|
| `notification.channel` | 알림 발송 채널 | `"openclaw"` | openclaw, none | Normal | 문자열 |
| `notification.min_level` | 최소 알림 레벨 | `"warning"` | info, warning, critical | Normal | 문자열 |
| `notification.events.trade_executed` | 매매 체결 시 알림 | `true` | true/false | Normal | bool |
| `notification.events.daily_report` | 일일 성과 리포트 발송 | `true` | true/false | Normal | bool |
| `notification.events.risk_level_change` | 리스크 레벨 변경 시 알림 | `true` | true/false | Normal | bool |
| `notification.events.circuit_breaker` | 서킷 브레이커 발동 시 알림 | `true` | true/false | Normal | bool |
| `notification.events.stop_loss_triggered` | 손절 발동 시 알림 | `true` | true/false | Normal | bool |
| `notification.events.config_changed` | 설정 변경 시 알림 | `true` | true/false | Normal | bool |
| `notification.events.system_error` | 시스템 오류 발생 시 알림 | `true` | true/false | Normal | bool |

---

## 2.10. 시스템 전체 제어 (System Control)

| 키 | 설명 | 기본값 | 위험등급 |
|----|------|--------|----------|
| `system.trading_enabled` | 매매 전체 ON/OFF | `true` | Critical |
| `system.paper_trading_mode` | Paper Trading 모드 (가상 체결) | `false` | Critical |
| `system.maintenance_mode` | 유지보수 모드 (데이터 수집만 동작) | `false` | Caution |

---

## 2.11. LLM 프로바이더 (LLM Provider)

에이전트의 분석/판단/자연어 처리에 사용되는 LLM 설정값.
**Claude**와 **Gemini**를 주요 프로바이더로 지원하며, OpenAI 등 다른 LLM도 확장 가능하다.

### 기본 설정

| 키 | 설명 | 기본값 | 선택지/범위 | 위험등급 |
|----|------|--------|-------------|----------|
| `llm.default_provider` | 기본 LLM 프로바이더 | `claude` | claude, gemini, openai | Caution |
| `llm.default_model` | 기본 모델 ID | `claude-sonnet-4-6` | 프로바이더별 모델 목록 참조 | Caution |
| `llm.fallback_provider` | 폴백 프로바이더 | `gemini` | claude, gemini, openai | Normal |
| `llm.fallback_model` | 폴백 모델 ID | `gemini-2.5-pro` | 프로바이더별 모델 목록 참조 | Normal |
| `llm.temperature` | 응답 온도 (창의성 조절) | `0.3` | 0.0~1.0 | Normal |
| `llm.max_tokens` | 최대 출력 토큰 수 | `4096` | 256~32768 | Normal |

### 프로바이더별 모델 ID

| 프로바이더 | 모델 ID | 등급 | 설명 |
|-----------|---------|------|------|
| Claude | `claude-opus-4-6` | Premium | 최고 성능, 복잡한 분석/판단 |
| Claude | `claude-sonnet-4-6` | Standard | 균형 잡힌 성능/비용 |
| Claude | `claude-haiku-4-5` | Light | 경량, 단순 분류/검증 |
| Gemini | `gemini-2.5-pro` | Premium | 대량 컨텍스트, 복잡한 분석 |
| Gemini | `gemini-2.5-flash` | Standard | 빠른 응답, 일반 분석 |
| Gemini | `gemini-2.0-flash-lite` | Light | 경량, 실시간 처리 |
| OpenAI | `gpt-4o` | Premium | 대안 프로바이더 |
| OpenAI | `gpt-4o-mini` | Light | 대안 프로바이더 |

### 재시도 및 폴백

| 키 | 설명 | 기본값 | 범위 | 위험등급 |
|----|------|--------|------|----------|
| `llm.retry.max_retries` | 동일 프로바이더 내 최대 재시도 | `3` | 1~10 | Normal |
| `llm.retry.backoff` | 재시도 간격 방식 | `exponential` | exponential, linear | Normal |
| `llm.retry.initial_delay_seconds` | 첫 재시도 대기 시간 (초) | `1` | 0.5~5.0 | Normal |
| `llm.fallback.consecutive_failures` | 폴백 전환 기준 연속 실패 수 | `5` | 3~20 | Normal |
| `llm.fallback.recovery_check_minutes` | 주 프로바이더 정상화 확인 주기 (분) | `5` | 1~30 | Normal |
| `llm.fallback.auto_recover` | 정상화 시 자동 복귀 여부 | `true` | true/false | Normal |

### 에이전트별 오버라이드

개별 에이전트에 기본값과 다른 프로바이더/모델을 지정할 수 있다.
미설정 에이전트는 `llm.default_provider` / `llm.default_model`을 사용한다.

| 키 | 설명 | 기본값 | 위험등급 |
|----|------|--------|----------|
| `llm.agent_overrides.<agent>.provider` | 에이전트별 프로바이더 | (default) | Caution |
| `llm.agent_overrides.<agent>.model` | 에이전트별 모델 ID | (default) | Caution |

**`<agent>` 값**: `orchestrator`, `analyst_macro`, `analyst_micro`, `analyst_sentiment`, `quant`, `risk`, `portfolio`, `executor`, `openclaw`

### 비용 제한

| 키 | 설명 | 기본값 | 범위 | 위험등급 |
|----|------|--------|------|----------|
| `llm.cost.daily_limit_usd` | 일일 LLM 비용 한도 (USD) | `50.0` | 5~500 | Caution |
| `llm.cost.alert_threshold` | 한도 대비 알림 발송 비율 | `0.80` | 0.50~0.95 | Normal |

### OpenClaw 변경 예시

```
사용자: "LLM을 Gemini로 바꿔줘"

시스템: ⚠️ [Caution 설정 변경]
  llm.default_provider: claude → gemini
  llm.default_model: claude-sonnet-4-6 → gemini-2.5-pro
  영향: 개별 설정이 없는 전체 에이전트의 LLM이 Gemini로 변경됩니다.
        에이전트별 개별 설정(orchestrator 등)은 유지됩니다.
  정말 변경하시겠습니까? (예/아니오)

사용자: "예"

시스템: ✅ LLM 프로바이더 변경 완료
  claude → gemini (적용 시각: 2026-03-01 14:30:00 UTC)
  변경된 에이전트: analyst_macro, analyst_micro, quant, risk, portfolio, openclaw
  유지된 에이전트: orchestrator (claude-opus-4-6), analyst_sentiment (claude-haiku-4-5)
```

```
사용자: "퀀트 에이전트 LLM을 Claude Opus로 설정해줘"

시스템: ⚠️ [Caution 설정 변경]
  llm.agent_overrides.quant.provider: (default) → claude
  llm.agent_overrides.quant.model: (default) → claude-opus-4-6
  영향: 퀀트 에이전트가 Claude Opus 4.6을 사용합니다 (비용 증가).
  정말 변경하시겠습니까? (예/아니오)

사용자: "예"

시스템: ✅ 퀀트 에이전트 LLM 변경 완료
  (default) → claude-opus-4-6
```

```
사용자: "현재 LLM 설정 보여줘"

시스템: 📊 LLM 프로바이더 현황
  ─────────────────────────────────
  ■ 기본 설정
    프로바이더: Claude (Anthropic)
    모델: claude-sonnet-4-6
    폴백: Gemini gemini-2.5-pro

  ■ 에이전트별 현재 설정
  ┌──────────────┬──────────┬───────────────────┬───────┐
  │ 에이전트      │ 프로바이더 │ 모델               │ 비고   │
  ├──────────────┼──────────┼───────────────────┼───────┤
  │ 오케스트레이터 │ Claude   │ claude-opus-4-6   │ 개별   │
  │ 경제분석(거시) │ Claude   │ claude-sonnet-4-6 │ 기본값 │
  │ 경제분석(미시) │ Claude   │ claude-sonnet-4-6 │ 기본값 │
  │ 경제분석(감성) │ Claude   │ claude-haiku-4-5  │ 개별   │
  │ 퀀트          │ Claude   │ claude-opus-4-6   │ 개별   │
  │ 리스크 관리    │ Claude   │ claude-sonnet-4-6 │ 기본값 │
  │ 포트폴리오     │ Claude   │ claude-sonnet-4-6 │ 기본값 │
  │ 매매 실행      │ Claude   │ claude-haiku-4-5  │ 개별   │
  │ OpenClaw      │ Claude   │ claude-sonnet-4-6 │ 기본값 │
  └──────────────┴──────────┴───────────────────┴───────┘

  ■ 폴백 체인: Claude → Gemini (gemini-2.5-pro)
  ■ 오늘 사용량: 1,247 호출 / $12.34 (한도: $50.00, 24.7%)
```

---

## 2.12. 거래소 API Rate Limiting (`exchange.rate_limit.*`)

다수의 에이전트가 거래소 API에 병렬 호출하는 것을 중앙에서 제어하는 설정값.
Token Bucket 알고리즘 기반으로 API 호출 예산을 관리한다.

| 키 | 설명 | 기본값 | 범위 | 위험등급 |
|----|------|--------|------|----------|
| `exchange.rate_limit.enabled` | 중앙 Rate Limiting 활성화 | `true` | true/false | Caution |
| `exchange.rate_limit.max_weight_per_minute` | 분당 최대 API 가중치 (Binance 한도: 1200) | `1000` | 500~1200 | Caution |
| `exchange.rate_limit.max_orders_per_second` | 초당 최대 주문 전송 수 (Binance 한도: 10) | `8` | 1~10 | Caution |
| `exchange.rate_limit.agent_priority.executor` | 매매 실행 에이전트 우선순위 (높을수록 우선) | `10` | 1~10 | Normal |
| `exchange.rate_limit.agent_priority.oms` | OMS 동기화 우선순위 | `5` | 1~10 | Normal |
| `exchange.rate_limit.agent_priority.quant` | 퀀트 에이전트 우선순위 | `3` | 1~10 | Normal |
| `exchange.rate_limit.agent_priority.data_engineer` | 데이터 수집 우선순위 | `3` | 1~10 | Normal |
| `exchange.rate_limit.backoff_max_retries` | 429 에러 수신 시 최대 재시도 횟수 | `5` | 1~10 | Normal |
| `exchange.rate_limit.backpressure_wait_max_seconds` | 토큰 부족 시 최대 대기 시간 (초) | `30` | 5~120 | Normal |

### OpenClaw 변경 예시

```
사용자: "API 속도 제한 낮춰줘"

시스템: ⚠️ [Caution 설정 변경]
  exchange.rate_limit.max_weight_per_minute: 1000 → 800
  영향: API 호출 예산이 줄어 데이터 수집 간격이 늘어날 수 있습니다.
  정말 변경하시겠습니까? (예/아니오)
```

---

## 2.13. 동시성 제어 (`concurrency.*`)

다수의 에이전트가 동일한 공유 자원(잔고, 포지션)에 동시 접근할 때
경쟁 상태(Race Condition)를 방지하는 분산 락(Distributed Lock) 설정값.

| 키 | 설명 | 기본값 | 범위 | 위험등급 |
|----|------|--------|------|----------|
| `concurrency.lock_backend` | 분산 락 백엔드 | `"redis"` | redis | Normal |
| `concurrency.order_lock_ttl_seconds` | 주문 배치 락 TTL (초). 심볼별 동시 주문 방지 | `5` | 2~30 | Caution |
| `concurrency.balance_lock_ttl_seconds` | 잔고 조회 락 TTL (초). 잔고 읽기→주문 원자적 처리 | `10` | 5~30 | Caution |
| `concurrency.lock_retry_attempts` | 락 획득 실패 시 재시도 횟수 | `3` | 1~10 | Normal |
| `concurrency.lock_retry_delay_ms` | 락 재시도 간격 (밀리초) | `100` | 50~1000 | Normal |

## 2.14. 데이터 품질 (`data_quality.*`)

거래소에서 수집한 데이터의 이상치(스파이크, 결측, 지연)를 탐지하고
자동 힐링(보간/대체)하여 정제된 데이터만 지표 계산에 유입시키는 설정값.

| 키 | 설명 | 기본값 | 범위 | 위험등급 |
|----|------|--------|------|----------|
| `data_quality.zscore_threshold` | Z-Score 이상치 판정 임계값. \|z\| > threshold 시 이상치 | `4.0` | 2.0~10.0 | Caution |
| `data_quality.iqr_multiplier` | IQR 기반 이상치 판정 배수. Q1 - k×IQR ~ Q3 + k×IQR | `3.0` | 1.5~5.0 | Caution |
| `data_quality.window_size` | 이상치 탐지 슬라이딩 윈도우 크기 (데이터 포인트 수) | `100` | 20~500 | Normal |
| `data_quality.healing_method` | 기본 힐링 방법 | `"linear_interpolation"` | linear_interpolation, forward_fill, ma_replacement | Normal |
| `data_quality.anomaly_halt_ratio` | 이상치 비율 이 값 초과 시 해당 심볼 수집 자동 중단 | `0.30` | 0.10~0.80 | Caution |
| `data_quality.anomaly_halt_window_minutes` | 이상치 비율 측정 윈도우 (분) | `10` | 5~60 | Normal |
| `data_quality.quarantine_enabled` | 이상치 원본을 격리 테이블에 보관 여부 | `true` | true/false | Normal |

---

## 2.15. LLM 메모리 (`llm_memory.*`)

에이전트의 단기/장기 메모리 관리 및 프롬프트 조합 시 토큰 제한 설정.
Section 10.7(ARCHITECTURE.md) 참조. 최상위 설정 섹션으로 `llm.*` 하위가 아닌 `llm_memory.*`로 독립 관리된다.

| 키 | 설명 | 기본값 | 범위 | 위험등급 |
|----|------|--------|------|----------|
| `llm_memory.short_term_ttl_hours` | 단기 메모리(Redis) TTL (시간) | `24` | 1~168 | Normal |
| `llm_memory.short_term_max_entries` | 에이전트당 단기 메모리 최대 항목 수 | `50` | 10~200 | Normal |
| `llm_memory.rag_enabled` | RAG (장기 메모리 검색) 활성화 여부 | `true` | true/false | Caution |
| `llm_memory.rag_top_k` | RAG 검색 시 반환할 최대 결과 수 | `5` | 1~20 | Normal |
| `llm_memory.rag_similarity_threshold` | RAG 유사도 최소 임계값 (코사인 유사도) | `0.70` | 0.50~0.95 | Normal |
| `llm_memory.compression_enabled` | 프롬프트 초과 시 자동 압축 활성화 | `true` | true/false | Caution |
| `llm_memory.compression_model` | 압축(요약)에 사용할 경량 모델 | `"claude-haiku-4-5"` | 경량 모델 ID | Normal |
| `llm_memory.embedding_dimension` | 임베딩 벡터 차원 수 (pgvector) | `768` | 256~1536 | Caution |

---

## 2.16. 부트 시퀀스 (`boot.*`)

시스템 콜드 스타트 시 6단계 부트 시퀀스의 동작을 제어하는 설정값.
P12(ARCHITECTURE.md) 및 TRADING_FLOW.md Section 9.1 참조.

| 키 | 설명 | 기본값 | 범위 | 위험등급 |
|----|------|--------|------|----------|
| `boot.candle_backfill_multiplier` | 지표별 최소 필요 캔들의 N배만큼 백필 | `1.5` | 1.0~5.0 | Caution |
| `boot.warmup_timeout_minutes` | 전체 워밍업 타임아웃 (분). 초과 시 알림 | `30` | 10~120 | Caution |
| `boot.partial_activation` | 준비된 지표의 전략만 선 활성화 허용 | `true` | true/false | Caution |
| `boot.auto_enable_trading` | 워밍업 완료 후 trading_enabled 자동 활성화 | `true` | true/false | Critical |
| `boot.infra_retry_attempts` | Phase 0 인프라 점검 재시도 횟수 | `3` | 1~10 | Normal |
| `boot.infra_retry_delay_seconds` | Phase 0 인프라 점검 재시도 간격 (초) | `5` | 1~30 | Normal |

---

## 2.17. DB 커넥션 풀링 (`db.pool.*`)

PgBouncer 및 SQLAlchemy 커넥션 풀 설정.
P13(ARCHITECTURE.md) 참조.

| 키 | 설명 | 기본값 | 범위 | 위험등급 |
|----|------|--------|------|----------|
| `db.pool.pgbouncer_host` | PgBouncer 호스트 | `"pgbouncer"` | 문자열 | Normal |
| `db.pool.pgbouncer_port` | PgBouncer 포트 | `6432` | 1024~65535 | Normal |
| `db.pool.pool_mode` | PgBouncer 풀링 모드 | `"transaction"` | session, transaction, statement | Caution |
| `db.pool.default_pool_size` | PgBouncer 기본 DB 연결 풀 크기 | `20` | 5~100 | Caution |
| `db.pool.max_client_conn` | PgBouncer 최대 클라이언트 연결 수 | `200` | 50~500 | Caution |
| `db.pool.reserve_pool_size` | PgBouncer 예비 연결 풀 크기 | `5` | 0~20 | Normal |
| `db.pool.sqlalchemy_pool_size` | SQLAlchemy 기본 풀 크기 (에이전트당) | `2` | 1~10 | Caution |
| `db.pool.sqlalchemy_max_overflow` | SQLAlchemy 최대 오버플로우 연결 수 | `3` | 0~20 | Caution |
| `db.pool.sqlalchemy_pool_timeout` | SQLAlchemy 풀 연결 대기 타임아웃 (초) | `30` | 5~120 | Normal |
| `db.pool.sqlalchemy_pool_recycle` | SQLAlchemy 연결 재활용 주기 (초) | `3600` | 300~7200 | Normal |
| `db.pool.postgres_max_connections` | PostgreSQL max_connections (PgBouncer 경유) | `50` | 20~200 | Caution |
| `db.pool.health_check_interval` | 커넥션 풀 헬스체크 주기 (초) | `30` | 10~300 | Normal |

---

# 3. OpenClaw 설정 변경 명령어

## 3.1. 자연어 명령 → 설정 매핑

관리자는 자연어로 설정을 변경할 수 있다. 오케스트레이터가 의도를 파싱하여 해당 설정 키에 매핑한다.

### 자금 관리

| 자연어 명령 | 매핑 키 | 예시 응답 |
|-------------|---------|-----------|
| "보유금 비율 25%로 바꿔" | `fund.reserve_ratio = 0.25` | ⚠️ Critical 변경, 확인 요청 |
| "한 코인 최대 15%까지만 투자" | `fund.max_single_position = 0.15` | 변경 완료 |
| "분할 매수 2번으로 줄여" | `fund.dca_phases = 2` | 변경 완료 |
| "동시 보유 코인 5개로 제한" | `fund.max_concurrent_coins = 5` | 변경 완료 |

### 리스크 관리

| 자연어 명령 | 매핑 키 | 예시 응답 |
|-------------|---------|-----------|
| "일일 손실 한도 5%로 변경" | `risk.daily_loss_limit = -0.05` | ⚠️ Critical 변경, 확인 요청 |
| "손절 비율 3%로 줄여" | `risk.default_stop_loss = -0.03` | 변경 완료 + 기존 포지션 반영 여부 확인 |
| "트레일링 스탑 5%로 변경" | `risk.trailing_stop = 0.05` | 변경 완료 + 기존 포지션 반영 여부 확인 |

### 전략 관리

| 자연어 명령 | 매핑 키 | 예시 응답 |
|-------------|---------|-----------|
| "모멘텀 전략 꺼줘" | `strategy.momentum.enabled = false` | 변경 완료. 모멘텀 시그널 비활성화됨 |
| "RSI 과매도 기준 25로 변경" | `strategy.mean_reversion.rsi_oversold = 25` | 변경 완료 |
| "시그널 임계값 60으로 올려" | `signal.buy_threshold = 60` | 변경 완료. 더 강한 시그널만 매수 실행됨 |

### 코인 선별

| 자연어 명령 | 매핑 키 | 예시 응답 |
|-------------|---------|-----------|
| "LUNA 블랙리스트에 추가" | `screening.blacklist += "LUNA/USDT"` | 변경 완료. LUNA 매매 제외됨 |
| "DOGE 블랙리스트에서 제거" | `screening.blacklist -= "DOGE/USDT"` | 변경 완료 |
| "시가총액 Top 50 이내만" | `screening.market_cap_rank = 50` | 변경 완료 |
| "최소 거래량 5천만 달러로" | `screening.min_daily_volume = 50000000` | 변경 완료 |

### 스캔 주기

| 자연어 명령 | 매핑 키 | 예시 응답 |
|-------------|---------|-----------|
| "스캔 주기 5분으로 줄여" | `schedule.quant_fast_scan = 5` | 변경 완료. API 호출량 증가 주의 |
| "뉴스 크롤링 30분마다" | `schedule.analyst_news = 30` | 변경 완료 |

### 시스템 제어

| 자연어 명령 | 매핑 키 | 예시 응답 |
|-------------|---------|-----------|
| "매매 전체 중단" | `system.trading_enabled = false` | ⚠️ Critical. 전체 매매가 즉시 중단됩니다. 확인? |
| "매매 재개" | `system.trading_enabled = true` | ⚠️ Critical. 매매를 재개합니다. 확인? |
| "페이퍼 트레이딩 모드로 전환" | `system.paper_trading_mode = true` | ⚠️ Critical. 실거래 중단, 가상 체결 모드로 전환. 확인? |

### LLM 프로바이더

| 자연어 명령 | 매핑 키 | 예시 응답 |
|-------------|---------|-----------|
| "LLM을 Gemini로 변경해줘" | `llm.default_provider = gemini` | ⚠️ Caution. 전체 기본 프로바이더 변경, 확인 요청 |
| "LLM Claude Opus로 바꿔" | `llm.default_model = claude-opus-4-6` | ⚠️ Caution. 기본 모델 변경 (비용 증가 안내) |
| "경제 분석은 Gemini로" | `llm.agent_overrides.analyst_macro.provider = gemini` | ⚠️ Caution 변경, 확인 요청 |
| "퀀트 에이전트 모델 Claude Opus로" | `llm.agent_overrides.quant.model = claude-opus-4-6` | ⚠️ Caution 변경, 확인 요청 |
| "LLM 비용 한도 100달러로 올려" | `llm.cost.daily_limit_usd = 100` | 변경 완료 |
| "현재 LLM 설정 보여줘" | 조회 | LLM 현황 테이블 출력 |
| "LLM 사용량 보여줘" | 조회 | 프로바이더별 호출 수/비용 출력 |

### 조회 명령

| 자연어 명령 | 동작 |
|-------------|------|
| "현재 설정값 보여줘" | 전체 설정값 테이블 출력 |
| "리스크 관련 설정 보여줘" | `risk.*` 카테고리 설정만 출력 |
| "설정 변경 이력 보여줘" | 최근 20건의 설정 변경 감사 로그 출력 |
| "기본값으로 복원해" | 전체 또는 지정 카테고리 기본값 복원 (확인 요청) |

## 3.2. 복합 명령 처리

```
사용자: "시장 상황이 불안정하니까 보수적으로 설정해줘"

시스템: 보수적 프리셋을 적용합니다. 다음 설정이 변경됩니다:

  ┌───────────────────────────────────┬──────────┬──────────┐
  │ 설정                              │ 현재값    │ 변경값    │
  ├───────────────────────────────────┼──────────┼──────────┤
  │ fund.reserve_ratio                │ 0.30     │ 0.40     │
  │ fund.max_single_position          │ 0.20     │ 0.10     │
  │ risk.default_stop_loss            │ -0.05    │ -0.03    │
  │ risk.trailing_stop                │ 0.03     │ 0.02     │
  │ signal.buy_threshold              │ 50       │ 70       │
  │ signal.consensus_similarity_min   │ 0.60     │ 0.75     │
  │ fund.max_concurrent_coins         │ 10       │ 5        │
  └───────────────────────────────────┴──────────┴──────────┘

  ⚠️ Critical 설정 변경 포함. 적용하시겠습니까? (예/아니오)
```

---

# 4. Admin UI 설정 페이지

## 4.1. 설정 페이지 구성

```
┌──────────────────────────────────────────────────────────┐
│  P.R.O.F.I.T. Settings                        [Save All] │
├──────────────────────────────────────────────────────────┤
│                                                          │
│  ┌─────────┐ ┌──────────┐ ┌──────────┐ ┌──────────────┐│
│  │Fund Mgmt│ │Risk Mgmt │ │Strategies│ │Coin Screening││
│  └─────────┘ └──────────┘ └──────────┘ └──────────────┘│
│  ┌──────────┐ ┌──────────┐ ┌──────────┐ ┌────────────┐ │
│  │Portfolio │ │Schedule  │ │Events    │ │Execution   │ │
│  └──────────┘ └──────────┘ └──────────┘ └────────────┘ │
│  ┌──────────┐ ┌──────────┐ ┌──────────┐ ┌────────────┐ │
│  │Alerts    │ │System    │ │Presets   │ │LLM Provider│ │
│  └──────────┘ └──────────┘ └──────────┘ └────────────┘ │
│                                                          │
│  ── Fund Management ─────────────────────────────────── │
│                                                          │
│  Reserve Ratio          [====|=======] 30%    (10~50%)  │
│  Max Single Position    [==|=========] 20%    (5~40%)   │
│  Max Concurrent Coins   [10 ▼]                (3~20)    │
│  DCA Phases             [3  ▼]                (1~5)     │
│                                                          │
│  ── Blacklist / Whitelist ───────────────────────────── │
│                                                          │
│  Blacklist:  [                          ] [+ Add]       │
│  Whitelist:  [BTC/USDT] [ETH/USDT]       [+ Add]       │
│                                                          │
│  ── Change Log ──────────────────────────────────────── │
│  │ 14:30 │ Admin UI │ fund.reserve_ratio │ 0.30→0.25  │ │
│  │ 13:15 │ OpenClaw │ screening.blacklist│ +LUNA      │ │
│  │ 09:00 │ System   │ risk.level         │ Med→High   │ │
│                                                          │
│                          [Reset to Defaults] [Save All]  │
└──────────────────────────────────────────────────────────┘
```

## 4.2. 프리셋 (Presets)

자주 사용하는 설정 조합을 프리셋으로 저장/불러오기 가능.

| 프리셋 | 특성 | 변경 설정 |
|--------|------|-----------|
| **Conservative (보수적)** | 안전 우선, 적은 매매 | 보유금 40%, 단일 한도 10%, 손절 -3%, 시그널 임계 70 |
| **Balanced (균형)** | 기본 설정 | 모든 값 기본값 |
| **Aggressive (공격적)** | 수익 추구, 높은 리스크 | 보유금 20%, 단일 한도 30%, 손절 -7%, 시그널 임계 40 |
| **Crisis (위기)** | 최소 리스크 | 보유금 50%, 매매 빈도 최소, 코인 3개 이하, 손절 -2% |
| **Custom (사용자 정의)** | 관리자가 직접 저장 | 원하는 조합 저장/불러오기 |

---

# 5. 설정 변경 감사 로그

모든 설정 변경은 아래 형식으로 TimescaleDB에 영구 기록된다.

```json
{
  "id": "CFG-20260301-001",
  "timestamp": "2026-03-01T14:30:00Z",
  "source": "openclaw",
  "user": "admin",
  "key": "fund.reserve_ratio",
  "old_value": 0.30,
  "new_value": 0.25,
  "risk_level": "critical",
  "confirmed": true,
  "reason": "관리자 수동 변경: 보유금 비율 25%로 변경해줘",
  "impact": "가용 투자금 700,000 → 750,000 USDT (+50,000)"
}
```

OpenClaw 조회:
```
사용자: "오늘 설정 변경 이력 보여줘"

시스템: 📋 2026-03-01 설정 변경 이력 (3건)

  ┌──────────┬───────────┬──────────────────────┬───────────────┐
  │ 시각      │ 변경 경로  │ 설정                  │ 변경 내용      │
  ├──────────┼───────────┼──────────────────────┼───────────────┤
  │ 14:30:00 │ OpenClaw  │ fund.reserve_ratio   │ 0.30 → 0.25  │
  │ 13:15:22 │ OpenClaw  │ screening.blacklist  │ + LUNA/USDT   │
  │ 10:05:11 │ Admin UI  │ risk.trailing_stop   │ 0.03 → 0.05  │
  └──────────┴───────────┴──────────────────────┴───────────────┘
```

---

# 6. 설정값 요약 (전체 카테고리 × 항목 수)

| 카테고리 | 항목 수 | 주요 설정 |
|----------|---------|-----------|
| 자금 관리 (`fund.*`) | 7 | 보유금 비율, 단일 한도, 분할 매수 |
| 리스크 관리 (`risk.*`) | 14 | 손실 한도, 손절, 트레일링, 리스크 레벨 경계 |
| 코인 선별 (`screening.*`) | 8 | 시총 기준, 거래량, 블랙/화이트리스트 |
| 시그널/전략 (`signal.*`, `strategy.*`) | 15 | 임계값, 전략 ON/OFF, 지표 파라미터 |
| 포트폴리오 (`portfolio.*`) | 10 | 보유 비중, 최대 보유일, 연장 조건 |
| 스캔 주기 (`schedule.*`) | 11 | 각 에이전트 동작 주기 |
| 이벤트 트리거 (`event.*`) | 5 | 급등/급락, 거래량, 공포 지수 기준 |
| 실행 (`execution.*`) | 5 | 주문 유형, TWAP 설정 |
| 알림 (`notification.*`) | 7 | 채널, 알림 레벨, 항목별 ON/OFF |
| 시스템 (`system.*`) | 3 | 매매 ON/OFF, 페이퍼 트레이딩, 유지보수 |
| LLM 프로바이더 (`llm.*`) | 14 | 프로바이더, 모델, 폴백, 에이전트별 오버라이드, 비용 한도 |
| API Rate Limiting (`exchange.rate_limit.*`) | 9 | API 가중치 예산, 에이전트 우선순위, 백프레셔 |
| 동시성 제어 (`concurrency.*`) | 5 | 분산 락 TTL, 재시도 설정 |
| 데이터 품질 (`data_quality.*`) | 7 | 이상치 탐지, 힐링, 격리 |
| LLM 메모리 (`llm_memory.*`) | 8 | 단기/장기 메모리, RAG, 압축 |
| 부트 시퀀스 (`boot.*`) | 6 | 백필 배수, 워밍업 타임아웃, 자동 활성화 |
| DB 커넥션 풀링 (`db.pool.*`) | 12 | PgBouncer, SQLAlchemy 풀, 헬스체크 |
| **합계** | **146** | |
