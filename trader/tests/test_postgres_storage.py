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
    async def storage(self):
        """Create storage instance"""
        storage = PostgreSQLStorage()
        yield storage
        if storage.is_connected:
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
