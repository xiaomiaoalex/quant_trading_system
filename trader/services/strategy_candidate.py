from __future__ import annotations

from datetime import datetime, timezone
from typing import Any

from trader.api.models.schemas import (
    BacktestGateResult,
    StrategyCandidate,
    StrategyCandidateCreateRequest,
    StrategyCandidateStatus,
    StrategyRegisterRequest,
)
from trader.services.strategy import StrategyService
from trader.storage.in_memory import ControlPlaneInMemoryStorage, get_storage


def _utc_now_ms() -> int:
    return int(datetime.now(timezone.utc).timestamp() * 1000)


class StrategyCandidateService:
    """Control-plane lifecycle service for research-to-runtime strategy candidates."""

    _allowed_transitions: dict[str, set[str]] = {
        "DRAFT": {"DEBUG_PASSED", "BACKTEST_RUNNING", "REJECTED"},
        "DEBUG_PASSED": {"BACKTEST_RUNNING", "REJECTED"},
        "BACKTEST_RUNNING": {"BACKTEST_PASSED", "REJECTED"},
        "BACKTEST_PASSED": {"VALIDATION_PASSED", "REJECTED"},
        "VALIDATION_PASSED": {"APPROVED_FOR_PAPER", "REJECTED"},
        "APPROVED_FOR_PAPER": {"PAPER_RUNNING", "STOPPED", "PAUSED_BY_RISK"},
        "PAPER_RUNNING": {"PAUSED_BY_RISK", "STOPPED"},
        "PAUSED_BY_RISK": {"PAPER_RUNNING", "STOPPED"},
        "STOPPED": {"PAPER_RUNNING"},
        "REJECTED": set(),
    }
    _delete_protected_statuses = {"APPROVED_FOR_PAPER", "PAPER_RUNNING", "PAUSED_BY_RISK"}

    def __init__(self, storage: ControlPlaneInMemoryStorage | None = None):
        self._storage = storage or get_storage()

    def create_candidate(self, request: StrategyCandidateCreateRequest) -> StrategyCandidate:
        if self._storage.get_strategy(request.strategy_id) is None:
            StrategyService().register_strategy(
                StrategyRegisterRequest(
                    strategy_id=request.strategy_id,
                    name=request.name or request.strategy_id,
                    description=request.description,
                    entrypoint=f"dynamic:{request.strategy_id}",
                )
            )

        feature_version = "dev_smoke"
        if request.dataset is not None:
            feature_version = request.dataset.feature_version

        candidate = self._storage.create_strategy_candidate(
            {
                "strategy_id": request.strategy_id,
                "name": request.name,
                "description": request.description,
                "code": request.code,
                "code_version": request.code_version,
                "config": request.config,
                "dataset": request.dataset.model_dump() if request.dataset else None,
                "feature_version": feature_version,
            }
        )
        self._append_lifecycle_event(candidate, None, "DRAFT", "candidate_created")
        return StrategyCandidate(**candidate)

    def list_candidates(
        self, strategy_id: str | None = None, status: str | None = None, limit: int = 100
    ) -> list[StrategyCandidate]:
        return [
            StrategyCandidate(**candidate)
            for candidate in self._storage.list_strategy_candidates(strategy_id, status, limit)
        ]

    def get_candidate(self, candidate_id: str) -> StrategyCandidate | None:
        candidate = self._storage.get_strategy_candidate(candidate_id)
        if candidate is None:
            return None
        return StrategyCandidate(**candidate)

    def delete_candidate(self, candidate_id: str) -> bool:
        candidate = self._require_candidate(candidate_id)
        status = str(candidate.get("status", "DRAFT"))
        if status in self._delete_protected_statuses:
            raise ValueError(f"Cannot delete StrategyCandidate in {status}; stop or detach runtime first")

        deployment_id = candidate.get("deployment_id")
        if deployment_id:
            deployment = self._storage.get_deployment(str(deployment_id))
            deployment_status = str((deployment or {}).get("status", "")).upper()
            if deployment_status in {"RUNNING", "PAUSED"}:
                raise ValueError(
                    f"Cannot delete StrategyCandidate with active deployment {deployment_id}"
                )

        self._storage.append_event(
            {
                "stream_key": f"strategy_candidate:{candidate_id}",
                "event_type": "strategy_candidate.deleted",
                "schema_version": 1,
                "trace_id": f"candidate:{candidate_id}",
                "ts_ms": _utc_now_ms(),
                "source": "strategy_candidate_service",
                "payload": {
                    "candidate_id": candidate_id,
                    "strategy_id": candidate.get("strategy_id"),
                    "status": status,
                    "deployment_id": deployment_id,
                    "reason": "user_deleted",
                },
            }
        )
        deleted = self._storage.delete_strategy_candidate(candidate_id)
        return deleted is not None

    def mark_debug_passed(
        self, candidate_id: str, code_version: int | None = None
    ) -> StrategyCandidate:
        candidate = self._require_candidate(candidate_id)
        updates: dict[str, Any] = {}
        if code_version is not None:
            updates["code_version"] = code_version
        return self._transition(candidate, "DEBUG_PASSED", "debug_passed", updates)

    def mark_backtest_running(self, candidate_id: str, backtest_run_id: str) -> StrategyCandidate:
        candidate = self._require_candidate(candidate_id)
        return self._transition(
            candidate,
            "BACKTEST_RUNNING",
            "backtest_submitted",
            {"backtest_run_id": backtest_run_id},
        )

    def validate_candidate(self, candidate_id: str) -> StrategyCandidate:
        candidate = self._require_candidate(candidate_id)
        backtest_run_id = candidate.get("backtest_run_id")
        failed_rules: list[str] = []
        metrics: dict[str, Any] = {}
        evidence_refs: dict[str, str] = {}

        if not backtest_run_id:
            failed_rules.append("missing_backtest_run_id")
        else:
            backtest = self._storage.get_backtest(str(backtest_run_id))
            if backtest is None:
                failed_rules.append("backtest_not_found")
            else:
                metrics = dict(backtest.get("metrics") or {})
                evidence_refs["backtest_run_id"] = str(backtest_run_id)
                if backtest.get("status") != "COMPLETED":
                    failed_rules.append("backtest_not_completed")
                data_mode = metrics.get("backtest_data_mode") or backtest.get("data_mode")
                if data_mode != "real_feature_store":
                    failed_rules.append("dev_smoke_backtest_not_deployable")
                max_drawdown_pct = float(metrics.get("max_drawdown_pct", 0.0) or 0.0)
                if max_drawdown_pct > 25.0:
                    failed_rules.append("max_drawdown_exceeded")
                quality_score = (
                    metrics.get("data_quality_summary", {}).get("quality_score")
                    if isinstance(metrics.get("data_quality_summary"), dict)
                    else None
                )
                if quality_score is not None and float(quality_score) < 0.8:
                    failed_rules.append("data_quality_below_threshold")
                if float(metrics.get("cost_stress_return", metrics.get("total_return", 0.0)) or 0.0) <= 0:
                    failed_rules.append("cost_stress_non_positive")

        validation = BacktestGateResult(
            passed=len(failed_rules) == 0,
            failed_rules=failed_rules,
            metrics=metrics,
            evidence_refs=evidence_refs,
        )
        target_status: StrategyCandidateStatus = "VALIDATION_PASSED" if validation.passed else "REJECTED"
        return self._transition(
            candidate,
            target_status,
            "validation_passed" if validation.passed else "validation_failed",
            {"validation": validation.model_dump()},
            allow_reject=True,
        )

    def approve_for_paper(self, candidate_id: str, deployment_id: str) -> StrategyCandidate:
        candidate = self._require_candidate(candidate_id)
        return self._transition(
            candidate,
            "APPROVED_FOR_PAPER",
            "promoted_to_paper",
            {"deployment_id": deployment_id},
        )

    def _require_candidate(self, candidate_id: str) -> dict[str, Any]:
        candidate = self._storage.get_strategy_candidate(candidate_id)
        if candidate is None:
            raise KeyError(candidate_id)
        return candidate

    def _transition(
        self,
        candidate: dict[str, Any],
        to_status: StrategyCandidateStatus,
        reason: str,
        updates: dict[str, Any] | None = None,
        allow_reject: bool = False,
    ) -> StrategyCandidate:
        from_status = str(candidate.get("status", "DRAFT"))
        allowed = self._allowed_transitions.get(from_status, set())
        if to_status not in allowed and not (allow_reject and to_status == "REJECTED"):
            raise ValueError(f"Illegal transition {from_status} -> {to_status}")
        data = {**(updates or {}), "status": to_status}
        updated = self._storage.update_strategy_candidate(str(candidate["candidate_id"]), data)
        if updated is None:
            raise KeyError(str(candidate["candidate_id"]))
        self._append_lifecycle_event(updated, from_status, to_status, reason)
        return StrategyCandidate(**updated)

    def _append_lifecycle_event(
        self,
        candidate: dict[str, Any],
        from_status: str | None,
        to_status: str,
        reason: str,
    ) -> None:
        payload = {
            "candidate_id": candidate["candidate_id"],
            "strategy_id": candidate["strategy_id"],
            "from_status": from_status,
            "to_status": to_status,
            "reason": reason,
        }
        event = {
            "stream_key": f"strategy_candidate:{candidate['candidate_id']}",
            "event_type": "strategy_candidate.lifecycle",
            "schema_version": 1,
            "trace_id": f"candidate:{candidate['candidate_id']}",
            "ts_ms": _utc_now_ms(),
            "source": "strategy_candidate_service",
            "payload": payload,
        }
        self._storage.append_event(event)
        events = list(candidate.get("events", []))
        events.append(payload)
        self._storage.update_strategy_candidate(str(candidate["candidate_id"]), {"events": events})
