"""Assemble the Control Plane surface around the unified trading runtime.

The production process composes this factory with PostgreSQL-backed stores.
Tests and local smoke runs can inject memory stores and a fake exchange.
"""

from __future__ import annotations

import os

from fastapi import FastAPI

from trader.api.auth import AuthSettings, SessionMiddleware
from trader.api.routes import (
    audit,
    chat,
    control_views,
    data_catalog,
    data_history,
    health,
    performance,
    portfolio_research,
    research,
    risk,
    sse,
    trading,
)


def create_trading_app(
    *,
    trading_service=None,
    research_store=None,
    research_application=None,
    sessions=None,
    auth_settings: AuthSettings | None = None,
    readiness_provider=None,
    lifespan=None,
    include_sse: bool = False,
    history_service=None,
    include_history: bool = True,
) -> FastAPI:
    settings = auth_settings or AuthSettings()
    app = FastAPI(
        title="QTS Trading Control Plane",
        version="1.0.0",
        docs_url="/docs",
        redoc_url=None,
        lifespan=lifespan,
    )
    app.state.account_id = os.getenv("QTS_ACCOUNT_ID", "binance_spot")
    app.state.trading_service = trading_service
    app.state.research_store = research_store
    app.state.research_application = research_application
    app.state.sessions = sessions
    app.state.auth_settings = settings
    app.state.readiness_provider = readiness_provider
    app.state.history_service = history_service
    if readiness_provider is not None:
        health.configure_trading_readiness(readiness_provider)

    app.add_middleware(SessionMiddleware)
    app.include_router(health.router)
    app.include_router(trading.router)
    app.include_router(control_views.router)
    # Non-execution surfaces migrated from the retired process: durable
    # research/data/performance reads, operator audit and chat authoring, and
    # risk configuration/audit. None of them holds an order or accounting path.
    app.include_router(data_catalog.router)
    app.include_router(performance.router)
    app.include_router(portfolio_research.router)
    app.include_router(audit.router)
    app.include_router(chat.router)
    app.include_router(risk.router)
    app.include_router(research.router)
    if include_history:
        app.include_router(data_history.router)
    if include_sse:
        app.include_router(sse.router)
    return app
