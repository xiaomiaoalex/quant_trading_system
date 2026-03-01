from typing import Optional

from trader.storage.in_memory import get_storage, InMemoryStorage
from trader.api.models.schemas import (
    KillSwitchState, KillSwitchSetRequest,
)


class KillSwitchService:
    """Service for kill switch management"""

    def __init__(self, storage: Optional[InMemoryStorage] = None):
        self._storage = storage or get_storage()

    def get_state(self, scope: str = "GLOBAL") -> KillSwitchState:
        """Get kill switch state"""
        state = self._storage.get_kill_switch(scope)
        return KillSwitchState(**state)

    def set_state(self, request: KillSwitchSetRequest) -> KillSwitchState:
        """Set kill switch level"""
        state = self._storage.set_kill_switch(
            request.scope, request.level, request.reason, request.updated_by
        )
        return KillSwitchState(**state)
