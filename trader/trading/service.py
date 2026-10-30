"""Unified spot trading application service.

One account transaction owns the command record, order intent, balance hold,
ledger entries, execution facts and outbox event. Network calls happen outside
the transaction. Exchange outcomes are facts, never local state transitions.
"""

from __future__ import annotations

import hashlib
import json
from decimal import Decimal
from typing import Any, Awaitable, Callable

from trader.trading.models import (
    TERMINAL,
    AccountRiskView,
    AccountState,
    CancelOrderCommand,
    CommandReceipt,
    CommandRecord,
    Conflict,
    ExchangeOrderResult,
    ExchangeOutcome,
    Execution,
    NotReady,
    OrderAggregate,
    OrderObservation,
    PlaceOrderCommand,
    ReconcileSnapshot,
    TradingError,
    json_value,
)
from trader.trading.risk import RiskPolicy, SpotRiskEngine, available_balance

TERMINAL_COMMAND = {"CONFIRMED", "FAILED"}
PERSISTENT_DIFFERENCES = {
    "EXECUTION_KEY_CONFLICT",
    "BALANCE_MISMATCH",
    "UNKNOWN_EXECUTION",
    "UNKNOWN_ORDER",
}


def _canonical(value: Any) -> str:
    return json.dumps(json_value(value), sort_keys=True, separators=(",", ":"), allow_nan=False)


def _place_payload(command: PlaceOrderCommand) -> dict[str, Any]:
    # decision_id is tracing metadata, not an economic order parameter.
    return {
        "account_id": command.account_id,
        "symbol": command.symbol,
        "base_asset": command.base_asset,
        "quote_asset": command.quote_asset,
        "side": command.side,
        "qty": command.qty,
        "price": command.price,
        "deployment_id": command.deployment_id,
        "order_type": command.order_type,
        "time_in_force": command.time_in_force,
    }


def _cancel_payload(command: CancelOrderCommand) -> dict[str, Any]:
    return {"account_id": command.account_id, "cl_ord_id": command.cl_ord_id}


def _digest(value: Any) -> str:
    return hashlib.sha256(_canonical(value).encode()).hexdigest()


class TradingService:
    def __init__(
        self,
        store,
        exchange,
        policy: RiskPolicy | None = None,
        clock: Callable[[], int] | None = None,
    ):
        self.store = store
        self.exchange = exchange
        self.risk = SpotRiskEngine(policy)
        self.policy = self.risk.policy
        self.clock = clock or (lambda: 0)

    # ------------------------------------------------------------------ commands
    async def place(self, command: PlaceOrderCommand, actor: str = "system") -> CommandReceipt:
        payload_hash = _digest(_place_payload(command))
        cl_ord_id = self.cl_ord_id_for(command.account_id, command.command_id)
        now = self.clock()

        async with self.store.transaction(command.account_id) as state:
            existing = state.commands.get(command.command_id)
            if existing is not None:
                if existing.payload_hash != payload_hash:
                    raise Conflict("COMMAND_ID_REUSED_WITH_DIFFERENT_PARAMETERS")
                return self._receipt(state, existing)

            # Admission, reservation and the order intent commit atomically.
            hold = self.risk.evaluate(state, command, now)
            if cl_ord_id in state.orders:
                raise Conflict("ORDER_ID_ALREADY_EXISTS")
            order = OrderAggregate(cl_ord_id=cl_ord_id, command=command)
            state.orders[cl_ord_id] = order
            state.holds[cl_ord_id] = hold
            record = CommandRecord(
                command_id=command.command_id,
                cl_ord_id=cl_ord_id,
                kind="PLACE",
                payload_hash=payload_hash,
                status="QUEUED",
                actor=actor,
            )
            state.commands[command.command_id] = record
            self._emit(
                state,
                "order.queued",
                cl_ord_id,
                command.decision_id or command.command_id,
                {
                    "command_id": command.command_id,
                    "symbol": command.symbol,
                    "side": command.side,
                    "qty": command.qty,
                    "price": command.price,
                    "order_type": command.order_type,
                },
                now,
            )

        # Only the caller that wins the QUEUED -> SENDING transition may call the venue.
        if not await self._mark_sending(command.account_id, command.command_id):
            return await self._current_receipt(command.account_id, command.command_id)

        result = await self.exchange.place_order(command, cl_ord_id)
        await self._apply_place_result(command.account_id, command.command_id, result)
        return await self._current_receipt(command.account_id, command.command_id)

    async def cancel(
        self,
        command_id: str,
        account_id: str,
        cl_ord_id: str,
        actor: str = "system",
    ) -> CommandReceipt:
        request = CancelOrderCommand(
            command_id=command_id, account_id=account_id, cl_ord_id=cl_ord_id
        )
        payload_hash = _digest(_cancel_payload(request))
        now = self.clock()

        async with self.store.transaction(account_id) as state:
            existing = state.commands.get(command_id)
            if existing is not None:
                if existing.payload_hash != payload_hash:
                    raise Conflict("COMMAND_ID_REUSED_WITH_DIFFERENT_PARAMETERS")
                return self._receipt(state, existing)
            if cl_ord_id not in state.orders:
                raise TradingError("ORDER_NOT_FOUND")
            record = CommandRecord(
                command_id=command_id,
                cl_ord_id=cl_ord_id,
                kind="CANCEL",
                payload_hash=payload_hash,
                status="QUEUED",
                actor=actor,
            )
            state.commands[command_id] = record
            self._emit(
                state, "cancel.queued", cl_ord_id, command_id, {"command_id": command_id}, now
            )

        if not await self._mark_sending(account_id, command_id):
            return await self._current_receipt(account_id, command_id)

        order = await self.store.read(account_id)
        symbol = order.orders[cl_ord_id].command.symbol if cl_ord_id in order.orders else ""
        result = await self.exchange.cancel_order(account_id, cl_ord_id, command_id, symbol)
        await self._apply_cancel_result(account_id, command_id, result)
        return await self._current_receipt(account_id, command_id)

    async def control(
        self,
        command_id: str,
        account_id: str,
        action: str,
        actor: str = "system",
    ) -> CommandReceipt:
        """Human/system control actions are commands, not order-state shortcuts."""
        if action not in {"BLOCK_NEW_RISK", "RESUME_NEW_RISK", "CANCEL_ALL"}:
            raise TradingError("UNSUPPORTED_CONTROL_ACTION")
        payload_hash = _digest({"account_id": account_id, "action": action})
        now = self.clock()

        active: list[str] = []
        async with self.store.transaction(account_id) as state:
            existing = state.commands.get(command_id)
            if existing is not None:
                if existing.payload_hash != payload_hash:
                    raise Conflict("COMMAND_ID_REUSED_WITH_DIFFERENT_PARAMETERS")
                return self._receipt(state, existing)
            record = CommandRecord(
                command_id=command_id,
                cl_ord_id="",
                kind="CONTROL",
                payload_hash=payload_hash,
                status="QUEUED",
                actor=actor,
            )
            state.commands[command_id] = record
            if action == "BLOCK_NEW_RISK":
                state.control["new_risk_blocked"] = True
            elif action == "RESUME_NEW_RISK":
                state.control["new_risk_blocked"] = False
            else:
                active = [
                    order.cl_ord_id
                    for order in state.orders.values()
                    if order.status not in TERMINAL and order.remaining_qty > 0
                ]
            self._emit(
                state,
                "control.requested",
                "",
                command_id,
                {"action": action, "active_orders": active},
                now,
            )

        if action != "CANCEL_ALL":
            async with self.store.transaction(account_id) as state:
                record = state.commands[command_id]
                record.status = "CONFIRMED"
                self._emit(state, "control.applied", "", command_id, {"action": action}, now)
                self._refresh_status(state)
            return await self._current_receipt(account_id, command_id)

        outcomes: list[dict[str, str]] = []
        for cl_ord_id in active:
            try:
                child = await self.cancel(
                    f"{command_id}:{cl_ord_id}", account_id, cl_ord_id, actor=actor
                )
                outcomes.append({"cl_ord_id": cl_ord_id, "status": child.status})
            except Exception as exc:
                outcomes.append({"cl_ord_id": cl_ord_id, "status": "FAILED", "error": str(exc)})

        statuses = {item["status"] for item in outcomes}
        final = (
            "UNKNOWN"
            if "UNKNOWN" in statuses
            else "FAILED" if "FAILED" in statuses else "CONFIRMED"
        )
        async with self.store.transaction(account_id) as state:
            record = state.commands[command_id]
            record.status = final
            record.error = "" if final == "CONFIRMED" else "CANCEL_ALL_INCOMPLETE"
            self._emit(
                state,
                "control.applied",
                "",
                command_id,
                {"action": action, "outcomes": outcomes},
                now,
            )
            self._refresh_status(state)
        return await self._current_receipt(account_id, command_id)

    # ------------------------------------------------------------------ recovery
    async def resume_queued(self, account_id: str) -> list[CommandReceipt]:
        """Send commands that provably never reached SENDING before a crash.

        A QUEUED record means the pre-send SENDING marker was never committed, so
        the venue call never happened. SENDING/UNKNOWN commands are never resent.
        """
        state = await self.store.read(account_id)
        receipts: list[CommandReceipt] = []
        for record in list(state.commands.values()):
            if record.status != "QUEUED":
                continue
            if not await self._mark_sending(account_id, record.command_id):
                continue
            if record.kind == "PLACE":
                order = state.orders.get(record.cl_ord_id)
                if order is None:
                    continue
                result = await self.exchange.place_order(order.command, order.cl_ord_id)
                await self._apply_place_result(account_id, record.command_id, result)
            elif record.kind == "CANCEL":
                order = state.orders.get(record.cl_ord_id)
                symbol = order.command.symbol if order is not None else ""
                result = await self.exchange.cancel_order(
                    account_id, record.cl_ord_id, record.command_id, symbol
                )
                await self._apply_cancel_result(account_id, record.command_id, result)
            else:
                async with self.store.transaction(account_id) as current:
                    current.commands[record.command_id].status = "UNKNOWN"
                    current.commands[record.command_id].error = "CONTROL_INTERRUPTED"
                    self._add_unresolved(
                        current,
                        "CONTROL_INTERRUPTED",
                        {"command_id": record.command_id},
                    )
                    self._refresh_status(current)
            receipts.append(await self._current_receipt(account_id, record.command_id))
        return receipts

    # ------------------------------------------------------------------ facts
    async def record_execution(self, execution: Execution) -> bool:
        outcome = "applied"
        async with self.store.transaction(execution.account_id) as state:
            key = execution.key
            existing = state.executions.get(key)
            if existing is not None:
                if _canonical(existing) != _canonical(execution.content):
                    self._add_unresolved(
                        state,
                        "EXECUTION_KEY_CONFLICT",
                        {"existing": existing, "incoming": execution.content},
                    )
                    self._emit(
                        state,
                        "reconciliation.conflict",
                        execution.cl_ord_id,
                        execution.exec_id,
                        {"execution_key": key, "code": "EXECUTION_KEY_CONFLICT"},
                        self.clock(),
                        source="reconciliation",
                    )
                    outcome = "conflict"
                else:
                    outcome = "duplicate"
            else:
                order = state.orders.get(execution.cl_ord_id)
                if order is None:
                    state.executions[key] = dict(execution.content, applied=False)
                    self._add_unresolved(
                        state,
                        "UNKNOWN_EXECUTION",
                        {"execution_key": key, "cl_ord_id": execution.cl_ord_id},
                    )
                    self._emit(
                        state,
                        "execution.unmatched",
                        execution.cl_ord_id,
                        execution.exec_id,
                        {"execution_key": key},
                        self.clock(),
                        source="reconciliation",
                    )
                    outcome = "unknown"
                else:
                    try:
                        order.apply(execution)
                    except Conflict as exc:
                        state.executions[key] = dict(execution.content, applied=False)
                        self._add_unresolved(
                            state,
                            "EXECUTION_QUANTITY_CONFLICT",
                            {
                                "execution_key": key,
                                "cl_ord_id": execution.cl_ord_id,
                                "reason": str(exc),
                            },
                        )
                        self._emit(
                            state,
                            "reconciliation.conflict",
                            execution.cl_ord_id,
                            execution.exec_id,
                            {"execution_key": key, "code": "EXECUTION_QUANTITY_CONFLICT"},
                            self.clock(),
                            source="reconciliation",
                        )
                        outcome = "conflict"
                    else:
                        self._apply_ledger(state, order.command, execution)
                        self._apply_position(state, order.command, execution)
                        self._release_hold(state, order.command, execution, execution.cl_ord_id)
                        state.executions[key] = execution.content
                        self._emit(
                            state,
                            "execution.booked",
                            execution.cl_ord_id,
                            execution.exec_id,
                            {
                                "execution_key": key,
                                "qty": execution.qty,
                                "price": execution.price,
                                "fee": execution.fee,
                                "fee_asset": execution.fee_asset,
                            },
                            execution.occurred_at_ms or self.clock(),
                            source="exchange",
                        )
                        self._reconcile_order_evidence(state)
                        self._post_execution(state, execution)
            self._refresh_status(state)

        if outcome == "conflict":
            raise Conflict("EXECUTION_KEY_CONFLICT")
        return outcome == "applied"

    async def observe(self, observation: OrderObservation) -> bool:
        async with self.store.transaction(observation.account_id) as state:
            order = state.orders.get(observation.cl_ord_id)
            if order is None:
                self._add_unresolved(
                    state,
                    "UNKNOWN_ORDER",
                    {"cl_ord_id": observation.cl_ord_id, "source": observation.source},
                )
                self._refresh_status(state)
                return False
            try:
                order.observe(observation)
            except Conflict as exc:
                self._add_unresolved(
                    state,
                    "ORDER_OBSERVATION_CONFLICT",
                    {"cl_ord_id": observation.cl_ord_id, "reason": str(exc)},
                )
                self._refresh_status(state)
                return False

            if observation.status == "FILLED" and order.booked_qty != order.command.qty:
                self._add_unresolved(
                    state,
                    "FILL_OBSERVATION_MISMATCH",
                    {
                        "cl_ord_id": order.cl_ord_id,
                        "observed_qty": observation.cumulative_qty,
                        "booked_qty": order.booked_qty,
                    },
                )
            elif observation.status in TERMINAL:
                self._resolve_unresolved(state, "FILL_OBSERVATION_MISMATCH")
                self._release_remaining_hold(state, order.cl_ord_id)

            self._reconcile_order_evidence(state)
            self._emit(
                state,
                "order.observed",
                observation.cl_ord_id,
                observation.cl_ord_id,
                {
                    "status": observation.status,
                    "cumulative_qty": observation.cumulative_qty,
                    "broker_order_id": observation.broker_order_id,
                },
                observation.observed_at_ms or self.clock(),
                source=observation.source,
            )
            self._refresh_status(state)
            return True

    async def recover(self, snapshot: ReconcileSnapshot) -> AccountRiskView:
        now = self.clock()
        async with self.store.transaction(snapshot.account_id) as state:
            previous_generation = state.reconciliation.get("generation")
            previous_checked = state.reconciliation.get("checked_at_ms")
            stale = (
                previous_generation is not None and snapshot.generation <= int(previous_generation)
            ) or (previous_checked is not None and snapshot.checked_at_ms <= int(previous_checked))
            if stale:
                self._add_unresolved(
                    state,
                    "STALE_SNAPSHOT",
                    {"generation": snapshot.generation, "checked_at_ms": snapshot.checked_at_ms},
                )
            elif not snapshot.complete:
                self._add_unresolved(
                    state, "INCOMPLETE_SNAPSHOT", {"generation": snapshot.generation}
                )
                state.reconciliation.update(
                    checked_at_ms=snapshot.checked_at_ms,
                    generation=snapshot.generation,
                    stream_healthy=snapshot.stream_healthy,
                )
            else:
                self._apply_reconcile_snapshot(state, snapshot, now)
            self._refresh_status(state)

        return await self.risk_view(snapshot.account_id)

    async def resolve(self, account_id: str, code: str, actor: str = "system") -> AccountRiskView:
        async with self.store.transaction(account_id) as state:
            self._resolve_unresolved(state, code)
            self._emit(
                state,
                "reconciliation.resolved",
                "",
                code,
                {"code": code, "resolved_by": actor},
                self.clock(),
                source="operator",
            )
            self._refresh_status(state)
        return await self.risk_view(account_id)

    # ------------------------------------------------------------------ queries
    async def risk_view(self, account_id: str, now_ms: int | None = None) -> AccountRiskView:
        state = await self.store.read(account_id)
        now = self.clock() if now_ms is None else now_ms
        reasons = self._reasons(state, now)
        assets: dict[str, dict[str, Decimal]] = {}
        for asset in sorted(
            set(state.balances)
            | set(state.external_locked)
            | {hold.asset for hold in state.holds.values()}
        ):
            total = state.balances.get(asset, Decimal(0))
            external = state.external_locked.get(asset, Decimal(0))
            held = sum(
                (hold.amount for hold in state.holds.values() if hold.asset == asset),
                Decimal(0),
            )
            assets[asset] = {
                "total": total,
                "external_locked": external,
                "held": held,
                "available": total - external - held,
            }
        return AccountRiskView(
            account_id=account_id,
            status=self._status(state, reasons),
            account_version=state.version,
            generation=int(state.reconciliation.get("generation", 0)),
            checked_at_ms=state.reconciliation.get("checked_at_ms"),
            reasons=tuple(reasons),
            assets=assets,
            positions=dict(state.positions),
            valuation_complete=self._valuation_complete(state, now),
        )

    async def command(self, account_id: str, command_id: str) -> dict[str, Any] | None:
        state = await self.store.read(account_id)
        record = state.commands.get(command_id)
        if record is None:
            return None
        order = state.orders.get(record.cl_ord_id)
        return {
            "command_id": record.command_id,
            "cl_ord_id": record.cl_ord_id,
            "kind": record.kind,
            "status": record.status,
            "error": record.error,
            "actor": record.actor,
            "order_status": order.status if order else "",
        }

    async def order(self, account_id: str, cl_ord_id: str) -> dict[str, Any] | None:
        state = await self.store.read(account_id)
        order = state.orders.get(cl_ord_id)
        if order is None:
            return None
        return self._order_dict(order)

    async def list_orders(self, account_id: str, limit: int = 200) -> list[dict[str, Any]]:
        state = await self.store.read(account_id)
        return [self._order_dict(order) for order in list(state.orders.values())[-limit:]]

    @staticmethod
    def _order_dict(order: OrderAggregate) -> dict[str, Any]:
        return {
            "cl_ord_id": order.cl_ord_id,
            "status": order.status,
            "broker_order_id": order.broker_order_id,
            "symbol": order.command.symbol,
            "base_asset": order.command.base_asset,
            "quote_asset": order.command.quote_asset,
            "side": order.command.side,
            "qty": str(order.command.qty),
            "price": str(order.command.price),
            "order_type": order.command.order_type,
            "booked_qty": str(order.booked_qty),
            "observed_qty": str(order.observed_qty),
            "remaining_qty": str(order.remaining_qty),
            "deployment_id": order.command.deployment_id,
        }

    async def metrics(self, account_id: str, now_ms: int | None = None) -> dict[str, Any]:
        state = await self.store.read(account_id)
        now = self.clock() if now_ms is None else now_ms
        checked = state.reconciliation.get("checked_at_ms")
        return {
            "status": self._status(state, self._reasons(state, now)),
            "unknown_commands": sum(
                1 for record in state.commands.values() if record.status == "UNKNOWN"
            ),
            "unbooked_fills": sum(
                1 for order in state.orders.values() if order.observed_qty > order.booked_qty
            ),
            "balance_anomalies": sum(1 for amount in state.balances.values() if amount < 0),
            "active_holds": len(state.holds),
            "reconciliation_age_ms": None if checked is None else max(0, now - int(checked)),
            "outbox_backlog": len(await self.store.pending_outbox(account_id)),
            "unresolved": [entry["code"] for entry in state.reconciliation.get("unresolved", [])],
        }

    # ------------------------------------------------------------------ outbox
    async def dispatch_outbox(
        self,
        account_id: str,
        notifier: Callable[[dict[str, Any]], Awaitable[None]],
    ) -> int:
        pending = await self.store.pending_outbox(account_id)
        delivered: list[str] = []
        try:
            for item in pending:
                try:
                    await notifier(item["data"])
                except Exception:
                    # Notification failure never changes booked accounting; retry later.
                    break
                delivered.append(item["event_id"])
        finally:
            if delivered:
                await self.store.mark_outbox_delivered(account_id, delivered)
        return len(delivered)

    # ------------------------------------------------------------------ helpers
    @staticmethod
    def cl_ord_id_for(account_id: str, command_id: str) -> str:
        return "qts-" + hashlib.sha256(f"{account_id}:{command_id}".encode()).hexdigest()[:24]

    async def _mark_sending(self, account_id: str, command_id: str) -> bool:
        async with self.store.transaction(account_id) as state:
            record = state.commands.get(command_id)
            if record is None or record.status != "QUEUED":
                return False
            record.status = "SENDING"
            return True

    async def _current_receipt(self, account_id: str, command_id: str) -> CommandReceipt:
        state = await self.store.read(account_id)
        record = state.commands[command_id]
        return self._receipt(state, record)

    def _receipt(self, state: AccountState, record: CommandRecord) -> CommandReceipt:
        order = state.orders.get(record.cl_ord_id)
        return CommandReceipt(
            command_id=record.command_id,
            cl_ord_id=record.cl_ord_id,
            kind=record.kind,
            status=record.status,
            order_status=order.status if order else "",
            error=record.error,
        )

    async def _apply_place_result(
        self, account_id: str, command_id: str, result: ExchangeOrderResult
    ) -> None:
        now = self.clock()
        async with self.store.transaction(account_id) as state:
            record = state.commands.get(command_id)
            if record is None or record.status != "SENDING":
                return
            order = state.orders[record.cl_ord_id]
            if result.outcome == ExchangeOutcome.ACCEPTED:
                record.status = "CONFIRMED"
                self._resolve_unresolved(state, "UNKNOWN_COMMAND")
                observation = OrderObservation(
                    account_id=account_id,
                    cl_ord_id=order.cl_ord_id,
                    status=result.status or "OPEN",
                    cumulative_qty=(
                        result.cumulative_qty if result.cumulative_qty > 0 else order.booked_qty
                    ),
                    broker_order_id=result.broker_order_id,
                    observed_at_ms=now,
                )
                try:
                    order.observe(observation)
                except Conflict:
                    self._add_unresolved(
                        state,
                        "ORDER_OBSERVATION_CONFLICT",
                        {"cl_ord_id": order.cl_ord_id, "reason": "accepted_observation"},
                    )
                self._emit(
                    state,
                    "order.accepted",
                    order.cl_ord_id,
                    command_id,
                    {"broker_order_id": result.broker_order_id, "status": order.status},
                    now,
                    source="exchange",
                )
            elif result.outcome == ExchangeOutcome.REJECTED:
                record.status = "FAILED"
                record.error = result.reason or "REJECTED"
                self._resolve_unresolved(state, "UNKNOWN_COMMAND")
                order.status = "REJECTED"
                state.holds.pop(order.cl_ord_id, None)
                self._emit(
                    state,
                    "order.rejected",
                    order.cl_ord_id,
                    command_id,
                    {"reason": record.error},
                    now,
                    source="exchange",
                )
            else:
                record.status = "UNKNOWN"
                record.error = result.reason or result.outcome.value
                self._add_unresolved(
                    state,
                    "UNKNOWN_COMMAND",
                    {"command_id": command_id, "cl_ord_id": order.cl_ord_id},
                )
                self._emit(
                    state,
                    "order.unknown",
                    order.cl_ord_id,
                    command_id,
                    {"outcome": result.outcome.value, "reason": record.error},
                    now,
                    source="exchange",
                )
            self._refresh_status(state)

    async def _apply_cancel_result(
        self, account_id: str, command_id: str, result: ExchangeOrderResult
    ) -> None:
        now = self.clock()
        async with self.store.transaction(account_id) as state:
            record = state.commands.get(command_id)
            if record is None or record.status != "SENDING":
                return
            if result.outcome == ExchangeOutcome.ACCEPTED:
                record.status = "CONFIRMED"
                # Cancellation is confirmed only by a terminal order observation.
                self._emit(
                    state,
                    "cancel.requested",
                    record.cl_ord_id,
                    command_id,
                    {"broker_order_id": result.broker_order_id},
                    now,
                    source="exchange",
                )
            elif result.outcome == ExchangeOutcome.REJECTED:
                record.status = "FAILED"
                record.error = result.reason or "REJECTED"
                self._emit(
                    state,
                    "cancel.rejected",
                    record.cl_ord_id,
                    command_id,
                    {"reason": record.error},
                    now,
                    source="exchange",
                )
            else:
                record.status = "UNKNOWN"
                record.error = result.reason or result.outcome.value
                self._emit(
                    state,
                    "cancel.unknown",
                    record.cl_ord_id,
                    command_id,
                    {"outcome": result.outcome.value, "reason": record.error},
                    now,
                    source="exchange",
                )
            self._refresh_status(state)

    def _apply_position(
        self, state: AccountState, command: PlaceOrderCommand, execution: Execution
    ) -> None:
        signed = execution.qty if command.side == "BUY" else -execution.qty
        state.positions[command.symbol] = state.positions.get(command.symbol, Decimal(0)) + signed

    def _apply_ledger(
        self, state: AccountState, command: PlaceOrderCommand, execution: Execution
    ) -> None:
        quote = command.quote_asset
        base = command.base_asset
        charge = execution.qty * execution.price
        if command.side == "BUY":
            self._ledger_entry(state, execution, "quote", quote, -charge, "trade")
            self._ledger_entry(state, execution, "base", base, execution.qty, "trade")
        else:
            self._ledger_entry(state, execution, "base", base, -execution.qty, "trade")
            self._ledger_entry(state, execution, "quote", quote, charge, "trade")
        if execution.fee > 0:
            if not execution.fee_asset:
                raise TradingError("FEE_ASSET_REQUIRED")
            self._ledger_entry(state, execution, "fee", execution.fee_asset, -execution.fee, "fee")

    def _ledger_entry(
        self,
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

    def _release_hold(
        self,
        state: AccountState,
        command: PlaceOrderCommand,
        execution: Execution,
        cl_ord_id: str,
    ) -> None:
        hold = state.holds.get(cl_ord_id)
        if hold is None:
            return
        if command.side == "BUY":
            release = execution.qty * command.price * (Decimal(1) + self.policy.fee_rate)
        else:
            release = execution.qty
        hold.amount -= release
        if hold.amount <= 0:
            state.holds.pop(cl_ord_id, None)

    def _release_remaining_hold(self, state: AccountState, cl_ord_id: str) -> None:
        state.holds.pop(cl_ord_id, None)

    def _reconcile_order_evidence(self, state: AccountState) -> None:
        for order in state.orders.values():
            if order.booked_qty == order.command.qty:
                if order.status not in TERMINAL:
                    order.status = "FILLED"
                self._release_remaining_hold(state, order.cl_ord_id)
                if order.observed_qty == order.command.qty:
                    self._resolve_unresolved(state, "FILL_OBSERVATION_MISMATCH")

    def _post_execution(self, state: AccountState, execution: Execution) -> None:
        """Post-write invariant check. A failure rolls the whole account transaction back."""
        key = execution.key
        if key not in state.executions or state.executions[key].get("applied") is False:
            raise TradingError("EXECUTION_NOT_PERSISTED")
        entry_ids = [entry["entry_id"] for entry in state.ledger]
        if len(entry_ids) != len(set(entry_ids)):
            raise TradingError("DUPLICATE_LEDGER_ENTRY")
        if any(not amount.is_finite() for amount in state.balances.values()):
            raise TradingError("NON_FINITE_BALANCE")

    def _apply_reconcile_snapshot(
        self, state: AccountState, snapshot: ReconcileSnapshot, now: int
    ) -> None:
        had_balances = bool(state.balances)
        expected = dict(state.balances)

        # Keep execution-key conflicts as evidence; reconciliation-owned codes are rebuilt.
        state.reconciliation["unresolved"] = [
            entry
            for entry in state.reconciliation.get("unresolved", [])
            if entry["code"] in PERSISTENT_DIFFERENCES
            or entry["code"] == "FILL_OBSERVATION_MISMATCH"
        ]

        open_ids = {observation.cl_ord_id for observation in snapshot.open_orders}

        for execution in snapshot.executions:
            key = execution.key
            existing = state.executions.get(key)
            if existing is not None:
                if _canonical(existing) != _canonical(execution.content):
                    self._add_unresolved(
                        state,
                        "EXECUTION_KEY_CONFLICT",
                        {"existing": existing, "incoming": execution.content},
                    )
                continue
            order = state.orders.get(execution.cl_ord_id)
            if order is None:
                state.executions[key] = dict(execution.content, applied=False)
                self._add_unresolved(
                    state,
                    "UNKNOWN_EXECUTION",
                    {"execution_key": key, "cl_ord_id": execution.cl_ord_id},
                )
                continue
            order.apply(execution)
            self._apply_ledger(state, order.command, execution)
            self._apply_position(state, order.command, execution)
            self._release_hold(state, order.command, execution, execution.cl_ord_id)
            state.executions[key] = execution.content
            self._emit(
                state,
                "execution.booked",
                execution.cl_ord_id,
                execution.exec_id,
                {
                    "execution_key": key,
                    "qty": execution.qty,
                    "price": execution.price,
                    "fee": execution.fee,
                    "fee_asset": execution.fee_asset,
                },
                execution.occurred_at_ms or now,
                source="rest_alignment",
            )

        for observation in snapshot.open_orders:
            order = state.orders.get(observation.cl_ord_id)
            if order is None:
                self._add_unresolved(
                    state,
                    "UNKNOWN_ORDER",
                    {"cl_ord_id": observation.cl_ord_id, "source": observation.source},
                )
                continue
            try:
                order.observe(observation)
            except Conflict as exc:
                self._add_unresolved(
                    state,
                    "ORDER_OBSERVATION_CONFLICT",
                    {"cl_ord_id": observation.cl_ord_id, "reason": str(exc)},
                )
                continue
            if observation.status == "FILLED" and order.booked_qty != order.command.qty:
                self._add_unresolved(
                    state,
                    "FILL_OBSERVATION_MISMATCH",
                    {
                        "cl_ord_id": order.cl_ord_id,
                        "observed_qty": observation.cumulative_qty,
                        "booked_qty": order.booked_qty,
                    },
                )
            elif observation.status in TERMINAL:
                self._resolve_unresolved(state, "FILL_OBSERVATION_MISMATCH")
                self._release_remaining_hold(state, order.cl_ord_id)

        after = dict(state.balances)
        if had_balances:
            assets = set(expected) | set(after) | set(snapshot.balances)
            mismatch = {
                asset: {
                    "expected": str(after.get(asset, Decimal(0))),
                    "exchange": str(snapshot.balances.get(asset, Decimal(0))),
                }
                for asset in assets
                if after.get(asset, Decimal(0)) != snapshot.balances.get(asset, Decimal(0))
            }
            if mismatch:
                self._add_unresolved(state, "BALANCE_MISMATCH", mismatch)

        state.balances = {asset: Decimal(value) for asset, value in snapshot.balances.items()}
        state.external_locked = {
            asset: Decimal(value) for asset, value in snapshot.external_locked.items()
        }
        state.reconciliation["marks"] = dict(snapshot.marks)
        state.reconciliation["stream_healthy"] = snapshot.stream_healthy
        state.reconciliation["checked_at_ms"] = snapshot.checked_at_ms
        state.reconciliation["generation"] = snapshot.generation
        state.reconciliation["complete"] = snapshot.complete

        # Commands whose venue visibility was ambiguous are resolved by the snapshot.
        for record in state.commands.values():
            if record.kind == "PLACE" and record.status in {"SENDING", "UNKNOWN"}:
                order = state.orders.get(record.cl_ord_id)
                visible = order is not None and (
                    order.broker_order_id
                    or order.booked_qty > 0
                    or order.status in TERMINAL
                    or order.cl_ord_id in open_ids
                )
                if visible:
                    record.status = "CONFIRMED"
                    self._resolve_unresolved(state, "UNKNOWN_COMMAND")
                else:
                    self._add_unresolved(
                        state,
                        "UNKNOWN_COMMAND",
                        {"command_id": record.command_id, "cl_ord_id": record.cl_ord_id},
                    )

        if snapshot.complete:
            for order in state.orders.values():
                if (
                    order.status not in TERMINAL
                    and order.remaining_qty > 0
                    and order.cl_ord_id not in open_ids
                ):
                    record = next(
                        (
                            value
                            for value in state.commands.values()
                            if value.cl_ord_id == order.cl_ord_id and value.kind == "PLACE"
                        ),
                        None,
                    )
                    if record is not None and record.status == "QUEUED":
                        continue  # known-unsent; safe to send once after recovery
                    self._add_unresolved(
                        state,
                        "ORDER_STATE_UNKNOWN",
                        {"cl_ord_id": order.cl_ord_id, "status": order.status},
                    )

        self._reconcile_order_evidence(state)
        self._emit(
            state,
            "account.reconciled",
            "",
            "",
            {
                "generation": snapshot.generation,
                "checked_at_ms": snapshot.checked_at_ms,
                "balances": snapshot.balances,
            },
            now,
            source="rest_alignment",
            notify=False,
        )

    def _reasons(self, state: AccountState, now: int) -> list[str]:
        reasons = [entry["code"] for entry in state.reconciliation.get("unresolved", [])]
        checked = state.reconciliation.get("checked_at_ms")
        if checked is None:
            reasons.append("ACCOUNT_NOT_RECONCILED")
        elif not 0 <= now - int(checked) <= self.policy.max_reconciliation_age_ms:
            reasons.append("ACCOUNT_SNAPSHOT_STALE")
        if state.reconciliation.get("stream_healthy", True) is not True:
            reasons.append("PRIVATE_STREAM_UNHEALTHY")
        if state.control.get("new_risk_blocked"):
            reasons.append("NEW_RISK_BLOCKED")
        if not self._valuation_complete(state, now):
            reasons.append("VALUATION_INCOMPLETE")
        if any(amount < 0 for amount in state.balances.values()):
            reasons.append("NEGATIVE_BALANCE")
        # Preserve insertion order while removing duplicates.
        return list(dict.fromkeys(reasons))

    def _status(self, state: AccountState, reasons: list[str]) -> str:
        if state.reconciliation.get("checked_at_ms") is None:
            return "RECOVERING"
        return "BLOCKED" if reasons else "READY"

    def _valuation_complete(self, state: AccountState, now: int) -> bool:
        marks = state.reconciliation.get("marks", {})
        symbols = {symbol for symbol, qty in state.positions.items() if qty != 0}
        for order in state.orders.values():
            if state.balances.get(order.command.base_asset, Decimal(0)) != 0:
                symbols.add(order.command.symbol)
        for symbol in symbols:
            mark = marks.get(symbol)
            if mark is None:
                return False
            if not 0 <= now - int(mark.get("at_ms", 0)) <= self.policy.max_mark_age_ms:
                return False
            if Decimal(str(mark.get("price", 0))) <= 0:
                return False
        return True

    def _add_unresolved(self, state: AccountState, code: str, detail: Any) -> None:
        entries = state.reconciliation.setdefault("unresolved", [])
        if not any(entry["code"] == code for entry in entries):
            entries.append({"code": code, "detail": json_value(detail), "at_ms": self.clock()})

    def _resolve_unresolved(self, state: AccountState, code: str) -> None:
        entries = state.reconciliation.get("unresolved", [])
        state.reconciliation["unresolved"] = [entry for entry in entries if entry["code"] != code]

    def _refresh_status(self, state: AccountState) -> None:
        checked = state.reconciliation.get("checked_at_ms")
        if checked is None:
            state.status = "RECOVERING"
            return
        reasons = self._reasons(state, self.clock())
        state.status = "BLOCKED" if reasons else "READY"

    def _emit(
        self,
        state: AccountState,
        event_type: str,
        cl_ord_id: str,
        trace_id: str,
        payload: dict[str, Any],
        occurred_at_ms: int,
        source: str = "api",
        notify: bool = True,
    ) -> None:
        event_id = f"{event_type}:{cl_ord_id}:{occurred_at_ms}:{len(state.events)}"
        event = {
            "event_id": event_id,
            "event_type": event_type,
            "schema_version": 1,
            "trace_id": trace_id,
            "account_id": state.account_id,
            "cl_ord_id": cl_ord_id,
            "occurred_at_ms": occurred_at_ms,
            "source": source,
            "payload": json_value(payload),
        }
        state.events.append(event)
        if notify:
            state.outbox.append({"event_id": event_id, "data": event, "delivered": False})
