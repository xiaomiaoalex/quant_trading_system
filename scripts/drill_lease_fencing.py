"""Prove execution-lease fencing against real PostgreSQL.

A second instance must not reconcile or send while the first owns the lease.
After the lease expires, the first instance's readiness closes and any send
attempt is refused; the new owner acquires a higher fencing token and trades.
"""

from __future__ import annotations

import argparse
import asyncio
import json
import os
import sys
import time
import uuid
from decimal import Decimal
from pathlib import Path
from typing import Any

import asyncpg

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from trader.services.alerting import AlertMonitor  # noqa: E402
from trader.trading import books as book_ledger  # noqa: E402
from trader.trading.models import (  # noqa: E402
    ExchangeOrderResult,
    ExchangeOutcome,
    ReconcileSnapshot,
)
from trader.trading.order_spec import build_order_spec  # noqa: E402
from trader.trading.postgres import PostgresTradingStore  # noqa: E402
from trader.trading.risk import RiskPolicy  # noqa: E402
from trader.trading.runtime import TradingRuntime  # noqa: E402

ACCOUNT = "lease_acct"
BOOK = "book:lease"


class CountingExchange:
    def __init__(self):
        self.sent: list[str] = []

    async def place_order(self, command, cl_ord_id):
        self.sent.append(cl_ord_id)
        return ExchangeOrderResult(ExchangeOutcome.ACCEPTED, "broker-lease", "OPEN")

    async def cancel_order(self, account_id, cl_ord_id, command_id, symbol):
        return ExchangeOrderResult(ExchangeOutcome.ACCEPTED, "broker-lease", "CANCELLED")

    async def query_order(self, account_id, cl_ord_id, symbol):
        return ExchangeOrderResult(ExchangeOutcome.NOT_FOUND)


class RecordingTransport:
    def __init__(self):
        self.alerts: list[dict[str, Any]] = []
        self.heartbeats: list[dict[str, Any]] = []

    async def send_alert(self, signal):
        from trader.services.alerting import DeliveryResult

        self.alerts.append(signal.payload())
        return DeliveryResult(True, signal.event_id, 1, 200, signal.event_id)

    async def send_heartbeat(self, payload):
        from trader.services.alerting import DeliveryResult

        self.heartbeats.append(payload)
        return DeliveryResult(True, str(payload.get("heartbeat_id")), 1, 200, "ack")

    async def close(self):
        return None


def _command(command_id: str = "lease-command-1"):
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
        book_id=BOOK,
        price_source="SIGNAL_LIMIT",
    )
    return spec.to_command(
        command_id=command_id,
        account_id=ACCOUNT,
        base_asset="BTC",
        quote_asset="USDT",
        deployment_id="lease-drill",
        decision_id=command_id,
    )


def _runtime(
    store,
    exchange,
    owner: str,
    *,
    generation: int = 1,
    checked_at_ms: int = 1_000_000,
    now_ms: int = 1_000_000,
) -> TradingRuntime:
    async def provider(_account_id):
        return ReconcileSnapshot(
            account_id=ACCOUNT,
            checked_at_ms=checked_at_ms,
            generation=generation,
            balances={"USDT": Decimal("1000"), "BTC": Decimal("0")},
            marks={"BTCUSDT": {"price": Decimal("100"), "at_ms": checked_at_ms}},
        )

    return TradingRuntime(
        store=store,
        exchange=exchange,
        account_id=ACCOUNT,
        snapshot_provider=provider,
        clock=lambda: now_ms,
        policy=RiskPolicy(fee_rate=Decimal("0.001")),
        owner=owner,
        lease_seconds=30,
        lease_retry_interval_seconds=3600,
        outbox_interval_seconds=3600,
        reconcile_interval_seconds=0,
        command_interval_seconds=0,
    )


async def _fund(store) -> None:
    async with store.transaction(ACCOUNT) as state:
        book_ledger.migrate_book_ledger(state)
        if BOOK not in state.books:
            unallocated = state.books[book_ledger.UNALLOCATED]
            amount = unallocated.balances.get("USDT", Decimal(0))
            if amount > 0:
                book_ledger.allocate(state, BOOK, "USDT", amount)


async def run(dsn: str, output: Path) -> dict:
    schema = "qts_lease_" + uuid.uuid4().hex[:12]
    admin = await asyncpg.connect(dsn, timeout=10)
    await admin.execute(f'CREATE SCHEMA "{schema}"')
    pool = await asyncpg.create_pool(
        dsn, min_size=1, max_size=4, server_settings={"search_path": schema}
    )
    store = PostgresTradingStore(pool)
    evidence: dict[str, Any] = {"schema": schema, "started_at_ms": int(time.time() * 1000)}
    try:
        await store.initialize()
        exchange_a = CountingExchange()
        runtime_a = _runtime(store, exchange_a, "instance-a")
        evidence["a_started"] = await runtime_a.start()
        evidence["a_ready"] = (await runtime_a.readiness())["ready"]
        await _fund(store)
        evidence["a_token"] = runtime_a.lease_token

        exchange_b = CountingExchange()
        runtime_b = _runtime(store, exchange_b, "instance-b")
        evidence["b_started_while_owned"] = await runtime_b.start()
        evidence["b_reasons"] = list(runtime_b.last_reasons)
        evidence["b_stages"] = list(runtime_b.stages)
        evidence["b_sent_while_blocked"] = len(exchange_b.sent)
        await runtime_b.stop()

        # Use the task schema's pool; the admin connection has the default
        # search_path and must never touch a non-task lease table.
        await pool.execute(
            "UPDATE trading_runtime_leases SET expires_at=clock_timestamp()-interval '1 second' "
            "WHERE scope=$1",
            f"trading:{ACCOUNT}",
        )
        runtime_b2 = _runtime(
            store,
            exchange_b,
            "instance-b",
            generation=2,
            checked_at_ms=1_000_100,
            now_ms=1_000_100,
        )
        evidence["b2_started_after_expiry"] = await runtime_b2.start()
        evidence["b2_token"] = runtime_b2.lease_token
        evidence["b2_ready"] = (await runtime_b2.readiness())["ready"]
        evidence["fencing_token_advanced"] = bool(
            evidence["b2_token"] and evidence["b2_token"] > (evidence["a_token"] or 0)
        )

        refused = None
        try:
            await runtime_a.place(_command("lease-command-a"))
        except Exception as exc:
            refused = str(exc)
        evidence["a_send_after_loss"] = len(exchange_a.sent)
        evidence["a_refusal"] = refused
        readiness_a = await runtime_a.readiness()
        evidence["a_ready_after_loss"] = readiness_a["ready"]
        evidence["a_reasons_after_loss"] = readiness_a["reasons"]

        monitor = AlertMonitor(
            source="lease-drill",
            account_id=ACCOUNT,
            readiness=runtime_a.readiness,
            transport=RecordingTransport(),
            interval_seconds=60,
        )
        scan = await monitor.scan_once()
        evidence["alert_codes"] = sorted(
            {item["code"] for item in scan["alerts"] if item["delivered"]}
        )
        evidence["alerts_delivered"] = all(item["delivered"] for item in scan["alerts"])

        receipt = await runtime_b2.place(_command("lease-command-b"))
        evidence["b2_command_status"] = receipt.status
        evidence["b2_send_count"] = len(exchange_b.sent)
        evidence["b2_ready_after_send"] = (await runtime_b2.readiness())["ready"]

        evidence["passed"] = bool(
            evidence["a_started"]
            and evidence["a_ready"]
            and evidence["b_started_while_owned"] is False
            and "EXECUTION_INSTANCE_CONFLICT" in evidence["b_reasons"]
            and evidence["b_sent_while_blocked"] == 0
            and evidence["b2_started_after_expiry"]
            and evidence["fencing_token_advanced"]
            and evidence["a_send_after_loss"] == 0
            and evidence["a_ready_after_loss"] is False
            and "EXECUTION_INSTANCE_CONFLICT" in evidence["a_reasons_after_loss"]
            and "EXECUTION_LEASE_LOST" in evidence["alert_codes"]
            and receipt.status in {"QUEUED", "CONFIRMED"}
        )
        await runtime_a.stop()
        await runtime_b.stop()
        await runtime_b2.stop()
    finally:
        await pool.close()
        await admin.execute(f'DROP SCHEMA IF EXISTS "{schema}" CASCADE')
        await admin.close()
        evidence["completed_at_ms"] = int(time.time() * 1000)
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(json.dumps(evidence, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    print(json.dumps({"passed": evidence["passed"], "output": str(output)}, sort_keys=True))
    return evidence


def _default_dsn() -> str:
    dsn = os.getenv("POSTGRES_CONNECTION_STRING", "").strip()
    if not dsn:
        raise SystemExit("POSTGRES_DSN_NOT_CONFIGURED")
    return dsn


def main() -> None:
    parser = argparse.ArgumentParser(description="QTS lease fencing drill")
    parser.add_argument(
        "--dsn",
        default=_default_dsn(),
    )
    parser.add_argument(
        "--output",
        type=Path,
        default=ROOT / "artifacts/foundation/phase5/evidence/lease-fencing.json",
    )
    args = parser.parse_args()
    evidence = asyncio.run(run(args.dsn, args.output))
    raise SystemExit(0 if evidence["passed"] else 1)


if __name__ == "__main__":
    main()
