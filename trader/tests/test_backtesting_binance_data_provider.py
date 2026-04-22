import pytest
from datetime import datetime, timezone
from unittest.mock import AsyncMock, MagicMock, patch
from trader.services.backtesting.binance_data_provider import BinanceDataProvider
from trader.services.backtesting.ports import OHLCV


@pytest.fixture
def provider():
    return BinanceDataProvider()


@pytest.mark.asyncio
async def test_get_klines_returns_ohlcv_list(provider):
    """Verify get_klines returns List[OHLCV] in ascending timestamp order"""
    raw_klines = [
        [1609459200000, "100.0", "101.0", "99.0", "100.5", "1000"],
        [1609459260000, "100.5", "102.0", "100.0", "101.5", "1200"],
    ]

    mock_response = MagicMock()
    mock_response.status = 200
    mock_response.json = AsyncMock(return_value=raw_klines)
    mock_response.__aenter__ = AsyncMock(return_value=mock_response)
    mock_response.__aexit__ = AsyncMock(return_value=None)

    mock_context = MagicMock()
    mock_context.__aenter__ = AsyncMock(return_value=mock_response)
    mock_context.__aexit__ = AsyncMock(return_value=None)

    mock_session = MagicMock()
    mock_session.get = MagicMock(return_value=mock_context)
    mock_session.closed = False

    provider._session = mock_session

    result = await provider.get_klines(
        symbol="BTCUSDT",
        interval="1m",
        start_date=datetime(2021, 1, 1, tzinfo=timezone.utc),
        end_date=datetime(2021, 1, 1, 0, 10, tzinfo=timezone.utc),
    )

    assert len(result) == 2
    assert all(isinstance(o, OHLCV) for o in result)
    assert result[0].timestamp < result[1].timestamp


@pytest.mark.asyncio
async def test_get_symbols_returns_list(provider):
    symbols = await provider.get_symbols()
    assert isinstance(symbols, list)
    assert "BTCUSDT" in symbols