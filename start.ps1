# Trading Agent Launcher
# - watchdog + guardian 기동 (없으면 시작, 있으면 스킵)
# - 포트 8000 열릴 때까지 대기 (최대 90초)
# - 준비되면 브라우저 자동 오픈 + 상태 요약 출력

param(
    [switch]$NoOpen,      # 브라우저 자동 오픈 skip
    [switch]$Restart,     # 기존 프로세스 강제 재시작
    [switch]$Stop         # 에이전트 + watchdog + guardian 정지
)

$workDir        = 'C:\Users\Coop220426-4\Project\Trading_Agent'
$watchdogScript = Join-Path $workDir 'start_agent_watchdog.ps1'
$guardianScript = Join-Path $workDir 'guardian.ps1'
$dashUrl        = 'http://localhost:8000/'
$apiStatus      = 'http://localhost:8000/api/status'
$modeFile       = Join-Path $workDir '.trading_mode'
$logFile        = Join-Path $workDir 'logs\agent_console.log'

function Write-Color($msg, $color = 'White') {
    Write-Host $msg -ForegroundColor $color
}

function Get-WatchdogProc {
    Get-WmiObject Win32_Process -Filter "Name='powershell.exe'" 2>$null |
        Where-Object { $_.CommandLine -like '*start_agent_watchdog*' }
}

function Get-GuardianProc {
    Get-WmiObject Win32_Process -Filter "Name='powershell.exe'" 2>$null |
        Where-Object { $_.CommandLine -like '*guardian*' }
}

function Test-Port8000 {
    $r = netstat -ano 2>$null | Select-String '0\.0\.0\.0:8000\s+0\.0\.0\.0:0\s+LISTENING'
    return $null -ne $r
}

# ── Stop mode ─────────────────────────────────────────────────────────────────
if ($Stop) {
    Write-Color '정지 중...' Yellow

    # guardian
    $g = Get-GuardianProc
    if ($g) { Stop-Process -Id $g.ProcessId -Force; Write-Color "  Guardian 정지 (PID $($g.ProcessId))" DarkGray }

    # watchdog
    $w = Get-WatchdogProc
    if ($w) { Stop-Process -Id $w.ProcessId -Force; Write-Color "  Watchdog 정지 (PID $($w.ProcessId))" DarkGray }

    # python agent on port 8000
    $pids = netstat -ano 2>$null | Select-String ':8000\s+.*LISTENING' |
        ForEach-Object { ($_ -split '\s+')[-1] } | Sort-Object -Unique
    foreach ($pid in $pids) {
        if ($pid -match '^\d+$') {
            Stop-Process -Id ([int]$pid) -Force -ErrorAction SilentlyContinue
            Write-Color "  Agent 프로세스 정지 (PID $pid)" DarkGray
        }
    }

    Write-Color '완료.' Green
    exit 0
}

# ── Header ────────────────────────────────────────────────────────────────────
Write-Host ''
Write-Color '========================================' Cyan
Write-Color '  Trading Agent Launcher' Cyan
Write-Color '========================================' Cyan

# ── Restart: stop everything first ────────────────────────────────────────────
if ($Restart) {
    Write-Color '[재시작] 기존 프로세스 종료 중...' Yellow
    & $PSCommandPath -Stop
    Start-Sleep -Seconds 3
}

# ── Check current mode ────────────────────────────────────────────────────────
$mode = if (Test-Path $modeFile) { (Get-Content $modeFile -Raw).Trim().ToUpper() } else { 'PAPER' }
Write-Color "  현재 모드 : $mode" $(if ($mode -eq 'LIVE') { 'Green' } else { 'Blue' })

# ── Already running? ──────────────────────────────────────────────────────────
if ((Test-Port8000) -and -not $Restart) {
    Write-Color '  대시보드  : 이미 실행 중' Green
    try {
        $s = Invoke-RestMethod -Uri $apiStatus -TimeoutSec 5
        Write-Color "  엔진 상태 : $($s.mode) / $(if($s.paused){'PAUSED'}else{'RUNNING'})" Cyan
        Write-Color "  서킷 브레이커: $($s.circuit)" $(if($s.circuit -ne 'CLOSED'){'Red'}else{'Green'})
    } catch {
        Write-Color '  (API 응답 대기 중)' DarkGray
    }
    Write-Color ''
    if (-not $NoOpen) { Start-Process $dashUrl }
    exit 0
}

# ── Start Watchdog ────────────────────────────────────────────────────────────
$w = Get-WatchdogProc
if (-not $w) {
    Write-Color '  Watchdog  : 시작 중...' Yellow
    Start-Process powershell.exe `
        -ArgumentList "-WindowStyle Hidden -NonInteractive -ExecutionPolicy Bypass -File `"$watchdogScript`"" `
        -WorkingDirectory $workDir
    Start-Sleep -Seconds 1
    $w = Get-WatchdogProc
    if ($w) {
        Write-Color "  Watchdog  : 시작됨 (PID $($w.ProcessId))" Green
    } else {
        Write-Color '  Watchdog  : 시작 실패 — 로그를 확인하세요' Red
    }
} else {
    Write-Color "  Watchdog  : 이미 실행 중 (PID $($w.ProcessId))" DarkGray
}

# ── Start Guardian ────────────────────────────────────────────────────────────
$g = Get-GuardianProc
if (-not $g) {
    Start-Process powershell.exe `
        -ArgumentList "-WindowStyle Hidden -NonInteractive -ExecutionPolicy Bypass -File `"$guardianScript`"" `
        -WorkingDirectory $workDir
    Start-Sleep -Seconds 1
    $g = Get-GuardianProc
    if ($g) {
        Write-Color "  Guardian  : 시작됨 (PID $($g.ProcessId))" Green
    }
} else {
    Write-Color "  Guardian  : 이미 실행 중 (PID $($g.ProcessId))" DarkGray
}

# ── Wait for dashboard ────────────────────────────────────────────────────────
Write-Color ''
Write-Color '  대시보드 준비 대기 중 (최대 90초)...' Yellow
$ready = $false
$elapsed = 0
for ($i = 0; $i -lt 90; $i++) {
    Start-Sleep -Seconds 1
    $elapsed++

    if ($elapsed % 10 -eq 0) {
        # 로그에서 최근 상태 메시지 출력
        if (Test-Path $logFile) {
            $recent = Get-Content $logFile -Tail 3 -ErrorAction SilentlyContinue
            foreach ($line in $recent) {
                if ($line -match 'error|fail|exception|started|running' -and $line.Length -gt 0) {
                    Write-Color "    > $($line.Substring([Math]::Min(0,$line.Length-[Math]::Min(80,$line.Length))))" DarkGray
                }
            }
        }
    }

    if (Test-Port8000) {
        Start-Sleep -Seconds 2  # 잠깐 더 대기 (uvicorn 완전 시작)
        $ready = $true
        break
    }
    Write-Host '.' -NoNewline
}

Write-Host ''

# ── Result ────────────────────────────────────────────────────────────────────
if ($ready) {
    Write-Color ''
    Write-Color '========================================' Green
    Write-Color '  대시보드 준비 완료!' Green

    try {
        $s = Invoke-RestMethod -Uri $apiStatus -TimeoutSec 8
        Write-Color "  모드      : $($s.mode)" $(if($s.mode -eq 'LIVE'){'Green'}else{'Blue'})
        Write-Color "  상태      : $(if($s.paused){'PAUSED'}else{'RUNNING'})" $(if($s.paused){'Yellow'}else{'Green'})
        Write-Color "  서킷 브레이커: $($s.circuit)" $(if($s.circuit -ne 'CLOSED'){'Red'}else{'Green'})
        Write-Color "  거래 시간 : $($s.trading_hours)" White
    } catch {
        Write-Color '  (엔진 초기화 중 — 곧 갱신됩니다)' DarkGray
    }

    Write-Color "  URL       : $dashUrl" Cyan
    Write-Color '========================================' Green
    Write-Host ''

    if (-not $NoOpen) {
        Write-Color '  브라우저 오픈 중...' DarkGray
        Start-Process $dashUrl
    }
} else {
    Write-Color ''
    Write-Color '========================================' Red
    Write-Color '  90초 내 대시보드가 열리지 않았습니다.' Red
    Write-Color '  로그 확인: logs\agent_console.log' Yellow
    Write-Color '  Watchdog 로그: logs\watchdog.log' Yellow
    Write-Color '========================================' Red

    if (Test-Path $logFile) {
        Write-Color ''
        Write-Color '  최근 로그 (마지막 10줄):' DarkGray
        Get-Content $logFile -Tail 10 | ForEach-Object { Write-Color "    $_" DarkGray }
    }
}
