"""
StrategyEvaluator - 策略评估器
================================

.. deprecated::
    本模块已被 `trader.services.backtesting` 模块替代。
    请使用新的回测框架，参见 `docs/migration_guide.md`。

迁移指南：
    1. BacktestEngine -> 使用 QuantConnectLeanBacktestEngine
    2. StrategyMetrics -> StandardizedBacktestReport
    3. LiveEvaluator -> Integration with StrategyLifecycleManager

负责策略回测验证和实时表现评估。

核心组件：
- BacktestEngine: 回测引擎，使用历史数据重放验证策略
- LiveEvaluator: 实时评估器，计算策略运行时的表现指标
- StrategyMetrics: 策略性能指标（夏普率、最大回撤、胜率等）
- FeatureStorePort: 特征存储端口（用于回测数据获取）

设计原则：
1. BacktestEngine 属于 Service 层，可以使用 IO
2. LiveEvaluator 属于 Service 层，可以有 IO
3. 计算逻辑尽量纯函数化，便于测试
4. 严格幂等：评估结果可重复计算

验收标准：
1. 回测报告含夏普率/最大回撤/胜率
2. 实时指标可查
3. 数据质量验证
4. 1年数据<1分钟
"""
from __future__ import annotations

import asyncio
import logging
import math
import time
from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from datetime import datetime, timezone, timedelta
from decimal import Decimal
from enum import Enum
from typing import (
    Any,
    Callable,
    Dict,
    List,
    Optional,
    Protocol,
    runtime_checkable,
    Sequence,
    Tuple,
)

logger = logging.getLogger(__name__)


# ============================================================================
# 策略指标 (StrategyMetrics)
# ============================================================================


@dataclass(slots=True)
class StrategyMetrics:
    """
    策略性能指标
    
    属性：
        total_pnl: 总盈亏（单位：USDT）
        sharpe_ratio: 夏普率（年化）
        max_drawdown: 最大回撤（单位：USDT）
        win_rate: 胜率（0-1）
        trade_count: 交易次数
        profit_factor: 盈亏比（gross profit / gross loss）
        avg_win: 平均盈利
        avg_loss: 平均亏损
        running_time: 运行时长（秒）
        equity_curve: 权益曲线（可选，用于绘图）
    """
    total_pnl: Decimal = Decimal("0")
    sharpe_ratio: float = 0.0
    max_drawdown: Decimal = Decimal("0")
    win_rate: float = 0.0
    trade_count: int = 0
    profit_factor: float = 0.0
    avg_win: Decimal = Decimal("0")
    avg_loss: Decimal = Decimal("0")
    running_time: float = 0.0
    equity_curve: List[Decimal] = field(default_factory=list)
    
    # 扩展指标
    monthly_return: float = 0.0
    annual_return: float = 0.0
    volatility: float = 0.0
    calmar_ratio: float = 0.0  # 年化收益 / 最大回撤
    
    def __post_init__(self):
        """类型安全转换"""
        if isinstance(self.total_pnl, (int, float)):
            object.__setattr__(self, 'total_pnl', Decimal(str(self.total_pnl)))
        if isinstance(self.max_drawdown, (int, float)):
            object.__setattr__(self, 'max_drawdown', Decimal(str(self.max_drawdown)))
        if isinstance(self.avg_win, (int, float)):
            object.__setattr__(self, 'avg_win', Decimal(str(self.avg_win)))
        if isinstance(self.avg_loss, (int, float)):
            object.__setattr__(self, 'avg_loss', Decimal(str(self.avg_loss)))
    
    @property
    def is_profitable(self) -> bool:
        """策略是否盈利"""
        return self.total_pnl > 0
    
    @property
    def summary(self) -> Dict[str, Any]:
        """返回指标摘要"""
        return {
            "total_pnl": float(self.total_pnl),
            "sharpe_ratio": self.sharpe_ratio,
            "max_drawdown": float(self.max_drawdown),
            "win_rate": self.win_rate,
            "trade_count": self.trade_count,
            "profit_factor": self.profit_factor,
            "is_profitable": self.is_profitable,
        }


# ============================================================================
# 回测报告 (BacktestReport)
# ============================================================================


@dataclass(slots=True)
class BacktestTrade:
    """回测交易记录"""
    trade_id: str
    entry_time: datetime
    exit_time: datetime
    entry_price: Decimal
    exit_price: Decimal
    quantity: Decimal
    pnl: Decimal
    side: str  # "LONG" or "SHORT"
    exit_reason: str  # "TAKE_PROFIT", "STOP_LOSS", "SIGNAL"
    

@dataclass(slots=True)
class BacktestReport:
    """
    回测报告
    
    包含完整的回测统计信息和交易记录。
    
    属性：
        strategy_name: 策略名称
        start_time: 回测开始时间
        end_time: 回测结束时间
        metrics: 策略指标
        trades: 交易记录列表
        data_quality: 数据质量验证结果
        execution_time_seconds: 回测执行时长
        initial_capital: 初始资金
        final_capital: 最终资金
    """
    strategy_name: str
    start_time: datetime
    end_time: datetime
    metrics: StrategyMetrics
    trades: List[BacktestTrade] = field(default_factory=list)
    data_quality: DataQualityResult = field(default_factory=lambda: DataQualityResult())
    execution_time_seconds: float = 0.0
    initial_capital: Decimal = Decimal("10000")
    final_capital: Decimal = Decimal("10000")
    
    @property
    def return_percent(self) -> float:
        """收益率（百分比）"""
        if self.initial_capital > 0:
            return float((self.final_capital - self.initial_capital) / self.initial_capital * 100)
        return 0.0
    
    def to_dict(self) -> Dict[str, Any]:
        """转换为字典格式"""
        return {
            "strategy_name": self.strategy_name,
            "start_time": self.start_time.isoformat(),
            "end_time": self.end_time.isoformat(),
            "return_percent": self.return_percent,
            "execution_time_seconds": self.execution_time_seconds,
            "initial_capital": float(self.initial_capital),
            "final_capital": float(self.final_capital),
            "metrics": {
                "total_pnl": float(self.metrics.total_pnl),
                "sharpe_ratio": self.metrics.sharpe_ratio,
                "max_drawdown": float(self.metrics.max_drawdown),
                "win_rate": self.metrics.win_rate,
                "trade_count": self.metrics.trade_count,
                "profit_factor": self.metrics.profit_factor,
                "monthly_return": self.metrics.monthly_return,
                "annual_return": self.metrics.annual_return,
                "calmar_ratio": self.metrics.calmar_ratio,
            },
            "data_quality": self.data_quality.to_dict(),
            "trade_count": len(self.trades),
        }


# ============================================================================
# 数据质量验证 (DataQuality)
# ============================================================================


class DataQualityStatus(Enum):
    """数据质量状态"""
    PASS = "PASS"
    WARNING = "WARNING"
    FAIL = "FAIL"


@dataclass(slots=True)
class DataQualityIssue:
    """数据质量问题"""
    issue_type: str
    message: str
    affected_range: Optional[Tuple[datetime, datetime]] = None
    severity: str = "WARNING"  # "WARNING" or "ERROR"


@dataclass(slots=True)
class DataQualityResult:
    """
    数据质量验证结果
    
    属性：
        status: 验证状态
        issues: 问题列表
        coverage_percent: 数据覆盖率
        gap_count: 数据间隙数量
        total_bars: 总K线数量
    """
    status: DataQualityStatus = DataQualityStatus.PASS
    issues: List[DataQualityIssue] = field(default_factory=list)
    coverage_percent: float = 100.0
    gap_count: int = 0
    total_bars: int = 0
    
    def add_issue(self, issue: DataQualityIssue) -> None:
        """添加问题"""
        self.issues.append(issue)
        if issue.severity == "ERROR":
            self.status = DataQualityStatus.FAIL
        elif self.status != DataQualityStatus.FAIL and issue.severity == "WARNING":
            self.status = DataQualityStatus.WARNING
    
    def to_dict(self) -> Dict[str, Any]:
        """转换为字典"""
        return {
            "status": self.status.value,
            "issues": [
                {
                    "issue_type": i.issue_type,
                    "message": i.message,
                    "affected_range": (
                        i.affected_range[0].isoformat() if i.affected_range else None
                    ),
                    "severity": i.severity,
                }
                for i in self.issues
            ],
            "coverage_percent": self.coverage_percent,
            "gap_count": self.gap_count,
            "total_bars": self.total_bars,
        }


# ============================================================================
# 评估结果 (EvaluationResult)
# ============================================================================


class EvaluationStatus(Enum):
    """评估状态"""
    HEALTHY = "HEALTHY"
    WARNING = "WARNING"
    CRITICAL = "CRITICAL"


@dataclass(slots=True)
class AnomalyAlert:
    """异常告警"""
    alert_type: str  # "DRAWDOWN_SPIKE", "WIN_RATE_DROP", "P&L_REVERSAL", etc.
    message: str
    severity: EvaluationStatus
    timestamp: datetime = field(default_factory=lambda: datetime.now(timezone.utc))
    metadata: Dict[str, Any] = field(default_factory=dict)


@dataclass(slots=True)
class EvaluationResult:
    """
    策略评估结果
    
    属性：
        strategy_name: 策略名称
        timestamp: 评估时间戳
        status: 评估状态
        metrics: 当前策略指标
        alerts: 告警列表
        recommendations: 建议列表
        comparison_with_backtest: 与回测结果的对比
    """
    strategy_name: str
    timestamp: datetime = field(default_factory=lambda: datetime.now(timezone.utc))
    status: EvaluationStatus = EvaluationStatus.HEALTHY
    metrics: StrategyMetrics = field(default_factory=StrategyMetrics)
    alerts: List[AnomalyAlert] = field(default_factory=list)
    recommendations: List[str] = field(default_factory=list)
    comparison_with_backtest: Optional[Dict[str, Any]] = None
    
    def add_alert(self, alert: AnomalyAlert) -> None:
        """添加告警"""
        self.alerts.append(alert)
        if alert.severity == EvaluationStatus.CRITICAL:
            self.status = EvaluationStatus.CRITICAL
        elif alert.severity == EvaluationStatus.WARNING and self.status == EvaluationStatus.HEALTHY:
            self.status = EvaluationStatus.WARNING


# ============================================================================
# FeatureStorePort (Port接口)
# ============================================================================


@runtime_checkable
class FeatureStorePort(Protocol):
    """
    特征存储端口
    
    定义回测引擎获取历史特征数据的接口。
    实现方可以是：
    - trader/adapters/persistence/feature_store.py::FeatureStore
    - 任何提供相同接口的适配器
    """
    
    async def read_feature_range(
        self,
        symbol: str,
        feature_name: str,
        start_time: int,  # milliseconds timestamp
        end_time: int,
        version: Optional[str] = None,
    ) -> List[Any]:
        """读取时间范围内的特征数据"""
        ...
    
    async def read_feature(
        self,
        symbol: str,
        feature_name: str,
        version: str,
        ts_ms: int,
    ) -> Optional[Dict[str, Any]]:
        """读取单个特征点"""
        ...


# ============================================================================
# MarketDataProvider (市场数据提供者接口)
# ============================================================================


@runtime_checkable
class MarketDataProvider(Protocol):
    """
    市场数据提供者接口
    
    用于回测引擎获取历史K线数据。
    """
    
    async def get_klines(
        self,
        symbol: str,
        interval: str,
        start_time_ms: int,
        end_time_ms: int,
        limit: int = 1000,
    ) -> List[Dict[str, Any]]:
        """获取K线数据"""
        ...


# ============================================================================
# BacktestEngine (回测引擎)
# ============================================================================


@dataclass
class BacktestConfig:
    """回测配置"""
    initial_capital: Decimal = Decimal("10000")
    commission_rate: Decimal = Decimal("0.001")  # 0.1% 手续费
    slippage_rate: Decimal = Decimal("0.0005")   # 0.05% 滑点
    risk_free_rate: float = 0.02  # 无风险利率（年化）
    symbols: List[str] = field(default_factory=lambda: ["BTCUSDT"])
    interval: str = "1h"  # K线周期


class BacktestEngine:
    """
    回测引擎
    
    职责：
    1. 使用历史市场数据重放策略
    2. 计算性能指标（夏普率、最大回撤、胜率等）
    3. 数据质量验证
    4. 生成完整回测报告
    
    性能要求：
    - 1年数据（每小时K线）< 1分钟执行完成
    
    使用示例：
        engine = BacktestEngine(
            data_provider=my_data_provider,
            feature_store=my_feature_store,
        )
        
        report = await engine.run_backtest(
            strategy=my_strategy,
            start_time=datetime(2024, 1, 1),
            end_time=datetime(2024, 12, 31),
        )
        
        print(f"夏普率: {report.metrics.sharpe_ratio}")
        print(f"最大回撤: {report.metrics.max_drawdown}")
    """
    
    def __init__(
        self,
        data_provider: Optional[MarketDataProvider] = None,
        feature_store: Optional[FeatureStorePort] = None,
        config: Optional[BacktestConfig] = None,
    ):
        """
        初始化回测引擎
        
        Args:
            data_provider: 市场数据提供者（可选，默认使用内存数据）
            feature_store: 特征存储端口（可选）
            config: 回测配置
        """
        self._data_provider = data_provider
        self._feature_store = feature_store
        self._config = config or BacktestConfig()
        
        # 内部状态
        self._equity_curve: List[Decimal] = []
        self._trades: List[BacktestTrade] = []
        self._positions: Dict[str, Dict[str, Any]] = {}  # symbol -> position
        self._current_capital: Decimal = self._config.initial_capital
    
    async def run_backtest(
        self,
        strategy: Any,  # StrategyPlugin
        start_time: datetime,
        end_time: datetime,
        strategy_id: Optional[str] = None,
    ) -> BacktestReport:
        """
        运行回测
        
        Args:
            strategy: 策略实例（需实现 on_market_data 或 on_tick）
            start_time: 回测开始时间
            end_time: 回测结束时间
            strategy_id: 策略ID（用于报告）
            
        Returns:
            BacktestReport: 完整的回测报告
        """
        start_exec = time.monotonic()
        
        strategy_name: str = strategy_id or getattr(strategy, 'name', None) or str(strategy)
        
        # 重置内部状态
        self._reset_state()
        
        # 获取市场数据
        klines_by_symbol: Dict[str, List[Dict[str, Any]]] = {}
        for symbol in self._config.symbols:
            if self._data_provider:
                klines = await self._data_provider.get_klines(
                    symbol=symbol,
                    interval=self._config.interval,
                    start_time_ms=int(start_time.timestamp() * 1000),
                    end_time_ms=int(end_time.timestamp() * 1000),
                    limit=10000,
                )
            else:
                # 生成模拟数据用于测试
                klines = self._generate_mock_klines(symbol, start_time, end_time)
            
            klines_by_symbol[symbol] = klines
        
        # 数据质量验证
        data_quality = self._validate_data_quality(klines_by_symbol, start_time, end_time)
        
        # 执行回测循环
        for symbol, klines in klines_by_symbol.items():
            for i, kline in enumerate(klines):
                # 构造 MarketData 输入
                market_data = self._kline_to_market_data(kline, symbol)
                
                # 调用策略
                signal = None
                try:
                    if hasattr(strategy, 'on_market_data'):
                        signal = strategy.on_market_data(market_data)
                    elif hasattr(strategy, 'on_tick'):
                        signal = await strategy.on_tick(market_data)
                except Exception as e:
                    logger.warning(f"策略信号生成失败: {e}")
                    continue
                
                # 执行交易
                if signal:
                    self._execute_signal(symbol, signal, market_data)
                
                # 更新权益曲线
                self._update_equity(symbol, market_data)
        
        # 计算性能指标
        metrics = self._calculate_metrics(start_time, end_time)
        
        execution_time = time.monotonic() - start_exec
        
        return BacktestReport(
            strategy_name=strategy_name,
            start_time=start_time,
            end_time=end_time,
            metrics=metrics,
            trades=self._trades.copy(),
            data_quality=data_quality,
            execution_time_seconds=execution_time,
            initial_capital=self._config.initial_capital,
            final_capital=self._current_capital,
        )
    
    def _reset_state(self) -> None:
        """重置内部状态"""
        self._equity_curve = []
        self._trades = []
        self._positions = {}
        self._current_capital = self._config.initial_capital
    
    def _generate_mock_klines(
        self,
        symbol: str,
        start_time: datetime,
        end_time: datetime,
    ) -> List[Dict[str, Any]]:
        """生成模拟K线数据（用于测试）"""
        from decimal import Decimal
        
        klines = []
        interval_map = {
            "1m": 1, "5m": 5, "15m": 15, "30m": 30,
            "1h": 60, "4h": 240, "1d": 1440,
        }
        interval_minutes = interval_map.get(self._config.interval, 60)
        
        current = start_time
        base_price = Decimal("50000")  # BTC初始价格
        
        import random
        random.seed(42)  # 确定性模拟
        
        while current < end_time:
            # 生成随机价格变动
            change_percent = (random.random() - 0.5) * 0.02  # ±1%
            open_price = base_price
            close_price = base_price * (1 + Decimal(str(change_percent)))
            high_price = max(open_price, close_price) * (1 + Decimal(str(random.random() * 0.005)))
            low_price = min(open_price, close_price) * (1 - Decimal(str(random.random() * 0.005)))
            volume = Decimal(str(random.randint(100, 1000)))
            
            klines.append({
                "symbol": symbol,
                "open_time": int(current.timestamp() * 1000),
                "close_time": int((current + timedelta(minutes=interval_minutes)).timestamp() * 1000),
                "open": float(open_price),
                "high": float(high_price),
                "low": float(low_price),
                "close": float(close_price),
                "volume": float(volume),
                "interval": self._config.interval,
            })
            
            base_price = close_price
            current += timedelta(minutes=interval_minutes)
        
        return klines
    
    def _kline_to_market_data(self, kline: Dict[str, Any], symbol: str) -> Any:
        """将K线转换为 MarketData 对象"""
        from dataclasses import dataclass, field
        from datetime import datetime, timezone
        from decimal import Decimal
        
        @dataclass(slots=True)
        class MarketDataSimple:
            symbol: str
            timestamp: datetime
            price: Decimal
            volume: Decimal
            kline_open: Decimal
            kline_high: Decimal
            kline_low: Decimal
            kline_close: Decimal
            kline_interval: str
            
            @property
            def spread(self) -> Optional[Decimal]:
                return None
        
        return MarketDataSimple(
            symbol=symbol,
            timestamp=datetime.fromtimestamp(kline["open_time"] / 1000, tz=timezone.utc),
            price=Decimal(str(kline["close"])),
            volume=Decimal(str(kline["volume"])),
            kline_open=Decimal(str(kline["open"])),
            kline_high=Decimal(str(kline["high"])),
            kline_low=Decimal(str(kline["low"])),
            kline_close=Decimal(str(kline["close"])),
            kline_interval=kline.get("interval", self._config.interval),
        )
    
    def _validate_data_quality(
        self,
        klines_by_symbol: Dict[str, List[Dict[str, Any]]],
        start_time: datetime,
        end_time: datetime,
    ) -> DataQualityResult:
        """验证数据质量"""
        result = DataQualityResult()
        
        total_expected_bars = 0
        total_actual_bars = 0
        
        interval_map = {
            "1m": 1, "5m": 5, "15m": 15, "30m": 30,
            "1h": 60, "4h": 240, "1d": 1440,
        }
        interval_minutes = interval_map.get(self._config.interval, 60)
        
        for symbol, klines in klines_by_symbol.items():
            # 计算期望的K线数量
            duration_minutes = (end_time - start_time).total_seconds() / 60
            expected_bars = int(duration_minutes / interval_minutes)
            total_expected_bars += expected_bars
            total_actual_bars += len(klines)
            
            # 检查时间间隙
            if len(klines) > 1:
                for i in range(1, len(klines)):
                    prev_close = klines[i-1]["close_time"]
                    curr_open = klines[i]["open_time"]
                    gap_ms = curr_open - prev_close
                    
                    expected_gap_ms = interval_minutes * 60 * 1000
                    if gap_ms > expected_gap_ms * 1.5:  # 超过50%的间隙
                        result.gap_count += 1
                        result.add_issue(DataQualityIssue(
                            issue_type="TIME_GAP",
                            message=f"时间间隙超过预期: {gap_ms}ms vs 预期 {expected_gap_ms}ms",
                            affected_range=(
                                datetime.fromtimestamp(prev_close / 1000, tz=timezone.utc),
                                datetime.fromtimestamp(curr_open / 1000, tz=timezone.utc),
                            ),
                        ))
            
            # 检查价格异常
            for kline in klines:
                if kline["high"] < kline["low"]:
                    result.add_issue(DataQualityIssue(
                        issue_type="INVALID_OHLC",
                        message=f"High < Low: high={kline['high']}, low={kline['low']}",
                        affected_range=(
                            datetime.fromtimestamp(kline["open_time"] / 1000, tz=timezone.utc),
                            datetime.fromtimestamp(kline["close_time"] / 1000, tz=timezone.utc),
                        ),
                        severity="ERROR",
                    ))
        
        result.total_bars = total_actual_bars
        
        if total_expected_bars > 0:
            result.coverage_percent = (total_actual_bars / total_expected_bars) * 100
        
        # 检查覆盖率
        if result.coverage_percent < 90:
            result.add_issue(DataQualityIssue(
                issue_type="LOW_COVERAGE",
                message=f"数据覆盖率过低: {result.coverage_percent:.2f}%",
                severity="WARNING",
            ))
        
        return result
    
    def _execute_signal(self, symbol: str, signal: Any, market_data: Any) -> None:
        """执行交易信号"""
        from decimal import Decimal
        from datetime import datetime, timezone
        import uuid
        
        if not signal or signal.quantity <= 0:
            return
        
        price = market_data.price
        quantity = signal.quantity
        
        # 计算手续费和滑点
        execution_price = price * (1 + self._config.slippage_rate)
        commission = execution_price * quantity * self._config.commission_rate
        
        side = getattr(signal, 'signal_type', None)
        if side is None:
            return
        
        # 平仓逻辑
        if symbol in self._positions:
            pos = self._positions[symbol]
            pos_pnl = pos["quantity"] * (execution_price - pos["entry_price"])
            
            # 更新资金
            self._current_capital += pos_pnl - commission
            
            # 记录交易
            self._trades.append(BacktestTrade(
                trade_id=str(uuid.uuid4()),
                entry_time=pos["entry_time"],
                exit_time=market_data.timestamp,
                entry_price=pos["entry_price"],
                exit_price=execution_price,
                quantity=pos["quantity"],
                pnl=pos_pnl - commission,
                side=pos["side"],
                exit_reason="SIGNAL",
            ))
            
            del self._positions[symbol]
        
        # 开仓逻辑
        if side in ["BUY", "LONG"]:
            self._positions[symbol] = {
                "entry_time": market_data.timestamp,
                "entry_price": execution_price,
                "quantity": quantity,
                "side": "LONG",
            }
            self._current_capital -= commission
        elif side in ["SELL", "SHORT"]:
            self._positions[symbol] = {
                "entry_time": market_data.timestamp,
                "entry_price": execution_price,
                "quantity": quantity,
                "side": "SHORT",
            }
            self._current_capital -= commission
    
    def _update_equity(self, symbol: str, market_data: Any) -> None:
        """更新权益曲线"""
        unrealized_pnl = Decimal("0")
        
        if symbol in self._positions:
            pos = self._positions[symbol]
            if pos["side"] == "LONG":
                unrealized_pnl = pos["quantity"] * (market_data.price - pos["entry_price"])
            elif pos["side"] == "SHORT":
                unrealized_pnl = pos["quantity"] * (pos["entry_price"] - market_data.price)
        
        current_equity = self._current_capital + unrealized_pnl
        self._equity_curve.append(current_equity)
    
    def _calculate_metrics(
        self,
        start_time: datetime,
        end_time: datetime,
    ) -> StrategyMetrics:
        """计算性能指标"""
        from decimal import Decimal
        
        # 基本统计
        total_trades = len(self._trades)
        
        if total_trades == 0:
            return StrategyMetrics(
                trade_count=0,
                running_time=float((end_time - start_time).total_seconds()),
                equity_curve=self._equity_curve.copy(),
            )
        
        # 计算盈亏
        gross_profit = Decimal("0")
        gross_loss = Decimal("0")
        winning_trades = 0
        
        for trade in self._trades:
            if trade.pnl > 0:
                gross_profit += trade.pnl
                winning_trades += 1
            else:
                gross_loss += abs(trade.pnl)
        
        total_pnl = gross_profit - gross_loss
        
        # 胜率
        win_rate = winning_trades / total_trades if total_trades > 0 else 0.0
        
        # 盈亏比
        avg_win = gross_profit / Decimal(str(winning_trades)) if winning_trades > 0 else Decimal("0")
        avg_loss = gross_loss / Decimal(str(total_trades - winning_trades)) if total_trades > winning_trades else Decimal("0")
        profit_factor = float(gross_profit / gross_loss) if gross_loss > 0 else 0.0
        
        # 最大回撤
        max_drawdown = Decimal("0")
        peak = self._equity_curve[0] if self._equity_curve else Decimal("0")
        
        for equity in self._equity_curve:
            if equity > peak:
                peak = equity
            drawdown = peak - equity
            if drawdown > max_drawdown:
                max_drawdown = drawdown
        
        # 夏普率计算
        sharpe_ratio = 0.0
        volatility = 0.0
        
        if len(self._equity_curve) > 1:
            returns = []
            for i in range(1, len(self._equity_curve)):
                ret = (self._equity_curve[i] - self._equity_curve[i-1]) / self._equity_curve[i-1]
                returns.append(float(ret))
            
            if returns:
                mean_return = sum(returns) / len(returns)
                variance = sum((r - mean_return) ** 2 for r in returns) / len(returns)
                volatility = math.sqrt(variance) if variance > 0 else 0.0
                
                # 年化
                periods_per_year = 365 * 24  # 假设每小时一个数据点
                annual_return = mean_return * periods_per_year
                annual_volatility = volatility * math.sqrt(periods_per_year)
                
                if annual_volatility > 0:
                    sharpe_ratio = (annual_return - self._config.risk_free_rate) / annual_volatility
        
        # Calmar ratio
        max_drawdown_abs = float(max_drawdown)
        annual_return_pct = float(total_pnl / self._config.initial_capital * 100)
        calmar_ratio = annual_return_pct / max_drawdown_abs if max_drawdown_abs > 0 else 0.0
        
        running_time = float((end_time - start_time).total_seconds())
        
        return StrategyMetrics(
            total_pnl=total_pnl,
            sharpe_ratio=sharpe_ratio,
            max_drawdown=max_drawdown,
            win_rate=win_rate,
            trade_count=total_trades,
            profit_factor=profit_factor,
            avg_win=avg_win,
            avg_loss=avg_loss,
            running_time=running_time,
            equity_curve=self._equity_curve.copy(),
            volatility=volatility,
            calmar_ratio=calmar_ratio,
        )


# ============================================================================
# LiveEvaluator (实时评估器)
# ============================================================================


@dataclass
class LiveEvaluatorConfig:
    """实时评估配置"""
    alert_threshold_drawdown: Decimal = Decimal("0.1")  # 10% 回撤告警
    alert_threshold_winrate_drop: float = 0.1  # 胜率下降10%告警
    min_trades_for_statistics: int = 10  # 最少交易次数才进行统计
    evaluation_interval_seconds: float = 60.0  # 评估间隔


class LiveEvaluator:
    """
    实时评估器
    
    职责：
    1. 实时计算策略表现指标
    2. 异常检测（回撤 spike、胜率下降等）
    3. 与回测结果对比
    4. 生成评估报告和建议
    
    使用示例：
        evaluator = LiveEvaluator(
            backtest_report=baseline_report,
            config=LiveEvaluatorConfig(),
        )
        
        # 定期调用
        result = await evaluator.evaluate(
            strategy=my_strategy,
            metrics=current_metrics,
        )
        
        if result.status == EvaluationStatus.CRITICAL:
            logger.warning(f"策略异常: {result.alerts}")
    """
    
    def __init__(
        self,
        backtest_report: Optional[BacktestReport] = None,
        config: Optional[LiveEvaluatorConfig] = None,
    ):
        """
        初始化实时评估器
        
        Args:
            backtest_report: 基线回测报告（用于对比）
            config: 评估配置
        """
        self._backtest_report = backtest_report
        self._config = config or LiveEvaluatorConfig()
        
        # 历史指标（用于趋势检测）
        self._metrics_history: List[StrategyMetrics] = []
        self._last_evaluation_time: Optional[datetime] = None
    
    async def evaluate(
        self,
        strategy: Any,  # StrategyPlugin
        metrics: StrategyMetrics,
        current_time: Optional[datetime] = None,
    ) -> EvaluationResult:
        """
        评估策略当前状态
        
        Args:
            strategy: 策略实例
            metrics: 当前策略指标
            current_time: 评估时间（默认当前时间）
            
        Returns:
            EvaluationResult: 评估结果
        """
        from datetime import datetime, timezone
        
        strategy_name = getattr(strategy, 'name', getattr(strategy, 'strategy_id', str(strategy)))
        now = current_time or datetime.now(timezone.utc)
        
        # 保存历史
        self._metrics_history.append(metrics)
        self._last_evaluation_time = now
        
        # 创建评估结果
        result = EvaluationResult(
            strategy_name=strategy_name,
            timestamp=now,
            metrics=metrics,
        )
        
        # 与回测对比
        if self._backtest_report:
            result.comparison_with_backtest = self._compare_with_backtest(
                metrics, self._backtest_report.metrics
            )
            
            # 检查显著偏离
            self._check_deviation(result, metrics)
        
        # 异常检测
        self._detect_anomalies(result, metrics)
        
        # 生成建议
        self._generate_recommendations(result, metrics)
        
        return result
    
    def _compare_with_backtest(
        self,
        live_metrics: StrategyMetrics,
        backtest_metrics: StrategyMetrics,
    ) -> Dict[str, Any]:
        """与回测结果对比"""
        return {
            "pnl_diff": float(live_metrics.total_pnl - backtest_metrics.total_pnl),
            "pnl_diff_percent": (
                float(live_metrics.total_pnl / backtest_metrics.total_pnl * 100)
                if backtest_metrics.total_pnl != 0 else 0.0
            ),
            "sharpe_ratio_diff": live_metrics.sharpe_ratio - backtest_metrics.sharpe_ratio,
            "drawdown_diff": float(live_metrics.max_drawdown - backtest_metrics.max_drawdown),
            "win_rate_diff": live_metrics.win_rate - backtest_metrics.win_rate,
            "backtest_metrics": backtest_metrics.summary,
            "live_metrics": live_metrics.summary,
        }
    
    def _check_deviation(
        self,
        result: EvaluationResult,
        metrics: StrategyMetrics,
    ) -> None:
        """检查显著偏离"""
        if not self._backtest_report:
            return
        
        b_metrics = self._backtest_report.metrics
        
        # 检查夏普率偏离
        if b_metrics.sharpe_ratio > 0 and metrics.sharpe_ratio < b_metrics.sharpe_ratio * 0.5:
            result.add_alert(AnomalyAlert(
                alert_type="SHARPE_RATIO_DROP",
                message=f"夏普率显著下降: {metrics.sharpe_ratio:.2f} vs 回测 {b_metrics.sharpe_ratio:.2f}",
                severity=EvaluationStatus.WARNING,
            ))
        
        # 检查胜率偏离
        if b_metrics.win_rate > 0 and metrics.win_rate < b_metrics.win_rate * (1 - self._config.alert_threshold_winrate_drop):
            result.add_alert(AnomalyAlert(
                alert_type="WIN_RATE_DROP",
                message=f"胜率下降: {metrics.win_rate:.2%} vs 回测 {b_metrics.win_rate:.2%}",
                severity=EvaluationStatus.WARNING,
            ))
        
        # 检查回撤超出预期（需要类型兼容）
        threshold = b_metrics.max_drawdown * Decimal("1.5")
        if metrics.max_drawdown > threshold:
            result.add_alert(AnomalyAlert(
                alert_type="DRAWDOWN_EXCEEDED",
                message=f"最大回撤超出回测: {metrics.max_drawdown} vs 回测 {b_metrics.max_drawdown}",
                severity=EvaluationStatus.CRITICAL,
            ))
    
    def _detect_anomalies(
        self,
        result: EvaluationResult,
        metrics: StrategyMetrics,
    ) -> None:
        """检测异常"""
        # 检查最小交易次数
        if metrics.trade_count < self._config.min_trades_for_statistics:
            result.recommendations.append(
                f"交易次数过少 ({metrics.trade_count})，统计数据可能不具代表性"
            )
            return
        
        # 检测回撤 spike（权益曲线急剧下降）
        if len(self._metrics_history) >= 3:
            recent = self._metrics_history[-3:]
            if len(recent) >= 3:
                # 计算近期权益变化率
                equity_changes = []
                for i in range(1, len(recent)):
                    if recent[i-1].equity_curve:
                        change = (
                            recent[i].equity_curve[-1] - recent[i-1].equity_curve[-1]
                        ) / recent[i-1].equity_curve[-1]
                        equity_changes.append(float(abs(change)))
                
                if equity_changes and max(equity_changes) > 0.05:  # 5% 以上的单次变化
                    result.add_alert(AnomalyAlert(
                        alert_type="EQUITY_SPIKE",
                        message=f"权益曲线异常波动: {max(equity_changes):.2%}",
                        severity=EvaluationStatus.WARNING,
                    ))
        
        # 检测连续亏损
        if len(metrics.equity_curve) >= 10:
            recent_equity = metrics.equity_curve[-10:]
            consecutive_losses = 0
            max_consecutive = 0
            
            for i in range(1, len(recent_equity)):
                if recent_equity[i] < recent_equity[i-1]:
                    consecutive_losses += 1
                    max_consecutive = max(max_consecutive, consecutive_losses)
                else:
                    consecutive_losses = 0
            
            if max_consecutive >= 7:
                result.add_alert(AnomalyAlert(
                    alert_type="CONSECUTIVE_LOSSES",
                    message=f"连续亏损: {max_consecutive} 次",
                    severity=EvaluationStatus.WARNING
                    if max_consecutive < 9 else EvaluationStatus.CRITICAL,
                ))
        
        # 检测负夏普率
        if len(self._metrics_history) >= 20 and metrics.sharpe_ratio < -1.0:
            result.add_alert(AnomalyAlert(
                alert_type="NEGATIVE_SHARPE",
                message=f"夏普率为负: {metrics.sharpe_ratio:.2f}",
                severity=EvaluationStatus.CRITICAL,
            ))
    
    def _generate_recommendations(
        self,
        result: EvaluationResult,
        metrics: StrategyMetrics,
    ) -> None:
        """生成建议"""
        # 基于告警生成建议
        alert_types = {alert.alert_type for alert in result.alerts}
        
        if "DRAWDOWN_EXCEEDED" in alert_types or "DRAWDOWN_SPIKE" in alert_types:
            result.recommendations.append("建议检查止损设置，考虑降低仓位")
        
        if "WIN_RATE_DROP" in alert_types or "SHARPE_RATIO_DROP" in alert_types:
            result.recommendations.append("建议检查策略参数是否失效，考虑重新优化")
        
        if "CONSECUTIVE_LOSSES" in alert_types:
            result.recommendations.append("建议暂时降低交易频率，等待市场状态恢复")
        
        # 基于性能指标生成建议
        if metrics.trade_count >= self._config.min_trades_for_statistics:
            if metrics.win_rate < 0.4:
                result.recommendations.append("胜率偏低，建议检查入场逻辑")
            
            if metrics.profit_factor < 1.2 and metrics.profit_factor > 0:
                result.recommendations.append("盈亏比偏低，建议优化止盈止损比例")
            
            if metrics.sharpe_ratio < 0.5 and metrics.sharpe_ratio > 0:
                result.recommendations.append("夏普率偏低，策略风险调整后收益不佳")
        
        # 如果一切正常
        if not result.recommendations and not result.alerts:
            result.recommendations.append("策略运行正常，继续监控")


# ============================================================================
# 辅助函数
# ============================================================================


def calculate_sharpe_ratio(
    returns: List[float],
    risk_free_rate: float = 0.02,
    periods_per_year: int = 365 * 24,
) -> Tuple[float, float]:
    """
    计算夏普率
    
    Args:
        returns: 收益率列表
        risk_free_rate: 年化无风险利率
        periods_per_year: 每年周期数
        
    Returns:
        Tuple[sharpe_ratio, volatility]
    """
    if not returns:
        return 0.0, 0.0
    
    mean_return = sum(returns) / len(returns)
    variance = sum((r - mean_return) ** 2 for r in returns) / len(returns)
    volatility = math.sqrt(variance) if variance > 0 else 0.0
    
    # 年化
    annual_return = mean_return * periods_per_year
    annual_volatility = volatility * math.sqrt(periods_per_year)
    
    sharpe = (annual_return - risk_free_rate) / annual_volatility if annual_volatility > 0 else 0.0
    
    return sharpe, volatility


def calculate_max_drawdown(equity_curve: List[Decimal]) -> Tuple[Decimal, int, int]:
    """
    计算最大回撤
    
    Args:
        equity_curve: 权益曲线
        
    Returns:
        Tuple[max_drawdown, peak_index, trough_index]
        peak_index: 最大回撤开始时的峰值位置
        trough_index: 最大回撤的最低点位置
    """
    if not equity_curve:
        return Decimal("0"), -1, -1
    
    max_dd = Decimal("0")
    peak_idx = 0
    trough_idx = 0
    
    # 当前峰值（在最大回撤期间的峰值）
    current_peak = equity_curve[0]
    current_peak_idx = 0
    
    for i, equity in enumerate(equity_curve):
        if equity > current_peak:
            current_peak = equity
            current_peak_idx = i
        
        drawdown = current_peak - equity
        if drawdown > max_dd:
            max_dd = drawdown
            peak_idx = current_peak_idx  # 记录最大回撤对应的峰值位置
            trough_idx = i
    
    # 处理无回撤情况（权益曲线单调递增）
    if max_dd == Decimal("0"):
        # 找全局最大位置作为 peak_idx
        max_val = equity_curve[0]
        peak_idx = 0
        for i, equity in enumerate(equity_curve):
            if equity > max_val:
                max_val = equity
                peak_idx = i
        trough_idx = peak_idx
    
    return max_dd, peak_idx, trough_idx
