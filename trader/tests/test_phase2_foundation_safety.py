"""Phase 2 acceptance tests: explicit order specs, books, holds and attribution.

Every test uses the public service / API / runtime surface with deterministic
fakes and the in-memory store. Real PostgreSQL coverage lives in
``test_phase2_postgres.py``. No network, no real account.
"""

from __future__ import annotations

import asyncio
from decimal import Decimal

import httpx
import pytest

from trader.api.auth import AuthSettings
from trader.api.trading_app import create_trading_app
from trader.core.application.strategy_protocol import MarketData, MarketDataType
from trader.core.domain.models.order import OrderType
from trader.core.domain.models.signal import Signal, SignalType
from trader.tests.test_trading_runtime import runtime, snapshot, start_runtime
from trader.tests.test_trading_service import (
    TEST_BOOK,
    Clock,
    FakeExchange,
    command,
    fund_book,
    make_service,
)
from trader.trading import books as book_ledger
from trader.trading import core as trading_core
from trader.trading.models import (
    AccountState,
    BookLedger,
    CommandRecord,
    Conflict,
    ExchangeOrderResult,
    ExchangeOutcome,
    Execution,
    Hold,
    NotReady,
    OrderAggregate,
    OrderObservation,
    PlaceOrderCommand,
    ReconcileSnapshot,
    TradingError,
    advance_order_status,
    json_value,
)
from trader.trading.order_spec import (
    PRICE_SOURCE_MARKET_TICKER,
    PRICE_SOURCE_SIGNAL_LIMIT,
    build_order_spec,
)
from trader.trading.risk import RiskPolicy, RiskRejected, SpotRiskEngine, risk_policy_from_env
from trader.trading.service import TradingService
from trader.trading.store import MemoryTradingStore, decode_state

ACCOUNT_ID = "acct"
BOOK_A = "book:a"
BOOK_B = "book:b"


def venue_snapshot(
    balances=None,
    *,
    checked=1_000_000,
    generation=1,
    marks=None,
    open_orders=(),
):
    return ReconcileSnapshot(
        ACCOUNT_ID,
        checked,
        generation,
        balances if balances is not None else {"USDT": Decimal("1000"), "BTC": Decimal("0")},
        open_orders=list(open_orders),
        marks=marks or {"BTCUSDT": {"price": Decimal("100"), "at_ms": checked}},
    )


def service(store=None, exchange=None, clock=None, policy=None, defer_send=False):
    return TradingService(
        store=store or MemoryTradingStore(),
        exchange=exchange or FakeExchange(),
        policy=policy or RiskPolicy(fee_rate=Decimal("0.001")),
        clock=clock or Clock(),
        defer_send=defer_send,
    )


async def recovered(*, balances=None, store=None, exchange=None, policy=None, clock=None):
    current = service(store, exchange, clock, policy)
    await current.recover(venue_snapshot(balances))
    return current


async def allocate(current, book_id, amount, asset="USDT", command_id=None):
    return await current.allocate(
        command_id or f"alloc:{book_id}:{asset}:{amount}",
        ACCOUNT_ID,
        book_id,
        asset,
        Decimal(str(amount)),
        actor="owner",
        reason="phase 2 test allocation",
    )


async def seed_state(store, state):
    async with store.transaction(ACCOUNT_ID) as target:
        for name in (
            "status",
            "version",
            "balances",
            "positions",
            "external_locked",
            "orders",
            "commands",
            "holds",
            "books",
            "executions",
            "ledger",
            "events",
            "outbox",
            "control",
            "reconciliation",
        ):
            setattr(target, name, getattr(state, name))


def single_book_state(*, balances, order, hold=None, ledger=None, reconciliation=None):
    state = AccountState(account_id=ACCOUNT_ID, status="READY", balances=dict(balances))
    state.orders[order.cl_ord_id] = order
    if hold is not None:
        state.holds[order.cl_ord_id] = hold
    state.ledger = list(ledger or [])
    state.reconciliation = dict(
        reconciliation
        or {
            "checked_at_ms": 1_000_000,
            "generation": 1,
            "stream_healthy": True,
            "marks": {"BTCUSDT": {"price": Decimal("100"), "at_ms": 1_000_000}},
        }
    )
    return state


def legacy_order(*, status="OPEN", booked_qty="0", book_id=""):
    return OrderAggregate(
        cl_ord_id="qts-legacy",
        command=PlaceOrderCommand(
            command_id="legacy-command",
            account_id=ACCOUNT_ID,
            symbol="BTCUSDT",
            base_asset="BTC",
            quote_asset="USDT",
            side="BUY",
            qty=Decimal("1"),
            price=Decimal("100"),
            deployment_id="legacy",
            order_type="LIMIT",
            decision_id="legacy",
            book_id=book_id,
        ),
        status=status,
        booked_qty=Decimal(booked_qty),
    )


# ---------------------------------------------------------------- 2.1 / 2.2
@pytest.mark.asyncio
async def test_strategy_order_type_comes_from_the_signal_field_not_metadata():
    rt = runtime(snap=snapshot())
    await start_runtime(rt)

    class MissingTypePlugin:
        stateful_indicators = False
        warmup_events = 0

        async def on_market_data(self, data):
            return Signal(
                signal_type=SignalType.BUY,
                symbol=data.symbol,
                price=data.price,
                quantity=Decimal("1"),
                metadata={"order_type": "MARKET"},
            )

    strategy = runner_for(rt, MissingTypePlugin())
    try:
        with pytest.raises(TradingError) as refused:
            await strategy.on_market_data(market_data(), event_id="missing-type")
        assert "ORDER_TYPE_REQUIRED" in str(refused.value)
        assert rt.exchange.placed == []
    finally:
        await rt.stop()


@pytest.mark.asyncio
async def test_limit_and_market_specs_are_explicit_auditable_and_idempotent():
    rt = runtime(snap=snapshot())
    await start_runtime(rt)

    class SpecPlugin:
        stateful_indicators = False
        warmup_events = 0
        order_type = OrderType.LIMIT
        price = Decimal("95")
        metadata = {"order_type": "MARKET", "time_in_force": "IOC"}

        async def on_market_data(self, data):
            return Signal(
                signal_type=SignalType.BUY,
                order_type=self.order_type,
                symbol=data.symbol,
                price=self.price,
                quantity=Decimal("1"),
                metadata=self.metadata,
            )

    plugin = SpecPlugin()
    strategy = runner_for(rt, plugin)
    try:
        receipt = await strategy.on_market_data(market_data(), event_id="limit-1")
        sent = rt.exchange.placed[0][0]
        assert sent.order_type == "LIMIT"
        assert sent.price == Decimal("95")
        assert sent.price_source == "SIGNAL_LIMIT"
        assert sent.price_at_ms == 1_000_000
        assert sent.book_id == TEST_BOOK
        assert sent.time_in_force == "GTC"
        order = await rt.service.order(ACCOUNT_ID, receipt.cl_ord_id)
        assert order["order_type"] == "LIMIT"
        assert order["book_id"] == TEST_BOOK
        assert order["price_source"] == "SIGNAL_LIMIT"
        state = await rt.store.read(ACCOUNT_ID)
        queued = next(event for event in state.events if event["event_type"] == "order.queued")
        assert queued["payload"]["book_id"] == TEST_BOOK
        assert queued["payload"]["price_source"] == "SIGNAL_LIMIT"
        assert queued["payload"]["price_at_ms"] == 1_000_000

        # A retry with the same decision but a changed order type is a
        # conflicting command, not a silent substitution.
        changed = Signal(
            signal_type=SignalType.BUY,
            order_type=OrderType.MARKET,
            symbol="BTCUSDT",
            price=Decimal("100"),
            quantity=Decimal("1"),
        )
        with pytest.raises(Conflict):
            await strategy.submit_signal(
                changed,
                decision_id="limit-1",
                reference_price=Decimal("100"),
                reference_price_source=PRICE_SOURCE_MARKET_TICKER,
                reference_at_ms=1_000_000,
                decision_at_ms=1_000_000,
            )
        assert len(rt.exchange.placed) == 1
    finally:
        await rt.stop()


@pytest.mark.asyncio
async def test_market_reference_price_source_and_time_are_recorded():
    rt = runtime(snap=snapshot())
    await start_runtime(rt)

    class MarketPlugin:
        stateful_indicators = False
        warmup_events = 0

        async def on_market_data(self, data):
            return Signal(
                signal_type=SignalType.BUY,
                order_type=OrderType.MARKET,
                symbol=data.symbol,
                price=Decimal("1"),
                quantity=Decimal("1"),
            )

    strategy = runner_for(rt, MarketPlugin())
    try:
        await strategy.on_market_data(market_data(), event_id="market-1")
        sent = rt.exchange.placed[0][0]
        assert sent.order_type == "MARKET"
        assert sent.price == Decimal("100")
        assert sent.price_source == PRICE_SOURCE_MARKET_TICKER
        assert sent.price_at_ms == 1_000_000
    finally:
        await rt.stop()


@pytest.mark.asyncio
async def test_closed_bar_reference_is_labelled_as_a_closed_bar_not_a_ticker():
    rt = runtime(snap=snapshot())
    await start_runtime(rt)

    class BarPlugin:
        stateful_indicators = False
        warmup_events = 0

        async def on_market_data(self, data):
            return Signal(
                signal_type=SignalType.BUY,
                order_type=OrderType.MARKET,
                symbol=data.symbol,
                quantity=Decimal("1"),
            )

    strategy = runner_for(rt, BarPlugin())
    try:
        bar = MarketData(
            symbol="BTCUSDT",
            data_type=MarketDataType.KLINE,
            price=Decimal("100"),
            volume=Decimal("1"),
            timestamp=market_data().timestamp,
            kline_open=Decimal("99"),
            kline_high=Decimal("101"),
            kline_low=Decimal("98"),
            kline_close=Decimal("100"),
            kline_interval="1m",
            metadata={"complete": True, "is_closed": True, "close_time_ms": 1_060_000},
        )
        await strategy.on_market_data(bar, event_id="bar-1")
        sent = rt.exchange.placed[0][0]
        assert sent.price_source == "MARKET_REFERENCE_CLOSED_BAR"
        assert sent.price_at_ms == 1_060_000
    finally:
        await rt.stop()


def test_order_spec_refuses_missing_fields_instead_of_defaulting():
    with pytest.raises(TradingError, match="ORDER_TYPE_REQUIRED"):
        build_order_spec(
            symbol="BTCUSDT",
            side="BUY",
            order_type=None,
            qty=Decimal("1"),
            limit_price=Decimal("100"),
            reference_price=Decimal("100"),
            reference_price_source=PRICE_SOURCE_MARKET_TICKER,
            reference_at_ms=1_000_000,
            decision_at_ms=1_000_000,
            time_in_force="GTC",
            book_id=TEST_BOOK,
        )
    with pytest.raises(TradingError, match="LIMIT_PRICE_REQUIRED"):
        build_order_spec(
            symbol="BTCUSDT",
            side="BUY",
            order_type=OrderType.LIMIT,
            qty=Decimal("1"),
            limit_price=None,
            reference_price=Decimal("100"),
            reference_price_source=PRICE_SOURCE_MARKET_TICKER,
            reference_at_ms=1_000_000,
            decision_at_ms=1_000_000,
            time_in_force="GTC",
            book_id=TEST_BOOK,
        )
    with pytest.raises(TradingError, match="ORDER_PRICE_SOURCE_REQUIRED"):
        build_order_spec(
            symbol="BTCUSDT",
            side="BUY",
            order_type=OrderType.MARKET,
            qty=Decimal("1"),
            limit_price=None,
            reference_price=Decimal("100"),
            reference_price_source="",
            reference_at_ms=1_000_000,
            decision_at_ms=1_000_000,
            time_in_force="GTC",
            book_id=TEST_BOOK,
        )
    with pytest.raises(TradingError, match="INVALID_ORDER_PRICE_SOURCE"):
        build_order_spec(
            symbol="BTCUSDT",
            side="BUY",
            order_type=OrderType.MARKET,
            qty=Decimal("1"),
            limit_price=None,
            reference_price=Decimal("100"),
            reference_price_source=PRICE_SOURCE_SIGNAL_LIMIT,
            reference_at_ms=1_000_000,
            decision_at_ms=1_000_000,
            time_in_force="GTC",
            book_id=TEST_BOOK,
        )


@pytest.mark.asyncio
async def test_adapter_refuses_an_unsupported_spec_before_any_venue_io():
    from dataclasses import replace

    from trader.adapters.broker.spot_execution_adapter import BinanceSpotExchangePort
    from trader.tests.test_spot_execution_adapter import FakeBroker
    from trader.tests.test_spot_execution_adapter import command as adapter_command

    broker = FakeBroker()
    port = BinanceSpotExchangePort(broker)
    result = await port.place_order(
        replace(adapter_command(), order_type="STOP"), "qts-unsupported"
    )
    assert result.outcome == ExchangeOutcome.REJECTED
    assert "UNSUPPORTED_ORDER_TYPE" in result.reason
    assert broker.place_calls == []

    missing_source = replace(adapter_command(), price_source="")
    result = await port.place_order(missing_source, "qts-missing-source")
    assert result.outcome == ExchangeOutcome.REJECTED
    assert "ORDER_PRICE_SOURCE_REQUIRED" in result.reason
    assert broker.place_calls == []


# ---------------------------------------------------------------- 2.4 / 2.12
@pytest.mark.asyncio
async def test_book_id_is_stable_across_service_restart_and_queried_per_book():
    store = MemoryTradingStore()
    clock = Clock()
    first = await recovered(store=store, clock=clock)
    await allocate(first, BOOK_A, "600")
    await allocate(first, BOOK_B, "400")
    receipt = await first.place(command(book_id=BOOK_A, qty=Decimal("2"), price=Decimal("100")))
    await first.record_execution(
        Execution(
            ACCOUNT_ID,
            receipt.cl_ord_id,
            "BTCUSDT",
            "fill-a",
            Decimal("2"),
            Decimal("100"),
            Decimal("0.2"),
            "USDT",
            1_000_100,
        )
    )

    restarted = service(store=store, clock=clock)
    state = await restarted.store.read(ACCOUNT_ID)
    assert state.orders[receipt.cl_ord_id].command.book_id == BOOK_A
    assert BOOK_A in state.books and BOOK_B in state.books
    books = {item["book_id"]: item for item in await restarted.books(ACCOUNT_ID)}
    assert books[BOOK_A]["positions"]["BTCUSDT"] == "2"
    assert books[BOOK_A]["equity"] == "599.8"
    assert books[BOOK_B]["equity"] == "400"
    assert books[book_ledger.UNALLOCATED]["equity"] == "0"

    # The per-book query is deterministic for the same state and clock.
    assert await restarted.books(ACCOUNT_ID) == await restarted.books(ACCOUNT_ID)


@pytest.mark.asyncio
async def test_deployment_id_never_becomes_a_book_key_or_asset_isolation_boundary():
    store = MemoryTradingStore()
    current = await recovered(store=store)
    await allocate(current, BOOK_A, "1000")
    first = await current.place(
        command(command_id="dep-a", book_id=BOOK_A, deployment_id="deployment-a", qty=Decimal("1"))
    )
    second = await current.place(
        command(command_id="dep-b", book_id=BOOK_A, deployment_id="deployment-b", qty=Decimal("1"))
    )
    state = await store.read(ACCOUNT_ID)
    assert set(state.books) == {book_ledger.UNALLOCATED, BOOK_A}
    assert state.orders[first.cl_ord_id].command.deployment_id == "deployment-a"
    assert state.orders[second.cl_ord_id].command.deployment_id == "deployment-b"
    assert state.books[BOOK_A].holds.keys() == {first.cl_ord_id, second.cl_ord_id}
    assert state.books[BOOK_A].balances["USDT"] == Decimal("1000")


# ---------------------------------------------------------------- 2.5
@pytest.mark.asyncio
async def test_every_commit_keeps_account_total_equal_to_book_partitions():
    store = MemoryTradingStore()
    clock = Clock()
    current = await recovered(store=store, clock=clock)
    await allocate(current, BOOK_A, "600")
    await allocate(current, BOOK_B, "400")

    def assert_conserved(state):
        assert book_ledger.conservation_mismatch(state) == {}
        assert state.balances["USDT"] == (
            state.books[book_ledger.UNALLOCATED].balances.get("USDT", Decimal(0))
            + state.books[BOOK_A].balances.get("USDT", Decimal(0))
            + state.books[BOOK_B].balances.get("USDT", Decimal(0))
        )

    assert_conserved(await store.read(ACCOUNT_ID))

    receipt = await current.place(command(book_id=BOOK_A, qty=Decimal("3"), price=Decimal("100")))
    assert_conserved(await store.read(ACCOUNT_ID))
    await current.record_execution(
        Execution(
            ACCOUNT_ID,
            receipt.cl_ord_id,
            "BTCUSDT",
            "e-1",
            Decimal("1"),
            Decimal("100"),
            Decimal("0.1"),
            "USDT",
            1_000_100,
        )
    )
    assert_conserved(await store.read(ACCOUNT_ID))
    await current.record_execution(
        Execution(
            ACCOUNT_ID,
            receipt.cl_ord_id,
            "BTCUSDT",
            "e-2",
            Decimal("1"),
            Decimal("101"),
            Decimal("0.001"),
            "BTC",
            1_000_200,
        )
    )
    assert_conserved(await store.read(ACCOUNT_ID))
    await current.cancel("cancel-1", ACCOUNT_ID, receipt.cl_ord_id)
    await current.observe(
        OrderObservation(ACCOUNT_ID, receipt.cl_ord_id, "CANCELLED", Decimal("2"), "b-1", 1_000_300)
    )
    assert_conserved(await store.read(ACCOUNT_ID))

    # An evidenced account-level difference is charged to UNALLOCATED and
    # keeps the partition identity true; no strategy book is guessed.
    clock.now = 1_000_400
    await current.recover(
        venue_snapshot(
            {"USDT": Decimal("799"), "BTC": Decimal("1.999")},
            checked=1_000_400,
            generation=2,
            marks={"BTCUSDT": {"price": Decimal("100"), "at_ms": 1_000_400}},
        )
    )
    resolved = await current.resolve(
        ACCOUNT_ID,
        "BALANCE_MISMATCH",
        actor="owner",
        evidence={
            "kind": "external_withdrawal",
            "reference": "statement-1",
            "reason": "operator checked the venue statement",
            "assets": {
                asset: values
                for asset, values in (
                    (await current.risk_view(ACCOUNT_ID)).balance_differences.items()
                )
            },
        },
    )
    assert resolved.ready is True
    state = await store.read(ACCOUNT_ID)
    assert_conserved(state)
    assert state.books[BOOK_A].balances["USDT"] > 0
    assert state.books[BOOK_B].balances["USDT"] == Decimal("400")


@pytest.mark.asyncio
async def test_book_hold_failure_rolls_back_account_hold_and_command(monkeypatch):
    venue = FakeExchange()
    store = MemoryTradingStore()
    current = await recovered(store=store, exchange=venue)
    await fund_book(current, BOOK_A, {"USDT": Decimal("1000")})

    def explode(*args, **kwargs):
        raise RuntimeError("book reservation unavailable")

    monkeypatch.setattr(book_ledger, "reserve_book_hold", explode)
    with pytest.raises(RuntimeError):
        await current.place(command(book_id=BOOK_A))

    state = await store.read(ACCOUNT_ID)
    assert state.commands == {}
    assert state.orders == {}
    assert state.holds == {}
    assert state.books[BOOK_A].holds == {}
    assert venue.placed == []
    assert book_ledger.conservation_mismatch(state) == {}


@pytest.mark.asyncio
async def test_execution_book_write_failure_rolls_back_account_and_book(monkeypatch):
    store = MemoryTradingStore()
    current = await recovered(store=store)
    await allocate(current, BOOK_A, "500")
    receipt = await current.place(command(book_id=BOOK_A, qty=Decimal("1"), price=Decimal("100")))

    def explode(*args, **kwargs):
        raise RuntimeError("book ledger unavailable")

    monkeypatch.setattr(trading_core, "apply_position", explode)
    with pytest.raises(RuntimeError):
        await current.record_execution(
            Execution(
                ACCOUNT_ID,
                receipt.cl_ord_id,
                "BTCUSDT",
                "e-rollback",
                Decimal("1"),
                Decimal("100"),
                Decimal("0.1"),
                "USDT",
                1_000_100,
            )
        )

    state = await store.read(ACCOUNT_ID)
    assert state.executions == {}
    assert state.balances == {"USDT": Decimal("1000"), "BTC": Decimal("0")}
    assert state.books[BOOK_A].balances == {"USDT": Decimal("500")}
    assert state.books[BOOK_A].positions == {}
    assert receipt.cl_ord_id in state.holds
    assert receipt.cl_ord_id in state.books[BOOK_A].holds
    assert book_ledger.conservation_mismatch(state) == {}


# ---------------------------------------------------------------- 2.6
@pytest.mark.asyncio
async def test_allocation_is_a_durable_idempotent_control_to_execution_command():
    store = MemoryTradingStore()
    venue = FakeExchange()
    control = service(store=store, exchange=venue, defer_send=True)
    execution = service(store=store, exchange=venue)
    await execution.recover(venue_snapshot())

    queued = await control.allocate(
        "alloc-1",
        ACCOUNT_ID,
        BOOK_A,
        "USDT",
        Decimal("250"),
        actor="owner",
        reason="initial strategy budget",
    )
    assert queued.status == "QUEUED"
    assert queued.kind == "ALLOCATE"
    state = await store.read(ACCOUNT_ID)
    assert BOOK_A not in state.books
    assert state.books[book_ledger.UNALLOCATED].balances["USDT"] == Decimal("1000")
    assert venue.placed == []

    await execution.resume_queued(ACCOUNT_ID)
    state = await store.read(ACCOUNT_ID)
    assert state.commands["alloc-1"].status == "CONFIRMED"
    assert state.books[BOOK_A].balances["USDT"] == Decimal("250")
    assert state.books[book_ledger.UNALLOCATED].balances["USDT"] == Decimal("750")
    assert book_ledger.conservation_mismatch(state) == {}
    audit = next(event for event in state.events if event["event_type"] == "book.allocated")
    assert audit["payload"]["book_id"] == BOOK_A
    assert audit["payload"]["amount"] == "250"

    # Draining again and retrying the same command never allocates twice.
    assert await execution.resume_queued(ACCOUNT_ID) == []
    retry = await control.allocate(
        "alloc-1",
        ACCOUNT_ID,
        BOOK_A,
        "USDT",
        Decimal("250"),
        actor="owner",
        reason="initial strategy budget",
    )
    assert retry.status == "CONFIRMED"
    state = await store.read(ACCOUNT_ID)
    assert state.books[BOOK_A].balances["USDT"] == Decimal("250")

    with pytest.raises(Conflict):
        await control.allocate(
            "alloc-1",
            ACCOUNT_ID,
            BOOK_A,
            "USDT",
            Decimal("400"),
            actor="owner",
            reason="initial strategy budget",
        )


@pytest.mark.asyncio
async def test_allocation_rejects_invalid_or_insufficient_requests_without_touching_books():
    store = MemoryTradingStore()
    current = service(store=store)
    await current.recover(venue_snapshot())

    with pytest.raises(TradingError, match="UNALLOCATED_BOOK_CANNOT_TRADE"):
        await allocate(current, book_ledger.UNALLOCATED, "10")
    with pytest.raises(TradingError, match="INVALID_BOOK_ID"):
        await allocate(current, "strategy-without-prefix", "10")
    with pytest.raises(TradingError, match="ALLOCATION_AMOUNT_INVALID"):
        await allocate(current, BOOK_A, "0")
    with pytest.raises(TradingError, match="ALLOCATION_REASON_REQUIRED"):
        await current.allocate(
            "alloc-no-reason", ACCOUNT_ID, BOOK_A, "USDT", Decimal("10"), actor="owner"
        )

    deferred = service(store=store, defer_send=True)
    queued = await deferred.allocate(
        "alloc-too-much",
        ACCOUNT_ID,
        BOOK_A,
        "USDT",
        Decimal("2000"),
        actor="owner",
        reason="too much",
    )
    assert queued.status == "QUEUED"
    await service(store=store).resume_queued(ACCOUNT_ID)
    state = await store.read(ACCOUNT_ID)
    record = state.commands["alloc-too-much"]
    assert record.status == "FAILED"
    assert "ALLOCATION_INSUFFICIENT_UNALLOCATED" in record.error
    assert BOOK_A not in state.books
    assert state.books[book_ledger.UNALLOCATED].balances["USDT"] == Decimal("1000")


@pytest.mark.asyncio
async def test_concurrent_allocations_cannot_overdraw_the_unallocated_bucket():
    store = MemoryTradingStore()
    current = service(store=store)
    await current.recover(venue_snapshot())
    deferred = service(store=store, defer_send=True)
    await asyncio.gather(
        deferred.allocate("alloc-a", ACCOUNT_ID, BOOK_A, "USDT", Decimal("600"), reason="a"),
        deferred.allocate("alloc-b", ACCOUNT_ID, BOOK_B, "USDT", Decimal("600"), reason="b"),
    )
    await service(store=store).resume_queued(ACCOUNT_ID)
    state = await store.read(ACCOUNT_ID)
    statuses = sorted(
        record.status for record in state.commands.values() if record.kind == "ALLOCATE"
    )
    assert statuses == ["CONFIRMED", "FAILED"]
    total = sum(
        (
            book.balances.get("USDT", Decimal(0))
            for book in state.books.values()
            if book.book_id != book_ledger.UNALLOCATED
        ),
        Decimal(0),
    )
    assert total == Decimal("600")
    assert state.books[book_ledger.UNALLOCATED].balances["USDT"] == Decimal("400")
    assert book_ledger.conservation_mismatch(state) == {}


@pytest.mark.asyncio
async def test_execution_owner_rechecks_a_queued_order_against_its_own_limits():
    store = MemoryTradingStore()
    strict = service(
        store=store,
        policy=risk_policy_from_env(
            {"QTS_RISK_POLICY_JSON": '{"max_symbol_notional":"150"}', "QTS_FEE_RATE": "0.001"}
        ),
    )
    await strict.recover(venue_snapshot())
    await fund_book(strict, BOOK_A, {"USDT": Decimal("1000")})
    control = service(store=store, defer_send=True)

    queued = await control.place(command(book_id=BOOK_A, qty=Decimal("2"), price=Decimal("100")))
    assert queued.status == "QUEUED"
    state = await store.read(ACCOUNT_ID)
    assert state.holds[queued.cl_ord_id].amount == Decimal("200.200")

    # The queued command was admitted under the control policy; the execution
    # owner's stricter limit must still be applied before any send.
    assert await strict.resume_queued(ACCOUNT_ID) == []
    assert strict.exchange.placed == []
    state = await store.read(ACCOUNT_ID)
    record = state.commands["cmd-1"]
    assert record.status == "FAILED"
    assert "SYMBOL_EXPOSURE_LIMIT" in record.error
    assert state.holds == {}
    assert state.books[BOOK_A].holds == {}
    assert state.orders[queued.cl_ord_id].status == "REJECTED"


# ---------------------------------------------------------------- 2.7
@pytest.mark.asyncio
async def test_account_and_book_holds_are_created_and_released_together():
    store = MemoryTradingStore()
    current = await recovered(store=store)
    await allocate(current, BOOK_A, "600")
    await allocate(current, BOOK_B, "400")

    receipt = await current.place(command(book_id=BOOK_A, qty=Decimal("2"), price=Decimal("100")))
    state = await store.read(ACCOUNT_ID)
    assert state.holds[receipt.cl_ord_id].amount == Decimal("200.200")
    assert state.books[BOOK_A].holds[receipt.cl_ord_id].amount == Decimal("200.200")

    await current.record_execution(
        Execution(
            ACCOUNT_ID,
            receipt.cl_ord_id,
            "BTCUSDT",
            "e-1",
            Decimal("1"),
            Decimal("100"),
            Decimal("0.1"),
            "USDT",
            1_000_100,
        )
    )
    state = await store.read(ACCOUNT_ID)
    assert state.holds[receipt.cl_ord_id].amount == Decimal("100.100")
    assert state.books[BOOK_A].holds[receipt.cl_ord_id].amount == Decimal("100.100")

    await current.cancel("cancel-1", ACCOUNT_ID, receipt.cl_ord_id)
    await current.observe(
        OrderObservation(ACCOUNT_ID, receipt.cl_ord_id, "CANCELLED", Decimal("1"), "b-1", 1_000_200)
    )
    state = await store.read(ACCOUNT_ID)
    assert receipt.cl_ord_id not in state.holds
    assert receipt.cl_ord_id not in state.books[BOOK_A].holds


@pytest.mark.asyncio
async def test_account_balance_alone_cannot_fund_a_book_and_vice_versa():
    store = MemoryTradingStore()
    current = await recovered(store=store)
    await allocate(current, BOOK_A, "100")
    await allocate(current, BOOK_B, "900")

    with pytest.raises(RiskRejected) as book_short:
        await current.place(command(book_id=BOOK_A, qty=Decimal("2"), price=Decimal("100")))
    assert "BOOK_INSUFFICIENT_AVAILABLE_BALANCE" in str(book_short.value)
    state = await store.read(ACCOUNT_ID)
    assert state.orders == {}
    assert state.holds == {}

    # A book with enough of its own budget can trade; the other book is not
    # polluted by the rejected order's reason or reservation.
    first = await current.place(command(book_id=BOOK_B, qty=Decimal("2"), price=Decimal("100")))
    assert first.status == "CONFIRMED"
    state = await store.read(ACCOUNT_ID)
    assert state.books[BOOK_A].holds == {}
    assert state.books[BOOK_B].holds[first.cl_ord_id].amount == Decimal("200.200")

    # Account-level risk still applies on top of the book budget.
    limited = service(
        store=MemoryTradingStore(),
        policy=risk_policy_from_env(
            {
                "QTS_RISK_POLICY_JSON": '{"max_symbol_notional":"150"}',
                "QTS_FEE_RATE": "0.001",
            }
        ),
    )
    await limited.recover(venue_snapshot())
    await allocate(limited, BOOK_A, "1000")
    with pytest.raises(RiskRejected) as exposure:
        await limited.place(command(book_id=BOOK_A, qty=Decimal("2"), price=Decimal("100")))
    assert "SYMBOL_EXPOSURE_LIMIT" in str(exposure.value)


def test_risk_policy_configuration_refuses_unknown_or_invalid_fields():
    policy = risk_policy_from_env(
        {
            "QTS_RISK_POLICY_JSON": '{"max_gross_notional":"150","max_symbol_notional":"50"}',
            "QTS_FEE_RATE": "0.002",
        }
    )
    assert policy.max_gross_notional == Decimal("150")
    assert policy.max_symbol_notional == Decimal("50")
    assert policy.fee_rate == Decimal("0.002")
    with pytest.raises(ValueError, match="Unknown risk policy fields"):
        risk_policy_from_env({"QTS_RISK_POLICY_JSON": '{"max_gross_notionl":"150"}'})
    with pytest.raises(ValueError, match="QTS_RISK_POLICY_JSON"):
        risk_policy_from_env({"QTS_RISK_POLICY_JSON": "not-json"})


# ---------------------------------------------------------------- 2.8
@pytest.mark.asyncio
async def test_same_book_opposite_in_flight_order_is_refused_without_new_risk():
    venue = FakeExchange()
    store = MemoryTradingStore()
    current = await recovered(
        balances={"USDT": Decimal("1000"), "BTC": Decimal("1")},
        store=store,
        exchange=venue,
    )
    await allocate(current, BOOK_A, "600")
    await allocate(current, BOOK_B, "400")
    await allocate(current, BOOK_B, "1", asset="BTC")
    first = await current.place(command(book_id=BOOK_A, qty=Decimal("1"), price=Decimal("100")))
    placed_before = len(venue.placed)

    with pytest.raises(RiskRejected) as refused:
        await current.place(
            command(command_id="reverse", book_id=BOOK_A, side="SELL", qty=Decimal("1"))
        )
    assert "BOOK_OPPOSITE_ORDER_IN_FLIGHT" in str(refused.value)
    assert len(venue.placed) == placed_before
    state = await store.read(ACCOUNT_ID)
    assert "reverse" not in state.commands
    assert len(state.orders) == 1
    assert state.books[BOOK_A].holds[first.cl_ord_id].amount == Decimal("100.100")

    # A partial fill keeps the opposite reservation active.
    await current.record_execution(
        Execution(ACCOUNT_ID, first.cl_ord_id, "BTCUSDT", "e-1", Decimal("0.5"), Decimal("100"))
    )
    with pytest.raises(RiskRejected, match="BOOK_OPPOSITE_ORDER_IN_FLIGHT"):
        await current.place(
            command(command_id="reverse-partial", book_id=BOOK_A, side="SELL", qty=Decimal("0.5"))
        )

    # Another book is independent: an opposite-side command there is allowed.
    other = await current.place(
        command(command_id="b-sell", book_id=BOOK_B, side="SELL", qty=Decimal("0.5"))
    )
    assert other.status == "CONFIRMED"

    # Once the first order is terminal and its hold is released, a reverse
    # command on the same book is allowed again (subject to its own inventory).
    await current.observe(
        OrderObservation(ACCOUNT_ID, first.cl_ord_id, "CANCELLED", Decimal("0.5"), "b-1", 1_000_300)
    )
    reopened = await current.place(
        command(command_id="reverse-after-cancel", book_id=BOOK_A, side="SELL", qty=Decimal("0.5"))
    )
    assert reopened.status == "CONFIRMED"


@pytest.mark.asyncio
async def test_unknown_reservation_keeps_blocking_and_is_never_treated_as_terminal():
    venue = FakeExchange()
    venue.place_outcome = ExchangeOrderResult(ExchangeOutcome.UNKNOWN, reason="timeout")
    store = MemoryTradingStore()
    current = await recovered(store=store, exchange=venue)
    await allocate(current, BOOK_A, "1000")
    first = await current.place(command(book_id=BOOK_A))
    assert first.status == "UNKNOWN"
    state = await store.read(ACCOUNT_ID)
    assert first.cl_ord_id in state.holds
    assert first.cl_ord_id in state.books[BOOK_A].holds

    # The account-wide UNKNOWN gate is stricter than the book conflict; either
    # way no new risk is admitted or sent.
    with pytest.raises(NotReady) as refused:
        await current.place(command(command_id="reverse", book_id=BOOK_A, side="SELL"))
    assert "UNKNOWN_COMMAND_PENDING" in str(refused.value)
    assert len(venue.placed) == 1
    state = await store.read(ACCOUNT_ID)
    assert "reverse" not in state.commands
    assert state.books[BOOK_A].holds[first.cl_ord_id].amount == Decimal("100.100")


@pytest.mark.asyncio
async def test_sell_first_then_buy_on_the_same_book_is_refused():
    current = await recovered(balances={"USDT": Decimal("1000"), "BTC": Decimal("1")})
    await allocate(current, BOOK_A, "1", asset="BTC")
    sell = await current.place(command(book_id=BOOK_A, side="SELL", qty=Decimal("1")))
    assert sell.status == "CONFIRMED"
    with pytest.raises(RiskRejected, match="BOOK_OPPOSITE_ORDER_IN_FLIGHT"):
        await current.place(
            command(command_id="buy-opposite", book_id=BOOK_A, side="BUY", qty=Decimal("1"))
        )
    state = await current.store.read(ACCOUNT_ID)
    assert "buy-opposite" not in state.commands
    assert len(state.orders) == 1


def test_risk_engine_treats_an_unknown_order_hold_as_an_active_opposite_order():
    state = AccountState(ACCOUNT_ID, status="READY", balances={"USDT": Decimal("1000")})
    state.books[BOOK_A] = BookLedger(BOOK_A, balances={"USDT": Decimal("1000")})
    order = OrderAggregate(
        cl_ord_id="qts-unknown",
        command=PlaceOrderCommand(
            command_id="unknown",
            account_id=ACCOUNT_ID,
            symbol="BTCUSDT",
            base_asset="BTC",
            quote_asset="USDT",
            side="BUY",
            qty=Decimal("1"),
            price=Decimal("100"),
            order_type="LIMIT",
            book_id=BOOK_A,
            price_source=PRICE_SOURCE_SIGNAL_LIMIT,
            price_at_ms=1_000_000,
        ),
        status="UNKNOWN",
    )
    state.orders[order.cl_ord_id] = order
    state.holds[order.cl_ord_id] = Hold("USDT", Decimal("100.1"))
    state.books[BOOK_A].holds[order.cl_ord_id] = Hold("USDT", Decimal("100.1"))
    state.reconciliation = {
        "checked_at_ms": 1_000_000,
        "stream_healthy": True,
        "marks": {"BTCUSDT": {"price": Decimal("100"), "at_ms": 1_000_000}},
    }
    reverse = command(command_id="reverse-risk", book_id=BOOK_A, side="SELL", qty=Decimal("1"))
    with pytest.raises(RiskRejected, match="BOOK_OPPOSITE_ORDER_IN_FLIGHT"):
        SpotRiskEngine(RiskPolicy()).evaluate(state, reverse, 1_000_000)


# ---------------------------------------------------------------- 2.10
@pytest.mark.asyncio
async def test_legacy_fill_without_a_book_is_recorded_in_unallocated_and_blocks():
    order = legacy_order(status="OPEN")
    state = single_book_state(
        balances={"USDT": Decimal("1000"), "BTC": Decimal("0")},
        order=order,
        hold=Hold("USDT", Decimal("100.1")),
        ledger=[
            {
                "entry_id": "opening:USDT",
                "execution_key": "",
                "account_id": ACCOUNT_ID,
                "cl_ord_id": "",
                "asset": "USDT",
                "delta": "1000",
                "kind": "opening_balance",
                "occurred_at_ms": 1_000_000,
            }
        ],
    )
    encoded = json_value(state)
    encoded.pop("books", None)
    decoded = decode_state(encoded)
    assert decoded.books[book_ledger.UNALLOCATED].balances["USDT"] == Decimal("1000")
    assert decoded.orders["qts-legacy"].command.book_id == ""

    store = MemoryTradingStore()
    await seed_state(store, decoded)
    current = service(store=store)
    view = await current.risk_view(ACCOUNT_ID)
    assert view.ready is False
    assert "BOOK_ATTRIBUTION_UNKNOWN" in view.reasons
    with pytest.raises(NotReady):
        await current.place(command(command_id="new-risk", book_id=TEST_BOOK, qty=Decimal("1")))

    await current.record_execution(
        Execution(
            ACCOUNT_ID,
            "qts-legacy",
            "BTCUSDT",
            "legacy-fill",
            Decimal("1"),
            Decimal("100"),
            Decimal("0.1"),
            "USDT",
            1_000_100,
        )
    )
    state = await store.read(ACCOUNT_ID)
    assert state.books[book_ledger.UNALLOCATED].balances["USDT"] == Decimal("899.9")
    assert state.books[book_ledger.UNALLOCATED].balances["BTC"] == Decimal("1")
    assert all(
        book.balances == {}
        for book_id, book in state.books.items()
        if book_id != book_ledger.UNALLOCATED
    )
    assert book_ledger.conservation_mismatch(state) == {}
    assert "BOOK_ATTRIBUTION_UNKNOWN" in (await current.risk_view(ACCOUNT_ID)).reasons

    # A bare code or an incomplete mapping does not clear the attribution.
    with pytest.raises(TradingError, match="RESOLVE_REQUIRES_EVIDENCE"):
        await current.resolve(ACCOUNT_ID, "BOOK_ATTRIBUTION_UNKNOWN")
    with pytest.raises(TradingError, match="RESOLVE_EVIDENCE_ORDER_MISMATCH"):
        await current.resolve(
            ACCOUNT_ID,
            "BOOK_ATTRIBUTION_UNKNOWN",
            evidence={
                "kind": "operator",
                "reference": "ticket-1",
                "reason": "verified",
                "orders": {"other-order": book_ledger.UNALLOCATED},
            },
        )
    settled = await current.resolve(
        ACCOUNT_ID,
        "BOOK_ATTRIBUTION_UNKNOWN",
        actor="owner",
        evidence={
            "kind": "operator_attribution",
            "reference": "ticket-1",
            "reason": "venue statement confirms the legacy order belongs to the unallocated bucket",
            "orders": {"qts-legacy": book_ledger.UNALLOCATED},
        },
    )
    assert settled.ready is True
    state = await store.read(ACCOUNT_ID)
    assert state.reconciliation[book_ledger.BOOK_LEDGER_KEY]["unattributed_orders"] == {}
    assert "qts-legacy" in state.reconciliation[book_ledger.BOOK_LEDGER_KEY]["resolved_orders"]


@pytest.mark.asyncio
async def test_cancelled_partial_legacy_order_cannot_be_attributed_while_late_fills_are_possible():
    order = legacy_order(status="CANCELLED", booked_qty="0.5")
    state = single_book_state(
        balances={"USDT": Decimal("950"), "BTC": Decimal("0.5")},
        order=order,
        reconciliation={
            "checked_at_ms": 1_000_000,
            "generation": 1,
            "stream_healthy": True,
            "marks": {"BTCUSDT": {"price": Decimal("100"), "at_ms": 1_000_000}},
        },
    )
    encoded = json_value(state)
    encoded.pop("books", None)
    decoded = decode_state(encoded)
    store = MemoryTradingStore()
    await seed_state(store, decoded)
    current = service(store=store)
    with pytest.raises(TradingError, match="RESOLVE_EVIDENCE_ORDER_NOT_SETTLED"):
        await current.resolve(
            ACCOUNT_ID,
            "BOOK_ATTRIBUTION_UNKNOWN",
            evidence={
                "kind": "operator_attribution",
                "reference": "ticket-2",
                "reason": "operator wants to settle a partial legacy order",
                "orders": {"qts-legacy": book_ledger.UNALLOCATED},
            },
        )


@pytest.mark.asyncio
async def test_legacy_unknown_command_is_migrated_only_when_its_owner_is_provable():
    known = single_book_state(
        balances={"USDT": Decimal("1000"), "BTC": Decimal("0")},
        order=legacy_order(),
        reconciliation={
            "checked_at_ms": 1_000_000,
            "generation": 1,
            "stream_healthy": True,
            "unresolved": [
                {
                    "code": "UNKNOWN_COMMAND",
                    "detail": {"command_id": "legacy-command", "cl_ord_id": "qts-legacy"},
                    "at_ms": 0,
                }
            ],
            "marks": {"BTCUSDT": {"price": Decimal("100"), "at_ms": 1_000_000}},
        },
    )
    known.commands["legacy-command"] = CommandRecord(
        command_id="legacy-command",
        cl_ord_id="qts-legacy",
        kind="PLACE",
        payload_hash="x",
        status="UNKNOWN",
    )
    encoded = json_value(known)
    encoded.pop("books", None)
    decoded = decode_state(encoded)
    entry = next(
        item for item in decoded.reconciliation["unresolved"] if item["code"] == "UNKNOWN_COMMAND"
    )
    assert entry["scope"] == "legacy-command"

    orphan = single_book_state(
        balances={"USDT": Decimal("1000"), "BTC": Decimal("0")},
        order=legacy_order(),
        reconciliation={
            "checked_at_ms": 1_000_000,
            "generation": 1,
            "stream_healthy": True,
            "unresolved": [
                {
                    "code": "UNKNOWN_COMMAND",
                    "detail": {"cl_ord_id": "qts-nowhere"},
                    "at_ms": 0,
                }
            ],
            "marks": {"BTCUSDT": {"price": Decimal("100"), "at_ms": 1_000_000}},
        },
    )
    encoded = json_value(orphan)
    encoded.pop("books", None)
    decoded = decode_state(encoded)
    store = MemoryTradingStore()
    await seed_state(store, decoded)
    current = service(store=store)
    view = await current.risk_view(ACCOUNT_ID)
    assert "LEGACY_UNKNOWN_COMMAND_UNATTRIBUTED" in view.reasons
    with pytest.raises(NotReady):
        await current.place(command(command_id="blocked", book_id=TEST_BOOK))


# ---------------------------------------------------------------- 2.11
def test_order_status_advances_monotonically_and_never_rolls_back_from_terminal():
    order = legacy_order(status="PENDING_ACCEPTANCE")
    assert advance_order_status(order, "OPEN") is True
    assert advance_order_status(order, "PARTIALLY_FILLED") is True
    assert advance_order_status(order, "OPEN") is False
    assert order.status == "PARTIALLY_FILLED"
    assert advance_order_status(order, "FILLED") is True
    assert advance_order_status(order, "OPEN") is False
    assert advance_order_status(order, "FILLED") is False
    with pytest.raises(Conflict):
        advance_order_status(order, "CANCELLED")
    with pytest.raises(Conflict):
        advance_order_status(order, "REJECTED")


@pytest.mark.asyncio
async def test_stale_observation_cannot_regress_status_and_terminal_is_final():
    store = MemoryTradingStore()
    current = await recovered(store=store)
    await allocate(current, BOOK_A, "1000")
    receipt = await current.place(command(book_id=BOOK_A, qty=Decimal("2"), price=Decimal("100")))

    await current.observe(
        OrderObservation(
            ACCOUNT_ID, receipt.cl_ord_id, "PARTIALLY_FILLED", Decimal("1"), "b-1", 1_000_100
        )
    )
    # A later OPEN observation cannot move the order backwards.
    await current.observe(
        OrderObservation(ACCOUNT_ID, receipt.cl_ord_id, "OPEN", Decimal("1"), "b-1", 1_000_200)
    )
    state = await store.read(ACCOUNT_ID)
    assert state.orders[receipt.cl_ord_id].status == "PARTIALLY_FILLED"

    await current.observe(
        OrderObservation(ACCOUNT_ID, receipt.cl_ord_id, "FILLED", Decimal("2"), "b-1", 1_000_300)
    )
    # A conflicting terminal observation is evidence, never a silent rewrite.
    assert (
        await current.observe(
            OrderObservation(
                ACCOUNT_ID, receipt.cl_ord_id, "CANCELLED", Decimal("2"), "b-1", 1_000_400
            )
        )
        is False
    )
    state = await store.read(ACCOUNT_ID)
    assert state.orders[receipt.cl_ord_id].status == "FILLED"
    assert "ORDER_OBSERVATION_CONFLICT" in (await current.risk_view(ACCOUNT_ID)).reasons


# ---------------------------------------------------------------- 2.12
@pytest.mark.asyncio
async def test_legacy_snapshot_migration_preserves_facts_and_is_idempotent():
    state = single_book_state(
        balances={"USDT": Decimal("1000"), "BTC": Decimal("0")},
        order=legacy_order(),
        hold=Hold("USDT", Decimal("100.1")),
        ledger=[
            {
                "entry_id": "opening:USDT",
                "execution_key": "",
                "account_id": ACCOUNT_ID,
                "cl_ord_id": "",
                "asset": "USDT",
                "delta": "1000",
                "kind": "opening_balance",
                "occurred_at_ms": 1_000_000,
            }
        ],
    )
    state.events = [{"event_id": "old", "event_type": "order.queued", "payload": {}}]
    encoded = json_value(state)
    encoded.pop("books", None)

    first = decode_state(encoded)
    second = decode_state(encoded)
    assert first.balances == second.balances == state.balances
    assert first.ledger == second.ledger == state.ledger
    assert first.events == second.events == state.events
    assert first.holds.keys() == second.holds.keys() == state.holds.keys()
    assert first.books[book_ledger.UNALLOCATED].balances == state.balances
    assert first.orders["qts-legacy"].command == state.orders["qts-legacy"].command
    assert (
        first.reconciliation[book_ledger.BOOK_LEDGER_KEY]["unattributed_orders"]
        == second.reconciliation[book_ledger.BOOK_LEDGER_KEY]["unattributed_orders"]
    )


# ---------------------------------------------------------------- API surface
@pytest.mark.asyncio
async def test_allocation_command_and_book_query_are_control_plane_endpoints():
    current = await recovered()
    app = create_trading_app(trading_service=current, auth_settings=AuthSettings())
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="http://testserver"
    ) as client:
        response = await client.post(
            "/v1/trading/allocations",
            json={
                "command_id": "api-alloc-1",
                "account_id": ACCOUNT_ID,
                "book_id": BOOK_A,
                "asset": "USDT",
                "amount": "250",
                "reason": "api allocation",
            },
        )
        assert response.status_code == 202, response.text
        assert response.json()["status"] == "CONFIRMED"
        assert response.json()["kind"] == "ALLOCATE"

        books = await client.get("/v1/trading/books", params={"account_id": ACCOUNT_ID})
        assert books.status_code == 200
        ids = {item["book_id"] for item in books.json()}
        assert {BOOK_A, book_ledger.UNALLOCATED} <= ids
        book = next(item for item in books.json() if item["book_id"] == BOOK_A)
        assert book["balances"]["USDT"] == "250"

        bad = await client.post(
            "/v1/trading/allocations",
            json={
                "command_id": "api-alloc-bad",
                "account_id": ACCOUNT_ID,
                "book_id": book_ledger.UNALLOCATED,
                "asset": "USDT",
                "amount": "10",
                "reason": "bad",
            },
        )
        assert bad.status_code == 400
        assert "UNALLOCATED_BOOK_CANNOT_TRADE" in bad.json()["detail"]


# ---------------------------------------------------------------- helpers
def market_data(price="100"):
    from datetime import datetime, timezone

    return MarketData(
        symbol="BTCUSDT",
        data_type=MarketDataType.TICKER,
        price=Decimal(price),
        timestamp=datetime.fromtimestamp(1000, tz=timezone.utc),
    )


def runner_for(rt, plugin):
    from trader.trading.strategy import UnifiedStrategyRunner

    return UnifiedStrategyRunner(
        rt,
        deployment_id="deployment-phase2",
        plugin=plugin,
        instruments={"BTCUSDT": ("BTC", "USDT")},
        book_id=TEST_BOOK,
    )
