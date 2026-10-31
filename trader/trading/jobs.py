"""Durable research jobs. API enqueue and worker execution are separate."""

from __future__ import annotations

import copy
import hashlib
import json
import re
import time
import uuid
from pathlib import Path

from trader.trading.datasets import canonical, decimal_text, digest, freeze_dataset


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
    if record.get("status") != "SUCCEEDED":
        return "event_runtime"
    result = record.get("result")
    if isinstance(result, dict) and result.get("engine") == "event_runtime":
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

    async def list_datasets(self):
        return [copy.deepcopy(data) for data in self.datasets.values()]

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
            if record["status"] not in {"QUEUED", "FAILED"} and not (
                record["status"] == "RUNNING" and expired
            ):
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
        self.store = store
        self.owner = owner
        self.lease_seconds = lease_seconds

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
        try:
            result = await execute(record)
        except Exception as exc:  # failure is recorded, never published as success
            return await self.store.finish(
                record["run_id"],
                self.owner,
                record["fencing_token"],
                error=str(exc),
            )
        return await self.store.finish(
            record["run_id"],
            self.owner,
            record["fencing_token"],
            result=result,
        )


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
                "INSERT INTO research.datasets(dataset_id,document) VALUES($1,$2::jsonb) ON CONFLICT DO NOTHING",
                data["dataset_id"],
                canonical(data),
            )
        return data

    async def get_dataset(self, dataset_id: str):
        async with self.pool.acquire() as conn:
            raw = await conn.fetchval(
                "SELECT document FROM research.datasets WHERE dataset_id=$1", dataset_id
            )
        return json.loads(raw) if isinstance(raw, str) else raw

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

    async def claim(self, owner, lease_seconds=30):
        async with self.pool.acquire() as conn, conn.transaction():
            await conn.execute(
                "UPDATE research.backtest_jobs SET status='CANCELLED',lease_owner=NULL,lease_until=NULL WHERE cancel_requested AND (status='QUEUED' OR (status='RUNNING' AND lease_until<clock_timestamp()))"
            )
            return _record(
                await conn.fetchrow(
                    """WITH candidate AS (
              SELECT run_id FROM research.backtest_jobs WHERE NOT cancel_requested AND
              (status IN ('QUEUED','FAILED') OR (status='RUNNING' AND lease_until<clock_timestamp()))
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
    def __init__(self, store):
        self.store = store

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
        }:
            raise ValueError("Unknown backtest manifest fields")
        ids = manifest.get("dataset_ids", [])
        if not isinstance(ids, list) or not ids or len(set(ids)) != len(ids):
            raise ValueError("Provide unique dataset_ids")
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
        if engine not in {"event_runtime", "vectorbt"} or purpose not in {
            "research_screening",
            "admission_evidence",
        }:
            raise ValueError("Unsupported engine/purpose")
        if engine == "vectorbt" and purpose != "research_screening":
            raise ValueError("Vectorbt results cannot be admission evidence")
        frozen = {
            "dataset_ids": sorted(ids),
            "strategy": freeze_strategy(manifest.get("strategy", {})),
            "initial_cash": decimal_text(manifest.get("initial_cash", "10000")),
            "fee_bps": decimal_text(manifest.get("fee_bps", "10")),
            "slippage_bps": decimal_text(manifest.get("slippage_bps", "5")),
            "engine": engine,
            "purpose": purpose,
            "runtime_version": "spot_bar_v1",
            "risk_config": manifest.get("risk_config", {}),
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
