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
    sse,
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
    get_binance_env,
    get_binance_env_config,
)
from trader.core.domain.services.order_ownership_registry import (
    get_order_ownership_registry,
    OrderOwnership,
)
from trader.adapters.binance.connector import BinanceConnector

logger = logging.getLogger(__name__)

import re


# ANSI 颜色代码
class LogColors:
    """日志颜色定义"""
    RESET = "\033[0m"
    BOLD = "\033[1m"
    # 级别颜色
    INFO = "\033[92m"      # 绿色
    WARNING = "\033[93m"   # 黄色
    ERROR = "\033[91m"     # 红色
    CRITICAL = "\033[95m"  # 紫色
    # 字段颜色
    STRATEGY = "\033[94m"  # 蓝色
    ORDER = "\033[96m"      # 青色
    SYMBOL = "\033[95m"    # 紫色
    PRICE = "\033[92m"     # 绿色
    QTY = "\033[93m"       # 黄色
    SIDE = "\033[93m"      # 黄色


class ColoredFormatter(logging.Formatter):
    """
    带颜色的日志格式化器。

    根据日志级别和消息内容中的字段自动着色：
    - strategy=xxx → 蓝色
    - order=cl_ord_id → 青色
    - symbol=BTCUSDT → 紫色
    - price/qty=xxx → 绿色/黄色
    - ERROR/WARNING 级别 → 对应颜色
    """

    LEVEL_COLORS = {
        "INFO": LogColors.INFO,
        "WARNING": LogColors.WARNING,
        "ERROR": LogColors.ERROR,
        "CRITICAL": LogColors.CRITICAL,
    }

    FIELD_PATTERNS = [
        # (pattern_regex, replacement_template)
        (r"(strategy)=(\w+)", rf"\1={LogColors.STRATEGY}\2{LogColors.RESET}"),
        (r"(order|cl_ord_id|exec_id)=(\S+)", rf"\1={LogColors.ORDER}\2{LogColors.RESET}"),
        (r"(symbol)=(\S+)", rf"\1={LogColors.SYMBOL}\2{LogColors.RESET}"),
        (r"(price|avg_price|fill_price)=([\d.]+)", rf"\1={LogColors.PRICE}\2{LogColors.RESET}"),
        (r"(qty|quantity|fill_qty)=([\d.]+)", rf"\1={LogColors.QTY}\2{LogColors.RESET}"),
        (r"(side)=(\w+)", rf"\1={LogColors.SIDE}\2{LogColors.RESET}"),
    ]

    def format(self, record: logging.LogRecord) -> str:
        formatted = super().format(record)

        # 替换级别颜色
        level_color = self.LEVEL_COLORS.get(record.levelname, "")
        if level_color:
            formatted = formatted.replace(
                record.levelname,
                f"{level_color}{record.levelname}{LogColors.RESET}"
            )

        # 替换消息中的字段颜色
        for pattern, replacement in self.FIELD_PATTERNS:
            formatted = re.sub(pattern, replacement, formatted)

        return formatted


def _configure_trader_logging() -> None:
    """
    为 trader.* 日志配置统一毫秒时间戳格式和颜色输出。

    - 仅作用于 `trader` 命名空间日志
    - 幂等安装，避免 --reload 时重复 handler
    """
    trader_logger = logging.getLogger("trader")
    trader_logger.setLevel(logging.INFO)

    for handler in trader_logger.handlers:
        if getattr(handler, "_qts_trader_ts_handler", False):
            return

    handler = logging.StreamHandler()
    handler.setFormatter(ColoredFormatter(
        fmt="%(asctime)s.%(msecs)03d %(levelname)s [%(name)s] %(message)s",
        datefmt="%Y-%m-%d %H:%M:%S",
    ))
    handler._qts_trader_ts_handler = True  # type: ignore[attr-defined]
    trader_logger.addHandler(handler)
    trader_logger.propagate = False


_configure_trader_logging()

# 全局 BinanceConnector 实例（用于成交回调）
_binance_connector_instance: Optional[Any] = None
_binance_cascade_controller: Optional[Any] = None

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

    logger.warning(
        f"[Lifespan] Startup: api_key={'set' if api_key else 'NOT SET'}, "
        f"secret_key={'set' if secret_key else 'NOT SET'}"
    )

    if api_key and secret_key:
        try:
            from trader.adapters.binance.connector import (
                BinanceConnectorConfig,
            )
            from trader.adapters.binance.public_stream import PublicStreamConfig
            from trader.adapters.binance.private_stream import PrivateStreamConfig
            from trader.adapters.binance.rest_alignment import AlignmentConfig

            # 使用统一的 env config
            binance_env = get_binance_env()
            env_config = get_binance_env_config()

            logger.info(
                "[Binance] env=%s rest_base=%s public_ws=%s private_ws=%s listenkey_rest=%s",
                binance_env,
                env_config["rest_base"],
                env_config["public_ws_base"],
                env_config["private_ws_base"],
                env_config["listenkey_rest_base"],
            )

            if binance_env in ("demo",):
                # Binance Spot Demo 环境
                config = BinanceConnectorConfig(
                    testnet=True,
                    public_stream_config=PublicStreamConfig(
                        base_url=env_config["public_ws_base"],
                    ),
                    private_stream_config=PrivateStreamConfig(
                        base_url=env_config["private_ws_base"],
                        rest_url=env_config["rest_base"],
                    ),
                    alignment_config=AlignmentConfig(
                        base_url=env_config["rest_base"],
                    ),
                )
            elif binance_env in ("testnet", "test"):
                config = BinanceConnectorConfig(
                    testnet=True,
                    public_stream_config=PublicStreamConfig(
                        base_url=env_config["public_ws_base"],
                    ),
                    private_stream_config=PrivateStreamConfig(
                        base_url=env_config["private_ws_base"],
                        rest_url=env_config["rest_base"],
                    ),
                    alignment_config=AlignmentConfig(
                        base_url=env_config["rest_base"],
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

            # ============================================================
            # Task 9.11: DegradedCascadeController 初始化和注册
            # ============================================================
            from trader.adapters.binance.degraded_cascade import (
                DegradedCascadeController,
                CascadeConfig,
                BackoffController,
                BackoffConfig,
            )
            from trader.api.routes.strategies import get_oms_metrics

            # 自保回调：触发 KillSwitch L1 时同步 OMS 指标
            async def on_self_protection(active: bool, reason: str | None) -> None:
                logger.warning(
                    "[Cascade] [Lifespan] Self-protection triggered: active=%s reason=%s",
                    active,
                    reason,
                )
                oms_metrics = get_oms_metrics()
                if oms_metrics and active:
                    # 记录本地自保触发的拒单
                    oms_metrics["order_submit_reject"] = oms_metrics.get("order_submit_reject", 0) + 1
                    oms_metrics.setdefault("reject_reason_counts", {})["SELF_PROTECTION"] = \
                        oms_metrics["reject_reason_counts"].get("SELF_PROTECTION", 0) + 1

            # 创建级联控制器
            _cascade_config = CascadeConfig(
                control_plane_base_url=f"http://localhost:{server_config.port}",
                dedup_window_ms=60000,
                min_report_interval_ms=5000,
                max_report_interval_ms=30000,
                self_protection_trigger_ms=30000,
                max_retries_per_event=5,
                request_timeout=10.0,
            )
            _cascade_backoff = BackoffController(BackoffConfig(
                initial_delay=1.0,
                max_delay=30.0,
                multiplier=2.0,
            ))
            _cascade_controller = DegradedCascadeController(
                control_plane_base_url=f"http://localhost:{server_config.port}",
                backoff=_cascade_backoff,
                config=_cascade_config,
                adapter_name="binance_connector",
            )
            _cascade_controller.register_self_protection_callback(on_self_protection)

            # 注册健康处理器到 connector
            async def cascade_health_handler(health_report):
                await _cascade_controller.on_adapter_health_changed(health_report, "periodic_health_check")
            connector.register_health_handler(cascade_health_handler)

            # 启动级联控制器 worker
            await _cascade_controller.start()
            logger.info("[Lifespan] DegradedCascadeController started")

            # 存储引用用于 shutdown 清理
            _binance_cascade_controller = _cascade_controller

            # ============================================================
            # Task 16: Startup Self-Check (fail-closed)
            # ============================================================
            async def _run_startup_self_check(c: BinanceConnector) -> bool:
                """
                Run connectivity self-check before marking connector as ready.

                Returns:
                    True if all checks passed, False otherwise
                """
                import time
                checks = {}
                try:
                    # Check 1: REST /v3/time via RESTAlignmentCoordinator
                    start = time.monotonic()
                    server_time = await c._rest_coordinator.get_server_time()
                    rest_latency_ms = (time.monotonic() - start) * 1000
                    checks["rest_time"] = True
                    logger.info("[Binance] Self-check rest_time: OK (latency=%.1fms)", rest_latency_ms)
                except Exception as e:
                    checks["rest_time"] = False
                    logger.error("[Binance] Self-check rest_time: FAILED - %s", e)

                try:
                    # Check 2: Public stream connected
                    public_state = c.public_stream.state
                    checks["public_stream"] = public_state.value == "CONNECTED"
                    logger.info("[Binance] Self-check public_stream: %s", public_state.value)
                except Exception as e:
                    checks["public_stream"] = False
                    logger.error("[Binance] Self-check public_stream: FAILED - %s", e)

                try:
                    # Check 3: Private stream available and connected
                    if c._private_stream_available:
                        private_state = c.private_stream.state
                        checks["private_stream"] = private_state.value == "CONNECTED"
                        logger.info("[Binance] Self-check private_stream: %s", private_state.value)
                    else:
                        checks["private_stream"] = False
                        logger.warning(
                            "[Binance] Self-check private_stream: SKIPPED (private_stream_available=False, reason=%s)",
                            c._private_stream_disabled_reason,
                        )
                except Exception as e:
                    checks["private_stream"] = False
                    logger.error("[Binance] Self-check private_stream: FAILED - %s", e)

                failed = [k for k, v in checks.items() if not v]
                if failed:
                    logger.error(
                        "[Binance] Startup self-check FAILED: %s. Connector will start but strategy RUNNING is blocked.",
                        failed,
                    )
                    return False
                logger.info("[Binance] Startup self-check: ALL PASSED")
                return True

            # 注入 connector 引用到策略编排器（在 connector.start() 之前，
            # 确保后续即使有 SSE keep-alive 轮询触发 orchestrator 初始化，
            # connector 也能在构造时就被注入）
            from trader.api.routes.strategies import set_strategy_orchestrator_connector
            logger.warning("[Lifespan] About to call set_strategy_orchestrator_connector")
            set_strategy_orchestrator_connector(connector)
            logger.warning("[Lifespan] set_strategy_orchestrator_connector returned")

            # 启动 connector（会启动 public 和 private streams）
            await connector.start()
            _binance_connector_instance = connector

            # Run self-check (but don't block startup)
            self_check_passed = await _run_startup_self_check(connector)

            # Store self-check result for strategy start gate
            connector._startup_self_check_passed = self_check_passed

            if self_check_passed:
                logger.info(
                    "[Lifespan] BinanceConnector started: env=%s",
                    binance_env,
                )
            else:
                logger.warning(
                    "[Lifespan] BinanceConnector started in DEGRADED mode: env=%s, self_check_failed",
                    binance_env,
                )

            # 注入 connector 到策略编排器
            from trader.api.routes.strategies import set_strategy_orchestrator_connector
            set_strategy_orchestrator_connector(connector)

            # ============================================================
            # Task 18: Runtime State Recovery
            # ============================================================
            async def _recover_runtime_state() -> None:
                """
                从持久化存储恢复策略运行时状态。

                恢复步骤：
                1. 获取所有 RUNNING 状态的策略运行时状态
                2. 验证环境一致性（recv_window 等）
                3. 恢复订阅（market data）
                4. 恢复策略到 RUNNING 状态
                5. 发布 strategy.recovered 事件
                """
                from trader.storage.in_memory import get_storage
                storage = get_storage()
                running_states = storage.list_running_strategy_states()

                if not running_states:
                    logger.info("[Recovery] No running strategies to recover")
                    return

                logger.info("[Recovery] Found %d running strategies to recover", len(running_states))

                from trader.api.routes.strategies import get_strategy_runner, get_strategy_orchestrator
                runner = get_strategy_runner()
                orchestrator = get_strategy_orchestrator()

                for state in running_states:
                    strategy_id = state.get("strategy_id")
                    saved_env = state.get("env", "demo")
                    symbols = state.get("symbols", [])

                    # 验证环境一致性
                    if saved_env != binance_env:
                        logger.warning(
                            "[Recovery] Strategy %s env mismatch: saved=%s current=%s, skipping",
                            strategy_id, saved_env, binance_env,
                        )
                        # 更新状态为错误
                        state["recovery_error"] = f"env_mismatch: saved={saved_env} current={binance_env}"
                        storage.save_strategy_runtime_state(state)
                        continue

                    # 验证策略是否已加载
                    info = runner.get_status(strategy_id)
                    if info is None:
                        logger.warning("[Recovery] Strategy %s not loaded, skipping", strategy_id)
                        state["recovery_error"] = "strategy_not_loaded"
                        storage.save_strategy_runtime_state(state)
                        continue

                    # 恢复订阅（如果有 symbols）
                    if symbols and orchestrator._connector:
                        for symbol in symbols:
                            try:
                                # 订阅已经在 connector 启动时通过 streams 配置处理
                                # 这里只是更新 orchestrator 的订阅状态
                                logger.info("[Recovery] Restoring subscription for %s: %s", strategy_id, symbol)
                            except Exception as e:
                                logger.warning("[Recovery] Failed to restore subscription for %s: %s", symbol, e)

                    # 更新策略信息中的 symbols 和 env
                    runner.update_strategy_subscription(strategy_id, symbols, binance_env)

                    # 启动策略
                    try:
                        await runner.start(strategy_id)
                        logger.info("[Recovery] Strategy recovered: %s", strategy_id)

                        # 发布恢复事件
                        from trader.api.routes.strategies import _event_callback_dispatcher
                        if _event_callback_dispatcher:
                            _event_callback_dispatcher(strategy_id, "strategy.recovered", {
                                "strategy_id": strategy_id,
                                "symbols": symbols,
                                "env": binance_env,
                                "recovered_at": datetime.now(timezone.utc).isoformat(),
                            })
                    except Exception as e:
                        logger.error("[Recovery] Failed to recover strategy %s: %s", strategy_id, e)
                        state["recovery_error"] = str(e)
                        storage.save_strategy_runtime_state(state)

            # 执行恢复（在 lifespan 启动阶段执行，不使用 fire-and-forget）
            await _recover_runtime_state()
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
            binance_env = get_binance_env()
            config = BinanceSpotDemoBrokerConfig.for_env(
                api_key,
                secret_key,
                env=binance_env,
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
    if _binance_cascade_controller is not None:
        try:
            await _binance_cascade_controller.stop()
            logger.info("[Lifespan] DegradedCascadeController stopped")
        except Exception as e:
            logger.error(f"[Lifespan] Error stopping DegradedCascadeController: {e}")
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
app.include_router(sse.router)


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
