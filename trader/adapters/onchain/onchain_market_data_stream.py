"""
On-Chain/Macro Market Data Adapter
==================================
从公开区块链数据源采集链上和宏观市场数据，写入 Feature Store。

数据源（按优先级）：
1. Binance Futures 公开数据（资金费率、OI、爆仓）
2. 公开区块链 API（链上数据）
3. 降级：使用内存缓存的最近数据

功能：
- 爆仓数据采集（ liquidation data）
- 交易所净流入/流出
- 稳定币供应量变化
- 写入 Feature Store：feature_name=liquidation|exchange_flows|stablecoin_supply

特性：
- 降级保护：采集失败仅记录日志，不影响主交易流程
- 可配置的定时任务
- 支持多 symbol 并行拉取
"""
import asyncio
import json
import logging
import random
import time
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Dict, List, Optional, Any, Set, TYPE_CHECKING, Callable, Awaitable

if TYPE_CHECKING:
    import aiohttp

from trader.adapters.persistence.feature_store import FeatureStore, get_feature_store


logger = logging.getLogger(__name__)


# 公开数据源 Base URLs
BINANCE_FUTURES_BASE_URL = "https://fapi.binance.com"
GLASSNODE_BASE_URL = "https://api.glassnode.com"

# Symbol 到 CoinGecko ID 的映射表（用于稳定币供应查询）
_COINGECKO_SYMBOL_TO_ID = {
    "USDT": "tether",
    "USDC": "usd-coin",
    "DAI": "dai",
    "BUSD": "binance-usd",
}


@dataclass
class OnChainMarketDataConfig:
    """On-Chain 数据采集配置"""
    # Binance Futures API
    binance_base_url: str = BINANCE_FUTURES_BASE_URL
    # 爆仓采集间隔（秒），默认 1 分钟
    liquidation_poll_interval: float = 60.0
    # 交易所流量采集间隔（秒），默认 5 分钟
    flow_poll_interval: float = 5.0 * 60
    # 稳定币供应采集间隔（秒），默认 30 分钟
    supply_poll_interval: float = 30.0 * 60
    # 采集超时（秒）
    request_timeout: float = 10.0
    # 重试次数
    max_retries: int = 3
    # 重试间隔（秒）
    retry_delay: float = 1.0


@dataclass
class LiquidationRecord:
    """爆仓记录"""
    symbol: str
    side: str  # "buy" or "sell"
    price: float
    quantity: float
    quantity_usd: float
    exchange_ts_ms: int
    local_ts_ms: int


@dataclass
class ExchangeFlowRecord:
    """交易所流量记录"""
    symbol: str  # "BTC" or "TOTAL" for all assets
    inflow: float
    outflow: float
    net_flow: float
    exchange_ts_ms: int
    local_ts_ms: int


@dataclass
class StablecoinSupplyRecord:
    """稳定币供应记录"""
    symbol: str  # "USDT", "USDC", etc.
    total_supply: float
    supply_change_24h: float
    exchange_ts_ms: int
    local_ts_ms: int


@dataclass(frozen=True)
class RawLiquidationEvent:
    """原始爆仓事件（来自 WebSocket）"""
    event_time_ms: int  # 事件时间（毫秒）
    symbol: str
    side: str  # "buy" or "sell"
    price: float
    quantity: float  # 数量（标的数量）
    notional_usd: float  # 价值（USD）
    order_type: str  # "ForceOrder"
    source: str = "binance_futures_ws"


@dataclass(frozen=True)
class LiquidationBucket:
    """1分钟爆仓聚合桶"""
    bucket_ts_ms: int  # 桶时间戳（1分钟对齐）
    liquidation_count: int
    liquidation_notional_usd: float
    long_liquidation_notional_usd: float
    short_liquidation_notional_usd: float
    net_liquidation_imbalance_usd: float  # long - short
    symbols: List[str]  # 该桶涉及的 symbol 列表


class LiquidationAggregator:
    """
    爆仓数据聚合器

    从 WebSocket 接收原始爆仓事件，按 1 分钟窗口聚合后写入 Feature Store。
    """

    # 聚合窗口大小（毫秒）
    BUCKET_SIZE_MS = 60 * 1000

    # 最大重试次数，超过后丢弃该桶
    MAX_FLUSH_RETRIES = 3

    def __init__(
        self,
        feature_store: Optional[FeatureStore] = None,
        flush_interval_seconds: float = 60.0,
    ):
        self._feature_store = feature_store or get_feature_store()
        self._flush_interval = flush_interval_seconds
        self._buckets: Dict[int, List[RawLiquidationEvent]] = {}  # bucket_ts -> events
        self._bucket_retry_count: Dict[int, int] = {}  # bucket_ts -> retry count
        self._running = False
        self._draining = False  # True during shutdown to reject new events
        self._flush_task: Optional[asyncio.Task] = None
        self._ws_task: Optional[asyncio.Task] = None
        self._ws_connector: Optional["BinanceLiquidationWSConnector"] = None
        self._lock = asyncio.Lock()

    async def add_event(self, event: RawLiquidationEvent) -> None:
        """添加一个原始爆仓事件（线程安全）"""
        # Reject new events during shutdown to prevent race with stop()
        if self._draining:
            logger.debug("[LiquidationAgg] add_event rejected: draining flag set")
            return
        bucket_ts = self._align_to_bucket(event.event_time_ms)
        async with self._lock:
            # Double-check draining flag after acquiring lock
            if self._draining:
                logger.debug("[LiquidationAgg] add_event rejected after lock: draining flag set")
                return
            if bucket_ts not in self._buckets:
                self._buckets[bucket_ts] = []
            self._buckets[bucket_ts].append(event)

    def _align_to_bucket(self, ts_ms: int) -> int:
        """将时间戳对齐到桶边界"""
        return (ts_ms // self.BUCKET_SIZE_MS) * self.BUCKET_SIZE_MS

    def _aggregate_bucket_unsafe(self, bucket_ts: int) -> Optional[tuple[LiquidationBucket, Dict[str, List[RawLiquidationEvent]]]]:
        """
        聚合单个桶的事件（内部方法，无锁保护）

        Note: 此方法读取 self._buckets。调用方必须确保已持有 self._lock：
        - _aggregate_bucket：调用方持有锁
        - _flush_bucket：内部获取锁后调用本方法

        Returns:
            (LiquidationBucket, Dict[symbol -> events]) 或 None
        """
        if bucket_ts not in self._buckets:
            return None
        events = self._buckets[bucket_ts]
        if not events:
            return None

        liquidation_count = len(events)
        total_notional = sum(e.notional_usd for e in events)
        long_notional = sum(e.notional_usd for e in events if e.side.lower() == "buy")
        short_notional = sum(e.notional_usd for e in events if e.side.lower() == "sell")
        symbols = list(set(e.symbol for e in events))

        # 按 symbol 分组
        events_by_symbol: Dict[str, List[RawLiquidationEvent]] = {}
        for event in events:
            if event.symbol not in events_by_symbol:
                events_by_symbol[event.symbol] = []
            events_by_symbol[event.symbol].append(event)

        bucket = LiquidationBucket(
            bucket_ts_ms=bucket_ts,
            liquidation_count=liquidation_count,
            liquidation_notional_usd=total_notional,
            long_liquidation_notional_usd=long_notional,
            short_liquidation_notional_usd=short_notional,
            net_liquidation_imbalance_usd=long_notional - short_notional,
            symbols=symbols,
        )
        return (bucket, events_by_symbol)

    async def _aggregate_bucket(self, bucket_ts: int) -> Optional[tuple[LiquidationBucket, Dict[str, List[RawLiquidationEvent]]]]:
        """
        聚合单个桶的事件（线程安全版本）

        Returns:
            (LiquidationBucket, Dict[symbol -> events]) 或 None
        """
        async with self._lock:
            return self._aggregate_bucket_unsafe(bucket_ts)

    async def _flush_bucket(self, bucket_ts: int) -> bool:
        """
        将聚合桶写入 Feature Store 并删除桶（原子操作，线程安全）

        设计说明：
        - 在锁内执行 read + delete（原子操作），确保 add_event 不会在 flush 期间
          向正在 flush 的桶添加新事件
        - I/O（write_feature）在锁外执行，避免长时间阻塞 add_event
        - 如果 I/O 失败，数据已从 _buckets 删除（无法重试），但这避免了
          长时间持有锁导致的 add_event 饥饿

        Returns:
            True if flush was successful (bucket deleted), False otherwise
        """
        return await self._flush_bucket_locked(bucket_ts)

    async def _flush_bucket_locked(self, bucket_ts: int) -> bool:
        """
        在持有锁的情况下执行 flush 操作（内部方法）

        锁管理策略：
        - 本方法内部获取锁，执行 read + delete（原子操作）
        - 然后释放锁，执行 I/O（不在锁内）
        - 这样设计是为了避免 I/O 期间长时间持有锁

        Returns:
            True if flush was successful (bucket deleted), False otherwise
        """
        # Step 1: 获取锁，读取并删除桶（原子操作）
        await self._lock.acquire()
        try:
            result = self._aggregate_bucket_unsafe(bucket_ts)
            if result is None:
                # Clean up retry count for empty buckets to prevent memory leaks
                if bucket_ts in self._bucket_retry_count:
                    del self._bucket_retry_count[bucket_ts]
                if bucket_ts in self._buckets:
                    del self._buckets[bucket_ts]
                return True  # No data to flush is considered success

            bucket, events_by_symbol = result

            # Check retry count BEFORE attempting flush
            retry_count = self._bucket_retry_count.get(bucket_ts, 0)
            if retry_count >= self.MAX_FLUSH_RETRIES:
                logger.error(f"[LiquidationAgg] Bucket {bucket_ts} exceeded max retries ({retry_count}), discarding")
                if bucket_ts in self._buckets:
                    del self._buckets[bucket_ts]
                del self._bucket_retry_count[bucket_ts]
                return False

            # 从 _buckets 中删除桶（在锁内执行，确保 add_event 不会向已删除的桶添加数据）
            if bucket_ts in self._buckets:
                del self._buckets[bucket_ts]
            if bucket_ts in self._bucket_retry_count:
                del self._bucket_retry_count[bucket_ts]

        finally:
            self._lock.release()

        # Step 2: 在锁外执行 I/O（避免长时间阻塞 add_event）
        # 注意：如果 I/O 失败，数据已从 _buckets 删除（无法重试）
        # 这是可接受的权衡（I/O 失败通常意味着系统有问题，重试也不会成功）
        try:
            meta = {
                "source": "binance_futures_ws",
                "aggregation": "1m",
                "symbols": ",".join(bucket.symbols),
                "flush_ts_ms": int(time.time() * 1000),
            }

            # 为每个 symbol 写入一条聚合记录
            all_succeeded = True
            for symbol, symbol_events in events_by_symbol.items():
                symbol_long = sum(e.notional_usd for e in symbol_events if e.side.lower() == "buy")
                symbol_short = sum(e.notional_usd for e in symbol_events if e.side.lower() == "sell")
                symbol_notional = sum(e.notional_usd for e in symbol_events)

                try:
                    created, err = await self._feature_store.write_feature(
                        symbol=symbol,
                        feature_name="liquidation_aggregated",
                        version="v1",
                        ts_ms=bucket_ts,
                        value={
                            "liquidation_count": len(symbol_events),
                            "liquidation_notional_usd": symbol_notional,
                            "long_liquidation_notional_usd": symbol_long,
                            "short_liquidation_notional_usd": symbol_short,
                            "net_liquidation_imbalance_usd": symbol_long - symbol_short,
                        },
                        meta=meta,
                    )

                    if created:
                        logger.debug(
                            f"[LiquidationAgg] Flushed bucket {bucket_ts} for {symbol}: "
                            f"count={len(symbol_events)}, notional={symbol_notional:.2f}"
                        )
                    elif err:
                        logger.warning(
                            f"[LiquidationAgg] Feature not created for {symbol}: {err}"
                        )
                        all_succeeded = False

                except Exception as write_err:
                    logger.error(f"[LiquidationAgg] Failed to write feature for {symbol}: {write_err}")
                    all_succeeded = False

            return all_succeeded

        except Exception as e:
            logger.error(f"[LiquidationAgg] Failed to flush bucket {bucket_ts}: {e}")
            return False

    async def _flush_loop(self) -> None:
        """定期 flush 过期桶"""
        while self._running:
            try:
                await asyncio.sleep(self._flush_interval)
                now_ms = int(time.time() * 1000)
                # flush 2分钟前的桶（确保数据完整）
                cutoff_ts = self._align_to_bucket(now_ms) - self.BUCKET_SIZE_MS * 2

                # 获取待 flush 的 bucket_ts 列表
                # 注意：不在此处持有锁，让 _flush_bucket 自己管理锁以避免嵌套获取死锁
                bucket_ts_list = []
                async with self._lock:
                    bucket_ts_list = [ts for ts in sorted(self._buckets.keys()) if ts < cutoff_ts]

                # 调用 _flush_bucket（每个调用独立获取锁）
                for bucket_ts in bucket_ts_list:
                    await self._flush_bucket(bucket_ts)

            except asyncio.CancelledError:
                break
            except Exception as e:
                logger.error(f"[LiquidationAgg] Flush loop error: {e}")

    async def start(self, ws_url: str = "wss://fstream.binance.com/ws/!forceOrder@arr") -> None:
        """启动聚合器（包含 WebSocket 连接）"""
        if self._running:
            logger.warning("[LiquidationAgg] Already running")
            return

        self._running = True
        self._ws_connector = BinanceLiquidationWSConnector(on_event=self.add_event)
        self._ws_task = asyncio.create_task(self._ws_connector.connect(ws_url))
        self._flush_task = asyncio.create_task(self._flush_loop())
        logger.info(f"[LiquidationAgg] Started with WS: {ws_url}")

    async def stop(self, timeout_seconds: float = 10.0) -> None:
        """停止聚合器

        Args:
            timeout_seconds: 最终 flush 的超时时间，避免在持有锁时无限等待
        """
        if not self._running:
            return

        # 设置 draining 标志，阻止新事件进入（但已在执行的事件会完成）
        # 这是防止死锁的关键步骤：确保 add_event 在 stop 获取锁之前就返回
        logger.info("[LiquidationAgg] Setting draining flag, stopping...")
        self._draining = True
        self._running = False

        if self._ws_connector:
            await self._ws_connector.disconnect()
        if self._ws_task is not None:
            self._ws_task.cancel()
            try:
                await self._ws_task
            except asyncio.CancelledError:
                pass
        if self._flush_task is not None:
            self._flush_task.cancel()
            try:
                await self._flush_task
            except asyncio.CancelledError:
                pass

        # 最后 flush 一次所有待处理的桶（带超时避免死锁）
        # 注意：stop 时仍需检查返回值以避免数据丢失
        try:
            logger.info(f"[LiquidationAgg] Final flush of {len(self._buckets)} buckets...")
            await asyncio.wait_for(self._flush_remaining_buckets(), timeout=timeout_seconds)
            logger.info("[LiquidationAgg] Final flush completed")
        except asyncio.TimeoutError:
            logger.warning("[LiquidationAgg] Flush timeout during stop, some buckets may be lost")
        except Exception as e:
            logger.error(f"[LiquidationAgg] Error during final flush: {e}")

        self._draining = False
        logger.info("[LiquidationAgg] Stopped")

    async def _flush_remaining_buckets(self) -> None:
        """Flush 所有剩余的桶（带重试机制）

        设计说明：
        - 每个 _flush_bucket 调用自己管理锁
        - 检查返回值：flush 失败的桶会保留在 self._buckets 中
        - 循环重试直到所有桶都成功 flush 或达到最大重试次数
        - 由于 stop() 设置了 draining 标志，新事件无法添加新桶，
          因此最终所有桶都应该能被 flush
        """
        max_iterations = self.MAX_FLUSH_RETRIES + 1  # 初始尝试 + 重试次数

        for iteration in range(max_iterations):
            if not self._buckets:
                break  # 所有桶都已 flush 成功

            bucket_ts_list = list(sorted(self._buckets.keys()))
            failed_count = 0

            for bucket_ts in bucket_ts_list:
                # 确认桶仍然存在（可能在之前的迭代中被删除）
                if bucket_ts not in self._buckets:
                    continue

                success = await self._flush_bucket(bucket_ts)
                if not success:
                    failed_count += 1
                    logger.warning(
                        f"[LiquidationAgg] Bucket {bucket_ts} flush failed "
                        f"(attempt {iteration + 1}/{max_iterations})"
                    )

            if failed_count == 0:
                break  # 所有桶都成功 flush

            if iteration < max_iterations - 1:
                # 短暂等待后重试
                await asyncio.sleep(0.1)

        # 最终检查：还有未 flush 的桶则记录错误
        if self._buckets:
            logger.error(
                f"[LiquidationAgg] Final flush: {len(self._buckets)} buckets "
                f"still remaining after {max_iterations} iterations, discarding"
            )
            # 清空以防止内存泄漏（这些桶的数据已丢失）
            self._buckets.clear()
            self._bucket_retry_count.clear()


class BinanceLiquidationWSConnector:
    """
    Binance Futures Liquidation WebSocket 连接器

    连接 wss://fstream.binance.com/ws/!forceOrder@arr 获取实时爆仓数据。
    支持自动重连和错误容忍。
    """

    # 回调类型：支持同步或异步回调
    _EventCallback = Callable[[RawLiquidationEvent], Optional[Awaitable[None]]]

    def __init__(self, on_event: _EventCallback):
        self._on_event = on_event
        self._running = False
        self._session: Optional["aiohttp.ClientSession"] = None
        self._ws: Optional["aiohttp.ClientWebSocketResponse"] = None
        self._reconnect_delay = 1.0
        self._max_reconnect_delay = 60.0

    async def connect(self, url: str) -> None:
        """连接到 WebSocket"""
        import aiohttp
        self._running = True

        while self._running:
            try:
                await self._ensure_session()
                async with self._session.ws_connect(url) as ws:
                    self._ws = ws
                    self._reconnect_delay = 1.0  # 重置重连延迟
                    logger.info(f"[LiquidationWS] Connected to {url}")

                    async for msg in ws:
                        if not self._running:
                            break

                        if msg.type == aiohttp.WSMsgType.TEXT:
                            try:
                                event = self._parse_message(msg.data)
                                if event:
                                    # 支持 sync 或 async 回调
                                    result = self._on_event(event)
                                    if asyncio.iscoroutine(result):
                                        await result
                            except asyncio.CancelledError:
                                raise  # Re-raise CancelledError to avoid being caught by broad Exception handler
                            except Exception as e:
                                # 单条消息解析错误不影响整个接收循环
                                logger.warning(f"[LiquidationWS] Failed to parse message: {e}")
                        elif msg.type == aiohttp.WSMsgType.ERROR:
                            logger.error(f"[LiquidationWS] WebSocket error: {ws.exception()}")
                            break
                        elif msg.type in (aiohttp.WSMsgType.CLOSE, aiohttp.WSMsgType.CLOSED):
                            logger.warning("[LiquidationWS] Connection closed")
                            break

            except asyncio.CancelledError:
                break
            except Exception as e:
                logger.error(f"[LiquidationWS] Connection error: {e}")

            if self._running:
                # 添加 jitter 防止多实例同时重连（惊群效应）
                jitter = random.uniform(0.5, 1.5)
                await asyncio.sleep(self._reconnect_delay * jitter)
                self._reconnect_delay = min(self._reconnect_delay * 2, self._max_reconnect_delay)

    async def disconnect(self) -> None:
        """断开连接"""
        self._running = False
        if self._ws:
            await self._ws.close()
            self._ws = None
        await self._close_session()

    async def _ensure_session(self) -> None:
        """确保 HTTP session 可用"""
        import aiohttp
        if self._session is None or self._session.closed:
            timeout = aiohttp.ClientTimeout(total=30)
            self._session = aiohttp.ClientSession(timeout=timeout)

    async def _close_session(self) -> None:
        """关闭 HTTP session"""
        if self._session and not self._session.closed:
            await self._session.close()
            self._session = None

    def _parse_message(self, data: str) -> Optional[RawLiquidationEvent]:
        """
        解析 Binance forceOrder 消息

        消息格式示例：
        {
            "e": "ForceOrder",
            "E": 1568014460943,
            "s": "BTCUSDT",
            "S": "SELL",
            "o": "LIMIT",
            "p": "11000.00",
            "q": "1.0",
            "ap": "11100.00",
            "l": "1.0",
            "v": "1.0"
        }
        """
        try:
            obj = json.loads(data)
        except json.JSONDecodeError as e:
            logger.warning(f"[LiquidationWS] JSON decode error: {e}")
            return None

        # 过滤非 ForceOrder 消息
        if obj.get("e") != "ForceOrder":
            return None

        symbol = obj.get("s", "")
        if not symbol:
            logger.warning(f"[LiquidationWS] Missing symbol field")
            return None
        if not symbol.endswith("USDT"):
            # 只处理 USDT 合约
            return None

        try:
            side = obj["S"].lower()
            price = float(obj["p"])
            quantity = float(obj["l"])
            # notional 使用 actual price (ap) * quantity
            notional = float(obj["ap"]) * quantity
            event_time_ms = int(obj["E"])
            order_type = obj.get("o", "LIMIT")
        except (KeyError, ValueError, TypeError) as e:
            # 数据格式异常，可能意味着 Binance API 发生了变化
            # 使用 error 级别以便生产环境告警
            logger.error(f"[LiquidationWS] Malformed event data: {e}, obj={obj}")
            return None

        return RawLiquidationEvent(
            event_time_ms=event_time_ms,
            symbol=symbol,
            side=side,
            price=price,
            quantity=quantity,
            notional_usd=notional,
            order_type=order_type,
        )


class OnChainMarketDataAdapter:
    """
    On-Chain/Macro 市场数据适配器

    负责从公开数据源采集链上和市场宏观数据，并写入 Feature Store。
    """

    def __init__(
        self,
        config: Optional[OnChainMarketDataConfig] = None,
        feature_store: Optional[FeatureStore] = None,
    ):
        """
        初始化适配器

        Args:
            config: 采集配置
            feature_store: Feature Store 实例
        """
        self._config = config or OnChainMarketDataConfig()
        self._feature_store = feature_store or get_feature_store()
        self._session: Optional[aiohttp.ClientSession] = None

        # 活跃的 symbol 集合
        self._symbols: Set[str] = set()
        # 定时任务
        self._liquidation_task: Optional[asyncio.Task] = None
        self._flow_task: Optional[asyncio.Task] = None
        self._supply_task: Optional[asyncio.Task] = None
        self._running = False

    async def _ensure_session(self) -> None:
        """确保 HTTP session 可用"""
        if self._session is None:
            import aiohttp
            timeout = aiohttp.ClientTimeout(total=self._config.request_timeout)
            self._session = aiohttp.ClientSession(timeout=timeout)
        elif self._session.closed:
            import aiohttp
            timeout = aiohttp.ClientTimeout(total=self._config.request_timeout)
            self._session = aiohttp.ClientSession(timeout=timeout)

    async def _close_session(self) -> None:
        """关闭 HTTP session"""
        if self._session and not self._session.closed:
            await self._session.close()
            self._session = None

    async def _fetch_binance_liquidation_stream(self) -> List[LiquidationRecord]:
        """
        从 Binance 获取最近爆仓数据

        [STUB IMPLEMENTATION] 此方法当前为桩实现，返回空列表。

        注意：Binance Futures 公开 API (fapi) 没有提供爆仓历史接口。
        可用的公开数据源：
        1. Binance WebSocket 得益于 '!liquidation@arr' - 但需要维持连接且仅提供实时数据
        2. Coinglass API (付费) - 提供历史爆仓数据
        3. 第三方数据聚合器

        当前实现：
        - 仅获取 ticker 价格用于监控调试
        - 不产生任何爆仓记录 (liquidation records)

        TODO (P1):
        - 如需历史爆仓分析，应接入 Coinglass 等付费 API
        - 或使用 WebSocket 实时流长期积累数据

        Returns:
            空列表 - Binance 无公开爆仓历史 API，需接入专业数据源
        """
        await self._ensure_session()

        url = f"{self._config.binance_base_url}/fapi/v1/ticker"
        records: List[LiquidationRecord] = []

        try:
            async with self._session.get(url) as resp:
                if resp.status == 200:
                    data = await resp.json()
                    now_ms = int(time.time() * 1000)

                    # 过滤合约交易对数据并记录价格（用于监控）
                    for ticker in data:
                        symbol = ticker.get("symbol", "")
                        if not symbol.endswith("USDT"):
                            continue

                        price = float(ticker.get("lastPrice", 0))
                        if price > 0:
                            # 记录价格数据用于调试/监控
                            # 注意：由于 Binance 没有公开爆仓 API，这里不创建爆仓记录
                            # 如需真实爆仓数据，应接入 Coinglass 等专业数据源
                            logger.debug(f"[OnChain] Ticker price for {symbol}: ${price}")

                    # 返回空列表 - Binance ticker 不包含爆仓历史数据
                    logger.info(
                        "[OnChain] Binance ticker fetched (STUB - no liquidation records, "
                        "Binance has no public liquidation history API)"
                    )
                    return records
                else:
                    logger.warning(f"[OnChain] Binance ticker request failed: status={resp.status}")

        except asyncio.TimeoutError:
            logger.warning("[OnChain] Binance ticker request timeout")
        except Exception as e:
            logger.error(f"[OnChain] Binance ticker request error: {e}")

        return records

    async def _fetch_exchange_flows(self, symbol: str = "BTC") -> Optional[ExchangeFlowRecord]:
        """
        获取交易所净流入/流出数据

        [STUB IMPLEMENTATION] 此方法当前为桩实现，始终返回 None。

        使用 Glassnode API（需要 API key，免费 tier 有限制）
        降级：返回 None 表示数据不可用

        Note:
            Glassnode API 需要注册并获取 API key 才能使用。
            实际部署时需要：
            1. 从 https://glassnode.com 注册账号
            2. 获取 API key 并配置到环境变量
            3. 实现实际的 API 调用逻辑

            当前返回 None 是预期行为，属于降级保护策略的一部分。
            不会抛出异常或阻塞主流程，但也不会采集任何数据。

        Returns:
            None - 当前为桩实现，数据采集功能待完成
        """
        # 注意：Glassnode 免费 tier 需要注册 API key
        # 这里演示架构设计，实际使用需要配置 API key
        logger.debug(f"[OnChain] Exchange flow fetch for {symbol} - STUB (requires API key)")

        # 降级返回 None，不阻塞流程
        # 这是预期行为，不是错误
        return None

    async def _fetch_stablecoin_supply(self, symbol: str = "USDT") -> Optional[StablecoinSupplyRecord]:
        """
        获取稳定币供应量数据

        使用 CoinGecko 免费 API（有限流）
        """
        await self._ensure_session()

        url = "https://api.coingecko.com/api/v3/coins/markets"
        params = {
            "vs_currency": "usd",
            "ids": "tether,usd-coin,binance-usd,dai",
            "order": "market_cap_desc",
            "per_page": 10,
            "page": 1,
            "sparkline": "false",
        }

        # 带退避重试的请求
        for attempt in range(self._config.max_retries):
            try:
                async with self._session.get(url, params=params) as resp:
                    if resp.status == 200:
                        data = await resp.json()
                        now_ms = int(time.time() * 1000)

                        # 查找对应 symbol 的 CoinGecko ID
                        target_coingecko_id = _COINGECKO_SYMBOL_TO_ID.get(symbol.upper())
                        if not target_coingecko_id:
                            logger.debug(f"[OnChain] Unknown stablecoin symbol: {symbol}")
                            return None

                        for coin in data:
                            coin_id = coin.get("id", "")
                            # 比较时忽略大小写和连字符
                            if coin_id.lower().replace("-", "") != target_coingecko_id.lower().replace("-", ""):
                                continue

                            supply = coin.get("total_supply", 0) or 0
                            # 注意：CoinGecko markets API 不返回 total_supply_change_24h
                            # 使用 market_cap_change_percentage_24h 作为市场整体变化的代理指标
                            # 如果需要真实的供应量变化，需要接入付费 API 或自行计算
                            change_24h = coin.get("market_cap_change_percentage_24h", 0) or 0

                            return StablecoinSupplyRecord(
                                symbol=symbol.upper(),
                                total_supply=float(supply),
                                supply_change_24h=float(change_24h),
                                exchange_ts_ms=now_ms,
                                local_ts_ms=now_ms,
                            )

                        logger.debug(f"[OnChain] Stablecoin {symbol} not found in response")
                        return None

                    elif resp.status == 429:
                        # 限流，使用指数退避
                        wait_time = self._config.retry_delay * (2 ** attempt)
                        logger.warning(
                            f"[OnChain] CoinGecko rate limit hit, attempt {attempt + 1}/{self._config.max_retries}, "
                            f"waiting {wait_time}s before retry"
                        )
                        if attempt < self._config.max_retries - 1:
                            await asyncio.sleep(wait_time)
                            continue
                        else:
                            logger.warning("[OnChain] CoinGecko rate limit max retries exceeded")
                            return None
                    else:
                        logger.warning(f"[OnChain] CoinGecko request failed: status={resp.status}")
                        return None

            except asyncio.TimeoutError:
                logger.warning(f"[OnChain] CoinGecko request timeout, attempt {attempt + 1}/{self._config.max_retries}")
                if attempt < self._config.max_retries - 1:
                    await asyncio.sleep(self._config.retry_delay * (2 ** attempt))
                    continue
                else:
                    logger.error("[OnChain] CoinGecko request timeout, max retries exceeded")
                    return None
            except Exception as e:
                logger.error(f"[OnChain] CoinGecko request error: {e}")
                return None

        return None

    async def _write_liquidation_to_store(self, record: LiquidationRecord) -> None:
        """
        将爆仓数据写入 Feature Store

        Args:
            record: 爆仓记录
        """
        try:
            latency_ms = record.local_ts_ms - record.exchange_ts_ms
            meta = {
                "source": "binance_futures",
                "side": record.side,
                "fetched_at": datetime.now(timezone.utc).isoformat() + "Z",
                "latency_ms": latency_ms,
            }

            created, is_dup = await self._feature_store.write_feature(
                symbol=record.symbol,
                feature_name="liquidation",
                version="v1",
                ts_ms=record.exchange_ts_ms,
                value={
                    "side": record.side,
                    "price": record.price,
                    "quantity": record.quantity,
                    "quantity_usd": record.quantity_usd,
                },
                meta=meta,
            )

            if created:
                logger.debug(
                    f"[OnChain] Liquidation written for {record.symbol}: "
                    f"{record.side} {record.quantity_usd} at {record.exchange_ts_ms}"
                )

        except Exception as e:
            # 降级保护：写入失败仅记录日志
            logger.error(f"[OnChain] Failed to write liquidation to store: {e}")

    async def _write_flow_to_store(self, record: ExchangeFlowRecord) -> None:
        """
        将交易所流量写入 Feature Store

        Args:
            record: 流量记录
        """
        try:
            latency_ms = record.local_ts_ms - record.exchange_ts_ms
            meta = {
                "source": "glassnode",
                "fetched_at": datetime.now(timezone.utc).isoformat() + "Z",
                "latency_ms": latency_ms,
            }

            created, is_dup = await self._feature_store.write_feature(
                symbol=record.symbol,
                feature_name="exchange_flow",
                version="v1",
                ts_ms=record.exchange_ts_ms,
                value={
                    "inflow": record.inflow,
                    "outflow": record.outflow,
                    "net_flow": record.net_flow,
                },
                meta=meta,
            )

            if created:
                logger.debug(
                    f"[OnChain] Exchange flow written for {record.symbol}: "
                    f"net={record.net_flow} at {record.exchange_ts_ms}"
                )

        except Exception as e:
            # 降级保护：写入失败仅记录日志
            logger.error(f"[OnChain] Failed to write exchange flow to store: {e}")

    async def _write_supply_to_store(self, record: StablecoinSupplyRecord) -> None:
        """
        将稳定币供应量写入 Feature Store

        Args:
            record: 供应量记录
        """
        try:
            latency_ms = record.local_ts_ms - record.exchange_ts_ms
            meta = {
                "source": "coingecko",
                "fetched_at": datetime.now(timezone.utc).isoformat() + "Z",
                "latency_ms": latency_ms,
            }

            created, is_dup = await self._feature_store.write_feature(
                symbol=record.symbol,
                feature_name="stablecoin_supply",
                version="v1",
                ts_ms=record.exchange_ts_ms,
                value={
                    "total_supply": record.total_supply,
                    "supply_change_24h": record.supply_change_24h,
                },
                meta=meta,
            )

            if created:
                logger.debug(
                    f"[OnChain] Stablecoin supply written for {record.symbol}: "
                    f"{record.total_supply} at {record.exchange_ts_ms}"
                )

        except Exception as e:
            # 降级保护：写入失败仅记录日志
            logger.error(f"[OnChain] Failed to write stablecoin supply to store: {e}")

    async def _fetch_and_write_liquidations(self) -> None:
        """采集并写入爆仓数据"""
        if not self._symbols:
            logger.debug("[OnChain] No symbols configured for liquidation fetch")
            return

        logger.info(f"[OnChain] Fetching liquidation data for {len(self._symbols)} symbols")

        try:
            records = await self._fetch_binance_liquidation_stream()
            for record in records:
                try:
                    await self._write_liquidation_to_store(record)
                except Exception as e:
                    # 降级保护：单个记录失败不影响其他
                    logger.error(f"[OnChain] Failed to write liquidation for {record.symbol}: {e}")

        except Exception as e:
            logger.error(f"[OnChain] Failed to fetch liquidation stream: {e}")

    async def _fetch_and_write_flows(self) -> None:
        """采集并写入交易所流量数据"""
        symbols_to_fetch = list(self._symbols) + ["BTC", "ETH", "TOTAL"]

        logger.info(f"[OnChain] Fetching exchange flows for {len(symbols_to_fetch)} assets")

        for symbol in symbols_to_fetch:
            try:
                record = await self._fetch_exchange_flows(symbol)
                if record:
                    await self._write_flow_to_store(record)
            except Exception as e:
                # 降级保护：单个 symbol 失败不影响其他
                logger.error(f"[OnChain] Failed to fetch exchange flow for {symbol}: {e}")

    async def _fetch_and_write_supplies(self) -> None:
        """采集并写入稳定币供应量数据"""
        stablecoins = ["USDT", "USDC"]

        logger.info(f"[OnChain] Fetching stablecoin supplies")

        for symbol in stablecoins:
            try:
                record = await self._fetch_stablecoin_supply(symbol)
                if record:
                    await self._write_supply_to_store(record)
            except Exception as e:
                # 降级保护：单个稳定币失败不影响其他
                logger.error(f"[OnChain] Failed to fetch stablecoin supply for {symbol}: {e}")

    async def _liquidation_poll_loop(self) -> None:
        """爆仓数据轮询循环"""
        while self._running:
            try:
                await self._fetch_and_write_liquidations()
                # 添加 10% jitter 防止多实例同时请求触发限流
                jitter = random.uniform(0.95, 1.05)
                await asyncio.sleep(self._config.liquidation_poll_interval * jitter)
            except asyncio.CancelledError:
                break
            except Exception as e:
                logger.error(f"[OnChain] Liquidation poll loop error: {e}")
                await asyncio.sleep(60)

    async def _flow_poll_loop(self) -> None:
        """交易所流量轮询循环"""
        while self._running:
            try:
                await self._fetch_and_write_flows()
                # 添加 10% jitter 防止多实例同时请求触发限流
                jitter = random.uniform(0.95, 1.05)
                await asyncio.sleep(self._config.flow_poll_interval * jitter)
            except asyncio.CancelledError:
                break
            except Exception as e:
                logger.error(f"[OnChain] Flow poll loop error: {e}")
                await asyncio.sleep(60)

    async def _supply_poll_loop(self) -> None:
        """稳定币供应量轮询循环"""
        while self._running:
            try:
                await self._fetch_and_write_supplies()
                # 添加 10% jitter 防止多实例同时请求触发限流
                jitter = random.uniform(0.95, 1.05)
                await asyncio.sleep(self._config.supply_poll_interval * jitter)
            except asyncio.CancelledError:
                break
            except Exception as e:
                logger.error(f"[OnChain] Supply poll loop error: {e}")
                await asyncio.sleep(60)

    async def start(self, symbols: Optional[List[str]] = None) -> None:
        """
        启动适配器

        Args:
            symbols: 要采集的 symbol 列表，默认 ["BTCUSDT"]
        """
        if self._running:
            logger.warning("[OnChain] Adapter already running")
            return

        self._running = True

        # 默认 symbol
        if symbols:
            self._symbols = set(symbols)
        elif not self._symbols:
            self._symbols = {"BTCUSDT"}

        await self._ensure_session()

        # 立即执行一次采集（静默失败）
        try:
            await self._fetch_and_write_liquidations()
            await self._fetch_and_write_supplies()
        except Exception as e:
            logger.debug(f"[OnChain] Initial fetch: {e}")

        # 启动定时任务
        self._liquidation_task = asyncio.create_task(self._liquidation_poll_loop())
        self._flow_task = asyncio.create_task(self._flow_poll_loop())
        self._supply_task = asyncio.create_task(self._supply_poll_loop())

        logger.info(f"[OnChain] Adapter started with symbols: {self._symbols}")

    async def stop(self) -> None:
        """停止适配器"""
        if not self._running:
            return

        self._running = False

        # 取消定时任务
        for task, name in [
            (self._liquidation_task, "liquidation"),
            (self._flow_task, "flow"),
            (self._supply_task, "supply"),
        ]:
            if task:
                task.cancel()
                try:
                    await task
                except asyncio.CancelledError:
                    pass
                logger.debug(f"[OnChain] {name} poll task cancelled")

        await self._close_session()

        logger.info("[OnChain] Adapter stopped")

    def add_symbol(self, symbol: str) -> None:
        """添加采集 symbol"""
        self._symbols.add(symbol)
        logger.debug(f"[OnChain] Symbol added: {symbol}, total: {len(self._symbols)}")

    def remove_symbol(self, symbol: str) -> None:
        """移除采集 symbol"""
        self._symbols.discard(symbol)
        logger.debug(f"[OnChain] Symbol removed: {symbol}, remaining: {len(self._symbols)}")

    def is_running(self) -> bool:
        """检查是否正在运行"""
        return self._running

    @property
    def symbols(self) -> Set[str]:
        """获取当前采集的 symbol 集合"""
        return self._symbols.copy()


# 全局实例（需要锁保护多协程并发访问）
_global_adapter: Optional[OnChainMarketDataAdapter] = None
_adapter_lock: Optional[asyncio.Lock] = None


def _get_adapter_lock() -> asyncio.Lock:
    """获取或创建适配器锁（延迟初始化）"""
    global _adapter_lock
    if _adapter_lock is None:
        _adapter_lock = asyncio.Lock()
    return _adapter_lock


def get_onchain_adapter() -> OnChainMarketDataAdapter:
    """
    获取全局 On-Chain 适配器实例（同步版本）

    注意：此函数不是协程，在异步上下文中可能存在线程安全问题。
    推荐在异步上下文中使用 get_onchain_adapter_async() 以确保线程安全。

    Raises:
        RuntimeError: 如果全局适配器未初始化（需要先调用异步版本初始化）
    """
    global _global_adapter
    if _global_adapter is None:
        raise RuntimeError(
            "Global adapter not initialized. "
            "Use get_onchain_adapter_async() or start_onchain_service() first."
        )
    return _global_adapter


async def get_onchain_adapter_async() -> OnChainMarketDataAdapter:
    """异步安全地获取全局 On-Chain 适配器实例"""
    global _global_adapter
    if _global_adapter is None:
        async with _get_adapter_lock():
            # 双重检查锁定
            if _global_adapter is None:
                _global_adapter = OnChainMarketDataAdapter()
    return _global_adapter


async def start_onchain_service(symbols: Optional[List[str]] = None) -> OnChainMarketDataAdapter:
    """
    启动 On-Chain 数据服务

    Args:
        symbols: 要采集的 symbol 列表

    Returns:
        OnChainMarketDataAdapter 实例
    """
    adapter = await get_onchain_adapter_async()
    await adapter.start(symbols)
    return adapter


async def stop_onchain_service() -> None:
    """停止 On-Chain 数据服务"""
    global _global_adapter
    if _global_adapter:
        await _global_adapter.stop()


async def reset_onchain_adapter() -> None:
    """
    重置全局 On-Chain 适配器实例

    用于测试隔离或服务重启。会在重置前自动停止适配器。

    Usage:
        # In test teardown
        await reset_onchain_adapter()

        # Or in production for service restart
        await reset_onchain_adapter()
        adapter = await start_onchain_service()
    """
    global _global_adapter
    if _global_adapter is not None:
        if _global_adapter._running:
            await _global_adapter.stop()
        _global_adapter = None
