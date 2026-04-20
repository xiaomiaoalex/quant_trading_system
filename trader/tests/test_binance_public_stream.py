"""
Public Stream Manager Unit Tests
================================
"""
import pytest

from trader.adapters.binance.public_stream import (
    PublicStreamManager,
    PublicStreamConfig,
)


def test_build_stream_url_single_stream():
    manager = PublicStreamManager(
        config=PublicStreamConfig(
            base_url="wss://demo-stream.binance.com/ws",
            streams=["btcusdt@trade"],
        )
    )
    assert (
        manager._build_stream_url()
        == "wss://demo-stream.binance.com/ws/btcusdt@trade"
    )


def test_build_stream_url_multi_stream():
    manager = PublicStreamManager(
        config=PublicStreamConfig(
            base_url="wss://demo-stream.binance.com/ws",
            streams=["btcusdt@trade", "btcusdt@kline_1m"],
        )
    )
    assert (
        manager._build_stream_url()
        == "wss://demo-stream.binance.com/stream?streams=btcusdt@trade/btcusdt@kline_1m"
    )


@pytest.mark.asyncio
async def test_handle_combined_stream_message():
    manager = PublicStreamManager(
        config=PublicStreamConfig(
            base_url="wss://demo-stream.binance.com/ws",
            streams=["btcusdt@trade", "btcusdt@kline_1m"],
        )
    )
    captured = []

    def handler(event):
        captured.append(event)

    manager.register_market_handler(handler)

    message = (
        '{"stream":"btcusdt@trade","data":{"e":"trade","E":1700000000000,'
        '"s":"BTCUSDT","p":"65000.1","q":"0.002"}}'
    )
    await manager._handle_message(message)

    assert len(captured) == 1
    event = captured[0]
    assert event.stream == "btcusdt@trade"
    assert event.event_type == "trade"
    assert event.data["s"] == "BTCUSDT"
