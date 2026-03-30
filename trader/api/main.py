"""
FastAPI Application - Systematic Trader Control Plane API
=======================================================
Main application entry point for the Systematic Trader Control Plane API.

Based on OpenAPI 3.0.3 specification v0.2.0
"""
from contextlib import asynccontextmanager
from fastapi import FastAPI

from trader.api.routes import (
    health,
    strategies,
    deployments,
    backtests,
    risk,
    orders,
    portfolio,
    events,
    killswitch,
    brokers,
    reconciler,
    monitor,
)
from trader.services.reconciler_service import ReconcilerService


@asynccontextmanager
async def lifespan(app: FastAPI):
    reconciler_service = reconciler.get_reconciler_service()
    await reconciler_service.start()
    yield
    await reconciler_service.stop()


app = FastAPI(
    title="Systematic Trader Control Plane API",
    description="Strategy-first trading system control plane API",
    version="0.2.0",
    docs_url="/docs",
    redoc_url="/redoc",
    lifespan=lifespan,
)

app.include_router(health.router)
app.include_router(strategies.router)
app.include_router(deployments.router)
app.include_router(backtests.router)
app.include_router(risk.router)
app.include_router(orders.router)
app.include_router(portfolio.router)
app.include_router(events.router)
app.include_router(killswitch.router)
app.include_router(brokers.router)
app.include_router(reconciler.router)
app.include_router(monitor.router)


@app.get("/")
async def root():
    """Root endpoint"""
    return {
        "name": "Systematic Trader Control Plane API",
        "version": "0.2.0",
        "docs": "/docs",
    }


if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host="0.0.0.0", port=8080)
