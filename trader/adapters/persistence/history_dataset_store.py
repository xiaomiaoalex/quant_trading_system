"""
History Dataset Store
=====================
历史归档导入任务（history_ingest_jobs）与数据集（history_datasets）的 PostgreSQL 存储。

职责：
- 任务的原子领取、状态流转、失败标记
- 数据集的版本化登记与发布（发布状态 + 任务 SUCCEEDED 在同一事务内提交）
- 发布前从 PostgreSQL 校验 FeatureStore 中的真实行数 / 时间范围

约束：
- 只做 IO，不含业务规则（规则在 `trader.services.history_data_ingestion`）
- PostgreSQL 不可用时抛 `HistoryStoreUnavailableError`，不得降级为内存实现
"""

from __future__ import annotations

import json
import logging
from typing import Any, Dict, List, Optional, Tuple

from trader.adapters.persistence.postgres import PostgreSQLStorage

logger = logging.getLogger(__name__)

JOB_STATUS_QUEUED = "QUEUED"
JOB_STATUS_RUNNING = "RUNNING"
JOB_STATUS_SUCCEEDED = "SUCCEEDED"
JOB_STATUS_FAILED = "FAILED"

DATASET_STATUS_PENDING = "PENDING"
DATASET_STATUS_PUBLISHED = "PUBLISHED"
DATASET_STATUS_FAILED = "FAILED"

_ACTIVE_STATUSES = (JOB_STATUS_QUEUED, JOB_STATUS_RUNNING)

HISTORY_WORKER_LOCK_KEY = 0x51545348
"""历史导入 worker 的数据库级独占锁（session advisory lock）。"""

_JOB_UPDATABLE_COLUMNS = frozenset(
    {
        "status",
        "stage",
        "dataset_id",
        "feature_version",
        "rows_processed",
        "bytes_downloaded",
        "error_code",
        "error_reason",
        "attempt",
        "started_at",
        "finished_at",
    }
)


class HistoryStoreUnavailableError(RuntimeError):
    """历史数据存储不可用（PostgreSQL 未连接 / 连接失败）。"""


class HistoryStoreError(RuntimeError):
    """历史数据存储操作失败。"""


class HistoryDatasetStore:
    """历史归档任务 / 数据集的 PostgreSQL 存储。"""

    def __init__(self, storage: Optional[PostgreSQLStorage] = None) -> None:
        self._storage = storage
        self._owns_storage = storage is None
        self._lock_holder: Optional[Tuple[Any, Any]] = None

    # ==================== lifecycle ====================

    async def connect(self) -> None:
        """建立 PostgreSQL 连接（失败抛 HistoryStoreUnavailableError）。"""
        if self._storage is None:
            self._storage = PostgreSQLStorage()
        try:
            await self._storage.connect()
        except Exception as exc:
            raise HistoryStoreUnavailableError(f"Failed to connect to PostgreSQL: {exc}") from exc

    async def disconnect(self) -> None:
        """关闭连接。"""
        if self._storage is not None and self._owns_storage:
            try:
                await self._storage.disconnect()
            except Exception as exc:  # pragma: no cover - defensive
                logger.warning("Failed to disconnect history dataset store: %s", exc)
        self._storage = None

    def _require_storage(self) -> PostgreSQLStorage:
        if self._storage is None or not self._storage.is_connected:
            raise HistoryStoreUnavailableError("History dataset store is not connected")
        return self._storage

    @property
    def storage(self) -> Optional[PostgreSQLStorage]:
        """底层连接（供需要同一事务的桥接存储复用）。"""
        return self._storage

    # ==================== worker ownership ====================

    async def try_acquire_worker_lock(self, key: int = HISTORY_WORKER_LOCK_KEY) -> bool:
        """尝试获取数据库级独占锁。

        锁是 session 级的：持锁连接会在释放前一直占用，因此同一个数据库里
        最多只有一个历史导入 worker 是 owner。获取失败时返回 False，调用方
        绝不能继续执行启动恢复或领取任务。
        """
        storage = self._require_storage()
        if self._lock_holder is not None:
            return True
        holder = storage.acquire()
        conn = await holder.__aenter__()
        try:
            acquired = bool(await conn.fetchval("SELECT pg_try_advisory_lock($1)", key))
        except Exception:
            await holder.__aexit__(None, None, None)
            raise
        if not acquired:
            await holder.__aexit__(None, None, None)
            return False
        self._lock_holder = (holder, conn)
        return True

    async def release_worker_lock(self, key: int = HISTORY_WORKER_LOCK_KEY) -> None:
        """释放数据库级独占锁。"""
        if self._lock_holder is None:
            return
        holder, conn = self._lock_holder
        self._lock_holder = None
        try:
            await conn.execute("SELECT pg_advisory_unlock($1)", key)
        except Exception:  # pragma: no cover - defensive
            logger.warning("Failed to release history worker lock", exc_info=True)
        finally:
            await holder.__aexit__(None, None, None)

    # ==================== jobs ====================

    async def create_job(
        self,
        *,
        job_id: str,
        source: str,
        market: str,
        symbol: str,
        interval: str,
        month: str,
        requested_by: str = "console_user",
        status: str = JOB_STATUS_QUEUED,
        stage: str = "QUEUED",
        dataset_id: Optional[str] = None,
        feature_version: Optional[str] = None,
    ) -> Dict[str, Any]:
        """
        创建任务；若同 (source, symbol, interval, month) 已有活动任务则复用该任务。
        """
        storage = self._require_storage()
        async with storage.acquire() as conn:
            row = await conn.fetchrow(
                """
                INSERT INTO history_ingest_jobs (
                    job_id, source, market, symbol, interval, month,
                    status, stage, dataset_id, feature_version, requested_by
                )
                VALUES ($1, $2, $3, $4, $5, $6, $7, $8, $9, $10, $11)
                ON CONFLICT (source, symbol, interval, month)
                    WHERE status IN ('QUEUED', 'RUNNING')
                DO NOTHING
                RETURNING *
                """,
                job_id,
                source,
                market,
                symbol,
                interval,
                month,
                status,
                stage,
                dataset_id,
                feature_version,
                requested_by,
            )
            if row is not None:
                return dict(row)

            existing = await conn.fetchrow(
                """
                SELECT * FROM history_ingest_jobs
                WHERE source = $1 AND symbol = $2 AND interval = $3 AND month = $4
                  AND status IN ('QUEUED', 'RUNNING')
                ORDER BY created_at ASC
                LIMIT 1
                """,
                source,
                symbol,
                interval,
                month,
            )
            if existing is None:
                raise HistoryStoreError(
                    "Job conflict detected but no active job could be resolved for "
                    f"{source}/{symbol}/{interval}/{month}"
                )
            return dict(existing)

    async def get_job(self, job_id: str) -> Optional[Dict[str, Any]]:
        storage = self._require_storage()
        async with storage.acquire() as conn:
            row = await conn.fetchrow(
                """
                SELECT j.*, d.research_dataset_id
                FROM history_ingest_jobs j
                LEFT JOIN history_datasets d ON d.dataset_id = j.dataset_id
                WHERE j.job_id = $1
                """,
                job_id,
            )
            return dict(row) if row is not None else None

    async def list_jobs(
        self,
        *,
        status: Optional[str] = None,
        symbol: Optional[str] = None,
        month: Optional[str] = None,
        source: Optional[str] = None,
        limit: int = 100,
    ) -> List[Dict[str, Any]]:
        storage = self._require_storage()
        clauses: List[str] = []
        params: List[Any] = []
        if status:
            params.append(status)
            clauses.append(f"j.status = ${len(params)}")
        if symbol:
            params.append(symbol.upper())
            clauses.append(f"j.symbol = ${len(params)}")
        if month:
            params.append(month)
            clauses.append(f"j.month = ${len(params)}")
        if source:
            params.append(source)
            clauses.append(f"j.source = ${len(params)}")
        where = f"WHERE {' AND '.join(clauses)}" if clauses else ""
        params.append(limit)
        query = (
            "SELECT j.*, d.research_dataset_id FROM history_ingest_jobs j "
            "LEFT JOIN history_datasets d ON d.dataset_id = j.dataset_id "
            f"{where} ORDER BY j.created_at DESC LIMIT ${len(params)}"
        )
        async with storage.acquire() as conn:
            rows = await conn.fetch(query, *params)
            return [dict(row) for row in rows]

    async def find_succeeded_job_by_dataset(self, dataset_id: str) -> Optional[Dict[str, Any]]:
        """按 dataset_id 反查关联的成功任务（用于重复提交时复用原任务）。"""
        storage = self._require_storage()
        async with storage.acquire() as conn:
            row = await conn.fetchrow(
                """
                SELECT * FROM history_ingest_jobs
                WHERE dataset_id = $1 AND status = 'SUCCEEDED'
                ORDER BY created_at ASC
                LIMIT 1
                """,
                dataset_id,
            )
            return dict(row) if row is not None else None

    async def find_active_job(
        self,
        *,
        source: str,
        symbol: str,
        interval: str,
        month: str,
    ) -> Optional[Dict[str, Any]]:
        """查找同 (source, symbol, interval, month) 的活动任务（QUEUED / RUNNING）。"""
        storage = self._require_storage()
        async with storage.acquire() as conn:
            row = await conn.fetchrow(
                """
                SELECT * FROM history_ingest_jobs
                WHERE source = $1 AND symbol = $2 AND interval = $3 AND month = $4
                  AND status IN ('QUEUED', 'RUNNING')
                ORDER BY created_at ASC
                LIMIT 1
                """,
                source,
                symbol,
                interval,
                month,
            )
            return dict(row) if row is not None else None

    async def list_queued_jobs(self, limit: int = 10) -> List[Dict[str, Any]]:
        storage = self._require_storage()
        async with storage.acquire() as conn:
            rows = await conn.fetch(
                """
                SELECT * FROM history_ingest_jobs
                WHERE status = 'QUEUED'
                ORDER BY created_at ASC
                LIMIT $1
                """,
                limit,
            )
            return [dict(row) for row in rows]

    async def claim_job(self, job_id: str) -> bool:
        """原子领取任务（QUEUED -> RUNNING）。已被领取返回 False。"""
        storage = self._require_storage()
        async with storage.acquire() as conn:
            row = await conn.fetchrow(
                """
                UPDATE history_ingest_jobs
                SET status = 'RUNNING',
                    stage = 'DOWNLOAD',
                    started_at = COALESCE(started_at, NOW()),
                    updated_at = NOW()
                WHERE job_id = $1 AND status = 'QUEUED'
                RETURNING job_id
                """,
                job_id,
            )
            return row is not None

    async def update_job(self, job_id: str, **fields: Any) -> Optional[Dict[str, Any]]:
        """更新任务字段（白名单列）。"""
        storage = self._require_storage()
        updates = {key: value for key, value in fields.items() if key in _JOB_UPDATABLE_COLUMNS}
        if not updates:
            return await self.get_job(job_id)

        assignments = []
        params: List[Any] = []
        for key, value in updates.items():
            params.append(value)
            assignments.append(f"{key} = ${len(params)}")
        assignments.append("updated_at = NOW()")
        params.append(job_id)
        query = (
            f"UPDATE history_ingest_jobs SET {', '.join(assignments)} "
            f"WHERE job_id = ${len(params)} RETURNING *"
        )
        async with storage.acquire() as conn:
            row = await conn.fetchrow(query, *params)
            return dict(row) if row is not None else None

    async def fail_running_jobs(self, reason: str) -> int:
        """把上次残留的 RUNNING 任务标记为 FAILED（服务中断）。"""
        storage = self._require_storage()
        async with storage.acquire() as conn:
            result = await conn.execute(
                """
                UPDATE history_ingest_jobs
                SET status = 'FAILED',
                    stage = 'FAILED',
                    error_code = 'SERVICE_INTERRUPTED',
                    error_reason = $1,
                    finished_at = NOW(),
                    updated_at = NOW()
                WHERE status = 'RUNNING'
                """,
                reason,
            )
            return _affected_rows(result)

    async def requeue_job(self, job_id: str) -> Optional[Dict[str, Any]]:
        """把 FAILED 任务重新置为 QUEUED（重试）。"""
        storage = self._require_storage()
        async with storage.acquire() as conn:
            row = await conn.fetchrow(
                """
                UPDATE history_ingest_jobs
                SET status = 'QUEUED',
                    stage = 'QUEUED',
                    error_code = NULL,
                    error_reason = NULL,
                    attempt = attempt + 1,
                    started_at = NULL,
                    finished_at = NULL,
                    updated_at = NOW()
                WHERE job_id = $1 AND status = 'FAILED'
                RETURNING *
                """,
                job_id,
            )
            return dict(row) if row is not None else None

    # ==================== datasets ====================

    async def next_version(
        self,
        *,
        source: str,
        market: str,
        symbol: str,
        interval: str,
        month: str,
    ) -> int:
        """下一个可用版本号（同一归档修订会产生新版本，旧版本保留）。"""
        storage = self._require_storage()
        async with storage.acquire() as conn:
            current = await conn.fetchval(
                """
                SELECT MAX(version) FROM history_datasets
                WHERE source = $1 AND market = $2 AND symbol = $3
                  AND interval = $4 AND month = $5
                """,
                source,
                market,
                symbol,
                interval,
                month,
            )
            return int(current or 0) + 1

    async def find_dataset_by_hash(
        self,
        *,
        source: str,
        market: str,
        symbol: str,
        interval: str,
        month: str,
        archive_sha256: str,
    ) -> Optional[Dict[str, Any]]:
        """按归档 hash 查找已有数据集（同 hash 幂等复用）。"""
        storage = self._require_storage()
        async with storage.acquire() as conn:
            row = await conn.fetchrow(
                """
                SELECT * FROM history_datasets
                WHERE source = $1 AND market = $2 AND symbol = $3
                  AND interval = $4 AND month = $5 AND archive_sha256 = $6
                ORDER BY version DESC
                LIMIT 1
                """,
                source,
                market,
                symbol,
                interval,
                month,
                archive_sha256.lower(),
            )
            return dict(row) if row is not None else None

    async def insert_dataset(self, **fields: Any) -> Dict[str, Any]:
        """登记数据集（PENDING 状态，不可用于回测）。"""
        storage = self._require_storage()
        quality = fields.get("quality") or {}
        if not isinstance(quality, str):
            quality = json.dumps(quality)
        async with storage.acquire() as conn:
            row = await conn.fetchrow(
                """
                INSERT INTO history_datasets (
                    dataset_id, source, market, symbol, interval, month, version,
                    feature_version, archive_path, archive_sha256, official_sha256,
                    source_url, checksum_url, row_count, expected_points, gap_count,
                    first_ts_ms, last_ts_ms, quality, status, persistence_ok,
                    backtest_eligible, job_id
                )
                VALUES (
                    $1, $2, $3, $4, $5, $6, $7,
                    $8, $9, $10, $11,
                    $12, $13, $14, $15, $16,
                    $17, $18, $19, $20, $21,
                    $22, $23
                )
                RETURNING *
                """,
                fields["dataset_id"],
                fields["source"],
                fields["market"],
                fields["symbol"],
                fields["interval"],
                fields["month"],
                fields.get("version", 1),
                fields["feature_version"],
                fields["archive_path"],
                fields["archive_sha256"],
                fields.get("official_sha256"),
                fields["source_url"],
                fields["checksum_url"],
                fields.get("row_count", 0),
                fields.get("expected_points", 0),
                fields.get("gap_count", 0),
                fields.get("first_ts_ms"),
                fields.get("last_ts_ms"),
                quality,
                fields.get("status", DATASET_STATUS_PENDING),
                fields.get("persistence_ok", False),
                fields.get("backtest_eligible", False),
                fields.get("job_id"),
            )
            return dict(row)

    async def get_dataset(self, dataset_id: str) -> Optional[Dict[str, Any]]:
        storage = self._require_storage()
        async with storage.acquire() as conn:
            row = await conn.fetchrow(
                "SELECT * FROM history_datasets WHERE dataset_id = $1",
                dataset_id,
            )
            return dict(row) if row is not None else None

    async def list_datasets(
        self,
        *,
        source: Optional[str] = None,
        market: Optional[str] = None,
        symbol: Optional[str] = None,
        interval: Optional[str] = None,
        month: Optional[str] = None,
        only_eligible: bool = False,
        limit: int = 200,
    ) -> List[Dict[str, Any]]:
        storage = self._require_storage()
        clauses: List[str] = []
        params: List[Any] = []
        for column, value in (
            ("source", source),
            ("market", market),
            ("symbol", symbol.upper() if symbol else None),
            ("interval", interval),
            ("month", month),
        ):
            if value:
                params.append(value)
                clauses.append(f"{column} = ${len(params)}")
        if only_eligible:
            clauses.append("status = 'PUBLISHED'")
            clauses.append("persistence_ok = TRUE")
            clauses.append("backtest_eligible = TRUE")
        where = f"WHERE {' AND '.join(clauses)}" if clauses else ""
        params.append(limit)
        query = (
            "SELECT * FROM history_datasets "
            f"{where} ORDER BY created_at DESC LIMIT ${len(params)}"
        )
        async with storage.acquire() as conn:
            rows = await conn.fetch(query, *params)
            return [dict(row) for row in rows]

    async def count_feature_rows(self, feature_version: str) -> Dict[str, Any]:
        """统计 FeatureStore 中某个 feature_version 的真实行数与时间范围。"""
        storage = self._require_storage()
        async with storage.acquire() as conn:
            row = await conn.fetchrow(
                """
                SELECT COUNT(*) AS total_points,
                       MIN(ts_ms) AS first_ts_ms,
                       MAX(ts_ms) AS last_ts_ms
                FROM feature_values
                WHERE feature_name = 'ohlcv' AND version = $1
                """,
                feature_version,
            )
            if row is None:
                return {"total_points": 0, "first_ts_ms": None, "last_ts_ms": None}
            return {
                "total_points": int(row["total_points"] or 0),
                "first_ts_ms": row["first_ts_ms"],
                "last_ts_ms": row["last_ts_ms"],
            }

    async def publish_dataset_with_job(
        self,
        *,
        job_id: str,
        dataset_id: str,
        feature_version: str,
        row_count: int,
        expected_points: int,
        gap_count: int,
        first_ts_ms: Optional[int],
        last_ts_ms: Optional[int],
        quality: Dict[str, Any],
    ) -> Dict[str, Any]:
        """
        在同一事务内发布数据集并把任务置为 SUCCEEDED。

        任一步失败则整体回滚：数据集保持 PENDING，任务不会被标记成功。
        """
        storage = self._require_storage()
        async with storage.acquire() as conn:
            async with conn.transaction():
                dataset = await conn.fetchrow(
                    """
                    UPDATE history_datasets
                    SET status = 'PUBLISHED',
                        persistence_ok = TRUE,
                        backtest_eligible = TRUE,
                        feature_version = $2,
                        row_count = $3,
                        expected_points = $4,
                        gap_count = $5,
                        first_ts_ms = $6,
                        last_ts_ms = $7,
                        quality = $8,
                        published_at = NOW()
                    WHERE dataset_id = $1
                    RETURNING *
                    """,
                    dataset_id,
                    feature_version,
                    row_count,
                    expected_points,
                    gap_count,
                    first_ts_ms,
                    last_ts_ms,
                    json.dumps(quality),
                )
                if dataset is None:
                    raise HistoryStoreError(f"Dataset {dataset_id} not found")

                job = await conn.fetchrow(
                    """
                    UPDATE history_ingest_jobs
                    SET status = 'SUCCEEDED',
                        stage = 'PUBLISHED',
                        dataset_id = $2,
                        feature_version = $3,
                        rows_processed = $4,
                        bytes_downloaded = COALESCE(bytes_downloaded, 0),
                        error_code = NULL,
                        error_reason = NULL,
                        finished_at = NOW(),
                        updated_at = NOW()
                    WHERE job_id = $1
                    RETURNING *
                    """,
                    job_id,
                    dataset_id,
                    feature_version,
                    row_count,
                )
                if job is None:
                    raise HistoryStoreError(f"Job {job_id} not found")
                return {"dataset": dict(dataset), "job": dict(job)}

    async def fail_dataset(
        self,
        dataset_id: str,
        *,
        reason: str,
        job_id: Optional[str] = None,
    ) -> None:
        """把数据集标记为 FAILED（不可用于回测）。"""
        storage = self._require_storage()
        async with storage.acquire() as conn:
            await conn.execute(
                """
                UPDATE history_datasets
                SET status = 'FAILED',
                    persistence_ok = FALSE,
                    backtest_eligible = FALSE,
                    quality = jsonb_set(quality, '{error}', to_jsonb($2::text), TRUE)
                WHERE dataset_id = $1
                """,
                dataset_id,
                reason,
            )
            if job_id:
                await conn.execute(
                    """
                    UPDATE history_ingest_jobs
                    SET status = 'FAILED',
                        stage = 'FAILED',
                        error_code = 'PERSISTENCE_ERROR',
                        error_reason = $2,
                        finished_at = NOW(),
                        updated_at = NOW()
                    WHERE job_id = $1
                    """,
                    job_id,
                    reason,
                )


def _affected_rows(result: str) -> int:
    """解析 asyncpg 的 command tag，返回受影响行数。"""
    try:
        return int(str(result).rsplit(" ", 1)[-1])
    except (ValueError, IndexError):  # pragma: no cover - defensive
        return 0
