# Trading Agent

## 개요

Python 기반 암호화폐 자동 트레이딩 에이전트.
전략 개발, 백테스트, 실매매를 통합한 풀스택 트레이딩 시스템.

## 기술 스택

- **언어**: Python 3.11+
- **거래소 연동**: ccxt (Binance, Upbit, Bybit 등)
- **백테스팅**: backtrader 또는 vectorbt
- **데이터 처리**: pandas, numpy
- **기술적 지표**: ta-lib, pandas-ta
- **스케줄링**: APScheduler
- **설정 관리**: pydantic-settings, python-dotenv
- **로깅**: loguru
- **테스트**: pytest, pytest-asyncio

## 빌드 & 테스트

```bash
# 의존성 설치
pip install -r requirements.txt

# 테스트 실행
pytest tests/ -v

# 백테스트 실행
python -m src.backtest.runner --strategy [strategy_name]

# 라이브 트레이딩 시작
python -m src.main --mode live

# 페이퍼 트레이딩 (시뮬레이션)
python -m src.main --mode paper
```

## 디렉토리 구조

```
Trading_Agent/
├── src/
│   ├── main.py              # 진입점
│   ├── config/              # 설정 (API 키, 파라미터)
│   ├── exchange/            # 거래소 연동 (ccxt 래퍼)
│   ├── strategy/            # 트레이딩 전략
│   ├── backtest/            # 백테스팅 엔진
│   ├── risk/                # 리스크 관리
│   ├── portfolio/           # 포지션/포트폴리오 관리
│   ├── data/                # 시장 데이터 수집/저장
│   └── utils/               # 공통 유틸리티
├── tests/                   # 단위/통합 테스트
├── data/                    # 로컬 데이터 저장
├── logs/                    # 로그 파일
├── .env.example             # 환경 변수 예시
├── requirements.txt
└── CLAUDE.md
```

## 코딩 컨벤션

- **불변성**: 데이터 객체는 `dataclass(frozen=True)` 또는 pydantic BaseModel 사용
- **타입 힌트**: 모든 함수에 타입 힌트 필수
- **에러 처리**: 거래소 API 호출은 항상 try/except로 감싸고 재시도 로직 포함
- **시크릿**: API 키는 반드시 `.env` 파일에만 저장, 코드에 하드코딩 금지
- **로깅**: print() 사용 금지, loguru logger 사용
- **파일 크기**: 800줄 이하, 함수 50줄 이하

## 환경 변수 (.env)

```
BINANCE_API_KEY=
BINANCE_SECRET_KEY=
UPBIT_ACCESS_KEY=
UPBIT_SECRET_KEY=
TELEGRAM_BOT_TOKEN=     # 알림용 (선택)
TELEGRAM_CHAT_ID=       # 알림용 (선택)
```

## 주의사항

- 실매매 모드 실행 전 반드시 페이퍼 트레이딩으로 검증
- 리스크 관리 모듈 없이는 실매매 진입 불가 (하드게이트)
- 포지션 사이징은 Kelly Criterion 또는 고정 비율 사용
