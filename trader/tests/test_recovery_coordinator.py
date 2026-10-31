"""Recovery readiness requires continuity evidence, never REST receive time."""

from dataclasses import replace
from decimal import Decimal

import pytest

from trader.trading.models import Execution, NotReady, ReconcileSnapshot
from trader.trading.recovery import RecoveryCoordinator, RecoveryCoverage


def snapshot():
    return ReconcileSnapshot("acct", 100, 1, {"USDT": Decimal("1000")})


def coordinator(max_buffer=10):
    calls = []

    async def block(reason):
        calls.append(("block", reason))

    async def apply_snapshot(value):
        calls.append(("snapshot", value.stream_healthy))

    async def execution(value):
        calls.append(("execution", value.key))

    async def observation(value):
        calls.append(("observation", value.cl_ord_id))

    async def ready():
        calls.append(("ready", True))

    return (
        RecoveryCoordinator(
            "acct", block, apply_snapshot, execution, observation, ready, max_buffer=max_buffer
        ),
        calls,
    )


def fact(exec_id="1"):
    return Execution("acct", "order", "BTCUSDT", exec_id, Decimal("1"), Decimal("100"))


def proof(generation):
    return RecoveryCoverage(generation, "fake_authoritative_cursor", 10, 20, 20, True)


@pytest.mark.asyncio
async def test_missing_coverage_never_enables_ready_despite_new_rest_timestamp():
    recovery, calls = coordinator()
    generation = await recovery.begin()
    with pytest.raises(NotReady, match="COVERAGE"):
        await recovery.finish(
            generation, snapshot(), replace(snapshot(), checked_at_ms=99999), None
        )
    assert not recovery.ready
    assert not any(call[0] == "ready" for call in calls)


@pytest.mark.asyncio
async def test_moving_market_price_does_not_block_recovery_but_a_missing_mark_does():
    """Live mark prices move between two collections; only the ledger state
    and the presence of a valuation may decide snapshot stability."""
    recovery, calls = coordinator()
    generation = await recovery.begin()
    first = replace(snapshot(), marks={"BTCUSDT": {"price": Decimal("100")}})
    later = replace(snapshot(), marks={"BTCUSDT": {"price": Decimal("101.5")}})
    await recovery.finish(generation, first, later, proof(generation))
    assert recovery.ready
    assert any(call[0] == "ready" for call in calls)

    other, other_calls = coordinator()
    generation = await other.begin()
    missing_mark = replace(snapshot(), marks={})
    with pytest.raises(NotReady, match="SNAPSHOT_UNSTABLE"):
        await other.finish(generation, first, missing_mark, proof(generation))
    assert not other.ready
    assert not any(call[0] == "ready" for call in other_calls)


@pytest.mark.asyncio
async def test_buffered_duplicates_replay_once_before_ready_and_old_generation_is_ignored():
    recovery, calls = coordinator()
    old = await recovery.begin()
    generation = await recovery.begin()
    assert not await recovery.ingest(old, fact("old"))
    assert await recovery.ingest(generation, fact())
    assert await recovery.ingest(generation, fact())
    await recovery.finish(generation, snapshot(), snapshot(), proof(generation))
    assert recovery.ready
    assert calls[-3:] == [("snapshot", False), ("execution", fact().key), ("ready", True)]


@pytest.mark.asyncio
async def test_overflow_and_changing_snapshot_remain_blocked():
    recovery, calls = coordinator(max_buffer=1)
    generation = await recovery.begin()
    await recovery.ingest(generation, fact())
    with pytest.raises(NotReady, match="OVERFLOW"):
        await recovery.ingest(generation, fact("2"))
    with pytest.raises(NotReady):
        await recovery.finish(generation, snapshot(), snapshot(), proof(generation))
    assert not recovery.ready
    generation = await recovery.begin()
    with pytest.raises(NotReady, match="UNSTABLE"):
        await recovery.finish(
            generation,
            snapshot(),
            replace(snapshot(), balances={"USDT": Decimal("999")}),
            proof(generation),
        )
    assert not recovery.ready


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "changes", [{"contiguous": False}, {"covered_through": 19}, {"stream_generation": 0}]
)
async def test_gap_insufficient_watermark_or_stale_proof_is_rejected(changes):
    recovery, calls = coordinator()
    generation = await recovery.begin()
    with pytest.raises(NotReady, match="COVERAGE"):
        await recovery.finish(
            generation, snapshot(), snapshot(), replace(proof(generation), **changes)
        )
    assert not recovery.ready


@pytest.mark.asyncio
async def test_buffered_fact_after_the_proven_window_is_rejected():
    recovery, calls = coordinator()
    generation = await recovery.begin()
    late = replace(fact("late"), occurred_at_ms=21)
    await recovery.ingest(generation, late)
    assert recovery.buffered_through == 21
    with pytest.raises(NotReady, match="COVERAGE"):
        await recovery.finish(generation, snapshot(), snapshot(), proof(generation))
    assert not recovery.ready


@pytest.mark.asyncio
async def test_live_apply_failure_closes_recovery_gate():
    recovery, calls = coordinator()
    generation = await recovery.begin()
    await recovery.finish(generation, snapshot(), snapshot(), proof(generation))

    async def failing(value):
        raise RuntimeError("ledger unavailable")

    recovery.apply_execution = failing
    with pytest.raises(RuntimeError, match="ledger unavailable"):
        await recovery.ingest(generation, fact())
    assert not recovery.ready
    assert calls[-1][0] == "block"
