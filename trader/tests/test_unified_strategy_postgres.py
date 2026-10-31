"""Strategy command ownership survives a runtime restart in real PostgreSQL."""

from decimal import Decimal

import pytest

from trader.tests.test_trading_postgres import pg_store
from trader.tests.test_trading_runtime import runtime, snapshot
from trader.tests.test_trading_service import Clock
from trader.tests.test_unified_strategy_runner import Plugin, market, runner
from trader.trading.models import OrderObservation


@pytest.mark.asyncio
async def test_strategy_accepted_event_survives_runtime_restart_without_resending():
    async with pg_store() as (store, _pool):
        clock = Clock(1_000_200)
        first = runtime(store, clock=clock, snap=snapshot())
        assert await first.start()
        try:
            receipt = await runner(first).on_market_data(market(), event_id="durable-event")
            assert len(first.exchange.placed) == 1
        finally:
            await first.stop()

        recovered = runtime(
            store,
            clock=clock,
            owner="new-runtime",
            snap=snapshot(
                generation=2,
                checked=1_000_100,
                open_orders=[
                    OrderObservation(
                        "acct", receipt.cl_ord_id, "OPEN", Decimal("0"), "b-1", 1_000_100
                    )
                ],
            ),
        )
        assert await recovered.start()
        try:
            plugin = Plugin()
            duplicate = await runner(recovered, plugin).on_market_data(
                market(), event_id="durable-event"
            )
            assert duplicate.command_id == receipt.command_id
            assert duplicate.cl_ord_id == receipt.cl_ord_id
            assert recovered.exchange.placed == []
            assert plugin.calls == 0
            state = await store.read("acct")
            assert len(state.orders) == len(state.commands) == 1
        finally:
            await recovered.stop()
