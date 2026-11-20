"""Task-owned crash/restart durability drill.

The writer process commits a command, an execution and ledger facts through the
real PostgreSQL transaction boundary, writes a fact snapshot, and then waits to
be killed. The verifier process reads a fresh connection and proves the
committed facts survived, then starts a new runtime to prove the restart does
not resend the already-committed command.
"""

from __future__ import annotations

import asyncio
import json
import os
import time
from decimal import Decimal
from pathlib import Path
from typing import Any

import asyncpg

from trader.ops.backup import account_invariants, collect_fact_snapshot
from trader.trading import books as book_ledger
from trader.trading.models import (
    ExchangeOrderResult,
    ExchangeOutcome,
    Execution,
    OrderObservation,
    PlaceOrderCommand,
    ReconcileSnapshot,
)
from trader.trading.order_spec import build_order_spec
from trader.trading.pg import connection_string
from trader.trading.postgres import PostgresTradingStore
from trader.trading.risk import RiskPolicy
from trader.trading.runtime import TradingRuntime
from trader.trading.service import TradingService


class CountingExchange:
    def __init__(self):
        self.sent: list[str] = []

    async def place_order(self, command, cl_ord_id):
        self.sent.append(cl_ord_id)
        return ExchangeOrderResult(ExchangeOutcome.ACCEPTED, "broker-1", "OPEN")

    async def cancel_order(self, account_id, cl_ord_id, command_id, symbol):
        return ExchangeOrderResult(ExchangeOutcome.ACCEPTED, "broker-1", "CANCELLED")

    async def query_order(self, account_id, cl_ord_id, symbol):
        return ExchangeOrderResult(ExchangeOutcome.NOT_FOUND)


def _command(account_id: str = "crash_acct") -> PlaceOrderCommand:
    spec = build_order_spec(
        symbol="BTCUSDT",
        side="BUY",
        order_type="LIMIT",
        qty=Decimal("1"),
        limit_price=Decimal("100"),
        reference_price=Decimal("100"),
        reference_price_source="SIGNAL_LIMIT",
        reference_at_ms=1_000_000,
        decision_at_ms=1_000_000,
        time_in_force="GTC",
        book_id="book:crash",
        price_source="SIGNAL_LIMIT",
    )
    return spec.to_command(
        command_id="crash-command-1",
        account_id=account_id,
        base_asset="BTC",
        quote_asset="USDT",
        deployment_id="crash-drill",
        decision_id="crash-decision-1",
    )


def _snapshot(account_id: str, generation: int = 1) -> ReconcileSnapshot:
    return ReconcileSnapshot(
        account_id=account_id,
        checked_at_ms=1_000_000,
        generation=generation,
        balances={"USDT": Decimal("1000"), "BTC": Decimal("0")},
        marks={"BTCUSDT": {"price": Decimal("100"), "at_ms": 1_000_000}},
    )


def _pool_kwargs(schema: str) -> dict[str, Any]:
    kwargs: dict[str, Any] = {}
    if schema:
        kwargs["server_settings"] = {"search_path": schema}
    return kwargs


async def _schema_pool(dsn: str, schema: str):
    kwargs = _pool_kwargs(schema)
    return await asyncpg.create_pool(dsn, min_size=1, max_size=2, **kwargs)


async def write_committed_facts(
    schema: str, account_id: str = "crash_acct", dsn: str | None = None
) -> dict[str, Any]:
    pool = await _schema_pool(dsn or connection_string(), schema)
    store = PostgresTradingStore(pool)
    exchange = CountingExchange()
    service = TradingService(
        store=store,
        exchange=exchange,
        policy=RiskPolicy(fee_rate=Decimal("0.001")),
        clock=lambda: 1_000_000,
    )
    try:
        await store.initialize()
        await service.recover(_snapshot(account_id))
        async with store.transaction(account_id) as state:
            book_ledger.migrate_book_ledger(state)
            if "book:crash" not in state.books:
                unallocated = state.books[book_ledger.UNALLOCATED]
                amount = unallocated.balances.get("USDT", Decimal(0))
                if amount > 0:
                    book_ledger.allocate(state, "book:crash", "USDT", amount)

        receipt = await service.place(_command(account_id))
        await service.record_execution(
            Execution(
                account_id,
                receipt.cl_ord_id,
                "BTCUSDT",
                "crash-exec-1",
                Decimal("1"),
                Decimal("100"),
                Decimal("0.1"),
                "USDT",
                1_000_100,
            )
        )
        await service.observe(
            OrderObservation(
                account_id, receipt.cl_ord_id, "FILLED", Decimal("1"), "broker-1", 1_000_100
            )
        )
        facts = await collect_fact_snapshot(pool)
        invariants = await account_invariants(pool)
        state = await store.read(account_id)
        return {
            "account_id": account_id,
            "pid": os.getpid(),
            "written_at_ms": int(time.time() * 1000),
            "sent_count": len(exchange.sent),
            "facts": facts,
            "invariants": invariants,
            "commands": {record.command_id: record.status for record in state.commands.values()},
            "execution_keys": sorted(state.executions),
            "ledger_entries": len(state.ledger),
            "balances": {asset: str(value) for asset, value in state.balances.items()},
        }
    finally:
        await pool.close()


async def verify_after_crash(
    schema: str,
    expected: dict[str, Any],
    *,
    start_runtime: bool = True,
    dsn: str | None = None,
) -> dict[str, Any]:
    account_id = str(expected.get("account_id", "crash_acct"))
    pool = await _schema_pool(dsn or connection_string(), schema)
    store = PostgresTradingStore(pool)
    try:
        facts = await collect_fact_snapshot(pool)
        invariants = await account_invariants(pool)
        state = await store.read(account_id)
        commands = {record.command_id: record.status for record in state.commands.values()}
        checks = {
            "counts": facts["counts"] == expected["facts"]["counts"],
            "digests": facts["digests"] == expected["facts"]["digests"],
            "commands": commands == expected["commands"],
            "execution_keys": sorted(state.executions) == expected["execution_keys"],
            "ledger_entries": len(state.ledger) == expected["ledger_entries"],
            "balances": {asset: str(value) for asset, value in state.balances.items()}
            == expected["balances"],
            "invariants": bool(invariants.get("ok")),
        }
        exchange = CountingExchange()
        runtime_started = None
        runtime_ready = None
        if start_runtime:
            balance_assets = state.balances
            snapshot = ReconcileSnapshot(
                account_id=account_id,
                checked_at_ms=int(time.time() * 1000),
                generation=2,
                balances=dict(balance_assets),
                marks={"BTCUSDT": {"price": Decimal("100"), "at_ms": int(time.time() * 1000)}},
            )

            async def provider(_account_id):
                return snapshot

            runtime = TradingRuntime(
                store=store,
                exchange=exchange,
                account_id=account_id,
                snapshot_provider=provider,
                clock=lambda: int(time.time() * 1000),
                policy=RiskPolicy(fee_rate=Decimal("0.001")),
                owner="crash-verifier",
                lease_seconds=5,
                outbox_interval_seconds=3600,
                reconcile_interval_seconds=0,
                command_interval_seconds=0,
            )
            runtime_started = await runtime.start()
            runtime_ready = (await runtime.readiness())["ready"]
            await runtime.stop()
        checks["no_resend"] = exchange.sent == []
        return {
            "account_id": account_id,
            "verified_at_ms": int(time.time() * 1000),
            "checks": checks,
            "passed": all(checks.values()),
            "runtime_started": runtime_started,
            "runtime_ready": runtime_ready,
            "resend_count": len(exchange.sent),
            "actual": {
                "counts": facts["counts"],
                "digests": facts["digests"],
                "commands": commands,
                "execution_keys": sorted(state.executions),
                "ledger_entries": len(state.ledger),
            },
        }
    finally:
        await pool.close()


def _write_json(path: str | os.PathLike[str], payload: dict[str, Any]) -> None:
    target = Path(path)
    target.parent.mkdir(parents=True, exist_ok=True)
    temporary = target.with_suffix(target.suffix + ".tmp")
    temporary.write_text(json.dumps(payload, indent=2, sort_keys=True), encoding="utf-8")
    temporary.replace(target)


async def writer_main() -> None:
    schema = os.getenv("QTS_DRILL_SCHEMA", "")
    account_id = os.getenv("QTS_DRILL_ACCOUNT", "crash_acct")
    ready_file = os.getenv("QTS_DRILL_READY_FILE", "")
    snapshot_file = os.getenv("QTS_DRILL_SNAPSHOT_FILE", "")
    snapshot = await write_committed_facts(schema, account_id)
    if snapshot_file:
        _write_json(snapshot_file, snapshot)
    if ready_file:
        _write_json(
            ready_file, {"ready": True, "pid": os.getpid(), "at_ms": int(time.time() * 1000)}
        )
    # Wait to be killed; the committed transaction above is the evidence.
    await asyncio.Event().wait()


async def verifier_main() -> None:
    schema = os.getenv("QTS_DRILL_SCHEMA", "")
    snapshot_file = os.getenv("QTS_DRILL_SNAPSHOT_FILE", "")
    result_file = os.getenv("QTS_DRILL_RESULT_FILE", "")
    expected = json.loads(Path(snapshot_file).read_text(encoding="utf-8"))
    result = await verify_after_crash(schema, expected)
    if result_file:
        _write_json(result_file, result)
    print(json.dumps(result, sort_keys=True))


if __name__ == "__main__":
    mode = (os.getenv("QTS_DRILL_MODE") or "writer").strip().lower()
    asyncio.run(writer_main() if mode == "writer" else verifier_main())
