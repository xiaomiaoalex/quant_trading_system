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
import logging
import random
import time
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Dict, List, Optional, Any, Set, TYPE_CHECKING

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
