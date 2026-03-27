# Trading Agent - 구현 계획

## Phase 1: 프로젝트 기반 구축 ✅ (완료 2026-03-26)

- [x] 디렉토리 구조 생성 (`src/`, `tests/`, `data/`, `logs/`)
- [x] `requirements.txt` 작성 (ccxt, pandas, numpy, loguru, pydantic-settings, pytest 등)
- [x] `.env.example` 파일 생성
- [x] 기본 설정 모듈 구현 (`src/config/settings.py`)
- [x] 로깅 설정 (`src/utils/logger.py`)
- [x] 기본 예외 클래스 정의 (`src/utils/exceptions.py`)
- [x] 53개 테스트 통과, 커버리지 86.6%

## Phase 2: 거래소 연동 ✅ (완료 2026-03-26)

- [x] `BaseExchangeClient` 추상 클래스 정의
- [x] Binance 클라이언트 구현 (`src/exchange/binance.py`)
- [x] Upbit 클라이언트 구현 (`src/exchange/upbit.py`)
- [x] 재시도 데코레이터 구현 (exponential backoff, sync/async)
- [x] 거래소 연동 단위 테스트 (89개 통과, 86.3% 커버리지)
- [x] WebSocket 실시간 데이터 수신 — REST 폴링 방식으로 대체 (APScheduler interval)

## Phase 3: 데이터 관리 ✅ (완료 2026-03-26)

- [x] OHLCV 데이터 수집기 구현 (`src/data/collector.py`)
- [x] 로컬 데이터 저장소 (SQLite, WAL 모드) 구현 (`src/data/storage.py`)
- [x] 기술적 지표 계산 유틸리티 (`src/utils/indicators.py`) — SMA/EMA/RSI/MACD/BB/ATR/VWAP
- [x] 히스토리 데이터 다운로드 CLI (`src/data/cli.py`)
- [x] 132개 테스트 통과, 커버리지 81.95%

## Phase 4: 전략 엔진 ✅ (완료 2026-03-26)

- [x] `BaseStrategy` 추상 클래스 정의 (validate_data 포함)
- [x] 이동평균 크로스오버 전략 구현 (`src/strategy/ma_crossover.py`) — SMA/EMA 지원
- [x] RSI 전략 구현 (`src/strategy/rsi_strategy.py`)
- [x] 볼린저 밴드 전략 구현 (`src/strategy/bollinger.py`)
- [x] 전략 레지스트리 구현 (`@register` 데코레이터)
- [x] Signal 불변 모델 구현
- [x] 160개 테스트 통과, 커버리지 85.03%

## Phase 5: 백테스팅 엔진 ✅ (완료 2026-03-26)

- [x] 백테스트 러너 구현 (`src/backtest/runner.py`) — 이벤트 드리븐, look-ahead bias 없음
- [x] 수수료/슬리피지 시뮬레이션 (BacktestConfig)
- [x] 성과 지표 계산 모듈 (`src/backtest/metrics.py`) — Sharpe/Sortino/MDD/WinRate/PF
- [x] 결과 시각화 (`src/backtest/visualizer.py`) — matplotlib, equity curve + drawdown + trade PnL 3패널
- [x] Walk-forward 분석 (`src/backtest/walk_forward.py`) — rolling/anchored 모드, efficiency ratio
- [x] 186개 테스트 통과, 커버리지 87.36%

## Phase 6: 리스크 관리 ✅ (완료 2026-03-26)

- [x] `RiskManager` 클래스 구현 (`src/risk/manager.py`) — 드로다운/일일손실/포지션 한도
- [x] 포지션 사이징 모듈 (`src/risk/position_sizing.py`) — Fixed Fraction, Kelly, %Equity
- [x] 스톱로스 자동 설정 (ATR 기반 / 고정 비율 / 기본값)
- [x] 드로다운 모니터링 및 자동 중단 (MaxDrawdownExceededError)
- [x] 214개 테스트 통과, 커버리지 87.83%

## Phase 7: 포트폴리오 관리 ✅ (완료 2026-03-26)

- [x] 포지션 추적기 (`src/portfolio/tracker.py`)
- [x] PnL 계산 모듈 (`src/portfolio/models.py`)
- [x] 포트폴리오 상태 조회 API (snapshot, pnl_report)
- [x] 246개 테스트 통과, 커버리지 88.65%

## Phase 8: 알림 시스템 ✅ (완료 2026-03-26)

- [x] Telegram 봇 클라이언트 (`src/utils/telegram.py`) — stdlib urllib 사용
- [x] 이벤트 기반 알림 발송 (주문체결/포지션청산/리스크경보/에러)
- [x] 일일 성과 리포트 템플릿
- [x] 247개 테스트 통과, 커버리지 88.70%

## Phase 9: 매매 실행 엔진 ✅ (완료 2026-03-26)

- [x] `TradingEngine` 메인 루프 구현 (`src/engine.py`)
- [x] 페이퍼 트레이딩 모드 구현 (시뮬레이션 체결, 0.1% 수수료)
- [x] 라이브 트레이딩 모드 구현 (실거래소 주문)
- [x] 스케줄러 연동 (APScheduler BackgroundScheduler)
- [x] 그레이스풀 셧다운 처리 (SIGINT/SIGTERM)
- [x] `src/main.py` 완성 (CLI argparse, 모든 컴포넌트 조립)
- [x] 270개 테스트 통과, 커버리지 89.40%

## Phase 10: 통합 테스트 & 배포 ✅ (완료 2026-03-26)

- [x] 엔드투엔드 통합 테스트 (`tests/integration/test_engine_integration.py`) — 전체 루프 9개 시나리오
- [x] Docker 컨테이너화 (`Dockerfile`, `.dockerignore`) — python:3.11-slim, 비루트 사용자
- [x] `docker-compose.yml` — paper trading + backtest 서비스
- [x] GitHub Actions CI/CD 파이프라인 (`.github/workflows/ci.yml`) — test/lint/docker 3단계
- [x] 279개 테스트 통과, 커버리지 87.80%

## Phase 11: 백테스트 고도화 ✅ (완료 2026-03-26)

- [x] 파라미터 최적화 엔진 (`src/backtest/optimizer.py`) — grid search, 6개 랭킹 지표
- [x] Walk-forward 분석 (`src/backtest/walk_forward.py`) — rolling/anchored, efficiency ratio
- [x] 백테스트 결과 시각화 (`src/backtest/visualizer.py`) — equity/drawdown/trade PnL 3패널
- [x] 백테스트 CLI (`src/backtest_cli.py`) — single/optimize/walk-forward 모드
- [x] `requirements.txt` 정리 (미사용 패키지 제거, matplotlib 추가)
- [x] 330개 테스트 통과, 커버리지 82.04%

## Phase 12: 엔진 강화 & 전략 확장 ✅ (완료 2026-03-26)

- [x] 스톱로스 / 테이크프로핏 자동 집행 (엔진 tick마다 가격 체크)
- [x] 일일 리포트 자동 스케줄링 (APScheduler cron, Telegram 활성화 시에만)
- [x] 복합 전략 (`src/strategy/composite.py`) — all/any/majority 투표 모드
- [x] 퍼-심볼 전략 지원 (`dict[str, BaseStrategy]`)
- [x] 359개 테스트 통과, 커버리지 82.55%

## Phase 13: 트레이드 저널 & 커버리지 강화 ✅ (완료 2026-03-26)

- [x] 트레이드 저널 구현 (`src/portfolio/journal.py`) — TradeRecord, TradeJournal
- [x] 엔진 `_close_position`에 저널 연동 — 매 청산마다 TradeRecord 기록
- [x] 일일 리포트 win_rate 수정 — 0.0 하드코딩 → journal.stats() 연동
- [x] `src/main.py` 버그 수정 — `MACrossoverStrategy()` → `MACrossoverStrategy(symbol=args.symbols[0])`
- [x] 0% 커버리지 모듈 테스트 추가 — `test_data_cli.py`, `test_main.py`, `test_backtest_cli.py`, `test_trade_journal.py`
- [x] 엔진 저널 통합 테스트 (`TestJournalIntegration`)
- [x] 423개 테스트 통과, 커버리지 91.93%

## Phase 14: 저널 영속성 (SQLite) ✅ (완료 2026-03-26)

- [x] `SQLiteJournalStore` 구현 (`src/portfolio/journal_store.py`) — WAL 모드 SQLite, TradeRecord 직렬화/역직렬화
- [x] `TradeJournal.from_store()` 팩토리 — 엔진 재시작 시 거래 이력 복원
- [x] `TradeJournal(store=...)` — `record()` 호출 시 자동 영속화
- [x] 엔진 `journal_store` 옵션 파라미터 추가 — 스토어 없으면 in-memory 폴백
- [x] `src/main.py` — `SQLiteJournalStore()` 생성 후 엔진에 전달
- [x] `test_journal_store.py` — 21개 테스트 (영속성, round-trip, 재시작 복원 시나리오)
- [x] 444개 테스트 통과, 커버리지 92.03%

## Phase 15: 포지션 영속성 (SQLite) ✅ (완료 2026-03-26)

- [x] `SQLitePositionStore` 구현 (`src/portfolio/position_store.py`) — 심볼별 upsert/delete, WAL 모드
- [x] `PortfolioTracker(store=...)` — `open_position()` 시 자동 저장, `close_position()` 시 자동 삭제
- [x] `PortfolioTracker.from_store()` 팩토리 — 복원된 포지션만큼 cash 차감, stop_loss/take_profit 포함
- [x] `src/main.py` — `SQLitePositionStore()` 생성 후 `from_store()`로 포트폴리오 초기화
- [x] `test_position_store.py` — 26개 테스트 (재시작 복원, cash 조정, SL/TP 보존)
- [x] 470개 테스트 통과, 커버리지 92.16%

- [x] `SQLiteJournalStore` 구현 (`src/portfolio/journal_store.py`) — WAL 모드 SQLite, TradeRecord 직렬화/역직렬화
- [x] `TradeJournal.from_store()` 팩토리 — 엔진 재시작 시 거래 이력 복원
- [x] `TradeJournal(store=...)` — `record()` 호출 시 자동 영속화
- [x] 엔진 `journal_store` 옵션 파라미터 추가 — 스토어 없으면 in-memory 폴백
- [x] `src/main.py` — `SQLiteJournalStore()` 생성 후 엔진에 전달
- [x] `test_journal_store.py` — 21개 테스트 (영속성, round-trip, 재시작 복원 시나리오)
- [x] 444개 테스트 통과, 커버리지 92.03%

## Phase 16: 상태 모니터링 CLI & 포지션 정합성 검증 ✅ (완료 2026-03-26)

- [x] `src/status_cli.py` — 실행 중 엔진 없이 SQLite 직접 조회
  - `portfolio` 서브커맨드: 열린 포지션, entry price, SL/TP
  - `trades [-n N]` 서브커맨드: 최근 N개 거래 내역
  - `stats` 서브커맨드: win rate, PF, avg win/loss, total PnL
  - 인수 없이 실행 시 전체 요약 출력
- [x] `_reconcile_positions()` — 라이브 모드 시작 시 로컬 DB vs 거래소 비교, 고스트 포지션 Telegram 경보
- [x] `test_status_cli.py` — 29개 테스트 (서브커맨드, 포맷, 엣지케이스)
- [x] `TestReconciliation` — 5개 테스트 (ghost alert, ticker error 스왈로우, paper/live 분기)
- [x] 504개 테스트 통과, 커버리지 92.40%

## Phase 17: VWAP 전략 & 모멘텀 전략 & main.py CLI 확장 ✅ (완료 2026-03-26)

- [x] `VWAPStrategy` 구현 (`src/strategy/vwap_strategy.py`) — VWAP 크로스오버 + 선택적 EMA 확인 필터
- [x] `MomentumStrategy` 구현 (`src/strategy/momentum.py`) — Rate of Change (ROC) + 선택적 RSI 필터
- [x] `src/strategy/__init__.py` 업데이트 — 신규 전략 import/export 추가
- [x] `src/main.py` 리팩토링 — `--strategy`, `--strategy-params` JSON, `--list-strategies` 플래그
- [x] `test_vwap_strategy.py` — 10개 테스트 (init, 크로스오버 신호, EMA 필터, 레지스트리 등록)
- [x] `test_momentum_strategy.py` — 14개 테스트 (init, ROC 신호, RSI 필터, 레지스트리 등록)
- [x] `test_main.py` 재작성 — `src.strategy.registry.get_strategy` 패치 방식으로 전환
- [x] 545개 테스트 통과, 커버리지 92.72%

## Phase 18: Circuit Breaker (거래소 장애 자동 차단) ✅ (완료 2026-03-26)

- [x] `CircuitBreakerOpenError` 예외 추가 (`src/utils/exceptions.py`)
- [x] `CircuitBreaker` 구현 (`src/utils/circuit_breaker.py`)
  - CLOSED → OPEN → HALF_OPEN 상태 머신
  - `failure_threshold` 연속 실패 횟수 초과 시 OPEN
  - `recovery_timeout` 초 경과 후 HALF_OPEN으로 자동 전환
  - HALF_OPEN: 프로브 성공 → CLOSED, 실패 → OPEN 재진입
  - 컨텍스트 매니저 (`with cb:`) + `call()` API
  - 스레드 안전 (`threading.Lock`)
  - `reset()` — 수동 복구 (운영자 오버라이드)
- [x] 엔진 통합 — `tick()` 내부에서 circuit breaker 래핑
  - OPEN 상태 시 tick 스킵 + Telegram 경보 발송
  - 예외는 circuit breaker가 카운트하고 상위 코드에 재발생
- [x] `test_circuit_breaker.py` — 28개 테스트 (상태 전환, 스레드 안전, 엔진 통합)
- [x] 573개 테스트 통과, 커버리지 92.99%

## Phase 19: 슬리피지 모델 (페이퍼 트레이딩 현실화) ✅ (완료 2026-03-26)

- [x] `SlippageConfig` + `apply_slippage()` 구현 (`src/exchange/slippage.py`)
  - `spread_bps`: bid-ask 스프레드 단방향 비용 (기본 5 bps)
  - `impact_bps_per_pct`: 일일 거래량 대비 주문 비율에 비례한 시장충격
  - 매수: `price × (1 + spread + impact)` / 매도: `price × (1 - spread - impact)`
  - 거래량 0 시 시장충격 계산 스킵
- [x] 엔진 페이퍼 모드 통합 — `_open_position()`, `_close_position()` 에 슬리피지 적용
  - `Decimal(str(ticker.volume))` 안전 변환 (MagicMock 방어)
  - SL/TP forced_price에는 슬리피지 미적용
- [x] `src/main.py` — `--slippage-bps` CLI 플래그 추가 (기본값 5.0)
- [x] `test_slippage.py` — 20개 테스트 (설정 검증, 매수/매도, 시장충격, 엔진 통합)
- [x] 593개 테스트 통과, 커버리지 93.10%

## Phase 20: Startup Validation (프리플라이트 체크) ✅ (완료 2026-03-26)

- [x] `CheckResult(name, passed, message, critical)` — frozen dataclass
- [x] `StartupChecker` 구현 (`src/utils/startup_check.py`)
  - `check_credentials(settings)` — API 키 존재 여부 (Binance/Upbit)
  - `check_exchange_connectivity(exchange, symbol)` — 거래소 ticker ping
  - `check_risk_params(settings)` — max_position_risk / drawdown / daily_loss 범위 검증
  - `check_db_writable(db_path)` — SQLite 디렉토리 쓰기 권한 확인
  - `check_telegram(settings)` — Telegram 자격증명 (non-critical)
  - `run_all()` — 5개 체크 일괄 실행
  - `print_summary()` — 구조화된 로그 출력, PASS/FAIL/WARN 상태 표시
- [x] `src/main.py` — `--skip-checks` 플래그 추가; 기본은 체크 실행 후 critical 실패 시 종료
- [x] `test_startup_check.py` — 34개 테스트 (각 체크 통과/실패, 예외 방어, main.py 통합)
- [x] 627개 테스트 통과, 커버리지 93.27%

## Phase 21: Trailing Stop Loss ✅ (완료 2026-03-26)

- [x] `Position.trailing_stop_pct` 필드 추가 — 포지션 단위 trailing % 저장
- [x] `PortfolioTracker.update_stop_loss(symbol, new_stop)` 추가
  - frozen dataclass `replace()`로 새 Position 생성, store 동기화
- [x] `PortfolioTracker.open_position()` — `trailing_stop_pct` 파라미터 추가
- [x] `SQLitePositionStore` 스키마 마이그레이션 — `trailing_stop_pct TEXT` 컬럼, 기존 DB 자동 마이그레이션 (ALTER TABLE try/except)
- [x] 엔진 `_process_symbol()` — trailing stop 래칫 로직
  - `new_trail = price × (1 - pct/100)`
  - 현재 stop_loss보다 높을 때만 갱신 (래칫: 내리지 않음)
  - 포지션 단위 `trailing_stop_pct` 우선, 없으면 엔진 전역 설정 사용
- [x] `engine.trailing_stop_pct` property + setter
- [x] `src/main.py` — `--trailing-stop-pct` CLI 플래그 추가 (기본값: 비활성)
- [x] `test_trailing_stop.py` — 20개 테스트 (모델, 트래커, 엔진 래칫, 스토어 영속성)
- [x] 647개 테스트 통과, 커버리지 93.32%

## Phase 22: Engine 생명주기 알림 + Heartbeat ✅ (완료 2026-03-26)

- [x] `TelegramClient.send_startup(mode, exchange, symbols, strategy)` 추가
- [x] `TelegramClient.send_shutdown(session_seconds, total_trades, win_rate, realized_pnl)` 추가
- [x] `TelegramClient.send_heartbeat(open_positions, cash, circuit_state)` 추가
  - CLOSED → 🟢, OPEN → 🔴 상태 이모지
- [x] 엔진 `start()` — 스케줄러 시작 후 `send_startup()` 호출
- [x] 엔진 `_shutdown()` — 세션 요약 계산 후 `send_shutdown()` 호출
- [x] 엔진 `_send_heartbeat()` — 주기적 로그 + Telegram 발송, 예외 흡수
- [x] `start()` — `heartbeat_interval` 파라미터 추가 (기본 3600초, None으로 비활성)
- [x] `src/main.py` — `--heartbeat-interval` CLI 플래그 추가 (기본 3600, 0=비활성)
- [x] `test_lifecycle_notifications.py` — 17개 테스트 (메서드, 시작/종료 알림, heartbeat, main.py)
- [x] 664개 테스트 통과, 커버리지 93.52%

## Phase 23: Multi-Timeframe 지원 + OHLCV 데이터 검증 ✅ (완료 2026-03-26)

- [x] `BaseStrategy.timeframe` property 추가 — 기본값 "1h", 서브클래스 오버라이드 지원
  - Common values: '1m', '5m', '15m', '1h', '4h', '1d'
- [x] `OHLCVValidator` 구현 (`src/data/validator.py`)
  - `ValidationResult(ok, issues)` — `__bool__` 지원
  - 필수 컬럼 체크 (open/high/low/close/volume)
  - 최소 행 수 체크 (`min_rows`)
  - OHLC NaN 검사
  - 0 이하 가격 검사
  - 음수 거래량 검사
  - 타임스탬프 단조증가 검사 (DatetimeIndex 전용)
  - 데이터 staleness 검사 (`max_age_seconds > 0`, DatetimeIndex 전용)
- [x] 엔진 `_process_symbol()` 업데이트
  - `strategy.timeframe`으로 OHLCV fetch (`get_ohlcv_dataframe(..., timeframe=strategy.timeframe, ...)`)
  - 검증 실패 시 `generate_signal()` 호출 스킵 + Warning 로그
- [x] `test_data_validator.py` — 36개 테스트 (ValidationResult, 컬럼/행/NaN/가격/타임스탬프/staleness/엔진 통합)
- [x] `test_engine.py` — `_make_dataframe()` 실제 DataFrame으로 교체, `_mock_strat()` 헬퍼 추가
- [x] 700개 테스트 통과, 커버리지 93.60%

## Phase 24: Per-Symbol Strategy Assignment ✅ (완료 2026-03-26)

- [x] `StrategyFactory` ABC 추가 (`src/strategy/base.py`)
  - 엔진이 진짜 팩토리 vs 단순 MagicMock을 구분하기 위한 마커 클래스
  - `__call__(symbol) -> BaseStrategy` 추상 메서드
- [x] `SymbolStrategyRouter(StrategyFactory)` 구현 (`src/strategy/symbol_router.py`)
  - `routes: dict[str, BaseStrategy]` — 심볼별 전략 매핑
  - `default: BaseStrategy | None` — 미매핑 심볼 폴백
  - `__call__(symbol)` — 라우팅 로직; 미매핑 + default 없으면 `KeyError`
  - `strategy_names()` — 표시/로깅용 `{symbol: class_name}` 딕셔너리
  - 빈 routes + default 없으면 `ValueError`
- [x] 엔진 `_resolve_strategy()` 업데이트
  - `isinstance(provider, StrategyFactory)` → callable factory 경로
  - dict → 명시적 심볼 매핑 경로
  - 그 외 → 단일 공유 전략 경로
- [x] 엔진 `start()` — provider 타입별 strategy_name 표현 개선
  - `StrategyFactory` → `strategy_names()` 호출
  - `dict` → `"SYM:Class"` 형식 열거
- [x] `src/main.py` — `--symbol-strategy SYMBOL=STRATEGY` 플래그 추가 (반복 가능)
  - 일부 심볼만 지정 시 → `SymbolStrategyRouter(routes, default=--strategy)`
  - 전체 심볼 지정 시 → `SymbolStrategyRouter(routes)` (default 없음)
  - 잘못된 형식(= 없음) → `SystemExit`
- [x] `test_symbol_router.py` — 22개 테스트 (init, resolve, fallback, strategy_names, callable 인터페이스, 엔진 통합)
- [x] `test_main.py` — 5개 테스트 추가 (router 빌드, 부분 매핑, 단일 전략 경로, 형식 오류)
- [x] 723개 테스트 통과, 커버리지 93.68%

## Phase 25: Health Check HTTP Server + Docker 연동 ✅ (완료 2026-03-26)

- [x] `HealthState` 구현 (`src/health.py`) — 스레드 안전 readiness 상태 컨테이너
  - `set_ready(details)` / `set_not_ready(reason)` / `is_ready` / `snapshot()`
  - `get_health_state()` — 모듈 수준 싱글턴
- [x] `_HealthHandler` — `GET /health` (항상 200), `GET /ready` (200/503), `GET *` (404)
- [x] `start_health_server(host, port)` — 데몬 스레드에서 HTTPServer 시작
- [x] 엔진 통합
  - `start()` — 스케줄러 시작 후 `set_ready({mode, exchange, symbols})` 호출
  - `_shutdown()` — `set_not_ready("shutting down")` 호출
- [x] `src/main.py` — `--health-port` 플래그 추가 (기본 8080, 0=비활성)
- [x] `Dockerfile` 업데이트 — `EXPOSE 8080` + `HEALTHCHECK` 명령 추가
- [x] `docker-compose.yml` 업데이트 — `ports: 8080:8080`, `healthcheck` 블록 추가
- [x] `test_health.py` — 21개 테스트 (HealthState, /health, /ready, start_health_server)
- [x] 744개 테스트 통과, 커버리지 93.82%

## 의존성

- Phase 2 완료 후 Phase 3, 4 병렬 진행 가능
- Phase 5는 Phase 3, 4 완료 후 진행
- Phase 6은 Phase 2 완료 후 진행
- Phase 9는 Phase 4, 6, 7 완료 후 진행
- Phase 10은 모든 Phase 완료 후 진행
