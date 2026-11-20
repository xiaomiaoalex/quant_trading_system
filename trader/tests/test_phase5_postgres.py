"""Phase-5 real-PostgreSQL durability, lease and backup fail-closed checks."""

from __future__ import annotations

import asyncio
import json
import os
import uuid
from contextlib import asynccontextmanager
from decimal import Decimal

import pytest

from trader.ops.backup import BackupConfig, backup_once
from trader.ops.crash_durability import verify_after_crash, write_committed_facts
from trader.ops.restore import RestoreConfig, RestoreError, restore_and_verify
from trader.trading.models import ExchangeOrderResult, ExchangeOutcome, ReconcileSnapshot
from trader.trading.postgres import PostgresTradingStore
from trader.trading.risk import RiskPolicy
from trader.trading.runtime import TradingRuntime

CONNECTION_STRING = os.getenv("POSTGRES_CONNECTION_STRING", "").strip()


def _asyncpg():
    import importlib

    return importlib.import_module("asyncpg")


async def _can_connect() -> bool:
    try:
        asyncpg = _asyncpg()
        connection = await asyncpg.connect(CONNECTION_STRING, timeout=3)
        await connection.close()
        return True
    except Exception:
        return False


@asynccontextmanager
async def schema_pool():
    try:
        asyncpg = _asyncpg()
    except ImportError:
        pytest.skip("asyncpg not installed")
    if not CONNECTION_STRING or not await _can_connect():
        pytest.skip("PostgreSQL not available")
    schema = "qts_phase5_" + uuid.uuid4().hex[:12]
    admin = await asyncpg.connect(CONNECTION_STRING)
    await admin.execute(f'CREATE SCHEMA "{schema}"')
    pool = await asyncpg.create_pool(
        CONNECTION_STRING, min_size=1, max_size=4, server_settings={"search_path": schema}
    )
    try:
        yield schema, pool
    finally:
        await pool.close()
        await admin.execute(f'DROP SCHEMA IF EXISTS "{schema}" CASCADE')
        await admin.close()


class CountingExchange:
    def __init__(self):
        self.sent = []

    async def place_order(self, command, cl_ord_id):
        self.sent.append(cl_ord_id)
        return ExchangeOrderResult(ExchangeOutcome.ACCEPTED, "broker-1", "OPEN")

    async def cancel_order(self, account_id, cl_ord_id, command_id, symbol):
        return ExchangeOrderResult(ExchangeOutcome.ACCEPTED, "broker-1", "CANCELLED")

    async def query_order(self, account_id, cl_ord_id, symbol):
        return ExchangeOrderResult(ExchangeOutcome.NOT_FOUND)


def _runtime(
    store,
    exchange,
    owner: str,
    *,
    generation: int = 1,
    checked_at_ms: int = 1_000_000,
    now_ms: int = 1_000_000,
    reconcile_interval_seconds: float = 0,
) -> TradingRuntime:
    async def provider(_account_id):
        return ReconcileSnapshot(
            account_id="lease_acct",
            checked_at_ms=checked_at_ms,
            generation=generation,
            balances={"USDT": Decimal("1000"), "BTC": Decimal("0")},
            marks={"BTCUSDT": {"price": Decimal("100"), "at_ms": checked_at_ms}},
        )

    return TradingRuntime(
        store=store,
        exchange=exchange,
        account_id="lease_acct",
        snapshot_provider=provider,
        clock=lambda: now_ms,
        policy=RiskPolicy(fee_rate=Decimal("0.001")),
        owner=owner,
        lease_seconds=30,
        lease_retry_interval_seconds=3600,
        outbox_interval_seconds=3600,
        reconcile_interval_seconds=reconcile_interval_seconds,
        command_interval_seconds=0,
    )


@pytest.mark.asyncio
async def test_lease_fencing_blocks_conflicting_instance_and_closes_old_owner():
    async with schema_pool() as (_schema, pool):
        store = PostgresTradingStore(pool)
        await store.initialize()
        exchange_a = CountingExchange()
        runtime_a = _runtime(store, exchange_a, "instance-a")
        assert await runtime_a.start() is True
        assert (await runtime_a.readiness())["ready"] is True
        token_a = runtime_a.lease_token

        exchange_b = CountingExchange()
        runtime_b = _runtime(store, exchange_b, "instance-b")
        assert await runtime_b.start() is False
        assert "EXECUTION_INSTANCE_CONFLICT" in runtime_b.last_reasons
        assert exchange_b.sent == []
        assert "reconcile" not in runtime_b.stages
        await runtime_b.stop()

        async with pool.acquire() as connection:
            await connection.execute(
                "UPDATE trading_runtime_leases SET expires_at=clock_timestamp()-interval '1 second' "
                "WHERE scope=$1",
                "trading:lease_acct",
            )
        runtime_b2 = _runtime(
            store,
            exchange_b,
            "instance-b",
            generation=2,
            checked_at_ms=1_000_100,
            now_ms=1_000_100,
        )
        started_b2 = await runtime_b2.start()
        assert started_b2 is True, (
            runtime_b2.stages,
            runtime_b2.last_reasons,
            runtime_b2.lease_token,
        )
        assert runtime_b2.lease_token and runtime_b2.lease_token > token_a

        with pytest.raises(Exception, match="EXECUTION_INSTANCE_CONFLICT"):
            await runtime_a.place(_lease_command())
        assert exchange_a.sent == []
        readiness = await runtime_a.readiness()
        assert readiness["ready"] is False
        assert "EXECUTION_INSTANCE_CONFLICT" in readiness["reasons"]

        await runtime_a.stop()
        await runtime_b.stop()
        await runtime_b2.stop()


def _lease_command():
    from trader.trading.order_spec import build_order_spec

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
        book_id="book:lease",
        price_source="SIGNAL_LIMIT",
    )
    return spec.to_command(
        command_id="lease-command-a",
        account_id="lease_acct",
        base_asset="BTC",
        quote_asset="USDT",
        deployment_id="lease-test",
        decision_id="lease-decision",
    )


@pytest.mark.asyncio
async def test_committed_facts_survive_a_fresh_connection_and_runtime_start():
    async with schema_pool() as (schema, _pool):
        expected = await write_committed_facts(schema)
        assert expected["sent_count"] == 1
        result = await verify_after_crash(schema, expected)
        assert result["checks"]["counts"] is True
        assert result["checks"]["digests"] is True
        assert result["checks"]["commands"] is True
        assert result["checks"]["no_resend"] is True
        assert result["resend_count"] == 0
        assert result["passed"] is True


@pytest.mark.asyncio
async def test_backup_without_target_is_a_failure_not_a_success(tmp_path):
    config = BackupConfig(
        dsn=CONNECTION_STRING,
        output_dir=tmp_path,
        interval_seconds=300,
        s3=None,
    )
    record = await backup_once(config)
    assert record["status"] == "failed"
    assert record["reason"] == "S3_TARGET_NOT_CONFIGURED"
    latest = json.loads((tmp_path / "latest-backup.json").read_text(encoding="utf-8"))
    assert latest["status"] == "failed"


class WrongDigestTarget:
    def download_and_hash(self, key, destination):
        return {"path": str(destination), "sha256": "b" * 64, "size_bytes": 1, "verified_at_ms": 1}

    def head(self, key):
        return {"content_length": 1, "metadata": {}, "etag": "", "last_modified_ms": None}

    def upload(self, path, key, metadata):
        return {}


@pytest.mark.asyncio
async def test_restore_rejects_an_object_that_does_not_match_the_manifest(tmp_path):
    manifest = {
        "schema_version": 1,
        "run_id": "test",
        "status": "succeeded",
        "backup": {
            "completed_at_ms": 1,
            "cadence_seconds": 300,
            "sha256": "a" * 64,
            "object_key": "qts/backups/test.dump",
            "source_database": {"host": "db-a", "database": "trading"},
            "target": {"off_host": True},
            "facts": {"counts": {}, "digests": {}},
        },
    }
    manifest_path = tmp_path / "manifest.json"
    manifest_path.write_text(json.dumps(manifest), encoding="utf-8")
    config = RestoreConfig(
        dsn=CONNECTION_STRING,
        manifest_path=manifest_path,
        target_database="qts_restore_digest_test",
        output_dir=tmp_path,
        drop_after=True,
    )
    with pytest.raises(RestoreError, match="RESTORE_OBJECT_DIGEST_MISMATCH"):
        await restore_and_verify(config, s3_target=WrongDigestTarget())


@pytest.mark.asyncio
async def test_readiness_itself_closes_when_the_lease_expires():
    async with schema_pool() as (_schema, pool):
        store = PostgresTradingStore(pool)
        await store.initialize()
        exchange = CountingExchange()
        runtime = _runtime(store, exchange, "instance-readiness")
        assert await runtime.start() is True
        assert (await runtime.readiness())["ready"] is True
        async with pool.acquire() as connection:
            await connection.execute(
                "UPDATE trading_runtime_leases SET expires_at=clock_timestamp()-interval '1 second' "
                "WHERE scope=$1",
                "trading:lease_acct",
            )
        readiness = await runtime.readiness()
        assert readiness["ready"] is False
        assert "EXECUTION_INSTANCE_CONFLICT" in readiness["reasons"]
        assert runtime.ready is False
        assert runtime.started is False
        assert exchange.sent == []
        await runtime.stop()


def test_backup_config_records_ssh_tunnel_remote_identity(tmp_path):
    from trader.ops.backup import BackupConfig
    from trader.ops.s3 import S3Config

    config = BackupConfig(
        dsn="postgresql://placeholder",
        output_dir=tmp_path,
        interval_seconds=240,
        s3=S3Config(
            endpoint_url="http://127.0.0.1:19000",
            bucket="qts-p5",
            prefix="phase5",
            access_key="placeholder",
            secret_key="placeholder",
        ),
        off_host_attested=True,
        attested_by="user-designated-alex@my-server",
        target_kind="s3_compatible",
        transport="ssh_tunnel",
        remote_host="192.168.3.4",
        ssh_host_key_verified=True,
    )
    identity = config.target_identity()
    assert identity["transport"] == "ssh_tunnel"
    assert identity["endpoint_host"] == "192.168.3.4"
    assert identity["tunnel_endpoint_host"] == "127.0.0.1"
    assert identity["ssh_host_key_verified"] is True
    assert identity["identity_verified"] is True
    assert identity["off_host"] is True


@pytest.mark.asyncio
async def test_reconcile_loop_does_not_touch_the_book_after_lease_expiry():
    async with schema_pool() as (_schema, pool):
        store = PostgresTradingStore(pool)
        await store.initialize()
        exchange = CountingExchange()
        runtime = _runtime(
            store,
            exchange,
            "instance-reconcile",
            generation=1,
            checked_at_ms=1_000_000,
            now_ms=1_000_000,
            reconcile_interval_seconds=0.5,
        )
        assert await runtime.start() is True
        stale: list[int] = []
        state = {"expired": False}
        original_recover = runtime.service.recover
        recovered = asyncio.Event()

        async def tracked_recover(snapshot):
            if state["expired"]:
                stale.append(1)
            result = await original_recover(snapshot)
            recovered.set()
            return result

        runtime.service.recover = tracked_recover
        await asyncio.wait_for(recovered.wait(), timeout=5)
        async with pool.acquire() as connection:
            await connection.execute(
                "UPDATE trading_runtime_leases SET expires_at=clock_timestamp()-interval '1 second' "
                "WHERE scope=$1",
                "trading:lease_acct",
            )
        state["expired"] = True
        await asyncio.sleep(0.8)
        assert stale == [], "a stale owner reconciled after losing the lease"
        assert runtime.ready is False
        assert runtime.started is False
        assert runtime.lease_token is None
        await runtime.stop()
