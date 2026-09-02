"""
뉴스 감성 기반 1일 1회 파라미터 자동 조정.

ScheduledTaskRunner(거래 건수 기준 조정)와 별개로, 거래가 아예 없어도
매일 정해진 시각에 동작하는 캘린더 기반 조정기다. 흐름:

    RSS 헤드라인 수집 → 키워드 감성 점수화 → 점수를 4개 파라미터의
    목표값으로 변환 → live_params.propose_and_apply()로 안전 범위 내에서
    반영(±20% 스텝캡 + PARAM_BOUNDS 하드클램프는 그대로 적용됨) →
    변경 있으면 재시작 요청.

강세(score>0)면 진입 문턱을 낮추고(Vol 배수↓, RSI 과매도 기준↑),
약세(score<0)면 반대로 문턱을 높인다. |score| < NEUTRAL_BAND 구간은
"판단 보류"로 보고 아무것도 바꾸지 않는다 — 뉴스 감성은 노이즈가 커서
약한 신호로 매일 흔들지 않기 위함.

dry_run=True(기본값)면 실제로 반영하지 않고 "이렇게 바꿨을 것"만 계산해
보고한다 — 처음 도입 시 며칠 관찰 후 안심하고 켜라는 의도.
"""
from __future__ import annotations

from dataclasses import dataclass

from loguru import logger

from src.config import live_params as lp
from src.news.sentiment import NewsScore, fetch_and_score
from src.report.task_runner import ActionResult

NEUTRAL_BAND = 0.15  # 이 값 미만의 |score|는 조정하지 않음(노이즈 구간)
MAX_ADJUST_FRACTION = 0.15  # score=±1.0일 때 각 파라미터에 적용할 최대 조정 비율

# (param, direction) — direction=+1이면 "강세일 때 값을 올린다"는 뜻.
# Vol 배수는 강세일 때 낮춰야(진입 완화) 하므로 -1, RSI 과매도 기준은
# 강세일 때 올려야(진입 완화) 하므로 +1.
_TUNABLE_PARAMS: tuple[tuple[str, float], ...] = (
    ("mr_vol_mult", -1.0),
    ("sm_vol_mult", -1.0),
    ("mr_rsi_oversold_fast", 1.0),
    ("mr_rsi_oversold_slow", 1.0),
)


@dataclass
class NewsTuningOutcome:
    score: NewsScore
    changes: list[dict]
    applied: bool  # False면 dry_run이었거나 중립 구간이라 반영 안 됨


def _format_samples(news: NewsScore) -> str:
    """강세/약세로 매칭된 샘플 헤드라인 원문을 사람이 읽을 수 있는 블록으로 만든다.

    이전엔 집계 숫자(헤드라인 건수, 강세/약세 히트 수)만 로그·텔레그램에 남아서
    실제로 어떤 뉴스가 그 점수를 만들었는지 나중에 확인할 방법이 없었다 —
    NewsScore가 이미 갖고 있는 sample_bullish/sample_bearish(각 최대 5개)를
    빠뜨리지 않고 붙여서, 조정이 실적용된 날 원인 추적이 가능하게 한다.
    """
    lines: list[str] = []
    if news.sample_bullish:
        lines.append("강세 헤드라인:")
        lines.extend(f"  · {h}" for h in news.sample_bullish)
    if news.sample_bearish:
        lines.append("약세 헤드라인:")
        lines.extend(f"  · {h}" for h in news.sample_bearish)
    return "\n".join(lines)


def _target_for(param: str, direction: float, old: float, score: float) -> float:
    fraction = MAX_ADJUST_FRACTION * score * direction
    return old * (1.0 + fraction)


def compute_adjustments(score: float) -> list[dict]:
    """중립 구간이면 빈 리스트. 아니면 각 파라미터의 (param, old, target)."""
    if abs(score) < NEUTRAL_BAND:
        return []
    out = []
    for param, direction in _TUNABLE_PARAMS:
        old = lp.get_current(param)
        target = _target_for(param, direction, old, score)
        out.append({"param": param, "old": old, "target": target})
    return out


def run_daily_news_tuning(
    dry_run: bool = True,
    hours: int = 24,
) -> ActionResult:
    """1일 1회 스케줄러가 호출하는 진입점."""
    try:
        news = fetch_and_score(hours=hours)
    except Exception as exc:  # noqa: BLE001
        logger.error("News sentiment fetch failed", error=str(exc))
        return ActionResult("news_sentiment", "뉴스 감성 조정", "error", str(exc))

    if news.headline_count == 0:
        detail = "수집된 헤드라인 없음 — 조정 건너뜀"
        logger.warning("News sentiment: no headlines", detail=detail)
        return ActionResult("news_sentiment", "뉴스 감성 조정", "skipped", detail)

    planned = compute_adjustments(news.score)
    if not planned:
        detail = (
            f"중립 구간(score={news.score:+.2f}, 헤드라인 {news.headline_count}건 "
            f"강세{news.bullish_hits}/약세{news.bearish_hits}) — 조정 없음"
        )
        samples = _format_samples(news)
        if samples:
            detail += "\n" + samples
        logger.info(
            "News sentiment: neutral, no adjustment",
            score=news.score,
            sample_bullish=news.sample_bullish,
            sample_bearish=news.sample_bearish,
        )
        return ActionResult("news_sentiment", "뉴스 감성 조정", "skipped", detail)

    direction_kr = "완화(강세)" if news.score > 0 else "강화(약세)"
    reason = (
        f"뉴스 감성 점수 {news.score:+.2f} (헤드라인 {news.headline_count}건, "
        f"강세 키워드 {news.bullish_hits}건 / 약세 키워드 {news.bearish_hits}건)"
    )

    if dry_run:
        lines = [f"[dry-run] {p['param']}: {p['old']} → {p['target']:.4f}" for p in planned]
        detail = f"{direction_kr} 방향 조정 시뮬레이션 (dry-run, 미반영)\n{reason}\n" + "\n".join(lines)
        samples = _format_samples(news)
        if samples:
            detail += "\n" + samples
        logger.info(
            "News sentiment: dry-run",
            score=news.score,
            planned=planned,
            sample_bullish=news.sample_bullish,
            sample_bearish=news.sample_bearish,
        )
        return ActionResult("news_sentiment", "뉴스 감성 조정 (dry-run)", "skipped", detail)

    applied_changes = []
    for p in planned:
        result = lp.propose_and_apply(
            param=p["param"],
            target_value=p["target"],
            trigger="news_sentiment",
            reason=reason,
        )
        applied_changes.append(result)

    lp.enforce_rsi_gap()

    changed = [c for c in applied_changes if c["changed"]]
    samples = _format_samples(news)
    if changed:
        lp.request_restart(f"뉴스 감성 자동 조정: {reason}")
        lines = [f"{c['param']}: {c['old']} → {c['new']}" for c in changed]
        detail = f"{direction_kr} 방향 조정 적용\n{reason}\n" + "\n".join(lines)
        if samples:
            detail += "\n" + samples
        logger.warning(
            "News sentiment: adjustment applied",
            changes=changed,
            sample_bullish=news.sample_bullish,
            sample_bearish=news.sample_bearish,
        )
        return ActionResult("news_sentiment", "뉴스 감성 조정", "executed", detail)

    detail = f"이미 안전범위 경계값이라 변경 없음\n{reason}"
    if samples:
        detail += "\n" + samples
    logger.info(
        "News sentiment: at bounds, no change",
        planned=planned,
        sample_bullish=news.sample_bullish,
        sample_bearish=news.sample_bearish,
    )
    return ActionResult("news_sentiment", "뉴스 감성 조정", "skipped", detail)


__all__ = ["run_daily_news_tuning", "compute_adjustments", "NewsTuningOutcome", "NEUTRAL_BAND"]
