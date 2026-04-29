# Trading Agent 사용 가이드

## 목차

1. [시스템 요구사항](#1-시스템-요구사항)
2. [초기 설정](#2-초기-설정)
3. [에이전트 시작 및 정지](#3-에이전트-시작-및-정지)
4. [대시보드 사용법](#4-대시보드-사용법)
5. [트레이딩 모드 전환 (PAPER ↔ LIVE)](#5-트레이딩-모드-전환-paper--live)
6. [전략 설명](#6-전략-설명)
7. [리스크 관리](#7-리스크-관리)
8. [보고서 및 로그](#8-보고서-및-로그)
9. [테스트 실행](#9-테스트-실행)
10. [문제 해결](#10-문제-해결)

---

## 1. 시스템 요구사항

- **OS**: Windows 10/11 (PowerShell 5.1+)
- **Python**: 3.11 이상
- **의존성**: `pip install -r requirements.txt`
- **환경 변수**: `.env` 파일 필수 (아래 참조)

---

## 2. 초기 설정

### `.env` 파일 생성

프로젝트 루트에 `.env` 파일을 생성합니다:

```env
UPBIT_ACCESS_KEY=your_access_key_here
UPBIT_SECRET_KEY=your_secret_key_here
TELEGRAM_BOT_TOKEN=your_telegram_bot_token   # 선택사항
TELEGRAM_CHAT_ID=your_chat_id                # 선택사항
```

> **주의**: API 키를 코드에 직접 입력하거나 git에 커밋하지 마세요.

### 의존성 설치

```bash
pip install -r requirements.txt
```

---

## 3. 에이전트 시작 및 정지

### 권장 방법: `start.ps1` 사용

PowerShell에서 실행합니다:

```powershell
# 기본 시작 (대시보드 자동 오픈)
.\start.ps1

# 브라우저 오픈 없이 시작
.\start.ps1 -NoOpen

# 기존 프로세스 재시작
.\start.ps1 -Restart

# 에이전트 + 워치독 + 가디언 정지
.\start.ps1 -Stop
```

`start.ps1`은 다음을 자동으로 처리합니다:
- 워치독(Watchdog) 프로세스 시작 (에이전트 충돌 시 자동 재시작)
- 가디언(Guardian) 프로세스 시작 (시스템 감시)
- 대시보드가 준비될 때까지 최대 90초 대기
- 준비 완료 시 브라우저 자동 오픈

### 직접 실행 (고급)

```bash
python -m src.main \
  --mode paper \
  --strategy MeanReversionStrategy \
  --symbols BTC/KRW \
  --dashboard-port 8000
```

### 현재 실행 중인 설정

워치독은 다음 파라미터로 에이전트를 시작합니다:

| 항목 | 값 |
|------|-----|
| 전략 | MeanReversionStrategy |
| 심볼 | BTC/KRW |
| 상위 심볼 수 | 20개 (거래량 기준) |
| 인터벌 | 60초 |
| 트레일링 스탑 | 1.5% |
| 페이퍼 자본금 | 100,000 KRW |
| 하트비트 | 1800초 (30분) |
| 거래 제외 시간 | UTC 0~2시 (KST 9~11시) |
| 블랙리스트 심볼 | DOGE/KRW, ELSA/KRW, DOOD/KRW |

---

## 4. 대시보드 사용법

에이전트 시작 후 브라우저에서 접속:

```
http://localhost:8000/
```

### 대시보드 구성

| 섹션 | 설명 |
|------|------|
| 모드 표시 | 현재 PAPER / LIVE 모드 (헤더에 표시) |
| 모드 전환 버튼 | PAPER ↔ LIVE 전환 (4월 30일 이후 활성화) |
| 포지션 현황 | 오픈 포지션 목록, 진입가, 현재가, 손익 |
| 서킷 브레이커 | OPEN/CLOSED 상태, 최근 트리거 이유 |
| 시장 스캔 | 거래량 상위 심볼 티커 테이블 (SSE 실시간) |
| 전략 통계 | 승률, 총 손익, PnL, 손익 비율 |
| 최근 트레이드 | 최근 체결 내역 |

### API 엔드포인트

```
GET  /api/status          # 엔진 상태 (모드, 서킷, 포지션 수)
GET  /api/positions        # 포지션 상세
GET  /api/stats            # 전략 통계
POST /api/mode             # 모드 전환 ({"mode": "live"})
POST /api/pause            # 일시 정지 / 재개
GET  /events               # SSE 실시간 스트림
```

---

## 5. 트레이딩 모드 전환 (PAPER ↔ LIVE)

### 모드 개요

| 모드 | 설명 |
|------|------|
| PAPER | 가상 자본 100,000 KRW으로 시뮬레이션. 실제 주문 없음. |
| LIVE | 실제 Upbit 계좌로 거래. **실제 자금 손실 위험!** |

### 대시보드에서 전환

1. `http://localhost:8000/` 접속
2. 헤더의 **"LIVE 전환"** 버튼 클릭 (4월 30일 이후 활성화)
3. 확인 다이얼로그에서 **"전환"** 클릭
4. 에이전트가 자동으로 재시작되며 LIVE 모드로 전환

### 직접 전환 (고급)

`.trading_mode` 파일을 수정합니다:

```bash
# LIVE 전환
echo live > .trading_mode

# PAPER 복귀
echo paper > .trading_mode
```

파일 변경 후 워치독이 다음 에이전트 재시작 시 자동 적용합니다.

> **LIVE 전환 전 체크리스트**
> - [ ] PAPER 모드에서 충분한 검증 완료 (권장: 2주 이상)
> - [ ] 승률 > 50%, 손익비 > 1.0 확인
> - [ ] Upbit API 키에 실거래 권한 확인
> - [ ] 서킷 브레이커 설정 확인
> - [ ] 포지션 사이징 설정 확인

---

## 6. 전략 설명

### MeanReversionStrategy (현재 운용 중)

평균회귀 전략. 과매도 구간에서 매수, RSI 과매수 시 매도.

| 파라미터 | 기본값 | 설명 |
|----------|--------|------|
| `rsi_fast_period` | 7 | 빠른 RSI 기간 |
| `rsi_slow_period` | 14 | 느린 RSI 기간 |
| `rsi_oversold_fast` | 20 | 빠른 RSI 과매도 기준 |
| `rsi_oversold_slow` | 35 | 느린 RSI 과매도 기준 |
| `rsi_exit` | 62 | RSI 매도 기준 |
| `vol_mult` | 1.5 | 거래량 배수 (평균 대비) |
| `sma_period` | 0 | SMA 필터 (0 = 비활성) |
| `no_entry_hours_utc` | [0,1,2] | 진입 금지 시간대 (UTC) |

### SwingMomentumStrategy

EMA 크로스오버 + RSI + MACD를 결합한 스윙 전략. 15분봉 기준.

### Scalping5mStrategy

단기 스캘핑 전략. EMA + RSI + ADX 기반. 3분봉 기준.

### 전략 파라미터 변경

`start_agent_watchdog.ps1` 파일의 `--strategy-params` 부분을 수정합니다:

```powershell
$agentArgs = "... --strategy-params '{\"vol_mult\": 2.0, \"rsi_exit\": 65}' ..."
```

---

## 7. 리스크 관리

### 서킷 브레이커

연속 손실이 임계값을 초과하면 자동으로 거래를 중단합니다:
- 대시보드에서 OPEN/CLOSED 상태 확인 가능
- `POST /api/circuit/reset`으로 수동 리셋

### 스탑로스 / 테이크프로핏

| 설정 | 설명 |
|------|------|
| `--trailing-stop-pct 1.5` | 트레일링 스탑 1.5% |
| `--stop-loss-pct` | 고정 스탑로스 |
| `--take-profit-pct` | 테이크프로핏 |

### 포지션 사이징

기본적으로 가용 잔고의 일정 비율로 포지션을 설정합니다. LIVE 모드에서는 Upbit 실제 잔고를 동기화합니다.

---

## 8. 보고서 및 로그

### 로그 파일

| 파일 | 내용 |
|------|------|
| `logs/agent_console.log` | 에이전트 stdout/stderr |
| `logs/watchdog.log` | 워치독 이벤트 |

### 일일 보고서

`reports/` 디렉토리에 날짜별 Markdown 보고서가 생성됩니다:

```
reports/report_2026-04-28.md
```

보고서 내용:
- 전략별 승률, 손익
- 당일 체결 내역
- 시장 상태 요약
- 파라미터 검토 권고사항

### 트레이드 기록 조회

```bash
# 최근 20건 조회
python -m src.report.status_cli -n 20

# 전체 통계
python -m src.report.status_cli --stats
```

---

## 9. 테스트 실행

### 전체 테스트

```bash
pytest tests/ -v
```

### 빠른 테스트 (커버리지 제외)

```bash
pytest tests/ -q --no-cov
```

### 특정 모듈만

```bash
pytest tests/unit/test_mean_reversion_strategy.py -v
pytest tests/unit/test_engine.py -v
```

### 커버리지 확인

```bash
pytest tests/ --cov=src --cov-report=html
# 브라우저에서 htmlcov/index.html 열기
```

현재 커버리지: **80%+** (993개 테스트 통과)

---

## 10. 문제 해결

### 대시보드가 열리지 않는 경우

1. 에이전트가 실행 중인지 확인: `netstat -ano | findstr :8000`
2. 로그 확인: `Get-Content logs\agent_console.log -Tail 30`
3. 강제 재시작: `.\start.ps1 -Restart`

### API 키 오류

- `.env` 파일에 올바른 키가 있는지 확인
- Upbit 대시보드에서 IP 허용 설정 확인
- LIVE 모드: 주문 권한이 활성화되어 있는지 확인

### 서킷 브레이커가 열린 경우

1. 대시보드에서 최근 손실 내역 확인
2. 전략 파라미터 검토 (vol_mult, RSI 기준값)
3. 시장 상황 파악 후 수동 리셋

### 포지션 동기화 오류 (InsufficientFunds)

LIVE 모드에서 Upbit 실제 잔고와 내부 상태가 불일치하는 경우 자동으로 잔고를 동기화합니다. 지속 발생 시 에이전트를 재시작하세요.

### 워치독이 계속 재시작하는 경우

```powershell
# 로그 확인
Get-Content logs\watchdog.log -Tail 20
Get-Content logs\agent_console.log -Tail 50

# 에이전트 직접 실행 (디버깅)
python -m src.main --mode paper --strategy MeanReversionStrategy --symbols BTC/KRW
```

---

## 빠른 참조

| 작업 | 명령 |
|------|------|
| 에이전트 시작 | `.\start.ps1` |
| 에이전트 정지 | `.\start.ps1 -Stop` |
| 재시작 | `.\start.ps1 -Restart` |
| 대시보드 | `http://localhost:8000/` |
| 전체 테스트 | `pytest tests/ -q` |
| 최근 트레이드 | `python -m src.report.status_cli -n 20` |
| 로그 보기 | `Get-Content logs\agent_console.log -Tail 50` |
