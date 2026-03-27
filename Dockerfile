FROM python:3.11-slim

WORKDIR /app

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
