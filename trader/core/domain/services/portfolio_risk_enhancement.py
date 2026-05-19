"""
Portfolio Risk Enhancement Services - 组合风险增强服务
=====================================================

Core domain services for enhanced portfolio risk management:
- Volatility regime-based discount factors
- Stress scenario calculation
- Concentration risk detection

All calculations are in Core Plane (no IO, deterministic, replayable).
"""

from __future__ import annotations

from dataclasses import dataclass, field
from decimal import Decimal
from enum import Enum
from typing import Optional, Sequence

from trader.core.domain.models.crypto_risk import CryptoPositionRisk


def _safe_divide(
    numerator: Decimal, denominator: Decimal, default: Decimal = Decimal("0")
) -> Decimal:
    if denominator <= 0:
        return default
    return numerator / denominator


def _risk_price(
    position: CryptoPositionRisk,
    mark_prices: dict[str, Decimal],
) -> Decimal:
    price = mark_prices.get(position.symbol, position.mark_price)
    if price <= 0:
        return Decimal("0")
    return price


class VolatilityRegime(Enum):
    LOW = "low"
    NORMAL = "normal"
    HIGH = "high"
    CRISIS = "crisis"


@dataclass(frozen=True, slots=True)
class VolatilityDiscountConfig:
    low_volatility_threshold: Decimal = Decimal("0.05")
    high_volatility_threshold: Decimal = Decimal("0.15")
    crisis_volatility_threshold: Decimal = Decimal("0.30")

    low_volatility_discount: Decimal = Decimal("1.0")
    normal_volatility_discount: Decimal = Decimal("0.85")
    high_volatility_discount: Decimal = Decimal("0.60")
    crisis_volatility_discount: Decimal = Decimal("0.30")


@dataclass(frozen=True, slots=True)
class StressScenario:
    name: str
    symbol_shocks: dict[str, Decimal]
    liquidity_haircut: Decimal = Decimal("1.0")

    def stress_symbol(self, symbol: str, base_price: Decimal) -> Decimal:
        price_multiplier = self.symbol_shocks.get(symbol, Decimal("1.0"))
        return base_price * price_multiplier * self.liquidity_haircut


@dataclass(frozen=True, slots=True)
class ConcentrationThresholds:
    max_single_symbol_ratio: Decimal = Decimal("0.30")
    max_single_cluster_ratio: Decimal = Decimal("0.50")
    max_single_direction_ratio: Decimal = Decimal("0.80")
    max_correlated_group_ratio: Decimal = Decimal("0.60")


@dataclass(frozen=True, slots=True)
class ConcentrationRiskResult:
    single_symbol_ratios: dict[str, Decimal]
    cluster_ratios: dict[str, Decimal]
    direction_ratios: dict[str, Decimal]
    violations: tuple[str, ...] = field(default_factory=tuple)

    @property
    def has_violations(self) -> bool:
        return len(self.violations) > 0


@dataclass(frozen=True, slots=True)
class StressScenarioResult:
    scenario: StressScenario
    original_exposure: Decimal
    stressed_exposure: Decimal
    loss: Decimal
    loss_percentage: Decimal
    pnl: Decimal = Decimal("0")


class VolatilityDiscountService:
    def __init__(self, config: Optional[VolatilityDiscountConfig] = None) -> None:
        self._config = config or VolatilityDiscountConfig()

    def determine_regime(self, volatility: Decimal) -> VolatilityRegime:
        if volatility < self._config.low_volatility_threshold:
            return VolatilityRegime.LOW
        elif volatility < self._config.high_volatility_threshold:
            return VolatilityRegime.NORMAL
        elif volatility < self._config.crisis_volatility_threshold:
            return VolatilityRegime.HIGH
        else:
            return VolatilityRegime.CRISIS

    def get_discount(self, regime: VolatilityRegime) -> Decimal:
        discount_map = {
            VolatilityRegime.LOW: self._config.low_volatility_discount,
            VolatilityRegime.NORMAL: self._config.normal_volatility_discount,
            VolatilityRegime.HIGH: self._config.high_volatility_discount,
            VolatilityRegime.CRISIS: self._config.crisis_volatility_discount,
        }
        return discount_map.get(regime, Decimal("1.0"))

    def apply_discount(
        self,
        exposure: Decimal,
        volatility: Decimal,
    ) -> tuple[Decimal, VolatilityRegime]:
        regime = self.determine_regime(volatility)
        discount = self.get_discount(regime)
        adjusted_exposure = exposure * discount
        return adjusted_exposure, regime


class StressScenarioService:
    DEFAULT_SCENARIOS: list[StressScenario] = [
        StressScenario(
            name="MODERATE_DOWNTURN",
            symbol_shocks={
                "BTCUSDT": Decimal("0.95"),
                "ETHUSDT": Decimal("0.92"),
            },
            liquidity_haircut=Decimal("1.0"),
        ),
        StressScenario(
            name="SEVERE_DOWNTURN",
            symbol_shocks={
                "BTCUSDT": Decimal("0.85"),
                "ETHUSDT": Decimal("0.80"),
            },
            liquidity_haircut=Decimal("0.80"),
        ),
        StressScenario(
            name="CRISIS",
            symbol_shocks={
                "BTCUSDT": Decimal("0.70"),
                "ETHUSDT": Decimal("0.60"),
                "ALTCOIN": Decimal("0.30"),
            },
            liquidity_haircut=Decimal("0.50"),
        ),
    ]

    def __init__(self, scenarios: Optional[list[StressScenario]] = None) -> None:
        self._scenarios = scenarios or self.DEFAULT_SCENARIOS

    def calculate_stress_result(
        self,
        scenario: StressScenario,
        positions: Sequence[CryptoPositionRisk],
        mark_prices: dict[str, Decimal],
    ) -> StressScenarioResult:
        original_exposure = Decimal("0")
        stressed_exposure = Decimal("0")
        total_pnl = Decimal("0")

        for position in positions:
            symbol = position.symbol
            base_price = _risk_price(position, mark_prices)
            current_notional = abs(position.qty) * base_price

            original_exposure += current_notional

            stressed_price = scenario.stress_symbol(symbol, base_price)
            stressed_notional = abs(position.qty) * stressed_price

            stressed_exposure += stressed_notional
            total_pnl += position.qty * (stressed_price - base_price)

        loss = max(Decimal("0"), -total_pnl)
        loss_pct = _safe_divide(
            loss * Decimal("100"),
            original_exposure,
            Decimal("0"),
        )

        return StressScenarioResult(
            scenario=scenario,
            original_exposure=original_exposure,
            stressed_exposure=stressed_exposure,
            loss=loss,
            loss_percentage=loss_pct,
            pnl=total_pnl,
        )

    def calculate_all_scenarios(
        self,
        positions: Sequence[CryptoPositionRisk],
        mark_prices: dict[str, Decimal],
    ) -> list[StressScenarioResult]:
        return [
            self.calculate_stress_result(scenario, positions, mark_prices)
            for scenario in self._scenarios
        ]

    def get_worst_case(
        self,
        positions: Sequence[CryptoPositionRisk],
        mark_prices: dict[str, Decimal],
    ) -> StressScenarioResult:
        results = self.calculate_all_scenarios(positions, mark_prices)
        return max(results, key=lambda r: r.loss_percentage)


class ConcentrationRiskService:
    def __init__(self, thresholds: Optional[ConcentrationThresholds] = None) -> None:
        self._thresholds = thresholds or ConcentrationThresholds()

    def calculate_concentration(
        self,
        positions: Sequence[CryptoPositionRisk],
        symbol_clusters: dict[str, str],
        mark_prices: dict[str, Decimal],
    ) -> ConcentrationRiskResult:
        if not positions:
            return ConcentrationRiskResult(
                single_symbol_ratios={},
                cluster_ratios={},
                direction_ratios={},
            )

        symbol_notionals: dict[str, Decimal] = {}
        cluster_notionals: dict[str, Decimal] = {}
        long_notional = Decimal("0")
        short_notional = Decimal("0")

        for position in positions:
            symbol = position.symbol
            price = _risk_price(position, mark_prices)
            notional = abs(position.qty) * price

            symbol_notionals[symbol] = symbol_notionals.get(symbol, Decimal("0")) + notional

            cluster = symbol_clusters.get(symbol, "UNCLUSTERED")
            cluster_notionals[cluster] = cluster_notionals.get(cluster, Decimal("0")) + notional

            if position.qty > 0:
                long_notional += notional
            else:
                short_notional += notional

        total_notional = sum(symbol_notionals.values(), Decimal("0"))

        symbol_ratios = {
            symbol: _safe_divide(n, total_notional)
            for symbol, n in symbol_notionals.items()
        }

        cluster_ratios = {
            cluster: _safe_divide(n, total_notional)
            for cluster, n in cluster_notionals.items()
        }

        total_direction = long_notional + short_notional
        direction_ratios: dict[str, Decimal] = {}
        if total_direction > 0:
            direction_ratios["LONG"] = _safe_divide(long_notional, total_direction)
            direction_ratios["SHORT"] = _safe_divide(short_notional, total_direction)

        violations = self._check_violations(symbol_ratios, cluster_ratios, direction_ratios)

        return ConcentrationRiskResult(
            single_symbol_ratios=symbol_ratios,
            cluster_ratios=cluster_ratios,
            direction_ratios=direction_ratios,
            violations=violations,
        )

    def _check_violations(
        self,
        symbol_ratios: dict[str, Decimal],
        cluster_ratios: dict[str, Decimal],
        direction_ratios: dict[str, Decimal],
    ) -> tuple[str, ...]:
        violations: list[str] = []

        for symbol, ratio in symbol_ratios.items():
            if ratio > self._thresholds.max_single_symbol_ratio:
                violations.append(f"SYMBOL_CONCENTRATION:{symbol}={ratio}")

        for cluster, ratio in cluster_ratios.items():
            if ratio > self._thresholds.max_single_cluster_ratio:
                violations.append(f"CLUSTER_CONCENTRATION:{cluster}={ratio}")

        for direction, ratio in direction_ratios.items():
            if ratio > self._thresholds.max_single_direction_ratio:
                violations.append(f"DIRECTION_CONCENTRATION:{direction}={ratio}")

        return tuple(violations)


@dataclass(frozen=True, slots=True)
class PortfolioRiskEnhancementResult:
    concentration: ConcentrationRiskResult
    worst_case_stress: StressScenarioResult | None = None
    regime: VolatilityRegime | None = None
    adjusted_exposure: Decimal | None = None


class PortfolioRiskEnhancementService:
    def __init__(
        self,
        concentration_service: Optional[ConcentrationRiskService] = None,
        stress_service: Optional[StressScenarioService] = None,
        volatility_service: Optional[VolatilityDiscountService] = None,
    ) -> None:
        self._concentration = concentration_service or ConcentrationRiskService()
        self._stress = stress_service or StressScenarioService()
        self._volatility = volatility_service or VolatilityDiscountService()

    def evaluate(
        self,
        positions: Sequence[CryptoPositionRisk],
        symbol_clusters: dict[str, str],
        mark_prices: dict[str, Decimal],
        volatility: Optional[Decimal] = None,
    ) -> PortfolioRiskEnhancementResult:
        concentration = self._concentration.calculate_concentration(
            positions=positions,
            symbol_clusters=symbol_clusters,
            mark_prices=mark_prices,
        )

        worst_case = self._stress.get_worst_case(positions, mark_prices)

        adjusted_exposure: Optional[Decimal] = None
        regime: Optional[VolatilityRegime] = None
        if volatility is not None:
            adjusted_exposure, regime = self._volatility.apply_discount(
                exposure=worst_case.stressed_exposure,
                volatility=volatility,
            )

        return PortfolioRiskEnhancementResult(
            concentration=concentration,
            worst_case_stress=worst_case,
            regime=regime,
            adjusted_exposure=adjusted_exposure,
        )
