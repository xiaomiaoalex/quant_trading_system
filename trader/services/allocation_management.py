from __future__ import annotations

from datetime import datetime, timezone
from typing import Any

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
        old_profile = self._storage.get_allocation_profile(deployment_id)
        profile_data = self._build_profile_data(deployment_id, request)
        profile = self._storage.upsert_allocation_profile(deployment_id, profile_data)
        self._append_profile_update_event(
            deployment_id=deployment_id,
            old_profile=old_profile,
            new_profile=profile,
            updated_by=request.updated_by or "api",
        )
        return StrategyAllocationProfile(**profile)

    def _build_profile_data(
        self, deployment_id: str, request: StrategyAllocationProfileUpdateRequest
    ) -> dict[str, Any]:
        data = request.model_dump()
        allocation_mode = request.allocation_mode
        basis_nav: float | None = None

        if allocation_mode == "ABSOLUTE_NOTIONAL":
            if request.max_notional is None:
                raise ValueError("max_notional is required for ABSOLUTE_NOTIONAL allocation")
            configured_notional = float(request.max_notional)
            effective_max_notional = configured_notional
        else:
            if request.target_weight is None:
                raise ValueError("target_weight is required for PERCENT_OF_NAV allocation")
            basis_nav = self._resolve_basis_nav(
                deployment_id=deployment_id,
                nav_source=request.nav_source,
                manual_nav=request.manual_nav,
            )
            if basis_nav is None or basis_nav <= 0:
                raise ValueError(
                    f"basis NAV is required for PERCENT_OF_NAV allocation "
                    f"(source={request.nav_source})"
                )
            configured_notional = float(basis_nav) * float(request.target_weight)
            effective_max_notional = configured_notional
            if request.hard_cap_notional is not None:
                effective_max_notional = min(
                    effective_max_notional,
                    float(request.hard_cap_notional),
                )

        data["basis_nav"] = basis_nav
        data["configured_notional"] = configured_notional
        data["effective_max_notional"] = effective_max_notional
        data["max_notional"] = effective_max_notional
        return data

    def _resolve_basis_nav(
        self,
        *,
        deployment_id: str,
        nav_source: str,
        manual_nav: float | None,
    ) -> float | None:
        if nav_source == "manual":
            return manual_nav

        if nav_source == "paper_nav":
            series = self._storage.get_nav_series(deployment_id)
            if not series:
                return None
            return _positive_float(series[-1].get("equity"))

        if nav_source == "account_equity":
            deployment = self._storage.get_deployment(deployment_id)
            account_id = deployment.get("account_id") if deployment is not None else None
            if not account_id:
                return None
            account = self._storage.broker_accounts.get(str(account_id))
            if not account:
                return None
            for field_name in (
                "equity",
                "account_equity",
                "total_equity",
                "balance",
                "available_balance",
            ):
                value = _positive_float(account.get(field_name))
                if value is not None:
                    return value
        return None

    def _append_profile_update_event(
        self,
        *,
        deployment_id: str,
        old_profile: dict[str, Any] | None,
        new_profile: dict[str, Any],
        updated_by: str,
    ) -> None:
        event = {
            "stream_key": "allocation:profiles",
            "event_type": "allocation.profile_updated",
            "schema_version": 1,
            "trace_id": f"allocation:{deployment_id}:{_utc_now_ms()}",
            "ts_ms": _utc_now_ms(),
            "source": "allocation_management",
            "payload": {
                "deployment_id": deployment_id,
                "strategy_id": new_profile.get("strategy_id"),
                "old_profile": old_profile,
                "new_profile": new_profile,
                "allocation_mode": new_profile.get("allocation_mode"),
                "basis_nav": new_profile.get("basis_nav"),
                "effective_max_notional": new_profile.get("effective_max_notional"),
                "updated_by": updated_by,
            },
        }
        self._storage.append_event(event)

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


def _utc_now_ms() -> int:
    return int(datetime.now(timezone.utc).timestamp() * 1000)


def _positive_float(value: Any) -> float | None:
    if value is None:
        return None
    try:
        parsed = float(value)
    except (TypeError, ValueError):
        return None
    if parsed <= 0:
        return None
    return parsed
