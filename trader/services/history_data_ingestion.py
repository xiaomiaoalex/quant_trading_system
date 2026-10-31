"""
History Data Ingestion Service
==============================
币安公开归档（Binance Vision）历史行情的导入编排层。

主链路：
    Data 页面 → 历史任务 API → 本服务 → Binance Vision Adapter
        → ZIP 校验 → CSV 规范化 → PostgreSQL FeatureStore → 数据集登记
        → 现有回测引擎 / 报告

职责：
- 任务生命周期：QUEUED → RUNNING → SUCCEEDED / FAILED
- 阶段推进（DOWNLOAD / VERIFY / IMPORT / PUBLISH），只上报真实进度
- 严格持久化：FeatureStore 严格模式，失败不降级为内存
- 幂等：同一归档 hash 复用同一 feature_version；官方修订产生新版本

约束：
- Control Plane 负责编排，IO 由 Adapter / Persistence 完成
- PostgreSQL 不可用时直接失败（503），不创建假任务
"""

from __future__ import annotations

import asyncio
import logging
import os
import uuid
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional, Tuple

from trader.adapters.market_data.binance_vision import (
    AiohttpArchiveHttpClient,
    ArchiveChecksumMismatch,
    ArchiveContentError,
    ArchiveDownloadError,
    ArchiveError,
    ArchiveNotFoundError,
    BinanceVisionArchiveClient,
    build_archive_ref,
    default_market_data_root,
)
from trader.adapters.persistence.feature_store import (
    FeatureStore,
    FeatureStoreStrictError,
    get_strict_feature_store,
)
from trader.adapters.persistence.history_dataset_store import (
    DATASET_STATUS_PENDING,
    JOB_STATUS_FAILED,
    JOB_STATUS_QUEUED,
    JOB_STATUS_RUNNING,
    JOB_STATUS_SUCCEEDED,
    HistoryDatasetStore,
    HistoryStoreError,
    HistoryStoreUnavailableError,
)
from trader.adapters.persistence.research_history_bridge_store import (
    ResearchHistoryBridgeStore,
    ResearchHistoryStoreError,
    ResearchHistoryStoreUnavailableError,
)
from trader.core.domain.services.ohlcv_archive import (
    ARCHIVE_SOURCE_NAME,
    ARCHIVE_SUPPORTED_INTERVAL,
    ARCHIVE_SUPPORTED_MARKET,
    ARCHIVE_SUPPORTED_SYMBOLS,
    DATASET_FEATURE_VERSION_PREFIX,
    KlineBar,
    OhlcvQualityError,
    OhlcvRequestError,
    build_feature_version,
    expected_points_for_month,
    parse_month,
    validate_month_coverage,
)
from trader.trading.datasets import dataset_metadata
from trader.trading.research_bridge import (
    CONVERSION_VERSION,
    build_research_dataset,
    build_research_provenance,
)

logger = logging.getLogger(__name__)

# ==================== error codes ====================

ERROR_ARCHIVE_NOT_FOUND = "ARCHIVE_NOT_FOUND"
ERROR_NETWORK = "NETWORK_ERROR"
ERROR_CHECKSUM_MISMATCH = "CHECKSUM_MISMATCH"
ERROR_QUALITY = "QUALITY_ERROR"
ERROR_PERSISTENCE = "PERSISTENCE_ERROR"
ERROR_UNSUPPORTED = "UNSUPPORTED_REQUEST"
ERROR_INTERNAL = "INTERNAL_ERROR"

STAGE_QUEUED = "QUEUED"
STAGE_DOWNLOAD = "DOWNLOAD"
STAGE_VERIFY = "VERIFY"
STAGE_IMPORT = "IMPORT"
STAGE_PUBLISH = "PUBLISH"
STAGE_FAILED = "FAILED"

_IMPORT_BATCH_SIZE = 2000


class HistoryIngestionError(RuntimeError):
    """历史数据导入失败（携带可读错误码）。"""

    def __init__(self, code: str, reason: str) -> None:
        super().__init__(reason)
        self.code = code
        self.reason = reason


class HistoryStoreNotReadyError(RuntimeError):
    """PostgreSQL 不可用，无法创建历史任务。"""


# ==================== store accessor ====================

_store_lock: Optional[asyncio.Lock] = None
_store_instance: Optional[HistoryDatasetStore] = None
_store_loop: Optional[asyncio.AbstractEventLoop] = None


def _get_store_lock() -> asyncio.Lock:
    global _store_lock
    if _store_lock is None:
        _store_lock = asyncio.Lock()
    return _store_lock


async def get_history_dataset_store() -> HistoryDatasetStore:
    """获取（必要时建立）全局历史数据存储连接。"""
    global _store_instance, _store_loop

    current_loop = asyncio.get_running_loop()
    if _store_loop is not None and _store_loop is not current_loop:
        await reset_history_dataset_store()
        _store_loop = current_loop

    if _store_instance is not None and _store_instance._storage is not None:
        if _store_instance._storage.is_connected:
            return _store_instance

    async with _get_store_lock():
        if (
            _store_instance is not None
            and _store_instance._storage is not None
            and _store_instance._storage.is_connected
        ):
            return _store_instance
        store = HistoryDatasetStore()
        await store.connect()
        _store_instance = store
        _store_loop = current_loop
        return store


async def reset_history_dataset_store() -> None:
    """断开并清空全局存储（测试 / 关停使用）。"""
    global _store_instance, _store_loop
    store = _store_instance
    _store_instance = None
    _store_loop = None
    if store is not None:
        await store.disconnect()


_archive_client_instance: Optional[BinanceVisionArchiveClient] = None


def get_history_archive_client() -> BinanceVisionArchiveClient:
    """
    获取全局归档客户端（复用 HTTP 会话，避免每次请求新建 session）。

    会话在 `reset_history_archive_client()` 时关闭（应用关停 / 测试）。
    """
    global _archive_client_instance
    if _archive_client_instance is None:
        _archive_client_instance = BinanceVisionArchiveClient(
            AiohttpArchiveHttpClient(),
            market_data_root=default_market_data_root(),
        )
    return _archive_client_instance


async def reset_history_archive_client() -> None:
    """关闭全局归档客户端的 HTTP 会话并清空（应用关停 / 测试）。"""
    global _archive_client_instance
    client = _archive_client_instance
    _archive_client_instance = None
    if client is not None:
        try:
            await client.close()
        except Exception:  # pragma: no cover - best effort cleanup
            pass


# ==================== service ====================


class HistoryIngestionService:
    """历史归档导入编排服务。"""

    def __init__(
        self,
        store: Optional[HistoryDatasetStore] = None,
        archive_client: Optional[BinanceVisionArchiveClient] = None,
        feature_store: Optional[FeatureStore] = None,
        market_data_root: Optional[str] = None,
        bridge_store: Optional[ResearchHistoryBridgeStore] = None,
    ) -> None:
        self._store = store
        self._archive_client = archive_client
        self._feature_store = feature_store
        self._market_data_root = market_data_root
        self._bridge_store = bridge_store
        self._owned_archive_client: Optional[BinanceVisionArchiveClient] = None

    # ==================== dependencies ====================

    async def _get_store(self) -> HistoryDatasetStore:
        if self._store is not None:
            return self._store
        return await get_history_dataset_store()

    def _get_bridge_store(self, store: HistoryDatasetStore) -> ResearchHistoryBridgeStore:
        """桥接存储：与历史存储共用同一条 PostgreSQL 连接池。

        没有底层连接时直接失败：桥接发布必须和历史发布在同一个事务里完成，
        绝不允许悄悄新建一个数据库连接。
        """
        if self._bridge_store is not None:
            return self._bridge_store
        storage = getattr(store, "storage", None)
        if storage is None:
            raise HistoryIngestionError(
                ERROR_PERSISTENCE,
                "History store has no PostgreSQL connection; cannot publish to Research",
            )
        self._bridge_store = ResearchHistoryBridgeStore(storage=storage)
        return self._bridge_store

    @property
    def bridge_store(self) -> Optional[ResearchHistoryBridgeStore]:
        """当前使用的桥接存储（测试与诊断用）。"""
        return self._bridge_store

    def _get_archive_client(self) -> BinanceVisionArchiveClient:
        if self._archive_client is not None:
            return self._archive_client
        if self._market_data_root:
            if self._owned_archive_client is None:
                self._owned_archive_client = BinanceVisionArchiveClient(
                    AiohttpArchiveHttpClient(),
                    market_data_root=self._market_data_root,
                )
            return self._owned_archive_client
        return get_history_archive_client()

    def _get_feature_store(self) -> FeatureStore:
        if self._feature_store is not None:
            return self._feature_store
        return get_strict_feature_store()

    # ==================== request validation ====================

    @staticmethod
    def validate_request(symbol: str, month: str, now: Optional[datetime] = None) -> None:
        """
        校验用户输入。

        只允许 BTCUSDT / ETHUSDT + 1m 现货归档；月份必须是已结束的完整月份。
        """
        normalized = (symbol or "").strip().upper()
        if normalized not in ARCHIVE_SUPPORTED_SYMBOLS:
            raise OhlcvRequestError(
                f"Unsupported symbol '{symbol}'. Supported: {', '.join(ARCHIVE_SUPPORTED_SYMBOLS)}"
            )
        year, month_number = parse_month(month)
        reference = now or datetime.now(timezone.utc)
        current_year, current_month = reference.year, reference.month
        if (year, month_number) >= (current_year, current_month):
            raise OhlcvRequestError(
                f"Month '{month}' has not ended yet; only completed months are available"
            )

    # ==================== job APIs ====================

    async def submit_job(
        self,
        *,
        symbol: str,
        month: str,
        requested_by: str = "console_user",
    ) -> Dict[str, Any]:
        """
        提交导入任务。

        决策：**提交路径不做任何远程 HTTP**。
        - 远程归档是否存在由 worker 在下载阶段判定（缺失 → 任务 FAILED，error_code=ARCHIVE_NOT_FOUND）。
        - 因此提交不会被远端 HEAD 阻塞，也不会因归档被官方修订 / 移除而误报 404。

        复用规则（完全走数据库）：
        - 已有可用数据集：返回**关联的成功任务**（不新建任务行、不重复下载）
        - 已有活动任务：复用该任务（数据库唯一约束保证并发正确）
        - 否则创建 QUEUED 任务
        """
        self.validate_request(symbol, month)
        normalized_symbol = symbol.strip().upper()
        year, month_number = parse_month(month)
        normalized_month = f"{year:04d}-{month_number:02d}"

        store = await self._get_store()

        # 1) 已有可用数据集：返回关联的成功任务（不触碰远程）
        available = await self._find_available_dataset(store, normalized_symbol, normalized_month)
        if available is not None:
            job = await self._find_associated_job(store, available)
            if job is not None:
                return job
            # 关联任务缺失（例如历史遗留的数据集）：补一条并复用
            return await store.create_job(
                job_id=_new_job_id(),
                source=ARCHIVE_SOURCE_NAME,
                market=ARCHIVE_SUPPORTED_MARKET,
                symbol=normalized_symbol,
                interval=ARCHIVE_SUPPORTED_INTERVAL,
                month=normalized_month,
                requested_by=requested_by,
                status=JOB_STATUS_SUCCEEDED,
                stage="PUBLISHED",
                dataset_id=available["dataset_id"],
                feature_version=available["feature_version"],
            )

        return await store.create_job(
            job_id=_new_job_id(),
            source=ARCHIVE_SOURCE_NAME,
            market=ARCHIVE_SUPPORTED_MARKET,
            symbol=normalized_symbol,
            interval=ARCHIVE_SUPPORTED_INTERVAL,
            month=normalized_month,
            requested_by=requested_by,
            status=JOB_STATUS_QUEUED,
            stage=STAGE_QUEUED,
        )

    async def _find_associated_job(
        self,
        store: HistoryDatasetStore,
        dataset: Dict[str, Any],
    ) -> Optional[Dict[str, Any]]:
        """
        查找数据集关联的成功任务。

        优先使用数据集登记时的 job_id（精确关联），否则按 dataset_id 反查成功任务。
        找不到时返回 None（由调用方决定是否补建）。
        """
        job_id = dataset.get("job_id")
        if job_id:
            job = await store.get_job(str(job_id))
            if job is not None and job.get("status") == JOB_STATUS_SUCCEEDED:
                return job
        return await store.find_succeeded_job_by_dataset(str(dataset["dataset_id"]))

    async def _find_available_dataset(
        self,
        store: HistoryDatasetStore,
        symbol: str,
        month: str,
    ) -> Optional[Dict[str, Any]]:
        datasets = await store.list_datasets(
            source=ARCHIVE_SOURCE_NAME,
            market=ARCHIVE_SUPPORTED_MARKET,
            symbol=symbol,
            interval=ARCHIVE_SUPPORTED_INTERVAL,
            month=month,
            only_eligible=True,
        )
        return datasets[0] if datasets else None

    async def get_job(self, job_id: str) -> Optional[Dict[str, Any]]:
        store = await self._get_store()
        return await store.get_job(job_id)

    async def list_jobs(
        self,
        *,
        status: Optional[str] = None,
        symbol: Optional[str] = None,
        month: Optional[str] = None,
        limit: int = 100,
    ) -> List[Dict[str, Any]]:
        store = await self._get_store()
        return await store.list_jobs(status=status, symbol=symbol, month=month, limit=limit)

    async def retry_job(self, job_id: str) -> Dict[str, Any]:
        """重试 FAILED 任务（幂等：同一 job_id 复用）。"""
        store = await self._get_store()
        job = await store.get_job(job_id)
        if job is None:
            raise KeyError(job_id)
        if job["status"] != JOB_STATUS_FAILED:
            raise HistoryIngestionError(
                ERROR_UNSUPPORTED,
                f"Job {job_id} is {job['status']}; only FAILED jobs can be retried",
            )
        requeued = await store.requeue_job(job_id)
        if requeued is None:
            raise HistoryIngestionError(
                ERROR_UNSUPPORTED,
                f"Job {job_id} could not be requeued",
            )
        return requeued

    async def get_dataset(self, dataset_id: str) -> Optional[Dict[str, Any]]:
        """按 dataset_id 直接查询数据集（不做列表扫描）。"""
        store = await self._get_store()
        return await store.get_dataset(dataset_id)

    async def list_datasets(
        self,
        *,
        symbol: Optional[str] = None,
        month: Optional[str] = None,
        only_eligible: bool = False,
        limit: int = 200,
    ) -> List[Dict[str, Any]]:
        store = await self._get_store()
        return await store.list_datasets(
            source=ARCHIVE_SOURCE_NAME,
            market=ARCHIVE_SUPPORTED_MARKET,
            symbol=symbol,
            interval=ARCHIVE_SUPPORTED_INTERVAL,
            month=month,
            only_eligible=only_eligible,
            limit=limit,
        )

    # ==================== execution ====================

    async def recover_interrupted_jobs(self) -> int:
        """启动恢复：把上次残留的 RUNNING 任务标记为 FAILED（服务中断）。"""
        store = await self._get_store()
        count = await store.fail_running_jobs("service restarted while job was running")
        if count:
            logger.warning("Marked %s interrupted history job(s) as FAILED", count)
        return count

    async def run_job(self, job_id: str) -> Dict[str, Any]:
        """执行一个任务（由 worker 调用）。"""
        store = await self._get_store()
        job = await store.get_job(job_id)
        if job is None:
            raise HistoryIngestionError(ERROR_INTERNAL, f"Job {job_id} not found")

        claimed = await store.claim_job(job_id)
        if not claimed:
            job = await store.get_job(job_id)
            return job or {}

        try:
            return await self._execute_job(store, job)
        except HistoryIngestionError as exc:
            await store.update_job(
                job_id,
                status=JOB_STATUS_FAILED,
                stage=STAGE_FAILED,
                error_code=exc.code,
                error_reason=exc.reason,
                finished_at=datetime.now(timezone.utc).isoformat(),
            )
            logger.warning("History job %s failed: %s (%s)", job_id, exc.reason, exc.code)
            return await store.get_job(job_id) or {}
        except Exception as exc:  # noqa: BLE001 - fail-closed: any unexpected error fails the job
            logger.exception("History job %s crashed", job_id)
            await store.update_job(
                job_id,
                status=JOB_STATUS_FAILED,
                stage=STAGE_FAILED,
                error_code=ERROR_INTERNAL,
                error_reason=str(exc),
                finished_at=datetime.now(timezone.utc).isoformat(),
            )
            return await store.get_job(job_id) or {}

    async def _execute_job(
        self,
        store: HistoryDatasetStore,
        job: Dict[str, Any],
    ) -> Dict[str, Any]:
        job_id = str(job["job_id"])
        symbol = str(job["symbol"])
        interval = str(job["interval"])
        month = str(job["month"])
        year, month_number = parse_month(month)

        client = self._get_archive_client()
        ref = build_archive_ref(symbol, interval, month)

        # ---- DOWNLOAD ----
        await store.update_job(job_id, stage=STAGE_DOWNLOAD)
        try:
            downloaded = await client.download(ref)
        except ArchiveNotFoundError as exc:
            raise HistoryIngestionError(ERROR_ARCHIVE_NOT_FOUND, str(exc)) from exc
        except ArchiveChecksumMismatch as exc:
            raise HistoryIngestionError(ERROR_CHECKSUM_MISMATCH, str(exc)) from exc
        except ArchiveDownloadError as exc:
            raise HistoryIngestionError(ERROR_NETWORK, str(exc)) from exc
        except ArchiveError as exc:
            raise HistoryIngestionError(ERROR_ARCHIVE_NOT_FOUND, str(exc)) from exc

        await store.update_job(job_id, bytes_downloaded=downloaded.size_bytes)

        # ---- VERIFY ----
        await store.update_job(job_id, stage=STAGE_VERIFY)
        existing = await store.find_dataset_by_hash(
            source=ARCHIVE_SOURCE_NAME,
            market=ARCHIVE_SUPPORTED_MARKET,
            symbol=symbol,
            interval=interval,
            month=month,
            archive_sha256=downloaded.sha256,
        )
        if (
            existing is not None
            and existing["status"] == "PUBLISHED"
            and existing.get("research_dataset_id")
        ):
            # 同 hash 已有可用数据集（且已桥接进 Research）：复用，不重复导入
            client.discard(downloaded)
            await store.update_job(
                job_id,
                stage=STAGE_PUBLISH,
                dataset_id=existing["dataset_id"],
                feature_version=existing["feature_version"],
                rows_processed=existing["row_count"],
            )
            published = await self._publish_dataset(
                store=store,
                job_id=job_id,
                dataset_id=str(existing["dataset_id"]),
                feature_version=str(existing["feature_version"]),
                row_count=int(existing["row_count"]),
                expected_points=int(existing["expected_points"]),
                gap_count=int(existing["gap_count"]),
                first_ts_ms=existing["first_ts_ms"],
                last_ts_ms=existing["last_ts_ms"],
                quality=_as_dict(existing["quality"]),
                provenance=_provenance_fields(
                    symbol=symbol,
                    interval=interval,
                    month=month,
                    source_url=str(existing["source_url"]),
                    checksum_url=str(existing["checksum_url"]),
                    official_sha256=existing.get("official_sha256"),
                    archive_sha256=str(existing["archive_sha256"]),
                    archive_version=int(existing["version"]),
                    feature_version=str(existing["feature_version"]),
                ),
                research_document=None,
                research_dataset_id=str(existing["research_dataset_id"]),
            )
            return published["job"]

        if existing is not None:
            # 已有同 hash 的未发布数据集：沿用它的版本（不新建版本）
            version = int(existing["version"])
        else:
            version = await store.next_version(
                source=ARCHIVE_SOURCE_NAME,
                market=ARCHIVE_SUPPORTED_MARKET,
                symbol=symbol,
                interval=interval,
                month=month,
            )
        try:
            # commit() 会校验目标文件 hash：一致则复用（并补写缺失的 CHECKSUM），
            # 不一致则抛错 —— 绝不覆盖已发布版本的证据。
            archive_path = client.commit(downloaded, version)
        except ArchiveChecksumMismatch as exc:
            raise HistoryIngestionError(ERROR_CHECKSUM_MISMATCH, str(exc)) from exc

        # ---- IMPORT ----
        await store.update_job(job_id, stage=STAGE_IMPORT)
        try:
            bars = await asyncio.to_thread(client.read_klines, archive_path, ref)
            coverage = validate_month_coverage(bars, year, month_number)
        except OhlcvQualityError as exc:
            raise HistoryIngestionError(ERROR_QUALITY, str(exc)) from exc
        except ArchiveContentError as exc:
            raise HistoryIngestionError(ERROR_QUALITY, str(exc)) from exc

        feature_version = build_feature_version(symbol, month, downloaded.sha256)
        dataset_id = str(existing["dataset_id"]) if existing is not None else _new_dataset_id()

        # 质量信息里记录官方文件名与本地版本化文件名（本地名带 -vN，官方名不带）
        quality = {
            **coverage.to_dict(),
            "official_file_name": downloaded.official_file_name,
            "local_archive_name": os.path.basename(archive_path),
            "archive_sha256": downloaded.sha256,
            "official_sha256": downloaded.official_sha256,
        }

        if existing is None:
            await store.insert_dataset(
                dataset_id=dataset_id,
                source=ARCHIVE_SOURCE_NAME,
                market=ARCHIVE_SUPPORTED_MARKET,
                symbol=symbol,
                interval=interval,
                month=month,
                version=version,
                feature_version=feature_version,
                archive_path=archive_path,
                archive_sha256=downloaded.sha256,
                official_sha256=downloaded.official_sha256,
                source_url=ref.zip_url,
                checksum_url=ref.checksum_url,
                row_count=len(bars),
                expected_points=coverage.expected_points,
                gap_count=coverage.gap_count,
                first_ts_ms=coverage.first_ts_ms,
                last_ts_ms=coverage.last_ts_ms,
                quality=quality,
                status=DATASET_STATUS_PENDING,
                persistence_ok=False,
                backtest_eligible=False,
                job_id=job_id,
            )

        try:
            await self._import_bars(
                symbol=symbol,
                feature_version=feature_version,
                bars=bars,
                month=month,
                archive_sha256=downloaded.sha256,
            )
        except FeatureStoreStrictError as exc:
            await store.fail_dataset(dataset_id, reason=str(exc), job_id=None)
            raise HistoryIngestionError(ERROR_PERSISTENCE, str(exc)) from exc

        # ---- PUBLISH ----
        await store.update_job(job_id, stage=STAGE_PUBLISH)
        persisted = await store.count_feature_rows(feature_version)
        expected = coverage.expected_points
        if (
            persisted["total_points"] != expected
            or persisted["first_ts_ms"] != coverage.first_ts_ms
            or persisted["last_ts_ms"] != coverage.last_ts_ms
        ):
            message = (
                "Persisted rows do not match the archive: "
                f"expected {expected} rows [{coverage.first_ts_ms}, {coverage.last_ts_ms}], "
                f"got {persisted['total_points']} rows "
                f"[{persisted['first_ts_ms']}, {persisted['last_ts_ms']}]"
            )
            await store.fail_dataset(dataset_id, reason=message)
            raise HistoryIngestionError(ERROR_PERSISTENCE, message)

        try:
            published = await self._publish_dataset(
                store=store,
                job_id=job_id,
                dataset_id=dataset_id,
                feature_version=feature_version,
                row_count=len(bars),
                expected_points=expected,
                gap_count=coverage.gap_count,
                first_ts_ms=coverage.first_ts_ms,
                last_ts_ms=coverage.last_ts_ms,
                quality=quality,
                provenance=_provenance_fields(
                    symbol=symbol,
                    interval=interval,
                    month=month,
                    source_url=ref.zip_url,
                    checksum_url=ref.checksum_url,
                    official_sha256=downloaded.official_sha256,
                    archive_sha256=downloaded.sha256,
                    archive_version=version,
                    feature_version=feature_version,
                ),
                research_document=build_research_dataset(
                    bars,
                    symbol=symbol,
                    archive_sha256=downloaded.sha256,
                    month=month,
                ),
                research_dataset_id=None,
            )
        except HistoryIngestionError as exc:
            # 原子发布失败：数据集保持不可选，绝不留下半成品
            await store.fail_dataset(dataset_id, reason=exc.reason, job_id=None)
            raise
        return published["job"]

    async def _publish_dataset(
        self,
        *,
        store: HistoryDatasetStore,
        job_id: str,
        dataset_id: str,
        feature_version: str,
        row_count: int,
        expected_points: int,
        gap_count: int,
        first_ts_ms: Optional[int],
        last_ts_ms: Optional[int],
        quality: Dict[str, Any],
        provenance: Dict[str, Any],
        research_document: Optional[Dict[str, Any]],
        research_dataset_id: Optional[str],
    ) -> Dict[str, Any]:
        """把已校验的历史归档原子地发布到 Research 平面并完成任务。"""
        bridge = self._get_bridge_store(store)
        coverage_start, coverage_end = _coverage_window(quality, first_ts_ms, last_ts_ms)
        link = build_research_provenance(
            history_dataset_id=dataset_id,
            coverage_start_ms=coverage_start,
            coverage_end_ms=coverage_end,
            bar_count=row_count,
            conversion_version=CONVERSION_VERSION,
            quality=quality,
            **provenance,
        )
        try:
            return await bridge.publish_dataset(
                job_id=job_id,
                history_dataset_id=dataset_id,
                feature_version=feature_version,
                row_count=row_count,
                expected_points=expected_points,
                gap_count=gap_count,
                first_ts_ms=first_ts_ms,
                last_ts_ms=last_ts_ms,
                quality=quality,
                link=link,
                research_document=research_document,
                research_metadata=(
                    dataset_metadata(research_document) if research_document else None
                ),
                research_dataset_id=research_dataset_id,
            )
        except ResearchHistoryStoreUnavailableError as exc:
            raise HistoryIngestionError(ERROR_PERSISTENCE, str(exc)) from exc
        except ResearchHistoryStoreError as exc:
            raise HistoryIngestionError(ERROR_PERSISTENCE, str(exc)) from exc

    async def _import_bars(
        self,
        *,
        symbol: str,
        feature_version: str,
        bars: List[KlineBar],
        month: str,
        archive_sha256: str,
    ) -> None:
        """分批写入 FeatureStore（严格模式，失败抛错）。"""
        feature_store = self._get_feature_store()
        meta = {
            "interval": ARCHIVE_SUPPORTED_INTERVAL,
            "source": ARCHIVE_SOURCE_NAME,
            "market": ARCHIVE_SUPPORTED_MARKET,
            "month": month,
            "archive_sha256": archive_sha256,
        }
        for start in range(0, len(bars), _IMPORT_BATCH_SIZE):
            batch = bars[start : start + _IMPORT_BATCH_SIZE]
            rows = [(bar.open_time_ms, bar.as_value()) for bar in batch]
            await feature_store.write_features_bulk(
                symbol=symbol,
                feature_name="ohlcv",
                version=feature_version,
                rows=rows,
                meta=meta,
            )

    async def process_next_queued_job(self) -> Optional[Dict[str, Any]]:
        """领取并执行一个 QUEUED 任务（单并发 worker）。"""
        store = await self._get_store()
        queued = await store.list_queued_jobs(limit=1)
        if not queued:
            return None
        job_id = str(queued[0]["job_id"])
        return await self.run_job(job_id)


class HistoryJobWorker:
    """单并发后台 worker：串行领取 QUEUED 任务。"""

    def __init__(
        self,
        service: Optional[HistoryIngestionService] = None,
        poll_interval_seconds: float = 2.0,
        unavailable_backoff_seconds: float = 30.0,
    ) -> None:
        self._service = service or HistoryIngestionService()
        self._poll_interval = poll_interval_seconds
        self._unavailable_backoff_seconds = unavailable_backoff_seconds
        self._task: Optional[asyncio.Task] = None
        self._stopping = False

    @property
    def running(self) -> bool:
        return self._task is not None and not self._task.done()

    async def start(self) -> None:
        if self.running:
            return
        self._stopping = False
        self._task = asyncio.create_task(self._run_loop())

    async def stop(self) -> None:
        self._stopping = True
        task = self._task
        self._task = None
        if task is not None:
            task.cancel()
            try:
                await task
            except (asyncio.CancelledError, Exception):  # noqa: B014 - shutdown must not raise
                pass

    async def _run_loop(self) -> None:
        recovered = False
        unavailable_logged = False
        while not self._stopping:
            if not recovered:
                try:
                    await self._service.recover_interrupted_jobs()
                    recovered = True
                except Exception as exc:  # noqa: BLE001 - recovery must not kill the loop
                    # 恢复失败必须重试；在恢复成功之前不领取任何任务，
                    # 否则可能把别的实例正在执行的任务误标为中断。
                    logger.warning("History job recovery failed: %s", exc)
                    await asyncio.sleep(self._unavailable_backoff_seconds)
                    continue

            try:
                processed = await self._service.process_next_queued_job()
                unavailable_logged = False
            except HistoryStoreUnavailableError as exc:
                if not unavailable_logged:
                    unavailable_logged = True
                    logger.warning(
                        "History dataset store unavailable (%s); "
                        "history ingestion worker is paused until it recovers",
                        exc,
                    )
                await asyncio.sleep(self._unavailable_backoff_seconds)
                continue
            except Exception:  # noqa: BLE001 - keep the worker alive
                logger.exception("History job worker iteration failed")
                processed = None

            if processed is None:
                await asyncio.sleep(self._poll_interval)


def _new_job_id() -> str:
    return f"hjob_{uuid.uuid4().hex[:16]}"


def _new_dataset_id() -> str:
    return f"hds_{uuid.uuid4().hex[:16]}"


def _as_dict(value: Any) -> Dict[str, Any]:
    if isinstance(value, dict):
        return value
    if isinstance(value, str):
        import json

        try:
            parsed = json.loads(value)
        except json.JSONDecodeError:
            return {}
        return parsed if isinstance(parsed, dict) else {}
    return {}


def _provenance_fields(
    *,
    symbol: str,
    interval: str,
    month: str,
    source_url: str,
    checksum_url: str,
    official_sha256: Optional[str],
    archive_sha256: str,
    archive_version: int,
    feature_version: str,
) -> Dict[str, Any]:
    """provenance 的稳定字段（不包含时间戳，保证可重复计算）。"""
    return {
        "symbol": symbol,
        "interval": interval,
        "month": month,
        "source_url": source_url,
        "checksum_url": checksum_url,
        "official_sha256": official_sha256,
        "archive_sha256": archive_sha256,
        "archive_version": int(archive_version),
        "feature_version": feature_version,
    }


def _coverage_window(
    quality: Dict[str, Any],
    first_ts_ms: Optional[int],
    last_ts_ms: Optional[int],
) -> Tuple[Optional[int], Optional[int]]:
    """UTC 覆盖窗口 [start, end)：结束时间为最后一根 K 线的收盘时间。"""
    start = quality.get("month_start_ms")
    end = quality.get("month_end_exclusive_ms")
    if start is None:
        start = first_ts_ms
    if end is None and last_ts_ms is not None:
        end = int(last_ts_ms) + 60_000
    return start, end


def dataset_feature_version_prefix() -> str:
    """数据集 feature_version 前缀（供上层做门禁判断）。"""
    return DATASET_FEATURE_VERSION_PREFIX


def market_data_root_from_env() -> str:
    """行情根目录（环境变量优先）。"""
    return os.environ.get("MARKET_DATA_ROOT", "").strip() or default_market_data_root()


def expected_points(month: str) -> int:
    """整月应有的 K 线数量。"""
    year, month_number = parse_month(month)
    return expected_points_for_month(year, month_number)


__all__ = [
    "ERROR_ARCHIVE_NOT_FOUND",
    "ERROR_CHECKSUM_MISMATCH",
    "ERROR_NETWORK",
    "ERROR_PERSISTENCE",
    "ERROR_QUALITY",
    "HistoryIngestionError",
    "HistoryIngestionService",
    "HistoryJobWorker",
    "HistoryStoreNotReadyError",
    "get_history_archive_client",
    "get_history_dataset_store",
    "reset_history_archive_client",
    "reset_history_dataset_store",
]
