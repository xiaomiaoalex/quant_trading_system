from decimal import Decimal

import pytest

from trader.core.application.plugins.crypto_pre_trade_risk_plugin import CryptoPreTradeRiskPlugin
from trader.core.application.risk_engine import RejectionReason, RiskMetrics
from trader.core.domain.models.crypto_risk import (
    CryptoAccountRisk,
    CryptoFundingOIRiskMetrics,
    CryptoInstrumentSpec,
    CryptoMarketType,
    CryptoPositionRisk,
    CryptoRiskBudget,
    CryptoRiskSnapshot,
    LeverageBracket,
    OpenOrderRisk,
)
from trader.core.domain.models.order import OrderSide
from trader.core.domain.models.signal import Signal, SignalType
from trader.core.domain.services.exchange_rule_guard import ExchangeRuleGuard
from trader.core.domain.services.margin_risk_calculator import MarginRiskCalculator
from trader.core.domain.services.open_order_exposure import OpenOrderExposureCalculator
from trader.core.domain.services.portfolio_exposure_aggregator import PortfolioExposureAggregator


def d(value: str) -> Decimal:
    return Decimal(value)


def btc_spec() -> CryptoInstrumentSpec:
    return CryptoInstrumentSpec(
        symbol="BTCUSDT",
        market_type=CryptoMarketType.USD_M_FUTURES,
        price_tick=d("0.10"),
        qty_step=d("0.001"),
        min_qty=d("0.001"),
        max_qty=d("100"),
        min_notional=d("10"),
        max_notional=d("1000000"),
    )


def eth_spec() -> CryptoInstrumentSpec:
    return CryptoInstrumentSpec(
        symbol="ETHUSDT",
        market_type=CryptoMarketType.USD_M_FUTURES,
        price_tick=d("0.01"),
        qty_step=d("0.001"),
        min_qty=d("0.001"),
        max_qty=d("1000"),
        min_notional=d("10"),
        max_notional=d("1000000"),
    )


def btc_bracket() -> LeverageBracket:
    return LeverageBracket(
        symbol="BTCUSDT",
        notional_floor=d("0"),
        notional_cap=d("50000"),
        initial_leverage=d("20"),
        maint_margin_ratio=d("0.004"),
        maint_amount=d("0"),
    )


def eth_bracket() -> LeverageBracket:
    return LeverageBracket(
        symbol="ETHUSDT",
        notional_floor=d("0"),
        notional_cap=d("50000"),
        initial_leverage=d("20"),
        maint_margin_ratio=d("0.004"),
        maint_amount=d("0"),
    )


def account() -> CryptoAccountRisk:
    return CryptoAccountRisk(
        equity=d("1000"),
        available_balance=d("800"),
        wallet_balance=d("1000"),
        margin_balance=d("1000"),
    )


class StaticSnapshotProvider:
    def __init__(self, snapshot: CryptoRiskSnapshot | Exception) -> None:
        self._snapshot = snapshot

    async def build(self, signal: Signal) -> CryptoRiskSnapshot:
        if isinstance(self._snapshot, Exception):
            raise self._snapshot
        return self._snapshot


def test_exchange_rule_guard_normalizes_and_checks_min_notional() -> None:
    result = ExchangeRuleGuard().check_order(
        spec=btc_spec(),
        side=OrderSide.BUY,
        qty=d("0.00249"),
        price=d("6000.123"),
    )

    assert result.ok is True
    assert result.normalized_qty == d("0.002")
    assert result.normalized_price == d("6000.10")
    assert result.notional == d("12.00020")

    too_small = ExchangeRuleGuard().check_order(
        spec=btc_spec(),
        side=OrderSide.BUY,
        qty=d("0.0019"),
        price=d("6000.123"),
    )

    assert too_small.ok is False
    assert too_small.rejection_reason == "MIN_NOTIONAL"


def test_open_order_exposure_counts_pending_orders_without_crediting_reduce_only() -> None:
    result = OpenOrderExposureCalculator().calculate_symbol_exposure(
        symbol="BTCUSDT",
        positions=[
            CryptoPositionRisk(
                symbol="BTCUSDT",
                qty=d("1"),
                entry_price=d("95"),
                mark_price=d("100"),
                leverage=d("5"),
            )
        ],
        open_orders=[
            OpenOrderRisk(
                cl_ord_id="open-buy",
                symbol="BTCUSDT",
                side=OrderSide.BUY,
                qty=d("0.5"),
                filled_qty=d("0.1"),
                price=d("110"),
                reduce_only=False,
            ),
            OpenOrderRisk(
                cl_ord_id="reduce-sell",
                symbol="BTCUSDT",
                side=OrderSide.SELL,
                qty=d("1"),
                filled_qty=d("0"),
                price=d("90"),
                reduce_only=True,
            ),
        ],
        mark_price=d("100"),
    )

    assert result.current_qty == d("1")
    assert result.pending_open_qty == d("0.4")
    assert result.pending_open_notional == d("44.0")
    assert result.risk_qty_after_open_orders == d("1.4")
    assert result.total_risk_notional == d("144.0")


def test_portfolio_exposure_aggregator_groups_cluster_risk() -> None:
    exposures = PortfolioExposureAggregator().calculate_cluster_exposures(
        positions=[
            CryptoPositionRisk(
                symbol="BTCUSDT",
                qty=d("0.1"),
                entry_price=d("49000"),
                mark_price=d("50000"),
                leverage=d("5"),
            )
        ],
        open_orders=[
            OpenOrderRisk(
                cl_ord_id="eth-open",
                symbol="ETHUSDT",
                side=OrderSide.BUY,
                qty=d("2"),
                filled_qty=d("0"),
                price=d("3000"),
            ),
            OpenOrderRisk(
                cl_ord_id="eth-reduce",
                symbol="ETHUSDT",
                side=OrderSide.SELL,
                qty=d("1"),
                filled_qty=d("0"),
                price=d("2990"),
                reduce_only=True,
            ),
        ],
        mark_prices={"BTCUSDT": d("50000"), "ETHUSDT": d("3000")},
        symbol_clusters={"BTCUSDT": "BTC_BETA", "ETHUSDT": "BTC_BETA"},
    )

    assert exposures["BTC_BETA"].symbols == ("BTCUSDT", "ETHUSDT")
    assert exposures["BTC_BETA"].total_risk_notional == d("11000")


def test_margin_risk_calculator_uses_leverage_bracket_and_fails_closed() -> None:
    calculator = MarginRiskCalculator()
    position = CryptoPositionRisk(
        symbol="BTCUSDT",
        qty=d("0.5"),
        entry_price=d("20000"),
        mark_price=d("21000"),
        leverage=d("10"),
    )

    result = calculator.evaluate_position(
        account=account(),
        position=position,
        brackets=[btc_bracket()],
    )

    assert result.ok is True
    assert result.notional == d("10500.0")
    assert result.initial_margin == d("1050.0")
    assert result.maintenance_margin == d("42.0000")
    assert result.margin_ratio == d("0.0420")

    missing = calculator.evaluate_position(
        account=account(),
        position=position,
        brackets=[],
    )

    assert missing.ok is False
    assert missing.rejection_reason == "MISSING_LEVERAGE_BRACKET"


@pytest.mark.asyncio
async def test_crypto_pre_trade_plugin_rejects_cap_breach_from_open_orders() -> None:
    snapshot = CryptoRiskSnapshot(
        account=account(),
        instrument_specs={"BTCUSDT": btc_spec()},
        leverage_brackets={"BTCUSDT": [btc_bracket()]},
        positions=[],
        open_orders=[
            OpenOrderRisk(
                cl_ord_id="pending",
                symbol="BTCUSDT",
                side=OrderSide.BUY,
                qty=d("0.5"),
                filled_qty=d("0"),
                price=d("20000"),
            )
        ],
        mark_prices={"BTCUSDT": d("20000")},
        risk_budget=CryptoRiskBudget(
            symbol_notional_caps={"BTCUSDT": d("10000")},
            total_notional_cap=d("20000"),
            max_margin_ratio=d("0.50"),
        ),
    )
    plugin = CryptoPreTradeRiskPlugin(StaticSnapshotProvider(snapshot))

    result = await plugin.check(
        signal=Signal(
            signal_type=SignalType.LONG,
            symbol="BTCUSDT",
            price=d("20000"),
            quantity=d("0.1"),
        ),
        metrics=RiskMetrics(),
        engine=None,
    )

    assert result is not None
    assert result.passed is False
    assert result.rejection_reason == RejectionReason.CRYPTO_OPEN_ORDER_EXPOSURE


@pytest.mark.asyncio
async def test_crypto_pre_trade_plugin_rejects_cluster_cap_breach() -> None:
    snapshot = CryptoRiskSnapshot(
        account=account(),
        instrument_specs={"BTCUSDT": btc_spec(), "ETHUSDT": eth_spec()},
        leverage_brackets={"BTCUSDT": [btc_bracket()], "ETHUSDT": [eth_bracket()]},
        positions=[
            CryptoPositionRisk(
                symbol="BTCUSDT",
                qty=d("0.1"),
                entry_price=d("49000"),
                mark_price=d("50000"),
                leverage=d("10"),
            )
        ],
        open_orders=[
            OpenOrderRisk(
                cl_ord_id="pending-eth",
                symbol="ETHUSDT",
                side=OrderSide.BUY,
                qty=d("1"),
                filled_qty=d("0"),
                price=d("3000"),
            )
        ],
        mark_prices={"BTCUSDT": d("50000"), "ETHUSDT": d("3000")},
        risk_budget=CryptoRiskBudget(
            symbol_notional_caps={"BTCUSDT": d("50000"), "ETHUSDT": d("50000")},
            total_notional_cap=d("100000"),
            max_margin_ratio=d("0.50"),
            symbol_clusters={"BTCUSDT": "BTC_BETA", "ETHUSDT": "BTC_BETA"},
            cluster_notional_caps={"BTC_BETA": d("10000")},
        ),
    )
    plugin = CryptoPreTradeRiskPlugin(StaticSnapshotProvider(snapshot))

    result = await plugin.check(
        signal=Signal(
            signal_type=SignalType.LONG,
            symbol="ETHUSDT",
            price=d("3000"),
            quantity=d("1"),
        ),
        metrics=RiskMetrics(),
        engine=None,
    )

    assert result is not None
    assert result.passed is False
    assert result.rejection_reason == RejectionReason.CRYPTO_CLUSTER_EXPOSURE
    assert result.details["cluster"] == "BTC_BETA"


@pytest.mark.asyncio
async def test_crypto_pre_trade_plugin_passes_valid_snapshot() -> None:
    snapshot = CryptoRiskSnapshot(
        account=account(),
        instrument_specs={"BTCUSDT": btc_spec()},
        leverage_brackets={"BTCUSDT": [btc_bracket()]},
        positions=[],
        open_orders=[],
        mark_prices={"BTCUSDT": d("20000")},
        risk_budget=CryptoRiskBudget(
            symbol_notional_caps={"BTCUSDT": d("10000")},
            total_notional_cap=d("20000"),
            max_margin_ratio=d("0.50"),
        ),
    )
    plugin = CryptoPreTradeRiskPlugin(StaticSnapshotProvider(snapshot))

    result = await plugin.check(
        signal=Signal(
            signal_type=SignalType.LONG,
            symbol="BTCUSDT",
            price=d("20000"),
            quantity=d("0.1"),
        ),
        metrics=RiskMetrics(),
        engine=None,
    )

    assert result is None


@pytest.mark.asyncio
async def test_crypto_pre_trade_plugin_rejects_stale_funding_oi_when_threshold_enabled() -> None:
    snapshot = CryptoRiskSnapshot(
        account=account(),
        instrument_specs={"BTCUSDT": btc_spec()},
        leverage_brackets={"BTCUSDT": [btc_bracket()]},
        positions=[],
        open_orders=[],
        mark_prices={"BTCUSDT": d("20000")},
        risk_budget=CryptoRiskBudget(max_abs_funding_rate_z_score=d("2.0")),
        funding_oi_metrics={
            "BTCUSDT": CryptoFundingOIRiskMetrics(
                symbol="BTCUSDT",
                current_funding_rate=d("0.0001"),
                funding_rate_z_score=None,
                funding_data_stale=True,
                funding_history_count=20,
            )
        },
    )
    plugin = CryptoPreTradeRiskPlugin(StaticSnapshotProvider(snapshot))

    result = await plugin.check(
        signal=Signal(
            signal_type=SignalType.LONG,
            symbol="BTCUSDT",
            price=d("20000"),
            quantity=d("0.1"),
        ),
        metrics=RiskMetrics(),
        engine=None,
    )

    assert result is not None
    assert result.passed is False
    assert result.rejection_reason == RejectionReason.CRYPTO_FUNDING_OI_RISK
    assert result.details["funding_data_stale"] is True


@pytest.mark.asyncio
async def test_crypto_pre_trade_plugin_rejects_extreme_open_interest_when_threshold_enabled() -> (
    None
):
    snapshot = CryptoRiskSnapshot(
        account=account(),
        instrument_specs={"BTCUSDT": btc_spec()},
        leverage_brackets={"BTCUSDT": [btc_bracket()]},
        positions=[],
        open_orders=[],
        mark_prices={"BTCUSDT": d("20000")},
        risk_budget=CryptoRiskBudget(max_abs_open_interest_change_rate=d("10")),
        funding_oi_metrics={
            "BTCUSDT": CryptoFundingOIRiskMetrics(
                symbol="BTCUSDT",
                current_open_interest=d("1200"),
                open_interest_change_rate=25.0,
                oi_history_count=20,
            )
        },
    )
    plugin = CryptoPreTradeRiskPlugin(StaticSnapshotProvider(snapshot))

    result = await plugin.check(
        signal=Signal(
            signal_type=SignalType.LONG,
            symbol="BTCUSDT",
            price=d("20000"),
            quantity=d("0.1"),
        ),
        metrics=RiskMetrics(),
        engine=None,
    )

    assert result is not None
    assert result.passed is False
    assert result.rejection_reason == RejectionReason.CRYPTO_FUNDING_OI_RISK
    assert result.details["open_interest_change_rate"] == 25.0


@pytest.mark.asyncio
async def test_crypto_pre_trade_plugin_fails_closed_when_snapshot_unavailable() -> None:
    plugin = CryptoPreTradeRiskPlugin(StaticSnapshotProvider(RuntimeError("snapshot down")))

    result = await plugin.check(
        signal=Signal(
            signal_type=SignalType.LONG,
            symbol="BTCUSDT",
            price=d("20000"),
            quantity=d("0.1"),
        ),
        metrics=RiskMetrics(),
        engine=None,
    )

    assert result is not None
    assert result.passed is False
    assert result.rejection_reason == RejectionReason.RISK_SYSTEM_ERROR
    assert "risk_sizing_decision" in result.details
    sizing_decision = result.details["risk_sizing_decision"]
    assert sizing_decision["reason"] == "SNAPSHOT_UNAVAILABLE"
    assert sizing_decision["decision"] == "reject"
    assert sizing_decision["max_allowed_qty"] == "0"
    assert sizing_decision["requested_qty"] == "0.1"


@pytest.mark.asyncio
async def test_crypto_pre_trade_plugin_rejects_non_trade_signal_type() -> None:
    snapshot = CryptoRiskSnapshot(
        account=account(),
        instrument_specs={"BTCUSDT": btc_spec()},
        leverage_brackets={"BTCUSDT": [btc_bracket()]},
        positions=[],
        open_orders=[],
        mark_prices={"BTCUSDT": d("20000")},
        risk_budget=CryptoRiskBudget(
            symbol_notional_caps={"BTCUSDT": d("10000")},
            total_notional_cap=d("20000"),
            max_margin_ratio=d("0.50"),
        ),
    )
    plugin = CryptoPreTradeRiskPlugin(StaticSnapshotProvider(snapshot))

    result = await plugin.check(
        signal=Signal(
            signal_type=SignalType.NONE,
            symbol="BTCUSDT",
            price=d("20000"),
            quantity=d("0.1"),
        ),
        metrics=RiskMetrics(),
        engine=None,
    )

    assert result is not None
    assert result.passed is False
    assert result.rejection_reason == RejectionReason.CRYPTO_EXCHANGE_RULE


@pytest.mark.asyncio
async def test_missing_mark_price_rejection_contains_risk_sizing_decision() -> None:
    snapshot = CryptoRiskSnapshot(
        account=account(),
        instrument_specs={"BTCUSDT": btc_spec()},
        leverage_brackets={"BTCUSDT": [btc_bracket()]},
        positions=[],
        open_orders=[],
        mark_prices={},
        risk_budget=CryptoRiskBudget(),
    )
    plugin = CryptoPreTradeRiskPlugin(StaticSnapshotProvider(snapshot))

    result = await plugin.check(
        signal=Signal(
            signal_type=SignalType.LONG,
            symbol="BTCUSDT",
            price=d("50000"),
            quantity=d("1"),
        ),
        metrics=RiskMetrics(),
        engine=None,
    )

    assert result is not None
    assert result.passed is False
    assert result.rejection_reason == RejectionReason.RISK_SYSTEM_ERROR
    assert "risk_sizing_decision" in result.details
    sizing_decision = result.details["risk_sizing_decision"]
    assert sizing_decision["reason"] == "NO_MARK_PRICE"
    assert sizing_decision["decision"] == "reject"
    assert sizing_decision["max_allowed_qty"] == "0"
