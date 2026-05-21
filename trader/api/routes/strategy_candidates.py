from __future__ import annotations

from fastapi import APIRouter, HTTPException, Path, Query

from trader.api.models.schemas import (
    ActionResult,
    BacktestRequest,
    PromotePaperResponse,
    StrategyCandidate,
    StrategyCandidateBacktestRequest,
    StrategyCandidateCreateRequest,
    StrategyCandidateDebugRequest,
    StrategyCandidateDebugResponse,
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


@router.post(
    "/v1/strategy-candidates/{candidate_id}/debug", response_model=StrategyCandidateDebugResponse
)
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
        # Debug 失败回到 DRAFT 状态，清空 code_version 防止旧版本被回测
        # 记录 debug_errors 供前端展示，不进入终态 REJECTED
        get_storage().update_strategy_candidate(
            candidate_id,
            {
                "status": "DRAFT",
                "code_version": None,
                "debug_errors": debug_result.errors,
                "debug_warnings": debug_result.warnings,
            },
        )
        updated = service.get_candidate(candidate_id)
        return StrategyCandidateDebugResponse(
            ok=False,
            syntax_ok=debug_result.syntax_ok,
            protocol_ok=debug_result.protocol_ok,
            checksum=debug_result.checksum,
            errors=debug_result.errors,
            warnings=debug_result.warnings,
            candidate=updated,
        )

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
    updated_candidate = service.mark_debug_passed(
        candidate_id, code_version=code_entry.code_version
    )
    return StrategyCandidateDebugResponse(
        ok=True,
        syntax_ok=True,
        protocol_ok=True,
        validation_status=debug_result.validation_status,
        checksum=debug_result.checksum,
        signals=debug_result.signals,
        errors=[],
        warnings=debug_result.warnings,
        candidate=updated_candidate,
    )


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
                risk_mode=dataset.risk_mode,
                candidate_id=candidate_id,
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


@router.post(
    "/v1/strategy-candidates/{candidate_id}/promote-paper",
    response_model=PromotePaperResponse,
    summary="原子 promote：VALIDATION_PASSED -> APPROVED_FOR_PAPER（含 runtime 加载）",
)
async def promote_candidate_to_paper(candidate_id: str = Path(...)):
    """
    原子编排接口。内部顺序固定：
    检查状态 -> 保存代码版本 -> 注册策略 -> 创建 deployment -> load strategy -> approve paper。

    失败时触发补偿回滚（运行态原子）：unload runtime -> 清理 deployment -> candidate 回 VALIDATION_PASSED。
    """
    return await StrategyCandidateService().promote_to_paper(candidate_id)


@router.post(
    "/v1/strategy-candidates/{candidate_id}/promote",
    deprecated=True,
    summary="[已废弃] 请使用 /promote-paper",
    include_in_schema=True,
)
async def promote_candidate_deprecated(
    request: StrategyCandidatePromoteRequest,
    candidate_id: str = Path(...),
):
    """旧 promote 接口已废弃。不再具备运行态原子、并发保护、回滚语义。

    请使用 POST /v1/strategy-candidates/{candidate_id}/promote-paper。
    """
    raise HTTPException(
        status_code=410,
        detail=(
            "This endpoint is deprecated and has been replaced by the atomic promote-paper interface. "
            f"Use: POST /v1/strategy-candidates/{candidate_id}/promote-paper"
        ),
    )
