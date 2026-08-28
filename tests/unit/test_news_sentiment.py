"""Unit tests for src.news.sentiment — keyword scoring and headline fetch/filter."""
from __future__ import annotations

import time
from types import SimpleNamespace

import pytest

from src.news import sentiment as ns


class TestScoreHeadlines:
    def test_empty_list_is_neutral(self):
        result = ns.score_headlines([])
        assert result.headline_count == 0
        assert result.score == 0.0

    def test_all_bullish_gives_positive_score(self):
        headlines = [
            "Bitcoin surges to new all-time high",
            "Institutional buying drives ETF approval rally",
        ]
        result = ns.score_headlines(headlines)
        assert result.bullish_hits > 0
        assert result.bearish_hits == 0
        assert result.score > 0

    def test_all_bearish_gives_negative_score(self):
        headlines = [
            "Exchange hack triggers massive sell-off",
            "Regulators announce crackdown, lawsuit filed",
        ]
        result = ns.score_headlines(headlines)
        assert result.bearish_hits > 0
        assert result.bullish_hits == 0
        assert result.score < 0

    def test_mixed_signals_partially_offset(self):
        headlines = ["Market rally turns into crash amid fraud probe"]
        result = ns.score_headlines(headlines)
        assert result.bullish_hits > 0
        assert result.bearish_hits > 0

    def test_neutral_headlines_score_zero(self):
        headlines = ["Exchange lists new trading pair", "Weekly market recap published"]
        result = ns.score_headlines(headlines)
        assert result.score == 0.0

    def test_score_is_clamped_to_unit_range(self):
        # 헤드라인 1건에 강세 키워드가 여러 개 몰려도 score는 1.0을 넘지 않음.
        headlines = ["surge rally soar jump breakout bullish adoption inflow"]
        result = ns.score_headlines(headlines)
        assert -1.0 <= result.score <= 1.0

    def test_word_boundary_avoids_false_positive_substring(self):
        # "ban"이 약세 키워드지만 "banana"처럼 단어 일부로 등장하면 매칭되면 안 됨.
        headlines = ["Banana Republic announces new store opening"]
        result = ns.score_headlines(headlines)
        assert result.bearish_hits == 0


class TestFetchHeadlines:
    def _fake_entry(self, title: str, hours_ago: float) -> SimpleNamespace:
        ts = time.localtime(time.time() - hours_ago * 3600)
        return {"title": title, "published_parsed": ts}

    def test_filters_out_entries_older_than_window(self, monkeypatch):
        recent = self._fake_entry("Recent headline", hours_ago=1)
        old = self._fake_entry("Old headline", hours_ago=48)

        fake_parsed = SimpleNamespace(bozo=False, entries=[recent, old])
        monkeypatch.setattr(ns.feedparser, "parse", lambda url: fake_parsed)

        result = ns.fetch_headlines(hours=24, feeds=("http://fake/feed",))
        assert result == ["Recent headline"]

    def test_entries_without_published_are_included(self, monkeypatch):
        entry = {"title": "No timestamp headline"}
        fake_parsed = SimpleNamespace(bozo=False, entries=[entry])
        monkeypatch.setattr(ns.feedparser, "parse", lambda url: fake_parsed)

        result = ns.fetch_headlines(hours=24, feeds=("http://fake/feed",))
        assert result == ["No timestamp headline"]

    def test_one_feed_failing_does_not_block_others(self, monkeypatch):
        good = self._fake_entry("Good feed headline", hours_ago=1)

        def fake_parse(url):
            if url == "http://broken/feed":
                raise ConnectionError("boom")
            return SimpleNamespace(bozo=False, entries=[good])

        monkeypatch.setattr(ns.feedparser, "parse", fake_parse)
        result = ns.fetch_headlines(
            hours=24, feeds=("http://broken/feed", "http://ok/feed")
        )
        assert result == ["Good feed headline"]
