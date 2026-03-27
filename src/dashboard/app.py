"""
FastAPI web dashboard application.

Exposes:
  GET  /                    — HTML dashboard (auto-refreshes every 30s)
  GET  /api/status          — Engine + circuit breaker status (JSON)
  GET  /api/positions       — Open positions (JSON)
  GET  /api/pnl             — Portfolio PnL summary (JSON)
  GET  /api/trades          — Recent closed trades (JSON, limit param)
  GET  /api/stats           — Journal statistics (JSON)
  GET  /api/equity          — Cumulative PnL curve (JSON)
  POST /api/engine/pause    — Pause trading
  POST /api/engine/resume   — Resume trading
"""
from __future__ import annotations

from fastapi import FastAPI, Query
from fastapi.responses import HTMLResponse, JSONResponse

from src.dashboard.state import get_dashboard_state
from src.dashboard.templates import render_dashboard

app = FastAPI(title="Trading Agent Dashboard", docs_url=None, redoc_url=None)


# ── HTML Dashboard ────────────────────────────────────────────────────────────

@app.get("/", response_class=HTMLResponse, include_in_schema=False)
async def dashboard() -> str:
    state = get_dashboard_state()
    return render_dashboard(
        status=state.get_status(),
        positions=state.get_positions(),
        pnl=state.get_pnl(),
        trades=state.get_trades(limit=50),
        stats=state.get_stats(),
        equity=state.get_equity_curve(),
        balance=state.get_balance(),
        ticks=state.get_ticks(),
    )


@app.get("/api/balance")
async def get_balance() -> JSONResponse:
    return JSONResponse(get_dashboard_state().get_balance())


@app.get("/api/ticks")
async def get_ticks() -> JSONResponse:
    return JSONResponse(get_dashboard_state().get_ticks())


# ── JSON API ──────────────────────────────────────────────────────────────────

@app.get("/api/status")
async def get_status() -> JSONResponse:
    return JSONResponse(get_dashboard_state().get_status())


@app.get("/api/positions")
async def get_positions() -> JSONResponse:
    return JSONResponse(get_dashboard_state().get_positions())


@app.get("/api/pnl")
async def get_pnl() -> JSONResponse:
    return JSONResponse(get_dashboard_state().get_pnl())


@app.get("/api/trades")
async def get_trades(limit: int = Query(default=50, ge=1, le=500)) -> JSONResponse:
    return JSONResponse(get_dashboard_state().get_trades(limit=limit))


@app.get("/api/stats")
async def get_stats() -> JSONResponse:
    return JSONResponse(get_dashboard_state().get_stats())


@app.get("/api/equity")
async def get_equity() -> JSONResponse:
    return JSONResponse(get_dashboard_state().get_equity_curve())


# ── Engine Control ────────────────────────────────────────────────────────────

@app.post("/api/engine/pause")
async def pause_engine() -> JSONResponse:
    state = get_dashboard_state()
    if state.engine is None:
        return JSONResponse({"success": False, "message": "Engine not registered"}, status_code=503)
    state.pause()
    return JSONResponse({"success": True, "message": "Engine paused"})


@app.post("/api/engine/resume")
async def resume_engine() -> JSONResponse:
    state = get_dashboard_state()
    if state.engine is None:
        return JSONResponse({"success": False, "message": "Engine not registered"}, status_code=503)
    state.resume()
    return JSONResponse({"success": True, "message": "Engine resumed"})
