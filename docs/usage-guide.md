# Trading Agent 사용 가이드

> 최종 수정: 2026-09-08 — 운영 환경이 로컬 Windows PC → **Oracle Cloud(OCI) VM + Docker**로
> 이전된 이후 기준으로 재작성. SSH 접속 및 서버 실측(컨테이너/설정 파일)을 근거로 함.
> GitHub 저장소는 **`aphenix-debug/trading-agent` → `BongRyoungKim/trading-agent`(Private)로
> 이전됨** (2026-09-08). 기존 `aphenix-debug/trading-agent`는 삭제하지 않고 그대로 남겨둠.

## 목차

1. [운영 환경 개요](#1-운영-환경-개요)
2. [초기 설정 (.env)](#2-초기-설정-env)
3. [에이전트 시작·정지·재기동](#3-에이전트-시작정지재기동)
4. [대시보드 사용법](#4-대시보드-사용법)
5. [트레이딩 모드 전환 (PAPER ↔ LIVE)](#5-트레이딩-모드-전환-paper--live)
6. [전략 설명](#6-전략-설명)
7. [리스크 관리](#7-리스크-관리)
8. [보고서, 자동 튜닝 및 로그](#8-보고서-자동-튜닝-및-로그)
9. [테스트 실행 (로컬 개발 환경)](#9-테스트-실행-로컬-개발-환경)
10. [문제 해결](#10-문제-해결)
11. [레거시: 로컬 Windows 실행 (개발용)](#11-레거시-로컬-windows-실행-개발용)

---

## 1. 운영 환경 개요

| 항목 | 값 |
|---|---|
| 서버 | Oracle Cloud Infrastructure, `VM.Standard.E2.1.Micro` (Always Free), Ubuntu |
| 접속 | `ssh -i ./ssh-key-2026-08-25.key ubuntu@129.225.163.29` |
| 실행 방식 | Docker Compose (`~/trading-agent/docker-compose.yml`) |
| 대시보드 | `http://129.225.163.29:8081/` |
| 헬스체크 | `http://129.225.163.29:8080/health` |
| 코드 관리 | GitHub (`origin`, branch `master`) — 그대로 유지 |

> 이후 모든 명령은 **서버에 SSH로 접속한 상태에서 `~/trading-agent` 디렉터리 기준**으로 실행한다.
> 로컬 Windows PC에서의 직접 실행(`start.ps1` 등)은 11번 "레거시" 절을 참고 — 개발/백테스트용으로만
> 유지되며 실거래 운영에는 사용하지 않는다.

---

## 2. 초기 설정 (.env)

서버에 SSH로 접속해 `.env`를 직접 편집한다 (API 키 등 민감정보는 절대 채팅이나 문서에 남기지 않는다):

```bash
ssh -i ./ssh-key-2026-08-25.key ubuntu@129.225.163.29
cd ~/trading-agent
nano .env
```

```env
EXCHANGE=upbit
TRADING_MODE=paper                          # 처음에는 paper로 시작 권장
UPBIT_ACCESS_KEY=your_access_key_here
UPBIT_SECRET_KEY=your_secret_key_here
TELEGRAM_BOT_TOKEN=your_telegram_bot_token   # 선택사항
TELEGRAM_CHAT_ID=your_chat_id                # 선택사항
```

> **주의**: `.env`는 `.gitignore`에 포함되어 git에 올라가지 않는다. 서버 재구축 시에는 별도
> 백업/복원이 필요하다 (`migration-guide.md` 참고). `setup_trading_server.sh`도 API 키 입력은
> 절대 자동으로 처리하지 않고 사람이 직접 `nano .env`로 입력하도록 안내한다.
>
> **하드 게이트**: `TRADING_MODE=live`인데 `EXCHANGE`로 지정한 거래소의 API 키/시크릿이 `.env`에
> 비어 있으면 설정 로드 시점에 `MissingCredentialsError`로 즉시 실패한다 (컨테이너가 계속
> 재시작을 반복하는 형태로 나타난다).

### 전체 환경 변수 (`src/config/settings.py` 기준, 기본값)

| 변수 | 기본값 | 설명 |
|---|---|---|
| `TRADING_MODE` | `paper` | `live` / `paper` / `backtest` |
| `EXCHANGE` | `upbit` | `binance` / `upbit` / `bybit` |
| `MAX_POSITION_RISK` | `0.02` | 포지션당 최대 리스크 비율 (0~1) |
| `MAX_DAILY_LOSS` | `0.05` | 일일 최대 손실 비율 (0~1) |
| `MAX_OPEN_POSITIONS` | `5` | 최대 동시 오픈 포지션 수 |
| `MAX_DRAWDOWN_HALT` | `0.15` | 최대 낙폭 도달 시 자동 정지 (0~1) |
| `LOG_LEVEL` | `INFO` | 로그 레벨 |
| `LOG_FILE_PATH` | `logs/trading_agent.log` | 로그 파일 경로 (컨테이너 내부 경로, `./logs`에 볼륨 마운트됨) |
| `MARKET_HOURS_ENABLED` | `false` | 거래 시간 제한 사용 여부 |
| `TRADING_HOURS` | `00:00-23:59` | 거래 허용 시간대 (UTC) |
| `TRADING_DAYS` | `mon-sun` | 거래 허용 요일 |
| `DASHBOARD_PORT` | `0` (비활성) | CLI 직접 실행 시에만 의미 있음 — 컨테이너는 `entrypoint.sh`가 `--dashboard-port 8081`을 고정 전달 |

---

## 3. 에이전트 시작·정지·재기동

### 3-1. 기본 명령 (Docker Compose)

```bash
cd ~/trading-agent

# 시작 (이미지 변경 시 --build)
docker compose up -d --build

# 상태 확인
docker compose ps

# 로그 실시간 확인
docker compose logs -f trading-agent

# 정지
docker compose down

# 재기동 (코드/설정 변경 반영)
docker compose restart trading-agent
```

`docker-compose.yml`은 `restart: unless-stopped` 정책이므로, 컨테이너 안의 프로세스가 죽거나
`engine.stop()`이 호출되면 **Docker가 자동으로 컨테이너를 다시 시작**한다 (Windows판의
`start_agent_watchdog.ps1` 워치독 루프 역할을 Docker 자체가 대신한다).

### 3-2. 실제 실행 중인 설정 (`entrypoint.sh` 기준, 컨테이너 시작 커맨드)

`entrypoint.sh`는 컨테이너 시작 시 아래 값으로 `python -m src.main`을 실행한다:

| 항목 | 값 |
|------|-----|
| 전략 | `RegimeAdaptiveStrategy` (고정) |
| 심볼 | BTC/KRW |
| 상위 심볼 수 | 12개 (거래량 기준, `--top-symbols 12`) |
| 인터벌 | 60초 |
| 트레일링 스탑 | 2.0% |
| 하트비트 | 1800초 (30분) |
| 대시보드 포트 | **8081** (헬스체크는 별도로 8080) |
| 블랙리스트 심볼 | DOGE/KRW, ELSA/KRW, DOOD/KRW, RE/KRW, KAITO/KRW, USDT/KRW |
| 뉴스 감성 자동 튜닝 | 매일 KST 8시 (`--news-tuning-hour 8`, dry-run — `--news-tuning-apply` 없음) |
| 프리플라이트 체크 | **`--skip-checks`로 생략됨** (아래 참고) |
| 모드 | 저장소 루트 `.trading_mode` 파일 값 |

> ⚠️ **`.trading_mode` 파일이 없으면 `entrypoint.sh`는 기본값을 `live`로 설정**하고 파일을 새로
> 만든다 (Windows 워치독은 반대로 `paper`가 기본값이었다 — 서버 이관·재구축 시 특히 주의).
>
> ⚠️ **`--skip-checks`가 걸려 있어 5개 프리플라이트 체크(`credentials`,
> `exchange_connectivity`, `risk_params`, `db_writable`, `telegram`)가 시작 시 실행되지 않는다.**
> 다만 이는 시작 전 사전 점검만 생략하는 것이며, `RiskManager`의 낙폭/일일손실/포지션수 하드
> 리밋과 서킷 브레이커는 이 플래그와 무관하게 항상 동작한다.
>
> `--strategy-params`, `--sl-floor-pct` 등 세부 파라미터는 `.strategy_params.json`을
> `scripts/render_launch_args.py`가 CLI 인자로 변환해 추가한다 (8번 항목 참고).

### 3-3. 이미지/코드 갱신 절차

```bash
cd ~/trading-agent
git pull
docker compose up -d --build   # 이미지 재빌드 후 무중단에 가깝게 재기동
```

---

## 4. 대시보드 사용법

브라우저에서 접속:

```
http://129.225.163.29:8081/
```

### 대시보드 구성

| 섹션 | 설명 |
|------|------|
| 모드 표시 | 현재 PAPER / LIVE 모드 (헤더에 표시) |
| 모드 전환 버튼 | PAPER ↔ LIVE 전환 (`.trading_mode` 파일 갱신, 5번 참고) |
| 포지션 현황 | 오픈 포지션 목록, 진입가, 현재가, 손익 |
| 서킷 브레이커 | CLOSED/OPEN/HALF_OPEN 상태 |
| 시장 스캔 | 거래량 상위 심볼 티커 테이블 (SSE 실시간) |
| 전략 통계 | 승률, 총 손익, PnL, 손익 비율 |
| 최근 트레이드 | 최근 체결 내역 |

### API 엔드포인트 (`src/dashboard/app.py` 기준)

```
GET  /                        # HTML 대시보드
GET  /api/balance
GET  /api/strategy
GET  /api/ticks
GET  /api/ticks/stream        # SSE 실시간 스트림 (심볼별 틱)
GET  /api/status               # 엔진 모드, paused 여부, 서킷 상태, 거래시간
GET  /api/positions
GET  /api/pnl
GET  /api/trades?limit=50     # 1~500, 기본 50
GET  /api/stats
GET  /api/equity
POST /api/engine/pause
POST /api/engine/resume
POST /api/engine/set-mode     # body {"mode": "paper"|"live"}
GET  /api/engine/mode
```

서버 로컬에서 직접 확인하려면:

```bash
curl http://localhost:8081/api/status
curl http://localhost:8080/health
```

---

## 5. 트레이딩 모드 전환 (PAPER ↔ LIVE)

### 모드 개요

| 모드 | 설명 |
|------|------|
| PAPER | 가상 자본(기본 100,000 KRW)으로 시뮬레이션. 실제 주문 없음. |
| LIVE | 실제 거래소 계좌로 거래. **실제 자금 손실 위험!** |

### 대시보드에서 전환

1. `http://129.225.163.29:8081/` 접속
2. 헤더의 모드 전환 버튼 클릭 → 확인
3. `POST /api/engine/set-mode`가 저장소 루트의 `.trading_mode` 파일을 갱신하고 `engine.stop()`을
   호출한다. 컨테이너 프로세스가 종료되면 `docker-compose.yml`의 `restart: unless-stopped`
   정책에 따라 **Docker가 컨테이너를 자동으로 재시작**하고, `entrypoint.sh`가 갱신된
   `.trading_mode` 값을 읽어 새 모드로 기동한다.
4. `GET /api/engine/mode`로 반영된 모드를 확인할 수 있다.

### SSH로 직접 전환

```bash
ssh -i ./ssh-key-2026-08-25.key ubuntu@129.225.163.29
cd ~/trading-agent
echo -n "live" > .trading_mode     # 또는 "paper"
docker compose restart trading-agent
```

> **LIVE 전환 전 체크리스트**
> - [ ] PAPER 모드에서 충분한 검증 완료 (권장: 2주 이상)
> - [ ] 승률 > 50%, 손익비 > 1.0 확인
> - [ ] 거래소 API 키에 실거래 권한 확인
> - [ ] `.env`의 `MAX_*` 리스크 값 확인
> - [ ] 서킷 브레이커 상태 확인 (`/api/status` 또는 텔레그램 `/status`)
> - [ ] `entrypoint.sh`는 `--skip-checks`로 프리플라이트 점검을 생략하므로, API 키/연결 상태는
>       전환 전에 사람이 직접 확인해야 한다 (자동 점검에 기대지 말 것)

---

## 6. 전략 설명

전략은 `src/strategy/registry.py`의 `@register` 데코레이터로 등록되며, 전체 목록은 아래로 확인:

```bash
docker compose exec trading-agent python -m src.main --list-strategies
```

### RegimeAdaptiveStrategy (현재 운용 중, 15분봉)

ADX + EMA 기반으로 시장 레짐(추세/횡보)을 판단해 하위 전략을 자동 전환하는 라우터형 전략.

- 상승 추세(ADX ≥ `adx_trend_threshold`, 상승 EMA 배열) → `SwingMomentumStrategy`로 전환
- 횡보/하락 → `MeanReversionStrategy`로 전환

| 파라미터 | 기본값 | **현재 서버 실제값**(`.strategy_params.json`, 자동 튜닝 결과) |
|---|---|---|
| `adx_trend_threshold` | 25.0 | 25.0 |
| `ema_fast` / `ema_slow` | 20 / 50 | (기본값 사용) |
| `mr_vol_mult` | 1.0 | **0.5** |
| `mr_rsi_oversold_fast` | 20.0 | **28.0** |
| `mr_rsi_oversold_slow` | 30.0 | **38.0** |
| `mr_rsi_exit` | 55.0 | **60.0** |
| `mr_no_entry_hours_utc` | 없음 | `[17, 18, 19, 20]` |
| `sm_vol_mult` | 1.5 | **0.4** |
| `sm_adx_threshold` | 28.0 | 28.0 |

> 위 "현재 서버 실제값"은 `ScheduledTaskRunner`가 자동 조정한 값이라 **주기적으로 바뀐다**.
> 정확한 현재값은 `cat ~/trading-agent/.strategy_params.json`으로 항상 다시 확인할 것 — 이
> 표는 스냅샷일 뿐이다.

### MeanReversionStrategy (15분봉)

평균회귀 전략. 과매도 구간에서 매수, RSI 과매수/설정 기준 도달 시 매도.

| 파라미터 | 기본값 | 설명 |
|----------|--------|------|
| `rsi_fast_period` / `rsi_slow_period` | 5 / 14 | RSI 기간 |
| `rsi_oversold_fast` / `rsi_oversold_slow` | 25.0 / 35.0 | 과매도 기준 |
| `rsi_exit` / `rsi_exit_fast` | 60.0 / 70.0 | 매도 기준 |
| `vol_mult` | 1.5 | 거래량 배수 (평균 대비) |
| `atr_period` | 14 | ATR 기간 |
| `sma_period` | 0 | SMA 필터 (0 = 비활성) |
| `sma_floor` | 0.85 | SMA 필터 하한 비율 |
| `no_entry_hours_utc` | 없음 | 진입 금지 시간대 (UTC) |

### SwingMomentumStrategy (15분봉)

EMA 추세추종 + 눌림목(pullback) + RSI/MACD 확인을 결합한 스윙 전략.

| 파라미터 | 기본값 |
|---|---|
| `ema_fast` / `ema_slow` | 20 / 50 |
| `rsi_period` | 14 |
| `rsi_oversold` / `rsi_max` / `rsi_overbought` | 40.0 / 58.0 / 72.0 |
| `adx_threshold` | 28.0 |
| `vol_mult` | 2.0 |
| `ema_proximity_pct` | 2.5 |
| `atr_period` | 14 |
| `macd_fast` / `macd_slow` / `macd_signal` | 12 / 26 / 9 |
| `rsi_lookback` | 4 |

### Scalping5mStrategy (3분봉, 파일명과 달리 실제 타임프레임은 3분)

단기 스캘핑 전략. EMA + RSI + ATR + 거래량 + ADX + MACD 결합.

| 파라미터 | 기본값 |
|---|---|
| `ema_fast` / `ema_slow` | 9 / 21 |
| `rsi_period` / `rsi_min` / `overbought` | 7 / 40.0 / 72.0 |
| `atr_period` | 14 |
| `vol_mult` | 1.5 |
| `adx_period` / `adx_threshold` | 14 / 25.0 |
| `macd_fast` / `macd_slow` / `macd_signal` / `macd_window` | 12 / 26 / 9 / 6 |
| `ema_proximity_pct` | 3.0 |

### 그 외 등록 전략

| 전략 | 설명 |
|---|---|
| `MACrossoverStrategy` | EMA/SMA 골든/데드크로스 (`--strategy` CLI 기본값). `fast_period=20, slow_period=50, ma_type="ema"` |
| `RSIStrategy` | RSI 과매수/과매도 (`period=14, oversold=30.0, overbought=70.0`) |
| `MomentumStrategy` | ROC 기반 모멘텀 + RSI 확인 |
| `BollingerBandStrategy` | 볼린저 밴드 (`period=20, std_dev=2.0`) |
| `VWAPStrategy` | VWAP 기준 매매 (`ema_period=20`, 0이면 EMA 확인 비활성) |
| `RSIMomentumStrategy` | "로스 카메론" 스타일, RSI+모멘텀+거래량 |
| `CompositeStrategy` | 여러 전략을 `all`/`any`/`majority` 모드로 결합 |

`SymbolStrategyRouter`(`--symbol-strategy` 옵션 전용)는 레지스트리에 등록된 전략이 아니라
심볼별 전략 라우팅을 위한 별도 팩토리다.

### 전략 파라미터 변경

컨테이너를 쓰는 운영 환경에서는 `.strategy_params.json`을 직접 수정한 뒤 재기동한다:

```bash
nano ~/trading-agent/.strategy_params.json
docker compose restart trading-agent
```

또는 별도 전략 이름으로 직접 실행할 경우 `--strategy-params` JSON 문자열로 전달한다.

---

## 7. 리스크 관리

### 서킷 브레이커 (`src/utils/circuit_breaker.py`)

연속 실패/손실이 임계값을 초과하면 자동으로 거래를 중단합니다 (`CLOSED` → `OPEN` → 일정 시간 후
`HALF_OPEN`).
- 대시보드/`GET /api/status`에서 CLOSED/OPEN/HALF_OPEN 상태 확인 가능
- 텔레그램 `/status` 명령으로도 확인 가능
- ⚠️ **HTTP를 통한 수동 리셋 엔드포인트는 존재하지 않는다** (`POST /api/circuit/reset` 없음).
  리셋이 필요하면 컨테이너를 재시작한다 (`docker compose restart trading-agent`).

### 손절 / 익절 (ATR 기반 동적 계산, `RiskManager.calculate_stop_loss`)

고정 퍼센트 플래그(`--stop-loss-pct`/`--take-profit-pct`)는 존재하지 않는다. ATR 기반 동적 계산과
트레일링 스탑을 사용한다.

| 설정 | 기본값 | 현재 서버 실제값 | 설명 |
|------|--------|---|------|
| `--trailing-stop-pct` | 비활성 | 2.0 | 트레일링 스탑 비율(%) |
| `--sl-floor-pct` | 3.0 | 3.0 | 손절 최대 거리(%) |
| `--sl-ceiling-pct` | 1.5 | 1.5 | 손절 최소 거리(%) |
| `--atr-multiplier` | 2.0 | 2.0 | 손절폭 = ATR × 이 배수 (floor~ceiling 범위로 clamp) |
| `--tp-rr-multiplier` | 1.5 | 1.5 | 익절폭 = 손절폭 × 이 배수 |

### 포지션 사이징

`src/risk/position_sizing.py`의 `percent_of_equity` 기준으로 가용 자본의 일정 비율만큼 포지션을
설정한다. LIVE 모드에서는 거래소 실제 잔고를 조회해 동기화한다.

### RiskManager 하드 리밋 (`src/risk/manager.py`, `--skip-checks`와 무관하게 항상 동작)

- `check_drawdown()`: 낙폭이 `MAX_DRAWDOWN_HALT` 도달 시 `MaxDrawdownExceededError`
- `check_daily_loss()`: 일일 손실이 `MAX_DAILY_LOSS` 도달 시 진입 차단, 자정(KST) 리셋
- `check_position_limit()`: 오픈 포지션 수가 `MAX_OPEN_POSITIONS` 초과 시 `PositionLimitExceededError`

---

## 8. 보고서, 자동 튜닝 및 로그

### 로그 파일 (호스트 경로, `docker-compose.yml` 볼륨 마운트 기준)

| 파일 | 내용 |
|------|------|
| `~/trading-agent/logs/trading_agent.log` | 애플리케이션 로거 출력 (`LOG_FILE_PATH`) |
| `~/trading-agent/logs/*.log.zip` | 로테이션된 과거 로그 |

```bash
docker compose logs -f trading-agent          # 컨테이너 stdout (실시간)
tail -f ~/trading-agent/logs/trading_agent.log  # 애플리케이션 로그 파일 (실시간)
```

### 일일 보고서 (`src/report/generator.py`, `DailyReportGenerator`)

`~/trading-agent/reports/` 디렉토리(호스트에 볼륨 마운트됨)에 날짜별(KST 기준) Markdown
보고서가 생성된다:

```
reports/2026-09-08.md
```

### 자동 파라미터 튜닝 (`ScheduledTaskRunner`, `src/report/task_runner.py`)

보고서 생성 시점마다 아래 조건을 평가해 필요 시 전략/리스크 파라미터를 자동 조정한다:

- 평가 조건: `param_review_20`, `wr_alert`(승률 경고), `pnl_alert`, `sl_review`, `walk_forward_50`
- 조정값은 `src/config/live_params.py`의 `PARAM_BOUNDS`로 범위가 clamp되고, 1회 조정폭은
  최대 ±20%(`MAX_STEP_FRACTION`)로 제한된다
- 변경 이력: `reports/param_change_log.jsonl`
- 실행 상태: `reports/.task_state.json`
- 재시작 필요 시 `.restart_requested` 플래그 파일을 남겨 다음 재기동 시 반영

조정 결과는 저장소 루트 `.strategy_params.json`에 반영되며, **이 파일은 git으로 관리되지
않는 서버 로컬 상태**다 (이전 값은 `.strategy_params.json.bak-YYYYMMDD-HHMMSS` 형식으로 백업됨).

### 뉴스 감성 자동 튜닝 (`src/report/news_tuner.py`)

현재 서버는 매일 KST 8시(`--news-tuning-hour 8`)에 뉴스 RSS 감성을 분석해 파라미터 조정을
제안한다. `--news-tuning-apply`가 붙어 있지 않으므로 **dry-run** — 제안만 로그에 남고 실제
적용되지는 않는다.

### 트레이드 기록 조회 (`src/status_cli.py`)

컨테이너 내부에서 실행한다 (호스트에는 Python 의존성이 없을 수 있음):

```bash
# 전체 요약 (포지션 + 최근 거래 + 통계)
docker compose exec trading-agent python -m src.status_cli

# 최근 20건 거래 내역
docker compose exec trading-agent python -m src.status_cli trades -n 20

# 오픈 포지션만
docker compose exec trading-agent python -m src.status_cli portfolio

# 전체 통계 (승률/손익비/PnL)
docker compose exec trading-agent python -m src.status_cli stats
```

---

## 9. 테스트 실행 (로컬 개발 환경)

> 운영 서버(`VM.Standard.E2.1.Micro`, 메모리 1GB)는 실거래 컨테이너 전용이며, `tests/` 디렉토리도
> Docker 이미지에 포함되어 있지 않다(`Dockerfile`이 `src/`만 COPY함). **테스트는 로컬 개발
> PC(또는 별도 워크스테이션)에서 저장소를 클론한 상태로 실행한다.**

```bash
git clone https://github.com/BongRyoungKim/trading-agent.git
cd trading-agent
pip install -r requirements.txt

pytest tests/ -v                                  # 전체 테스트
pytest tests/ -q --no-cov                          # 빠른 테스트 (커버리지 제외)
pytest tests/unit/test_engine.py -v                # 특정 모듈만
pytest tests/ --cov=src --cov-report=html           # 커버리지 (htmlcov/index.html)
```

`pytest.ini` 설정: `--cov-fail-under=80`. 아래 3개 파일은 실행에서 제외(`--ignore`)된다:
`tests/unit/test_collector.py`, `tests/unit/test_data_cli.py`, `tests/unit/test_storage.py`.

> 테스트 규모(추정 — `pytest --collect-only`로 직접 확인 권장): `tests/unit/` +
> `tests/integration/` 아래 테스트 파일 약 56개, `def test_...` 정의 약 1,156개.

---

## 10. 문제 해결

### 대시보드가 안 열리는 경우

```bash
ssh -i ./ssh-key-2026-08-25.key ubuntu@129.225.163.29
docker compose ps                                  # 컨테이너 상태 확인
docker compose logs --tail 50 trading-agent
sudo ufw status verbose                            # 8081 ALLOW 확인
# OCI Security List(VCN 인바운드)도 8081이 열려 있는지 콘솔에서 확인
```

### API 키 오류 / 컨테이너가 계속 재시작을 반복함

```bash
docker compose logs trading-agent | grep -i -E "MissingCredentialsError|error"
```

- `.env`에 올바른 키가 있는지 확인
- 거래소 대시보드에서 IP 허용 설정 확인 (서버 공인 IP 등록 필요할 수 있음)
- `TRADING_MODE=live`인데 키가 비어 있으면 `MissingCredentialsError`로 즉시 종료 → 재시작 반복

### 서킷 브레이커가 열린 경우

1. `/api/status` 또는 텔레그램 `/status`에서 상태 확인
2. `.strategy_params.json`의 파라미터 검토
3. `docker compose restart trading-agent`로 리셋 (HTTP 리셋 엔드포인트는 없음)

### 포지션 동기화 오류 (InsufficientFunds)

LIVE 모드에서 거래소 실제 잔고와 내부 상태가 불일치하면 자동으로 동기화를 시도한다. 지속되면
컨테이너를 재시작한다.

### 컨테이너가 계속 재시작되는 경우 (`MissingCredentialsError` 외)

```bash
docker compose logs --tail 100 trading-agent
docker inspect --format='{{json .State.Health}}' trading-agent-trading-agent-1
```

### 메모리 부족(OOM)

```bash
free -h   # Swap 2.0G 존재 확인 (setup_trading_server.sh가 생성)
docker stats --no-stream
```

### 실수로 `.trading_mode` 없이 재기동해 `live`로 시작된 경우

`entrypoint.sh`는 `.trading_mode`가 없으면 `live`를 기본값으로 새로 만든다.

```bash
docker compose stop trading-agent
echo -n "paper" > ~/trading-agent/.trading_mode
docker compose up -d trading-agent
```

---

## 11. 레거시: 로컬 Windows 실행 (개발용)

과거 로컬 PC 운영 시절 스크립트로, 저장소에는 남아 있지만 **실거래 운영에는 더 이상 사용하지
않는다.** 로컬 개발·디버깅·백테스트 실행 용도로만 참고한다.

```powershell
# 시작 (대시보드 자동 오픈)
.\start.ps1

# 정지
.\start.ps1 -Stop

# 재시작
.\start.ps1 -Restart

# 직접 실행 (디버깅)
python -m src.main --mode paper --strategy RegimeAdaptiveStrategy --symbols BTC/KRW --dashboard-port 8000
```

`start.ps1`은 `start_agent_watchdog.ps1`(자동 재시작)과 `guardian.ps1`(워치독 생존 감시)을
백그라운드로 띄우던 방식으로, 지금은 이 역할을 Docker의 `restart: unless-stopped` 정책이
대신한다. `start_agent.bat`, `start_agent_watchdog.bat`은 그보다 더 오래된 설정이 남아있는
보조 스크립트이므로 참고용으로만 본다.

---

## 빠른 참조

| 작업 | 명령 |
|------|------|
| 서버 접속 | `ssh -i ./ssh-key-2026-08-25.key ubuntu@129.225.163.29` |
| 에이전트 시작 | `docker compose up -d --build` |
| 에이전트 정지 | `docker compose down` |
| 재시작 | `docker compose restart trading-agent` |
| 상태 확인 | `docker compose ps` |
| 로그 실시간 | `docker compose logs -f trading-agent` |
| 대시보드 | `http://129.225.163.29:8081/` |
| 헬스체크 | `http://129.225.163.29:8080/health` |
| 최근 트레이드 | `docker compose exec trading-agent python -m src.status_cli trades -n 20` |
| 전체 테스트 (로컬 개발환경) | `pytest tests/ -q` |
| 등록 전략 목록 | `docker compose exec trading-agent python -m src.main --list-strategies` |
