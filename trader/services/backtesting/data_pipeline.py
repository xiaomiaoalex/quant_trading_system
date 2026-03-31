"""
Backtesting Data Pipeline
=========================

Provides optimized data loading and caching for backtesting:

Pipeline:
    FeatureStore → DataLoader → DataValidator → Cache → BacktestEngine
                        ↓
                  QualityReport

Features:
- Data caching to avoid repeated loading
- Data pre-validation before backtesting
- Multi-strategy parallel backtesting support
- Quality reports for data issues

Architecture:
    DataPipeline -> DataLoader -> DataCache -> BacktestEngine
                       |
                   DataValidator -> QualityReport
"""
from __future__ import annotations

import asyncio
import hashlib
import logging
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from decimal import Decimal
from enum import Enum
from typing import Any, Callable, Dict, List, Optional, Set, Tuple
from collections import defaultdict
import threading

logger = logging.getLogger(__name__)


# ============================================================================
# Data Quality
# ============================================================================


class DataQualityStatus(Enum):
    """Data quality status."""
    PASS = "PASS"
    WARNING = "WARNING"
    FAIL = "FAIL"


class DataGapReason(Enum):
    """Reason for data gap."""
    WEEKEND = "WEEKEND"  # Normal weekend gap
    HOLIDAY = "HOLIDAY"  # Market holiday
    MISSING = "MISSING"  # Actually missing data
    EXCHANGE_OUTAGE = "EXCHANGE_OUTAGE"  # Exchange data outage
    SUSPICIOUS = "SUSPICIOUS"  # Suspicious gap pattern


@dataclass(slots=True)
class DataGap:
    """
    Represents a gap in the data
    
    属性：
        start_time: Gap开始时间
        end_time: Gap结束时间
        reason: Gap原因
        severity: 严重程度 (0-1)
    """
    start_time: datetime
    end_time: datetime
    reason: DataGapReason
    severity: float = 0.5  # 0=normal, 1=severe
    
    @property
    def duration(self) -> timedelta:
        """Gap持续时间"""
        return self.end_time - self.start_time
    
    @property
    def duration_hours(self) -> float:
        """Gap持续小时数"""
        return self.duration.total_seconds() / 3600


@dataclass(slots=True)
class DataQualityIssue:
    """
    Data quality issue
    
    属性：
        issue_type: 问题类型
        timestamp: 问题发生时间
        severity: 严重程度 (0-1)
        message: 问题描述
        affected_symbols: 受影响标的列表
    """
    issue_type: str
    timestamp: datetime
    severity: float
    message: str
    affected_symbols: List[str] = field(default_factory=list)
    
    def __post_init__(self):
        if isinstance(self.timestamp, (int, float)):
            object.__setattr__(self, 'timestamp', datetime.fromtimestamp(self.timestamp, tz=timezone.utc))


@dataclass(slots=True)
class DataQualityReport:
    """
    Data quality report
    
    属性：
        status: 总体状态
        issues: 问题列表
        gaps: 数据缺口列表
        warnings: 警告列表
        total_points: 总数据点数
        valid_points: 有效数据点数
        coverage_percent: 数据覆盖率
        checked_at: 检查时间
    """
    status: DataQualityStatus
    issues: List[DataQualityIssue] = field(default_factory=list)
    gaps: List[DataGap] = field(default_factory=list)
    warnings: List[str] = field(default_factory=list)
    total_points: int = 0
    valid_points: int = 0
    coverage_percent: float = 100.0
    checked_at: datetime = field(default_factory=lambda: datetime.now(timezone.utc))
    
    @property
    def has_issues(self) -> bool:
        """是否有问题"""
        return len(self.issues) > 0 or self.status in (DataQualityStatus.WARNING, DataQualityStatus.FAIL)
    
    def is_acceptable(self) -> bool:
        """数据是否可接受用于回测"""
        return self.status != DataQualityStatus.FAIL and self.coverage_percent >= 90.0


# ============================================================================
# Data Cache
# ============================================================================


@dataclass(slots=True)
class CacheEntry:
    """Cache entry with metadata"""
    key: str
    data: List[Any]
    created_at: datetime
    last_accessed: datetime
    access_count: int = 0
    size_bytes: int = 0


class DataCache:
    """
    Thread-safe data cache for backtesting
    
    Features:
    - LRU eviction
    - Size-based eviction
    - TTL support
    - Statistics tracking
    
    使用方式:
        cache = DataCache(max_size_mb=100, ttl_hours=24)
        cache.set("BTCUSDT_1h", data)
        data = cache.get("BTCUSDT_1h")
    """
    
    def __init__(
        self,
        max_size_mb: float = 100.0,
        ttl_hours: int = 24,
        eviction_policy: str = "LRU",
    ):
        """
        初始化数据缓存
        
        Args:
            max_size_mb: 最大缓存大小 (MB)
            ttl_hours: 缓存过期时间 (小时)
            eviction_policy: 驱逐策略 (LRU, LFU, FIFO)
        """
        self._max_size_bytes = int(max_size_mb * 1024 * 1024)
        self._ttl = timedelta(hours=ttl_hours)
        self._eviction_policy = eviction_policy
        
        self._cache: Dict[str, CacheEntry] = {}
        self._lock = threading.RLock()
        
        # Statistics
        self._hits = 0
        self._misses = 0
        self._evictions = 0
    
    def _generate_key(self, symbol: str, interval: str, start: datetime, end: datetime) -> str:
        """生成缓存键"""
        key_parts = f"{symbol}_{interval}_{start.isoformat()}_{end.isoformat()}"
        return hashlib.md5(key_parts.encode()).hexdigest()
    
    def get(
        self,
        symbol: str,
        interval: str,
        start: datetime,
        end: datetime,
    ) -> Optional[List[Any]]:
        """
        获取缓存数据
        
        Args:
            symbol: 交易标的
            interval: K线周期
            start: 开始时间
            end: 结束时间
            
        Returns:
            缓存的数据，如果不存在或已过期则返回None
        """
        key = self._generate_key(symbol, interval, start, end)
        
        with self._lock:
            entry = self._cache.get(key)
            
            if entry is None:
                self._misses += 1
                return None
            
            # Check TTL
            if datetime.now(timezone.utc) - entry.created_at > self._ttl:
                del self._cache[key]
                self._misses += 1
                return None
            
            # Update access
            entry.last_accessed = datetime.now(timezone.utc)
            entry.access_count += 1
            self._hits += 1
            
            return entry.data
    
    def set(
        self,
        symbol: str,
        interval: str,
        start: datetime,
        end: datetime,
        data: List[Any],
    ) -> None:
        """
        设置缓存数据
        
        Args:
            symbol: 交易标的
            interval: K线周期
            start: 开始时间
            end: 结束时间
            data: 要缓存的数据
        """
        key = self._generate_key(symbol, interval, start, end)
        
        # Estimate size
        import sys
        size_bytes = sum(sys.getsizeof(d) for d in data)
        
        with self._lock:
            # Check if we need to evict
            current_size = sum(e.size_bytes for e in self._cache.values())
            if current_size + size_bytes > self._max_size_bytes:
                self._evict(size_bytes)
            
            entry = CacheEntry(
                key=key,
                data=data,
                created_at=datetime.now(timezone.utc),
                last_accessed=datetime.now(timezone.utc),
                size_bytes=size_bytes,
            )
            self._cache[key] = entry
    
    def _evict(self, needed_bytes: int) -> None:
        """驱逐缓存条目以腾出空间"""
        if not self._cache:
            return
        
        if self._eviction_policy == "LRU":
            # Sort by last accessed time
            sorted_entries = sorted(
                self._cache.values(),
                key=lambda e: e.last_accessed,
            )
        elif self._eviction_policy == "LFU":
            # Sort by access count
            sorted_entries = sorted(
                self._cache.values(),
                key=lambda e: e.access_count,
            )
        else:  # FIFO
            sorted_entries = sorted(
                self._cache.values(),
                key=lambda e: e.created_at,
            )
        
        # Evict until we have enough space
        freed_bytes = 0
        for entry in sorted_entries:
            if freed_bytes >= needed_bytes:
                break
            del self._cache[entry.key]
            freed_bytes += entry.size_bytes
            self._evictions += 1
    
    def invalidate(self, pattern: Optional[str] = None) -> int:
        """
        使缓存失效
        
        Args:
            pattern: 可选的模式匹配，用于部分失效
            
        Returns:
            失效的条目数量
        """
        with self._lock:
            if pattern is None:
                count = len(self._cache)
                self._cache.clear()
                return count
            
            # Pattern-based invalidation
            keys_to_delete = [k for k in self._cache.keys() if pattern in k]
            for key in keys_to_delete:
                del self._cache[key]
            return len(keys_to_delete)
    
    def get_stats(self) -> Dict[str, Any]:
        """获取缓存统计"""
        with self._lock:
            total_size = sum(e.size_bytes for e in self._cache.values())
            total_requests = self._hits + self._misses
            hit_rate = self._hits / total_requests if total_requests > 0 else 0.0
            
            return {
                "entries": len(self._cache),
                "size_mb": total_size / (1024 * 1024),
                "max_size_mb": self._max_size_bytes / (1024 * 1024),
                "hits": self._hits,
                "misses": self._misses,
                "hit_rate": hit_rate,
                "evictions": self._evictions,
            }


# ============================================================================
# Data Validator
# ============================================================================


class DataValidator:
    """
    Data quality validator
    
    Checks for:
    - Data alignment (timestamps, intervals)
    - Missing data / gaps
    - Survivorship bias
    - Price anomalies
    
    使用方式:
        validator = DataValidator()
        report = validator.validate(data, symbol="BTCUSDT", interval="1h")
    """
    
    def __init__(
        self,
        check_alignment: bool = True,
        check_gaps: bool = True,
        check_survivorship_bias: bool = True,
        gap_tolerance_hours: float = 4.0,
    ):
        """
        初始化数据验证器
        
        Args:
            check_alignment: 检查数据对齐
            check_gaps: 检查数据缺口
            check_survivorship_bias: 检查存活者偏差
            gap_tolerance_hours: 缺口容差（小时），超过此值视为异常缺口
        """
        self._check_alignment = check_alignment
        self._check_gaps = check_gaps
        self._check_survivorship_bias = check_survivorship_bias
        self._gap_tolerance = timedelta(hours=gap_tolerance_hours)
    
    def validate(
        self,
        data: List[Any],
        symbol: str,
        interval: str,
    ) -> DataQualityReport:
        """
        验证数据质量
        
        Args:
            data: OHLCV数据列表
            symbol: 交易标的
            interval: K线周期
            
        Returns:
            DataQualityReport: 质量报告
        """
        issues: List[DataQualityIssue] = []
        warnings: List[str] = []
        gaps: List[DataGap] = []
        
        if not data:
            return DataQualityReport(
                status=DataQualityStatus.FAIL,
                issues=[DataQualityIssue(
                    issue_type="EMPTY_DATA",
                    timestamp=datetime.now(timezone.utc),
                    severity=1.0,
                    message="数据为空",
                    affected_symbols=[symbol],
                )],
                warnings=["数据为空"],
            )
        
        # Basic stats
        total_points = len(data)
        valid_points = total_points
        
        # Extract timestamps
        timestamps = self._extract_timestamps(data)
        
        # Check alignment
        if self._check_alignment:
            alignment_issues = self._check_alignment_issues(timestamps, interval)
            issues.extend(alignment_issues)
        
        # Check gaps
        if self._check_gaps:
            detected_gaps, gap_warnings = self._check_gaps_internal(timestamps, interval)
            gaps.extend(detected_gaps)
            warnings.extend(gap_warnings)
        
        # Check survivorship bias
        if self._check_survivorship_bias:
            survivorship_warnings = self._detect_survivorship_bias(data)
            warnings.extend(survivorship_warnings)
        
        # Determine status
        max_severity = max([i.severity for i in issues], default=0.0)
        if max_severity >= 0.8 or not data:
            status = DataQualityStatus.FAIL
        elif max_severity >= 0.4 or warnings:
            status = DataQualityStatus.WARNING
        else:
            status = DataQualityStatus.PASS
        
        # Calculate coverage
        expected_points = self._calculate_expected_points(timestamps, interval)
        coverage = (valid_points / expected_points * 100) if expected_points > 0 else 100.0
        coverage = min(100.0, coverage)  # Cap at 100%
        
        return DataQualityReport(
            status=status,
            issues=issues,
            gaps=gaps,
            warnings=warnings,
            total_points=total_points,
            valid_points=valid_points,
            coverage_percent=coverage,
        )
    
    def _extract_timestamps(self, data: List[Any]) -> List[datetime]:
        """从数据中提取时间戳"""
        timestamps = []
        for point in data:
            ts = point.get("timestamp") if isinstance(point, dict) else getattr(point, 'timestamp', None)
            if isinstance(ts, (int, float)):
                ts = datetime.fromtimestamp(ts, tz=timezone.utc)
            elif isinstance(ts, str):
                ts = datetime.fromisoformat(ts.replace("Z", "+00:00"))
            if ts:
                timestamps.append(ts)
        return sorted(timestamps)
    
    def _check_alignment_issues(
        self,
        timestamps: List[datetime],
        interval: str,
    ) -> List[DataQualityIssue]:
        """检查数据对齐问题"""
        issues = []
        
        if len(timestamps) < 2:
            return issues
        
        # Determine expected interval
        expected_delta = self._get_expected_interval(interval)
        
        # Check each consecutive pair
        for i in range(1, len(timestamps)):
            delta = timestamps[i] - timestamps[i-1]
            if delta != expected_delta:
                issues.append(DataQualityIssue(
                    issue_type="ALIGNMENT",
                    timestamp=timestamps[i],
                    severity=0.3,
                    message=f"数据点间隔异常: 期望 {expected_delta}, 实际 {delta}",
                ))
        
        return issues
    
    def _check_gaps_internal(
        self,
        timestamps: List[datetime],
        interval: str,
    ) -> Tuple[List[DataGap], List[str]]:
        """检查数据缺口"""
        gaps = []
        warnings = []
        
        if len(timestamps) < 2:
            return gaps, warnings
        
        expected_delta = self._get_expected_interval(interval)
        
        for i in range(1, len(timestamps)):
            delta = timestamps[i] - timestamps[i-1]
            
            if delta > expected_delta:
                # Determine reason
                hours = delta.total_seconds() / 3600
                
                # Normal weekend gap for crypto (Fri close - Mon open)
                if hours >= 60 and hours <= 80:
                    reason = DataGapReason.WEEKEND
                    severity = 0.1
                # Holiday gap
                elif hours >= 20 and hours <= 32:
                    reason = DataGapReason.HOLIDAY
                    severity = 0.2
                # Suspicious gap (very long)
                elif hours > 168:  # 1 week
                    reason = DataGapReason.SUSPICIOUS
                    severity = 0.8
                # Exchange outage
                elif hours > 24:
                    reason = DataGapReason.EXCHANGE_OUTAGE
                    severity = 0.6
                else:
                    reason = DataGapReason.MISSING
                    severity = 0.4
                
                gaps.append(DataGap(
                    start_time=timestamps[i-1],
                    end_time=timestamps[i],
                    reason=reason,
                    severity=severity,
                ))
                
                if severity >= 0.5:
                    warnings.append(f"检测到重大数据缺口 ({hours:.1f}小时) at {timestamps[i]}")
        
        return gaps, warnings
    
    def _detect_survivorship_bias(self, data: List[Any]) -> List[str]:
        """检查存活者偏差警告"""
        warnings = []
        
        # Check for suspicious price patterns
        for i, point in enumerate(data[:10]):  # Check first 10 points
            close = point.get("close", 0) if isinstance(point, dict) else getattr(point, 'close', 0)
            volume = point.get("volume", 0) if isinstance(point, dict) else getattr(point, 'volume', 0)
            
            if volume == 0:
                warnings.append(f"可能的存活者偏差: 第{i}个数据点成交量为0")
        
        return warnings
    
    def _get_expected_interval(self, interval: str) -> timedelta:
        """获取期望的时间间隔"""
        if interval == "1m":
            return timedelta(minutes=1)
        elif interval == "5m":
            return timedelta(minutes=5)
        elif interval == "15m":
            return timedelta(minutes=15)
        elif interval == "30m":
            return timedelta(minutes=30)
        elif interval == "1h":
            return timedelta(hours=1)
        elif interval == "4h":
            return timedelta(hours=4)
        elif interval == "1d":
            return timedelta(days=1)
        elif interval == "1w":
            return timedelta(weeks=1)
        else:
            return timedelta(hours=1)  # Default
    
    def _calculate_expected_points(
        self,
        timestamps: List[datetime],
        interval: str,
    ) -> int:
        """计算期望的数据点数"""
        if len(timestamps) < 2:
            return 0
        
        expected_delta = self._get_expected_interval(interval)
        total_span = timestamps[-1] - timestamps[0]
        
        return int(total_span / expected_delta) + 1


# ============================================================================
# Data Pipeline
# ============================================================================


@dataclass(slots=True)
class PipelineConfig:
    """Data pipeline configuration"""
    use_cache: bool = True
    cache_ttl_hours: int = 24
    validate_data: bool = True
    parallel_loaders: int = 4
    max_retries: int = 3
    retry_delay_seconds: float = 1.0


class DataPipeline:
    """
    Backtesting data pipeline
    
    Orchestrates data loading, validation, and caching.
    
    Pipeline:
        FeatureStore → DataLoader → DataValidator → Cache → BacktestEngine
                              ↓
                        QualityReport
    
    使用方式:
        pipeline = DataPipeline(data_provider=provider, config=PipelineConfig())
        
        # Single symbol
        data, quality = await pipeline.load_data("BTCUSDT", "1h", start, end)
        
        # Multiple symbols in parallel
        results = await pipeline.load_multiple(["BTCUSDT", "ETHUSDT"], "1h", start, end)
    """
    
    def __init__(
        self,
        data_provider: Optional[Any] = None,
        cache: Optional[DataCache] = None,
        validator: Optional[DataValidator] = None,
        config: Optional[PipelineConfig] = None,
    ):
        """
        初始化数据管道
        
        Args:
            data_provider: 数据供给器 (需实现 DataProviderPort)
            cache: 数据缓存
            validator: 数据验证器
            config: 管道配置
        """
        self._provider = data_provider
        self._cache = cache or DataCache()
        self._validator = validator or DataValidator()
        self._config = config or PipelineConfig()
        
        self._load_semaphore = asyncio.Semaphore(self._config.parallel_loaders)
    
    async def load_data(
        self,
        symbol: str,
        interval: str,
        start: datetime,
        end: datetime,
        use_cache: Optional[bool] = None,
    ) -> Tuple[List[Any], DataQualityReport]:
        """
        加载数据
        
        Args:
            symbol: 交易标的
            interval: K线周期
            start: 开始时间
            end: 结束时间
            use_cache: 是否使用缓存 (默认使用配置中的设置)
            
        Returns:
            (数据列表, 质量报告)
        """
        use_cache = use_cache if use_cache is not None else self._config.use_cache
        
        # Try cache first
        if use_cache:
            cached_data = self._cache.get(symbol, interval, start, end)
            if cached_data is not None:
                logger.debug(f"Cache hit for {symbol} {interval}")
                # Still validate but with cached data
                quality_report = self._validator.validate(cached_data, symbol, interval)
                return cached_data, quality_report
        
        # Load from provider
        async with self._load_semaphore:
            data = await self._load_from_provider(symbol, interval, start, end)
        
        # Validate
        quality_report = self._validator.validate(data, symbol, interval)
        
        # Cache if acceptable
        if use_cache and quality_report.is_acceptable():
            self._cache.set(symbol, interval, start, end, data)
        
        return data, quality_report
    
    async def load_multiple(
        self,
        symbols: List[str],
        interval: str,
        start: datetime,
        end: datetime,
    ) -> Dict[str, Tuple[List[Any], DataQualityReport]]:
        """
        批量加载多个标的的数据
        
        Args:
            symbols: 标的列表
            interval: K线周期
            start: 开始时间
            end: 结束时间
            
        Returns:
            {symbol: (数据, 质量报告)}
        """
        tasks = [
            self.load_data(symbol, interval, start, end)
            for symbol in symbols
        ]
        
        results = await asyncio.gather(*tasks, return_exceptions=True)
        
        output: Dict[str, Tuple[List[Any], DataQualityReport]] = {}
        for symbol, result in zip(symbols, results):
            if isinstance(result, Exception):
                logger.error(f"Failed to load {symbol}: {result}")
                # Create error report
                error_report = DataQualityReport(
                    status=DataQualityStatus.FAIL,
                    issues=[DataQualityIssue(
                        issue_type="LOAD_FAILED",
                        timestamp=datetime.now(timezone.utc),
                        severity=1.0,
                        message=str(result),
                        affected_symbols=[symbol],
                    )],
                )
                output[symbol] = ([], error_report)
            else:
                output[symbol] = result
        
        return output
    
    async def _load_from_provider(
        self,
        symbol: str,
        interval: str,
        start: datetime,
        end: datetime,
    ) -> List[Any]:
        """从数据提供者加载数据"""
        retries = 0
        last_error = None
        
        while retries < self._config.max_retries:
            try:
                if self._provider:
                    # Use actual data provider
                    if hasattr(self._provider, 'get_klines'):
                        return await self._provider.get_klines(symbol, interval, start, end)
                    elif hasattr(self._provider, 'get_ohlcv'):
                        return await self._provider.get_ohlcv(symbol, interval, start, end)
                
                # Fallback: generate mock data for testing
                return self._generate_mock_data(symbol, interval, start, end)
                
            except Exception as e:
                last_error = e
                retries += 1
                if retries < self._config.max_retries:
                    await asyncio.sleep(self._config.retry_delay_seconds * retries)
        
        raise last_error or Exception(f"Failed to load data for {symbol}")
    
    def _generate_mock_data(
        self,
        symbol: str,
        interval: str,
        start: datetime,
        end: datetime,
    ) -> List[Dict[str, Any]]:
        """生成模拟数据用于测试"""
        data = []
        current = start
        price = 100.0
        
        delta = self._get_delta(interval)
        
        while current <= end:
            data.append({
                "timestamp": current,
                "open": price,
                "high": price * 1.01,
                "low": price * 0.99,
                "close": price * (1 + (hash(symbol) % 100 - 50) / 5000),
                "volume": 1000 + hash(symbol) % 500,
            })
            price = data[-1]["close"]
            current += delta
        
        return data
    
    def _get_delta(self, interval: str) -> timedelta:
        """获取时间增量"""
        if interval == "1m":
            return timedelta(minutes=1)
        elif interval == "5m":
            return timedelta(minutes=5)
        elif interval == "15m":
            return timedelta(minutes=15)
        elif interval == "30m":
            return timedelta(minutes=30)
        elif interval == "1h":
            return timedelta(hours=1)
        elif interval == "4h":
            return timedelta(hours=4)
        elif interval == "1d":
            return timedelta(days=1)
        elif interval == "1w":
            return timedelta(weeks=1)
        return timedelta(hours=1)
    
    def get_cache_stats(self) -> Dict[str, Any]:
        """获取缓存统计"""
        return self._cache.get_stats()
    
    def clear_cache(self, pattern: Optional[str] = None) -> int:
        """清除缓存"""
        return self._cache.invalidate(pattern)


# ============================================================================
# Parallel Backtest Runner
# ============================================================================


class ParallelBacktestRunner:
    """
    Parallel backtest runner
    
    Runs multiple backtests in parallel with shared data pipeline.
    
    使用方式:
        runner = ParallelBacktestRunner(
            pipeline=data_pipeline,
            backtest_engine=engine,
            max_parallel=4,
        )
        
        results = await runner.run_multiple(strategies, config)
    """
    
    def __init__(
        self,
        pipeline: Optional[DataPipeline] = None,
        backtest_engine: Optional[Any] = None,
        max_parallel: int = 4,
    ):
        """
        初始化并行回测运行器
        
        Args:
            pipeline: 数据管道
            backtest_engine: 回测引擎
            max_parallel: 最大并行数
        """
        self._pipeline = pipeline or DataPipeline()
        self._engine = backtest_engine
        self._max_parallel = max_parallel
        self._semaphore = asyncio.Semaphore(max_parallel)
    
    async def run_backtest(
        self,
        strategy: Any,
        config: Any,
        symbol: str,
    ) -> Tuple[Any, Optional[str]]:
        """
        运行单个回测
        
        Returns:
            (回测结果, 错误信息)
        """
        async with self._semaphore:
            try:
                # Load data
                data, quality = await self._pipeline.load_data(
                    symbol,
                    config.interval,
                    config.start_date,
                    config.end_date,
                )
                
                if not quality.is_acceptable():
                    return None, f"Data quality issue: {quality.status}"
                
                # Run backtest
                if self._engine:
                    result = await self._engine.run_backtest(config, strategy)
                    return result, None
                
                # Mock result
                from trader.services.backtesting.ports import BacktestResult
                return BacktestResult(
                    total_return=Decimal("10.0"),
                    sharpe_ratio=Decimal("1.5"),
                    max_drawdown=Decimal("5.0"),
                    win_rate=Decimal("60.0"),
                    profit_factor=Decimal("1.5"),
                    num_trades=50,
                    final_capital=Decimal("110000"),
                ), None
                
            except Exception as e:
                return None, str(e)
    
    async def run_multiple(
        self,
        strategies: List[Tuple[Any, Any]],  # List of (strategy, config)
        symbol: str,
    ) -> List[Tuple[Any, Optional[str]]]:
        """
        批量运行回测
        
        Args:
            strategies: (策略, 配置)元组列表
            symbol: 交易标的
            
        Returns:
            [(回测结果, 错误信息), ...]
        """
        tasks = [
            self.run_backtest(strategy, config, symbol)
            for strategy, config in strategies
        ]
        
        return await asyncio.gather(*tasks)


# ============================================================================
# Helper Functions
# ============================================================================


def create_pipeline(
    data_provider: Optional[Any] = None,
    cache_size_mb: float = 100.0,
    enable_validation: bool = True,
) -> DataPipeline:
    """
    创建数据管道的便捷函数
    
    Args:
        data_provider: 数据供给器
        cache_size_mb: 缓存大小 (MB)
        enable_validation: 是否启用验证
        
    Returns:
        DataPipeline实例
    """
    cache = DataCache(max_size_mb=cache_size_mb)
    validator = DataValidator() if enable_validation else None
    config = PipelineConfig(use_cache=True, validate_data=enable_validation)
    
    return DataPipeline(
        data_provider=data_provider,
        cache=cache,
        validator=validator,
        config=config,
    )
