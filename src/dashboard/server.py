"""
Starts the FastAPI dashboard on a background daemon thread via uvicorn.

Usage:
    from src.dashboard.server import start_dashboard_server
    start_dashboard_server(port=8000)
"""
from __future__ import annotations

import threading

from loguru import logger


def start_dashboard_server(host: str = "0.0.0.0", port: int = 8000) -> None:
    """Launch the uvicorn server on a daemon thread. Returns immediately."""
    try:
        import uvicorn  # noqa: PLC0415
    except ImportError:
        logger.error(
            "uvicorn is not installed — dashboard unavailable. "
            "Run: pip install fastapi uvicorn"
        )
        return

    from src.dashboard.app import app  # noqa: PLC0415

    config = uvicorn.Config(
        app,
        host=host,
        port=port,
        log_level="warning",
        access_log=False,
    )
    server = uvicorn.Server(config)

    thread = threading.Thread(
        target=server.run,
        daemon=True,
        name="dashboard-server",
    )
    thread.start()
    logger.info("Dashboard server started", host=host, port=port, url=f"http://localhost:{port}")
