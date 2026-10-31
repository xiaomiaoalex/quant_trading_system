"""The actual WS parser and REST facts must identify the same economic trade."""

from decimal import Decimal

import pytest

from trader.adapters.binance.private_stream import BinanceCredentials, PrivateStreamManager
from trader.adapters.binance.stream_base import StreamEvent
from trader.services.trading_ingress import ExecutionIngress
from trader.tests.test_trading_service import bootstrap, command, make_service
from trader.trading.models import Execution


@pytest.mark.asyncio
async def test_actual_ws_parser_preserves_precision_fee_asset_and_rest_trade_identity():
    service = make_service()
    await bootstrap(service)
    receipt = await service.place(command())
    payload = {
        "c": receipt.cl_ord_id,
        "s": "BTCUSDT",
        "i": "b-1",
        "x": "TRADE",
        "S": "BUY",
        "t": 42,
        "I": 999,
        "T": 1_000_010,
        "l": "0.123456789123456789",
        "L": "100.000000000000001",
        "n": "0.000000000000001",
        "N": "BTC",
    }
    manager = PrivateStreamManager(BinanceCredentials("fake", "fake"))
    parsed = manager._parse_fill_update(payload, 1_000_099)
    assert await ExecutionIngress(service, account_id="acct").on_fill(parsed)
    fact = Execution(
        "acct",
        receipt.cl_ord_id,
        "BTCUSDT",
        "42",
        Decimal(payload["l"]),
        Decimal(payload["L"]),
        Decimal(payload["n"]),
        "BTC",
        1_000_010,
    )
    assert not await service.record_execution(fact)
    state = await service.store.read("acct")
    assert state.balances["BTC"] == Decimal(payload["l"]) - Decimal(payload["n"])
    assert state.positions["BTCUSDT"] == state.balances["BTC"]
    assert len(state.executions) == 1


@pytest.mark.asyncio
async def test_private_disconnect_notifies_barrier_before_closing_socket():
    manager = PrivateStreamManager(BinanceCredentials("fake", "fake"))
    calls = []

    async def block(event, data):
        calls.append("blocked")

    class Socket:
        async def close(self):
            assert calls == ["blocked"]
            calls.append("closed")

    manager.register_handler(StreamEvent.DISCONNECTED, block)
    manager._ws = Socket()
    await manager._disconnect()
    assert calls == ["blocked", "closed"]
