"""
EscapeTime Simulator - 平仓时间模拟器
=====================================
用于估算最快平仓时间的领域服务。

核心功能：
1. KillSwitch 检查：L2/L3 级别阻止平仓
2. 冷却期检查：上次交易冷却中则延迟
3. 深度模拟：基于订单簿深度计算分批卖出滑点
4. Regime 折扣：根据市场状态调整可交易量
5. 时间估算：考虑订单执行时间和市场冲击

重要约束：
- Core Plane 禁止 IO
- 纯计算逻辑
- 所有计算使用 Decimal
- Fail-Closed 异常处理
"""
from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timezone, timedelta
from decimal import Decimal
from enum import IntEnum
from typing import Optional, List, Protocol, TYPE_CHECKING

from trader.core.domain.models.position import Position
from trader.core.domain.models.orderbook import OrderBook, OrderBookLevel
from trader.core.domain.services.position_risk_constructor import (
    MarketRegime,
    PositionRiskConstructorConfig,
)


if TYPE_CHECKING:
    pass


class KillSwitchLevel(IntEnum):
    """KillSwitch 级别（与 .traerules 规范一致）"""
    L0_NORMAL = 0                        # 正常交易
    L1_NO_NEW_POSITIONS = 1               # 禁止新开仓，允许平仓
    L2_CANCEL_ALL_AND_HALT = 2            # 取消所有订单并暂停交易
    L3_LIQUIDATE_AND_DISCONNECT = 3       # 平仓并断开连接


@dataclass(frozen=True)
class DepthLevel:
    """
    订单簿深度级别（不可变）
    
    表示在特定价格范围内的可交易量。
    
    Attributes:
        price: 价位
        cumulative_quantity: 累计可交易量
        level_index: 档位索引
    """
    price: Decimal
    cumulative_quantity: Decimal
    level_index: int


@dataclass
class EscapeTimeResult:
    """
    平仓时间估算结果
    
    Attributes:
        estimated_seconds: 估算的平仓时间（秒）
        max_slippage_bps: 最大滑点（基点）
        can_escape: 是否可以平仓
        blocking_factors: 阻塞因素列表
        escape_path: 平仓路径（按深度级别）
    """
    estimated_seconds: int
    max_slippage_bps: int
    can_escape: bool
    blocking_factors: List[str] = field(default_factory=list)
    escape_path: List[DepthLevel] = field(default_factory=list)
    
    @classmethod
    def blocked(
        cls,
        blocking_factors: List[str],
        estimated_seconds: int = 0
    ) -> "EscapeTimeResult":
        """创建被阻止的结果"""
        return cls(
            estimated_seconds=estimated_seconds,
            max_slippage_bps=0,
            can_escape=False,
            blocking_factors=blocking_factors,
            escape_path=[]
        )
    
    @classmethod
    def can_escape_result(
        cls,
        estimated_seconds: int,
        max_slippage_bps: int,
        escape_path: List[DepthLevel]
    ) -> "EscapeTimeResult":
        """创建可以平仓的结果"""
        return cls(
            estimated_seconds=estimated_seconds,
            max_slippage_bps=max_slippage_bps,
            can_escape=True,
            blocking_factors=[],
            escape_path=escape_path
        )


class KillSwitchProviderPort(Protocol):
    """KillSwitch 提供者端口协议"""
    
    def get_killswitch_level(self, scope: str) -> KillSwitchLevel:
        """
        获取 KillSwitch 级别
        
        Args:
            scope: 范围 (如 "GLOBAL", "BTC", "ETH")
            
        Returns:
            KillSwitchLevel: 当前级别
        """
        ...


class CooldownProviderPort(Protocol):
    """冷却期提供者端口协议"""
    
    def get_last_trade_time(self, symbol: str) -> Optional[datetime]:
        """
        获取上次交易时间
        
        Args:
            symbol: 交易标的
            
        Returns:
            datetime: 上次交易时间，不存在返回 None
        """
        ...


class EscapeTimeSimulatorConfig:
    """EscapeTime 模拟器配置"""
    
    def __init__(
        self,
        order_execution_time_seconds: int = 5,
        market_impact_coefficient_bps: Decimal = Decimal("1.0"),
        insufficient_depth_penalty_seconds: int = 30,
    ):
        if order_execution_time_seconds <= 0:
            raise ValueError("order_execution_time_seconds must be positive")
        if insufficient_depth_penalty_seconds < 0:
            raise ValueError("insufficient_depth_penalty_seconds cannot be negative")
        
        self.order_execution_time_seconds = order_execution_time_seconds
        self.market_impact_coefficient_bps = market_impact_coefficient_bps
        self.insufficient_depth_penalty_seconds = insufficient_depth_penalty_seconds


class EscapeTimeSimulator:
    """
    平仓时间模拟器
    
    估算在当前市场条件下平掉特定持仓所需的时间。
    """
    
    def __init__(
        self,
        risk_config: PositionRiskConstructorConfig,
        killswitch_provider: Optional[KillSwitchProviderPort] = None,
        cooldown_provider: Optional[CooldownProviderPort] = None,
        config: Optional[EscapeTimeSimulatorConfig] = None,
    ):
        self._risk_config = risk_config
        self._killswitch_provider = killswitch_provider
        self._cooldown_provider = cooldown_provider
        self._config = config or EscapeTimeSimulatorConfig()
    
    def estimate_escape_time(
        self,
        position: Position,
        orderbook: OrderBook,
        regime: MarketRegime,
        scope: str = "GLOBAL",
        current_time: Optional[datetime] = None,
    ) -> EscapeTimeResult:
        """
        估算平仓时间
        
        Args:
            position: 当前持仓
            orderbook: 实时订单簿
            regime: 市场状态
            scope: KillSwitch 范围
            current_time: 可选的当前时间，默认使用 UTC now
            
        Returns:
            EscapeTimeResult: 平仓时间估算结果
        """
        # 如果持仓为空，立即返回
        if position.quantity == 0:
            return EscapeTimeResult.can_escape_result(
                estimated_seconds=0,
                max_slippage_bps=0,
                escape_path=[]
            )
        
        # 1. KillSwitch 检查
        killswitch_result = self._check_killswitch(scope)
        if killswitch_result is not None:
            return killswitch_result
        
        # 2. 冷却期检查
        cooldown_result = self._check_cooldown(position.symbol, current_time)
        if cooldown_result is not None:
            return cooldown_result
        
        # 3. 深度模拟
        depth_result = self._simulate_depth_escape(position, orderbook, regime)
        
        # 深度不足时阻止平仓
        if depth_result.insufficient_depth:
            return EscapeTimeResult.blocked(
                blocking_factors=["INSUFFICIENT_DEPTH"],
                estimated_seconds=self._config.insufficient_depth_penalty_seconds
            )
        
        # 4. 时间估算
        estimated_seconds = self._estimate_time(
            position=position,
            depth_result=depth_result,
            regime=regime
        )
        
        return EscapeTimeResult.can_escape_result(
            estimated_seconds=estimated_seconds,
            max_slippage_bps=depth_result.max_slippage_bps,
            escape_path=depth_result.escape_path
        )
    
    def _check_killswitch(self, scope: str) -> Optional[EscapeTimeResult]:
        """
        检查 KillSwitch 状态
        
        Args:
            scope: 范围
            
        Returns:
            如果被阻止返回 EscapeTimeResult，否则返回 None
        """
        if self._killswitch_provider is None:
            return None
        
        level = self._killswitch_provider.get_killswitch_level(scope)
        
        # L2 和 L3 级别阻止平仓
        if level >= KillSwitchLevel.L2_CANCEL_ALL_AND_HALT:
            level_name = "L2_CANCEL_ALL_AND_HALT" if level == KillSwitchLevel.L2_CANCEL_ALL_AND_HALT else "L3_LIQUIDATE_AND_DISCONNECT"
            return EscapeTimeResult.blocked(
                blocking_factors=[level_name],
                estimated_seconds=0
            )
        
        return None
    
    def _check_cooldown(
        self,
        symbol: str,
        current_time: Optional[datetime]
    ) -> Optional[EscapeTimeResult]:
        """
        检查冷却期状态
        
        Args:
            symbol: 交易标的
            current_time: 当前时间
            
        Returns:
            如果在冷却中返回 EscapeTimeResult，否则返回 None
        """
        if not self._risk_config.cooldown_enabled:
            return None
        
        if self._cooldown_provider is None:
            return None
        
        last_trade = self._cooldown_provider.get_last_trade_time(symbol)
        if last_trade is None:
            return None
        
        now = current_time
        if now is None:
            now = datetime.now(timezone.utc)
        elif now.tzinfo is None:
            now = now.replace(tzinfo=timezone.utc)
        cooldown_duration = timedelta(seconds=self._risk_config.cooldown_seconds)
        elapsed = now - last_trade
        remaining = cooldown_duration - elapsed
        
        if remaining.total_seconds() > 0:
            return EscapeTimeResult.blocked(
                blocking_factors=["IN_COOLDOWN"],
                estimated_seconds=int(remaining.total_seconds())
            )
        
        return None
    
    def _simulate_depth_escape(
        self,
        position: Position,
        orderbook: OrderBook,
        regime: MarketRegime,
    ) -> "_DepthSimResult":
        """
        模拟基于深度的平仓
        
        Args:
            position: 持仓
            orderbook: 订单簿
            regime: 市场状态
            
        Returns:
            _DepthSimResult: 深度模拟结果
        """
        # 确定方向：平多卖出买盘(bids)，平空买入卖盘(asks)
        is_long = position.quantity > 0
        is_short = position.quantity < 0
        
        if is_long:
            levels = orderbook.bids  # 平多：卖出买盘
        elif is_short:
            levels = orderbook.asks  # 平空：买入卖盘
        else:
            levels = orderbook.bids  # 默认
        
        if not levels:
            return _DepthSimResult(
                max_slippage_bps=0,
                escape_path=[],
                insufficient_depth=True
            )
        
        # 获取 regime 折扣因子
        regime_discount = self._risk_config.regime_discounts.get(
            regime,
            Decimal("1.0")
        )
        
        # 目标卖出数量（应用 regime 折扣）
        target_qty = abs(position.quantity) * regime_discount
        
        # 遍历档位计算滑点和路径
        accumulated_qty = Decimal("0")
        accumulated_value = Decimal("0")
        escape_path: List[DepthLevel] = []
        max_slippage_bps = 0
        
        mid_price = orderbook.mid_price
        # 注意：如果 mid_price 不可用，滑点将设为 0
        # 这表示无法估算滑点而非无滑点，调用方应注意此情况
        
        for idx, level in enumerate(levels):
            if accumulated_qty >= target_qty:
                break
            
            # 计算该档位可成交量
            remaining_qty = target_qty - accumulated_qty
            fill_qty = min(level.quantity, remaining_qty)
            
            # 记录档位
            new_cumulative = accumulated_qty + fill_qty
            depth_level = DepthLevel(
                price=level.price,
                cumulative_quantity=new_cumulative,
                level_index=idx
            )
            escape_path.append(depth_level)
            
            accumulated_qty = new_cumulative
            accumulated_value += fill_qty * level.price
            
            # 计算该档位的滑点
            if mid_price is not None and mid_price > 0:
                if is_long:
                    # 平多（卖出）：成交价越低，滑点越大
                    slippage = (mid_price - level.price) / mid_price * Decimal("10000")
                else:
                    # 平空（买入）：成交价越高，滑点越大
                    slippage = (level.price - mid_price) / mid_price * Decimal("10000")
                # 保持 Decimal 精度，避免 float 转换丢失精度
                slippage_bps = max(Decimal("0"), slippage)
                max_slippage_bps = max(max_slippage_bps, int(slippage_bps))
        
        # 检查深度是否充足
        insufficient_depth = accumulated_qty < target_qty
        
        return _DepthSimResult(
            max_slippage_bps=max_slippage_bps,
            escape_path=escape_path,
            insufficient_depth=insufficient_depth
        )
    
    def _estimate_time(
        self,
        position: Position,
        depth_result: "_DepthSimResult",
        regime: MarketRegime,
    ) -> int:
        """
        估算平仓时间
        
        Args:
            position: 持仓
            depth_result: 深度模拟结果
            regime: 市场状态
            
        Returns:
            int: 估算的平仓时间（秒）
        """
        if depth_result.insufficient_depth:
            # 深度不足，添加惩罚时间
            return self._config.insufficient_depth_penalty_seconds
        
        # 基于路径长度计算订单数
        num_orders = len(depth_result.escape_path)
        if num_orders == 0:
            num_orders = 1
        
        # 基本执行时间
        base_time = num_orders * self._config.order_execution_time_seconds
        
        # 市场冲击调整
        market_impact_adjustment = int(
            Decimal(str(depth_result.max_slippage_bps)) *
            self._config.market_impact_coefficient_bps *
            Decimal("0.1")
        )
        
        # Regime 调整
        regime_factor = Decimal("1.0")
        if regime == MarketRegime.CRISIS:
            regime_factor = Decimal("2.0")  # 危机时需要更长时间
        elif regime == MarketRegime.BEAR:
            regime_factor = Decimal("1.5")  # 熊市需要更长时间
        
        total_time = int((base_time + market_impact_adjustment) * float(regime_factor))
        
        # 最小时间为 5 秒
        return max(5, total_time)


@dataclass
class _DepthSimResult:
    """深度模拟内部结果"""
    max_slippage_bps: int
    escape_path: List[DepthLevel]
    insufficient_depth: bool
