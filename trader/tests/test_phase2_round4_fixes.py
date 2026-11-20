"""Phase 2 round-4 engineer tests: real hold release and explicit order types.

Every expected amount is hand-computed from the contract:

* A BUY execution releases ``qty * execution.price`` of quote plus the fee only
  when that fee is charged in the quote asset. The admission fee buffer is not
  a fabricated execution fee.
* Every built-in strategy and every signal consumer declares the order type
  explicitly; price never selects MARKET or LIMIT.
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from decimal import Decimal

import pytest

from trader.core.application.strategy_protocol import MarketData, MarketDataType
from trader.core.domain.models.order import OrderType
from trader.core.domain.models.signal import Signal, SignalType
from trader.trading import books as book_ledger
from trader.trading.models import (
    SETTLEMENT_BASE,
    Conflict,
    ExchangeOrderResult,
    ExchangeOutcome,
    Execution,
    OrderObservation,
    PlaceOrderCommand,
    ReconcileSnapshot,
)
from trader.trading.order_spec import PRICE_SOURCE_MARKET_TICKER, PRICE_SOURCE_SIGNAL_LIMIT
from trader.trading.risk import RiskPolicy
from trader.trading.service import TradingService
from trader.trading.store import MemoryTradingStore, decode_state

ACCOUNT = "acct-round4"
SYMBOL = "BTCUSDT"
BASE = "BTC"
QUOTE = "USDT"
T0 = 1_700_000_000_000
FEE_RATE = Decimal("0.001")
BOOK_ALPHA = "book:alpha"


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


def build_service(store=None, exchange=None, clock=None):
    return TradingService(
        store=store if store is not None else MemoryTradingStore(),
        exchange=exchange if exchange is not None else RecordingExchange(),
        policy=RiskPolicy(fee_rate=FEE_RATE),
        clock=clock if clock is not None else FixedClock(),
    )


def venue_snapshot(*, balances=None, checked=T0, generation=1, marks=None):
    return ReconcileSnapshot(
        ACCOUNT,
        checked,
        generation,
        balances if balances is not None else {QUOTE: Decimal("1000"), BASE: Decimal("0")},
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
        deployment_id="dep-round4",
        decision_id=f"dec-{command_id}",
        order_type="MARKET",
        time_in_force="GTC",
        book_id=BOOK_ALPHA,
        price_source=PRICE_SOURCE_MARKET_TICKER,
        price_at_ms=T0,
    )
    values.update(overrides)
    return PlaceOrderCommand(**values)


async def funded_service(
    quote_amount="100.1", *, balances=None, extra_allocations=None, marks=None
):
    store = MemoryTradingStore()
    current = build_service(store=store)
    await current.recover(venue_snapshot(balances=balances, marks=marks))
    await current.allocate(
        "alloc-round4", ACCOUNT, BOOK_ALPHA, QUOTE, Decimal(quote_amount), reason="round 4 setup"
    )
    for asset, amount in (extra_allocations or {}).items():
        await current.allocate(
            f"alloc-round4-{asset}",
            ACCOUNT,
            BOOK_ALPHA,
            asset,
            Decimal(amount),
            reason="round 4 setup",
        )
    return current, store


def execution(cl_ord_id: str, exec_id: str, qty, price, fee="0", fee_asset=""):
    return Execution(
        ACCOUNT,
        cl_ord_id,
        SYMBOL,
        exec_id,
        Decimal(str(qty)),
        Decimal(str(price)),
        Decimal(str(fee)),
        fee_asset,
        T0 + 1,
    )


def both_holds(state, cl_ord_id) -> tuple[Decimal, Decimal]:
    return (
        state.holds[cl_ord_id].amount,
        state.books[BOOK_ALPHA].holds[cl_ord_id].amount,
    )


# ---------------------------------------------------------------- hold release
async def test_partial_quote_settled_fill_keeps_the_real_remaining_budget():
    """1 * 100 * (1 + 0.001) = 100.1 reserved; 1.1 @ 90 zero fee spends 99."""
    current, store = await funded_service()
    receipt = await current.place(market_command("cmd-r4-overfill"))
    cl_ord_id = receipt.cl_ord_id
    state = await store.read(ACCOUNT)
    assert both_holds(state, cl_ord_id) == (Decimal("100.1"), Decimal("100.1"))

    assert await current.record_execution(execution(cl_ord_id, "e-r4-1", "1.1", "90")) is True
    state = await store.read(ACCOUNT)
    order = state.orders[cl_ord_id]
    assert order.booked_qty == Decimal("1.1")
    assert order.booked_quote == Decimal("99")
    assert order.status == "PARTIALLY_FILLED"
    assert both_holds(state, cl_ord_id) == (Decimal("1.1"), Decimal("1.1"))
    assert state.balances[QUOTE] == Decimal("901")
    assert state.balances[BASE] == Decimal("1.1")
    assert book_ledger.conservation_mismatch(state) == {}

    # The remaining 1.1 reservation cannot fund another order on the book.
    with pytest.raises(Exception) as refused:
        await current.place(
            market_command("cmd-r4-second", qty=Decimal("0.01"), price=Decimal("100"))
        )
    assert "BOOK_INSUFFICIENT_AVAILABLE_BALANCE" in str(refused.value)

    # Only the terminal FILLED observation clears the real residue.
    assert (
        await current.observe(
            OrderObservation(ACCOUNT, cl_ord_id, "FILLED", Decimal("1.1"), "b-1", T0 + 2)
        )
        is True
    )
    state = await store.read(ACCOUNT)
    assert cl_ord_id not in state.holds
    assert cl_ord_id not in state.books[BOOK_ALPHA].holds


async def test_zero_fee_partial_fill_does_not_consume_the_fee_buffer():
    """0.5 * 10 = 5 spent; 100.1 - 5 = 95.1 stays reserved on both layers."""
    current, store = await funded_service()
    receipt = await current.place(market_command("cmd-r4-zerofee"))
    cl_ord_id = receipt.cl_ord_id
    assert await current.record_execution(execution(cl_ord_id, "e-r4-z", "0.5", "10")) is True
    state = await store.read(ACCOUNT)
    assert both_holds(state, cl_ord_id) == (Decimal("95.1"), Decimal("95.1"))
    assert state.balances[QUOTE] == Decimal("995")


async def test_quote_fee_release_uses_the_actual_fee_currency():
    """A quote fee adds to the release; a base or third-asset fee does not."""
    current, store = await funded_service()
    quote_fee = await current.place(market_command("cmd-r4-quote-fee"))
    assert (
        await current.record_execution(
            execution(quote_fee.cl_ord_id, "e-r4-qf", "1", "90", fee="0.09", fee_asset=QUOTE)
        )
        is True
    )
    state = await store.read(ACCOUNT)
    # 90 + 0.09 = 90.09 released -> 100.1 - 90.09 = 10.01
    assert both_holds(state, quote_fee.cl_ord_id) == (Decimal("10.01"), Decimal("10.01"))
    # Covering the requested base quantity does not complete a quote-settled
    # MARKET BUY; a terminal venue fact must release the real remainder.
    assert state.orders[quote_fee.cl_ord_id].status == "PARTIALLY_FILLED"
    assert (
        await current.observe(
            OrderObservation(ACCOUNT, quote_fee.cl_ord_id, "FILLED", Decimal("1"), "b-1", T0 + 2)
        )
        is True
    )
    settled = await store.read(ACCOUNT)
    assert quote_fee.cl_ord_id not in settled.holds
    assert quote_fee.cl_ord_id not in settled.books[BOOK_ALPHA].holds
    assert book_ledger.conservation_mismatch(settled) == {}

    current, store = await funded_service()
    base_fee = await current.place(market_command("cmd-r4-base-fee"))
    assert (
        await current.record_execution(
            execution(base_fee.cl_ord_id, "e-r4-bf", "1", "90", fee="0.0001", fee_asset=BASE)
        )
        is True
    )
    state = await store.read(ACCOUNT)
    # The 0.0001 BTC fee is booked in BTC; the quote hold only releases 90.
    assert both_holds(state, base_fee.cl_ord_id) == (Decimal("10.1"), Decimal("10.1"))
    assert state.balances[BASE] == Decimal("0.9999")

    current, store = await funded_service(
        balances={QUOTE: Decimal("1000"), BASE: Decimal("0"), "BNB": Decimal("1")},
        extra_allocations={"BNB": "1"},
        marks={
            SYMBOL: {"price": Decimal("100"), "at_ms": T0},
            "BNBUSDT": {"price": Decimal("500"), "at_ms": T0},
        },
    )
    third_fee = await current.place(market_command("cmd-r4-third-fee"))
    assert (
        await current.record_execution(
            execution(third_fee.cl_ord_id, "e-r4-tf", "1", "90", fee="0.001", fee_asset="BNB")
        )
        is True
    )
    state = await store.read(ACCOUNT)
    assert both_holds(state, third_fee.cl_ord_id) == (Decimal("10.1"), Decimal("10.1"))
    assert state.balances["BNB"] == Decimal("0.999")


def legacy_state_with_open_order() -> dict:
    """A pre-book-contract snapshot: 1000 USDT and an OPEN order without a book."""
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
            "marks": {SYMBOL: {"price": Decimal("100"), "at_ms": T0}},
        },
    }


async def test_a_legacy_terminal_order_releases_the_mirrored_unallocated_hold():
    """The mirrored reservation must die with the account reservation.

    A migrated order has no strategy book, so its reservation is mirrored on
    UNALLOCATED. A terminal observation must release both layers, otherwise
    the funds are stranded and can never be allocated again.
    """
    store = MemoryTradingStore()
    decoded = decode_state(legacy_state_with_open_order())
    assert decoded.books[book_ledger.UNALLOCATED].holds["qts-legacy"].amount == Decimal("100.1")
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
            setattr(target, name, getattr(decoded, name))

    current = build_service(store=store)
    accepted = await current.observe(
        OrderObservation(ACCOUNT, "qts-legacy", "CANCELLED", Decimal("0"), "b-legacy", T0 + 1)
    )
    assert accepted is True
    state = await store.read(ACCOUNT)
    assert "qts-legacy" not in state.holds
    assert "qts-legacy" not in state.books[book_ledger.UNALLOCATED].holds

    # The reserved cash is allocatable again.
    await current.allocate(
        "alloc-legacy", ACCOUNT, BOOK_ALPHA, QUOTE, Decimal("1000"), reason="legacy release"
    )
    state = await store.read(ACCOUNT)
    assert state.books[book_ledger.UNALLOCATED].balances[QUOTE] == Decimal("0")
    assert state.books[BOOK_ALPHA].balances[QUOTE] == Decimal("1000")
    assert book_ledger.conservation_mismatch(state) == {}


async def test_a_quote_settled_market_buy_completes_when_the_quote_budget_is_exhausted():
    """Once the whole quote budget is spent the venue cannot fill more.

    1 * 100 = 100 USDT budget; a 1 @ 100 fill spends it, so the order is
    complete and the remaining fee buffer is released.
    """
    current, store = await funded_service()
    receipt = await current.place(market_command("cmd-r4-budget"))
    cl_ord_id = receipt.cl_ord_id
    assert await current.record_execution(execution(cl_ord_id, "e-r4-budget", "1", "100")) is True
    state = await store.read(ACCOUNT)
    assert state.orders[cl_ord_id].status == "FILLED"
    assert state.orders[cl_ord_id].booked_quote == Decimal("100")
    assert cl_ord_id not in state.holds
    assert cl_ord_id not in state.books[BOOK_ALPHA].holds
    assert book_ledger.conservation_mismatch(state) == {}


async def test_limit_and_base_settled_market_keep_their_base_bounds():
    current, store = await funded_service()
    limit = await current.place(
        market_command("cmd-r4-limit", order_type="LIMIT", price_source=PRICE_SOURCE_SIGNAL_LIMIT)
    )
    with pytest.raises(Conflict):
        await current.record_execution(execution(limit.cl_ord_id, "e-r4-limit", "1.1", "90"))
    state = await store.read(ACCOUNT)
    assert state.orders[limit.cl_ord_id].booked_qty == Decimal("0")

    current, store = await funded_service()
    base_settled = await current.place(market_command("cmd-r4-base", settlement=SETTLEMENT_BASE))
    with pytest.raises(Conflict):
        await current.record_execution(execution(base_settled.cl_ord_id, "e-r4-base", "1.1", "90"))
    state = await store.read(ACCOUNT)
    assert state.orders[base_settled.cl_ord_id].booked_qty == Decimal("0")


async def test_terminal_cleanup_releases_the_real_remaining_two_layer_reservation():
    current, store = await funded_service()
    receipt = await current.place(market_command("cmd-r4-terminal"))
    cl_ord_id = receipt.cl_ord_id
    await current.record_execution(execution(cl_ord_id, "e-r4-t", "0.5", "90"))
    state = await store.read(ACCOUNT)
    assert both_holds(state, cl_ord_id) == (Decimal("55.1"), Decimal("55.1"))

    await current.cancel("cancel-r4", ACCOUNT, cl_ord_id)
    await current.observe(
        OrderObservation(ACCOUNT, cl_ord_id, "CANCELLED", Decimal("0.5"), "b-1", T0 + 3)
    )
    state = await store.read(ACCOUNT)
    assert cl_ord_id not in state.holds
    assert cl_ord_id not in state.books[BOOK_ALPHA].holds
    assert book_ledger.conservation_mismatch(state) == {}


async def test_multi_fill_releases_are_cumulative_and_replay_is_a_noop():
    current, store = await funded_service(quote_amount="200.2")
    receipt = await current.place(market_command("cmd-r4-multi", qty=Decimal("2")))
    cl_ord_id = receipt.cl_ord_id
    assert both_holds(await store.read(ACCOUNT), cl_ord_id) == (
        Decimal("200.2"),
        Decimal("200.2"),
    )

    first = execution(cl_ord_id, "e-r4-m1", "1", "90", fee="0.09", fee_asset=QUOTE)
    assert await current.record_execution(first) is True
    state = await store.read(ACCOUNT)
    # 200.2 - (90 + 0.09) = 110.11
    assert both_holds(state, cl_ord_id) == (Decimal("110.11"), Decimal("110.11"))

    second = execution(cl_ord_id, "e-r4-m2", "0.5", "80")
    assert await current.record_execution(second) is True
    state = await store.read(ACCOUNT)
    # 110.11 - (0.5 * 80) = 70.11
    assert both_holds(state, cl_ord_id) == (Decimal("70.11"), Decimal("70.11"))

    assert await current.record_execution(first) is False
    state = await store.read(ACCOUNT)
    assert both_holds(state, cl_ord_id) == (Decimal("70.11"), Decimal("70.11"))
    assert state.orders[cl_ord_id].booked_qty == Decimal("1.5")
    assert state.orders[cl_ord_id].status == "PARTIALLY_FILLED"


# ------------------------------------------------------------ explicit order type
def _market_data(price: str, ts: datetime) -> MarketData:
    return MarketData(
        symbol=SYMBOL,
        data_type=MarketDataType.TICKER,
        price=Decimal(price),
        volume=Decimal("1"),
        timestamp=ts,
    )


async def _signals(module_path: str, config: dict, prices: list[tuple[str, int]]):
    import importlib

    plugin = importlib.import_module(module_path).get_plugin()
    await plugin.initialize(config)
    start = datetime(2026, 2, 1, tzinfo=timezone.utc)
    signals = []
    for index, (price, offset) in enumerate(prices):
        signal = await plugin.on_market_data(_market_data(price, start + timedelta(seconds=offset)))
        if signal is not None:
            signals.append(signal)
    return signals


async def test_fire_test_emits_an_explicit_market_order_type_with_a_reference_price():
    signals = await _signals(
        "trader.strategies.fire_test",
        {
            "mode": "ALTERNATE",
            "start_with": "BUY",
            "interval_seconds": 10,
            "order_size": "0.001",
            "max_signals": 2,
        },
        [("100", 0), ("101", 10)],
    )
    assert signals, "fire_test must emit a signal"
    for signal in signals:
        assert signal.order_type is OrderType.MARKET
    assert signals[0].price > 0


async def test_every_builtin_plugin_signal_declares_a_valid_order_type():
    cases = {
        "trader.strategies.ema_cross_btc": (
            {
                "fast_period": 3,
                "slow_period": 5,
                "order_size": "0.01",
                "min_confidence": "0.60",
            },
            [
                (price, index)
                for index, price in enumerate(
                    ["100", "98", "96", "94", "92", "94", "96", "99", "102"]
                )
            ],
        ),
        "trader.strategies.rsi_grid": (
            {
                "rsi_period": 3,
                "oversold": "35",
                "overbought": "65",
                "grid_step_pct": "0.02",
                "order_size": "0.01",
            },
            [
                (price, index)
                for index, price in enumerate(["100", "95", "90", "85", "95", "105", "115"])
            ],
        ),
        "trader.strategies.dca_btc": (
            {
                "interval_seconds": 60,
                "min_buy_gap_seconds": 10,
                "base_order_size": "0.001",
                "dip_threshold_pct": "0.02",
                "dip_multiplier": "2",
            },
            [("100", 0), ("99", 30), ("96", 45), ("97", 120)],
        ),
        "trader.strategies.fire_test": (
            {
                "mode": "ALTERNATE",
                "start_with": "BUY",
                "interval_seconds": 10,
                "order_size": "0.001",
                "max_signals": 2,
            },
            [("100", 0), ("101", 10)],
        ),
    }
    for module_path, (config, prices) in cases.items():
        signals = await _signals(module_path, config, prices)
        assert signals, f"{module_path} produced no signal"
        for signal in signals:
            assert signal.order_type in {OrderType.MARKET, OrderType.LIMIT}, module_path


def test_signal_to_order_params_requires_an_explicit_order_type():
    missing = Signal(
        symbol=SYMBOL, signal_type=SignalType.BUY, quantity=Decimal("1"), price=Decimal("100")
    )
    with pytest.raises(ValueError, match="ORDER_TYPE_REQUIRED"):
        missing.to_order_params()

    unsupported = Signal(
        symbol=SYMBOL,
        signal_type=SignalType.BUY,
        quantity=Decimal("1"),
        price=Decimal("100"),
        order_type="STOP",  # type: ignore[arg-type]
    )
    with pytest.raises(ValueError, match="UNSUPPORTED_ORDER_TYPE"):
        unsupported.to_order_params()


def test_signal_to_order_params_never_turns_a_market_reference_into_a_limit():
    market = Signal(
        symbol=SYMBOL,
        signal_type=SignalType.BUY,
        quantity=Decimal("1"),
        price=Decimal("100"),
        order_type=OrderType.MARKET,
    )
    params = market.to_order_params()
    assert params["order_type"] is OrderType.MARKET
    assert params["price"] is None

    limit = Signal(
        symbol=SYMBOL,
        signal_type=SignalType.BUY,
        quantity=Decimal("1"),
        price=Decimal("95"),
        order_type=OrderType.LIMIT,
    )
    params = limit.to_order_params()
    assert params["order_type"] is OrderType.LIMIT
    assert params["price"] == Decimal("95")
