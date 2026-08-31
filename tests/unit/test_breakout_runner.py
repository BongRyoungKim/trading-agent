"""Unit tests for src/breakout/runner.py CLI wiring (argparse only, no network)."""
from __future__ import annotations

from src.breakout.runner import _build_arg_parser


class TestArgParser:
    def test_defaults_match_confirmed_design(self) -> None:
        args = _build_arg_parser().parse_args([])
        assert args.trailing_stop_pct == 12.0  # 사용자 확정: 10~15% 범위, 큰 움직임 끝까지 추적
        assert args.time_stop_minutes == 60
        assert args.interval_seconds == 60
        assert args.scan_every_n_ticks == 5
        assert args.max_concurrent_positions == 3

    def test_overrides_apply(self) -> None:
        args = _build_arg_parser().parse_args(
            ["--trailing-stop-pct", "15.0", "--max-concurrent-positions", "5"]
        )
        assert args.trailing_stop_pct == 15.0
        assert args.max_concurrent_positions == 5
