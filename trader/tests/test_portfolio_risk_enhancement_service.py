"""
test_portfolio_risk_enhancement_service - 组合风险增强服务测试
=============================================================
"""

from decimal import Decimal

import pytest

from trader.core.domain.models.crypto_risk import CryptoPositionRisk, MarginMode
from trader.core.domain.services.portfolio_risk_enhancement import (
    ConcentrationRiskResult,
    ConcentrationRiskService,
    ConcentrationThresholds,
    PortfolioRiskEnhancementService,
    StressScenario,
    StressScenarioResult,
    StressScenarioService,
    VolatilityDiscountConfig,
    VolatilityDiscountService,
    VolatilityRegime,
)


def d(value: str) -> Decimal:
    return Decimal(value)


class TestVolatilityDiscountService:
    """测试波动率折扣服务"""

    def test_low_volatility_regime(self) -> None:
        service = VolatilityDiscountService()
        volatility = d("0.02")

        regime = service.determine_regime(volatility)

        assert regime == VolatilityRegime.LOW

    def test_normal_volatility_regime(self) -> None:
        service = VolatilityDiscountService()
        volatility = d("0.10")

        regime = service.determine_regime(volatility)

        assert regime == VolatilityRegime.NORMAL

    def test_high_volatility_regime(self) -> None:
        service = VolatilityDiscountService()
        volatility = d("0.20")

        regime = service.determine_regime(volatility)

        assert regime == VolatilityRegime.HIGH

    def test_crisis_volatility_regime(self) -> None:
        service = VolatilityDiscountService()
        volatility = d("0.40")

        regime = service.determine_regime(volatility)

        assert regime == VolatilityRegime.CRISIS

    def test_apply_discount_low_volatility(self) -> None:
        service = VolatilityDiscountService()
        exposure = d("10000")
        volatility = d("0.02")

        adjusted, regime = service.apply_discount(exposure, volatility)

        assert regime == VolatilityRegime.LOW
        assert adjusted == d("10000")

    def test_apply_discount_high_volatility(self) -> None:
        service = VolatilityDiscountService()
        exposure = d("10000")
        volatility = d("0.20")

        adjusted, regime = service.apply_discount(exposure, volatility)

        assert regime == VolatilityRegime.HIGH
        assert adjusted == d("6000")

    def test_custom_discount_config(self) -> None:
        config = VolatilityDiscountConfig(
            crisis_volatility_discount=Decimal("0.1"),
        )
        service = VolatilityDiscountService(config)
        exposure = d("10000")
        volatility = d("0.40")

        adjusted, regime = service.apply_discount(exposure, volatility)

        assert adjusted == d("1000")


class TestStressScenarioService:
    """测试压力场景服务"""

    def test_calculate_stress_result(self) -> None:
        service = StressScenarioService()
        positions = [
            CryptoPositionRisk(
                symbol="BTCUSDT",
                qty=d("1"),
                entry_price=d("50000"),
                mark_price=d("50000"),
                leverage=d("10"),
            )
        ]
        mark_prices = {"BTCUSDT": d("50000")}

        scenario = StressScenario(
            name="MODERATE",
            symbol_shocks={"BTCUSDT": d("0.95")},
        )

        result = service.calculate_stress_result(scenario, positions, mark_prices)

        assert result.original_exposure == d("50000")
        assert result.stressed_exposure == d("47500")
        assert result.loss == d("2500")
        assert result.loss_percentage == d("5")

    def test_calculate_all_scenarios(self) -> None:
        service = StressScenarioService()
        positions = [
            CryptoPositionRisk(
                symbol="BTCUSDT",
                qty=d("1"),
                entry_price=d("50000"),
                mark_price=d("50000"),
                leverage=d("10"),
            )
        ]
        mark_prices = {"BTCUSDT": d("50000")}

        results = service.calculate_all_scenarios(positions, mark_prices)

        assert len(results) >= 3
        worst = max(results, key=lambda r: r.loss_percentage)
        assert worst.loss_percentage > d("0")

    def test_get_worst_case(self) -> None:
        service = StressScenarioService()
        positions = [
            CryptoPositionRisk(
                symbol="BTCUSDT",
                qty=d("1"),
                entry_price=d("50000"),
                mark_price=d("50000"),
                leverage=d("10"),
            )
        ]
        mark_prices = {"BTCUSDT": d("50000")}

        worst = service.get_worst_case(positions, mark_prices)

        assert worst.loss_percentage > d("0")

    def test_multi_asset_stress(self) -> None:
        service = StressScenarioService()
        positions = [
            CryptoPositionRisk(
                symbol="BTCUSDT",
                qty=d("1"),
                entry_price=d("50000"),
                mark_price=d("50000"),
                leverage=d("10"),
            ),
            CryptoPositionRisk(
                symbol="ETHUSDT",
                qty=d("10"),
                entry_price=d("3000"),
                mark_price=d("3000"),
                leverage=d("10"),
            ),
        ]
        mark_prices = {"BTCUSDT": d("50000"), "ETHUSDT": d("3000")}

        scenario = StressScenario(
            name="DUAL_DOWNTURN",
            symbol_shocks={
                "BTCUSDT": d("0.95"),
                "ETHUSDT": d("0.92"),
            },
        )

        result = service.calculate_stress_result(scenario, positions, mark_prices)

        assert result.original_exposure == d("80000")
        assert result.loss > d("0")

    def test_short_position_gains_on_downturn(self) -> None:
        service = StressScenarioService()
        positions = [
            CryptoPositionRisk(
                symbol="BTCUSDT",
                qty=d("-1"),
                entry_price=d("50000"),
                mark_price=d("50000"),
                leverage=d("10"),
            )
        ]
        mark_prices = {"BTCUSDT": d("50000")}
        scenario = StressScenario(
            name="BTC_DOWNTURN",
            symbol_shocks={"BTCUSDT": d("0.95")},
        )

        result = service.calculate_stress_result(scenario, positions, mark_prices)

        assert result.pnl == d("2500")
        assert result.loss == d("0")
        assert result.loss_percentage == d("0")


class TestConcentrationRiskService:
    """测试集中度风险服务"""

    def test_single_symbol_concentration(self) -> None:
        service = ConcentrationRiskService()
        positions = [
            CryptoPositionRisk(
                symbol="BTCUSDT",
                qty=d("10"),
                entry_price=d("50000"),
                mark_price=d("50000"),
                leverage=d("10"),
            )
        ]
        mark_prices = {"BTCUSDT": d("50000")}
        symbol_clusters = {"BTCUSDT": "ALPHA"}

        result = service.calculate_concentration(positions, symbol_clusters, mark_prices)

        assert result.single_symbol_ratios["BTCUSDT"] == Decimal("1.0")
        assert result.has_violations is True

    def test_multi_symbol_spread(self) -> None:
        service = ConcentrationRiskService()
        positions = [
            CryptoPositionRisk(
                symbol="BTCUSDT",
                qty=d("1"),
                entry_price=d("50000"),
                mark_price=d("50000"),
                leverage=d("10"),
            ),
            CryptoPositionRisk(
                symbol="ETHUSDT",
                qty=d("10"),
                entry_price=d("3000"),
                mark_price=d("3000"),
                leverage=d("10"),
            ),
        ]
        mark_prices = {"BTCUSDT": d("50000"), "ETHUSDT": d("3000")}
        symbol_clusters = {"BTCUSDT": "ALPHA", "ETHUSDT": "BETA"}

        result = service.calculate_concentration(positions, symbol_clusters, mark_prices)

        btc_ratio = result.single_symbol_ratios["BTCUSDT"]
        eth_ratio = result.single_symbol_ratios["ETHUSDT"]
        assert btc_ratio < Decimal("1.0")
        assert eth_ratio < Decimal("1.0")
        assert btc_ratio + eth_ratio == Decimal("1.0")

    def test_direction_concentration(self) -> None:
        service = ConcentrationRiskService()
        positions = [
            CryptoPositionRisk(
                symbol="BTCUSDT",
                qty=d("1"),
                entry_price=d("50000"),
                mark_price=d("50000"),
                leverage=d("10"),
            ),
            CryptoPositionRisk(
                symbol="ETHUSDT",
                qty=d("-2"),
                entry_price=d("3000"),
                mark_price=d("3000"),
                leverage=d("10"),
            ),
        ]
        mark_prices = {"BTCUSDT": d("50000"), "ETHUSDT": d("3000")}
        symbol_clusters = {"BTCUSDT": "ALPHA", "ETHUSDT": "ALPHA"}

        result = service.calculate_concentration(positions, symbol_clusters, mark_prices)

        assert "LONG" in result.direction_ratios
        assert "SHORT" in result.direction_ratios
        assert result.direction_ratios["LONG"] > Decimal("0")
        assert result.direction_ratios["SHORT"] > Decimal("0")

    def test_no_violations_within_thresholds(self) -> None:
        service = ConcentrationRiskService(
            thresholds=ConcentrationThresholds(
                max_single_symbol_ratio=Decimal("0.70"),
                max_single_cluster_ratio=Decimal("0.70"),
                max_single_direction_ratio=Decimal("1.00"),
            )
        )
        positions = [
            CryptoPositionRisk(
                symbol="BTCUSDT",
                qty=d("0.5"),
                entry_price=d("50000"),
                mark_price=d("50000"),
                leverage=d("10"),
            ),
            CryptoPositionRisk(
                symbol="ETHUSDT",
                qty=d("-3"),
                entry_price=d("3000"),
                mark_price=d("3000"),
                leverage=d("10"),
            ),
            CryptoPositionRisk(
                symbol="BNBUSDT",
                qty=d("5"),
                entry_price=d("500"),
                mark_price=d("500"),
                leverage=d("10"),
            ),
            CryptoPositionRisk(
                symbol="ADAUSDT",
                qty=d("-100"),
                entry_price=d("0.5"),
                mark_price=d("0.5"),
                leverage=d("10"),
            ),
        ]
        mark_prices = {
            "BTCUSDT": d("50000"),
            "ETHUSDT": d("3000"),
            "BNBUSDT": d("500"),
            "ADAUSDT": d("0.5"),
        }
        symbol_clusters = {
            "BTCUSDT": "ALPHA",
            "ETHUSDT": "BETA",
            "BNBUSDT": "GAMMA",
            "ADAUSDT": "DELTA",
        }

        result = service.calculate_concentration(positions, symbol_clusters, mark_prices)

        assert result.has_violations is False

    def test_duplicate_symbol_positions_are_aggregated(self) -> None:
        service = ConcentrationRiskService(
            thresholds=ConcentrationThresholds(max_single_symbol_ratio=Decimal("0.90"))
        )
        positions = [
            CryptoPositionRisk(
                symbol="BTCUSDT",
                qty=d("0.5"),
                entry_price=d("50000"),
                mark_price=d("50000"),
                leverage=d("10"),
            ),
            CryptoPositionRisk(
                symbol="BTCUSDT",
                qty=d("0.5"),
                entry_price=d("50000"),
                mark_price=d("50000"),
                leverage=d("10"),
            ),
            CryptoPositionRisk(
                symbol="ETHUSDT",
                qty=d("10"),
                entry_price=d("3000"),
                mark_price=d("3000"),
                leverage=d("10"),
            ),
        ]
        mark_prices = {"BTCUSDT": d("50000"), "ETHUSDT": d("3000")}
        symbol_clusters = {"BTCUSDT": "ALPHA", "ETHUSDT": "BETA"}

        result = service.calculate_concentration(positions, symbol_clusters, mark_prices)

        assert result.single_symbol_ratios["BTCUSDT"] == d("0.625")

    def test_non_positive_mark_price_does_not_create_negative_exposure(self) -> None:
        service = ConcentrationRiskService()
        positions = [
            CryptoPositionRisk(
                symbol="BADUSDT",
                qty=d("10"),
                entry_price=d("10"),
                mark_price=d("-1"),
                leverage=d("10"),
            )
        ]
        result = service.calculate_concentration(
            positions=positions,
            symbol_clusters={"BADUSDT": "BAD"},
            mark_prices={},
        )

        assert result.single_symbol_ratios["BADUSDT"] == d("0")
        assert result.cluster_ratios["BAD"] == d("0")


class TestPortfolioRiskEnhancementService:
    """测试组合风险增强服务"""

    def test_full_evaluation(self) -> None:
        service = PortfolioRiskEnhancementService()
        positions = [
            CryptoPositionRisk(
                symbol="BTCUSDT",
                qty=d("1"),
                entry_price=d("50000"),
                mark_price=d("50000"),
                leverage=d("10"),
            ),
            CryptoPositionRisk(
                symbol="ETHUSDT",
                qty=d("10"),
                entry_price=d("3000"),
                mark_price=d("3000"),
                leverage=d("10"),
            ),
        ]
        mark_prices = {"BTCUSDT": d("50000"), "ETHUSDT": d("3000")}
        symbol_clusters = {"BTCUSDT": "ALPHA", "ETHUSDT": "ALPHA"}

        result = service.evaluate(positions, symbol_clusters, mark_prices)

        assert result.concentration is not None
        assert result.worst_case_stress is not None
        assert result.worst_case_stress.loss > d("0")

    def test_evaluation_with_volatility(self) -> None:
        service = PortfolioRiskEnhancementService()
        positions = [
            CryptoPositionRisk(
                symbol="BTCUSDT",
                qty=d("1"),
                entry_price=d("50000"),
                mark_price=d("50000"),
                leverage=d("10"),
            )
        ]
        mark_prices = {"BTCUSDT": d("50000")}
        symbol_clusters = {"BTCUSDT": "ALPHA"}

        result = service.evaluate(
            positions,
            symbol_clusters,
            mark_prices,
            volatility=d("0.10"),
        )

        assert result.regime == VolatilityRegime.NORMAL
        assert result.adjusted_exposure is not None
        assert result.adjusted_exposure < result.worst_case_stress.stressed_exposure


class TestStressScenarioWithLiquidity:
    """测试流动性折扣压力场景"""

    def test_altcoin_liquidity_haircut(self) -> None:
        service = StressScenarioService()
        positions = [
            CryptoPositionRisk(
                symbol="ALTCOIN",
                qty=d("10000"),
                entry_price=d("1"),
                mark_price=d("1"),
                leverage=d("5"),
            )
        ]
        mark_prices = {"ALTCOIN": d("1")}

        scenario = StressScenario(
            name="ALT_LIQUIDITY_CRISIS",
            symbol_shocks={"ALTCOIN": d("0.30")},
            liquidity_haircut=Decimal("0.5"),
        )

        result = service.calculate_stress_result(scenario, positions, mark_prices)

        assert result.original_exposure == d("10000")
        assert result.stressed_exposure == d("1500")
        assert result.loss_percentage == d("85")


class TestBacktestLiveConsistency:
    """测试回测/实盘一致性"""

    def test_same_result_in_backtest_and_live(self) -> None:
        service = PortfolioRiskEnhancementService()
        positions = [
            CryptoPositionRisk(
                symbol="BTCUSDT",
                qty=d("1"),
                entry_price=d("50000"),
                mark_price=d("50000"),
                leverage=d("10"),
            )
        ]
        mark_prices = {"BTCUSDT": d("50000")}
        symbol_clusters = {"BTCUSDT": "ALPHA"}

        result_live = service.evaluate(positions, symbol_clusters, mark_prices)
        result_backtest = service.evaluate(positions, symbol_clusters, mark_prices)

        assert result_live.worst_case_stress.loss == result_backtest.worst_case_stress.loss
        assert (
            result_live.concentration.has_violations == result_backtest.concentration.has_violations
        )

    def test_stress_calculation_deterministic(self) -> None:
        service = StressScenarioService()
        positions = [
            CryptoPositionRisk(
                symbol="BTCUSDT",
                qty=d("1"),
                entry_price=d("50000"),
                mark_price=d("50000"),
                leverage=d("10"),
            )
        ]
        mark_prices = {"BTCUSDT": d("50000")}

        scenario = StressScenario(
            name="TEST",
            symbol_shocks={"BTCUSDT": d("0.95")},
        )

        result1 = service.calculate_stress_result(scenario, positions, mark_prices)
        result2 = service.calculate_stress_result(scenario, positions, mark_prices)
        result3 = service.calculate_stress_result(scenario, positions, mark_prices)

        assert result1 == result2 == result3
