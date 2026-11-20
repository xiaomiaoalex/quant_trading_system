"""Phase 2 round-2 fixes: B1 unified spec, B2 quote-settled MARKET, B4 migration, B5 joint backtest.

These tests are written by the engineer against the contract, not against the
implementation: every expected number is computed by hand in the test body.
"""

from __future__ import annotations

import inspect
from decimal import Decimal
from pathlib import Path

import pytest

from trader.trading import books as book_ledger
from trader.trading.backtest import EventBacktestRuntime, StrategySignal
from trader.trading.datasets import freeze_dataset
from trader.trading.models import (
    SETTLEMENT_BASE,
    SETTLEMENT_QUOTE,
    Conflict,
    ExchangeOrderResult,
    ExchangeOutcome,
    Execution,
    OrderObservation,
    PlaceOrderCommand,
    ReconcileSnapshot,
    TradingError,
)
from trader.trading.order_spec import (
    PRICE_SOURCE_MARKET_TICKER,
    PRICE_SOURCE_SIGNAL_LIMIT,
    build_order_spec,
)
from trader.trading.risk import RiskPolicy
from trader.trading.service import TradingService
from trader.trading.store import MemoryTradingStore, decode_state

ACCOUNT = "acct-round2"
SYMBOL = "BTCUSDT"
BASE = "BTC"
QUOTE = "USDT"
T0 = 1_700_000_000_000
FEE_RATE = Decimal("0.001")
BOOK_ALPHA = "book:alpha"
BOOK_BETA = "book:beta"
UNALLOCATED = book_ledger.UNALLOCATED


class FixedClock:
    def __init__(self, now: int = T0) -> None:
        self.now = now

    def __call__(self) -> int:
        return self.now


class RecordingExchange:
    def __init__(self) -> None:
        self.place_calls: list[tuple[PlaceOrderCommand, str]] = []
        self.place_result = ExchangeOrderResult(ExchangeOutcome.ACCEPTED, "b-1", "OPEN")

    async def place_order(self, command: PlaceOrderCommand, cl_ord_id: str) -> ExchangeOrderResult:
        self.place_calls.append((command, cl_ord_id))
        return self.place_result

    async def cancel_order(self, account_id, cl_ord_id, command_id, symbol):
        return ExchangeOrderResult(ExchangeOutcome.ACCEPTED, "b-1", "OPEN")

    async def query_order(self, account_id, cl_ord_id, symbol):
        return ExchangeOrderResult(ExchangeOutcome.NOT_FOUND)


def build_service(store=None, exchange=None, clock=None, policy=None):
    return TradingService(
        store=store if store is not None else MemoryTradingStore(),
        exchange=exchange if exchange is not None else RecordingExchange(),
        policy=policy if policy is not None else RiskPolicy(fee_rate=FEE_RATE),
        clock=clock if clock is not None else FixedClock(),
    )


def venue_snapshot(balances=None, *, checked=T0, generation=1, marks=None):
    return ReconcileSnapshot(
        account_id=ACCOUNT,
        checked_at_ms=checked,
        generation=generation,
        balances=(
            balances if balances is not None else {QUOTE: Decimal("1000"), BASE: Decimal("0")}
        ),
        marks=marks or {SYMBOL: {"price": Decimal("100"), "at_ms": checked}},
    )


def market_command(command_id: str, **overrides) -> PlaceOrderCommand:
    values = dict(
        command_id=command_id,
        account_id=ACCOUNT,
        symbol=SYMBOL,
        base_asset=BASE,
        quote_asset=QUOTE,
        side="BUY",
        qty=Decimal("1"),
        price=Decimal("100"),
        deployment_id="dep-round2",
        decision_id=f"dec-{command_id}",
        order_type="MARKET",
        time_in_force="GTC",
        book_id=BOOK_ALPHA,
        price_source=PRICE_SOURCE_MARKET_TICKER,
        price_at_ms=T0,
    )
    values.update(overrides)
    return PlaceOrderCommand(**values)


async def funded_service(amount: str = "1000"):
    store = MemoryTradingStore()
    current = build_service(store=store)
    await current.recover(venue_snapshot())
    await current.allocate(
        "alloc-round2", ACCOUNT, BOOK_ALPHA, QUOTE, Decimal(amount), reason="round 2 setup"
    )
    return current, store


# =============================================================== B1 unified spec
def test_production_code_never_builds_a_place_order_command_outside_the_factory():
    """Every business order is produced by ``order_spec``.

    ``store.decode_state`` rebuilds a persisted command; that is a decode, not
    a new order, and it is the only other place allowed to name the DTO.
    """
    root = Path(__file__).resolve().parents[2]
    allowed = {"trader/trading/order_spec.py", "trader/trading/store.py"}
    offenders: list[str] = []
    for path in (root / "trader").rglob("*.py"):
        relative = path.relative_to(root).as_posix()
        if relative.startswith("trader/tests/") or relative in allowed:
            continue
        if "PlaceOrderCommand(" in path.read_text(encoding="utf-8"):
            offenders.append(relative)
    assert offenders == [], f"orders must be built by order_spec: {offenders}"


def test_the_spec_factory_is_the_only_place_that_knows_the_command_shape():
    assert "metadata" not in inspect.signature(build_order_spec).parameters
    assert "metadata" not in inspect.signature(PlaceOrderCommand).parameters


def test_api_and_backtest_build_their_command_through_the_factory():
    api_source = (Path(__file__).resolve().parents[2] / "trader/api/routes/trading.py").read_text(
        encoding="utf-8"
    )
    backtest_source = (
        Path(__file__).resolve().parents[2] / "trader/trading/backtest.py"
    ).read_text(encoding="utf-8")
    assert "build_order_spec(" in api_source
    assert "build_order_spec(" in backtest_source
    assert "PlaceOrderCommand(" not in api_source
    assert "PlaceOrderCommand(" not in backtest_source


def test_a_simulated_signal_must_state_its_order_type():
    with pytest.raises(TypeError):
        StrategySignal(symbol=SYMBOL, side="BUY", qty=Decimal("1"))  # type: ignore[call-arg]


# =========================================================== B2 quote settlement
async def test_market_buy_may_overfill_base_inside_the_quote_budget():
    """1.1 base at 90 spends 99 of the 100 USDT budget: a legal settlement."""
    current, store = await funded_service()
    receipt = await current.place(market_command("cmd-b2"))
    cl_ord_id = receipt.cl_ord_id
    assert (await store.read(ACCOUNT)).holds[cl_ord_id].amount == Decimal("100.1")

    applied = await current.record_execution(
        Execution(
            ACCOUNT,
            cl_ord_id,
            SYMBOL,
            "exec-b2",
            Decimal("1.1"),
            Decimal("90"),
            Decimal("0"),
            "",
            T0 + 1,
        )
    )
    assert applied is True
    state = await store.read(ACCOUNT)
    order = state.orders[cl_ord_id]
    assert order.booked_qty == Decimal("1.1")
    assert order.booked_quote == Decimal("99")
    assert state.balances[QUOTE] == Decimal("901")
    assert state.balances[BASE] == Decimal("1.1")
    assert state.positions[SYMBOL] == Decimal("1.1")
    assert state.books[BOOK_ALPHA].positions[SYMBOL] == Decimal("1.1")
    # 100.1 reserved - 99 actually spent = 1.1 of real remaining budget/buffer.
    assert state.holds[cl_ord_id].amount == Decimal("1.1")
    assert state.books[BOOK_ALPHA].holds[cl_ord_id].amount == Decimal("1.1")
    assert order.status == "PARTIALLY_FILLED"
    assert book_ledger.conservation_mismatch(state) == {}

    # Only the venue's terminal observation releases the remaining residue.
    assert (
        await current.observe(
            OrderObservation(ACCOUNT, cl_ord_id, "FILLED", Decimal("1.1"), "b-1", T0 + 2)
        )
        is True
    )
    settled = await store.read(ACCOUNT)
    assert cl_ord_id not in settled.holds
    assert cl_ord_id not in settled.books[BOOK_ALPHA].holds


async def test_a_quote_settled_market_buy_stays_in_flight_for_opposite_orders():
    """The order is non-terminal even after the base is over-covered.

    A quote-settled MARKET BUY may still spend the rest of its quote budget, so
    the same book must keep refusing an opposite order until the venue says the
    order is terminal.
    """
    from trader.trading.risk import RiskRejected

    current, store = await funded_service()
    receipt = await current.place(market_command("cmd-b2-inflight"))
    await current.record_execution(
        Execution(
            ACCOUNT,
            receipt.cl_ord_id,
            SYMBOL,
            "exec-b2-inflight",
            Decimal("1.1"),
            Decimal("90"),
            Decimal("0"),
            "",
            T0 + 1,
        )
    )
    state = await store.read(ACCOUNT)
    assert state.orders[receipt.cl_ord_id].status == "PARTIALLY_FILLED"
    assert state.holds[receipt.cl_ord_id].amount == Decimal("1.1")
    assert state.books[BOOK_ALPHA].holds[receipt.cl_ord_id].amount == Decimal("1.1")

    with pytest.raises(RiskRejected) as refused:
        await current.place(
            market_command(
                "cmd-b2-opposite",
                side="SELL",
                qty=Decimal("1"),
                price=Decimal("100"),
            )
        )
    assert "BOOK_OPPOSITE_ORDER_IN_FLIGHT" in str(refused.value)
    state = await store.read(ACCOUNT)
    assert "cmd-b2-opposite" not in state.commands


async def test_market_buy_may_not_spend_more_than_the_quote_budget():
    """1.1 base at 100 spends 110 of a 100 USDT budget: refused, not booked."""
    current, store = await funded_service()
    receipt = await current.place(market_command("cmd-b2-budget"))
    with pytest.raises(Conflict):
        await current.record_execution(
            Execution(
                ACCOUNT,
                receipt.cl_ord_id,
                SYMBOL,
                "exec-budget",
                Decimal("1.1"),
                Decimal("100"),
                Decimal("0"),
                "",
                T0 + 1,
            )
        )
    state = await store.read(ACCOUNT)
    assert state.orders[receipt.cl_ord_id].booked_qty == Decimal("0")
    assert state.balances[QUOTE] == Decimal("1000")


async def test_limit_orders_keep_the_exact_base_boundary():
    """The quote-settled allowance never applies to a LIMIT order."""
    current, store = await funded_service()
    receipt = await current.place(
        market_command(
            "cmd-limit",
            order_type="LIMIT",
            price_source=PRICE_SOURCE_SIGNAL_LIMIT,
        )
    )
    with pytest.raises(Conflict):
        await current.record_execution(
            Execution(
                ACCOUNT,
                receipt.cl_ord_id,
                SYMBOL,
                "exec-limit",
                Decimal("1.1"),
                Decimal("90"),
                Decimal("0"),
                "",
                T0 + 1,
            )
        )
    state = await store.read(ACCOUNT)
    assert state.orders[receipt.cl_ord_id].booked_qty == Decimal("0")


async def test_a_market_filled_observation_may_carry_the_venue_base_quantity():
    current, store = await funded_service()
    receipt = await current.place(market_command("cmd-b2-obs"))
    accepted = await current.observe(
        OrderObservation(ACCOUNT, receipt.cl_ord_id, "FILLED", Decimal("1.1"), "b-1", T0 + 2)
    )
    assert accepted is True
    state = await store.read(ACCOUNT)
    assert state.orders[receipt.cl_ord_id].status == "FILLED"
    assert state.orders[receipt.cl_ord_id].observed_qty == Decimal("1.1")
    assert receipt.cl_ord_id not in state.holds
    assert receipt.cl_ord_id not in state.books[BOOK_ALPHA].holds


# ============================================================== B4 legacy hold
def legacy_state() -> dict:
    return {
        "account_id": ACCOUNT,
        "status": "READY",
        "version": 3,
        "balances": {QUOTE: "1000", BASE: "0"},
        "positions": {},
        "external_locked": {},
        "orders": {
            "qts-legacy": {
                "cl_ord_id": "qts-legacy",
                "command": {
                    "command_id": "legacy-command",
                    "account_id": ACCOUNT,
                    "symbol": SYMBOL,
                    "base_asset": BASE,
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
                },
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
            "marks": {SYMBOL: {"price": Decimal("100"), "at_ms": T0}},
        },
    }


def test_a_legacy_hold_is_mirrored_into_unallocated_only():
    state = decode_state(legacy_state())
    assert list(state.books) == [UNALLOCATED]
    assert state.holds["qts-legacy"].amount == Decimal("100.1")
    assert book_ledger.book_hold_amount(state.books[UNALLOCATED], QUOTE) == Decimal("100.1")
    assert book_ledger.book_available(state.books[UNALLOCATED], QUOTE) == Decimal("899.9")
    assert book_ledger.conservation_mismatch(state) == {}


def test_the_migration_mirror_is_idempotent_and_never_invents_a_strategy_book():
    first = decode_state(legacy_state())
    second = decode_state(json_value_of(first))
    assert book_ledger.book_hold_amount(second.books[UNALLOCATED], QUOTE) == Decimal("100.1")
    assert list(second.books) == [UNALLOCATED]
    assert second.holds["qts-legacy"].amount == Decimal("100.1")


def json_value_of(state):
    from trader.trading.models import json_value

    return json_value(state)


async def test_an_allocation_cannot_spend_the_reserved_unallocated_funds():
    store = MemoryTradingStore()
    current = build_service(store=store)
    async with store.transaction(ACCOUNT) as target:
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
            setattr(target, name, getattr(decode_state(legacy_state()), name))
    with pytest.raises(TradingError) as refused:
        await current.allocate(
            "cmd-b4-over", ACCOUNT, BOOK_ALPHA, QUOTE, Decimal("1000"), reason="over"
        )
    assert str(refused.value) == "ALLOCATION_INSUFFICIENT_UNALLOCATED"
    state = await store.read(ACCOUNT)
    assert state.holds["qts-legacy"].amount == Decimal("100.1")
    assert state.books[UNALLOCATED].balances[QUOTE] == Decimal("1000")
    assert state.books[BOOK_ALPHA].balances.get(QUOTE, Decimal("0")) == Decimal("0")

    await current.allocate(
        "cmd-b4-free", ACCOUNT, BOOK_ALPHA, QUOTE, Decimal("899.9"), reason="free part"
    )
    state = await store.read(ACCOUNT)
    assert state.books[UNALLOCATED].balances[QUOTE] == Decimal("100.1")
    assert state.books[BOOK_ALPHA].balances[QUOTE] == Decimal("899.9")
    assert book_ledger.book_available(state.books[UNALLOCATED], QUOTE) == Decimal("0")
    assert book_ledger.conservation_mismatch(state) == {}


# ======================================================== B5 joint multi-book run
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


def _bars(count: int = 4, price: str = "100") -> list[dict]:
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


def joint_datasets():
    return [
        freeze_dataset(_identity("BTCUSDT", "BTC"), _bars()),
        freeze_dataset(_identity("ETHUSDT", "ETH"), _bars()),
    ]


def joint_strategy(context, bar):
    """alpha holds BTC; beta round-trips ETH and ends holding only quote."""
    if bar["symbol"] == "BTCUSDT":
        if context.bar_index != 0:
            return []
        return [
            StrategySignal(
                symbol="BTCUSDT",
                side="BUY",
                qty=Decimal("1"),
                order_type="MARKET",
                strategy_id="alpha",
            )
        ]
    if context.bar_index == 0:
        return [
            StrategySignal(
                symbol="ETHUSDT",
                side="BUY",
                qty=Decimal("2"),
                order_type="MARKET",
                strategy_id="beta",
            )
        ]
    if context.bar_index == 1:
        return [
            StrategySignal(
                symbol="ETHUSDT",
                side="SELL",
                qty=Decimal("2"),
                order_type="MARKET",
                strategy_id="beta",
            )
        ]
    return []


def run_joint(**overrides):
    values = dict(
        datasets=joint_datasets(),
        strategy=joint_strategy,
        initial_cash=Decimal("1200"),
        fee_bps=Decimal("10"),
        slippage_bps=Decimal("0"),
        strategy_books={"alpha": BOOK_ALPHA, "beta": BOOK_BETA},
        initial_allocations={BOOK_ALPHA: Decimal("600"), BOOK_BETA: Decimal("400")},
    )
    values.update(overrides)
    return EventBacktestRuntime(**values).run()


def test_a_joint_backtest_reports_every_book_equity_and_budget():
    """One book holds a valued base asset; the other holds only quote.

    alpha: 600 - 100 - 0.1 fee = 499.9 USDT + 1 BTC * 100 = 599.9
    beta : 400 - 200 - 0.2 fee + 200 - 0.2 fee = 399.6 USDT, no ETH left
    unallocated: 1200 - 600 - 400 = 200 USDT
    account: 1099.5 USDT + 1 BTC * 100 = 1199.5 = 599.9 + 399.6 + 200
    """
    result = run_joint()
    books = {item["book_id"]: item for item in result.books}
    assert set(books) == {BOOK_ALPHA, BOOK_BETA, UNALLOCATED}

    alpha = books[BOOK_ALPHA]
    assert {asset: Decimal(value) for asset, value in alpha["balances"].items()} == {
        QUOTE: Decimal("499.9"),
        "BTC": Decimal("1"),
    }
    assert alpha["positions"] == {"BTCUSDT": "1"}
    assert Decimal(alpha["equity"]) == Decimal("599.9")
    assert alpha["valuation_complete"] is True
    assert {asset: Decimal(value) for asset, value in alpha["available"].items()} == {
        QUOTE: Decimal("499.9"),
        "BTC": Decimal("1"),
    }

    beta = books[BOOK_BETA]
    assert {asset: Decimal(value) for asset, value in beta["balances"].items()} == {
        QUOTE: Decimal("399.6"),
        "ETH": Decimal("0"),
    }
    assert beta["positions"] == {"ETHUSDT": "0"}
    assert Decimal(beta["equity"]) == Decimal("399.6")
    assert beta["valuation_complete"] is True
    # beta never needed the ETH mark: it holds only quote at the end.
    assert set(beta["valuations"]) == {QUOTE}
    assert alpha["valuations"]["BTC"] == {
        "price": "100",
        "at_ms": 240_000,
        "source": "MARK",
    }

    unallocated = books[UNALLOCATED]
    assert unallocated["balances"][QUOTE] == "200"
    assert Decimal(unallocated["equity"]) == Decimal("200")

    equities = [Decimal(item["equity"]) for item in result.books]
    account_equity = result.cash + Decimal("1") * Decimal("100")
    assert sum(equities) == account_equity == Decimal("1199.5")


def test_a_joint_backtest_keeps_each_strategy_inside_its_own_budget():
    """alpha may not spend beta's money: the book layer refuses it."""

    def greedy(context, bar):
        if context.bar_index != 0:
            return []
        if bar["symbol"] == "BTCUSDT":
            return [
                StrategySignal(
                    symbol="BTCUSDT",
                    side="BUY",
                    qty=Decimal("10"),
                    order_type="MARKET",
                    strategy_id="alpha",
                )
            ]
        return []

    result = run_joint(strategy=greedy)
    assert result.rejections.get("BOOK_INSUFFICIENT_AVAILABLE_BALANCE") == 1
    books = {item["book_id"]: item for item in result.books}
    # beta is untouched by alpha's refusal
    assert Decimal(books[BOOK_BETA]["balances"][QUOTE]) == Decimal("400")
    assert Decimal(books[BOOK_ALPHA]["balances"][QUOTE]) == Decimal("600")


def test_a_joint_backtest_refuses_two_strategies_sharing_one_book(monkeypatch):
    """Duplicate strategy->book mappings would merge two budgets: refused.

    The rejection happens while the configuration is parsed, before any account
    is built or any initial allocation is applied, so a refused configuration
    leaves no economic side effect behind.
    """
    from trader.trading import backtest as backtest_module

    class ForbiddenAccountState:
        def __init__(self, *args, **kwargs):
            raise AssertionError("an account must not be built for a duplicate mapping")

    allocate_calls = []
    real_allocate = book_ledger.allocate

    def spy_allocate(*args, **kwargs):
        allocate_calls.append(args)
        return real_allocate(*args, **kwargs)

    monkeypatch.setattr(backtest_module, "AccountState", ForbiddenAccountState)
    monkeypatch.setattr(book_ledger, "allocate", spy_allocate)

    with pytest.raises(ValueError) as refused:
        EventBacktestRuntime(
            datasets=joint_datasets(),
            strategy=joint_strategy,
            initial_cash=Decimal("1200"),
            strategy_books={"alpha": BOOK_ALPHA, "beta": BOOK_ALPHA},
            initial_allocations={BOOK_ALPHA: Decimal("1000")},
        )
    message = str(refused.value)
    assert "book" in message.lower()
    assert BOOK_ALPHA in message
    assert allocate_calls == [], "the refusal must happen before any allocation"


def test_a_legal_two_strategy_book_mapping_still_runs():
    """The duplicate guard must not disturb a legal alpha/beta partition."""
    result = run_joint()
    assert {item["book_id"] for item in result.books} == {
        BOOK_ALPHA,
        BOOK_BETA,
        UNALLOCATED,
    }
    assert result.fills, "the legal joint configuration must still trade"
    assert {fill["symbol"] for fill in result.fills} == {"BTCUSDT", "ETHUSDT"}


def test_a_joint_backtest_requires_explicit_allocations_and_explicit_strategy_books():
    with pytest.raises(ValueError, match="explicit initial allocations"):
        EventBacktestRuntime(
            datasets=joint_datasets(),
            strategy=joint_strategy,
            initial_cash=Decimal("1200"),
            strategy_books={"alpha": BOOK_ALPHA, "beta": BOOK_BETA},
        ).run()
    with pytest.raises(ValueError, match="Invalid book id"):
        EventBacktestRuntime(
            datasets=joint_datasets(),
            strategy=joint_strategy,
            initial_cash=Decimal("1200"),
            strategy_books={"alpha": "alpha"},
            initial_allocations={"alpha": Decimal("1200")},
        ).run()


def test_a_joint_backtest_refuses_a_signal_without_a_resolvable_book():
    def orphan(context, bar):
        if context.bar_index != 0 or bar["symbol"] != "BTCUSDT":
            return []
        return [StrategySignal(symbol="BTCUSDT", side="BUY", qty=Decimal("1"), order_type="MARKET")]

    result = run_joint(strategy=orphan)
    assert result.rejections.get("STRATEGY_ID_REQUIRED") == 1

    def unknown(context, bar):
        if context.bar_index != 0 or bar["symbol"] != "BTCUSDT":
            return []
        return [
            StrategySignal(
                symbol="BTCUSDT",
                side="BUY",
                qty=Decimal("1"),
                order_type="MARKET",
                strategy_id="gamma",
            )
        ]

    result = run_joint(strategy=unknown)
    assert result.rejections.get("UNKNOWN_STRATEGY_BOOK") == 1


def test_the_single_strategy_backtest_path_still_runs_and_reports_its_book():
    """Compatibility: one strategy, one simulator book, unchanged economics."""
    data = freeze_dataset(_identity("BTCUSDT", "BTC"), _bars())

    def strategy(context, bar):
        if context.bar_index != 0:
            return []
        return [StrategySignal(symbol="BTCUSDT", side="BUY", qty=Decimal("1"), order_type="MARKET")]

    result = EventBacktestRuntime(
        datasets=[data], strategy=strategy, initial_cash=Decimal("1000")
    ).run()
    assert len(result.fills) == 1
    books = {item["book_id"]: item for item in result.books}
    assert "book:backtest" in books
    assert books["book:backtest"]["equity"] is not None
    assert Decimal(books["book:backtest"]["equity"]) == Decimal("999.9")


def test_the_joint_manifest_hash_covers_the_book_configuration():
    first = run_joint()
    second = run_joint()
    assert first.manifest_hash == second.manifest_hash
    other = run_joint(initial_allocations={BOOK_ALPHA: Decimal("500"), BOOK_BETA: Decimal("500")})
    assert other.manifest_hash != first.manifest_hash


def test_the_settlement_semantics_are_explicit_on_the_command():
    quote_settled = build_order_spec(
        symbol=SYMBOL,
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
    )
    assert quote_settled.settlement == SETTLEMENT_QUOTE
    base_settled = build_order_spec(
        symbol=SYMBOL,
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
        settlement=SETTLEMENT_BASE,
    )
    assert base_settled.settlement == SETTLEMENT_BASE
