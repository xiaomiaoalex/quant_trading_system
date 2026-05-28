"""Tests for API lifespan account REST snapshot wiring."""

from types import SimpleNamespace

import pytest


class _ConnectedState:
    value = "CONNECTED"


class _FakeStream:
    state = _ConnectedState()


class _FakeRestCoordinator:
    async def get_server_time(self) -> int:
        return 1_700_000_000_000


class _FakeConnector:
    broker_name = "binance_spot_demo"
    public_stream = _FakeStream()
    private_stream = _FakeStream()
    _private_stream_available = True
    _private_stream_disabled_reason = None

    def __init__(self, *args, **kwargs) -> None:
        self._rest_coordinator = _FakeRestCoordinator()
        self._startup_self_check_passed = False
        self.stopped = False

    def register_fill_handler(self, handler) -> None:
        self.fill_handler = handler

    def register_account_update_handler(self, handler) -> None:
        self.account_handler = handler

    def register_balance_update_handler(self, handler) -> None:
        self.balance_handler = handler

    def register_health_handler(self, handler) -> None:
        self.health_handler = handler

    async def start(self) -> None:
        return None

    async def stop(self) -> None:
        self.stopped = True


class _FakeBroker:
    broker_name = "binance_spot_demo"

    def __init__(self) -> None:
        self.fetch_account_calls = 0

    async def _fetch_account(self) -> dict:
        self.fetch_account_calls += 1
        return {"balances": [{"asset": "USDT", "free": "123", "locked": "0"}]}


class _FakeBridge:
    instances: list["_FakeBridge"] = []

    def __init__(self, account_state, config) -> None:
        self.account_state = account_state
        self.config = config
        self.snapshot_balances = None
        self.snapshot_error = None
        self.periodic_fetch = None
        self.__class__.instances.append(self)

    def on_account_update(self, event: dict) -> None:
        self.last_account_update = event

    def on_balance_update(self, event: dict) -> None:
        self.last_balance_update = event

    async def fetch_and_apply_rest_snapshot(self, fetch_balances) -> bool:
        try:
            self.snapshot_balances = await fetch_balances()
            return True
        except Exception as exc:
            self.snapshot_error = exc
            return False

    def start_periodic_calibration(self, fetch_balances) -> None:
        self.periodic_fetch = fetch_balances


class _FakeCascadeController:
    def __init__(self, *args, **kwargs) -> None:
        self.started = False
        self.stopped = False

    def register_self_protection_callback(self, callback) -> None:
        self.self_protection_callback = callback

    async def on_adapter_health_changed(self, health_report, reason: str) -> None:
        self.last_health = (health_report, reason)

    async def start(self) -> None:
        self.started = True

    async def stop(self) -> None:
        self.stopped = True


class _FakeBackgroundService:
    async def start(self) -> None:
        return None

    async def stop(self) -> None:
        return None


@pytest.mark.asyncio
async def test_lifespan_rest_snapshot_uses_oms_broker_not_connector(monkeypatch) -> None:
    from trader.adapters.binance import degraded_cascade
    from trader.api import main as api_main
    from trader.api.routes import strategies
    from trader.services import account_stream_bridge

    fake_broker = _FakeBroker()
    _FakeBridge.instances.clear()

    async def fake_fill_handler_ready():
        return lambda *args, **kwargs: None

    monkeypatch.setenv("BINANCE_API_KEY", "test_key")
    monkeypatch.setenv("BINANCE_SECRET_KEY", "test_secret")
    monkeypatch.setenv("CRYPTO_RISK_ENABLED", "false")
    monkeypatch.setenv("DISABLE_EXCHANGE_RECONCILIATION", "true")
    monkeypatch.setattr(api_main, "_seed_strategies", lambda: None)
    monkeypatch.setattr(api_main, "BinanceConnector", _FakeConnector)
    monkeypatch.setattr(api_main, "ProcessHeartbeatService", _FakeBackgroundService)
    monkeypatch.setattr(api_main, "ConnectionManager", _FakeBackgroundService)
    monkeypatch.setattr(
        api_main.data_catalog,
        "maybe_start_ohlcv_ingestion_from_env",
        fake_fill_handler_ready,
    )
    monkeypatch.setattr(
        api_main.data_catalog,
        "shutdown_ohlcv_ingestion_worker",
        fake_fill_handler_ready,
    )
    monkeypatch.setattr(strategies, "ensure_fill_handler_ready", fake_fill_handler_ready)
    monkeypatch.setattr(strategies, "get_oms_broker", lambda: fake_broker)
    monkeypatch.setattr(account_stream_bridge, "AccountStreamBridge", _FakeBridge)
    monkeypatch.setattr(
        degraded_cascade,
        "DegradedCascadeController",
        _FakeCascadeController,
    )

    async with api_main.lifespan(SimpleNamespace()):
        bridge = _FakeBridge.instances[0]
        assert bridge.snapshot_error is None
        assert bridge.snapshot_balances == [{"asset": "USDT", "free": "123", "locked": "0"}]
        assert fake_broker.fetch_account_calls == 1
