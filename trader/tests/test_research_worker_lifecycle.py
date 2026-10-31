"""Lease and cancellation checks while a research job is executing."""

import asyncio
from contextlib import suppress

import pytest

from trader.tests.test_research_runtime import bars, dataset
from trader.trading.backtest import EventBacktestRuntime
from trader.trading.jobs import MemoryResearchStore, ResearchWorker


class Clock:
    now = 0

    def __call__(self):
        return self.now


class HeartbeatTicks:
    def __init__(self):
        self.ticks = asyncio.Queue()

    async def wait(self):
        await self.ticks.get()


@pytest.mark.asyncio
async def test_worker_renews_during_execution_before_original_lease_expires():
    clock = Clock()
    store = MemoryResearchStore(clock=clock)
    job = await store.enqueue({})
    worker = ResearchWorker(store, "worker", lease_seconds=30)
    ticks = HeartbeatTicks()
    worker._wait_heartbeat = ticks.wait
    entered, finish = asyncio.Event(), asyncio.Event()

    async def execute(record):
        entered.set()
        await finish.wait()
        return {"ok": True}

    running = asyncio.create_task(worker.run_once(execute))
    try:
        await entered.wait()
        clock.now = 20
        ticks.ticks.put_nowait(None)
        for _ in range(6):
            await asyncio.sleep(0)
        clock.now = 31
        assert await store.claim("competitor") is None
        finish.set()
        assert (await running)["status"] == "SUCCEEDED"
        assert (await store.get(job["run_id"]))["result"] == {"ok": True}
    finally:
        running.cancel()
        with suppress(asyncio.CancelledError, RuntimeError):
            await running


@pytest.mark.asyncio
@pytest.mark.parametrize("lose_lease", [False, True])
async def test_worker_cancels_local_execution_when_cancelled_or_fenced(lose_lease):
    clock = Clock()
    store = MemoryResearchStore(clock=clock)
    job = await store.enqueue({})
    worker = ResearchWorker(store, "worker", lease_seconds=30)
    ticks = HeartbeatTicks()
    worker._wait_heartbeat = ticks.wait
    entered, stopped = asyncio.Event(), asyncio.Event()

    async def execute(record):
        entered.set()
        try:
            await asyncio.Event().wait()
        finally:
            stopped.set()

    running = asyncio.create_task(worker.run_once(execute))
    try:
        await entered.wait()
        if lose_lease:
            clock.now = 31
            replacement = await store.claim("replacement")
            assert replacement["fencing_token"] == 2
        else:
            await store.cancel(job["run_id"])
        ticks.ticks.put_nowait(None)
        for _ in range(10):
            await asyncio.sleep(0)
        assert stopped.is_set()
        if lose_lease:
            with pytest.raises(RuntimeError, match="Worker lease lost"):
                await running
            state = await store.get(job["run_id"])
            assert state["lease_owner"] == "replacement"
            assert state["result"] is None
        else:
            assert (await running)["status"] == "CANCELLED"
    finally:
        running.cancel()
        with suppress(asyncio.CancelledError, RuntimeError):
            await running


@pytest.mark.asyncio
async def test_async_bar_replay_yields_to_worker_heartbeat():
    ticks = []

    async def heartbeat():
        ticks.append(True)

    task = asyncio.create_task(heartbeat())
    runtime = EventBacktestRuntime(datasets=[dataset(bars(1000))], strategy=lambda context, bar: [])
    await runtime.run_async()
    assert ticks
    await task
