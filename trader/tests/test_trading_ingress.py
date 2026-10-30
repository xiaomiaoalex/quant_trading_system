"""Private-stream ingress tests: real fills only, idempotent, fail closed."""

from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal

import pytest

from trader.services.trading_ingress import ExecutionIngress
from trader.tests.test_trading_service import Clock, FakeExchange, bootstrap, command, make_service


@dataclass
class Fill:
    cl_ord_id: str
    trade_id: int
    qty: float
    price: float
    commission: float
    exchange_ts_ms: int
    symbol: str = "BTCUSDT"
    exec_id: str | None = None
    commission_asset: str = ""


@dataclass
class OrderUpdate:
    cl_ord_id: str
    status: str
    filled_qty: float
    exchange_ts_ms: int
    broker_order_id: str = "b-1"
    source: str = "WS"


async def setup():
    service = make_service(FakeExchange(), clock=Clock())
    await bootstrap(service)
    receipt = await service.place(command(qty=Decimal("2"), price=Decimal("100")))
    return service, ExecutionIngress(service, account_id="acct"), receipt


@pytest.mark.asyncio
async def test_fill_books_once_even_when_private_stream_replays_it():
    service, ingress, receipt = await setup()
    fill = Fill(receipt.cl_ord_id, 10, 1.0, 100.0, 0.1, 1_000_100, exec_id="e-10")

    assert await ingress.on_fill(fill) is True
    assert await ingress.on_fill(fill) is False
    assert (
        await ingress.on_fill(
            Fill(receipt.cl_ord_id, 10, 1.0, 100.0, 0.1, 1_000_100, exec_id="e-10")
        )
        is False
    )

    state = await service.store.read("acct")
    assert state.orders[receipt.cl_ord_id].booked_qty == Decimal("1")
    assert state.balances["USDT"] == Decimal("899.9")
    assert state.balances["BTC"] == Decimal("1")


@pytest.mark.asyncio
async def test_order_update_before_execution_is_reconciled_after_the_fill_arrives():
    service, ingress, receipt = await setup()
    await ingress.on_order_update(OrderUpdate(receipt.cl_ord_id, "FILLED", 2.0, 1_000_100))
    assert "FILL_OBSERVATION_MISMATCH" in (await service.risk_view("acct")).reasons

    for index, exec_id in enumerate(("e-1", "e-2"), start=1):
        await ingress.on_fill(
            Fill(receipt.cl_ord_id, index, 1.0, 100.0, 0.1, 1_000_100 + index, exec_id=exec_id)
        )
    view = await service.risk_view("acct")
    assert view.ready is True
    assert view.reasons == ()


@pytest.mark.asyncio
async def test_unknown_client_order_id_blocks_new_risk_instead_of_creating_an_order():
    service, ingress, _ = await setup()
    accepted = await ingress.on_fill(
        Fill("external-manual-order", 1, 1.0, 100.0, 0.0, 1_000_100, exec_id="external-1")
    )
    assert accepted is False
    view = await service.risk_view("acct")
    assert view.ready is False
    assert "UNKNOWN_EXECUTION" in view.reasons
    state = await service.store.read("acct")
    assert "external-manual-order" not in state.orders


@pytest.mark.asyncio
async def test_fee_without_commission_asset_falls_back_to_quote_asset():
    service, ingress, receipt = await setup()
    await ingress.on_fill(Fill(receipt.cl_ord_id, 1, 1.0, 100.0, 0.1, 1_000_100, exec_id="e-1"))
    state = await service.store.read("acct")
    assert state.balances["USDT"] == Decimal("899.9")
    assert state.ledger[2]["asset"] == "USDT"


@pytest.mark.asyncio
async def test_malformed_fill_without_ids_is_dropped_without_side_effects():
    service, ingress, receipt = await setup()
    assert await ingress.on_fill(Fill("", 0, 1.0, 100.0, 0.0, 1_000_100)) is False
    state = await service.store.read("acct")
    assert state.executions == {}
    assert state.ledger == []
    assert state.balances["USDT"] == Decimal("1000")
