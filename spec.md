# Trading Agent - 기능 명세

## Feature 1: 거래소 연동 (Exchange Integration)

### 요구사항
1. ccxt를 통해 Binance, Upbit, Bybit 등 주요 거래소 연결
2. REST API + WebSocket 실시간 데이터 수신
3. 주문 생성/취소/조회 (시장가, 지정가, 스톱로스)
4. 잔고 및 포지션 조회
5. API 에러 시 자동 재시도 (exponential backoff)

### API 명세
- `ExchangeClient.get_balance() -> Balance`
- `ExchangeClient.place_order(symbol, side, amount, price) -> Order`
- `ExchangeClient.cancel_order(order_id) -> bool`
- `ExchangeClient.get_ohlcv(symbol, timeframe, limit) -> DataFrame`
- `ExchangeClient.get_ticker(symbol) -> Ticker`

### 데이터 모델
```python
@dataclass(frozen=True)
class Order:
    id: str
    symbol: str
    side: Literal["buy", "sell"]
    amount: Decimal
    price: Decimal
    status: OrderStatus
    created_at: datetime

@dataclass(frozen=True)
class Balance:
    currency: str
    free: Decimal
    used: Decimal
    total: Decimal
```

---

## Feature 2: 전략 엔진 (Strategy Engine)

### 요구사항
1. 전략 기본 클래스 (`BaseStrategy`) 정의
2. 플러그인 방식으로 전략 추가 가능
3. 기본 전략 구현: 이동평균 크로스오버, RSI, 볼린저 밴드
4. 전략 파라미터 최적화 지원
5. 멀티 타임프레임 분석

### 전략 인터페이스
```python
class BaseStrategy:
    def generate_signal(self, data: DataFrame) -> Signal
    def calculate_position_size(self, signal: Signal, balance: Balance) -> Decimal
    def get_parameters(self) -> dict
```

### 데이터 모델
```python
@dataclass(frozen=True)
class Signal:
    symbol: str
    action: Literal["buy", "sell", "hold"]
    strength: float  # 0.0 ~ 1.0
    reason: str
    timestamp: datetime
```

---

## Feature 3: 백테스팅 엔진 (Backtesting Engine)

### 요구사항
1. 과거 OHLCV 데이터로 전략 시뮬레이션
2. 수수료, 슬리피지 반영
3. 성과 지표 계산: Sharpe Ratio, MDD, Win Rate, PnL
4. 결과 시각화 (matplotlib/plotly)
5. Walk-forward 분석 지원

### 성과 지표
- Total Return (%)
- Annualized Return (%)
- Max Drawdown (%)
- Sharpe Ratio
- Sortino Ratio
- Win Rate (%)
- Profit Factor
- Total Trades

---

## Feature 4: 리스크 관리 (Risk Management)

### 요구사항
1. 포지션당 최대 손실 한도 설정 (기본: 잔고의 2%)
2. 일일 최대 손실 한도 (기본: 잔고의 5%)
3. 최대 동시 포지션 수 제한
4. 스톱로스 자동 설정
5. 드로다운 한도 초과 시 자동 매매 중단

### 리스크 파라미터
```python
class RiskConfig(BaseModel):
    max_position_risk: float = 0.02   # 포지션당 2%
    max_daily_loss: float = 0.05      # 일일 5%
    max_open_positions: int = 5
    max_drawdown_halt: float = 0.15   # 15% MDD 시 중단
```

---

## Feature 5: 알림 시스템 (Notification)

### 요구사항
1. Telegram 봇을 통한 실시간 알림
2. 주문 체결/취소 알림
3. 에러/경고 알림
4. 일일 성과 리포트
5. 리스크 한도 도달 시 긴급 알림

---

## Feature 6: 데이터 관리 (Data Management)

### 요구사항
1. 거래소에서 OHLCV 데이터 수집 및 로컬 저장 (SQLite 또는 CSV)
2. 실시간 시세 캐싱
3. 데이터 정합성 검증
4. 백테스트용 히스토리 데이터 다운로드
