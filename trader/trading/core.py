"""Pure spot economics shared by live transactions and historical simulation.

These functions perform no IO. Callers provide account serialization and a
rollback boundary when composing several mutations into a unit of work.
"""

from dataclasses import fields
from decimal import Decimal
from typing import Any

from trader.trading.models import (
    AccountState,
    Conflict,
    Execution,
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
    if cl_ord_id in state.orders:
        raise Conflict("ORDER_ID_ALREADY_EXISTS")
    hold = SpotRiskEngine(policy).evaluate(state, command, now_ms)
    order = OrderAggregate(cl_ord_id=cl_ord_id, command=command)
    state.orders[cl_ord_id] = order
    state.holds[cl_ord_id] = hold
    return order


def ledger_entry(
    state: AccountState,
    execution: Execution,
    leg: str,
    asset: str,
    delta: Decimal,
    kind: str,
) -> None:
    state.balances[asset] = state.balances.get(asset, Decimal(0)) + delta
    state.ledger.append(
        {
            "entry_id": f"{execution.key}:{leg}",
            "execution_key": execution.key,
            "account_id": execution.account_id,
            "cl_ord_id": execution.cl_ord_id,
            "asset": asset,
            "delta": str(delta),
            "kind": kind,
            "occurred_at_ms": execution.occurred_at_ms,
        }
    )


def apply_ledger(state: AccountState, command: PlaceOrderCommand, execution: Execution) -> None:
    if execution.fee > 0 and not execution.fee_asset:
        raise TradingError("FEE_ASSET_REQUIRED")
    charge = execution.qty * execution.price
    if command.side == "BUY":
        ledger_entry(state, execution, "quote", command.quote_asset, -charge, "trade")
        ledger_entry(state, execution, "base", command.base_asset, execution.qty, "trade")
    else:
        ledger_entry(state, execution, "base", command.base_asset, -execution.qty, "trade")
        ledger_entry(state, execution, "quote", command.quote_asset, charge, "trade")
    if execution.fee > 0:
        ledger_entry(state, execution, "fee", execution.fee_asset, -execution.fee, "fee")


def apply_position(state: AccountState, command: PlaceOrderCommand, execution: Execution) -> None:
    signed = execution.qty if command.side == "BUY" else -execution.qty
    if execution.fee_asset == command.base_asset:
        signed -= execution.fee
    state.positions[command.symbol] = state.positions.get(command.symbol, Decimal(0)) + signed


def release_hold(
    state: AccountState,
    command: PlaceOrderCommand,
    execution: Execution,
    cl_ord_id: str,
    policy: RiskPolicy,
) -> None:
    hold = state.holds.get(cl_ord_id)
    if hold is None:
        return
    release = execution.qty
    if command.side == "BUY":
        release *= command.price * (Decimal(1) + policy.fee_rate)
    hold.amount -= release
    if hold.amount <= 0:
        state.holds.pop(cl_ord_id, None)


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
    order.apply(execution)
    apply_ledger(state, order.command, execution)
    apply_position(state, order.command, execution)
    release_hold(state, order.command, execution, execution.cl_ord_id, policy)
    state.executions[execution.key] = execution.content
    return True
