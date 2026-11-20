"""Phase 3 independent tester acceptance: dataset eligibility and the gate.

Oracles come from docs/INTERFACE_CONTRACTS.md 14.3.2 / 16.1-16.2 and the
phase-3 acceptance table: eligibility follows a trusted published provenance
link, never the caller's ``source`` string; the history plane is fail-closed;
dataset sets share one completed-bar calendar; retries re-verify. All values
below are hand-derived; no engineer assertion is reused.
"""

from __future__ import annotations

import copy
from types import SimpleNamespace

import pytest
from fastapi import HTTPException

from trader.api.routes.research import BacktestSubmitRequest, retry_backtest, submit_backtest
from trader.runtime.history_plane import build_history_plane
from trader.trading.backtest import validate_dataset_set
from trader.trading.datasets import digest
from trader.trading.eligibility import (
    DATASET_ELIGIBILITY_GATE_UNAVAILABLE,
    DATASET_PROVENANCE_MISMATCH,
    DATASET_PROVENANCE_UNTRUSTED,
    HISTORY_STORE_UNAVAILABLE,
    link_is_trusted,
    require_published_dataset,
)
from trader.trading.jobs import (
    BacktestApplication,
    MemoryResearchStore,
    UnavailableBacktestApplication,
)
from trader.trading.research import ResearchExecutor

HISTORY_ID = "hds_p3i_offline_1"
PROVENANCE_HASH = "a" * 64


def _identity(**overrides):
    identity = {
        "venue": "BINANCE",
        "market_type": "SPOT",
        "symbol": "BTCUSDT",
        "base_asset": "BTC",
        "quote_asset": "USDT",
        "interval": "1m",
        # Deliberately the official archive source: the string itself must not
        # confer eligibility without a trusted, published provenance link.
        "source": "binance_vision",
        "version": "p3i-1",
    }
    identity.update(overrides)
    return identity


def _bars(*, start_ms=0, count=4, interval_ms=60_000, price="100"):
    bars = []
    for index in range(count):
        opened = start_ms + index * interval_ms
        closed = opened + interval_ms
        bars.append(
            {
                "open_time_ms": opened,
                "close_time_ms": closed,
                "available_at_ms": closed,
                "open": price,
                "high": "110",
                "low": "90",
                "close": price,
                "volume": "3",
            }
        )
    return bars


def _link(dataset_id, *, history_dataset_id=HISTORY_ID):
    return {
        "research_dataset_id": dataset_id,
        "history_dataset_id": history_dataset_id,
        "official_eligible": True,
        "provenance_hash": PROVENANCE_HASH,
        "symbol": "BTCUSDT",
        "interval": "1m",
        "month": "2026-09",
        "conversion_version": "p3i_v1",
        "bar_count": 4,
    }


def _published_row():
    return {
        "dataset_id": HISTORY_ID,
        "status": "PUBLISHED",
        "persistence_ok": True,
        "backtest_eligible": True,
    }


def _manifest(dataset_ids, *, provenance=None):
    manifest = {
        "dataset_ids": list(dataset_ids),
        "strategy": {"module_path": "trader.strategies.dca_btc", "params": {}},
        "initial_cash": "2000",
        "fee_bps": "10",
        "slippage_bps": "5",
        "engine": "event_runtime",
        "purpose": "research_screening",
        "risk_config": {},
    }
    if provenance is not None:
        manifest["provenance"] = provenance
    return manifest


def _raw_dataset(*, identity=None, bars=None):
    return {
        "identity": _identity() if identity is None else identity,
        "bars": _bars() if bars is None else bars,
    }


async def _eligibility_of(dataset_id, bridge, history):
    return await require_published_dataset(dataset_id, bridge=bridge, history_store=history)


async def _always_true(dataset_id):
    return True


async def _registered(store, *, identity=None, bars=None, history_dataset_id=HISTORY_ID):
    data = await store.register_dataset(
        _identity() if identity is None else identity,
        _bars() if bars is None else bars,
    )
    link = _link(data["dataset_id"], history_dataset_id=history_dataset_id)
    store.register_dataset_link(data["dataset_id"], link)
    return data, link


class StubHistoryStore:
    """Read-side double for the history-dataset store."""

    def __init__(self, rows=None, *, connect_fails=False, reads_fail=False):
        self.rows = copy.deepcopy(dict(rows or {}))
        self.connect_calls = 0
        self.connect_fails = connect_fails
        self.reads_fail = reads_fail
        self.storage = object()

    async def connect(self):
        self.connect_calls += 1
        if self.connect_fails:
            raise RuntimeError("history database is unreachable")

    async def get_dataset(self, dataset_id):
        if self.reads_fail:
            raise RuntimeError("history database is unreachable")
        return copy.deepcopy(self.rows.get(dataset_id))


class StubBridge:
    """Read-side double for the research/history provenance link."""

    def __init__(self, links=None, *, reads_fail=False):
        self.links = copy.deepcopy(dict(links or {}))
        self.reads_fail = reads_fail

    async def find_link_by_research_dataset(self, dataset_id):
        if self.reads_fail:
            raise RuntimeError("history database is unreachable")
        return copy.deepcopy(self.links.get(dataset_id))


async def _gate(store, *, links=None, rows=None):
    bridge = StubBridge(links)
    history = StubHistoryStore(rows)

    async def eligible(dataset_id):
        return await _eligibility_of(dataset_id, bridge, history)

    return BacktestApplication(store, eligibility=eligible), bridge, history


async def _plane(
    store,
    *,
    links=None,
    rows=None,
    factory_fails=False,
    connect_fails=False,
    service_fails=False,
):
    history = StubHistoryStore(rows, connect_fails=connect_fails)
    bridge = StubBridge(links)

    def history_factory():
        if factory_fails:
            raise RuntimeError("history store construction failed")
        return history

    def bridge_factory(storage):
        return bridge

    def service_factory(*, store, bridge_store):
        if service_fails:
            raise RuntimeError("ingestion service construction failed")
        return SimpleNamespace(store=store, bridge_store=bridge_store)

    built = await build_history_plane(
        store,
        history_store_factory=history_factory,
        bridge_factory=bridge_factory,
        service_factory=service_factory,
    )
    return (*built, history, bridge)


# ---------------------------------------------------------------- 3.8 -------


def test_p3i_3_8_backtest_application_cannot_be_built_without_a_gate():
    store = MemoryResearchStore()
    with pytest.raises(ValueError, match=DATASET_ELIGIBILITY_GATE_UNAVAILABLE):
        BacktestApplication(store)
    with pytest.raises(ValueError, match=DATASET_ELIGIBILITY_GATE_UNAVAILABLE):
        BacktestApplication(store, eligibility=None)


@pytest.mark.parametrize("failure", ["factory", "connect", "service"])
@pytest.mark.asyncio
async def test_p3i_3_8_history_plane_is_fail_closed_when_the_history_store_cannot_assemble(
    failure,
):
    store = MemoryResearchStore()
    data, _link = await _registered(store)
    built = await _plane(
        store,
        factory_fails=failure == "factory",
        connect_fails=failure == "connect",
        service_fails=failure == "service",
    )
    history_store, service, application, _stub, _bridge = built
    # Whichever assembly step fails, no eligibility=None application appears.
    assert history_store is None
    assert service is None
    assert isinstance(application, UnavailableBacktestApplication)
    with pytest.raises(ValueError, match=HISTORY_STORE_UNAVAILABLE):
        await application.submit(_manifest([data["dataset_id"]]))
    with pytest.raises(ValueError, match=HISTORY_STORE_UNAVAILABLE):
        await application.retry("bt_p3i_missing")
    assert store.jobs == {}


@pytest.mark.asyncio
async def test_p3i_3_8_history_plane_positive_path_submits_through_the_real_gate():
    store = MemoryResearchStore()
    data, link = await _registered(store)
    built = await _plane(
        store,
        links={data["dataset_id"]: link},
        rows={HISTORY_ID: _published_row()},
    )
    history_store, service, application, stub, bridge = built
    assert history_store is stub
    assert service.store is stub and service.bridge_store is bridge
    assert isinstance(application, BacktestApplication)

    record = await application.submit(_manifest([data["dataset_id"]]))
    frozen = {data["dataset_id"]: PROVENANCE_HASH}
    assert record["manifest"]["provenance"] == frozen
    assert len(store.jobs) == 1

    # Revoking publication is observed on the next submission: the gate is a
    # live check of the history plane, not a snapshot taken at assembly time.
    stub.rows[HISTORY_ID]["status"] = "PENDING"
    with pytest.raises(ValueError, match=DATASET_PROVENANCE_UNTRUSTED):
        await application.submit(_manifest([data["dataset_id"]]))
    assert len(store.jobs) == 1


# ---------------------------------------------------------------- 3.7 -------


@pytest.mark.asyncio
async def test_p3i_3_7_dataset_without_a_trusted_link_is_refused_even_by_a_lenient_callback():
    store = MemoryResearchStore()
    data, link = await _registered(store)
    # Remove the trusted link; keep the "binance_vision" source string.
    store.links.pop(data["dataset_id"])
    app = BacktestApplication(store, eligibility=_always_true)
    with pytest.raises(ValueError, match=DATASET_PROVENANCE_UNTRUSTED):
        await app.submit(_manifest([data["dataset_id"]]))
    assert store.jobs == {}

    # A link that exists but is not official is still not evidence.
    store.register_dataset_link(data["dataset_id"], {**link, "official_eligible": False})
    with pytest.raises(ValueError, match=DATASET_PROVENANCE_UNTRUSTED):
        await app.submit(_manifest([data["dataset_id"]]))
    assert store.jobs == {}


@pytest.mark.parametrize(
    "mutation,trusted",
    [
        ({}, True),
        ({"official_eligible": False}, False),
        ({"provenance_hash": ""}, False),
        ({"provenance_hash": None}, False),
        ({"history_dataset_id": ""}, False),
        ({"history_dataset_id": None}, False),
    ],
)
@pytest.mark.asyncio
async def test_p3i_3_7_link_trust_requires_official_flag_provenance_and_history_identity(
    mutation,
    trusted,
):
    link = _link("ds_p3i_link")
    link.update(mutation)
    assert link_is_trusted(link) is trusted
    bridge = StubBridge({"ds_p3i_link": link})
    history = StubHistoryStore({HISTORY_ID: _published_row()})
    decision = await _eligibility_of("ds_p3i_link", bridge, history)
    assert decision is trusted
    absent = await _eligibility_of("ds_p3i_absent", bridge, history)
    assert absent is False


# ---------------------------------------------------------------- 3.11 ------


@pytest.mark.asyncio
async def test_p3i_3_11_eligibility_follows_published_state_not_the_source_string():
    store = MemoryResearchStore()
    data, link = await _registered(store)
    bridge = StubBridge({data["dataset_id"]: link})
    history = StubHistoryStore({HISTORY_ID: _published_row()})
    dataset_id = data["dataset_id"]

    for mutation in (
        {"status": "PENDING"},
        {"persistence_ok": False},
        {"backtest_eligible": False},
    ):
        row = _published_row()
        row.update(mutation)
        history.rows[HISTORY_ID] = row
        decision = await _eligibility_of(dataset_id, bridge, history)
        assert decision is False
    history.rows.pop(HISTORY_ID)
    missing = await _eligibility_of(dataset_id, bridge, history)
    assert missing is False

    history.rows[HISTORY_ID] = _published_row()
    published = await _eligibility_of(dataset_id, bridge, history)
    assert published is True
    history.reads_fail = True
    with pytest.raises(ValueError) as refused:
        await _eligibility_of(dataset_id, bridge, history)
    assert str(refused.value) == HISTORY_STORE_UNAVAILABLE
    history.reads_fail = False

    async def eligible(dataset_id_arg):
        return await _eligibility_of(dataset_id_arg, bridge, history)

    app = BacktestApplication(store, eligibility=eligible)
    history.rows[HISTORY_ID]["status"] = "PENDING"
    with pytest.raises(ValueError, match=DATASET_PROVENANCE_UNTRUSTED):
        await app.submit(_manifest([dataset_id]))
    assert store.jobs == {}
    history.rows[HISTORY_ID] = _published_row()
    record = await app.submit(_manifest([dataset_id]))
    assert record["manifest"]["provenance"] == {dataset_id: PROVENANCE_HASH}


@pytest.mark.parametrize(
    "case",
    ["wrong_hash", "extra_dataset", "empty_mapping", "absent", "matching"],
)
@pytest.mark.asyncio
async def test_p3i_3_11_caller_provenance_may_only_repeat_the_trusted_hash(case):
    store = MemoryResearchStore()
    data, _link = await _registered(store)
    dataset_id = data["dataset_id"]
    app = BacktestApplication(store, eligibility=_always_true)
    supplied = {
        "wrong_hash": {dataset_id: "f" * 64},
        "extra_dataset": {dataset_id: PROVENANCE_HASH, "ds_other": PROVENANCE_HASH},
        "empty_mapping": {},
        "absent": None,
        "matching": {dataset_id: PROVENANCE_HASH},
    }[case]
    expected_error = None if case in {"absent", "matching"} else DATASET_PROVENANCE_MISMATCH
    manifest = _manifest([dataset_id], provenance=supplied)
    if expected_error is not None:
        with pytest.raises(ValueError, match=expected_error):
            await app.submit(manifest)
        assert store.jobs == {}
    else:
        record = await app.submit(manifest)
        # The frozen provenance always comes from the trusted link.
        assert record["manifest"]["provenance"] == {dataset_id: PROVENANCE_HASH}


@pytest.mark.asyncio
async def test_p3i_3_11_retry_rechecks_eligibility_and_failure_keeps_failed_state():
    store = MemoryResearchStore()
    data, link = await _registered(store)
    app, _bridge, history = await _gate(
        store,
        links={data["dataset_id"]: link},
        rows={HISTORY_ID: _published_row()},
    )
    record = await app.submit(_manifest([data["dataset_id"]]))
    run_id = record["run_id"]
    claimed = await store.claim("p3i-worker")
    await store.finish(run_id, "p3i-worker", claimed["fencing_token"], error="boom")
    frozen_manifest = copy.deepcopy((await store.get(run_id))["manifest"])

    # A retry must re-verify instead of trusting the earlier submission.
    history.rows[HISTORY_ID]["status"] = "PENDING"
    with pytest.raises(ValueError, match=DATASET_PROVENANCE_UNTRUSTED):
        await app.retry(run_id)
    kept = await store.get(run_id)
    assert kept["status"] == "FAILED"
    assert kept["attempt"] == 1
    assert kept["manifest"] == frozen_manifest
    assert kept["manifest_hash"] == digest(frozen_manifest)
    assert kept["error"] == "boom"

    history.rows[HISTORY_ID]["status"] = "PUBLISHED"
    requeued = await app.retry(run_id)
    assert requeued["status"] == "QUEUED"
    assert requeued["run_id"] == run_id
    assert requeued["manifest"] == frozen_manifest
    assert requeued["manifest_hash"] == digest(frozen_manifest)

    # A history outage is also fail-closed and leaves the FAILED run intact.
    claimed2 = await store.claim("p3i-worker-2")
    await store.finish(run_id, "p3i-worker-2", claimed2["fencing_token"], error="boom2")
    history.reads_fail = True
    with pytest.raises(ValueError) as refused:
        await app.retry(run_id)
    assert str(refused.value) == HISTORY_STORE_UNAVAILABLE
    kept2 = await store.get(run_id)
    assert kept2["status"] == "FAILED"
    assert kept2["attempt"] == 2
    assert kept2["manifest"] == frozen_manifest
    assert kept2["manifest_hash"] == digest(frozen_manifest)


# ---------------------------------------------------------------- 3.9 -------


@pytest.mark.asyncio
async def test_p3i_3_9_incompatible_dataset_sets_are_refused_before_any_work_is_queued():
    store = MemoryResearchStore()
    btc, _btc_link = await _registered(store)
    eth, _eth_link = await _registered(
        store,
        identity=_identity(symbol="ETHUSDT", base_asset="ETH", version="p3i-eth"),
        history_dataset_id="hds_p3i_offline_eth",
    )
    app = BacktestApplication(store, eligibility=_always_true)
    accepted = await app.submit(_manifest([btc["dataset_id"], eth["dataset_id"]]))
    assert accepted["status"] == "QUEUED"
    queued = len(store.jobs)

    cases = [
        (
            _identity(symbol="ETHUSDT", base_asset="ETH", quote_asset="USDC", version="p3i-quote"),
            _bars(),
            "Datasets must share a quote asset",
        ),
        (
            _identity(symbol="ETHUSDT", base_asset="ETH", interval="5m", version="p3i-5m"),
            _bars(interval_ms=300_000),
            "Datasets must share an interval",
        ),
        (
            _identity(symbol="ETHUSDT", base_asset="ETH", venue="OKX", version="p3i-venue"),
            _bars(),
            "Datasets must share a venue",
        ),
        (
            _identity(symbol="ETHUSDT", base_asset="ETH", version="p3i-calendar"),
            _bars(start_ms=60_000),
            "Datasets must share the same completed-bar calendar",
        ),
        (
            _identity(version="p3i-duplicate"),
            _bars(start_ms=60_000),
            "Duplicate or missing instrument",
        ),
    ]
    for identity, bars, message in cases:
        other = await store.register_dataset(identity, bars)
        with pytest.raises(ValueError, match=message):
            await app.submit(_manifest([btc["dataset_id"], other["dataset_id"]]))
        assert len(store.jobs) == queued


@pytest.mark.parametrize(
    "case,expected",
    [
        ("no_datasets", "At least one dataset is required"),
        ("no_bars", "Dataset has no bars"),
        ("not_spot", "Only SPOT data is supported"),
        ("unfinished", "Unfinished bars are not visible to the runtime"),
        ("wrong_duration", "Dataset bars do not match the declared interval"),
    ],
)
def test_p3i_3_9_validate_dataset_set_rejects_unfinished_and_mismatched_bars(case, expected):
    # Hand-built documents (not run through freeze_dataset) allow invalid bars
    # to reach the shared calendar validator.
    if case == "no_datasets":
        candidates = []
    elif case == "no_bars":
        candidates = [_raw_dataset(bars=[])]
    elif case == "not_spot":
        candidates = [_raw_dataset(identity=_identity(market_type="FUTURES"))]
    elif case == "unfinished":
        bars = _bars()
        for bar in bars:
            bar["available_at_ms"] = bar["close_time_ms"] + 1
        candidates = [_raw_dataset(bars=bars)]
    else:
        bars = _bars()
        for bar in bars:
            bar["close_time_ms"] = bar["open_time_ms"] + 30_000
            bar["available_at_ms"] = bar["close_time_ms"]
        candidates = [_raw_dataset(bars=bars)]

    with pytest.raises(ValueError, match=expected):
        validate_dataset_set(candidates)
    # Positive control for the same helper: a complete BAR_V1 calendar passes.
    assert validate_dataset_set([_raw_dataset()]) == [_raw_dataset()]


# ---------------------------------------------------------------- 3.7 -------


@pytest.mark.asyncio
async def test_p3i_3_7_executor_rechecks_frozen_provenance_before_running():
    store = MemoryResearchStore()
    data, _link = await _registered(store)
    app = BacktestApplication(store, eligibility=_always_true)
    record = await app.submit(_manifest([data["dataset_id"]]))
    dataset_id = data["dataset_id"]
    executor = ResearchExecutor(store)

    # Provenance changed while the job was queued: execution must refuse.
    store.links[dataset_id]["provenance_hash"] = "f" * 64
    with pytest.raises(ValueError, match="Frozen provenance mismatch"):
        await executor.execute(record)
    store.links[dataset_id]["provenance_hash"] = PROVENANCE_HASH

    # Missing frozen provenance is refused even with a self-consistent hash.
    tampered = dict(record)
    tampered_manifest = dict(record["manifest"])
    tampered_manifest.pop("provenance")
    tampered["manifest"] = tampered_manifest
    tampered["manifest_hash"] = digest(tampered_manifest)
    with pytest.raises(ValueError, match="Frozen provenance is required"):
        await executor.execute(tampered)

    store.links.pop(dataset_id)
    with pytest.raises(ValueError, match="Frozen provenance mismatch"):
        await executor.execute(record)


# ---------------------------------------------------------------- API -------


def _request(application=None, store=None):
    state = SimpleNamespace(research_application=application, research_store=store)
    return SimpleNamespace(app=SimpleNamespace(state=state))


@pytest.mark.asyncio
async def test_p3i_api_retry_maps_outage_to_503_and_eligibility_to_409():
    store = MemoryResearchStore()
    outage = UnavailableBacktestApplication(store)
    with pytest.raises(HTTPException) as unavailable:
        await retry_backtest("bt_p3i_missing", _request(outage))
    assert unavailable.value.status_code == 503
    assert unavailable.value.detail == HISTORY_STORE_UNAVAILABLE
    with pytest.raises(HTTPException) as submitted:
        await submit_backtest(BacktestSubmitRequest(manifest=_manifest([])), _request(outage))
    assert submitted.value.status_code == 422
    assert submitted.value.detail == HISTORY_STORE_UNAVAILABLE

    data, link = await _registered(store)
    app, _bridge, history = await _gate(
        store,
        links={data["dataset_id"]: link},
        rows={HISTORY_ID: _published_row()},
    )
    record = await app.submit(_manifest([data["dataset_id"]]))
    claimed = await store.claim("p3i-api-worker")
    owner, token = "p3i-api-worker", claimed["fencing_token"]
    await store.finish(record["run_id"], owner, token, error="boom")
    history.rows[HISTORY_ID]["status"] = "PENDING"
    with pytest.raises(HTTPException) as conflict:
        await retry_backtest(record["run_id"], _request(app))
    assert conflict.value.status_code == 409
    assert DATASET_PROVENANCE_UNTRUSTED in conflict.value.detail
    assert (await store.get(record["run_id"]))["status"] == "FAILED"


@pytest.mark.asyncio
async def test_p3i_api_submit_and_retry_refuse_without_an_application():
    with pytest.raises(HTTPException) as submitted:
        await submit_backtest(BacktestSubmitRequest(manifest=_manifest([])), _request())
    assert submitted.value.status_code == 503
    with pytest.raises(HTTPException) as retried:
        await retry_backtest("bt_p3i_missing", _request())
    assert retried.value.status_code == 503
