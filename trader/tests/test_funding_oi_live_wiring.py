"""
阶段3.3 Wiring 测试 - Live Funding/OI 数据源接线

测试覆盖：
1. BinanceFundingOIMetricsSource 真实拉取 funding rate 和 OI
2. DataSourceCryptoRiskSnapshotProvider 调用 FundingOIMetricsPort
3. funding rate 为 0 时不会被误判成缺失
4. 没有 current source 时返回 stale flags
"""

import asyncio
from decimal import Decimal
from unittest.mock import AsyncMock, MagicMock

import pytest

from trader.core.domain.models.crypto_risk import CryptoRiskBudget, CryptoRiskSnapshot
from trader.services.crypto_risk_snapshot import (
    BinanceFundingOIMetricsSource,
    FundingOIMetricsPort,
)


def make_budget(
    funding_z_score_cap: str = "2.0",
    oi_change_rate_cap: str = "2.0",
    funding_history_window: int = 20,
    oi_history_window: int = 20,
    funding_min_periods: int = 10,
    oi_min_periods: int = 10,
) -> CryptoRiskBudget:
    return CryptoRiskBudget(
        max_abs_funding_rate_z_score=Decimal(funding_z_score_cap),
        max_abs_open_interest_change_rate=Decimal(oi_change_rate_cap),
        funding_history_window=funding_history_window,
        oi_history_window=oi_history_window,
        funding_min_periods=funding_min_periods,
        oi_min_periods=oi_min_periods,
        max_data_age_seconds=86400,
    )


class MockCurrentSource:
    def __init__(
        self,
        funding_rate: Decimal | None = Decimal("0.0001"),
        open_interest: Decimal | None = Decimal("1000000"),
        funding_ts: int = 0,
        oi_ts: int = 0,
    ) -> None:
        self._funding_rate = funding_rate
        self._open_interest = open_interest
        self._funding_ts = funding_ts or int(asyncio.get_event_loop().time() * 1000)
        self._oi_ts = oi_ts or int(asyncio.get_event_loop().time() * 1000)

    async def get_current_funding_rate(self, symbol: str) -> Decimal | None:
        return self._funding_rate

    async def get_current_open_interest(self, symbol: str) -> Decimal | None:
        return self._open_interest

    async def get_latest_funding_ts_ms(self, symbol: str) -> int:
        return self._funding_ts

    async def get_latest_oi_ts_ms(self, symbol: str) -> int:
        return self._oi_ts


class TestBinanceFundingOIMetricsSource:
    @pytest.mark.asyncio
    async def test_has_budget_enabled_with_z_score_cap(self):
        budget = make_budget(funding_z_score_cap="2.0")
        source = BinanceFundingOIMetricsSource(budget=budget)
        assert source.has_budget_enabled() is True

    @pytest.mark.asyncio
    async def test_has_budget_enabled_with_oi_cap(self):
        budget = make_budget(oi_change_rate_cap="2.0")
        source = BinanceFundingOIMetricsSource(budget=budget)
        assert source.has_budget_enabled() is True

    @pytest.mark.asyncio
    async def test_has_budget_enabled_disabled(self):
        budget = make_budget(funding_z_score_cap="0", oi_change_rate_cap="0")
        source = BinanceFundingOIMetricsSource(budget=budget)
        assert source.has_budget_enabled() is False

    @pytest.mark.asyncio
    async def test_compute_metrics_with_current_source(self):
        current_source = MockCurrentSource(
            funding_rate=Decimal("0.0001"),
            open_interest=Decimal("1000000"),
        )
        budget = make_budget()
        source = BinanceFundingOIMetricsSource(
            current_source=current_source,
            budget=budget,
        )
        result = await source.compute_funding_oi_metrics({"BTCUSDT"})

        assert "BTCUSDT" in result
        metrics = result["BTCUSDT"]
        assert metrics is not None
        assert metrics.funding_current_missing is False
        assert metrics.oi_current_missing is False

    @pytest.mark.asyncio
    async def test_compute_metrics_without_current_source_returns_stale_flags(self):
        budget = make_budget()
        source = BinanceFundingOIMetricsSource(budget=budget)
        result = await source.compute_funding_oi_metrics({"BTCUSDT"})

        assert "BTCUSDT" in result
        metrics = result["BTCUSDT"]
        assert metrics.funding_current_missing is True
        assert metrics.oi_current_missing is True

    @pytest.mark.asyncio
    async def test_funding_rate_zero_not_treated_as_missing(self):
        current_source = MockCurrentSource(funding_rate=Decimal("0"))
        budget = make_budget(funding_min_periods=1)
        source = BinanceFundingOIMetricsSource(
            current_source=current_source,
            budget=budget,
        )
        result = await source.compute_funding_oi_metrics({"BTCUSDT"})

        assert "BTCUSDT" in result
        metrics = result["BTCUSDT"]
        assert metrics.funding_current_missing is False

    @pytest.mark.asyncio
    async def test_compute_metrics_returns_metrics_for_multiple_symbols(self):
        budget = make_budget()
        source = BinanceFundingOIMetricsSource(budget=budget)
        result = await source.compute_funding_oi_metrics({"BTCUSDT", "ETHUSDT"})

        assert "BTCUSDT" in result
        assert "ETHUSDT" in result


class TestDataSourceCryptoRiskSnapshotProviderFundingOI:
    @pytest.mark.asyncio
    async def test_provider_without_funding_oi_returns_empty_metrics(self):
        from trader.services.crypto_risk_snapshot import (
            CryptoRiskSnapshotProviderConfig,
            DataSourceCryptoRiskSnapshotProvider,
        )

        mock_source = MagicMock()
        mock_source.get_account_risk = AsyncMock(
            return_value=MagicMock(
                total_margin_balance=Decimal("1000"),
                total_position_value=Decimal("500"),
            )
        )
        mock_source.get_positions = AsyncMock(return_value=[])
        mock_source.get_open_orders = AsyncMock(return_value=[])
        mock_source.get_instrument_specs = AsyncMock(
            return_value={
                "BTCUSDT": MagicMock(
                    tick_size=Decimal("0.01"),
                    min_notional=Decimal("10"),
                    market_type=MagicMock(value="spot"),
                )
            }
        )
        mock_source.get_leverage_brackets = AsyncMock(return_value={"BTCUSDT": []})
        mock_source.get_mark_prices = AsyncMock(return_value={"BTCUSDT": Decimal("50000")})
        mock_source.get_venue_health = AsyncMock(return_value="HEALTHY")

        provider = DataSourceCryptoRiskSnapshotProvider(
            source=mock_source,
            config=CryptoRiskSnapshotProviderConfig(fail_on_missing_brackets=False),
        )

        signal = MagicMock()
        signal.symbol = "BTCUSDT"

        snapshot = await provider.build(signal)

        assert snapshot.funding_oi_metrics == {}


class TestBinanceCurrentFundingOISource:
    @pytest.mark.asyncio
    async def test_get_current_funding_rate_returns_decimal(self):
        from trader.adapters.binance.funding_oi_stream import BinanceCurrentFundingOISource

        class FakeResponse:
            status = 200
            async def json(self):
                return [{"fundingRate": "0.0001", "fundingTime": 1234567890000}]

        class FakeContextManager:
            async def __aenter__(self):
                return FakeResponse()
            async def __aexit__(self, *args):
                pass

        class FakeSession:
            closed = False
            def get(self, url, params=None):
                return FakeContextManager()
            async def close(self):
                pass

        source = BinanceCurrentFundingOISource()
        source._session = FakeSession()
        result = await source.get_current_funding_rate("BTCUSDT")
        assert isinstance(result, Decimal)
        assert result == Decimal("0.0001")

    @pytest.mark.asyncio
    async def test_get_current_open_interest_returns_decimal(self):
        from trader.adapters.binance.funding_oi_stream import BinanceCurrentFundingOISource

        class FakeResponse:
            status = 200
            async def json(self):
                return {"openInterest": "1000000.5", "updateTime": 1234567890000}

        class FakeContextManager:
            async def __aenter__(self):
                return FakeResponse()
            async def __aexit__(self, *args):
                pass

        class FakeSession:
            closed = False
            def get(self, url, params=None):
                return FakeContextManager()
            async def close(self):
                pass

        source = BinanceCurrentFundingOISource()
        source._session = FakeSession()
        result = await source.get_current_open_interest("BTCUSDT")
        assert isinstance(result, Decimal)
        assert result == Decimal("1000000.5")

    @pytest.mark.asyncio
    async def test_get_latest_funding_ts_ms_returns_int(self):
        from trader.adapters.binance.funding_oi_stream import BinanceCurrentFundingOISource

        class FakeResponse:
            status = 200
            async def json(self):
                return [{"fundingRate": "0.0001", "fundingTime": 1234567890000}]

        class FakeContextManager:
            async def __aenter__(self):
                return FakeResponse()
            async def __aexit__(self, *args):
                pass

        class FakeSession:
            closed = False
            def get(self, url, params=None):
                return FakeContextManager()
            async def close(self):
                pass

        source = BinanceCurrentFundingOISource()
        source._session = FakeSession()
        result = await source.get_latest_funding_ts_ms("BTCUSDT")
        assert isinstance(result, int)
        assert result == 1234567890000

    @pytest.mark.asyncio
    async def test_get_latest_oi_ts_ms_returns_int(self):
        from trader.adapters.binance.funding_oi_stream import BinanceCurrentFundingOISource

        class FakeResponse:
            status = 200
            async def json(self):
                return {"openInterest": "1000000.5", "updateTime": 1234567890000}

        class FakeContextManager:
            async def __aenter__(self):
                return FakeResponse()
            async def __aexit__(self, *args):
                pass

        class FakeSession:
            closed = False
            def get(self, url, params=None):
                return FakeContextManager()
            async def close(self):
                pass

        source = BinanceCurrentFundingOISource()
        source._session = FakeSession()
        result = await source.get_latest_oi_ts_ms("BTCUSDT")
        assert isinstance(result, int)
        assert result == 1234567890000

    @pytest.mark.asyncio
    async def test_empty_response_returns_none_or_zero(self):
        from trader.adapters.binance.funding_oi_stream import BinanceCurrentFundingOISource

        class FakeResponse:
            status = 200
            async def json(self):
                return []

        class FakeContextManager:
            async def __aenter__(self):
                return FakeResponse()
            async def __aexit__(self, *args):
                pass

        class FakeSession:
            closed = False
            def get(self, url, params=None):
                return FakeContextManager()
            async def close(self):
                pass

        source = BinanceCurrentFundingOISource()
        source._session = FakeSession()
        result = await source.get_current_funding_rate("BTCUSDT")
        assert result is None

    @pytest.mark.asyncio
    async def test_429_returns_none_after_retry(self):
        from trader.adapters.binance.funding_oi_stream import BinanceCurrentFundingOISource

        class FakeResponse:
            status = 429
            async def text(self):
                return "Rate limited"

        class FakeContextManager:
            async def __aenter__(self):
                return FakeResponse()
            async def __aexit__(self, *args):
                pass

        class FakeSession:
            closed = False
            def get(self, url, params=None):
                return FakeContextManager()
            async def close(self):
                pass

        source = BinanceCurrentFundingOISource()
        source._session = FakeSession()
        result = await source.get_current_funding_rate("BTCUSDT")
        assert result is None