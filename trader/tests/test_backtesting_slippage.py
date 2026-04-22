# trader/tests/test_backtesting_slippage.py
import pytest
from decimal import Decimal
from trader.services.backtesting.slippage import (
    calculate_slippage,
    BinanceSlippageConfig,
    SlippageModel,
)


class TestDirectionAwareSlippage:
    def test_buy_slippage_is_positive(self):
        config = BinanceSlippageConfig(model=SlippageModel.FIXED, fixed_slippage_bps=10.0)
        slippage = calculate_slippage("BUY", Decimal("100"), Decimal("1"), Decimal("100"), config)
        assert slippage > 0  # Buy should slide UP

    def test_sell_slippage_is_negative(self):
        config = BinanceSlippageConfig(model=SlippageModel.FIXED, fixed_slippage_bps=10.0)
        slippage = calculate_slippage("SELL", Decimal("100"), Decimal("1"), Decimal("100"), config)
        assert slippage < 0  # Sell should slide DOWN

    def test_no_slippage_returns_zero(self):
        config = BinanceSlippageConfig(model=SlippageModel.NO_SLIPPAGE)
        slippage = calculate_slippage("BUY", Decimal("100"), Decimal("1"), Decimal("100"), config)
        assert slippage == Decimal("0")

    def test_volume_based_slippage_scales_with_volume_ratio(self):
        config = BinanceSlippageConfig(model=SlippageModel.VOLUME_BASED, volume_profile_enabled=True)
        # Small order relative to volume: low slippage
        small = calculate_slippage("BUY", Decimal("100"), Decimal("0.1"), Decimal("100"), config)
        # Large order relative to volume: high slippage
        large = calculate_slippage("BUY", Decimal("100"), Decimal("50"), Decimal("100"), config)
        assert abs(large) > abs(small)