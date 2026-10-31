"""REST coverage proof: any uncertainty must fail closed, never guess."""

from dataclasses import replace
from datetime import datetime, timezone
from decimal import Decimal

import pytest

from trader.adapters.broker.spot_coverage import BinanceSpotCoverageProver
from trader.core.application.ports import BrokerOrder
from trader.core.domain.models.order import OrderSide, OrderType
from trader.trading.models import Execution, ReconcileSnapshot

CONNECTED_FROM = 1_000_000
COVERED_THROUGH = 2_000_000
START = CONNECTED_FROM - 1_000


def order(
    order_id: str = "1",
    cl_ord_id: str = "qts-1",
    symbol: str = "BTCUSDT",
) -> BrokerOrder:
    return BrokerOrder(
        broker_order_id=order_id,
        cl_ord_id=cl_ord_id,
        symbol=symbol,
        side=OrderSide.BUY,
        order_type=OrderType.MARKET,
        qty=Decimal("1"),
        filled_qty=Decimal("1"),
        average_price=Decimal("100"),
        status="FILLED",
        created_at=datetime.fromtimestamp(1_000_000 / 1000, tz=timezone.utc),
    )


def trade(
    trade_id: int = 1,
    order_id: str = "1",
    *,
    symbol: str = "BTCUSDT",
    at_ms: int = 1_000_000,
) -> dict:
    return {
        "id": trade_id,
        "orderId": order_id,
        "symbol": symbol,
        "qty": "1",
        "price": "100",
        "commission": "0.1",
        "commissionAsset": "USDT",
        "time": at_ms,
    }


class Rest:
    def __init__(
        self,
        *,
        orders: dict[str, list[BrokerOrder]] | None = None,
        trades: dict[str, list[dict]] | None = None,
        orders_complete: bool = True,
        trades_complete: bool = True,
        raise_on_trades: bool = False,
    ):
        self.orders = orders or {"BTCUSDT": [order()]}
        self.trades = trades or {"BTCUSDT": [trade()]}
        self.orders_complete = orders_complete
        self.trades_complete = trades_complete
        self.raise_on_trades = raise_on_trades
        self.calls: list[tuple[str, str]] = []

    async def fetch_orders(self, symbol: str, start_time_ms: int):
        self.calls.append(("orders", symbol))
        assert start_time_ms == START
        return list(self.orders.get(symbol, [])), self.orders_complete

    async def fetch_trades(self, symbol: str, start_time_ms: int):
        self.calls.append(("trades", symbol))
        assert start_time_ms == START
        if self.raise_on_trades:
            raise ConnectionError("venue unavailable")
        return list(self.trades.get(symbol, [])), self.trades_complete


class GrowingRest(Rest):
    """Order history is still being applied on the venue during the second pass."""

    def __init__(self, **kwargs):
        super().__init__(**kwargs)
        self.passes = 0

    async def fetch_trades(self, symbol: str, start_time_ms: int):
        page, complete = await super().fetch_trades(symbol, start_time_ms)
        self.passes += 1
        if self.passes > 1:
            page = page + [trade(2, at_ms=1_500_000)]
        return page, complete


def snapshot(
    *,
    account_id: str = "acct",
    checked_at_ms: int = 1_000_100,
    complete: bool = True,
    executions: list[Execution] | None = None,
) -> ReconcileSnapshot:
    return ReconcileSnapshot(
        account_id,
        checked_at_ms,
        1,
        {"USDT": Decimal("1000")},
        executions=list(executions or []),
        complete=complete,
    )


def execution(exec_id: str = "1", symbol: str = "BTCUSDT") -> Execution:
    return Execution(
        "acct",
        "qts-1",
        symbol,
        exec_id,
        Decimal("1"),
        Decimal("100"),
        Decimal("0.1"),
        "USDT",
        1_000_000,
    )


async def prove(rest, *, clock=COVERED_THROUGH, lookback_ms=1_000, settle_ms=0, **snapshots):
    prover = BinanceSpotCoverageProver(
        rest=rest,
        symbols=["BTCUSDT"],
        exchange_clock=lambda: clock,
        lookback_ms=lookback_ms,
        settle_ms=settle_ms,
    )
    first = snapshots.pop("first", None) or snapshot()
    last = snapshots.pop("last", None) or snapshot()
    return await prover(
        generation=7,
        first=first,
        last=last,
        connected_from_ms=CONNECTED_FROM,
        buffered_through_ms=snapshots.pop("buffered_through_ms", CONNECTED_FROM),
    )


@pytest.mark.asyncio
async def test_complete_two_pass_backfill_proves_coverage():
    rest = Rest()
    proof = await prove(rest)
    assert proof is not None
    assert proof.stream_generation == 7
    assert proof.source == "binance_spot_rest_backfill"
    assert proof.connected_from == CONNECTED_FROM
    assert proof.covered_through == COVERED_THROUGH
    assert proof.buffered_through == CONNECTED_FROM
    assert proof.contiguous
    # Two complete passes over the whole lookback window.
    assert rest.calls == [
        ("orders", "BTCUSDT"),
        ("trades", "BTCUSDT"),
        ("orders", "BTCUSDT"),
        ("trades", "BTCUSDT"),
    ]


@pytest.mark.asyncio
async def test_async_exchange_clock_is_supported():
    async def clock():
        return COVERED_THROUGH

    prover = BinanceSpotCoverageProver(
        rest=Rest(), symbols=["BTCUSDT"], exchange_clock=clock, lookback_ms=1_000, settle_ms=0
    )
    proof = await prover(1, snapshot(), snapshot(), CONNECTED_FROM, CONNECTED_FROM)
    assert proof is not None and proof.covered_through == COVERED_THROUGH


@pytest.mark.asyncio
async def test_order_pagination_truncation_is_unproven():
    assert await prove(Rest(orders_complete=False)) is None


@pytest.mark.asyncio
async def test_trade_pagination_truncation_is_unproven():
    assert await prove(Rest(trades_complete=False)) is None


@pytest.mark.asyncio
async def test_rest_failure_is_unproven_and_not_raised():
    assert await prove(Rest(raise_on_trades=True)) is None


@pytest.mark.asyncio
async def test_trade_without_our_order_is_unproven():
    assert await prove(Rest(trades={"BTCUSDT": [trade(order_id="external")]})) is None


@pytest.mark.asyncio
async def test_trade_for_foreign_cl_ord_id_is_unproven():
    rest = Rest(
        orders={"BTCUSDT": [order(cl_ord_id="manual-order")]},
        trades={"BTCUSDT": [trade()]},
    )
    assert await prove(rest) is None


@pytest.mark.asyncio
async def test_trade_outside_the_proven_window_is_unproven():
    assert await prove(Rest(trades={"BTCUSDT": [trade(at_ms=START - 1)]})) is None


@pytest.mark.asyncio
async def test_trade_timestamp_regression_is_unproven():
    rest = Rest(
        trades={
            "BTCUSDT": [
                trade(1, at_ms=1_500_000),
                trade(2, at_ms=1_200_000),
            ]
        }
    )
    assert await prove(rest) is None


@pytest.mark.asyncio
async def test_snapshot_execution_outside_the_proven_window_is_unproven():
    outside = snapshot(executions=[execution(exec_id="1")])
    outside.executions[0] = replace(outside.executions[0], occurred_at_ms=START - 1)
    assert await prove(Rest(), first=outside, last=outside) is None


@pytest.mark.asyncio
async def test_duplicate_trade_identity_is_unproven():
    rest = Rest(trades={"BTCUSDT": [trade(), trade()]})
    assert await prove(rest) is None


@pytest.mark.asyncio
async def test_duplicate_order_identity_is_unproven():
    rest = Rest(orders={"BTCUSDT": [order(), order()]})
    assert await prove(rest) is None


@pytest.mark.asyncio
async def test_symbol_mismatch_in_order_history_is_unproven():
    rest = Rest(orders={"BTCUSDT": [order(symbol="ETHUSDT")]})
    assert await prove(rest) is None


@pytest.mark.asyncio
async def test_symbol_mismatch_in_trade_history_is_unproven():
    rest = Rest(trades={"BTCUSDT": [trade(symbol="ETHUSDT")]})
    assert await prove(rest) is None


@pytest.mark.asyncio
async def test_backfill_that_changes_between_passes_is_unproven():
    assert await prove(GrowingRest(), settle_ms=0) is None


@pytest.mark.asyncio
async def test_buffered_fact_after_the_proven_window_is_unproven():
    assert await prove(Rest(), buffered_through_ms=COVERED_THROUGH + 1) is None


@pytest.mark.asyncio
async def test_snapshot_execution_missing_from_rest_backfill_is_unproven():
    with_execution = snapshot(executions=[execution(exec_id="1")])
    assert await prove(Rest(), first=with_execution, last=with_execution) is not None
    missing = snapshot(executions=[execution(exec_id="999")])
    assert await prove(Rest(), first=missing, last=missing) is None


@pytest.mark.asyncio
async def test_incomplete_snapshot_needs_no_rest_and_is_unproven():
    rest = Rest()
    assert await prove(rest, first=snapshot(complete=False)) is None
    assert rest.calls == []


@pytest.mark.asyncio
async def test_unknown_connection_boundary_is_unproven_without_rest():
    rest = Rest()
    prover = BinanceSpotCoverageProver(
        rest=rest, symbols=["BTCUSDT"], exchange_clock=lambda: COVERED_THROUGH, settle_ms=0
    )
    assert await prover(1, snapshot(), snapshot(), 0, 0) is None
    assert rest.calls == []


@pytest.mark.asyncio
async def test_snapshot_older_than_the_connection_boundary_is_unproven():
    old = snapshot(checked_at_ms=CONNECTED_FROM - 1)
    assert await prove(Rest(), first=old, last=old) is None


@pytest.mark.asyncio
async def test_account_mismatch_is_unproven():
    assert await prove(Rest(), last=snapshot(account_id="other")) is None


@pytest.mark.asyncio
async def test_prover_requires_explicit_symbols():
    with pytest.raises(ValueError):
        BinanceSpotCoverageProver(rest=Rest(), symbols=[], exchange_clock=lambda: COVERED_THROUGH)
