"""
Alternative Data Health Gate - Data Quality as Risk Input
========================================================

Core Plane module that converts alternative data quality into explicit risk inputs.

Functions:
- Data health model: freshness / coverage / delay / source_quality
- Integrates with signal gating or risk_sizer
- Degradation strategy for missing/delayed data

This module is IO-free (Core Plane constraint).
"""
from __future__ import annotations

from dataclasses import dataclass
from enum import Enum
from typing import Literal
import math


class DataSourceType(Enum):
    FUNDING_RATE = "funding_rate"
    OPEN_INTEREST = "open_interest"
    LIQUIDATION = "liquidation"
    ONCHAIN = "onchain"
    ANNOUNCEMENT = "announcement"
    EXTERNAL_SIGNAL = "external_signal"


class DataHealthLevel(Enum):
    HEALTHY = "healthy"     # All metrics acceptable
    DEGRADED = "degraded"   # Some degradation, reduced confidence
    UNHEALTHY = "unhealthy"  # Significant issues, heavily discounted
    STALE = "stale"         # Data too old, should block new positions
    UNAVAILABLE = "unavailable"  # No data, fail-closed


@dataclass(frozen=True, slots=True)
class DataHealthMetrics:
    source: DataSourceType
    freshness_seconds: float | None       # Seconds since last update
    coverage_pct: float | None            # % of expected data points available
    delay_seconds: float | None            # Known latency in seconds
    source_quality_score: float | None     # 0.0 to 1.0 quality score


@dataclass(frozen=True, slots=True)
class DataHealthThresholds:
    max_freshness_seconds: float = 600.0      # 10 min for most crypto data
    min_coverage_pct: float = 0.80           # 80% coverage minimum
    max_delay_seconds: float = 120.0          # 2 min max delay
    min_quality_score: float = 0.5           # 0.5 quality minimum


@dataclass(frozen=True, slots=True)
class DataHealthConfig:
    thresholds: DataHealthThresholds
    staleness_grace_seconds: float = 60.0     # Additional grace period before blocking
    fail_closed: bool = True


@dataclass(frozen=True, slots=True)
class DataReliabilityResult:
    health_level: DataHealthLevel
    reliability_coef: float           # 0.0 to 1.0 for risk_sizer
    freshness_coef: float
    coverage_coef: float
    delay_coef: float
    quality_coef: float
    is_blocked: bool                   # True if new positions should be blocked
    reason: str
    stale_data_sources: list[str]      # List of sources in STALE or worse


class AlternativeDataHealthGate:
    """
    Evaluates alternative data quality and produces reliability coefficients.

    This is a pure computation module (no IO) that converts raw data health
    metrics into risk-sizer-compatible coefficients.
    """

    def __init__(self, config: DataHealthConfig | None = None):
        if config is None:
            config = DataHealthConfig(thresholds=DataHealthThresholds())
        self._config = config

    def evaluate(
        self, metrics: list[DataHealthMetrics]
    ) -> DataReliabilityResult:
        """
        Evaluate a list of data source metrics and return combined reliability.

        Returns a DataReliabilityResult with:
        - Overall health level (worst source determines level)
        - Reliability coefficient for risk_sizer (0.0 to 1.0)
        - Individual coefficients for debugging
        - is_blocked flag for gating new positions
        """
        if self._config.fail_closed and not metrics:
            return DataReliabilityResult(
                health_level=DataHealthLevel.UNAVAILABLE,
                reliability_coef=0.0,
                freshness_coef=0.0,
                coverage_coef=0.0,
                delay_coef=0.0,
                quality_coef=0.0,
                is_blocked=True,
                reason="No data sources provided",
                stale_data_sources=[],
            )

        freshness_coefs: list[float] = []
        coverage_coefs: list[float] = []
        delay_coefs: list[float] = []
        quality_coefs: list[float] = []
        stale_sources: list[str] = []
        worst_level = DataHealthLevel.HEALTHY

        for m in metrics:
            if m.freshness_seconds is None:
                freshness = 0.0  # No data = no reliability (fail-closed)
                source_level_for_none = DataHealthLevel.UNAVAILABLE
            else:
                freshness = self._compute_freshness_coef(m.freshness_seconds)
                source_level_for_none = None

            if m.coverage_pct is None:
                coverage = 0.0  # No data = no reliability (fail-closed)
            else:
                coverage = self._compute_coverage_coef(m.coverage_pct)

            if m.delay_seconds is None:
                delay = 0.0  # No data = no reliability (fail-closed)
            else:
                delay = self._compute_delay_coef(m.delay_seconds)

            if m.source_quality_score is None:
                quality = 0.0  # No data = no reliability (fail-closed)
            else:
                quality = self._compute_quality_coef(m.source_quality_score)

            freshness_coefs.append(freshness)
            coverage_coefs.append(coverage)
            delay_coefs.append(delay)
            quality_coefs.append(quality)

            if source_level_for_none is not None:
                source_level = source_level_for_none
            else:
                source_level = self._determine_source_level(
                    freshness, coverage, delay, quality
                )

            if source_level == DataHealthLevel.STALE:
                stale_sources.append(f"{m.source.value}:stale")
            elif source_level == DataHealthLevel.UNAVAILABLE:
                stale_sources.append(f"{m.source.value}:unavailable")

            if self._level_to_priority(source_level) > self._level_to_priority(worst_level):
                worst_level = source_level

        # Use geometric mean of per-source combined values for consistency
        combined_values = [f * c * d * q for f, c, d, q in zip(freshness_coefs, coverage_coefs, delay_coefs, quality_coefs)]
        n = len(combined_values)
        if n == 0:
            reliability_coef = 0.0
        else:
            product = 1.0
            for v in combined_values:
                product *= v
            reliability_coef = product ** (1.0 / n)  # geometric mean

        # Individual coefficients are still arithmetic mean for transparency
        avg_freshness = sum(freshness_coefs) / n if n else 0.0
        avg_coverage = sum(coverage_coefs) / n if n else 0.0
        avg_delay = sum(delay_coefs) / n if n else 0.0
        avg_quality = sum(quality_coefs) / n if n else 0.0

        is_blocked = (
            worst_level in (DataHealthLevel.STALE, DataHealthLevel.UNAVAILABLE)
            or reliability_coef < 0.1
        )

        reason = self._build_reason(worst_level, stale_sources, reliability_coef)

        return DataReliabilityResult(
            health_level=worst_level,
            reliability_coef=reliability_coef,
            freshness_coef=avg_freshness,
            coverage_coef=avg_coverage,
            delay_coef=avg_delay,
            quality_coef=avg_quality,
            is_blocked=is_blocked,
            reason=reason,
            stale_data_sources=stale_sources,
        )

    def _compute_freshness_coef(self, freshness_seconds: float) -> float:
        """Compute freshness coefficient: 1.0 (fresh) to 0.0 (stale)."""
        if freshness_seconds < 0:
            return 0.0
        t = self._config.thresholds
        if freshness_seconds <= t.max_freshness_seconds * 0.5:
            return 1.0
        elif freshness_seconds <= t.max_freshness_seconds:
            return 0.75
        elif freshness_seconds <= t.max_freshness_seconds + self._config.staleness_grace_seconds:
            return 0.25
        else:
            return 0.0

    def _compute_coverage_coef(self, coverage_pct: float) -> float:
        """Compute coverage coefficient: 1.0 (100%) to 0.0 (0%)."""
        if coverage_pct <= 0:
            return 0.0
        t = self._config.thresholds
        if coverage_pct >= 1.0:
            return 1.0
        if coverage_pct >= t.min_coverage_pct:
            return 1.0
        return coverage_pct / t.min_coverage_pct

    def _compute_delay_coef(self, delay_seconds: float) -> float:
        """Compute delay coefficient: 1.0 (no delay) to 0.0 (excessive delay)."""
        if delay_seconds < 0:
            return 0.0
        t = self._config.thresholds
        if delay_seconds <= t.max_delay_seconds * 0.5:
            return 1.0
        elif delay_seconds <= t.max_delay_seconds:
            return 0.75
        else:
            return max(0.0, 1.0 - (delay_seconds - t.max_delay_seconds) / t.max_delay_seconds)

    def _compute_quality_coef(self, quality_score: float) -> float:
        """Compute quality coefficient: 1.0 (perfect) to 0.0 (useless)."""
        if quality_score < 0:
            return 0.0
        if quality_score > 1:
            return 1.0
        t = self._config.thresholds
        if quality_score >= t.min_quality_score:
            return 1.0
        return quality_score / t.min_quality_score

    def _determine_source_level(
        self, freshness: float, coverage: float, delay: float, quality: float
    ) -> DataHealthLevel:
        """Determine health level for a single source."""
        combined = freshness * coverage * delay * quality
        if combined >= 0.9:
            return DataHealthLevel.HEALTHY
        elif combined >= 0.6:
            return DataHealthLevel.DEGRADED
        elif combined >= 0.3:
            return DataHealthLevel.UNHEALTHY
        elif combined >= 0.1:
            return DataHealthLevel.STALE
        else:
            return DataHealthLevel.UNAVAILABLE

    def _level_to_priority(self, level: DataHealthLevel) -> int:
        """Convert health level to priority (higher = worse)."""
        return {
            DataHealthLevel.HEALTHY: 0,
            DataHealthLevel.DEGRADED: 1,
            DataHealthLevel.UNHEALTHY: 2,
            DataHealthLevel.STALE: 3,
            DataHealthLevel.UNAVAILABLE: 4,
        }.get(level, 0)

    def _build_reason(
        self, worst_level: DataHealthLevel, stale_sources: list[str], reliability_coef: float
    ) -> str:
        parts = [f"worst_level={worst_level.value}", f"reliability_coef={reliability_coef:.3f}"]
        if stale_sources:
            parts.append(f"stale_sources=[{', '.join(stale_sources)}]")
        return ", ".join(parts)
