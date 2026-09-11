#!/bin/bash
set -e
cd /app
MODE_FILE="/app/.trading_mode"
if [ -f "$MODE_FILE" ]; then
  TRADE_MODE=$(tr -d '[:space:]' < "$MODE_FILE")
else
  TRADE_MODE="live"
  echo -n "live" > "$MODE_FILE"
fi
if [ "$TRADE_MODE" != "paper" ] && [ "$TRADE_MODE" != "live" ]; then
  TRADE_MODE="paper"
fi
LAUNCH_ARGS=$(python scripts/render_launch_args.py)
eval "args=($LAUNCH_ARGS)"
echo "Starting trading agent (mode=$TRADE_MODE)"
exec python -m src.main \
  --mode "$TRADE_MODE" \
  --strategy RegimeAdaptiveStrategy \
  "${args[@]}" \
  --interval 60 \
  --dashboard-port 8081 \
  --telegram-bot \
  --symbols BTC/KRW \
  --top-symbols 12 \
  --trailing-stop-pct 2.0 \
  --heartbeat-interval 1800 \
  --exclude-symbols DOGE/KRW ELSA/KRW DOOD/KRW RE/KRW KAITO/KRW USDT/KRW \
  --news-tuning-hour 8 \
  --skip-checks
