"""
StrategyRuntimeOrchestrator - 策略运行时编排服务
================================================

职责：
1. 管理每个策略的运行时上下文（task/queue/symbol/status）
2. 订阅实时行情并转换为 MarketData
3. 按顺序调用 runner.tick(strategy_id, market_data)
4. 与 Binance 公有流组件对接
5. 处理 start/stop/unload 生命周期

架构约束：
- 属于 Service 层，可以有 IO
- 不允许跨层污染
- 使用 asyncio.TaskGroup 管理任务，确保资源正确释放
"""
import asyncio
import logging
import time
from dataclasses import dataclass, field
from datetime import datetime, timezone
from decimal import Decimal
from typing import Dict, List, Optional, Any, Callable, TYPE_CHECKING

from trader.adapters.binance.public_stream import MarketEvent
from trader.core.application.strategy_protocol import MarketData, MarketDataType
from trader.services.strategy_runner import StrategyRunner

if TYPE_CHECKING:
    from trader.adapters.binance.connector import BinanceConnector

logger = logging.getLogger(__name__)


class RuntimeStatus:
    """运行时状态"""
    IDLE = "IDLE"
    RUNNING = "RUNNING"
    STOPPING = "STOPPING"
    STOPPED = "STOPPED"
    ERROR = "ERROR"


@dataclass(slots=True)
class RuntimeContext:
    """策略运行时上下文"""
    strategy_id: str
    symbol: str
    status: str = RuntimeStatus.IDLE
    started_at: Optional[datetime] = None
    last_tick_at: Optional[datetime] = None
    tick_count: int = 0
    signal_count: int = 0
    error_count: int = 0
    last_error: Optional[str] = None
    stop_reason: Optional[str] = None


class StrategyRuntimeOrchestrator:
    """
    策略运行时编排服务

    负责：
    1. 多策略并发运行的调度管理
    2. 实时行情订阅与 tick 驱动
    3. 异常隔离（单策略崩溃不影响其他策略）

    使用示例：
        orchestrator = StrategyRuntimeOrchestrator(runner, connector)

        # 启动策略
        await orchestrator.start_strategy("my_strategy", "BTCUSDT")

        # 停止策略
        await orchestrator.stop_strategy("my_strategy")

        # 卸载策略（清理所有资源）
        await orchestrator.unload_strategy("my_strategy")
    """

    def __init__(
        self,
        runner: StrategyRunner,
        connector: Optional["BinanceConnector"] = None,
        event_callback: Optional[Callable[[str, str, Dict[str, Any]], None]] = None,
    ):
        """
        初始化编排服务

        Args:
            runner: 策略执行器实例
            connector: Binance 连接器（用于订阅公有流）
            event_callback: 事件发布回调，接收 (strategy_id, event_type, payload)
        """
        self._runner = runner
        self._connector = connector
        self._event_callback = event_callback

        # 每个策略的运行时上下文
        self._contexts: Dict[str, RuntimeContext] = {}

        # 全局运行标记
        self._running = False

        # 锁
        self._contexts_lock = asyncio.Lock()

        # 已订阅的 symbol 集合（避免重复订阅）
        self._subscribed_symbols: set = set()

        # 诊断：市场事件计数器（用于限速日志）
        self._market_event_count: int = 0
        self._last_diag_log_ts: float = 0.0

        # 如果有 connector，注册市场数据处理器
        if connector is not None:
            connector.register_market_handler(self._on_market_event)

    def set_connector(self, connector: "BinanceConnector") -> None:
        """
        注入 BinanceConnector（由 lifespan 调用）

        Args:
            connector: BinanceConnector 实例
        """
        self._connector = connector
        connector.register_market_handler(self._on_market_event)
        handler_count = 0
        try:
            handler_count = len(connector._public_manager._market_event_handlers)
        except Exception:
            pass
        logger.info(
            f"[Orchestrator] Connector injected: connector={type(connector).__name__}, "
            f"handler_count={handler_count}"
        )

    def _on_market_event(self, event: MarketEvent) -> None:
        """
        处理市场事件，将事件分发给所有运行中的策略

        注意：此方法在市场事件循环中调用，必须快速返回。
        """
        self._market_event_count += 1
        now = time.time()

        # 限速诊断日志：每 10 秒最多一条
        if now - self._last_diag_log_ts > 10.0:
            self._last_diag_log_ts = now
            running_contexts = [
                ctx for ctx in self._contexts.values()
                if ctx.status == RuntimeStatus.RUNNING
            ]
            pub_running = False
            try:
                pub_running = self._connector.public_stream.is_running()
            except Exception:
                pass
            logger.debug(
                f"[Orchestrator] Market event diag: "
                f"event_type={event.event_type} orch_running={self._running} "
                f"running_strategies={len(running_contexts)} pub_stream_running={pub_running} "
                f"(total_events_since_start={self._market_event_count})"
            )

        if not self._running:
            return

        try:
            # 从事件中提取交易对
            symbol = self._extract_symbol_from_event(event)
            if symbol is None:
                return

            # 将 MarketEvent 转换为 MarketData
            market_data = self._convert_to_market_data(event, symbol)
            if market_data is None:
                return

            # 异步分发给所有订阅该交易对的策略
            asyncio.create_task(self._dispatch_to_strategies(symbol, market_data))

        except Exception as e:
            logger.error(f"[Orchestrator] Error processing market event: {e}")

    def _extract_symbol_from_event(self, event: MarketEvent) -> Optional[str]:
        """从 MarketEvent 中提取交易对"""
        data = event.data
        if "s" in data:
            return data["s"]
        if "symbol" in data:
            return data["symbol"]
        return None

    def _convert_to_market_data(
        self, event: MarketEvent, symbol: str
    ) -> Optional[MarketData]:
        """
        将 MarketEvent 转换为 MarketData

        Args:
            event: 市场事件
            symbol: 交易对符号

        Returns:
            MarketData 或 None（如果事件类型不支持）
        """
        data = event.data
        event_type = event.event_type

        try:
            if event_type == "trade":
                price = Decimal(str(data.get("p", "0")))
                volume = Decimal(str(data.get("q", "0")))
                return MarketData(
                    symbol=symbol,
                    data_type=MarketDataType.TRADE,
                    price=price,
                    volume=volume,
                    timestamp=datetime.fromtimestamp(
                        event.exchange_ts_ms / 1000, tz=timezone.utc
                    ),
                    metadata={
                        "is_buyer_maker": data.get("m", False),
                        "trade_id": data.get("t"),
                    },
                )

            elif event_type == "kline":
                kline = data.get("k", {})
                if kline:
                    interval = kline.get("i", "1m")
                    o = Decimal(str(kline.get("o", "0")))
                    h = Decimal(str(kline.get("h", "0")))
                    l = Decimal(str(kline.get("l", "0")))
                    c = Decimal(str(kline.get("c", "0")))
                    v = Decimal(str(kline.get("v", "0")))
                    return MarketData(
                        symbol=symbol,
                        data_type=MarketDataType.KLINE,
                        price=c,
                        volume=v,
                        timestamp=datetime.fromtimestamp(
                            kline.get("T", event.exchange_ts_ms) / 1000, tz=timezone.utc
                        ),
                        kline_open=o,
                        kline_high=h,
                        kline_low=l,
                        kline_close=c,
                        kline_interval=interval,
                        metadata={
                            "is_closed": kline.get("x", False),
                        },
                    )

            elif event_type == "depthUpdate" or event_type == "depth":
                return MarketData(
                    symbol=symbol,
                    data_type=MarketDataType.DEPTH,
                    price=Decimal(str(data.get("b", ["0"])[0] if data.get("b") else "0")),
                    bid=Decimal(str(data.get("b", ["0"])[0] if data.get("b") else "0")),
                    ask=Decimal(str(data.get("a", ["0"])[0] if data.get("a") else "0")),
                    timestamp=datetime.fromtimestamp(
                        event.exchange_ts_ms / 1000, tz=timezone.utc
                    ),
                )

            elif event_type == "24hrTicker" or event_type == "ticker":
                price = Decimal(str(data.get("c", "0")))
                volume = Decimal(str(data.get("v", "0")))
                return MarketData(
                    symbol=symbol,
                    data_type=MarketDataType.TICKER,
                    price=price,
                    volume=volume,
                    timestamp=datetime.fromtimestamp(
                        event.exchange_ts_ms / 1000, tz=timezone.utc
                    ),
                )

            else:
                # 对于其他事件类型，使用默认转换
                return MarketData(
                    symbol=symbol,
                    data_type=MarketDataType.KLINE,
                    price=Decimal(str(data.get("c", data.get("price", "0")))),
                    volume=Decimal(str(data.get("v", data.get("volume", "0")))),
                    timestamp=datetime.fromtimestamp(
                        event.exchange_ts_ms / 1000, tz=timezone.utc
                    ),
                )

        except Exception as e:
            logger.warning(f"[Orchestrator] Failed to convert event {event_type}: {e}")
            return None

    async def _dispatch_to_strategies(self, symbol: str, market_data: MarketData) -> None:
        """
        将市场数据分发给所有订阅该交易对的策略

        Args:
            symbol: 交易对
            market_data: 市场数据
        """
        async with self._contexts_lock:
            running_contexts = [
                ctx for ctx in self._contexts.values()
                if ctx.status == RuntimeStatus.RUNNING and ctx.symbol == symbol
            ]

        for ctx in running_contexts:
            try:
                # 调用 runner.tick()
                signal = await self._runner.tick(ctx.strategy_id, market_data)

                # 从 StrategyRuntimeInfo (唯一真相源) 同步计数器到 ctx
                info = self._runner.get_status(ctx.strategy_id)
                if info is not None:
                    ctx.tick_count = info.tick_count
                    ctx.signal_count = info.signal_count
                    ctx.error_count = info.error_count
                    ctx.last_tick_at = info.last_tick_at
                    ctx.last_error = info.last_error

                # 发布 tick 事件
                if self._event_callback:
                    self._event_callback(ctx.strategy_id, "strategy.tick", {
                        "symbol": symbol,
                        "tick_count": ctx.tick_count,
                        "has_signal": signal is not None,
                    })

            except Exception as e:
                logger.error(
                    f"[Orchestrator] Tick error for {ctx.strategy_id}: {e}"
                )

                # runner.tick() 未捕获的异常，手动递增 info.error_count
                info = self._runner.get_status(ctx.strategy_id)
                if info is not None:
                    info.error_count += 1
                    info.last_error = str(e)
                    ctx.error_count = info.error_count
                    ctx.last_error = info.last_error
                else:
                    ctx.error_count += 1
                    ctx.last_error = str(e)

                # 错误次数过多，停止策略
                if ctx.error_count >= 10:
                    logger.error(
                        f"[Orchestrator] {ctx.strategy_id} error count exceeded, stopping"
                    )
                    await self.stop_strategy(ctx.strategy_id, reason=f"Error count exceeded: {e}")

    async def start_strategy(
        self,
        strategy_id: str,
        symbol: str,
    ) -> RuntimeContext:
        """
        启动策略的 tick 调度

        Args:
            strategy_id: 策略ID
            symbol: 交易对（如 BTCUSDT）

        Returns:
            RuntimeContext: 运行时上下文

        Raises:
            ValueError: 策略未加载或已在运行
        """
        # 检查策略是否已加载
        info = self._runner.get_status(strategy_id)
        if info is None:
            raise ValueError(f"策略未加载: {strategy_id}")

        async with self._contexts_lock:
            # 检查是否已在运行
            if strategy_id in self._contexts:
                ctx = self._contexts[strategy_id]
                if ctx.status == RuntimeStatus.RUNNING:
                    raise ValueError(f"策略已在运行: {strategy_id}")

            # 创建运行时上下文
            ctx = RuntimeContext(
                strategy_id=strategy_id,
                symbol=symbol.upper(),
                status=RuntimeStatus.RUNNING,
                started_at=datetime.now(timezone.utc),
            )
            self._contexts[strategy_id] = ctx

        # 确保 connector 已启动并订阅对应 symbol 的行情
        logger.info(f"[Orchestrator] start_strategy: strategy={strategy_id} self._connector={type(self._connector).__name__ if self._connector else None}")
        if self._connector is not None:
            symbol_lower = symbol.lower()
            if symbol_lower not in self._subscribed_symbols:
                try:
                    trade_stream = f"{symbol_lower}@trade"
                    kline_stream = f"{symbol_lower}@kline_1m"
                    pub_mgr = self._connector.public_stream
                    existing_streams = list(pub_mgr._public_config.streams)
                    new_streams = list(existing_streams)
                    for stream in (trade_stream, kline_stream):
                        if stream not in new_streams:
                            new_streams.append(stream)

                    pub_mgr._public_config.streams = new_streams
                    if pub_mgr.is_running():
                        await pub_mgr.stop()
                        await pub_mgr.start()
                    self._subscribed_symbols.add(symbol_lower)
                    logger.info(f"[Orchestrator] Subscribed to {trade_stream}, {kline_stream}")
                except Exception as e:
                    logger.warning(f"[Orchestrator] Failed to subscribe {symbol}: {e}")
        else:
            logger.warning(f"[Orchestrator] No connector available, market data will not flow for {symbol}")

        # 全局运行标记
        self._running = True

        logger.info(f"[Orchestrator] Strategy started: {strategy_id} for {symbol}")
        return ctx

    async def stop_strategy(
        self,
        strategy_id: str,
        reason: Optional[str] = None,
    ) -> RuntimeContext:
        """
        停止策略的 tick 调度

        Args:
            strategy_id: 策略ID
            reason: 停止原因

        Returns:
            RuntimeContext: 更新后的运行时上下文

        Raises:
            ValueError: 策略未启动
        """
        async with self._contexts_lock:
            if strategy_id not in self._contexts:
                raise ValueError(f"策略未启动: {strategy_id}")

            ctx = self._contexts[strategy_id]
            ctx.status = RuntimeStatus.STOPPING
            ctx.stop_reason = reason

        ctx.status = RuntimeStatus.STOPPED
        logger.info(f"[Orchestrator] Strategy stopped: {strategy_id}, reason={reason}")
        return ctx

    async def unload_strategy(self, strategy_id: str) -> None:
        """
        卸载策略，清理所有资源

        Args:
            strategy_id: 策略ID
        """
        async with self._contexts_lock:
            if strategy_id in self._contexts:
                ctx = self._contexts[strategy_id]

                # 如果还在运行，先标记为停止中
                if ctx.status == RuntimeStatus.RUNNING:
                    ctx.status = RuntimeStatus.STOPPING

                # 移除上下文
                del self._contexts[strategy_id]
                logger.info(f"[Orchestrator] Strategy unloaded: {strategy_id}")

        # 检查是否还有运行中的策略
        async with self._contexts_lock:
            has_running = any(
                ctx.status == RuntimeStatus.RUNNING for ctx in self._contexts.values()
            )
            if not has_running:
                self._running = False

    def get_context(self, strategy_id: str) -> Optional[RuntimeContext]:
        """
        获取策略运行时上下文

        Args:
            strategy_id: 策略ID

        Returns:
            RuntimeContext 或 None
        """
        return self._contexts.get(strategy_id)

    def list_contexts(self) -> List[RuntimeContext]:
        """
        列出所有策略运行时上下文

        Returns:
            List[RuntimeContext]
        """
        return list(self._contexts.values())

    async def shutdown(self) -> None:
        """
        关闭编排服务，停止所有策略
        """
        logger.info("[Orchestrator] Shutting down...")

        # 停止所有策略
        async with self._contexts_lock:
            strategy_ids = list(self._contexts.keys())

        for strategy_id in strategy_ids:
            try:
                await self.stop_strategy(strategy_id, reason="Orchestrator shutdown")
            except Exception as e:
                logger.warning(f"[Orchestrator] Error stopping {strategy_id}: {e}")

        # 清理所有上下文
        async with self._contexts_lock:
            self._contexts.clear()

        self._running = False
        logger.info("[Orchestrator] Shutdown complete")
