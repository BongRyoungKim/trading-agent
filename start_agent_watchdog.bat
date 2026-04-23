@echo off
cd /D C:\Users\Coop220426-4\Project\Trading_Agent

:restart
echo [%date% %time%] Starting trading agent... >> logs\watchdog.log
C:\Users\Coop220426-4\AppData\Local\Python\pythoncore-3.14-64\python.exe -m src.main --mode paper --strategy MeanReversionStrategy --strategy-params "{\"vol_mult\": 1.5, \"rsi_oversold_fast\": 20, \"rsi_oversold_slow\": 35, \"rsi_exit\": 62, \"no_entry_hours_utc\": [0, 1, 2]}" --interval 60 --dashboard-port 8000 --telegram-bot --symbols BTC/KRW --top-symbols 20 --trailing-stop-pct 1.5 --paper-capital 100000 --heartbeat-interval 1800 --exclude-symbols DOGE/KRW ELSA/KRW DOOD/KRW >> logs\agent_console.log 2>&1
echo [%date% %time%] Agent exited with code %errorlevel%. Restarting in 30s... >> logs\watchdog.log
timeout /t 30 /nobreak > nul
goto restart
