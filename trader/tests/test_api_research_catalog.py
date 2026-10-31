"""Research catalog and run-control API tests."""

from __future__ import annotations

import httpx
import pytest

from trader.api.trading_app import create_trading_app
from trader.trading.jobs import BacktestApplication, MemoryResearchStore


def identity(**overrides):
    values = dict(
        venue="BINANCE",
        market_type="SPOT",
        symbol="BTCUSDT",
        base_asset="BTC",
        quote_asset="USDT",
        interval="1m",
        source="binance_vision",
        version="binance_vision_1m_v1_aaaaaaaaaaaa",
    )
    values.update(overrides)
    return values


def bars(count: int = 4):
    return [
        {
            "open_time_ms": index * 60_000,
            "close_time_ms": index * 60_000 + 60_000,
            "available_at_ms": index * 60_000 + 60_000,
            "open": "100",
            "high": "101",
            "low": "99",
            "close": "100",
            "volume": "1",
        }
        for index in range(count)
    ]


def build_client(store=None, application=None):
    store = store or MemoryResearchStore()
    application = application or BacktestApplication(store)
    app = create_trading_app(
        research_store=store,
        research_application=application,
        include_history=False,
    )
    return (
        httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://test"),
        store,
        application,
    )


@pytest.mark.asyncio
async def test_catalog_lists_metadata_without_the_bar_payload():
    client, store, _ = build_client()
    async with client:
        data = await store.register_dataset(identity(), bars())

        response = await client.get("/v1/research/datasets")

        assert response.status_code == 200, response.text
        entries = response.json()
        assert len(entries) == 1
        entry = entries[0]
        assert entry["dataset_id"] == data["dataset_id"]
        assert entry["symbol"] == "BTCUSDT"
        assert entry["base_asset"] == "BTC"
        assert entry["quote_asset"] == "USDT"
        assert entry["interval"] == "1m"
        assert entry["bar_count"] == 4
        assert entry["official_eligible"] is False
        assert "bars" not in entry
        assert "document" not in entry


@pytest.mark.asyncio
async def test_catalog_filters_by_symbol_month_and_official_eligibility():
    client, store, _ = build_client()
    async with client:
        btc = await store.register_dataset(identity(), bars())
        eth = await store.register_dataset(identity(symbol="ETHUSDT", base_asset="ETH"), bars())

        by_symbol = await client.get("/v1/research/datasets", params={"symbol": "ETHUSDT"})
        assert [item["dataset_id"] for item in by_symbol.json()] == [eth["dataset_id"]]

        other_month = await client.get("/v1/research/datasets", params={"month": "2026-09"})
        assert other_month.json() == []

        official = await client.get("/v1/research/datasets", params={"official_only": "true"})
        assert official.json() == []
        assert btc["dataset_id"] != eth["dataset_id"]


@pytest.mark.asyncio
async def test_strategy_catalogue_lists_installed_modules():
    client, _, _ = build_client()
    async with client:
        response = await client.get("/v1/research/strategies")

        assert response.status_code == 200, response.text
        entries = response.json()
        modules = {entry["module_path"] for entry in entries}
        assert "trader.strategies.ema_cross_btc" in modules
        for entry in entries:
            assert entry["code_hash"]
            assert entry["params"]
            assert entry["module_path"].startswith("trader.strategies.")


@pytest.mark.asyncio
async def test_submit_rejects_non_event_engines():
    client, store, _ = build_client()
    async with client:
        data = await store.register_dataset(identity(), bars())
        response = await client.post(
            "/v1/research/backtests",
            json={
                "manifest": {
                    "dataset_ids": [data["dataset_id"]],
                    "strategy": {"module_path": "trader.strategies.ema_cross_btc"},
                    "engine": "vectorbt",
                }
            },
        )

        assert response.status_code == 422, response.text
        assert "event runtime" in response.json()["detail"]


@pytest.mark.asyncio
async def test_submit_rejects_foreign_dataset_ids():
    client, _, _ = build_client()
    async with client:
        response = await client.post(
            "/v1/research/backtests",
            json={
                "manifest": {
                    "dataset_ids": ["hds_legacy"],
                    "strategy": {"module_path": "trader.strategies.ema_cross_btc"},
                }
            },
        )

        assert response.status_code == 422, response.text
        assert "ds_" in response.json()["detail"]


@pytest.mark.asyncio
async def test_submit_requires_trusted_eligibility_when_a_checker_is_configured():
    store = MemoryResearchStore()

    async def not_eligible(dataset_id):
        return False

    application = BacktestApplication(store, eligibility=not_eligible)
    client, _, _ = build_client(store=store, application=application)
    async with client:
        data = await store.register_dataset(identity(), bars())
        response = await client.post(
            "/v1/research/backtests",
            json={
                "manifest": {
                    "dataset_ids": [data["dataset_id"]],
                    "strategy": {"module_path": "trader.strategies.ema_cross_btc"},
                }
            },
        )

        assert response.status_code == 422, response.text
        assert "provenance" in response.json()["detail"]


@pytest.mark.asyncio
async def test_retry_conflicts_when_the_run_has_not_failed():
    client, store, application = build_client()
    async with client:
        data = await store.register_dataset(identity(), bars())
        record = await application.submit(
            {
                "dataset_ids": [data["dataset_id"]],
                "strategy": {"module_path": "trader.strategies.ema_cross_btc"},
            }
        )

        response = await client.post(f"/v1/research/backtests/{record['run_id']}/retry")

        assert response.status_code == 409, response.text
        assert "only FAILED runs can be retried" in response.json()["detail"]


@pytest.mark.asyncio
async def test_retry_returns_404_for_unknown_runs():
    client, _, _ = build_client()
    async with client:
        response = await client.post("/v1/research/backtests/bt_missing/retry")

        assert response.status_code == 404, response.text


@pytest.mark.asyncio
async def test_retry_requeues_a_failed_run_and_keeps_the_frozen_manifest():
    client, store, application = build_client()
    async with client:
        data = await store.register_dataset(identity(), bars())
        record = await application.submit(
            {
                "dataset_ids": [data["dataset_id"]],
                "strategy": {"module_path": "trader.strategies.ema_cross_btc"},
            }
        )
        run_id = record["run_id"]
        claimed = await store.claim("worker-1")
        await store.finish(run_id, "worker-1", claimed["fencing_token"], error="boom")

        response = await client.post(f"/v1/research/backtests/{run_id}/retry")

        assert response.status_code == 202, response.text
        payload = response.json()
        assert payload["status"] == "QUEUED"
        assert payload["run_id"] == run_id
        assert payload["manifest_hash"] == record["manifest_hash"]
        assert payload["manifest"] == record["manifest"]
