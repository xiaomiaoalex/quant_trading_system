"""
Backtesting Ports - 回测框架集成接口定义
=========================================
定义回测引擎、数据供给、结果汇报和策略适配的抽象接口。

支持框架：
- QuantConnect Lean (primary): 工业级回测引擎
- VectorBT (alternative): 快速原型验证

设计原则：
1. 协议独立于具体实现，可自由切换回测引擎
2. 使用鸭子类型实现灵活性
3. 类型安全与运行时检查兼顾

核心协议：
- BacktestEnginePort: 回测引擎驱动接口
- DataProviderPort: 历史数据供给接口
- ResultReporterPort: 回测报告存储接口
- StrategyAdapterPort: 策略格式转换接口
"""
from __future__ import annotations

from abc import ABC
from dataclasses import dataclass, field
from datetime import datetime, timezone
from decimal import Decimal
from enum import Enum
from typing import (
    Protocol,
    runtime_checkable,
    Optional,
    Dict,
    Any,
    Sequence,
    List,
)

from trader.core.domain.models.signal import Signal


class BacktestFeature(Enum):
    """回测引擎支持的特性"""
    PARAMETER_OPTIMIZATION = "PARAMETER_OPTIMIZATION"
    WALK_FORWARD = "WALK_FORWARD"
    MONTE_CARLO = "MONTE_CARLO"
    SECTOR_CONSTRAINTS = "SECTOR_CONSTRAINTS"
    MARGIN_MODELING = "MARGIN_MODELING"
    SLIPPAGE_MODEL = "SLIPPAGE_MODEL"
    COMMISSION_MODEL = "COMMISSION_MODEL"


class OptimizationMethod(Enum):
    """参数优化方法"""
    GRID_SEARCH = "GRID_SEARCH"
    RANDOM_SEARCH = "RANDOM_SEARCH"
    BAYESIAN = "BAYESIAN"
    CAGR_OPTIMIZED = "CAGR_OPTIMIZED"


@dataclass(slots=True)
class BacktestConfig:
    """
    回测配置
    
    属性：
        start_date: 回测开始日期
        end_date: 回测结束日期
        initial_capital: 初始资金
        symbol: 交易标的
        interval: K线周期
        benchmark: 基准回测标的（可选）
        commission_rate: 手续费率
        slippage_rate: 滑点率
        optimization_method: 优化方法（用于参数优化）
        optimization_params: 优化参数范围
    """
    start_date: datetime
    end_date: datetime
    initial_capital: Decimal
    symbol: str
    interval: str = "1h"
    benchmark: Optional[str] = None
    commission_rate: Decimal = Decimal("0.001")
    slippage_rate: Decimal = Decimal("0.0005")
    optimization_method: OptimizationMethod = OptimizationMethod.GRID_SEARCH
    optimization_params: Dict[str, Sequence[Any]] = field(default_factory=dict)

    def __post_init__(self):
        """类型安全转换"""
        if isinstance(self.initial_capital, (int, float)):
            object.__setattr__(self, 'initial_capital', Decimal(str(self.initial_capital)))
        if isinstance(self.commission_rate, (int, float)):
            object.__setattr__(self, 'commission_rate', Decimal(str(self.commission_rate)))
        if isinstance(self.slippage_rate, (int, float)):
            object.__setattr__(self, 'slippage_rate', Decimal(str(self.slippage_rate)))


@dataclass(slots=True)
class BacktestResult:
    """
    回测结果
    
    属性：
        total_return: 总收益率
        sharpe_ratio: 夏普比率
        max_drawdown: 最大回撤
        win_rate: 胜率
        profit_factor: 盈亏比
        num_trades: 交易次数
        final_capital: 最终资金
        equity_curve: 权益曲线数据点
        trades: 交易记录列表
        metrics: 扩展指标字典
    """
    total_return: Decimal
    sharpe_ratio: Decimal
    max_drawdown: Decimal
    win_rate: Decimal
    profit_factor: Decimal
    num_trades: int
    final_capital: Decimal
    equity_curve: Sequence[Dict[str, Any]] = field(default_factory=list)
    trades: Sequence[Dict[str, Any]] = field(default_factory=list)
    metrics: Dict[str, Any] = field(default_factory=dict)
    start_date: Optional[datetime] = None
    end_date: Optional[datetime] = None

    def __post_init__(self):
        """类型安全转换"""
        if isinstance(self.total_return, (int, float)):
            object.__setattr__(self, 'total_return', Decimal(str(self.total_return)))
        if isinstance(self.sharpe_ratio, (int, float)):
            object.__setattr__(self, 'sharpe_ratio', Decimal(str(self.sharpe_ratio)))
        if isinstance(self.max_drawdown, (int, float)):
            object.__setattr__(self, 'max_drawdown', Decimal(str(self.max_drawdown)))
        if isinstance(self.win_rate, (int, float)):
            object.__setattr__(self, 'win_rate', Decimal(str(self.win_rate)))
        if isinstance(self.profit_factor, (int, float)):
            object.__setattr__(self, 'profit_factor', Decimal(str(self.profit_factor)))


@dataclass(slots=True)
class OptimizationResult:
    """
    参数优化结果
    
    属性：
        best_params: 最优参数组合
        best_metrics: 最优指标
        all_results: 所有参数组合的结果
        optimization_time: 优化耗时（秒）
    """
    best_params: Dict[str, Any]
    best_metrics: BacktestResult
    all_results: Sequence[Dict[str, Any]] = field(default_factory=list)
    optimization_time: float = 0.0


@dataclass(slots=True)
class OHLCV:
    """
    OHLCV K线数据
    
    属性：
        timestamp: 时间戳
        open: 开盘价
        high: 最高价
        low: 最低价
        close: 收盘价
        volume: 成交量
    """
    timestamp: datetime
    open: Decimal
    high: Decimal
    low: Decimal
    close: Decimal
    volume: Decimal

    def __post_init__(self):
        """类型安全转换"""
        if isinstance(self.open, (int, float)):
            object.__setattr__(self, 'open', Decimal(str(self.open)))
        if isinstance(self.high, (int, float)):
            object.__setattr__(self, 'high', Decimal(str(self.high)))
        if isinstance(self.low, (int, float)):
            object.__setattr__(self, 'low', Decimal(str(self.low)))
        if isinstance(self.close, (int, float)):
            object.__setattr__(self, 'close', Decimal(str(self.close)))
        if isinstance(self.volume, (int, float)):
            object.__setattr__(self, 'volume', Decimal(str(self.volume)))


@dataclass(slots=True)
class BacktestReport:
    """
    回测报告
    
    属性：
        report_id: 报告唯一ID
        strategy_name: 策略名称
        config: 回测配置
        result: 回测结果
        created_at: 创建时间
        framework: 来源框架 (quantconnect/vectorbt)
        metadata: 扩展元数据
    """
    report_id: str
    strategy_name: str
    config: BacktestConfig
    result: BacktestResult
    created_at: datetime = field(default_factory=lambda: datetime.now(timezone.utc))
    framework: str = "quantconnect"
    metadata: Dict[str, Any] = field(default_factory=dict)


class FrameworkType(Enum):
    """支持的回测框架类型"""
    QUANTCONNECT_LEAN = "quantconnect_lean"
    VECTORBT = "vectorbt"


@runtime_checkable
class BacktestEnginePort(Protocol):
    """
    回测引擎端口
    
    定义与回测引擎交互的接口。
    支持 QuantConnect Lean 和 VectorBT 两种引擎。
    
    实现要求：
    1. run_backtest: 执行单次回测
    2. run_optimization: 执行参数优化
    3. get_supported_features: 查询支持的特性
    
    示例：
        class LeanEngineAdapter:
            @property
            def framework_type(self) -> FrameworkType:
                return FrameworkType.QUANTCONNECT_LEAN
            
            async def run_backtest(
                self,
                config: BacktestConfig,
                strategy: StrategyPlugin
            ) -> BacktestResult:
                ...
            
            def get_supported_features(self) -> List[BacktestFeature]:
                ...
    """
    
    @property
    def framework_type(self) -> FrameworkType:
        """框架类型"""
        ...
    
    def get_supported_features(self) -> List[BacktestFeature]:
        """
        获取支持的特性列表
        
        Returns:
            支持的 BacktestFeature 列表
        """
        ...
    
    async def run_backtest(
        self,
        config: BacktestConfig,
        strategy: Any,
    ) -> BacktestResult:
        """
        执行单次回测
        
        Args:
            config: 回测配置参数
            strategy: 策略实例（需实现 StrategyPlugin）
            
        Returns:
            BacktestResult: 回测结果
            
        Raises:
            BacktestError: 回测执行失败
        """
        ...
    
    async def run_optimization(
        self,
        config: BacktestConfig,
        strategy: Any,
        param_ranges: Dict[str, Sequence[Any]],
    ) -> OptimizationResult:
        """
        执行参数优化
        
        Args:
            config: 回测配置参数
            strategy: 策略实例
            param_ranges: 参数范围字典
            
        Returns:
            OptimizationResult: 优化结果
            
        Raises:
            BacktestError: 优化执行失败
        """
        ...


@runtime_checkable
class DataProviderPort(Protocol):
    """
    数据供给端口
    
    定义历史数据获取的接口。
    支持从数据库、文件系统或API获取K线数据。
    
    实现要求：
    1. get_klines: 获取OHLCV数据
    2. get_features: 获取预计算特征
    3. get_symbols: 获取可用交易标的
    
    示例：
        class FileDataProvider:
            async def get_klines(
                self,
                symbol: str,
                interval: str,
                start_date: datetime,
                end_date: datetime
            ) -> List[OHLCV]:
                ...
    """
    
    async def get_klines(
        self,
        symbol: str,
        interval: str,
        start_date: datetime,
        end_date: datetime,
    ) -> List[OHLCV]:
        """
        获取OHLCV K线数据
        
        Args:
            symbol: 交易标的 (如 BTCUSDT)
            interval: K线周期 (1m, 5m, 1h, 1d)
            start_date: 开始日期
            end_date: 结束日期
            
        Returns:
            List[OHLCV]: K线数据列表，按时间升序
        """
        ...
    
    async def get_features(
        self,
        symbol: str,
        feature_names: List[str],
        start_date: datetime,
        end_date: datetime,
    ) -> Dict[str, List[Any]]:
        """
        获取预计算特征
        
        Args:
            symbol: 交易标的
            feature_names: 特征名称列表
            start_date: 开始日期
            end_date: 结束日期
            
        Returns:
            Dict[str, List[Any]]: 特征名到特征值列表的映射
        """
        ...
    
    async def get_symbols(self) -> List[str]:
        """
        获取可用交易标的列表
        
        Returns:
            List[str]: 可用标的列表
        """
        ...


@runtime_checkable
class ResultReporterPort(Protocol):
    """
    结果汇报端口
    
    定义回测报告存储和检索的接口。
    
    实现要求：
    1. save_report: 保存回测报告
    2. get_report: 获取指定报告
    3. list_reports: 列出策略的报告
    
    示例：
        class PostgresResultReporter:
            async def save_report(self, report: BacktestReport) -> str:
                ...
            
            async def get_report(self, report_id: str) -> Optional[BacktestReport]:
                ...
    """
    
    async def save_report(self, report: BacktestReport) -> str:
        """
        保存回测报告
        
        Args:
            report: 回测报告
            
        Returns:
            str: 报告ID
        """
        ...
    
    async def get_report(self, report_id: str) -> Optional[BacktestReport]:
        """
        获取指定报告
        
        Args:
            report_id: 报告ID
            
        Returns:
            Optional[BacktestReport]: 报告，如果不存在则返回 None
        """
        ...
    
    async def list_reports(
        self,
        strategy_name: str,
        limit: int = 100,
    ) -> List[BacktestReport]:
        """
        列出策略的回测报告
        
        Args:
            strategy_name: 策略名称
            limit: 返回数量限制
            
        Returns:
            List[BacktestReport]: 报告列表，按创建时间降序
        """
        ...


@runtime_checkable
class StrategyAdapterPort(Protocol):
    """
    策略适配端口
    
    定义策略格式转换的接口。
    在不同回测框架间转换策略和信号格式。
    
    实现要求：
    1. convert_to_framework_format: 将 StrategyPlugin 转为框架特定格式
    2. convert_signals: 将框架信号转为内部 Signal 格式
    
    QuantConnect Lean 适配示例：
        class QuantConnectStrategyAdapter:
            def convert_to_framework_format(
                self,
                strategy: StrategyPlugin,
                config: Dict[str, Any]
            ) -> Any:
                # 转换为 QuantConnect Algorithm class
                ...
            
            def convert_signals(
                self,
                framework_signals: List[Any]
            ) -> List[Signal]:
                # 转换 Lean ExecutionReport 为 Signal
                ...
    
    VectorBT 适配示例：
        class VectorBTStrategyAdapter:
            def convert_to_framework_format(
                self,
                strategy: StrategyPlugin,
                config: Dict[str, Any]
            ) -> Any:
                # 转换为 VectorBT signals dict
                ...
            
            def convert_signals(
                self,
                framework_signals: Dict[str, Any]
            ) -> List[Signal]:
                # 转换 vbt.Entry谈起为 Signal
                ...
    """
    
    @property
    def target_framework(self) -> FrameworkType:
        """目标框架类型"""
        ...
    
    def convert_to_framework_format(
        self,
        strategy: Any,
        config: Dict[str, Any],
    ) -> Any:
        """
        将 StrategyPlugin 转换为框架特定格式
        
        Args:
            strategy: 策略实例（实现 StrategyPlugin）
            config: 框架特定配置
            
        Returns:
            Any: 框架特定格式的策略对象
        """
        ...
    
    def convert_signals(
        self,
        framework_signals: Any,
    ) -> List[Signal]:
        """
        将框架信号转换为内部 Signal 格式
        
        Args:
            framework_signals: 框架特定的信号格式
            
        Returns:
            List[Signal]: 内部 Signal 列表
        """
        ...
