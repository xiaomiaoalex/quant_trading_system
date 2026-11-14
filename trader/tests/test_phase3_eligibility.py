"""Phase 3 engineer acceptance: dataset eligibility and retry fail-closed.

The eligibility decision comes from a trusted published provenance link plus a
published history dataset, never from a caller-supplied source string. Every
submission and retry entry point must go through the gate.
"""

from __future__ import annotations

from pathlib import Path

import httpx
import pytest

from trader.api.trading_app import create_trading_app
from trader.tests.fakes.research_eligibility import always_eligible, trust_memory_dataset
from trader.tests.test_research_runtime import bars, identity
from trader.trading.datasets import digest
from trader.trading.eligibility import (
    DATASET_ELIGIBILITY_GATE_UNAVAILABLE,
    DATASET_PROVENANCE_MISMATCH,
    DATASET_PROVENANCE_UNTRUSTED,
    HISTORY_STORE_UNAVAILABLE,
)
from trader.trading.jobs import (
    BacktestApplication,
    MemoryResearchStore,
    UnavailableBacktestApplication,
)
from trader.trading.research import ResearchExecutor


def manifest(dataset_ids, **overrides):
    value = {
        "dataset_ids": list(dataset_ids),
        "strategy": {"module_path": "trader.strategies.ema_cross_btc", "params": {}},
        "initial_cash": "1000",
        "fee_bps": "10",
        "slippage_bps": "5",
        "engine": "event_runtime",
        "purpose": "research_screening",
    }
    value.update(overrides)
    return value


async def trusted_store(*, official_eligible=True):
    store = MemoryResearchStore()
    data = await store.register_dataset(identity(), bars())
    link = trust_memory_dataset(store, data["dataset_id"], official_eligible=official_eligible)
    return store, data, link


def test_application_without_an_eligibility_gate_cannot_be_constructed():
    with pytest.raises(ValueError, match=DATASET_ELIGIBILITY_GATE_UNAVAILABLE):
        BacktestApplication(MemoryResearchStore())
    with pytest.raises(ValueError, match=DATASET_ELIGIBILITY_GATE_UNAVAILABLE):
        BacktestApplication(MemoryResearchStore(), eligibility=None)


@pytest.mark.asyncio
async def test_submission_without_trusted_provenance_is_refused_before_queue():
    store = MemoryResearchStore()
    data = await store.register_dataset(identity(), bars())
    application = BacktestApplication(store, eligibility=always_eligible)
    with pytest.raises(ValueError, match=DATASET_PROVENANCE_UNTRUSTED):
        await application.submit(manifest([data["dataset_id"]]))
    assert store.jobs == {}


@pytest.mark.asyncio
async def test_official_eligible_false_is_refused():
    store, data, _ = await trusted_store(official_eligible=False)
    application = BacktestApplication(store, eligibility=always_eligible)
    with pytest.raises(ValueError, match=DATASET_PROVENANCE_UNTRUSTED):
        await application.submit(manifest([data["dataset_id"]]))
    assert store.jobs == {}


@pytest.mark.asyncio
async def test_caller_provenance_cannot_override_the_trusted_link():
    store, data, link = await trusted_store()
    application = BacktestApplication(store, eligibility=always_eligible)
    with pytest.raises(ValueError, match=DATASET_PROVENANCE_MISMATCH):
        await application.submit(
            manifest([data["dataset_id"]], provenance={data["dataset_id"]: "f" * 64})
        )
    assert store.jobs == {}

    accepted = await application.submit(
        manifest([data["dataset_id"]], provenance={data["dataset_id"]: link["provenance_hash"]})
    )
    assert accepted["manifest"]["provenance"] == {data["dataset_id"]: link["provenance_hash"]}


@pytest.mark.asyncio
async def test_incompatible_venue_interval_quote_and_calendar_are_refused_before_queue():
    store = MemoryResearchStore()
    btc = await store.register_dataset(identity(), bars())
    trust_memory_dataset(store, btc["dataset_id"])
    application = BacktestApplication(store, eligibility=always_eligible)

    cases = {
        "venue": (identity(venue="other", symbol="ETHUSDT", base_asset="ETH"), bars()),
        "quote": (identity(symbol="ETHUSDT", base_asset="ETH", quote_asset="USDC"), bars()),
        "interval": (
            identity(symbol="ETHUSDT", base_asset="ETH", interval="5m"),
            [
                {
                    "open_time_ms": index * 300_000,
                    "close_time_ms": (index + 1) * 300_000,
                    "available_at_ms": (index + 1) * 300_000,
                    "open": "100",
                    "high": "100",
                    "low": "100",
                    "close": "100",
                    "volume": "1",
                }
                for index in range(4)
            ],
        ),
        "calendar": (identity(symbol="ETHUSDT", base_asset="ETH"), bars(start=60_000)),
    }
    for label, (other_identity, other_bars) in cases.items():
        other = await store.register_dataset(other_identity, other_bars)
        trust_memory_dataset(store, other["dataset_id"], symbol=other_identity["symbol"])
        with pytest.raises(ValueError):
            await application.submit(manifest([btc["dataset_id"], other["dataset_id"]]))
        assert store.jobs == {}, label


@pytest.mark.asyncio
async def test_eligibility_storage_failure_is_a_stable_rejection():
    store, data, _ = await trusted_store()

    async def unavailable(_dataset_id):
        raise ValueError(HISTORY_STORE_UNAVAILABLE)

    application = BacktestApplication(store, eligibility=unavailable)
    with pytest.raises(ValueError) as refused:
        await application.submit(manifest([data["dataset_id"]]))
    assert str(refused.value) == HISTORY_STORE_UNAVAILABLE
    assert store.jobs == {}


@pytest.mark.asyncio
async def test_retry_rechecks_eligibility_and_keeps_the_run_failed():
    store, data, link = await trusted_store()
    application = BacktestApplication(store, eligibility=always_eligible)
    record = await application.submit(manifest([data["dataset_id"]]))
    claimed = await store.claim("worker")
    await store.finish(record["run_id"], "worker", claimed["fencing_token"], error="boom")

    store.links.pop(data["dataset_id"])
    with pytest.raises(ValueError, match=DATASET_PROVENANCE_UNTRUSTED):
        await application.retry(record["run_id"])
    still_failed = await store.get(record["run_id"])
    assert still_failed["status"] == "FAILED"
    assert still_failed["attempt"] == 1
    assert still_failed["manifest_hash"] == record["manifest_hash"]

    trust_memory_dataset(store, data["dataset_id"], provenance_hash=link["provenance_hash"])
    requeued = await application.retry(record["run_id"])
    assert requeued["status"] == "QUEUED"
    assert requeued["manifest_hash"] == record["manifest_hash"]
    assert requeued["manifest"]["provenance"] == {data["dataset_id"]: link["provenance_hash"]}


@pytest.mark.asyncio
async def test_retry_rechecks_official_eligible_flag():
    store, data, link = await trusted_store()
    application = BacktestApplication(store, eligibility=always_eligible)
    record = await application.submit(manifest([data["dataset_id"]]))
    claimed = await store.claim("worker")
    await store.finish(record["run_id"], "worker", claimed["fencing_token"], error="boom")

    trust_memory_dataset(
        store,
        data["dataset_id"],
        provenance_hash=link["provenance_hash"],
        official_eligible=False,
    )
    with pytest.raises(ValueError, match=DATASET_PROVENANCE_UNTRUSTED):
        await application.retry(record["run_id"])
    assert (await store.get(record["run_id"]))["status"] == "FAILED"


@pytest.mark.asyncio
async def test_unavailable_application_refuses_submit_and_retry():
    store = MemoryResearchStore()
    application = UnavailableBacktestApplication(store)
    with pytest.raises(ValueError) as submitted:
        await application.submit(manifest(["ds_x"]))
    assert str(submitted.value) == HISTORY_STORE_UNAVAILABLE
    with pytest.raises(ValueError) as retried:
        await application.retry("bt_missing")
    assert str(retried.value) == HISTORY_STORE_UNAVAILABLE


@pytest.mark.asyncio
async def test_retry_api_maps_a_storage_outage_to_503():
    store, data, _ = await trusted_store()
    state = {"fail": False}

    async def checker(dataset_id):
        if state["fail"]:
            raise ValueError(HISTORY_STORE_UNAVAILABLE)
        return True

    application = BacktestApplication(store, eligibility=checker)
    record = await application.submit(manifest([data["dataset_id"]]))
    claimed = await store.claim("worker")
    await store.finish(record["run_id"], "worker", claimed["fencing_token"], error="boom")
    state["fail"] = True

    app = create_trading_app(research_store=store, research_application=application)
    client = httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://test")
    try:
        response = await client.post(f"/v1/research/backtests/{record['run_id']}/retry")
        assert response.status_code == 503, response.text
        assert response.json()["detail"] == HISTORY_STORE_UNAVAILABLE
    finally:
        await client.aclose()


@pytest.mark.asyncio
async def test_executor_refuses_a_job_without_frozen_provenance():
    store, data, _ = await trusted_store()
    application = BacktestApplication(store, eligibility=always_eligible)
    record = await application.submit(manifest([data["dataset_id"]]))
    record["manifest"].pop("provenance")
    record["manifest_hash"] = digest(record["manifest"])
    with pytest.raises(ValueError, match="Frozen provenance is required"):
        await ResearchExecutor(store).execute(record)


def test_composed_entry_has_no_ungated_research_application():
    source = (Path(__file__).resolve().parents[1] / "runtime" / "trading_main.py").read_text(
        encoding="utf-8"
    )
    assert "build_history_plane(" in source
    assert "BacktestApplication(" not in source
