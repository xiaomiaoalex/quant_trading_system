"""
Unit Tests for Funding/OI Metrics Provider - Service 层
========================================================

测试覆盖：
1. FundingOIMetricsProvider - 正常计算、缺数据、过期数据
2. FeatureStoreFundingOIMetricsProvider - FeatureStore 集成
3. 缺 funding 或 OI 时只影响对应启用阈值
4. fail-closed 行为验证
"""

import time as time_module
from decimal import Decimal
from typing import List

import pytest

from trader.adapters.persistence.feature_store import FeaturePoint
from trader.core.domain.models.crypto_risk import CryptoFundingOIRiskMetrics, CryptoRiskBudget
from trader.services.funding_oi_metrics_provider import (
    CurrentFundingOIPort,
    FundingOIHistoryPort,
    FundingOIMetricsProvider,
    FundingOIMetricsProviderConfig,
)


class FakeFundingOIHistoryAdapter(FundingOIHistoryPort):
    def __init__(
        self,
        funding_history: List[FeaturePoint] | None = None,
        oi_history: List[FeaturePoint] | None = None,
    ) -> None:
        self._funding_history = funding_history or []
        self._oi_history = oi_history or []

    async def read_funding_history(
        self,
        symbol: str,
        start_ts_ms: int,
        end_ts_ms: int,
        version: str = "v1",
    ) -> List[FeaturePoint]:
        return [f for f in self._funding_history if start_ts_ms <= f.ts_ms <= end_ts_ms]

    async def read_oi_history(
        self,
        symbol: str,
        start_ts_ms: int,
        end_ts_ms: int,
        version: str = "v1",
    ) -> List[FeaturePoint]:
        return [f for f in self._oi_history if start_ts_ms <= f.ts_ms <= end_ts_ms]


class FakeCurrentFundingOIAdapter(CurrentFundingOIPort):
    def __init__(
        self,
        current_funding: Decimal | None = None,
        current_oi: Decimal | None = None,
        latest_funding_ts: int = 0,
        latest_oi_ts: int = 0,
    ) -> None:
        self._current_funding = current_funding
        self._current_oi = current_oi
        self._latest_funding_ts = latest_funding_ts
        self._latest_oi_ts = latest_oi_ts

    async def get_current_funding_rate(self, symbol: str) -> Decimal | None:
        return self._current_funding

    async def get_current_open_interest(self, symbol: str) -> Decimal | None:
        return self._current_oi

    async def get_latest_funding_ts_ms(self, symbol: str) -> int:
        return self._latest_funding_ts

    async def get_latest_oi_ts_ms(self, symbol: str) -> int:
        return self._latest_oi_ts


def make_feature_point(symbol: str, feature_name: str, ts_ms: int, value: float) -> FeaturePoint:
    return FeaturePoint(
        symbol=symbol,
        feature_name=feature_name,
        version="v1",
        ts_ms=ts_ms,
        value=value,
    )


def make_budget(
    funding_z_score_cap: str = "2.0",
    oi_change_rate_cap: str = "2.0",
) -> CryptoRiskBudget:
    return CryptoRiskBudget(
        max_abs_funding_rate_z_score=Decimal(funding_z_score_cap),
        max_abs_open_interest_change_rate=Decimal(oi_change_rate_cap),
        funding_history_window=20,
        oi_history_window=20,
        funding_min_periods=10,
        oi_min_periods=10,
        max_data_age_seconds=86400,
    )


def create_test_data(now_ms: int, history_days: int = 7) -> tuple:
    history_start = now_ms - (history_days * 24 * 3600 * 1000)
    funding_history = [
        make_feature_point(
            "BTCUSDT", "funding_rate", history_start + i * 100000, 0.0001 + (i % 5) * 0.00001
        )
        for i in range(20)
    ]
    oi_history = [
        make_feature_point("BTCUSDT", "open_interest", history_start + i * 100000, 1000.0 + i * 10)
        for i in range(20)
    ]
    return funding_history, oi_history, history_start


@pytest.mark.asyncio
async def test_provider_computes_metrics_for_single_symbol() -> None:
    now_ms = int(time_module.time() * 1000)
    funding_history, oi_history, _ = create_test_data(now_ms)

    history_adapter = FakeFundingOIHistoryAdapter(
        funding_history=funding_history,
        oi_history=oi_history,
    )
    current_adapter = FakeCurrentFundingOIAdapter(
        current_funding=Decimal("0.00015"),
        current_oi=Decimal("1200.0"),
        latest_funding_ts=now_ms - 1000,
        latest_oi_ts=now_ms - 1000,
    )

    provider = FundingOIMetricsProvider(
        funding_oi_history=history_adapter,
        current_funding_oi=current_adapter,
    )
    budget = make_budget()

    result = await provider.compute_metrics(
        symbol="BTCUSDT",
        budget=budget,
    )

    assert "BTCUSDT" in result
    metrics = result["BTCUSDT"]
    assert isinstance(metrics, CryptoFundingOIRiskMetrics)
    assert metrics.symbol == "BTCUSDT"
    assert metrics.current_funding_rate == Decimal("0.00015")
    assert metrics.current_open_interest == Decimal("1200.0")
    assert metrics.funding_rate_z_score is not None
    assert metrics.open_interest_change_rate is not None
    assert metrics.data_stale is False
    assert metrics.window_insufficient is False


@pytest.mark.asyncio
async def test_provider_computes_metrics_for_multiple_symbols() -> None:
    now_ms = int(time_module.time() * 1000)
    history_days = 7
    history_start = now_ms - (history_days * 24 * 3600 * 1000)

    funding_history = [
        make_feature_point("BTCUSDT", "funding_rate", history_start + i * 100000, 0.0001)
        for i in range(20)
    ] + [
        make_feature_point("ETHUSDT", "funding_rate", history_start + i * 100000, 0.0002)
        for i in range(20)
    ]
    oi_history = [
        make_feature_point("BTCUSDT", "open_interest", history_start + i * 100000, 1000.0)
        for i in range(20)
    ] + [
        make_feature_point("ETHUSDT", "open_interest", history_start + i * 100000, 5000.0)
        for i in range(20)
    ]

    history_adapter = FakeFundingOIHistoryAdapter(
        funding_history=funding_history,
        oi_history=oi_history,
    )
    current_adapter = FakeCurrentFundingOIAdapter(
        current_funding=Decimal("0.00015"),
        current_oi=Decimal("1200.0"),
        latest_funding_ts=now_ms - 1000,
        latest_oi_ts=now_ms - 1000,
    )

    provider = FundingOIMetricsProvider(
        funding_oi_history=history_adapter,
        current_funding_oi=current_adapter,
    )
    budget = make_budget()

    result = await provider.compute_metrics(
        symbol="BTCUSDT",
        budget=budget,
        symbols={"BTCUSDT", "ETHUSDT"},
    )

    assert "BTCUSDT" in result
    assert "ETHUSDT" in result


@pytest.mark.asyncio
async def test_provider_window_insufficient_returns_none_z_score() -> None:
    now_ms = int(time_module.time() * 1000)
    history_days = 7
    history_start = now_ms - (history_days * 24 * 3600 * 1000)

    funding_history = [
        make_feature_point("BTCUSDT", "funding_rate", history_start + i * 100000, 0.0001)
        for i in range(5)
    ]
    oi_history = [
        make_feature_point("BTCUSDT", "open_interest", history_start + i * 100000, 1000.0 + i * 10)
        for i in range(20)
    ]

    history_adapter = FakeFundingOIHistoryAdapter(
        funding_history=funding_history,
        oi_history=oi_history,
    )
    current_adapter = FakeCurrentFundingOIAdapter(
        current_funding=Decimal("0.00015"),
        current_oi=Decimal("1200.0"),
        latest_funding_ts=now_ms - 1000,
        latest_oi_ts=now_ms - 1000,
    )

    provider = FundingOIMetricsProvider(
        funding_oi_history=history_adapter,
        current_funding_oi=current_adapter,
    )
    budget = make_budget()

    result = await provider.compute_metrics(
        symbol="BTCUSDT",
        budget=budget,
    )

    metrics = result["BTCUSDT"]
    assert metrics.window_insufficient is True
    assert metrics.funding_rate_z_score is None
    assert metrics.open_interest_change_rate is not None


@pytest.mark.asyncio
async def test_provider_missing_current_funding_returns_none() -> None:
    now_ms = int(time_module.time() * 1000)
    funding_history, oi_history, _ = create_test_data(now_ms)

    history_adapter = FakeFundingOIHistoryAdapter(
        funding_history=funding_history,
        oi_history=oi_history,
    )
    current_adapter = FakeCurrentFundingOIAdapter(
        current_funding=None,
        current_oi=Decimal("1200.0"),
        latest_funding_ts=now_ms - 1000,
        latest_oi_ts=now_ms - 1000,
    )

    provider = FundingOIMetricsProvider(
        funding_oi_history=history_adapter,
        current_funding_oi=current_adapter,
    )
    budget = make_budget()

    result = await provider.compute_metrics(
        symbol="BTCUSDT",
        budget=budget,
    )

    metrics = result["BTCUSDT"]
    assert metrics.current_funding_rate is None
    assert metrics.funding_rate_z_score is None
    assert metrics.funding_current_missing is True
    assert metrics.open_interest_change_rate is not None
    assert metrics.oi_current_missing is False


@pytest.mark.asyncio
async def test_provider_missing_current_oi_returns_none() -> None:
    now_ms = int(time_module.time() * 1000)
    funding_history, oi_history, _ = create_test_data(now_ms)

    history_adapter = FakeFundingOIHistoryAdapter(
        funding_history=funding_history,
        oi_history=oi_history,
    )
    current_adapter = FakeCurrentFundingOIAdapter(
        current_funding=Decimal("0.00015"),
        current_oi=None,
        latest_funding_ts=now_ms - 1000,
        latest_oi_ts=now_ms - 1000,
    )

    provider = FundingOIMetricsProvider(
        funding_oi_history=history_adapter,
        current_funding_oi=current_adapter,
    )
    budget = make_budget()

    result = await provider.compute_metrics(
        symbol="BTCUSDT",
        budget=budget,
    )

    metrics = result["BTCUSDT"]
    assert metrics.current_open_interest is None
    assert metrics.open_interest_change_rate is None
    assert metrics.oi_current_missing is True
    assert metrics.funding_rate_z_score is not None
    assert metrics.funding_current_missing is False


@pytest.mark.asyncio
async def test_provider_data_stale_when_too_old() -> None:
    now_ms = int(time_module.time() * 1000)
    funding_history, oi_history, _ = create_test_data(now_ms)

    history_adapter = FakeFundingOIHistoryAdapter(
        funding_history=funding_history,
        oi_history=oi_history,
    )
    current_adapter = FakeCurrentFundingOIAdapter(
        current_funding=Decimal("0.00015"),
        current_oi=Decimal("1200.0"),
        latest_funding_ts=now_ms - 100000000,
        latest_oi_ts=now_ms - 100000000,
    )

    provider = FundingOIMetricsProvider(
        funding_oi_history=history_adapter,
        current_funding_oi=current_adapter,
    )
    budget = make_budget()

    result = await provider.compute_metrics(
        symbol="BTCUSDT",
        budget=budget,
    )

    metrics = result["BTCUSDT"]
    assert metrics.data_stale is True


@pytest.mark.asyncio
async def test_provider_oi_only_stale() -> None:
    now_ms = int(time_module.time() * 1000)
    funding_history, oi_history, _ = create_test_data(now_ms)

    history_adapter = FakeFundingOIHistoryAdapter(
        funding_history=funding_history,
        oi_history=oi_history,
    )
    current_adapter = FakeCurrentFundingOIAdapter(
        current_funding=Decimal("0.00015"),
        current_oi=Decimal("1200.0"),
        latest_funding_ts=now_ms - 1000,
        latest_oi_ts=now_ms - 100000000,
    )

    provider = FundingOIMetricsProvider(
        funding_oi_history=history_adapter,
        current_funding_oi=current_adapter,
    )
    budget = make_budget()

    result = await provider.compute_metrics(
        symbol="BTCUSDT",
        budget=budget,
    )

    metrics = result["BTCUSDT"]
    assert metrics.data_stale is True


@pytest.mark.asyncio
async def test_provider_funding_only_stale() -> None:
    now_ms = int(time_module.time() * 1000)
    funding_history, oi_history, _ = create_test_data(now_ms)

    history_adapter = FakeFundingOIHistoryAdapter(
        funding_history=funding_history,
        oi_history=oi_history,
    )
    current_adapter = FakeCurrentFundingOIAdapter(
        current_funding=Decimal("0.00015"),
        current_oi=Decimal("1200.0"),
        latest_funding_ts=now_ms - 100000000,
        latest_oi_ts=now_ms - 1000,
    )

    provider = FundingOIMetricsProvider(
        funding_oi_history=history_adapter,
        current_funding_oi=current_adapter,
    )
    budget = make_budget()

    result = await provider.compute_metrics(
        symbol="BTCUSDT",
        budget=budget,
    )

    metrics = result["BTCUSDT"]
    assert metrics.data_stale is True


@pytest.mark.asyncio
async def test_provider_missing_funding_no_impact_on_oi() -> None:
    now_ms = int(time_module.time() * 1000)
    history_days = 7
    history_start = now_ms - (history_days * 24 * 3600 * 1000)

    funding_history: List[FeaturePoint] = []
    oi_history = [
        make_feature_point("BTCUSDT", "open_interest", history_start + i * 100000, 1000.0 + i * 10)
        for i in range(20)
    ]

    history_adapter = FakeFundingOIHistoryAdapter(
        funding_history=funding_history,
        oi_history=oi_history,
    )
    current_adapter = FakeCurrentFundingOIAdapter(
        current_funding=None,
        current_oi=Decimal("1200.0"),
        latest_funding_ts=now_ms - 1000,
        latest_oi_ts=now_ms - 1000,
    )

    provider = FundingOIMetricsProvider(
        funding_oi_history=history_adapter,
        current_funding_oi=current_adapter,
    )
    budget = make_budget()

    result = await provider.compute_metrics(
        symbol="BTCUSDT",
        budget=budget,
    )

    metrics = result["BTCUSDT"]
    assert metrics.window_insufficient is True
    assert metrics.funding_rate_z_score is None
    assert metrics.open_interest_change_rate is not None


@pytest.mark.asyncio
async def test_provider_missing_oi_no_impact_on_funding() -> None:
    now_ms = int(time_module.time() * 1000)
    history_days = 7
    history_start = now_ms - (history_days * 24 * 3600 * 1000)

    funding_history = [
        make_feature_point(
            "BTCUSDT", "funding_rate", history_start + i * 100000, 0.0001 + (i % 3) * 0.00001
        )
        for i in range(20)
    ]
    oi_history: List[FeaturePoint] = []

    history_adapter = FakeFundingOIHistoryAdapter(
        funding_history=funding_history,
        oi_history=oi_history,
    )
    current_adapter = FakeCurrentFundingOIAdapter(
        current_funding=Decimal("0.00015"),
        current_oi=None,
        latest_funding_ts=now_ms - 1000,
        latest_oi_ts=now_ms - 1000,
    )

    provider = FundingOIMetricsProvider(
        funding_oi_history=history_adapter,
        current_funding_oi=current_adapter,
    )
    budget = make_budget()

    result = await provider.compute_metrics(
        symbol="BTCUSDT",
        budget=budget,
    )

    metrics = result["BTCUSDT"]
    assert metrics.window_insufficient is True
    assert metrics.funding_rate_z_score is not None
    assert metrics.open_interest_change_rate is None


@pytest.mark.asyncio
async def test_provider_custom_config() -> None:
    now_ms = int(time_module.time() * 1000)
    history_days = 3
    funding_history_days = 3
    history_start = now_ms - (funding_history_days * 24 * 3600 * 1000)

    funding_history = [
        make_feature_point(
            "BTCUSDT", "funding_rate", history_start + i * 100000, 0.0001 + (i % 3) * 0.00001
        )
        for i in range(30)
    ]
    oi_history = [
        make_feature_point("BTCUSDT", "open_interest", history_start + i * 100000, 1000.0 + i * 10)
        for i in range(30)
    ]

    history_adapter = FakeFundingOIHistoryAdapter(
        funding_history=funding_history,
        oi_history=oi_history,
    )
    current_adapter = FakeCurrentFundingOIAdapter(
        current_funding=Decimal("0.00015"),
        current_oi=Decimal("1200.0"),
        latest_funding_ts=now_ms - 5000000,
        latest_oi_ts=now_ms - 5000000,
    )

    config = FundingOIMetricsProviderConfig(
        feature_version="v2",
        default_window=25,
        default_min_periods=15,
        max_data_age_seconds=3600,
        funding_history_days=3,
        oi_history_days=3,
    )

    provider = FundingOIMetricsProvider(
        funding_oi_history=history_adapter,
        current_funding_oi=current_adapter,
        config=config,
    )
    budget = CryptoRiskBudget(
        max_abs_funding_rate_z_score=Decimal("2.0"),
        max_abs_open_interest_change_rate=Decimal("2.0"),
        funding_history_window=25,
        oi_history_window=25,
        funding_min_periods=15,
        oi_min_periods=15,
        max_data_age_seconds=3600,
    )

    result = await provider.compute_metrics(
        symbol="BTCUSDT",
        budget=budget,
    )

    metrics = result["BTCUSDT"]
    assert metrics.data_stale is True
    assert metrics.funding_history_count == 25
    assert metrics.oi_history_count == 25
