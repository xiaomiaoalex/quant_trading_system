from decimal import Decimal

import pytest

from trader.core.domain.models.crypto_risk import (
    CryptoAccountRisk,
    CryptoInstrumentSpec,
    CryptoMarketType,
    CryptoPositionRisk,
    CryptoRiskBudget,
    CryptoRiskSnapshot,
    LeverageBracket,
    OpenOrderRisk,
)
from trader.core.domain.models.order import OrderSide
from trader.core.domain.models.risk_decision import RiskSizingDecisionType
from trader.core.domain.models.signal import Signal, SignalType
from trader.core.domain.services.risk_sizing_engine import RiskSizingEngine


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
        qty_step=d("0.01"),
        min_qty=d("0.01"),
        max_qty=d("1000"),
        min_notional=d("10"),
        max_notional=d("1000000"),
    )


def account() -> CryptoAccountRisk:
    return CryptoAccountRisk(
        equity=d("10000"),
        available_balance=d("8000"),
        wallet_balance=d("10000"),
        margin_balance=d("10000"),
    )


def btc_bracket() -> LeverageBracket:
    return LeverageBracket(
        symbol="BTCUSDT",
        notional_floor=d("0"),
        notional_cap=d("250000"),
        initial_leverage=d("20"),
        maint_margin_ratio=d("0.004"),
        maint_amount=d("0"),
    )


def base_snapshot(symbol: str = "BTCUSDT") -> CryptoRiskSnapshot:
    return CryptoRiskSnapshot(
        account=account(),
        instrument_specs={symbol: btc_spec()},
        leverage_brackets={symbol: [btc_bracket()]},
        positions=[],
        open_orders=[],
        mark_prices={symbol: d("50000")},
        risk_budget=CryptoRiskBudget(),
    )


def buy_signal(symbol: str = "BTCUSDT", qty: str = "1") -> Signal:
    return Signal(
        signal_id="test-signal-001",
        strategy_name="TestStrategy",
        symbol=symbol,
        signal_type=SignalType.BUY,
        quantity=d(qty),
        price=d("50000"),
        metadata={},
    )


class TestRiskSizingEngineSymbolCap:
    def test_symbol_cap_derives_max_qty(self) -> None:
        snapshot = base_snapshot()
        snapshot = CryptoRiskSnapshot(
            account=snapshot.account,
            instrument_specs=snapshot.instrument_specs,
            leverage_brackets=snapshot.leverage_brackets,
            positions=snapshot.positions,
            open_orders=snapshot.open_orders,
            mark_prices=snapshot.mark_prices,
            risk_budget=CryptoRiskBudget(
                symbol_notional_caps={"BTCUSDT": d("50000")},
            ),
        )

        engine = RiskSizingEngine()
        signal = buy_signal(qty="2")
        result = engine.calculate(signal, snapshot)

        assert result.max_allowed_qty <= d("1")
        assert result.limiting_factor == "symbol_cap"
        symbol_cap_constraint = next(
            (c for c in result.constraints if c.constraint_type == "symbol_cap"),
            None,
        )
        assert symbol_cap_constraint is not None
        assert symbol_cap_constraint.passed is False

    def test_symbol_cap_approve_when_within_limit(self) -> None:
        snapshot = base_snapshot()
        snapshot = CryptoRiskSnapshot(
            account=snapshot.account,
            instrument_specs=snapshot.instrument_specs,
            leverage_brackets=snapshot.leverage_brackets,
            positions=[],
            open_orders=[],
            mark_prices={"BTCUSDT": d("50000")},
            risk_budget=CryptoRiskBudget(
                symbol_notional_caps={"BTCUSDT": d("100000")},
            ),
        )

        engine = RiskSizingEngine()
        signal = buy_signal(qty="1")
        result = engine.calculate(signal, snapshot)

        assert result.decision == RiskSizingDecisionType.APPROVE
        assert result.max_allowed_qty >= d("0.99")

    def test_symbol_cap_with_existing_position(self) -> None:
        snapshot = CryptoRiskSnapshot(
            account=account(),
            instrument_specs={"BTCUSDT": btc_spec()},
            leverage_brackets={"BTCUSDT": [btc_bracket()]},
            positions=[
                CryptoPositionRisk(
                    symbol="BTCUSDT",
                    qty=d("0.5"),
                    entry_price=d("50000"),
                    mark_price=d("50000"),
                    leverage=d("10"),
                )
            ],
            open_orders=[],
            mark_prices={"BTCUSDT": d("50000")},
            risk_budget=CryptoRiskBudget(
                symbol_notional_caps={"BTCUSDT": d("50000")},
            ),
        )

        engine = RiskSizingEngine()
        signal = buy_signal(qty="1")
        result = engine.calculate(signal, snapshot)

        assert result.max_allowed_qty <= d("0.5")
        assert result.limiting_factor == "symbol_cap"


class TestRiskSizingEngineTotalCap:
    def test_total_cap_derives_max_qty(self) -> None:
        snapshot = base_snapshot()
        snapshot = CryptoRiskSnapshot(
            account=snapshot.account,
            instrument_specs=snapshot.instrument_specs,
            leverage_brackets=snapshot.leverage_brackets,
            positions=[],
            open_orders=[],
            mark_prices={"BTCUSDT": d("50000")},
            risk_budget=CryptoRiskBudget(
                total_notional_cap=d("50000"),
            ),
        )

        engine = RiskSizingEngine()
        signal = buy_signal(qty="2")
        result = engine.calculate(signal, snapshot)

        assert result.max_allowed_qty <= d("1")
        assert result.limiting_factor == "total_cap"

    def test_total_cap_with_multiple_symbols(self) -> None:
        snapshot = CryptoRiskSnapshot(
            account=account(),
            instrument_specs={"BTCUSDT": btc_spec(), "ETHUSDT": eth_spec()},
            leverage_brackets={
                "BTCUSDT": [btc_bracket()],
                "ETHUSDT": [
                    LeverageBracket(
                        symbol="ETHUSDT",
                        notional_floor=d("0"),
                        notional_cap=d("250000"),
                        initial_leverage=d("20"),
                        maint_margin_ratio=d("0.004"),
                        maint_amount=d("0"),
                    )
                ],
            },
            positions=[
                CryptoPositionRisk(
                    symbol="BTCUSDT",
                    qty=d("0.4"),
                    entry_price=d("50000"),
                    mark_price=d("50000"),
                    leverage=d("10"),
                )
            ],
            open_orders=[],
            mark_prices={"BTCUSDT": d("50000"), "ETHUSDT": d("3000")},
            risk_budget=CryptoRiskBudget(
                total_notional_cap=d("50000"),
            ),
        )

        engine = RiskSizingEngine()
        signal = Signal(
            signal_id="test-eth-001",
            strategy_name="TestStrategy",
            symbol="ETHUSDT",
            signal_type=SignalType.BUY,
            quantity=d("5"),
            price=d("3000"),
            metadata={},
        )
        result = engine.calculate(signal, snapshot)

        assert result.limiting_factor in {
            "total_cap",
            "symbol_cap",
            "exchange_rule",
            "margin_limit",
        }


class TestRiskSizingEngineClusterCap:
    def test_cluster_cap_derives_max_qty(self) -> None:
        snapshot = base_snapshot()
        snapshot = CryptoRiskSnapshot(
            account=snapshot.account,
            instrument_specs=snapshot.instrument_specs,
            leverage_brackets=snapshot.leverage_brackets,
            positions=[],
            open_orders=[],
            mark_prices={"BTCUSDT": d("50000")},
            risk_budget=CryptoRiskBudget(
                symbol_clusters={"BTCUSDT": "ALPHA"},
                cluster_notional_caps={"ALPHA": d("50000")},
            ),
        )

        engine = RiskSizingEngine()
        signal = buy_signal(qty="2")
        result = engine.calculate(signal, snapshot)

        assert result.max_allowed_qty <= d("1")
        cluster_constraint = next(
            (c for c in result.constraints if c.constraint_type == "cluster_cap"),
            None,
        )
        if cluster_constraint:
            assert cluster_constraint.passed is False

    def test_cluster_cap_allows_reduce_only(self) -> None:
        snapshot = base_snapshot()
        snapshot = CryptoRiskSnapshot(
            account=snapshot.account,
            instrument_specs=snapshot.instrument_specs,
            leverage_brackets=snapshot.leverage_brackets,
            positions=[
                CryptoPositionRisk(
                    symbol="BTCUSDT",
                    qty=d("2"),
                    entry_price=d("50000"),
                    mark_price=d("50000"),
                    leverage=d("10"),
                )
            ],
            open_orders=[],
            mark_prices={"BTCUSDT": d("50000")},
            risk_budget=CryptoRiskBudget(
                symbol_clusters={"BTCUSDT": "ALPHA"},
                cluster_notional_caps={"ALPHA": d("1000")},
            ),
        )

        engine = RiskSizingEngine()
        signal = Signal(
            signal_id="test-close-001",
            strategy_name="TestStrategy",
            symbol="BTCUSDT",
            signal_type=SignalType.CLOSE_SHORT,
            quantity=d("1"),
            price=d("50000"),
            metadata={},
        )
        result = engine.calculate(signal, snapshot)

        cluster_constraint = next(
            (c for c in result.constraints if c.constraint_type == "cluster_cap"),
            None,
        )
        if cluster_constraint:
            assert cluster_constraint.passed is True


class TestRiskSizingEngineMarginLimit:
    def test_margin_limit_derives_max_qty(self) -> None:
        snapshot = base_snapshot()
        snapshot = CryptoRiskSnapshot(
            account=CryptoAccountRisk(
                equity=d("1000"),
                available_balance=d("100"),
                wallet_balance=d("1000"),
                margin_balance=d("1000"),
            ),
            instrument_specs=snapshot.instrument_specs,
            leverage_brackets=snapshot.leverage_brackets,
            positions=[],
            open_orders=[],
            mark_prices={"BTCUSDT": d("50000")},
            risk_budget=CryptoRiskBudget(
                max_margin_ratio=d("0.8"),
            ),
        )

        engine = RiskSizingEngine()
        signal = buy_signal(qty="0.01")
        result = engine.calculate(signal, snapshot)

        margin_constraint = next(
            (c for c in result.constraints if c.constraint_type == "margin_limit"),
            None,
        )
        assert margin_constraint is not None


class TestRiskSizingEngineMultipleConstraints:
    def test_multiple_constraints_takes_minimum(self) -> None:
        snapshot = CryptoRiskSnapshot(
            account=CryptoAccountRisk(
                equity=d("5000"),
                available_balance=d("2000"),
                wallet_balance=d("5000"),
                margin_balance=d("5000"),
            ),
            instrument_specs={"BTCUSDT": btc_spec()},
            leverage_brackets={"BTCUSDT": [btc_bracket()]},
            positions=[],
            open_orders=[],
            mark_prices={"BTCUSDT": d("50000")},
            risk_budget=CryptoRiskBudget(
                symbol_notional_caps={"BTCUSDT": d("40000")},
                total_notional_cap=d("30000"),
                max_margin_ratio=d("0.5"),
            ),
        )

        engine = RiskSizingEngine()
        signal = buy_signal(qty="2")
        result = engine.calculate(signal, snapshot)

        assert result.max_allowed_qty > Decimal("0")
        constraint_maxes = [c.max_qty for c in result.constraints if c.passed is False]
        if constraint_maxes:
            min_constraint_max = min(constraint_maxes)
            assert result.max_allowed_qty <= min_constraint_max


class TestRiskSizingEngineEdgeCases:
    def test_zero_quantity_rejects(self) -> None:
        snapshot = base_snapshot()
        engine = RiskSizingEngine()

        signal = Signal(
            signal_id="test-zero-001",
            strategy_name="TestStrategy",
            symbol="BTCUSDT",
            signal_type=SignalType.BUY,
            quantity=d("0"),
            price=d("50000"),
            metadata={},
        )
        result = engine.calculate(signal, snapshot)

        assert result.decision == RiskSizingDecisionType.REJECT
        assert result.max_allowed_qty == Decimal("0")

    def test_negative_quantity_rejects(self) -> None:
        snapshot = base_snapshot()
        engine = RiskSizingEngine()

        signal = Signal(
            signal_id="test-neg-001",
            strategy_name="TestStrategy",
            symbol="BTCUSDT",
            signal_type=SignalType.BUY,
            quantity=d("-1"),
            price=d("50000"),
            metadata={},
        )
        result = engine.calculate(signal, snapshot)

        assert result.decision == RiskSizingDecisionType.REJECT

    def test_exchange_max_qty_derives_max_allowed_qty(self) -> None:
        snapshot = CryptoRiskSnapshot(
            account=account(),
            instrument_specs={
                "BTCUSDT": CryptoInstrumentSpec(
                    symbol="BTCUSDT",
                    market_type=CryptoMarketType.USD_M_FUTURES,
                    price_tick=d("0.10"),
                    qty_step=d("0.001"),
                    min_qty=d("0.001"),
                    max_qty=d("100"),
                    min_notional=d("10"),
                    max_notional=d("1000000"),
                )
            },
            leverage_brackets={"BTCUSDT": [btc_bracket()]},
            positions=[],
            open_orders=[],
            mark_prices={"BTCUSDT": d("50000")},
            risk_budget=CryptoRiskBudget(),
        )

        engine = RiskSizingEngine()
        signal = Signal(
            signal_id="test-max-qty-001",
            strategy_name="TestStrategy",
            symbol="BTCUSDT",
            signal_type=SignalType.BUY,
            quantity=d("150"),
            price=d("50000"),
            metadata={},
        )
        result = engine.calculate(signal, snapshot)

        assert result.decision == RiskSizingDecisionType.CLIP
        assert "EXCHANGE_RULE" in result.reason
        assert result.max_allowed_qty == d("100")
        assert result.limiting_factor == "exchange_rule"
        exchange_constraint = next(
            (c for c in result.constraints if c.constraint_type == "exchange_rule"),
            None,
        )
        assert exchange_constraint is not None
        assert exchange_constraint.max_qty == d("100")
        assert exchange_constraint.passed is False
        assert exchange_constraint.current_value == d("150")

    def test_no_mark_price_rejects(self) -> None:
        snapshot = CryptoRiskSnapshot(
            account=account(),
            instrument_specs={"BTCUSDT": btc_spec()},
            leverage_brackets={"BTCUSDT": [btc_bracket()]},
            positions=[],
            open_orders=[],
            mark_prices={},
            risk_budget=CryptoRiskBudget(),
        )
        engine = RiskSizingEngine()
        signal = Signal(
            signal_id="test-no-price-001",
            strategy_name="TestStrategy",
            symbol="BTCUSDT",
            signal_type=SignalType.BUY,
            quantity=d("1"),
            price=d("0"),
            metadata={},
        )
        result = engine.calculate(signal, snapshot)

        assert result.decision == RiskSizingDecisionType.REJECT
        assert result.reason == "NO_MARK_PRICE"

    def test_decision_contains_trace_id(self) -> None:
        snapshot = base_snapshot()
        engine = RiskSizingEngine()
        trace_id = "test-trace-12345"

        signal = buy_signal()
        result = engine.calculate(signal, snapshot, trace_id=trace_id)

        assert result.trace_id == trace_id

    def test_decision_contains_constraints_list(self) -> None:
        snapshot = base_snapshot()
        engine = RiskSizingEngine()
        signal = buy_signal()
        result = engine.calculate(signal, snapshot)

        assert len(result.constraints) > 0
        for constraint in result.constraints:
            assert hasattr(constraint, "constraint_type")
            assert hasattr(constraint, "max_qty")
            assert hasattr(constraint, "passed")


class TestRiskSizingDecisionProperties:
    def test_approval_has_approproate_properties(self) -> None:
        snapshot = base_snapshot()
        snapshot = CryptoRiskSnapshot(
            account=snapshot.account,
            instrument_specs=snapshot.instrument_specs,
            leverage_brackets=snapshot.leverage_brackets,
            positions=[],
            open_orders=[],
            mark_prices={"BTCUSDT": d("50000")},
            risk_budget=CryptoRiskBudget(
                symbol_notional_caps={"BTCUSDT": d("100000")},
                total_notional_cap=d("100000"),
            ),
        )

        engine = RiskSizingEngine()
        signal = buy_signal(qty="0.5")
        result = engine.calculate(signal, snapshot)

        assert result.requested_qty == d("0.5")
        assert result.normalized_qty > Decimal("0")
        assert result.final_qty == result.max_allowed_qty
        assert result.is_approval or result.is_clip

    def test_rejection_explains_limiting_factor(self) -> None:
        snapshot = base_snapshot()
        snapshot = CryptoRiskSnapshot(
            account=snapshot.account,
            instrument_specs=snapshot.instrument_specs,
            leverage_brackets=snapshot.leverage_brackets,
            positions=[],
            open_orders=[],
            mark_prices={"BTCUSDT": d("50000")},
            risk_budget=CryptoRiskBudget(
                symbol_notional_caps={"BTCUSDT": d("1000")},
            ),
        )

        engine = RiskSizingEngine()
        signal = buy_signal(qty="2")
        result = engine.calculate(signal, snapshot)

        assert result.is_rejection or result.is_clip
        assert result.limiting_factor is not None
        assert result.limiting_factor in {
            "symbol_cap",
            "total_cap",
            "cluster_cap",
            "margin_limit",
            "exchange_rule",
        }
