from __future__ import annotations

from decimal import Decimal

import pytest

from trader.core.domain.models.crypto_risk import (
    CryptoAccountRisk,
    CryptoPositionRisk,
    LeverageBracket,
    MarginMode,
)
from trader.core.domain.services.margin_risk_calculator import (
    FeeBufferConfig,
    MarginRiskCalculator,
    MarginRiskResult,
)


def d(value: str) -> Decimal:
    return Decimal(value)


class TestMarginRiskCalculatorBasic:
    def test_evaluate_position_approves_valid_long_position(self) -> None:
        calculator = MarginRiskCalculator()
        account = CryptoAccountRisk(
            equity=d("10000"),
            available_balance=d("8000"),
            wallet_balance=d("10000"),
            margin_balance=d("10000"),
        )
        position = CryptoPositionRisk(
            symbol="BTCUSDT",
            qty=d("1"),
            entry_price=d("50000"),
            mark_price=d("50000"),
            leverage=d("10"),
        )
        brackets = [
            LeverageBracket(
                symbol="BTCUSDT",
                notional_floor=d("0"),
                notional_cap=d("250000"),
                initial_leverage=d("20"),
                maint_margin_ratio=d("0.004"),
                maint_amount=d("0"),
            )
        ]

        result = calculator.evaluate_position(account, position, brackets)

        assert result.ok is True
        assert result.notional == d("50000")
        assert result.initial_margin == d("5000")
        assert result.maintenance_margin == d("200")
        assert result.margin_ratio == d("0.02")
        assert result.bracket is not None

    def test_evaluate_position_approves_valid_short_position(self) -> None:
        calculator = MarginRiskCalculator()
        account = CryptoAccountRisk(
            equity=d("10000"),
            available_balance=d("8000"),
            wallet_balance=d("10000"),
            margin_balance=d("10000"),
        )
        position = CryptoPositionRisk(
            symbol="BTCUSDT",
            qty=d("-1"),
            entry_price=d("50000"),
            mark_price=d("48000"),
            leverage=d("10"),
        )
        brackets = [
            LeverageBracket(
                symbol="BTCUSDT",
                notional_floor=d("0"),
                notional_cap=d("250000"),
                initial_leverage=d("20"),
                maint_margin_ratio=d("0.004"),
                maint_amount=d("0"),
            )
        ]

        result = calculator.evaluate_position(account, position, brackets)

        assert result.ok is True
        assert result.notional == d("48000")
        assert result.initial_margin == d("4800")
        assert result.maintenance_margin == d("192")
        assert result.margin_ratio == d("0.0192")
        assert result.bracket is not None


class TestMarginRiskCalculatorBracketTiers:
    def test_bracket_tier_selection_low_notional(self) -> None:
        calculator = MarginRiskCalculator()
        brackets = [
            LeverageBracket(
                symbol="BTCUSDT",
                notional_floor=d("0"),
                notional_cap=d("50000"),
                initial_leverage=d("20"),
                maint_margin_ratio=d("0.004"),
                maint_amount=d("0"),
            ),
            LeverageBracket(
                symbol="BTCUSDT",
                notional_floor=d("50000"),
                notional_cap=d("250000"),
                initial_leverage=d("15"),
                maint_margin_ratio=d("0.005"),
                maint_amount=d("0"),
            ),
            LeverageBracket(
                symbol="BTCUSDT",
                notional_floor=d("250000"),
                notional_cap=d("1000000"),
                initial_leverage=d("10"),
                maint_margin_ratio=d("0.01"),
                maint_amount=d("0"),
            ),
        ]

        selected = calculator.select_bracket(d("30000"), brackets)
        assert selected is not None
        assert selected.notional_cap == d("50000")
        assert selected.initial_leverage == d("20")
        assert selected.maint_margin_ratio == d("0.004")

    def test_bracket_tier_selection_mid_notional(self) -> None:
        calculator = MarginRiskCalculator()
        brackets = [
            LeverageBracket(
                symbol="BTCUSDT",
                notional_floor=d("0"),
                notional_cap=d("50000"),
                initial_leverage=d("20"),
                maint_margin_ratio=d("0.004"),
                maint_amount=d("0"),
            ),
            LeverageBracket(
                symbol="BTCUSDT",
                notional_floor=d("50000"),
                notional_cap=d("250000"),
                initial_leverage=d("15"),
                maint_margin_ratio=d("0.005"),
                maint_amount=d("0"),
            ),
            LeverageBracket(
                symbol="BTCUSDT",
                notional_floor=d("250000"),
                notional_cap=d("1000000"),
                initial_leverage=d("10"),
                maint_margin_ratio=d("0.01"),
                maint_amount=d("0"),
            ),
        ]

        selected = calculator.select_bracket(d("100000"), brackets)
        assert selected is not None
        assert selected.notional_cap == d("250000")
        assert selected.initial_leverage == d("15")

    def test_bracket_tier_selection_high_notional(self) -> None:
        calculator = MarginRiskCalculator()
        brackets = [
            LeverageBracket(
                symbol="BTCUSDT",
                notional_floor=d("0"),
                notional_cap=d("50000"),
                initial_leverage=d("20"),
                maint_margin_ratio=d("0.004"),
                maint_amount=d("0"),
            ),
            LeverageBracket(
                symbol="BTCUSDT",
                notional_floor=d("50000"),
                notional_cap=d("250000"),
                initial_leverage=d("15"),
                maint_margin_ratio=d("0.005"),
                maint_amount=d("0"),
            ),
            LeverageBracket(
                symbol="BTCUSDT",
                notional_floor=d("250000"),
                notional_cap=d("1000000"),
                initial_leverage=d("10"),
                maint_margin_ratio=d("0.01"),
                maint_amount=d("0"),
            ),
        ]

        selected = calculator.select_bracket(d("500000"), brackets)
        assert selected is not None
        assert selected.notional_cap == d("1000000")
        assert selected.initial_leverage == d("10")

    def test_missing_leverage_bracket_fails_closed(self) -> None:
        calculator = MarginRiskCalculator()
        account = CryptoAccountRisk(
            equity=d("10000"),
            available_balance=d("8000"),
            wallet_balance=d("10000"),
            margin_balance=d("10000"),
        )
        position = CryptoPositionRisk(
            symbol="EXOTICUSDT",
            qty=d("10"),
            entry_price=d("100"),
            mark_price=d("100"),
            leverage=d("5"),
        )
        brackets: list[LeverageBracket] = []

        result = calculator.evaluate_position(account, position, brackets)

        assert result.ok is False
        assert result.rejection_reason == "MISSING_LEVERAGE_BRACKET"

    def test_notional_above_all_brackets_fails_closed(self) -> None:
        calculator = MarginRiskCalculator()
        account = CryptoAccountRisk(
            equity=d("100000"),
            available_balance=d("80000"),
            wallet_balance=d("100000"),
            margin_balance=d("100000"),
        )
        position = CryptoPositionRisk(
            symbol="BTCUSDT",
            qty=d("50"),
            entry_price=d("50000"),
            mark_price=d("50000"),
            leverage=d("5"),
        )
        brackets = [
            LeverageBracket(
                symbol="BTCUSDT",
                notional_floor=d("0"),
                notional_cap=d("50000"),
                initial_leverage=d("20"),
                maint_margin_ratio=d("0.004"),
                maint_amount=d("0"),
            ),
        ]

        result = calculator.evaluate_position(account, position, brackets)

        assert result.ok is False
        assert result.rejection_reason == "MISSING_LEVERAGE_BRACKET"


class TestMarginRiskCalculatorMarginModes:
    def test_cross_margin_mode_reduces_available_margin(self) -> None:
        calculator = MarginRiskCalculator()
        account = CryptoAccountRisk(
            equity=d("10000"),
            available_balance=d("8000"),
            wallet_balance=d("10000"),
            margin_balance=d("10000"),
        )
        position = CryptoPositionRisk(
            symbol="BTCUSDT",
            qty=d("1"),
            entry_price=d("50000"),
            mark_price=d("50000"),
            leverage=d("10"),
            margin_mode=MarginMode.CROSS,
        )
        brackets = [
            LeverageBracket(
                symbol="BTCUSDT",
                notional_floor=d("0"),
                notional_cap=d("250000"),
                initial_leverage=d("20"),
                maint_margin_ratio=d("0.004"),
                maint_amount=d("0"),
            )
        ]

        result = calculator.evaluate_position(account, position, brackets)

        assert result.ok is True
        assert result.initial_margin == d("5000")

    def test_isolated_margin_mode_independent_buffer(self) -> None:
        calculator = MarginRiskCalculator()
        account = CryptoAccountRisk(
            equity=d("10000"),
            available_balance=d("8000"),
            wallet_balance=d("10000"),
            margin_balance=d("10000"),
        )
        position = CryptoPositionRisk(
            symbol="BTCUSDT",
            qty=d("1"),
            entry_price=d("50000"),
            mark_price=d("50000"),
            leverage=d("10"),
            margin_mode=MarginMode.ISOLATED,
        )
        brackets = [
            LeverageBracket(
                symbol="BTCUSDT",
                notional_floor=d("0"),
                notional_cap=d("250000"),
                initial_leverage=d("20"),
                maint_margin_ratio=d("0.004"),
                maint_amount=d("0"),
            )
        ]

        result = calculator.evaluate_position(account, position, brackets)

        assert result.ok is True
        assert result.initial_margin == d("5000")


class TestMarginRiskCalculatorLiquidationBuffer:
    def test_liquidation_buffer_long_position_positive(self) -> None:
        position = CryptoPositionRisk(
            symbol="BTCUSDT",
            qty=d("1"),
            entry_price=d("50000"),
            mark_price=d("50000"),
            leverage=d("10"),
            liquidation_price=d("45000"),
        )

        buffer = position.liquidation_buffer_ratio
        assert buffer is not None
        assert buffer > Decimal("0")
        assert buffer == d("0.10")

    def test_liquidation_buffer_short_position_positive(self) -> None:
        position = CryptoPositionRisk(
            symbol="BTCUSDT",
            qty=d("-1"),
            entry_price=d("50000"),
            mark_price=d("50000"),
            leverage=d("10"),
            liquidation_price=d("55000"),
        )

        buffer = position.liquidation_buffer_ratio
        assert buffer is not None
        assert buffer > Decimal("0")
        assert buffer == d("0.10")

    def test_liquidation_buffer_zero_when_no_liquidation_price(self) -> None:
        position = CryptoPositionRisk(
            symbol="BTCUSDT",
            qty=d("1"),
            entry_price=d("50000"),
            mark_price=d("50000"),
            leverage=d("10"),
            liquidation_price=None,
        )

        buffer = position.liquidation_buffer_ratio
        assert buffer is None

    def test_liquidation_buffer_zero_when_empty_position(self) -> None:
        position = CryptoPositionRisk(
            symbol="BTCUSDT",
            qty=d("0"),
            entry_price=d("50000"),
            mark_price=d("50000"),
            leverage=d("10"),
            liquidation_price=d("45000"),
        )

        buffer = position.liquidation_buffer_ratio
        assert buffer is None

    def test_liquidation_buffer_zero_when_zero_mark_price(self) -> None:
        position = CryptoPositionRisk(
            symbol="BTCUSDT",
            qty=d("1"),
            entry_price=d("50000"),
            mark_price=d("0"),
            leverage=d("10"),
            liquidation_price=d("45000"),
        )

        buffer = position.liquidation_buffer_ratio
        assert buffer is None


class TestMarginRiskCalculatorFailClosed:
    def test_zero_margin_balance_fails_closed(self) -> None:
        calculator = MarginRiskCalculator()
        account = CryptoAccountRisk(
            equity=d("10000"),
            available_balance=d("0"),
            wallet_balance=d("10000"),
            margin_balance=d("0"),
        )
        position = CryptoPositionRisk(
            symbol="BTCUSDT",
            qty=d("1"),
            entry_price=d("50000"),
            mark_price=d("50000"),
            leverage=d("10"),
        )
        brackets = [
            LeverageBracket(
                symbol="BTCUSDT",
                notional_floor=d("0"),
                notional_cap=d("250000"),
                initial_leverage=d("20"),
                maint_margin_ratio=d("0.004"),
                maint_amount=d("0"),
            )
        ]

        result = calculator.evaluate_position(account, position, brackets)

        assert result.ok is False
        assert result.rejection_reason == "INVALID_MARGIN_BALANCE"

    def test_negative_mark_price_fails_closed(self) -> None:
        calculator = MarginRiskCalculator()
        account = CryptoAccountRisk(
            equity=d("10000"),
            available_balance=d("8000"),
            wallet_balance=d("10000"),
            margin_balance=d("10000"),
        )
        position = CryptoPositionRisk(
            symbol="BTCUSDT",
            qty=d("1"),
            entry_price=d("50000"),
            mark_price=d("-100"),
            leverage=d("10"),
        )
        brackets = [
            LeverageBracket(
                symbol="BTCUSDT",
                notional_floor=d("0"),
                notional_cap=d("250000"),
                initial_leverage=d("20"),
                maint_margin_ratio=d("0.004"),
                maint_amount=d("0"),
            )
        ]

        result = calculator.evaluate_position(account, position, brackets)

        assert result.ok is False
        assert result.rejection_reason == "INVALID_POSITION_INPUT"

    def test_zero_leverage_fails_closed(self) -> None:
        calculator = MarginRiskCalculator()
        account = CryptoAccountRisk(
            equity=d("10000"),
            available_balance=d("8000"),
            wallet_balance=d("10000"),
            margin_balance=d("10000"),
        )
        position = CryptoPositionRisk(
            symbol="BTCUSDT",
            qty=d("1"),
            entry_price=d("50000"),
            mark_price=d("50000"),
            leverage=d("0"),
        )
        brackets = [
            LeverageBracket(
                symbol="BTCUSDT",
                notional_floor=d("0"),
                notional_cap=d("250000"),
                initial_leverage=d("20"),
                maint_margin_ratio=d("0.004"),
                maint_amount=d("0"),
            )
        ]

        result = calculator.evaluate_position(account, position, brackets)

        assert result.ok is False
        assert result.rejection_reason == "INVALID_POSITION_INPUT"

    def test_empty_position_returns_ok(self) -> None:
        calculator = MarginRiskCalculator()
        account = CryptoAccountRisk(
            equity=d("10000"),
            available_balance=d("8000"),
            wallet_balance=d("10000"),
            margin_balance=d("10000"),
        )
        position = CryptoPositionRisk(
            symbol="BTCUSDT",
            qty=d("0"),
            entry_price=d("50000"),
            mark_price=d("50000"),
            leverage=d("10"),
        )
        brackets = [
            LeverageBracket(
                symbol="BTCUSDT",
                notional_floor=d("0"),
                notional_cap=d("250000"),
                initial_leverage=d("20"),
                maint_margin_ratio=d("0.004"),
                maint_amount=d("0"),
            )
        ]

        result = calculator.evaluate_position(account, position, brackets)

        assert result.ok is True
        assert result.notional == Decimal("0")
        assert result.initial_margin == Decimal("0")


class TestMarginRiskCalculatorFeeBuffers:
    def test_funding_fee_reduces_maintenance_margin(self) -> None:
        calculator = MarginRiskCalculator(
            FeeBufferConfig(
                funding_rate=d("0.0003"),
                taker_fee_rate=d("0"),
                slippage_bps=d("0"),
                funding_interval_hours=8,
            )
        )
        account = CryptoAccountRisk(
            equity=d("10000"),
            available_balance=d("8000"),
            wallet_balance=d("10000"),
            margin_balance=d("10000"),
        )
        position = CryptoPositionRisk(
            symbol="BTCUSDT",
            qty=d("1"),
            entry_price=d("50000"),
            mark_price=d("50000"),
            leverage=d("10"),
        )
        brackets = [
            LeverageBracket(
                symbol="BTCUSDT",
                notional_floor=d("0"),
                notional_cap=d("250000"),
                initial_leverage=d("20"),
                maint_margin_ratio=d("0.004"),
                maint_amount=d("0"),
            )
        ]

        _, adjusted_maintenance, adjusted_ratio = calculator.calculate_risk_adjusted_margin(
            account, position, brackets
        )

        assert adjusted_maintenance == d("205")
        assert adjusted_ratio == d("0.0205")

    def test_taker_fee_buffer_reduces_risk_budget(self) -> None:
        calculator = MarginRiskCalculator(
            FeeBufferConfig(
                funding_rate=d("0"),
                taker_fee_rate=d("0.0004"),
                slippage_bps=d("0"),
            )
        )
        account = CryptoAccountRisk(
            equity=d("10000"),
            available_balance=d("8000"),
            wallet_balance=d("10000"),
            margin_balance=d("10000"),
        )
        position = CryptoPositionRisk(
            symbol="BTCUSDT",
            qty=d("1"),
            entry_price=d("50000"),
            mark_price=d("50000"),
            leverage=d("10"),
        )
        brackets = [
            LeverageBracket(
                symbol="BTCUSDT",
                notional_floor=d("0"),
                notional_cap=d("250000"),
                initial_leverage=d("20"),
                maint_margin_ratio=d("0.004"),
                maint_amount=d("0"),
            )
        ]

        _, adjusted_maintenance, adjusted_ratio = calculator.calculate_risk_adjusted_margin(
            account, position, brackets
        )

        assert adjusted_maintenance == d("220")
        assert adjusted_ratio == d("0.022")

    def test_slippage_buffer_considered_in_risk(self) -> None:
        calculator = MarginRiskCalculator(
            FeeBufferConfig(
                funding_rate=d("0"),
                taker_fee_rate=d("0"),
                slippage_bps=d("50"),
            )
        )
        account = CryptoAccountRisk(
            equity=d("10000"),
            available_balance=d("8000"),
            wallet_balance=d("10000"),
            margin_balance=d("10000"),
        )
        position = CryptoPositionRisk(
            symbol="BTCUSDT",
            qty=d("1"),
            entry_price=d("50000"),
            mark_price=d("50000"),
            leverage=d("10"),
        )
        brackets = [
            LeverageBracket(
                symbol="BTCUSDT",
                notional_floor=d("0"),
                notional_cap=d("250000"),
                initial_leverage=d("20"),
                maint_margin_ratio=d("0.004"),
                maint_amount=d("0"),
            )
        ]

        _, adjusted_maintenance, adjusted_ratio = calculator.calculate_risk_adjusted_margin(
            account, position, brackets
        )

        assert adjusted_maintenance == d("450")
        assert adjusted_ratio == d("0.045")


class TestLiquidationPriceCalculation:
    def test_long_position_liquidation_price(self) -> None:
        position = CryptoPositionRisk(
            symbol="BTCUSDT",
            qty=d("1"),
            entry_price=d("50000"),
            mark_price=d("50000"),
            leverage=d("10"),
        )
        bracket = LeverageBracket(
            symbol="BTCUSDT",
            notional_floor=d("0"),
            notional_cap=d("250000"),
            initial_leverage=d("20"),
            maint_margin_ratio=d("0.004"),
            maint_amount=d("0"),
        )
        account = CryptoAccountRisk(
            equity=d("10000"),
            available_balance=d("8000"),
            wallet_balance=d("10000"),
            margin_balance=d("10000"),
        )

        result = MarginRiskCalculator(
            FeeBufferConfig(funding_rate=d("0"), taker_fee_rate=d("0"), slippage_bps=d("0"))
        ).calculate_liquidation_price(account, position, [bracket])

        assert result.ok is True
        assert result.liquidation_price is not None
        assert result.liquidation_price < position.mark_price
        assert result.buffer_ratio is not None
        assert result.buffer_ratio > Decimal("0")
        assert result.liquidation_price == d("45180.72289156626506024096386")

    def test_short_position_liquidation_price(self) -> None:
        position = CryptoPositionRisk(
            symbol="BTCUSDT",
            qty=d("-1"),
            entry_price=d("50000"),
            mark_price=d("48000"),
            leverage=d("10"),
        )
        bracket = LeverageBracket(
            symbol="BTCUSDT",
            notional_floor=d("0"),
            notional_cap=d("250000"),
            initial_leverage=d("20"),
            maint_margin_ratio=d("0.004"),
            maint_amount=d("0"),
        )
        account = CryptoAccountRisk(
            equity=d("10000"),
            available_balance=d("8000"),
            wallet_balance=d("10000"),
            margin_balance=d("10000"),
        )

        result = MarginRiskCalculator(
            FeeBufferConfig(funding_rate=d("0"), taker_fee_rate=d("0"), slippage_bps=d("0"))
        ).calculate_liquidation_price(account, position, [bracket])

        assert result.ok is True
        assert result.liquidation_price is not None
        assert result.liquidation_price > position.mark_price
        assert result.buffer_ratio is not None
        assert result.buffer_ratio > Decimal("0")
        assert result.liquidation_price == d("54780.87649402390438247011952")

    def test_higher_leverage_closer_liquidation(self) -> None:
        position_low_lev = CryptoPositionRisk(
            symbol="BTCUSDT",
            qty=d("1"),
            entry_price=d("50000"),
            mark_price=d("50000"),
            leverage=d("5"),
        )
        position_high_lev = CryptoPositionRisk(
            symbol="BTCUSDT",
            qty=d("1"),
            entry_price=d("50000"),
            mark_price=d("50000"),
            leverage=d("20"),
        )
        bracket = LeverageBracket(
            symbol="BTCUSDT",
            notional_floor=d("0"),
            notional_cap=d("250000"),
            initial_leverage=d("20"),
            maint_margin_ratio=d("0.004"),
            maint_amount=d("0"),
        )
        account = CryptoAccountRisk(
            equity=d("10000"),
            available_balance=d("8000"),
            wallet_balance=d("10000"),
            margin_balance=d("10000"),
        )

        calculator = MarginRiskCalculator(
            FeeBufferConfig(funding_rate=d("0"), taker_fee_rate=d("0"), slippage_bps=d("0"))
        )

        result_low = calculator.calculate_liquidation_price(account, position_low_lev, [bracket])
        result_high = calculator.calculate_liquidation_price(account, position_high_lev, [bracket])

        assert result_low.ok is True
        assert result_high.ok is True
        assert result_high.liquidation_price is not None
        assert result_low.liquidation_price is not None
        assert result_high.liquidation_price > result_low.liquidation_price
