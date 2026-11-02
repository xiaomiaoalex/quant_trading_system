"""Acceptance tests for the unified spot trading application service.

These tests exercise the public service API only: command idempotency, the
transactional account ledger, exchange outcomes, recovery and outbox delivery.
"""

from __future__ import annotations

import asyncio
from decimal import Decimal

import pytest

from trader.trading.models import (
    AccountState,
    CancelOrderCommand,
    Conflict,
    ExchangeOrderResult,
    ExchangeOutcome,
    Execution,
    NotReady,
    OrderObservation,
    PlaceOrderCommand,
    ReconcileSnapshot,
)
from trader.trading.risk import RiskPolicy
from trader.trading.service import TradingService
from trader.trading.store import MemoryTradingStore


class FakeExchange:
    def __init__(self):
        self.placed: list[tuple[PlaceOrderCommand, str]] = []
        self.cancelled: list[tuple[str, str]] = []
        self.place_outcome = ExchangeOrderResult(ExchangeOutcome.ACCEPTED, "b-1", "OPEN")
        self.cancel_outcome = ExchangeOrderResult(ExchangeOutcome.ACCEPTED, "b-1", "OPEN")
        self.query_result = ExchangeOrderResult(ExchangeOutcome.NOT_FOUND)
        self.place_gate: asyncio.Event | None = None
        self.enter_place = asyncio.Event()

    async def place_order(self, command: PlaceOrderCommand, cl_ord_id: str) -> ExchangeOrderResult:
        self.placed.append((command, cl_ord_id))
        self.enter_place.set()
        if self.place_gate is not None:
            await self.place_gate.wait()
        return self.place_outcome

    async def cancel_order(
        self, account_id: str, cl_ord_id: str, command_id: str, symbol: str
    ) -> ExchangeOrderResult:
        self.cancelled.append((cl_ord_id, command_id))
        return self.cancel_outcome

    async def query_order(
        self, account_id: str, cl_ord_id: str, symbol: str
    ) -> ExchangeOrderResult:
        return self.query_result


def command(command_id="cmd-1", **overrides) -> PlaceOrderCommand:
    values = dict(
        command_id=command_id,
        account_id="acct",
        symbol="BTCUSDT",
        base_asset="BTC",
        quote_asset="USDT",
        side="BUY",
        qty=Decimal("1"),
        price=Decimal("100"),
        deployment_id="dep-1",
        decision_id="dec-1",
    )
    values.update(overrides)
    return PlaceOrderCommand(**values)


class Clock:
    def __init__(self, now=1_000_000):
        self.now = now

    def __call__(self):
        return self.now


def make_service(exchange=None, clock=None, store=None, policy=None):
    return TradingService(
        store=store or MemoryTradingStore(),
        exchange=exchange or FakeExchange(),
        policy=policy or RiskPolicy(fee_rate=Decimal("0.001")),
        clock=clock or Clock(),
    )


async def bootstrap(
    service,
    *,
    balances=None,
    external_locked=None,
    generation=1,
    checked=1_000_000,
    complete=True,
    stream_healthy=True,
    open_orders=(),
    executions=(),
    marks=None,
):
    snapshot = ReconcileSnapshot(
        account_id="acct",
        checked_at_ms=checked,
        generation=generation,
        balances=(
            balances if balances is not None else {"USDT": Decimal("1000"), "BTC": Decimal("0")}
        ),
        external_locked=external_locked or {},
        open_orders=list(open_orders),
        executions=list(executions),
        marks=marks or {"BTCUSDT": {"price": Decimal("100"), "at_ms": checked}},
        complete=complete,
        stream_healthy=stream_healthy,
    )
    return await service.recover(snapshot)


@pytest.mark.asyncio
async def test_place_is_idempotent_and_conflicts_on_changed_payload():
    exchange = FakeExchange()
    service = make_service(exchange)
    await bootstrap(service)

    first = await service.place(command())
    second = await service.place(command())

    assert first == second
    assert first.status == "CONFIRMED"
    assert first.cl_ord_id == second.cl_ord_id
    assert len(exchange.placed) == 1

    with pytest.raises(Conflict):
        await service.place(command(qty=Decimal("2")))
    assert len(exchange.placed) == 1


@pytest.mark.asyncio
async def test_concurrent_place_creates_one_intent_and_one_send():
    exchange = FakeExchange()
    exchange.place_gate = asyncio.Event()
    service = make_service(exchange)
    await bootstrap(service)

    tasks = [asyncio.create_task(service.place(command())) for _ in range(100)]
    await asyncio.wait_for(exchange.enter_place.wait(), timeout=5)
    await asyncio.sleep(0)
    exchange.place_gate.set()
    receipts = await asyncio.gather(*tasks)

    assert len({r.cl_ord_id for r in receipts}) == 1
    assert len(exchange.placed) == 1
    state = await service.store.read("acct")
    assert len(state.orders) == 1
    assert len([c for c in state.commands.values() if c.kind == "PLACE"]) == 1


@pytest.mark.asyncio
async def test_available_balance_subtracts_our_holds_and_external_lock_once():
    service = make_service()
    await bootstrap(
        service,
        balances={"USDT": Decimal("1000"), "BTC": Decimal("0")},
        external_locked={"USDT": Decimal("100")},
    )

    view = await service.risk_view("acct")
    assert view.assets["USDT"]["available"] == Decimal("900")

    await service.place(command(command_id="buy-1", qty=Decimal("3"), price=Decimal("100")))
    view = await service.risk_view("acct")
    # 3 * 100 * 1.001 reserved by this system; 100 locked externally.
    assert view.assets["USDT"]["held"] == Decimal("300.300")
    assert view.assets["USDT"]["available"] == Decimal("599.700")


@pytest.mark.asyncio
async def test_order_acceptance_does_not_create_a_second_reservation():
    exchange = FakeExchange()
    service = make_service(exchange)
    await bootstrap(service)

    await service.place(command(qty=Decimal("3")))
    state = await service.store.read("acct")
    order = next(iter(state.orders.values()))
    assert len(state.holds) == 1
    assert state.holds[order.cl_ord_id].amount == Decimal("300.300")
    assert exchange.placed[0][1] == order.cl_ord_id


@pytest.mark.asyncio
async def test_concurrent_orders_cannot_both_reserve_more_than_balance():
    service = make_service()
    await bootstrap(service)

    async def place(command_id):
        try:
            return await service.place(
                command(command_id=command_id, qty=Decimal("6"), price=Decimal("100"))
            )
        except Exception as exc:  # risk rejection is the expected loser
            return exc

    results = await asyncio.gather(place("a"), place("b"))
    accepted = [r for r in results if not isinstance(r, Exception)]
    rejected = [r for r in results if isinstance(r, Exception)]
    assert len(accepted) == 1
    assert len(rejected) == 1


@pytest.mark.asyncio
async def test_partial_fill_releases_only_the_executed_portion():
    exchange = FakeExchange()
    service = make_service(exchange)
    await bootstrap(service)
    receipt = await service.place(command(qty=Decimal("2"), price=Decimal("100")))

    await service.record_execution(
        Execution(
            account_id="acct",
            cl_ord_id=receipt.cl_ord_id,
            symbol="BTCUSDT",
            exec_id="e-1",
            qty=Decimal("1"),
            price=Decimal("100"),
            fee=Decimal("0.1"),
            fee_asset="USDT",
            occurred_at_ms=1_000_100,
        )
    )

    state = await service.store.read("acct")
    assert state.balances["USDT"] == Decimal("899.9")
    assert state.balances["BTC"] == Decimal("1")
    assert state.positions["BTCUSDT"] == Decimal("1")
    assert state.holds[receipt.cl_ord_id].amount == Decimal("100.100")
    assert state.orders[receipt.cl_ord_id].status == "PARTIALLY_FILLED"
    assert state.orders[receipt.cl_ord_id].booked_qty == Decimal("1")


@pytest.mark.asyncio
async def test_cancel_confirmation_releases_remaining_hold_but_late_fill_books():
    exchange = FakeExchange()
    service = make_service(exchange)
    await bootstrap(service)
    receipt = await service.place(command(qty=Decimal("2"), price=Decimal("100")))
    await service.record_execution(
        Execution(
            "acct",
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

    cancel = await service.cancel("cancel-1", "acct", receipt.cl_ord_id)
    assert cancel.kind == "CANCEL"
    await service.observe(
        OrderObservation("acct", receipt.cl_ord_id, "CANCELLED", Decimal("1"), "b-1", 1_000_200)
    )
    state = await service.store.read("acct")
    assert receipt.cl_ord_id not in state.holds
    assert state.orders[receipt.cl_ord_id].status == "CANCELLED"

    # A late execution after the terminal cancel still books and never reopens the order.
    await service.record_execution(
        Execution(
            "acct",
            receipt.cl_ord_id,
            "BTCUSDT",
            "e-2",
            Decimal("0.5"),
            Decimal("100"),
            Decimal("0.05"),
            "USDT",
            1_000_300,
        )
    )
    state = await service.store.read("acct")
    order = state.orders[receipt.cl_ord_id]
    assert order.status == "CANCELLED"
    assert order.booked_qty == Decimal("1.5")
    assert state.balances["BTC"] == Decimal("1.5")
    assert receipt.cl_ord_id not in state.holds


@pytest.mark.asyncio
async def test_execution_dedup_survives_restart_and_ordering():
    store = MemoryTradingStore()
    exchange = FakeExchange()
    service = make_service(exchange, store=store)
    await bootstrap(service)
    receipt = await service.place(command(qty=Decimal("2"), price=Decimal("100")))

    executions = [
        Execution(
            "acct",
            receipt.cl_ord_id,
            "BTCUSDT",
            "e-2",
            Decimal("1"),
            Decimal("100"),
            Decimal("0.1"),
            "USDT",
            1_000_200,
        ),
        Execution(
            "acct",
            receipt.cl_ord_id,
            "BTCUSDT",
            "e-1",
            Decimal("1"),
            Decimal("100"),
            Decimal("0.1"),
            "USDT",
            1_000_100,
        ),
    ]
    for fact in executions + executions[::-1] + executions:
        await service.record_execution(fact)

    restarted = make_service(exchange, store=store)
    await restarted.record_execution(executions[0])

    state = await store.read("acct")
    assert state.orders[receipt.cl_ord_id].booked_qty == Decimal("2")
    assert state.balances["USDT"] == Decimal("799.8")
    assert state.balances["BTC"] == Decimal("2")
    kinds = [entry["kind"] for entry in state.ledger]
    # 1 opening balance + 2 executions x (asset leg + fee leg) + 2 cash legs
    assert kinds.count("opening_balance") == 1
    assert len([kind for kind in kinds if kind in {"trade", "fee"}]) == 6
    assert len(state.executions) == 2


@pytest.mark.asyncio
async def test_our_in_flight_order_observation_does_not_block_before_the_order_exists():
    service = make_service()
    await bootstrap(service)

    handled = await service.observe(
        OrderObservation(
            "acct", "qts-not-local-yet", "OPEN", Decimal("0"), "b-1", 1_000_100, source="WS"
        )
    )

    assert handled is True
    assert "UNKNOWN_ORDER" not in (await service.risk_view("acct")).reasons


@pytest.mark.asyncio
async def test_foreign_order_observation_still_blocks_before_the_order_exists():
    service = make_service()
    await bootstrap(service)

    handled = await service.observe(
        OrderObservation("acct", "manual-order", "OPEN", Decimal("1"), "99", 1_000_100, source="WS")
    )

    assert handled is False
    assert "UNKNOWN_ORDER" in (await service.risk_view("acct")).reasons


@pytest.mark.asyncio
async def test_terminal_foreign_order_observation_does_not_block_live_risk():
    service = make_service()
    await bootstrap(service)

    handled = await service.observe(
        OrderObservation(
            "acct", "manual-order", "CANCELLED", Decimal("1"), "99", 1_000_100, source="WS"
        )
    )

    assert handled is True
    assert "UNKNOWN_ORDER" not in (await service.risk_view("acct")).reasons


@pytest.mark.asyncio
async def test_foreign_order_observation_records_status_and_time_as_evidence():
    service = make_service()
    await bootstrap(service)

    await service.observe(
        OrderObservation("acct", "manual-order", "OPEN", Decimal("0"), "99", 1_000_100, source="WS")
    )

    state = await service.store.read("acct")
    entry = next(
        item for item in state.reconciliation["unresolved"] if item["code"] == "UNKNOWN_ORDER"
    )
    assert entry["detail"]["status"] == "OPEN"
    assert entry["detail"]["observed_at_ms"] == 1_000_100


@pytest.mark.asyncio
async def test_conflicting_duplicate_execution_blocks_new_risk():
    service = make_service()
    await bootstrap(service)
    receipt = await service.place(command(qty=Decimal("2"), price=Decimal("100")))
    first = Execution(
        "acct",
        receipt.cl_ord_id,
        "BTCUSDT",
        "e-1",
        Decimal("1"),
        Decimal("100"),
        Decimal("0.1"),
        "USDT",
        1_000_100,
    )
    await service.record_execution(first)

    conflicting = Execution(
        "acct",
        receipt.cl_ord_id,
        "BTCUSDT",
        "e-1",
        Decimal("1"),
        Decimal("101"),
        Decimal("0.1"),
        "USDT",
        1_000_100,
    )
    with pytest.raises(Conflict):
        await service.record_execution(conflicting)

    view = await service.risk_view("acct")
    assert view.status != "READY"
    assert "EXECUTION_KEY_CONFLICT" in view.reasons
    with pytest.raises(NotReady):
        await service.place(command(command_id="blocked", qty=Decimal("1"), price=Decimal("100")))


@pytest.mark.asyncio
async def test_filled_observation_before_executions_temporary_blocks_then_resolves():
    service = make_service()
    await bootstrap(service)
    receipt = await service.place(command(qty=Decimal("1"), price=Decimal("100")))

    await service.observe(
        OrderObservation("acct", receipt.cl_ord_id, "FILLED", Decimal("1"), "b-1", 1_000_100)
    )
    assert "FILL_OBSERVATION_MISMATCH" in (await service.risk_view("acct")).reasons

    await service.record_execution(
        Execution(
            "acct",
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
    view = await service.risk_view("acct")
    assert view.status == "READY"
    assert view.reasons == ()


@pytest.mark.asyncio
async def test_unknown_exchange_result_keeps_hold_and_is_never_auto_resent():
    exchange = FakeExchange()
    exchange.place_outcome = ExchangeOrderResult(ExchangeOutcome.UNKNOWN, reason="timeout")
    service = make_service(exchange)
    await bootstrap(service)

    receipt = await service.place(command())
    assert receipt.status == "UNKNOWN"
    state = await service.store.read("acct")
    assert receipt.cl_ord_id in state.holds
    assert state.commands["cmd-1"].status == "UNKNOWN"

    # Recovery with an order that is not visible leaves the command UNKNOWN and blocks risk.
    await service.recover(
        ReconcileSnapshot(
            "acct",
            1_000_100,
            2,
            {"USDT": Decimal("1000"), "BTC": Decimal("0")},
            marks={"BTCUSDT": {"price": Decimal("100"), "at_ms": 1_000_100}},
        )
    )
    state = await service.store.read("acct")
    assert state.commands["cmd-1"].status == "UNKNOWN"
    assert len(exchange.placed) == 1
    assert "UNKNOWN_COMMAND" in (await service.risk_view("acct")).reasons

    # A later snapshot that finds the order resolves the command, still without resending.
    await service.recover(
        ReconcileSnapshot(
            "acct",
            1_000_200,
            3,
            {"USDT": Decimal("1000"), "BTC": Decimal("0")},
            open_orders=[
                OrderObservation("acct", receipt.cl_ord_id, "OPEN", Decimal("0"), "b-1", 1_000_200)
            ],
            marks={"BTCUSDT": {"price": Decimal("100"), "at_ms": 1_000_200}},
        )
    )
    state = await service.store.read("acct")
    assert state.commands["cmd-1"].status == "CONFIRMED"
    assert len(exchange.placed) == 1


@pytest.mark.asyncio
async def test_recovery_rejects_stale_incomplete_and_unhealthy_snapshots():
    service = make_service()
    await bootstrap(service, generation=2, checked=1_000_000)

    stale = await service.recover(
        ReconcileSnapshot("acct", 1_000_000, 2, {"USDT": Decimal("1000"), "BTC": Decimal("0")})
    )
    assert stale.status != "READY"

    incomplete = await service.recover(
        ReconcileSnapshot(
            "acct",
            1_000_100,
            3,
            {"USDT": Decimal("1000"), "BTC": Decimal("0")},
            complete=False,
            marks={"BTCUSDT": {"price": Decimal("100"), "at_ms": 1_000_100}},
        )
    )
    assert incomplete.status != "READY"
    assert "INCOMPLETE_SNAPSHOT" in incomplete.reasons

    unhealthy = await service.recover(
        ReconcileSnapshot(
            "acct",
            1_000_200,
            4,
            {"USDT": Decimal("1000"), "BTC": Decimal("0")},
            stream_healthy=False,
            marks={"BTCUSDT": {"price": Decimal("100"), "at_ms": 1_000_200}},
        )
    )
    assert unhealthy.status != "READY"
    assert "PRIVATE_STREAM_UNHEALTHY" in unhealthy.reasons


@pytest.mark.asyncio
async def test_recovery_adopts_new_execution_and_balance_without_double_counting():
    service = make_service(clock=Clock(1_000_100))
    await bootstrap(service)
    receipt = await service.place(command(qty=Decimal("2"), price=Decimal("100")))

    execution = Execution(
        "acct",
        receipt.cl_ord_id,
        "BTCUSDT",
        "e-1",
        Decimal("1"),
        Decimal("100"),
        Decimal("0.1"),
        "USDT",
        1_000_100,
    )
    view = await service.recover(
        ReconcileSnapshot(
            "acct",
            1_000_100,
            2,
            {"USDT": Decimal("899.9"), "BTC": Decimal("1")},
            open_orders=[
                OrderObservation(
                    "acct", receipt.cl_ord_id, "PARTIALLY_FILLED", Decimal("1"), "b-1", 1_000_100
                )
            ],
            executions=[execution],
            marks={"BTCUSDT": {"price": Decimal("110"), "at_ms": 1_000_100}},
        )
    )

    assert view.status == "READY"
    state = await service.store.read("acct")
    assert state.balances["USDT"] == Decimal("899.9")
    assert state.balances["BTC"] == Decimal("1")
    assert state.orders[receipt.cl_ord_id].booked_qty == Decimal("1")
    assert state.holds[receipt.cl_ord_id].amount == Decimal("100.100")


@pytest.mark.asyncio
async def test_balance_mismatch_is_evidence_and_blocks_new_risk():
    service = make_service()
    await bootstrap(service)

    view = await service.recover(
        ReconcileSnapshot(
            "acct",
            1_000_100,
            2,
            {"USDT": Decimal("990"), "BTC": Decimal("0")},
            marks={"BTCUSDT": {"price": Decimal("100"), "at_ms": 1_000_100}},
        )
    )
    assert view.status != "READY"
    assert "BALANCE_MISMATCH" in view.reasons
    state = await service.store.read("acct")
    assert state.balances["USDT"] == Decimal("990")  # exchange remains the balance truth


@pytest.mark.asyncio
async def test_outbox_retries_notification_until_delivered():
    service = make_service()
    await bootstrap(service)
    await service.place(command())

    delivered = []

    async def flaky(event):
        if not delivered:
            delivered.append("first-failed")
            raise RuntimeError("sse down")
        delivered.append(event["event_type"])

    assert await service.dispatch_outbox("acct", flaky) == 0
    assert await service.dispatch_outbox("acct", flaky) == 2
    assert delivered == ["first-failed", "order.queued", "order.accepted"]
    assert await service.dispatch_outbox("acct", flaky) == 0


@pytest.mark.asyncio
async def test_ledger_write_failure_rolls_back_the_whole_execution():
    service = make_service()
    await bootstrap(service)
    receipt = await service.place(command(qty=Decimal("1"), price=Decimal("100")))

    original = service._post_execution

    def explode(*args, **kwargs):
        raise RuntimeError("ledger unavailable")

    service._post_execution = explode
    with pytest.raises(RuntimeError):
        await service.record_execution(
            Execution(
                "acct",
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
    service._post_execution = original

    state = await service.store.read("acct")
    assert state.executions == {}
    # Only the opening balance survives; the failed execution wrote nothing.
    assert [entry["kind"] for entry in state.ledger] == ["opening_balance"]
    assert state.balances["USDT"] == Decimal("1000")
    assert state.orders[receipt.cl_ord_id].booked_qty == Decimal("0")
    assert receipt.cl_ord_id in state.holds


@pytest.mark.asyncio
async def test_unsupported_spot_intents_are_explicitly_rejected():
    service = make_service()
    await bootstrap(service, balances={"USDT": Decimal("1000"), "BTC": Decimal("0")})

    with pytest.raises(Exception) as short:
        await service.place(command(command_id="short", side="SHORT"))
    assert "UNSUPPORTED_SPOT_INTENT" in str(short.value)

    with pytest.raises(Exception) as sell:
        await service.place(
            command(command_id="sell", side="SELL", qty=Decimal("1"), price=Decimal("100"))
        )
    assert "INSUFFICIENT_AVAILABLE_BALANCE" in str(sell.value)

    market = await service.place(
        command(command_id="market", order_type="MARKET", qty=Decimal("0.5"), price=Decimal("100"))
    )
    assert market.status == "CONFIRMED"


@pytest.mark.asyncio
async def test_risk_view_reports_stale_reconciliation_and_marks():
    clock = Clock(1_000_000)
    service = make_service(clock=clock, policy=RiskPolicy(max_reconciliation_age_ms=1000))
    await bootstrap(service)

    clock.now = 1_002_000
    view = await service.risk_view("acct")
    assert view.status != "READY"
    assert "ACCOUNT_SNAPSHOT_STALE" in view.reasons


@pytest.mark.asyncio
async def test_command_and_order_queries_expose_persistent_state():
    service = make_service()
    await bootstrap(service)
    receipt = await service.place(command())

    record = await service.command("acct", "cmd-1")
    assert record["status"] == "CONFIRMED"
    assert record["cl_ord_id"] == receipt.cl_ord_id
    order = await service.order("acct", receipt.cl_ord_id)
    assert order["status"] == "OPEN"
    assert order["remaining_qty"] == "1"


@pytest.mark.asyncio
async def test_cancel_unknown_state_does_not_report_cancelled():
    exchange = FakeExchange()
    exchange.cancel_outcome = ExchangeOrderResult(ExchangeOutcome.UNKNOWN, reason="timeout")
    service = make_service(exchange)
    await bootstrap(service)
    receipt = await service.place(command())

    cancel = await service.cancel("cancel-1", "acct", receipt.cl_ord_id, actor="owner")
    assert cancel.status == "UNKNOWN"
    order = await service.order("acct", receipt.cl_ord_id)
    assert order["status"] == "OPEN"
    state = await service.store.read("acct")
    assert state.commands["cancel-1"].actor == "owner"


@pytest.mark.asyncio
async def test_metrics_expose_operational_blockers():
    exchange = FakeExchange()
    exchange.place_outcome = ExchangeOrderResult(ExchangeOutcome.UNKNOWN, reason="timeout")
    service = make_service(exchange)
    await bootstrap(service)
    await service.place(command())

    metrics = await service.metrics("acct")
    assert metrics["unknown_commands"] == 1
    assert metrics["unbooked_fills"] == 0
    assert metrics["balance_anomalies"] == 0
    assert metrics["active_holds"] == 1
    assert metrics["outbox_backlog"] >= 1
    assert metrics["reconciliation_age_ms"] == 0
    assert "UNKNOWN_COMMAND" in metrics["unresolved"]


@pytest.mark.asyncio
async def test_execution_overfill_is_evidence_and_blocks_new_risk():
    service = make_service()
    await bootstrap(service)
    receipt = await service.place(command(qty=Decimal("1"), price=Decimal("100")))
    with pytest.raises(Conflict):
        await service.record_execution(
            Execution(
                "acct",
                receipt.cl_ord_id,
                "BTCUSDT",
                "e-over",
                Decimal("2"),
                Decimal("100"),
                Decimal("0.2"),
                "USDT",
                1_000_100,
            )
        )
    view = await service.risk_view("acct")
    assert view.ready is False
    assert "EXECUTION_QUANTITY_CONFLICT" in view.reasons
    state = await service.store.read("acct")
    assert state.orders[receipt.cl_ord_id].booked_qty == Decimal("0")
    assert state.balances["USDT"] == Decimal("1000")
    assert "e-over" in " ".join(state.executions)


@pytest.mark.asyncio
async def test_control_blocks_new_risk_and_can_be_resumed():
    service = make_service()
    await bootstrap(service)
    blocked = await service.control("ctl-1", "acct", "BLOCK_NEW_RISK", actor="owner")
    assert blocked.status == "CONFIRMED"
    assert "NEW_RISK_BLOCKED" in (await service.risk_view("acct")).reasons
    with pytest.raises(NotReady):
        await service.place(command(command_id="blocked-order"))

    resumed = await service.control("ctl-2", "acct", "RESUME_NEW_RISK", actor="owner")
    assert resumed.status == "CONFIRMED"
    assert (await service.risk_view("acct")).ready is True
    receipt = await service.place(command(command_id="allowed-order"))
    assert receipt.status == "CONFIRMED"


@pytest.mark.asyncio
async def test_cancel_all_uses_the_same_cancel_entry_and_preserves_unknown_state():
    exchange = FakeExchange()
    service = make_service(exchange)
    await bootstrap(service)
    first = await service.place(command(command_id="a"))
    second = await service.place(command(command_id="b"))

    control = await service.control("ctl-cancel-all", "acct", "CANCEL_ALL", actor="owner")
    assert control.status == "CONFIRMED"
    assert {cl for cl, _ in exchange.cancelled} == {first.cl_ord_id, second.cl_ord_id}

    exchange.cancel_outcome = ExchangeOrderResult(ExchangeOutcome.UNKNOWN, reason="timeout")
    third = await service.place(command(command_id="c"))
    unknown = await service.control("ctl-cancel-all-2", "acct", "CANCEL_ALL", actor="owner")
    assert unknown.status == "UNKNOWN"
    order = await service.order("acct", third.cl_ord_id)
    assert order["status"] == "OPEN"
    state = await service.store.read("acct")
    assert state.commands["ctl-cancel-all"].actor == "owner"
    assert state.commands[f"ctl-cancel-all:{first.cl_ord_id}"].kind == "CANCEL"


@pytest.mark.asyncio
async def test_stale_valuation_blocks_new_risk():
    clock = Clock(1_000_000)
    service = make_service(clock=clock, policy=RiskPolicy(max_mark_age_ms=1000))
    await bootstrap(service)
    receipt = await service.place(command(command_id="first", qty=Decimal("1")))
    await service.record_execution(
        Execution(
            "acct",
            receipt.cl_ord_id,
            "BTCUSDT",
            "e-1",
            Decimal("1"),
            Decimal("100"),
            Decimal("0.1"),
            "USDT",
            1_000_000,
        )
    )

    clock.now = 1_002_000
    view = await service.risk_view("acct")
    assert view.ready is False
    assert "VALUATION_INCOMPLETE" in view.reasons
    with pytest.raises(NotReady) as blocked:
        await service.place(command(command_id="second", qty=Decimal("1")))
    assert "VALUATION_STALE_OR_MISSING" in str(blocked.value)


@pytest.mark.asyncio
async def test_resume_queued_sends_only_known_unsent_commands():
    exchange = FakeExchange()
    store = MemoryTradingStore()
    service = make_service(exchange, store=store)
    await bootstrap(service)
    receipt = await service.place(command())

    # Simulate a crash before the SENDING marker committed: the intent is
    # persisted but the venue result never was.
    async with store.transaction("acct") as state:
        state.commands["cmd-1"].status = "QUEUED"
        state.orders[receipt.cl_ord_id].status = "PENDING_ACCEPTANCE"
        state.orders[receipt.cl_ord_id].broker_order_id = ""
    exchange.placed.clear()

    receipts = await service.resume_queued("acct")
    assert [item.status for item in receipts] == ["CONFIRMED"]
    assert len(exchange.placed) == 1
    assert await service.resume_queued("acct") == []


@pytest.mark.asyncio
async def test_resume_queued_never_resends_unknown_commands():
    exchange = FakeExchange()
    exchange.place_outcome = ExchangeOrderResult(ExchangeOutcome.UNKNOWN, reason="timeout")
    service = make_service(exchange)
    await bootstrap(service)
    await service.place(command())
    exchange.placed.clear()
    assert await service.resume_queued("acct") == []
    assert exchange.placed == []


@pytest.mark.asyncio
async def test_ledger_step_failure_rolls_back_order_and_execution():
    service = make_service()
    await bootstrap(service)
    receipt = await service.place(command(qty=Decimal("1"), price=Decimal("100")))

    original = service._apply_ledger

    def explode(*args, **kwargs):
        raise RuntimeError("ledger unavailable")

    service._apply_ledger = explode
    with pytest.raises(RuntimeError):
        await service.record_execution(
            Execution(
                "acct",
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
    service._apply_ledger = original

    state = await service.store.read("acct")
    assert state.executions == {}
    assert state.orders[receipt.cl_ord_id].booked_qty == Decimal("0")
    assert state.balances["USDT"] == Decimal("1000")


@pytest.mark.asyncio
async def test_recovery_releases_hold_on_terminal_observation_and_flags_missing_fills():
    service = make_service(clock=Clock(1_000_100))
    await bootstrap(service)
    receipt = await service.place(command(qty=Decimal("2"), price=Decimal("100")))

    view = await service.recover(
        ReconcileSnapshot(
            "acct",
            1_000_100,
            2,
            {"USDT": Decimal("1000"), "BTC": Decimal("0")},
            open_orders=[
                OrderObservation(
                    "acct", receipt.cl_ord_id, "CANCELLED", Decimal("0"), "b-1", 1_000_100
                )
            ],
            marks={"BTCUSDT": {"price": Decimal("100"), "at_ms": 1_000_100}},
        )
    )
    state = await service.store.read("acct")
    assert receipt.cl_ord_id not in state.holds
    assert state.orders[receipt.cl_ord_id].status == "CANCELLED"
    assert view.ready is True

    service.clock.now = 1_000_200
    second = await service.place(command(command_id="cmd-2", qty=Decimal("1")))
    view = await service.recover(
        ReconcileSnapshot(
            "acct",
            1_000_200,
            3,
            {"USDT": Decimal("1000"), "BTC": Decimal("0")},
            open_orders=[
                OrderObservation("acct", second.cl_ord_id, "FILLED", Decimal("1"), "b-1", 1_000_200)
            ],
            marks={"BTCUSDT": {"price": Decimal("100"), "at_ms": 1_000_200}},
        )
    )
    assert "FILL_OBSERVATION_MISMATCH" in view.reasons


@pytest.mark.asyncio
async def test_unknown_foreign_order_blocks_then_clears_when_the_venue_no_longer_shows_it():
    clock = Clock(1_000_000)
    service = make_service(clock=clock)
    await bootstrap(service)

    clock.now = 1_000_100
    view = await service.recover(
        ReconcileSnapshot(
            "acct",
            1_000_100,
            2,
            {"USDT": Decimal("1000"), "BTC": Decimal("0")},
            open_orders=[
                OrderObservation("acct", "foreign-1", "OPEN", Decimal("0"), "b-9", 1_000_100)
            ],
            marks={"BTCUSDT": {"price": Decimal("100"), "at_ms": 1_000_100}},
        )
    )
    assert "UNKNOWN_ORDER" in view.reasons
    assert view.ready is False

    clock.now = 1_000_200
    view = await service.recover(
        ReconcileSnapshot(
            "acct",
            1_000_200,
            3,
            {"USDT": Decimal("1000"), "BTC": Decimal("0")},
            marks={"BTCUSDT": {"price": Decimal("100"), "at_ms": 1_000_200}},
        )
    )
    assert "UNKNOWN_ORDER" not in view.reasons
    assert view.ready is True


@pytest.mark.asyncio
async def test_market_order_accepts_a_venue_fill_that_differs_from_the_requested_base_qty():
    service = make_service()
    await bootstrap(service)
    receipt = await service.place(
        command(
            command_id="mkt", order_type="MARKET", qty=Decimal("0.0002"), price=Decimal("77468")
        )
    )
    # The venue settles a MARKET buy in quote currency, so the executed base
    # quantity may differ from the requested base quantity.
    await service.observe(
        OrderObservation("acct", receipt.cl_ord_id, "FILLED", Decimal("0.00019"), "b-1", 1_000_100)
    )
    order = await service.order("acct", receipt.cl_ord_id)
    assert order["status"] == "FILLED"
    view = await service.risk_view("acct")
    assert "FILL_OBSERVATION_MISMATCH" not in view.reasons
    assert "ORDER_OBSERVATION_CONFLICT" not in view.reasons


@pytest.mark.asyncio
async def test_limit_order_still_requires_an_exact_fill_cover():
    service = make_service()
    await bootstrap(service)
    receipt = await service.place(command(command_id="lim", qty=Decimal("1"), price=Decimal("100")))
    await service.observe(
        OrderObservation("acct", receipt.cl_ord_id, "FILLED", Decimal("0.5"), "b-1", 1_000_100)
    )
    order = await service.order("acct", receipt.cl_ord_id)
    assert order["status"] != "FILLED"
    view = await service.risk_view("acct")
    assert "ORDER_OBSERVATION_CONFLICT" in view.reasons
