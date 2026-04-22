"""
Direction-Aware Slippage Model
===============================
Binance 特定滑点模型：
- 买入时向不利方向滑（高价）
- 卖出时向不利方向滑（低价）
- 基于成交量比例的动态滑点
"""
from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal
from enum import Enum
from typing import Literal


class SlippageModel(Enum):
    NO_SLIPPAGE = "no_slippage"
    FIXED = "fixed"
    PERCENTAGE = "percentage"
    VOLUME_BASED = "volume_based"


@dataclass(slots=True)
class BinanceSlippageConfig:
    """Binance 滑点配置"""
    model: SlippageModel = SlippageModel.VOLUME_BASED
    fixed_slippage_bps: float = 5.0  # 基点 (5 bps = 0.05%)
    percentage_slippage: float = 0.0005  # 0.05%
    volume_profile_enabled: bool = True


def calculate_slippage(
    side: Literal["BUY", "SELL"],
    price: Decimal,
    quantity: Decimal,
    volume: Decimal,
    config: BinanceSlippageConfig,
) -> Decimal:
    """
    计算方向感知滑点

    Args:
        side: BUY 或 SELL
        price: 订单价格
        quantity: 订单数量
        volume: 成交量（用于动态滑点）
        config: 滑点配置

    Returns:
        Decimal: 滑点成本
    """
    if config.model == SlippageModel.NO_SLIPPAGE:
        return Decimal("0")

    price_float = float(price)

    if config.model == SlippageModel.FIXED:
        slippage_bps = config.fixed_slippage_bps
    elif config.model == SlippageModel.PERCENTAGE:
        slippage_bps = config.percentage_slippage * 10000
    elif config.model == SlippageModel.VOLUME_BASED:
        if volume and float(volume) > 0:
            volume_ratio = float(quantity) / float(volume)
            # 成交量占比越高，滑点越大（线性模型）
            slippage_bps = min(50.0, volume_ratio * 500 + 5.0)  # max 50bps
        else:
            slippage_bps = config.fixed_slippage_bps
    else:
        slippage_bps = 5.0

    # BUY: 向高价滑（+），SELL: 向低价滑（-）
    direction = 1 if side == "BUY" else -1
    slippage_amount = price_float * (slippage_bps / 10000) * direction

    return Decimal(str(round(slippage_amount, 6)))