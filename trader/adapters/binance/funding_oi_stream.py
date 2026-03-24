"""
Funding/OI Stream Adapter - Binance Funding Rate & Open Interest Fetcher
=========================================================================
定期从 Binance 拉取 Funding Rate 和 Open Interest 数据并写入 Feature Store。

功能：
- REST 拉取 Funding Rate：GET /fapi/v1/fundingRate
- REST 拉取 Open Interest：GET /fapi/v1/openInterest
- 定时采集（默认8h funding周期前30min触发）
- 写入 Feature Store：feature_name=funding_rate|open_interest

特性：
- 降级保护：采集失败仅记录日志，不影响主交易流程
- 支持多 symbol 并行拉取
- 可配置的定时任务
"""
import asyncio
import logging
import time
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Dict, List, Optional, Any, Set, TYPE_CHECKING

if TYPE_CHECKING:
    import aiohttp

from trader.adapters.persistence.feature_store import FeatureStore, get_feature_store


logger = logging.getLogger(__name__)


# Binance Futures API base URL
BINANCE_FUTURES_BASE_URL = "https://fapi.binance.com"


@dataclass
class FundingOIConfig:
    """Funding/OI 采集配置"""
    base_url: str = BINANCE_FUTURES_BASE_URL
    # Funding Rate 采集间隔（秒），默认 30 分钟
    funding_poll_interval: float = 30.0 * 60
    # Open Interest 采集间隔（秒），默认 5 分钟
    oi_poll_interval: float = 5.0 * 60
    # 采集超时（秒）
    request_timeout: float = 10.0
    # 重试次数
    max_retries: int = 3
    # 重试间隔（秒）
    retry_delay: float = 1.0
    # Funding 周期（小时）
    funding_interval_hours: int = 8
    # Funding 前触发提前量（分钟）
    funding_pre_trigger_minutes: int = 30


@dataclass
class FundingRecord:
    """Funding Rate 记录"""
    symbol: str
    funding_rate: float
    exchange_ts_ms: int
    local_ts_ms: int
    next_funding_time_ms: int


@dataclass
class OIRecord:
    """Open Interest 记录"""
    symbol: str
    open_interest: float
    exchange_ts_ms: int
    local_ts_ms: int


class FundingOIAdapter:
    """
    Funding Rate & Open Interest 适配器

    负责从 Binance Futures API 拉取 Funding Rate 和 Open Interest 数据，
    并写入 Feature Store。
    """

    def __init__(
        self,
        config: Optional[FundingOIConfig] = None,
        feature_store: Optional[FeatureStore] = None,
    ):
        """
        初始化适配器

        Args:
            config: 采集配置
            feature_store: Feature Store 实例
        """
        self._config = config or FundingOIConfig()
        self._feature_store = feature_store or get_feature_store()
        self._session: Optional[aiohttp.ClientSession] = None

        # 活跃的 symbol 集合
        self._symbols: Set[str] = set()
        # 定时任务
        self._funding_task: Optional[asyncio.Task] = None
        self._oi_task: Optional[asyncio.Task] = None
        self._running = False

    async def _ensure_session(self) -> None:
        """确保 HTTP session 可用"""
        if self._session is None or self._session.closed:
            import aiohttp
            timeout = aiohttp.ClientTimeout(total=self._config.request_timeout)
            self._session = aiohttp.ClientSession(timeout=timeout)

    async def _close_session(self) -> None:
        """关闭 HTTP session"""
        if self._session and not self._session.closed:
            await self._session.close()
            self._session = None

    async def _fetch_funding_rate(self, symbol: str) -> Optional[FundingRecord]:
        """
        拉取单个 symbol 的 Funding Rate

        Args:
            symbol: 交易对，如 "BTCUSDT"

        Returns:
            FundingRecord 或 None
        """
        await self._ensure_session()

        url = f"{self._config.base_url}/fapi/v1/fundingRate"
        params = {"symbol": symbol}

        for attempt in range(self._config.max_retries):
            try:
                async with self._session.get(url, params=params) as resp:
                    if resp.status == 200:
                        data = await resp.json()
                        if data and len(data) > 0:
                            record = data[0]
                            now_ms = int(time.time() * 1000)
                            return FundingRecord(
                                symbol=symbol,
                                funding_rate=float(record["fundingRate"]),
                                exchange_ts_ms=record["fundingTime"],
                                local_ts_ms=now_ms,
                                next_funding_time_ms=record.get("nextFundingTime", 0),
                            )
                        logger.warning(f"[FundingOI] Empty funding rate response for {symbol}")
                        return None
                    else:
                        error_text = await resp.text()
                        logger.warning(
                            f"[FundingOI] Funding rate request failed for {symbol}: "
                            f"status={resp.status}, error={error_text}"
                        )
            except asyncio.TimeoutError:
                logger.warning(
                    f"[FundingOI] Funding rate request timeout for {symbol}, "
                    f"attempt {attempt + 1}/{self._config.max_retries}"
                )
            except Exception as e:
                logger.error(
                    f"[FundingOI] Funding rate request error for {symbol}: {e}, "
                    f"attempt {attempt + 1}/{self._config.max_retries}"
                )

            if attempt < self._config.max_retries - 1:
                await asyncio.sleep(self._config.retry_delay * (attempt + 1))

        return None

    async def _fetch_open_interest(self, symbol: str) -> Optional[OIRecord]:
        """
        拉取单个 symbol 的 Open Interest

        Args:
            symbol: 交易对，如 "BTCUSDT"

        Returns:
            OIRecord 或 None
        """
        await self._ensure_session()

        url = f"{self._config.base_url}/fapi/v1/openInterest"
        params = {"symbol": symbol}

        for attempt in range(self._config.max_retries):
            try:
                async with self._session.get(url, params=params) as resp:
                    if resp.status == 200:
                        data = await resp.json()
                        now_ms = int(time.time() * 1000)
                        return OIRecord(
                            symbol=symbol,
                            open_interest=float(data["openInterest"]),
                            exchange_ts_ms=data.get("updateTime", now_ms),
                            local_ts_ms=now_ms,
                        )
                    else:
                        error_text = await resp.text()
                        logger.warning(
                            f"[FundingOI] Open interest request failed for {symbol}: "
                            f"status={resp.status}, error={error_text}"
                        )
            except asyncio.TimeoutError:
                logger.warning(
                    f"[FundingOI] Open interest request timeout for {symbol}, "
                    f"attempt {attempt + 1}/{self._config.max_retries}"
                )
            except Exception as e:
                logger.error(
                    f"[FundingOI] Open interest request error for {symbol}: {e}, "
                    f"attempt {attempt + 1}/{self._config.max_retries}"
                )

            if attempt < self._config.max_retries - 1:
                await asyncio.sleep(self._config.retry_delay * (attempt + 1))

        return None

    async def _write_funding_to_store(self, record: FundingRecord) -> None:
        """
        将 Funding Rate 写入 Feature Store

        Args:
            record: Funding 记录
        """
        try:
            meta = {
                "next_funding_time_ms": record.next_funding_time_ms,
                "source": "binance_futures",
                "fetched_at": datetime.now(timezone.utc).isoformat() + "Z",
            }

            created, is_dup = await self._feature_store.write_feature(
                symbol=record.symbol,
                feature_name="funding_rate",
                version="v1",
                ts_ms=record.exchange_ts_ms,
                value={
                    "funding_rate": record.funding_rate,
                    "symbol": record.symbol,
                },
                meta=meta,
            )

            if created:
                logger.debug(
                    f"[FundingOI] Funding rate written for {record.symbol}: "
                    f"{record.funding_rate} at {record.exchange_ts_ms}"
                )
            elif is_dup:
                logger.debug(
                    f"[FundingOI] Funding rate duplicate for {record.symbol} at {record.exchange_ts_ms}"
                )

        except Exception as e:
            # 降级保护：写入失败仅记录日志
            logger.error(f"[FundingOI] Failed to write funding rate to store: {e}")

    async def _write_oi_to_store(self, record: OIRecord) -> None:
        """
        将 Open Interest 写入 Feature Store

        Args:
            record: OI 记录
        """
        try:
            meta = {
                "source": "binance_futures",
                "fetched_at": datetime.now(timezone.utc).isoformat() + "Z",
            }

            created, is_dup = await self._feature_store.write_feature(
                symbol=record.symbol,
                feature_name="open_interest",
                version="v1",
                ts_ms=record.exchange_ts_ms,
                value={
                    "open_interest": record.open_interest,
                    "symbol": record.symbol,
                },
                meta=meta,
            )

            if created:
                logger.debug(
                    f"[FundingOI] Open interest written for {record.symbol}: "
                    f"{record.open_interest} at {record.exchange_ts_ms}"
                )
            elif is_dup:
                logger.debug(
                    f"[FundingOI] Open interest duplicate for {record.symbol} at {record.exchange_ts_ms}"
                )

        except Exception as e:
            # 降级保护：写入失败仅记录日志
            logger.error(f"[FundingOI] Failed to write open interest to store: {e}")

    async def _fetch_all_funding_rates(self) -> None:
        """拉取所有 symbol 的 Funding Rate"""
        if not self._symbols:
            logger.debug("[FundingOI] No symbols configured for funding rate fetch")
            return

        logger.info(f"[FundingOI] Fetching funding rates for {len(self._symbols)} symbols")

        for symbol in list(self._symbols):
            try:
                record = await self._fetch_funding_rate(symbol)
                if record:
                    await self._write_funding_to_store(record)
            except Exception as e:
                # 降级保护：单个 symbol 失败不影响其他
                logger.error(f"[FundingOI] Failed to fetch funding rate for {symbol}: {e}")

    async def _fetch_all_open_interests(self) -> None:
        """拉取所有 symbol 的 Open Interest"""
        if not self._symbols:
            logger.debug("[FundingOI] No symbols configured for open interest fetch")
            return

        logger.info(f"[FundingOI] Fetching open interests for {len(self._symbols)} symbols")

        for symbol in list(self._symbols):
            try:
                record = await self._fetch_open_interest(symbol)
                if record:
                    await self._write_oi_to_store(record)
            except Exception as e:
                # 降级保护：单个 symbol 失败不影响其他
                logger.error(f"[FundingOI] Failed to fetch open interest for {symbol}: {e}")

    async def _funding_poll_loop(self) -> None:
        """Funding Rate 轮询循环"""
        while self._running:
            try:
                await self._fetch_all_funding_rates()
                await asyncio.sleep(self._config.funding_poll_interval)
            except asyncio.CancelledError:
                break
            except Exception as e:
                logger.error(f"[FundingOI] Funding poll loop error: {e}")
                # 发生错误后短暂等待再继续
                await asyncio.sleep(60)

    async def _oi_poll_loop(self) -> None:
        """Open Interest 轮询循环"""
        while self._running:
            try:
                await self._fetch_all_open_interests()
                await asyncio.sleep(self._config.oi_poll_interval)
            except asyncio.CancelledError:
                break
            except Exception as e:
                logger.error(f"[FundingOI] Open interest poll loop error: {e}")
                # 发生错误后短暂等待再继续
                await asyncio.sleep(60)

    async def start(self, symbols: Optional[List[str]] = None) -> None:
        """
        启动适配器

        Args:
            symbols: 要采集的 symbol 列表，默认 ["BTCUSDT"]
        """
        if self._running:
            logger.warning("[FundingOI] Adapter already running")
            return

        self._running = True

        # 默认 symbol
        if symbols:
            self._symbols = set(symbols)
        elif not self._symbols:
            self._symbols = {"BTCUSDT"}

        await self._ensure_session()

        # 立即执行一次采集
        try:
            await self._fetch_all_funding_rates()
            await self._fetch_all_open_interests()
        except Exception as e:
            logger.warning(f"[FundingOI] Initial fetch warning: {e}")

        # 启动定时任务
        self._funding_task = asyncio.create_task(self._funding_poll_loop())
        self._oi_task = asyncio.create_task(self._oi_poll_loop())

        logger.info(f"[FundingOI] Adapter started with symbols: {self._symbols}")

    async def stop(self) -> None:
        """停止适配器"""
        if not self._running:
            return

        self._running = False

        # 取消定时任务
        for task, name in [(self._funding_task, "funding"), (self._oi_task, "oi")]:
            if task:
                task.cancel()
                try:
                    await task
                except asyncio.CancelledError:
                    pass
                logger.debug(f"[FundingOI] {name} poll task cancelled")

        await self._close_session()

        logger.info("[FundingOI] Adapter stopped")

    def add_symbol(self, symbol: str) -> None:
        """添加采集 symbol"""
        self._symbols.add(symbol)
        logger.debug(f"[FundingOI] Symbol added: {symbol}, total: {len(self._symbols)}")

    def remove_symbol(self, symbol: str) -> None:
        """移除采集 symbol"""
        self._symbols.discard(symbol)
        logger.debug(f"[FundingOI] Symbol removed: {symbol}, remaining: {len(self._symbols)}")

    def is_running(self) -> bool:
        """检查是否正在运行"""
        return self._running

    @property
    def symbols(self) -> Set[str]:
        """获取当前采集的 symbol 集合"""
        return self._symbols.copy()


# 全局实例
_global_adapter: Optional[FundingOIAdapter] = None


def get_funding_oi_adapter() -> FundingOIAdapter:
    """获取全局 Funding/OI 适配器实例"""
    global _global_adapter
    if _global_adapter is None:
        _global_adapter = FundingOIAdapter()
    return _global_adapter


async def start_funding_oi_service(symbols: Optional[List[str]] = None) -> FundingOIAdapter:
    """
    启动 Funding/OI 服务

    Args:
        symbols: 要采集的 symbol 列表

    Returns:
        FundingOIAdapter 实例
    """
    adapter = get_funding_oi_adapter()
    await adapter.start(symbols)
    return adapter


async def stop_funding_oi_service() -> None:
    """停止 Funding/OI 服务"""
    global _global_adapter
    if _global_adapter:
        await _global_adapter.stop()
