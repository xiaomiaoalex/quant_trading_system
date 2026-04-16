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
    chat,
    portfolio_research,
    audit,
)
from trader.services.reconciler_service import ReconcilerService
from trader.services.strategy import StrategyService
from trader.api.models.schemas import StrategyRegisterRequest

_BUILTIN_STRATEGIES = [
    StrategyRegisterRequest(
        strategy_id="ema_cross_btc",
        name="EMA Cross BTC",
        description="EMA 交叉趋势跟踪策略 - 快线上穿慢线买入，下穿卖出",
        entrypoint="trader.strategies.ema_cross_btc",
    ),
    StrategyRegisterRequest(
        strategy_id="rsi_grid",
        name="RSI Grid",
        description="RSI 超买超卖网格策略 - 超卖买入，超买卖出，带网格间距过滤",
        entrypoint="trader.strategies.rsi_grid",
    ),
    StrategyRegisterRequest(
        strategy_id="dca_btc",
        name="DCA BTC",
        description="BTC 定投策略 - 定期定额买入，带价格偏离和持仓上限保护",
        entrypoint="trader.strategies.dca_btc",
    ),
]


def _seed_strategies() -> None:
    service = StrategyService()
    for req in _BUILTIN_STRATEGIES:
        existing = service.get_strategy(req.strategy_id)
        if existing is None:
            service.register_strategy(req)


@asynccontextmanager
async def lifespan(app: FastAPI):
    _seed_strategies()
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
app.include_router(chat.router)
app.include_router(portfolio_research.router)
app.include_router(audit.router)


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
