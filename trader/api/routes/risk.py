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
    
    闭环逻辑：
    1. 存储事件（dedup_key 幂等）
    2. 若 recommended_level > 当前级别，执行升级
    3. 升级使用 dedup_key 防止重复触发
    
    - Returns 201 when a new dedup_key is accepted.
    - Returns 409 when dedup_key already exists (idempotent duplicate).
    """
    service = RiskService()
    killswitch_service = KillSwitchService()
    
    created = service.ingest_event(request)
    
    if created:
        current_state = killswitch_service.get_state(request.scope)
        current_level = current_state.level
        
        if request.recommended_level > current_level:
            upgrade_key = f"upgrade:{request.scope}:{request.recommended_level}:{request.dedup_key}"
            existing_upgrade = service.get_upgrade_record(upgrade_key)
            
            if existing_upgrade is None:
                killswitch_service.set_state(
                    type('KillSwitchSetRequest', (), {
                        'scope': request.scope,
                        'level': request.recommended_level,
                        'reason': f"Risk event upgrade: {request.reason}",
                        'updated_by': f"risk_event:{request.dedup_key}"
                    })()
                )
                service.record_upgrade(upgrade_key, {
                    "scope": request.scope,
                    "level": request.recommended_level,
                    "reason": request.reason,
                    "dedup_key": request.dedup_key,
                })
    
    if created:
        response.status_code = 201
        return ActionResult(ok=True, message="risk event accepted")

    response.status_code = 409
    return ActionResult(ok=True, message="risk event duplicate")
