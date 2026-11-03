"""Pure spot economics shared by live transactions and historical simulation.

These functions perform no IO. Callers provide account serialization and a
rollback boundary when composing several mutations into a unit of work.
"""

from dataclasses import fields
from decimal import Decimal
from typing import Any

from trader.trading import books as book_ledger
from trader.trading.models import (
    AccountState,
    Conflict,
    Execution,
    Hold,
    OrderAggregate,
    PlaceOrderCommand,
    TradingError,
)
from trader.trading.risk import RiskPolicy, SpotRiskEngine


def policy_from_config(config: dict[str, Any], fee_rate: Decimal) -> RiskPolicy:
    allowed = {item.name for item in fields(RiskPolicy)}
    unknown = set(config) - allowed
    if unknown:
        raise ValueError("Unknown risk policy fields: " + ", ".join(sorted(unknown)))
    values = {"fee_rate": fee_rate}
    for key, value in config.items():
        if key in {"max_mark_age_ms", "max_reconciliation_age_ms"}:
            if isinstance(value, bool) or Decimal(str(value)) != int(value):
                raise ValueError("Risk freshness bounds must be integer milliseconds")
            values[key] = int(value)
        else:
            if key == "fee_rate" and value is None:
                raise ValueError("Risk fee_rate must be finite and nonnegative")
            values[key] = None if value is None else Decimal(str(value))
    return RiskPolicy(**values)


def reserve_order(
    state: AccountState,
    command: PlaceOrderCommand,
    cl_ord_id: str,
    policy: RiskPolicy,
    now_ms: int,
) -> OrderAggregate:
    """Account admission, account hold and book hold in one caller transaction."""
    if cl_ord_id in state.orders:
        raise Conflict("ORDER_ID_ALREADY_EXISTS")
    hold = SpotRiskEngine(policy).evaluate(state, command, now_ms)
    order = OrderAggregate(cl_ord_id=cl_ord_id, command=command)
    state.orders[cl_ord_id] = order
    state.holds[cl_ord_id] = hold
    # The book layer is part of the same unit of work: if it fails, the caller's
    # account transaction (including the account-level hold) rolls back.
    book_ledger.reserve_book_hold(state, command.book_id, cl_ord_id, hold)
    return order


def release_order_hold(state: AccountState, order: OrderAggregate) -> None:
    """Release both reservation layers for a terminal/rejected order.

    A migrated or still unattributed order has no strategy book, so its
    reservation is mirrored on ``UNALLOCATED``. Resolving the owner through
    ``execution_book_id`` releases that layer as well; addressing the book by
    the (empty) command field would silently strand the reserved funds.
    """
    state.holds.pop(order.cl_ord_id, None)
    book_id = book_ledger.execution_book_id(state, order)
    book_ledger.release_book_hold(state, book_id, order.cl_ord_id)


def allocate_to_book(state: AccountState, book_id: str, asset: str, amount: Decimal) -> None:
    book_ledger.allocate(state, book_id, asset, amount)


def book_budget_available(
    state: AccountState, book_id: str, asset: str, cl_ord_id: str = ""
) -> Decimal:
    """Per-book spendable budget for one order, including its own reservation.

    Shared pure rule: a book funds itself only, never the account total and
    never a neighbouring book. Live admission and the historical simulator use
    the same book facts, so a fill that no longer fits its book is refused
    instead of overrunning the book budget.
    """
    return book_ledger.book_available_including(state, book_id, asset, cl_ord_id)


def ledger_entry(
    state: AccountState,
    execution: Execution,
    leg: str,
    asset: str,
    delta: Decimal,
    kind: str,
    book_id: str,
) -> None:
    state.balances[asset] = state.balances.get(asset, Decimal(0)) + delta
    book = state.books.get(book_id)
    if book is None:
        raise TradingError("BOOK_NOT_FOUND")
    book.balances[asset] = book.balances.get(asset, Decimal(0)) + delta
    state.ledger.append(
        {
            "entry_id": f"{execution.key}:{leg}",
            "execution_key": execution.key,
            "account_id": execution.account_id,
            "cl_ord_id": execution.cl_ord_id,
            "book_id": book_id,
            "asset": asset,
            "delta": str(delta),
            "kind": kind,
            "occurred_at_ms": execution.occurred_at_ms,
        }
    )


def apply_ledger(
    state: AccountState, command: PlaceOrderCommand, execution: Execution, book_id: str
) -> None:
    if execution.fee > 0 and not execution.fee_asset:
        raise TradingError("FEE_ASSET_REQUIRED")
    charge = execution.qty * execution.price
    if command.side == "BUY":
        ledger_entry(state, execution, "quote", command.quote_asset, -charge, "trade", book_id)
        ledger_entry(state, execution, "base", command.base_asset, execution.qty, "trade", book_id)
    else:
        ledger_entry(state, execution, "base", command.base_asset, -execution.qty, "trade", book_id)
        ledger_entry(state, execution, "quote", command.quote_asset, charge, "trade", book_id)
    if execution.fee > 0:
        ledger_entry(state, execution, "fee", execution.fee_asset, -execution.fee, "fee", book_id)


def apply_position(
    state: AccountState, command: PlaceOrderCommand, execution: Execution, book_id: str
) -> None:
    signed = execution.qty if command.side == "BUY" else -execution.qty
    if execution.fee_asset == command.base_asset:
        signed -= execution.fee
    state.positions[command.symbol] = state.positions.get(command.symbol, Decimal(0)) + signed
    book = state.books.get(book_id)
    if book is None:
        raise TradingError("BOOK_NOT_FOUND")
    book.positions[command.symbol] = book.positions.get(command.symbol, Decimal(0)) + signed


def release_hold(
    state: AccountState,
    command: PlaceOrderCommand,
    execution: Execution,
    cl_ord_id: str,
    policy: RiskPolicy,
    book_id: str,
) -> None:
    """Release the actual execution cost from both reservation layers.

    A BUY releases the quote it actually spent (``qty * execution price``)
    plus the fee only when that fee is charged in the quote asset. The
    configured ``fee_rate`` is an admission buffer, not a fabricated execution
    fee: a zero-fee partial fill must not consume it, and BASE or third-asset
    fees are already booked in their own currencies, so they release no quote.
    A SELL still releases the sold base quantity.
    """
    release = execution.qty
    asset = command.base_asset
    if command.side == "BUY":
        asset = command.quote_asset
        release = execution.qty * execution.price
        if execution.fee > 0 and execution.fee_asset == command.quote_asset:
            release += execution.fee
    hold = state.holds.get(cl_ord_id)
    if hold is not None:
        hold.amount -= release
        if hold.amount <= 0:
            state.holds.pop(cl_ord_id, None)
    book_ledger.release_book_execution_hold(state, book_id, cl_ord_id, asset, release)


def book_execution(state: AccountState, execution: Execution, policy: RiskPolicy) -> bool:
    """Apply a genuine execution once inside the caller's account unit of work."""
    if execution.account_id != state.account_id:
        raise Conflict("ACCOUNT_MISMATCH")
    existing = state.executions.get(execution.key)
    if existing is not None:
        if existing != execution.content:
            raise Conflict("EXECUTION_KEY_CONFLICT")
        return False
    if execution.fee > 0 and not execution.fee_asset:
        raise TradingError("FEE_ASSET_REQUIRED")
    order = state.orders[execution.cl_ord_id]
    book_id = book_ledger.execution_book_id(state, order)
    order.apply(execution)
    apply_ledger(state, order.command, execution, book_id)
    apply_position(state, order.command, execution, book_id)
    release_hold(state, order.command, execution, execution.cl_ord_id, policy, book_id)
    state.executions[execution.key] = execution.content
    return True


def assert_book_conservation(state: AccountState) -> None:
    book_ledger.assert_conservation(state)
