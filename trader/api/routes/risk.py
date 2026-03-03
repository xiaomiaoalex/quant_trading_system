"""
Risk API Routes
==============
Risk limits management endpoints (versioned).

级别映射矩阵 (Level Mapping Matrix):
======================================
| RejectionReason         | recommended_level | 外部动作 (KillSwitch) |
|-------------------------|------------------|----------------------|
| LOW_RISK               | 0 (L0_NORMAL)    | 无动作               |
| MINOR_STREAM_LAG       | 1 (L1_NO_NEW_POS)| 禁止新开仓           |
| MAJOR_STREAM_LAG       | 1 (L1_NO_NEW_POS)| 禁止新开仓           |
| PRIVATE_STREAM_DOWN    | 2 (L2_CLOSE_ONLY)| 只允许平仓           |
| RATE_LIMIT_EXCEEDED    | 2 (L2_CLOSE_ONLY)| 只允许平仓           |
| CRITICAL_FAILURE       | 3 (L3_FULL_STOP) | 完全停止             |
| CONNECTION_LOST        | 3 (L3_FULL_STOP) | 完全停止             |

升降级规则：
- recommended_level > 当前级别：执行升级
- recommended_level <= 当前级别：保持不降级（Fail-Closed）
- 同一 dedup_key 不重复触发升级（幂等保护）
"""
from typing import Optional
from fastapi import APIRouter, Query, Response

from trader.api.models.schemas import (
    VersionedConfig,
    VersionedConfigUpsertRequest,
    RiskEventIngestRequest,
    ActionResult,
    KillSwitchSetRequest,
)
from trader.services import RiskService, KillSwitchService

router = APIRouter(tags=["Risk"])


@router.get("/v1/risk/limits", response_model=Optional[VersionedConfig])
async def get_risk_limits(scope: str = Query("GLOBAL", description="Risk scope: GLOBAL or per account/strategy")):
    """
    Get latest risk limits.

    Returns the latest risk limits for the specified scope.
    """
    service = RiskService()
    return service.get_limits(scope)


@router.post("/v1/risk/limits", response_model=VersionedConfig)
async def set_risk_limits(request: VersionedConfigUpsertRequest):
    """
    Set new risk limits.

    Creates a new version of risk limits.
    """
    service = RiskService()
    return service.set_limits(request)


@router.post("/v1/risk/events", response_model=ActionResult)
async def ingest_risk_event(request: RiskEventIngestRequest, response: Response):
    """
    Ingest risk event with recommended_level-driven KillSwitch upgrade.
    
    闭环逻辑（原子事务）：
    BEGIN -> dedup -> upgrade record -> side-effect intent -> COMMIT
    
    - Returns 201 when a new dedup_key is accepted.
    - Returns 409 when dedup_key already exists (idempotent duplicate).
    - Returns 500 when side-effect fails (Fail-Closed).
    """
    service = RiskService()
    killswitch_service = KillSwitchService()
    
    current_state = killswitch_service.get_state(request.scope)
    current_level = current_state.level
    
    if request.recommended_level > current_level:
        upgrade_key = f"upgrade:{request.scope}:{request.recommended_level}:{request.dedup_key}"
        
        event_data = request.model_dump()
        event_id, created, is_first_upgrade, is_first_effect = await service.ingest_event_with_upgrade(
            event_data, upgrade_key, request.recommended_level
        )
        
        if is_first_effect:
            try:
                killswitch_service.set_state(
                    KillSwitchSetRequest(
                        scope=request.scope,
                        level=request.recommended_level,
                        reason=f"Risk event upgrade: {request.reason}",
                        updated_by=f"risk_event:{request.dedup_key}"
                    )
                )
                await service.mark_effect_applied(upgrade_key)
            except Exception as e:
                await service.mark_effect_failed(upgrade_key, str(e))
                response.status_code = 500
                return ActionResult(ok=False, message=f"upgrade failed: {str(e)}")
        
        if created:
            response.status_code = 201
            return ActionResult(ok=True, message="risk event accepted")
        
        response.status_code = 409
        return ActionResult(ok=True, message="risk event duplicate")
    else:
        created = await service.ingest_event(request)
        
        if created:
            response.status_code = 201
            return ActionResult(ok=True, message="risk event accepted")
        
        response.status_code = 409
        return ActionResult(ok=True, message="risk event duplicate")


@router.post("/v1/risk/recover", response_model=ActionResult)
async def recover_pending_effects():
    """
    Recovery endpoint: scan and retry pending/failed effects.
    
    This is a manual trigger for recovery after failures.
    """
    service = RiskService()
    killswitch_service = KillSwitchService()
    
    try:
        pending = await service.get_pending_effects()
    except Exception as e:
        return ActionResult(ok=False, message=f"获取待恢复效果失败: {str(e)}")
    
    if not pending:
        return ActionResult(ok=True, message="无待恢复效果")
    
    recovered = 0
    failed = 0
    
    for effect in pending:
        upgrade_key = effect["upgrade_key"]
        try:
            level = effect.get("level", 1)
            killswitch_service.set_state(
                KillSwitchSetRequest(
                    scope=effect.get("scope", "GLOBAL"),
                    level=level,
                    reason=f"Recovery: {effect.get('last_error', 'unknown')}",
                    updated_by="recovery_trigger"
                )
            )
            await service.mark_effect_applied(upgrade_key)
            recovered += 1
        except Exception as e:
            await service.mark_effect_failed(upgrade_key, f"Recovery failed: {str(e)}")
            failed += 1
    
    return ActionResult(
        ok=True, 
        message=f"recovery completed: {recovered} recovered, {failed} failed"
    )
