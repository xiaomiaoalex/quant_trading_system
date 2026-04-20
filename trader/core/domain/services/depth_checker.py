"""
DepthChecker - 深度检查服务
===========================
交易前验证订单簿深度是否充足，防止在薄流动性市场下单导致大幅滑点。

核心逻辑：
1. 遍历 orderbook 档位，累计可成交量
2. 计算加权平均成交价
3. 与中间价对比计算滑点（基点）
4. 判断深度是否满足下单需求
5. 判断滑点是否在可接受范围内

重要约束：
- Core Plane 禁止 IO
- 所有计算使用 Decimal
- Fail-Closed 异常处理
"""
from __future__ import annotations

from decimal import Decimal
from typing import Protocol, Optional, TYPE_CHECKING

from trader.core.domain.models.orderbook import OrderBook, DepthCheckResult
from trader.core.domain.models.signal import Signal, SignalType
from trader.core.domain.models.order import OrderSide

if TYPE_CHECKING:
    from trader.core.application.risk_engine import RiskEngine


class MarketDataPort(Protocol):
    """行情数据端口协议（用于获取 orderbook）"""
    
    async def get_orderbook(self, symbol: str) -> Optional[OrderBook]:
        """
        获取订单簿快照
        
        Args:
            symbol: 交易标的
            
        Returns:
            OrderBook: 订单簿快照，失败返回 None
        """
        ...


class DepthCheckerConfig:
    """深度检查配置"""
    
    def __init__(
        self,
        max_slippage_bps: Decimal = Decimal("50"),      # 最大滑点（基点）
        min_depth_levels: int = 1,                         # 最少档位要求（默认1，避免误杀）
        depth_check_enabled: bool = True                  # 是否启用深度检查
    ):
        self.max_slippage_bps = max_slippage_bps
        self.min_depth_levels = min_depth_levels
        self.depth_check_enabled = depth_check_enabled


class DepthChecker:
    """
    深度检查服务
    
    在交易前检查订单簿深度是否充足，估算滑点。
    """
    
    def __init__(
        self,
        market_data: Optional[MarketDataPort] = None,
        config: Optional[DepthCheckerConfig] = None
    ):
        self._market_data = market_data
        self._config = config or DepthCheckerConfig()
    
    @property
    def config(self) -> DepthCheckerConfig:
        """获取配置"""
        return self._config
    
    def check_depth(
        self,
        orderbook: OrderBook,
        target_qty: Decimal,
        side: OrderSide
    ) -> DepthCheckResult:
        """
        检查订单簿深度
        
        Args:
            orderbook: 订单簿快照
            target_qty: 目标下单量
            side: 订单方向（BUY/SELL）
            
        Returns:
            DepthCheckResult: 深度检查结果
        """
        # 基础校验
        if not orderbook.bids and not orderbook.asks:
            return DepthCheckResult(
                ok=False,
                estimated_slippage_bps=float("inf"),
                available_qty=0.0,
                rejection_reason="EMPTY_ORDERBOOK",
                message="订单簿为空"
            )
        
        # 选择对应方向的档位
        if side == OrderSide.BUY:
            levels = orderbook.asks  # 买入看卖盘
        else:
            levels = orderbook.bids  # 卖出看买盘
        
        if not levels:
            return DepthCheckResult(
                ok=False,
                estimated_slippage_bps=float("inf"),
                available_qty=0.0,
                rejection_reason="NO_LEVELS",
                message=f"方向 {side} 对应的档位为空"
            )
        
        # 档位数量检查
        if len(levels) < self._config.min_depth_levels:
            return DepthCheckResult(
                ok=False,
                estimated_slippage_bps=float("inf"),
                available_qty=float(sum(l.quantity for l in levels)),
                rejection_reason="INSUFFICIENT_LEVELS",
                message=f"档位数 {len(levels)} < 要求 {self._config.min_depth_levels}"
            )
        
        # 遍历档位计算可成交量和加权均价
        accumulated_qty = Decimal("0")
        accumulated_value = Decimal("0")
        mid_price = orderbook.mid_price
        
        if mid_price is None or mid_price <= 0:
            return DepthCheckResult(
                ok=False,
                estimated_slippage_bps=float("inf"),
                available_qty=0.0,
                rejection_reason="INVALID_MID_PRICE",
                message="无法计算中间价"
            )
        
        for level in levels:
            if accumulated_qty >= target_qty:
                break
            
            # 计算该档位可成交量
            remaining_qty = target_qty - accumulated_qty
            fill_qty = min(level.quantity, remaining_qty)
            
            accumulated_qty += fill_qty
            accumulated_value += fill_qty * level.price
        
        # 计算加权平均成交价
        if accumulated_qty <= 0:
            return DepthCheckResult(
                ok=False,
                estimated_slippage_bps=float("inf"),
                available_qty=0.0,
                rejection_reason="ZERO_ACCUMULATED_QTY",
                message="累计可成交量为零"
            )
        
        vwap = accumulated_value / accumulated_qty
        
        # 计算滑点（基点）
        # 买入时：成交价 >= 中间价，滑点为正
        # 卖出时：成交价 <= 中间价，滑点为正
        if side == OrderSide.BUY:
            price_diff = vwap - mid_price
        else:
            price_diff = mid_price - vwap
        
        slippage_bps = float((price_diff / mid_price) * Decimal("10000"))
        
        # 判断深度是否充足
        if accumulated_qty < target_qty:
            return DepthCheckResult.reject_insufficient_depth(
                available_qty=float(accumulated_qty),
                required_qty=float(target_qty)
            )
        
        # 判断滑点是否超限
        if slippage_bps > float(self._config.max_slippage_bps):
            return DepthCheckResult.reject_excessive_slippage(
                slippage_bps=slippage_bps,
                max_slippage_bps=float(self._config.max_slippage_bps),
                available_qty=float(accumulated_qty)
            )
        
        return DepthCheckResult.pass_result(
            slippage_bps=slippage_bps,
            available_qty=float(accumulated_qty)
        )
    
    def check_signal_depth(
        self,
        orderbook: OrderBook,
        signal: Signal
    ) -> DepthCheckResult:
        """
        检查交易信号的订单簿深度
        
        Args:
            orderbook: 订单簿快照
            signal: 交易信号
            
        Returns:
            DepthCheckResult: 深度检查结果
        """
        # 根据信号类型确定买卖方向
        # 映射: LONG/SHORT -> 对应方向, CLOSE_LONG/CLOSE_SHORT -> SELL
        if signal.signal_type == SignalType.BUY:
            side = OrderSide.BUY
        elif signal.signal_type == SignalType.LONG:
            side = OrderSide.BUY  # 开多仓
        elif signal.signal_type == SignalType.SHORT:
            side = OrderSide.SELL  # 开空仓 (注: 现货不支持做空, 此处按 SELL 处理)
        elif signal.signal_type == SignalType.SELL:
            side = OrderSide.SELL
        elif signal.signal_type == SignalType.CLOSE_LONG:
            side = OrderSide.SELL  # 平多仓
        elif signal.signal_type == SignalType.CLOSE_SHORT:
            side = OrderSide.BUY  # 平空仓 (注: 现货不支持做空, 此处按 BUY 处理)
        else:
            # SignalType.NONE 或其他未知类型
            return DepthCheckResult(
                ok=False,
                estimated_slippage_bps=0.0,
                available_qty=0.0,
                rejection_reason="INVALID_SIGNAL_TYPE",
                message=f"[DepthChecker] 不支持的信号类型: {signal.signal_type}"
            )
        
        return self.check_depth(
            orderbook=orderbook,
            target_qty=signal.quantity,
            side=side
        )


class DepthCheckPreTradePlugin:
    """
    深度检查 Pre-Trade 插件
    
    集成到 RiskEngine 的交易前风控流程。
    """
    
    def __init__(
        self,
        market_data: MarketDataPort,
        config: Optional[DepthCheckerConfig] = None
    ):
        self._market_data = market_data
        self._config = config or DepthCheckerConfig()
        self._checker = DepthChecker(
            market_data=market_data,
            config=self._config
        )
    
    async def check(
        self,
        signal: Signal,
        metrics: "RiskEngine",  # 实际上是 RiskMetrics，但协议定义如此
        engine: "RiskEngine"
    ) -> Optional["DepthCheckResult"]:  # 实际上是 RiskCheckResult
        """
        执行深度检查
        
        注意：此方法实现了 PreTradeRiskPlugin 协议，
        返回 RiskCheckResult 类型，但内部使用 DepthCheckResult。
        """
        from trader.core.application.risk_engine import RiskCheckResult, RiskLevel, RejectionReason
        
        # 如果未启用深度检查，跳过
        if not self._config.depth_check_enabled:
            return None
        
        # 获取订单簿
        try:
            orderbook = await self._market_data.get_orderbook(signal.symbol)
        except Exception:
            # 获取订单簿失败时，Fail-Closed
            return RiskCheckResult(
                passed=False,
                risk_level=RiskLevel.CRITICAL,
                rejection_reason=RejectionReason.RISK_SYSTEM_ERROR,
                message="获取订单簿失败，已 Fail-Closed",
                details={"symbol": signal.symbol}
            )
        
        if orderbook is None:
            return RiskCheckResult(
                passed=False,
                risk_level=RiskLevel.CRITICAL,
                rejection_reason=RejectionReason.RISK_SYSTEM_ERROR,
                message="订单簿数据为空，已 Fail-Closed",
                details={"symbol": signal.symbol}
            )
        
        # 执行深度检查
        depth_result = self._checker.check_signal_depth(orderbook, signal)
        
        # 转换为 RiskCheckResult
        if depth_result.ok:
            return None  # 通过检查，不阻塞交易
        else:
            return RiskCheckResult(
                passed=False,
                risk_level=RiskLevel.HIGH,
                rejection_reason=RejectionReason.RISK_SYSTEM_ERROR,  # 可以定义新的拒绝原因
                message=depth_result.message,
                details={
                    "symbol": signal.symbol,
                    "estimated_slippage_bps": depth_result.estimated_slippage_bps,
                    "available_qty": depth_result.available_qty,
                    "rejection_reason": depth_result.rejection_reason
                }
            )
