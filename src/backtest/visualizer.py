"""
Backtest result visualizer.
Generates equity curve and drawdown charts from BacktestResult.
matplotlib is an optional dependency — ImportError is raised with a helpful message
if it is not installed.
"""
from __future__ import annotations

from pathlib import Path
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from src.backtest.models import BacktestResult


def _require_matplotlib():
    try:
        import matplotlib
        import matplotlib.pyplot as plt
        return matplotlib, plt
    except ImportError as exc:
        raise ImportError(
            "matplotlib is required for visualization. "
            "Install it with: pip install matplotlib"
        ) from exc


class BacktestVisualizer:
    """
    Renders backtest performance charts using matplotlib.

    Usage:
        viz = BacktestVisualizer(result)
        viz.plot()                        # display interactively
        viz.save("reports/backtest.png")  # save to file
    """

    def __init__(self, result: "BacktestResult") -> None:
        self._result = result

    # ── Public API ────────────────────────────────────────────────────────────

    def plot(self) -> None:
        """Display the full report chart interactively."""
        _, plt = _require_matplotlib()
        fig = self._build_figure()
        plt.show()
        plt.close(fig)

    def save(self, path: str | Path, dpi: int = 150) -> Path:
        """
        Save the chart to a file.

        Args:
            path: Destination file path (PNG, PDF, SVG).
            dpi:  Resolution for raster formats.

        Returns:
            Resolved Path of the saved file.
        """
        _, plt = _require_matplotlib()
        dest = Path(path)
        dest.parent.mkdir(parents=True, exist_ok=True)
        fig = self._build_figure()
        fig.savefig(dest, dpi=dpi, bbox_inches="tight")
        plt.close(fig)
        return dest

    def equity_curve_data(self):
        """
        Return (timestamps, equity_values) lists without rendering.
        Useful for alternative renderers or further processing.
        """
        import pandas as pd
        from src.backtest.metrics import build_equity_curve

        trades = list(self._result.trades)
        if not trades:
            return [], []

        curve = build_equity_curve(self._result.initial_capital, trades)
        # prepend the initial capital point
        start_ts = self._result.start_date
        start_val = float(self._result.initial_capital)
        ts = [start_ts] + list(curve.index)
        vals = [start_val] + list(curve.values)
        return ts, vals

    # ── Internal ──────────────────────────────────────────────────────────────

    def _build_figure(self):
        import pandas as pd
        import numpy as np
        from src.backtest.metrics import build_equity_curve

        matplotlib, plt = _require_matplotlib()

        result = self._result
        trades = list(result.trades)

        fig, axes = plt.subplots(
            3, 1,
            figsize=(12, 10),
            gridspec_kw={"height_ratios": [3, 1.5, 1]},
        )
        fig.suptitle(
            f"{result.strategy_name} — {result.symbol} {result.timeframe}\n"
            f"{result.start_date.date()} → {result.end_date.date()}",
            fontsize=13,
            fontweight="bold",
        )

        # ── Panel 1: Equity curve ─────────────────────────────────────────────
        ax_eq = axes[0]
        ts, vals = self.equity_curve_data()
        if ts:
            color = "steelblue"
            ax_eq.plot(ts, vals, color=color, linewidth=1.5, label="Equity")
            ax_eq.axhline(
                float(result.initial_capital),
                color="grey",
                linestyle="--",
                linewidth=0.8,
                alpha=0.6,
                label=f"Initial capital {float(result.initial_capital):,.0f}",
            )
            ax_eq.fill_between(ts, float(result.initial_capital), vals,
                               where=[v >= float(result.initial_capital) for v in vals],
                               alpha=0.15, color="green")
            ax_eq.fill_between(ts, float(result.initial_capital), vals,
                               where=[v < float(result.initial_capital) for v in vals],
                               alpha=0.15, color="red")
        ax_eq.set_ylabel("Portfolio Value")
        ax_eq.legend(loc="upper left", fontsize=8)
        ax_eq.grid(True, alpha=0.3)
        self._annotate_stats(ax_eq, result)

        # ── Panel 2: Drawdown ─────────────────────────────────────────────────
        ax_dd = axes[1]
        if ts and len(vals) > 1:
            eq_series = pd.Series(vals, index=pd.DatetimeIndex(ts))
            rolling_max = eq_series.cummax()
            drawdown = (eq_series - rolling_max) / rolling_max * 100
            ax_dd.fill_between(drawdown.index, drawdown.values, 0,
                               color="crimson", alpha=0.5)
            ax_dd.plot(drawdown.index, drawdown.values, color="crimson", linewidth=0.8)
        ax_dd.set_ylabel("Drawdown (%)")
        ax_dd.set_ylim(top=0)
        ax_dd.grid(True, alpha=0.3)

        # ── Panel 3: Trade PnL bar chart ──────────────────────────────────────
        ax_tr = axes[2]
        if trades:
            sorted_trades = sorted(trades, key=lambda t: t.exit_time)
            pnls = [float(t.net_pnl) for t in sorted_trades]
            exit_times = [t.exit_time for t in sorted_trades]
            colors = ["green" if p >= 0 else "red" for p in pnls]
            ax_tr.bar(range(len(pnls)), pnls, color=colors, alpha=0.7, width=0.8)
            ax_tr.axhline(0, color="black", linewidth=0.6)
        ax_tr.set_xlabel("Trade #")
        ax_tr.set_ylabel("Net PnL")
        ax_tr.grid(True, alpha=0.3, axis="y")

        fig.tight_layout()
        return fig

    @staticmethod
    def _annotate_stats(ax, result: "BacktestResult") -> None:
        stats = (
            f"Return: {result.total_return_pct:+.2f}%  "
            f"MDD: {result.max_drawdown_pct:.2f}%  "
            f"Sharpe: {result.sharpe_ratio:.3f}  "
            f"Win: {result.win_rate_pct:.1f}%  "
            f"Trades: {result.total_trades}"
        )
        ax.set_title(stats, fontsize=9, loc="right", pad=4)
