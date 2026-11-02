"""Stable strategy sub-ledgers inside the single spot account.

The account total is partitioned into ``UNALLOCATED`` and one ``book:{strategy}``
ledger per strategy. Every mutation keeps ``account_total == UNALLOCATED + all
books`` true at commit time. Nothing here guesses a strategy attribution: an
order or fill whose book cannot be established is recorded as unattributed and
blocks new risk instead of being assigned by symbol, ratio or time order.
"""

from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal, DecimalException
from typing import Any

from trader.trading.models import TERMINAL, AccountState, BookLedger, Hold, TradingError

UNALLOCATED = "UNALLOCATED"
BOOK_PREFIX = "book:"
QUOTE_ASSET = "USDT"
BOOK_LEDGER_KEY = "book_ledger"
_ALLOWED_BOOK_CHARS = frozenset(
    "abcdefghijklmnopqrstuvwxyzABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789_.:-"
)


def book_id_for(strategy_id: str) -> str:
    """The frozen convention ``book:{strategy_id}``.

    ``strategy_id`` is stable across deployments, restarts and backtest runs;
    ``deployment_id`` remains command ownership and never names a ledger.
    """
    strategy_id = str(strategy_id or "").strip()
    if not strategy_id:
        raise TradingError("STRATEGY_BOOK_ID_REQUIRED")
    return BOOK_PREFIX + strategy_id


def is_strategy_book(book_id: Any) -> bool:
    return (
        isinstance(book_id, str)
        and book_id.startswith(BOOK_PREFIX)
        and len(book_id) > len(BOOK_PREFIX)
        and not any(char not in _ALLOWED_BOOK_CHARS for char in book_id)
    )


def validate_strategy_book_id(book_id: Any) -> str:
    if book_id == UNALLOCATED:
        raise TradingError("UNALLOCATED_BOOK_CANNOT_TRADE")
    if not is_strategy_book(book_id):
        raise TradingError("INVALID_BOOK_ID")
    return book_id


def ensure_unallocated(state: AccountState) -> BookLedger:
    book = state.books.get(UNALLOCATED)
    if book is None:
        book = BookLedger(
            book_id=UNALLOCATED,
            balances=dict(state.balances),
            positions=dict(state.positions),
        )
        state.books[UNALLOCATED] = book
    return book


def book_hold_amount(book: BookLedger, asset: str) -> Decimal:
    return sum(
        (hold.amount for hold in book.holds.values() if hold.asset == asset),
        Decimal(0),
    )


def book_available(book: BookLedger, asset: str) -> Decimal:
    total = book.balances.get(asset, Decimal(0))
    if not total.is_finite() or total < 0:
        raise TradingError("BOOK_LEDGER_INCONSISTENT")
    holds = book_hold_amount(book, asset)
    if not holds.is_finite() or holds < 0:
        raise TradingError("BOOK_LEDGER_INCONSISTENT")
    return total - holds


def reserve_book_hold(state: AccountState, book_id: str, cl_ord_id: str, hold: Hold) -> None:
    """Book layer of the two-layer reservation, in the account transaction."""
    book = state.books.get(book_id)
    if book is None:
        raise TradingError("BOOK_NOT_FOUND")
    book.holds[cl_ord_id] = Hold(hold.asset, hold.amount)


def release_book_hold(state: AccountState, book_id: str, cl_ord_id: str) -> None:
    book = state.books.get(book_id)
    if book is not None:
        book.holds.pop(cl_ord_id, None)


def release_book_execution_hold(
    state: AccountState,
    book_id: str,
    cl_ord_id: str,
    asset: str,
    amount: Decimal,
) -> None:
    book = state.books.get(book_id)
    if book is None:
        return
    hold = book.holds.get(cl_ord_id)
    if hold is None or hold.asset != asset:
        return
    hold.amount -= amount
    if hold.amount <= 0:
        book.holds.pop(cl_ord_id, None)


def execution_book_id(state: AccountState, order) -> str:
    """Resolve the book for a fill without guessing a strategy attribution."""
    book_id = getattr(order.command, "book_id", "")
    if is_strategy_book(book_id) and book_id in state.books:
        return book_id
    return UNALLOCATED


def mirror_account_holds(state: AccountState) -> None:
    """Show a legacy account-level reservation on the unallocated book.

    A snapshot written before the book contract keeps its hold on the account
    layer only. The unallocated book must show the same reserved cash,
    otherwise it would advertise funds that a live order already committed.
    This is one economic reservation seen on two layers, not a second asset
    commitment, and it never invents a strategy attribution: only holds whose
    owner is still unknown are mirrored, and they stay in ``UNALLOCATED``.

    A hold of an order that *does* have a strategy book is never mirrored: that
    layer is created by the reservation itself, so a missing one is a defect to
    detect, not something to repair silently.
    """
    for cl_ord_id, order in state.orders.items():
        account_hold = state.holds.get(cl_ord_id)
        if account_hold is None:
            continue
        book_id = getattr(order.command, "book_id", "")
        if is_strategy_book(book_id) and book_id in state.books:
            continue
        book = state.books.get(UNALLOCATED)
        if book is None or cl_ord_id in book.holds:
            continue
        book.holds[cl_ord_id] = Hold(account_hold.asset, account_hold.amount)


def allocate(state: AccountState, book_id: str, asset: str, amount: Decimal) -> BookLedger:
    """Move assets from UNALLOCATED into an explicit strategy book."""
    validate_strategy_book_id(book_id)
    if not isinstance(asset, str) or not asset.strip():
        raise TradingError("ALLOCATION_ASSET_REQUIRED")
    try:
        amount = Decimal(amount)
    except (DecimalException, TypeError, ValueError):
        raise TradingError("ALLOCATION_AMOUNT_INVALID") from None
    if not amount.is_finite() or amount <= 0:
        raise TradingError("ALLOCATION_AMOUNT_INVALID")
    unallocated = ensure_unallocated(state)
    if book_available(unallocated, asset) < amount:
        raise TradingError("ALLOCATION_INSUFFICIENT_UNALLOCATED")
    book = state.books.get(book_id)
    if book is None:
        book = BookLedger(book_id=book_id)
        state.books[book_id] = book
    unallocated.balances[asset] = unallocated.balances.get(asset, Decimal(0)) - amount
    book.balances[asset] = book.balances.get(asset, Decimal(0)) + amount
    return book


def conservation_mismatch(state: AccountState) -> dict[str, dict[str, str]]:
    """Per-asset difference between the account total and its partitions."""
    assets: set[str] = set(state.balances)
    for book in state.books.values():
        assets.update(book.balances)
    mismatch: dict[str, dict[str, str]] = {}
    for asset in sorted(assets):
        account_total = state.balances.get(asset, Decimal(0))
        partitioned = sum(
            (book.balances.get(asset, Decimal(0)) for book in state.books.values()),
            Decimal(0),
        )
        if account_total != partitioned:
            mismatch[asset] = {
                "account": str(account_total),
                "books": str(partitioned),
            }
    return mismatch


def assert_conservation(state: AccountState) -> None:
    if conservation_mismatch(state):
        raise TradingError("BOOK_LEDGER_INCONSISTENT")


def _marker(state: AccountState) -> dict[str, Any]:
    marker = state.reconciliation.setdefault(BOOK_LEDGER_KEY, {})
    marker.setdefault("initialized_at_ms", 0)
    marker.setdefault("unattributed_orders", {})
    marker.setdefault("resolved_orders", [])
    marker.setdefault("inconsistent", {})
    marker.setdefault("legacy_unknown_commands", [])
    return marker


@dataclass(frozen=True)
class BookMigrationReport:
    initialized: bool
    unattributed_orders: tuple[str, ...]
    inconsistent: dict[str, dict[str, str]]
    legacy_unknown_commands: tuple[str, ...]


def record_unattributed_order(
    state: AccountState, cl_ord_id: str, requested_book_id: str, reason: str = ""
) -> None:
    marker = _marker(state)
    if cl_ord_id in marker["resolved_orders"]:
        return
    marker["unattributed_orders"].setdefault(
        cl_ord_id,
        {
            "requested_book_id": str(requested_book_id or ""),
            "reason": str(reason or "BOOK_ATTRIBUTION_UNKNOWN"),
        },
    )


def resolve_attribution(state: AccountState, orders: dict[str, str]) -> None:
    """Record an operator's explicit per-order attribution evidence."""
    marker = _marker(state)
    for cl_ord_id in orders:
        marker["unattributed_orders"].pop(cl_ord_id, None)
        if cl_ord_id not in marker["resolved_orders"]:
            marker["resolved_orders"].append(cl_ord_id)


def _order_needs_attribution(state: AccountState, order) -> bool:
    if order.booked_qty > 0:
        return True
    if order.cl_ord_id in state.holds:
        return True
    return order.status not in TERMINAL and order.remaining_qty > 0


def migrate_book_ledger(state: AccountState) -> BookMigrationReport:
    """Make an old account snapshot explicit without rewriting its facts.

    Existing balances and positions move to the reserved ``UNALLOCATED`` book.
    Orders whose book cannot be established keep their account hold and become
    a persisted, blocking difference. No ledger, event, execution or order
    intent is rewritten.
    """
    initialized = False
    marker = _marker(state)
    if not state.books:
        state.books[UNALLOCATED] = BookLedger(
            book_id=UNALLOCATED,
            balances=dict(state.balances),
            positions=dict(state.positions),
        )
        initialized = True
    elif UNALLOCATED not in state.books:
        state.books[UNALLOCATED] = BookLedger(book_id=UNALLOCATED)
        initialized = True

    mirror_account_holds(state)

    for cl_ord_id, order in state.orders.items():
        if cl_ord_id in marker["resolved_orders"]:
            continue
        book_id = getattr(order.command, "book_id", "")
        known = is_strategy_book(book_id) and book_id in state.books
        if known or not _order_needs_attribution(state, order):
            continue
        record_unattributed_order(
            state,
            cl_ord_id,
            book_id,
            "LEGACY_ORDER_WITHOUT_BOOK" if not book_id else "ORDER_BOOK_NOT_FOUND",
        )

    # A legacy UNKNOWN_COMMAND without a scope may be migrated only when the
    # stored detail proves which command it belongs to. Otherwise it keeps
    # blocking with an explicit reason; a bare code is never cleared.
    legacy: list[str] = []
    for entry in state.reconciliation.get("unresolved", []):
        if entry.get("code") != "UNKNOWN_COMMAND" or entry.get("scope"):
            continue
        detail = entry.get("detail") or {}
        command_id = str(detail.get("command_id") or "")
        record = state.commands.get(command_id)
        if command_id and record is not None and record.kind == "PLACE":
            entry["scope"] = command_id
            continue
        legacy.append(command_id or str(detail.get("cl_ord_id") or "UNKNOWN"))
    marker["legacy_unknown_commands"] = sorted(set(marker["legacy_unknown_commands"]) | set(legacy))

    mismatch = conservation_mismatch(state)
    marker["inconsistent"] = mismatch
    return BookMigrationReport(
        initialized=initialized,
        unattributed_orders=tuple(sorted(marker["unattributed_orders"])),
        inconsistent=mismatch,
        legacy_unknown_commands=tuple(marker["legacy_unknown_commands"]),
    )


def book_reasons(state: AccountState) -> list[str]:
    marker = state.reconciliation.get(BOOK_LEDGER_KEY) or {}
    reasons: list[str] = []
    if marker.get("unattributed_orders"):
        reasons.append("BOOK_ATTRIBUTION_UNKNOWN")
    if marker.get("inconsistent"):
        reasons.append("BOOK_LEDGER_INCONSISTENT")
    if marker.get("legacy_unknown_commands"):
        reasons.append("LEGACY_UNKNOWN_COMMAND_UNATTRIBUTED")
    return reasons


def book_snapshot(
    state: AccountState,
    book: BookLedger,
    *,
    marks: dict[str, Any],
    now_ms: int,
    max_mark_age_ms: int,
) -> dict[str, Any]:
    """Deterministic per-book balances, budget and equity for later phases."""
    assets = sorted(set(book.balances) | {hold.asset for hold in book.holds.values()})
    balances: dict[str, str] = {}
    held: dict[str, str] = {}
    available: dict[str, str] = {}
    equity = Decimal(0)
    valuation_complete = True
    valuations: dict[str, Any] = {}
    for asset in assets:
        total = book.balances.get(asset, Decimal(0))
        holds = book_hold_amount(book, asset)
        balances[asset] = str(total)
        held[asset] = str(holds)
        available[asset] = str(total - holds)
        if total == 0:
            continue
        if asset == QUOTE_ASSET:
            price = Decimal(1)
            price_at_ms = now_ms
            source = "QUOTE_UNIT"
        else:
            mark = marks.get(f"{asset}{QUOTE_ASSET}")
            if mark is None:
                valuation_complete = False
                continue
            try:
                price = Decimal(str(mark["price"]))
                price_at_ms = int(mark["at_ms"])
            except (KeyError, TypeError, ValueError, DecimalException):
                valuation_complete = False
                continue
            if (
                not price.is_finite()
                or price <= 0
                or not 0 <= now_ms - price_at_ms <= max_mark_age_ms
            ):
                valuation_complete = False
                continue
            source = "MARK"
        equity += total * price
        valuations[asset] = {
            "price": str(price),
            "at_ms": price_at_ms,
            "source": source,
        }
    return {
        "book_id": book.book_id,
        "balances": balances,
        "positions": {symbol: str(qty) for symbol, qty in sorted(book.positions.items())},
        "held": held,
        "available": available,
        "equity": str(equity) if valuation_complete else None,
        "valuation_complete": valuation_complete,
        "valuations": valuations,
    }
