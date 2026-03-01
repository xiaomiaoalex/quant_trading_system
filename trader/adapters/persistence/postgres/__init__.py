"""
PostgreSQL Storage - Event Sourcing Storage Implementation
==========================================================
PostgreSQL-based implementation for event sourcing storage.

This module provides:
- Event log persistence
- Snapshot persistence
- Event replay capabilities

Requirements:
- PostgreSQL database
- asyncpg package

Usage:
    # With environment variables configured
    storage = PostgreSQLStorage(
        host="localhost",
        port=5432,
        database="trading",
        user="trader",
        password="secret"
    )
    await storage.connect()
"""
import os
from typing import List, Optional, Dict, Any
from datetime import datetime, timezone
from dataclasses import dataclass
import json

try:
    import asyncpg
    ASYNCPG_AVAILABLE = True
except ImportError:
    ASYNCPG_AVAILABLE = False
    asyncpg = None


@dataclass
class StoredEvent:
    """Stored event representation"""
    event_id: str
    event_type: str
    aggregate_id: str
    aggregate_type: str
    timestamp: datetime
    data: Dict[str, Any]
    metadata: Dict[str, Any]


@dataclass
class StoredSnapshot:
    """Stored snapshot representation"""
    snapshot_id: str
    stream_key: str
    aggregate_id: str
    aggregate_type: str
    timestamp: datetime
    state: Dict[str, Any]


class PostgreSQLStorage:
    """
    PostgreSQL Storage for Event Sourcing
    ======================================
    
    Responsibilities:
    - Store domain events in event_log table
    - Store aggregate snapshots in snapshots table
    - Support event replay and snapshot recovery
    
    Database Schema:
    - event_log: event_id, event_type, aggregate_id, aggregate_type, timestamp, data, metadata
    - snapshots: snapshot_id, stream_key, aggregate_id, aggregate_type, timestamp, state
    
    Note:
    - This is a synchronous wrapper around asyncpg
    - For async usage, use PostgreSQLStorageAsync
    """

    def __init__(
        self,
        host: Optional[str] = None,
        port: Optional[int] = None,
        database: Optional[str] = None,
        user: Optional[str] = None,
        password: Optional[str] = None,
        connection_string: Optional[str] = None,
    ):
        self._host = host or os.getenv("POSTGRES_HOST", "localhost")
        self._port = port or int(os.getenv("POSTGRES_PORT", "5432"))
        self._database = database or os.getenv("POSTGRES_DB", "trading")
        self._user = user or os.getenv("POSTGRES_USER", "trader")
        self._password = password or os.getenv("POSTGRES_PASSWORD", "")
        self._connection_string = connection_string or os.getenv("POSTGRES_CONNECTION_STRING")
        
        self._pool: Optional[asyncpg.Pool] = None
        self._connected = False

    @property
    def is_connected(self) -> bool:
        """Check if storage is connected"""
        return self._connected

    async def connect(self) -> None:
        """Connect to PostgreSQL"""
        if self._connection_string:
            self._pool = await asyncpg.create_pool(self._connection_string)
        else:
            self._pool = await asyncpg.create_pool(
                host=self._host,
                port=self._port,
                database=self._database,
                user=self._user,
                password=self._password,
            )
        await self._initialize_schema()
        self._connected = True

    async def disconnect(self) -> None:
        """Disconnect from PostgreSQL"""
        if self._pool:
            await self._pool.close()
        self._connected = False

    async def _initialize_schema(self) -> None:
        """Initialize database schema"""
        async with self._pool.acquire() as conn:
            await conn.execute("""
                CREATE TABLE IF NOT EXISTS event_log (
                    event_id VARCHAR(255) PRIMARY KEY,
                    event_type VARCHAR(255) NOT NULL,
                    aggregate_id VARCHAR(255) NOT NULL,
                    aggregate_type VARCHAR(255) NOT NULL,
                    timestamp TIMESTAMP WITH TIME ZONE NOT NULL,
                    data JSONB NOT NULL,
                    metadata JSONB DEFAULT '{}'
                )
            """)
            
            await conn.execute("""
                CREATE TABLE IF NOT EXISTS snapshots (
                    snapshot_id VARCHAR(255) PRIMARY KEY,
                    stream_key VARCHAR(255) NOT NULL,
                    aggregate_id VARCHAR(255) NOT NULL,
                    aggregate_type VARCHAR(255) NOT NULL,
                    timestamp TIMESTAMP WITH TIME ZONE NOT NULL,
                    state JSONB NOT NULL,
                    UNIQUE(stream_key, aggregate_id)
                )
            """)
            
            await conn.execute("""
                CREATE INDEX IF NOT EXISTS idx_event_log_aggregate_id 
                ON event_log(aggregate_id)
            """)
            
            await conn.execute("""
                CREATE INDEX IF NOT EXISTS idx_event_log_timestamp 
                ON event_log(timestamp)
            """)
            
            await conn.execute("""
                CREATE INDEX IF NOT EXISTS idx_snapshots_stream_key 
                ON snapshots(stream_key)
            """)

    async def append_event(self, event) -> str:
        """
        Append an event to the event log
        
        Args:
            event: Event object with event_id, event_type, aggregate_id, 
                   aggregate_type, timestamp, data, metadata attributes
                   
        Returns:
            event_id of the stored event
        """
        async with self._pool.acquire() as conn:
            await conn.execute(
                """
                INSERT INTO event_log (event_id, event_type, aggregate_id, aggregate_type, timestamp, data, metadata)
                VALUES ($1, $2, $3, $4, $5, $6, $7)
                """,
                event.event_id,
                event.event_type.value if hasattr(event.event_type, 'value') else str(event.event_type),
                event.aggregate_id,
                event.aggregate_type,
                event.timestamp,
                json.dumps(event.data),
                json.dumps(event.metadata),
            )
        return event.event_id

    async def get_events(
        self,
        aggregate_id: Optional[str] = None,
        event_type: Optional[str] = None,
        since: Optional[datetime] = None,
        limit: int = 1000,
    ) -> List[StoredEvent]:
        """
        Query events from the event log
        
        Args:
            aggregate_id: Filter by aggregate ID
            event_type: Filter by event type
            since: Filter events after this timestamp
            limit: Maximum number of events to return
            
        Returns:
            List of stored events
        """
        query = "SELECT event_id, event_type, aggregate_id, aggregate_type, timestamp, data, metadata FROM event_log WHERE 1=1"
        params = []
        param_count = 0
        
        if aggregate_id:
            param_count += 1
            query += f" AND aggregate_id = ${param_count}"
            params.append(aggregate_id)
            
        if event_type:
            param_count += 1
            query += f" AND event_type = ${param_count}"
            params.append(event_type)
            
        if since:
            param_count += 1
            query += f" AND timestamp >= ${param_count}"
            params.append(since)
            
        query += f" ORDER BY timestamp ASC LIMIT ${param_count + 1}"
        params.append(limit)
        
        async with self._pool.acquire() as conn:
            rows = await conn.fetch(query, *params)
            
        return [
            StoredEvent(
                event_id=row["event_id"],
                event_type=row["event_type"],
                aggregate_id=row["aggregate_id"],
                aggregate_type=row["aggregate_type"],
                timestamp=row["timestamp"],
                data=row["data"],
                metadata=row["metadata"],
            )
            for row in rows
        ]

    async def save_snapshot(self, snapshot_data: Dict[str, Any]) -> str:
        """
        Save a snapshot
        
        Args:
            snapshot_data: Dictionary containing snapshot information
                - snapshot_id: Unique identifier
                - stream_key: Stream identifier
                - aggregate_id: Aggregate ID
                - aggregate_type: Aggregate type
                - timestamp: Snapshot timestamp
                - state: Current state
                
        Returns:
            snapshot_id of the stored snapshot
        """
        async with self._pool.acquire() as conn:
            await conn.execute(
                """
                INSERT INTO snapshots (snapshot_id, stream_key, aggregate_id, aggregate_type, timestamp, state)
                VALUES ($1, $2, $3, $4, $5, $6)
                ON CONFLICT (snapshot_id) DO UPDATE SET
                    stream_key = EXCLUDED.stream_key,
                    aggregate_id = EXCLUDED.aggregate_id,
                    aggregate_type = EXCLUDED.aggregate_type,
                    timestamp = EXCLUDED.timestamp,
                    state = EXCLUDED.state
                """,
                snapshot_data["snapshot_id"],
                snapshot_data["stream_key"],
                snapshot_data["aggregate_id"],
                snapshot_data.get("aggregate_type", "Unknown"),
                snapshot_data.get("timestamp", datetime.now(timezone.utc)),
                json.dumps(snapshot_data.get("state", {})),
            )
        return snapshot_data["snapshot_id"]

    async def get_latest_snapshot(self, stream_key: str) -> Optional[StoredSnapshot]:
        """
        Get the latest snapshot for a stream
        
        Args:
            stream_key: Stream identifier
            
        Returns:
            Latest snapshot or None if not found
        """
        async with self._pool.acquire() as conn:
            row = await conn.fetchrow(
                """
                SELECT snapshot_id, stream_key, aggregate_id, aggregate_type, timestamp, state
                FROM snapshots
                WHERE stream_key = $1
                ORDER BY timestamp DESC
                LIMIT 1
                """,
                stream_key,
            )
            
        if row:
            return StoredSnapshot(
                snapshot_id=row["snapshot_id"],
                stream_key=row["stream_key"],
                aggregate_id=row["aggregate_id"],
                aggregate_type=row["aggregate_type"],
                timestamp=row["timestamp"],
                state=row["state"],
            )
        return None

    async def clear(self) -> None:
        """Clear all events and snapshots (for testing)"""
        async with self._pool.acquire() as conn:
            await conn.execute("DELETE FROM event_log")
            await conn.execute("DELETE FROM snapshots")


def is_postgres_available() -> bool:
    """
    Check if PostgreSQL is available
    
    Returns True if:
    - asyncpg package is installed, AND
    - (POSTGRES_CONNECTION_STRING is set OR all required environment variables are set)
    """
    if not ASYNCPG_AVAILABLE:
        return False
    
    if os.getenv("POSTGRES_CONNECTION_STRING"):
        return True
    
    return bool(
        os.getenv("POSTGRES_HOST") 
        and os.getenv("POSTGRES_DB") 
        and os.getenv("POSTGRES_USER")
    )
