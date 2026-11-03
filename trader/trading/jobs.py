"""Durable research jobs. API enqueue and worker execution are separate."""

from __future__ import annotations

import asyncio
import copy
import hashlib
import json
import re
import time
import uuid
from pathlib import Path
from typing import Any

from trader.trading import books as book_ledger
from trader.trading.core import policy_from_config
from trader.trading.datasets import (
    canonical,
    dataset_metadata,
    decimal_text,
    digest,
    freeze_dataset,
)
from trader.trading.eligibility import (
    DATASET_ELIGIBILITY_GATE_UNAVAILABLE,
    DATASET_PROVENANCE_MISMATCH,
    DATASET_PROVENANCE_UNTRUSTED,
    HISTORY_STORE_UNAVAILABLE,
    link_is_trusted,
)
from trader.trading.research_schema import research_schema_sql
from trader.trading.version import SPOT_RUNTIME_VERSION

_STRATEGY_FIELDS = frozenset(
    {"module_path", "params", "version", "code_hash", "strategy_id", "book_id"}
)
_MANIFEST_FIELDS = frozenset(
    {
        "dataset_ids",
        "strategy",
        "strategies",
        "book_allocations",
        "initial_cash",
        "fee_bps",
        "slippage_bps",
        "engine",
        "purpose",
        "risk_config",
        "provenance",
        "runtime_version",
    }
)


def _positive_decimal(value: Any, code: str) -> "Decimal":
    from decimal import Decimal, DecimalException

    try:
        amount = Decimal(str(value))
    except (DecimalException, TypeError, ValueError):
        raise ValueError(code) from None
    if not amount.is_finite() or amount <= 0:
        raise ValueError(code)
    return amount


def _strategy_identity(spec: dict) -> tuple[str, str]:
    """Resolve the stable ``strategy_id``/``book_id`` pair of one strategy."""
    strategy_id = spec.get("strategy_id")
    if not isinstance(strategy_id, str) or not strategy_id.strip():
        raise ValueError("Joint strategies require a non-empty strategy_id")
    strategy_id = strategy_id.strip()
    book_id = book_ledger.book_id_for(strategy_id)
    book_ledger.validate_strategy_book_id(book_id)
    return strategy_id, book_id


def freeze_strategy(spec: dict) -> dict:
    if set(spec) - _STRATEGY_FIELDS:
        raise ValueError("Dynamic strategy code is not supported")
    module = spec.get("module_path", "")
    if not re.fullmatch(r"trader\.strategies\.[a-z][a-z0-9_]*", module):
        raise ValueError("Strategy must be an installed trader.strategies module")
    root = (Path(__file__).resolve().parents[1] / "strategies").resolve()
    source = (root / (module.rsplit(".", 1)[1] + ".py")).resolve()
    if source.parent != root or not source.is_file():
        raise ValueError("Unknown strategy module")
    code_hash = hashlib.sha256(source.read_bytes()).hexdigest()
    if spec.get("code_hash") not in (None, code_hash):
        raise ValueError("Strategy source changed since manifest was frozen")
    params = spec.get("params", {})
    if not isinstance(params, dict):
        raise ValueError("Strategy params must be an object")
    frozen = {
        "module_path": module,
        "params": copy.deepcopy(params),
        "version": str(spec.get("version", "1")),
        "code_hash": code_hash,
    }
    if "strategy_id" in spec or "book_id" in spec:
        strategy_id, book_id = _strategy_identity(spec)
        declared = spec.get("book_id")
        if declared is not None and declared != book_id:
            raise ValueError(
                f"Strategy {strategy_id} must use its stable book {book_id}, not {declared}"
            )
        frozen["strategy_id"] = strategy_id
        frozen["book_id"] = book_id
    return frozen


def normalize_backtest_manifest(manifest: dict) -> dict:
    """The single manifest normalizer used by submit, retry and execution.

    One shape feeds the frozen store, the worker revalidation and the retry
    path, so a joint run cannot be accepted through a laxer caller. Nested
    parameters and allocations are copied; a caller mutating its own input
    after submission cannot change the frozen document.
    """
    from decimal import Decimal

    if not isinstance(manifest, dict):
        raise ValueError("Backtest manifest must be an object")
    if set(manifest) - _MANIFEST_FIELDS:
        raise ValueError("Unknown backtest manifest fields")
    ids = manifest.get("dataset_ids", [])
    if not isinstance(ids, list) or not ids or len(set(ids)) != len(ids):
        raise ValueError("Provide unique dataset_ids")
    for dataset_id in ids:
        if not isinstance(dataset_id, str) or not dataset_id.startswith("ds_"):
            raise ValueError("dataset_ids must reference Research datasets (ds_*)")
    engine = manifest.get("engine", "event_runtime")
    purpose = manifest.get("purpose", "research_screening")
    if engine != "event_runtime":
        raise ValueError("Only the event runtime may execute frozen backtests")
    if purpose not in {"research_screening", "admission_evidence"}:
        raise ValueError("Unsupported purpose")
    initial_cash = decimal_text(manifest.get("initial_cash", "10000"))
    fee_bps = decimal_text(manifest.get("fee_bps", "10"))
    slippage_bps = decimal_text(manifest.get("slippage_bps", "5"))
    if (
        Decimal(initial_cash) <= 0
        or not 0 <= Decimal(fee_bps) <= 100
        or not 0 <= Decimal(slippage_bps) <= 1000
    ):
        raise ValueError("Invalid capital or execution costs")
    if purpose == "admission_evidence" and (Decimal(fee_bps) <= 0 or Decimal(slippage_bps) <= 0):
        raise ValueError("Admission evidence requires fees and slippage")
    raw_risk = manifest.get("risk_config", {}) or {}
    if not isinstance(raw_risk, dict):
        raise ValueError("risk_config must be an object")
    risk_config = copy.deepcopy(raw_risk)
    # Refuse an invalid risk policy at freeze time as well; the executor still
    # builds the policy through the same shared parser.
    policy_from_config(risk_config, Decimal(fee_bps) / Decimal(10000))

    has_strategy = "strategy" in manifest
    has_strategies = "strategies" in manifest
    if has_strategy == has_strategies:
        raise ValueError("Provide exactly one of strategy or strategies")
    frozen: dict[str, Any] = {
        "dataset_ids": sorted(ids),
        "initial_cash": initial_cash,
        "fee_bps": fee_bps,
        "slippage_bps": slippage_bps,
        "engine": engine,
        "purpose": purpose,
        "risk_config": risk_config,
    }
    if has_strategy:
        if "book_allocations" in manifest:
            raise ValueError("book_allocations require a joint strategies manifest")
        strategy = freeze_strategy(manifest["strategy"])
        if "strategy_id" in strategy:
            raise ValueError("A single-strategy manifest must not declare strategy_id")
        frozen["strategy"] = strategy
    else:
        strategies = manifest["strategies"]
        if not isinstance(strategies, list) or len(strategies) < 2:
            raise ValueError("A joint backtest requires at least two strategies")
        frozen_strategies = []
        seen_strategy_ids: set[str] = set()
        seen_books: set[str] = set()
        for item in strategies:
            if not isinstance(item, dict):
                raise ValueError("Each joint strategy must be an object")
            strategy = freeze_strategy(item)
            strategy_id = strategy["strategy_id"]
            if strategy_id in seen_strategy_ids:
                raise ValueError(f"Duplicate strategy_id: {strategy_id}")
            seen_strategy_ids.add(strategy_id)
            if strategy["book_id"] in seen_books:
                raise ValueError(f"Duplicate strategy book: {strategy['book_id']}")
            seen_books.add(strategy["book_id"])
            frozen_strategies.append(strategy)
        frozen_strategies.sort(key=lambda entry: entry["strategy_id"])
        allocations = manifest.get("book_allocations")
        if not isinstance(allocations, dict) or not allocations:
            raise ValueError("A joint backtest needs explicit book_allocations")
        if set(allocations) != seen_books:
            raise ValueError("Book allocations must match the configured strategy books")
        frozen_allocations: dict[str, str] = {}
        total = Decimal(0)
        for book_id in sorted(allocations):
            amount = _positive_decimal(allocations[book_id], "Invalid book allocation")
            frozen_allocations[book_id] = decimal_text(amount)
            total += amount
        if total != Decimal(initial_cash):
            raise ValueError("Book allocations must sum to the initial cash")
        frozen["strategies"] = frozen_strategies
        frozen["book_allocations"] = frozen_allocations

    if "runtime_version" in manifest:
        if manifest["runtime_version"] != SPOT_RUNTIME_VERSION:
            raise ValueError("Legacy runtime version; submit a new frozen job manifest")
        frozen["runtime_version"] = SPOT_RUNTIME_VERSION
    if "provenance" in manifest:
        provenance = manifest["provenance"]
        if not isinstance(provenance, dict) or not all(
            isinstance(key, str) and isinstance(value, str) for key, value in provenance.items()
        ):
            raise ValueError("provenance must be an object of dataset ids to hashes")
        if set(provenance) != set(frozen["dataset_ids"]):
            raise ValueError(f"{DATASET_PROVENANCE_MISMATCH}: dataset set")
        frozen["provenance"] = dict(provenance)
    return frozen


def _model_marker(record: dict) -> str:
    if record.get("manifest", {}).get("runtime_version") != SPOT_RUNTIME_VERSION:
        return "legacy"
    if record.get("status") != "SUCCEEDED":
        return "event_runtime"
    result = record.get("result")
    if (
        isinstance(result, dict)
        and result.get("engine") == "event_runtime"
        and result.get("runtime_version") == SPOT_RUNTIME_VERSION
    ):
        return "event_runtime"
    # Results produced before the event runtime existed are not admission evidence.
    return "legacy"


def _record(row):
    if row is None:
        return None
    result = dict(row)
    for field in ("manifest", "result"):
        if isinstance(result.get(field), str):
            result[field] = json.loads(result[field])
    for field in ("created_at", "updated_at", "lease_until"):
        if result.get(field) is not None and hasattr(result[field], "isoformat"):
            result[field] = result[field].isoformat()
    result["model"] = _model_marker(result)
    return result


class MemoryResearchStore:
    """Deterministic lease/fencing store used by unit tests and local smoke runs."""

    def __init__(self, clock=None):
        self.datasets: dict[str, dict] = {}
        self.links: dict[str, dict] = {}
        self.jobs: dict[str, dict] = {}
        self.clock = clock or time.monotonic

    async def initialize(self):
        return None

    async def register_dataset(self, identity: dict, bars: list[dict]) -> dict:
        data = freeze_dataset(identity, bars)
        self.datasets[data["dataset_id"]] = copy.deepcopy(data)
        return data

    async def get_dataset(self, dataset_id: str):
        data = self.datasets.get(dataset_id)
        return copy.deepcopy(data) if data is not None else None

    def register_dataset_link(self, dataset_id: str, link: dict) -> None:
        """Attach trusted provenance to an in-memory dataset (test fixture)."""
        self.links[dataset_id] = copy.deepcopy(link)

    async def get_dataset_link(self, dataset_id: str):
        link = self.links.get(dataset_id)
        return copy.deepcopy(link) if link is not None else None

    async def list_datasets(self, **filters: Any) -> list[dict]:
        """Catalog listing: metadata only, never the bar payload."""
        records = []
        for dataset_id, data in self.datasets.items():
            link = self.links.get(dataset_id) or {}
            records.append(
                {
                    "dataset_id": dataset_id,
                    "metadata": dataset_metadata(data),
                    "month": link.get("month"),
                    "history_dataset_id": link.get("history_dataset_id"),
                    "official_eligible": bool(link.get("official_eligible")),
                    "provenance_hash": link.get("provenance_hash"),
                    "published_at": link.get("published_at"),
                    "created_at": None,
                }
            )
        symbol = filters.get("symbol")
        if symbol:
            records = [r for r in records if r["metadata"].get("symbol") == symbol.upper()]
        month = filters.get("month")
        if month:
            records = [r for r in records if r.get("month") == month]
        if filters.get("official_only"):
            records = [r for r in records if r.get("official_eligible")]
        limit = int(filters.get("limit") or 50)
        offset = int(filters.get("offset") or 0)
        return records[offset : offset + limit]

    async def enqueue(self, manifest: dict) -> dict:
        run_id = "bt_" + uuid.uuid4().hex
        record = {
            "run_id": run_id,
            "manifest": copy.deepcopy(manifest),
            "manifest_hash": digest(manifest),
            "status": "QUEUED",
            "attempt": 0,
            "fencing_token": 0,
            "lease_owner": None,
            "lease_until": None,
            "cancel_requested": False,
            "result": None,
            "error": None,
            "created_at": self.clock(),
            "updated_at": self.clock(),
        }
        self.jobs[run_id] = record
        return copy.deepcopy(record)

    async def get(self, run_id: str):
        record = self.jobs.get(run_id)
        if record is None:
            return None
        value = copy.deepcopy(record)
        value["model"] = _model_marker(value)
        return value

    async def list(self, limit: int = 100):
        ordered = sorted(self.jobs.values(), key=lambda job: job["created_at"], reverse=True)
        values = copy.deepcopy(ordered[: min(max(int(limit), 1), 500)])
        for value in values:
            value["model"] = _model_marker(value)
        return values

    async def cancel(self, run_id: str):
        record = self.jobs.get(run_id)
        if record is None:
            return None
        record["cancel_requested"] = True
        if record["status"] == "QUEUED":
            record["status"] = "CANCELLED"
        record["updated_at"] = self.clock()
        return copy.deepcopy(record)

    async def retry(self, run_id: str):
        """Re-queue a FAILED run, keeping its frozen inputs and run id."""
        record = self.jobs.get(run_id)
        if record is None:
            return None
        if record["status"] != "FAILED":
            raise ValueError(f"Run {run_id} is {record['status']}; only FAILED runs can be retried")
        record["status"] = "QUEUED"
        record["error"] = None
        record["lease_owner"] = None
        record["lease_until"] = None
        record["updated_at"] = self.clock()
        return copy.deepcopy(record)

    async def claim(self, owner: str, lease_seconds: int = 30):
        now = self.clock()
        for record in sorted(self.jobs.values(), key=lambda job: job["created_at"]):
            expired = record["lease_until"] is None or record["lease_until"] <= now
            if record["cancel_requested"] and (
                record["status"] == "QUEUED" or (record["status"] == "RUNNING" and expired)
            ):
                record["status"] = "CANCELLED"
                record["lease_owner"] = None
                record["lease_until"] = None
                continue
            if record["cancel_requested"]:
                continue
            # FAILED is a terminal state: a deterministic failure must never be
            # picked up again automatically. Only an explicit retry re-queues it.
            if record["status"] != "QUEUED" and not (record["status"] == "RUNNING" and expired):
                continue
            record["status"] = "RUNNING"
            record["attempt"] += 1
            record["fencing_token"] += 1
            record["lease_owner"] = owner
            record["lease_until"] = now + lease_seconds
            record["updated_at"] = now
            return copy.deepcopy(record)
        return None

    def _assert_lease(self, record, owner, token):
        now = self.clock()
        if (
            record is None
            or record["status"] != "RUNNING"
            or record["lease_owner"] != owner
            or record["fencing_token"] != token
            or record["lease_until"] is None
            or record["lease_until"] <= now
        ):
            raise RuntimeError("Worker lease lost")

    async def heartbeat(self, run_id: str, owner: str, token: int, lease_seconds: int = 30):
        record = self.jobs.get(run_id)
        self._assert_lease(record, owner, token)
        record["lease_until"] = self.clock() + lease_seconds
        record["updated_at"] = self.clock()
        return bool(record["cancel_requested"])

    async def finish(
        self,
        run_id: str,
        owner: str,
        token: int,
        result=None,
        error=None,
        cancelled: bool = False,
    ):
        record = self.jobs.get(run_id)
        self._assert_lease(record, owner, token)
        if record["cancel_requested"] or cancelled:
            record["status"] = "CANCELLED"
            record["result"] = None
        elif error:
            record["status"] = "FAILED"
            record["error"] = str(error)
        else:
            record["status"] = "SUCCEEDED"
            record["result"] = copy.deepcopy(result)
        record["lease_owner"] = None
        record["lease_until"] = None
        record["updated_at"] = self.clock()
        return copy.deepcopy(record)


class ResearchWorker:
    """Claims one durable job, executes it and publishes under a fencing token."""

    def __init__(self, store, owner: str, lease_seconds: int = 30):
        if lease_seconds <= 0:
            raise ValueError("Worker lease must be positive")
        self.store = store
        self.owner = owner
        self.lease_seconds = lease_seconds

    async def _wait_heartbeat(self):
        await asyncio.sleep(self.lease_seconds / 3)

    async def run_once(self, execute):
        record = await self.store.claim(self.owner, self.lease_seconds)
        if record is None:
            return None
        if await self.store.heartbeat(
            record["run_id"], self.owner, record["fencing_token"], self.lease_seconds
        ):
            return await self.store.finish(
                record["run_id"], self.owner, record["fencing_token"], cancelled=True
            )

        async def keep_lease():
            while True:
                await self._wait_heartbeat()
                if await self.store.heartbeat(
                    record["run_id"], self.owner, record["fencing_token"], self.lease_seconds
                ):
                    return True

        async def invoke():
            return await execute(record)

        execution = asyncio.create_task(invoke())
        heartbeat = asyncio.create_task(keep_lease())
        try:
            done, _ = await asyncio.wait(
                (execution, heartbeat), return_when=asyncio.FIRST_COMPLETED
            )
            if heartbeat in done:
                # Lease errors propagate without attempting finish with an old
                # token. Cancellation is published only under a valid lease.
                heartbeat.result()
                execution.cancel()
                await asyncio.gather(execution, return_exceptions=True)
                return await self.store.finish(
                    record["run_id"], self.owner, record["fencing_token"], cancelled=True
                )
            try:
                result = execution.result()
            except Exception as exc:
                return await self.store.finish(
                    record["run_id"], self.owner, record["fencing_token"], error=str(exc)
                )
            return await self.store.finish(
                record["run_id"], self.owner, record["fencing_token"], result=result
            )
        finally:
            # Also stop local work on caller cancellation or storage failure.
            execution.cancel()
            heartbeat.cancel()
            await asyncio.gather(execution, heartbeat, return_exceptions=True)


class PostgresResearchStore:
    def __init__(self, pool):
        self.pool = pool

    async def initialize(self):
        async with self.pool.acquire() as conn:
            await conn.execute(research_schema_sql())

    async def register_dataset(self, identity: dict, bars: list[dict]) -> dict:
        data = freeze_dataset(identity, bars)
        async with self.pool.acquire() as conn:
            await conn.execute(
                "INSERT INTO research.datasets(dataset_id,document,metadata) VALUES($1,$2::jsonb,$3::jsonb) "
                "ON CONFLICT (dataset_id) DO UPDATE SET metadata=EXCLUDED.metadata",
                data["dataset_id"],
                canonical(data),
                canonical(dataset_metadata(data)),
            )
        return data

    async def get_dataset(self, dataset_id: str):
        async with self.pool.acquire() as conn:
            raw = await conn.fetchval(
                "SELECT document FROM research.datasets WHERE dataset_id=$1", dataset_id
            )
        return json.loads(raw) if isinstance(raw, str) else raw

    async def get_dataset_link(self, dataset_id: str):
        """Trusted provenance link of a dataset imported from an official archive."""
        async with self.pool.acquire() as conn:
            row = await conn.fetchrow(
                "SELECT * FROM research.dataset_links WHERE research_dataset_id=$1",
                dataset_id,
            )
        if row is None:
            return None
        record = dict(row)
        for key, value in list(record.items()):
            if hasattr(value, "isoformat"):
                record[key] = value.isoformat()
        return record

    async def list_datasets(
        self,
        *,
        symbol: str | None = None,
        month: str | None = None,
        official_only: bool = False,
        limit: int = 50,
        offset: int = 0,
    ) -> list[dict]:
        """Catalog listing.

        Only catalog metadata and provenance are read; the (potentially very
        large) bar payload is never loaded.
        """
        clauses: list[str] = []
        params: list[Any] = []
        if symbol:
            params.append(symbol.upper())
            clauses.append(f"COALESCE(l.symbol, d.metadata->>'symbol') = ${len(params)}")
        if month:
            params.append(month)
            clauses.append(f"l.month = ${len(params)}")
        if official_only:
            clauses.append("COALESCE(l.official_eligible, FALSE) = TRUE")
        where = f"WHERE {' AND '.join(clauses)}" if clauses else ""
        params.append(min(max(int(limit), 1), 200))
        params.append(max(int(offset), 0))
        query = f"""
            SELECT d.dataset_id, d.metadata, d.created_at,
                   l.history_dataset_id, l.source_url, l.checksum_url, l.official_sha256,
                   l.archive_sha256, l.archive_version, l.feature_version, l.month,
                   l.coverage_start_ms, l.coverage_end_ms, l.bar_count, l.conversion_version,
                   l.quality, l.provenance_hash, l.official_eligible, l.published_at
            FROM research.datasets d
            LEFT JOIN research.dataset_links l ON l.research_dataset_id = d.dataset_id
            {where}
            ORDER BY d.created_at DESC, d.dataset_id
            LIMIT ${len(params) - 1} OFFSET ${len(params)}
        """
        async with self.pool.acquire() as conn:
            rows = await conn.fetch(query, *params)
        records = []
        for row in rows:
            record = dict(row)
            metadata = record.pop("metadata", None)
            if isinstance(metadata, str):
                metadata = json.loads(metadata)
            metadata = metadata or {}
            quality = record.get("quality")
            if isinstance(quality, str):
                quality = json.loads(quality)
            record["metadata"] = metadata
            record["quality"] = quality or {}
            for key, value in list(record.items()):
                if hasattr(value, "isoformat"):
                    record[key] = value.isoformat()
            record["official_eligible"] = bool(record.get("official_eligible"))
            records.append(record)
        return records

    async def enqueue(self, manifest: dict) -> dict:
        async with self.pool.acquire() as conn:
            return _record(
                await conn.fetchrow(
                    "INSERT INTO research.backtest_jobs(run_id,manifest,manifest_hash) VALUES($1,$2::jsonb,$3) RETURNING *",
                    "bt_" + uuid.uuid4().hex,
                    canonical(manifest),
                    digest(manifest),
                )
            )

    async def get(self, run_id):
        async with self.pool.acquire() as conn:
            return _record(
                await conn.fetchrow("SELECT * FROM research.backtest_jobs WHERE run_id=$1", run_id)
            )

    async def list(self, limit=100):
        async with self.pool.acquire() as conn:
            return [
                _record(x)
                for x in await conn.fetch(
                    "SELECT * FROM research.backtest_jobs ORDER BY created_at DESC LIMIT $1",
                    min(max(int(limit), 1), 500),
                )
            ]

    async def cancel(self, run_id):
        async with self.pool.acquire() as conn:
            return _record(
                await conn.fetchrow(
                    "UPDATE research.backtest_jobs SET cancel_requested=true,status=CASE WHEN status='QUEUED' THEN 'CANCELLED' ELSE status END,updated_at=clock_timestamp() WHERE run_id=$1 RETURNING *",
                    run_id,
                )
            )

    async def retry(self, run_id):
        """Re-queue a FAILED run.

        The run id, the frozen manifest and its hash are preserved; only the
        attempt counter keeps growing. The compare-and-set on ``status='FAILED'``
        makes concurrent retries idempotent.
        """
        async with self.pool.acquire() as conn:
            row = await conn.fetchrow(
                "UPDATE research.backtest_jobs SET status='QUEUED',error=NULL,lease_owner=NULL,lease_until=NULL,"
                "updated_at=clock_timestamp() WHERE run_id=$1 AND status='FAILED' RETURNING *",
                run_id,
            )
            if row is None:
                current = await conn.fetchrow(
                    "SELECT status FROM research.backtest_jobs WHERE run_id=$1", run_id
                )
                if current is None:
                    return None
                raise ValueError(
                    f"Run {run_id} is {current['status']}; only FAILED runs can be retried"
                )
            return _record(row)

    async def claim(self, owner, lease_seconds=30):
        async with self.pool.acquire() as conn, conn.transaction():
            await conn.execute(
                "UPDATE research.backtest_jobs SET status='CANCELLED',lease_owner=NULL,lease_until=NULL WHERE cancel_requested AND (status='QUEUED' OR (status='RUNNING' AND lease_until<clock_timestamp()))"
            )
            return _record(
                await conn.fetchrow(
                    """WITH candidate AS (
              SELECT run_id FROM research.backtest_jobs WHERE NOT cancel_requested AND
              (status='QUEUED' OR (status='RUNNING' AND lease_until<clock_timestamp()))
              ORDER BY created_at FOR UPDATE SKIP LOCKED LIMIT 1)
              UPDATE research.backtest_jobs j SET status='RUNNING',attempt=attempt+1,
              fencing_token=fencing_token+1,lease_owner=$1,
              lease_until=clock_timestamp()+$2*interval '1 second',updated_at=clock_timestamp()
              FROM candidate c WHERE j.run_id=c.run_id RETURNING j.*""",
                    owner,
                    lease_seconds,
                )
            )

    async def heartbeat(self, run_id, owner, token, lease_seconds=30):
        async with self.pool.acquire() as conn:
            row = await conn.fetchrow(
                "UPDATE research.backtest_jobs SET lease_until=clock_timestamp()+$4*interval '1 second' WHERE run_id=$1 AND lease_owner=$2 AND fencing_token=$3 AND status='RUNNING' AND lease_until>clock_timestamp() RETURNING cancel_requested",
                run_id,
                owner,
                token,
                lease_seconds,
            )
        if row is None:
            raise RuntimeError("Worker lease lost")
        return bool(row["cancel_requested"])

    async def finish(self, run_id, owner, token, result=None, error=None, cancelled=False):
        status = "CANCELLED" if cancelled else ("FAILED" if error else "SUCCEEDED")
        async with self.pool.acquire() as conn:
            row = await conn.fetchrow(
                """UPDATE research.backtest_jobs SET
              status=CASE WHEN cancel_requested THEN 'CANCELLED' ELSE $4 END,
              result=CASE WHEN cancel_requested THEN NULL ELSE $5::jsonb END,error=$6,
              lease_owner=NULL,lease_until=NULL,updated_at=clock_timestamp()
              WHERE run_id=$1 AND lease_owner=$2 AND fencing_token=$3 AND status='RUNNING'
              AND lease_until>clock_timestamp() RETURNING *""",
                run_id,
                owner,
                token,
                status,
                canonical(result) if result is not None else None,
                error,
            )
        if row is None:
            raise RuntimeError("Stale worker cannot publish results")
        return _record(row)


class BacktestApplication:
    def __init__(self, store, eligibility=None):
        self.store = store
        # A mandatory async callable ``(dataset_id) -> bool``. There is no
        # ``eligibility=None`` mode: an application without a gate must never be
        # able to submit a backtest. The decision never trusts a caller supplied
        # source string.
        if eligibility is None:
            raise ValueError(DATASET_ELIGIBILITY_GATE_UNAVAILABLE)
        self._eligibility = eligibility

    async def _dataset_links(self, dataset_ids: list[str]) -> dict[str, dict]:
        getter = getattr(self.store, "get_dataset_link", None)
        if getter is None:
            return {}
        links = {}
        for dataset_id in dataset_ids:
            link = await getter(dataset_id)
            if link:
                links[dataset_id] = link
        return links

    async def _trusted_provenance(self, dataset_ids: list[str]) -> dict[str, str]:
        """Every dataset must have a trusted published provenance link."""
        links = await self._dataset_links(dataset_ids)
        trusted: dict[str, str] = {}
        for dataset_id in dataset_ids:
            link = links.get(dataset_id)
            if not link_is_trusted(link):
                raise ValueError(f"{DATASET_PROVENANCE_UNTRUSTED}: {dataset_id}")
            trusted[dataset_id] = str(link["provenance_hash"])
        return trusted

    @staticmethod
    def _match_manifest_provenance(provenance, trusted: dict[str, str]) -> dict[str, str]:
        """A caller may repeat the trusted hashes, never invent different ones."""
        if provenance is None:
            return trusted
        if not isinstance(provenance, dict):
            raise ValueError("provenance must be an object")
        if set(provenance) != set(trusted):
            raise ValueError(f"{DATASET_PROVENANCE_MISMATCH}: dataset set")
        for dataset_id, expected in trusted.items():
            if str(provenance.get(dataset_id) or "") != expected:
                raise ValueError(f"{DATASET_PROVENANCE_MISMATCH}: {dataset_id}")
        return trusted

    async def submit(self, manifest: dict):
        frozen = normalize_backtest_manifest(manifest)
        ids = frozen["dataset_ids"]
        datasets = []
        for dataset_id in ids:
            data = await self.store.get_dataset(dataset_id)
            if data is None:
                raise ValueError("Dataset not found: " + dataset_id)
            datasets.append(data)
        # BAR_V1 requires a shared venue, interval, quote asset, instrument set
        # and completed-bar calendar. Refuse before any work is queued.
        from trader.trading.backtest import validate_dataset_set

        validate_dataset_set(datasets)
        if self._eligibility is None:
            raise ValueError(DATASET_ELIGIBILITY_GATE_UNAVAILABLE)
        for dataset_id in ids:
            if not await self._eligibility(dataset_id):
                raise ValueError(
                    f"Dataset has no trusted published provenance ({DATASET_PROVENANCE_UNTRUSTED}): "
                    + dataset_id
                )
        trusted = await self._trusted_provenance(ids)
        provenance = self._match_manifest_provenance(frozen.get("provenance"), trusted)
        frozen["runtime_version"] = SPOT_RUNTIME_VERSION
        frozen["provenance"] = provenance
        return await self.store.enqueue(frozen)

    async def get(self, run_id):
        return await self.store.get(run_id)

    async def list(self, limit=100):
        return await self.store.list(limit)

    async def cancel(self, run_id):
        return await self.store.cancel(run_id)

    async def retry(self, run_id):
        """Re-queue a FAILED run only after re-verifying its frozen inputs."""
        record = await self.store.get(run_id)
        if record is None:
            return None
        if record.get("status") != "FAILED":
            # Preserve the store's canonical conflict error and CAS semantics.
            return await self.store.retry(run_id)
        manifest = record.get("manifest") or {}
        frozen = normalize_backtest_manifest(manifest)
        if frozen.get("runtime_version") != SPOT_RUNTIME_VERSION:
            raise ValueError("Legacy runtime version; submit a new frozen job manifest")
        # The stored document itself must still be exactly the frozen input.
        # A mutated manifest (including nested params/allocations) is refused
        # instead of being retried under the old hash.
        stored_hash = record.get("manifest_hash")
        if stored_hash != digest(manifest) or stored_hash != digest(frozen):
            raise ValueError("Frozen manifest hash mismatch")
        dataset_ids = list(frozen["dataset_ids"])
        if self._eligibility is None:
            raise ValueError(DATASET_ELIGIBILITY_GATE_UNAVAILABLE)
        for dataset_id in dataset_ids:
            if not await self._eligibility(dataset_id):
                raise ValueError(
                    f"Dataset has no trusted published provenance ({DATASET_PROVENANCE_UNTRUSTED}): "
                    + dataset_id
                )
        trusted = await self._trusted_provenance(dataset_ids)
        self._match_manifest_provenance(frozen.get("provenance"), trusted)
        return await self.store.retry(run_id)


class UnavailableBacktestApplication(BacktestApplication):
    """Fail-closed research submission while the history store is unavailable.

    Dataset eligibility needs the trusted, published provenance link that only
    the history store can verify. Without that store there is no trustworthy
    provenance, so submissions and retries are refused with a stable reason
    instead of running with the eligibility gate skipped.
    """

    reason = HISTORY_STORE_UNAVAILABLE

    def __init__(self, store, eligibility=None):
        # This application never checks eligibility because it never accepts
        # work. It must not inherit the mandatory gate construction.
        self.store = store
        self._eligibility = None

    async def submit(self, manifest: dict):
        raise ValueError(self.reason)

    async def retry(self, run_id):
        raise ValueError(self.reason)
