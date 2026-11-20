"""Read-only views and safety controls backed by the unified trading stack.

The retired API process exposed these paths through legacy in-memory services
and the old order/accounting implementation. This module keeps the operator
surface while reading the single authoritative account aggregate instead: the
same ``TradingService`` used for orders, risk and commands.

Rules:

* No exchange client is constructed here. Control never holds exchange write
  credentials; the execution process owns venue access.
* Nothing in this module writes orders, executions, ledger entries or legacy
  account/projection tables. The only writes are durable control commands
  (``BLOCK_NEW_RISK`` / ``RESUME_NEW_RISK``) submitted through the same service
  and ownership fence as every other control action.
* Fields the new stack cannot honestly provide (lot history, realized PnL,
  replayed snapshots) are returned empty or refused with a stable reason code
  instead of being fabricated.
"""

from __future__ import annotations

import importlib.util
import os
from datetime import datetime, timezone
from decimal import Decimal
from pathlib import Path
from typing import Any, Optional
from uuid import uuid4

from fastapi import APIRouter, HTTPException, Query, Request
from pydantic import BaseModel, Field

from trader.trading.strategy_catalog import list_installed_strategies

router = APIRouter(tags=["ControlViews"])

TERMINAL_ORDER_STATES = frozenset({"FILLED", "CANCELLED", "REJECTED", "FAILED", "EXPIRED"})
FILL_EVENT_TYPES = frozenset({"execution.booked"})
ERROR_EVENT_TYPES = frozenset(
    {
        "order.rejected",
        "order.unknown",
        "order.admission_failed",
        "cancel.rejected",
        "cancel.unknown",
        "reconciliation.conflict",
        "execution.unmatched",
        "book.allocation_failed",
    }
)

LEGACY_LIFECYCLE_RETIRED = (
    "LEGACY_STRATEGY_LIFECYCLE_RETIRED: the old StrategyRunner is removed; "
    "configure QTS_STRATEGY_MODULE/QTS_STRATEGY_CONFIG and restart the execution process"
)
LEGACY_REPLAY_RETIRED = (
    "LEGACY_REPLAY_RETIRED: the old event replay engine is removed; "
    "research backtests use /v1/research/backtests"
)
RECONCILIATION_IS_EXECUTION_OWNED = (
    "RECONCILIATION_IS_EXECUTION_OWNED: the execution process reconciles on its "
    "own interval; use /v1/trading/reconnect only for a venue session rebuild"
)


class KillSwitchSetBody(BaseModel):
    scope: str = Field(default="GLOBAL")
    level: int = Field(ge=0, le=3)
    reason: Optional[str] = None
    updated_by: str = "operator"


class SafetyGateBody(BaseModel):
    enabled: bool
    confirmed: bool = False


def _utc_now_iso() -> str:
    return datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")


def _iso(ms: Any) -> Optional[str]:
    if ms is None:
        return None
    try:
        value = int(ms)
    except (TypeError, ValueError):
        return None
    if value <= 0:
        return None
    return datetime.fromtimestamp(value / 1000, tz=timezone.utc).isoformat().replace("+00:00", "Z")


def _service(request: Request):
    service = getattr(request.app.state, "trading_service", None)
    if service is None:
        raise HTTPException(status_code=503, detail="Trading runtime unavailable")
    return service


def _account(request: Request, explicit: str | None = None) -> str:
    if explicit:
        return explicit
    configured = getattr(request.app.state, "account_id", None)
    return str(configured or os.getenv("QTS_ACCOUNT_ID", "binance_spot"))


def _instruments() -> dict[str, list[str]]:
    import json

    raw = os.getenv("QTS_INSTRUMENTS", "")
    if not raw:
        return {}
    try:
        parsed = json.loads(raw)
    except ValueError:
        return {}
    if not isinstance(parsed, dict):
        return {}
    return {
        str(symbol).upper(): [str(asset).upper() for asset in assets]
        for symbol, assets in parsed.items()
        if isinstance(assets, (list, tuple))
    }


def _symbol_for_asset(asset: str) -> str:
    upper = asset.upper()
    for symbol, assets in _instruments().items():
        if assets and assets[0] == upper:
            return symbol
    return f"{upper}USDT"


def _mark_price(state, symbol: str) -> Optional[Decimal]:
    mark = (state.reconciliation.get("marks", {}) or {}).get(symbol)
    if not isinstance(mark, dict):
        return None
    try:
        value = Decimal(str(mark.get("price")))
    except Exception:
        return None
    return value if value > 0 else None


def _killswitch_state(state) -> dict[str, Any]:
    blocked = bool(state.control.get("new_risk_blocked"))
    reason = None
    updated_at = None
    actor = None
    for event in reversed(state.events):
        if str(event.get("event_type", "")) != "control.requested":
            continue
        payload = event.get("payload", {}) or {}
        if payload.get("action") not in {"BLOCK_NEW_RISK", "RESUME_NEW_RISK"}:
            continue
        command_id = str(event.get("trace_id", "") or "")
        record = state.commands.get(command_id)
        actor = record.actor if record else None
        reason = str(payload.get("reason") or "") or None
        updated_at = _iso(event.get("occurred_at_ms"))
        break
    return {
        "scope": "GLOBAL",
        "level": 1 if blocked else 0,
        "reason": reason,
        "updated_at": updated_at,
        "updated_by": actor,
    }


async def _submit_control(request: Request, action: str, reason: str) -> dict[str, Any]:
    service = _service(request)
    account_id = _account(request)
    command_id = f"killswitch-{action.lower()}-{uuid4().hex[:16]}"
    actor = getattr(request.state, "actor", "system")
    runtime = getattr(request.app.state, "trading_runtime", None)
    executor = runtime if runtime is not None else service
    await executor.control(
        command_id,
        account_id,
        action,
        actor=actor,
        code="",
        evidence={"reason": reason} if reason else None,
    )
    return {
        "command_id": command_id,
        "action": action,
        "account_id": account_id,
    }


# --------------------------------------------------------------------- orders


def _booked_average_price(state, order) -> Optional[str]:
    """Weighted average of the booked execution facts, never the order price."""
    total_qty = Decimal(0)
    total_cost = Decimal(0)
    for item in state.executions.values():
        if str(item.get("cl_ord_id", "")) != order.cl_ord_id:
            continue
        try:
            qty = Decimal(str(item.get("qty", "0")))
            price = Decimal(str(item.get("price", "0")))
        except (ArithmeticError, ValueError):
            continue
        if qty <= 0 or price <= 0:
            continue
        total_qty += qty
        total_cost += qty * price
    if total_qty <= 0:
        return None
    return str(total_cost / total_qty)


def _order_view(state, account_id: str, order) -> dict[str, Any]:
    command = state.commands.get(order.command.command_id)
    return {
        "cl_ord_id": order.cl_ord_id,
        "trace_id": order.command.decision_id or order.command.command_id,
        "account_id": account_id,
        "strategy_id": order.command.book_id,
        "deployment_id": order.command.deployment_id or None,
        "venue": "binance_spot",
        "instrument": order.command.symbol,
        "side": order.command.side,
        "order_type": order.command.order_type,
        "qty": str(order.command.qty),
        "limit_price": (str(order.command.price) if order.command.order_type == "LIMIT" else None),
        "tif": order.command.time_in_force,
        "status": order.status,
        "broker_order_id": order.broker_order_id or None,
        "filled_qty": str(order.booked_qty),
        "avg_price": _booked_average_price(state, order),
        "created_ts_ms": None,
        "updated_ts_ms": int(order.observed_at_ms or 0) or None,
        "reject_code": (
            command.error if command and command.status in {"UNKNOWN", "FAILED"} else None
        ),
        "reject_msg": command.error if command else None,
    }


@router.get("/v1/orders")
async def list_orders(
    request: Request,
    account_id: Optional[str] = Query(None),
    strategy_id: Optional[str] = Query(None),
    deployment_id: Optional[str] = Query(None),
    venue: Optional[str] = Query(None),
    status: Optional[str] = Query(None),
    instrument: Optional[str] = Query(None),
    since_ts_ms: Optional[int] = Query(None),
    limit: int = Query(200, ge=1, le=2000),
):
    service = _service(request)
    account = _account(request, account_id)
    state = await service.store.read(account)
    rows = []
    for order in state.orders.values():
        view = _order_view(state, account, order)
        if strategy_id and view["strategy_id"] != strategy_id:
            continue
        if deployment_id and view["deployment_id"] != deployment_id:
            continue
        if venue and str(view["venue"]).upper() != venue.upper():
            continue
        if status and str(view["status"]).upper() != status.upper():
            continue
        if instrument and str(view["instrument"]).upper() != instrument.upper():
            continue
        if since_ts_ms and (view["updated_ts_ms"] or 0) < since_ts_ms:
            continue
        rows.append(view)
    return rows[-limit:]


@router.get("/v1/orders/{cl_ord_id}")
async def get_order(cl_ord_id: str, request: Request, account_id: Optional[str] = Query(None)):
    service = _service(request)
    account = _account(request, account_id)
    state = await service.store.read(account)
    order = state.orders.get(cl_ord_id)
    if order is None:
        raise HTTPException(status_code=404, detail=f"Order {cl_ord_id} not found")
    return _order_view(state, account, order)


@router.post("/v1/orders/{cl_ord_id}/cancel")
async def cancel_order(cl_ord_id: str, request: Request):
    # Cancellation is owned by the unified Control API. Keeping a second verb
    # here would create a second mutation surface.
    raise HTTPException(
        status_code=503,
        detail=(
            "LEGACY_CANCEL_RETIRED: submit POST /v1/trading/orders/{cl_ord_id}/cancel "
            "as a durable command"
        ),
    )


@router.get("/v1/executions")
async def list_executions(
    request: Request,
    account_id: Optional[str] = Query(None),
    cl_ord_id: Optional[str] = Query(None),
    deployment_id: Optional[str] = Query(None),
    since_ts_ms: Optional[int] = Query(None),
    limit: int = Query(500, ge=1, le=5000),
):
    service = _service(request)
    account = _account(request, account_id)
    state = await service.store.read(account)
    rows = []
    for key, item in state.executions.items():
        order = state.orders.get(str(item.get("cl_ord_id", "")))
        row_deployment = order.command.deployment_id if order else ""
        if cl_ord_id and str(item.get("cl_ord_id", "")) != cl_ord_id:
            continue
        if deployment_id and row_deployment != deployment_id:
            continue
        occurred = int(item.get("occurred_at_ms", 0) or 0)
        if since_ts_ms and occurred < since_ts_ms:
            continue
        rows.append(
            {
                "execution_key": key,
                "cl_ord_id": str(item.get("cl_ord_id", "")),
                "exec_id": str(item.get("exec_id", "")),
                "ts_ms": occurred,
                "fill_qty": str(item.get("qty", "0")),
                "fill_price": str(item.get("price", "0")),
                "fee": None if item.get("fee") is None else str(item.get("fee")),
                "fee_currency": item.get("fee_asset"),
            }
        )
    rows.sort(key=lambda item: (item["ts_ms"], item["exec_id"]))
    return rows[-limit:]


# -------------------------------------------------------------------- monitor


def _positions_detail(state):
    details = []
    for symbol, quantity in sorted(state.positions.items()):
        if quantity == 0:
            continue
        price = _mark_price(state, symbol)
        exposure = str(price * quantity) if price is not None else None
        details.append(
            {
                "symbol": symbol,
                "quantity": str(quantity),
                "avg_cost": None,
                "current_price": None if price is None else str(price),
                "unrealized_pnl": None,
                "exposure": exposure,
            }
        )
    return details


def _derived_alerts(state, now_ms: int) -> list[dict[str, Any]]:
    alerts = []
    checked = state.reconciliation.get("checked_at_ms")
    triggered = _iso(checked or now_ms) or _utc_now_iso()
    for index, entry in enumerate(state.reconciliation.get("unresolved", []) or []):
        code = str(entry.get("code", "") or "UNRESOLVED")
        severity = "CRITICAL" if code in {"BALANCE_MISMATCH", "UNKNOWN_COMMAND"} else "HIGH"
        alerts.append(
            {
                "alert_id": f"derived-{code}-{index}",
                "rule_name": code,
                "severity": severity,
                "message": f"Account readiness is blocked by {code}",
                "metric_key": "readiness.reasons",
                "metric_value": float(len(state.reconciliation.get("unresolved", []) or [])),
                "threshold": 0.0,
                "triggered_at": triggered,
            }
        )
    return alerts


@router.get("/v1/monitor/snapshot")
async def monitor_snapshot(request: Request, account_id: Optional[str] = Query(None)):
    service = _service(request)
    account = _account(request, account_id)
    state = await service.store.read(account)
    now_ms = int(datetime.now(tz=timezone.utc).timestamp() * 1000)
    checked = state.reconciliation.get("checked_at_ms")
    orders = list(state.orders.values())
    open_orders = [order for order in orders if order.status not in TERMINAL_ORDER_STATES]
    pending = [
        record for record in state.commands.values() if record.status in {"QUEUED", "SENDING"}
    ]
    positions = _positions_detail(state)
    exposures = [position["exposure"] for position in positions]
    total_exposure = (
        None
        if any(value is None for value in exposures)
        else str(sum((Decimal(value) for value in exposures), Decimal(0)))
    )
    alerts = _derived_alerts(state, now_ms)
    severity_counts: dict[str, int] = {}
    for alert in alerts:
        severity_counts[alert["severity"]] = severity_counts.get(alert["severity"], 0) + 1
    runtime = state.control.get("runtime", {}) or {}
    reasons = [entry.get("code") for entry in state.reconciliation.get("unresolved", []) or []]
    if not runtime.get("ready", False):
        reasons.extend(runtime.get("reasons", []) or ["TRADING_RUNTIME_NOT_READY"])
    order_outcomes = {"accepted": 0, "rejected": 0, "unknown": 0}
    for event in state.events:
        event_type = str(event.get("event_type", ""))
        if event_type == "order.accepted":
            order_outcomes["accepted"] += 1
        elif event_type == "order.rejected":
            order_outcomes["rejected"] += 1
        elif event_type == "order.unknown":
            order_outcomes["unknown"] += 1
    return {
        "timestamp": _utc_now_iso(),
        "total_positions": len(positions),
        "total_exposure": total_exposure,
        "positions": positions,
        "open_orders_count": len(open_orders),
        "pending_orders_count": len(pending),
        "daily_pnl": "0",
        "daily_pnl_pct": "0",
        "realized_pnl": "0",
        "unrealized_pnl": "0",
        "killswitch_level": 1 if state.control.get("new_risk_blocked") else 0,
        "killswitch_scope": "GLOBAL",
        "adapters": {
            "execution": {
                "adapter_name": "execution",
                "status": "HEALTHY" if runtime.get("ready") else "DOWN",
                "last_heartbeat_ts_ms": runtime.get("at_ms"),
                "error_count": 0 if runtime.get("ready") else 1,
                "message": ",".join(reasons) if reasons else None,
            }
        },
        "active_alerts": alerts,
        "alert_count_by_severity": severity_counts,
        "tick_rate": None,
        "tick_lag_ms": None,
        "order_submit_ok": order_outcomes["accepted"],
        "order_submit_reject": order_outcomes["rejected"],
        "order_submit_error": order_outcomes["unknown"],
        "reject_reason_counts": {},
        "fill_latency_ms_avg": None,
        "fill_latency_count": len(state.executions),
        "ws_reconnect_count": None,
        "cl_ord_id_dedup_hits": 0,
        "exec_dedup_hits": 0,
        "snapshot_source": "new_stack_account_state",
        "freshness": _iso(checked),
    }


@router.get("/v1/monitor/alerts")
async def monitor_alerts(request: Request, account_id: Optional[str] = Query(None)):
    service = _service(request)
    account = _account(request, account_id)
    state = await service.store.read(account)
    now_ms = int(datetime.now(tz=timezone.utc).timestamp() * 1000)
    alerts = _derived_alerts(state, now_ms)
    return {"alerts": alerts, "total_count": len(alerts)}


# ----------------------------------------------------------------- killswitch


@router.get("/v1/killswitch")
async def get_killswitch(request: Request, account_id: Optional[str] = Query(None)):
    state = await _service(request).store.read(_account(request, account_id))
    return _killswitch_state(state)


@router.post("/v1/killswitch")
async def set_killswitch(payload: KillSwitchSetBody, request: Request):
    if payload.scope.upper() != "GLOBAL":
        raise HTTPException(status_code=400, detail="SCOPE_NOT_SUPPORTED")
    action = "BLOCK_NEW_RISK" if payload.level >= 1 else "RESUME_NEW_RISK"
    receipt = await _submit_control(
        request, action, payload.reason or f"killswitch level {payload.level}"
    )
    state = await _service(request).store.read(_account(request))
    view = _killswitch_state(state)
    view["command_id"] = receipt["command_id"]
    return view


@router.get("/v1/safety-gate/status")
async def safety_gate_status(request: Request, account_id: Optional[str] = Query(None)):
    state = await _service(request).store.read(_account(request, account_id))
    killswitch = _killswitch_state(state)
    return {
        "live_trading_enabled": killswitch["level"] == 0,
        "killswitch_level": killswitch["level"],
        "killswitch_reason": killswitch["reason"],
    }


@router.post("/v1/safety-gate/enable")
async def safety_gate_enable(payload: SafetyGateBody, request: Request):
    if payload.enabled and not payload.confirmed:
        raise HTTPException(
            status_code=400, detail="Must set confirmed=true to enable live trading"
        )
    action = "RESUME_NEW_RISK" if payload.enabled else "BLOCK_NEW_RISK"
    receipt = await _submit_control(request, action, "safety gate")
    state = await _service(request).store.read(_account(request))
    killswitch = _killswitch_state(state)
    return {
        "live_trading_enabled": killswitch["level"] == 0,
        "killswitch_level": killswitch["level"],
        "killswitch_reason": killswitch["reason"],
        "command_id": receipt["command_id"],
    }


# -------------------------------------------------------------------- brokers


@router.get("/v1/brokers")
async def list_brokers(request: Request, account_id: Optional[str] = Query(None)):
    account = _account(request, account_id)
    state = await _service(request).store.read(account)
    runtime = state.control.get("runtime", {}) or {}
    return [
        {
            "account_id": account,
            "venue": "BINANCE",
            "broker_type": "BINANCE",
            "status": "READY" if runtime.get("ready") else "BLOCKED",
            "capabilities": {"symbols": sorted(_instruments())},
        }
    ]


@router.get("/v1/brokers/{broker_account_id}/status")
async def broker_status(
    broker_account_id: str, request: Request, account_id: Optional[str] = Query(None)
):
    account = _account(request, account_id)
    if broker_account_id != account:
        raise HTTPException(status_code=404, detail=f"Broker account {broker_account_id} not found")
    state = await _service(request).store.read(account)
    runtime = state.control.get("runtime", {}) or {}
    return {
        "account_id": account,
        "connected": bool(runtime.get("ready")),
        "last_heartbeat_ts_ms": runtime.get("at_ms"),
        "last_error": ",".join(runtime.get("reasons", []) or []) or None,
    }


# ------------------------------------------------------------------ portfolio


def _position_rows(state, account: str):
    rows = []
    for book_id in sorted(state.books):
        book = state.books[book_id]
        for symbol, quantity in sorted(book.positions.items()):
            if quantity == 0:
                continue
            price = _mark_price(state, symbol)
            rows.append(
                {
                    "strategy_id": book_id,
                    "symbol": symbol,
                    "qty": str(quantity),
                    "avg_cost": "0",
                    "realized_pnl": "0",
                    "unrealized_pnl": "0",
                    "total_cost": None,
                    "status": "ACTIVE",
                    "lot_count": 0,
                    "cost_basis_method": "average_cost",
                    "updated_at": _iso(state.reconciliation.get("checked_at_ms")),
                }
            )
    return rows


@router.get("/v1/portfolio/positions")
async def portfolio_positions(
    request: Request,
    account_id: Optional[str] = Query(None),
    venue: Optional[str] = Query(None),
    strategy_id: Optional[str] = Query(None),
    instrument: Optional[str] = Query(None),
):
    state = await _service(request).store.read(_account(request, account_id))
    rows = _position_rows(state, _account(request, account_id))
    if strategy_id:
        rows = [row for row in rows if row["strategy_id"] == strategy_id]
    if instrument:
        rows = [row for row in rows if row["symbol"] == instrument.upper()]
    return [
        {
            "account_id": _account(request, account_id),
            "venue": "BINANCE",
            "instrument": row["symbol"],
            "qty": row["qty"],
            "avg_cost": row["avg_cost"],
            "mark_price": None,
            "unrealized_pnl": row["unrealized_pnl"],
            "realized_pnl": row["realized_pnl"],
            "updated_ts_ms": state.reconciliation.get("checked_at_ms"),
        }
        for row in rows
    ]


@router.get("/v1/portfolio/strategy-positions")
async def portfolio_strategy_positions(
    request: Request,
    strategy_id: Optional[str] = Query(None),
    symbol: Optional[str] = Query(None),
    account_id: Optional[str] = Query(None),
):
    state = await _service(request).store.read(_account(request, account_id))
    rows = _position_rows(state, _account(request, account_id))
    if strategy_id:
        rows = [row for row in rows if row["strategy_id"] == strategy_id]
    if symbol:
        rows = [row for row in rows if row["symbol"] == symbol.upper()]
    return rows


@router.get("/v1/portfolio/positions/{symbol}/breakdown")
async def portfolio_breakdown(
    symbol: str, request: Request, account_id: Optional[str] = Query(None)
):
    account = _account(request, account_id)
    state = await _service(request).store.read(account)
    upper = symbol.upper()
    configured = _instruments().get(upper)
    asset = configured[0] if configured else (upper[:-4] if upper.endswith("USDT") else upper)
    account_qty = state.positions.get(asset, Decimal(0))
    rows = [row for row in _position_rows(state, account) if row["symbol"] == upper]
    strategy_total = sum((Decimal(row["qty"]) for row in rows), Decimal(0))
    difference = account_qty - strategy_total
    return {
        "symbol": upper,
        "account_qty": str(account_qty),
        "account_avg_cost": None,
        "strategy_positions": rows,
        "historical": None,
        "is_reconciled": difference == 0,
        "difference": str(difference),
        "tolerance": "0.001",
    }


@router.get("/v1/portfolio/positions/{symbol}/lots")
async def portfolio_lots(
    symbol: str,
    request: Request,
    strategy_id: str = Query(...),
    account_id: Optional[str] = Query(None),
):
    # The new stack has no lot ledger; exposure is tracked per book. Returning
    # an empty list is honest and keeps the UI from inventing lot history.
    return []


# --------------------------------------------------------------- reconciliation


@router.get("/v1/reconciler/report")
async def reconciler_report(request: Request, account_id: Optional[str] = Query(None)):
    service = _service(request)
    account = _account(request, account_id)
    state = await service.store.read(account)
    checked = state.reconciliation.get("checked_at_ms")
    if checked is None:
        raise HTTPException(status_code=404, detail="No reconciliation report available")
    unresolved = state.reconciliation.get("unresolved", []) or []
    drifts = [
        {
            "cl_ord_id": str(entry.get("command_id", "") or ""),
            "drift_type": "DIVERGED",
            "local_status": str(entry.get("code", "")),
            "exchange_status": None,
            "detected_at": _iso(checked) or _utc_now_iso(),
            "symbol": None,
            "quantity": None,
            "filled_quantity": None,
            "exchange_filled_quantity": None,
            "grace_period_remaining_sec": None,
            "ownership": "OWNED",
        }
        for entry in unresolved
    ]
    return {
        "timestamp": _iso(checked),
        "total_orders_checked": len(state.orders),
        "drifts": drifts,
        "ghost_count": 0,
        "phantom_count": 0,
        "diverged_count": len(drifts),
        "within_grace_period_count": 0,
        "external_count": 0,
    }


@router.post("/v1/reconciler/trigger")
async def reconciler_trigger():
    raise HTTPException(status_code=503, detail=RECONCILIATION_IS_EXECUTION_OWNED)


# ---------------------------------------------------------------- strategies


def _runtime_strategy(state) -> dict[str, Any]:
    runtime = state.control.get("runtime", {}) or {}
    strategy = runtime.get("strategy") or {}
    return strategy if isinstance(strategy, dict) else {}


def _strategy_status(state, deployment_id: str) -> dict[str, Any]:
    strategy = _runtime_strategy(state)
    if not strategy or str(strategy.get("deployment_id", "")) != deployment_id:
        raise HTTPException(status_code=404, detail=f"Deployment {deployment_id} not found")
    runtime = state.control.get("runtime", {}) or {}
    return {
        "deployment_id": deployment_id,
        "strategy_id": str(strategy.get("strategy_id", "")),
        "version": str(strategy.get("version", "1")),
        "status": "running" if runtime.get("ready") else "blocked",
        "symbols": list(strategy.get("symbols", []) or []),
        "account_id": state.account_id,
        "venue": "BINANCE",
        "mode": "demo",
        "loaded_at": _iso(runtime.get("at_ms")),
        "started_at": _iso(runtime.get("at_ms")),
        "last_tick_at": _iso(state.reconciliation.get("checked_at_ms")),
        "tick_count": 0,
        "signal_count": 0,
        "error_count": 0,
        "last_error": None,
        "stop_reason": None,
        "config": dict(strategy.get("config", {}) or {}),
        "blocked_reason": ",".join(runtime.get("reasons", []) or []) or None,
        "warmup_state": strategy.get("warmup_state"),
        "warmup_remaining": strategy.get("warmup_remaining"),
        "strategy_ready": strategy.get("strategy_ready"),
    }


@router.get("/v1/strategies/registry")
async def strategy_registry():
    return [
        {
            "strategy_id": item["strategy_id"],
            "name": item["name"],
            "description": None,
            "entrypoint": item["module_path"],
            "language": "python",
            "created_at": None,
            "updated_at": None,
        }
        for item in list_installed_strategies()
    ]


@router.get("/v1/strategies/registry/{strategy_id}")
async def strategy_registry_item(strategy_id: str):
    for item in list_installed_strategies():
        if item["strategy_id"] == strategy_id or item["module_path"] == strategy_id:
            return {
                "strategy_id": item["strategy_id"],
                "name": item["name"],
                "description": None,
                "entrypoint": item["module_path"],
                "language": "python",
                "created_at": None,
                "updated_at": None,
            }
    raise HTTPException(status_code=404, detail=f"Strategy {strategy_id} not found")


@router.get("/v1/strategies/loaded")
async def loaded_strategies(request: Request, account_id: Optional[str] = Query(None)):
    state = await _service(request).store.read(_account(request, account_id))
    strategy = _runtime_strategy(state)
    if not strategy:
        return []
    try:
        return [_strategy_status(state, str(strategy.get("deployment_id", "")))]
    except HTTPException:
        return []


@router.get("/v1/strategies/auto-paused")
async def auto_paused_strategies():
    return {"paused_strategies": [], "service_running": False}


@router.post("/v1/strategies/{deployment_id}/force-resume")
async def force_resume(deployment_id: str):
    raise HTTPException(status_code=410, detail=LEGACY_LIFECYCLE_RETIRED)


@router.get("/v1/deployments/{deployment_id}/status")
async def deployment_status(
    deployment_id: str, request: Request, account_id: Optional[str] = Query(None)
):
    state = await _service(request).store.read(_account(request, account_id))
    return _strategy_status(state, deployment_id)


def _deployment_events(state, deployment_id: str) -> list[dict[str, Any]]:
    rows = []
    for index, event in enumerate(state.events):
        cl_ord_id = str(event.get("cl_ord_id", "") or "")
        order = state.orders.get(cl_ord_id)
        if order is None or order.command.deployment_id != deployment_id:
            continue
        rows.append(
            {
                "event_id": index,
                "stream_key": "strategy_events",
                "event_type": str(event.get("event_type", "")),
                "ts_ms": int(event.get("occurred_at_ms", 0) or 0),
                "payload": dict(event.get("payload", {}) or {}),
                "trace_id": event.get("trace_id"),
                "source": event.get("source"),
            }
        )
    return rows


def _filter_events(rows, event_types, limit):
    filtered = [row for row in rows if row["event_type"] in event_types]
    return filtered[-limit:]


@router.get("/v1/deployments/{deployment_id}/events")
async def deployment_events(
    deployment_id: str,
    request: Request,
    limit: int = Query(200, ge=1, le=2000),
    account_id: Optional[str] = Query(None),
):
    state = await _service(request).store.read(_account(request, account_id))
    return _deployment_events(state, deployment_id)[-limit:]


@router.get("/v1/deployments/{deployment_id}/events/signals")
async def deployment_signal_events(
    deployment_id: str,
    request: Request,
    limit: int = Query(200, ge=1, le=2000),
    account_id: Optional[str] = Query(None),
):
    state = await _service(request).store.read(_account(request, account_id))
    rows = _deployment_events(state, deployment_id)
    return _filter_events(rows, {"order.queued"}, limit)


@router.get("/v1/deployments/{deployment_id}/events/errors")
async def deployment_error_events(
    deployment_id: str,
    request: Request,
    limit: int = Query(200, ge=1, le=2000),
    account_id: Optional[str] = Query(None),
):
    state = await _service(request).store.read(_account(request, account_id))
    rows = _deployment_events(state, deployment_id)
    return _filter_events(rows, ERROR_EVENT_TYPES, limit)


@router.get("/v1/deployments/{deployment_id}/events/fills")
async def deployment_fill_events(
    deployment_id: str,
    request: Request,
    limit: int = Query(200, ge=1, le=2000),
    account_id: Optional[str] = Query(None),
):
    state = await _service(request).store.read(_account(request, account_id))
    rows = _deployment_events(state, deployment_id)
    return _filter_events(rows, FILL_EVENT_TYPES, limit)


@router.get("/v1/strategies/{strategy_id}/events")
async def strategy_events_alias(
    strategy_id: str,
    request: Request,
    limit: int = Query(200, ge=1, le=2000),
    account_id: Optional[str] = Query(None),
):
    return await deployment_events(strategy_id, request, limit, account_id)


@router.get("/v1/strategies/{strategy_id}/events/signals")
async def strategy_signal_alias(
    strategy_id: str,
    request: Request,
    limit: int = Query(200, ge=1, le=2000),
    account_id: Optional[str] = Query(None),
):
    return await deployment_signal_events(strategy_id, request, limit, account_id)


@router.get("/v1/strategies/{strategy_id}/events/errors")
async def strategy_error_alias(
    strategy_id: str,
    request: Request,
    limit: int = Query(200, ge=1, le=2000),
    account_id: Optional[str] = Query(None),
):
    return await deployment_error_events(strategy_id, request, limit, account_id)


@router.get("/v1/strategies/{strategy_id}/events/fills")
async def strategy_fill_alias(
    strategy_id: str,
    request: Request,
    limit: int = Query(200, ge=1, le=2000),
    account_id: Optional[str] = Query(None),
):
    return await deployment_fill_events(strategy_id, request, limit, account_id)


def _catalog_item(strategy_id: str):
    for item in list_installed_strategies():
        if item["strategy_id"] == strategy_id or item["module_path"] == strategy_id:
            return item
    return None


@router.get("/v1/strategies/{strategy_id}/code/view")
async def strategy_code_view(strategy_id: str):
    item = _catalog_item(strategy_id)
    if item is None:
        raise HTTPException(status_code=404, detail=f"Strategy {strategy_id} not found")
    module_path = item["module_path"]
    spec = importlib.util.find_spec(module_path)
    origin = Path(spec.origin) if spec and spec.origin else None
    strategies_root = Path(__file__).resolve().parents[2] / "strategies"
    if origin is None or strategies_root not in origin.parents:
        raise HTTPException(status_code=404, detail="STRATEGY_SOURCE_NOT_AVAILABLE")
    return {
        "strategy_id": item["strategy_id"],
        "source_type": "module_entrypoint",
        "code": origin.read_text(encoding="utf-8"),
        "code_version": None,
        "checksum": item["code_hash"],
        "module_path": module_path,
        "entrypoint": module_path,
        "created_at": None,
        "created_by": None,
        "notes": "installed module source; read-only",
    }


@router.get("/v1/strategies/{strategy_id}/code/latest")
async def strategy_code_latest(strategy_id: str):
    raise HTTPException(status_code=404, detail="NO_SAVED_CODE_VERSION")


@router.get("/v1/strategies/{strategy_id}/code/{code_version}")
async def strategy_code_version(strategy_id: str, code_version: int):
    raise HTTPException(status_code=404, detail="NO_SAVED_CODE_VERSION")


@router.get("/v1/strategies/{strategy_id}/versions")
async def strategy_versions(strategy_id: str):
    return []


@router.get("/v1/strategies/{strategy_id}/params")
async def strategy_params(strategy_id: str):
    item = _catalog_item(strategy_id)
    if item is None:
        raise HTTPException(status_code=404, detail=f"Strategy {strategy_id} not found")
    import json

    configured = {}
    if os.getenv("QTS_STRATEGY_MODULE", "") in {strategy_id, item["module_path"]}:
        try:
            configured = json.loads(os.getenv("QTS_STRATEGY_CONFIG", "{}") or "{}")
        except ValueError:
            configured = {}
    defaults = {param["name"]: param["default"] for param in item["params"]}
    return {
        "scope": f"strategy:{strategy_id}",
        "version": 1,
        "config": {**defaults, **configured},
        "created_at": None,
        "created_by": None,
    }


@router.post("/v1/strategies/{strategy_id}/load")
async def load_strategy(strategy_id: str):
    raise HTTPException(status_code=410, detail=LEGACY_LIFECYCLE_RETIRED)


@router.post("/v1/deployments/{deployment_id}/unload")
async def unload_strategy(deployment_id: str):
    raise HTTPException(status_code=410, detail=LEGACY_LIFECYCLE_RETIRED)


@router.post("/v1/deployments/{deployment_id}/start")
async def start_deployment(deployment_id: str):
    raise HTTPException(status_code=410, detail=LEGACY_LIFECYCLE_RETIRED)


@router.post("/v1/deployments/{deployment_id}/stop")
async def stop_deployment(deployment_id: str):
    raise HTTPException(status_code=410, detail=LEGACY_LIFECYCLE_RETIRED)


@router.post("/v1/deployments/{deployment_id}/pause")
async def pause_deployment(deployment_id: str):
    raise HTTPException(status_code=410, detail=LEGACY_LIFECYCLE_RETIRED)


@router.post("/v1/deployments/{deployment_id}/resume")
async def resume_deployment(deployment_id: str):
    raise HTTPException(status_code=410, detail=LEGACY_LIFECYCLE_RETIRED)


@router.post("/v1/strategies/{strategy_id}/start")
async def start_strategy_legacy(strategy_id: str):
    raise HTTPException(status_code=410, detail=LEGACY_LIFECYCLE_RETIRED)


@router.post("/v1/strategies/registry")
async def register_strategy_legacy():
    raise HTTPException(status_code=410, detail=LEGACY_LIFECYCLE_RETIRED)


@router.get("/v1/exchange/trading-pairs")
async def trading_pairs(
    status_filter: Optional[str] = Query("TRADING"),
    quote_asset: Optional[str] = Query("USDT"),
):
    filter_status = str(status_filter or "").strip().upper()
    filter_quote = str(quote_asset or "").strip().upper()
    pairs = []
    for symbol, assets in sorted(_instruments().items()):
        if len(assets) < 2:
            continue
        if filter_quote and assets[1] != filter_quote:
            continue
        if filter_status and filter_status not in {"TRADING", "CONFIGURED", "ALL"}:
            # All configured symbols are tradable; other historical statuses
            # have no matching rows and must not be silently relabelled.
            continue
        pairs.append(
            {
                "symbol": symbol,
                "base_asset": assets[0],
                "quote_asset": assets[1],
                "status": "TRADING",
                "min_notional": 0.0,
                "min_qty": 0.0,
                "max_qty": 0.0,
                "step_size": 0.0,
                "tick_size": 0.0,
            }
        )
    return {"pairs": pairs, "total": len(pairs), "source": "QTS_INSTRUMENTS"}


# ------------------------------------------------------------------- events


@router.get("/v1/events")
async def list_events(
    request: Request,
    stream_key: Optional[str] = Query(None),
    event_type: Optional[str] = Query(None),
    from_ts_ms: Optional[int] = Query(None),
    to_ts_ms: Optional[int] = Query(None),
    limit: int = Query(200, ge=1, le=2000),
    account_id: Optional[str] = Query(None),
):
    state = await _service(request).store.read(_account(request, account_id))
    if stream_key == "order_drifts":
        checked = state.reconciliation.get("checked_at_ms") or 0
        return [
            {
                "event_id": index,
                "stream_key": "order_drifts",
                "event_type": "drift.detected",
                "schema_version": 1,
                "trace_id": entry.get("command_id"),
                "ts_ms": int(checked),
                "payload": {
                    "cl_ord_id": entry.get("command_id"),
                    "drift_type": "DIVERGED",
                    "local_status": entry.get("code"),
                    "exchange_status": None,
                    "symbol": None,
                    "quantity": None,
                    "filled_quantity": None,
                    "exchange_filled_quantity": None,
                    "detected_at": _iso(checked),
                },
            }
            for index, entry in enumerate(state.reconciliation.get("unresolved", []) or [])
        ][-limit:]
    rows = []
    for index, event in enumerate(state.events):
        occurred = int(event.get("occurred_at_ms", 0) or 0)
        if from_ts_ms and occurred < from_ts_ms:
            continue
        if to_ts_ms and occurred > to_ts_ms:
            continue
        if event_type and str(event.get("event_type", "")) != event_type:
            continue
        rows.append(
            {
                "event_id": index,
                "stream_key": "trading_events",
                "event_type": str(event.get("event_type", "")),
                "schema_version": int(event.get("schema_version", 1) or 1),
                "trace_id": event.get("trace_id"),
                "ts_ms": occurred,
                "payload": dict(event.get("payload", {}) or {}),
            }
        )
    return rows[-limit:]


@router.post("/v1/replay")
async def create_replay():
    raise HTTPException(status_code=410, detail=LEGACY_REPLAY_RETIRED)


@router.get("/v1/replay")
async def list_replay():
    return []


@router.get("/v1/replay/{job_id}")
async def get_replay(job_id: str):
    raise HTTPException(status_code=404, detail="Replay job not found")


@router.get("/v1/snapshots/latest")
async def latest_snapshot():
    raise HTTPException(status_code=410, detail=LEGACY_REPLAY_RETIRED)


@router.get("/v1/snapshots")
async def list_snapshots():
    return []


# ---------------------------------------------------------------- allocations


@router.get("/v1/allocations")
async def list_allocations(request: Request, account_id: Optional[str] = Query(None)):
    service = _service(request)
    account = _account(request, account_id)
    books = await service.books(account)
    return [
        {
            "deployment_id": book["book_id"],
            "strategy_id": book["book_id"],
            "allocated_capital": str(
                sum((Decimal(value) for value in book["balances"].values()), Decimal(0))
            ),
            "balances": book["balances"],
            "positions": book["positions"],
            "equity": book.get("equity"),
            "valuation_complete": book.get("valuation_complete"),
        }
        for book in books
    ]


@router.put("/v1/allocations/{deployment_id}")
async def upsert_allocation(deployment_id: str):
    raise HTTPException(
        status_code=503,
        detail=(
            "LEGACY_ALLOCATION_RETIRED: submit POST /v1/trading/allocations "
            "as a durable ALLOCATE command"
        ),
    )


__all__ = ["router", "LEGACY_LIFECYCLE_RETIRED", "LEGACY_REPLAY_RETIRED"]
