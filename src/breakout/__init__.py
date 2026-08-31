"""
가격/거래량 기반 브레이크아웃 조기진입 실험 모듈.

메인 라이브 엔진(src/engine.py, RegimeAdaptiveStrategy)과는 완전히 독립적이다.
자체 페이퍼 계좌·자체 스캔 루프로 동작하며, 라이브 자본·포지션에는
전혀 손대지 않는다. src/main.py가 시작하는 프로세스와도 별개의 프로세스로
실행하는 것을 전제로 설계했다 — `python -m src.breakout.runner`로 기동하며
docker-compose.yml의 breakout-scanner 서비스가 이를 감싼다 (runner.py 참고).

모듈 구성:
  detector.py — 순수 브레이크아웃 판별 로직 (가격/거래량만, 네트워크 없음)
  exits.py    — 순수 청산 판별 로직 (트레일링 스탑/타임 스탑, 네트워크 없음)
  scanner.py  — 위 둘을 실제 거래소·페이퍼 포트폴리오·텔레그램과 연결하는 조립 레이어
  runner.py   — 프로세스 진입점 (CLI 인자 파싱 + 무한 루프 시작)
"""
