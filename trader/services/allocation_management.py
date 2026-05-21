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
        return [
            StrategyAllocationProfile(**item) for item in self._storage.list_allocation_profiles()
        ]

    def get_profile(self, deployment_id: str) -> StrategyAllocationProfile | None:
        profile = self._storage.get_allocation_profile(deployment_id)
        if profile is None:
            return None
        return StrategyAllocationProfile(**profile)

    def upsert_profile(
        self, deployment_id: str, request: StrategyAllocationProfileUpdateRequest
    ) -> StrategyAllocationProfile:
        profile = self._storage.upsert_allocation_profile(deployment_id, request.model_dump())
        return StrategyAllocationProfile(**profile)

    def add_runtime_notional(
        self, deployment_id: str, delta_notional: float
    ) -> StrategyAllocationProfile | None:
        profile = self._storage.get_allocation_profile(deployment_id)
        if profile is None:
            return None
        current_notional = float(profile.get("current_notional", 0.0))
        updated = {
            **profile,
            "current_notional": max(0.0, current_notional + float(delta_notional)),
        }
        return StrategyAllocationProfile(
            **self._storage.upsert_allocation_profile(deployment_id, updated)
        )

    def append_trace(
        self, deployment_id: str, request: AllocationTraceCreateRequest
    ) -> AllocationTrace:
        trace = self._storage.append_allocation_trace(deployment_id, request.model_dump())
        return AllocationTrace(**trace)

    def append_trace_data(self, deployment_id: str, trace_data: dict) -> AllocationTrace:
        trace = self._storage.append_allocation_trace(deployment_id, trace_data)
        return AllocationTrace(**trace)

    def list_traces(self, deployment_id: str, limit: int = 100) -> list[AllocationTrace]:
        return [
            AllocationTrace(**item)
            for item in self._storage.list_allocation_traces(deployment_id, limit=limit)
        ]
