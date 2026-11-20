"""Phase 2 round-2 fix retest: independent B1 / B2 / B3 / B4 / B5 coverage.

authored_by = 29822745-df5a-4e5e-b5f7-f66230c24ddd (tester).

Every expected number below was derived **by hand** from the contract
(``docs/INTERFACE_CONTRACTS.md`` 13.1/13.2/13.13 + ``docs/PHASE2_BOOK_LEDGER_MIGRATION.md``)
before reading the implementation:

* quote budget of a quote-settled MARKET BUY = ``qty * price`` (13.13, no fee);
* BUY reservation = ``qty * price * (1 + fee_rate)`` (13.2 upper bound with fee);
* a booking debits ``execution.qty * execution.price`` quote and credits
  ``execution.qty`` base, the fee is a separate entry (13.1);
* the two reservation layers (account + book) move together (13.2);
* a terminal order releases whatever is left on **both** layers (13.13/B3).

The contract's own "review finding already fixed" wording is deliberately not
treated as evidence. Fakes only: in-memory store, fixed clock, recording
exchange port.
"""

from __future__ import annotations

from datetime import datetime, timezone
from decimal import Decimal
from pathlib import Path

import pytest

from trader.core.application.strategy_protocol import MarketData, MarketDataType
from trader.core.domain.models.order import OrderType
from trader.core.domain.models.signal import Signal, SignalType
from trader.trading import books as book_ledger
from trader.trading.backtest import EventBacktestRuntime, StrategySignal
from trader.trading.datasets import freeze_dataset
from trader.trading.models import (
    SETTLEMENT_BASE,
    SETTLEMENT_QUOTE,
    SETTLEMENTS,
    CommandReceipt,
    Conflict,
    ExchangeOrderResult,
    ExchangeOutcome,
    Execution,
    NotReady,
    OrderObservation,
    PlaceOrderCommand,
    ReconcileSnapshot,
    TradingError,
)
from trader.trading.order_spec import (
    PRICE_SOURCE_BACKTEST_CLOSED_BAR,
    PRICE_SOURCE_MARKET_CLOSED_BAR,
    PRICE_SOURCE_MARKET_TICKER,
    PRICE_SOURCE_SIGNAL_LIMIT,
    build_order_spec,
    validate_order_command,
)
from trader.trading.risk import RiskPolicy, RiskRejected
from trader.trading.service import TradingService
from trader.trading.store import MemoryTradingStore, decode_state
from trader.trading.strategy import UnifiedStrategyRunner

ACCOUNT = "acct-indep-r2"
SYMBOL_BTC = "BTCUSDT"
SYMBOL_ETH = "ETHUSDT"
BASE_BTC = "BTC"
BASE_ETH = "ETH"
QUOTE = "USDT"
T0 = 1_700_000_000_000
FEE_RATE = Decimal("0.001")
BOOK_ALPHA = "book:alpha"
BOOK_BETA = "book:beta"
UNALLOCATED = book_ledger.UNALLOCATED

_STATE_FIELDS = (
    "account_id",
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
)

_REPO_ROOT = Path(__file__).resolve().parents[2]
_ALLOWED_CONSTRUCTORS = {"trader/trading/order_spec.py", "trader/trading/store.py"}


class FixedClock:
    """Deterministic clock; no wall time anywhere in this module."""

    def __init__(self, now: int = T0) -> None:
        self.now = now

    def __call__(self) -> int:
        return self.now

    def advance(self, ms: int) -> None:
        self.now += ms


class RecordingExchange:
    """Exchange port fake that records every outbound call."""

    def __init__(self) -> None:
        self.place_calls: list[tuple[PlaceOrderCommand, str]] = []
        self.place_result = ExchangeOrderResult(ExchangeOutcome.ACCEPTED, "b-1", "OPEN")

    async def place_order(self, command: PlaceOrderCommand, cl_ord_id: str) -> ExchangeOrderResult:
        self.place_calls.append((command, cl_ord_id))
        return self.place_result

    async def cancel_order(
        self, account_id: str, cl_ord_id: str, command_id: str, symbol: str
    ) -> ExchangeOrderResult:
        return ExchangeOrderResult(ExchangeOutcome.ACCEPTED, "b-1", "OPEN")

    async def query_order(
        self, account_id: str, cl_ord_id: str, symbol: str
    ) -> ExchangeOrderResult:
        return ExchangeOrderResult(ExchangeOutcome.NOT_FOUND)


class RecordingRuntime:
    """Runtime stand-in over a real service: the runner still drives production
    admission, only the lease/ownership plumbing is stubbed out.

    The stand-in mirrors every attribute ``UnifiedStrategyRunner`` reads off a
    runtime: ``store`` and ``service`` (admission), ``account_id``,
    ``strategy_start_allowed``, ``require_ownership`` (lease) and ``place``.
    """

    def __init__(
        self,
        service: TradingService,
        store: MemoryTradingStore,
        exchange: RecordingExchange,
    ) -> None:
        self.service = service
        self.store = store
        self.exchange = exchange
        self.account_id = ACCOUNT
        self.strategy_start_allowed = True
        self.placed: list[tuple[PlaceOrderCommand, str]] = []

    async def require_ownership(self, account_id: str) -> None:
        return None

    async def place(self, command: PlaceOrderCommand, actor: str = "system") -> CommandReceipt:
        self.placed.append((command, actor))
        return await self.service.place(command, actor=actor)


class StatelessPlugin:
    """A plugin with no indicator window, so it may trade at once."""

    stateful_indicators = False
    warmup_events = 0

    async def on_market_data(self, data: MarketData) -> Signal:
        return Signal(
            signal_type=SignalType.BUY,
            order_type=OrderType.MARKET,
            symbol=data.symbol,
            price=data.price,
            quantity=Decimal("1"),
        )

    async def on_account_view(self, view) -> None:
        return None


def build_service(store=None, exchange=None, clock=None, policy=None, defer_send=False):
    return TradingService(
        store=store if store is not None else MemoryTradingStore(),
        exchange=exchange if exchange is not None else RecordingExchange(),
        policy=policy if policy is not None else RiskPolicy(fee_rate=FEE_RATE),
        clock=clock if clock is not None else FixedClock(),
        defer_send=defer_send,
    )


def venue_snapshot(balances=None, *, checked=T0, generation=1, marks=None):
    if balances is None:
        balances = {QUOTE: Decimal("1000"), BASE_BTC: Decimal("0")}
    if marks is None:
        marks = {SYMBOL_BTC: {"price": Decimal("100"), "at_ms": checked}}
    return ReconcileSnapshot(
        account_id=ACCOUNT,
        checked_at_ms=checked,
        generation=generation,
        balances=balances,
        marks=marks,
    )


def market_buy(command_id: str, *, qty="1", price="100", settlement=SETTLEMENT_QUOTE):
    """A MARKET BUY built with the production order-spec factory."""
    spec = build_order_spec(
        symbol=SYMBOL_BTC,
        side="BUY",
        order_type="MARKET",
        qty=Decimal(qty),
        limit_price=None,
        reference_price=Decimal(price),
        reference_price_source=PRICE_SOURCE_MARKET_TICKER,
        reference_at_ms=T0,
        decision_at_ms=T0,
        time_in_force="GTC",
        book_id=BOOK_ALPHA,
        price_source=PRICE_SOURCE_MARKET_TICKER,
        settlement=settlement,
    )
    return spec.to_command(
        command_id=command_id,
        account_id=ACCOUNT,
        base_asset=BASE_BTC,
        quote_asset=QUOTE,
        deployment_id="dep-r2",
        decision_id=f"dec-{command_id}",
    )


def limit_buy(command_id: str, *, qty="1", price="100"):
    spec = build_order_spec(
        symbol=SYMBOL_BTC,
        side="BUY",
        order_type="LIMIT",
        qty=Decimal(qty),
        limit_price=Decimal(price),
        reference_price=None,
        reference_price_source=PRICE_SOURCE_SIGNAL_LIMIT,
        reference_at_ms=T0,
        decision_at_ms=T0,
        time_in_force="GTC",
        book_id=BOOK_ALPHA,
        price_source=PRICE_SOURCE_SIGNAL_LIMIT,
    )
    return spec.to_command(
        command_id=command_id,
        account_id=ACCOUNT,
        base_asset=BASE_BTC,
        quote_asset=QUOTE,
        deployment_id="dep-r2",
        decision_id=f"dec-{command_id}",
    )


async def funded_service(*, amount="1000", defer_send=False):
    store = MemoryTradingStore()
    exchange = RecordingExchange()
    clock = FixedClock()
    current = build_service(store=store, exchange=exchange, clock=clock, defer_send=defer_send)
    await current.recover(venue_snapshot(balances={QUOTE: Decimal(amount)}))
    await current.allocate(
        "alloc-r2", ACCOUNT, BOOK_ALPHA, QUOTE, Decimal(amount), actor="repro", reason="setup"
    )
    return current, exchange, store


def legacy_snapshot(*, command_extra=None, command_omit=()):
    """A pre-book-contract snapshot: 1000 USDT with a 100.1 account reservation."""
    command = {
        "command_id": "legacy-command",
        "account_id": ACCOUNT,
        "symbol": SYMBOL_BTC,
        "base_asset": BASE_BTC,
        "quote_asset": QUOTE,
        "side": "BUY",
        "qty": "1",
        "price": "100",
        "deployment_id": "legacy",
        "order_type": "LIMIT",
        "time_in_force": "GTC",
        "decision_id": "legacy",
        "book_id": "",
        "price_source": PRICE_SOURCE_SIGNAL_LIMIT,
        "price_at_ms": T0,
        "settlement": SETTLEMENT_QUOTE,
    }
    for name in command_omit:
        command.pop(name, None)
    command.update(command_extra or {})
    return {
        "account_id": ACCOUNT,
        "status": "READY",
        "version": 3,
        "balances": {QUOTE: "1000", BASE_BTC: "0"},
        "positions": {},
        "external_locked": {},
        "orders": {
            "qts-legacy": {
                "cl_ord_id": "qts-legacy",
                "command": command,
                "status": "OPEN",
                "broker_order_id": "b-legacy",
                "booked_qty": "0",
                "booked_quote": "0",
                "observed_qty": "0",
                "observed_at_ms": T0,
            }
        },
        "commands": {
            "legacy-command": {
                "command_id": "legacy-command",
                "cl_ord_id": "qts-legacy",
                "kind": "PLACE",
                "payload_hash": "legacy-hash",
                "status": "QUEUED",
                "actor": "legacy",
            }
        },
        "holds": {"qts-legacy": {"asset": QUOTE, "amount": "100.1"}},
        "books": {},
        "executions": {},
        "ledger": [],
        "events": [],
        "outbox": [],
        "control": {},
        "reconciliation": {
            "checked_at_ms": T0,
            "generation": 1,
            "stream_healthy": True,
            "unresolved": [],
            "marks": {SYMBOL_BTC: {"price": "100", "at_ms": T0}},
        },
    }


def _files_constructing_command() -> list[str]:
    """Every production module that hand-builds a command.

    Scope: the ``trader`` package (test modules excluded). ``scripts/`` holds
    operator rehearsals and is out of this contract's scope; the report names
    the one site found there.
    """
    found: list[str] = []
    for name in ("trader",):
        base = _REPO_ROOT / name
        if not base.exists():
            continue
        for path in sorted(base.rglob("*.py")):
            if "tests" in path.parts:
                continue
            for raw in path.read_text(encoding="utf-8").splitlines():
                code = raw.split("#", 1)[0]
                if "PlaceOrderCommand(" in code:
                    found.append(path.relative_to(_REPO_ROOT).as_posix())
                    break
    return found


# ============================================================ B1 unified spec
def test_b1_only_the_order_spec_factory_builds_production_order_commands():
    """No production module may hand-build a ``PlaceOrderCommand``.

    The allowed sites are the order-spec factory and the persistence decoder.
    This guard runs against the checked-out tree, so a new hand-built command
    anywhere in the ``trader`` package turns this test red.
    """
    found = _files_constructing_command()
    assert found, "the scan found nothing: the guard itself is broken"
    assert sorted(set(found)) == sorted(_ALLOWED_CONSTRUCTORS), (
        "production code builds PlaceOrderCommand outside the factory: "
        f"{sorted(set(found) - _ALLOWED_CONSTRUCTORS)}"
    )


def test_b1_order_type_and_price_source_are_mandatory_and_explicit():
    """Omitting either field is refused at the admission boundary."""
    with pytest.raises(TradingError) as missing_type:
        build_order_spec(
            symbol=SYMBOL_BTC,
            side="BUY",
            order_type="",
            qty=Decimal("1"),
            limit_price=None,
            reference_price=Decimal("100"),
            reference_price_source=PRICE_SOURCE_MARKET_TICKER,
            reference_at_ms=T0,
            decision_at_ms=T0,
            time_in_force="GTC",
            book_id=BOOK_ALPHA,
        )
    assert str(missing_type.value) == "ORDER_TYPE_REQUIRED"

    with pytest.raises(TradingError) as bad_source:
        build_order_spec(
            symbol=SYMBOL_BTC,
            side="BUY",
            order_type="LIMIT",
            qty=Decimal("1"),
            limit_price=Decimal("100"),
            reference_price=None,
            reference_price_source="GUESS",
            reference_at_ms=T0,
            decision_at_ms=T0,
            time_in_force="GTC",
            book_id=BOOK_ALPHA,
            price_source="GUESS",
        )
    assert str(bad_source.value) == "INVALID_ORDER_PRICE_SOURCE"

    # A MARKET order may not borrow a LIMIT price source either.
    with pytest.raises(TradingError) as crossed:
        build_order_spec(
            symbol=SYMBOL_BTC,
            side="BUY",
            order_type="MARKET",
            qty=Decimal("1"),
            limit_price=None,
            reference_price=Decimal("100"),
            reference_price_source=PRICE_SOURCE_SIGNAL_LIMIT,
            reference_at_ms=T0,
            decision_at_ms=T0,
            time_in_force="GTC",
            book_id=BOOK_ALPHA,
            price_source=PRICE_SOURCE_SIGNAL_LIMIT,
        )
    assert str(crossed.value) == "INVALID_ORDER_PRICE_SOURCE"


def test_b1_the_factory_command_carries_an_explicit_settlement():
    """The economic identity must state how the order settles, not inherit it."""
    spec = build_order_spec(
        symbol=SYMBOL_BTC,
        side="BUY",
        order_type="MARKET",
        qty=Decimal("1"),
        limit_price=None,
        reference_price=Decimal("100"),
        reference_price_source=PRICE_SOURCE_MARKET_TICKER,
        reference_at_ms=T0,
        decision_at_ms=T0,
        time_in_force="GTC",
        book_id=BOOK_ALPHA,
        price_source=PRICE_SOURCE_MARKET_TICKER,
    )
    assert spec.settlement in SETTLEMENTS
    assert spec.settlement == SETTLEMENT_QUOTE

    base_spec = build_order_spec(
        symbol=SYMBOL_BTC,
        side="BUY",
        order_type="MARKET",
        qty=Decimal("1"),
        limit_price=None,
        reference_price=Decimal("100"),
        reference_price_source=PRICE_SOURCE_BACKTEST_CLOSED_BAR,
        reference_at_ms=T0,
        decision_at_ms=T0,
        time_in_force="GTC",
        book_id=BOOK_ALPHA,
        price_source=PRICE_SOURCE_BACKTEST_CLOSED_BAR,
        settlement=SETTLEMENT_BASE,
    )
    assert base_spec.settlement == SETTLEMENT_BASE

    command = spec.to_command(
        command_id="cmd-r2-spec",
        account_id=ACCOUNT,
        base_asset=BASE_BTC,
        quote_asset=QUOTE,
        deployment_id="dep-r2",
        decision_id="dec-r2-spec",
    )
    assert command.settlement == SETTLEMENT_QUOTE
    assert command.order_type == "MARKET"
    assert command.price_source == PRICE_SOURCE_MARKET_TICKER
    assert command.book_id == BOOK_ALPHA
    assert validate_order_command(command) is None


def test_b1_an_old_snapshot_without_settlement_still_decodes():
    """Backward compatibility: missing fields decode to defaults, not a crash.

    A decoded command with no ``order_type`` is then refused at admission, so
    compatibility never means silently guessing the specification.
    """
    state = decode_state(legacy_snapshot(command_omit=("settlement",)))
    assert state.orders["qts-legacy"].command.settlement == SETTLEMENT_QUOTE

    stale = decode_state(legacy_snapshot(command_omit=("order_type",)))
    command = stale.orders["qts-legacy"].command
    assert command.order_type == ""
    with pytest.raises(TradingError) as refused:
        validate_order_command(command)
    assert str(refused.value) == "ORDER_TYPE_REQUIRED"


async def test_b1_the_strategy_runner_goes_through_the_spec_factory():
    """A runner signal produces a fully explicit command, not adapter parameters.

    The runner is executed for real; only the runtime port is a recording
    stand-in, so the production path ``closed KLINE -> plugin signal ->
    build_order_spec -> to_command -> runtime.place`` is exercised end to end.
    A ticker snapshot is no longer a decision input (contract 13.15): only a
    completed bar may produce a command, and its close prices a MARKET order.
    """
    store = MemoryTradingStore()
    exchange = RecordingExchange()
    service = build_service(store=store, exchange=exchange, clock=FixedClock())
    await service.recover(venue_snapshot())
    await service.allocate(
        "alloc-r2-runner",
        ACCOUNT,
        BOOK_ALPHA,
        QUOTE,
        Decimal("1000"),
        actor="repro",
        reason="runner setup",
    )
    runtime = RecordingRuntime(service, store, exchange)
    # The decision clock is explicit: visibility must not fall back to the
    # observation timestamp by accident.
    runtime.clock = FixedClock()
    runner = UnifiedStrategyRunner(
        runtime,
        deployment_id="dep-r2",
        plugin=StatelessPlugin(),
        instruments={SYMBOL_BTC: (BASE_BTC, QUOTE)},
        strategy_id="alpha",
        book_id=BOOK_ALPHA,
    )
    bar_id = f"bar:{SYMBOL_BTC}:1m:{T0 - 60_000}"
    data = MarketData(
        symbol=SYMBOL_BTC,
        data_type=MarketDataType.KLINE,
        price=Decimal("100"),
        volume=Decimal("2"),
        timestamp=datetime.fromtimestamp(T0 / 1000, tz=timezone.utc),
        available_at_ms=T0,
        kline_open=Decimal("99"),
        kline_high=Decimal("101"),
        kline_low=Decimal("98"),
        kline_close=Decimal("100"),
        kline_interval="1m",
        metadata={
            "event_id": bar_id,
            "bar_id": bar_id,
            "open_time_ms": T0 - 60_000,
            "close_time_ms": T0,
            "available_at_ms": T0,
            "interval": "1m",
            "complete": True,
            "is_closed": True,
        },
    )
    receipt = await runner.on_market_data(data, event_id=bar_id)
    assert receipt is not None
    assert receipt.status == "CONFIRMED"
    assert runtime.placed, "the runner did not submit any command"
    assert exchange.place_calls, "the runner never reached the venue"

    command, actor = runtime.placed[0]
    assert actor == "strategy:dep-r2"
    assert command.order_type == "MARKET"
    assert command.settlement == SETTLEMENT_QUOTE
    assert command.price_source == PRICE_SOURCE_MARKET_CLOSED_BAR
    assert command.price_reference_id == bar_id
    assert command.price_at_ms == T0
    assert command.book_id == BOOK_ALPHA
    assert command.qty == Decimal("1")
    assert command.price == Decimal("100")
    assert command.decision_id
    assert validate_order_command(command) is None

    # The same closed bar replayed must return the accepted command instead of
    # trading twice, and the phase-2 reservation must stay exactly one hold.
    again = await runner.on_market_data(data, event_id=bar_id)
    assert again is not None
    assert again.cl_ord_id == receipt.cl_ord_id
    assert len(runtime.placed) == 1
    assert len(exchange.place_calls) == 1
    state = await store.read(ACCOUNT)
    assert len(state.orders) == 1
    # The setup produced one ALLOCATE command; the runner adds exactly one
    # PLACE, and the replay above must not add a second one.
    kinds = [row.kind for row in state.commands.values()]
    assert kinds.count("PLACE") == 1
    assert kinds.count("ALLOCATE") == 1
    assert len(state.commands) == 2
    assert len(state.holds) == 1
    # qty * price * (1 + fee) = 1 * 100 * 1.001 on both reservation layers.
    assert state.holds[receipt.cl_ord_id].amount == Decimal("100.1")
    assert state.books[BOOK_ALPHA].holds[receipt.cl_ord_id].amount == Decimal("100.1")
    assert book_ledger.conservation_mismatch(state) == {}


# ========================================================= B2 quote settlement
async def test_b2_base_overfill_inside_the_quote_budget_is_booked():
    """1.1 base at 90 spends 99 of the 100 quote budget: book it, do not refuse.

    Booking is not terminal (contract 13.14): 1 of the quote budget is unspent
    and no venue terminal observation arrived, so both hold layers keep the
    real residue 100.1 - 1.1 * 90 = 1.1 and the order stays PARTIALLY_FILLED.
    """
    current, exchange, store = await funded_service()
    receipt = await current.place(market_buy("cmd-r2-b2a"))
    cl_ord_id = receipt.cl_ord_id

    placed = await store.read(ACCOUNT)
    assert placed.holds[cl_ord_id].amount == Decimal("100.1")
    assert placed.books[BOOK_ALPHA].holds[cl_ord_id].amount == Decimal("100.1")

    execution = Execution(
        ACCOUNT,
        cl_ord_id,
        SYMBOL_BTC,
        "exec-r2-b2a",
        Decimal("1.1"),
        Decimal("90"),
        Decimal("0"),
        "",
        T0 + 1,
    )
    assert await current.record_execution(execution) is True

    state = await store.read(ACCOUNT)
    order = state.orders[cl_ord_id]
    assert order.booked_qty == Decimal("1.1")
    assert order.booked_quote == Decimal("99")
    assert order.remaining_qty == Decimal("0")
    assert state.balances[QUOTE] == Decimal("901")
    assert state.balances[BASE_BTC] == Decimal("1.1")
    assert state.positions[SYMBOL_BTC] == Decimal("1.1")
    assert state.books[BOOK_ALPHA].balances[QUOTE] == Decimal("901")
    assert state.books[BOOK_ALPHA].positions[SYMBOL_BTC] == Decimal("1.1")
    assert order.status == "PARTIALLY_FILLED"
    # real residue kept on both layers: 100.1 - (1.1 * 90 + 0) = 1.1
    assert state.holds[cl_ord_id].amount == Decimal("1.1")
    assert state.books[BOOK_ALPHA].holds[cl_ord_id].amount == Decimal("1.1")
    assert book_ledger.conservation_mismatch(state) == {}


async def test_b2_cumulative_quote_over_budget_is_refused_without_double_debit():
    """0.5@100 then 0.6@100 spends 110 of a 100 budget: refuse, never re-debit."""
    current, exchange, store = await funded_service()
    receipt = await current.place(market_buy("cmd-r2-b2b"))
    cl_ord_id = receipt.cl_ord_id

    first = Execution(
        ACCOUNT,
        cl_ord_id,
        SYMBOL_BTC,
        "exec-r2-b2b-1",
        Decimal("0.5"),
        Decimal("100"),
        Decimal("0"),
        "",
        T0 + 1,
    )
    assert await current.record_execution(first) is True

    after_first = await store.read(ACCOUNT)
    assert after_first.balances[QUOTE] == Decimal("950")
    assert after_first.balances[BASE_BTC] == Decimal("0.5")
    # release = 0.5 * 100 + 0 (zero fee): 100.1 - 50 = 50.1 on both layers
    assert after_first.holds[cl_ord_id].amount == Decimal("50.1")
    assert after_first.books[BOOK_ALPHA].holds[cl_ord_id].amount == Decimal("50.1")

    over = Execution(
        ACCOUNT,
        cl_ord_id,
        SYMBOL_BTC,
        "exec-r2-b2b-2",
        Decimal("0.6"),
        Decimal("100"),
        Decimal("0"),
        "",
        T0 + 2,
    )
    with pytest.raises(Conflict) as conflict:
        await current.record_execution(over)
    assert str(conflict.value) == "EXECUTION_KEY_CONFLICT"

    refused = await store.read(ACCOUNT)
    assert refused.balances[QUOTE] == Decimal("950"), "the over-budget fill was debited"
    assert refused.balances[BASE_BTC] == Decimal("0.5")
    assert refused.orders[cl_ord_id].booked_qty == Decimal("0.5")
    assert refused.orders[cl_ord_id].booked_quote == Decimal("50")
    assert refused.holds[cl_ord_id].amount == Decimal("50.1")
    assert refused.executions[over.key]["applied"] is False
    codes = [entry["code"] for entry in refused.reconciliation["unresolved"]]
    assert "EXECUTION_QUANTITY_CONFLICT" in codes
    assert len(refused.ledger) == len(after_first.ledger), "a refused fill wrote ledger entries"

    # Repeating the same refused fill must not debit a second time.
    with pytest.raises(Conflict):
        await current.record_execution(over)
    repeat = await store.read(ACCOUNT)
    assert repeat.balances[QUOTE] == Decimal("950")
    assert repeat.balances[BASE_BTC] == Decimal("0.5")
    assert len(repeat.ledger) == len(after_first.ledger)


async def test_b2_two_fills_exactly_at_the_budget_are_allowed():
    """0.5@100 + 0.5@100 = 100 = budget: the boundary is inclusive."""
    current, exchange, store = await funded_service()
    receipt = await current.place(market_buy("cmd-r2-b2c"))
    cl_ord_id = receipt.cl_ord_id

    for suffix in ("1", "2"):
        execution = Execution(
            ACCOUNT,
            cl_ord_id,
            SYMBOL_BTC,
            f"exec-r2-b2c-{suffix}",
            Decimal("0.5"),
            Decimal("100"),
            Decimal("0"),
            "",
            T0 + int(suffix),
        )
        assert await current.record_execution(execution) is True

    state = await store.read(ACCOUNT)
    assert state.orders[cl_ord_id].status == "FILLED"
    assert state.orders[cl_ord_id].booked_qty == Decimal("1")
    assert state.orders[cl_ord_id].booked_quote == Decimal("100")
    assert state.balances[QUOTE] == Decimal("900")
    assert state.balances[BASE_BTC] == Decimal("1")
    assert cl_ord_id not in state.holds
    assert cl_ord_id not in state.books[BOOK_ALPHA].holds


async def test_b2_one_cent_over_the_budget_is_refused():
    """0.5@100 + 0.5001@100 = 100.01 > 100: refused, no partial booking."""
    current, exchange, store = await funded_service()
    receipt = await current.place(market_buy("cmd-r2-b2d"))
    cl_ord_id = receipt.cl_ord_id

    first = Execution(
        ACCOUNT,
        cl_ord_id,
        SYMBOL_BTC,
        "exec-r2-b2d-1",
        Decimal("0.5"),
        Decimal("100"),
        Decimal("0"),
        "",
        T0 + 1,
    )
    assert await current.record_execution(first) is True

    over = Execution(
        ACCOUNT,
        cl_ord_id,
        SYMBOL_BTC,
        "exec-r2-b2d-2",
        Decimal("0.5001"),
        Decimal("100"),
        Decimal("0"),
        "",
        T0 + 2,
    )
    with pytest.raises(Conflict):
        await current.record_execution(over)

    state = await store.read(ACCOUNT)
    assert state.orders[cl_ord_id].booked_qty == Decimal("0.5")
    assert state.balances[QUOTE] == Decimal("950")
    assert state.balances[BASE_BTC] == Decimal("0.5")


async def test_b2_a_base_settled_market_buy_keeps_the_quantity_bound():
    """The simulator settles in base: more base than requested is refused."""
    current, exchange, store = await funded_service()
    receipt = await current.place(market_buy("cmd-r2-b2e", settlement=SETTLEMENT_BASE))
    cl_ord_id = receipt.cl_ord_id

    execution = Execution(
        ACCOUNT,
        cl_ord_id,
        SYMBOL_BTC,
        "exec-r2-b2e",
        Decimal("1.1"),
        Decimal("90"),
        Decimal("0"),
        "",
        T0 + 1,
    )
    with pytest.raises(Conflict) as conflict:
        await current.record_execution(execution)

    assert str(conflict.value) == "EXECUTION_KEY_CONFLICT"
    state = await store.read(ACCOUNT)
    assert state.orders[cl_ord_id].booked_qty == Decimal("0")
    assert state.balances[QUOTE] == Decimal("1000")
    assert state.holds[cl_ord_id].amount == Decimal("100.1")


async def test_b2_a_limit_order_keeps_the_quantity_bound():
    """The quote-settled allowance never applies to a LIMIT order."""
    current, exchange, store = await funded_service()
    receipt = await current.place(limit_buy("cmd-r2-b2f"))
    cl_ord_id = receipt.cl_ord_id

    too_much = Execution(
        ACCOUNT,
        cl_ord_id,
        SYMBOL_BTC,
        "exec-r2-b2f-1",
        Decimal("1.0001"),
        Decimal("100"),
        Decimal("0"),
        "",
        T0 + 1,
    )
    with pytest.raises(Conflict):
        await current.record_execution(too_much)

    settled = Execution(
        ACCOUNT,
        cl_ord_id,
        SYMBOL_BTC,
        "exec-r2-b2f-2",
        Decimal("1"),
        Decimal("99"),
        Decimal("0"),
        "",
        T0 + 2,
    )
    assert await current.record_execution(settled) is True

    state = await store.read(ACCOUNT)
    assert state.orders[cl_ord_id].status == "FILLED"
    assert state.orders[cl_ord_id].booked_qty == Decimal("1")
    assert state.orders[cl_ord_id].booked_quote == Decimal("99")
    assert state.balances[QUOTE] == Decimal("901")
    assert state.balances[BASE_BTC] == Decimal("1")
    assert cl_ord_id not in state.holds
    assert book_ledger.conservation_mismatch(state) == {}


async def test_b2_a_terminal_observation_then_the_real_fill_settle_cleanly():
    """Observation first: the venue quantity is accepted, then the fill books it."""
    current, exchange, store = await funded_service()
    receipt = await current.place(market_buy("cmd-r2-b2g"))
    cl_ord_id = receipt.cl_ord_id

    accepted = await current.observe(
        OrderObservation(
            account_id=ACCOUNT,
            cl_ord_id=cl_ord_id,
            status="FILLED",
            cumulative_qty=Decimal("1.1"),
            broker_order_id=exchange.place_result.broker_order_id,
            observed_at_ms=T0 + 1,
        )
    )
    assert accepted is True
    pending = await store.read(ACCOUNT)
    assert pending.orders[cl_ord_id].status == "FILLED"
    assert pending.orders[cl_ord_id].observed_qty == Decimal("1.1")
    codes = [entry["code"] for entry in pending.reconciliation["unresolved"]]
    assert "UNBOOKED_EXECUTIONS" in codes
    execution = Execution(
        ACCOUNT,
        cl_ord_id,
        SYMBOL_BTC,
        "exec-r2-b2g",
        Decimal("1.1"),
        Decimal("90"),
        Decimal("0"),
        "",
        T0 + 2,
    )
    assert await current.record_execution(execution) is True

    state = await store.read(ACCOUNT)
    assert state.orders[cl_ord_id].booked_qty == Decimal("1.1")
    assert state.orders[cl_ord_id].booked_quote == Decimal("99")
    assert state.balances[QUOTE] == Decimal("901")
    assert state.balances[BASE_BTC] == Decimal("1.1")
    assert cl_ord_id not in state.holds
    assert cl_ord_id not in state.books[BOOK_ALPHA].holds
    codes = [
        entry["code"] for entry in state.reconciliation["unresolved"] if not entry.get("resolved")
    ]
    assert "UNBOOKED_EXECUTIONS" not in codes


async def test_b2_duplicate_and_stale_evidence_are_inert():
    """A repeated fill and an older observation must change nothing."""
    current, exchange, store = await funded_service()
    receipt = await current.place(market_buy("cmd-r2-b2h"))
    cl_ord_id = receipt.cl_ord_id

    execution = Execution(
        ACCOUNT,
        cl_ord_id,
        SYMBOL_BTC,
        "exec-r2-b2h",
        Decimal("0.5"),
        Decimal("100"),
        Decimal("0"),
        "",
        T0 + 1,
    )
    assert await current.record_execution(execution) is True
    assert await current.record_execution(execution) is False

    fresh = await current.observe(
        OrderObservation(
            account_id=ACCOUNT,
            cl_ord_id=cl_ord_id,
            status="PARTIALLY_FILLED",
            cumulative_qty=Decimal("0.5"),
            broker_order_id=exchange.place_result.broker_order_id,
            observed_at_ms=T0 + 5,
        )
    )
    assert fresh is True
    before = await store.read(ACCOUNT)
    assert before.orders[cl_ord_id].observed_qty == Decimal("0.5")

    stale = await current.observe(
        OrderObservation(
            account_id=ACCOUNT,
            cl_ord_id=cl_ord_id,
            status="FILLED",
            cumulative_qty=Decimal("1.1"),
            broker_order_id=exchange.place_result.broker_order_id,
            observed_at_ms=T0 + 2,
        )
    )
    after = await store.read(ACCOUNT)
    assert after.orders[cl_ord_id].observed_qty == Decimal("0.5")
    assert after.orders[cl_ord_id].observed_at_ms == T0 + 5


# ============================================================ B3 terminal guard
async def test_b3_a_partially_filled_market_buy_blocks_the_same_book_opposite():
    """While a reservation remains, the opposite side in that book is refused.

    After the terminal observation releases both layers, the opposite side is
    admitted again - the release is economic, not cosmetic.
    """
    current, exchange, store = await funded_service()
    receipt = await current.place(market_buy("cmd-r2-b3a"))
    cl_ord_id = receipt.cl_ord_id

    execution = Execution(
        ACCOUNT,
        cl_ord_id,
        SYMBOL_BTC,
        "exec-r2-b3a",
        Decimal("0.5"),
        Decimal("100"),
        Decimal("0"),
        "",
        T0 + 1,
    )
    assert await current.record_execution(execution) is True

    with pytest.raises(RiskRejected) as blocked:
        await current.place(
            PlaceOrderCommand(
                command_id="cmd-r2-b3a-sell",
                account_id=ACCOUNT,
                symbol=SYMBOL_BTC,
                base_asset=BASE_BTC,
                quote_asset=QUOTE,
                side="SELL",
                qty=Decimal("0.5"),
                price=Decimal("100"),
                deployment_id="dep-r2",
                order_type="MARKET",
                time_in_force="GTC",
                book_id=BOOK_ALPHA,
                price_source=PRICE_SOURCE_MARKET_TICKER,
                price_at_ms=T0,
                settlement=SETTLEMENT_QUOTE,
                decision_id="dec-r2-b3a-sell",
            )
        )
    assert str(blocked.value) == "BOOK_OPPOSITE_ORDER_IN_FLIGHT"

    assert await current.observe(
        OrderObservation(
            account_id=ACCOUNT,
            cl_ord_id=cl_ord_id,
            status="FILLED",
            cumulative_qty=Decimal("0.5"),
            broker_order_id=exchange.place_result.broker_order_id,
            observed_at_ms=T0 + 2,
        )
    )
    released = await store.read(ACCOUNT)
    assert cl_ord_id not in released.holds
    assert cl_ord_id not in released.books[BOOK_ALPHA].holds

    admitted = await current.place(
        PlaceOrderCommand(
            command_id="cmd-r2-b3a-sell2",
            account_id=ACCOUNT,
            symbol=SYMBOL_BTC,
            base_asset=BASE_BTC,
            quote_asset=QUOTE,
            side="SELL",
            qty=Decimal("0.5"),
            price=Decimal("100"),
            deployment_id="dep-r2",
            order_type="MARKET",
            time_in_force="GTC",
            book_id=BOOK_ALPHA,
            price_source=PRICE_SOURCE_MARKET_TICKER,
            price_at_ms=T0,
            settlement=SETTLEMENT_QUOTE,
            decision_id="dec-r2-b3a-sell2",
        )
    )
    assert admitted.status == "CONFIRMED"


# ====================================================== B4 legacy attribution
def test_b4_a_legacy_reservation_is_mirrored_into_unallocated():
    """Funds still parked in UNALLOCATED must show the reservation there too."""
    state = decode_state(legacy_snapshot())
    assert list(state.books) == [UNALLOCATED]
    assert state.holds["qts-legacy"].amount == Decimal("100.1")
    unallocated = state.books[UNALLOCATED]
    assert book_ledger.book_hold_amount(unallocated, QUOTE) == Decimal("100.1")
    assert book_ledger.book_available(unallocated, QUOTE) == Decimal("899.9")
    assert book_ledger.conservation_mismatch(state) == {}


def test_b4_the_mirror_is_idempotent_and_never_invents_a_strategy_book():
    """Migrating twice must not reserve the same money twice."""
    state = decode_state(legacy_snapshot())
    book_ledger.migrate_book_ledger(state)
    book_ledger.migrate_book_ledger(state)
    assert book_ledger.book_hold_amount(state.books[UNALLOCATED], QUOTE) == Decimal("100.1")
    assert list(state.books) == [UNALLOCATED], "migration invented a strategy book"


def test_b4_a_missing_strategy_book_stays_unattributed():
    """An order pointing at a book that does not exist keeps blocking, silently."""
    snapshot = legacy_snapshot(command_extra={"book_id": "book:ghost"})
    state = decode_state(snapshot)
    assert "book:ghost" not in state.books, "migration created the missing book"
    marker = state.reconciliation["book_ledger"]
    entry = marker["unattributed_orders"]["qts-legacy"]
    assert entry["requested_book_id"] == "book:ghost"
    assert entry["reason"] == "ORDER_BOOK_NOT_FOUND"
    assert "BOOK_ATTRIBUTION_UNKNOWN" in book_ledger.book_reasons(state)


async def test_b4_an_allocation_cannot_spend_the_mirrored_reservation():
    """Reserved cash in UNALLOCATED is not available for allocation."""
    store = MemoryTradingStore()
    current = build_service(store=store, clock=FixedClock())
    await _seed_legacy(store)

    with pytest.raises(TradingError) as refused:
        await current.allocate(
            "cmd-r2-over",
            ACCOUNT,
            BOOK_ALPHA,
            QUOTE,
            Decimal("1000"),
            actor="repro",
            reason="b4 over-allocation",
        )
    assert str(refused.value) == "ALLOCATION_INSUFFICIENT_UNALLOCATED"

    untouched = await store.read(ACCOUNT)
    assert untouched.holds["qts-legacy"].amount == Decimal("100.1")
    assert untouched.books[UNALLOCATED].balances[QUOTE] == Decimal("1000")
    assert book_quote(untouched, BOOK_ALPHA) == Decimal("0")

    # The unreserved part may still move: 1000 - 100.1 = 899.9
    await current.allocate(
        "cmd-r2-free",
        ACCOUNT,
        BOOK_ALPHA,
        QUOTE,
        Decimal("899.9"),
        actor="repro",
        reason="b4 free part",
    )
    state = await store.read(ACCOUNT)
    assert state.books[UNALLOCATED].balances[QUOTE] == Decimal("100.1")
    assert state.books[BOOK_ALPHA].balances[QUOTE] == Decimal("899.9")
    assert book_ledger.book_available(state.books[UNALLOCATED], QUOTE) == Decimal("0")
    assert book_ledger.conservation_mismatch(state) == {}


def book_quote(state, book_id: str) -> Decimal:
    """USDT held in one book; a book that was never registered holds nothing."""
    book = state.books.get(book_id)
    if book is None:
        return Decimal("0")
    return book.balances.get(QUOTE, Decimal("0"))


async def test_b4_a_deferred_allocation_failure_is_atomic_and_observable():
    """Control defers the request; the execution side refuses it with a trace.

    Hand-computed: the account holds 1000 USDT, all of it in UNALLOCATED, so a
    5000 allocation can never succeed. Nothing may move, and the refusal must
    be visible both on the command record and in the event log.
    """
    store = MemoryTradingStore()
    clock = FixedClock()
    execution = build_service(store=store, clock=clock)
    await execution.recover(venue_snapshot())
    control = build_service(store=store, clock=clock, defer_send=True)

    receipt = await control.allocate(
        "cmd-r2-defer-over",
        ACCOUNT,
        BOOK_ALPHA,
        QUOTE,
        Decimal("5000"),
        actor="repro",
        reason="b4 deferred over-allocation",
    )
    assert receipt.status == "QUEUED"

    queued = await store.read(ACCOUNT)
    assert queued.books[UNALLOCATED].balances[QUOTE] == Decimal("1000")
    assert book_quote(queued, BOOK_ALPHA) == Decimal("0")

    receipts = await execution.resume_queued(ACCOUNT)
    assert len(receipts) == 1
    assert receipts[0].status == "FAILED"
    assert "ALLOCATION_INSUFFICIENT" in receipts[0].error

    after = await store.read(ACCOUNT)
    assert after.books[UNALLOCATED].balances[QUOTE] == Decimal("1000"), "allocation was applied"
    assert book_quote(after, BOOK_ALPHA) == Decimal("0")
    assert after.commands["cmd-r2-defer-over"].status == "FAILED"
    assert len(after.events) > len(queued.events), "the refusal left no trace"

    # Draining again must not retry a settled (failed) command.
    assert await execution.resume_queued(ACCOUNT) == []


async def test_b4_the_control_process_cannot_execute_commands():
    """A Control assembly records commands; it never applies them."""
    store = MemoryTradingStore()
    control = build_service(store=store, clock=FixedClock(), defer_send=True)
    await control.recover(venue_snapshot())

    with pytest.raises(NotReady) as refused:
        await control.resume_queued(ACCOUNT)
    assert str(refused.value) == "CONTROL_PROCESS_CANNOT_EXECUTE"


async def _seed_legacy(store):
    async with store.transaction(ACCOUNT) as target:
        source = decode_state(legacy_snapshot())
        for name in _STATE_FIELDS:
            setattr(target, name, getattr(source, name))


# ======================================================== B5 joint backtest run
def _identity(symbol: str, base: str) -> dict:
    return {
        "venue": "binance",
        "market_type": "SPOT",
        "symbol": symbol,
        "base_asset": base,
        "quote_asset": QUOTE,
        "interval": "1m",
        "source": "fixture",
        "version": "1",
    }


def _flat_bars(count: int, price: str) -> list[dict]:
    """Flat bars at one price, so a fill price can never mask an error."""
    rows = []
    for index in range(count):
        opened = index * 60_000
        rows.append(
            {
                "open_time_ms": opened,
                "close_time_ms": opened + 60_000,
                "available_at_ms": opened + 60_000,
                "open": price,
                "high": price,
                "low": price,
                "close": price,
                "volume": "10",
            }
        )
    return rows


def _joint_datasets():
    return [
        freeze_dataset(_identity(SYMBOL_BTC, BASE_BTC), _flat_bars(4, "100")),
        freeze_dataset(_identity(SYMBOL_ETH, BASE_ETH), _flat_bars(4, "50")),
    ]


def _joint_strategy(context, bar):
    """alpha buys 1 BTC and holds; beta round-trips 2 ETH."""
    symbol = bar["symbol"]
    if symbol == SYMBOL_BTC:
        if context.bar_index != 0:
            return []
        return [
            StrategySignal(
                symbol=SYMBOL_BTC,
                side="BUY",
                qty=Decimal("1"),
                order_type="MARKET",
                strategy_id="alpha",
            )
        ]
    if context.bar_index == 0:
        return [
            StrategySignal(
                symbol=SYMBOL_ETH,
                side="BUY",
                qty=Decimal("2"),
                order_type="MARKET",
                strategy_id="beta",
            )
        ]
    if context.bar_index == 1:
        return [
            StrategySignal(
                symbol=SYMBOL_ETH,
                side="SELL",
                qty=Decimal("2"),
                order_type="MARKET",
                strategy_id="beta",
            )
        ]
    return []


def _run_joint(**overrides):
    values = dict(
        datasets=_joint_datasets(),
        strategy=_joint_strategy,
        initial_cash=Decimal("1000"),
        fee_bps=Decimal("10"),
        slippage_bps=Decimal("0"),
        strategy_books={"alpha": BOOK_ALPHA, "beta": BOOK_BETA},
        initial_allocations={BOOK_ALPHA: Decimal("700"), BOOK_BETA: Decimal("300")},
    )
    values.update(overrides)
    return EventBacktestRuntime(**values).run()


def _book(result, book_id):
    for snapshot in result.books:
        if snapshot["book_id"] == book_id:
            return snapshot
    raise AssertionError(f"missing book snapshot: {book_id}")


def test_b5_a_joint_backtest_reports_every_book_equity_and_budget():
    """Hand-computed joint run (fee_rate = 10 bps = 0.001, flat prices).

    alpha: 700 - 100 - 0.10 fee = 599.90 USDT + 1 BTC @ 100 -> equity 699.90
    beta : 300 - 100 - 0.10 fee = 199.90 USDT + 2 ETH @ 50
           -> sell 2 ETH: 199.90 + 100 - 0.10 fee = 299.80, no ETH left
           equity 299.80
    UNALLOCATED: 0
    total = 999.70 = cash 899.70 + 1 BTC * 100; fees = 0.30
    """
    result = _run_joint()

    assert sorted(entry["book_id"] for entry in result.books) == [
        UNALLOCATED,
        BOOK_ALPHA,
        BOOK_BETA,
    ]
    alpha = _book(result, BOOK_ALPHA)
    beta = _book(result, BOOK_BETA)
    unallocated = _book(result, UNALLOCATED)

    assert alpha["equity"] is not None, "alpha valuation is missing"
    assert beta["equity"] is not None, "beta valuation is missing"
    assert Decimal(alpha["equity"]) == Decimal("699.90")
    assert Decimal(beta["equity"]) == Decimal("299.80")
    assert Decimal(unallocated["equity"]) == Decimal("0")

    assert Decimal(alpha["balances"]["USDT"]) == Decimal("599.90")
    assert Decimal(alpha["balances"]["BTC"]) == Decimal("1")
    assert Decimal(beta["balances"]["USDT"]) == Decimal("299.80")
    assert Decimal(beta["balances"].get("ETH", "0")) == Decimal("0")

    # budget == available of the book's own balances
    assert Decimal(alpha["available"]["USDT"]) == Decimal("599.90")
    assert Decimal(beta["available"]["USDT"]) == Decimal("299.80")
    assert Decimal(alpha["held"].get("USDT", "0")) == Decimal("0")

    assert result.fees == Decimal("0.30")
    assert result.cash == Decimal("899.70")
    assert result.positions[SYMBOL_BTC] == Decimal("1")
    assert result.positions[SYMBOL_ETH] == Decimal("0")
    assert sum(Decimal(entry["equity"]) for entry in result.books) == result.cash + Decimal("100")


def test_b5_each_strategy_stays_inside_its_own_budget():
    """alpha may not spend beta's money: the book layer refuses the intent."""
    overspending = _run_joint(
        initial_allocations={BOOK_ALPHA: Decimal("100"), BOOK_BETA: Decimal("300")},
    )
    # alpha's own book only holds 100 USDT, so its 1 BTC intent (100.10 to
    # reserve) has to be refused there; beta's 300 are untouched by that refusal.
    btc_fills = [fill for fill in overspending.fills if fill["symbol"] == SYMBOL_BTC]
    assert not btc_fills, "alpha bought BTC with money it was never allocated"
    assert overspending.rejections or overspending.execution_rejections
    alpha = _book(overspending, BOOK_ALPHA)
    assert Decimal(alpha["balances"]["USDT"]) == Decimal("100")
    assert Decimal(alpha["balances"].get("BTC", "0")) == Decimal("0")
    # beta still trades inside its own budget: 2 ETH bought, then sold.
    eth_fills = [fill for fill in overspending.fills if fill["symbol"] == SYMBOL_ETH]
    assert len(eth_fills) == 2

    funded = _run_joint(initial_allocations={BOOK_ALPHA: Decimal("700"), BOOK_BETA: Decimal("300")})
    assert len(funded.fills) == 3
    alpha = _book(funded, BOOK_ALPHA)
    beta = _book(funded, BOOK_BETA)
    assert Decimal(alpha["balances"]["USDT"]) == Decimal("599.90")
    assert Decimal(beta["balances"]["USDT"]) == Decimal("299.80")


def test_b5_a_joint_run_refuses_implicit_allocations():
    """A joint run must state the split explicitly."""
    with pytest.raises(ValueError) as refused:
        _run_joint(initial_allocations=None)
    assert "explicit initial allocations" in str(refused.value)


def test_b5_a_joint_run_refuses_an_unconfigured_book_allocation():
    """Money may only be allocated to a book the run actually configured."""
    with pytest.raises(ValueError) as refused:
        _run_joint(initial_allocations={BOOK_ALPHA: Decimal("600"), "book:ghost": Decimal("400")})
    assert "unconfigured book" in str(refused.value)


def test_b5_a_joint_run_refuses_over_allocated_cash():
    """The split may not exceed the declared initial cash."""
    with pytest.raises(ValueError) as refused:
        _run_joint(initial_allocations={BOOK_ALPHA: Decimal("900"), BOOK_BETA: Decimal("300")})
    assert "exceed" in str(refused.value)


def test_b5_a_joint_run_refuses_a_non_positive_allocation():
    """A zero or negative allocation is refused, never treated as 'all'."""
    with pytest.raises(ValueError) as refused:
        _run_joint(initial_allocations={BOOK_ALPHA: Decimal("0"), BOOK_BETA: Decimal("300")})
    assert "ALLOCATION_AMOUNT_INVALID" in str(refused.value)


def test_b5_a_joint_run_refuses_an_invalid_strategy_book():
    """``book:{strategy_id}`` is the only accepted shape for a strategy book."""
    with pytest.raises(ValueError) as refused:
        _run_joint(strategy_books={"alpha": "alpha"})
    assert "Invalid book id" in str(refused.value)


def test_b5_a_signal_without_a_resolvable_book_is_rejected():
    """A joint signal must name its strategy; an unknown one is refused too."""

    def no_strategy(context, bar):
        if bar["symbol"] == SYMBOL_BTC and context.bar_index == 0:
            return [
                StrategySignal(
                    symbol=SYMBOL_BTC,
                    side="BUY",
                    qty=Decimal("1"),
                    order_type="MARKET",
                )
            ]
        return []

    def unknown_strategy(context, bar):
        if bar["symbol"] == SYMBOL_BTC and context.bar_index == 0:
            return [
                StrategySignal(
                    symbol=SYMBOL_BTC,
                    side="BUY",
                    qty=Decimal("1"),
                    order_type="MARKET",
                    strategy_id="ghost",
                )
            ]
        return []

    missing = _run_joint(strategy=no_strategy)
    assert missing.rejections.get("STRATEGY_ID_REQUIRED") == 1
    assert not missing.fills

    unknown = _run_joint(strategy=unknown_strategy)
    assert unknown.rejections.get("UNKNOWN_STRATEGY_BOOK") == 1
    assert not unknown.fills


def test_b5_duplicate_book_configuration_is_refused():
    """Two strategies sharing one book would merge their budgets: refused."""
    with pytest.raises(ValueError) as refused:
        _run_joint(
            strategy_books={"alpha": BOOK_ALPHA, "beta": BOOK_ALPHA},
            initial_allocations={BOOK_ALPHA: Decimal("1000")},
        )
    assert "book" in str(refused.value).lower()


def test_b5_the_single_strategy_path_still_runs_and_reports_its_book():
    """Compatibility: one strategy keeps the legacy simulator book.

    Legacy input carries no strategy id at all, so the run must fall back to the
    simulator book instead of asking for a book partition.
    """

    def legacy_strategy(context, bar):
        if bar["symbol"] == SYMBOL_BTC and context.bar_index == 0:
            return [
                StrategySignal(
                    symbol=SYMBOL_BTC,
                    side="BUY",
                    qty=Decimal("1"),
                    order_type="MARKET",
                )
            ]
        return []

    result = EventBacktestRuntime(
        datasets=_joint_datasets(),
        strategy=legacy_strategy,
        initial_cash=Decimal("1000"),
        fee_bps=Decimal("10"),
    ).run()
    assert sorted(entry["book_id"] for entry in result.books) == [UNALLOCATED, "book:backtest"]
    assert len(result.fills) == 1, "the legacy path did not trade"
    legacy = _book(result, "book:backtest")
    # 1000 - 100 - 0.10 fee = 899.90 cash, and the 1 BTC still held is valued at 100.
    assert result.cash == Decimal("899.9")
    assert Decimal(legacy["balances"]["BTC"]) == Decimal("1")
    assert Decimal(legacy["equity"]) == Decimal("999.9")


def test_b5_the_manifest_hash_covers_the_book_configuration():
    """The run identity must change when the book split changes."""
    baseline = _run_joint()
    identical = _run_joint()
    assert baseline.manifest_hash == identical.manifest_hash

    moved = _run_joint(initial_allocations={BOOK_ALPHA: Decimal("600"), BOOK_BETA: Decimal("400")})
    assert moved.manifest_hash != baseline.manifest_hash
