$workDir     = 'C:\Users\Coop220426-4\Project\Trading_Agent'
$watchdogLog = Join-Path $workDir 'logs\watchdog.log'
$agentScript = 'C:\Users\Coop220426-4\AppData\Local\Python\pythoncore-3.14-64\python.exe'
$agentArgs   = '-m src.main --mode paper --strategy MeanReversionStrategy ' +
               '--strategy-params "{\"vol_mult\": 1.5, \"rsi_oversold_fast\": 20, \"rsi_oversold_slow\": 35, \"rsi_exit\": 62, \"no_entry_hours_utc\": [0, 1, 2]}" ' +
               '--interval 60 --dashboard-port 8000 --telegram-bot --symbols BTC/KRW ' +
               '--top-symbols 20 --trailing-stop-pct 1.5 --paper-capital 100000 ' +
               '--heartbeat-interval 1800 --exclude-symbols DOGE/KRW ELSA/KRW DOOD/KRW'
$logFile     = Join-Path $workDir 'logs\agent_console.log'

function Write-Log($msg) {
    $ts = Get-Date -Format 'yyyy-MM-dd HH:mm:ss'
    Add-Content -Path $watchdogLog -Value "[$ts] $msg"
}

Write-Log "Watchdog started"

while ($true) {
    Write-Log "Starting trading agent..."

    # 창 없이 에이전트 실행, stdout/stderr → 로그 파일
    $psi = New-Object System.Diagnostics.ProcessStartInfo
    $psi.FileName               = $agentScript
    $psi.Arguments              = $agentArgs
    $psi.WorkingDirectory       = $workDir
    $psi.RedirectStandardOutput = $true
    $psi.RedirectStandardError  = $true
    $psi.UseShellExecute        = $false
    $psi.CreateNoWindow         = $true

    $proc = New-Object System.Diagnostics.Process
    $proc.StartInfo = $psi

    # 비동기 로그 수집
    $proc.EnableRaisingEvents = $true
    $logStream = [System.IO.StreamWriter]::new($logFile, $true, [System.Text.Encoding]::UTF8)
    $logStream.AutoFlush = $true
    $proc.add_OutputDataReceived({ param($s,$e); if ($e.Data) { $logStream.WriteLine($e.Data) } })
    $proc.add_ErrorDataReceived({  param($s,$e); if ($e.Data) { $logStream.WriteLine($e.Data) } })

    $proc.Start() | Out-Null
    $proc.BeginOutputReadLine()
    $proc.BeginErrorReadLine()
    $proc.WaitForExit()

    $logStream.Close()
    $exitCode = $proc.ExitCode
    Write-Log "Agent exited (code=$exitCode). Restarting in 30s..."
    Start-Sleep -Seconds 30
}
