"""
Backtest Dataset Resolution
============================
回测请求与研究数据集的统一解析 / 准入校验入口。

普通回测（POST /v1/backtests）与候选回测（POST /v1/strategy-candidates/{id}/backtests）
共用本模块，保证：
- 准入校验先于创建 / 调度（不依赖前端禁用按钮）
- 已发布历史数据集必须显式提供 dataset_id，不能只靠 feature_version 绕过门禁
- 用户显式提交的字段与数据集冲突时直接拒绝（区分「未提供的旧默认值」与「显式提交」）

约束：
- 解析是异步的（PG 读取不能阻塞 API loop）
"""

from __future__ import annotations

import logging
from typing import Any, Dict, Optional, Set

from trader.api.models.schemas import BacktestDatasetSpec, BacktestRequest
from trader.core.domain.services.ohlcv_archive import DATASET_FEATURE_VERSION_PREFIX
from trader.services.backtesting.history_dataset_resolver import (
    DatasetNotEligibleError,
    DatasetNotFoundError,
    DatasetRangeError,
    HistoryDatasetResolver,
    ResolvedDataset,
)

logger = logging.getLogger(__name__)


class BacktestRequestConflictError(ValueError):
    """请求字段与数据集定义冲突。"""


def _explicit_fields(model: Any) -> Set[str]:
    """返回用户显式提交的字段名（Pydantic model_fields_set）。"""
    fields = getattr(model, "model_fields_set", None)
    if fields is None:  # pragma: no cover - defensive
        return set()
    return set(fields)


def requires_dataset_id(feature_version: Optional[str]) -> bool:
    """
    判断该 feature_version 是否属于「已登记历史数据集」。

    已登记数据集的 feature_version 以 ``binance_spot_1m`` 开头，
    这类请求必须显式提供 dataset_id，否则视为绕过门禁。
    """
    if not feature_version:
        return False
    return feature_version.startswith(DATASET_FEATURE_VERSION_PREFIX)


async def resolve_dataset(
    dataset_id: str,
    *,
    start_ts_ms: Optional[int] = None,
    end_ts_ms: Optional[int] = None,
    resolver: Optional[HistoryDatasetResolver] = None,
) -> ResolvedDataset:
    """解析并校验数据集（准入校验的唯一入口）。"""
    active = resolver or HistoryDatasetResolver()
    return await active.resolve(dataset_id, start_ts_ms=start_ts_ms, end_ts_ms=end_ts_ms)


def _check_symbols(explicit: Set[str], symbols: Any, dataset: ResolvedDataset) -> None:
    if "symbols" not in explicit:
        return
    provided = [str(item).upper() for item in (symbols or [])]
    if provided != [dataset.symbol]:
        raise BacktestRequestConflictError(
            f"Dataset {dataset.dataset_id} only provides symbol {dataset.symbol}; "
            f"got {provided}"
        )


def _check_feature_version(explicit: Set[str], feature_version: Optional[str], dataset) -> None:
    if "feature_version" not in explicit:
        return
    if feature_version != dataset.feature_version:
        raise BacktestRequestConflictError(
            f"Dataset {dataset.dataset_id} uses feature_version {dataset.feature_version}; "
            f"got {feature_version}"
        )


def _check_data_mode(explicit: Set[str], data_mode: Optional[str]) -> None:
    if "data_mode" not in explicit:
        return
    if data_mode != "real_feature_store":
        raise BacktestRequestConflictError(
            "A published history dataset requires data_mode='real_feature_store'; "
            f"got {data_mode}"
        )


def _check_interval(explicit: Set[str], interval: Optional[str], dataset) -> None:
    if "interval" not in explicit:
        return
    if interval != dataset.interval:
        raise BacktestRequestConflictError(
            f"Dataset {dataset.dataset_id} only provides interval {dataset.interval}; "
            f"got {interval}"
        )


def _check_params_interval(params: Optional[Dict[str, Any]], dataset) -> None:
    if not params:
        return
    interval = params.get("interval")
    if interval is None:
        return
    if str(interval) != dataset.interval:
        raise BacktestRequestConflictError(
            f"Dataset {dataset.dataset_id} only provides interval {dataset.interval}; "
            f"params.interval={interval} conflicts"
        )


async def resolve_backtest_request(
    request: BacktestRequest,
    resolver: Optional[HistoryDatasetResolver] = None,
) -> BacktestRequest:
    """
    解析回测请求，返回可执行请求。

    Raises:
        BacktestRequestConflictError: 显式字段与数据集冲突
        DatasetNotFoundError: 数据集不存在
        DatasetNotEligibleError: 数据集未发布 / 不完整 / 持久化不可用
        DatasetRangeError: 时间范围越界或未对齐
        ValueError: 旧入口缺少必需的时间范围
    """
    explicit = _explicit_fields(request)

    if request.dataset_id:
        dataset = await resolve_dataset(
            request.dataset_id,
            start_ts_ms=request.start_ts_ms,
            end_ts_ms=request.end_ts_ms,
            resolver=resolver,
        )
        _check_symbols(explicit, request.symbols, dataset)
        _check_feature_version(explicit, request.feature_version, dataset)
        _check_data_mode(explicit, request.data_mode)
        _check_interval(explicit, request.interval, dataset)
        _check_params_interval(request.params, dataset)
        if "engine" in explicit and request.engine != "vectorbt":
            raise BacktestRequestConflictError(
                f"Dataset {dataset.dataset_id} requires engine='vectorbt'; got {request.engine}"
            )

        params = dict(request.params or {})
        params["interval"] = dataset.interval
        return request.model_copy(
            update={
                "engine": "vectorbt",
                "data_mode": "real_feature_store",
                "symbols": [dataset.symbol],
                "feature_version": dataset.feature_version,
                "interval": dataset.interval,
                "start_ts_ms": dataset.start_ts_ms,
                "end_ts_ms": dataset.end_ts_ms,
                "params": params,
            }
        )

    if requires_dataset_id(request.feature_version):
        raise BacktestRequestConflictError(
            "dataset_id is required for registered history datasets "
            f"(feature_version={request.feature_version}); refusing to bypass the "
            "dataset admission gate"
        )

    if request.start_ts_ms is None or request.end_ts_ms is None:
        raise ValueError("start_ts_ms and end_ts_ms are required when dataset_id is not provided")

    return request


async def resolve_dataset_spec(
    spec: BacktestDatasetSpec,
    resolver: Optional[HistoryDatasetResolver] = None,
) -> BacktestDatasetSpec:
    """
    解析研究回测数据集描述（候选回测共用）。

    Raises:
        BacktestRequestConflictError / DatasetNotFoundError / DatasetNotEligibleError /
        DatasetRangeError / ValueError
    """
    explicit = _explicit_fields(spec)

    if spec.dataset_id:
        dataset = await resolve_dataset(
            spec.dataset_id,
            start_ts_ms=spec.start_ts_ms,
            end_ts_ms=spec.end_ts_ms,
            resolver=resolver,
        )
        _check_symbols(explicit, spec.symbols, dataset)
        _check_feature_version(explicit, spec.feature_version, dataset)
        _check_data_mode(explicit, spec.data_mode)
        _check_interval(explicit, spec.interval, dataset)
        return spec.model_copy(
            update={
                "symbols": [dataset.symbol],
                "feature_version": dataset.feature_version,
                "data_mode": "real_feature_store",
                "interval": dataset.interval,
                "start_ts_ms": dataset.start_ts_ms,
                "end_ts_ms": dataset.end_ts_ms,
            }
        )

    if requires_dataset_id(spec.feature_version):
        raise BacktestRequestConflictError(
            "dataset_id is required for registered history datasets "
            f"(feature_version={spec.feature_version}); refusing to bypass the "
            "dataset admission gate"
        )

    if spec.start_ts_ms is None or spec.end_ts_ms is None:
        raise ValueError("start_ts_ms and end_ts_ms are required when dataset_id is not provided")

    return spec


__all__ = [
    "BacktestRequestConflictError",
    "requires_dataset_id",
    "resolve_backtest_request",
    "resolve_dataset",
    "resolve_dataset_spec",
]
