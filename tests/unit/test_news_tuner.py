"""Unit tests for src.report.news_tuner — score→adjustment mapping and dry-run gate."""
from __future__ import annotations

import pytest

import src.config.live_params as lp
import src.report.news_tuner as nt
from src.news.sentiment import NewsScore


@pytest.fixture(autouse=True)
def isolate_files(tmp_path, monkeypatch):
    monkeypatch.setattr(lp, "PARAMS_FILE", tmp_path / ".strategy_params.json")
    monkeypatch.setattr(lp, "AUDIT_LOG", tmp_path / "reports" / "param_change_log.jsonl")
    monkeypatch.setattr(lp, "RESTART_FLAG", tmp_path / ".restart_requested")
    return tmp_path


def _score(
    value: float,
    count: int = 10,
    bull: int = 0,
    bear: int = 0,
    sample_bull: list[str] | None = None,
    sample_bear: list[str] | None = None,
) -> NewsScore:
    return NewsScore(
        headline_count=count,
        bullish_hits=bull,
        bearish_hits=bear,
        score=value,
        sample_bullish=sample_bull or [],
        sample_bearish=sample_bear or [],
    )


class TestComputeAdjustments:
    def test_neutral_band_produces_no_adjustments(self):
        assert nt.compute_adjustments(0.05) == []
        assert nt.compute_adjustments(-0.10) == []

    def test_bullish_score_lowers_vol_mult_and_raises_rsi(self):
        planned = nt.compute_adjustments(0.8)
        by_param = {p["param"]: p for p in planned}
        assert by_param["mr_vol_mult"]["target"] < by_param["mr_vol_mult"]["old"]
        assert by_param["mr_rsi_oversold_fast"]["target"] > by_param["mr_rsi_oversold_fast"]["old"]

    def test_bearish_score_raises_vol_mult_and_lowers_rsi(self):
        planned = nt.compute_adjustments(-0.8)
        by_param = {p["param"]: p for p in planned}
        assert by_param["mr_vol_mult"]["target"] > by_param["mr_vol_mult"]["old"]
        assert by_param["mr_rsi_oversold_fast"]["target"] < by_param["mr_rsi_oversold_fast"]["old"]

    def test_all_four_tunable_params_present(self):
        planned = nt.compute_adjustments(0.5)
        params = {p["param"] for p in planned}
        assert params == {"mr_vol_mult", "sm_vol_mult", "mr_rsi_oversold_fast", "mr_rsi_oversold_slow"}


class TestRunDailyNewsTuning:
    def test_no_headlines_is_skipped(self, monkeypatch):
        monkeypatch.setattr(nt, "fetch_and_score", lambda hours: _score(0.0, count=0))
        result = nt.run_daily_news_tuning(dry_run=True)
        assert result.status == "skipped"

    def test_neutral_score_is_skipped_and_applies_nothing(self, monkeypatch):
        monkeypatch.setattr(nt, "fetch_and_score", lambda hours: _score(0.05))
        applied = []
        monkeypatch.setattr(
            lp, "propose_and_apply",
            lambda **kw: applied.append(kw) or {"param": kw["param"], "old": 0, "new": 0, "changed": False},
        )
        result = nt.run_daily_news_tuning(dry_run=True)
        assert result.status == "skipped"
        assert applied == []

    def test_dry_run_never_calls_propose_and_apply(self, monkeypatch):
        monkeypatch.setattr(nt, "fetch_and_score", lambda hours: _score(0.6, bull=6))
        calls = []
        monkeypatch.setattr(lp, "propose_and_apply", lambda **kw: calls.append(kw))
        monkeypatch.setattr(lp, "request_restart", lambda reason: calls.append(("restart", reason)))

        result = nt.run_daily_news_tuning(dry_run=True)

        assert calls == []
        assert result.status == "skipped"
        assert "dry-run" in result.title

    def test_applies_via_live_params_when_not_dry_run(self, monkeypatch):
        monkeypatch.setattr(nt, "fetch_and_score", lambda hours: _score(0.6, bull=6))

        applied_params = []

        def fake_apply(param, target_value, trigger, reason):
            applied_params.append(param)
            return {"param": param, "old": 1.0, "new": 0.9, "changed": True}

        restart_calls = []
        monkeypatch.setattr(lp, "propose_and_apply", fake_apply)
        monkeypatch.setattr(lp, "enforce_rsi_gap", lambda: None)
        monkeypatch.setattr(lp, "request_restart", lambda reason: restart_calls.append(reason))

        result = nt.run_daily_news_tuning(dry_run=False)

        assert set(applied_params) == {
            "mr_vol_mult", "sm_vol_mult", "mr_rsi_oversold_fast", "mr_rsi_oversold_slow",
        }
        assert len(restart_calls) == 1
        assert result.status == "executed"

    def test_fetch_failure_reports_error_without_raising(self, monkeypatch):
        def boom(hours):
            raise ConnectionError("network down")

        monkeypatch.setattr(nt, "fetch_and_score", boom)
        result = nt.run_daily_news_tuning(dry_run=True)
        assert result.status == "error"

    def test_neutral_result_includes_sample_headlines_when_present(self, monkeypatch):
        monkeypatch.setattr(
            nt, "fetch_and_score",
            lambda hours: _score(
                0.05,
                sample_bull=["비트코인 상승세 지속"],
                sample_bear=["규제 우려로 하락 압력"],
            ),
        )
        result = nt.run_daily_news_tuning(dry_run=True)
        assert result.status == "skipped"
        assert "강세 헤드라인:" in result.detail
        assert "비트코인 상승세 지속" in result.detail
        assert "약세 헤드라인:" in result.detail
        assert "규제 우려로 하락 압력" in result.detail

    def test_neutral_result_omits_sample_headers_when_no_samples(self, monkeypatch):
        monkeypatch.setattr(nt, "fetch_and_score", lambda hours: _score(0.05))
        result = nt.run_daily_news_tuning(dry_run=True)
        assert result.status == "skipped"
        assert "강세 헤드라인:" not in result.detail
        assert "약세 헤드라인:" not in result.detail

    def test_dry_run_result_includes_sample_headlines_when_present(self, monkeypatch):
        monkeypatch.setattr(
            nt, "fetch_and_score",
            lambda hours: _score(0.6, bull=6, sample_bull=["ETF 승인 기대감 확산"]),
        )
        result = nt.run_daily_news_tuning(dry_run=True)
        assert result.status == "skipped"
        assert "dry-run" in result.title
        assert "강세 헤드라인:" in result.detail
        assert "ETF 승인 기대감 확산" in result.detail

    def test_applied_result_includes_sample_headlines_when_present(self, monkeypatch):
        monkeypatch.setattr(
            nt, "fetch_and_score",
            lambda hours: _score(0.6, bull=6, sample_bull=["기관 매수 유입 확대"]),
        )

        def fake_apply(param, target_value, trigger, reason):
            return {"param": param, "old": 1.0, "new": 0.9, "changed": True}

        monkeypatch.setattr(lp, "propose_and_apply", fake_apply)
        monkeypatch.setattr(lp, "enforce_rsi_gap", lambda: None)
        monkeypatch.setattr(lp, "request_restart", lambda reason: None)

        result = nt.run_daily_news_tuning(dry_run=False)
        assert result.status == "executed"
        assert "강세 헤드라인:" in result.detail
        assert "기관 매수 유입 확대" in result.detail
