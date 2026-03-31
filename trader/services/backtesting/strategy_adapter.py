"""
QuantConnect Lean Strategy Adapter - 策略适配器
===============================================

将内部 StrategyPlugin 转换为 QuantConnect Lean 算法格式，
并处理信号转换、指标映射等功能。

功能：
- 将 StrategyPlugin 转换为 QuantConnect Algorithm class
- 转换内部 Signal 为 Lean OrderSignal
- 映射技术指标到 Lean 指标
- 配置订单/Fill/佣金/滑点模型

QuantConnect Lean 引擎：
    https://github.com/QuantConnect/Lean

架构：
    StrategyPlugin -> QuantConnectStrategyWrapper -> Lean Algorithm
    Lean Insight -> SignalConverter -> Signal
"""
from __future__ import annotations

import logging
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
    runtime_checkable,
)

from trader.core.application.strategy_protocol import StrategyPlugin, MarketData
from trader.core.domain.models.signal import Signal, SignalType
from trader.core.domain.models.order import OrderSide, OrderType
from trader.services.backtesting.ports import StrategyAdapterPort, FrameworkType

logger = logging.getLogger(__name__)


class OrderSignal(Enum):
    """QuantConnect Lean 订单信号"""
    BUY = "buy"
    SELL = "sell"
    HOLD = "hold"
    BUY_LIMIT = "buy_limit"
    SELL_LIMIT = "sell_limit"
    BUY_MARKET = "buy_market"
    SELL_MARKET = "sell_market"


class IndicatorType(Enum):
    """支持的技术指标类型"""
    SMA = "sma"
    EMA = "ema"
    RSI = "rsi"
    MACD = "macd"
    BOLLINGER_BANDS = "bollinger_bands"
    ATR = "atr"
    Stochastic = "stochastic"
    VWAP = "vwap"


class OrderModel(Enum):
    """订单模型"""
    MARKET = "market"
    LIMIT = "limit"
    STOP = "stop"
    STOP_LIMIT = "stop_limit"


class FillModel(Enum):
    """成交模型"""
    IMMEDIATE = "immediate"
    NEXT_BAR = "next_bar"
    PARTIAL_FILL = "partial_fill"


class CommissionModel(Enum):
    """佣金模型"""
    COINBASE = "coinbase"
    GDAX = "gdax"
    BITMEX = "bitmex"
    KRINGLE = "kringle"
    CUSTOM = "custom"


class SlippageModel(Enum):
    """滑点模型"""
    NO_SLIPPAGE = "no_slippage"
    VOLUME_SLIPPAGE = "volume_slippage"
    PRICE_SLIPPAGE = "price_slippage"
    CUSTOM = "custom"


class ConversionError(Exception):
    """信号转换错误"""
    pass


class InvalidSignalError(ConversionError):
    """无效信号错误"""
    pass


class FrameworkError(Exception):
    """框架特定错误"""
    pass


@dataclass(slots=True)
class IndicatorConfig:
    """指标配置"""
    indicator_type: IndicatorType
    symbol: str
    period: int = 14
    parameters: Dict[str, Any] = field(default_factory=dict)


@dataclass(slots=True)
class StrategyAdapterConfig:
    """策略适配器配置"""
    order_model: OrderModel = OrderModel.MARKET
    fill_model: FillModel = FillModel.IMMEDIATE
    commission_model: CommissionModel = CommissionModel.COINBASE
    slippage_model: SlippageModel = SlippageModel.NO_SLIPPAGE
    custom_commission: float = 0.0
    custom_slippage_bps: float = 0.0
    leverage: int = 1
    indicators: List[IndicatorConfig] = field(default_factory=list)


@dataclass(slots=True)
class LeanInsight:
    """QuantConnect Lean Insight 表示"""
    symbol: str
    type: str
    direction: str
    confidence: float
    period: int
    weight: float


@dataclass(slots=True)
class LeanAlgorithmSpec:
    """Lean 算法规格表示"""
    class_name: str
    source_code: str
    indicators: List[IndicatorConfig]
    symbols: List[str]
    cash: float
    start_date: str
    end_date: str
    resolution: str


class SignalConverter:
    """
    信号转换器
    
    将内部 Signal 转换为 QuantConnect Lean OrderSignal
    """

    def __init__(self, config: StrategyAdapterConfig):
        self._config = config

    def convert_to_order_signal(self, signal: Signal) -> OrderSignal:
        """
        将内部 Signal 转换为 Lean OrderSignal
        
        Args:
            signal: 内部信号
            
        Returns:
            OrderSignal: Lean 订单信号
            
        Raises:
            InvalidSignalError: 无效信号
        """
        if signal.signal_type == SignalType.NONE:
            return OrderSignal.HOLD
        
        if signal.is_buy_signal():
            if self._config.order_model == OrderModel.LIMIT:
                return OrderSignal.BUY_LIMIT
            return OrderSignal.BUY_MARKET
        
        if signal.is_sell_signal():
            if self._config.order_model == OrderModel.LIMIT:
                return OrderSignal.SELL_LIMIT
            return OrderSignal.SELL_MARKET
        
        return OrderSignal.HOLD

    def convert_to_direction(self, signal: Signal) -> str:
        """
        转换信号方向
        
        Returns:
            "up", "down", or "flat"
        """
        if signal.is_buy_signal():
            return "up"
        if signal.is_sell_signal():
            return "down"
        return "flat"

    def convert_from_lean_insight(self, insight: LeanInsight) -> Signal:
        """
        从 Lean Insight 转换为内部 Signal
        
        Args:
            insight: Lean Insight 对象
            
        Returns:
            Signal: 内部信号
        """
        signal_type_map = {
            "up": SignalType.BUY,
            "down": SignalType.SELL,
            "flat": SignalType.NONE,
        }
        
        signal_type = signal_type_map.get(insight.direction.lower(), SignalType.NONE)
        
        return Signal(
            signal_type=signal_type,
            symbol=insight.symbol,
            confidence=Decimal(str(insight.confidence)),
            reason=f"LeanInsight:{insight.type}",
            metadata={
                "insight_type": insight.type,
                "insight_period": insight.period,
                "insight_weight": insight.weight,
            }
        )

    def convert_from_lean_signals(self, lean_signals: List[LeanInsight]) -> List[Signal]:
        """
        批量转换 Lean Insight 为内部 Signal
        
        Args:
            lean_signals: Lean Insight 列表
            
        Returns:
            List[Signal]: 内部信号列表
        """
        return [self.convert_from_lean_insight(s) for s in lean_signals]


class IndicatorMapper:
    """
    指标映射器
    
    将内部指标映射到 QuantConnect 技术指标
    """

    def __init__(self):
        self._indicator_registry: Dict[str, IndicatorType] = {
            "sma": IndicatorType.SMA,
            "simple_moving_average": IndicatorType.SMA,
            "ema": IndicatorType.EMA,
            "exponential_moving_average": IndicatorType.EMA,
            "rsi": IndicatorType.RSI,
            "relative_strength_index": IndicatorType.RSI,
            "macd": IndicatorType.MACD,
            "moving_average_convergence_divergence": IndicatorType.MACD,
            "bb": IndicatorType.BOLLINGER_BANDS,
            "bollinger_bands": IndicatorType.BOLLINGER_BANDS,
            "atr": IndicatorType.ATR,
            "average_true_range": IndicatorType.ATR,
            "stoch": IndicatorType.Stochastic,
            "stochastic": IndicatorType.Stochastic,
            "vwap": IndicatorType.VWAP,
            "volume_weighted_average_price": IndicatorType.VWAP,
        }

    def register_indicator(self, name: str, indicator_type: IndicatorType) -> None:
        """注册自定义指标映射"""
        self._indicator_registry[name.lower()] = indicator_type

    def resolve_indicator(self, name: str) -> IndicatorType:
        """
        解析指标名称为 IndicatorType
        
        Args:
            name: 指标名称
            
        Returns:
            IndicatorType: 指标类型
        """
        normalized = name.lower()
        if normalized not in self._indicator_registry:
            raise ValueError(f"Unknown indicator: {name}. Supported: {list(self._indicator_registry.keys())}")
        return self._indicator_registry[normalized]

    def get_lean_indicator_code(self, config: IndicatorConfig) -> str:
        """
        生成 Lean 指标代码
        
        Args:
            config: 指标配置
            
        Returns:
            str: Lean C# 指标代码片段
        """
        indicator_type = config.indicator_type
        symbol = config.symbol
        period = config.period
        params = config.parameters

        if indicator_type == IndicatorType.SMA:
            return f"SMAModel({symbol}, {period})"
        elif indicator_type == IndicatorType.EMA:
            return f"EMAModel({symbol}, {period})"
        elif indicator_type == IndicatorType.RSI:
            return f"RSI({symbol}, {period})"
        elif indicator_type == IndicatorType.MACD:
            fast = params.get("fast", 12)
            slow = params.get("slow", 26)
            signal = params.get("signal", 9)
            return f"MACD({symbol}, {fast}, {slow}, {signal})"
        elif indicator_type == IndicatorType.BOLLINGER_BANDS:
            period = params.get("period", 20)
            std = params.get("std", 2)
            return f"BollingerBands({symbol}, {period}, {std})"
        elif indicator_type == IndicatorType.ATR:
            return f"ATR({symbol}, {period})"
        elif indicator_type == IndicatorType.Stochastic:
            return f"Stochastic({symbol}, {period})"
        elif indicator_type == IndicatorType.VWAP:
            return f"VWAP({symbol})"
        else:
            raise ValueError(f"Unsupported indicator type: {indicator_type}")

    def build_indicator_map(self, configs: List[IndicatorConfig]) -> Dict[str, str]:
        """
        构建指标名称到 Lean 代码的映射
        
        Args:
            configs: 指标配置列表
            
        Returns:
            Dict[str, str]: 指标名到代码的映射
        """
        result = {}
        for config in configs:
            key = f"{config.indicator_type.value}_{config.symbol}_{config.period}"
            result[key] = self.get_lean_indicator_code(config)
        return result


class QuantConnectStrategyWrapper:
    """
    QuantConnect 策略包装器
    
    包装内部 StrategyPlugin 用于 QuantConnect Lean 回测
    """

    def __init__(
        self,
        strategy: StrategyPlugin,
        config: StrategyAdapterConfig,
        cash: float = 10000.0,
    ):
        self._strategy = strategy
        self._config = config
        self._cash = cash
        self._signal_converter = SignalConverter(config)
        self._indicator_mapper = IndicatorMapper()
        self._initialized = False

    @property
    def strategy(self) -> StrategyPlugin:
        """获取原始策略"""
        return self._strategy

    @property
    def config(self) -> StrategyAdapterConfig:
        """获取配置"""
        return self._config

    def set_cash(self, cash: float) -> None:
        """设置初始资金"""
        self._cash = cash

    async def initialize(self) -> None:
        """初始化包装器"""
        if self._initialized:
            return
        await self._strategy.initialize({})
        self._initialized = True

    async def shutdown(self) -> None:
        """关闭清理"""
        await self._strategy.shutdown()
        self._initialized = False

    async def process_market_data(self, data: MarketData) -> Optional[Signal]:
        """
        处理市场数据并生成信号
        
        Args:
            data: 市场数据
            
        Returns:
            Optional[Signal]: 交易信号
        """
        return await self._strategy.on_market_data(data)

    def convert_signal_to_lean(self, signal: Signal) -> LeanInsight:
        """
        将内部 Signal 转换为 Lean Insight
        
        Args:
            signal: 内部信号
            
        Returns:
            LeanInsight: Lean Insight
        """
        direction = self._signal_converter.convert_to_direction(signal)
        
        return LeanInsight(
            symbol=signal.symbol,
            type="price",
            direction=direction,
            confidence=float(signal.confidence),
            period=60,
            weight=float(signal.quantity) if signal.quantity else 1.0,
        )

    def build_algorithm_spec(
        self,
        symbols: List[str],
        start_date: str,
        end_date: str,
        resolution: str = "Daily",
    ) -> LeanAlgorithmSpec:
        """
        构建 Lean 算法规格
        
        Args:
            symbols: 交易标的列表
            start_date: 开始日期
            end_date: 结束日期
            resolution: K线分辨率
            
        Returns:
            LeanAlgorithmSpec: Lean 算法规格
        """
        class_name = f"{self._strategy.name}Algorithm"
        
        source_code = self._generate_algorithm_code(symbols, resolution, start_date, end_date)
        
        return LeanAlgorithmSpec(
            class_name=class_name,
            source_code=source_code,
            indicators=self._config.indicators,
            symbols=symbols,
            cash=self._cash,
            start_date=start_date,
            end_date=end_date,
            resolution=resolution,
        )

    def _generate_algorithm_code(
        self,
        symbols: List[str],
        resolution: str,
        start_date: str,
        end_date: str,
    ) -> str:
        """生成 Lean C# 算法代码"""
        symbol_list = ", ".join(f'"{s}"' for s in symbols)
        
        indicator_inits = []
        for config in self._config.indicators:
            indicator_code = self._indicator_mapper.get_lean_indicator_code(config)
            var_name = f"{config.indicator_type.value}_{config.symbol}"
            indicator_inits.append(f"private {indicator_code} {var_name};")
        
        indicator_init_block = "\n            ".join(indicator_inits) if indicator_inits else " // No indicators"
        
        return f"""
using QuantConnect;
using QuantConnect.Algorithm;
using QuantConnect.Indicators;
using QuantConnect.Data;

namespace QuantConnect.Algorithms
{{
    public class {self._strategy.name}Algorithm : QCAlgorithm
    {{
{indicator_init_block}

        public override void Initialize()
        {{
            SetStartDate({start_date});
            SetEndDate({end_date});
            SetCash({self._cash});

            foreach (var symbol in new[] {{ {symbol_list} }})
            {{
                AddCrypto(symbol, Resolution.{resolution});
            }}

            // Initialize indicators
{self._generate_indicator_initialization()}
        }}

        public override void OnData(Slice data)
        {{
            // Trading logic will be injected here
        }}
    }}
}}
"""

    def _generate_indicator_initialization(self) -> str:
        """生成指标初始化代码"""
        lines = []
        for config in self._config.indicators:
            var_name = f"{config.indicator_type.value}_{config.symbol}"
            if config.indicator_type == IndicatorType.MACD:
                fast = config.parameters.get("fast", 12)
                slow = config.parameters.get("slow", 26)
                signal = config.parameters.get("signal", 9)
                lines.append(f"            {var_name} = MACD({config.symbol}, {fast}, {slow}, {signal}, MovingAverageType.Exponential);")
            elif config.indicator_type == IndicatorType.BOLLINGER_BANDS:
                period = config.parameters.get("period", 20)
                std = config.parameters.get("std", 2)
                lines.append(f"            {var_name} = BB({config.symbol}, {period}, {std}, MovingAverageType.Simple);")
            else:
                lines.append(f"            {var_name} = {self._indicator_mapper.get_lean_indicator_code(config)};")
        return "\n".join(lines) if lines else "            // No indicators to initialize"


class QuantConnectStrategyAdapter:
    """
    QuantConnect Lean 策略适配器
    
    实现 StrategyAdapterPort 协议，
    提供 StrategyPlugin 与 QuantConnect Lean 之间的格式转换
    """

    def __init__(self, config: Optional[StrategyAdapterConfig] = None):
        self._config = config or StrategyAdapterConfig()
        self._wrappers: Dict[str, QuantConnectStrategyWrapper] = {}

    @property
    def target_framework(self) -> FrameworkType:
        """目标框架类型"""
        return FrameworkType.QUANTCONNECT_LEAN

    def convert_to_framework_format(
        self,
        strategy: Any,
        config: Dict[str, Any],
    ) -> LeanAlgorithmSpec:
        """
        将 StrategyPlugin 转换为 QuantConnect Lean 算法格式
        
        Args:
            strategy: StrategyPlugin 实例
            config: 框架特定配置
            
        Returns:
            LeanAlgorithmSpec: Lean 算法规格
            
        Raises:
            ConversionError: 转换失败
        """
        if not isinstance(strategy, StrategyPlugin):
            raise ConversionError(f"Strategy must implement StrategyPlugin protocol, got {type(strategy)}")
        
        adapter_config = self._parse_config(config)
        cash = config.get("cash", 10000.0)
        
        wrapper = QuantConnectStrategyWrapper(
            strategy=strategy,
            config=adapter_config,
            cash=cash,
        )
        
        symbols = config.get("symbols", ["BTCUSDT"])
        start_date = config.get("start_date", "20240101")
        end_date = config.get("end_date", "20241231")
        resolution = config.get("resolution", "Daily")
        
        self._wrappers[strategy.name] = wrapper
        
        return wrapper.build_algorithm_spec(symbols, start_date, end_date, resolution)

    def convert_signals(
        self,
        framework_signals: Any,
    ) -> List[Signal]:
        """
        将 QuantConnect Lean 信号转换为内部 Signal 格式
        
        Args:
            framework_signals: Lean 信号格式
            
        Returns:
            List[Signal]: 内部 Signal 列表
            
        Raises:
            ConversionError: 转换失败
        """
        if isinstance(framework_signals, list):
            if all(isinstance(s, LeanInsight) for s in framework_signals):
                converter = SignalConverter(self._config)
                return converter.convert_from_lean_signals(framework_signals)
            if all(isinstance(s, dict) for s in framework_signals):
                return self._convert_dict_signals(framework_signals)
        
        if isinstance(framework_signals, dict):
            return self._convert_dict_signals([framework_signals])
        
        raise ConversionError(f"Unsupported signal format: {type(framework_signals)}")

    def _convert_dict_signals(self, signals: List[Dict[str, Any]]) -> List[Signal]:
        """从字典列表转换信号"""
        result = []
        for sig in signals:
            direction = sig.get("direction", "flat").lower()
            symbol = sig.get("symbol", "")
            confidence = float(sig.get("confidence", 1.0))
            
            signal_type_map = {
                "up": SignalType.BUY,
                "down": SignalType.SELL,
                "flat": SignalType.NONE,
            }
            signal_type = signal_type_map.get(direction, SignalType.NONE)
            
            result.append(Signal(
                signal_type=signal_type,
                symbol=symbol,
                confidence=Decimal(str(confidence)),
                reason="LeanDictSignal",
                metadata=sig,
            ))
        return result

    def _parse_config(self, config: Dict[str, Any]) -> StrategyAdapterConfig:
        """解析配置字典"""
        order_model = OrderModel(config.get("order_model", "market"))
        fill_model = FillModel(config.get("fill_model", "immediate"))
        commission_model = CommissionModel(config.get("commission_model", "coinbase"))
        slippage_model = SlippageModel(config.get("slippage_model", "no_slippage"))
        
        indicators = []
        for ind_config in config.get("indicators", []):
            ind_type = IndicatorType(ind_config.get("type", "sma"))
            ind_symbol = ind_config.get("symbol", "BTCUSDT")
            ind_period = ind_config.get("period", 14)
            ind_params = ind_config.get("parameters", {})
            indicators.append(IndicatorConfig(
                indicator_type=ind_type,
                symbol=ind_symbol,
                period=ind_period,
                parameters=ind_params,
            ))
        
        return StrategyAdapterConfig(
            order_model=order_model,
            fill_model=fill_model,
            commission_model=commission_model,
            slippage_model=slippage_model,
            custom_commission=config.get("custom_commission", 0.0),
            custom_slippage_bps=config.get("custom_slippage_bps", 0.0),
            leverage=config.get("leverage", 1),
            indicators=indicators,
        )

    def get_wrapper(self, strategy_name: str) -> Optional[QuantConnectStrategyWrapper]:
        """获取策略包装器"""
        return self._wrappers.get(strategy_name)

    def list_wrappers(self) -> List[str]:
        """列出所有已注册的策略名称"""
        return list(self._wrappers.keys())
