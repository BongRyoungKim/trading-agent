$workDir     = 'C:\Users\Coop220426-4\Project\Trading_Agent'
$guardianLog = Join-Path $workDir 'logs\guardian.log'
$watchdogScript = Join-Path $workDir 'start_agent_watchdog.ps1'

function Write-Log($msg) {
    $ts = Get-Date -Format 'yyyy-MM-dd HH:mm:ss'
    Add-Content -Path $guardianLog -Value "[$ts] $msg"
    # 로그 5000줄 초과 시 앞부분 1000줄 제거
    try {
        $lines = Get-Content $guardianLog -ErrorAction SilentlyContinue
        if ($lines.Count -gt 5000) {
            $lines | Select-Object -Skip 1000 | Set-Content $guardianLog
        }
    } catch {}
}

function Test-AgentAlive {
    $result = netstat -ano 2>$null | Select-String '0\.0\.0\.0:8000\s+0\.0\.0\.0:0\s+LISTENING'
    return $null -ne $result
}

function Get-WatchdogProcess {
    return Get-WmiObject Win32_Process -Filter "Name='powershell.exe'" 2>$null |
        Where-Object { $_.CommandLine -like '*start_agent_watchdog*' }
}

Write-Log "Guardian started — checking every 60s"

# 최초 기동 여유 시간 (watchdog + agent 시작 대기)
Start-Sleep -Seconds 30

while ($true) {
    Start-Sleep -Seconds 60

    if (-not (Test-AgentAlive)) {
        $watchdog = Get-WatchdogProcess
        if ($watchdog) {
            Write-Log "Port 8000 down but watchdog alive (PID=$($watchdog.ProcessId)) — agent starting or crashing, wait..."
        } else {
            Write-Log "Port 8000 down AND watchdog dead — restarting watchdog..."
            Start-Process powershell.exe `
                -ArgumentList "-WindowStyle Hidden -NonInteractive -ExecutionPolicy Bypass -File `"$watchdogScript`"" `
                -WorkingDirectory $workDir
            Write-Log "Watchdog restarted"
        }
    }
}
