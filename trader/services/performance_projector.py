"""Recover performance ledger projections from durable execution facts."""

from __future__ import annotations

import asyncio
import hashlib
import logging
import uuid
from dataclasses import dataclass
from decimal import Decimal
from typing import Any

from trader.adapters.persistence.execution_repository import (
    ExecutionRepository,
    get_execution_repository,
)
from trader.adapters.persistence.performance_repository import (
    PerformanceRepository,
    get_performance_repository,
)
from trader.core.domain.models.performance import StrategyRun
from trader.services.performance import PerformanceService, get_performance_service

logger = logging.getLogger(__name__)


@dataclass(slots=True)
class PerformanceProjectionResult:
    projector_name: str
    processed: int = 0
    projected: int = 0
    duplicates: int = 0
    failed: int = 0


@dataclass(slots=True)
class PerformanceFactsBackfillResult:
    """Result summary for attribution fact backfill."""

    backfill_name: str
    processed: int = 0
    projected: int = 0
    failed: int = 0
    errors: list[str] | None = None


class PerformanceAttributionFactsBackfill:
    """Backfill benchmark, factor, and risk-budget facts into Performance storage."""

    def __init__(
        self,
        *,
        performance_service: PerformanceService | None = None,
        backfill_name: str = "performance_attribution_facts_backfill",
    ) -> None:
        self._performance_service = performance_service or get_performance_service()
        self._backfill_name = backfill_name

    async def backfill(self, payload: dict[str, Any]) -> PerformanceFactsBackfillResult:
        result = PerformanceFactsBackfillResult(
            backfill_name=self._backfill_name,
            errors=[],
        )
        steps = [
            ("asset_classifications", self._record_asset_classification),
            ("benchmark_holdings", self._record_benchmark_holding),
            ("portfolio_holdings", self._record_portfolio_holding),
            ("factor_exposures", self._record_factor_exposure),
            ("factor_returns", self._record_factor_return),
            ("risk_budget_traces", self._record_risk_budget_trace),
        ]
        for key, recorder in steps:
            for raw_item in payload.get(key) or []:
                result.processed += 1
                try:
                    await recorder(dict(raw_item))
                    result.projected += 1
                except Exception as exc:
                    result.failed += 1
                    assert result.errors is not None
                    result.errors.append(f"{key}: {exc}")
        return result

    async def _record_asset_classification(self, item: dict[str, Any]) -> None:
        await self._performance_service.record_asset_classification(
            symbol=self._required_text(item, "symbol"),
            asset_class=self._required_text(item, "asset_class"),
            effective_from_ms=self._int(item.get("effective_from_ms")),
            effective_to_ms=(
                self._int(item.get("effective_to_ms"))
                if item.get("effective_to_ms") is not None
                else None
            ),
            classification_id=item.get("classification_id")
            or self._stable_id(
                "asset_classification",
                item.get("symbol"),
                item.get("asset_class"),
                item.get("effective_from_ms"),
            ),
            source=str(item.get("source") or self._backfill_name),
            quality=str(item.get("quality") or "complete"),
            metadata=self._metadata(item),
        )

    async def _record_benchmark_holding(self, item: dict[str, Any]) -> None:
        await self._performance_service.record_benchmark_holding(
            benchmark_id=self._required_text(item, "benchmark_id"),
            symbol=self._required_text(item, "symbol"),
            period_start_ms=self._int(item.get("period_start_ms")),
            period_end_ms=self._int(item.get("period_end_ms")),
            weight=self._decimal(item.get("weight")),
            period_return=self._decimal(item.get("period_return")),
            holding_id=item.get("holding_id")
            or self._stable_id(
                "benchmark_holding",
                item.get("benchmark_id"),
                item.get("symbol"),
                item.get("period_start_ms"),
                item.get("period_end_ms"),
            ),
            source=str(item.get("source") or self._backfill_name),
            quality=str(item.get("quality") or "complete"),
            metadata=self._metadata(item),
        )

    async def _record_portfolio_holding(self, item: dict[str, Any]) -> None:
        await self._performance_service.record_portfolio_holding_fact(
            run_id=self._required_text(item, "run_id"),
            symbol=self._required_text(item, "symbol"),
            period_start_ms=self._int(item.get("period_start_ms")),
            period_end_ms=self._int(item.get("period_end_ms")),
            weight=self._decimal(item.get("weight")),
            period_return=self._decimal(item.get("period_return")),
            holding_id=item.get("holding_id")
            or self._stable_id(
                "portfolio_holding",
                item.get("run_id"),
                item.get("symbol"),
                item.get("period_start_ms"),
                item.get("period_end_ms"),
            ),
            source=str(item.get("source") or self._backfill_name),
            quality=str(item.get("quality") or "complete"),
            metadata=self._metadata(item),
        )

    async def _record_factor_exposure(self, item: dict[str, Any]) -> None:
        await self._performance_service.record_factor_exposure(
            run_id=self._required_text(item, "run_id"),
            symbol=self._required_text(item, "symbol"),
            factor_name=self._required_text(item, "factor_name"),
            period_start_ms=self._int(item.get("period_start_ms")),
            period_end_ms=self._int(item.get("period_end_ms")),
            exposure=self._decimal(item.get("exposure")),
            weight=self._decimal(item.get("weight")),
            exposure_id=item.get("exposure_id")
            or self._stable_id(
                "factor_exposure",
                item.get("run_id"),
                item.get("symbol"),
                item.get("factor_name"),
                item.get("period_start_ms"),
                item.get("period_end_ms"),
            ),
            source=str(item.get("source") or self._backfill_name),
            quality=str(item.get("quality") or "complete"),
            metadata=self._metadata(item),
        )

    async def _record_factor_return(self, item: dict[str, Any]) -> None:
        await self._performance_service.record_factor_return(
            factor_name=self._required_text(item, "factor_name"),
            period_start_ms=self._int(item.get("period_start_ms")),
            period_end_ms=self._int(item.get("period_end_ms")),
            period_return=self._decimal(item.get("period_return")),
            return_id=item.get("return_id")
            or self._stable_id(
                "factor_return",
                item.get("factor_name"),
                item.get("period_start_ms"),
                item.get("period_end_ms"),
            ),
            source=str(item.get("source") or self._backfill_name),
            quality=str(item.get("quality") or "complete"),
            metadata=self._metadata(item),
        )

    async def _record_risk_budget_trace(self, item: dict[str, Any]) -> None:
        await self._performance_service.record_risk_budget_trace(
            run_id=self._required_text(item, "run_id"),
            period_start_ms=self._int(item.get("period_start_ms")),
            period_end_ms=self._int(item.get("period_end_ms")),
            constraint_type=self._required_text(item, "constraint_type"),
            requested_notional=self._decimal(item.get("requested_notional")),
            allowed_notional=self._decimal(item.get("allowed_notional")),
            final_notional=self._decimal(item.get("final_notional")),
            decision=self._required_text(item, "decision"),
            trace_id=item.get("trace_id")
            or self._stable_id(
                "risk_budget_trace",
                item.get("run_id"),
                item.get("constraint_type"),
                item.get("period_start_ms"),
                item.get("period_end_ms"),
                item.get("requested_notional"),
                item.get("final_notional"),
            ),
            source=str(item.get("source") or self._backfill_name),
            quality=str(item.get("quality") or "complete"),
            metadata=self._metadata(item),
        )

    def _required_text(self, item: dict[str, Any], field: str) -> str:
        value = str(item.get(field) or "").strip()
        if not value:
            raise ValueError(f"Fact missing required field: {field}")
        return value

    def _decimal(self, value: Any) -> Decimal:
        return Decimal(str(value or "0"))

    def _int(self, value: Any) -> int:
        return int(value or 0)

    def _metadata(self, item: dict[str, Any]) -> dict[str, Any]:
        metadata = item.get("metadata")
        return metadata if isinstance(metadata, dict) else {}

    def _stable_id(self, prefix: str, *parts: Any) -> str:
        raw = "|".join(str(part or "") for part in parts)
        digest = hashlib.sha256(raw.encode("utf-8")).hexdigest()[:24]
        return f"{prefix}:{digest}"


class PerformanceExecutionProjector:
    """Project execution facts into the performance ledger."""

    def __init__(
        self,
        *,
        execution_repository: ExecutionRepository | None = None,
        performance_service: PerformanceService | None = None,
        performance_repository: PerformanceRepository | None = None,
        projector_name: str = "performance_execution_projector",
    ) -> None:
        self._execution_repository = execution_repository or get_execution_repository()
        self._performance_service = performance_service or get_performance_service()
        self._performance_repository = performance_repository or get_performance_repository()
        self._projector_name = projector_name

    async def project_batch(self, *, limit: int = 100) -> PerformanceProjectionResult:
        result = PerformanceProjectionResult(projector_name=self._projector_name)
        cursor = await self._performance_repository.get_projection_cursor(self._projector_name)
        executions = await self._execution_repository.list_executions_for_projection(
            after_ts_ms=cursor["last_ts_ms"] if cursor else None,
            after_execution_id=cursor["last_execution_id"] if cursor else None,
            limit=limit,
        )
        for execution in executions:
            result.processed += 1
            execution_id = str(execution.get("execution_id") or "")
            run_id = self._resolve_run_id(execution)
            attempt_count = await self._next_attempt_count(execution_id)
            try:
                created = await self._project_execution(execution, run_id=run_id)
            except Exception as exc:
                result.failed += 1
                await self._save_audit(
                    execution_id=execution_id,
                    run_id=run_id,
                    status="FAILED",
                    attempt_count=attempt_count,
                    last_error=str(exc),
                )
                break

            status = "PROJECTED" if created else "DUPLICATE"
            if created:
                result.projected += 1
            else:
                result.duplicates += 1
            await self._save_audit(
                execution_id=execution_id,
                run_id=run_id,
                status=status,
                attempt_count=attempt_count,
            )
            await self._performance_repository.save_projection_cursor(
                projector_name=self._projector_name,
                last_ts_ms=int(execution.get("ts_ms") or 0),
                last_execution_id=execution_id,
            )
        return result

    async def _project_execution(self, execution: dict[str, Any], *, run_id: str) -> bool:
        cl_ord_id = self._required_text(execution, "cl_ord_id")
        exec_id = self._required_text(execution, "exec_id")
        symbol = self._required_text(execution, "symbol")
        side = self._required_text(execution, "side")
        deployment_id = str(execution.get("deployment_id") or execution.get("strategy_id") or "")
        strategy_id = str(execution.get("strategy_id") or deployment_id)
        account_id = str(execution.get("account_id") or "unknown")
        venue = str(execution.get("venue") or "unknown")
        context_complete = all(
            execution.get(field) for field in ("run_id", "deployment_id", "account_id")
        )
        run = await self._performance_service.get_run(run_id)
        if run is None:
            run = await self._performance_service.create_run(
                StrategyRun(
                    run_id=run_id,
                    deployment_id=deployment_id,
                    strategy_id=strategy_id,
                    account_id=account_id,
                    venue=venue,
                    initial_capital=Decimal("0"),
                    started_at_ms=int(execution.get("ts_ms") or 0),
                    quality="partial",
                    metadata={
                        "source": "performance_execution_projector",
                        "initial_capital_source": "source_unavailable",
                        "execution_context_complete": context_complete,
                    },
                )
            )
        quality = "complete" if context_complete and run.quality == "complete" else "partial"
        _, created = await self._performance_service.record_fill(
            run_id=run_id,
            cl_ord_id=cl_ord_id,
            exec_id=exec_id,
            symbol=symbol,
            side=side,
            qty=self._decimal(execution.get("quantity") or execution.get("fill_qty")),
            price=self._decimal(execution.get("price") or execution.get("fill_price")),
            fee=self._decimal(execution.get("fee")),
            fee_currency=execution.get("fee_currency"),
            ts_ms=int(execution.get("ts_ms") or 0),
            mark_prices={
                symbol: self._decimal(execution.get("price") or execution.get("fill_price"))
            },
            source="performance_execution_projector",
            quality=quality,
            metadata={
                "source": "performance_execution_projector",
                "execution_id": execution.get("execution_id"),
                "fee_source": (
                    "exchange_commission"
                    if execution.get("fee") is not None
                    else "source_unavailable"
                ),
                "slippage_source": "source_unavailable",
                "funding_source": "not_applicable_spot",
            },
        )
        return created

    async def _next_attempt_count(self, execution_id: str) -> int:
        audits = await self._performance_repository.list_projection_audits(
            projector_name=self._projector_name,
            execution_id=execution_id,
        )
        return len(audits) + 1

    async def _save_audit(
        self,
        *,
        execution_id: str,
        run_id: str,
        status: str,
        attempt_count: int,
        last_error: str | None = None,
    ) -> None:
        await self._performance_repository.save_projection_audit(
            {
                "audit_id": str(uuid.uuid4()),
                "projector_name": self._projector_name,
                "execution_id": execution_id,
                "run_id": run_id,
                "status": status,
                "attempt_count": attempt_count,
                "last_error": last_error,
            }
        )

    def _resolve_run_id(self, execution: dict[str, Any]) -> str:
        deployment_id = execution.get("deployment_id") or execution.get("strategy_id") or "unknown"
        return str(execution.get("run_id") or f"run:{deployment_id}")

    def _required_text(self, execution: dict[str, Any], field: str) -> str:
        value = str(execution.get(field) or "").strip()
        if not value:
            raise ValueError(f"Execution missing required field: {field}")
        return value

    def _decimal(self, value: Any) -> Decimal:
        return Decimal(str(value or "0"))


class PerformanceProjectorWorker:
    """Periodically recover missing performance projections."""

    def __init__(
        self,
        projector: PerformanceExecutionProjector | None = None,
        *,
        interval_seconds: float = 5.0,
        batch_size: int = 100,
    ) -> None:
        self._projector = projector or PerformanceExecutionProjector()
        self._interval_seconds = interval_seconds
        self._batch_size = batch_size
        self._task: asyncio.Task[None] | None = None
        self._stop_event = asyncio.Event()

    async def start(self) -> None:
        if self._task is not None and not self._task.done():
            return
        self._stop_event.clear()
        self._task = asyncio.create_task(self._run(), name="performance-execution-projector")

    async def stop(self) -> None:
        self._stop_event.set()
        if self._task is not None:
            await self._task
        self._task = None

    async def _run(self) -> None:
        while not self._stop_event.is_set():
            try:
                result = await self._projector.project_batch(limit=self._batch_size)
                if result.failed:
                    logger.warning(
                        "Performance execution projector batch failed: processed=%s failed=%s",
                        result.processed,
                        result.failed,
                    )
            except Exception:
                logger.exception("Performance execution projector worker iteration failed")
            try:
                await asyncio.wait_for(self._stop_event.wait(), timeout=self._interval_seconds)
            except TimeoutError:
                continue
