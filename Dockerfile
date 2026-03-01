# P.R.O.F.I.T. - Production Dockerfile
FROM python:3.12-slim AS base

# 시스템 의존성 설치
RUN apt-get update && \
    apt-get install -y --no-install-recommends curl && \
    rm -rf /var/lib/apt/lists/*

WORKDIR /app

# 의존성 먼저 복사 (캐시 최적화)
COPY pyproject.toml ./
RUN pip install --no-cache-dir -e .

# 소스 코드 복사
COPY src/ ./src/
COPY config/ ./config/

# 비root 사용자
RUN groupadd -r profit && useradd -r -g profit profit
USER profit

EXPOSE 8000

HEALTHCHECK --interval=30s --timeout=10s --retries=3 \
    CMD curl -f http://localhost:8000/health || exit 1

CMD ["uvicorn", "src.main:app", "--host", "0.0.0.0", "--port", "8000"]
