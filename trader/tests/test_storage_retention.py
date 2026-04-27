"""Storage retention and compatibility tests."""
import time

import pytest

from trader.storage.in_memory import ControlPlaneInMemoryStorage
from trader.services.strategy_event_service import StrategyEvent, StrategyEventService, StrategyEventType


@pytest.fixture
def bounded_storage():
    return ControlPlaneInMemoryStorage(
        max_events=50,
        max_executions=100,
        max_snapshots_per_stream=5,
        max_replay_jobs=10,
        execution_dedup_ttl_seconds=2,
    )


def test_events_trimmed_at_limit(bounded_storage):
    for i in range(60):
        bounded_storage.append_event({"stream_key": "test", "ts_ms": i})
    assert len(bounded_storage.events) == 50
    assert bounded_storage.events[0]["ts_ms"] == 10


def test_executions_and_side_index_bounded(bounded_storage):
    for i in range(120):
        bounded_storage.create_execution({"cl_ord_id": f"c{i}", "exec_id": f"e{i}"})
    assert len(bounded_storage.executions) == 100
    assert len(bounded_storage.execution_by_key) == 100
    assert "c0:e0" not in bounded_storage.execution_by_key
    assert "c119:e119" in bounded_storage.execution_by_key


def test_cleanup_expired_dedup_keys(bounded_storage):
    for i in range(5):
        bounded_storage.create_execution({"cl_ord_id": f"c{i}", "exec_id": f"e{i}"})
    time.sleep(2.5)
    assert bounded_storage._cleanup_expired_dedup_keys() == 5
    assert len(bounded_storage.execution_by_key) == 0


def test_snapshots_trimmed_per_stream(bounded_storage):
    for i in range(10):
        bounded_storage.save_snapshot({"stream_key": "stream_a", "ts_ms": i})
    assert len(bounded_storage.snapshots["stream_a"]) == 5
    assert bounded_storage.snapshots["stream_a"][0]["ts_ms"] == 5


@pytest.mark.asyncio
async def test_clear_events_correct_prefix():
    event_service = StrategyEventService(max_events_per_strategy=100)
    await event_service.publish(StrategyEvent(
        strategy_id="strat_a",
        event_type=StrategyEventType.SIGNAL_GENERATED,
        payload={"msg": "a"},
    ))
    await event_service.publish(StrategyEvent(
        strategy_id="strat_b",
        event_type=StrategyEventType.SIGNAL_GENERATED,
        payload={"msg": "b"},
    ))
    assert await event_service.clear_events("strat_a") == 1
    remaining = await event_service.list_events()
    assert len(remaining) == 1
    assert remaining[0].stream_key == "strategy:strat_b"


def test_crawler_processed_ids_bounded():
    from trader.adapters.announcements.binance_crawler import BinanceAnnouncementCrawler

    crawler = BinanceAnnouncementCrawler(event_store=None, max_processed_ids=3)
    for i in range(5):
        crawler._mark_processed(f"id_{i}")
    assert len(crawler._processed_ids) == 3
    assert "id_0" not in crawler._processed_ids
    assert "id_4" in crawler._processed_ids


def test_memory_stats(bounded_storage):
    bounded_storage.append_event({"stream_key": "s", "ts_ms": 1})
    bounded_storage.create_execution({"cl_ord_id": "c", "exec_id": "e"})
    bounded_storage.save_snapshot({"stream_key": "sk", "ts_ms": 1})
    stats = bounded_storage.get_memory_stats()
    assert stats["events"]["count"] == 1
    assert stats["executions"]["count"] == 1
    assert stats["snapshots"]["count"] == 1
    assert stats["dedup_keys"]["ttl_seconds"] == 2


def test_runtime_state_deployment_id_keying():
    storage = ControlPlaneInMemoryStorage()
    storage.save_strategy_runtime_state({"deployment_id": "d1", "strategy_id": "s", "status": "RUNNING"})
    storage.save_strategy_runtime_state({"deployment_id": "d2", "strategy_id": "s", "status": "STOPPED"})
    assert len(storage.strategy_runtime_states) == 2
    assert storage.get_strategy_runtime_state("d1")["status"] == "RUNNING"
    assert storage.delete_strategy_runtime_state("d1") is True
    with pytest.raises(ValueError, match="requires deployment_id or strategy_id"):
        storage.save_strategy_runtime_state({"status": "RUNNING"})


@pytest.mark.asyncio
async def test_replay_jobs_eviction():
    from trader.api.models.schemas import ReplayJob
    from trader.api.routes.events import (
        _MAX_REPLAY_JOBS,
        _evict_replay_jobs_locked,
        _replay_jobs,
        _replay_jobs_lock,
        clear_replay_jobs,
    )

    clear_replay_jobs()
    async with _replay_jobs_lock:
        for i in range(_MAX_REPLAY_JOBS + 2):
            _replay_jobs[f"job_{i}"] = ReplayJob(
                job_id=f"job_{i}",
                stream_key="test",
                status="COMPLETED",
                requested_by="test",
                requested_at=f"2026-01-01T00:{i // 60:02d}:{i % 60:02d}Z",
            )
        _evict_replay_jobs_locked()
        assert len(_replay_jobs) == _MAX_REPLAY_JOBS
        assert "job_0" not in _replay_jobs
    clear_replay_jobs()


def test_replay_uses_data_when_payload_missing():
    from trader.api.models.schemas import ReplayRequest
    from trader.services.event import EventService

    storage = ControlPlaneInMemoryStorage()
    storage.append_event({
        "stream_key": "deployment:dep_1",
        "event_type": "strategy.signal",
        "ts_ms": 1000,
        "data": {"cl_ord_id": "order-1", "value": 42},
    })
    events = EventService(storage=storage)._to_stream_events(
        ReplayRequest(stream_key="deployment:dep_1", requested_by="test")
    )
    assert events[0].data["value"] == 42
    assert events[0].aggregate_id == "order-1"
