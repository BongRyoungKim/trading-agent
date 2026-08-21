@echo off
cd /D C:\Users\Coop220426-4\Project\Trading_Agent
set PY=C:\Users\Coop220426-4\AppData\Local\Python\pythoncore-3.14-64\python.exe
for /f "delims=" %%A in ('%PY% scripts\render_launch_args.py') do set LAUNCH_ARGS=%%A
%PY% -m src.main --mode live --strategy RegimeAdaptiveStrategy %LAUNCH_ARGS% --interval 60 --dashboard-port 8000 --telegram-bot --symbols BTC/KRW --top-symbols 20 --trailing-stop-pct 1.5 --heartbeat-interval 1800 --exclude-symbols DOGE/KRW ELSA/KRW DOOD/KRW RE/KRW KAITO/KRW USDT/KRW >> logs\agent_console.log 2>&1
