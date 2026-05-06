import sys
from datetime import datetime, timezone
from decimal import Decimal
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch

import numpy as np
import pytest

from trader.services.backtesting.ports import OHLCV, BacktestConfig, FrameworkType
from trader.services.backtesting.vectorbt_adapter import VectorBTAdapter, VectorBTConfig


@pytest.fixture
def adapter():
    return VectorBTAdapter()


@pytest.fixture
def sample_config():
    return BacktestConfig(
        start_date=datetime(2021, 1, 1, tzinfo=timezone.utc),
        end_date=datetime(2021, 1, 31, tzinfo=timezone.utc),
        initial_capital=Decimal("10000"),
        symbol="BTCUSDT",
        interval="1h",
        commission_rate=Decimal("0.001"),
        slippage_rate=Decimal("0.0005"),
    )


def test_framework_type_is_vectorbt(adapter):
    assert adapter.framework_type == FrameworkType.VECTORBT


def test_supported_features(adapter):
    features = adapter.get_supported_features()
    feature_values = [f.value for f in features]
    assert "PARAMETER_OPTIMIZATION" in feature_values


def test_vectorbt_config_defaults():
    config = VectorBTConfig()
    assert config.freq == "1h"
    assert config.direction_aware_slippage is True
    assert config.include_commission is True


@pytest.mark.asyncio
async def test_vectorbt_adapter_uses_injected_data_provider(monkeypatch, sample_config):
    class FakeDataProvider:
        def __init__(self) -> None:
            self.calls = []

        async def get_klines(self, symbol, interval, start_date, end_date):
            self.calls.append((symbol, interval, start_date, end_date))
            return [
                OHLCV(
                    sample_config.start_date,
                    Decimal("1"),
                    Decimal("1"),
                    Decimal("1"),
                    Decimal("1"),
                    Decimal("10"),
                ),
                OHLCV(
                    sample_config.end_date,
                    Decimal("2"),
                    Decimal("2"),
                    Decimal("2"),
                    Decimal("2"),
                    Decimal("20"),
                ),
            ]

    class FakeTrades:
        def count(self):
            return 0

        def __iter__(self):
            return iter(())

    class FakePortfolio:
        trades = FakeTrades()

        def total_return(self):
            return 0.1

        def sharpe_ratio(self, risk_free=1.0):
            return 1.2

        def max_drawdown(self):
            return -0.05

        def win_rate(self):
            return 0.5

        def profit_factor(self):
            return 1.3

        def final_capital(self):
            return 11000

        def annualized_return(self):
            return 0.2

        def calmar_ratio(self):
            return 2.0

    class FakePortfolioFactory:
        @staticmethod
        def from_signals(**kwargs):
            return FakePortfolio()

    fake_provider = FakeDataProvider()
    fake_vectorbt = SimpleNamespace(Portfolio=FakePortfolioFactory)
    monkeypatch.setitem(sys.modules, "vectorbt", fake_vectorbt)

    async def strategy(_klines):
        return [True, False]

    result = await VectorBTAdapter(data_provider=fake_provider).run_backtest(
        sample_config,
        strategy,
    )

    assert fake_provider.calls == [
        (
            sample_config.symbol,
            sample_config.interval,
            sample_config.start_date,
            sample_config.end_date,
        )
    ]
    assert result.total_return == Decimal("0.1")
