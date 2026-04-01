"""
QuantConnect Lean Data Source Adapter - 回测数据供给适配器
============================================================

连接 QuantConnect Lean 引擎，获取历史 K 线数据和预计算特征。

功能：
- 从 QuantConnect Lean data feed 获取 OHLCV 数据
- 支持多时间周期 (1m, 5m, 15m, 1h, 4h, 1d)
- 支持 Binance 等加密货币交易所
- 内置数据缓存提升性能
- 完整的错误处理与数据验证

架构：
- 遵循 DataProviderPort 协议
- 支持回测/研究模式切换
- 缓存穿透保护

QuantConnect Lean 引擎：
    https://github.com/QuantConnect/Lean

数据流：
    Lean Data Feed -> Adapter -> Internal OHLCV Format -> Strategy
"""
from __future__ import annotations

import asyncio
import hashlib
import json
import logging
import time
from dataclasses import dataclass, field
from datetime import datetime, timezone
from decimal import Decimal
from enum import Enum
from typing import (
    Dict,
    List,
    Optional,
    Any,
    Sequence,
    Callable,
    Awaitable,
)

from trader.services.backtesting.ports import DataProviderPort, OHLCV

logger = logging.getLogger(__name__)


class TimeFrame(Enum):
    """支持的 K 线时间周期"""
    MINUTE_1 = "1m"
    MINUTE_5 = "5m"
    MINUTE_15 = "15m"
    HOUR_1 = "1h"
    HOUR_4 = "4h"
    DAY_1 = "1d"

    @classmethod
    def from_string(cls, value: str) -> TimeFrame:
        """从字符串解析时间周期"""
        normalized = value.lower().strip()
        for tf in cls:
            if tf.value == normalized:
                return tf
        raise ValueError(f"Unsupported timeframe: {value}. Supported: 1m, 5m, 15m, 1h, 4h, 1d")

    def to_lean_resolution(self) -> str:
        """转换为 Lean 引擎分辨率"""
        mapping = {
            "1m": "minute",
            "5m": "minute",
            "15m": "minute",
            "1h": "hour",
            "4h": "hour",
            "1d": "daily",
        }
        return mapping.get(self.value, "minute")

    def to_seconds(self) -> int:
        """转换为秒数"""
        mapping = {
            "1m": 60,
            "5m": 300,
            "15m": 900,
            "1h": 3600,
            "4h": 14400,
            "1d": 86400,
        }
        return mapping[self.value]


class Exchange(Enum):
    """支持的交易所"""
    BINANCE = "binance"
    COINBASE = "coinbase"
    KRAKEN = "kraken"
    FTX = "ftx"
    BYBIT = "bybit"


class DataSourceError(Exception):
    """数据源错误基类"""
    pass


class NetworkError(DataSourceError):
    """网络错误"""
    pass


class DataValidationError(DataSourceError):
    """数据验证错误"""
    pass


class MissingDataError(DataSourceError):
    """数据缺失错误"""
    pass


@dataclass(slots=True)
class CacheConfig:
    """缓存配置"""
    enabled: bool = True
    max_size_mb: int = 512
    ttl_seconds: int = 3600
    cache_dir: str = ".cache/lean_data"

    def __post_init__(self):
        if self.max_size_mb <= 0:
            object.__setattr__(self, 'max_size_mb', 512)
        if self.ttl_seconds <= 0:
            object.__setattr__(self, 'ttl_seconds', 3600)


@dataclass(slots=True)
class DataSourceConfig:
    """数据源配置"""
    exchange: Exchange = Exchange.BINANCE
    data_folder: str = "./Lean/Data"
    resolution: str = "1h"
    market: str = "crypto"
    map_symbols: bool = True
    allow_during_algorithm: bool = False
    increase_date_transactions: bool = False
    transaction_mode: str = "live"

    def __post_init__(self):
        if isinstance(self.exchange, str):
            object.__setattr__(self, 'exchange', Exchange(self.exchange.lower()))


@dataclass(slots=True)
class RetryConfig:
    """重试配置"""
    max_attempts: int = 3
    base_delay_seconds: float = 1.0
    max_delay_seconds: float = 30.0
    exponential_base: float = 2.0

    def __post_init__(self):
        if self.max_attempts <= 0:
            object.__setattr__(self, 'max_attempts', 3)
        if self.base_delay_seconds <= 0:
            object.__setattr__(self, 'base_delay_seconds', 1.0)


@dataclass(slots=True)
class LeanDataProviderConfig:
    """QuantConnect Lean 数据提供者配置"""
    data_source: DataSourceConfig = field(default_factory=DataSourceConfig)
    cache: CacheConfig = field(default_factory=CacheConfig)
    retry: RetryConfig = field(default_factory=RetryConfig)
    validate_data: bool = True
    strict_validation: bool = False
    default_symbol: str = "BTCUSDT"
    default_interval: str = "1h"


class LeanDataCache:
    """
    Lean 数据缓存

    职责：
    - 缓存 OHLCV 数据减少重复查询
    - 基于时间戳的 TTL 过期机制
    - 缓存键生成与验证
    """

    def __init__(self, config: CacheConfig):
        self._config = config
        self._cache: Dict[str, Dict[str, Any]] = {}
        self._lock = asyncio.Lock()

    def _make_cache_key(
        self,
        symbol: str,
        interval: str,
        start_date: datetime,
        end_date: datetime,
    ) -> str:
        """生成缓存键"""
        key_data = f"{symbol}:{interval}:{start_date.isoformat()}:{end_date.isoformat()}"
        return hashlib.sha256(key_data.encode()).hexdigest()

    def _is_expired(self, entry: Dict[str, Any]) -> bool:
        """检查缓存项是否过期"""
        if not self._config.enabled:
            return True
        age = time.time() - entry.get("cached_at", 0)
        return age > self._config.ttl_seconds

    async def get(
        self,
        symbol: str,
        interval: str,
        start_date: datetime,
        end_date: datetime,
    ) -> Optional[List[OHLCV]]:
        """获取缓存数据"""
        if not self._config.enabled:
            return None

        key = self._make_cache_key(symbol, interval, start_date, end_date)

        async with self._lock:
            entry = self._cache.get(key)
            if entry is None:
                return None

            if self._is_expired(entry):
                del self._cache[key]
                return None

            logger.debug(f"Cache hit for {symbol} {interval}: {len(entry['data'])} klines")
            return entry["data"]

    async def set(
        self,
        symbol: str,
        interval: str,
        start_date: datetime,
        end_date: datetime,
        data: List[OHLCV],
    ) -> None:
        """设置缓存数据"""
        if not self._config.enabled:
            return

        key = self._make_cache_key(symbol, interval, start_date, end_date)

        async with self._lock:
            self._cache[key] = {
                "data": data,
                "cached_at": time.time(),
                "symbol": symbol,
                "interval": interval,
            }

    async def clear(self) -> None:
        """清空缓存"""
        async with self._lock:
            self._cache.clear()

    async def remove_expired(self) -> int:
        """移除过期缓存项"""
        removed = 0
        async with self._lock:
            expired_keys = [
                k for k, v in self._cache.items()
                if self._is_expired(v)
            ]
            for key in expired_keys:
                del self._cache[key]
                removed += 1
        return removed


class QuantConnectLeanAdapter(DataProviderPort):
    """
    QuantConnect Lean 数据源适配器

    实现 DataProviderPort 协议，从 QuantConnect Lean 引擎获取历史数据。

    特性：
    - 多时间周期支持 (1m, 5m, 15m, 1h, 4h, 1d)
    - 多交易所支持 (Binance, Coinbase, Kraken, etc.)
    - 内置数据缓存
    - 自动重试与错误恢复
    - 数据验证

    使用示例：
        config = LeanDataProviderConfig(
            data_source=DataSourceConfig(exchange=Exchange.BINANCE),
            cache=CacheConfig(enabled=True, ttl_seconds=3600),
        )
        adapter = QuantConnectLeanAdapter(config)

        klines = await adapter.get_klines(
            symbol="BTCUSDT",
            interval="1h",
            start_date=datetime(2024, 1, 1),
            end_date=datetime(2024, 1, 31),
        )

        features = await adapter.get_features(
            symbol="BTCUSDT",
            feature_names=["ema_20", "volume_ratio"],
            start_date=datetime(2024, 1, 1),
            end_date=datetime(2024, 1, 31),
        )

        symbols = await adapter.get_symbols()
    """

    def __init__(
        self,
        config: Optional[LeanDataProviderConfig] = None,
        cache: Optional[LeanDataCache] = None,
    ):
        self._config = config or LeanDataProviderConfig()
        self._cache = cache or LeanDataCache(self._config.cache)
        self._lean_algorithm: Optional[Any] = None
        self._is_initialized = False
        self._symbols_cache: Optional[List[str]] = None
        self._symbols_cache_time: float = 0

    async def _ensure_initialized(self) -> None:
        """确保 Lean 引擎已初始化"""
        if self._is_initialized:
            return

        try:
            await self._initialize_lean()
            self._is_initialized = True
            logger.info("[QuantConnectLeanAdapter] Initialized successfully")
        except Exception as e:
            logger.error(f"[QuantConnectLeanAdapter] Failed to initialize: {e}")
            raise NetworkError(f"Failed to initialize Lean engine: {e}") from e

    async def _initialize_lean(self) -> None:
        """
        初始化 QuantConnect Lean 引擎

        实际实现中，这里会初始化 Lean Python API。
        由于 Lean 是 C# 引擎，此处提供抽象接口。
        """
        try:
            import lean
            self._lean_algorithm = lean.LocalPythonAlgorithm()
            logger.debug("[QuantConnectLeanAdapter] Lean engine loaded")
        except ImportError:
            logger.warning("[QuantConnectLeanAdapter] Lean engine not available, using mock mode")
            self._lean_algorithm = None

    def _validate_kline_data(self, kline: Dict[str, Any]) -> bool:
        """验证 K 线数据有效性"""
        required_fields = ["timestamp", "open", "high", "low", "close", "volume"]

        for field_name in required_fields:
            if field_name not in kline:
                if self._config.strict_validation:
                    raise DataValidationError(f"Missing required field: {field_name}")
                return False

        try:
            open_price = Decimal(str(kline["open"]))
            high_price = Decimal(str(kline["high"]))
            low_price = Decimal(str(kline["low"]))
            close_price = Decimal(str(kline["close"]))

            if not (low_price <= open_price <= high_price):
                if self._config.strict_validation:
                    raise DataValidationError(
                        f"Invalid OHLC relationship: low={low_price} <= open={open_price} <= high={high_price}"
                    )
                return False

            if not (low_price <= close_price <= high_price):
                if self._config.strict_validation:
                    raise DataValidationError(
                        f"Invalid OHLC relationship: low={low_price} <= close={close_price} <= high={high_price}"
                    )
                return False

            volume = Decimal(str(kline["volume"]))
            if volume < 0:
                if self._config.strict_validation:
                    raise DataValidationError(f"Negative volume: {volume}")
                return False

            return True

        except (ValueError, TypeError) as e:
            if self._config.strict_validation:
                raise DataValidationError(f"Invalid numeric value in kline: {e}") from e
            return False

    def _convert_to_ohlcv(self, lean_kline: Dict[str, Any]) -> OHLCV:
        """
        将 Lean K线数据转换为内部 OHLCV 格式

        Args:
            lean_kline: Lean 格式的 K 线数据

        Returns:
            OHLCV: 内部格式的 K 线数据
        """
        timestamp_raw = lean_kline.get("timestamp")
        if isinstance(timestamp_raw, (int, float)):
            timestamp = datetime.fromtimestamp(timestamp_raw, tz=timezone.utc)
        elif isinstance(timestamp_raw, str):
            timestamp = datetime.fromisoformat(timestamp_raw.replace("Z", "+00:00"))
        elif isinstance(timestamp_raw, datetime):
            timestamp = timestamp_raw
        else:
            raise DataValidationError(f"Invalid timestamp type: {type(timestamp_raw)}")

        return OHLCV(
            timestamp=timestamp,
            open=Decimal(str(lean_kline["open"])),
            high=Decimal(str(lean_kline["high"])),
            low=Decimal(str(lean_kline["low"])),
            close=Decimal(str(lean_kline["close"])),
            volume=Decimal(str(lean_kline["volume"])),
        )

    def _convert_from_lean_symbol(self, symbol: str) -> str:
        """
        将 Lean 格式的交易对转换为内部格式

        Args:
            symbol: Lean 格式交易对 (如 "BTCUSD")

        Returns:
            str: 内部格式交易对 (如 "BTCUSDT")
        """
        if symbol.endswith("USD") and self._config.data_source.exchange == Exchange.BINANCE:
            return symbol.replace("USD", "USDT")
        return symbol

    def _convert_to_lean_symbol(self, symbol: str) -> str:
        """
        将内部格式的交易对转换为 Lean 格式

        Args:
            symbol: 内部格式交易对 (如 "BTCUSDT")

        Returns:
            str: Lean 格式交易对 (如 "BTCUSD")
        """
        if symbol.endswith("USDT") and self._config.data_source.exchange == Exchange.BINANCE:
            return symbol.replace("USDT", "USD")
        return symbol

    async def _fetch_from_lean(
        self,
        symbol: str,
        interval: str,
        start_date: datetime,
        end_date: datetime,
    ) -> List[OHLCV]:
        """
        从 Lean 引擎获取数据

        实现数据获取逻辑，支持多种数据源。
        """
        lean_symbol = self._convert_to_lean_symbol(symbol)
        timeframe = TimeFrame.from_string(interval)

        if self._lean_algorithm is not None:
            try:
                data = self._lean_algorithm.get_historical_data(
                    lean_symbol,
                    timeframe.to_lean_resolution(),
                    start_date,
                    end_date,
                )
                return [self._convert_to_ohlcv(k) for k in data]
            except Exception as e:
                logger.warning(f"[QuantConnectLeanAdapter] Lean fetch failed: {e}")

        return await self._fetch_from_file(
            lean_symbol,
            timeframe,
            start_date,
            end_date,
        )

    async def _fetch_from_file(
        self,
        symbol: str,
        timeframe: TimeFrame,
        start_date: datetime,
        end_date: datetime,
    ) -> List[OHLCV]:
        """
        从文件系统获取数据 (Lean Data Folder)

        适用于本地回测场景，数据存储在 Lean 标准化目录结构中。
        目录结构: Data/{exchange}/{market}/{resolution}/{symbol}.csv
        """
        data_folder = self._config.data_source.data_folder
        exchange_name = self._config.data_source.exchange.value
        market = self._config.data_source.market

        import os
        file_path = os.path.join(
            data_folder,
            exchange_name,
            market,
            timeframe.value,
            f"{symbol}.csv",
        )

        if not os.path.exists(file_path):
            raise MissingDataError(f"Data file not found: {file_path}")

        klines: List[OHLCV] = []

        try:
            import csv
            with open(file_path, "r", encoding="utf-8") as f:
                reader = csv.DictReader(f)
                for row in reader:
                    try:
                        timestamp = datetime.fromisoformat(row["timestamp"].replace("Z", "+00:00"))
                    except (ValueError, KeyError):
                        try:
                            timestamp = datetime.fromtimestamp(
                                int(row.get("time", row.get("date", 0))),
                                tz=timezone.utc,
                            )
                        except (ValueError, TypeError):
                            if self._config.validate_data:
                                logger.warning(f"[QuantConnectLeanAdapter] Invalid timestamp in row: {row}")
                                continue
                            continue

                    if timestamp < start_date or timestamp > end_date:
                        continue

                    lean_kline = {
                        "timestamp": timestamp,
                        "open": row.get("open", row.get("o", 0)),
                        "high": row.get("high", row.get("h", 0)),
                        "low": row.get("low", row.get("l", 0)),
                        "close": row.get("close", row.get("c", 0)),
                        "volume": row.get("volume", row.get("v", 0)),
                    }

                    if self._config.validate_data and not self._validate_kline_data(lean_kline):
                        continue

                    klines.append(self._convert_to_ohlcv(lean_kline))

        except FileNotFoundError as e:
            raise MissingDataError(f"Data file not found: {file_path}") from e
        except PermissionError as e:
            raise DataSourceError(f"Permission denied reading: {file_path}") from e
        except Exception as e:
            raise DataSourceError(f"Error reading data file: {e}") from e

        klines.sort(key=lambda x: x.timestamp)
        return klines

    async def _fetch_from_api(
        self,
        symbol: str,
        interval: str,
        start_date: datetime,
        end_date: datetime,
    ) -> List[OHLCV]:
        """
        从交易所 API 获取数据 (研究/实时模式)

        用于非回测场景，直接从交易所获取数据。
        """
        import aiohttp

        timeframe = TimeFrame.from_string(interval)
        exchange = self._config.data_source.exchange

        base_urls = {
            Exchange.BINANCE: "https://api.binance.com/api/v3/klines",
            Exchange.COINBASE: "https://api.exchange.coinbase.com/products",
            Exchange.KRAKEN: "https://api.kraken.com/0/public/OHLC",
            Exchange.BYBIT: "https://api.bybit.com/v5/market/kline",
        }

        base_url = base_urls.get(exchange)
        if not base_url:
            raise DataSourceError(f"Unsupported exchange for API fetch: {exchange}")

        params: Dict[str, Any] = {
            "symbol": symbol.upper(),
            "interval": timeframe.value,
            "startTime": int(start_date.timestamp() * 1000),
            "endTime": int(end_date.timestamp() * 1000),
        }

        if exchange == Exchange.BINANCE:
            params["limit"] = 1000
        elif exchange == Exchange.COINBASE:
            params["granularity"] = timeframe.to_seconds()
        elif exchange == Exchange.KRAKEN:
            params["pair"] = symbol.upper()
            params["interval"] = timeframe.value
        elif exchange == Exchange.BYBIT:
            params["category"] = "spot"

        retry_config = self._config.retry
        last_error: Optional[Exception] = None

        for attempt in range(retry_config.max_attempts):
            try:
                async with aiohttp.ClientSession() as session:
                    async with session.get(base_url, params=params, timeout=aiohttp.ClientTimeout(total=30)) as response:
                        if response.status == 429:
                            retry_delay = min(
                                retry_config.base_delay_seconds * (retry_config.exponential_base ** attempt),
                                retry_config.max_delay_seconds,
                            )
                            logger.warning(f"[QuantConnectLeanAdapter] Rate limited, retrying in {retry_delay}s")
                            await asyncio.sleep(retry_delay)
                            continue

                        if response.status != 200:
                            text = await response.text()
                            raise NetworkError(f"API returned status {response.status}: {text}")

                        data = await response.json()
                        return self._parse_api_response(data, exchange)

            except aiohttp.ClientError as e:
                last_error = e
                retry_delay = min(
                    retry_config.base_delay_seconds * (retry_config.exponential_base ** attempt),
                    retry_config.max_delay_seconds,
                )
                logger.warning(f"[QuantConnectLeanAdapter] Network error (attempt {attempt + 1}): {e}")
                if attempt < retry_config.max_attempts - 1:
                    await asyncio.sleep(retry_delay)
            except asyncio.TimeoutError as e:
                last_error = e
                retry_delay = min(
                    retry_config.base_delay_seconds * (retry_config.exponential_base ** attempt),
                    retry_config.max_delay_seconds,
                )
                logger.warning(f"[QuantConnectLeanAdapter] Request timeout (attempt {attempt + 1})")
                if attempt < retry_config.max_attempts - 1:
                    await asyncio.sleep(retry_delay)

        raise NetworkError(f"Failed to fetch data after {retry_config.max_attempts} attempts: {last_error}")

    def _parse_api_response(
        self,
        data: Any,
        exchange: Exchange,
    ) -> List[OHLCV]:
        """
        解析交易所 API 响应

        Args:
            data: API 响应数据
            exchange: 交易所类型

        Returns:
            List[OHLCV]: 解析后的 K 线数据
        """
        klines: List[OHLCV] = []

        if exchange == Exchange.BINANCE:
            for item in data:
                if len(item) < 6:
                    continue
                try:
                    klines.append(OHLCV(
                        timestamp=datetime.fromtimestamp(item[0] / 1000, tz=timezone.utc),
                        open=Decimal(str(item[1])),
                        high=Decimal(str(item[2])),
                        low=Decimal(str(item[3])),
                        close=Decimal(str(item[4])),
                        volume=Decimal(str(item[5])),
                    ))
                except (ValueError, TypeError, IndexError) as e:
                    logger.warning(f"[QuantConnectLeanAdapter] Failed to parse Binance kline: {e}")
                    continue

        elif exchange == Exchange.COINBASE:
            for item in data:
                if len(item) < 6:
                    continue
                try:
                    klines.append(OHLCV(
                        timestamp=datetime.fromisoformat(item[0].replace("Z", "+00:00")),
                        open=Decimal(str(item[3])),
                        high=Decimal(str(item[4])),
                        low=Decimal(str(item[5])),
                        close=Decimal(str(item[6])),
                        volume=Decimal(str(item[7])),
                    ))
                except (ValueError, TypeError, IndexError) as e:
                    logger.warning(f"[QuantConnectLeanAdapter] Failed to parse Coinbase kline: {e}")
                    continue

        elif exchange == Exchange.KRAKEN:
            if isinstance(data, dict) and "result" in data:
                for pair_data in data["result"].values():
                    if not isinstance(pair_data, list):
                        continue
                    for item in pair_data:
                        if len(item) < 6:
                            continue
                        try:
                            klines.append(OHLCV(
                                timestamp=datetime.fromtimestamp(item[0], tz=timezone.utc),
                                open=Decimal(str(item[4])),
                                high=Decimal(str(item[5])),
                                low=Decimal(str(item[6])),
                                close=Decimal(str(item[7])),
                                volume=Decimal(str(item[8])),
                            ))
                        except (ValueError, TypeError, IndexError) as e:
                            logger.warning(f"[QuantConnectLeanAdapter] Failed to parse Kraken kline: {e}")
                            continue

        elif exchange == Exchange.BYBIT:
            if isinstance(data, dict) and "result" in data:
                items = data["result"].get("list", [])
                for item in items:
                    if len(item) < 6:
                        continue
                    try:
                        klines.append(OHLCV(
                            timestamp=datetime.fromtimestamp(int(item["t"]) / 1000, tz=timezone.utc),
                            open=Decimal(str(item["o"])),
                            high=Decimal(str(item["h"])),
                            low=Decimal(str(item["l"])),
                            close=Decimal(str(item["c"])),
                            volume=Decimal(str(item["v"])),
                        ))
                    except (ValueError, TypeError, IndexError) as e:
                        logger.warning(f"[QuantConnectLeanAdapter] Failed to parse Bybit kline: {e}")
                        continue

        klines.sort(key=lambda x: x.timestamp)
        return klines

    async def get_klines(
        self,
        symbol: str,
        interval: str,
        start_date: datetime,
        end_date: datetime,
    ) -> List[OHLCV]:
        """
        获取 OHLCV K 线数据

        实现 DataProviderPort 协议。

        Args:
            symbol: 交易标的 (如 BTCUSDT)
            interval: K 线周期 (1m, 5m, 15m, 1h, 4h, 1d)
            start_date: 开始日期
            end_date: 结束日期

        Returns:
            List[OHLCV]: K 线数据列表，按时间升序

        Raises:
            NetworkError: 网络请求失败
            DataValidationError: 数据验证失败
            MissingDataError: 数据不存在
        """
        await self._ensure_initialized()

        cached = await self._cache.get(symbol, interval, start_date, end_date)
        if cached is not None:
            return cached

        try:
            TimeFrame.from_string(interval)
        except ValueError as e:
            raise DataValidationError(f"Invalid interval: {e}") from e

        if start_date >= end_date:
            raise DataValidationError(f"start_date ({start_date}) must be before end_date ({end_date})")

        symbol = symbol.upper().strip()

        try:
            if self._config.data_source.transaction_mode == "live":
                klines = await self._fetch_from_api(symbol, interval, start_date, end_date)
            else:
                klines = await self._fetch_from_file(
                    self._convert_to_lean_symbol(symbol),
                    TimeFrame.from_string(interval),
                    start_date,
                    end_date,
                )
        except MissingDataError:
            if self._config.data_source.transaction_mode == "live":
                raise
            klines = await self._fetch_from_api(symbol, interval, start_date, end_date)
        except NetworkError:
            if self._config.data_source.transaction_mode != "live":
                klines = await self._fetch_from_file(
                    self._convert_to_lean_symbol(symbol),
                    TimeFrame.from_string(interval),
                    start_date,
                    end_date,
                )
            else:
                raise

        if not klines:
            raise MissingDataError(
                f"No data found for {symbol} {interval} from {start_date} to {end_date}"
            )

        filtered_klines = [
            k for k in klines
            if start_date <= k.timestamp <= end_date
        ]

        await self._cache.set(symbol, interval, start_date, end_date, filtered_klines)

        return filtered_klines

    async def get_features(
        self,
        symbol: str,
        feature_names: List[str],
        start_date: datetime,
        end_date: datetime,
    ) -> Dict[str, List[Any]]:
        """
        获取预计算特征

        实现 DataProviderPort 协议。

        Args:
            symbol: 交易标的
            feature_names: 特征名称列表
            start_date: 开始日期
            end_date: 结束日期

        Returns:
            Dict[str, List[Any]]: 特征名到特征值列表的映射

        Raises:
            MissingDataError: 特征数据不存在
        """
        await self._ensure_initialized()

        symbol = symbol.upper().strip()
        result: Dict[str, List[Any]] = {name: [] for name in feature_names}

        for feature_name in feature_names:
            feature_data = await self._fetch_feature(
                symbol,
                feature_name,
                start_date,
                end_date,
            )
            result[feature_name] = feature_data

        return result

    async def _fetch_feature(
        self,
        symbol: str,
        feature_name: str,
        start_date: datetime,
        end_date: datetime,
    ) -> List[Any]:
        """
        获取单个预计算特征

        Args:
            symbol: 交易标的
            feature_name: 特征名称
            start_date: 开始日期
            end_date: 结束日期

        Returns:
            List[Any]: 特征值列表
        """
        from trader.adapters.persistence.feature_store import get_feature_store

        feature_store = get_feature_store()
        start_ts = int(start_date.timestamp() * 1000)
        end_ts = int(end_date.timestamp() * 1000)

        feature_points = await feature_store.read_feature_range(
            symbol=symbol,
            feature_name=feature_name,
            start_time=start_ts,
            end_time=end_ts,
        )

        return [point.value for point in feature_points]

    async def get_symbols(self) -> List[str]:
        """
        获取可用交易标的列表

        实现 DataProviderPort 协议。

        Returns:
            List[str]: 可用标的列表

        Raises:
            NetworkError: 获取标的列表失败
        """
        await self._ensure_initialized()

        current_time = time.time()
        if (
            self._symbols_cache is not None
            and current_time - self._symbols_cache_time < 300
        ):
            return self._symbols_cache

        symbols = await self._fetch_available_symbols()
        self._symbols_cache = symbols
        self._symbols_cache_time = current_time

        return symbols

    async def _fetch_available_symbols(self) -> List[str]:
        """
        获取可用交易标的

        Returns:
            List[str]: 可用交易标的列表
        """
        import aiohttp

        exchange = self._config.data_source.exchange
        base_urls = {
            Exchange.BINANCE: "https://api.binance.com/api/v3/exchangeInfo",
            Exchange.COINBASE: "https://api.exchange.coinbase.com/products",
            Exchange.BYBIT: "https://api.bybit.com/v5/market/instruments-info",
        }

        base_url = base_urls.get(exchange)
        if not base_url:
            return self._get_default_symbols()

        retry_config = self._config.retry
        last_error: Optional[Exception] = None

        for attempt in range(retry_config.max_attempts):
            try:
                async with aiohttp.ClientSession() as session:
                    async with session.get(base_url, timeout=aiohttp.ClientTimeout(total=30)) as response:
                        if response.status == 429:
                            retry_delay = min(
                                retry_config.base_delay_seconds * (retry_config.exponential_base ** attempt),
                                retry_config.max_delay_seconds,
                            )
                            await asyncio.sleep(retry_delay)
                            continue

                        if response.status != 200:
                            raise NetworkError(f"API returned status {response.status}")

                        data = await response.json()
                        return self._parse_symbols_response(data, exchange)

            except aiohttp.ClientError as e:
                last_error = e
                retry_delay = min(
                    retry_config.base_delay_seconds * (retry_config.exponential_base ** attempt),
                    retry_config.max_delay_seconds,
                )
                if attempt < retry_config.max_attempts - 1:
                    await asyncio.sleep(retry_delay)
            except asyncio.TimeoutError as e:
                last_error = e
                retry_delay = min(
                    retry_config.base_delay_seconds * (retry_config.exponential_base ** attempt),
                    retry_config.max_delay_seconds,
                )
                if attempt < retry_config.max_attempts - 1:
                    await asyncio.sleep(retry_delay)

        logger.warning(f"[QuantConnectLeanAdapter] Failed to fetch symbols: {last_error}")
        return self._get_default_symbols()

    def _parse_symbols_response(
        self,
        data: Any,
        exchange: Exchange,
    ) -> List[str]:
        """解析交易所符号响应"""
        symbols: List[str] = []

        if exchange == Exchange.BINANCE:
            if isinstance(data, dict):
                for symbol_info in data.get("symbols", []):
                    if symbol_info.get("status") == "TRADING":
                        base = symbol_info.get("baseAsset", "")
                        quote = symbol_info.get("quoteAsset", "")
                        if base and quote:
                            symbols.append(f"{base}{quote}")

        elif exchange == Exchange.COINBASE:
            if isinstance(data, list):
                for product in data:
                    if product.get("status") == "online":
                        symbol = product.get("product_id", "")
                        if symbol:
                            symbols.append(symbol.replace("-", ""))

        elif exchange == Exchange.BYBIT:
            if isinstance(data, dict):
                for item in data.get("result", {}).get("list", []):
                    if item.get("status") == "Trading":
                        symbol = item.get("symbol", "")
                        if symbol:
                            symbols.append(symbol)

        return sorted(symbols)

    def _get_default_symbols(self) -> List[str]:
        """获取默认交易标的列表"""
        return [
            "BTCUSDT",
            "ETHUSDT",
            "BNBUSDT",
            "ADAUSDT",
            "DOGEUSDT",
            "XRPUSDT",
            "DOTUSDT",
            "MATICUSDT",
            "LTCUSDT",
            "SOLUSDT",
        ]

    async def health_check(self) -> bool:
        """
        执行健康检查

        Returns:
            bool: 健康状态
        """
        try:
            await self._ensure_initialized()
            return True
        except Exception as e:
            logger.error(f"[QuantConnectLeanAdapter] Health check failed: {e}")
            return False

    def get_config(self) -> LeanDataProviderConfig:
        """获取配置"""
        return self._config

    async def clear_cache(self) -> None:
        """清空缓存"""
        await self._cache.clear()

    async def close(self) -> None:
        """关闭适配器"""
        await self.clear_cache()
        self._lean_algorithm = None
        self._is_initialized = False
        logger.info("[QuantConnectLeanAdapter] Closed")
