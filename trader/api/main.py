"""
FastAPI Application - Systematic Trader Control Plane API
======================================================
Main application entry point for the Systematic Trader Control Plane API.

Based on OpenAPI 3.0.3 specification v0.2.0
"""
import asyncio
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
from trader.api.env_config import (
    get_binance_recv_window,
    get_reconciler_exchange_client_order_prefixes,
    get_system_order_namespace_prefix,
)
from trader.core.domain.services.order_ownership_registry import (
    get_order_ownership_registry,
    OrderOwnership,
)
from trader.adapters.binance.connector import BinanceConnector

logger = logging.getLogger(__name__)


def _configure_trader_logging() -> None:
    """
    为 trader.* 日志配置统一毫秒时间戳格式。

    - 仅作用于 `trader` 命名空间日志
    - 幂等安装，避免 --reload 时重复 handler
    """
    trader_logger = logging.getLogger("trader")
    trader_logger.setLevel(logging.INFO)

    for handler in trader_logger.handlers:
        if getattr(handler, "_qts_trader_ts_handler", False):
            return

    handler = logging.StreamHandler()
    handler.setFormatter(
        logging.Formatter(
            fmt="%(asctime)s.%(msecs)03d %(levelname)s [%(name)s] %(message)s",
            datefmt="%Y-%m-%d %H:%M:%S",
        )
    )
    handler._qts_trader_ts_handler = True  # type: ignore[attr-defined]
    trader_logger.addHandler(handler)
    trader_logger.propagate = False


_configure_trader_logging()

# 全局 BinanceConnector 实例（用于成交回调）
_binance_connector_instance: Optional[Any] = None

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
    StrategyRegisterRequest(
        strategy_id="fire_test",
        name="Fire Test",
        description="开火测试策略 - 固定节奏发 BUY/SELL，用于验证真实下单链路",
        entrypoint="trader.strategies.fire_test",
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
    global _binance_connector_instance

    # 初始化策略
    _seed_strategies()

    # ================================================================
    # BinanceConnector 初始化（用于成交回调）
    # ================================================================
    # 检查是否启用 Binance 连接
    api_key = os.environ.get("BINANCE_API_KEY")
    secret_key = os.environ.get("BINANCE_SECRET_KEY")

    if api_key and secret_key:
        try:
            from trader.adapters.binance.connector import (
                BinanceConnectorConfig,
            )

            # 根据 BINANCE_ENV 统一 connector 的 REST/WS 环境
            binance_env = os.environ.get("BINANCE_ENV", "demo").lower()
            from trader.adapters.binance.public_stream import PublicStreamConfig
            from trader.adapters.binance.private_stream import PrivateStreamConfig
            from trader.adapters.binance.rest_alignment import AlignmentConfig

            if binance_env in ("demo",):
                # Binance Spot Demo 环境
                config = BinanceConnectorConfig(
                    testnet=True,
                    public_stream_config=PublicStreamConfig(
                        base_url="wss://demo-stream.binance.com/ws",
                    ),
                    private_stream_config=PrivateStreamConfig(
                        base_url="wss://demo-stream.binance.com/ws",
                        rest_url="https://demo-api.binance.com/api",
                    ),
                    alignment_config=AlignmentConfig(
                        base_url="https://demo-api.binance.com/api",
                    ),
                )
            elif binance_env in ("testnet", "test"):
                config = BinanceConnectorConfig(
                    testnet=True,
                    public_stream_config=PublicStreamConfig(
                        base_url="wss://stream.testnet.binance.vision/ws",
                    ),
                    private_stream_config=PrivateStreamConfig(
                        base_url="wss://stream.testnet.binance.vision/ws",
                        rest_url="https://testnet.binance.vision/api",
                    ),
                    alignment_config=AlignmentConfig(
                        base_url="https://testnet.binance.vision/api",
                    ),
                )
            else:
                # 生产环境
                config = BinanceConnectorConfig(testnet=False)

            # 创建 connector
            connector = BinanceConnector(
                api_key=api_key,
                secret_key=secret_key,
                config=config,
            )

            # 注册成交回调处理器（先确保 handler 已初始化）
            from trader.api.routes.strategies import ensure_fill_handler_ready
            fill_handler = await ensure_fill_handler_ready()
            if fill_handler is not None:
                connector.register_fill_handler(fill_handler)
                logger.info("[Lifespan] Fill handler registered to connector")
            else:
                logger.warning("[Lifespan] Fill handler not available yet")

            # 启动 connector（会启动 public 和 private streams）
            await connector.start()
            _binance_connector_instance = connector
            logger.info(
                "[Lifespan] BinanceConnector started: env=%s public_ws=%s private_ws=%s private_rest=%s align_rest=%s",
                binance_env,
                config.public_stream_config.base_url if config.public_stream_config else "default",
                config.private_stream_config.base_url if config.private_stream_config else "default",
                config.private_stream_config.rest_url if config.private_stream_config else "default",
                config.alignment_config.base_url if config.alignment_config else "default",
            )

            # 注入 connector 到策略编排器
            from trader.api.routes.strategies import set_strategy_orchestrator_connector
            set_strategy_orchestrator_connector(connector)
        except Exception as e:
            logger.exception(
                "[Lifespan] Failed to start BinanceConnector: type=%s repr=%r",
                type(e).__name__,
                e,
            )
    else:
        logger.info("[Lifespan] BINANCE_API_KEY or BINANCE_SECRET_KEY not set, skipping connector")

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
            binance_env = os.environ.get("BINANCE_ENV", "demo").lower()
            if binance_env in ("testnet", "test"):
                config = BinanceSpotDemoBrokerConfig.for_testnet(
                    api_key,
                    secret_key,
                    recv_window=recv_window,
                )
            else:
                config = BinanceSpotDemoBrokerConfig.for_demo(
                    api_key,
                    secret_key,
                    recv_window=recv_window,
                )
            broker = BinanceSpotDemoBroker(config)
            await broker.connect()
            try:
                broker_orders = await broker.get_open_orders()
                prefixes = get_reconciler_exchange_client_order_prefixes()
                if prefixes:
                    broker_orders = [
                        order
                        for order in broker_orders
                        if order.client_order_id
                        and any(order.client_order_id.startswith(prefix) for prefix in prefixes)
                    ]
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

    def _connector_getter() -> Optional[BinanceConnector]:
        return _binance_connector_instance

    reconciler_service = reconciler.get_reconciler_service()

    # 获取订单归属注册表
    ownership_registry = get_order_ownership_registry()

    # 外部订单 ID getter
    def _external_order_ids_getter() -> set[str]:
        """获取当前已识别的外部订单 ID 集合"""
        # 从注册表获取所有标记为 EXTERNAL 的订单 ID
        stats = ownership_registry.get_statistics()
        external_count = stats.get("external", 0)
        if external_count > 0:
            return ownership_registry.get_external_order_ids()
        return set()
    # 检查是否启用交易所对账
    if os.environ.get("DISABLE_EXCHANGE_RECONCILIATION", "false").lower() == "true":
        logger.info("[Reconciler] Exchange reconciliation disabled")
    else:
        reconciler_service.configure_periodic_reconciliation(
            _local_orders_getter,
            _exchange_orders_getter,
            _external_order_ids_getter,
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
    try:
        await strategies.shutdown_strategy_runtime_resources()
    except Exception as e:
        logger.error(f"[Main] Failed to shutdown strategy runtime: {e}")
    if _binance_connector_instance is not None:
        try:
            await _binance_connector_instance.stop()
            logger.info("[Lifespan] BinanceConnector stopped")
        except Exception as e:
            logger.error(f"[Lifespan] Error stopping BinanceConnector: {e}")
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
