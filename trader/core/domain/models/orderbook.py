"""
OrderBook - 订单簿模型
======================
订单簿快照模型，用于深度检查和滑点估算。

核心概念：
- OrderBookLevel: 订单簿档位（价格 + 数量）
- OrderBook: 订单簿快照（多档位数据）

重要原则：
1. 所有价格/数量使用 Decimal
2. 档位按价格排序（卖盘从低到高，买盘从高到低）
3. 不依赖任何外部 IO
"""
from __future__ import annotations
import builtins
from dataclasses import dataclass, field
from decimal import Decimal
from typing import List, Optional
from datetime import datetime

# 使用 lambda 包装 list 调用，确保在运行时从 builtins 获取
_list_factory = lambda: list()


@dataclass(frozen=True)
class OrderBookLevel:
    """
    订单簿档位（不可变）
    
    Attributes:
        price: 价格
        quantity: 数量
    """
    price: Decimal
    quantity: Decimal


@dataclass
class OrderBook:
    """
    订单簿快照
    
    包含指定交易标的的买卖盘数据。
    
    Attributes:
        symbol: 交易标的
        bids: 买盘档位列表（按价格降序排列）
        asks: 卖盘档位列表（按价格升序排列）
        timestamp: 快照时间戳
    """
    symbol: str
    bids: List[OrderBookLevel] = field(default_factory=_list_factory)  # 买盘（价格降序）
    asks: List[OrderBookLevel] = field(default_factory=_list_factory)  # 卖盘（价格升序）
    timestamp: Optional[datetime] = field(default=None)
    
    @property
    def best_bid(self) -> Optional[Decimal]:
        """买一价"""
        return self.bids[0].price if self.bids else None
    
    @property
    def best_ask(self) -> Optional[Decimal]:
        """卖一价"""
        return self.asks[0].price if self.asks else None
    
    @property
    def mid_price(self) -> Optional[Decimal]:
        """中间价（买一和卖一的平均值）"""
        if self.best_bid is not None and self.best_ask is not None:
            return (self.best_bid + self.best_ask) / Decimal("2")
        return None
    
    @property
    def spread(self) -> Optional[Decimal]:
        """买卖价差"""
        if self.best_bid is not None and self.best_ask is not None:
            return self.best_ask - self.best_bid
        return None
    
    @property
    def spread_bps(self) -> Optional[Decimal]:
        """买卖价差（基点）"""
        if self.spread is not None and self.mid_price is not None and self.mid_price > 0:
            return (self.spread / self.mid_price) * Decimal("10000")
        return None


@dataclass
class DepthCheckResult:
    """
    深度检查结果
    
    Attributes:
        ok: 检查是否通过
        estimated_slippage_bps: 预估滑点（基点）
        available_qty: 可成交量
        rejection_reason: 拒绝原因（可选）
        message: 消息
    """
    ok: bool
    estimated_slippage_bps: float
    available_qty: float
    rejection_reason: Optional[str] = None
    message: str = ""
    
    @classmethod
    def pass_result(cls, slippage_bps: float, available_qty: float) -> "DepthCheckResult":
        """创建通过结果"""
        return cls(
            ok=True,
            estimated_slippage_bps=slippage_bps,
            available_qty=available_qty,
            message=f"深度检查通过，预估滑点 {slippage_bps:.2f} bps"
        )
    
    @classmethod
    def reject_insufficient_depth(cls, available_qty: float, required_qty: float) -> "DepthCheckResult":
        """创建深度不足拒绝结果"""
        return cls(
            ok=False,
            estimated_slippage_bps=float("inf"),
            available_qty=available_qty,
            rejection_reason="INSUFFICIENT_DEPTH",
            message=f"深度不足: 可成交 {available_qty} < 需求 {required_qty}"
        )
    
    @classmethod
    def reject_excessive_slippage(cls, slippage_bps: float, max_slippage_bps: float, available_qty: float) -> "DepthCheckResult":
        """创建滑点超限拒绝结果"""
        return cls(
            ok=False,
            estimated_slippage_bps=slippage_bps,
            available_qty=available_qty,
            rejection_reason="EXCESSIVE_SLIPPAGE",
            message=f"滑点超限: {slippage_bps:.2f} bps > 限制 {max_slippage_bps} bps"
        )
