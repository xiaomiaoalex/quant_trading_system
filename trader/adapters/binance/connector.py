"""
Binance Connector - Unified Coordinator
=========================================
统一的 Binance 连接协调器，整合 Public/Private 流和 REST 对齐。

功能：
- 统一管理 PublicStreamManager 和 PrivateStreamManager
- 输出 RawOrderUpdate, RawFillUpdate, RestAlignmentSnapshot
- 提供 AdapterHealth 状态给外部
- 完整的错误处理和恢复

设计原则：
- Public/Private 流完全隔离
- 共享限流器和退避器
- 统一的健康状态监控
"""
import asyncio
import logging
import time
from dataclasses import dataclass, field
from typing import Dict, List, Optional, Any, Callable, Awaitable
from enum import Enum

from trader.adapters.binance.rate_limit import RestRateBudget, Priority, RateBudgetConfig
from trader.adapters.binance.backoff import BackoffController, BackoffConfig
from trader.adapters.binance.stream_base import StreamState
from trader.adapters.binance.public_stream import (
    PublicStreamManager, PublicStreamConfig, MarketEvent
)
from trader.adapters.binance.private_stream import (
    PrivateStreamManager, PrivateStreamConfig,
    BinanceCredentials, RawOrderUpdate, RawFillUpdate
)
from trader.adapters.binance.rest_alignment import (
    RESTAlignmentCoordinator, AlignmentConfig, RestAlignmentSnapshot
)


logger = logging.getLogger(__name__)


class AdapterHealth(Enum):
    """适配器健康状态"""
    HEALTHY = "HEALTHY"
    DEGRADED = "DEGRADED"
    UNHEALTHY = "UNHEALTHY"
    DISCONNECTED = "DISCONNECTED"


@dataclass
class AdapterHealthReport:
    """适配器健康报告"""
    public_stream_state: StreamState
    private_stream_state: StreamState
    public_stream_healthy: bool
    private_stream_healthy: bool
    rest_alignment_healthy: bool
    rate_budget_state: Dict
    backoff_state: Dict
    overall_health: AdapterHealth
    last_update_ts: float
    metrics: Dict[str, Any]


@dataclass
class BinanceConnectorConfig:
    """连接器配置"""
    testnet: bool = True

    public_stream_config: Optional[PublicStreamConfig] = None
    private_stream_config: Optional[PrivateStreamConfig] = None
    alignment_config: Optional[AlignmentConfig] = None
    rate_budget_config: Optional[RateBudgetConfig] = None
    backoff_config: Optional[BackoffConfig] = None


class BinanceConnector:
    """
    Binance 连接协调器

    整合 Public/Private 流和 REST 对齐，提供统一的接口。
    """

    def __init__(
        self,
        api_key: str,
        secret_key: str,
        streams: Optional[List[str]] = None,
        config: Optional[BinanceConnectorConfig] = None
    ):
        self._api_key = api_key
        self._secret_key = secret_key
        self._config = config or BinanceConnectorConfig()

        self._credentials = BinanceCredentials(
            api_key=api_key,
            secret_key=secret_key,
            testnet=self._config.testnet
        )

        self._rate_budget = RestRateBudget(self._config.rate_budget_config)
        self._backoff = BackoffController(self._config.backoff_config)

        public_streams = streams or ["btcusdt@trade", "btcusdt@kline_1m"]
        public_config = self._config.public_stream_config or PublicStreamConfig(
            streams=public_streams
        )
        self._public_manager = PublicStreamManager(config=public_config)

        if self._config.private_stream_config is not None:
            private_config = self._config.private_stream_config
        else:
            if self._config.testnet:
                private_config = PrivateStreamConfig(
                    base_url="wss://testnet.binance.vision/ws",
                    rest_url="https://testnet.binance.vision/api",
                )
            else:
                private_config = PrivateStreamConfig(
                    base_url="wss://stream.binance.com:9443/ws",
                    rest_url="https://api.binance.com/api",
                )
        self._private_manager = PrivateStreamManager(
            credentials=self._credentials,
            config=private_config
        )

        alignment_config = self._config.alignment_config or AlignmentConfig(
            base_url="https://testnet.binance.vision/api" if self._config.testnet else "https://api.binance.com/api"
        )
        self._rest_coordinator = RESTAlignmentCoordinator(
            api_key=api_key,
            secret_key=secret_key,
            rate_budget=self._rate_budget,
            backoff=self._backoff,
            config=alignment_config
        )

        self._running = False
        self._public_manager.register_market_handler(self._on_public_data)
        self._private_manager.register_order_handler(self._on_order_update)
        self._private_manager.register_fill_handler(self._on_fill_update)
        self._private_manager.set_force_resync_callback(self._on_force_resync)
        self._rest_coordinator.register_snapshot_handler(self._on_rest_snapshot)

        self._order_update_handlers: List[Callable[[RawOrderUpdate], None]] = []
        self._fill_update_handlers: List[Callable[[RawFillUpdate], None]] = []
        self._market_event_handlers: List[Callable[[MarketEvent], None]] = []
        self._snapshot_handlers: List[Callable[[RestAlignmentSnapshot], None]] = []
        self._health_handlers: List[Callable[[AdapterHealthReport], None]] = []

        self._health_check_task: Optional[asyncio.Task] = None

    def register_order_handler(self, handler: Callable[[RawOrderUpdate], None]) -> None:
        """注册订单更新处理器"""
        self._order_update_handlers.append(handler)

    def register_fill_handler(self, handler: Callable[[RawFillUpdate], None]) -> None:
        """注册成交更新处理器"""
        self._fill_update_handlers.append(handler)

    def register_market_handler(self, handler: Callable[[MarketEvent], None]) -> None:
        """注册市场事件处理器"""
        self._market_event_handlers.append(handler)

    def register_snapshot_handler(self, handler: Callable[[RestAlignmentSnapshot], None]) -> None:
        """注册 REST 对齐快照处理器"""
        self._snapshot_handlers.append(handler)

    def register_health_handler(self, handler: Callable[[AdapterHealthReport], None]) -> None:
        """注册健康状态处理器"""
        self._health_handlers.append(handler)

    async def start(self) -> None:
        """启动连接器"""
        if self._running:
            return

        self._running = True
        logger.info("[BinanceConnector] Starting...")

        await self._rest_coordinator.start()
        await self._public_manager.start()
        await self._private_manager.start()

        self._health_check_task = asyncio.create_task(self._health_check_loop())

        logger.info("[BinanceConnector] Started")

    async def stop(self) -> None:
        """停止连接器"""
        if not self._running:
            return

        self._running = False
        logger.info("[BinanceConnector] Stopping...")

        if self._health_check_task:
            self._health_check_task.cancel()
            try:
                await self._health_check_task
            except asyncio.CancelledError:
                pass

        await self._private_manager.stop()
        await self._public_manager.stop()
        await self._rest_coordinator.stop()

        logger.info("[BinanceConnector] Stopped")

    def _on_public_data(self, event: MarketEvent) -> None:
        """处理公有流数据"""
        for handler in self._market_event_handlers:
            try:
                handler(event)
            except Exception as e:
                logger.error(f"[BinanceConnector] Market handler error: {e}")

    def _on_order_update(self, update: RawOrderUpdate) -> None:
        """处理订单更新"""
        for handler in self._order_update_handlers:
            try:
                handler(update)
            except Exception as e:
                logger.error(f"[BinanceConnector] Order handler error: {e}")

    def _on_fill_update(self, update: RawFillUpdate) -> None:
        """处理成交更新"""
        for handler in self._fill_update_handlers:
            try:
                handler(update)
            except Exception as e:
                logger.error(f"[BinanceConnector] Fill handler error: {e}")

    def _on_rest_snapshot(self, snapshot: RestAlignmentSnapshot) -> None:
        """处理 REST 对齐快照"""
        logger.info(
            f"[BinanceConnector] RestAlignmentSnapshot received: "
            f"orders={len(snapshot.open_orders)}, reason={snapshot.alignment_reason}"
        )
        for handler in self._snapshot_handlers:
            try:
                handler(snapshot)
            except Exception as e:
                logger.error(f"[BinanceConnector] Snapshot handler error: {e}")

    async def _on_force_resync(self, reason: str) -> Optional[RestAlignmentSnapshot]:
        """处理强制对齐请求"""
        logger.info(f"[BinanceConnector] Force resync triggered: {reason}")
        if reason == "ws_reconnect":
            return await self._rest_coordinator.force_alignment_p0(reason)
        else:
            priority = Priority.P0
            return await self._rest_coordinator.force_alignment(reason, priority)

    async def _health_check_loop(self) -> None:
        """健康检查循环"""
        while self._running:
            try:
                await asyncio.sleep(10)
                health_report = self.get_health()

                for handler in self._health_handlers:
                    try:
                        handler(health_report)
                    except Exception as e:
                        logger.error(f"[BinanceConnector] Health handler error: {e}")

            except asyncio.CancelledError:
                break
            except Exception as e:
                logger.error(f"[BinanceConnector] Health check error: {e}")

    def get_health(self) -> AdapterHealthReport:
        """获取健康状态"""
        public_state = self._public_manager.state
        private_state = self._private_manager.state

        public_healthy = public_state == StreamState.CONNECTED
        private_healthy = private_state == StreamState.CONNECTED
        rest_metrics = self._rest_coordinator.get_metrics()
        last_rest_success_ts = rest_metrics.get("last_rest_success_ts_ms", 0)
        rest_healthy = last_rest_success_ts > 0 and (time.time() * 1000 - last_rest_success_ts) < 60000

        if public_healthy and private_healthy and rest_healthy:
            overall = AdapterHealth.HEALTHY
        elif not private_healthy:
            overall = AdapterHealth.UNHEALTHY
        elif not public_healthy or not rest_healthy:
            overall = AdapterHealth.DEGRADED
        else:
            overall = AdapterHealth.DISCONNECTED

        return AdapterHealthReport(
            public_stream_state=public_state,
            private_stream_state=private_state,
            public_stream_healthy=public_healthy,
            private_stream_healthy=private_healthy,
            rest_alignment_healthy=rest_healthy,
            rate_budget_state=self._rate_budget.get_state(),
            backoff_state=self._backoff.get_state(),
            overall_health=overall,
            last_update_ts=time.time(),
            metrics={
                "public": self._public_manager.get_status(),
                "private": self._private_manager.get_status(),
                "rest": rest_metrics,
            }
        )

    @property
    def public_stream(self) -> PublicStreamManager:
        """获取公有流管理器"""
        return self._public_manager

    @property
    def private_stream(self) -> PrivateStreamManager:
        """获取私有流管理器"""
        return self._private_manager

    @property
    def rest_coordinator(self) -> RESTAlignmentCoordinator:
        """获取 REST 对齐协调器"""
        return self._rest_coordinator
