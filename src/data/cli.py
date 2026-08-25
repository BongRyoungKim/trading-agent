"""
CLI for managing locally-stored OHLCV data.

    python -m src.data.cli download --symbol BTC/USDT --days 30
    python -m src.data.cli list
"""
from __future__ import annotations

import argparse
import sys
from pathlib import Path

from src.config.settings import get_settings
from src.data.collector import DataCollector
from src.data.storage import OHLCVStorage
from src.utils.logger import setup_logger

_DEFAULT_DB_PATH = Path("data/ohlcv.db")


def _build_client(exchange: str):
    """Construct an exchange client for `exchange` from configured settings."""
    settings = get_settings()

    if exchange == "binance":
        from src.exchange.binance import BinanceClient

        return BinanceClient(
            api_key=settings.binance_api_key,
            secret_key=settings.binance_secret_key,
        )
    elif exchange == "upbit":
        from src.exchange.upbit import UpbitClient

        return UpbitClient(
            access_key=settings.upbit_access_key,
            secret_key=settings.upbit_secret_key,
        )
    else:
        raise ValueError(f"Unsupported exchange: {exchange}")


def cmd_download(args: argparse.Namespace) -> int:
    client = _build_client(args.exchange)
    storage = OHLCVStorage(db_path=_DEFAULT_DB_PATH)
    collector = DataCollector(client, storage)

    total = collector.download_history(
        symbol=args.symbol,
        timeframe=args.timeframe,
        days_back=args.days,
        batch_size=args.batch_size,
    )
    print(
        f"Downloaded {total} bars for {args.symbol} "
        f"({args.timeframe}, {args.exchange})."
    )
    return 0


def cmd_list(args: argparse.Namespace) -> int:
    storage = OHLCVStorage(db_path=_DEFAULT_DB_PATH)
    series = storage.available_series()

    if not series:
        print("No data stored yet.")
        return 0

    print(f"{'exchange':<10} {'symbol':<12} {'timeframe':<10} {'count':>8}")
    for s in series:
        print(f"{s['exchange']:<10} {s['symbol']:<12} {s['timeframe']:<10} {s['count']:>8}")
    return 0


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="data-cli", description="Manage locally stored OHLCV data"
    )
    sub = parser.add_subparsers(dest="command", required=True)

    dl = sub.add_parser("download", help="Download historical OHLCV bars")
    dl.add_argument("--exchange", default="binance", choices=["binance", "upbit"])
    dl.add_argument("--symbol", required=True)
    dl.add_argument("--timeframe", default="1h")
    dl.add_argument("--days", type=int, default=30)
    dl.add_argument("--batch-size", type=int, default=500, dest="batch_size")

    sub.add_parser("list", help="List locally stored OHLCV series")

    return parser


def main(argv: list[str] | None = None) -> int:
    parser = _build_parser()
    args = parser.parse_args(argv)

    setup_logger()

    if args.command == "download":
        return cmd_download(args)
    elif args.command == "list":
        return cmd_list(args)
    return 1


if __name__ == "__main__":
    sys.exit(main())
