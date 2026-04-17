"""
FastAPI Application - Systematic Trader Control Plane API
======================================================
Main application entry point for the Systematic Trader Control Plane API.

Based on OpenAPI 3.0.3 specification v0.2.0
"""
import logging
import os
from contextlib import asynccontextmanager
from datetime import datetime, timezone
from pathlib import Path
from typing import List, Dict, Any, Optional

# 自动加载项目根目录的 .env 文件
_env_file = Path(__file__).parent.parent.parent / ".env"
if _env_file.exists():
    from dotenv import load_dotenv
    load_dotenv(_env_file)

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
from trader.services.order import OrderService
from trader.services.heartbeat import ProcessHeartbeatService
from trader.api.connections import ConnectionManager
from trader.api.models.schemas import StrategyRegisterRequest
from trader.api.env_config import get_binance_recv_window
from trader.adapters.binance.connector import BinanceConnector

logger = logging.getLogger(__name__)

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

    async def _local_orders_getter() -> List[Dict[str, Any]]:
        try:
            order_svc = OrderService()
            orders = order_svc.list_orders(limit=10000)
            return [
                {
                    "client_order_id": o.cl_ord_id,
                    "status": o.status,
                    "symbol": o.instrument or "",
                    "quantity": o.qty or "0",
                    "filled_quantity": o.filled_qty or "0",
                    "created_at": (
                        datetime.fromtimestamp(o.created_ts_ms / 1000, tz=timezone.utc)
                        if o.created_ts_ms else datetime.now(timezone.utc)
                    ),
                    "updated_at": (
                        datetime.fromtimestamp(o.updated_ts_ms / 1000, tz=timezone.utc)
                        if o.updated_ts_ms else datetime.now(timezone.utc)
                    ),
                }
                for o in orders
            ]
        except Exception as e:
            logger.error(f"[Reconciler] local_orders_getter failed: {e}")
            return []

    async def _exchange_orders_getter() -> List[Dict[str, Any]]:
        try:
            # 检查是否禁用交易所对账
            if os.environ.get("DISABLE_EXCHANGE_RECONCILIATION", "false").lower() == "true":
                logger.info("[Reconciler] exchange_orders_getter disabled by DISABLE_EXCHANGE_RECONCILIATION=true")
                return []

            from trader.adapters.broker.binance_spot_demo_broker import (
                BinanceSpotDemoBroker,
                BinanceSpotDemoBrokerConfig,
            )

            api_key = os.environ.get("BINANCE_API_KEY")
            secret_key = os.environ.get("BINANCE_SECRET_KEY")

            if not api_key or not secret_key:
                logger.warning(
                    "[Reconciler] BINANCE_API_KEY or BINANCE_SECRET_KEY not set, "
                    "exchange_orders_getter returning empty list"
                )
                return []

            recv_window = get_binance_recv_window()
            config = BinanceSpotDemoBrokerConfig.for_demo(
                api_key,
                secret_key,
                recv_window=recv_window,
            )
            broker = BinanceSpotDemoBroker(config)
            await broker.connect()
            try:
                broker_orders = await broker.get_open_orders()
                return [
                    {
                        "client_order_id": order.client_order_id,
                        "status": order.status.value,
                        "symbol": order.symbol,
                        "quantity": str(order.quantity),
                        "filled_quantity": str(order.filled_quantity),
                        "updated_at": order.created_at,
                    }
                    for order in broker_orders
                ]
            finally:
                await broker.disconnect()
        except Exception as e:
            logger.error(f"[Reconciler] exchange_orders_getter failed: {e}")
            return []

    _connector: Optional[BinanceConnector] = None

    api_key = os.environ.get("BINANCE_API_KEY")
    secret_key = os.environ.get("BINANCE_SECRET_KEY")

    if api_key and secret_key:
        try:
            _connector = BinanceConnector(
                api_key=api_key,
                secret_key=secret_key,
                streams=["btcusdt@trade", "btcusdt@kline_1m"],
            )
            await _connector.start()
            logger.info("[Main] BinanceConnector started")
        except Exception as e:
            logger.error(f"[Main] Failed to start BinanceConnector: {e}")
            _connector = None
    else:
        logger.warning("[Main] BINANCE_API_KEY or BINANCE_SECRET_KEY not set, skipping BinanceConnector")

    def _connector_getter() -> Optional[BinanceConnector]:
        return _connector

    reconciler_service = reconciler.get_reconciler_service()

    # 检查是否启用交易所对账
    if os.environ.get("DISABLE_EXCHANGE_RECONCILIATION", "false").lower() == "true":
        logger.info("[Reconciler] Exchange reconciliation disabled")
    else:
        reconciler_service.configure_periodic_reconciliation(
            _local_orders_getter,
            _exchange_orders_getter,
        )
        await reconciler_service.start()

    _heartbeat_service = ProcessHeartbeatService()
    await _heartbeat_service.start()

    _connection_manager = ConnectionManager()
    await _connection_manager.start()

    health.configure_heartbeat(
        heartbeat_service=_heartbeat_service,
        connection_manager=_connection_manager,
        connector_getter=_connector_getter,
    )

    yield
    if _connector:
        await _connector.stop()
        logger.info("[Main] BinanceConnector stopped")
    await _heartbeat_service.stop()
    await _connection_manager.stop()
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
