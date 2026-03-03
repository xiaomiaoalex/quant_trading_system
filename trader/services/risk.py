from typing import Optional, Dict, Any

from trader.storage.in_memory import get_storage, InMemoryStorage
from trader.api.models.schemas import (
    VersionedConfig, VersionedConfigUpsertRequest,
    RiskEventIngestRequest,
)
from trader.adapters.persistence.risk_repository import get_risk_event_repository


class RiskService:
    """Service for managing risk limits"""

    def __init__(self, storage: Optional[InMemoryStorage] = None):
        self._storage = storage or get_storage()
        self._risk_repo = get_risk_event_repository()

    def get_limits(self, scope: str = "GLOBAL") -> Optional[VersionedConfig]:
        """Get latest risk limits"""
        limits = self._storage.get_latest_risk_limits(scope)
        if limits:
            return VersionedConfig(**limits)
        return None

    def set_limits(self, request: VersionedConfigUpsertRequest) -> VersionedConfig:
        """Set new risk limits"""
        risk_data = request.model_dump()
        limits = self._storage.create_risk_limits(risk_data)
        return VersionedConfig(**limits)

    async def ingest_event(self, request: RiskEventIngestRequest) -> bool:
        """Ingest risk event and return whether it is newly created"""
        event_data = request.model_dump()
        _, created = await self._risk_repo.save_risk_event(event_data)
        return created

    async def get_upgrade_record(self, upgrade_key: str) -> Optional[Dict[str, Any]]:
        """Get upgrade record by key"""
        return await self._risk_repo.get_upgrade_record(upgrade_key)

    async def record_upgrade(self, upgrade_key: str, upgrade_data: Dict[str, Any]) -> None:
        """Record an upgrade action for idempotency"""
        await self._risk_repo.save_upgrade_record(upgrade_key, upgrade_data)
