"""Performance accounting API routes."""

from __future__ import annotations

import time
from dataclasses import asdict, is_dataclass
from decimal import Decimal, DecimalException
from typing import Any

from fastapi import APIRouter, Body, HTTPException, Query
from fastapi.responses import FileResponse, JSONResponse

from trader.core.domain.services.performance_accounting import (
    calculate_daily_returns,
    calculate_empyrical_compatible_metrics,
)
from trader.services.backtesting.quantstats_report import generate_tearsheet
from trader.services.benchmark_projector import (
    BenchmarkConstituentProjector,
    InitialWeightGenerator,
)
from trader.services.performance import PerformanceRunNotFoundError, get_performance_service
from trader.services.performance_projector import (
    PerformanceAttributionFactsBackfill,
    PerformanceExecutionProjector,
    PerformanceProjectorWorker,
)
from trader.storage.artifact_storage import get_artifact_storage

router = APIRouter(tags=["Performance"])


def compute_period_ms(period_days: int) -> tuple[int, int]:
    """Compute period start and end timestamps in milliseconds.

    Args:
        period_days: Number of days to look back

    Returns:
        tuple of (period_start_ms, period_end_ms)
    """
    now_ms = int(time.time() * 1000)
    period_end_ms = now_ms
    period_start_ms = now_ms - (period_days * 86400 * 1000)
    return period_start_ms, period_end_ms


@router.get("/v1/performance/runs")
async def list_performance_runs(
    deployment_id: str | None = None,
    strategy_id: str | None = None,
    status: str | None = None,
    limit: int = Query(100, ge=1, le=500),
) -> list[dict[str, Any]]:
    service = get_performance_service()
    runs = await service.list_runs(
        deployment_id=deployment_id,
        strategy_id=strategy_id,
        status=status,
        limit=limit,
    )
    return [_serialize(run) for run in runs]


@router.get("/v1/performance/nav")
async def list_performance_nav(
    run_id: str | None = None,
    deployment_id: str | None = None,
    strategy_id: str | None = None,
    account_id: str | None = None,
    venue: str | None = None,
    since_ms: int | None = None,
    limit: int = Query(500, ge=1, le=2000),
) -> list[dict[str, Any]]:
    service = get_performance_service()
    points = await service.list_nav(
        run_id=run_id,
        deployment_id=deployment_id,
        strategy_id=strategy_id,
        account_id=account_id,
        venue=venue,
        since_ms=since_ms,
        limit=limit,
    )
    return [_serialize(point) for point in points]


@router.get("/v1/performance/account-nav")
async def list_account_performance_nav(
    account_id: str,
    venue: str | None = None,
    since_ms: int | None = None,
    limit: int = Query(500, ge=1, le=2000),
) -> list[dict[str, Any]]:
    service = get_performance_service()
    points = await service.list_account_nav(
        account_id=account_id,
        venue=venue,
        since_ms=since_ms,
        limit=limit,
    )
    return [_serialize(point) for point in points]


@router.get("/v1/performance/pnl-explain")
async def get_pnl_explain(run_id: str) -> dict[str, Any]:
    service = get_performance_service()
    try:
        return _serialize(await service.get_pnl_explain(run_id=run_id))
    except PerformanceRunNotFoundError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc


@router.get("/v1/performance/snapshots/{run_id}")
async def get_performance_snapshot(run_id: str) -> dict[str, Any]:
    service = get_performance_service()
    try:
        snapshot = await service.get_latest_snapshot(run_id)
    except PerformanceRunNotFoundError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    if snapshot is None:
        raise HTTPException(
            status_code=404, detail=f"No performance snapshot found for run_id={run_id}"
        )
    return _serialize(snapshot)


@router.get("/v1/performance/attribution")
async def get_attribution(
    scope: str = "run",
    group_by: str = "symbol",
    run_id: str | None = None,
    account_id: str | None = None,
    venue: str | None = None,
) -> dict[str, Any]:
    service = get_performance_service()
    result = await service.calculate_attribution(
        scope=scope,
        group_by=group_by,
        run_id=run_id,
        account_id=account_id,
        venue=venue,
    )
    return _serialize(result)


@router.get("/v1/performance/attribution/brinson")
async def get_brinson_attribution(
    run_id: str,
    benchmark_id: str,
    period_start_ms: int,
    period_end_ms: int,
) -> dict[str, Any]:
    service = get_performance_service()
    try:
        result = await service.calculate_brinson_attribution(
            run_id=run_id,
            benchmark_id=benchmark_id,
            period_start_ms=period_start_ms,
            period_end_ms=period_end_ms,
        )
    except PerformanceRunNotFoundError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    return _serialize(result)


@router.get("/v1/performance/attribution/brinson/linked")
async def get_brinson_linked_attribution(
    run_id: str,
    benchmark_id: str,
    periods: str,
) -> dict[str, Any]:
    service = get_performance_service()
    try:
        result = await service.calculate_brinson_linked_attribution(
            run_id=run_id,
            benchmark_id=benchmark_id,
            periods=_parse_periods(periods),
        )
    except PerformanceRunNotFoundError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    return _serialize(result)


@router.get("/v1/performance/attribution/factors")
async def get_factor_attribution(
    run_id: str,
    period_start_ms: int,
    period_end_ms: int,
) -> dict[str, Any]:
    service = get_performance_service()
    try:
        result = await service.calculate_factor_attribution(
            run_id=run_id,
            period_start_ms=period_start_ms,
            period_end_ms=period_end_ms,
        )
    except PerformanceRunNotFoundError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    return _serialize(result)


@router.get("/v1/performance/attribution/risk-budget")
async def get_risk_budget_attribution(
    run_id: str,
    period_start_ms: int,
    period_end_ms: int,
) -> dict[str, Any]:
    service = get_performance_service()
    try:
        result = await service.calculate_risk_budget_attribution(
            run_id=run_id,
            period_start_ms=period_start_ms,
            period_end_ms=period_end_ms,
        )
    except PerformanceRunNotFoundError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    return _serialize(result)


@router.post("/v1/performance/facts/backfill")
async def backfill_performance_facts(payload: dict[str, Any]) -> dict[str, Any]:
    """Backfill attribution fact tables with deterministic IDs for idempotent replays."""

    result = await PerformanceAttributionFactsBackfill().backfill(payload)
    return _serialize(result)


# ============================================================================
# Execution Projector — recover ledger from durable execution facts
# ============================================================================


@router.post("/v1/performance/projector/trigger")
async def trigger_performance_projector(
    limit: int = Query(100, ge=1, le=500),
) -> dict[str, Any]:
    """Project executions into performance ledger (idempotent batch).

    Reads from ExecutionRepository, writes to PerformanceService.
    Returns counts: processed, projected, duplicates, failed.
    Cursor advances only on successful projection of a single execution.
    """
    projector = PerformanceExecutionProjector()
    try:
        result = await projector.project_batch(limit=limit)
        return {
            "projector_name": result.projector_name,
            "processed": result.processed,
            "projected": result.projected,
            "duplicates": result.duplicates,
            "failed": result.failed,
        }
    except Exception as exc:
        raise HTTPException(status_code=500, detail=str(exc)) from exc


@router.get("/v1/performance/projector/status")
async def get_performance_projector_status(
    projector_name: str = Query("performance_execution_projector"),
) -> dict[str, Any]:
    """Query projector cursor position and recent audit records."""
    from trader.adapters.persistence.performance_repository import get_performance_repository

    repo = get_performance_repository()
    cursor = await repo.get_projection_cursor(projector_name)
    audits = await repo.list_projection_audits(projector_name=projector_name)
    recent_audits = audits[-10:] if len(audits) > 10 else audits
    return {
        "projector_name": projector_name,
        "cursor": cursor,
        "total_audits": len(audits),
        "recent_audits": [_serialize(a) for a in recent_audits],
    }


@router.get("/v1/performance/reports/{run_id}")
async def get_performance_report(run_id: str) -> dict[str, Any]:
    service = get_performance_service()
    nav_points = await service.list_all_nav(run_id=run_id)
    if not nav_points:
        raise HTTPException(status_code=404, detail=f"No performance NAV found for run_id={run_id}")
    explanation = await service.get_pnl_explain(run_id=run_id)
    snapshot = await service.get_latest_snapshot(run_id)
    attribution = await service.calculate_attribution(
        scope="run",
        group_by="symbol",
        run_id=run_id,
    )
    standard_returns = calculate_daily_returns(nav_points)
    return {
        "run_id": run_id,
        "quality": nav_points[-1].quality,
        "latest_nav": _serialize(nav_points[-1]),
        "snapshot": _serialize(snapshot),
        "standard_returns": _serialize(standard_returns),
        "empyrical_metrics": _serialize(calculate_empyrical_compatible_metrics(standard_returns)),
        "pnl_explain": _serialize(explanation),
        "attribution": _serialize(attribution),
        "tearsheet_available": get_artifact_storage().get_performance_tearsheet_path(run_id)
        is not None,
        "tearsheet_url": f"/v1/performance/reports/{run_id}/tearsheet",
        "pyfolio_status": {
            "available": False,
            "reason": "pyfolio-compatible tear sheets require benchmark/position inputs not yet available",
        },
        "report_engine": "internal_v2_quantstats_optional_empyrical_compatible",
    }


@router.get("/v1/performance/reports/{run_id}/tearsheet")
async def get_performance_tearsheet(run_id: str):
    """Download or generate a QuantStats HTML report from internal NAV points."""

    storage = get_artifact_storage()
    html_path = storage.get_performance_tearsheet_path(run_id)
    if html_path is None:
        service = get_performance_service()
        nav_points = await service.list_all_nav(run_id=run_id)
        if not nav_points:
            raise HTTPException(
                status_code=404,
                detail=f"No performance NAV found for run_id={run_id}",
            )
        run = await service.get_run(run_id)
        generated = generate_tearsheet(
            [
                {
                    "timestamp": point.timestamp_ms,
                    "equity": float(point.equity),
                }
                for point in nav_points
            ],
            run_id=run_id,
            strategy_name=run.strategy_id if run else "Strategy",
        )
        if generated is None:
            return JSONResponse(
                status_code=202,
                content={
                    "message": "Tearsheet not generated yet; NAV history may be too short or QuantStats unavailable.",
                    "run_id": run_id,
                },
            )
        storage.save_performance_tearsheet(run_id, generated)
        html_path = storage.get_performance_tearsheet_path(run_id)

    assert html_path is not None
    return FileResponse(
        path=str(html_path),
        media_type="text/html",
        filename=f"performance_tearsheet_{run_id[:8]}.html",
    )


# ============================================================================
# Benchmark Constituent Projector
# ============================================================================


@router.post("/v1/performance/benchmark/project")
async def project_benchmark(
    benchmark_id: str,
    source: str = Query("config", pattern="^(binance_ticker|config)$"),
    period_days: int = Query(1, ge=1, le=365),
    top_n: int = Query(20, ge=1, le=100),
    symbols: str | None = None,
) -> dict[str, Any]:
    """Project benchmark constituents from Binance or config.

    Args:
        benchmark_id: Unique benchmark identifier (e.g., "binance:top20" or "config:my_benchmark")
        source: "binance_ticker" to fetch from Binance API, "config" to use default symbols
        period_days: Number of days to look back for period (1-365)
        top_n: Number of top coins to include when fetching from Binance (1-100)
        symbols: Optional comma-separated list of symbols to filter (e.g., "BTCUSDT,ETHUSDT")

    Returns:
        benchmark_id, projected, duplicates, failed, period_start_ms, period_end_ms
    """
    period_start_ms, period_end_ms = compute_period_ms(period_days)

    projector = BenchmarkConstituentProjector()

    try:
        if source == "binance_ticker":
            result = await projector.project_benchmark(
                benchmark_id=benchmark_id,
                period_start_ms=period_start_ms,
                period_end_ms=period_end_ms,
                top_n=top_n,
                symbols=symbols,
            )
        else:
            # source == "config" - use default constituents from config
            # Default top 5 coins by quote volume for quick demo
            default_constituents = {
                "BTCUSDT": Decimal("1"),
                "ETHUSDT": Decimal("0.8"),
                "BNBUSDT": Decimal("0.5"),
                "XRPUSDT": Decimal("0.3"),
                "SOLUSDT": Decimal("0.2"),
            }
            result = await projector.project_from_config(
                benchmark_id=benchmark_id,
                constituents=default_constituents,
                period_start_ms=period_start_ms,
                period_end_ms=period_end_ms,
                period_return="0",
            )
    except Exception as exc:
        raise HTTPException(status_code=500, detail=str(exc)) from exc

    return {
        "benchmark_id": result.benchmark_id,
        "projected": result.projected,
        "duplicates": result.duplicates,
        "failed": result.failed,
        "period_start_ms": result.period_start_ms,
        "period_end_ms": result.period_end_ms,
    }


@router.post("/v1/performance/benchmark/config")
async def project_benchmark_from_config(
    benchmark_id: str,
    constituents: list[dict[str, Any]],
    period_days: int = Body(1, ge=1, le=365),
    period_return: str = Body("0"),
) -> dict[str, Any]:
    """Project benchmark from explicit symbol/weight config.

    Args:
        benchmark_id: Unique benchmark identifier
        constituents: List of {"symbol": "BTCUSDT", "weight": "0.5", "period_return": "0.1"}
        period_days: Number of days for the period (1-365)
        period_return: Default period return for all constituents

    Returns:
        benchmark_id, projected, duplicates, failed, period_start_ms, period_end_ms
    """
    period_start_ms, period_end_ms = compute_period_ms(period_days)

    # Convert list of dicts to dict[str, Decimal]
    constituents_dict: dict[str, Decimal] = {}
    for item in constituents:
        symbol = item.get("symbol", "")
        if not symbol:
            raise HTTPException(
                status_code=400,
                detail="Each constituent must have a non-empty 'symbol' field",
            )
        try:
            weight_str = str(item.get("weight", "0"))
            constituents_dict[symbol] = Decimal(weight_str)
        except DecimalException as exc:
            raise HTTPException(
                status_code=400,
                detail=f"Invalid weight value for symbol {symbol}: {exc}",
            ) from exc

    projector = BenchmarkConstituentProjector()
    try:
        result = await projector.project_from_config(
            benchmark_id=benchmark_id,
            constituents=constituents_dict,
            period_start_ms=period_start_ms,
            period_end_ms=period_end_ms,
            period_return=period_return,
        )
    except Exception as exc:
        raise HTTPException(status_code=500, detail=str(exc)) from exc

    return {
        "benchmark_id": result.benchmark_id,
        "projected": result.projected,
        "duplicates": result.duplicates,
        "failed": result.failed,
        "period_start_ms": result.period_start_ms,
        "period_end_ms": result.period_end_ms,
    }


@router.post("/v1/performance/initial-weights")
async def generate_initial_weights(
    benchmark_id: str,
    constituents: list[dict[str, Any]],
    period_start_ms: int,
    period_end_ms: int,
) -> dict[str, Any]:
    """Generate period-start portfolio holding facts from config.

    Args:
        benchmark_id: Strategy run identifier (used as run_id)
        constituents: List of {"symbol": "BTCUSDT", "weight": "0.5"}
        period_start_ms: Period start timestamp in milliseconds
        period_end_ms: Period end timestamp in milliseconds

    Returns:
        run_id, projected, duplicates, failed, period_start_ms, period_end_ms
    """
    # Convert list of dicts to dict[str, Decimal]
    constituents_dict: dict[str, Decimal] = {}
    for item in constituents:
        symbol = item.get("symbol", "")
        if not symbol:
            raise HTTPException(
                status_code=400,
                detail="Each constituent must have a non-empty 'symbol' field",
            )
        try:
            weight_str = str(item.get("weight", "0"))
            constituents_dict[symbol] = Decimal(weight_str)
        except DecimalException as exc:
            raise HTTPException(
                status_code=400,
                detail=f"Invalid weight value for symbol {symbol}: {exc}",
            ) from exc

    generator = InitialWeightGenerator()
    try:
        result = await generator.generate_from_config(
            run_id=benchmark_id,
            constituents=constituents_dict,
            period_start_ms=period_start_ms,
            period_end_ms=period_end_ms,
        )
    except Exception as exc:
        raise HTTPException(status_code=500, detail=str(exc)) from exc

    return {
        "benchmark_id": result.run_id,
        "projected": result.projected,
        "total": len(constituents),
        "duplicates": result.duplicates,
        "failed": result.failed,
        "period_start_ms": result.period_start_ms,
        "period_end_ms": result.period_end_ms,
    }


def _serialize(value: Any) -> Any:
    if isinstance(value, Decimal):
        return str(value)
    if is_dataclass(value):
        return _serialize(asdict(value))
    if isinstance(value, dict):
        return {key: _serialize(item) for key, item in value.items()}
    if isinstance(value, list):
        return [_serialize(item) for item in value]
    return value


def _parse_periods(value: str) -> list[tuple[int, int]]:
    periods: list[tuple[int, int]] = []
    for raw_period in value.split(","):
        raw_period = raw_period.strip()
        if not raw_period:
            continue
        parts = raw_period.split(":")
        if len(parts) != 2:
            raise ValueError("periods must use 'start:end,start:end' format")
        start_ms = int(parts[0])
        end_ms = int(parts[1])
        if end_ms <= start_ms:
            raise ValueError("period end must be greater than period start")
        periods.append((start_ms, end_ms))
    if not periods:
        raise ValueError("periods must contain at least one period")
    return periods
