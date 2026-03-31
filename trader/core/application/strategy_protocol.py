"""
StrategyPlugin Protocol - 策略插件协议定义
============================================
所有策略必须实现的统一协议，是AI生成代码、StrategyRunner加载、热插拔切换的基础。

核心类型：
- MarketData: 市场数据输入
- Signal: 信号输出（已定义于 domain/models/signal.py）
- StrategyPlugin: 策略插件接口
- StrategyResourceLimits: 资源限制配置
- ValidationResult: 策略有效性验证结果

使用方式：
1. AI生成策略时引用此协议实现 StrategyPlugin 接口
2. StrategyRunner 加载策略时验证协议合规性
3. 热插拔切换时检查资源限制和风控等级

设计原则：
- 协议独立于执行器，可被AI引用
- risk_level 属性集成风控
- 资源限制可配置
"""
from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from datetime import datetime, timezone
from decimal import Decimal
from enum import Enum
from typing import Protocol, runtime_checkable, Optional, Dict, Any, Sequence

from trader.core.application.risk_engine import RiskLevel
from trader.core.domain.models.signal import Signal, SignalType


class MarketDataType(Enum):
    """市场数据类型"""
    TRADE = "TRADE"                 # 成交数据
    KLINE = "KLINE"                 # K线数据
    DEPTH = "DEPTH"                 # 订单簿深度
    TICKER = "TICKER"               #  ticker行情
    FUNDING = "FUNDING"             # 资金费率
    INDEX_PRICE = "INDEX_PRICE"     # 指数价格
    MARK_PRICE = "MARK_PRICE"       # 标记价格


@dataclass(slots=True)
class MarketData:
    """
    市场数据输入
    
    策略接收到的市场信息，用于生成交易信号。
    所有字段均为只读，策略不应修改原始数据。
    
    属性：
        symbol: 交易标的（如 BTCUSDT）
        data_type: 数据类型
        price: 当前价格
        volume: 成交量
        timestamp: 数据时间戳
        kline_open/kline_high/kline_low/kline_close: K线数据（可选）
        bid/ask: 买卖盘价格（可选）
        bid_volume/ask_volume: 买卖盘量（可选）
        metadata: 扩展元数据
    """
    symbol: str
    data_type: MarketDataType
    price: Decimal
    volume: Decimal = Decimal("0")
    timestamp: datetime = field(default_factory=lambda: datetime.now(timezone.utc))
    
    # K线数据（当 data_type == KLINE 时）
    kline_open: Optional[Decimal] = None
    kline_high: Optional[Decimal] = None
    kline_low: Optional[Decimal] = None
    kline_close: Optional[Decimal] = None
    kline_interval: Optional[str] = None
    
    # 订单簿数据（当 data_type == DEPTH 时）
    bid: Optional[Decimal] = None
    ask: Optional[Decimal] = None
    bid_volume: Optional[Decimal] = None
    ask_volume: Optional[Decimal] = None
    
    # 指数/标记价格（当 data_type 为 INDEX_PRICE/MARK_PRICE 时）
    index_price: Optional[Decimal] = None
    mark_price: Optional[Decimal] = None
    
    # 扩展元数据
    metadata: Dict[str, Any] = field(default_factory=dict)
    
    def __post_init__(self):
        """类型安全转换"""
        if isinstance(self.price, (int, float)):
            object.__setattr__(self, 'price', Decimal(str(self.price)))
        if isinstance(self.volume, (int, float)):
            object.__setattr__(self, 'volume', Decimal(str(self.volume)))
        if self.kline_open and isinstance(self.kline_open, (int, float)):
            object.__setattr__(self, 'kline_open', Decimal(str(self.kline_open)))
        if self.kline_high and isinstance(self.kline_high, (int, float)):
            object.__setattr__(self, 'kline_high', Decimal(str(self.kline_high)))
        if self.kline_low and isinstance(self.kline_low, (int, float)):
            object.__setattr__(self, 'kline_low', Decimal(str(self.kline_low)))
        if self.kline_close and isinstance(self.kline_close, (int, float)):
            object.__setattr__(self, 'kline_close', Decimal(str(self.kline_close)))
        if self.bid and isinstance(self.bid, (int, float)):
            object.__setattr__(self, 'bid', Decimal(str(self.bid)))
        if self.ask and isinstance(self.ask, (int, float)):
            object.__setattr__(self, 'ask', Decimal(str(self.ask)))
        if self.bid_volume and isinstance(self.bid_volume, (int, float)):
            object.__setattr__(self, 'bid_volume', Decimal(str(self.bid_volume)))
        if self.ask_volume and isinstance(self.ask_volume, (int, float)):
            object.__setattr__(self, 'ask_volume', Decimal(str(self.ask_volume)))
        if self.index_price and isinstance(self.index_price, (int, float)):
            object.__setattr__(self, 'index_price', Decimal(str(self.index_price)))
        if self.mark_price and isinstance(self.mark_price, (int, float)):
            object.__setattr__(self, 'mark_price', Decimal(str(self.mark_price)))
    
    @property
    def spread(self) -> Optional[Decimal]:
        """计算买卖价差"""
        if self.bid and self.ask:
            return self.ask - self.bid
        return None
    
    @property
    def spread_percent(self) -> Optional[Decimal]:
        """计算买卖价差百分比"""
        if self.bid and self.ask and self.bid > 0:
            return (self.ask - self.bid) / self.bid * Decimal("100")
        return None


@dataclass(slots=True)
class StrategyResourceLimits:
    """
    策略资源限制配置
    
    限制策略的资源使用，防止过度交易或超出风险限额。
    这些限制由 StrategyRunner 在信号生成后应用。
    
    属性：
        max_position_size: 最大持仓数量（以标的数量计）
        max_daily_loss: 最大日亏损金额
        max_orders_per_minute: 最大每分钟订单数
        timeout_seconds: 策略执行超时时间（秒）
    """
    max_position_size: Decimal = Decimal("1.0")
    max_daily_loss: Decimal = Decimal("100.0")
    max_orders_per_minute: int = 10
    timeout_seconds: float = 5.0
    
    def __post_init__(self):
        """类型安全转换"""
        if isinstance(self.max_position_size, (int, float)):
            object.__setattr__(self, 'max_position_size', Decimal(str(self.max_position_size)))
        if isinstance(self.max_daily_loss, (int, float)):
            object.__setattr__(self, 'max_daily_loss', Decimal(str(self.max_daily_loss)))
        if not isinstance(self.max_orders_per_minute, int):
            object.__setattr__(self, 'max_orders_per_minute', int(self.max_orders_per_minute))
        if not isinstance(self.timeout_seconds, (int, float)):
            object.__setattr__(self, 'timeout_seconds', float(self.timeout_seconds))


class ValidationStatus(Enum):
    """验证状态"""
    VALID = "VALID"
    INVALID = "INVALID"
    WARNING = "WARNING"  # 警告但不阻断


@dataclass(slots=True)
class ValidationError:
    """验证错误"""
    field: str
    message: str
    code: str


@dataclass(slots=True)
class ValidationResult:
    """
    策略有效性验证结果
    
    属性：
        status: 验证状态
        errors: 错误列表（如果 status != VALID）
        warnings: 警告列表（如果 status == WARNING）
        metadata: 扩展元数据
    """
    status: ValidationStatus
    errors: Sequence[ValidationError] = field(default_factory=list)
    warnings: Sequence[str] = field(default_factory=list)
    metadata: Dict[str, Any] = field(default_factory=dict)
    
    @property
    def is_valid(self) -> bool:
        """验证是否通过"""
        return self.status == ValidationStatus.VALID
    
    @property
    def is_warning(self) -> bool:
        """是否有警告"""
        return self.status == ValidationStatus.WARNING
    
    @property
    def has_errors(self) -> bool:
        """是否有错误"""
        return self.status == ValidationStatus.INVALID or len(self.errors) > 0
    
    @classmethod
    def valid(cls, metadata: Optional[Dict[str, Any]] = None) -> ValidationResult:
        """创建有效验证结果"""
        return cls(status=ValidationStatus.VALID, metadata=metadata or {})
    
    @classmethod
    def invalid(cls, errors: Sequence[ValidationError], 
                metadata: Optional[Dict[str, Any]] = None) -> ValidationResult:
        """创建无效验证结果"""
        return cls(
            status=ValidationStatus.INVALID,
            errors=errors,
            metadata=metadata or {}
        )
    
    @classmethod
    def with_warnings(cls, warnings: Sequence[str],
                      metadata: Optional[Dict[str, Any]] = None) -> ValidationResult:
        """创建带警告的验证结果"""
        return cls(
            status=ValidationStatus.WARNING,
            warnings=warnings,
            metadata=metadata or {}
        )


@runtime_checkable
class StrategyPlugin(Protocol):
    """
    策略插件接口
    
    所有策略必须实现此协议。
    StrategyRunner 通过此接口与策略交互。
    
    实现要求：
    1. name: 策略名称，应全局唯一
    2. version: 策略版本号，格式为 semver
    3. risk_level: 策略风险等级，影响风控决策
    4. resource_limits: 资源限制配置
    5. on_market_data: 市场数据回调，返回交易信号
    6. validate: 策略有效性验证
    
    示例：
        @dataclass
        class MyStrategy:
            name: str = "MyStrategy"
            version: str = "1.0.0"
            risk_level: RiskLevel = RiskLevel.LOW
            resource_limits: StrategyResourceLimits = field(
                default_factory=lambda: StrategyResourceLimits()
            )
            
            async def on_market_data(self, data: MarketData) -> Optional[Signal]:
                # 策略逻辑
                ...
            
            def validate(self) -> ValidationResult:
                # 验证逻辑
                ...
    """
    
    @property
    def name(self) -> str:
        """策略名称"""
        ...
    
    @property
    def version(self) -> str:
        """策略版本（semver格式）"""
        ...
    
    @version.setter
    def version(self, value: str) -> None:
        """设置策略版本（用于热插拔）"""
        ...
    
    @property
    def risk_level(self) -> RiskLevel:
        """策略风险等级"""
        ...
    
    @property
    def resource_limits(self) -> StrategyResourceLimits:
        """资源限制配置"""
        ...
    
    async def on_market_data(self, data: MarketData) -> Signal | None:
        """
        市场数据回调
        
        Args:
            data: 市场数据输入
            
        Returns:
            交易信号，如果当前不适合交易则返回 None
        """
        ...
    
    async def on_fill(self, order_id: str, symbol: str, side: str, quantity: float, price: float) -> None:
        """
        订单成交回调
        
        Args:
            order_id: 订单ID
            symbol: 交易对
            side: 买卖方向
            quantity: 成交数量
            price: 成交价格
        """
        ...
    
    async def on_cancel(self, order_id: str, reason: str) -> None:
        """
        订单取消回调
        
        Args:
            order_id: 订单ID
            reason: 取消原因
        """
        ...
    
    async def initialize(self, config: Dict[str, Any]) -> None:
        """
        策略初始化
        
        Args:
            config: 策略配置参数
        """
        ...
    
    async def shutdown(self) -> None:
        """策略关闭清理"""
        ...
    
    async def update_config(self, config: Dict[str, Any]) -> ValidationResult:
        """
        更新策略配置参数
        
        允许在策略运行期间动态调整参数，无需停止策略。
        参数变更后应调用 validate() 确保配置有效。
        
        Args:
            config: 新的配置参数（部分更新，支持增量更新）
            
        Returns:
            ValidationResult: 更新后的验证结果
        """
        ...
    
    def validate(self) -> ValidationResult:
        """
        策略有效性验证
        
        在策略加载和参数更新时调用。
        应检查：
        - 必需参数是否已设置
        - 参数值是否在有效范围内
        - 策略逻辑是否完整
        
        Returns:
            验证结果
        """
        ...


def validate_strategy_plugin(plugin: Any) -> tuple[bool, str]:
    """
    验证插件是否实现了 StrategyPlugin 协议。
    
    由于 Python 的 @runtime_checkable 与 async 方法不完全兼容，
    此函数手动检查必需的属性和方法。
    
    Args:
        plugin: 待验证的插件对象
        
    Returns:
        (is_valid, error_message) 元组
    """
    # 检查必需的属性
    required_properties = ['name', 'version', 'risk_level', 'resource_limits']
    for prop in required_properties:
        if not hasattr(plugin, prop):
            return False, f"插件缺少必需属性: {prop}"
    
    # 检查必需的方法（包括 async 和 sync 方法）
    required_methods = [
        'initialize', 'on_market_data', 'on_fill', 'on_cancel', 'shutdown', 'validate',
        'update_config'
    ]
    for method in required_methods:
        if not hasattr(plugin, method):
            return False, f"插件缺少必需方法: {method}"
    
    # 验证方法是否是可调用的
    for method in required_methods:
        if not callable(getattr(plugin, method, None)):
            return False, f"{method} 必须是可调用的方法"
    
    return True, ""
