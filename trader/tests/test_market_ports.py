"""
test_market_ports.py - P9.5 回测市场端口单元测试
================================================
测试 TradingCalendarPort、MarketCostModelPort、MarketRuleSnapshotProviderPort。

覆盖场景：
- A 股买入不收印花税
- A 股卖出收印花税
- 最低佣金生效
- Calendar port 拒绝非交易时段
- Fake/Crypto 实现不报错
- A 股专属字段放入 metadata

参考: docs/INTERFACE_CONTRACTS.md P9.5 契约
"""

from __future__ import annotations

from datetime import datetime, timezone
from decimal import Decimal

import pytest

from trader.core.domain.models.market_risk import AssetClass
from trader.services.backtesting.market_cost_model_port import (
    ChinaStockCostModel,
    ChinaStockCostModelConfig,
    CostCalculationRequest,
    NoOpCostModel,
)
from trader.services.backtesting.market_rule_snapshot_provider_port import (
    ChinaStockMetadata,
    ChinaStockSnapshotProvider,
    FakeMarketRuleSnapshotProvider,
    MarketRuleSnapshot,
    Venue,
)
from trader.services.backtesting.trading_calendar_port import (
    ChinaStockCalendar,
    FakeTradingCalendar,
    TradingPhase,
)


class TestChinaStockCostModel:
    @pytest.mark.asyncio
    async def test_buy_no_stamp_tax(self):
        model = ChinaStockCostModel()
        request = CostCalculationRequest(
            symbol="600000",
            side="BUY",
            price=Decimal("10"),
            quantity=Decimal("1000"),
            asset_class="CHINA_STOCK",
        )

        result = await model.calculate_costs(request)

        assert result.breakdown.stamp_tax == Decimal("0")
        assert result.breakdown.commission_buy > Decimal("0")
        assert result.effective_cost > Decimal("0")

    @pytest.mark.asyncio
    async def test_sell_has_stamp_tax(self):
        model = ChinaStockCostModel()
        request = CostCalculationRequest(
            symbol="600000",
            side="SELL",
            price=Decimal("10"),
            quantity=Decimal("1000"),
            asset_class="CHINA_STOCK",
        )

        result = await model.calculate_costs(request)

        assert result.breakdown.stamp_tax > Decimal("0")
        assert result.breakdown.commission_sell > Decimal("0")

    @pytest.mark.asyncio
    async def test_minimum_commission_applied(self):
        config = ChinaStockCostModelConfig(minimum_commission=Decimal("5"))
        model = ChinaStockCostModel(config=config)

        request = CostCalculationRequest(
            symbol="600000",
            side="BUY",
            price=Decimal("1"),
            quantity=Decimal("100"),
            asset_class="CHINA_STOCK",
        )

        result = await model.calculate_costs(request)

        assert result.effective_cost >= Decimal("5")

    @pytest.mark.asyncio
    async def test_slippage_affects_buy_price(self):
        model = ChinaStockCostModel()
        request = CostCalculationRequest(
            symbol="600000",
            side="BUY",
            price=Decimal("10"),
            quantity=Decimal("1000"),
            asset_class="CHINA_STOCK",
        )

        result = await model.calculate_costs(request)

        assert result.effective_price > Decimal("10")

    @pytest.mark.asyncio
    async def test_slippage_affects_sell_price(self):
        model = ChinaStockCostModel()
        request = CostCalculationRequest(
            symbol="600000",
            side="SELL",
            price=Decimal("10"),
            quantity=Decimal("1000"),
            asset_class="CHINA_STOCK",
        )

        result = await model.calculate_costs(request)

        assert result.effective_price < Decimal("10")


class TestNoOpCostModel:
    @pytest.mark.asyncio
    async def test_no_op_cost_model_zero_cost(self):
        model = NoOpCostModel()
        request = CostCalculationRequest(
            symbol="BTCUSDT",
            side="BUY",
            price=Decimal("50000"),
            quantity=Decimal("0.1"),
            asset_class="CRYPTO",
        )

        result = await model.calculate_costs(request)

        assert result.effective_cost == Decimal("0")
        assert result.effective_price == Decimal("50000")


class TestFakeTradingCalendar:
    @pytest.mark.asyncio
    async def test_always_open_returns_true(self):
        calendar = FakeTradingCalendar(always_open=True)
        dt = datetime(2024, 1, 1, 12, 0, tzinfo=timezone.utc)

        is_trading = await calendar.is_trading_day("BTCUSDT", dt)

        assert is_trading is True

    @pytest.mark.asyncio
    async def test_trading_phase_is_continuous(self):
        calendar = FakeTradingCalendar(always_open=True)
        dt = datetime(2024, 1, 1, 12, 0, tzinfo=timezone.utc)

        phase = await calendar.get_trading_phase("BTCUSDT", dt)

        assert phase == TradingPhase.CONTINUOUS


class TestChinaStockCalendar:
    @pytest.mark.asyncio
    async def test_weekend_not_trading_day(self):
        calendar = ChinaStockCalendar()
        saturday = datetime(2024, 1, 6, 12, 0, tzinfo=timezone.utc)

        is_trading = await calendar.is_trading_day("600000", saturday)

        assert is_trading is False

    @pytest.mark.asyncio
    async def test_weekday_is_trading_day(self):
        calendar = ChinaStockCalendar()
        monday = datetime(2024, 1, 8, 12, 0, tzinfo=timezone.utc)

        is_trading = await calendar.is_trading_day("600000", monday)

        assert is_trading is True

    @pytest.mark.asyncio
    async def test_suspended_symbol_returns_suspended_phase(self):
        calendar = ChinaStockCalendar(suspended_symbols=["600000"])
        monday = datetime(2024, 1, 8, 12, 0, tzinfo=timezone.utc)

        phase = await calendar.get_trading_phase("600000", monday)

        assert phase == TradingPhase.SUSPENDED

    @pytest.mark.asyncio
    async def test_pre_open_phase_before_930(self):
        calendar = ChinaStockCalendar()
        dt = datetime(2024, 1, 8, 9, 15, tzinfo=timezone.utc)

        phase = await calendar.get_trading_phase("600000", dt)

        assert phase == TradingPhase.PRE_OPEN

    @pytest.mark.asyncio
    async def test_call_auction_phase(self):
        calendar = ChinaStockCalendar()
        dt = datetime(2024, 1, 8, 9, 35, tzinfo=timezone.utc)

        phase = await calendar.get_trading_phase("600000", dt)

        assert phase == TradingPhase.CALL_AUCTION

    @pytest.mark.asyncio
    async def test_continuous_phase_during_trading(self):
        calendar = ChinaStockCalendar()
        dt = datetime(2024, 1, 8, 10, 30, tzinfo=timezone.utc)

        phase = await calendar.get_trading_phase("600000", dt)

        assert phase == TradingPhase.CONTINUOUS

    @pytest.mark.asyncio
    async def test_lunch_break_phase(self):
        calendar = ChinaStockCalendar()
        dt = datetime(2024, 1, 8, 12, 0, tzinfo=timezone.utc)

        phase = await calendar.get_trading_phase("600000", dt)

        assert phase == TradingPhase.CLOSED

    @pytest.mark.asyncio
    async def test_post_close_phase(self):
        calendar = ChinaStockCalendar()
        dt = datetime(2024, 1, 8, 15, 30, tzinfo=timezone.utc)

        phase = await calendar.get_trading_phase("600000", dt)

        assert phase == TradingPhase.POST_CLOSE


class TestFakeMarketRuleSnapshotProvider:
    @pytest.mark.asyncio
    async def test_default_snapshot_for_crypto(self):
        provider = FakeMarketRuleSnapshotProvider()
        dt = datetime(2024, 1, 1, 12, 0, tzinfo=timezone.utc)

        snapshot = await provider.get_snapshot("BTCUSDT", dt)

        assert snapshot.symbol == "BTCUSDT"
        assert snapshot.asset_class == AssetClass.CRYPTO
        assert "china_stock" not in snapshot.metadata

    @pytest.mark.asyncio
    async def test_custom_snapshot(self):
        custom = MarketRuleSnapshot(
            symbol="ETHUSDT",
            asset_class=AssetClass.CRYPTO,
            venue="binance",
            timestamp=datetime.now(timezone.utc),
        )
        provider = FakeMarketRuleSnapshotProvider(snapshots={"ETHUSDT": custom})

        snapshot = await provider.get_snapshot("ETHUSDT")

        assert snapshot.symbol == "ETHUSDT"
        assert snapshot.venue == "binance"


class TestChinaStockSnapshotProvider:
    @pytest.mark.asyncio
    async def test_default_limits_in_metadata(self):
        provider = ChinaStockSnapshotProvider()
        dt = datetime(2024, 1, 1, 12, 0, tzinfo=timezone.utc)

        snapshot = await provider.get_snapshot("600000", dt)

        assert snapshot.asset_class == AssetClass.CN_STOCK
        assert "china_stock" in snapshot.metadata

        china_meta = snapshot.metadata["china_stock"]
        assert isinstance(china_meta, ChinaStockMetadata)
        assert china_meta.lot_size == 100
        assert china_meta.allow_short is False
        assert china_meta.limit_up_rate == 0.10
        assert china_meta.limit_down_rate == 0.10

    @pytest.mark.asyncio
    async def test_suspended_symbol(self):
        provider = ChinaStockSnapshotProvider(suspended_symbols=["600000"])

        snapshot = await provider.get_snapshot("600000")

        china_meta = snapshot.metadata["china_stock"]
        assert china_meta.is_suspended is True

    @pytest.mark.asyncio
    async def test_custom_limit_rates(self):
        provider = ChinaStockSnapshotProvider()
        provider.set_limit_rates("600001", 0.20, 0.20)

        snapshot = await provider.get_snapshot("600001")

        china_meta = snapshot.metadata["china_stock"]
        assert china_meta.limit_up_rate == 0.20
        assert china_meta.limit_down_rate == 0.20

    @pytest.mark.asyncio
    async def test_shanghai_venue_for_60_prefix(self):
        provider = ChinaStockSnapshotProvider()

        snapshot = await provider.get_snapshot("600000")

        assert snapshot.venue == Venue.SHANGHAI.value

    @pytest.mark.asyncio
    async def test_shenzhen_venue_for_00_prefix(self):
        provider = ChinaStockSnapshotProvider()

        snapshot = await provider.get_snapshot("000001")

        assert snapshot.venue == Venue.SHENZHEN.value

    @pytest.mark.asyncio
    async def test_venue_is_string_not_enum(self):
        provider = ChinaStockSnapshotProvider()

        snapshot = await provider.get_snapshot("600000")

        assert isinstance(snapshot.venue, str)
        assert snapshot.venue == "SHANGHAI"
