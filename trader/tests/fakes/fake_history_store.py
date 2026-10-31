"""
Fake History Store / Archive Client / Feature Store
===================================================
历史数据导入链路（任务 / 数据集 / 归档下载 / 严格 FeatureStore）的内存替身。

用于单元测试：测试不访问网络、不依赖 PostgreSQL。
"""

from __future__ import annotations

import copy
import hashlib
import io
import os
import tempfile
import zipfile
from typing import Any, Dict, List, Optional, Sequence, Tuple

from trader.adapters.market_data.binance_vision import (
    ArchiveChecksumMismatch,
    ArchiveDownloadError,
    ArchiveNotFoundError,
    DownloadedArchive,
)
from trader.adapters.persistence.feature_store import FeatureStoreStrictError
from trader.adapters.persistence.research_history_bridge_store import ResearchHistoryStoreError
from trader.core.domain.services.ohlcv_archive import KlineBar

JobKey = Tuple[str, str, str, str]


class FakeHistoryDatasetStore:
    """内存版历史任务 / 数据集存储（接口与 PostgreSQL 版一致）。"""

    def __init__(self, feature_store: Optional["FakeFeatureStore"] = None) -> None:
        self.jobs: Dict[str, Dict[str, Any]] = {}
        self.datasets: Dict[str, Dict[str, Any]] = {}
        self._feature_rows: Dict[str, List[int]] = {}
        self._feature_store = feature_store
        self._counter = 0
        self.fail_connect = False
        self.fail_writes = False

    # ---------- helpers ----------

    def _now(self) -> str:
        return "2026-10-29T00:00:00Z"

    async def connect(self) -> None:
        if self.fail_connect:
            raise RuntimeError("postgres unavailable")

    async def disconnect(self) -> None:
        return None

    # ---------- jobs ----------

    async def create_job(self, **fields: Any) -> Dict[str, Any]:
        self._counter += 1
        active_key = (
            fields["source"],
            fields["symbol"],
            fields["interval"],
            fields["month"],
        )
        for job in self.jobs.values():
            if (
                job["status"] in ("QUEUED", "RUNNING")
                and (
                    job["source"],
                    job["symbol"],
                    job["interval"],
                    job["month"],
                )
                == active_key
            ):
                return dict(job)

        job_id = fields.get("job_id") or f"hjob_{self._counter}"
        job = {
            "job_id": job_id,
            "source": fields["source"],
            "market": fields["market"],
            "symbol": fields["symbol"],
            "interval": fields["interval"],
            "month": fields["month"],
            "status": fields.get("status", "QUEUED"),
            "stage": fields.get("stage", "QUEUED"),
            "dataset_id": fields.get("dataset_id"),
            "feature_version": fields.get("feature_version"),
            "rows_processed": 0,
            "bytes_downloaded": 0,
            "error_code": None,
            "error_reason": None,
            "requested_by": fields.get("requested_by", "console_user"),
            "attempt": 1,
            "created_at": self._now(),
            "started_at": None,
            "finished_at": None,
            "updated_at": self._now(),
        }
        self.jobs[job_id] = job
        return dict(job)

    async def get_job(self, job_id: str) -> Optional[Dict[str, Any]]:
        job = self.jobs.get(job_id)
        return dict(job) if job else None

    async def list_jobs(self, **filters: Any) -> List[Dict[str, Any]]:
        jobs = list(self.jobs.values())
        status = filters.get("status")
        symbol = filters.get("symbol")
        month = filters.get("month")
        if status:
            jobs = [job for job in jobs if job["status"] == status]
        if symbol:
            jobs = [job for job in jobs if job["symbol"] == symbol.upper()]
        if month:
            jobs = [job for job in jobs if job["month"] == month]
        jobs.sort(key=lambda item: item["created_at"], reverse=True)
        return [dict(job) for job in jobs[: filters.get("limit", 100)]]

    async def list_queued_jobs(self, limit: int = 10) -> List[Dict[str, Any]]:
        queued = [job for job in self.jobs.values() if job["status"] == "QUEUED"]
        queued.sort(key=lambda item: item["created_at"])
        return [dict(job) for job in queued[:limit]]

    async def find_succeeded_job_by_dataset(self, dataset_id: str) -> Optional[Dict[str, Any]]:
        for job in self.jobs.values():
            if job.get("dataset_id") == dataset_id and job["status"] == "SUCCEEDED":
                return dict(job)
        return None

    async def find_active_job(self, **fields: Any) -> Optional[Dict[str, Any]]:
        for job in self.jobs.values():
            if job["status"] not in ("QUEUED", "RUNNING"):
                continue
            if (
                job["source"],
                job["symbol"],
                job["interval"],
                job["month"],
            ) == (
                fields["source"],
                fields["symbol"],
                fields["interval"],
                fields["month"],
            ):
                return dict(job)
        return None

    async def claim_job(self, job_id: str) -> bool:
        job = self.jobs.get(job_id)
        if job is None or job["status"] != "QUEUED":
            return False
        job["status"] = "RUNNING"
        job["stage"] = "DOWNLOAD"
        job["started_at"] = self._now()
        return True

    async def update_job(self, job_id: str, **fields: Any) -> Optional[Dict[str, Any]]:
        job = self.jobs.get(job_id)
        if job is None:
            return None
        job.update(fields)
        job["updated_at"] = self._now()
        return dict(job)

    async def fail_running_jobs(self, reason: str) -> int:
        count = 0
        for job in self.jobs.values():
            if job["status"] == "RUNNING":
                job["status"] = "FAILED"
                job["stage"] = "FAILED"
                job["error_code"] = "SERVICE_INTERRUPTED"
                job["error_reason"] = reason
                job["finished_at"] = self._now()
                count += 1
        return count

    async def requeue_job(self, job_id: str) -> Optional[Dict[str, Any]]:
        job = self.jobs.get(job_id)
        if job is None or job["status"] != "FAILED":
            return None
        job["status"] = "QUEUED"
        job["stage"] = "QUEUED"
        job["error_code"] = None
        job["error_reason"] = None
        job["attempt"] += 1
        job["finished_at"] = None
        return dict(job)

    # ---------- datasets ----------

    async def next_version(self, **fields: Any) -> int:
        versions = [
            int(dataset["version"])
            for dataset in self.datasets.values()
            if (
                dataset["source"],
                dataset["market"],
                dataset["symbol"],
                dataset["interval"],
                dataset["month"],
            )
            == (
                fields["source"],
                fields["market"],
                fields["symbol"],
                fields["interval"],
                fields["month"],
            )
        ]
        return max(versions, default=0) + 1

    async def find_dataset_by_hash(self, **fields: Any) -> Optional[Dict[str, Any]]:
        for dataset in self.datasets.values():
            if (
                dataset["source"],
                dataset["market"],
                dataset["symbol"],
                dataset["interval"],
                dataset["month"],
                dataset["archive_sha256"],
            ) == (
                fields["source"],
                fields["market"],
                fields["symbol"],
                fields["interval"],
                fields["month"],
                fields["archive_sha256"],
            ):
                return dict(dataset)
        return None

    async def insert_dataset(self, **fields: Any) -> Dict[str, Any]:
        dataset = {
            "dataset_id": fields["dataset_id"],
            "source": fields["source"],
            "market": fields["market"],
            "symbol": fields["symbol"],
            "interval": fields["interval"],
            "month": fields["month"],
            "version": fields.get("version", 1),
            "feature_version": fields["feature_version"],
            "archive_path": fields["archive_path"],
            "archive_sha256": fields["archive_sha256"],
            "official_sha256": fields.get("official_sha256"),
            "source_url": fields["source_url"],
            "checksum_url": fields["checksum_url"],
            "row_count": fields.get("row_count", 0),
            "expected_points": fields.get("expected_points", 0),
            "gap_count": fields.get("gap_count", 0),
            "first_ts_ms": fields.get("first_ts_ms"),
            "last_ts_ms": fields.get("last_ts_ms"),
            "quality": fields.get("quality", {}),
            "status": fields.get("status", "PENDING"),
            "persistence_ok": fields.get("persistence_ok", False),
            "backtest_eligible": fields.get("backtest_eligible", False),
            "job_id": fields.get("job_id"),
            "research_dataset_id": fields.get("research_dataset_id"),
            "created_at": self._now(),
            "published_at": None,
        }
        self.datasets[dataset["dataset_id"]] = dataset
        return dict(dataset)

    async def get_dataset(self, dataset_id: str) -> Optional[Dict[str, Any]]:
        dataset = self.datasets.get(dataset_id)
        return dict(dataset) if dataset else None

    async def list_datasets(self, **filters: Any) -> List[Dict[str, Any]]:
        datasets = list(self.datasets.values())
        for column in ("source", "market", "symbol", "interval", "month"):
            value = filters.get(column)
            if value:
                datasets = [item for item in datasets if item[column] == value]
        if filters.get("only_eligible"):
            datasets = [
                item
                for item in datasets
                if item["status"] == "PUBLISHED"
                and item["persistence_ok"]
                and item["backtest_eligible"]
            ]
        return [dict(item) for item in datasets[: filters.get("limit", 200)]]

    async def count_feature_rows(self, feature_version: str) -> Dict[str, Any]:
        if self._feature_store is not None:
            ts_values: List[int] = []
            for key, values in self._feature_store.rows.items():
                if key.endswith(f":{feature_version}"):
                    ts_values.extend(values.keys())
            if not ts_values:
                return {"total_points": 0, "first_ts_ms": None, "last_ts_ms": None}
            return {
                "total_points": len(ts_values),
                "first_ts_ms": min(ts_values),
                "last_ts_ms": max(ts_values),
            }
        rows = self._feature_rows.get(feature_version, [])
        if not rows:
            return {"total_points": 0, "first_ts_ms": None, "last_ts_ms": None}
        return {
            "total_points": len(rows),
            "first_ts_ms": min(rows),
            "last_ts_ms": max(rows),
        }

    # feature rows are injected by the fake feature store
    def register_feature_rows(self, feature_version: str, ts_values: List[int]) -> None:
        self._feature_rows[feature_version] = list(ts_values)

    async def publish_dataset_with_job(self, **fields: Any) -> Dict[str, Any]:
        dataset = self.datasets.get(fields["dataset_id"])
        if dataset is None:
            raise RuntimeError(f"Dataset {fields['dataset_id']} not found")
        dataset.update(
            {
                "status": "PUBLISHED",
                "persistence_ok": True,
                "backtest_eligible": True,
                "feature_version": fields["feature_version"],
                "row_count": fields["row_count"],
                "expected_points": fields["expected_points"],
                "gap_count": fields["gap_count"],
                "first_ts_ms": fields["first_ts_ms"],
                "last_ts_ms": fields["last_ts_ms"],
                "quality": fields["quality"],
                "research_dataset_id": fields.get("research_dataset_id"),
                "published_at": self._now(),
            }
        )
        job = self.jobs.get(fields["job_id"])
        if job is None:
            raise RuntimeError(f"Job {fields['job_id']} not found")
        job.update(
            {
                "status": "SUCCEEDED",
                "stage": "PUBLISHED",
                "dataset_id": fields["dataset_id"],
                "feature_version": fields["feature_version"],
                "rows_processed": fields["row_count"],
                "finished_at": self._now(),
            }
        )
        return {"dataset": dict(dataset), "job": dict(job)}

    async def fail_dataset(self, dataset_id: str, **fields: Any) -> None:
        dataset = self.datasets.get(dataset_id)
        if dataset is not None:
            dataset["status"] = "FAILED"
            dataset["persistence_ok"] = False
            dataset["backtest_eligible"] = False
            dataset["quality"] = {**dataset["quality"], "error": fields.get("reason")}


class FakeResearchHistoryBridgeStore:
    """桥接存储替身。

    模拟「Research 数据集 + provenance 关联 + 历史数据集/任务发布」在同一个
    原子步骤内完成，并保留足够的记录用于断言。
    """

    def __init__(self, history_store: "FakeHistoryDatasetStore") -> None:
        self.history_store = history_store
        self.datasets: Dict[str, Dict[str, Any]] = {}
        self.links: Dict[str, Dict[str, Any]] = {}
        self.publish_calls: List[Dict[str, Any]] = []
        self.fail_publish = False

    async def publish_dataset(self, **fields: Any) -> Dict[str, Any]:
        self.publish_calls.append(dict(fields))
        if self.fail_publish:
            raise ResearchHistoryStoreError("simulated atomic publish failure")
        document = fields.get("research_document")
        if document is not None:
            research_dataset_id = str(document["dataset_id"])
            self.datasets[research_dataset_id] = copy.deepcopy(document)
        else:
            research_dataset_id = str(fields.get("research_dataset_id"))
            if research_dataset_id not in self.datasets:
                raise ResearchHistoryStoreError(f"Research dataset {research_dataset_id} not found")
        link = fields["link"]
        self.links[research_dataset_id] = {
            "research_dataset_id": research_dataset_id,
            "provenance_hash": link["provenance_hash"],
            "official_eligible": True,
            **link["payload"],
        }
        return await self.history_store.publish_dataset_with_job(
            job_id=fields["job_id"],
            dataset_id=fields["history_dataset_id"],
            feature_version=fields["feature_version"],
            row_count=fields["row_count"],
            expected_points=fields["expected_points"],
            gap_count=fields["gap_count"],
            first_ts_ms=fields["first_ts_ms"],
            last_ts_ms=fields["last_ts_ms"],
            quality=fields["quality"],
            research_dataset_id=research_dataset_id,
        )

    async def find_link_by_research_dataset(self, research_dataset_id: str):
        link = self.links.get(research_dataset_id)
        return dict(link) if link else None

    async def find_link_by_history_dataset(self, history_dataset_id: str):
        for link in self.links.values():
            if link.get("history_dataset_id") == history_dataset_id:
                return dict(link)
        return None


class FakeFeatureStore:
    """严格模式 FeatureStore 替身（记录写入的行）。"""

    def __init__(self, strict: bool = True, fail: bool = False) -> None:
        self.strict = strict
        self.fail = fail
        self.rows: Dict[str, Dict[int, Any]] = {}
        self.write_calls = 0

    async def write_feature(
        self,
        symbol: str,
        feature_name: str,
        version: str,
        ts_ms: int,
        value: Any,
        meta: Optional[Dict[str, Any]] = None,
    ) -> Tuple[bool, bool]:
        self.write_calls += 1
        if self.fail:
            raise FeatureStoreStrictError("postgres write failed")
        key = f"{symbol}:{feature_name}:{version}"
        self.rows.setdefault(key, {})[ts_ms] = value
        return True, False

    async def write_features_bulk(
        self,
        symbol: str,
        feature_name: str,
        version: str,
        rows: Sequence[Tuple[int, Any]],
        meta: Optional[Dict[str, Any]] = None,
    ) -> Tuple[int, int]:
        self.write_calls += len(rows)
        if self.fail:
            raise FeatureStoreStrictError("postgres write failed")
        key = f"{symbol}:{feature_name}:{version}"
        bucket = self.rows.setdefault(key, {})
        created = 0
        duplicates = 0
        for ts_ms, value in rows:
            if ts_ms in bucket:
                duplicates += 1
                continue
            bucket[ts_ms] = value
            created += 1
        return created, duplicates


class FakeArchiveClient:
    """归档下载 / 提交 / 解析替身。"""

    def __init__(
        self,
        zip_bytes: Optional[bytes] = None,
        bars: Optional[List[KlineBar]] = None,
        download_error: Optional[Exception] = None,
        root: Optional[str] = None,
    ) -> None:
        self.zip_bytes = zip_bytes or b""
        self.bars = bars or []
        self._download_error = download_error
        self._root = root or tempfile.mkdtemp(prefix="fake-archive-")
        self.committed: List[str] = []
        self.download_calls = 0
        """下载调用次数（用于断言提交路径不做任何远程调用）。"""

    def set_payload(self, zip_bytes: bytes) -> None:
        """替换归档内容（模拟官方修订归档 → hash 变化）。"""
        self.zip_bytes = zip_bytes

    async def download(self, ref: Any) -> DownloadedArchive:
        self.download_calls += 1
        if self._download_error is not None:
            raise self._download_error
        digest = hashlib.sha256(self.zip_bytes).hexdigest()
        os.makedirs(self._root, exist_ok=True)
        temp_path = os.path.join(self._root, f"{ref.file_stem}.part")
        with open(temp_path, "wb") as handle:
            handle.write(self.zip_bytes)
        return DownloadedArchive(
            ref=ref,
            temp_path=temp_path,
            size_bytes=len(self.zip_bytes),
            sha256=digest,
            official_sha256=digest,
            official_checksum_text=f"{digest}  {ref.file_stem}.zip\n",
            official_file_name=f"{ref.file_stem}.zip",
        )

    def commit(self, downloaded: DownloadedArchive, version: int) -> str:
        target = os.path.join(
            self._root,
            f"{downloaded.ref.symbol}-{downloaded.ref.interval}-{downloaded.ref.month}-v{version}.zip",
        )
        os.replace(downloaded.temp_path, target)
        self.committed.append(target)
        return target

    def discard(self, downloaded: DownloadedArchive) -> None:
        """丢弃未提交的临时文件（复用已有数据集时调用）。"""
        if os.path.exists(downloaded.temp_path):
            os.remove(downloaded.temp_path)

    def read_klines(self, archive_path: str, ref: Any) -> List[KlineBar]:
        return list(self.bars)


def make_zip_bytes(member_name: str, payload: str) -> bytes:
    """构造测试用 ZIP。"""
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w", zipfile.ZIP_DEFLATED) as archive:
        archive.writestr(member_name, payload)
    return buffer.getvalue()


__all__ = [
    "ArchiveChecksumMismatch",
    "ArchiveDownloadError",
    "ArchiveNotFoundError",
    "FakeArchiveClient",
    "FakeFeatureStore",
    "FakeHistoryDatasetStore",
    "FakeResearchHistoryBridgeStore",
    "make_zip_bytes",
]
