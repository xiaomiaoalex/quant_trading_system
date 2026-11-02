"""Independent round-4 tests: explicit order_type fails closed (contract 13.14, R3-B1).

Oracles are derived from the contract before the implementations were read:
every production built-in emits an explicit order_type; ``Signal.to_order_params``
and the legacy OMS resolver consume the *declared* type; a price never selects
MARKET versus LIMIT, and a missing/unsupported specification fails closed
before any broker or economic side effect. The hold-release semantics of
R3-B2 are covered by the recomputed oracles in the three pre-existing
independent modules; this module adds the independent R3-B1 coverage.

Post-round4-fix retest (R4-F1): the counter-example below was designed from
``docs/INTERFACE_CONTRACTS.md`` 13.9/13.12/13.14 and
``docs/PHASE2_BOOK_LEDGER_MIGRATION.md`` §4.2/§8 by hand arithmetic before the
engineer's regression test was read:

* legacy snapshot: 1000 USDT, one OPEN BUY 1 @ 100 with ``book_id=""`` and an
  account-layer hold of 100.1 USDT (100 quote + 0.1 admission fee buffer at
  fee_rate 0.001);
* after migration the mirrored reservation on ``UNALLOCATED`` must be 100.1;
* a terminal observation must release *both* the account layer and the
  ``UNALLOCATED`` mirror, restoring 1000 available for allocation while the
  attribution block itself (``BOOK_ATTRIBUTION_UNKNOWN``) stays in force;
* per-asset conservation must hold at every step;
* addressing the book layer through the raw ``command.book_id`` (the pre-fix
  behaviour reported as R4-F1) strands the mirror and leaves only 899.9
  allocatable, so the full 1000 allocation fails.
"""

from decimal import Decimal

import pytest

from trader.core.application.strategy_protocol import MarketData, MarketDataType
from trader.core.domain.models.order import OrderType
from trader.core.domain.models.signal import Signal, SignalType
from trader.services.oms_callback import OMSCallbackError, OMSCallbackHandler
from trader.strategies.fire_test import FireTestStrategy
from trader.trading import books as book_ledger
from trader.trading.models import NotReady, OrderObservation, PlaceOrderCommand, TradingError
from trader.trading.order_spec import PRICE_SOURCE_SIGNAL_LIMIT
from trader.trading.risk import RiskPolicy
from trader.trading.service import TradingService
from trader.trading.store import MemoryTradingStore, decode_state


def _signal(order_type, price=Decimal("100"), quantity=Decimal("0.01")):
    return Signal(
        strategy_name="indep-round4",
        signal_type=SignalType.BUY,
        order_type=order_type,
        symbol="BTCUSDT",
        price=price,
        quantity=quantity,
        confidence=Decimal("0.95"),
        reason="independent round-4 oracle",
    )


async def test_fire_test_emits_explicit_market_with_a_reference_price():
    """The built-in fire test declares MARKET; its price is only a reference."""
    plugin = FireTestStrategy()
    signal = await plugin.on_market_data(
        MarketData(symbol="BTCUSDT", data_type=MarketDataType.TICKER, price=Decimal("65000"))
    )
    assert signal is not None
    assert signal.order_type is OrderType.MARKET
    assert signal.price == Decimal("65000"), "reference price must stay observable"
    assert signal.quantity > 0


def test_to_order_params_requires_an_explicit_order_type():
    """A missing order_type is refused even when a nonzero price is present."""
    with pytest.raises(ValueError) as missing:
        _signal(None).to_order_params()
    assert str(missing.value) == "ORDER_TYPE_REQUIRED"
    with pytest.raises(ValueError) as unsupported:
        _signal("STOP").to_order_params()
    assert "UNSUPPORTED_ORDER_TYPE" in str(unsupported.value)


def test_limit_carries_the_declared_price_and_market_does_not():
    """LIMIT keeps its declared limit price; MARKET never fakes one from reference."""
    limit = _signal(OrderType.LIMIT, price=Decimal("101.5")).to_order_params()
    assert limit["order_type"] is OrderType.LIMIT
    assert limit["price"] == Decimal("101.5")
    market = _signal(OrderType.MARKET, price=Decimal("101.5")).to_order_params()
    assert market["order_type"] is OrderType.MARKET
    assert market["price"] is None, "MARKET must not turn the reference price into a limit"


def test_the_oms_resolver_fails_closed_and_never_guesses_from_price():
    """Legacy OMS gate: declared type or a stable error; no price heuristic."""
    resolve = OMSCallbackHandler._resolve_signal_order_type
    with pytest.raises(OMSCallbackError) as missing:
        resolve(_signal(None, price=Decimal("100")))
    assert str(missing.value) == "ORDER_TYPE_REQUIRED"
    with pytest.raises(OMSCallbackError) as unsupported:
        resolve(_signal("STOP", price=Decimal("100")))
    assert "UNSUPPORTED_ORDER_TYPE" in str(unsupported.value)
    assert resolve(_signal(OrderType.MARKET, price=Decimal("100"))) is OrderType.MARKET
    assert resolve(_signal(OrderType.LIMIT, price=Decimal("100"))) is OrderType.LIMIT


# ============================================ R4-F1 post-fix independent retest
R4F1_ACCOUNT = "acct-indep-r4f1"
R4F1_CL_ORD = "qts-r4f1-legacy"
R4F1_BOOK = "book:gamma"
R4F1_T0 = 1_700_000_000_000
R4F1_FEE_RATE = Decimal("0.001")

_STATE_FIELDS = (
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


def _r4f1_legacy_snapshot_dict():
    """A snapshot written before the book contract: account hold, no book."""
    return {
        "account_id": R4F1_ACCOUNT,
        "status": "READY",
        "version": 3,
        "balances": {"USDT": "1000", "BTC": "0"},
        "positions": {},
        "external_locked": {},
        "orders": {
            R4F1_CL_ORD: {
                "cl_ord_id": R4F1_CL_ORD,
                "command": {
                    "command_id": "r4f1-legacy-command",
                    "account_id": R4F1_ACCOUNT,
                    "symbol": "BTCUSDT",
                    "base_asset": "BTC",
                    "quote_asset": "USDT",
                    "side": "BUY",
                    "qty": "1",
                    "price": "100",
                    "deployment_id": "legacy",
                    "order_type": "LIMIT",
                    "time_in_force": "GTC",
                    "decision_id": "legacy",
                    "book_id": "",
                    "price_source": PRICE_SOURCE_SIGNAL_LIMIT,
                    "price_at_ms": R4F1_T0,
                },
                "status": "OPEN",
                "broker_order_id": "b-r4f1-legacy",
                "booked_qty": "0",
                "booked_quote": "0",
                "observed_qty": "0",
                "observed_at_ms": R4F1_T0,
            }
        },
        "commands": {
            "r4f1-legacy-command": {
                "command_id": "r4f1-legacy-command",
                "cl_ord_id": R4F1_CL_ORD,
                "kind": "PLACE",
                "payload_hash": "r4f1-legacy-hash",
                "status": "QUEUED",
                "actor": "legacy",
            }
        },
        "holds": {R4F1_CL_ORD: {"asset": "USDT", "amount": "100.1"}},
        "books": {},
        "executions": {},
        "ledger": [],
        "events": [],
        "outbox": [],
        "control": {},
        "reconciliation": {
            "checked_at_ms": R4F1_T0,
            "generation": 1,
            "stream_healthy": True,
            "unresolved": [],
            "marks": {"BTCUSDT": {"price": "100", "at_ms": R4F1_T0}},
        },
    }


def _r4f1_place_command():
    """A new BUY addressed to a proper strategy book, as a fresh strategy would."""
    return PlaceOrderCommand(
        command_id="cmd-r4f1-after",
        account_id=R4F1_ACCOUNT,
        symbol="BTCUSDT",
        base_asset="BTC",
        quote_asset="USDT",
        side="BUY",
        qty=Decimal("1"),
        price=Decimal("100"),
        deployment_id="dep-r4f1",
        decision_id="dec-r4f1",
        order_type="LIMIT",
        time_in_force="GTC",
        book_id=R4F1_BOOK,
        price_source=PRICE_SOURCE_SIGNAL_LIMIT,
        price_at_ms=R4F1_T0,
    )


class _R4F1Exchange:
    """Deterministic exchange-port fake; the retest path never reaches it."""

    async def place_order(self, command, cl_ord_id):
        raise AssertionError("the R4-F1 retest never places an order")

    async def cancel_order(self, account_id, cl_ord_id, command_id, symbol):
        raise AssertionError("the R4-F1 retest never cancels an order")

    async def query_order(self, account_id, cl_ord_id, symbol):
        raise AssertionError("the R4-F1 retest never queries an order")


async def _r4f1_seed(store):
    state = decode_state(_r4f1_legacy_snapshot_dict())
    async with store.transaction(R4F1_ACCOUNT) as target:
        for name in _STATE_FIELDS:
            setattr(target, name, getattr(state, name))
    return state


def _r4f1_service(store):
    return TradingService(
        store=store,
        exchange=_R4F1Exchange(),
        policy=RiskPolicy(fee_rate=R4F1_FEE_RATE),
        clock=lambda: R4F1_T0,
    )


async def test_r4f1_migration_mirrors_the_legacy_hold_on_unallocated():
    """Hand-computed precondition: the mirror on UNALLOCATED is exactly 100.1."""
    state = await _r4f1_seed(MemoryTradingStore())
    assert state.balances["USDT"] == Decimal("1000")
    assert state.books[book_ledger.UNALLOCATED].balances["USDT"] == Decimal("1000")
    mirror = state.books[book_ledger.UNALLOCATED].holds.get(R4F1_CL_ORD)
    assert mirror is not None, "the legacy account hold must be mirrored on UNALLOCATED"
    assert mirror.asset == "USDT"
    assert mirror.amount == Decimal("100.1"), "100 quote + 0.1 fee buffer at 0.001"
    assert book_ledger.book_available(state.books[book_ledger.UNALLOCATED], "USDT") == Decimal(
        "899.9"
    )
    assert book_ledger.conservation_mismatch(state) == {}
    assert R4F1_CL_ORD in state.reconciliation[book_ledger.BOOK_LEDGER_KEY]["unattributed_orders"]


def _old_release_order_hold(state, order):
    """Replica of the pre-fix release reported by R4-F1.

    The round-4 finding states the old ``core.release_order_hold`` addressed
    the book layer with the raw ``order.command.book_id``; for a migrated
    order that field is ``""`` and ``release_book_hold`` silently no-ops.
    """
    state.holds.pop(order.cl_ord_id, None)
    book_ledger.release_book_hold(state, order.command.book_id, order.cl_ord_id)


async def test_r4f1_old_release_logic_strands_the_mirror_and_blocks_allocation():
    """Red proof: the pre-fix logic strands funds on UNALLOCATED."""
    state = await _r4f1_seed(MemoryTradingStore())
    order = state.orders[R4F1_CL_ORD]

    _old_release_order_hold(state, order)

    assert R4F1_CL_ORD not in state.holds, "the account layer is released"
    assert (
        R4F1_CL_ORD in state.books[book_ledger.UNALLOCATED].holds
    ), "the mirrored reservation is stranded: no operational exit for the funds"
    assert book_ledger.book_available(state.books[book_ledger.UNALLOCATED], "USDT") == Decimal(
        "899.9"
    )
    with pytest.raises(TradingError) as refused:
        book_ledger.allocate(state, R4F1_BOOK, "USDT", Decimal("1000"))
    assert str(refused.value) == "ALLOCATION_INSUFFICIENT_UNALLOCATED"


@pytest.mark.parametrize("terminal_status", ["CANCELLED", "EXPIRED"])
async def test_r4f1_terminal_observation_releases_both_layers_and_unblocks_funds(
    terminal_status,
):
    """Green oracle: terminal release frees both layers; attribution stays blocked."""
    store = MemoryTradingStore()
    service = _r4f1_service(store)
    await _r4f1_seed(store)

    accepted = await service.observe(
        OrderObservation(
            R4F1_ACCOUNT,
            R4F1_CL_ORD,
            terminal_status,
            Decimal("0"),
            broker_order_id="b-r4f1-legacy",
            observed_at_ms=R4F1_T0 + 50,
        )
    )
    assert accepted is True

    state = await store.read(R4F1_ACCOUNT)
    unallocated = state.books[book_ledger.UNALLOCATED]
    assert R4F1_CL_ORD not in state.holds, "the account layer must be released"
    assert R4F1_CL_ORD not in unallocated.holds, "the mirrored UNALLOCATED layer must be released"
    assert unallocated.balances["USDT"] == Decimal("1000"), "releasing a hold moves no balance"
    assert book_ledger.book_available(unallocated, "USDT") == Decimal("1000")
    assert book_ledger.conservation_mismatch(state) == {}

    # The attribution block itself survives the terminal release (doc §4.2/§8):
    # the marker, the derived reasons, the operator view and new-risk admission
    # all still refuse. (``_refresh_status`` marks the whole account BLOCKED,
    # so the precise gate code is taken from ``book_reasons``/``risk_view``.)
    marker = state.reconciliation[book_ledger.BOOK_LEDGER_KEY]
    assert R4F1_CL_ORD in marker["unattributed_orders"]
    assert "BOOK_ATTRIBUTION_UNKNOWN" in book_ledger.book_reasons(state)
    view = await service.risk_view(R4F1_ACCOUNT)
    assert "BOOK_ATTRIBUTION_UNKNOWN" in view.reasons
    assert view.ready is False
    with pytest.raises(NotReady):
        await service.place(_r4f1_place_command())

    # The freed funds are allocatable again in full.
    receipt = await service.allocate(
        "alloc-r4f1-1",
        R4F1_ACCOUNT,
        R4F1_BOOK,
        "USDT",
        Decimal("1000"),
        actor="independent-tester",
        reason="R4-F1 retest: full release restores allocatability",
    )
    assert receipt.status == "CONFIRMED"
    state = await store.read(R4F1_ACCOUNT)
    assert state.books[book_ledger.UNALLOCATED].balances["USDT"] == Decimal("0")
    assert state.books[R4F1_BOOK].balances["USDT"] == Decimal("1000")
    assert state.balances["USDT"] == Decimal("1000"), "allocation never changes the account total"
    assert book_ledger.conservation_mismatch(state) == {}
