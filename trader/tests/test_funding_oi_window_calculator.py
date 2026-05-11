"""
Unit Tests for Funding/OI Window Calculator - Core 纯计算服务
=============================================================

测试覆盖：
1. compute_funding_z_score - 正常计算、边界情况、窗口不足、当前值缺失
2. compute_oi_change_rate - 正常计算、边界情况、窗口不足、当前值缺失、真正的变化率
3. compute_funding_oi_metrics - 综合计算、数据过期判断、缺失标志拆分
4. fail-closed 行为验证
"""

from decimal import Decimal

import pytest

from trader.core.domain.models.crypto_risk import CryptoFundingOIRiskMetrics
from trader.core.domain.services.funding_oi_window_calculator import (
    FundingOIWindowCalculator,
    FundingRateZScoreResult,
    OIChangeRateResult,
)


class TestFundingRateZScore:
    def test_compute_with_sufficient_history(self) -> None:
        symbol = "BTCUSDT"
        current_rate = 0.00015
        history = [0.0001 + (i % 5) * 0.00001 for i in range(20)]

        result = FundingOIWindowCalculator.compute_funding_z_score(
            symbol=symbol,
            current_funding_rate=current_rate,
            history=history,
            window=20,
            min_periods=10,
        )

        assert isinstance(result, FundingRateZScoreResult)
        assert result.symbol == symbol
        assert result.z_score is not None
        assert result.sample_count == 20
        assert result.mean > 0
        assert result.std >= 0

    def test_compute_window_insufficient_returns_none(self) -> None:
        symbol = "BTCUSDT"
        current_rate = 0.0001
        history = [0.0001] * 5

        result = FundingOIWindowCalculator.compute_funding_z_score(
            symbol=symbol,
            current_funding_rate=current_rate,
            history=history,
            window=20,
            min_periods=10,
        )

        assert result.z_score is None
        assert result.sample_count == 5

    def test_compute_zero_std_returns_none(self) -> None:
        symbol = "BTCUSDT"
        current_rate = 0.0002
        history = [0.0001] * 20

        result = FundingOIWindowCalculator.compute_funding_z_score(
            symbol=symbol,
            current_funding_rate=current_rate,
            history=history,
            window=20,
            min_periods=10,
        )

        assert result.z_score is None
        assert result.std == 0.0

    def test_compute_negative_zscore(self) -> None:
        symbol = "BTCUSDT"
        current_rate = 0.00005
        history = [0.0001 + (i % 3) * 0.00002 for i in range(20)]

        result = FundingOIWindowCalculator.compute_funding_z_score(
            symbol=symbol,
            current_funding_rate=current_rate,
            history=history,
            window=20,
            min_periods=10,
        )

        assert result.z_score is not None
        assert result.z_score < 0

    def test_compute_positive_zscore(self) -> None:
        symbol = "BTCUSDT"
        current_rate = 0.00020
        history = [0.0001] * 15 + [0.00015] * 5

        result = FundingOIWindowCalculator.compute_funding_z_score(
            symbol=symbol,
            current_funding_rate=current_rate,
            history=history,
            window=20,
            min_periods=10,
        )

        assert result.z_score is not None
        assert result.z_score > 0

    def test_compute_empty_history(self) -> None:
        symbol = "BTCUSDT"
        current_rate = 0.0001
        history: list[float] = []

        result = FundingOIWindowCalculator.compute_funding_z_score(
            symbol=symbol,
            current_funding_rate=current_rate,
            history=history,
            window=20,
            min_periods=10,
        )

        assert result.z_score is None
        assert result.sample_count == 0

    def test_compute_uses_only_window_samples(self) -> None:
        symbol = "BTCUSDT"
        current_rate = 0.00025
        history = [0.0001] * 100 + [0.0001 + (i % 3) * 0.00003 for i in range(20)]

        result = FundingOIWindowCalculator.compute_funding_z_score(
            symbol=symbol,
            current_funding_rate=current_rate,
            history=history,
            window=20,
            min_periods=10,
        )

        assert result.sample_count == 20
        assert result.z_score is not None

    def test_compute_none_current_returns_none(self) -> None:
        symbol = "BTCUSDT"
        current_rate = None
        history = [0.0001 + (i % 5) * 0.00001 for i in range(20)]

        result = FundingOIWindowCalculator.compute_funding_z_score(
            symbol=symbol,
            current_funding_rate=current_rate,
            history=history,
            window=20,
            min_periods=10,
        )

        assert result.z_score is None
        assert result.sample_count == 20


class TestOIChangeRate:
    def test_compute_with_sufficient_history(self) -> None:
        symbol = "BTCUSDT"
        current_oi = 1000.0
        history = [1000.0] * 20

        result = FundingOIWindowCalculator.compute_oi_change_rate(
            symbol=symbol,
            current_oi=current_oi,
            history=history,
            window=20,
            min_periods=10,
        )

        assert isinstance(result, OIChangeRateResult)
        assert result.symbol == symbol
        assert result.change_rate is not None
        assert result.sample_count == 20
        assert result.change_rate == 0.0

    def test_compute_window_insufficient_returns_none(self) -> None:
        symbol = "BTCUSDT"
        current_oi = 1000.0
        history = [900.0] * 5

        result = FundingOIWindowCalculator.compute_oi_change_rate(
            symbol=symbol,
            current_oi=current_oi,
            history=history,
            window=20,
            min_periods=10,
        )

        assert result.change_rate is None
        assert result.sample_count == 5

    def test_compute_zero_mean_returns_none(self) -> None:
        symbol = "BTCUSDT"
        current_oi = 100.0
        history = [0.0] * 20

        result = FundingOIWindowCalculator.compute_oi_change_rate(
            symbol=symbol,
            current_oi=current_oi,
            history=history,
            window=20,
            min_periods=10,
        )

        assert result.change_rate is None

    def test_compute_positive_change_rate(self) -> None:
        symbol = "BTCUSDT"
        current_oi = 1500.0
        history = [1000.0] * 15 + [1100.0] * 5

        result = FundingOIWindowCalculator.compute_oi_change_rate(
            symbol=symbol,
            current_oi=current_oi,
            history=history,
            window=20,
            min_periods=10,
        )

        assert result.change_rate is not None
        assert result.change_rate > 0

    def test_compute_negative_change_rate(self) -> None:
        symbol = "BTCUSDT"
        current_oi = 800.0
        history = [1000.0] * 15 + [900.0] * 5

        result = FundingOIWindowCalculator.compute_oi_change_rate(
            symbol=symbol,
            current_oi=current_oi,
            history=history,
            window=20,
            min_periods=10,
        )

        assert result.change_rate is not None
        assert result.change_rate < 0

    def test_compute_empty_history(self) -> None:
        symbol = "BTCUSDT"
        current_oi = 1000.0
        history: list[float] = []

        result = FundingOIWindowCalculator.compute_oi_change_rate(
            symbol=symbol,
            current_oi=current_oi,
            history=history,
            window=20,
            min_periods=10,
        )

        assert result.change_rate is None
        assert result.sample_count == 0

    def test_compute_none_current_returns_none(self) -> None:
        symbol = "BTCUSDT"
        current_oi = None
        history = [900.0 + i * 5.0 for i in range(20)]

        result = FundingOIWindowCalculator.compute_oi_change_rate(
            symbol=symbol,
            current_oi=current_oi,
            history=history,
            window=20,
            min_periods=10,
        )

        assert result.change_rate is None
        assert result.sample_count == 20

    def test_change_rate_is_percentage(self) -> None:
        symbol = "BTCUSDT"
        current_oi = 1100.0
        history = [1000.0] * 20

        result = FundingOIWindowCalculator.compute_oi_change_rate(
            symbol=symbol,
            current_oi=current_oi,
            history=history,
            window=20,
            min_periods=10,
        )

        assert result.change_rate == 10.0


class TestComputeFundingOIMetrics:
    def test_compute_combined_metrics(self) -> None:
        symbol = "BTCUSDT"
        current_funding = 0.00015
        current_oi = 1000.0
        funding_history = [0.0001 + (i % 5) * 0.00001 for i in range(20)]
        oi_history = [900.0 + i * 5.0 for i in range(20)]
        current_ts = 1700000000000
        latest_funding_ts = 1699999900000
        latest_oi_ts = 1699999900000

        result = FundingOIWindowCalculator.compute_funding_oi_metrics(
            symbol=symbol,
            current_funding_rate=current_funding,
            current_oi=current_oi,
            funding_history=funding_history,
            oi_history=oi_history,
            funding_window=20,
            oi_window=20,
            funding_min_periods=10,
            oi_min_periods=10,
            latest_funding_ts_ms=latest_funding_ts,
            latest_oi_ts_ms=latest_oi_ts,
            current_ts_ms=current_ts,
            max_data_age_seconds=86400,
        )

        assert isinstance(result, CryptoFundingOIRiskMetrics)
        assert result.symbol == symbol
        assert result.current_funding_rate == Decimal(str(current_funding))
        assert result.current_open_interest == Decimal(str(current_oi))
        assert result.funding_rate_z_score is not None
        assert result.open_interest_change_rate is not None
        assert result.data_stale is False
        assert result.window_insufficient is False
        assert result.funding_current_missing is False
        assert result.oi_current_missing is False

    def test_compute_data_stale(self) -> None:
        symbol = "BTCUSDT"
        current_funding = 0.00015
        current_oi = 1000.0
        funding_history = [0.0001] * 20
        oi_history = [1000.0] * 20
        current_ts = 1700000000000
        latest_funding_ts = 1699800000000
        latest_oi_ts = 1699800000000

        result = FundingOIWindowCalculator.compute_funding_oi_metrics(
            symbol=symbol,
            current_funding_rate=current_funding,
            current_oi=current_oi,
            funding_history=funding_history,
            oi_history=oi_history,
            funding_window=20,
            oi_window=20,
            funding_min_periods=10,
            oi_min_periods=10,
            latest_funding_ts_ms=latest_funding_ts,
            latest_oi_ts_ms=latest_oi_ts,
            current_ts_ms=current_ts,
            max_data_age_seconds=86400,
        )

        assert result.funding_data_stale is True
        assert result.oi_data_stale is True
        assert result.data_stale is True

    def test_compute_window_insufficient(self) -> None:
        symbol = "BTCUSDT"
        current_funding = 0.00015
        current_oi = 1000.0
        funding_history = [0.0001] * 5
        oi_history = [1000.0] * 5
        current_ts = 1700000000000

        result = FundingOIWindowCalculator.compute_funding_oi_metrics(
            symbol=symbol,
            current_funding_rate=current_funding,
            current_oi=current_oi,
            funding_history=funding_history,
            oi_history=oi_history,
            funding_window=20,
            oi_window=20,
            funding_min_periods=10,
            oi_min_periods=10,
            latest_funding_ts_ms=0,
            latest_oi_ts_ms=0,
            current_ts_ms=current_ts,
            max_data_age_seconds=86400,
        )

        assert result.window_insufficient is True
        assert result.funding_window_insufficient is True
        assert result.oi_window_insufficient is True
        assert result.funding_rate_z_score is None
        assert result.open_interest_change_rate is None

    def test_compute_funding_only_stale(self) -> None:
        symbol = "BTCUSDT"
        current_funding = 0.00015
        current_oi = 1000.0
        funding_history = [0.0001] * 20
        oi_history = [1000.0] * 20
        current_ts = 1700000000000
        latest_funding_ts = 1699800000000
        latest_oi_ts = 1699999900000

        result = FundingOIWindowCalculator.compute_funding_oi_metrics(
            symbol=symbol,
            current_funding_rate=current_funding,
            current_oi=current_oi,
            funding_history=funding_history,
            oi_history=oi_history,
            funding_window=20,
            oi_window=20,
            funding_min_periods=10,
            oi_min_periods=10,
            latest_funding_ts_ms=latest_funding_ts,
            latest_oi_ts_ms=latest_oi_ts,
            current_ts_ms=current_ts,
            max_data_age_seconds=86400,
        )

        assert result.data_stale is True
        assert result.funding_data_stale is True
        assert result.oi_data_stale is False

    def test_compute_missing_current_funding(self) -> None:
        symbol = "BTCUSDT"
        current_funding = None
        current_oi = 1000.0
        funding_history = [0.0001 + (i % 5) * 0.00001 for i in range(20)]
        oi_history = [900.0 + i * 5.0 for i in range(20)]
        current_ts = 1700000000000
        latest_funding_ts = 1699999900000
        latest_oi_ts = 1699999900000

        result = FundingOIWindowCalculator.compute_funding_oi_metrics(
            symbol=symbol,
            current_funding_rate=current_funding,
            current_oi=current_oi,
            funding_history=funding_history,
            oi_history=oi_history,
            funding_window=20,
            oi_window=20,
            funding_min_periods=10,
            oi_min_periods=10,
            latest_funding_ts_ms=latest_funding_ts,
            latest_oi_ts_ms=latest_oi_ts,
            current_ts_ms=current_ts,
            max_data_age_seconds=86400,
        )

        assert result.current_funding_rate is None
        assert result.funding_rate_z_score is None
        assert result.funding_current_missing is True
        assert result.open_interest_change_rate is not None
        assert result.oi_current_missing is False

    def test_compute_missing_current_oi(self) -> None:
        symbol = "BTCUSDT"
        current_funding = 0.00015
        current_oi = None
        funding_history = [0.0001 + (i % 5) * 0.00001 for i in range(20)]
        oi_history = [900.0 + i * 5.0 for i in range(20)]
        current_ts = 1700000000000
        latest_funding_ts = 1699999900000
        latest_oi_ts = 1699999900000

        result = FundingOIWindowCalculator.compute_funding_oi_metrics(
            symbol=symbol,
            current_funding_rate=current_funding,
            current_oi=current_oi,
            funding_history=funding_history,
            oi_history=oi_history,
            funding_window=20,
            oi_window=20,
            funding_min_periods=10,
            oi_min_periods=10,
            latest_funding_ts_ms=latest_funding_ts,
            latest_oi_ts_ms=latest_oi_ts,
            current_ts_ms=current_ts,
            max_data_age_seconds=86400,
        )

        assert result.current_open_interest is None
        assert result.open_interest_change_rate is None
        assert result.oi_current_missing is True
        assert result.funding_rate_z_score is not None
        assert result.funding_current_missing is False

    def test_any_funding_missing_property(self) -> None:
        symbol = "BTCUSDT"
        result = FundingOIWindowCalculator.compute_funding_oi_metrics(
            symbol=symbol,
            current_funding_rate=None,
            current_oi=1000.0,
            funding_history=[0.0001] * 20,
            oi_history=[1000.0] * 20,
            funding_window=20,
            oi_window=20,
            funding_min_periods=10,
            oi_min_periods=10,
            latest_funding_ts_ms=0,
            latest_oi_ts_ms=0,
            current_ts_ms=1700000000000,
            max_data_age_seconds=86400,
        )

        assert result.any_funding_missing is True
        assert result.any_oi_missing is False

    def test_any_oi_missing_property(self) -> None:
        symbol = "BTCUSDT"
        result = FundingOIWindowCalculator.compute_funding_oi_metrics(
            symbol=symbol,
            current_funding_rate=0.00015,
            current_oi=None,
            funding_history=[0.0001] * 20,
            oi_history=[1000.0] * 20,
            funding_window=20,
            oi_window=20,
            funding_min_periods=10,
            oi_min_periods=10,
            latest_funding_ts_ms=0,
            latest_oi_ts_ms=0,
            current_ts_ms=1700000000000,
            max_data_age_seconds=86400,
        )

        assert result.any_funding_missing is False
        assert result.any_oi_missing is True

    def test_combined_missing_flags(self) -> None:
        symbol = "BTCUSDT"
        result = FundingOIWindowCalculator.compute_funding_oi_metrics(
            symbol=symbol,
            current_funding_rate=None,
            current_oi=None,
            funding_history=[0.0001] * 5,
            oi_history=[1000.0] * 5,
            funding_window=20,
            oi_window=20,
            funding_min_periods=10,
            oi_min_periods=10,
            latest_funding_ts_ms=0,
            latest_oi_ts_ms=0,
            current_ts_ms=1700000000000,
            max_data_age_seconds=86400,
        )

        assert result.any_funding_missing is True
        assert result.any_oi_missing is True
        assert result.funding_current_missing is True
        assert result.oi_current_missing is True
        assert result.funding_window_insufficient is True
        assert result.oi_window_insufficient is True
