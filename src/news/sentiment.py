"""
암호화폐 뉴스 헤드라인 수집 + 규칙 기반(키워드) 감성 점수화.

LLM 없이 동작하는 가벼운 1차 버전이다. RSS 피드에서 최근 헤드라인을 모아
강세/약세 키워드 매칭 빈도로 -1.0(강한 약세) ~ +1.0(강한 강세) 점수를 낸다.
네트워크 I/O(fetch_headlines)와 순수 로직(score_headlines)을 분리해서
스코어링 로직은 네트워크 없이 단위 테스트 가능하게 했다.
"""
from __future__ import annotations

import re
import time
from dataclasses import dataclass, field
from datetime import UTC, datetime

import feedparser
from loguru import logger

# 기본 피드: 등록/키 없이 접근 가능한 공개 RSS만 사용.
DEFAULT_FEEDS: tuple[str, ...] = (
    "https://www.coindesk.com/arc/outboundfeeds/rss/",
    "https://cointelegraph.com/rss",
)

# 매칭은 단어 경계 기준, 대소문자 무시. 코인 시황에 흔히 쓰이는 표현 위주.
# 영어 활용형(rally/rallies/rallying 등)은 단순 접두사 매칭으로 커버가 안 돼서
# (불규칙 복수형 y→ies 등) 자주 쓰이는 변형을 별도 항목으로 나열했다.
BULLISH_KEYWORDS: tuple[str, ...] = (
    "surge", "surges", "surging", "surged",
    "rally", "rallies", "rallying", "rallied",
    "soar", "soars", "soaring", "soared",
    "jump", "jumps", "jumping", "jumped",
    "breakout", "bullish", "all-time high", "record high",
    "approve", "approves", "approved", "approval",
    "adopt", "adopts", "adoption",
    "inflow", "inflows",
    "buy the dip", "accumulate", "accumulating",
    "upgrade", "upgrades", "upgraded",
    "partnership", "institutional buying",
    "etf approval", "outperform", "outperforms",
    "recover", "recovers", "recovery", "rebound", "rebounds", "rebounding",
)
BEARISH_KEYWORDS: tuple[str, ...] = (
    "crash", "crashes", "crashing", "crashed",
    "plunge", "plunges", "plunging", "plunged",
    "tumble", "tumbles", "tumbling", "tumbled",
    "sell-off", "selloff", "sell-offs", "selloffs",
    "bearish", "collapse", "collapses", "collapsing", "collapsed",
    "hack", "hacks", "hacked", "hacking",
    "exploit", "exploits", "exploited",
    "breach", "breaches", "breached",
    "ban", "bans", "banned", "banning",
    "crackdown", "lawsuit", "lawsuits", "sue", "sues", "sued", "fraud",
    "liquidation", "liquidations", "outflow", "outflows",
    "delist", "delists", "delisted", "delisting",
    "investigation", "investigations", "fine", "fined", "warning", "warnings",
    "downgrade", "downgrades", "downgraded", "fear", "fears", "panic", "panicking",
)

_WORD_BOUNDARY = r"(?<![a-z0-9]){}(?![a-z0-9])"


@dataclass
class NewsScore:
    headline_count: int
    bullish_hits: int
    bearish_hits: int
    score: float  # -1.0 ~ +1.0. 0에 가까울수록 중립/판단 보류.
    sample_bullish: list[str] = field(default_factory=list)
    sample_bearish: list[str] = field(default_factory=list)


def fetch_headlines(
    hours: int = 24,
    feeds: tuple[str, ...] = DEFAULT_FEEDS,
    timeout: int = 10,
) -> list[str]:
    """RSS 피드에서 최근 `hours`시간 이내 헤드라인만 모아 반환.

    피드 하나가 실패해도(네트워크 오류, 파싱 실패) 나머지는 계속 시도한다.
    published 시각을 못 읽는 항목은 "최근 것"으로 간주해 포함한다(누락보다
    과다포함이 안전 — 판단은 어차피 여러 헤드라인의 평균 점수이므로).
    """
    cutoff = time.time() - hours * 3600
    headlines: list[str] = []
    for url in feeds:
        try:
            parsed = feedparser.parse(url)
            if getattr(parsed, "bozo", False) and not parsed.entries:
                logger.warning("News feed parse failed", feed=url, error=str(parsed.get("bozo_exception", "")))
                continue
            for entry in parsed.entries:
                published = entry.get("published_parsed") or entry.get("updated_parsed")
                if published is not None:
                    ts = time.mktime(published)
                    if ts < cutoff:
                        continue
                title = entry.get("title", "").strip()
                if title:
                    headlines.append(title)
        except Exception as exc:  # noqa: BLE001
            logger.warning("News feed fetch failed", feed=url, error=str(exc))
            continue
    return headlines


def _count_matches(text: str, keywords: tuple[str, ...]) -> int:
    hits = 0
    for kw in keywords:
        pattern = _WORD_BOUNDARY.format(re.escape(kw.lower()))
        if re.search(pattern, text):
            hits += 1
    return hits


def score_headlines(headlines: list[str]) -> NewsScore:
    """헤드라인 목록을 강세/약세 키워드 매칭으로 점수화.

    score = (강세 히트 - 약세 히트) / max(총 헤드라인 수, 1), [-1, 1]로 클램프.
    헤드라인 1건에 강세/약세 키워드가 둘 다 매칭될 수 있음(혼합 신호는 상쇄).
    """
    bullish = 0
    bearish = 0
    sample_bull: list[str] = []
    sample_bear: list[str] = []

    for h in headlines:
        low = h.lower()
        b = _count_matches(low, BULLISH_KEYWORDS)
        s = _count_matches(low, BEARISH_KEYWORDS)
        if b:
            bullish += b
            if len(sample_bull) < 5:
                sample_bull.append(h)
        if s:
            bearish += s
            if len(sample_bear) < 5:
                sample_bear.append(h)

    total = max(len(headlines), 1)
    raw_score = (bullish - bearish) / total
    score = max(-1.0, min(1.0, raw_score))

    return NewsScore(
        headline_count=len(headlines),
        bullish_hits=bullish,
        bearish_hits=bearish,
        score=round(score, 4),
        sample_bullish=sample_bull,
        sample_bearish=sample_bear,
    )


def fetch_and_score(hours: int = 24, feeds: tuple[str, ...] = DEFAULT_FEEDS) -> NewsScore:
    headlines = fetch_headlines(hours=hours, feeds=feeds)
    logger.info(
        "News headlines fetched",
        count=len(headlines),
        window_hours=hours,
        at=datetime.now(UTC).isoformat(),
    )
    return score_headlines(headlines)


__all__ = [
    "NewsScore",
    "fetch_headlines",
    "score_headlines",
    "fetch_and_score",
    "DEFAULT_FEEDS",
    "BULLISH_KEYWORDS",
    "BEARISH_KEYWORDS",
]
