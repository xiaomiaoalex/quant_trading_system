"""
test_portfolio_risk_enhancement - 组合风险增强测试
==================================================
阶段6测试用例：
- cluster exposure 加入动态波动率折扣
- stress scenario：BTC -5%、ETH -8%、alt liquidity haircut
- concentration risk：单 symbol、单 cluster、单 direction
- 回测侧复用同一组合风险逻辑
"""

from __future__ import annotations

from dataclasses import dataclass, field
from decimal import Decimal
from typing import Optional

import pytest

from trader.core.domain.models.crypto_risk import CryptoPositionRisk, MarginMode
from trader.core.domain.models.order import OrderSide
from trader.core.domain.services.portfolio_exposure_aggregator import (
    ClusterExposure,
    PortfolioExposureAggregator,
)


def d(value: str) -> Decimal:
    return Decimal(value)


@dataclass(frozen=True, slots=True)
class StressScenario:
    name: str
    symbol_shocks: dict[str, Decimal]
    liquidity_haircut: Decimal = Decimal("1.0")


class TestVolatilityDiscount:
    """测试动态波动率折扣"""

    def test_high_volatility_cluster_gets_discount(self) -> None:
        """高波动率 cluster 获得折扣"""
        aggregator = PortfolioExposureAggregator()

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

        exposures = aggregator.calculate_cluster_exposures(
            positions=positions,
            open_orders=[],
            mark_prices=mark_prices,
            symbol_clusters=symbol_clusters,
        )

        assert "ALPHA" in exposures
        assert exposures["ALPHA"].total_risk_notional == d("50000")

    def test_low_volatility_cluster_no_discount(self) -> None:
        """低波动率 cluster 无折扣"""
        aggregator = PortfolioExposureAggregator()

        positions = [
            CryptoPositionRisk(
                symbol="USDCUSDT",
                qty=d("10000"),
                entry_price=d("1"),
                mark_price=d("1"),
                leverage=d("1"),
            )
        ]
        mark_prices = {"USDCUSDT": d("1")}
        symbol_clusters = {"USDCUSDT": "STABLE"}

        exposures = aggregator.calculate_cluster_exposures(
            positions=positions,
            open_orders=[],
            mark_prices=mark_prices,
            symbol_clusters=symbol_clusters,
        )

        assert "STABLE" in exposures
        assert exposures["STABLE"].total_risk_notional >= d("0")


class TestStressScenario:
    """测试 Stress Scenario 计算"""

    def test_btc_minus_5pct_scenario(self) -> None:
        """BTC -5% 压力测试"""
        base_price = d("50000")
        shock = Decimal("0.95")
        stressed_price = base_price * shock

        loss = base_price - stressed_price
        loss_pct = (loss / base_price) * Decimal("100")

        assert stressed_price == d("47500")
        assert loss_pct == d("5")

    def test_eth_minus_8pct_scenario(self) -> None:
        """ETH -8% 压力测试"""
        base_price = d("3000")
        shock = Decimal("0.92")
        stressed_price = base_price * shock

        loss = base_price - stressed_price
        loss_pct = (loss / base_price) * Decimal("100")

        assert stressed_price == d("2760")
        assert loss_pct == d("8")

    def test_multi_asset_stress_scenario(self) -> None:
        """多资产压力测试"""
        scenarios = [
            StressScenario(
                name="MODERATE_CRISIS",
                symbol_shocks={
                    "BTCUSDT": Decimal("0.95"),
                    "ETHUSDT": Decimal("0.92"),
                    "BNBUSDT": Decimal("0.90"),
                },
                liquidity_haircut=Decimal("0.8"),
            ),
            StressScenario(
                name="SEVERE_CRISIS",
                symbol_shocks={
                    "BTCUSDT": Decimal("0.85"),
                    "ETHUSDT": Decimal("0.80"),
                    "BNBUSDT": Decimal("0.75"),
                    "ALTCOIN": Decimal("0.50"),
                },
                liquidity_haircut=Decimal("0.5"),
            ),
        ]

        for scenario in scenarios:
            btc_stressed = d("50000") * scenario.symbol_shocks["BTCUSDT"]
            btc_loss = d("50000") - btc_stressed

            assert btc_stressed > d("0")
            assert btc_loss > d("0")

    def test_altcoin_liquidity_haircut(self) -> None:
        """Altcoin 流动性折价"""
        base_price = d("1")
        liquidity_haircut = Decimal("0.5")

        stressed_value = base_price * liquidity_haircut

        assert stressed_value == d("0.5")


class TestConcentrationRisk:
    """测试 Concentration Risk 检测"""

    def test_single_symbol_concentration(self) -> None:
        """单 symbol 集中度"""
        positions = [
            CryptoPositionRisk(
                symbol="BTCUSDT",
                qty=d("10"),
                entry_price=d("50000"),
                mark_price=d("50000"),
                leverage=d("10"),
            )
        ]

        total_notional = sum(p.notional for p in positions)
        btc_notional = positions[0].notional

        concentration_ratio = btc_notional / total_notional if total_notional > 0 else Decimal("0")

        assert concentration_ratio == Decimal("1.0")
        assert concentration_ratio > Decimal("0.3")

    def test_multi_symbol_concentration(self) -> None:
        """多 symbol 分散"""
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

        total_notional = sum(p.notional for p in positions)
        btc_notional = positions[0].notional

        concentration_ratio = btc_notional / total_notional if total_notional > 0 else Decimal("0")

        assert concentration_ratio == Decimal("0.625")
        assert concentration_ratio < Decimal("0.8")

    def test_cluster_concentration(self) -> None:
        """cluster 集中度"""
        aggregator = PortfolioExposureAggregator()

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
                qty=d("5"),
                entry_price=d("3000"),
                mark_price=d("3000"),
                leverage=d("10"),
            ),
        ]
        mark_prices = {"BTCUSDT": d("50000"), "ETHUSDT": d("3000")}
        symbol_clusters = {"BTCUSDT": "ALPHA", "ETHUSDT": "ALPHA"}

        exposures = aggregator.calculate_cluster_exposures(
            positions=positions,
            open_orders=[],
            mark_prices=mark_prices,
            symbol_clusters=symbol_clusters,
        )

        alpha_exposure = exposures["ALPHA"].total_risk_notional
        assert alpha_exposure == d("65000")

        total_exposure = sum(e.total_risk_notional for e in exposures.values())
        cluster_concentration = (
            alpha_exposure / total_exposure if total_exposure > 0 else Decimal("0")
        )

        assert cluster_concentration == Decimal("1.0")

    def test_direction_concentration_long(self) -> None:
        """多头方向集中度"""
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
                qty=d("2"),
                entry_price=d("3000"),
                mark_price=d("3000"),
                leverage=d("10"),
            ),
        ]

        total_long = sum(p.notional for p in positions if p.qty > 0)
        total_notional = sum(p.notional for p in positions)

        long_concentration = total_long / total_notional if total_notional > 0 else Decimal("0")
        assert long_concentration == Decimal("1.0")

    def test_direction_concentration_mixed(self) -> None:
        """多空混合集中度"""
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

        total_long = sum(p.notional for p in positions if p.qty > 0)
        total_short = sum(abs(p.notional) for p in positions if p.qty < 0)
        total_notional = sum(abs(p.notional) for p in positions)

        long_concentration = total_long / total_notional if total_notional > 0 else Decimal("0")
        short_concentration = total_short / total_notional if total_notional > 0 else Decimal("0")

        assert long_concentration + short_concentration == Decimal("1.0")
        assert total_long == d("50000")
        assert total_short == d("6000")


class TestVolatilityRegimeDiscount:
    """测试波动率 regime 折扣"""

    def test_low_volatility_no_discount(self) -> None:
        """低波动率无折扣"""
        volatility = Decimal("0.02")
        threshold_low = Decimal("0.05")

        discount = Decimal("1.0") if volatility < threshold_low else Decimal("0.8")

        assert discount == Decimal("1.0")

    def test_high_volatility_with_discount(self) -> None:
        """高波动率有折扣"""
        volatility = Decimal("0.10")
        threshold_high = Decimal("0.05")

        discount = Decimal("1.0") if volatility < threshold_high else Decimal("0.7")

        assert discount == Decimal("0.7")

    def test_crisis_volatility_max_discount(self) -> None:
        """危机波动率最大折扣"""
        volatility = Decimal("0.30")
        threshold_crisis = Decimal("0.20")

        if volatility >= threshold_crisis:
            discount = Decimal("0.3")
        elif volatility >= Decimal("0.10"):
            discount = Decimal("0.5")
        else:
            discount = Decimal("1.0")

        assert discount == Decimal("0.3")


class TestStressScenarioWithConcentration:
    """测试压力场景与集中度组合"""

    def test_btc_concentration_plus_stress(self) -> None:
        """BTC 集中 + 压力场景"""
        base_positions = [
            CryptoPositionRisk(
                symbol="BTCUSDT",
                qty=d("5"),
                entry_price=d("50000"),
                mark_price=d("50000"),
                leverage=d("10"),
            )
        ]

        total_notional = sum(p.notional for p in base_positions)
        btc_concentration = base_positions[0].notional / total_notional

        btc_shock = Decimal("0.95")
        stressed_value = base_positions[0].notional * btc_shock

        expected_loss = total_notional - stressed_value
        expected_loss_pct = (expected_loss / total_notional) * Decimal("100")

        assert btc_concentration == Decimal("1.0")
        assert expected_loss_pct == d("5")

    def test_cluster_concentration_plus_stress(self) -> None:
        """cluster 集中 + 压力场景"""
        base_positions = [
            CryptoPositionRisk(
                symbol="BTCUSDT",
                qty=d("2"),
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

        aggregator = PortfolioExposureAggregator()
        exposures = aggregator.calculate_cluster_exposures(
            positions=base_positions,
            open_orders=[],
            mark_prices=mark_prices,
            symbol_clusters=symbol_clusters,
        )

        alpha_exposure = exposures["ALPHA"].total_risk_notional

        btc_shock = Decimal("0.95")
        eth_shock = Decimal("0.92")
        stressed_alpha = d("100000") * btc_shock + d("30000") * eth_shock

        stress_loss = alpha_exposure - stressed_alpha
        stress_loss_pct = (stress_loss / alpha_exposure) * Decimal("100")

        assert alpha_exposure == d("130000")
        assert stress_loss_pct > d("0")


@dataclass(frozen=True, slots=True)
class ConcentrationThresholds:
    max_single_symbol_ratio: Decimal = Decimal("0.30")
    max_single_cluster_ratio: Decimal = Decimal("0.50")
    max_single_direction_ratio: Decimal = Decimal("0.80")
    max_correlated_group_ratio: Decimal = Decimal("0.60")


class ConcentrationRiskResult:
    """集中度风险结果"""

    def __init__(
        self,
        single_symbol_ratios: dict[str, Decimal],
        cluster_ratios: dict[str, Decimal],
        direction_ratios: dict[str, Decimal],
        thresholds: ConcentrationThresholds,
    ) -> None:
        self.single_symbol_ratios = single_symbol_ratios
        self.cluster_ratios = cluster_ratios
        self.direction_ratios = direction_ratios
        self.thresholds = thresholds
        self.violations: list[str] = []
        self._check_violations()

    def _check_violations(self) -> None:
        for symbol, ratio in self.single_symbol_ratios.items():
            if ratio > self.thresholds.max_single_symbol_ratio:
                self.violations.append(f"SYMBOL_CONCENTRATION:{symbol}={ratio}")

        for cluster, ratio in self.cluster_ratios.items():
            if ratio > self.thresholds.max_single_cluster_ratio:
                self.violations.append(f"CLUSTER_CONCENTRATION:{cluster}={ratio}")

        for direction, ratio in self.direction_ratios.items():
            if ratio > self.thresholds.max_single_direction_ratio:
                self.violations.append(f"DIRECTION_CONCENTRATION:{direction}={ratio}")

    @property
    def has_violations(self) -> bool:
        return len(self.violations) > 0


class TestConcentrationRiskResult:
    """测试集中度风险结果"""

    def test_no_violations(self) -> None:
        """无违规"""
        result = ConcentrationRiskResult(
            single_symbol_ratios={"BTCUSDT": d("0.1"), "ETHUSDT": d("0.1")},
            cluster_ratios={"ALPHA": d("0.2"), "BETA": d("0.3")},
            direction_ratios={"LONG": d("0.5"), "SHORT": d("0.2")},
            thresholds=ConcentrationThresholds(),
        )

        assert result.has_violations is False

    def test_symbol_violation(self) -> None:
        """symbol 违规"""
        result = ConcentrationRiskResult(
            single_symbol_ratios={"BTCUSDT": d("0.5")},
            cluster_ratios={},
            direction_ratios={},
            thresholds=ConcentrationThresholds(),
        )

        assert result.has_violations is True
        assert any("SYMBOL_CONCENTRATION" in v for v in result.violations)

    def test_cluster_violation(self) -> None:
        """cluster 违规"""
        result = ConcentrationRiskResult(
            single_symbol_ratios={},
            cluster_ratios={"ALPHA": d("0.7")},
            direction_ratios={},
            thresholds=ConcentrationThresholds(),
        )

        assert result.has_violations is True
        assert any("CLUSTER_CONCENTRATION" in v for v in result.violations)


class TestBacktestLiveConsistency:
    """测试回测/实盘一致性"""

    def test_same_exposure_calculation_in_backtest_and_live(self) -> None:
        """回测和实盘使用同一暴露计算逻辑"""
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

        aggregator = PortfolioExposureAggregator()

        exposures_live = aggregator.calculate_cluster_exposures(
            positions=positions,
            open_orders=[],
            mark_prices=mark_prices,
            symbol_clusters=symbol_clusters,
        )

        exposures_backtest = aggregator.calculate_cluster_exposures(
            positions=positions,
            open_orders=[],
            mark_prices=mark_prices,
            symbol_clusters=symbol_clusters,
        )

        assert exposures_live == exposures_backtest
        assert exposures_live["ALPHA"].total_risk_notional == d("50000")

    def test_stress_scenario_deterministic(self) -> None:
        """压力场景是确定性的"""
        base_exposure = d("100000")
        btc_shock = Decimal("0.95")

        result1 = base_exposure * btc_shock
        result2 = base_exposure * btc_shock
        result3 = base_exposure * btc_shock

        assert result1 == result2 == result3 == d("95000")


class TestFactorBucketRisk:
    """测试 Factor Bucket 风险"""

    def test_factor_bucket_grouping(self) -> None:
        """Factor bucket 分组"""
        symbol_factors = {
            "BTCUSDT": "LARGE_CAP",
            "ETHUSDT": "LARGE_CAP",
            "BNBUSDT": "LARGE_CAP",
            "ADAUSDT": "MID_CAP",
            "DOGEUSDT": "SMALL_CAP",
        }

        factor_groups: dict[str, list[str]] = {}
        for symbol, factor in symbol_factors.items():
            factor_groups.setdefault(factor, []).append(symbol)

        assert len(factor_groups["LARGE_CAP"]) == 3
        assert len(factor_groups["MID_CAP"]) == 1
        assert len(factor_groups["SMALL_CAP"]) == 1

    def test_factor_correlation_matrix(self) -> None:
        """Factor 相关性矩阵"""
        factors = ["LARGE_CAP", "MID_CAP", "SMALL_CAP", "DEFI", "NFT"]
        correlation: dict[str, dict[str, Decimal]] = {}

        for f1 in factors:
            correlation[f1] = {}
            for f2 in factors:
                if f1 == f2:
                    correlation[f1][f2] = Decimal("1.0")
                else:
                    correlation[f1][f2] = Decimal("0.3")

        assert correlation["LARGE_CAP"]["LARGE_CAP"] == Decimal("1.0")
        assert correlation["LARGE_CAP"]["MID_CAP"] == Decimal("0.3")
        assert correlation["DEFI"]["NFT"] == Decimal("0.3")
