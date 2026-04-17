from typing import Optional, Dict, Any, Tuple, List

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

    async def try_record_upgrade(self, upgrade_key: str, upgrade_data: Dict[str, Any]) -> bool:
        """
        Try to record an upgrade action. Returns True if first write, False if already exists.
        
        Args:
            upgrade_key: Unique upgrade key
            upgrade_data: Dictionary containing:
                - scope: Risk scope
                - level: Target level
                - reason: Upgrade reason
                - dedup_key: Related dedup key
                
        Returns:
            True if this is the first time recording this upgrade_key, False if already exists
        """
        return await self._risk_repo.try_record_upgrade(upgrade_key, upgrade_data)

    async def try_record_upgrade_with_effect(self, upgrade_key: str, scope: str, level: int,
                                             reason: str, dedup_key: str) -> Tuple[bool, bool]:
        """
        Atomically record upgrade and side-effect intent.
        
        Returns:
            Tuple of (is_first_upgrade, is_first_effect)
        """
        return await self._risk_repo.try_record_upgrade_with_effect(
            upgrade_key, scope, level, reason, dedup_key
        )

    async def mark_effect_applied(self, upgrade_key: str) -> None:
        """Mark side-effect as successfully applied"""
        await self._risk_repo.mark_effect_applied(upgrade_key)

    async def mark_effect_failed(self, upgrade_key: str, error: str) -> None:
        """Mark side-effect as failed"""
        await self._risk_repo.mark_effect_failed(upgrade_key, error)

    async def get_pending_effects(self) -> List[Dict[str, Any]]:
        """Get all pending or failed effects for recovery"""
        return await self._risk_repo.get_pending_effects()

    async def ingest_event_with_upgrade(self, event_data: Dict[str, Any], 
                                       upgrade_key: str, upgrade_level: int) -> Tuple[Optional[str], bool, bool, bool]:
        """
        Atomically ingest risk event and record upgrade with effect.
        
        Returns:
            Tuple of (event_id, created, is_first_upgrade, is_first_effect)
        """
        return await self._risk_repo.ingest_event_with_upgrade(event_data, upgrade_key, upgrade_level)
