"""
Startup validation: runs pre-flight checks before the engine starts.

Each check returns a CheckResult.  Critical failures abort startup.
Non-critical failures emit a warning but allow the engine to continue.

Usage:
    checker = StartupChecker()
    results = checker.run_all(settings=settings, exchange=exchange)
    if not checker.all_passed(results):
        raise SystemExit("Startup checks failed. Fix the errors above.")
"""
from __future__ import annotations

import sqlite3
import tempfile
from dataclasses import dataclass, field
from pathlib import Path

from loguru import logger

from src.config.settings import Settings
from src.exchange.base import BaseExchangeClient


@dataclass(frozen=True)
class CheckResult:
    """Result of a single pre-flight check."""

    name: str
    passed: bool
    message: str
    critical: bool = True  # False → warning only, does not block startup


class StartupChecker:
    """
    Runs a suite of pre-flight checks and summarises the results.

    Checks performed:
      1. API credentials present (non-empty)
      2. Exchange connectivity (get_ticker ping)
      3. Risk parameter sanity (thresholds in [0, 1])
      4. SQLite DB directory is writable
      5. Telegram configuration (non-critical — app still runs without it)
    """

    # ── Public API ────────────────────────────────────────────────────────────

    def run_all(
        self,
        settings: Settings,
        exchange: BaseExchangeClient,
        symbols: list[str] | None = None,
        db_path: Path | None = None,
    ) -> list[CheckResult]:
        """Run all checks and return results.  Never raises."""
        probe_symbol = (symbols or ["BTC/USDT"])[0]
        results: list[CheckResult] = [
            self.check_credentials(settings),
            self.check_exchange_connectivity(exchange, probe_symbol),
            self.check_risk_params(settings),
            self.check_db_writable(db_path),
            self.check_telegram(settings),
        ]
        return results

    @staticmethod
    def all_critical_passed(results: list[CheckResult]) -> bool:
        """True when every *critical* check has passed."""
        return all(r.passed for r in results if r.critical)

    def print_summary(self, results: list[CheckResult]) -> bool:
        """
        Log a summary table and return True if all critical checks passed.
        """
        logger.info("─── Startup Check Results ───────────────────────────────")
        for r in results:
            status = "PASS" if r.passed else ("FAIL" if r.critical else "WARN")
            log_fn = logger.info if r.passed else (logger.error if r.critical else logger.warning)
            log_fn(f"  [{status}] {r.name}: {r.message}")
        logger.info("─────────────────────────────────────────────────────────")
        ok = self.all_critical_passed(results)
        if ok:
            logger.info("All critical startup checks passed.")
        else:
            failed = [r.name for r in results if r.critical and not r.passed]
            logger.error("Startup checks FAILED", failed=failed)
        return ok

    # ── Individual checks ─────────────────────────────────────────────────────

    @staticmethod
    def check_credentials(settings: Settings) -> CheckResult:
        """Verify that at least one set of exchange credentials is non-empty."""
        try:
            exchange = settings.exchange
            if exchange == "upbit":
                ok = bool(
                    getattr(settings, "upbit_access_key", "") and
                    getattr(settings, "upbit_secret_key", "")
                )
                missing = "upbit_access_key / upbit_secret_key"
            else:
                ok = bool(
                    getattr(settings, "binance_api_key", "") and
                    getattr(settings, "binance_secret_key", "")
                )
                missing = "binance_api_key / binance_secret_key"

            if ok:
                return CheckResult("credentials", True, f"{exchange} credentials present")
            return CheckResult("credentials", False, f"Missing {missing} for {exchange}")
        except Exception as exc:  # noqa: BLE001
            return CheckResult("credentials", False, f"Error reading credentials: {exc}")

    @staticmethod
    def check_exchange_connectivity(
        exchange: BaseExchangeClient,
        symbol: str = "BTC/USDT",
    ) -> CheckResult:
        """Ping the exchange by fetching a ticker."""
        try:
            ticker = exchange.get_ticker(symbol)
            price = float(ticker.last)
            return CheckResult(
                "exchange_connectivity",
                True,
                f"{exchange.exchange_id} reachable — {symbol} last={price:,.2f}",
            )
        except Exception as exc:  # noqa: BLE001
            return CheckResult(
                "exchange_connectivity",
                False,
                f"Cannot reach exchange: {exc}",
            )

    @staticmethod
    def check_risk_params(settings: Settings) -> CheckResult:
        """Sanity-check risk thresholds are in reasonable ranges."""
        try:
            issues: list[str] = []
            risk = getattr(settings, "max_position_risk", None)
            if risk is not None and not (0 < float(risk) <= 1):
                issues.append(f"max_position_risk={risk} (expected 0 < x ≤ 1)")
            drawdown = getattr(settings, "max_drawdown_halt", None)
            if drawdown is not None and not (0 < float(drawdown) <= 1):
                issues.append(f"max_drawdown_halt={drawdown} (expected 0 < x ≤ 1)")
            daily_loss = getattr(settings, "max_daily_loss", None)
            if daily_loss is not None and not (0 < float(daily_loss) <= 1):
                issues.append(f"max_daily_loss={daily_loss} (expected 0 < x ≤ 1)")

            if issues:
                return CheckResult("risk_params", False, "; ".join(issues))
            return CheckResult("risk_params", True, "Risk parameters look sane")
        except Exception as exc:  # noqa: BLE001
            return CheckResult("risk_params", False, f"Error checking risk params: {exc}")

    @staticmethod
    def check_db_writable(db_path: Path | None = None) -> CheckResult:
        """Verify the SQLite database directory is writable."""
        try:
            target = db_path or Path(".")
            target = Path(target)
            # Use a temp file in the same directory to test write access
            with tempfile.NamedTemporaryFile(dir=target, suffix=".tmp", delete=True):
                pass
            # Also verify SQLite itself works (in-memory quick test)
            conn = sqlite3.connect(":memory:")
            conn.execute("CREATE TABLE t (x INTEGER)")
            conn.close()
            return CheckResult("db_writable", True, f"DB directory writable: {target.resolve()}")
        except Exception as exc:  # noqa: BLE001
            return CheckResult("db_writable", False, f"DB directory not writable: {exc}")

    @staticmethod
    def check_telegram(settings: Settings) -> CheckResult:
        """Check Telegram credentials are present (non-critical)."""
        try:
            token = getattr(settings, "telegram_bot_token", "") or ""
            chat_id = getattr(settings, "telegram_chat_id", "") or ""
            if token and chat_id:
                return CheckResult(
                    "telegram", True, "Telegram credentials present", critical=False
                )
            return CheckResult(
                "telegram",
                False,
                "Telegram not configured — notifications disabled",
                critical=False,
            )
        except Exception as exc:  # noqa: BLE001
            return CheckResult("telegram", False, f"Error: {exc}", critical=False)
