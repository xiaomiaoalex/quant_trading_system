from __future__ import annotations

from datetime import datetime, timezone
from typing import Any

from trader.api.models.schemas import (
    PortfolioAutopilotDecision,
    PortfolioAutopilotSnapshot,
    PortfolioAutopilotTickRequest,
    StrategyAllocationProfile,
)
from trader.storage.in_memory import ControlPlaneInMemoryStorage, get_storage


def _utc_now_ms() -> int:
    return int(datetime.now(timezone.utc).timestamp() * 1000)


class PortfolioRuntimeController:
    """First paper/shadow autopilot controller for portfolio-level runtime actions."""

    def __init__(self, storage: ControlPlaneInMemoryStorage | None = None):
        self._storage = storage or get_storage()

    def snapshot(self, request: PortfolioAutopilotTickRequest | None = None) -> PortfolioAutopilotSnapshot:
        req = request or PortfolioAutopilotTickRequest()
        return PortfolioAutopilotSnapshot(
            ts_ms=_utc_now_ms(),
            kill_switch_level=req.kill_switch_level,
            portfolio_exposure=req.portfolio_exposure,
            max_portfolio_exposure=req.max_portfolio_exposure,
            data_stale=req.data_stale,
            profiles=[
                StrategyAllocationProfile(**profile)
                for profile in self._storage.list_allocation_profiles()
            ],
            decisions=[
                PortfolioAutopilotDecision(**decision)
                for decision in self._storage.list_autopilot_decisions(limit=50)
            ],
        )

    def tick(self, request: PortfolioAutopilotTickRequest) -> PortfolioAutopilotSnapshot:
        snapshot = self.snapshot(request)
        decisions: list[PortfolioAutopilotDecision] = []
        profiles = [p for p in snapshot.profiles if p.enabled]

        if request.kill_switch_level > 0:
            for profile in profiles:
                decisions.append(
                    self._record_decision(
                        "STOP",
                        profile.deployment_id,
                        f"kill_switch_level={request.kill_switch_level}",
                        snapshot.model_dump(),
                    )
                )
        elif request.data_stale:
            for profile in profiles:
                decisions.append(
                    self._record_decision(
                        "PAUSE",
                        profile.deployment_id,
                        "data_stale",
                        snapshot.model_dump(),
                    )
                )
        elif (
            request.max_portfolio_exposure > 0
            and request.portfolio_exposure > request.max_portfolio_exposure
            and profiles
        ):
            profile = sorted(profiles, key=lambda p: p.priority, reverse=True)[0]
            decisions.append(
                self._record_decision(
                    "REDUCE_ALLOCATION",
                    profile.deployment_id,
                    "portfolio_exposure_exceeded",
                    snapshot.model_dump(),
                )
            )

        for deployment_id, error_count in request.deployment_errors.items():
            if error_count >= 10:
                decisions.append(
                    self._record_decision(
                        "STOP",
                        deployment_id,
                        f"deployment_errors={error_count}",
                        snapshot.model_dump(),
                    )
                )

        return PortfolioAutopilotSnapshot(
            **{
                **snapshot.model_dump(),
                "decisions": decisions
                or [
                    PortfolioAutopilotDecision(**item)
                    for item in self._storage.list_autopilot_decisions(limit=50)
                ],
            }
        )

    def _record_decision(
        self,
        action: str,
        deployment_id: str | None,
        reason: str,
        input_snapshot: dict[str, Any],
    ) -> PortfolioAutopilotDecision:
        decision = self._storage.append_autopilot_decision(
            {
                "action": action,
                "deployment_id": deployment_id,
                "reason": reason,
                "input_snapshot": input_snapshot,
                "mode": "paper",
            }
        )
        self._storage.append_event(
            {
                "stream_key": "portfolio_autopilot",
                "event_type": "portfolio_autopilot.decision",
                "schema_version": 1,
                "trace_id": f"autopilot:{decision['decision_id']}",
                "ts_ms": _utc_now_ms(),
                "source": "portfolio_runtime_controller",
                "payload": decision,
            }
        )
        if deployment_id and action in {"PAUSE", "STOP"}:
            self._storage.update_deployment_status(
                deployment_id, "PAUSED" if action == "PAUSE" else "STOPPED"
            )
        return PortfolioAutopilotDecision(**decision)
