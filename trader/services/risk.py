from typing import Optional, Dict, Any

from trader.storage.in_memory import get_storage, InMemoryStorage
from trader.api.models.schemas import (
    VersionedConfig, VersionedConfigUpsertRequest,
    RiskEventIngestRequest,
)


class RiskService:
    """Service for managing risk limits"""

    def __init__(self, storage: Optional[InMemoryStorage] = None):
        self._storage = storage or get_storage()

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

    def ingest_event(self, request: RiskEventIngestRequest) -> bool:
        """Ingest risk event and return whether it is newly created"""
        event_data = request.model_dump()
        _, created = self._storage.ingest_risk_event(event_data)
        return created
