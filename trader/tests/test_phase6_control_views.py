"""Phase-6 Control compatibility views are backed by the unified account."""

from __future__ import annotations

import asyncio
from decimal import Decimal

import pytest
from fastapi.testclient import TestClient

from trader.api.trading_app import create_trading_app
from trader.trading.models import ReconcileSnapshot
from trader.trading.risk import RiskPolicy
from trader.trading.service import TradingService
from trader.trading.store import MemoryTradingStore


class _NoExchange:
    async def place_order(self, *args, **kwargs):
        raise AssertionError("control views must never reach the venue")

    async def cancel_order(self, *args, **kwargs):
        raise AssertionError("control views must never reach the venue")

    async def query_order(self, *args, **kwargs):
        raise AssertionError("control views must never reach the venue")


async def _service(account_id: str = "acct") -> TradingService:
    store = MemoryTradingStore()
    service = TradingService(store, _NoExchange(), RiskPolicy(), clock=lambda: 1_000_000)
    await service.recover(
        ReconcileSnapshot(
            account_id=account_id,
            checked_at_ms=1_000_000,
            generation=1,
            balances={"USDT": Decimal("1000"), "BTC": Decimal("0.01")},
            marks={"BTCUSDT": {"price": Decimal("100"), "at_ms": 1_000_000}},
        )
    )
    async with store.transaction(account_id) as state:
        state.control["runtime"] = {
            "owner": "owner",
            "token": 1,
            "ready": True,
            "at_ms": 1_000_000,
            "reasons": [],
            "strategy": {
                "deployment_id": "dep-1",
                "strategy_id": "trader.strategies.fire_test",
                "book_id": "book:trader.strategies.fire_test",
                "version": "1",
                "symbols": ["BTCUSDT"],
                "warmup_events": 0,
                "warmup_state": "WARM",
                "warmup_reason": "",
                "warmup_remaining": 0,
                "strategy_ready": True,
            },
        }
    return service


@pytest.fixture
def client(monkeypatch) -> TestClient:
    monkeypatch.setenv("QTS_ACCOUNT_ID", "acct")
    monkeypatch.setenv("QTS_INSTRUMENTS", '{"BTCUSDT":["BTC","USDT"]}')
    service = asyncio.run(_service())
    return TestClient(create_trading_app(trading_service=service))


def test_monitor_orders_and_account_views_read_the_unified_state(client: TestClient) -> None:
    snapshot = client.get("/v1/monitor/snapshot")
    assert snapshot.status_code == 200
    body = snapshot.json()
    assert body["snapshot_source"] == "new_stack_account_state"
    assert body["killswitch_level"] == 0
    assert client.get("/v1/orders").json() == []
    assert client.get("/v1/executions").json() == []
    assert client.get("/v1/brokers").json()[0]["account_id"] == "acct"
    assert client.get("/v1/portfolio/positions").status_code == 200
    assert client.get("/v1/reconciler/report").status_code == 200
    pairs = client.get("/v1/exchange/trading-pairs").json()
    assert pairs["pairs"][0]["symbol"] == "BTCUSDT"


def test_strategy_status_comes_from_persisted_runtime_metadata(client: TestClient) -> None:
    loaded = client.get("/v1/strategies/loaded").json()
    assert loaded[0]["deployment_id"] == "dep-1"
    assert loaded[0]["strategy_id"] == "trader.strategies.fire_test"
    assert loaded[0]["strategy_ready"] is True
    status = client.get("/v1/deployments/dep-1/status")
    assert status.status_code == 200
    assert status.json()["warmup_state"] == "WARM"
    events = client.get("/v1/deployments/dep-1/events")
    assert events.status_code == 200


def test_killswitch_maps_to_the_durable_control_flag(client: TestClient) -> None:
    response = client.post(
        "/v1/killswitch",
        json={"scope": "GLOBAL", "level": 1, "reason": "operator drill", "updated_by": "test"},
    )
    assert response.status_code == 200
    assert response.json()["level"] == 1
    assert client.get("/v1/killswitch").json()["level"] == 1
    resumed = client.post(
        "/v1/killswitch",
        json={"scope": "GLOBAL", "level": 0, "reason": "resume", "updated_by": "test"},
    )
    assert resumed.json()["level"] == 0


def test_retired_lifecycle_and_replay_surfaces_refuse_explicitly(client: TestClient) -> None:
    assert client.post("/v1/strategies/x/load").status_code == 410
    assert client.post("/v1/deployments/dep-1/start").status_code == 410
    assert client.post("/v1/replay").status_code == 410
    assert client.post("/v1/reconciler/trigger").status_code == 503
    assert client.post("/v1/orders/some-order/cancel").status_code == 503


def test_no_fact_write_route_is_mounted(client: TestClient) -> None:
    paths = {getattr(route, "path", "") for route in client.app.routes}
    for path in (
        "/v1/trading/executions",
        "/v1/trading/observations",
        "/v1/trading/reconcile",
        "/v1/trading/resolve/{code}",
    ):
        assert path not in paths
