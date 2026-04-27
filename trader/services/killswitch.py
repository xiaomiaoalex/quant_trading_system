import logging
from typing import Optional

from trader.storage.in_memory import get_storage, InMemoryStorage
from trader.api.models.schemas import (
    KillSwitchState, KillSwitchSetRequest,
)
from trader.adapters.persistence.killswitch_repository import (
    KillSwitchRepository,
    get_killswitch_repository,
)

logger = logging.getLogger(__name__)


class KillSwitchService:
    """Service for kill switch management"""

    def __init__(
        self,
        storage: Optional[InMemoryStorage] = None,
        repository: Optional[KillSwitchRepository] = None,
        require_durable_writes: bool = False,
    ):
        self._storage = storage or get_storage()
        self._repository = (
            repository
            or (KillSwitchRepository(self._storage) if storage is not None else get_killswitch_repository())
        )
        self._require_durable_writes = require_durable_writes

    def get_state(self, scope: str = "GLOBAL") -> KillSwitchState:
        """Get kill switch state"""
        state = self._repository.get_state(scope)
        return KillSwitchState(**state)

    def set_state(self, request: KillSwitchSetRequest) -> KillSwitchState:
        """Set kill switch level in memory for tests/dev paths."""
        if self._require_durable_writes:
            raise RuntimeError("Durable KillSwitch writes require set_state_durable()")
        previous_state = self.get_state(request.scope)
        level_names = {0: "NORMAL", 1: "NO_NEW_POSITIONS", 2: "CLOSE_ONLY", 3: "FULL_STOP"}

        logger.info(
            "[KillSwitch] [Service] Setting state: scope=%s level=%s (%s) reason=%s updated_by=%s",
            request.scope,
            request.level,
            level_names.get(request.level, f"LEVEL_{request.level}"),
            request.reason or "N/A",
            request.updated_by,
        )

        state = self._storage.set_kill_switch(
            request.scope, request.level, request.reason, request.updated_by
        )
        new_state = KillSwitchState(**state)

        logger.info(
            "[KillSwitch] [Service] State changed: scope=%s %s (%s) -> %s (%s) by %s",
            request.scope,
            previous_state.level,
            level_names.get(previous_state.level, f"LEVEL_{previous_state.level}"),
            new_state.level,
            level_names.get(new_state.level, f"LEVEL_{new_state.level}"),
            request.updated_by,
        )

        return new_state

    async def set_state_durable(self, request: KillSwitchSetRequest) -> KillSwitchState:
        """Persist KillSwitch state before returning."""
        previous_state = self.get_state(request.scope)
        level_names = {0: "NORMAL", 1: "NO_NEW_POSITIONS", 2: "CLOSE_ONLY", 3: "FULL_STOP"}

        logger.info(
            "[KillSwitch] [Service] Durably setting state: scope=%s level=%s (%s) reason=%s updated_by=%s",
            request.scope,
            request.level,
            level_names.get(request.level, f"LEVEL_{request.level}"),
            request.reason or "N/A",
            request.updated_by,
        )

        state = await self._repository.save_state(
            scope=request.scope,
            level=request.level,
            reason=request.reason,
            updated_by=request.updated_by,
            previous_level=previous_state.level,
        )
        new_state = KillSwitchState(**state)

        logger.info(
            "[KillSwitch] [Service] Durable state changed: scope=%s %s (%s) -> %s (%s) by %s",
            request.scope,
            previous_state.level,
            level_names.get(previous_state.level, f"LEVEL_{previous_state.level}"),
            new_state.level,
            level_names.get(new_state.level, f"LEVEL_{new_state.level}"),
            request.updated_by,
        )
        return new_state
