"""
Tests for Alternative Data Health Gate
======================================
"""
import pytest
from trader.core.domain.services.alternative_data_health_gate import (
    AlternativeDataHealthGate,
    DataHealthConfig,
    DataHealthLevel,
    DataHealthMetrics,
    DataHealthThresholds,
    DataReliabilityResult,
    DataSourceType,
)


class TestDataHealthMetrics:
    def test_metrics_creation(self):
        m = DataHealthMetrics(
            source=DataSourceType.FUNDING_RATE,
            freshness_seconds=60.0,
            coverage_pct=0.95,
            delay_seconds=5.0,
            source_quality_score=0.9,
        )
        assert m.source == DataSourceType.FUNDING_RATE
        assert m.freshness_seconds == 60.0

    def test_metrics_optional_fields_none(self):
        m = DataHealthMetrics(
            source=DataSourceType.LIQUIDATION,
            freshness_seconds=None,
            coverage_pct=None,
            delay_seconds=None,
            source_quality_score=None,
        )
        assert m.freshness_seconds is None


class TestAlternativeDataHealthGate:
    def test_all_healthy_returns_healthy(self):
        gate = AlternativeDataHealthGate()
        metrics = [
            DataHealthMetrics(
                source=DataSourceType.FUNDING_RATE,
                freshness_seconds=30.0,
                coverage_pct=0.99,
                delay_seconds=2.0,
                source_quality_score=0.95,
            ),
            DataHealthMetrics(
                source=DataSourceType.OPEN_INTEREST,
                freshness_seconds=45.0,
                coverage_pct=0.98,
                delay_seconds=3.0,
                source_quality_score=0.92,
            ),
        ]
        result = gate.evaluate(metrics)
        assert result.health_level == DataHealthLevel.HEALTHY
        assert result.reliability_coef > 0.9
        assert result.is_blocked is False

    def test_stale_data_returns_stale(self):
        # Use stricter thresholds: freshness_seconds=320 is in grace period (300 < x <= 360)
        # so freshness_coef=0.25, giving combined=0.25 → STALE
        config = DataHealthConfig(
            thresholds=DataHealthThresholds(
                max_freshness_seconds=300.0,
                max_delay_seconds=60.0,
            )
        )
        gate = AlternativeDataHealthGate(config)
        metrics = [
            DataHealthMetrics(
                source=DataSourceType.FUNDING_RATE,
                freshness_seconds=320.0,  # In grace period: 300 < x <= 360 → 0.25 coef
                coverage_pct=0.90,
                delay_seconds=10.0,
                source_quality_score=0.90,
            ),
        ]
        result = gate.evaluate(metrics)
        assert result.health_level == DataHealthLevel.STALE
        assert result.is_blocked is True

    def test_missing_data_fail_closed(self):
        gate = AlternativeDataHealthGate(DataHealthConfig(thresholds=DataHealthThresholds(), fail_closed=True))
        result = gate.evaluate([])
        assert result.health_level == DataHealthLevel.UNAVAILABLE
        assert result.reliability_coef == 0.0
        assert result.is_blocked is True

    def test_low_coverage_degraded(self):
        gate = AlternativeDataHealthGate()
        metrics = [
            DataHealthMetrics(
                source=DataSourceType.ONCHAIN,
                freshness_seconds=120.0,
                coverage_pct=0.50,  # Low coverage
                delay_seconds=5.0,
                source_quality_score=0.90,
            ),
        ]
        result = gate.evaluate(metrics)
        assert result.health_level == DataHealthLevel.DEGRADED
        assert result.is_blocked is False

    def test_high_delay_unhealthy(self):
        # Use stricter threshold: delay=180 exceeds 120s max, becomes UNAVAILABLE
        # Use higher freshness and lower quality to target UNHEALTHY
        config = DataHealthConfig(
            thresholds=DataHealthThresholds(
                max_freshness_seconds=600.0,
                max_delay_seconds=120.0,
            )
        )
        gate = AlternativeDataHealthGate(config)
        metrics = [
            DataHealthMetrics(
                source=DataSourceType.ANNOUNCEMENT,
                freshness_seconds=600.0,  # freshness_coef=0.75
                coverage_pct=0.90,
                delay_seconds=180.0,  # Exceeds 120s max, delay_coef≈0.5
                source_quality_score=0.50,  # min_quality=0.5, quality_coef=1.0
            ),
        ]
        result = gate.evaluate(metrics)
        # combined ≈ 0.75 * 0.9 * 0.5 * 1.0 = 0.3375 → UNHEALTHY [0.3, 0.6)
        assert result.health_level == DataHealthLevel.UNHEALTHY

    def test_poor_quality_unhealthy(self):
        gate = AlternativeDataHealthGate()
        metrics = [
            DataHealthMetrics(
                source=DataSourceType.EXTERNAL_SIGNAL,
                freshness_seconds=30.0,
                coverage_pct=0.95,
                delay_seconds=5.0,
                source_quality_score=0.20,  # Poor quality
            ),
        ]
        result = gate.evaluate(metrics)
        assert result.health_level == DataHealthLevel.UNHEALTHY

    def test_multiple_sources_worst_determines(self):
        gate = AlternativeDataHealthGate()
        metrics = [
            DataHealthMetrics(
                source=DataSourceType.FUNDING_RATE,
                freshness_seconds=30.0,
                coverage_pct=0.99,
                delay_seconds=2.0,
                source_quality_score=0.95,
            ),
            DataHealthMetrics(
                source=DataSourceType.LIQUIDATION,
                freshness_seconds=700.0,  # Exceeds grace period 660 → 0.0
                coverage_pct=0.80,
                delay_seconds=100.0,
                source_quality_score=0.60,
            ),
        ]
        result = gate.evaluate(metrics)
        # LIQ: freshness=0.0, coverage=1.0, delay=0.75, quality=1.0 → 0.0 → UNAVAILABLE
        # FUNDING: healthy → worst should be UNAVAILABLE
        assert result.health_level == DataHealthLevel.UNAVAILABLE

    def test_none_values_treated_as_unavailable(self):
        """None values = no data = UNAVAILABLE (fail-closed)"""
        gate = AlternativeDataHealthGate()
        metrics = [
            DataHealthMetrics(
                source=DataSourceType.FUNDING_RATE,
                freshness_seconds=None,
                coverage_pct=None,
                delay_seconds=None,
                source_quality_score=None,
            ),
        ]
        result = gate.evaluate(metrics)
        assert result.health_level == DataHealthLevel.UNAVAILABLE
        assert result.reliability_coef == 0.0

    def test_negative_freshness_zero_coef(self):
        gate = AlternativeDataHealthGate()
        metrics = [
            DataHealthMetrics(
                source=DataSourceType.FUNDING_RATE,
                freshness_seconds=-10.0,  # Invalid
                coverage_pct=0.95,
                delay_seconds=5.0,
                source_quality_score=0.90,
            ),
        ]
        result = gate.evaluate(metrics)
        assert result.freshness_coef == 0.0

    def test_custom_thresholds(self):
        config = DataHealthConfig(
            thresholds=DataHealthThresholds(
                max_freshness_seconds=60.0,  # Stricter
                min_coverage_pct=0.95,        # Stricter
            )
        )
        gate = AlternativeDataHealthGate(config)
        metrics = [
            DataHealthMetrics(
                source=DataSourceType.FUNDING_RATE,
                freshness_seconds=120.0,  # Stale under custom threshold
                coverage_pct=0.90,         # Low under custom threshold
                delay_seconds=5.0,
                source_quality_score=0.90,
            ),
        ]
        result = gate.evaluate(metrics)
        assert result.is_blocked is True

    def test_reliability_coef_calculation(self):
        gate = AlternativeDataHealthGate()
        metrics = [
            DataHealthMetrics(
                source=DataSourceType.FUNDING_RATE,
                freshness_seconds=30.0,      # 1.0 coef
                coverage_pct=1.0,            # 1.0 coef
                delay_seconds=0.0,           # 1.0 coef
                source_quality_score=1.0,    # 1.0 coef
            ),
        ]
        result = gate.evaluate(metrics)
        assert result.reliability_coef == 1.0
        assert result.freshness_coef == 1.0
        assert result.coverage_coef == 1.0
        assert result.delay_coef == 1.0
        assert result.quality_coef == 1.0

    def test_reliability_coef_partial_degradation(self):
        # Use stricter thresholds to ensure partial degradation
        config = DataHealthConfig(
            thresholds=DataHealthThresholds(
                max_freshness_seconds=200.0,  # freshness_seconds=250 exceeds this
                max_delay_seconds=20.0,       # delay_seconds=30 exceeds this
            )
        )
        gate = AlternativeDataHealthGate(config)
        metrics = [
            DataHealthMetrics(
                source=DataSourceType.FUNDING_RATE,
                freshness_seconds=250.0,     # 0.0 coef (exceeds max)
                coverage_pct=0.90,           # 1.0 coef
                delay_seconds=30.0,          # ~0.5 coef (exceeds max)
                source_quality_score=0.80,   # 1.0 coef
            ),
        ]
        result = gate.evaluate(metrics)
        # freshness=0.25, coverage=1.0, delay=0.5, quality=1.0 → 0.125 → STALE
        assert result.health_level == DataHealthLevel.STALE
        assert 0.1 < result.reliability_coef < 0.3

    def test_all_source_types(self):
        gate = AlternativeDataHealthGate()
        for source_type in DataSourceType:
            metrics = [
                DataHealthMetrics(
                    source=source_type,
                    freshness_seconds=30.0,
                    coverage_pct=0.95,
                    delay_seconds=5.0,
                    source_quality_score=0.90,
                ),
            ]
            result = gate.evaluate(metrics)
            assert result.health_level == DataHealthLevel.HEALTHY

    def test_empty_stale_sources_on_healthy(self):
        gate = AlternativeDataHealthGate()
        metrics = [
            DataHealthMetrics(
                source=DataSourceType.FUNDING_RATE,
                freshness_seconds=30.0,
                coverage_pct=0.95,
                delay_seconds=5.0,
                source_quality_score=0.90,
            ),
        ]
        result = gate.evaluate(metrics)
        assert result.stale_data_sources == []

    def test_reason_includes_worst_level(self):
        gate = AlternativeDataHealthGate()
        metrics = [
            DataHealthMetrics(
                source=DataSourceType.LIQUIDATION,
                freshness_seconds=600.0,
                coverage_pct=0.50,
                delay_seconds=120.0,
                source_quality_score=0.20,
            ),
        ]
        result = gate.evaluate(metrics)
        assert "stale" in result.reason or "unhealthy" in result.reason
