FROM python:3.11-slim

WORKDIR /app

# 타임존: 기본 이미지는 UTC로 동작해 로그 타임스탬프가 한국 시간(KST, UTC+9)과
# 9시간 차이가 남. tzdata 설치 후 TZ=Asia/Seoul로 고정해 로그·컨테이너 시스템
# 시간을 KST로 맞춘다. 매매/리스크 로직은 datetime.now(UTC)를 명시적으로 쓰고
# 있어 이 변경의 영향을 받지 않는다.
RUN apt-get update \
    && apt-get install -y --no-install-recommends tzdata \
    && rm -rf /var/lib/apt/lists/*

ENV TZ=Asia/Seoul

# Install dependencies first (layer cache)
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# Copy source
COPY src/ ./src/

# Non-root user for security
RUN useradd -m -u 1000 trader
USER trader

ENV TRADING_MODE=paper \
    LOG_LEVEL=INFO \
    PYTHONUNBUFFERED=1

EXPOSE 8080

# Health check: probe the /health endpoint
HEALTHCHECK --interval=30s --timeout=5s --start-period=10s --retries=3 \
    CMD python -c \
    "import urllib.request; urllib.request.urlopen('http://localhost:8080/health', timeout=4)"

ENTRYPOINT ["python", "-m", "src.main"]
CMD ["--mode", "paper", "--symbols", "BTC/USDT"]
