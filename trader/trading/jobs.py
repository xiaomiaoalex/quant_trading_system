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

from trader.trading.datasets import (
    canonical,
    dataset_metadata,
    decimal_text,
    digest,
    freeze_dataset,
)
from trader.trading.version import SPOT_RUNTIME_VERSION


def freeze_strategy(spec: dict) -> dict:
    if set(spec) - {"module_path", "params", "version", "code_hash"}:
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
    return {
        "module_path": module,
        "params": copy.deepcopy(params),
        "version": str(spec.get("version", "1")),
        "code_hash": code_hash,
    }


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

    async def get_dataset_link(self, dataset_id: str):
        """In-memory stores never carry a trusted archive provenance link."""
        return None

    async def list_datasets(self, **filters: Any) -> list[dict]:
        """Catalog listing: metadata only, never the bar payload."""
        records = []
        for dataset_id, data in self.datasets.items():
            record = {
                "dataset_id": dataset_id,
                **dataset_metadata(data),
                "history_dataset_id": None,
                "official_eligible": False,
                "provenance_hash": None,
                "published_at": None,
                "created_at": None,
            }
            records.append(record)
        symbol = filters.get("symbol")
        if symbol:
            records = [r for r in records if r.get("symbol") == symbol.upper()]
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
            await conn.execute(Path(__file__).with_name("research_schema.sql").read_text())

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
        # Optional async callable ``(dataset_id) -> bool``. When present, a
        # dataset that is not backed by a trusted, published provenance link is
        # refused. The eligibility decision never trusts a caller supplied
        # source string.
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

    async def submit(self, manifest: dict):
        if set(manifest) - {
            "dataset_ids",
            "strategy",
            "initial_cash",
            "fee_bps",
            "slippage_bps",
            "engine",
            "purpose",
            "risk_config",
            "provenance",
        }:
            raise ValueError("Unknown backtest manifest fields")
        ids = manifest.get("dataset_ids", [])
        if not isinstance(ids, list) or not ids or len(set(ids)) != len(ids):
            raise ValueError("Provide unique dataset_ids")
        for dataset_id in ids:
            if not isinstance(dataset_id, str) or not dataset_id.startswith("ds_"):
                raise ValueError("dataset_ids must reference Research datasets (ds_*)")
        datasets = []
        for dataset_id in ids:
            data = await self.store.get_dataset(dataset_id)
            if data is None:
                raise ValueError("Dataset not found: " + dataset_id)
            datasets.append(data)
        # BAR_V1 requires a common completed-bar calendar and quote asset.
        first = datasets[0]
        calendar = [(x["open_time_ms"], x["close_time_ms"]) for x in first["bars"]]
        if len({x["identity"]["symbol"] for x in datasets}) != len(datasets):
            raise ValueError("Duplicate instrument in run")
        for data in datasets:
            if (
                data["identity"]["quote_asset"] != first["identity"]["quote_asset"]
                or [(x["open_time_ms"], x["close_time_ms"]) for x in data["bars"]] != calendar
            ):
                raise ValueError("Datasets must share quote asset and bar calendar")
        engine = manifest.get("engine", "event_runtime")
        purpose = manifest.get("purpose", "research_screening")
        if engine != "event_runtime":
            raise ValueError("Only the event runtime may execute frozen backtests")
        if purpose not in {"research_screening", "admission_evidence"}:
            raise ValueError("Unsupported purpose")
        if self._eligibility is not None:
            for dataset_id in ids:
                if not await self._eligibility(dataset_id):
                    raise ValueError("Dataset has no trusted published provenance: " + dataset_id)
        links = await self._dataset_links(ids)
        provenance = manifest.get("provenance")
        if provenance is None:
            provenance = {
                dataset_id: link.get("provenance_hash")
                for dataset_id, link in links.items()
                if link.get("provenance_hash")
            }
        if not isinstance(provenance, dict):
            raise ValueError("provenance must be an object")
        frozen = {
            "dataset_ids": sorted(ids),
            "strategy": freeze_strategy(manifest.get("strategy", {})),
            "initial_cash": decimal_text(manifest.get("initial_cash", "10000")),
            "fee_bps": decimal_text(manifest.get("fee_bps", "10")),
            "slippage_bps": decimal_text(manifest.get("slippage_bps", "5")),
            "engine": engine,
            "purpose": purpose,
            "runtime_version": SPOT_RUNTIME_VERSION,
            "risk_config": manifest.get("risk_config", {}),
            "provenance": provenance,
        }
        from decimal import Decimal

        if (
            Decimal(frozen["initial_cash"]) <= 0
            or not 0 <= Decimal(frozen["fee_bps"]) <= 100
            or not 0 <= Decimal(frozen["slippage_bps"]) <= 1000
        ):
            raise ValueError("Invalid capital or execution costs")
        if purpose == "admission_evidence" and (
            Decimal(frozen["fee_bps"]) <= 0 or Decimal(frozen["slippage_bps"]) <= 0
        ):
            raise ValueError("Admission evidence requires fees and slippage")
        return await self.store.enqueue(frozen)

    async def get(self, run_id):
        return await self.store.get(run_id)

    async def list(self, limit=100):
        return await self.store.list(limit)

    async def cancel(self, run_id):
        return await self.store.cancel(run_id)

    async def retry(self, run_id):
        """Re-queue a FAILED run with its frozen inputs untouched."""
        return await self.store.retry(run_id)
