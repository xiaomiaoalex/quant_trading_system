"""
PostgreSQL Storage Tests
========================
Tests for PostgreSQL event sourcing storage.

These tests are skipped if PostgreSQL is not available.
Set environment variables to enable:
- POSTGRES_CONNECTION_STRING, or
- POSTGRES_HOST, POSTGRES_DB, POSTGRES_USER, POSTGRES_PASSWORD

Note: asyncpg package must be installed for these tests to run.
"""
import pytest
import os
from datetime import datetime, timezone
from dataclasses import dataclass, field
from typing import Dict, Any
from unittest.mock import patch

from trader.adapters.persistence.postgres import (
    PostgreSQLStorage,
    is_postgres_available,
    StoredEvent,
    StoredSnapshot,
    ASYNCPG_AVAILABLE,
)


# 清理全局 PostgreSQL 连接池的 fixture
@pytest.fixture(scope="function", autouse=False)
async def cleanup_postgres_pool():
    """Cleanup PostgreSQL connection pool after test"""
    yield
    try:
        from trader.adapters.persistence.postgres import close_pool
        await close_pool()
    except Exception:
        # Ignore cleanup errors
        pass


skip_if_no_asyncpg = pytest.mark.skipif(
    not ASYNCPG_AVAILABLE,
    reason="asyncpg package not installed"
)

skip_if_no_postgres = pytest.mark.skipif(
    not is_postgres_available(),
    reason="PostgreSQL not available. Set POSTGRES_CONNECTION_STRING or POSTGRES_HOST/POSTGRES_DB/POSTGRES_USER"
)


@dataclass
class MockEvent:
    """Mock event for testing"""
    event_id: str
    event_type: str
    aggregate_id: str
    aggregate_type: str
    timestamp: datetime
    data: Dict[str, Any]
    metadata: Dict[str, Any] = field(default_factory=dict)


class TestPostgresAvailability:
    """Test PostgreSQL availability detection"""

    @skip_if_no_asyncpg
    def test_is_postgres_available_with_connection_string(self):
        """Test detection with connection string"""
        with patch.dict(os.environ, {"POSTGRES_CONNECTION_STRING": "postgresql://user:pass@localhost/db"}):
            from trader.adapters.persistence.postgres import is_postgres_available as recheck
            assert recheck() is True

    @skip_if_no_asyncpg
    def test_is_postgres_available_with_env_vars(self):
        """Test detection with individual env vars"""
        with patch.dict(os.environ, {
            "POSTGRES_HOST": "localhost",
            "POSTGRES_DB": "trading",
            "POSTGRES_USER": "trader",
        }):
            from trader.adapters.persistence.postgres import is_postgres_available as recheck
            assert recheck() is True

    def test_is_postgres_available_no_asyncpg(self):
        """Test detection when asyncpg is not installed"""
        with patch("trader.adapters.persistence.postgres.ASYNCPG_AVAILABLE", False):
            from trader.adapters.persistence.postgres import is_postgres_available as recheck
            assert recheck() is False


@skip_if_no_postgres
class TestPostgreSQLStorage:
    """Test PostgreSQL storage implementation"""

    @pytest.fixture
    async def storage(self, cleanup_postgres_pool):
        """Create storage instance"""
        storage = PostgreSQLStorage()
        await storage.connect()
        await storage.clear()
        yield storage
        if storage.is_connected:
            await storage.clear()
            await storage.disconnect()

    @pytest.mark.asyncio
    @skip_if_no_asyncpg
    async def test_connect(self, storage):
        """Test connection"""
        await storage.connect()
        assert storage.is_connected is True
        await storage.disconnect()
        assert storage.is_connected is False

    @pytest.mark.asyncio
    @skip_if_no_asyncpg
    async def test_append_and_get_event(self, storage):
        """Test append and get events"""
        await storage.connect()
        
        event = MockEvent(
            event_id="evt-001",
            event_type="OrderCreated",
            aggregate_id="order-123",
            aggregate_type="Order",
            timestamp=datetime.now(timezone.utc),
            data={"symbol": "BTCUSDT", "quantity": 1.0},
            metadata={"user": "test"},
        )
        
        result = await storage.append_event(event)
        assert result == "evt-001"
        
        events = await storage.get_events(aggregate_id="order-123")
        assert len(events) == 1
        assert events[0].event_id == "evt-001"
        assert events[0].event_type == "OrderCreated"
        
        await storage.disconnect()

    @pytest.mark.asyncio
    @skip_if_no_asyncpg
    async def test_get_events_with_filters(self, storage):
        """Test get events with filters"""
        await storage.connect()
        
        now = datetime.now(timezone.utc)
        
        event1 = MockEvent(
            event_id="evt-001",
            event_type="OrderCreated",
            aggregate_id="order-123",
            aggregate_type="Order",
            timestamp=now,
            data={"symbol": "BTCUSDT"},
        )
        
        event2 = MockEvent(
            event_id="evt-002",
            event_type="OrderFilled",
            aggregate_id="order-123",
            aggregate_type="Order",
            timestamp=now,
            data={"fill_price": 50000},
        )
        
        await storage.append_event(event1)
        await storage.append_event(event2)
        
        events = await storage.get_events(event_type="OrderCreated")
        assert len(events) == 1
        assert events[0].event_type == "OrderCreated"
        
        events = await storage.get_events(limit=1)
        assert len(events) == 1
        
        await storage.disconnect()

    @pytest.mark.asyncio
    @skip_if_no_asyncpg
    async def test_get_events_since_includes_boundary(self, storage):
        """Test get_events since filter uses >= semantics (includes boundary timestamp)"""
        await storage.connect()
        
        boundary_ts = datetime.now(timezone.utc)
        
        event_at_boundary = MockEvent(
            event_id="evt-bound-001",
            event_type="OrderCreated",
            aggregate_id="order-bound",
            aggregate_type="Order",
            timestamp=boundary_ts,
            data={"symbol": "ETHUSDT"},
        )
        
        event_after = MockEvent(
            event_id="evt-bound-002",
            event_type="OrderFilled",
            aggregate_id="order-bound",
            aggregate_type="Order",
            timestamp=datetime.now(timezone.utc),
            data={"fill_price": 50000},
        )
        
        await storage.append_event(event_at_boundary)
        await storage.append_event(event_after)
        
        events = await storage.get_events(aggregate_id="order-bound", since=boundary_ts)
        
        assert len(events) == 2, "get_events should include events at exactly the since timestamp (>= semantics)"
        
        await storage.disconnect()

    @pytest.mark.asyncio
    @skip_if_no_asyncpg
    async def test_save_and_get_snapshot(self, storage):
        """Test save and get snapshot"""
        await storage.connect()
        
        snapshot_data = {
            "snapshot_id": "snap-001",
            "stream_key": "order-123",
            "aggregate_id": "order-123",
            "aggregate_type": "Order",
            "timestamp": datetime.now(timezone.utc),
            "state": {"status": "FILLED", "filled_quantity": 1.0},
        }
        
        result = await storage.save_snapshot(snapshot_data)
        assert result == "snap-001"
        
        snapshot = await storage.get_latest_snapshot("order-123")
        assert snapshot is not None
        assert snapshot.snapshot_id == "snap-001"
        assert snapshot.stream_key == "order-123"
        assert snapshot.state["status"] == "FILLED"
        
        await storage.disconnect()

    @pytest.mark.asyncio
    @skip_if_no_asyncpg
    async def test_clear(self, storage):
        """Test clear all data"""
        await storage.connect()
        
        event = MockEvent(
            event_id="evt-001",
            event_type="OrderCreated",
            aggregate_id="order-123",
            aggregate_type="Order",
            timestamp=datetime.now(timezone.utc),
            data={},
        )
        
        await storage.append_event(event)
        
        snapshot_data = {
            "snapshot_id": "snap-001",
            "stream_key": "order-123",
            "aggregate_id": "order-123",
            "aggregate_type": "Order",
            "timestamp": datetime.now(timezone.utc),
            "state": {},
        }
        await storage.save_snapshot(snapshot_data)
        
        await storage.clear()
        
        events = await storage.get_events()
        assert len(events) == 0
        
        snapshot = await storage.get_latest_snapshot("order-123")
        assert snapshot is None
        
        await storage.disconnect()

    @pytest.mark.asyncio
    @skip_if_no_asyncpg
    async def test_snapshot_and_event_reconstruction(self, storage):
        """Test state reconstruction from snapshot + events - consistent replay after restart"""
        await storage.connect()
        
        snapshot_ts = datetime.now(timezone.utc)
        
        snapshot_data = {
            "snapshot_id": "snap-recon-001",
            "stream_key": "order-recon",
            "aggregate_id": "order-recon",
            "aggregate_type": "Order",
            "timestamp": snapshot_ts,
            "state": {"status": "NEW", "quantity": 0},
        }
        await storage.save_snapshot(snapshot_data)
        
        event1 = MockEvent(
            event_id="evt-recon-001",
            event_type="OrderCreated",
            aggregate_id="order-recon",
            aggregate_type="Order",
            timestamp=datetime.now(timezone.utc),
            data={"symbol": "BTCUSDT", "quantity": 1.0},
        )
        
        event2 = MockEvent(
            event_id="evt-recon-002",
            event_type="OrderFilled",
            aggregate_id="order-recon",
            aggregate_type="Order",
            timestamp=datetime.now(timezone.utc),
            data={"fill_price": 50000, "filled_quantity": 1.0},
        )
        
        await storage.append_event(event1)
        await storage.append_event(event2)
        
        snapshot = await storage.get_latest_snapshot("order-recon")
        assert snapshot is not None
        assert snapshot.state["status"] == "NEW"
        
        events_after_snapshot = await storage.get_events(
            aggregate_id="order-recon",
            since=snapshot_ts,
        )
        assert len(events_after_snapshot) == 2
        
        reconstructed_state = snapshot.state.copy()
        for event in events_after_snapshot:
            if event.event_type == "OrderCreated":
                reconstructed_state["quantity"] = event.data.get("quantity", 0)
                reconstructed_state["status"] = "CREATED"
            elif event.event_type == "OrderFilled":
                reconstructed_state["filled_quantity"] = event.data.get("filled_quantity", 0)
                reconstructed_state["fill_price"] = event.data.get("fill_price")
                reconstructed_state["status"] = "FILLED"
        
        assert reconstructed_state["status"] == "FILLED"
        assert reconstructed_state["quantity"] == 1.0
        assert reconstructed_state["filled_quantity"] == 1.0
        assert reconstructed_state["fill_price"] == 50000
        
        await storage.disconnect()

    @pytest.mark.asyncio
    @skip_if_no_asyncpg
    async def test_reconstruct_state_with_projection(self, storage):
        """Test reconstruct_state method with projection function"""
        await storage.connect()
        
        snapshot_ts = datetime.now(timezone.utc)
        
        snapshot_data = {
            "snapshot_id": "snap-proj-001",
            "stream_key": "order-proj",
            "aggregate_id": "order-proj",
            "aggregate_type": "Order",
            "timestamp": snapshot_ts,
            "state": {"status": "NEW", "quantity": 0, "filled_quantity": 0},
        }
        await storage.save_snapshot(snapshot_data)
        
        await storage.append_event(MockEvent(
            event_id="evt-proj-001",
            event_type="OrderCreated",
            aggregate_id="order-proj",
            aggregate_type="Order",
            timestamp=datetime.now(timezone.utc),
            data={"symbol": "BTCUSDT", "quantity": 2.0},
        ))
        
        await storage.append_event(MockEvent(
            event_id="evt-proj-002",
            event_type="OrderFilled",
            aggregate_id="order-proj",
            aggregate_type="Order",
            timestamp=datetime.now(timezone.utc),
            data={"fill_price": 51000, "filled_quantity": 2.0},
        ))
        
        def order_projection(state, event):
            if event.event_type == "OrderCreated":
                state["quantity"] = event.data.get("quantity", 0)
                state["status"] = "CREATED"
            elif event.event_type == "OrderFilled":
                state["filled_quantity"] = event.data.get("filled_quantity", 0)
                state["fill_price"] = event.data.get("fill_price")
                state["status"] = "FILLED"
            return state
        
        reconstructed = await storage.reconstruct_state("order-proj", order_projection)
        
        assert reconstructed is not None
        assert reconstructed["status"] == "FILLED"
        assert reconstructed["quantity"] == 2.0
        assert reconstructed["filled_quantity"] == 2.0
        assert reconstructed["fill_price"] == 51000
        
        await storage.disconnect()

    @pytest.mark.asyncio
    @skip_if_no_asyncpg
    async def test_reconstruct_state_returns_snapshot_and_events(self, storage):
        """Test reconstruct_state returns snapshot + events when no projection provided"""
        await storage.connect()
        
        snapshot_ts = datetime.now(timezone.utc)
        
        snapshot_data = {
            "snapshot_id": "snap-raw-001",
            "stream_key": "order-raw",
            "aggregate_id": "order-raw",
            "aggregate_type": "Order",
            "timestamp": snapshot_ts,
            "state": {"status": "NEW"},
        }
        await storage.save_snapshot(snapshot_data)
        
        await storage.append_event(MockEvent(
            event_id="evt-raw-001",
            event_type="OrderCreated",
            aggregate_id="order-raw",
            aggregate_type="Order",
            timestamp=datetime.now(timezone.utc),
            data={"symbol": "ETHUSDT"},
        ))
        
        result = await storage.reconstruct_state("order-raw")
        
        assert result is not None
        assert "snapshot" in result
        assert "events" in result
        assert result["snapshot"]["state"]["status"] == "NEW"
        assert len(result["events"]) == 1
        assert result["events"][0]["event_type"] == "OrderCreated"
        
        await storage.disconnect()

    @pytest.mark.asyncio
    @skip_if_no_asyncpg
    async def test_reconstruct_state_with_different_stream_key_and_aggregate_id(self, storage):
        """Test reconstruct_state works when stream_key != aggregate_id"""
        await storage.connect()
        
        snapshot_ts = datetime.now(timezone.utc)
        
        snapshot_data = {
            "snapshot_id": "snap-diff-001",
            "stream_key": "account-123",  # stream_key different from aggregate_id
            "aggregate_id": "order-456",   # aggregate_id different from stream_key
            "aggregate_type": "Order",
            "timestamp": snapshot_ts,
            "state": {"status": "NEW", "quantity": 0},
        }
        await storage.save_snapshot(snapshot_data)
        
        await storage.append_event(MockEvent(
            event_id="evt-diff-001",
            event_type="OrderCreated",
            aggregate_id="order-456",  # uses aggregate_id, not stream_key
            aggregate_type="Order",
            timestamp=datetime.now(timezone.utc),
            data={"symbol": "BTCUSDT", "quantity": 1.5},
        ))
        
        await storage.append_event(MockEvent(
            event_id="evt-diff-002",
            event_type="OrderFilled",
            aggregate_id="order-456",  # uses aggregate_id, not stream_key
            aggregate_type="Order",
            timestamp=datetime.now(timezone.utc),
            data={"fill_price": 50000, "filled_quantity": 1.5},
        ))
        
        def order_projection(state, event):
            if event.event_type == "OrderCreated":
                state["quantity"] = event.data.get("quantity", 0)
                state["status"] = "CREATED"
            elif event.event_type == "OrderFilled":
                state["filled_quantity"] = event.data.get("filled_quantity", 0)
                state["fill_price"] = event.data.get("fill_price")
                state["status"] = "FILLED"
            return state
        
        result = await storage.reconstruct_state("account-123", order_projection)
        
        assert result is not None
        assert result["status"] == "FILLED"
        assert result["quantity"] == 1.5
        assert result["filled_quantity"] == 1.5
        assert result["fill_price"] == 50000
        
        await storage.disconnect()

    @pytest.mark.asyncio
    @skip_if_no_asyncpg
    async def test_reconstruct_state_excludes_boundary_events(self, storage):
        """Test reconstruct_state does NOT replay events at exactly snapshot timestamp"""
        await storage.connect()
        
        boundary_ts = datetime.now(timezone.utc)
        
        snapshot_data = {
            "snapshot_id": "snap-bound-001",
            "stream_key": "order-bound",
            "aggregate_id": "order-bound",
            "aggregate_type": "Order",
            "timestamp": boundary_ts,
            "state": {"status": "CREATED", "quantity": 1.0, "filled_quantity": 1.0},
        }
        await storage.save_snapshot(snapshot_data)
        
        await storage.append_event(MockEvent(
            event_id="evt-bound-001",
            event_type="OrderFilled",
            aggregate_id="order-bound",
            aggregate_type="Order",
            timestamp=boundary_ts,  # exactly the same timestamp as snapshot
            data={"fill_price": 51000, "filled_quantity": 0.5},
        ))
        
        await storage.append_event(MockEvent(
            event_id="evt-bound-002",
            event_type="OrderFilled",
            aggregate_id="order-bound",
            aggregate_type="Order",
            timestamp=datetime.now(timezone.utc),  # later timestamp
            data={"fill_price": 52000, "filled_quantity": 0.5},
        ))
        
        def order_projection(state, event):
            if event.event_type == "OrderFilled":
                state["filled_quantity"] = state.get("filled_quantity", 0) + event.data.get("filled_quantity", 0)
                state["fill_price"] = event.data.get("fill_price")
            return state
        
        result = await storage.reconstruct_state("order-bound", order_projection)
        
        assert result is not None
        assert result["filled_quantity"] == 1.5, "Should only replay 1 event (later timestamp), not boundary event"
        assert result["fill_price"] == 52000
        
        await storage.disconnect()


@skip_if_no_postgres
class TestStoredEventDataclass:
    """Test StoredEvent dataclass"""

    @skip_if_no_asyncpg
    def test_stored_event_creation(self):
        """Test StoredEvent creation"""
        event = StoredEvent(
            event_id="evt-001",
            event_type="OrderCreated",
            aggregate_id="order-123",
            aggregate_type="Order",
            timestamp=datetime.now(timezone.utc),
            data={"symbol": "BTCUSDT"},
            metadata={"user": "test"},
        )
        
        assert event.event_id == "evt-001"
        assert event.event_type == "OrderCreated"
        assert event.aggregate_id == "order-123"


@skip_if_no_postgres
class TestStoredSnapshotDataclass:
    """Test StoredSnapshot dataclass"""

    @skip_if_no_asyncpg
    def test_stored_snapshot_creation(self):
        """Test StoredSnapshot creation"""
        snapshot = StoredSnapshot(
            snapshot_id="snap-001",
            stream_key="order-123",
            aggregate_id="order-123",
            aggregate_type="Order",
            timestamp=datetime.now(timezone.utc),
            state={"status": "FILLED"},
        )
        
        assert snapshot.snapshot_id == "snap-001"
        assert snapshot.stream_key == "order-123"
        assert snapshot.state["status"] == "FILLED"


@skip_if_no_asyncpg
class TestPostgresStorageSchema:
    """Test PostgreSQL storage schema definitions"""

    def test_schema_initialization(self):
        """Test that PostgreSQLStorage can be instantiated"""
        storage = PostgreSQLStorage(
            host="localhost",
            port=5432,
            database="trading",
            user="trader",
            password="secret"
        )
        
        assert storage._host == "localhost"
        assert storage._port == 5432
        assert storage._database == "trading"
        assert storage._user == "trader"
        assert storage._password == "secret"
        assert storage.is_connected is False

    def test_schema_initialization_from_env(self):
        """Test initialization from environment variables"""
        with patch.dict(os.environ, {
            "POSTGRES_HOST": "db.example.com",
            "POSTGRES_PORT": "5433",
            "POSTGRES_DB": "testdb",
            "POSTGRES_USER": "testuser",
            "POSTGRES_PASSWORD": "testpass",
        }):
            storage = PostgreSQLStorage()
            
            assert storage._host == "db.example.com"
            assert storage._port == 5433
            assert storage._database == "testdb"
            assert storage._user == "testuser"
            assert storage._password == "testpass"


@skip_if_no_postgres
class TestRiskEventsPersistence:
    """Test risk_events and risk_upgrades persistence"""

    @pytest.fixture
    async def storage(self):
        """Create storage instance"""
        storage = PostgreSQLStorage()
        await storage.connect()
        await storage.clear()
        yield storage
        if storage.is_connected:
            await storage.disconnect()

    @pytest.mark.asyncio
    async def test_save_and_get_risk_event(self, storage):
        """Test saving and retrieving risk event"""
        event_data = {
            "event_id": "risk-evt-001",
            "dedup_key": "dedup-key-001",
            "scope": "GLOBAL",
            "reason": "Test risk event",
            "recommended_level": 1,
            "data": {"test": "data"},
        }
        
        event_id, created = await storage.save_risk_event(event_data)
        assert created is True
        assert event_id == "risk-evt-001"
        
        stored = await storage.get_risk_event("dedup-key-001")
        assert stored is not None
        assert stored.dedup_key == "dedup-key-001"
        assert stored.scope == "GLOBAL"
        assert stored.recommended_level == 1

    @pytest.mark.asyncio
    async def test_duplicate_risk_event(self, storage):
        """Test duplicate risk event returns False for created with same event_id"""
        event_data = {
            "event_id": "risk-evt-002",
            "dedup_key": "dedup-key-002",
            "scope": "GLOBAL",
            "reason": "Test duplicate",
            "recommended_level": 2,
            "data": {},
        }
        
        event_id_1, created_1 = await storage.save_risk_event(event_data)
        assert created_1 is True
        assert event_id_1 == "risk-evt-002"
        
        event_id_2, created_2 = await storage.save_risk_event(event_data)
        assert created_2 is False
        assert event_id_2 == event_id_1, "Duplicate should return existing event_id"

    @pytest.mark.asyncio
    async def test_save_and_get_upgrade_record(self, storage):
        """Test saving and retrieving upgrade record"""
        upgrade_key = "upgrade:GLOBAL:2:dedup-key-003"
        upgrade_data = {
            "scope": "GLOBAL",
            "level": 2,
            "reason": "Test upgrade",
            "dedup_key": "dedup-key-003",
        }
        
        await storage.save_upgrade_record(upgrade_key, upgrade_data)
        
        stored = await storage.get_upgrade_record(upgrade_key)
        assert stored is not None
        assert stored.upgrade_key == upgrade_key
        assert stored.scope == "GLOBAL"
        assert stored.level == 2
        assert stored.dedup_key == "dedup-key-003"

    @pytest.mark.asyncio
    async def test_risk_event_full_data_preservation(self, storage):
        """Test that severity/metrics/adapter_name fields are preserved in full event data"""
        event_data = {
            "dedup_key": "dedup-key-full-001",
            "scope": "GLOBAL",
            "reason": "ENV_RISK:AdapterDegraded:binance_adapter",
            "severity": "HIGH",
            "metrics": {"private_stream_state": "DEGRADED"},
            "recommended_level": 1,
            "adapter_name": "binance_adapter",
            "venue": "BINANCE",
            "account_id": "acc_001",
            "ts_ms": 1700000000000,
        }
        
        event_id, created = await storage.save_risk_event(event_data)
        assert created is True
        
        stored = await storage.get_risk_event("dedup-key-full-001")
        assert stored is not None
        assert stored.data.get("severity") == "HIGH"
        assert stored.data.get("metrics") == {"private_stream_state": "DEGRADED"}
        assert stored.data.get("adapter_name") == "binance_adapter"
        assert stored.data.get("venue") == "BINANCE"
        assert stored.data.get("account_id") == "acc_001"

    @pytest.mark.asyncio
    async def test_ingest_event_with_upgrade_preserves_full_event_data(self, storage):
        """Transactional ingest path should serialize risk_events.data consistently."""
        event_data = {
            "dedup_key": "dedup-key-ingest-full-001",
            "scope": "GLOBAL",
            "reason": "ENV_RISK:AdapterDegraded:binance_adapter",
            "severity": "HIGH",
            "metrics": {"private_stream_state": "DEGRADED"},
            "recommended_level": 1,
            "adapter_name": "binance_adapter",
            "venue": "BINANCE",
            "account_id": "acc_001",
            "ts_ms": 1700000000000,
        }

        event_id, created, is_first_upgrade, is_first_effect = await storage.ingest_event_with_upgrade(
            event_data,
            "upgrade:GLOBAL:1:dedup-key-ingest-full-001",
            1,
        )

        assert event_id is not None
        assert created is True
        assert is_first_upgrade is True
        assert is_first_effect is True

        stored = await storage.get_risk_event("dedup-key-ingest-full-001")
        assert stored is not None
        assert stored.data.get("severity") == "HIGH"
        assert stored.data.get("metrics") == {"private_stream_state": "DEGRADED"}
        assert stored.data.get("adapter_name") == "binance_adapter"
        assert stored.data.get("venue") == "BINANCE"
        assert stored.data.get("account_id") == "acc_001"

    @pytest.mark.asyncio
    async def test_get_nonexistent_risk_event(self, storage):
        """Test getting non-existent risk event returns None"""
        stored = await storage.get_risk_event("nonexistent-key")
        assert stored is None

    @pytest.mark.asyncio
    async def test_get_nonexistent_upgrade_record(self, storage):
        """Test getting non-existent upgrade record returns None"""
        stored = await storage.get_upgrade_record("nonexistent-upgrade-key")
        assert stored is None

    @pytest.mark.asyncio
    async def test_risk_upgrade_effects_upgrade_key_is_effectively_unique(self, storage):
        """PRIMARY KEY on upgrade_key must prevent duplicate effect intents."""
        upgrade_key = "upgrade:GLOBAL:2:dedup-key-unique"

        first_upgrade, first_effect = await storage.try_record_upgrade_with_effect(
            upgrade_key, "GLOBAL", 2, "first", "dedup-key-unique"
        )
        assert first_upgrade is True
        assert first_effect is True

        second_upgrade, second_effect = await storage.try_record_upgrade_with_effect(
            upgrade_key, "GLOBAL", 2, "first", "dedup-key-unique"
        )
        assert second_upgrade is False
        assert second_effect is False

        pending = await storage.get_pending_effects()
        matching = [effect for effect in pending if effect["upgrade_key"] == upgrade_key]
        assert len(matching) == 1

    @pytest.mark.asyncio
    async def test_risk_upgrade_effects_has_updated_at_index(self, storage):
        """Recovery query should have an index backing ORDER BY updated_at."""
        async with storage._pool.acquire() as conn:
            index_def = await conn.fetchval(
                """
                SELECT indexdef
                FROM pg_indexes
                WHERE schemaname = current_schema()
                  AND tablename = 'risk_upgrade_effects'
                  AND indexname = 'idx_risk_upgrade_effects_updated_at'
                """
            )

        assert index_def is not None
        assert "updated_at" in index_def

    @pytest.mark.asyncio
    async def test_risk_event_idempotency_after_reconnect(self, storage):
        """Test that duplicate dedup_key returns same event_id after reconnect (restart simulation)."""
        event_data = {
            "event_id": "risk-evt-reconnect-001",
            "dedup_key": "dedup-key-reconnect-001",
            "scope": "GLOBAL",
            "reason": "Test idempotency after reconnect",
            "recommended_level": 2,
            "data": {"test": "reconnect"},
        }
        
        event_id_1, created_1 = await storage.save_risk_event(event_data)
        assert created_1 is True
        
        await storage.disconnect()
        await storage.connect()
        
        event_id_2, created_2 = await storage.save_risk_event(event_data)
        assert created_2 is False, "After reconnect, duplicate should return created=False"
        assert event_id_2 == event_id_1, "After reconnect, duplicate should return same event_id"

    @pytest.mark.asyncio
    async def test_upgrade_key_idempotency_after_reconnect(self, storage):
        """Test that duplicate upgrade_key does NOT trigger duplicate side effects after reconnect."""
        upgrade_key = "upgrade:ACCOUNT:1:dedup-key-reconnect-001"
        upgrade_data = {
            "scope": "ACCOUNT",
            "level": 1,
            "reason": "Test upgrade idempotency after reconnect",
            "dedup_key": "dedup-key-reconnect-001",
        }
        
        first_upgrade, first_effect = await storage.try_record_upgrade_with_effect(
            upgrade_key, "ACCOUNT", 1, "test_reason", "dedup-key-reconnect-001"
        )
        assert first_upgrade is True
        assert first_effect is True
        
        await storage.disconnect()
        await storage.connect()
        
        second_upgrade, second_effect = await storage.try_record_upgrade_with_effect(
            upgrade_key, "ACCOUNT", 1, "test_reason", "dedup-key-reconnect-001"
        )
        assert second_upgrade is False, "After reconnect, duplicate upgrade should return upgrade=False"
        assert second_effect is False, "After reconnect, duplicate should NOT trigger side effect"
        
        pending = await storage.get_pending_effects()
        matching = [effect for effect in pending if effect["upgrade_key"] == upgrade_key]
        assert len(matching) == 1, "Only one pending effect should exist after reconnect"

    @pytest.mark.asyncio
    async def test_recovery_endpoint_works_after_reconnect(self, storage):
        """Test that get_pending_effects works correctly after reconnect (recovery simulation)."""
        upgrade_key_1 = "upgrade:ACCOUNT:1:dedup-key-recovery-001"
        upgrade_key_2 = "upgrade:ACCOUNT:2:dedup-key-recovery-002"
        
        await storage.try_record_upgrade_with_effect(
            upgrade_key_1, "ACCOUNT", 1, "reason_1", "dedup-key-recovery-001"
        )
        await storage.try_record_upgrade_with_effect(
            upgrade_key_2, "ACCOUNT", 2, "reason_2", "dedup-key-recovery-002"
        )
        
        pending_before = await storage.get_pending_effects()
        count_before = len(pending_before)
        
        await storage.disconnect()
        await storage.connect()
        
        pending_after = await storage.get_pending_effects()
        count_after = len(pending_after)
        
        assert count_before == count_after, "Pending effects count should be same after reconnect"
        assert count_after == 2, "Should have 2 pending effects after reconnect"


@skip_if_no_asyncpg
class TestStoredRiskEventDataclass:
    """Test StoredRiskEvent and StoredUpgradeRecord dataclasses"""

    def test_stored_risk_event_creation(self):
        """Test creating StoredRiskEvent"""
        from trader.adapters.persistence.postgres import StoredRiskEvent
        from datetime import datetime, timezone
        
        event = StoredRiskEvent(
            event_id="evt-001",
            dedup_key="key-001",
            scope="GLOBAL",
            reason="Test",
            recommended_level=1,
            ingested_at=datetime.now(timezone.utc),
            data={"test": "data"},
        )
        
        assert event.event_id == "evt-001"
        assert event.dedup_key == "key-001"
        assert event.recommended_level == 1

    def test_stored_upgrade_record_creation(self):
        """Test creating StoredUpgradeRecord"""
        from trader.adapters.persistence.postgres import StoredUpgradeRecord
        from datetime import datetime, timezone
        
        record = StoredUpgradeRecord(
            upgrade_key="upgrade-001",
            scope="GLOBAL",
            level=2,
            reason="Test upgrade",
            dedup_key="key-001",
            recorded_at=datetime.now(timezone.utc),
        )
        
        assert record.upgrade_key == "upgrade-001"
        assert record.level == 2
