# 트레이딩 에이전트 — PC 이관 가이드 (GitHub 방식)

> 작성일: 2026-03-27
> 소스코드는 GitHub Private Repository로 관리하고,
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

### 1-1. Git 설치 확인

```powershell
git --version
```

> 없으면 https://git-scm.com/download/win 에서 설치

### 1-2. GitHub CLI 설치 및 로그인

> 없으면 https://cli.github.com 에서 설치

```powershell
gh --version

gh auth login
# 선택: GitHub.com → HTTPS → Login with a web browser
# 브라우저에서 코드 입력 후 승인
```

### 1-3. Git 초기화 및 첫 커밋

```powershell
cd C:\Users\aphen\Project\Trading_Agent

git init
git add .
git commit -m "init: trading agent"
```

> ✅ `.env`와 `data/*.db`는 `.gitignore`에 이미 제외되어 있어 자동으로 업로드되지 않습니다.

### 1-4. GitHub Private Repository 생성 및 Push

```powershell
gh repo create trading-agent --private --source=. --push
```

완료 확인:

```powershell
git remote -v
# origin  https://github.com/YOUR_ID/trading-agent.git (fetch)
# origin  https://github.com/YOUR_ID/trading-agent.git (push)
```

### 1-5. 이후 코드 변경 시 업데이트 방법

```powershell
cd C:\Users\aphen\Project\Trading_Agent

git add .
git commit -m "feat: 변경 내용 설명"
git push
```

---

## STEP 2 — 기존 PC: DB 및 설정 파일 백업

> ⚠️ 반드시 **STEP 4(새 PC 실행 직전)** 이전에 백업하세요.
> 프로세스가 실행 중인 상태에서 DB를 복사하면 데이터가 손상될 수 있습니다.

### 2-1. 백업 폴더 생성 및 파일 복사

```powershell
# 바탕화면에 백업 폴더 생성
mkdir C:\Users\aphen\Desktop\trading_backup

# 거래 이력 DB 복사
copy C:\Users\aphen\Project\Trading_Agent\data\journal.db `
     C:\Users\aphen\Desktop\trading_backup\journal.db

# 포지션 DB 복사
copy C:\Users\aphen\Project\Trading_Agent\data\positions.db `
     C:\Users\aphen\Desktop\trading_backup\positions.db

# 환경 변수 파일 복사
copy C:\Users\aphen\Project\Trading_Agent\.env `
     C:\Users\aphen\Desktop\trading_backup\.env
```

### 2-2. 백업 파일 확인

```powershell
dir C:\Users\aphen\Desktop\trading_backup
# journal.db, positions.db, .env 3개 파일이 보여야 합니다
```

### 2-3. 새 PC로 전달

`C:\Users\aphen\Desktop\trading_backup` 폴더를 아래 방법 중 하나로 전달:

- USB 드라이브에 복사
- 카카오톡 나에게 보내기 (파일 압축 후)
- 구글 드라이브 / OneDrive 업로드

---

## STEP 3 — 기존 PC: 프로세스 종료

> ⚠️ 새 PC에서 실행하기 **직전**에 실행합니다.
> 두 PC가 동시에 같은 API 키로 실행되면 중복 주문이 발생합니다.

```powershell
Get-Process python -ErrorAction SilentlyContinue | Stop-Process -Force

# 종료 확인
Get-Process python -ErrorAction SilentlyContinue
# 아무것도 출력되지 않으면 정상 종료
```

---

## STEP 4 — 새 PC: 환경 구성

### 4-1. Python 설치

- 다운로드: https://www.python.org/downloads/
- **버전**: Python 3.11 이상 필수
- 설치 시 **"Add Python to PATH"** 반드시 체크 ✅

```powershell
python --version
# Python 3.11.x 이상이어야 합니다
```

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

---

## STEP 5 — 새 PC: 코드 Clone 및 패키지 설치

### 5-1. 프로젝트 폴더 준비

```powershell
mkdir C:\Users\NEW_USER\Project
cd C:\Users\NEW_USER\Project
```

> `NEW_USER`는 새 PC의 실제 Windows 사용자명으로 변경하세요.

### 5-2. 저장소 Clone

```powershell
git clone https://github.com/YOUR_ID/trading-agent.git Trading_Agent
cd Trading_Agent
```

> `YOUR_ID`는 본인의 GitHub 아이디로 변경하세요.

### 5-3. 패키지 설치

```powershell
pip install -r requirements.txt
```

설치 완료 확인:

```powershell
python -c "import ccxt, fastapi, loguru, apscheduler; print('패키지 정상')"
# 출력: 패키지 정상
```

---

## STEP 6 — 새 PC: DB 복원 및 .env 설정

### 6-1. 필수 폴더 생성

```powershell
mkdir C:\Users\NEW_USER\Project\Trading_Agent\data
mkdir C:\Users\NEW_USER\Project\Trading_Agent\logs
```

### 6-2. DB 파일 복원

전달받은 백업 파일을 복사합니다 (USB가 D: 드라이브인 경우):

```powershell
copy D:\trading_backup\journal.db `
     C:\Users\NEW_USER\Project\Trading_Agent\data\journal.db

copy D:\trading_backup\positions.db `
     C:\Users\NEW_USER\Project\Trading_Agent\data\positions.db
```

복원 확인:

```powershell
dir C:\Users\NEW_USER\Project\Trading_Agent\data
# journal.db, positions.db가 0 바이트가 아닌지 확인
```

### 6-3. .env 파일 설정

백업에서 복사:

```powershell
copy D:\trading_backup\.env `
     C:\Users\NEW_USER\Project\Trading_Agent\.env
```

또는 메모장으로 직접 생성:

```powershell
notepad C:\Users\NEW_USER\Project\Trading_Agent\.env
```

아래 내용 입력 후 저장:

```
EXCHANGE=upbit
UPBIT_ACCESS_KEY=여기에_업비트_액세스키_입력
UPBIT_SECRET_KEY=여기에_업비트_시크릿키_입력
TELEGRAM_BOT_TOKEN=여기에_텔레그램_봇_토큰_입력
TELEGRAM_CHAT_ID=여기에_텔레그램_채팅_ID_입력
```

.env 파일 확인:

```powershell
Get-Content C:\Users\NEW_USER\Project\Trading_Agent\.env
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

```powershell
cd C:\Users\NEW_USER\Project\Trading_Agent

python -m src.main `
  --mode live `
  --strategy Scalping5mStrategy `
  --interval 60 `
  --dashboard-port 8000 `
  --telegram-bot `
  --symbols XRP/KRW BTC/KRW ETH/KRW KAT/KRW JST/KRW SOL/KRW PROVE/KRW TAO/KRW KITE/KRW SIGN/KRW
```

### 7-3. 정상 실행 확인 (로그)

아래 5개 항목이 모두 `[PASS]`로 표시되어야 합니다:

```
[PASS] credentials: upbit credentials present
[PASS] exchange_connectivity: upbit reachable — XRP/KRW last=2,060.00
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
- 매수/매도 이력이 이관 전 데이터 기준으로 표시되는지 확인
- 신호 평가 현황이 약 1분 후 갱신되는지 확인
- 텔레그램 봇에서 시작 알림 수신 확인

---

## STEP 8 — 기존 PC 완전 종료 (STEP 3이 되지 않은 경우)

```powershell
Get-Process python -ErrorAction SilentlyContinue | Stop-Process -Force
```

---

## 이관 완료 체크리스트

```
[기존 PC]
□ GitHub Private Repository 생성 및 push 완료
  - git init
  - git add .
  - git commit -m "init: trading agent"
  - gh repo create trading-agent --private --source=. --push

□ DB 백업 완료
  - data/journal.db
  - data/positions.db
  - .env

□ 기존 PC 프로세스 종료
  - Get-Process python | Stop-Process -Force

[새 PC]
□ Python 3.11+ 설치 및 PATH 등록 확인
□ Git 설치 확인
□ gh auth login 완료
□ git clone https://github.com/YOUR_ID/trading-agent.git Trading_Agent
□ pip install -r requirements.txt 완료
□ data/journal.db 복원 완료 (크기 > 0)
□ data/positions.db 복원 완료 (크기 > 0)
□ .env 파일 작성 완료
□ powercfg 절전 비활성화 완료
□ python -m src.main 실행 후 [PASS] 5개 확인
□ http://localhost:8000 대시보드 접속 확인
□ 텔레그램 알림 수신 확인
```

---

## 문제 해결

### gh 명령어를 찾을 수 없을 때

```powershell
# GitHub CLI 설치 후 PowerShell 재시작
# https://cli.github.com 에서 설치
gh auth login
```

### git push 인증 오류

```powershell
gh auth login
# 재로그인 후 다시 시도
git push
```

### pip install 오류

```powershell
python -m pip install --upgrade pip
pip install -r requirements.txt
```

### 포트 8000 이미 사용 중

```powershell
# 사용 중인 프로세스 확인
netstat -ano | findstr :8000

# PID로 종료 (숫자는 실제 PID로 변경)
taskkill /PID 12345 /F
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

# 첫 줄 확인 (EXCHANGE=upbit 로 시작해야 함)
Get-Content .env | Select-Object -First 3
```

### DB 권한 오류

```powershell
icacls ".\data" /grant "%USERNAME%:F"
```
