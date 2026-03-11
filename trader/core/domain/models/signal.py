"""
Signal - 交易信号领域模型
=========================
信号是策略与执行层之间的桥梁。
信号包含足够的上下文信息以便追溯和审计。
"""
from dataclasses import dataclass, field
from datetime import datetime, timezone
from decimal import Decimal
from enum import Enum
from typing import Optional, Dict, Any
import uuid

from trader.core.domain.models.order import OrderSide, OrderType


class SignalType(Enum):
    """信号类型"""
    NONE = "NONE"
    BUY = "BUY"                     # 买入（现货做多）
    SELL = "SELL"                   # 卖出（现货平多）
    LONG = "LONG"                   # 开多（合约）
    SHORT = "SHORT"                 # 开空（合约）
    CLOSE_LONG = "CLOSE_LONG"       # 平多
    CLOSE_SHORT = "CLOSE_SHORT"     # 平空


@dataclass
class Signal:
    """
    交易信号

    策略分析行情后产生的交易意图。
    信号需要经过风控检查后才能转换为订单。
    """
    signal_id: str = ""                      # 信号唯一ID
    strategy_name: str = ""                  # 策略名称

    # 信号内容
    signal_type: SignalType = SignalType.NONE
    symbol: str = ""                        # 交易标的
    price: Decimal = Decimal("0")            # 触发价格（当前价）

    # 建议参数
    quantity: Decimal = Decimal("0")         # 建议数量
    confidence: Decimal = Decimal("1.0")     # 置信度 0-1

    # 风控参数
    stop_loss: Optional[Decimal] = None    # 止损价格
    take_profit: Optional[Decimal] = None  # 止盈价格

    # 上下文
    reason: str = ""                       # 信号产生原因
    timestamp: datetime = field(default_factory=lambda: datetime.now(timezone.utc))
    metadata: Dict[str, Any] = field(default_factory=dict)

    def __post_init__(self):
        if not self.signal_id:
            object.__setattr__(self, 'signal_id', str(uuid.uuid4()))

        if isinstance(self.price, (int, float)):
            object.__setattr__(self, 'price', Decimal(str(self.price)))
        if isinstance(self.quantity, (int, float)):
            object.__setattr__(self, 'quantity', Decimal(str(self.quantity)))
        if isinstance(self.confidence, (int, float)):
            object.__setattr__(self, 'confidence', Decimal(str(self.confidence)))

    # ==================== 辅助方法 ====================

    def is_buy_signal(self) -> bool:
        """是否为买入信号"""
        return self.signal_type in [SignalType.BUY, SignalType.LONG]

    def is_sell_signal(self) -> bool:
        """是否为卖出信号"""
        return self.signal_type in [SignalType.SELL, SignalType.CLOSE_LONG, SignalType.CLOSE_SHORT]

    def is_open_signal(self) -> bool:
        """是否为开仓信号"""
        return self.signal_type in [SignalType.BUY, SignalType.LONG, SignalType.SHORT]

    def is_close_signal(self) -> bool:
        """是否为平仓信号"""
        return self.signal_type in [SignalType.SELL, SignalType.CLOSE_LONG, SignalType.CLOSE_SHORT]

    def get_order_side(self) -> OrderSide:
        """转换为订单方向"""
        if self.is_buy_signal():
            return OrderSide.BUY
        return OrderSide.SELL

    def to_order_params(self) -> Dict[str, Any]:
        """转换为订单参数（供OMS使用）"""
        return {
            "symbol": self.symbol,
            "side": self.get_order_side(),
            "order_type": OrderType.MARKET,  # 默认市价单
            "quantity": self.quantity,
            "price": None,  # 市价单不需要价格
            "stop_loss": self.stop_loss,
            "take_profit": self.take_profit,
            "strategy_name": self.strategy_name,
            "metadata": {
                **self.metadata,
                "signal_id": self.signal_id,
                "confidence": str(self.confidence),
                "reason": self.reason,
            }
        }

    def __repr__(self) -> str:
        return (f"Signal({self.signal_type.value}, {self.symbol}, "
                f"qty={self.quantity}, conf={self.confidence})")
