from __future__ import annotations

from fastapi import APIRouter, HTTPException, Path, Query

from trader.api.models.schemas import (
    ActionResult,
    BacktestRequest,
    StrategyCandidate,
    StrategyCandidateBacktestRequest,
    StrategyCandidateCreateRequest,
    StrategyCandidateDebugRequest,
    StrategyCandidatePromoteRequest,
    StrategyCodeCreateRequest,
    StrategyCodeDebugRequest,
)
from trader.services.deployment import BacktestService
from trader.services.strategy_candidate import StrategyCandidateService
from trader.storage.in_memory import get_storage

router = APIRouter(tags=["StrategyCandidates"])


@router.post("/v1/strategy-candidates", response_model=StrategyCandidate, status_code=201)
async def create_candidate(request: StrategyCandidateCreateRequest):
    return StrategyCandidateService().create_candidate(request)


@router.get("/v1/strategy-candidates", response_model=list[StrategyCandidate])
async def list_candidates(
    strategy_id: str | None = Query(None),
    status: str | None = Query(None),
    limit: int = Query(100, ge=1, le=500),
):
    return StrategyCandidateService().list_candidates(
        strategy_id=strategy_id, status=status, limit=limit
    )


@router.get("/v1/strategy-candidates/{candidate_id}", response_model=StrategyCandidate)
async def get_candidate(candidate_id: str = Path(...)):
    candidate = StrategyCandidateService().get_candidate(candidate_id)
    if candidate is None:
        raise HTTPException(status_code=404, detail=f"StrategyCandidate {candidate_id} not found")
    return candidate


@router.delete("/v1/strategy-candidates/{candidate_id}", response_model=ActionResult)
async def delete_candidate(candidate_id: str = Path(...)):
    try:
        deleted = StrategyCandidateService().delete_candidate(candidate_id)
    except KeyError:
        raise HTTPException(status_code=404, detail=f"StrategyCandidate {candidate_id} not found")
    except ValueError as exc:
        raise HTTPException(status_code=409, detail=str(exc))
    return ActionResult(
        ok=deleted,
        message=f"StrategyCandidate {candidate_id} deleted" if deleted else "No candidate deleted",
    )


@router.post("/v1/strategy-candidates/{candidate_id}/debug", response_model=StrategyCandidate)
async def debug_candidate(
    request: StrategyCandidateDebugRequest,
    candidate_id: str = Path(...),
):
    service = StrategyCandidateService()
    candidate = service.get_candidate(candidate_id)
    if candidate is None:
        raise HTTPException(status_code=404, detail=f"StrategyCandidate {candidate_id} not found")

    code = request.code or candidate.code
    if not code:
        raise HTTPException(status_code=422, detail="StrategyCandidate has no code to debug")

    from trader.api.routes.strategies import create_strategy_code, debug_strategy_code

    debug_result = await debug_strategy_code(
        StrategyCodeDebugRequest(
            strategy_id=candidate.strategy_id,
            code=code,
            config={**candidate.config, **request.config},
        )
    )
    if not debug_result.ok:
        get_storage().update_strategy_candidate(
            candidate_id,
            {
                "status": "REJECTED",
                "validation": {
                    "passed": False,
                    "failed_rules": ["debug_failed"],
                    "metrics": {},
                    "evidence_refs": {},
                },
            },
        )
        rejected = service.get_candidate(candidate_id)
        if rejected is None:
            raise HTTPException(
                status_code=404, detail=f"StrategyCandidate {candidate_id} not found"
            )
        return rejected

    code_entry = await create_strategy_code(
        StrategyCodeCreateRequest(
            strategy_id=candidate.strategy_id,
            name=candidate.name,
            description=candidate.description,
            code=code,
            created_by="strategy_candidate",
            notes=f"candidate_id={candidate_id}",
            register_if_missing=True,
        )
    )
    return service.mark_debug_passed(candidate_id, code_version=code_entry.code_version)


@router.post("/v1/strategy-candidates/{candidate_id}/backtests", response_model=StrategyCandidate)
async def run_candidate_backtest(
    request: StrategyCandidateBacktestRequest,
    candidate_id: str = Path(...),
):
    service = StrategyCandidateService()
    candidate = service.get_candidate(candidate_id)
    if candidate is None:
        raise HTTPException(status_code=404, detail=f"StrategyCandidate {candidate_id} not found")
    if candidate.code_version is None:
        raise HTTPException(status_code=409, detail="Candidate must pass debug and save code first")

    dataset = request.dataset
    try:
        backtest = BacktestService().create_backtest(
            BacktestRequest(
                strategy_id=candidate.strategy_id,
                version=1,
                strategy_code_version=candidate.code_version,
                params={
                    **candidate.config,
                    "initial_capital": dataset.initial_capital,
                    "fee_bps": dataset.fee_bps,
                    "slippage_bps": dataset.slippage_bps,
                    "benchmark": dataset.benchmark,
                },
                symbols=dataset.symbols,
                start_ts_ms=dataset.start_ts_ms,
                end_ts_ms=dataset.end_ts_ms,
                venue=dataset.venue,
                requested_by=request.requested_by,
                feature_version=dataset.feature_version,
                initial_capital=dataset.initial_capital,
                fee_bps=dataset.fee_bps,
                slippage_bps=dataset.slippage_bps,
                benchmark=dataset.benchmark,
                data_mode=dataset.data_mode,
            )
        )
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc))

    storage = get_storage()
    storage.update_strategy_candidate(
        candidate_id,
        {
            "dataset": dataset.model_dump(),
            "feature_version": dataset.feature_version,
        },
    )
    return service.mark_backtest_running(candidate_id, backtest.run_id)


@router.post("/v1/strategy-candidates/{candidate_id}/validate", response_model=StrategyCandidate)
async def validate_candidate(candidate_id: str = Path(...)):
    service = StrategyCandidateService()
    try:
        return service.validate_candidate(candidate_id)
    except KeyError:
        raise HTTPException(status_code=404, detail=f"StrategyCandidate {candidate_id} not found")
    except ValueError as exc:
        raise HTTPException(status_code=409, detail=str(exc))


@router.post("/v1/strategy-candidates/{candidate_id}/promote", response_model=StrategyCandidate)
async def promote_candidate(
    request: StrategyCandidatePromoteRequest,
    candidate_id: str = Path(...),
):
    service = StrategyCandidateService()
    candidate = service.get_candidate(candidate_id)
    if candidate is None:
        raise HTTPException(status_code=404, detail=f"StrategyCandidate {candidate_id} not found")
    if candidate.status != "VALIDATION_PASSED":
        raise HTTPException(
            status_code=409,
            detail="Candidate must be VALIDATION_PASSED before promote",
        )
    if request.mode == "live":
        raise HTTPException(
            status_code=409, detail="First release only promotes paper/shadow deployments"
        )

    deployment_id = request.deployment_id or (
        f"{candidate.strategy_id}__{request.symbols[0].lower()}__"
        f"{request.mode}__{request.account_id.lower()}"
    )

    from trader.api.routes.strategies import LoadStrategyRequest, load_strategy

    await load_strategy(
        candidate.strategy_id,
        LoadStrategyRequest(
            deployment_id=deployment_id,
            code_version=candidate.code_version,
            version=request.version,
            config={**candidate.config, **request.config},
            symbols=request.symbols,
            account_id=request.account_id,
            venue=request.venue,
            mode=request.mode,
        ),
    )
    return service.approve_for_paper(candidate_id, deployment_id)
