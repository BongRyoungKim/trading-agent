# 트레이딩 에이전트 — PC 이관 가이드 (GitHub 방식)

> 작성일: 2026-03-30
> 소스코드: https://github.com/aphenix-debug/trading-agent (Private)
> API 키와 거래 DB는 별도로 안전하게 전달합니다.

---

## 전체 흐름

```
[기존 PC]                          [새 PC]
─────────────────────              ─────────────────────────────
1. GitHub에 코드 push
2. DB + .env 백업
3. 프로세스 종료          →        4. Python / Git 설치
                                   5. git clone + pip install
                                   6. DB 복원 + .env 설정
                                   7. 실행 및 검증
```

---

## STEP 1 — 기존 PC: GitHub에 코드 업로드

### 1-1. Git 사용자 정보 확인

```powershell
git config user.name
git config user.email
```

설정이 비어 있으면 아래와 같이 입력:

```powershell
git config --global user.name "aphenix"
git config --global user.email "aphenix@coopnc.com"
```

### 1-2. GitHub 로그인 확인

```powershell
gh auth status
```

> 로그인이 안 되어 있으면:
> ```powershell
> gh auth login
> # GitHub.com → HTTPS → Login with a web browser 선택
> ```

### 1-3. 변경 사항 커밋 및 Push

```powershell
cd C:\Users\YOUR_USERNAME\Project\Trading_Agent

git add .
git commit -m "feat: 변경 내용 설명"
git push origin master
```

완료 확인:

```powershell
git log --oneline -3
# 최신 커밋이 보이면 정상
```

---

## STEP 2 — 기존 PC: DB 및 설정 파일 백업

> ⚠️ **엔진 실행 중에는 DB를 복사하지 마세요.** 데이터가 손상될 수 있습니다.
> 반드시 STEP 3(프로세스 종료) 이후에 백업하세요.

### 2-1. 백업 폴더 생성 및 파일 복사

```powershell
# 바탕화면에 백업 폴더 생성
mkdir C:\Users\YOUR_USERNAME\Desktop\trading_backup

# 거래 이력 DB
copy C:\Users\YOUR_USERNAME\Project\Trading_Agent\data\journal.db `
     C:\Users\YOUR_USERNAME\Desktop\trading_backup\journal.db

# 오픈 포지션 DB
copy C:\Users\YOUR_USERNAME\Project\Trading_Agent\data\positions.db `
     C:\Users\YOUR_USERNAME\Desktop\trading_backup\positions.db

# 환경 변수 파일
copy C:\Users\YOUR_USERNAME\Project\Trading_Agent\.env `
     C:\Users\YOUR_USERNAME\Desktop\trading_backup\.env
```

> `YOUR_USERNAME`은 본인의 Windows 사용자명으로 변경하세요.
> (예: `C:\Users\aphen\Project\...`)

### 2-2. 백업 파일 확인

```powershell
dir C:\Users\YOUR_USERNAME\Desktop\trading_backup
# journal.db, positions.db, .env — 3개 파일이 보여야 합니다
# 각 파일 크기가 0이 아닌지 확인
```

### 2-3. 새 PC로 전달

`trading_backup` 폴더를 아래 방법 중 하나로 전달:

- USB 드라이브에 복사
- 카카오톡 나에게 보내기 (파일 압축 후)
- 구글 드라이브 / OneDrive 업로드

---

## STEP 3 — 기존 PC: 프로세스 종료

> ⚠️ 새 PC에서 실행하기 **직전**에 실행합니다.
> 두 PC가 동시에 같은 API 키로 실행되면 중복 주문이 발생합니다.

```powershell
# 실행 중인 Python 프로세스 모두 종료
Get-Process python -ErrorAction SilentlyContinue | Stop-Process -Force

# 종료 확인 (아무것도 출력되지 않으면 정상)
Get-Process python -ErrorAction SilentlyContinue
```

---

## STEP 4 — 새 PC: 환경 구성

### 4-1. Python 설치

- 다운로드: https://www.python.org/downloads/
- **버전**: Python 3.11 이상 필수
- 설치 시 **"Add Python to PATH"** 반드시 체크 ✅

설치 확인:

```powershell
py --version
# Python 3.11.x 이상이어야 합니다
```

> Windows는 `python` 대신 **`py`** 명령어를 사용합니다.

### 4-2. Git 설치

- 다운로드: https://git-scm.com/download/win
- 기본 설정으로 설치

```powershell
git --version
```

### 4-3. GitHub CLI 설치 및 로그인

- 다운로드: https://cli.github.com

```powershell
gh auth login
# GitHub.com → HTTPS → Login with a web browser 선택
# 브라우저에서 코드 입력 후 승인
```

### 4-4. Git 사용자 정보 설정

```powershell
git config --global user.name "aphenix"
git config --global user.email "aphenix@coopnc.com"
```

---

## STEP 5 — 새 PC: 코드 Clone 및 패키지 설치

### 5-1. 프로젝트 폴더 준비

```powershell
mkdir C:\Users\YOUR_USERNAME\Project
cd C:\Users\YOUR_USERNAME\Project
```

### 5-2. 저장소 Clone

```powershell
git clone https://github.com/aphenix-debug/trading-agent.git Trading_Agent
cd Trading_Agent
```

### 5-3. 패키지 설치

```powershell
py -m pip install --upgrade pip
py -m pip install -r requirements.txt
```

설치 완료 확인:

```powershell
py -c "import ccxt, fastapi, loguru, apscheduler; print('패키지 정상')"
# 출력: 패키지 정상
```

---

## STEP 6 — 새 PC: DB 복원 및 .env 설정

### 6-1. 필수 폴더 생성

```powershell
mkdir C:\Users\YOUR_USERNAME\Project\Trading_Agent\data
mkdir C:\Users\YOUR_USERNAME\Project\Trading_Agent\logs
```

### 6-2. DB 파일 복원

#### 이관 (거래 이력 유지) — 이전 PC에서 백업한 경우

USB가 D: 드라이브인 경우:

```powershell
copy D:\trading_backup\journal.db `
     C:\Users\YOUR_USERNAME\Project\Trading_Agent\data\journal.db

copy D:\trading_backup\positions.db `
     C:\Users\YOUR_USERNAME\Project\Trading_Agent\data\positions.db
```

복원 확인:

```powershell
Get-Item .\data\journal.db | Select-Object Name, Length
Get-Item .\data\positions.db | Select-Object Name, Length
# Length가 0이 아니어야 합니다
```

#### 신규 설치 (거래 이력 초기화) — 새로 시작하는 경우

DB 파일을 복사하지 않으면 엔진 첫 실행 시 자동으로 빈 DB를 생성합니다.
별도 작업 불필요.

> ⚠️ **positions.db가 있으면** 엔진 시작 시 이전 오픈 포지션을 자동으로 복원합니다.
> 포지션을 초기화하고 싶다면 `data/positions.db`를 삭제하세요.

### 6-3. .env 파일 설정

백업에서 복사:

```powershell
copy D:\trading_backup\.env `
     C:\Users\YOUR_USERNAME\Project\Trading_Agent\.env
```

또는 메모장으로 직접 생성:

```powershell
notepad C:\Users\YOUR_USERNAME\Project\Trading_Agent\.env
```

아래 내용 입력 후 저장:

```
EXCHANGE=upbit
TRADING_MODE=live
UPBIT_ACCESS_KEY=여기에_업비트_액세스키_입력
UPBIT_SECRET_KEY=여기에_업비트_시크릿키_입력

# 텔레그램 알림 (선택)
TELEGRAM_BOT_TOKEN=여기에_텔레그램_봇_토큰_입력
TELEGRAM_CHAT_ID=여기에_텔레그램_채팅_ID_입력

# 리스크 설정
MAX_POSITION_RISK=0.05
MAX_DAILY_LOSS=0.99
MAX_OPEN_POSITIONS=1
MAX_DRAWDOWN_HALT=0.99
```

.env 파일 확인:

```powershell
Get-Content .env
# EXCHANGE=upbit 로 시작해야 합니다
```

---

## STEP 7 — 새 PC: 실행 및 검증

### 7-1. 절전 방지 설정

```powershell
powercfg /change standby-timeout-ac 0
powercfg /change hibernate-timeout-ac 0
```

설정 확인:

```powershell
powercfg /query SCHEME_CURRENT SUB_SLEEP 29f6c1db-86da-48c5-9fdb-f2b67b1f44da
# "현재 AC 전원 설정 값: 0x00000000" 이면 정상
```

### 7-2. 트레이딩 에이전트 실행

> ⚠️ Windows에서 한글 로그 깨짐 방지를 위해 `PYTHONUTF8=1`을 반드시 설정합니다.

```powershell
cd C:\Users\YOUR_USERNAME\Project\Trading_Agent

$env:PYTHONUTF8=1
py -m src.main `
  --mode live `
  --strategy Scalping5mStrategy `
  --interval 60 `
  --dashboard-port 8000 `
  --telegram-bot `
  --symbols ONT/KRW ANKR/KRW FLOCK/KRW NOM/KRW ORDER/KRW DOOD/KRW ELSA/KRW STRAX/KRW API3/KRW ONG/KRW
```

> 심볼 목록은 본인 전략에 맞게 변경하세요.

### 7-3. 정상 실행 확인 (로그)

아래 항목들이 모두 표시되어야 합니다:

```
[PASS] credentials: upbit credentials present
[PASS] exchange_connectivity: upbit reachable
[PASS] risk_params: Risk parameters look sane
[PASS] db_writable: DB directory writable
[PASS] telegram: Telegram credentials present
Sleep prevention enabled (SetThreadExecutionState)
TradingEngine running — press Ctrl+C to stop
```

### 7-4. 대시보드 확인

브라우저에서 접속:

```
http://localhost:8000
```

확인 항목:
- 엔진 상태: `RUNNING` 뱃지 표시
- 이관한 경우: 매수/매도 이력이 이관 전 데이터 기준으로 표시
- 약 1분 후 신호 평가 현황 갱신
- 텔레그램 봇에서 시작 알림 수신

---

## STEP 8 — 기존 PC 완전 종료 (STEP 3 이전에 미실행한 경우)

```powershell
Get-Process python -ErrorAction SilentlyContinue | Stop-Process -Force
```

---

## 이관 완료 체크리스트

```
[기존 PC]
□ git push origin master 완료
  - git add .
  - git commit -m "feat: ..."
  - git push origin master

□ 엔진 종료 후 DB 백업 완료
  - data/journal.db
  - data/positions.db
  - .env

□ 기존 PC 프로세스 종료
  - Get-Process python | Stop-Process -Force

[새 PC]
□ Python 3.11+ 설치 및 py 명령어 확인
□ Git 설치 확인
□ gh auth login 완료 (aphenix@coopnc.com)
□ git config user.email "aphenix@coopnc.com" 설정
□ git clone https://github.com/aphenix-debug/trading-agent.git Trading_Agent 완료
□ py -m pip install -r requirements.txt 완료
□ data/journal.db 복원 완료 (크기 > 0)
□ data/positions.db 복원 완료 (이관 시) 또는 삭제 (초기화 시)
□ .env 파일 작성 완료 (TRADING_MODE=live 포함)
□ powercfg 절전 비활성화 완료
□ $env:PYTHONUTF8=1 설정 후 py -m src.main 실행
□ [PASS] 5개 항목 확인
□ http://localhost:8000 대시보드 접속 확인
□ 텔레그램 알림 수신 확인
```

---

## 문제 해결

### gh 명령어를 찾을 수 없을 때

```powershell
# GitHub CLI 재설치: https://cli.github.com
# 설치 후 PowerShell 재시작
gh auth login
```

### git push 인증 오류

```powershell
gh auth login
# 재로그인 후 다시 시도
git push origin master
```

### `py` 명령어를 찾을 수 없을 때

```powershell
# Python 재설치 시 "Add Python to PATH" 체크 필수
# 또는 아래 명령어로 PATH 수동 확인
where python
where py
```

### pip install 오류

```powershell
py -m pip install --upgrade pip
py -m pip install -r requirements.txt
```

### 포트 8000 이미 사용 중

```powershell
# 사용 중인 프로세스 확인
netstat -ano | findstr :8000

# PID로 종료 (숫자는 실제 PID로 변경)
taskkill /PID 12345 /F
```

### 한글 로그 깨짐 (UnicodeEncodeError)

```powershell
# 실행 전 반드시 설정
$env:PYTHONUTF8=1
py -m src.main ...
```

### DB 파일 크기가 0일 때 (복사 실패)

```powershell
# 파일 크기 확인
Get-Item .\data\journal.db | Select-Object Name, Length
# Length가 0이면 백업 파일에서 다시 복사

copy D:\trading_backup\journal.db .\data\journal.db
```

### .env 파일 인식 오류

```powershell
# 파일 존재 확인
Test-Path .env

# 내용 확인 (EXCHANGE=upbit 로 시작해야 함)
Get-Content .env | Select-Object -First 5
```

### DB 권한 오류

```powershell
icacls ".\data" /grant "%USERNAME%:F"
```

### positions.db 복원 후 포지션 중복 오류

엔진 시작 시 `positions.db`의 포지션과 실제 거래소 보유 현황을 자동으로 비교합니다.
포지션이 중복으로 잡히는 경우 `positions.db`를 삭제하고 재시작하면 거래소 잔고 기준으로 자동 재등록됩니다.

```powershell
del .\data\positions.db
py -m src.main ...
```
