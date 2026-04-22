import pytest
import numpy as np
from datetime import datetime, timezone
from decimal import Decimal
from unittest.mock import AsyncMock, MagicMock, patch

from trader.services.backtesting.vectorbt_adapter import VectorBTAdapter, VectorBTConfig
from trader.services.backtesting.ports import BacktestConfig, FrameworkType


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