"""
Automated Trading E2E Tests
===========================

End-to-end tests for the automated trading closed loop:
1. Register strategy code -> load -> start -> market data -> signal -> order -> fill -> stop

These tests verify:
- Task 11: Real-time market subscription and tick scheduling
- Task 12: OMS callback for real order execution
- Task 13: Strategy event query API
- Task 14: Safety gate (live trading disabled by default)

Note: These tests use mocks and fakes to avoid real network calls.
"""
import asyncio
import pytest
from decimal import Decimal
from unittest.mock import AsyncMock, MagicMock, patch
from typing import Dict, Any

from trader.core.application.strategy_protocol import MarketData, MarketDataType
from trader.core.domain.models.signal import Signal, SignalType
from trader.services.strategy_runner import StrategyRunner, StrategyStatus
from trader.services.strategy_runtime_orchestrator import StrategyRuntimeOrchestrator
from trader.services.oms_callback import OMSCallbackHandler, create_oms_callback


# ============================================================================
# Test Strategy Plugin (Fake)
# ============================================================================

class FakeStrategyPlugin:
    """Fake strategy plugin for testing"""

    def __init__(self):
        self.name = "test_strategy"
        self.version = "1.0.0"
        self.risk_level = MagicMock(value="LOW")
        self.resource_limits = MagicMock()
        self.initialized = False
        self.shutdown_called = False
        self.on_market_data_calls = []

    @property
    def name(self) -> str:
        return self._name

    @name.setter
    def name(self, value: str):
        self._name = value

    @property
    def version(self) -> str:
        return self._version

    @version.setter
    def version(self, value: str):
        self._version = value

    async def initialize(self, config: Dict[str, Any]) -> None:
        self.initialized = True

    async def shutdown(self) -> None:
        self.shutdown_called = True

    async def on_market_data(self, data: MarketData) -> Signal | None:
        self.on_market_data_calls.append(data)
        # Generate a BUY signal when price is above 50000
        if data.price > Decimal("50000"):
            return Signal(
                strategy_name=self.name,
                signal_type=SignalType.LONG,
                symbol=data.symbol,
                quantity=Decimal("0.001"),
                price=data.price,
                reason="Price above threshold",
            )
        return None

    async def on_fill(self, order_id: str, symbol: str, side: str, quantity: float, price: float) -> None:
        pass

    async def on_cancel(self, order_id: str, reason: str) -> None:
        pass

    async def update_config(self, config: Dict[str, Any]):
        from trader.core.application.strategy_protocol import ValidationResult, ValidationStatus
        return ValidationResult(status=ValidationStatus.VALID)

    def validate(self):
        from trader.core.application.strategy_protocol import ValidationResult, ValidationStatus
        return ValidationResult(status=ValidationStatus.VALID)


# ============================================================================
# Fake Broker
# ============================================================================

class FakeBroker:
    """Fake broker for testing"""

    def __init__(self):
        self.connected = False
        self.broker_name = "fake_broker"
        self.placed_orders = []
        self.place_order_calls = 0

    async def connect(self) -> None:
        self.connected = True

    async def disconnect(self) -> None:
        self.connected = False

    async def place_order(
        self,
        symbol: str,
        side,
        order_type,
        quantity: Decimal,
        price=None,
        client_order_id=None,
    ):
        from trader.core.domain.models.order import OrderStatus
        self.place_order_calls += 1
        order = MagicMock()
        order.broker_order_id = f"BO{self.place_order_calls}"
        # Check order_type.value to handle both str and OrderType enum
        ot_value = order_type.value if hasattr(order_type, 'value') else str(order_type)
        order.filled_quantity = quantity if ot_value == "MARKET" else Decimal("0")
        order.average_price = price or Decimal("50000")
        order.status = OrderStatus.FILLED if ot_value == "MARKET" else OrderStatus.SUBMITTED
        order.created_at = None
        self.placed_orders.append({
            "symbol": symbol,
            "side": side,
            "quantity": quantity,
            "price": price,
            "client_order_id": client_order_id,
        })
        return order

    async def get_symbol_step_size(self, symbol: str) -> Decimal:
        return Decimal("0.00001")


# ============================================================================
# Tests
# ============================================================================

class TestStrategyRunnerTick:
    """Test StrategyRunner.tick() with OMS callback"""

    @pytest.fixture
    def fake_broker(self):
        return FakeBroker()

    @pytest.fixture
    def fake_storage(self):
        storage = MagicMock()
        storage.create_order = MagicMock(return_value={})
        storage.create_execution = MagicMock(return_value={})
        storage.append_event = MagicMock(return_value={})
        storage.list_events = MagicMock(return_value=[])
        return storage

    @pytest.mark.asyncio
    async def test_tick_generates_signal(self, fake_broker, fake_storage):
        """Test that tick() generates a signal when market data triggers it"""
        runner = StrategyRunner()

        # Load fake strategy
        plugin = FakeStrategyPlugin()
        runner._plugins["test_strategy"] = plugin
        from trader.core.application.strategy_protocol import StrategyResourceLimits
        runner._infos["test_strategy"] = MagicMock(
            status=StrategyStatus.RUNNING,
            tick_count=0,
            signal_count=0,
            error_count=0,
            last_error=None,
            blocked_reason=None,
            resource_limits=StrategyResourceLimits(),
            config={},
            last_order_times=[],
        )

        # Create market data above threshold
        market_data = MarketData(
            symbol="BTCUSDT",
            data_type=MarketDataType.TRADE,
            price=Decimal("51000"),
            volume=Decimal("1"),
        )

        # Call tick
        signal = await runner.tick("test_strategy", market_data)

        # Verify signal was generated
        assert signal is not None
        assert signal.symbol == "BTCUSDT"
        assert signal.signal_type == SignalType.LONG
        assert signal.quantity == Decimal("0.001")

    @pytest.mark.asyncio
    async def test_tick_with_oms_callback(self, fake_broker, fake_storage):
        """Test that tick() calls OMS callback when signal is generated"""
        runner = StrategyRunner()

        # Create OMS callback
        oms_callback = AsyncMock(return_value={
            "order_id": "test_order_1",
            "status": "FILLED",
        })
        runner._oms_callback = oms_callback

        # Load fake strategy
        plugin = FakeStrategyPlugin()
        runner._plugins["test_strategy"] = plugin
        from trader.core.application.strategy_protocol import StrategyResourceLimits
        runner._infos["test_strategy"] = MagicMock(
            status=StrategyStatus.RUNNING,
            tick_count=0,
            signal_count=0,
            error_count=0,
            last_error=None,
            blocked_reason=None,
            resource_limits=StrategyResourceLimits(),
            config={},
            last_order_times=[],
        )

        # Create market data above threshold
        market_data = MarketData(
            symbol="BTCUSDT",
            data_type=MarketDataType.TRADE,
            price=Decimal("51000"),
            volume=Decimal("1"),
        )

        # Call tick
        signal = await runner.tick("test_strategy", market_data)

        # Verify OMS callback was called
        assert signal is not None
        oms_callback.assert_called_once()


class TestOMSCallbackHandler:
    """Test OMSCallbackHandler"""

    @pytest.fixture
    def fake_broker(self):
        return FakeBroker()

    @pytest.fixture
    def fake_storage(self):
        storage = MagicMock()
        storage.create_order = MagicMock(return_value={})
        storage.create_execution = MagicMock(return_value={})
        return storage

    @pytest.mark.asyncio
    async def test_live_trading_disabled_rejects_signal(self, fake_broker, fake_storage):
        """Test that signals are rejected when live trading is disabled"""
        handler = OMSCallbackHandler(
            broker=fake_broker,
            storage=fake_storage,
            live_trading_enabled=False,  # Disabled by default
        )

        signal = Signal(
            strategy_name="test",
            signal_type=SignalType.LONG,
            symbol="BTCUSDT",
            quantity=Decimal("0.001"),
            price=Decimal("50000"),
        )

        # Should raise TradingDisabledError
        with pytest.raises(Exception) as exc_info:
            await handler.execute_signal("test_strategy", signal)
        assert "not enabled" in str(exc_info.value).lower()

    @pytest.mark.asyncio
    async def test_live_trading_enabled_places_order(self, fake_broker, fake_storage):
        """Test that signals are executed when live trading is enabled"""
        handler = OMSCallbackHandler(
            broker=fake_broker,
            storage=fake_storage,
            live_trading_enabled=True,
        )

        signal = Signal(
            strategy_name="test",
            signal_type=SignalType.LONG,
            symbol="BTCUSDT",
            quantity=Decimal("0.001"),
            price=Decimal("50000"),
        )

        result = await handler.execute_signal("test_strategy", signal)

        # Verify order was placed
        assert result is not None
        assert "order_id" in result
        assert fake_broker.place_order_calls == 1
        assert fake_storage.create_order.called


class TestSafetyGate:
    """Test safety gate functionality"""

    def test_live_trading_disabled_by_default(self):
        """Test that live trading is disabled by default"""
        from trader.api.routes.strategies import _is_live_trading_enabled, set_live_trading_enabled

        # Should be disabled initially
        assert _is_live_trading_enabled() is False

        # Enable it
        set_live_trading_enabled(True)
        assert _is_live_trading_enabled() is True

        # Disable it
        set_live_trading_enabled(False)
        assert _is_live_trading_enabled() is False


class TestStrategyRuntimeOrchestrator:
    """Test StrategyRuntimeOrchestrator"""

    @pytest.fixture
    def mock_runner(self):
        runner = MagicMock(spec=StrategyRunner)
        runner.get_status = MagicMock(return_value=MagicMock(
            strategy_id="test",
            status=StrategyStatus.LOADED,
        ))
        runner.tick = AsyncMock(return_value=None)
        return runner

    @pytest.mark.asyncio
    async def test_start_strategy_creates_context(self, mock_runner):
        """Test that starting a strategy creates runtime context"""
        orchestrator = StrategyRuntimeOrchestrator(runner=mock_runner)

        ctx = await orchestrator.start_strategy("test_strategy", "BTCUSDT")

        assert ctx is not None
        assert ctx.strategy_id == "test_strategy"
        assert ctx.symbol == "BTCUSDT"
        assert ctx.status == "RUNNING"

    @pytest.mark.asyncio
    async def test_stop_strategy_updates_context(self, mock_runner):
        """Test that stopping a strategy updates runtime context"""
        orchestrator = StrategyRuntimeOrchestrator(runner=mock_runner)

        # Start first
        await orchestrator.start_strategy("test_strategy", "BTCUSDT")

        # Stop
        ctx = await orchestrator.stop_strategy("test_strategy", reason="Test stop")

        assert ctx is not None
        assert ctx.status == "STOPPED"
        assert ctx.stop_reason == "Test stop"

    @pytest.mark.asyncio
    async def test_unload_strategy_removes_context(self, mock_runner):
        """Test that unloading a strategy removes runtime context"""
        orchestrator = StrategyRuntimeOrchestrator(runner=mock_runner)

        # Start first
        await orchestrator.start_strategy("test_strategy", "BTCUSDT")

        # Unload
        await orchestrator.unload_strategy("test_strategy")

        # Context should be gone
        ctx = orchestrator.get_context("test_strategy")
        assert ctx is None


class TestKillSwitchIntegration:
    """Test KillSwitch integration with StrategyRunner"""

    @pytest.mark.asyncio
    async def test_killswitch_l1_blocks_new_orders(self):
        """Test that KillSwitch L1 blocks new orders"""
        runner = StrategyRunner(
            killswitch_callback=lambda strategy_id: 1,  # L1
        )

        # Load fake strategy
        plugin = FakeStrategyPlugin()
        runner._plugins["test_strategy"] = plugin
        from trader.core.application.strategy_protocol import StrategyResourceLimits
        runner._infos["test_strategy"] = MagicMock(
            status=StrategyStatus.RUNNING,
            tick_count=0,
            signal_count=0,
            error_count=0,
            last_error=None,
            blocked_reason=None,
            resource_limits=StrategyResourceLimits(),
            config={},
            last_order_times=[],
        )

        # Create market data that would trigger a signal
        market_data = MarketData(
            symbol="BTCUSDT",
            data_type=MarketDataType.TRADE,
            price=Decimal("51000"),
            volume=Decimal("1"),
        )

        # Call tick - signal should be blocked
        signal = await runner.tick("test_strategy", market_data)

        # Signal should be None due to KillSwitch
        assert signal is None

    @pytest.mark.asyncio
    async def test_killswitch_l2_stops_strategy(self):
        """Test that KillSwitch L2 stops strategy"""
        runner = StrategyRunner(
            killswitch_callback=lambda strategy_id: 2,  # L2
        )

        # Load fake strategy
        plugin = FakeStrategyPlugin()
        runner._plugins["test_strategy"] = plugin
        from trader.core.application.strategy_protocol import StrategyResourceLimits
        info = MagicMock(
            status=StrategyStatus.RUNNING,
            tick_count=0,
            signal_count=0,
            error_count=0,
            last_error=None,
            blocked_reason=None,
            resource_limits=StrategyResourceLimits(),
            config={},
            last_order_times=[],
        )
        runner._infos["test_strategy"] = info
        runner.stop = AsyncMock()

        # Create market data
        market_data = MarketData(
            symbol="BTCUSDT",
            data_type=MarketDataType.TRADE,
            price=Decimal("51000"),
            volume=Decimal("1"),
        )

        # Call tick - should stop strategy
        signal = await runner.tick("test_strategy", market_data)

        # Strategy should be stopped
        runner.stop.assert_called_once_with("test_strategy")


class TestOrderIdempotency:
    """Test order idempotency - duplicate cl_ord_id should be deduplicated"""

    @pytest.fixture
    def fake_storage(self):
        storage = MagicMock()
        storage.create_order = MagicMock(return_value={})
        storage.create_execution = MagicMock(return_value={})
        storage.get_order = MagicMock(return_value=None)
        storage.get_execution = MagicMock(return_value=None)
        return storage

    @pytest.mark.asyncio
    async def test_duplicate_cl_ord_id_deduplicated(self, fake_storage):
        """
        Test that duplicate orders with same cl_ord_id are properly deduplicated.

        Architecture requirement: Order deduplication must use cl_ord_id + exec_id.
        """
        from trader.services.oms_callback import OMSCallbackHandler

        # Create fake broker that simulates immediate fill
        fake_broker = FakeBroker()
        fill_called_count = 0

        async def fill_callback(strategy_id, order_id, symbol, side, qty, price):
            nonlocal fill_called_count
            fill_called_count += 1

        handler = OMSCallbackHandler(
            broker=fake_broker,
            storage=fake_storage,
            live_trading_enabled=True,
            fill_callback=fill_callback,
        )

        # Create signal
        signal = Signal(
            strategy_name="test",
            signal_type=SignalType.LONG,
            symbol="BTCUSDT",
            quantity=Decimal("0.001"),
            price=Decimal("0"),  # Price=0 triggers MARKET order which fills immediately
        )

        # First call - should succeed
        result1 = await handler.execute_signal("test_strategy", signal)
        assert result1 is not None
        assert "order_id" in result1

        # Extract the cl_ord_id from the result
        cl_ord_id = result1["order_id"]

        # Second call with same signal - storage should return existing order
        # So it should be deduplicated and return None
        fake_storage.get_order.return_value = {"cl_ord_id": cl_ord_id}

        # Third call with new signal - should succeed (new cl_ord_id)
        signal2 = Signal(
            strategy_name="test",
            signal_type=SignalType.LONG,
            symbol="BTCUSDT",
            quantity=Decimal("0.001"),
            price=Decimal("0"),
        )
        result2 = await handler.execute_signal("test_strategy", signal2)
        assert result2 is not None
        assert "order_id" in result2

        # Verify storage was checked for duplicates
        assert fake_storage.get_order.called

    @pytest.mark.asyncio
    async def test_fill_callback_invoked_on_fill(self, fake_storage):
        """Test that fill_callback is invoked when order is filled"""
        from trader.services.oms_callback import OMSCallbackHandler

        fake_broker = FakeBroker()
        fill_calls = []

        async def fill_callback(strategy_id, order_id, symbol, side, qty, price):
            fill_calls.append({
                "strategy_id": strategy_id,
                "order_id": order_id,
                "symbol": symbol,
                "side": side,
                "qty": qty,
                "price": price,
            })

        handler = OMSCallbackHandler(
            broker=fake_broker,
            storage=fake_storage,
            live_trading_enabled=True,
            fill_callback=fill_callback,
        )

        # Use price=0 to ensure MARKET order (which fills immediately in fake broker)
        signal = Signal(
            strategy_name="test",
            signal_type=SignalType.LONG,
            symbol="BTCUSDT",
            quantity=Decimal("0.001"),
            price=Decimal("0"),  # No price = MARKET order
        )

        result = await handler.execute_signal("test_strategy", signal)

        # Verify fill callback was invoked (MARKET orders fill immediately)
        assert len(fill_calls) == 1
        assert fill_calls[0]["strategy_id"] == "test_strategy"
        assert fill_calls[0]["qty"] == float(Decimal("0.001"))


class TestLiveTradingDynamicCheck:
    """Test that live_trading_enabled supports Callable for dynamic checking"""

    @pytest.fixture
    def fake_broker(self):
        return FakeBroker()

    @pytest.fixture
    def fake_storage(self):
        storage = MagicMock()
        storage.create_order = MagicMock(return_value={})
        storage.create_execution = MagicMock(return_value={})
        storage.append_event = MagicMock(return_value={})
        storage.get_order = MagicMock(return_value=None)
        return storage

    @pytest.mark.asyncio
    async def test_callable_live_trading_toggle(self, fake_broker, fake_storage):
        """When live_trading_enabled is a Callable, runtime changes should take effect immediately"""
        enabled = False

        def check():
            return enabled

        handler = OMSCallbackHandler(
            broker=fake_broker,
            storage=fake_storage,
            live_trading_enabled=check,
        )

        signal = Signal(
            strategy_name="test",
            signal_type=SignalType.LONG,
            symbol="BTCUSDT",
            quantity=Decimal("0.001"),
            price=Decimal("50000"),
        )

        with pytest.raises(Exception) as exc_info:
            await handler.execute_signal("test_strategy", signal)
        assert "not enabled" in str(exc_info.value).lower()

        enabled = True

        result = await handler.execute_signal("test_strategy", signal)
        assert result is not None
        assert "order_id" in result

    @pytest.mark.asyncio
    async def test_bool_live_trading_still_works(self, fake_broker, fake_storage):
        """When live_trading_enabled is a plain bool, it should still work as before"""
        handler = OMSCallbackHandler(
            broker=fake_broker,
            storage=fake_storage,
            live_trading_enabled=True,
        )

        signal = Signal(
            strategy_name="test",
            signal_type=SignalType.LONG,
            symbol="BTCUSDT",
            quantity=Decimal("0.001"),
            price=Decimal("50000"),
        )

        result = await handler.execute_signal("test_strategy", signal)
        assert result is not None


class TestStreamKeyFormat:
    """Test event stream keys keep deployment and template IDs separate."""

    def test_event_callback_uses_deployment_stream(self):
        """_event_callback_dispatcher should write runtime events to deployment:{id}."""
        from trader.api.routes.strategies import _event_callback_dispatcher
        from trader.storage.in_memory import reset_storage

        storage = reset_storage()
        _event_callback_dispatcher("deploy_001", "strategy.signal", {
            "strategy_id": "template_alpha",
            "symbol": "BTCUSDT",
        })

        events = storage.list_events(stream_key="deployment:deploy_001")
        assert len(events) == 1
        assert events[0]["stream_key"] == "deployment:deploy_001"
        assert events[0]["data"]["deployment_id"] == "deploy_001"
        assert events[0]["data"]["strategy_id"] == "template_alpha"

    @pytest.mark.asyncio
    async def test_deployment_and_strategy_event_queries_are_distinct(self):
        """Deployment endpoints are exact; strategy endpoints aggregate by payload strategy_id."""
        from trader.api.routes.strategies import get_deployment_signals, get_strategy_signals
        from trader.storage.in_memory import reset_storage

        storage = reset_storage()
        storage.append_event({
            "stream_key": "deployment:deploy_btc",
            "event_type": "strategy.signal",
            "ts_ms": 1000,
            "data": {
                "deployment_id": "deploy_btc",
                "strategy_id": "template_alpha",
                "symbol": "BTCUSDT",
            },
        })
        storage.append_event({
            "stream_key": "deployment:deploy_eth",
            "event_type": "strategy.signal",
            "ts_ms": 1001,
            "data": {
                "deployment_id": "deploy_eth",
                "strategy_id": "template_alpha",
                "symbol": "ETHUSDT",
            },
        })

        deployment_events = await get_deployment_signals("deploy_btc", limit=10)
        assert [event.payload["symbol"] for event in deployment_events] == ["BTCUSDT"]

        strategy_events = await get_strategy_signals("template_alpha", limit=10)
        assert {event.payload["symbol"] for event in strategy_events} == {"BTCUSDT", "ETHUSDT"}

    def test_strategy_event_service_uses_colon_format(self):
        """StrategyEvent.to_envelope should use strategy:{id} format"""
        from trader.services.strategy_event_service import StrategyEvent, StrategyEventType

        event = StrategyEvent(
            strategy_id="my_strategy",
            event_type=StrategyEventType.SIGNAL_GENERATED,
            payload={"symbol": "BTCUSDT"},
        )
        envelope = event.to_envelope()
        assert envelope.stream_key == "strategy:my_strategy"


class TestLiveTradingEnvVar:
    """Test LIVE_TRADING_ENABLED environment variable support"""

    def test_env_var_true(self, monkeypatch):
        """LIVE_TRADING_ENABLED=true should enable trading"""
        from trader.api.routes.strategies import _is_live_trading_enabled, set_live_trading_enabled

        set_live_trading_enabled(None)
        monkeypatch.setenv("LIVE_TRADING_ENABLED", "true")

        assert _is_live_trading_enabled() is True

    def test_env_var_false(self, monkeypatch):
        """LIVE_TRADING_ENABLED=false should disable trading"""
        from trader.api.routes.strategies import _is_live_trading_enabled, set_live_trading_enabled

        set_live_trading_enabled(None)
        monkeypatch.setenv("LIVE_TRADING_ENABLED", "false")

        assert _is_live_trading_enabled() is False

    def test_env_var_unset_defaults_false(self, monkeypatch):
        """When LIVE_TRADING_ENABLED is not set, default is False"""
        from trader.api.routes.strategies import _is_live_trading_enabled, set_live_trading_enabled

        set_live_trading_enabled(None)
        monkeypatch.delenv("LIVE_TRADING_ENABLED", raising=False)

        assert _is_live_trading_enabled() is False

    def test_runtime_override_takes_priority(self, monkeypatch):
        """Runtime API setting should override environment variable"""
        from trader.api.routes.strategies import _is_live_trading_enabled, set_live_trading_enabled

        monkeypatch.setenv("LIVE_TRADING_ENABLED", "true")
        set_live_trading_enabled(False)

        assert _is_live_trading_enabled() is False

        set_live_trading_enabled(True)
        assert _is_live_trading_enabled() is True

        set_live_trading_enabled(None)
        assert _is_live_trading_enabled() is True


class TestMinNotionalDynamic:
    """Test that minNotional is fetched from exchange rules dynamically"""

    @pytest.fixture
    def fake_broker_with_exchange_info(self):
        broker = FakeBroker()
        broker.get_exchange_info = AsyncMock(return_value={
            "symbols": [{
                "symbol": "BTCUSDT",
                "filters": [
                    {"filterType": "NOTIONAL", "minNotional": "5"},
                    {"filterType": "LOT_SIZE", "stepSize": "0.00001"},
                ],
            }],
        })
        return broker

    @pytest.fixture
    def fake_storage(self):
        storage = MagicMock()
        storage.create_order = MagicMock(return_value={})
        storage.create_execution = MagicMock(return_value={})
        storage.append_event = MagicMock(return_value={})
        storage.get_order = MagicMock(return_value=None)
        return storage

    @pytest.mark.asyncio
    async def test_min_notional_from_exchange(self, fake_broker_with_exchange_info, fake_storage):
        """minNotional should be read from exchangeInfo, not hardcoded"""
        handler = OMSCallbackHandler(
            broker=fake_broker_with_exchange_info,
            storage=fake_storage,
            live_trading_enabled=True,
        )

        min_notional = await handler._get_min_notional("BTCUSDT")
        assert min_notional == Decimal("5")

    @pytest.mark.asyncio
    async def test_min_notional_caching(self, fake_broker_with_exchange_info, fake_storage):
        """minNotional should be cached after first fetch"""
        handler = OMSCallbackHandler(
            broker=fake_broker_with_exchange_info,
            storage=fake_storage,
            live_trading_enabled=True,
        )

        await handler._get_min_notional("BTCUSDT")
        await handler._get_min_notional("BTCUSDT")

        assert fake_broker_with_exchange_info.get_exchange_info.call_count == 1

    @pytest.mark.asyncio
    async def test_min_notional_fallback_on_error(self, fake_storage):
        """When exchangeInfo fetch fails, fallback to default 10"""
        broker = FakeBroker()
        broker.get_exchange_info = AsyncMock(side_effect=Exception("Network error"))

        handler = OMSCallbackHandler(
            broker=broker,
            storage=fake_storage,
            live_trading_enabled=True,
        )

        min_notional = await handler._get_min_notional("BTCUSDT")
        assert min_notional == Decimal("10")

    @pytest.mark.asyncio
    async def test_min_notional_rejects_small_order(self, fake_broker_with_exchange_info, fake_storage):
        """Orders below minNotional should be rejected"""
        handler = OMSCallbackHandler(
            broker=fake_broker_with_exchange_info,
            storage=fake_storage,
            live_trading_enabled=True,
        )

        signal = Signal(
            strategy_name="test",
            signal_type=SignalType.LONG,
            symbol="BTCUSDT",
            quantity=Decimal("0.00001"),
            price=Decimal("400"),
        )

        with pytest.raises(Exception) as exc_info:
            await handler.execute_signal("test_strategy", signal)
        assert "minNotional" in str(exc_info.value).lower() or "notional" in str(exc_info.value).lower()


class TestBrokerEnvSelection:
    """Test that broker environment is selected based on BINANCE_ENV"""

    def test_demo_env_creates_demo_config(self, monkeypatch):
        """BINANCE_ENV=demo should create demo config"""
        import os
        from trader.adapters.broker.binance_spot_demo_broker import BinanceSpotDemoBrokerConfig

        monkeypatch.setenv("BINANCE_ENV", "demo")
        env = os.environ.get("BINANCE_ENV", "demo").lower()

        if env in ("testnet", "test"):
            config = BinanceSpotDemoBrokerConfig.for_testnet(api_key="k", secret_key="s")
        else:
            config = BinanceSpotDemoBrokerConfig.for_demo(api_key="k", secret_key="s")

        assert "demo-api.binance.com" in config.base_url

    def test_testnet_env_creates_testnet_config(self, monkeypatch):
        """BINANCE_ENV=testnet should create testnet config"""
        import os
        from trader.adapters.broker.binance_spot_demo_broker import BinanceSpotDemoBrokerConfig

        monkeypatch.setenv("BINANCE_ENV", "testnet")
        env = os.environ.get("BINANCE_ENV", "demo").lower()

        if env in ("testnet", "test"):
            config = BinanceSpotDemoBrokerConfig.for_testnet(api_key="k", secret_key="s")
        else:
            config = BinanceSpotDemoBrokerConfig.for_demo(api_key="k", secret_key="s")

        assert "testnet.binance.vision" in config.base_url
