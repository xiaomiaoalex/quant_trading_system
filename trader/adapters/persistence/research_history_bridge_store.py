"""
Research History Bridge Store
=============================
原子地把「已校验的历史归档」发布到 Research 平面。

职责：
- 在**同一个 PostgreSQL 事务**内写入 Research 数据集、provenance 关联、
  历史数据集发布状态与历史任务成功状态
- 任一步失败则整体回滚：不会出现「Research 有数据但历史任务未完成」这类
  半成品状态被下游选中的情况

约束：
- 只做 IO，不含业务规则（规则在 `trader.trading.research_bridge`）
- 关联表只保存 provenance（URL / 摘要 / 覆盖 / 转换版本），**不重复存行情**
- PostgreSQL 不可用时抛 `ResearchHistoryStoreUnavailableError`，不得降级为内存实现
"""

from __future__ import annotations

import json
import logging
from typing import Any, Dict, List, Optional

from trader.adapters.persistence.postgres import PostgreSQLStorage

logger = logging.getLogger(__name__)

_LINK_COLUMNS = (
    "research_dataset_id",
    "history_dataset_id",
    "source_url",
    "checksum_url",
    "official_sha256",
    "archive_sha256",
    "archive_version",
    "feature_version",
    "symbol",
    "interval",
    "month",
    "coverage_start_ms",
    "coverage_end_ms",
    "bar_count",
    "conversion_version",
    "quality",
    "provenance_hash",
)


class ResearchHistoryStoreUnavailableError(RuntimeError):
    """桥接存储不可用（PostgreSQL 未连接 / 连接失败）。"""


class ResearchHistoryStoreError(RuntimeError):
    """桥接存储操作失败。"""


class ResearchHistoryBridgeStore:
    """历史归档 -> Research 数据集的原子发布入口。"""

    def __init__(self, storage: Optional[PostgreSQLStorage] = None) -> None:
        self._storage = storage
        self._owns_storage = storage is None

    # ==================== lifecycle ====================

    async def connect(self) -> None:
        """建立 PostgreSQL 连接（失败抛 ResearchHistoryStoreUnavailableError）。"""
        if self._storage is None:
            self._storage = PostgreSQLStorage()
        try:
            await self._storage.connect()
        except Exception as exc:
            raise ResearchHistoryStoreUnavailableError(
                f"Failed to connect to PostgreSQL: {exc}"
            ) from exc

    async def disconnect(self) -> None:
        """关闭连接。"""
        if self._storage is not None and self._owns_storage:
            try:
                await self._storage.disconnect()
            except Exception as exc:  # pragma: no cover - defensive
                logger.warning("Failed to disconnect research bridge store: %s", exc)
        self._storage = None

    def _require_storage(self) -> PostgreSQLStorage:
        if self._storage is None or not self._storage.is_connected:
            raise ResearchHistoryStoreUnavailableError("Research bridge store is not connected")
        return self._storage

    async def ensure_research_schema(self) -> None:
        """确保 Research 平面 schema 存在。

        历史导入进程会直接写入 ``research.datasets`` 与 ``research.dataset_links``，
        因此它必须能独立把 Research schema 建好。
        """
        from trader.trading.research_schema import research_schema_sql

        storage = self._require_storage()
        async with storage.acquire() as conn:
            await conn.execute(research_schema_sql())

    # ==================== publish ====================

    async def publish_dataset(
        self,
        *,
        job_id: Optional[str],
        history_dataset_id: str,
        feature_version: str,
        row_count: int,
        expected_points: int,
        gap_count: int,
        first_ts_ms: Optional[int],
        last_ts_ms: Optional[int],
        quality: Dict[str, Any],
        link: Dict[str, Any],
        research_document: Optional[Dict[str, Any]] = None,
        research_metadata: Optional[Dict[str, Any]] = None,
        research_dataset_id: Optional[str] = None,
    ) -> Dict[str, Any]:
        """
        在同一个事务内完成 Research 数据集、provenance 关联与历史发布。

        ``research_document`` 为 None 时表示 Research 数据集已经存在（例如复用
        同一份归档），此时只校验其存在并补齐关联，不会重新写入行情。

        幂等：同一份冻结输入重复调用得到相同结果，不会产生第二个数据集。
        """
        storage = self._require_storage()
        payload = link["payload"]
        link_quality = payload.get("quality") or {}
        link_json = json.dumps(link_quality)
        if research_document is not None:
            resolved_research_dataset_id = str(research_document["dataset_id"])
        else:
            resolved_research_dataset_id = str(research_dataset_id or "")
            if not resolved_research_dataset_id:
                raise ResearchHistoryStoreError("research dataset id is required")

        async with storage.acquire() as conn:
            async with conn.transaction():
                if research_document is not None:
                    await conn.execute(
                        """
                        INSERT INTO research.datasets (dataset_id, document, metadata)
                        VALUES ($1, $2::jsonb, $3::jsonb)
                        ON CONFLICT (dataset_id) DO UPDATE SET metadata = EXCLUDED.metadata
                        """,
                        resolved_research_dataset_id,
                        json.dumps(research_document),
                        json.dumps(research_metadata or {}),
                    )
                else:
                    exists = await conn.fetchval(
                        "SELECT 1 FROM research.datasets WHERE dataset_id = $1",
                        resolved_research_dataset_id,
                    )
                    if exists is None:
                        raise ResearchHistoryStoreError(
                            f"Research dataset {resolved_research_dataset_id} not found"
                        )
                await conn.execute(
                    """
                    INSERT INTO research.dataset_links (
                        research_dataset_id, history_dataset_id, source_url, checksum_url,
                        official_sha256, archive_sha256, archive_version, feature_version,
                        symbol, interval, month, coverage_start_ms, coverage_end_ms,
                        bar_count, conversion_version, quality, provenance_hash,
                        official_eligible
                    )
                    VALUES (
                        $1, $2, $3, $4,
                        $5, $6, $7, $8,
                        $9, $10, $11, $12, $13,
                        $14, $15, $16::jsonb, $17,
                        TRUE
                    )
                    ON CONFLICT (research_dataset_id) DO UPDATE SET
                        history_dataset_id = EXCLUDED.history_dataset_id,
                        source_url = EXCLUDED.source_url,
                        checksum_url = EXCLUDED.checksum_url,
                        official_sha256 = EXCLUDED.official_sha256,
                        archive_sha256 = EXCLUDED.archive_sha256,
                        archive_version = EXCLUDED.archive_version,
                        feature_version = EXCLUDED.feature_version,
                        symbol = EXCLUDED.symbol,
                        interval = EXCLUDED.interval,
                        month = EXCLUDED.month,
                        coverage_start_ms = EXCLUDED.coverage_start_ms,
                        coverage_end_ms = EXCLUDED.coverage_end_ms,
                        bar_count = EXCLUDED.bar_count,
                        conversion_version = EXCLUDED.conversion_version,
                        quality = EXCLUDED.quality,
                        provenance_hash = EXCLUDED.provenance_hash,
                        official_eligible = TRUE
                    """,
                    resolved_research_dataset_id,
                    payload.get("history_dataset_id"),
                    payload.get("source_url"),
                    payload.get("checksum_url"),
                    payload.get("official_sha256"),
                    payload.get("archive_sha256"),
                    payload.get("archive_version"),
                    payload.get("feature_version"),
                    payload.get("symbol"),
                    payload.get("interval"),
                    payload.get("month"),
                    payload.get("coverage_start_ms"),
                    payload.get("coverage_end_ms"),
                    int(payload.get("bar_count") or 0),
                    payload.get("conversion_version"),
                    link_json,
                    link["provenance_hash"],
                )
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
                        research_dataset_id = $9,
                        published_at = NOW()
                    WHERE dataset_id = $1
                    RETURNING *
                    """,
                    history_dataset_id,
                    feature_version,
                    row_count,
                    expected_points,
                    gap_count,
                    first_ts_ms,
                    last_ts_ms,
                    json.dumps(quality),
                    resolved_research_dataset_id,
                )
                if dataset is None:
                    raise ResearchHistoryStoreError(
                        f"History dataset {history_dataset_id} not found"
                    )
                job = None
                if job_id:
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
                        history_dataset_id,
                        feature_version,
                        row_count,
                    )
                    if job is None:
                        raise ResearchHistoryStoreError(f"History job {job_id} not found")
                return {
                    "dataset": dict(dataset),
                    "job": dict(job) if job is not None else None,
                }

    # ==================== queries ====================

    async def find_link_by_history_dataset(
        self, history_dataset_id: str
    ) -> Optional[Dict[str, Any]]:
        """按历史数据集 ID 查 provenance 关联。"""
        storage = self._require_storage()
        async with storage.acquire() as conn:
            row = await conn.fetchrow(
                "SELECT * FROM research.dataset_links WHERE history_dataset_id = $1",
                history_dataset_id,
            )
        return dict(row) if row is not None else None

    async def find_link_by_research_dataset(
        self, research_dataset_id: str
    ) -> Optional[Dict[str, Any]]:
        """按 Research 数据集 ID 查 provenance 关联。"""
        storage = self._require_storage()
        async with storage.acquire() as conn:
            row = await conn.fetchrow(
                "SELECT * FROM research.dataset_links WHERE research_dataset_id = $1",
                research_dataset_id,
            )
        return dict(row) if row is not None else None

    async def list_links(self, *, symbol: Optional[str] = None) -> List[Dict[str, Any]]:
        """列出全部 provenance 关联（用于补录命令的幂等检查）。"""
        storage = self._require_storage()
        clauses: List[str] = []
        params: List[Any] = []
        if symbol:
            params.append(symbol.upper())
            clauses.append(f"symbol = ${len(params)}")
        where = f"WHERE {' AND '.join(clauses)}" if clauses else ""
        async with storage.acquire() as conn:
            rows = await conn.fetch(
                f"SELECT * FROM research.dataset_links {where} ORDER BY created_at DESC",
                *params,
            )
        return [dict(row) for row in rows]
