"""
test_historical_snapshot_provider.py - P10 Historical Crypto Risk Snapshot Provider 测试
=======================================================================================
测试 FakeHistoricalCryptoRiskSnapshotProvider 的各项功能：
1. 实现 build(signal) -> CryptoRiskSnapshot（满足 CryptoRiskSnapshotProvider Protocol）
2. 将历史输入转换为 Crypto DTOs
3. stale/missing Funding/OI 进入 CryptoRiskSnapshot.funding_oi_metrics
4. 防未来数据泄露（as-of lookup）
5. 可直接注入 CryptoPreTradeRiskPlugin

参考:
- docs/INTERFACE_CONTRACTS.md 8.13.9 HistoricalCryptoRiskSnapshotProvider 契约
- trader/core/application/plugins/crypto_pre_trade_risk_plugin.py::CryptoRiskSnapshotProvider
"""

from __future__ import annotations

from decimal import Decimal

import pytest

from trader.core.domain.models.crypto_risk import CryptoRiskSnapshot
from trader.core.domain.models.signal import Signal, SignalType
from trader.services.backtesting.historical_snapshot_provider import (
    FakeHistoricalCryptoRiskSnapshotProvider,
    HistoricalAccountData,
    HistoricalFundingOI,
    HistoricalPositionData,
    HistoricalSnapshotInput,
    create_stale_funding_oi,
    create_test_snapshot_input,
)


def _default_account() -> HistoricalAccountData:
    return HistoricalAccountData(
        equity=Decimal("100000"),
        available_balance=Decimal("80000"),
        wallet_balance=Decimal("100000"),
        margin_balance=Decimal("0"),
        total_initial_margin=Decimal("0"),
        total_maintenance_margin=Decimal("0"),
    )


def _default_positions() -> list[HistoricalPositionData]:
    return [
        HistoricalPositionData(
            symbol="BTCUSDT",
            quantity=Decimal("0.1"),
            entry_price=Decimal("50000"),
            mark_price=Decimal("50000"),
            unrealized_pnl=Decimal("0"),
            leverage=Decimal("1"),
            position_side="BOTH",
        )
    ]


def _make_signal(symbol: str = "BTCUSDT", timestamp_ms: int = 1000000) -> Signal:
    return Signal(
        signal_id="test-signal",
        strategy_name="test",
        signal_type=SignalType.BUY,
        symbol=symbol,
        price=Decimal("50000"),
        quantity=Decimal("0.1"),
        timestamp=timestamp_ms,
    )


class TestBuildCryptoRiskSnapshot:
    """测试 build(signal) -> CryptoRiskSnapshot"""

    @pytest.mark.asyncio
    async def test_build_returns_crypto_risk_snapshot(self):
        provider = FakeHistoricalCryptoRiskSnapshotProvider(
            initial_account=_default_account(),
            initial_positions=_default_positions(),
        )
        provider.add_historical_snapshot(
            "BTCUSDT",
            create_test_snapshot_input(
                symbol="BTCUSDT",
                timestamp_ms=1000000,
                mark_price=Decimal("50000"),
                account=_default_account(),
            ),
        )

        signal = _make_signal()
        result = await provider.build(signal)

        assert isinstance(result, CryptoRiskSnapshot)
        assert result.account is not None
        assert len(result.instrument_specs) > 0
        assert len(result.positions) > 0

    @pytest.mark.asyncio
    async def test_build_converts_account(self):
        provider = FakeHistoricalCryptoRiskSnapshotProvider(
            initial_account=_default_account(),
            initial_positions=[],
        )

        signal = _make_signal()
        result = await provider.build(signal)

        assert result.account.equity == Decimal("100000")
        assert result.account.available_balance == Decimal("80000")

    @pytest.mark.asyncio
    async def test_build_converts_positions(self):
        provider = FakeHistoricalCryptoRiskSnapshotProvider(
            initial_account=_default_account(),
            initial_positions=_default_positions(),
        )

        signal = _make_signal()
        result = await provider.build(signal)

        btc_pos = next((p for p in result.positions if p.symbol == "BTCUSDT"), None)
        assert btc_pos is not None
        assert btc_pos.qty == Decimal("0.1")
        assert btc_pos.entry_price == Decimal("50000")

    @pytest.mark.asyncio
    async def test_build_converts_instrument_specs(self):
        provider = FakeHistoricalCryptoRiskSnapshotProvider(
            initial_account=_default_account(),
            initial_positions=[],
        )
        provider.add_historical_snapshot(
            "BTCUSDT",
            create_test_snapshot_input(
                symbol="BTCUSDT",
                timestamp_ms=1000000,
                mark_price=Decimal("50000"),
                account=_default_account(),
            ),
        )

        signal = _make_signal()
        result = await provider.build(signal)

        assert "BTCUSDT" in result.instrument_specs
        spec = result.instrument_specs["BTCUSDT"]
        assert spec.price_tick == Decimal("0.01")
        assert spec.qty_step == Decimal("0.001")

    @pytest.mark.asyncio
    async def test_build_converts_leverage_brackets(self):
        provider = FakeHistoricalCryptoRiskSnapshotProvider(
            initial_account=_default_account(),
            initial_positions=[],
        )
        provider.add_historical_snapshot(
            "BTCUSDT",
            create_test_snapshot_input(
                symbol="BTCUSDT",
                timestamp_ms=1000000,
                mark_price=Decimal("50000"),
                account=_default_account(),
            ),
        )

        signal = _make_signal()
        result = await provider.build(signal)

        assert "BTCUSDT" in result.leverage_brackets
        bracket = result.leverage_brackets["BTCUSDT"][0]
        assert bracket.initial_leverage == Decimal("20")


class TestFundingOIMetrics:
    """测试 Funding/OI metrics 进入 CryptoRiskSnapshot"""

    @pytest.mark.asyncio
    async def test_funding_oi_metrics_in_snapshot(self):
        provider = FakeHistoricalCryptoRiskSnapshotProvider(
            initial_account=_default_account(),
            initial_positions=[],
        )

        funding_oi = HistoricalFundingOI(
            timestamp_ms=1000000,
            symbol="BTCUSDT",
            funding_rate=Decimal("-0.0001"),
            open_interest=Decimal("1000000"),
            funding_data_stale=False,
            oi_data_stale=False,
        )

        provider.add_historical_snapshot(
            "BTCUSDT",
            create_test_snapshot_input(
                symbol="BTCUSDT",
                timestamp_ms=1000000,
                mark_price=Decimal("50000"),
                account=_default_account(),
                funding_oi=funding_oi,
            ),
        )

        signal = _make_signal()
        result = await provider.build(signal)

        assert "BTCUSDT" in result.funding_oi_metrics
        metrics = result.funding_oi_metrics["BTCUSDT"]
        assert metrics.current_funding_rate == Decimal("-0.0001")
        assert metrics.current_open_interest == Decimal("1000000")

    @pytest.mark.asyncio
    async def test_stale_funding_in_metrics(self):
        provider = FakeHistoricalCryptoRiskSnapshotProvider(
            initial_account=_default_account(),
            initial_positions=[],
        )

        funding_oi = create_stale_funding_oi(
            symbol="BTCUSDT",
            timestamp_ms=1000000,
            stale_funding=True,
        )

        provider.add_historical_snapshot(
            "BTCUSDT",
            create_test_snapshot_input(
                symbol="BTCUSDT",
                timestamp_ms=1000000,
                mark_price=Decimal("50000"),
                account=_default_account(),
                funding_oi=funding_oi,
            ),
        )

        signal = _make_signal()
        result = await provider.build(signal)

        assert result.funding_oi_metrics["BTCUSDT"].funding_data_stale is True

    @pytest.mark.asyncio
    async def test_stale_oi_in_metrics(self):
        provider = FakeHistoricalCryptoRiskSnapshotProvider(
            initial_account=_default_account(),
            initial_positions=[],
        )

        funding_oi = create_stale_funding_oi(
            symbol="BTCUSDT",
            timestamp_ms=1000000,
            stale_oi=True,
        )

        provider.add_historical_snapshot(
            "BTCUSDT",
            create_test_snapshot_input(
                symbol="BTCUSDT",
                timestamp_ms=1000000,
                mark_price=Decimal("50000"),
                account=_default_account(),
                funding_oi=funding_oi,
            ),
        )

        signal = _make_signal()
        result = await provider.build(signal)

        assert result.funding_oi_metrics["BTCUSDT"].oi_data_stale is True

    @pytest.mark.asyncio
    async def test_missing_funding_in_metrics(self):
        provider = FakeHistoricalCryptoRiskSnapshotProvider(
            initial_account=_default_account(),
            initial_positions=[],
        )

        funding_oi = create_stale_funding_oi(
            symbol="BTCUSDT",
            timestamp_ms=1000000,
            missing_funding=True,
        )

        provider.add_historical_snapshot(
            "BTCUSDT",
            create_test_snapshot_input(
                symbol="BTCUSDT",
                timestamp_ms=1000000,
                mark_price=Decimal("50000"),
                account=_default_account(),
                funding_oi=funding_oi,
            ),
        )

        signal = _make_signal()
        result = await provider.build(signal)

        assert result.funding_oi_metrics["BTCUSDT"].funding_current_missing is True
        assert result.funding_oi_metrics["BTCUSDT"].current_funding_rate is None

    @pytest.mark.asyncio
    async def test_missing_oi_in_metrics(self):
        provider = FakeHistoricalCryptoRiskSnapshotProvider(
            initial_account=_default_account(),
            initial_positions=[],
        )

        funding_oi = create_stale_funding_oi(
            symbol="BTCUSDT",
            timestamp_ms=1000000,
            missing_oi=True,
        )

        provider.add_historical_snapshot(
            "BTCUSDT",
            create_test_snapshot_input(
                symbol="BTCUSDT",
                timestamp_ms=1000000,
                mark_price=Decimal("50000"),
                account=_default_account(),
                funding_oi=funding_oi,
            ),
        )

        signal = _make_signal()
        result = await provider.build(signal)

        assert result.funding_oi_metrics["BTCUSDT"].oi_current_missing is True
        assert result.funding_oi_metrics["BTCUSDT"].current_open_interest is None


class TestAsOfLookup:
    """测试 as-of lookup 防未来数据"""

    @pytest.mark.asyncio
    async def test_no_future_data_used(self):
        provider = FakeHistoricalCryptoRiskSnapshotProvider(
            initial_account=_default_account(),
            initial_positions=[],
        )

        provider.add_historical_batch(
            "BTCUSDT",
            [
                create_test_snapshot_input(
                    symbol="BTCUSDT",
                    timestamp_ms=1000000,
                    mark_price=Decimal("50000"),
                    account=_default_account(),
                ),
                create_test_snapshot_input(
                    symbol="BTCUSDT",
                    timestamp_ms=2000000,
                    mark_price=Decimal("60000"),
                    account=_default_account(),
                ),
                create_test_snapshot_input(
                    symbol="BTCUSDT",
                    timestamp_ms=3000000,
                    mark_price=Decimal("70000"),
                    account=_default_account(),
                ),
            ],
        )

        signal = _make_signal(timestamp_ms=1500000)
        result = await provider.build(signal)

        assert result.mark_prices["BTCUSDT"] == Decimal("50000")

    @pytest.mark.asyncio
    async def test_future_data_not_used_at_exact_timestamp(self):
        provider = FakeHistoricalCryptoRiskSnapshotProvider(
            initial_account=_default_account(),
            initial_positions=[],
        )

        provider.add_historical_batch(
            "BTCUSDT",
            [
                create_test_snapshot_input(
                    symbol="BTCUSDT",
                    timestamp_ms=1000000,
                    mark_price=Decimal("50000"),
                    account=_default_account(),
                ),
                create_test_snapshot_input(
                    symbol="BTCUSDT",
                    timestamp_ms=2000000,
                    mark_price=Decimal("60000"),
                    account=_default_account(),
                ),
            ],
        )

        signal = _make_signal(timestamp_ms=2000000)
        result = await provider.build(signal)

        assert result.mark_prices["BTCUSDT"] == Decimal("60000")

    @pytest.mark.asyncio
    async def test_as_of_snapshot_with_future_data_available(self):
        provider = FakeHistoricalCryptoRiskSnapshotProvider(
            initial_account=_default_account(),
            initial_positions=[],
        )

        provider.add_historical_batch(
            "BTCUSDT",
            [
                create_test_snapshot_input(
                    symbol="BTCUSDT",
                    timestamp_ms=1000000,
                    mark_price=Decimal("50000"),
                    account=_default_account(),
                ),
                create_test_snapshot_input(
                    symbol="BTCUSDT",
                    timestamp_ms=2000000,
                    mark_price=Decimal("60000"),
                    account=_default_account(),
                ),
                create_test_snapshot_input(
                    symbol="BTCUSDT",
                    timestamp_ms=3000000,
                    mark_price=Decimal("70000"),
                    account=_default_account(),
                ),
                create_test_snapshot_input(
                    symbol="BTCUSDT",
                    timestamp_ms=4000000,
                    mark_price=Decimal("80000"),
                    account=_default_account(),
                ),
            ],
        )

        signal = _make_signal(timestamp_ms=2500000)
        result = await provider.build(signal)

        assert result.mark_prices["BTCUSDT"] == Decimal("60000")


class TestMultipleSymbols:
    """测试多 symbol 场景"""

    @pytest.mark.asyncio
    async def test_multiple_symbols_snapshot(self):
        provider = FakeHistoricalCryptoRiskSnapshotProvider(
            initial_account=_default_account(),
            initial_positions=[
                HistoricalPositionData(
                    symbol="BTCUSDT",
                    quantity=Decimal("0.1"),
                    entry_price=Decimal("50000"),
                    leverage=Decimal("1"),
                    position_side="BOTH",
                ),
            ],
        )

        btc_input = create_test_snapshot_input(
            symbol="BTCUSDT",
            timestamp_ms=1000000,
            mark_price=Decimal("50000"),
            account=_default_account(),
        )
        eth_input = create_test_snapshot_input(
            symbol="ETHUSDT",
            timestamp_ms=1000000,
            mark_price=Decimal("3000"),
            account=_default_account(),
        )

        provider.add_historical_snapshot("BTCUSDT", btc_input)
        provider.add_historical_snapshot("ETHUSDT", eth_input)

        signal = Signal(
            signal_id="multi-sym-test",
            strategy_name="test",
            signal_type=SignalType.BUY,
            symbol="BTCUSDT",
            price=Decimal("50000"),
            quantity=Decimal("0.1"),
            timestamp=1000000,
        )

        result = await provider.build(signal)

        assert "BTCUSDT" in result.instrument_specs
        assert "ETHUSDT" in result.instrument_specs
        assert result.mark_prices["ETHUSDT"] == Decimal("3000")


class TestNoIOInCore:
    """测试 Core 层无 IO"""

    def test_provider_in_service_layer(self):
        import inspect

        from trader.services.backtesting.historical_snapshot_provider import (
            FakeHistoricalCryptoRiskSnapshotProvider,
        )

        module = inspect.getmodule(FakeHistoricalCryptoRiskSnapshotProvider)
        assert module is not None
        assert "services.backtesting" in module.__name__

    def test_reuses_crypto_funding_oi_metrics(self):
        from trader.core.domain.models.crypto_risk import CryptoFundingOIRiskMetrics

        metrics = CryptoFundingOIRiskMetrics(
            symbol="BTCUSDT",
            current_funding_rate=Decimal("-0.0001"),
            funding_data_stale=True,
        )

        assert metrics.symbol == "BTCUSDT"
        assert metrics.funding_data_stale is True

    def test_reuses_crypto_risk_snapshot(self):
        from trader.core.domain.models.crypto_risk import CryptoRiskSnapshot

        snapshot = CryptoRiskSnapshot(
            account=None,
            instrument_specs={},
            leverage_brackets={},
            positions=[],
            open_orders=[],
            mark_prices={},
            funding_oi_metrics={},
        )

        assert hasattr(snapshot, "funding_oi_metrics")


class TestTimelineHelpers:
    """测试 replay timeline helper 方法"""

    @pytest.mark.asyncio
    async def test_get_account_snapshot_helper(self):
        provider = FakeHistoricalCryptoRiskSnapshotProvider(
            initial_account=_default_account(),
            initial_positions=_default_positions(),
        )

        snapshot = await provider.get_account_snapshot(
            symbol="BTCUSDT",
            timestamp_ms=1000000,
        )

        assert snapshot.total_equity == Decimal("100000")

    @pytest.mark.asyncio
    async def test_get_position_snapshot_helper(self):
        provider = FakeHistoricalCryptoRiskSnapshotProvider(
            initial_account=_default_account(),
            initial_positions=_default_positions(),
        )

        snapshot = await provider.get_position_snapshot(
            symbol="BTCUSDT",
            timestamp_ms=1000000,
        )

        assert snapshot.symbol == "BTCUSDT"
        assert snapshot.quantity == Decimal("0.1")


class TestCryptoPreTradeRiskPluginIntegration:
    """测试真实注入 CryptoPreTradeRiskPlugin

    验证 stale/missing Funding/OI 能被真实插件拒绝。
    """

    @pytest.mark.asyncio
    async def test_stale_funding_rejected_by_plugin(self):
        from decimal import Decimal

        from trader.core.application.plugins.crypto_pre_trade_risk_plugin import (
            CryptoPreTradeRiskConfig,
            CryptoPreTradeRiskPlugin,
        )
        from trader.core.application.risk_engine import RejectionReason, RiskLevel
        from trader.core.domain.models.crypto_risk import CryptoRiskBudget

        budget = CryptoRiskBudget(
            max_abs_funding_rate_z_score=Decimal("3"),
            funding_history_window=20,
            funding_min_periods=10,
        )

        provider = FakeHistoricalCryptoRiskSnapshotProvider(
            initial_account=_default_account(),
            initial_positions=_default_positions(),
            risk_budget=budget,
        )

        stale_funding = create_stale_funding_oi(
            symbol="BTCUSDT",
            timestamp_ms=1000000,
            stale_funding=True,
        )

        provider.add_historical_snapshot(
            "BTCUSDT",
            create_test_snapshot_input(
                symbol="BTCUSDT",
                timestamp_ms=1000000,
                mark_price=Decimal("50000"),
                account=_default_account(),
                funding_oi=stale_funding,
            ),
        )

        plugin = CryptoPreTradeRiskPlugin(
            snapshot_provider=provider,
            config=CryptoPreTradeRiskConfig(),
        )

        signal = _make_signal()
        result = await plugin.check(
            signal=signal,
            metrics=None,
            engine=None,
        )

        assert result is not None
        assert result.passed is False
        assert result.rejection_reason == RejectionReason.CRYPTO_FUNDING_OI_RISK

    @pytest.mark.asyncio
    async def test_missing_oi_rejected_by_plugin(self):
        from decimal import Decimal

        from trader.core.application.plugins.crypto_pre_trade_risk_plugin import (
            CryptoPreTradeRiskConfig,
            CryptoPreTradeRiskPlugin,
        )
        from trader.core.application.risk_engine import RejectionReason
        from trader.core.domain.models.crypto_risk import CryptoRiskBudget

        budget = CryptoRiskBudget(
            max_abs_open_interest_change_rate=Decimal("0.5"),
            oi_history_window=20,
            oi_min_periods=10,
        )

        provider = FakeHistoricalCryptoRiskSnapshotProvider(
            initial_account=_default_account(),
            initial_positions=_default_positions(),
            risk_budget=budget,
        )

        missing_oi = create_stale_funding_oi(
            symbol="BTCUSDT",
            timestamp_ms=1000000,
            missing_oi=True,
        )

        provider.add_historical_snapshot(
            "BTCUSDT",
            create_test_snapshot_input(
                symbol="BTCUSDT",
                timestamp_ms=1000000,
                mark_price=Decimal("50000"),
                account=_default_account(),
                funding_oi=missing_oi,
            ),
        )

        plugin = CryptoPreTradeRiskPlugin(
            snapshot_provider=provider,
            config=CryptoPreTradeRiskConfig(),
        )

        signal = _make_signal()
        result = await plugin.check(
            signal=signal,
            metrics=None,
            engine=None,
        )

        assert result is not None
        assert result.passed is False
        assert result.rejection_reason == RejectionReason.CRYPTO_FUNDING_OI_RISK

    @pytest.mark.asyncio
    async def test_missing_funding_metrics_rejected_by_plugin(self):
        from decimal import Decimal

        from trader.core.application.plugins.crypto_pre_trade_risk_plugin import (
            CryptoPreTradeRiskConfig,
            CryptoPreTradeRiskPlugin,
        )
        from trader.core.application.risk_engine import RejectionReason
        from trader.core.domain.models.crypto_risk import CryptoRiskBudget

        budget = CryptoRiskBudget(
            max_abs_funding_rate_z_score=Decimal("3"),
            funding_history_window=20,
            funding_min_periods=10,
        )

        provider = FakeHistoricalCryptoRiskSnapshotProvider(
            initial_account=_default_account(),
            initial_positions=_default_positions(),
            risk_budget=budget,
        )

        provider.add_historical_snapshot(
            "BTCUSDT",
            create_test_snapshot_input(
                symbol="BTCUSDT",
                timestamp_ms=1000000,
                mark_price=Decimal("50000"),
                account=_default_account(),
                funding_oi=None,
            ),
        )

        plugin = CryptoPreTradeRiskPlugin(
            snapshot_provider=provider,
            config=CryptoPreTradeRiskConfig(),
        )

        signal = _make_signal()
        result = await plugin.check(
            signal=signal,
            metrics=None,
            engine=None,
        )

        assert result is not None
        assert result.passed is False
        assert result.rejection_reason == RejectionReason.CRYPTO_FUNDING_OI_RISK
