from __future__ import annotations

from trader.api.models.schemas import (
    AllocationTrace,
    AllocationTraceCreateRequest,
    StrategyAllocationProfile,
    StrategyAllocationProfileUpdateRequest,
)
from trader.storage.in_memory import ControlPlaneInMemoryStorage, get_storage


class AllocationManagementService:
    """Control-plane facade for per-deployment allocation profiles and traces."""

    def __init__(self, storage: ControlPlaneInMemoryStorage | None = None):
        self._storage = storage or get_storage()

    def list_profiles(self) -> list[StrategyAllocationProfile]:
        return [StrategyAllocationProfile(**item) for item in self._storage.list_allocation_profiles()]

    def upsert_profile(
        self, deployment_id: str, request: StrategyAllocationProfileUpdateRequest
    ) -> StrategyAllocationProfile:
        profile = self._storage.upsert_allocation_profile(deployment_id, request.model_dump())
        return StrategyAllocationProfile(**profile)

    def append_trace(
        self, deployment_id: str, request: AllocationTraceCreateRequest
    ) -> AllocationTrace:
        trace = self._storage.append_allocation_trace(deployment_id, request.model_dump())
        return AllocationTrace(**trace)

    def list_traces(self, deployment_id: str, limit: int = 100) -> list[AllocationTrace]:
        return [
            AllocationTrace(**item)
            for item in self._storage.list_allocation_traces(deployment_id, limit=limit)
        ]
