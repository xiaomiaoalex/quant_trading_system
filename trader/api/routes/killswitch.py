"""
KillSwitch API Routes
=====================
Kill switch (emergency stop) management endpoints.
"""
from typing import Optional
from fastapi import APIRouter, Query

from trader.api.models.schemas import KillSwitchState, KillSwitchSetRequest
from trader.services import KillSwitchService

router = APIRouter(tags=["KillSwitch"])


@router.get("/v1/killswitch", response_model=KillSwitchState)
async def get_kill_switch_state(scope: str = Query("GLOBAL", description="Scope: GLOBAL or per account")):
    """
    Get kill switch state.

    Returns the current kill switch state for the specified scope.
    """
    service = KillSwitchService()
    return service.get_state(scope)


@router.post("/v1/killswitch", response_model=KillSwitchState)
async def set_kill_switch(request: KillSwitchSetRequest):
    """
    Set kill switch level.

    Sets the kill switch level (0-3) for emergency control.
    - Level 0: Normal operation
    - Level 1: No new positions
    - Level 2: Close positions only
    - Level 3: Full stop
    """
    service = KillSwitchService()
    return service.set_state(request)
