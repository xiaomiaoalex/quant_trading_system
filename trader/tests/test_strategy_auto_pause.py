from __future__ import annotations

import asyncio

import pytest
from fastapi import HTTPException

from trader.api.routes import strategies as strategy_routes
from trader.core.application.risk_engine import RiskCheckResult, RiskLevel
from trader.services.strategy_auto_pause import AutoPauseConfig, StrategyAutoPauseService
from trader.services.strategy_candidate import StrategyCandidateService
from trader.storage.in_memory import get_storage


class FakeRunner:
    def __init__(self) -> None:
        self.paused: list[str] = []
        self.resumed: list[str] = []
        self.fail_resume = False

    async def pause(self, deployment_id: str) -> object:
        self.paused.append(deployment_id)
        return object()

    async def resume(self, deployment_id: str) -> object:
        if self.fail_resume:
            raise RuntimeError("runner still blocked")
        self.resumed.append(deployment_id)
        return object()


class FakeSSEManager:
    def __init__(self) -> None:
        self.events: list[tuple[str, str, dict]] = []

    async def broadcast(self, channel: str, event_type: str, data: dict) -> int:
        self.events.append((channel, event_type, data))
        return 1


class FakeOMS:
    def __init__(self) -> None:
        self.healthy = False
        self.probed_strategy_names: list[str] = []

    async def _pre_trade_risk_check(self, signal) -> RiskCheckResult:
        self.probed_strategy_names.append(signal.strategy_name)
        return RiskCheckResult(
            passed=self.healthy,
            risk_level=RiskLevel.LOW,
            message="ok" if self.healthy else "still blocked",
        )


class FakeAutoPauseAPIService:
    def __init__(self) -> None:
        self.resumed: list[tuple[str, str]] = []

    def get_paused_strategies(self) -> list[dict]:
        return [{"deployment_id": "dep-1", "strategy_id": "s-1"}]

    def is_paused(self, deployment_id: str) -> bool:
        return deployment_id == "dep-1"

    async def force_resume(self, deployment_id: str, requested_by: str) -> None:
        self.resumed.append((deployment_id, requested_by))


def _make_service(
    monkeypatch: pytest.MonkeyPatch,
    *,
    threshold: int = 3,
    probe_required: int = 2,
) -> tuple[StrategyAutoPauseService, FakeRunner, FakeSSEManager, FakeOMS, str, str]:
    from trader.api.routes import strategies as strategy_routes

    storage = get_storage()
    candidate = storage.create_strategy_candidate(
        {
            "candidate_id": "candidate-1",
            "strategy_id": "mean_revert",
            "status": "APPROVED_FOR_PAPER",
            "deployment_id": "mean_revert__promote__candidate__paper",
        }
    )
    deployment_id = str(candidate["deployment_id"])
    strategy_id = str(candidate["strategy_id"])

    runner = FakeRunner()
    monkeypatch.setattr(strategy_routes, "_strategy_runner_instance", runner)

    sse = FakeSSEManager()
    oms = FakeOMS()
    service = StrategyAutoPauseService(
        oms_handler=oms,
        sse_manager=sse,
        candidate_service=StrategyCandidateService(storage),
        config=AutoPauseConfig(
            window_sec=60,
            threshold=threshold,
            probe_interval_sec=60,
            consecutive_probe_required=probe_required,
        ),
    )
    return service, runner, sse, oms, strategy_id, deployment_id


@pytest.mark.asyncio
async def test_auto_pause_api_routes_list_and_force_resume() -> None:
    fake = FakeAutoPauseAPIService()
    strategy_routes.set_auto_pause_service(fake)

    listed = await strategy_routes.list_auto_paused_strategies()
    assert listed["service_running"] is True
    assert listed["paused_strategies"] == [{"deployment_id": "dep-1", "strategy_id": "s-1"}]

    resumed = await strategy_routes.force_resume_strategy("dep-1", requested_by="operator")
    assert resumed == {
        "deployment_id": "dep-1",
        "status": "resumed",
        "requested_by": "operator",
    }
    assert fake.resumed == [("dep-1", "operator")]

    with pytest.raises(HTTPException) as exc:
        await strategy_routes.force_resume_strategy("dep-2")
    assert exc.value.status_code == 404


@pytest.mark.asyncio
async def test_record_rejection_auto_pauses_candidate_and_broadcasts(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    service, runner, sse, _, strategy_id, deployment_id = _make_service(monkeypatch)

    for i in range(2):
        decision = await service.record_rejection(strategy_id, deployment_id, f"reject-{i}")
        assert decision.should_pause is False

    decision = await service.record_rejection(strategy_id, deployment_id, "risk unavailable")

    assert decision.should_pause is True
    assert runner.paused == [deployment_id]
    assert service.is_paused(deployment_id)

    candidate = get_storage().get_strategy_candidate("candidate-1")
    assert candidate is not None
    assert candidate["status"] == "PAUSED_BY_RISK"

    auto_paused_events = get_storage().list_events(
        stream_key="strategy_candidate:candidate-1",
        event_type="strategy_candidate.auto_paused",
    )
    assert len(auto_paused_events) == 1
    assert auto_paused_events[0]["payload"]["reason"] == "risk unavailable"
    assert auto_paused_events[0]["payload"]["reject_count_in_window"] == 3

    assert sse.events[-1][0] == "strategies"
    assert sse.events[-1][1] == "strategy_update"
    assert sse.events[-1][2]["event_type"] == "auto_paused"


@pytest.mark.asyncio
async def test_concurrent_rejections_pause_once(monkeypatch: pytest.MonkeyPatch) -> None:
    service, runner, sse, _, strategy_id, deployment_id = _make_service(
        monkeypatch,
        threshold=2,
    )

    await asyncio.gather(
        *[service.record_rejection(strategy_id, deployment_id, f"reject-{i}") for i in range(10)]
    )

    assert runner.paused == [deployment_id]
    assert len([event for event in sse.events if event[2]["event_type"] == "auto_paused"]) == 1


@pytest.mark.asyncio
async def test_probe_requires_consecutive_healthy_checks_before_auto_resume(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    service, runner, sse, oms, strategy_id, deployment_id = _make_service(monkeypatch)
    for i in range(3):
        await service.record_rejection(strategy_id, deployment_id, f"reject-{i}")

    oms.healthy = True
    await service._probe_paused_once()

    assert service.is_paused(deployment_id)
    assert runner.resumed == []
    assert service.get_paused_strategies()[0]["consecutive_probe_pass"] == 1

    await service._probe_paused_once()

    assert not service.is_paused(deployment_id)
    assert runner.resumed == [deployment_id]
    assert oms.probed_strategy_names == [strategy_id, strategy_id]

    candidate = get_storage().get_strategy_candidate("candidate-1")
    assert candidate is not None
    assert candidate["status"] == "PAPER_RUNNING"

    resumed_events = get_storage().list_events(
        stream_key="strategy_candidate:candidate-1",
        event_type="strategy_candidate.auto_resumed",
    )
    assert len(resumed_events) == 1
    assert resumed_events[0]["payload"]["probe_consecutive_pass"] == 2
    assert sse.events[-1][2]["event_type"] == "auto_resumed"


@pytest.mark.asyncio
async def test_force_resume_skips_probe_and_clears_paused_state(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    service, runner, _, _, strategy_id, deployment_id = _make_service(monkeypatch)
    for i in range(3):
        await service.record_rejection(strategy_id, deployment_id, f"reject-{i}")

    await service.force_resume(deployment_id, requested_by="operator")

    assert not service.is_paused(deployment_id)
    assert runner.resumed == [deployment_id]
    resumed_events = get_storage().list_events(
        stream_key="strategy_candidate:candidate-1",
        event_type="strategy_candidate.auto_resumed",
    )
    assert resumed_events[0]["payload"]["requested_by"] == "operator"


@pytest.mark.asyncio
async def test_auto_resume_failure_keeps_paused_state_for_retry(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    service, runner, sse, oms, strategy_id, deployment_id = _make_service(monkeypatch)
    for i in range(3):
        await service.record_rejection(strategy_id, deployment_id, f"reject-{i}")

    runner.fail_resume = True
    oms.healthy = True
    await service._probe_paused_once()
    await service._probe_paused_once()

    assert service.is_paused(deployment_id)
    assert runner.resumed == []
    assert service.get_paused_strategies()[0]["consecutive_probe_pass"] == 2

    candidate = get_storage().get_strategy_candidate("candidate-1")
    assert candidate is not None
    assert candidate["status"] == "PAUSED_BY_RISK"
    assert not any(event[2]["event_type"] == "auto_resumed" for event in sse.events)

    runner.fail_resume = False
    await service._probe_paused_once()

    assert not service.is_paused(deployment_id)
    assert runner.resumed == [deployment_id]
