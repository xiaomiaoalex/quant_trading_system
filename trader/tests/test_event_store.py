"""
Unit tests for EventStoreWithFallback and PostgresEventStore
"""
import asyncio
import json
import logging
import pytest
from datetime import datetime, timezone
from unittest.mock import AsyncMock, MagicMock, patch

from trader.adapters.persistence.event_store import EventStoreWithFallback
from trader.adapters.persistence.memory.event_store import InMemoryEventStore
from trader.adapters.persistence.postgres.event_store import PostgresEventStore, StreamEvent
from trader.core.domain.models.events import DomainEvent, EventType


# Module-level fixtures for shared use
@pytest.fixture
def mock_pool():
    """Shared mock pool for all tests"""
    return MockPool()


@pytest.fixture
def memory_store():
    """Shared memory store for all tests"""
    return InMemoryEventStore()


@pytest.fixture
def pg_store(mock_pool):
    """Shared PostgresEventStore for all tests"""
    return PostgresEventStore(pool=mock_pool)


@pytest.fixture
def fallback_store(pg_store, memory_store):
    """Shared EventStoreWithFallback for all tests"""
    return EventStoreWithFallback(
        pg_event_store=pg_store,
        memory_event_store=memory_store,
    )


class MockPool:
    """Mock asyncpg Pool for testing"""
    def __init__(self):
        self._conn = MockConnection()
    
    def acquire(self):
        """Returns an async context manager"""
        return MockPoolContextManager(self._conn)
    
    async def release(self, conn):
        pass


class MockPoolContextManager:
    """Mock async context manager for pool.acquire()"""
    def __init__(self, conn):
        self._conn = conn
    
    async def __aenter__(self):
        return self._conn
    
    async def __aexit__(self, exc_type, exc_val, exc_tb):
        pass


class FailingPoolAcquire:
    """Async context manager that raises an exception when acquired.
    
    This is used to simulate PostgreSQL connection failures in tests.
    """
    def __init__(self, error_message: str = "PG connection failed"):
        self._error_message = error_message
    
    async def __aenter__(self):
        raise Exception(self._error_message)
    
    async def __aexit__(self, exc_type, exc_val, exc_tb):
        pass


class MockConnection:
    """Mock asyncpg Connection for testing"""
    def __init__(self):
        self._data = {}  # stream_key -> list of events
    
    async def execute(self, query, *args):
        # Parse the query to understand what operation to perform
        if "INSERT INTO event_log" in query:
            # Extract values from the query
            if "ON CONFLICT (stream_key, seq) DO NOTHING" in query:
                # This is an idempotent insert
                event_id = args[0]
                stream_key = args[1]
                seq = args[2]
                
                # Check if this exact (stream_key, seq) already exists
                key = (stream_key, seq)
                if key in self._data:
                    return 0  # Conflict, do nothing
                
                # Store the event
                self._data[key] = {
                    "event_id": event_id,
                    "stream_key": stream_key,
                    "seq": seq,
                    "event_type": args[3],
                    "aggregate_id": args[4],
                    "aggregate_type": args[5],
                    "timestamp": args[6],
                    "ts_ms": args[7],
                    "data": json.loads(args[8]) if isinstance(args[8], str) else args[8],
                    "metadata": json.loads(args[9]) if isinstance(args[9], str) else args[9],
                    "schema_version": args[10],
                }
                return 1
        return 0
    
    async def fetch(self, query, *args):
        stream_key = args[0]
        from_seq = args[1]
        limit = args[2]
        
        # Validate stream_key is not empty
        if not stream_key:
            raise ValueError("stream_key cannot be empty")
        
        # Find all events for this stream_key with seq > from_seq
        results = []
        for (sk, seq), event in self._data.items():
            if sk == stream_key and seq > from_seq:
                results.append((sk, seq, event))
        
        # Sort by seq and limit
        results.sort(key=lambda x: x[1])
        results = results[:limit]
        
        # Return mock rows
        return [self._make_row(sk, seq, event) for sk, seq, event in results]
    
    async def fetchrow(self, query, *args):
        # Handle INSERT with RETURNING clause (used by append() for idempotent insert)
        if "INSERT INTO event_log" in query and "RETURNING" in query:
            if "ON CONFLICT (stream_key, seq) DO NOTHING" in query:
                event_id = args[0]
                stream_key = args[1]
                seq = args[2]
                
                key = (stream_key, seq)
                if key in self._data:
                    # Conflict - return None to indicate DO NOTHING
                    return None
                
                # Store the event
                self._data[key] = {
                    "event_id": event_id,
                    "stream_key": stream_key,
                    "seq": seq,
                    "event_type": args[3],
                    "aggregate_id": args[4],
                    "aggregate_type": args[5],
                    "timestamp": args[6],
                    "ts_ms": args[7],
                    "data": json.loads(args[8]) if isinstance(args[8], str) else args[8],
                    "metadata": json.loads(args[9]) if isinstance(args[9], str) else args[9],
                    "schema_version": args[10],
                }
                # Return the row with event_id
                row = MagicMock()
                row.__getitem__ = lambda self, key: {"event_id": event_id}.get(key)
                return row
        
        if "SELECT MAX(seq)" in query:
            stream_key = args[0]
            seqs = [seq for (sk, seq) in self._data.keys() if sk == stream_key]
            max_seq = max(seqs) if seqs else None
            row = MagicMock()
            row.__getitem__ = lambda self, key: {"max_seq": max_seq}.get(key)
            return row
        
        if "SELECT" in query and "event_log" in query and "COUNT" not in query and "MAX" not in query:
            stream_key = args[0]
            seq = args[1]
            key = (stream_key, seq)
            if key in self._data:
                event = self._data[key]
                return self._make_row(stream_key, seq, event)
            return None
        
        if "COUNT" in query:
            stream_key = args[0]
            events = [v for k, v in self._data.items() if k[0] == stream_key]
            seqs = [k[1] for k in self._data.keys() if k[0] == stream_key]
            row = MagicMock()
            row.__getitem__ = lambda self, key: {
                "event_count": len(events),
                "latest_seq": max(seqs) if seqs else -1,
                "earliest_ts_ms": min([v["ts_ms"] for v in events]) if events else None,
                "latest_ts_ms": max([v["ts_ms"] for v in events]) if events else None,
            }.get(key)
            return row
        
        return None
    
    def _make_row(self, stream_key, seq, event):
        row = MagicMock()
        row.__getitem__ = lambda self, key: {
            "event_id": event["event_id"],
            "stream_key": stream_key,
            "seq": seq,
            "event_type": event["event_type"],
            "aggregate_id": event["aggregate_id"],
            "aggregate_type": event["aggregate_type"],
            "timestamp": event["timestamp"],
            "ts_ms": event["ts_ms"],
            "data": event["data"],
            "metadata": event["metadata"],
            "schema_version": event["schema_version"],
        }.get(key)
        return row


class TestPostgresEventStore:
    """Tests for PostgresEventStore using mocked pool"""

    @pytest.fixture
    def mock_pool(self):
        return MockPool()

    @pytest.fixture
    def event_store(self, mock_pool):
        return PostgresEventStore(pool=mock_pool)

    @pytest.mark.asyncio
    async def test_append_idempotent(self, event_store, mock_pool):
        """Test that append is idempotent - same event_id + stream_key + seq doesn't duplicate"""
        event_id = "test-event-1"
        stream_key = "order-123"
        seq = 0
        
        # First insert should succeed
        result1 = await event_store.append(
            stream_key=stream_key,
            seq=seq,
            event_type="OrderCreated",
            aggregate_id="order-123",
            aggregate_type="Order",
            data={"amount": 100},
            event_id=event_id,
        )
        assert result1 == event_id
        
        # Second insert with same (stream_key, seq) should be idempotent (DO NOTHING)
        result2 = await event_store.append(
            stream_key=stream_key,
            seq=seq,
            event_type="OrderCreated",
            aggregate_id="order-123",
            aggregate_type="Order",
            data={"amount": 100},
            event_id=event_id,
        )
        assert result2 == event_id  # Returns the same event_id

    @pytest.mark.asyncio
    async def test_append_different_seq_same_stream(self, event_store, mock_pool):
        """Test appending events with different seq to same stream"""
        stream_key = "order-123"
        
        # Append first event
        await event_store.append(
            stream_key=stream_key,
            seq=0,
            event_type="OrderCreated",
            aggregate_id="order-123",
            aggregate_type="Order",
            data={"amount": 100},
            event_id="event-1",
        )
        
        # Append second event with seq=1
        await event_store.append(
            stream_key=stream_key,
            seq=1,
            event_type="OrderUpdated",
            aggregate_id="order-123",
            aggregate_type="Order",
            data={"amount": 200},
            event_id="event-2",
        )
        
        # Read stream should return both events
        events = await event_store.read_stream(stream_key, from_seq=-1, limit=10)
        assert len(events) == 2
        assert events[0].seq == 0
        assert events[1].seq == 1

    @pytest.mark.asyncio
    async def test_read_stream_empty(self, event_store, mock_pool):
        """Test reading from empty stream"""
        events = await event_store.read_stream("nonexistent", from_seq=0, limit=10)
        assert len(events) == 0

    @pytest.mark.asyncio
    async def test_get_latest_seq_empty(self, event_store, mock_pool):
        """Test get_latest_seq on empty stream returns -1"""
        seq = await event_store.get_latest_seq("nonexistent")
        assert seq == -1

    @pytest.mark.asyncio
    async def test_get_latest_seq_with_events(self, event_store, mock_pool):
        """Test get_latest_seq returns correct value"""
        stream_key = "order-123"
        
        await event_store.append(
            stream_key=stream_key,
            seq=0,
            event_type="OrderCreated",
            aggregate_id="order-123",
            aggregate_type="Order",
            data={"amount": 100},
            event_id="event-1",
        )
        
        await event_store.append(
            stream_key=stream_key,
            seq=1,
            event_type="OrderUpdated",
            aggregate_id="order-123",
            aggregate_type="Order",
            data={"amount": 200},
            event_id="event-2",
        )
        
        seq = await event_store.get_latest_seq(stream_key)
        assert seq == 1

    @pytest.mark.asyncio
    async def test_snapshot_at(self, event_store, mock_pool):
        """Test snapshot_at returns event at specific seq"""
        stream_key = "order-123"
        
        await event_store.append(
            stream_key=stream_key,
            seq=0,
            event_type="OrderCreated",
            aggregate_id="order-123",
            aggregate_type="Order",
            data={"amount": 100},
            event_id="event-1",
        )
        
        await event_store.append(
            stream_key=stream_key,
            seq=1,
            event_type="OrderUpdated",
            aggregate_id="order-123",
            aggregate_type="Order",
            data={"amount": 200},
            event_id="event-2",
        )
        
        event = await event_store.snapshot_at(stream_key, 0)
        assert event is not None
        assert event.seq == 0
        assert event.event_id == "event-1"
        
        event = await event_store.snapshot_at(stream_key, 99)  # Non-existent
        assert event is None

    @pytest.mark.asyncio
    async def test_get_stream_info(self, event_store, mock_pool):
        """Test get_stream_info returns correct metadata"""
        stream_key = "order-123"
        
        await event_store.append(
            stream_key=stream_key,
            seq=0,
            event_type="OrderCreated",
            aggregate_id="order-123",
            aggregate_type="Order",
            data={"amount": 100},
            event_id="event-1",
        )
        
        info = await event_store.get_stream_info(stream_key)
        assert info["stream_key"] == stream_key
        assert info["event_count"] == 1
        assert info["latest_seq"] == 0


class TestEventStoreWithFallback:
    """Tests for EventStoreWithFallback"""

    @pytest.fixture
    def memory_store(self):
        return InMemoryEventStore()

    @pytest.fixture
    def pg_store(self, mock_pool):
        return PostgresEventStore(pool=mock_pool)

    @pytest.fixture
    def fallback_store(self, pg_store, memory_store):
        return EventStoreWithFallback(
            pg_event_store=pg_store,
            memory_event_store=memory_store,
        )

    def test_init_with_pg(self, pg_store, memory_store):
        """Test initialization with PostgreSQL store"""
        store = EventStoreWithFallback(pg_event_store=pg_store)
        assert store.is_using_postgres is True

    def test_init_without_pg(self):
        """Test initialization without PostgreSQL store"""
        store = EventStoreWithFallback()
        assert store.is_using_postgres is False

    @pytest.mark.asyncio
    async def test_append_uses_pg_initially(self, fallback_store, mock_pool):
        """Test that append uses PostgreSQL when available"""
        event_id = await fallback_store.append(
            stream_key="order-123",
            seq=0,
            event_type="OrderCreated",
            aggregate_id="order-123",
            aggregate_type="Order",
            data={"amount": 100},
            event_id="event-1",
        )
        assert event_id == "event-1"
        assert fallback_store.is_using_postgres is True

    @pytest.mark.asyncio
    async def test_fallback_on_pg_error(self, pg_store, memory_store):
        """Test that fallback to memory occurs when PG fails"""
        # Create a failing mock pool
        failing_pool = MagicMock()
        failing_pool.acquire = lambda: FailingPoolAcquire("PG connection failed")
        
        failing_pg_store = PostgresEventStore(pool=failing_pool)
        store = EventStoreWithFallback(
            pg_event_store=failing_pg_store,
            memory_event_store=memory_store,
        )
        
        # Append should fail over to memory
        # Note: We use EventType directly to avoid enum conversion issues in fallback
        event_id = await store.append(
            stream_key="order-123",
            seq=0,
            event_type=EventType.ORDER_CREATED,  # Use EventType enum directly
            aggregate_id="order-123",
            aggregate_type="Order",
            data={"amount": 100},
            event_id="event-1",
        )
        
        assert event_id == "event-1"
        assert store.is_using_postgres is False

    @pytest.mark.asyncio
    async def test_read_stream_fallback(self, pg_store, memory_store):
        """Test that read_stream falls back to memory on PG error"""
        failing_pool = MagicMock()
        failing_pool.acquire = lambda: FailingPoolAcquire("PG connection failed")
        
        failing_pg_store = PostgresEventStore(pool=failing_pool)
        store = EventStoreWithFallback(
            pg_event_store=failing_pg_store,
            memory_event_store=memory_store,
        )
        
        # Append first to memory store
        await memory_store.append(DomainEvent(
            event_id="event-1",
            event_type=EventType.ORDER_CREATED,
            aggregate_id="order-123",
            aggregate_type="Order",
            timestamp=datetime.now(timezone.utc),
            data={"amount": 100},
        ))
        
        # Read should work via fallback
        # Note: In fallback mode, stream_key is ignored and all events are returned.
        # This is a known limitation of the fallback mechanism (see event_store.py docstring).
        events = await store.read_stream("order-123", from_seq=0, limit=10)
        
        # Verify that the event we wrote is returned (fallback mode returns all events)
        assert len(events) == 1, "Fallback should return the event we wrote to memory"
        assert events[0].event_id == "event-1"
        assert events[0].data == {"amount": 100}

    @pytest.mark.asyncio
    async def test_get_latest_seq_returns_none_on_fallback(self, pg_store, memory_store):
        """Test that get_latest_seq returns None when using fallback"""
        failing_pool = MagicMock()
        failing_pool.acquire = lambda: FailingPoolAcquire("PG connection failed")
        
        failing_pg_store = PostgresEventStore(pool=failing_pool)
        store = EventStoreWithFallback(
            pg_event_store=failing_pg_store,
            memory_event_store=memory_store,
        )
        
        # get_latest_seq should return None in fallback mode
        # because memory store doesn't track seq per stream_key
        seq = await store.get_latest_seq("order-123")
        assert seq is None

    def test_enable_postgres(self, memory_store):
        """Test enabling PostgreSQL after it was disabled"""
        store = EventStoreWithFallback(memory_event_store=memory_store)
        assert store.is_using_postgres is False
        
        # Create a mock PG store
        mock_pool = MagicMock()
        pg_store = PostgresEventStore(pool=mock_pool)
        
        store.enable_postgres(pg_store)
        assert store.is_using_postgres is True

    def test_disable_postgres(self, pg_store, memory_store):
        """Test disabling PostgreSQL"""
        store = EventStoreWithFallback(
            pg_event_store=pg_store,
            memory_event_store=memory_store,
        )
        assert store.is_using_postgres is True
        
        store.disable_postgres()
        assert store.is_using_postgres is False


class TestStreamEventDataclass:
    """Tests for StreamEvent dataclass"""

    def test_stream_event_creation(self):
        """Test StreamEvent can be created with all fields"""
        event = StreamEvent(
            event_id="event-1",
            stream_key="order-123",
            seq=0,
            event_type="OrderCreated",
            aggregate_id="order-123",
            aggregate_type="Order",
            timestamp=datetime.now(timezone.utc),
            ts_ms=1234567890,
            data={"amount": 100},
            metadata={"user": "test"},
            schema_version=1,
        )
        assert event.event_id == "event-1"
        assert event.stream_key == "order-123"
        assert event.seq == 0
        assert event.schema_version == 1

    def test_stream_event_default_schema_version(self):
        """Test StreamEvent has default schema_version of 1"""
        event = StreamEvent(
            event_id="event-1",
            stream_key="order-123",
            seq=0,
            event_type="OrderCreated",
            aggregate_id="order-123",
            aggregate_type="Order",
            timestamp=datetime.now(timezone.utc),
            ts_ms=1234567890,
            data={},
            metadata={},
        )
        assert event.schema_version == 1
