@echo off
echo [%TIME%] Killing all python processes...
taskkill /F /IM python.exe >nul 2>&1

echo [%TIME%] Getting watchdog PIDs...
for /f "tokens=2" %%a in ('wmic process where "commandline like '%%start_agent_watchdog%%'" get processid ^| findstr /r "[0-9]"') do (
    echo Killing watchdog PID %%a
    taskkill /F /PID %%a >nul 2>&1
)

echo [%TIME%] Waiting 3 seconds...
timeout /t 3 /nobreak >nul

echo [%TIME%] Checking remaining...
wmic process where "commandline like '%%src.main%%'" get processid 2>nul | findstr /r "[0-9]"
wmic process where "commandline like '%%start_agent_watchdog%%'" get processid 2>nul | findstr /r "[0-9]"

echo [%TIME%] Starting watchdog (single instance)...
start "" /B powershell.exe -NoProfile -ExecutionPolicy Bypass -File "C:\Users\Coop220426-4\Project\Trading_Agent\start_agent_watchdog.ps1"

echo [%TIME%] Done.
