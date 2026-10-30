"""Deterministic fault rehearsal for the unified spot trading runtime.

No network, no exchange, no real funds. Prints JSON evidence for each scenario.
"""

from __future__ import annotations

import asyncio
import json
import sys
from decimal import Decimal
from pathlib import Path as _Path

sys.path.insert(0, str(_Path(__file__).resolve().parents[1]))

from trader.trading.models import (
    Conflict,
    ExchangeOrderResult,
    ExchangeOutcome,
    Execution,
    OrderObservation,
    PlaceOrderCommand,
    ReconcileSnapshot,
)
from trader.trading.risk import RiskPolicy
from trader.trading.service import TradingService
from trader.trading.store import MemoryTradingStore


class RehearsalExchange:
    def __init__(self):
        self.sent = []
        self.place_result = ExchangeOrderResult(ExchangeOutcome.ACCEPTED, "b-1", "OPEN")
        self.cancel_result = ExchangeOrderResult(ExchangeOutcome.ACCEPTED, "b-1", "OPEN")

    async def place_order(self, command, cl_ord_id):
        self.sent.append(cl_ord_id)
        return self.place_result

    async def cancel_order(self, account_id, cl_ord_id, command_id, symbol):
        return self.cancel_result

    async def query_order(self, account_id, cl_ord_id, symbol):
        return ExchangeOrderResult(ExchangeOutcome.NOT_FOUND)


def command(command_id="cmd-1", **overrides):
    values = dict(
        command_id=command_id,
        account_id="acct",
        symbol="BTCUSDT",
        base_asset="BTC",
        quote_asset="USDT",
        side="BUY",
        qty=Decimal("1"),
        price=Decimal("100"),
    )
    values.update(overrides)
    return PlaceOrderCommand(**values)


def snapshot(**overrides):
    values = dict(
        account_id="acct",
        checked_at_ms=1_000_000,
        generation=1,
        balances={"USDT": Decimal("1000"), "BTC": Decimal("0")},
        marks={"BTCUSDT": {"price": Decimal("100"), "at_ms": 1_000_000}},
    )
    values.update(overrides)
    return ReconcileSnapshot(**values)


async def scenario(name, body):
    try:
        evidence = await body()
        return {"name": name, "ok": True, **evidence}
    except Exception as exc:
        return {"name": name, "ok": False, "error": f"{type(exc).__name__}: {exc}"}


async def unknown_timeout_is_not_resent():
    exchange = RehearsalExchange()
    exchange.place_result = ExchangeOrderResult(ExchangeOutcome.UNKNOWN, reason="timeout")
    service = TradingService(MemoryTradingStore(), exchange, RiskPolicy(), clock=lambda: 1_000_000)
    await service.recover(snapshot())
    receipt = await service.place(command())
    await service.recover(snapshot(checked_at_ms=1_000_100, generation=2))
    state = await service.store.read("acct")
    return {
        "command_status": receipt.status,
        "sent_count": len(exchange.sent),
        "hold_preserved": receipt.cl_ord_id in state.holds,
        "unresolved": [x["code"] for x in state.reconciliation.get("unresolved", [])],
        "passed": len(exchange.sent) == 1 and receipt.status == "UNKNOWN",
    }


async def accepted_but_unpersisted_response_is_adopted_without_resend():
    exchange = RehearsalExchange()
    store = MemoryTradingStore()
    service = TradingService(store, exchange, RiskPolicy(), clock=lambda: 1_000_000)
    await service.recover(snapshot())
    receipt = await service.place(command())
    # Simulate the process dying after the venue accepted but before the result
    # was persisted: force the command back to SENDING in storage.
    async with store.transaction("acct") as state:
        state.commands["cmd-1"].status = "SENDING"
        state.orders[receipt.cl_ord_id].broker_order_id = ""

    await service.recover(
        snapshot(
            checked_at_ms=1_000_100,
            generation=2,
            open_orders=[
                OrderObservation("acct", receipt.cl_ord_id, "OPEN", Decimal("0"), "b-1", 1_000_100)
            ],
        )
    )
    state = await store.read("acct")
    return {
        "command_status": state.commands["cmd-1"].status,
        "sent_count": len(exchange.sent),
        "passed": len(exchange.sent) == 1 and state.commands["cmd-1"].status == "CONFIRMED",
    }


async def ledger_failure_rolls_back_atomically():
    store = MemoryTradingStore()
    service = TradingService(store, RehearsalExchange(), RiskPolicy(), clock=lambda: 1_000_000)
    await service.recover(snapshot())
    receipt = await service.place(command())

    original = service._post_execution
    service._post_execution = lambda *args, **kwargs: (_ for _ in ()).throw(
        RuntimeError("ledger unavailable")
    )
    try:
        await service.record_execution(
            Execution(
                "acct",
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
    except RuntimeError:
        pass
    finally:
        service._post_execution = original
    state = await store.read("acct")
    return {
        "executions": len(state.executions),
        "ledger_entries": len(state.ledger),
        "balance_unchanged": state.balances["USDT"] == Decimal("1000"),
        "passed": not state.executions and not state.ledger,
    }


async def late_fill_after_cancel_is_booked_without_reactivation():
    service = TradingService(
        memory_store := MemoryTradingStore(),
        RehearsalExchange(),
        RiskPolicy(),
        clock=lambda: 1_000_000,
    )
    await service.recover(snapshot())
    receipt = await service.place(command(qty=Decimal("2")))
    await service.cancel("cancel-1", "acct", receipt.cl_ord_id)
    await service.observe(
        OrderObservation("acct", receipt.cl_ord_id, "CANCELLED", Decimal("0"), "b-1", 1_000_100)
    )
    await service.record_execution(
        Execution(
            "acct",
            receipt.cl_ord_id,
            "BTCUSDT",
            "late-1",
            Decimal("0.5"),
            Decimal("100"),
            Decimal("0.05"),
            "USDT",
            1_000_200,
        )
    )
    state = await memory_store.read("acct")
    order = state.orders[receipt.cl_ord_id]
    return {
        "order_status": order.status,
        "booked_qty": str(order.booked_qty),
        "hold_released": receipt.cl_ord_id not in state.holds,
        "passed": order.status == "CANCELLED" and order.booked_qty == Decimal("0.5"),
    }


async def duplicate_fill_conflict_blocks_new_risk():
    service = TradingService(
        memory_store := MemoryTradingStore(),
        RehearsalExchange(),
        RiskPolicy(),
        clock=lambda: 1_000_000,
    )
    await service.recover(snapshot())
    receipt = await service.place(command(qty=Decimal("2")))
    await service.record_execution(
        Execution(
            "acct",
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
    try:
        await service.record_execution(
            Execution(
                "acct",
                receipt.cl_ord_id,
                "BTCUSDT",
                "e-1",
                Decimal("1"),
                Decimal("101"),
                Decimal("0.1"),
                "USDT",
                1_000_100,
            )
        )
    except Conflict:
        pass
    view = await service.risk_view("acct")
    return {
        "ready": view.ready,
        "reasons": list(view.reasons),
        "passed": not view.ready and "EXECUTION_KEY_CONFLICT" in view.reasons,
    }


async def main():
    scenarios = [
        await scenario("send_timeout_unknown_not_resent", unknown_timeout_is_not_resent),
        await scenario(
            "accepted_response_not_persisted_recovered",
            accepted_but_unpersisted_response_is_adopted_without_resend,
        ),
        await scenario("ledger_failure_rolls_back", ledger_failure_rolls_back_atomically),
        await scenario(
            "late_fill_after_cancel", late_fill_after_cancel_is_booked_without_reactivation
        ),
        await scenario("duplicate_fill_conflict_blocks", duplicate_fill_conflict_blocks_new_risk),
    ]
    print(
        json.dumps(
            {
                "ok": all(item.get("ok") and item.get("passed", True) for item in scenarios),
                "scenarios": scenarios,
            },
            indent=2,
        )
    )


if __name__ == "__main__":
    asyncio.run(main())
