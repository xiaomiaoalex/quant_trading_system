"""Private-stream assembly against the real trading service, without network."""

from decimal import Decimal
from types import SimpleNamespace

import pytest

from trader.adapters.binance.stream_base import StreamEvent, StreamState
from trader.tests.test_trading_service import Clock, bootstrap, command, make_service
from trader.trading.models import Execution, OrderObservation, ReconcileSnapshot
from trader.trading.private_session import PrivateTradingSession
from trader.trading.recovery import RecoveryCoverage


class FakeStream:
    def __init__(self):
        self.fills, self.orders, self.events = [], [], {}
        self.state = StreamState.IDLE

    def register_fill_handler(self, handler):
        self.fills.append(handler)

    def register_order_handler(self, handler):
        self.orders.append(handler)

    def register_handler(self, event, handler):
        self.events.setdefault(event, []).append(handler)

    async def start(self):
        self.state = StreamState.CONNECTED

    async def stop(self):
        self.state = StreamState.DISCONNECTED

    async def emit(self, handlers, value):
        for handler in handlers:
            await handler(value)

    async def disconnect(self):
        self.state = StreamState.DISCONNECTED
        for handler in self.events.get(StreamEvent.DISCONNECTED, []):
            await handler(StreamEvent.DISCONNECTED, None)


async def setup_session(*, covered=True, complete=True, buffered=False):
    service = make_service(clock=Clock(1_000_100))
    await bootstrap(service)
    receipt = await service.place(command())
    streams = []

    def factory():
        stream = FakeStream()
        streams.append(stream)
        return stream

    raw = SimpleNamespace(
        cl_ord_id=receipt.cl_ord_id,
        exec_id="1",
        trade_id=1,
        symbol="BTCUSDT",
        qty=Decimal("0.5"),
        price=Decimal("100"),
        commission=Decimal("0.05"),
        commission_asset="USDT",
        exchange_ts_ms=1_000_100,
    )
    calls = 0

    async def snapshot(account_id):
        nonlocal calls
        calls += 1
        service.clock.now = 1_000_100 + calls
        if buffered and calls == 1:
            # Cumulative order update arrives before either repeated true fill.
            await streams[-1].emit(
                streams[-1].orders,
                SimpleNamespace(
                    cl_ord_id=receipt.cl_ord_id,
                    broker_order_id="b-1",
                    status="PARTIALLY_FILLED",
                    filled_qty=Decimal("0.5"),
                    exchange_ts_ms=1_000_100,
                ),
            )
            await streams[-1].emit(streams[-1].fills, raw)
            await streams[-1].emit(streams[-1].fills, raw)
        return ReconcileSnapshot(
            "acct",
            1_000_100 + calls,
            calls + 1,
            (
                {"USDT": Decimal("949.95"), "BTC": Decimal("0.5")}
                if buffered
                else {"USDT": Decimal("1000"), "BTC": Decimal("0")}
            ),
            open_orders=[
                OrderObservation(
                    "acct",
                    receipt.cl_ord_id,
                    "PARTIALLY_FILLED" if buffered else "OPEN",
                    Decimal("0.5") if buffered else Decimal("0"),
                    "b-1",
                    1_000_100,
                )
            ],
            executions=(
                [
                    Execution(
                        "acct",
                        receipt.cl_ord_id,
                        "BTCUSDT",
                        "1",
                        Decimal("0.5"),
                        Decimal("100"),
                        Decimal("0.05"),
                        "USDT",
                        1_000_100,
                    )
                ]
                if buffered
                else []
            ),
            marks={"BTCUSDT": {"price": Decimal("100"), "at_ms": 1_000_100}},
            complete=complete,
        )

    async def coverage(generation, first, last, connected_from_ms, buffered_through_ms):
        return (
            RecoveryCoverage(
                generation,
                "fake_exchange_cursor",
                1,
                2_000_000,
                2_000_000,
                True,
            )
            if covered
            else None
        )

    async def block(reason):
        async with service.store.transaction("acct") as state:
            state.reconciliation["stream_healthy"] = False
            service._refresh_status(state)

    async def ready():
        async with service.store.transaction("acct") as state:
            state.reconciliation["stream_healthy"] = True
            service._refresh_status(state)

    return (
        PrivateTradingSession(
            service=service,
            account_id="acct",
            stream_factory=factory,
            snapshot_provider=snapshot,
            coverage_provider=coverage,
            exchange_clock=lambda: 1_000_100,
            on_block=block,
            on_ready=ready,
        ),
        service,
        streams,
        raw,
    )


def test_coverage_requires_an_exchange_clock_not_local_receive_time():
    service = SimpleNamespace(recover=None, record_execution=None, observe=None)
    with pytest.raises(ValueError, match="EXCHANGE_CLOCK_REQUIRED_FOR_COVERAGE"):
        PrivateTradingSession(
            service=service,
            account_id="acct",
            stream_factory=FakeStream,
            snapshot_provider=lambda account_id: None,
            coverage_provider=lambda *args: None,
            on_block=lambda reason: None,
            on_ready=lambda: None,
        )


@pytest.mark.asyncio
async def test_rest_and_duplicate_buffered_ws_facts_book_once_then_disconnect_blocks():
    session, service, streams, raw = await setup_session(buffered=True)
    assert await session.start()
    state = await service.store.read("acct")
    assert state.balances["USDT"] == Decimal("949.95")
    assert state.balances["BTC"] == Decimal("0.5")
    assert len(state.executions) == 1
    assert len([entry for entry in state.ledger if entry["kind"] in {"trade", "fee"}]) == 3
    await streams[0].disconnect()
    assert not session.ready
    assert not (await service.risk_view("acct")).ready
    await streams[0].emit(streams[0].fills, raw)
    assert len((await service.store.read("acct")).executions) == 1


@pytest.mark.asyncio
@pytest.mark.parametrize("covered,complete", [(False, True), (True, False)])
async def test_missing_coverage_or_paginated_snapshot_gap_keeps_account_blocked(covered, complete):
    session, service, streams, raw = await setup_session(covered=covered, complete=complete)
    assert not await session.start()
    assert not session.ready
    assert not (await service.risk_view("acct")).ready


@pytest.mark.asyncio
async def test_reconnect_creates_new_stream_and_late_old_connection_events_are_ignored():
    session, service, streams, raw = await setup_session()
    assert await session.start()
    assert await session.reconnect()
    assert len(streams) == 2
    await streams[0].emit(streams[0].fills, raw)
    assert (await service.store.read("acct")).executions == {}
    await streams[1].emit(streams[1].fills, raw)
    assert len((await service.store.read("acct")).executions) == 1


@pytest.mark.asyncio
async def test_periodic_alignment_keeps_the_same_continuous_connection():
    session, service, streams, raw = await setup_session()
    assert await session.start()
    assert await session.align()
    assert len(streams) == 1


@pytest.mark.asyncio
async def test_unusable_fill_cannot_be_silently_dropped_while_remaining_ready():
    session, service, streams, raw = await setup_session()
    assert await session.start()
    await streams[0].emit(streams[0].fills, SimpleNamespace(cl_ord_id=None, trade_id=None))
    assert not session.ready
    assert not (await service.risk_view("acct")).ready
