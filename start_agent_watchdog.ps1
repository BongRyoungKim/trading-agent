$workDir     = 'C:\Users\Coop220426-4\Project\Trading_Agent'
$watchdogLog = Join-Path $workDir 'logs\watchdog.log'
$modeFile    = Join-Path $workDir '.trading_mode'
$agentScript = 'C:\Users\Coop220426-4\AppData\Local\Python\pythoncore-3.14-64\python.exe'
$logFile     = Join-Path $workDir 'logs\agent_console.log'

function Write-Log($msg) {
    $ts = Get-Date -Format 'yyyy-MM-dd HH:mm:ss'
    Add-Content -Path $watchdogLog -Value "[$ts] $msg"
}

# Single-instance guard: only one watchdog may run at a time
$mutex = New-Object System.Threading.Mutex($false, 'Global\TradingAgentWatchdog')
if (-not $mutex.WaitOne(0)) {
    Write-Log "Another watchdog instance is already running. Exiting."
    exit 0
}

Write-Log "Watchdog started"

# Kill any stale agent processes from a previous watchdog session
$staleProcs = Get-WmiObject Win32_Process | Where-Object {
    $_.CommandLine -like '*src.main*' -and $_.ProcessId -ne $PID
}
foreach ($sp in $staleProcs) {
    Write-Log "Killing stale agent process (PID=$($sp.ProcessId))"
    Stop-Process -Id $sp.ProcessId -Force -ErrorAction SilentlyContinue
}
if ($staleProcs) { Start-Sleep -Seconds 3 }

while ($true) {
    # Read mode from .trading_mode file (default: paper)
    $tradeMode = if (Test-Path $modeFile) { (Get-Content $modeFile -Raw).Trim() } else { 'paper' }
    if ($tradeMode -notin @('paper','live')) { $tradeMode = 'paper' }

    # 전략/리스크 파라미터는 .strategy_params.json 에서 동적으로 읽는다.
    # ScheduledTaskRunner가 자동 재조정한 값이 다음 재시작부터 즉시 반영된다.
    $launchArgs = & $agentScript (Join-Path $workDir 'scripts\render_launch_args.py')
    $agentArgs = "-m src.main --mode $tradeMode --strategy RegimeAdaptiveStrategy " +
                 "$launchArgs " +
                 '--interval 60 --dashboard-port 8000 --telegram-bot --symbols BTC/KRW ' +
                 '--top-symbols 20 --trailing-stop-pct 1.5 ' +
                 '--heartbeat-interval 1800 --exclude-symbols DOGE/KRW ELSA/KRW DOOD/KRW RE/KRW KAITO/KRW USDT/KRW'

    Write-Log "Starting trading agent (mode=$tradeMode)..."

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

    # 비동기 로그 수집.
    # 주의: .NET 이벤트(OutputDataReceived/ErrorDataReceived)에 PowerShell
    # 스크립트블록을 raw add_*()로 직접 연결하면, 이벤트가 별도 스레드에서
    # 동시에 발생할 때 동일 러너스페이스를 공유하지 못해
    # PSInvalidOperationException으로 powershell.exe 프로세스 전체가 죽는다
    # (에이전트가 시작 직후 로그를 쏟아낼 때 거의 항상 발생 — 이 워치독이
    # 몇 초~수십 초 만에 조용히 죽던 원인). Register-ObjectEvent는 이벤트를
    # 엔진의 이벤트 큐로 안전하게 넘겨주므로 이 문제가 없다.
    $proc.EnableRaisingEvents = $true
    $logStream = [System.IO.StreamWriter]::new($logFile, $true, [System.Text.Encoding]::UTF8)
    $logStream.AutoFlush = $true
    $outSub = Register-ObjectEvent -InputObject $proc -EventName OutputDataReceived -MessageData $logStream -Action {
        if ($EventArgs.Data) { $Event.MessageData.WriteLine($EventArgs.Data) }
    }
    $errSub = Register-ObjectEvent -InputObject $proc -EventName ErrorDataReceived -MessageData $logStream -Action {
        if ($EventArgs.Data) { $Event.MessageData.WriteLine($EventArgs.Data) }
    }

    $proc.Start() | Out-Null
    $proc.BeginOutputReadLine()
    $proc.BeginErrorReadLine()
    $proc.WaitForExit()

    Unregister-Event -SourceIdentifier $outSub.Name -ErrorAction SilentlyContinue
    Unregister-Event -SourceIdentifier $errSub.Name -ErrorAction SilentlyContinue
    Remove-Job -Job $outSub, $errSub -Force -ErrorAction SilentlyContinue
    $logStream.Close()
    $exitCode = $proc.ExitCode
    Write-Log "Agent exited (code=$exitCode). Restarting in 30s..."
    Start-Sleep -Seconds 30
}

$mutex.ReleaseMutex()
$mutex.Dispose()
