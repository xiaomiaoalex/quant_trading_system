"""Assemble the Control Plane surface around the unified trading runtime.

The production process composes this factory with PostgreSQL-backed stores.
Tests and local smoke runs can inject memory stores and a fake exchange.
"""

from __future__ import annotations

from fastapi import FastAPI

from trader.api.auth import AuthSettings, SessionMiddleware
from trader.api.routes import health, research, sse, trading


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
) -> FastAPI:
    settings = auth_settings or AuthSettings()
    app = FastAPI(
        title="QTS Trading Control Plane",
        version="1.0.0",
        docs_url="/docs",
        redoc_url=None,
        lifespan=lifespan,
    )
    app.state.trading_service = trading_service
    app.state.research_store = research_store
    app.state.research_application = research_application
    app.state.sessions = sessions
    app.state.auth_settings = settings
    app.state.readiness_provider = readiness_provider
    if readiness_provider is not None:
        health.configure_trading_readiness(readiness_provider)

    app.add_middleware(SessionMiddleware)
    app.include_router(health.router)
    app.include_router(trading.router)
    app.include_router(research.router)
    if include_sse:
        app.include_router(sse.router)
    return app
