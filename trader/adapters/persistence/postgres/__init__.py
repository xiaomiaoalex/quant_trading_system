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
import asyncio
import uuid
from typing import List, Optional, Dict, Any, Tuple, TYPE_CHECKING
from datetime import datetime, timezone
from dataclasses import dataclass
import json

if TYPE_CHECKING:
    import asyncpg

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


@dataclass
class StoredRiskEvent:
    """Stored risk event representation"""
    event_id: str
    dedup_key: str
    scope: str
    reason: str
    recommended_level: int
    ingested_at: datetime
    data: Dict[str, Any]


@dataclass
class StoredUpgradeRecord:
    """Stored upgrade record representation"""
    upgrade_key: str
    scope: str
    level: int
    reason: str
    dedup_key: str
    recorded_at: datetime


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
            
            await conn.execute("""
                CREATE TABLE IF NOT EXISTS risk_events (
                    event_id VARCHAR(255) PRIMARY KEY,
                    dedup_key VARCHAR(512) NOT NULL UNIQUE,
                    scope VARCHAR(255) NOT NULL,
                    reason VARCHAR(512) NOT NULL,
                    recommended_level INTEGER NOT NULL,
                    ingested_at TIMESTAMP WITH TIME ZONE NOT NULL,
                    data JSONB NOT NULL DEFAULT '{}'
                )
            """)
            
            await conn.execute("""
                CREATE INDEX IF NOT EXISTS idx_risk_events_dedup_key 
                ON risk_events(dedup_key)
            """)
            
            await conn.execute("""
                CREATE TABLE IF NOT EXISTS risk_upgrades (
                    upgrade_key VARCHAR(512) PRIMARY KEY,
                    scope VARCHAR(255) NOT NULL,
                    level INTEGER NOT NULL,
                    reason VARCHAR(512) NOT NULL,
                    dedup_key VARCHAR(512) NOT NULL,
                    recorded_at TIMESTAMP WITH TIME ZONE NOT NULL
                )
            """)

            await conn.execute("""
                CREATE TABLE IF NOT EXISTS risk_upgrade_effects (
                    upgrade_key VARCHAR(512) PRIMARY KEY,
                    scope VARCHAR(255) NOT NULL,
                    level INTEGER NOT NULL,
                    status VARCHAR(32) NOT NULL DEFAULT 'PENDING',
                    attempts INTEGER NOT NULL DEFAULT 0,
                    last_error TEXT,
                    updated_at TIMESTAMP WITH TIME ZONE NOT NULL DEFAULT NOW()
                )
            """)

            await conn.execute("""
                CREATE INDEX IF NOT EXISTS idx_risk_upgrade_effects_status 
                ON risk_upgrade_effects(status)
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

    async def save_risk_event(self, event_data: Dict[str, Any]) -> tuple[str, bool]:
        """
        Save a risk event with deduplication.
        
        Uses dedup_key unique constraint to ensure idempotency.
        
        Args:
            event_data: Dictionary containing full event data (from model_dump)
                
        Returns:
            Tuple of (event_id, created) where created is True if new, False if duplicate
        """
        event_id = event_data.get("event_id") or str(uuid.uuid4())
        dedup_key = event_data["dedup_key"]
        scope = event_data.get("scope", "GLOBAL")
        reason = event_data.get("reason", "")
        recommended_level = event_data.get("recommended_level", 0)
        ingested_at = event_data.get("ingested_at") or datetime.now(timezone.utc)
        data = json.dumps(event_data)
        
        try:
            async with self._pool.acquire() as conn:
                await conn.execute(
                    """
                    INSERT INTO risk_events (event_id, dedup_key, scope, reason, recommended_level, ingested_at, data)
                    VALUES ($1, $2, $3, $4, $5, $6, $7)
                    """,
                    event_id, dedup_key, scope, reason, recommended_level, ingested_at, data,
                )
            return event_id, True
        except asyncpg.UniqueViolationError:
            existing = await self.get_risk_event(dedup_key)
            if existing:
                return existing.event_id, False
            return event_id, False

    async def ingest_event_with_upgrade(self, event_data: Dict[str, Any], 
                                       upgrade_key: str, upgrade_level: int) -> Tuple[Optional[str], bool, bool, bool]:
        """
        Atomically ingest risk event and record upgrade with effect in a single transaction.
        
        This implements: BEGIN -> dedup -> upgrade record -> side-effect intent -> COMMIT
        
        Args:
            event_data: Dictionary containing full event data
            upgrade_key: The upgrade key
            upgrade_level: Target level for upgrade
            
        Returns:
            Tuple of (event_id, created, is_first_upgrade, is_first_effect)
            - event_id: The event ID (None if duplicate)
            - created: True if new event was created
            - is_first_upgrade: True if this is first time recording this upgrade
            - is_first_effect: True if this is first time recording this effect
        """
        from asyncpg import UniqueViolationError
        
        event_id = event_data.get("event_id") or str(uuid.uuid4())
        dedup_key = event_data["dedup_key"]
        scope = event_data.get("scope", "GLOBAL")
        reason = event_data.get("reason", "")
        recommended_level = event_data.get("recommended_level", 0)
        ingested_at = event_data.get("ingested_at") or datetime.now(timezone.utc)
        data = json.dumps(event_data)
        
        async with self._pool.acquire() as conn:
            async with conn.transaction():
                created = False
                try:
                    await conn.execute(
                        """
                        INSERT INTO risk_events (event_id, dedup_key, scope, reason, recommended_level, ingested_at, data)
                        VALUES ($1, $2, $3, $4, $5, $6, $7)
                        """,
                        event_id, dedup_key, scope, reason, recommended_level, ingested_at, data,
                    )
                    created = True
                except UniqueViolationError:
                    existing = await conn.fetchrow(
                        "SELECT event_id FROM risk_events WHERE dedup_key = $1",
                        dedup_key,
                    )
                    if existing:
                        event_id = existing["event_id"]
                    created = False
                
                result_upgrade = await conn.execute(
                    """
                    INSERT INTO risk_upgrades (upgrade_key, scope, level, reason, dedup_key, recorded_at)
                    VALUES ($1, $2, $3, $4, $5, NOW())
                    ON CONFLICT (upgrade_key) DO NOTHING
                    """,
                    upgrade_key, scope, upgrade_level, reason, dedup_key,
                )
                is_first_upgrade = result_upgrade != "INSERT 0 0"
                
                result_effect = await conn.execute(
                    """
                    INSERT INTO risk_upgrade_effects (upgrade_key, scope, level, status, attempts, updated_at)
                    VALUES ($1, $2, $3, 'PENDING', 1, NOW())
                    ON CONFLICT (upgrade_key) DO NOTHING
                    """,
                    upgrade_key, scope, upgrade_level,
                )
                is_first_effect = result_effect != "INSERT 0 0"
                
                return event_id, created, is_first_upgrade, is_first_effect

    async def get_risk_event(self, dedup_key: str) -> Optional[StoredRiskEvent]:
        """
        Get a risk event by dedup_key.
        
        Args:
            dedup_key: The deduplication key
            
        Returns:
            StoredRiskEvent or None if not found
        """
        async with self._pool.acquire() as conn:
            row = await conn.fetchrow(
                """
                SELECT event_id, dedup_key, scope, reason, recommended_level, ingested_at, data
                FROM risk_events
                WHERE dedup_key = $1
                """,
                dedup_key,
            )
            
        if row:
            return StoredRiskEvent(
                event_id=row["event_id"],
                dedup_key=row["dedup_key"],
                scope=row["scope"],
                reason=row["reason"],
                recommended_level=row["recommended_level"],
                ingested_at=row["ingested_at"],
                data=row["data"],
            )
        return None

    async def save_upgrade_record(self, upgrade_key: str, upgrade_data: Dict[str, Any]) -> None:
        """
        Save an upgrade record for idempotency.
        
        Note: This method is kept for backward compatibility but uses DO NOTHING
        to maintain the "first write only" idempotency principle.
        
        Args:
            upgrade_key: Unique upgrade key
            upgrade_data: Dictionary containing:
                - scope: Risk scope
                - level: Target level
                - reason: Upgrade reason
                - dedup_key: Related dedup key
                - recorded_at: Recording timestamp
        """
        scope = upgrade_data.get("scope", "GLOBAL")
        level = upgrade_data.get("level", 0)
        reason = upgrade_data.get("reason", "")
        dedup_key = upgrade_data.get("dedup_key", "")
        recorded_at = upgrade_data.get("recorded_at", datetime.now(timezone.utc))
        
        async with self._pool.acquire() as conn:
            await conn.execute(
                """
                INSERT INTO risk_upgrades (upgrade_key, scope, level, reason, dedup_key, recorded_at)
                VALUES ($1, $2, $3, $4, $5, $6)
                ON CONFLICT (upgrade_key) DO NOTHING
                """,
                upgrade_key, scope, level, reason, dedup_key, recorded_at,
            )

    async def get_upgrade_record(self, upgrade_key: str) -> Optional[StoredUpgradeRecord]:
        """
        Get an upgrade record by upgrade_key.
        
        Args:
            upgrade_key: The upgrade key
            
        Returns:
            StoredUpgradeRecord or None if not found
        """
        async with self._pool.acquire() as conn:
            row = await conn.fetchrow(
                """
                SELECT upgrade_key, scope, level, reason, dedup_key, recorded_at
                FROM risk_upgrades
                WHERE upgrade_key = $1
                """,
                upgrade_key,
            )
            
        if row:
            return StoredUpgradeRecord(
                upgrade_key=row["upgrade_key"],
                scope=row["scope"],
                level=row["level"],
                reason=row["reason"],
                dedup_key=row["dedup_key"],
                recorded_at=row["recorded_at"],
            )
        return None

    async def try_record_upgrade(self, upgrade_key: str, upgrade_data: Dict[str, Any]) -> bool:
        """
        Try to record an upgrade action. Returns True if first write, False if already exists.
        
        Args:
            upgrade_key: Unique upgrade key
            upgrade_data: Dictionary containing:
                - scope: Risk scope
                - level: Target level
                - reason: Upgrade reason
                - dedup_key: Related dedup key
                
        Returns:
            True if this is the first time recording this upgrade_key, False if already exists
        """
        scope = upgrade_data.get("scope", "GLOBAL")
        level = upgrade_data.get("level", 0)
        reason = upgrade_data.get("reason", "")
        dedup_key = upgrade_data.get("dedup_key", "")
        recorded_at = upgrade_data.get("recorded_at", datetime.now(timezone.utc))
        
        async with self._pool.acquire() as conn:
            result = await conn.execute(
                """
                INSERT INTO risk_upgrades (upgrade_key, scope, level, reason, dedup_key, recorded_at)
                VALUES ($1, $2, $3, $4, $5, $6)
                ON CONFLICT (upgrade_key) DO NOTHING
                """,
                upgrade_key, scope, level, reason, dedup_key, recorded_at,
            )
            return result != "INSERT 0 0"

    async def clear(self) -> None:
        """Clear all events, snapshots, risk_events and risk_upgrades (for testing)"""
        async with self._pool.acquire() as conn:
            await conn.execute("DELETE FROM event_log")
            await conn.execute("DELETE FROM snapshots")
            await conn.execute("DELETE FROM risk_events")
            await conn.execute("DELETE FROM risk_upgrades")
            await conn.execute("DELETE FROM risk_upgrade_effects")

    async def try_record_upgrade_with_effect(self, upgrade_key: str, scope: str, level: int, 
                                            reason: str, dedup_key: str) -> Tuple[bool, bool]:
        """
        Atomically record upgrade and side-effect intent in a single transaction.
        
        Returns:
            Tuple of (is_first_upgrade, is_first_effect)
            - is_first_upgrade: True if this is the first time recording this upgrade_key
            - is_first_effect: True if this is the first time recording this effect
        """
        async with self._pool.acquire() as conn:
            async with conn.transaction():
                result_upgrade = await conn.execute(
                    """
                    INSERT INTO risk_upgrades (upgrade_key, scope, level, reason, dedup_key, recorded_at)
                    VALUES ($1, $2, $3, $4, $5, NOW())
                    ON CONFLICT (upgrade_key) DO NOTHING
                    """,
                    upgrade_key, scope, level, reason, dedup_key,
                )
                is_first_upgrade = result_upgrade != "INSERT 0 0"
                
                result_effect = await conn.execute(
                    """
                    INSERT INTO risk_upgrade_effects (upgrade_key, scope, level, status, attempts, updated_at)
                    VALUES ($1, $2, $3, 'PENDING', 1, NOW())
                    ON CONFLICT (upgrade_key) DO NOTHING
                    """,
                    upgrade_key, scope, level,
                )
                is_first_effect = result_effect != "INSERT 0 0"
                
                return is_first_upgrade, is_first_effect

    async def mark_effect_applied(self, upgrade_key: str) -> None:
        """Mark side-effect as successfully applied"""
        async with self._pool.acquire() as conn:
            await conn.execute(
                """
                UPDATE risk_upgrade_effects 
                SET status = 'APPLIED', updated_at = NOW()
                WHERE upgrade_key = $1
                """,
                upgrade_key,
            )

    async def mark_effect_failed(self, upgrade_key: str, error: str) -> None:
        """Mark side-effect as failed with error message"""
        async with self._pool.acquire() as conn:
            await conn.execute(
                """
                UPDATE risk_upgrade_effects 
                SET status = 'FAILED', last_error = $2, attempts = attempts + 1, updated_at = NOW()
                WHERE upgrade_key = $1
                """,
                upgrade_key, error,
            )

    async def get_pending_effects(self) -> List[Dict[str, Any]]:
        """Get all pending or failed effects for recovery"""
        async with self._pool.acquire() as conn:
            rows = await conn.fetch(
                """
                SELECT upgrade_key, scope, level, status, attempts, last_error, updated_at
                FROM risk_upgrade_effects
                WHERE status IN ('PENDING', 'FAILED')
                ORDER BY updated_at
                """
            )
            return [dict(row) for row in rows]


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


_pool_cache: Optional["asyncpg.Pool"] = None
_pool_config_hash: Optional[str] = None


def _get_pool_config_hash() -> str:
    """Generate a hash of the current pool configuration"""
    connection_string = os.getenv("POSTGRES_CONNECTION_STRING", "")
    host = os.getenv("POSTGRES_HOST", "localhost")
    port = os.getenv("POSTGRES_PORT", "5432")
    database = os.getenv("POSTGRES_DB", "trading")
    user = os.getenv("POSTGRES_USER", "trader")
    return f"{connection_string}:{host}:{port}:{database}:{user}"


async def _get_pool(timeout: float = 2.0) -> Optional["asyncpg.Pool"]:
    """Get or create a cached connection pool"""
    global _pool_cache, _pool_config_hash
    
    current_hash = _get_pool_config_hash()
    
    if _pool_cache is not None and _pool_config_hash == current_hash:
        try:
            await _pool_cache.fetchval("SELECT 1")
            return _pool_cache
        except Exception:
            _pool_cache = None
    
    connection_string = os.getenv("POSTGRES_CONNECTION_STRING")
    host = os.getenv("POSTGRES_HOST", "localhost")
    port = os.getenv("POSTGRES_PORT", "5432")
    database = os.getenv("POSTGRES_DB", "trading")
    user = os.getenv("POSTGRES_USER", "trader")
    password = os.getenv("POSTGRES_PASSWORD", "")
    
    conn_args = {"min_size": 1, "max_size": 2}
    if connection_string:
        conn_args["dsn"] = connection_string
    else:
        conn_args["host"] = host
        conn_args["port"] = int(port)
        conn_args["database"] = database
        conn_args["user"] = user
        if password:
            conn_args["password"] = password
    
    try:
        _pool_cache = await asyncpg.create_pool(**conn_args)
        _pool_config_hash = current_hash
        return _pool_cache
    except Exception:
        _pool_cache = None
        _pool_config_hash = None
        return None


async def close_pool() -> None:
    """Close the cached connection pool"""
    global _pool_cache, _pool_config_hash
    if _pool_cache is not None:
        await _pool_cache.close()
        _pool_cache = None
        _pool_config_hash = None


async def check_postgres_connection(timeout: float = 2.0) -> tuple[bool, str]:
    """
    Check if PostgreSQL is actually reachable.
    
    Performs an actual connection test with a short timeout.
    Uses a cached connection pool for efficiency.
    
    Args:
        timeout: Connection timeout in seconds (default 2.0)
        
    Returns:
        Tuple of (is_reachable, message)
    """
    if not ASYNCPG_AVAILABLE:
        return False, "asyncpg not installed"
    
    if not is_postgres_available():
        return False, "PostgreSQL not configured"
    
    try:
        pool = await _get_pool(timeout)
        if pool is None:
            return False, "Failed to create connection pool"
        
        async with pool.acquire(timeout=timeout) as conn:
            result = await conn.fetchval("SELECT 1")
            if result == 1:
                return True, "Connection successful"
            return False, "Unexpected response"
    except asyncio.TimeoutError:
        return False, f"Connection timeout ({timeout}s)"
    except ConnectionRefusedError:
        return False, "Connection refused"
    except OSError as e:
        return False, f"Network error: {str(e)}"
    except Exception as e:
        return False, f"Connection failed: {str(e)}"
