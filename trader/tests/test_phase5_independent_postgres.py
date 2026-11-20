"""Phase-5 independent acceptance tests - real PostgreSQL half.

Requires an explicit DSN (docs/INTERFACE_CONTRACTS.md 15.1):

    POSTGRES_CONNECTION_STRING=postgresql://... python -m pytest \\
        trader/tests/test_phase5_independent_postgres.py

Every test creates its own schema ``qts_p5ind_<random>`` and drops it on exit.
No test reads or writes the default schema, an existing runtime, an existing
container or anything that holds real funds.

Covered here: 5.8 (real task-owned process crash + restart), 5.11 (lease
fencing), 5.9 (DR gate refuses new risk on a real runtime), 5.6 (object-store
receipt fail-closed against real PostgreSQL facts) and the 5.4 readiness
invariant on a real runtime.

Expectations come from the parent work order and contract 13.16, not from the
engineer's evidence counts.
"""

from __future__ import annotations

import asyncio
import hashlib
import json
import os
import subprocess
import sys
import time
import uuid
from contextlib import asynccontextmanager
from decimal import Decimal
from pathlib import Path
from urllib.parse import urlsplit, urlunsplit

import pytest

ROOT = Path(__file__).resolve().parents[2]
CONNECTION_STRING = os.getenv("POSTGRES_CONNECTION_STRING", "").strip()

ACCOUNT_ID = "p5ind_acct"
MARK_PRICE = Decimal("50")
FUND_USDT = Decimal("1000")
ORDER_QTY = Decimal("2")
# A frozen venue clock keeps signal timestamps, snapshots and risk checks aligned.
NOW_FIXED_MS = 1_700_000_000_000


def _asyncpg():
    import importlib

    return importlib.import_module("asyncpg")


async def _can_connect() -> bool:
    if not CONNECTION_STRING:
        return False
    try:
        asyncpg = _asyncpg()
        connection = await asyncpg.connect(CONNECTION_STRING, timeout=3)
        await connection.close()
        return True
    except Exception:
        return False


@asynccontextmanager
async def task_schema():
    """A unique schema owned by this test, dropped on exit."""
    try:
        asyncpg = _asyncpg()
    except ImportError:
        pytest.skip("asyncpg is not installed")
    if not await _can_connect():
        pytest.skip("POSTGRES_CONNECTION_STRING is not set or the server is unreachable")
    schema = "qts_p5ind_" + uuid.uuid4().hex[:12]
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
    """Counts every venue send; a blocked instance must stay at zero."""

    def __init__(self):
        self.sent: list[str] = []

    async def place_order(self, command, cl_ord_id):
        self.sent.append(cl_ord_id)
        from trader.trading.models import ExchangeOrderResult, ExchangeOutcome

        return ExchangeOrderResult(ExchangeOutcome.ACCEPTED, "broker-independent", "OPEN")

    async def cancel_order(self, account_id, cl_ord_id, command_id, symbol):
        from trader.trading.models import ExchangeOrderResult, ExchangeOutcome

        return ExchangeOrderResult(ExchangeOutcome.ACCEPTED, "broker-independent", "CANCELLED")

    async def query_order(self, account_id, cl_ord_id, symbol):
        from trader.trading.models import ExchangeOrderResult, ExchangeOutcome

        return ExchangeOrderResult(ExchangeOutcome.NOT_FOUND)


def _snapshot(account_id: str, generation: int, at_ms: int):
    from trader.trading.models import ReconcileSnapshot

    return ReconcileSnapshot(
        account_id=account_id,
        checked_at_ms=at_ms,
        generation=generation,
        balances={"USDT": FUND_USDT, "BTC": Decimal("0")},
        marks={"BTCUSDT": {"price": MARK_PRICE, "at_ms": at_ms}},
    )


def _command(account_id: str, command_id: str):
    """An independently chosen order: 2 BTC at 50 USDT = 100 USDT notional."""
    from trader.trading.order_spec import build_order_spec

    spec = build_order_spec(
        symbol="BTCUSDT",
        side="BUY",
        order_type="LIMIT",
        qty=ORDER_QTY,
        limit_price=MARK_PRICE,
        reference_price=MARK_PRICE,
        reference_price_source="SIGNAL_LIMIT",
        reference_at_ms=NOW_FIXED_MS,
        decision_at_ms=NOW_FIXED_MS,
        time_in_force="GTC",
        book_id="book:independent",
        price_source="SIGNAL_LIMIT",
    )
    return spec.to_command(
        command_id=command_id,
        account_id=account_id,
        base_asset="BTC",
        quote_asset="USDT",
        deployment_id="independent-drill",
        decision_id=f"decision-{command_id}",
    )


# A single venue clock shared by every runtime fixture in this module. The
# trading store rejects any reconciliation whose checked_at_ms is not strictly
# later than the last one committed for the account, so each snapshot provider
# call must observe a strictly advancing timestamp (STALE_SNAPSHOT otherwise).
_VENUE_CLOCK = {"ms": NOW_FIXED_MS}


def _runtime(store, exchange, owner, **overrides):
    from trader.trading.risk import RiskPolicy
    from trader.trading.runtime import TradingRuntime

    # Each instance keeps its own monotonic generation so a second instance
    # never reconciles with a generation the first one already used.
    generation_base = int(overrides.pop("generation_base", 1))
    counter = {"generation": generation_base}

    async def provider(account_id):
        counter["generation"] += 1
        _VENUE_CLOCK["ms"] += 1
        return _snapshot(account_id, counter["generation"], _VENUE_CLOCK["ms"])

    defaults = dict(
        store=store,
        exchange=exchange,
        account_id=ACCOUNT_ID,
        snapshot_provider=provider,
        clock=lambda: _VENUE_CLOCK["ms"],
        policy=RiskPolicy(fee_rate=Decimal("0.001")),
        owner=owner,
        lease_seconds=15,
        outbox_interval_seconds=3600,
        reconcile_interval_seconds=0,
        command_interval_seconds=0,
    )
    defaults.update(overrides)
    return TradingRuntime(**defaults)


async def _allocate_to_book(store, book_id: str) -> None:
    from trader.trading import books as book_ledger

    async with store.transaction(ACCOUNT_ID) as state:
        book_ledger.migrate_book_ledger(state)
        if book_id in state.books:
            return
        unallocated = state.books[book_ledger.UNALLOCATED]
        amount = unallocated.balances.get("USDT", Decimal(0))
        if amount > 0:
            book_ledger.allocate(state, book_id, "USDT", amount)


# --------------------------------------------------------------------------- #
# 5.8 - a real task-owned process crash preserves committed economic facts
# --------------------------------------------------------------------------- #


def _drill_child(mode: str, schema: str, tmp_path: Path) -> subprocess.Popen:
    env = dict(os.environ)
    env.update(
        {
            "QTS_DRILL_MODE": mode,
            "QTS_DRILL_SCHEMA": schema,
            "QTS_DRILL_ACCOUNT": ACCOUNT_ID,
            "QTS_DRILL_SNAPSHOT_FILE": str(tmp_path / "snapshot.json"),
            "QTS_DRILL_READY_FILE": str(tmp_path / "ready.json"),
            "QTS_DRILL_RESULT_FILE": str(tmp_path / "result.json"),
            "POSTGRES_CONNECTION_STRING": CONNECTION_STRING,
            "PYTHONPATH": str(ROOT),
        }
    )
    return subprocess.Popen(
        [sys.executable, "-m", "trader.ops.crash_durability"],
        cwd=str(ROOT),
        env=env,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
    )


async def test_5_8_killing_the_writer_process_loses_nothing(tmp_path):
    async with task_schema() as (schema, _pool):
        writer = _drill_child("writer", schema, tmp_path)
        snapshot_file = tmp_path / "snapshot.json"
        ready_file = tmp_path / "ready.json"
        try:
            deadline = time.time() + 60
            while time.time() < deadline:
                if ready_file.is_file() and snapshot_file.is_file():
                    break
                if writer.poll() is not None:
                    break
                await asyncio.sleep(0.2)
            assert snapshot_file.is_file(), (
                f"writer did not commit facts; rc={writer.returncode} "
                f"stderr={(writer.stderr.read() if writer.stderr else '')[-800:]}"
            )
            before = json.loads(snapshot_file.read_text(encoding="utf-8"))
            # A committed command, execution and ledger must exist before the kill.
            assert before["facts"]["counts"]["trading_commands"] >= 1
            assert before["facts"]["counts"]["trading_executions"] >= 1
            assert before["facts"]["counts"]["trading_ledger"] >= 1
            assert before["sent_count"] == 1

            # Hard kill: no graceful shutdown, no flush, no cleanup.
            writer.kill()
            returncode = writer.wait(timeout=30)
            crash_output = (writer.stdout.read() if writer.stdout else "") + (
                writer.stderr.read() if writer.stderr else ""
            )
        finally:
            if writer.poll() is None:
                writer.kill()
                writer.wait(timeout=30)
            for stream in (writer.stdout, writer.stderr):
                if stream is not None:
                    stream.close()

        assert returncode != 0, f"the writer was killed, rc={returncode}: {crash_output[-800:]}"

        verifier = subprocess.run(
            [sys.executable, "-m", "trader.ops.crash_durability"],
            cwd=str(ROOT),
            env={
                **os.environ,
                "QTS_DRILL_MODE": "verifier",
                "QTS_DRILL_SCHEMA": schema,
                "QTS_DRILL_ACCOUNT": ACCOUNT_ID,
                "QTS_DRILL_SNAPSHOT_FILE": str(snapshot_file),
                "QTS_DRILL_RESULT_FILE": str(tmp_path / "result.json"),
                "POSTGRES_CONNECTION_STRING": CONNECTION_STRING,
                "PYTHONPATH": str(ROOT),
            },
            capture_output=True,
            text=True,
            timeout=90,
            check=False,
        )
        assert verifier.returncode == 0, f"verifier failed: {verifier.stderr[-1200:]}"
        after = json.loads(verifier.stdout)

        assert after["checks"]["counts"] is True
        assert after["checks"]["digests"] is True, after["actual"]["digests"]
        assert after["checks"]["commands"] is True
        assert after["checks"]["execution_keys"] is True
        assert after["checks"]["ledger_entries"] is True
        assert after["checks"]["balances"] is True
        assert after["checks"]["invariants"] is True
        # Restarting the runtime must not resend an already-committed command.
        assert after["checks"]["no_resend"] is True
        assert after["resend_count"] == 0, after


async def test_5_8_mutation_a_single_lost_row_is_detected(tmp_path):
    """T7: the durability comparison must fail on a broken copy, not pass vacuously."""
    from trader.ops.crash_durability import verify_after_crash, write_committed_facts

    async with task_schema() as (schema, _pool):
        expected = await write_committed_facts(schema, ACCOUNT_ID, dsn=CONNECTION_STRING)
        assert expected["facts"]["counts"]["trading_ledger"] >= 1

        baseline = await verify_after_crash(
            schema, expected, start_runtime=False, dsn=CONNECTION_STRING
        )
        assert baseline["passed"] is True, baseline["checks"]

        asyncpg = _asyncpg()
        connection = await asyncpg.connect(CONNECTION_STRING)
        try:
            await connection.execute(f'SET search_path TO "{schema}"')
            deleted = await connection.execute(
                "DELETE FROM trading_ledger WHERE entry_id = "
                "(SELECT entry_id FROM trading_ledger ORDER BY entry_id LIMIT 1)"
            )
            assert "DELETE 1" in deleted, deleted
        finally:
            await connection.close()

        mutated = await verify_after_crash(
            schema, expected, start_runtime=False, dsn=CONNECTION_STRING
        )
        assert mutated["passed"] is False, mutated["checks"]
        assert mutated["checks"]["digests"] is False
        assert mutated["checks"]["counts"] is False
        assert mutated["actual"]["counts"]["trading_ledger"] == (
            expected["facts"]["counts"]["trading_ledger"] - 1
        )


# --------------------------------------------------------------------------- #
# 5.11 - lease loss closes this instance; a conflicting instance cannot send
# --------------------------------------------------------------------------- #


async def test_5_11_a_conflicting_instance_cannot_reconcile_or_send():
    from trader.trading.models import NotReady
    from trader.trading.postgres import PostgresTradingStore

    async with task_schema() as (_schema, pool):
        store = PostgresTradingStore(pool)
        await store.initialize()

        exchange_a = CountingExchange()
        exchange_b = CountingExchange()
        # A never renews during this test, so its lease really expires.
        runtime_a = _runtime(
            store,
            exchange_a,
            "instance-a",
            lease_retry_interval_seconds=3600,
            generation_base=1,
        )
        runtime_b = _runtime(
            store,
            exchange_b,
            "instance-b",
            lease_retry_interval_seconds=0.2,
            generation_base=100,
        )

        assert await runtime_a.start() is True
        token_a = runtime_a.lease_token
        assert token_a is not None
        await _allocate_to_book(store, "book:independent")

        # B starts while A owns the lease: conflict, zero reconcile, zero send.
        assert await runtime_b.start() is False
        assert "EXECUTION_INSTANCE_CONFLICT" in runtime_b.last_reasons
        assert exchange_b.sent == []
        blocked_readiness = await runtime_b.readiness()
        assert blocked_readiness["ready"] is False
        assert "EXECUTION_INSTANCE_CONFLICT" in blocked_readiness["reasons"]

        with pytest.raises(NotReady, match="EXECUTION_INSTANCE_CONFLICT"):
            await runtime_b.place(_command(ACCOUNT_ID, "indep-command-conflict"))
        assert exchange_b.sent == [], "a conflicting instance must send nothing"

        # A's lease now expires; B takes over with a strictly greater token.
        await asyncio.sleep(float(runtime_a.lease_seconds) + 1.5)
        assert await runtime_b.start() is True
        assert runtime_b.lease_token is not None
        assert runtime_b.lease_token > token_a, "fencing token must only advance"

        # The loser refuses at its next IO boundary and closes readiness.
        with pytest.raises(NotReady, match="EXECUTION_INSTANCE_CONFLICT"):
            await runtime_a.place(_command(ACCOUNT_ID, "indep-command-loser"))
        assert exchange_a.sent == [], "the previous owner must send nothing after losing the lease"
        assert runtime_a.ready is False
        assert runtime_a.started is False
        loser_readiness = await runtime_a.readiness()
        assert loser_readiness["ready"] is False
        assert "EXECUTION_INSTANCE_CONFLICT" in loser_readiness["reasons"]

        # The new owner may trade.
        await _allocate_to_book(store, "book:independent")
        await runtime_b.place(_command(ACCOUNT_ID, "indep-command-owner"))
        assert len(exchange_b.sent) == 1

        await runtime_b.stop()
        await runtime_a.stop()


async def test_5_11_a_stale_owner_reconcile_pass_touches_no_account_state():
    """BF-2: once the lease is gone, the old owner's background pass must not
    take a venue snapshot or recover the account, and the next owner must win.

    The row is expired from inside the old owner's own renewal, so the pass that
    observes the loss is the one that caused it: no timer race is involved.
    """
    from trader.trading.models import NotReady
    from trader.trading.postgres import PostgresTradingStore

    async with task_schema() as (_schema, pool):
        inner = PostgresTradingStore(pool)
        await inner.initialize()

        gate = {"armed": False, "renewed": 0, "expired_rows": 0}

        class FencedStore:
            """Delegates to the real store, but can expire the row during renew."""

            def __getattr__(self, name):
                return getattr(inner, name)

            async def renew_lease(self, scope, owner, token, seconds):
                if gate["armed"]:
                    gate["armed"] = False
                    async with pool.acquire() as connection:
                        # The lease table is trading_runtime_leases (schema 001);
                        # expiring it here is what the DB would do on clock advance.
                        gate["expired_rows"] = await connection.fetchval(
                            "UPDATE trading_runtime_leases SET expires_at = "
                            "clock_timestamp() - interval '5 seconds' "
                            "WHERE scope = $1 RETURNING 1",
                            scope,
                        )
                valid = await inner.renew_lease(scope, owner, token, seconds)
                if valid:
                    gate["renewed"] += 1
                return valid

        store = FencedStore()
        exchange = CountingExchange()
        calls = {"snapshots": 0, "generation": 0}

        async def provider(account_id):
            calls["snapshots"] += 1
            calls["generation"] += 1
            _VENUE_CLOCK["ms"] += 1
            return _snapshot(account_id, calls["generation"], _VENUE_CLOCK["ms"])

        runtime_a = _runtime(
            store,
            exchange,
            "instance-a",
            lease_seconds=15,
            reconcile_interval_seconds=0.05,
            lease_retry_interval_seconds=3600,
            snapshot_provider=provider,
        )
        assert await runtime_a.start() is True
        token_a = runtime_a.lease_token
        assert token_a is not None
        await _allocate_to_book(store, "book:independent")

        recovered: list[int] = []
        original_recover = runtime_a.service.recover

        async def spy(snapshot):
            recovered.append(snapshot.generation)
            return await original_recover(snapshot)

        runtime_a.service.recover = spy

        # Count only steady-state work; startup reconciles once by design.
        calls["snapshots"] = 0
        gate["renewed"] = 0
        await asyncio.sleep(0.4)
        assert calls["snapshots"] >= 2, "the healthy owner reconciles periodically"
        assert recovered, "the healthy owner recovers the account"
        assert runtime_a.ready is True, runtime_a.last_reasons

        gate["armed"] = True
        deadline = time.monotonic() + 5.0
        while runtime_a.ready and time.monotonic() < deadline:
            await asyncio.sleep(0.02)

        # A fixture that expires zero rows would otherwise surface as a SQL error.
        assert gate["expired_rows"] == 1, "the fixture must expire exactly one lease row"
        assert runtime_a.ready is False
        assert runtime_a.started is False
        assert runtime_a.lease_token is None
        assert "EXECUTION_INSTANCE_CONFLICT" in runtime_a.last_reasons

        snapshots_after_close = calls["snapshots"]
        recovered_after_close = len(recovered)
        await asyncio.sleep(0.3)
        assert calls["snapshots"] == snapshots_after_close, "a fenced owner must stop snapshotting"
        assert len(recovered) == recovered_after_close, "a fenced owner must stop recovering"
        # Every steady-state snapshot sits behind one successful renewal.
        assert calls["snapshots"] <= gate["renewed"]
        assert len(recovered) <= calls["snapshots"]

        with pytest.raises(NotReady, match="EXECUTION_INSTANCE_CONFLICT"):
            await runtime_a.place(_command(ACCOUNT_ID, "indep-command-stale-owner"))
        assert exchange.sent == [], "a fenced owner must not send"

        exchange_b = CountingExchange()
        runtime_b = _runtime(
            store,
            exchange_b,
            "instance-b",
            lease_retry_interval_seconds=0.2,
            generation_base=100,
        )
        assert await runtime_b.start() is True
        assert runtime_b.lease_token > token_a, "the fencing token must advance on takeover"
        await _allocate_to_book(store, "book:independent")
        await runtime_b.place(_command(ACCOUNT_ID, "indep-command-new-owner"))
        assert len(exchange_b.sent) == 1

        await runtime_b.stop()
        await runtime_a.stop()


# --------------------------------------------------------------------------- #
# 5.9 - insufficient DR evidence keeps a real runtime non-READY
# --------------------------------------------------------------------------- #


class _FakeDrGate:
    """Minimal object exposing the gate contract: ready + reasons."""

    def __init__(self, ready: bool, reasons: tuple[str, ...]):
        self.ready = ready
        self.reasons = reasons


async def test_5_9_a_real_runtime_refuses_new_risk_without_dr_evidence():
    from trader.trading.models import NotReady
    from trader.trading.postgres import PostgresTradingStore

    async with task_schema() as (_schema, pool):
        store = PostgresTradingStore(pool)
        await store.initialize()
        exchange = CountingExchange()
        runtime = _runtime(
            store,
            exchange,
            "instance-dr",
            dr_gate=lambda: _FakeDrGate(False, ("DR_EVIDENCE_MISSING",)),
        )

        started = await runtime.start()
        readiness = await runtime.readiness()
        assert "DR_EVIDENCE_MISSING" in readiness["reasons"]
        assert readiness["ready"] is False, f"started={started} readiness={readiness}"

        with pytest.raises(NotReady, match="DR_EVIDENCE_MISSING"):
            await runtime.place(_command(ACCOUNT_ID, "indep-command-dr"))
        assert exchange.sent == [], "no new risk may leave the process without DR evidence"
        await runtime.stop()


async def test_5_9_mutation_adequate_dr_evidence_releases_the_same_runtime():
    """T7 companion: the refusal above is caused by the evidence, not the harness."""
    from trader.trading.postgres import PostgresTradingStore

    async with task_schema() as (_schema, pool):
        store = PostgresTradingStore(pool)
        await store.initialize()
        exchange = CountingExchange()
        runtime = _runtime(store, exchange, "instance-dr-ok", dr_gate=lambda: _FakeDrGate(True, ()))
        assert await runtime.start() is True
        readiness = await runtime.readiness()
        assert readiness["reasons"] == [], readiness
        assert readiness["ready"] is True, readiness

        await _allocate_to_book(store, "book:independent")
        await runtime.place(_command(ACCOUNT_ID, "indep-command-dr-ok"))
        assert len(exchange.sent) == 1
        await runtime.stop()


# --------------------------------------------------------------------------- #
# 5.4 - readiness invariant on a real runtime
# --------------------------------------------------------------------------- #


async def test_5_4_readiness_ready_flag_equals_an_empty_reason_list():
    from trader.trading.postgres import PostgresTradingStore

    async with task_schema() as (_schema, pool):
        store = PostgresTradingStore(pool)
        await store.initialize()
        exchange = CountingExchange()
        runtime = _runtime(store, exchange, "instance-invariant")

        before = await runtime.readiness()
        assert before["ready"] is False
        assert before["reasons"], "not-ready must always explain itself"
        assert before["ready"] == (not before["reasons"])

        assert await runtime.start() is True
        after = await runtime.readiness()
        assert after["ready"] == (not after["reasons"]), after
        assert after["ready"] is True, after
        await runtime.stop()


# --------------------------------------------------------------------------- #
# 5.6 - object-store receipt fail-closed against real PostgreSQL facts
# --------------------------------------------------------------------------- #


class _FakeObjectStore:
    """In-memory stand-in for the object store; the receipt rules are the target."""

    def __init__(self, corrupt_readback: bool = False):
        self.objects: dict[str, bytes] = {}
        self.metadata: dict[str, dict[str, str]] = {}
        self.corrupt_readback = corrupt_readback

    def upload(self, path: Path, object_key: str, metadata: dict[str, str]) -> None:
        self.objects[object_key] = Path(path).read_bytes()
        self.metadata[object_key] = dict(metadata)

    def head(self, object_key: str) -> dict:
        return {
            "content_length": len(self.objects[object_key]),
            "metadata": dict(self.metadata[object_key]),
        }

    def download_and_hash(self, object_key: str, path: Path) -> dict:
        payload = self.objects[object_key]
        if self.corrupt_readback:
            payload = payload + b"-tampered"
        Path(path).write_bytes(payload)
        return {
            "sha256": hashlib.sha256(payload).hexdigest(),
            "size_bytes": len(payload),
            "verified_at_ms": int(time.time() * 1000),
        }


class _FakeDumpRunner:
    """Writes a deterministic dump body; the dump tool itself is out of scope here."""

    def __init__(self, body: bytes):
        self.body = body

    async def dump(self, snapshot_id: str, output_path: Path) -> None:
        Path(output_path).write_bytes(self.body)


def _backup_config(output_dir: Path, off_host: bool, dsn: str = CONNECTION_STRING):
    from trader.ops.backup import BackupConfig
    from trader.ops.s3 import S3Config

    return BackupConfig(
        dsn=dsn,
        output_dir=output_dir,
        interval_seconds=300,
        s3=S3Config(
            endpoint_url="http://127.0.0.1:9000",
            bucket="qts-independent-drill",
            prefix="independent",
            access_key="placeholder",
            secret_key="placeholder",
        ),
        target_kind="local_emulator" if not off_host else "s3_compatible",
        off_host_attested=off_host,
        attested_by="independent-tester" if off_host else "",
    )


def _with_database(dsn: str, database: str) -> str:
    """Retarget a DSN at another database. The DSN is never printed or logged."""
    parts = urlsplit(dsn)
    return urlunsplit((parts.scheme, parts.netloc, "/" + database, parts.query, parts.fragment))


@asynccontextmanager
async def task_database():
    """A one-off task-owned database, dropped on exit.

    ``backup_once`` connects with a plain DSN and no ``search_path`` override, so
    the receipt tests need their own database rather than a schema.
    """
    try:
        asyncpg = _asyncpg()
    except ImportError:
        pytest.skip("asyncpg is not installed")
    if not await _can_connect():
        pytest.skip("POSTGRES_CONNECTION_STRING is not set or the server is unreachable")
    name = "qts_p5ind_bk_" + uuid.uuid4().hex[:12]
    admin = await asyncpg.connect(CONNECTION_STRING)
    await admin.execute(f'CREATE DATABASE "{name}"')
    dsn = _with_database(CONNECTION_STRING, name)
    try:
        yield name, dsn
    finally:
        await admin.execute(f'DROP DATABASE IF EXISTS "{name}" WITH (FORCE)')
        await admin.close()


async def test_5_6_a_honest_receipt_records_success_with_real_facts(tmp_path):
    from trader.ops.backup import backup_once
    from trader.ops.crash_durability import write_committed_facts

    async with task_database() as (_name, dsn):
        written = await write_committed_facts("", ACCOUNT_ID, dsn=dsn)
        expected_counts = written["facts"]["counts"]

        store = _FakeObjectStore()
        record = await backup_once(
            _backup_config(tmp_path / "out", off_host=False, dsn=dsn),
            dump_runner=_FakeDumpRunner(b"independent-dump-body"),
            s3_target=store,
            now_ms=int(time.time() * 1000),
        )

        assert record["status"] == "succeeded", record
        assert record["facts"]["counts"] == expected_counts
        assert record["invariants"]["ok"] is True
        body = b"independent-dump-body"
        assert record["backup"]["sha256"] == hashlib.sha256(body).hexdigest()
        assert record["backup"]["size_bytes"] == len(body)
        assert record["backup"]["receipt"]["head_content_length"] == len(body)
        assert record["backup"]["receipt"]["readback_sha256"] == record["backup"]["sha256"]
        assert record["backup"]["receipt"]["head_metadata_sha256"] == record["backup"]["sha256"]
        # A local emulator is never attested as an off-host target: 5.6 stays blocked.
        assert record["backup"]["target"]["off_host"] is False
        assert record["backup"]["target"]["kind"] == "local_emulator"
        assert record["backup"]["target"]["identity_verified"] is False


async def test_5_6_mutation_a_tampered_readback_is_recorded_as_failed(tmp_path):
    """T7: a read-back mismatch must be recorded as a failure, never as success."""
    from trader.ops.backup import BackupError, backup_once, load_manifest
    from trader.ops.crash_durability import write_committed_facts

    async with task_database() as (_name, dsn):
        await write_committed_facts("", ACCOUNT_ID, dsn=dsn)

        output = tmp_path / "out"
        record = await backup_once(
            _backup_config(output, off_host=False, dsn=dsn),
            dump_runner=_FakeDumpRunner(b"independent-dump-body"),
            s3_target=_FakeObjectStore(corrupt_readback=True),
            now_ms=int(time.time() * 1000),
        )

        assert record["status"] == "failed", record
        assert record["reason"] == "S3_READBACK_DIGEST_MISMATCH"
        assert "backup" not in record
        # The persisted manifest must not be usable as a success record.
        with pytest.raises(BackupError, match="BACKUP_MANIFEST_NOT_SUCCESSFUL"):
            load_manifest(output / "latest-backup.json")


async def test_5_6_a_size_mismatch_is_recorded_as_failed(tmp_path):
    from trader.ops.backup import backup_once
    from trader.ops.crash_durability import write_committed_facts

    async with task_database() as (_name, dsn):
        await write_committed_facts("", ACCOUNT_ID, dsn=dsn)

        class ShortHeadStore(_FakeObjectStore):
            def head(self, object_key: str) -> dict:
                head = super().head(object_key)
                head["content_length"] = head["content_length"] - 1
                return head

        record = await backup_once(
            _backup_config(tmp_path / "out", off_host=False, dsn=dsn),
            dump_runner=_FakeDumpRunner(b"independent-dump-body"),
            s3_target=ShortHeadStore(),
            now_ms=int(time.time() * 1000),
        )
        assert record["status"] == "failed"
        assert record["reason"] == "S3_RECEIPT_SIZE_MISMATCH"


async def test_5_6_a_metadata_digest_mismatch_is_recorded_as_failed(tmp_path):
    from trader.ops.backup import backup_once
    from trader.ops.crash_durability import write_committed_facts

    async with task_database() as (_name, dsn):
        await write_committed_facts("", ACCOUNT_ID, dsn=dsn)

        class BadMetadataStore(_FakeObjectStore):
            def upload(self, path: Path, object_key: str, metadata: dict[str, str]) -> None:
                super().upload(path, object_key, metadata)
                self.metadata[object_key]["sha256"] = "0" * 64

        record = await backup_once(
            _backup_config(tmp_path / "out", off_host=False, dsn=dsn),
            dump_runner=_FakeDumpRunner(b"independent-dump-body"),
            s3_target=BadMetadataStore(),
            now_ms=int(time.time() * 1000),
        )
        assert record["status"] == "failed"
        assert record["reason"] == "S3_RECEIPT_DIGEST_MISMATCH"


async def test_5_6_fact_snapshot_counts_match_what_was_committed():
    from trader.ops.backup import collect_fact_snapshot
    from trader.ops.crash_durability import write_committed_facts

    async with task_schema() as (schema, pool):
        written = await write_committed_facts(schema, ACCOUNT_ID, dsn=CONNECTION_STRING)
        reread = await collect_fact_snapshot(pool)
        assert reread["counts"] == written["facts"]["counts"]
        assert reread["digests"] == written["facts"]["digests"]
        assert set(reread["counts"]) == {
            "trading_accounts",
            "trading_commands",
            "trading_executions",
            "trading_ledger",
            "trading_events",
            "trading_outbox",
        }
