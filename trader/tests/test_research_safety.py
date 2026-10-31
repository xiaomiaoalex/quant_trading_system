"""Research invariants that remain observable across frozen retries."""

from decimal import Decimal
from pathlib import Path
from types import SimpleNamespace

import pytest

from trader.tests.test_research_runtime import bars, dataset, identity
from trader.trading.backtest import EventBacktestRuntime, StrategySignal
from trader.trading.jobs import BacktestApplication, MemoryResearchStore
from trader.trading.research import ResearchExecutor


async def frozen_job(store, *, purpose="research_screening", strategy_version="1", bar_list=None):
    data = await store.register_dataset(identity(), bar_list or bars())
    record = await BacktestApplication(store).submit(
        {
            "dataset_ids": [data["dataset_id"]],
            "strategy": {
                "module_path": "trader.strategies.ema_cross_btc",
                "version": strategy_version,
            },
            "purpose": purpose,
        }
    )
    return data, record


def test_sell_holds_are_partitioned_by_instrument():
    def strategy(context, bar):
        if context.bar_index == 0:
            return [StrategySignal(bar["symbol"], "BUY", Decimal("1"))]
        if context.bar_index == 1:
            return [StrategySignal(bar["symbol"], "SELL", Decimal("1"))]
        return []

    result = EventBacktestRuntime(
        datasets=[dataset(), dataset(symbol="ETHUSDT", base_asset="ETH")],
        strategy=strategy,
        initial_cash=Decimal("1000"),
        fee_bps=Decimal("0"),
    ).run()
    assert result.rejections == {}
    assert result.positions == {"BTCUSDT": Decimal("0"), "ETHUSDT": Decimal("0")}
    assert len(result.fills) == 4


@pytest.mark.asyncio
async def test_changed_strategy_source_is_rejected_before_plugin_load(monkeypatch):
    store = MemoryResearchStore()
    data = await store.register_dataset(identity(), bars())
    record = await BacktestApplication(store).submit(
        {
            "dataset_ids": [data["dataset_id"]],
            "strategy": {"module_path": "trader.strategies.ema_cross_btc"},
        }
    )
    original = Path.read_bytes

    def changed_source(path):
        value = original(path)
        if path.name == "ema_cross_btc.py":
            return value + b"\n# source changed after enqueue"
        return value

    monkeypatch.setattr(Path, "read_bytes", changed_source)
    with pytest.raises(ValueError, match="Strategy source changed"):
        await ResearchExecutor(store).execute(record)


@pytest.mark.asyncio
async def test_unfrozen_strategy_is_rejected_before_plugin_load():
    store = MemoryResearchStore()
    data = await store.register_dataset(identity(), bars())
    record = await BacktestApplication(store).submit(
        {
            "dataset_ids": [data["dataset_id"]],
            "strategy": {"module_path": "trader.strategies.ema_cross_btc"},
        }
    )
    del record["manifest"]["strategy"]["code_hash"]
    with pytest.raises(ValueError, match="Frozen strategy code_hash is required"):
        await ResearchExecutor(store).execute(record)


@pytest.mark.asyncio
async def test_executor_rejects_dataset_content_changed_under_frozen_id():
    store = MemoryResearchStore()
    data, record = await frozen_job(store)
    store.datasets[data["dataset_id"]]["bars"][0]["volume"] = "999"
    with pytest.raises(ValueError, match="Dataset content hash mismatch"):
        await ResearchExecutor(store).execute(record)


@pytest.mark.asyncio
async def test_executor_reports_full_manifest_hash_and_bar_limitations():
    store = MemoryResearchStore()
    _, first = await frozen_job(store)
    _, second = await frozen_job(store, strategy_version="2")
    first_result = await ResearchExecutor(store).execute(first)
    second_result = await ResearchExecutor(store).execute(second)
    assert first_result["manifest_hash"] == first["manifest_hash"]
    assert second_result["manifest_hash"] == second["manifest_hash"]
    assert first_result["manifest_hash"] != second_result["manifest_hash"]
    assert first_result["runtime_manifest_hash"] == second_result["runtime_manifest_hash"]
    assert first_result["limitations"] == [
        "BAR data cannot validate queue priority or market impact."
    ]


@pytest.mark.asyncio
async def test_screening_result_cannot_be_admission_evidence():
    store = MemoryResearchStore()
    _, record = await frozen_job(store)
    result = await ResearchExecutor(store).execute(record)
    assert result["errors"] == []
    assert result["admissible"] is False


@pytest.mark.asyncio
async def test_clean_admission_result_retains_evidence_eligibility():
    store = MemoryResearchStore()
    _, record = await frozen_job(store, purpose="admission_evidence")
    result = await ResearchExecutor(store).execute(record)
    assert result["errors"] == []
    assert result["admissible"] is True


@pytest.mark.asyncio
async def test_manifest_changed_after_enqueue_is_rejected():
    store = MemoryResearchStore()
    _, record = await frozen_job(store)
    record["manifest"]["initial_cash"] = "2000"
    with pytest.raises(ValueError, match="Frozen manifest hash mismatch"):
        await ResearchExecutor(store).execute(record)


@pytest.mark.asyncio
@pytest.mark.parametrize("version", [None, "spot_bar_v1"])
async def test_legacy_runtime_manifest_cannot_execute_under_shared_core(version):
    from trader.trading.datasets import digest

    store = MemoryResearchStore()
    _, record = await frozen_job(store)
    if version is None:
        record["manifest"].pop("runtime_version")
    else:
        record["manifest"]["runtime_version"] = version
    record["manifest_hash"] = digest(record["manifest"])
    with pytest.raises(ValueError, match="Legacy runtime version"):
        await ResearchExecutor(store).execute(record)


@pytest.mark.asyncio
async def test_current_job_result_and_manifest_share_explicit_runtime_version():
    store = MemoryResearchStore()
    _, record = await frozen_job(store)
    result = await ResearchExecutor(store).execute(record)
    assert record["manifest"]["runtime_version"] == "spot_bar_v2_shared"
    assert result["runtime_version"] == record["manifest"]["runtime_version"]


@pytest.mark.asyncio
@pytest.mark.parametrize("version", [None, "spot_bar_v1"])
async def test_old_event_reports_are_marked_legacy_even_if_engine_name_matches(version):
    store = MemoryResearchStore()
    manifest = {"runtime_version": version} if version else {}
    record = await store.enqueue(manifest)
    claimed = await store.claim("worker")
    await store.finish(
        record["run_id"],
        "worker",
        claimed["fencing_token"],
        result={"engine": "event_runtime", "admissible": True},
    )
    assert (await store.get(record["run_id"]))["model"] == "legacy"


@pytest.mark.asyncio
async def test_execution_error_disqualifies_admission_evidence(monkeypatch):
    class Plugin:
        count = 0

        async def on_market_data(self, market):
            self.count += 1
            if self.count == 1:
                return SimpleNamespace(signal_type=SimpleNamespace(value="BUY"), quantity="60")
            return None

    monkeypatch.setattr("trader.trading.research.load_plugin", lambda module, params: Plugin())
    store = MemoryResearchStore()
    bar_list = bars(2)
    bar_list[1].update(open="200", high="200", low="200", close="200")
    _, record = await frozen_job(store, purpose="admission_evidence", bar_list=bar_list)
    result = await ResearchExecutor(store).execute(record)
    assert result["errors"] == ["INSUFFICIENT_CASH:BTCUSDT"]
    assert result["admissible"] is False
