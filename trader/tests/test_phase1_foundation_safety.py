"""Phase 1 acceptance tests: accounting, UNKNOWN handling, fact ingress, eligibility.

Every test uses the public service / API surface with deterministic fakes and an
in-memory store unless the test is explicitly about PostgreSQL. No network, no
real account. The real-PostgreSQL checks live in ``test_trading_postgres.py``.
"""

from __future__ import annotations

import asyncio
from decimal import Decimal
from pathlib import Path

import httpx
import pytest

from trader.api.auth import AuthSettings
from trader.api.trading_app import create_trading_app
from trader.tests.test_trading_service import Clock, FakeExchange, bootstrap, command, make_service
from trader.trading.models import (
    Conflict,
    ExchangeOrderResult,
    ExchangeOutcome,
    Execution,
    NotReady,
    OrderObservation,
    ReconcileSnapshot,
    TradingError,
)
from trader.trading.risk import RiskPolicy
from trader.trading.service import TradingService
from trader.trading.store import MemoryTradingStore

ACCOUNT_ID = "acct"


class ScriptedExchange(FakeExchange):
    """Exchange fake that replays a fixed script of place outcomes."""

    def __init__(self, outcomes):
        super().__init__()
        self.outcomes = list(outcomes)

    async def place_order(self, command, cl_ord_id):
        self.placed.append((command, cl_ord_id))
        if not self.outcomes:
            raise AssertionError("unexpected place_order call")
        return self.outcomes.pop(0)


def service(store, exchange, *, defer_send=False, clock=None):
    return TradingService(
        store=store,
        exchange=exchange,
        policy=RiskPolicy(fee_rate=Decimal("0.001")),
        clock=clock or Clock(),
        defer_send=defer_send,
    )


def snapshot(balances, *, checked_at_ms=1_000_100, generation=2, open_orders=()):
    return ReconcileSnapshot(
        ACCOUNT_ID,
        checked_at_ms,
        generation,
        balances,
        open_orders=list(open_orders),
        marks={"BTCUSDT": {"price": Decimal("100"), "at_ms": checked_at_ms}},
    )


def balance_evidence(*, venue="990", internal="1000", difference="-10", reference="statement-1"):
    return {
        "kind": "external_withdrawal",
        "reference": reference,
        "reason": "operator verified the venue statement explains the difference",
        "assets": {
            "USDT": {
                "internal": internal,
                "venue": venue,
                "difference": difference,
            }
        },
    }


# ---------------------------------------------------------------- 1.2 / 1.3 UNKNOWN
@pytest.mark.asyncio
async def test_resume_queued_sends_no_new_risk_while_a_command_is_unknown():
    store = MemoryTradingStore()
    venue = ScriptedExchange(
        [
            ExchangeOrderResult(ExchangeOutcome.UNKNOWN, reason="timeout"),
            ExchangeOrderResult(ExchangeOutcome.ACCEPTED, "b-2", "OPEN"),
        ]
    )
    control = service(store, venue, defer_send=True)
    execution = service(store, venue)
    await bootstrap(control)

    assert (await control.place(command("cmd-1"))).status == "QUEUED"
    assert (await control.place(command("cmd-2"))).status == "QUEUED"
    assert venue.placed == []

    await execution.resume_queued(ACCOUNT_ID)

    # The first send was uncertain, so the second command must never be sent.
    assert [item[0].command_id for item in venue.placed] == ["cmd-1"]
    state = await store.read(ACCOUNT_ID)
    assert state.commands["cmd-1"].status == "UNKNOWN"
    assert state.commands["cmd-1"].cl_ord_id in state.holds
    assert state.commands["cmd-2"].status == "QUEUED"

    view = await execution.risk_view(ACCOUNT_ID)
    assert view.ready is False
    assert "UNKNOWN_COMMAND" in view.reasons
    assert "UNKNOWN_COMMAND_PENDING" in view.reasons

    # A new PLACE is refused with an explicit reason while the send is pending.
    with pytest.raises(NotReady) as refused:
        await control.place(command("cmd-3"))
    assert "UNKNOWN_COMMAND_PENDING" in str(refused.value)

    # Draining again changes nothing: UNKNOWN commands are never resent.
    await execution.resume_queued(ACCOUNT_ID)
    assert [item[0].command_id for item in venue.placed] == ["cmd-1"]


@pytest.mark.asyncio
async def test_send_cas_rechecks_the_unknown_block_before_sending():
    """The pre-send check lives in the SENDING CAS, not in stale loop state."""
    store = MemoryTradingStore()
    venue = FakeExchange()
    venue.place_outcome = ExchangeOrderResult(ExchangeOutcome.UNKNOWN, reason="timeout")
    control = service(store, venue, defer_send=True)
    execution = service(store, venue)
    await bootstrap(control)
    assert (await control.place(command("cmd-1"))).status == "QUEUED"
    assert (await control.place(command("cmd-2"))).status == "QUEUED"

    await execution.resume_queued(ACCOUNT_ID)
    state = await store.read(ACCOUNT_ID)
    assert state.commands["cmd-1"].status == "UNKNOWN"
    assert state.commands["cmd-2"].status == "QUEUED"

    # Even a caller that read the account before the UNKNOWN appeared cannot
    # win the SENDING transition for a new risk.
    assert await execution._mark_sending(ACCOUNT_ID, "cmd-2", require_no_unknown=True) is False
    assert (await store.read(ACCOUNT_ID)).commands["cmd-2"].status == "QUEUED"


@pytest.mark.asyncio
async def test_unknown_release_is_scoped_to_its_own_command():
    store = MemoryTradingStore()
    venue = FakeExchange()
    execution = service(store, venue)
    await bootstrap(execution)

    first = await execution.place(command("cmd-1"))
    second = await execution.place(command("cmd-2"))

    # Simulate a crash while both sends were in flight: both commands are
    # SENDING and the venue never reported either order.
    async with store.transaction(ACCOUNT_ID) as state:
        for record in state.commands.values():
            record.status = "SENDING"
        for order in state.orders.values():
            order.status = "PENDING_ACCEPTANCE"
            order.broker_order_id = ""
    for command_id in ("cmd-1", "cmd-2"):
        await execution._apply_place_result(
            ACCOUNT_ID,
            command_id,
            ExchangeOrderResult(ExchangeOutcome.UNKNOWN, reason="timeout"),
        )

    view = await execution.recover(snapshot({"USDT": Decimal("1000"), "BTC": Decimal("0")}))
    assert view.ready is False
    state = await store.read(ACCOUNT_ID)
    assert state.commands["cmd-1"].status == "UNKNOWN"
    assert state.commands["cmd-2"].status == "UNKNOWN"
    entries = [
        entry for entry in state.reconciliation["unresolved"] if entry["code"] == "UNKNOWN_COMMAND"
    ]
    assert {entry["scope"] for entry in entries} == {"cmd-1", "cmd-2"}

    # Venue evidence proves only the first order: only that command is settled.
    view = await execution.recover(
        snapshot(
            {"USDT": Decimal("1000"), "BTC": Decimal("0")},
            checked_at_ms=1_000_200,
            generation=3,
            open_orders=[
                OrderObservation(
                    ACCOUNT_ID, first.cl_ord_id, "OPEN", Decimal("0"), "b-1", 1_000_200
                )
            ],
        )
    )
    state = await store.read(ACCOUNT_ID)
    assert state.commands["cmd-1"].status == "CONFIRMED"
    assert state.commands["cmd-2"].status == "UNKNOWN"
    entries = [
        entry for entry in state.reconciliation["unresolved"] if entry["code"] == "UNKNOWN_COMMAND"
    ]
    assert {entry["scope"] for entry in entries} == {"cmd-2"}
    assert "UNKNOWN_COMMAND" in view.reasons
    # cmd-2 keeps its hold: the venue may still hold that order.
    assert second.cl_ord_id in state.holds


@pytest.mark.asyncio
async def test_another_commands_result_cannot_clear_a_pending_unknown():
    store = MemoryTradingStore()
    venue = FakeExchange()
    execution = service(store, venue)
    await bootstrap(execution)
    first = await execution.place(command("cmd-1"))
    second = await execution.place(command("cmd-2"))

    # Simulate: cmd-1 is UNKNOWN from an earlier send while cmd-2's exchange
    # call is in flight (its result is applied next).
    async with store.transaction(ACCOUNT_ID) as state:
        state.commands["cmd-1"].status = "UNKNOWN"
        state.commands["cmd-2"].status = "SENDING"
        state.reconciliation["unresolved"] = [
            {
                "code": "UNKNOWN_COMMAND",
                "detail": {"command_id": "cmd-1", "cl_ord_id": first.cl_ord_id},
                "scope": "cmd-1",
                "at_ms": 0,
            }
        ]

    await execution._apply_place_result(
        ACCOUNT_ID, "cmd-2", ExchangeOrderResult(ExchangeOutcome.ACCEPTED, "b-2", "OPEN")
    )
    state = await store.read(ACCOUNT_ID)
    assert state.commands["cmd-2"].status == "CONFIRMED"
    assert any(
        entry["code"] == "UNKNOWN_COMMAND" and entry["scope"] == "cmd-1"
        for entry in state.reconciliation["unresolved"]
    )

    # The same holds for another command's rejection.
    async with store.transaction(ACCOUNT_ID) as state:
        state.commands["cmd-2"].status = "SENDING"
        state.orders[second.cl_ord_id].status = "OPEN"
    await execution._apply_place_result(
        ACCOUNT_ID, "cmd-2", ExchangeOrderResult(ExchangeOutcome.REJECTED, reason="bad")
    )
    state = await store.read(ACCOUNT_ID)
    assert state.commands["cmd-2"].status == "FAILED"
    assert any(
        entry["code"] == "UNKNOWN_COMMAND" and entry["scope"] == "cmd-1"
        for entry in state.reconciliation["unresolved"]
    )
    assert (await execution.risk_view(ACCOUNT_ID)).ready is False


# ---------------------------------------------------------------- 1.4 resolve loop
@pytest.mark.asyncio
async def test_resolve_is_an_evidenced_command_applied_by_the_execution_process():
    store = MemoryTradingStore()
    venue = FakeExchange()
    clock = Clock(1_000_000)
    control = service(store, venue, defer_send=True, clock=clock)
    execution = service(store, venue, clock=clock)
    await bootstrap(control)
    clock.now = 1_000_100
    await execution.recover(snapshot({"USDT": Decimal("990"), "BTC": Decimal("0")}))

    # Without evidence the control side refuses to even record the command.
    with pytest.raises(TradingError) as refused:
        await control.control(
            "resolve-bad-0", ACCOUNT_ID, "RESOLVE", code="BALANCE_MISMATCH", evidence={}
        )
    assert "RESOLVE_REQUIRES_EVIDENCE" in str(refused.value)

    # Evidence that does not match the observed difference fails on the
    # execution side and leaves the account blocked.
    queued = await control.control(
        "resolve-bad",
        ACCOUNT_ID,
        "RESOLVE",
        actor="owner",
        code="BALANCE_MISMATCH",
        evidence=balance_evidence(venue="980", difference="-20"),
    )
    assert queued.status == "QUEUED"
    await execution.resume_queued(ACCOUNT_ID)
    record = await execution.command(ACCOUNT_ID, "resolve-bad")
    assert record["status"] == "FAILED"
    assert "RESOLVE_EVIDENCE_AMOUNT_MISMATCH" in record["error"]
    assert (await execution.risk_view(ACCOUNT_ID)).ready is False

    # UNKNOWN commands are never settled by an operator command.
    with pytest.raises(TradingError) as not_manual:
        await control.control(
            "resolve-unknown",
            ACCOUNT_ID,
            "RESOLVE",
            code="UNKNOWN_COMMAND",
            evidence={"kind": "manual", "reference": "x", "reason": "y"},
        )
    assert "RESOLVE_CODE_NOT_MANUALLY_RESOLVABLE" in str(not_manual.value)

    # Correct evidence is applied by the execution process and settles it.
    queued = await control.control(
        "resolve-good",
        ACCOUNT_ID,
        "RESOLVE",
        actor="owner",
        code="BALANCE_MISMATCH",
        evidence=balance_evidence(),
    )
    assert queued.status == "QUEUED"
    assert queued.kind == "CONTROL"
    await execution.resume_queued(ACCOUNT_ID)
    record = await execution.command(ACCOUNT_ID, "resolve-good")
    assert record["status"] == "CONFIRMED"
    assert (await execution.risk_view(ACCOUNT_ID)).ready is True
    state = await store.read(ACCOUNT_ID)
    assert state.balances["USDT"] == Decimal("990")
    assert [entry["kind"] for entry in state.ledger].count("external_adjustment") == 1

    # The evidence is part of the command identity: reusing the id with other
    # evidence is a conflict, not a retry.
    with pytest.raises(Conflict) as conflicting:
        await control.control(
            "resolve-good",
            ACCOUNT_ID,
            "RESOLVE",
            code="BALANCE_MISMATCH",
            evidence=balance_evidence(reference="another-statement"),
        )
    assert "COMMAND_ID_REUSED_WITH_DIFFERENT_PARAMETERS" in str(conflicting.value)


@pytest.mark.asyncio
async def test_resolve_command_applies_directly_when_the_process_owns_the_account():
    store = MemoryTradingStore()
    clock = Clock(1_000_000)
    execution = service(store, FakeExchange(), clock=clock)
    await bootstrap(execution)
    clock.now = 1_000_100
    await execution.recover(snapshot({"USDT": Decimal("990"), "BTC": Decimal("0")}))

    # The same code path a combined dev process uses: the account owner applies
    # the command instead of only queueing it.
    receipt = await execution.control(
        "resolve-direct",
        ACCOUNT_ID,
        "RESOLVE",
        actor="owner",
        code="BALANCE_MISMATCH",
        evidence=balance_evidence(),
    )
    assert receipt.status == "CONFIRMED"
    assert (await execution.risk_view(ACCOUNT_ID)).ready is True


@pytest.mark.asyncio
async def test_balance_difference_is_queryable_until_it_is_explained():
    store = MemoryTradingStore()
    clock = Clock(1_000_000)
    execution = service(store, FakeExchange(), clock=clock)
    await bootstrap(execution)
    clock.now = 1_000_100

    view = await execution.recover(snapshot({"USDT": Decimal("990"), "BTC": Decimal("0")}))

    assert view.venue_balances == {"USDT": "990", "BTC": "0"}
    assert view.balance_differences["USDT"] == {
        "internal": "1000",
        "venue": "990",
        "difference": "-10",
    }
    metrics = await execution.metrics(ACCOUNT_ID)
    assert metrics["venue_balances"]["USDT"] == "990"
    assert metrics["balance_differences"][0]["difference"] == "-10"
    state = await store.read(ACCOUNT_ID)
    assert state.balances["USDT"] == Decimal("1000")

    settled = await execution.resolve(ACCOUNT_ID, "BALANCE_MISMATCH", evidence=balance_evidence())
    assert settled.balance_differences == {}
    assert settled.ready is True


# ---------------------------------------------------------------- 1.6 fact ingress
@pytest.mark.asyncio
async def test_control_api_exposes_no_fact_write_routes():
    app = create_trading_app(
        trading_service=make_service(FakeExchange(), clock=Clock()),
        auth_settings=AuthSettings(),
    )
    paths = set(app.openapi()["paths"])
    assert "/v1/trading/orders" in paths
    assert "/v1/trading/control" in paths
    for route in (
        "/v1/trading/executions",
        "/v1/trading/observations",
        "/v1/trading/reconcile",
    ):
        assert route not in paths
    assert not [path for path in paths if path.startswith("/v1/trading/resolve")]

    # The execution process keeps the only fact write path: the same service
    # API, called in-process, still records executions and observations.
    service_under_test = make_service(FakeExchange(), clock=Clock())
    await bootstrap(service_under_test)
    receipt = await service_under_test.place(command(qty=Decimal("1"), price=Decimal("100")))
    assert await service_under_test.record_execution(
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
    assert await service_under_test.observe(
        OrderObservation(ACCOUNT_ID, receipt.cl_ord_id, "FILLED", Decimal("1"), "b-1", 1_000_100)
    )


@pytest.mark.asyncio
async def test_control_process_never_sends_even_with_credentials_in_the_environment(monkeypatch):
    import trader.runtime.control_main as control_main
    from trader.runtime.control_main import NoExchangeIO

    monkeypatch.setenv("BINANCE_API_KEY", "present-but-unused")
    monkeypatch.setenv("BINANCE_SECRET_KEY", "present-but-unused")
    source = Path(control_main.__file__).read_text(encoding="utf-8")
    assert "BINANCE_API_KEY" not in source
    assert "BINANCE_SECRET_KEY" not in source

    io = NoExchangeIO()
    with pytest.raises(NotReady) as refused:
        await io.place_order(command(), "qts-x")
    assert "CONTROL_PROCESS_CANNOT_EXECUTE" in str(refused.value)

    store = MemoryTradingStore()
    venue = FakeExchange()
    control = service(store, venue, defer_send=True)
    await bootstrap(control)
    receipt = await control.place(command())
    assert receipt.status == "QUEUED"
    assert venue.placed == []


# ---------------------------------------------------------------- 1.8 eligibility
@pytest.mark.asyncio
async def test_history_store_failure_makes_research_submission_fail_closed(monkeypatch):
    from trader.runtime import control_main
    from trader.trading.jobs import (
        HISTORY_STORE_UNAVAILABLE,
        MemoryResearchStore,
        UnavailableBacktestApplication,
    )

    class BrokenHistoryStore:
        async def connect(self):
            raise RuntimeError("history database unavailable")

    monkeypatch.setattr(control_main, "HistoryDatasetStore", BrokenHistoryStore)
    research = MemoryResearchStore()
    _, history_service, application = await control_main.build_history_plane(research)

    assert history_service is None
    assert isinstance(application, UnavailableBacktestApplication)
    with pytest.raises(ValueError) as refused:
        await application.submit({"dataset_ids": ["ds_x"]})
    assert str(refused.value) == HISTORY_STORE_UNAVAILABLE


@pytest.mark.asyncio
async def test_provenance_check_failure_is_a_stable_rejection(monkeypatch):
    from trader.runtime import control_main
    from trader.tests.test_research_runtime import bars, identity
    from trader.trading.jobs import HISTORY_STORE_UNAVAILABLE, MemoryResearchStore

    class HistoryStore:
        def __init__(self):
            self.storage = object()

        async def connect(self):
            return None

        async def get_dataset(self, dataset_id):
            return None

    class BrokenBridge:
        def __init__(self, storage=None):
            pass

        async def find_link_by_research_dataset(self, dataset_id):
            raise RuntimeError("bridge unavailable")

    monkeypatch.setattr(control_main, "HistoryDatasetStore", HistoryStore)
    monkeypatch.setattr(control_main, "ResearchHistoryBridgeStore", BrokenBridge)
    research = MemoryResearchStore()
    data = await research.register_dataset(identity(), bars())
    _, _, application = await control_main.build_history_plane(research)

    with pytest.raises(ValueError) as refused:
        await application.submit(
            {
                "dataset_ids": [data["dataset_id"]],
                "strategy": {"module_path": "trader.strategies.ema_cross_btc"},
            }
        )
    assert str(refused.value) == HISTORY_STORE_UNAVAILABLE


@pytest.mark.asyncio
async def test_research_api_maps_the_fail_closed_rejection_to_422(monkeypatch):
    from trader.runtime import control_main
    from trader.trading.jobs import MemoryResearchStore

    class BrokenHistoryStore:
        async def connect(self):
            raise RuntimeError("history database unavailable")

    monkeypatch.setattr(control_main, "HistoryDatasetStore", BrokenHistoryStore)
    _, _, application = await control_main.build_history_plane(MemoryResearchStore())
    app = create_trading_app(
        trading_service=make_service(FakeExchange(), clock=Clock()),
        research_store=MemoryResearchStore(),
        research_application=application,
        auth_settings=AuthSettings(),
    )
    client = httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://testserver")
    try:
        response = await client.post(
            "/v1/research/backtests",
            json={"manifest": {"dataset_ids": ["ds_none"], "engine": "event_runtime"}},
        )
        assert response.status_code == 422
        assert response.json()["detail"] == "HISTORY_STORE_UNAVAILABLE"
    finally:
        await client.aclose()


# ---------------------------------------------------------------- 1.9 idempotency
@pytest.mark.asyncio
async def test_duplicate_execution_is_booked_once():
    store = MemoryTradingStore()
    execution = service(store, FakeExchange())
    await bootstrap(execution)
    receipt = await execution.place(command(qty=Decimal("2"), price=Decimal("100")))
    fill = Execution(
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

    assert await execution.record_execution(fill) is True
    assert await execution.record_execution(fill) is False
    assert await execution.record_execution(fill) is False
    state = await store.read(ACCOUNT_ID)
    assert state.orders[receipt.cl_ord_id].booked_qty == Decimal("1")
    assert state.balances["USDT"] == Decimal("899.9")
    trade_legs = [entry for entry in state.ledger if entry["kind"] in {"trade", "fee"}]
    assert len(trade_legs) == 3


@pytest.mark.asyncio
async def test_out_of_order_execution_reports_are_booked_once():
    store = MemoryTradingStore()
    execution = service(store, FakeExchange())
    await bootstrap(execution)
    receipt = await execution.place(command(qty=Decimal("2"), price=Decimal("100")))

    later = Execution(
        ACCOUNT_ID,
        receipt.cl_ord_id,
        "BTCUSDT",
        "e-2",
        Decimal("1"),
        Decimal("101"),
        Decimal("0.1"),
        "USDT",
        1_000_200,
    )
    earlier = Execution(
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
    # The later fill is reported first, the earlier one after it.
    assert await execution.record_execution(later) is True
    assert await execution.record_execution(earlier) is True
    assert await execution.record_execution(later) is False
    assert await execution.record_execution(earlier) is False

    state = await store.read(ACCOUNT_ID)
    assert state.orders[receipt.cl_ord_id].booked_qty == Decimal("2")
    assert state.balances["USDT"] == Decimal("798.8")
    assert len(state.executions) == 2


@pytest.mark.asyncio
async def test_concurrent_place_with_the_same_command_sends_once():
    venue = FakeExchange()
    venue.place_gate = asyncio.Event()
    store = MemoryTradingStore()
    execution = service(store, venue)
    await bootstrap(execution)

    first = asyncio.create_task(execution.place(command("same")))
    await venue.enter_place.wait()
    second = asyncio.create_task(execution.place(command("same")))
    await asyncio.sleep(0)
    venue.place_gate.set()
    results = await asyncio.gather(first, second)

    assert len(venue.placed) == 1
    assert {item.command_id for item in results} == {"same"}
    state = await store.read(ACCOUNT_ID)
    assert len(state.orders) == 1
    assert len(state.holds) == 1


@pytest.mark.asyncio
async def test_concurrent_distinct_commands_each_send_once():
    venue = FakeExchange()
    store = MemoryTradingStore()
    execution = service(store, venue)
    await bootstrap(execution)

    await asyncio.gather(
        execution.place(command("cmd-a")),
        execution.place(command("cmd-b")),
    )

    assert len(venue.placed) == 2
    state = await store.read(ACCOUNT_ID)
    assert len(state.orders) == 2
    assert len(state.holds) == 2


# ---------------------------------------------------------------- 1.10 outcomes
@pytest.mark.parametrize(
    "outcome",
    [ExchangeOutcome.UNKNOWN, ExchangeOutcome.UNAVAILABLE, ExchangeOutcome.NOT_FOUND],
)
@pytest.mark.asyncio
async def test_uncertain_place_outcomes_keep_the_hold_and_block(outcome):
    venue = FakeExchange()
    venue.place_outcome = ExchangeOrderResult(outcome, reason="uncertain")
    store = MemoryTradingStore()
    execution = service(store, venue)
    await bootstrap(execution)

    receipt = await execution.place(command())
    assert receipt.status == "UNKNOWN"
    state = await store.read(ACCOUNT_ID)
    assert state.commands["cmd-1"].status == "UNKNOWN"
    assert receipt.cl_ord_id in state.holds
    view = await execution.risk_view(ACCOUNT_ID)
    assert view.ready is False
    assert "UNKNOWN_COMMAND" in view.reasons
    # Never resent automatically.
    assert await execution.resume_queued(ACCOUNT_ID) == []
    assert len(venue.placed) == 1


@pytest.mark.asyncio
async def test_rejected_place_releases_the_hold_and_does_not_block():
    venue = FakeExchange()
    venue.place_outcome = ExchangeOrderResult(ExchangeOutcome.REJECTED, reason="bad")
    store = MemoryTradingStore()
    execution = service(store, venue)
    await bootstrap(execution)

    receipt = await execution.place(command())
    assert receipt.status == "FAILED"
    assert receipt.error == "bad"
    state = await store.read(ACCOUNT_ID)
    assert state.holds == {}
    assert state.orders[receipt.cl_ord_id].status == "REJECTED"
    assert (await execution.risk_view(ACCOUNT_ID)).ready is True


@pytest.mark.asyncio
async def test_cancel_not_found_does_not_report_the_order_cancelled():
    venue = FakeExchange()
    venue.cancel_outcome = ExchangeOrderResult(ExchangeOutcome.NOT_FOUND)
    store = MemoryTradingStore()
    execution = service(store, venue)
    await bootstrap(execution)
    receipt = await execution.place(command())

    cancel = await execution.cancel("cancel-1", ACCOUNT_ID, receipt.cl_ord_id)
    assert cancel.status == "UNKNOWN"
    order = await execution.order(ACCOUNT_ID, receipt.cl_ord_id)
    assert order["status"] == "OPEN"
    assert receipt.cl_ord_id in (await store.read(ACCOUNT_ID)).holds
