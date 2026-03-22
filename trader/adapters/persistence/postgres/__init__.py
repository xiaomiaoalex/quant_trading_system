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
import logging
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

logger = logging.getLogger(__name__)


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

    @staticmethod
    def _decode_json_field(value: Any) -> Dict[str, Any]:
        """Normalize JSON/JSONB payloads from asyncpg into dicts."""
        if isinstance(value, dict):
            return value
        if isinstance(value, str):
            return json.loads(value)
        return {}

    async def connect(self) -> None:
        """Connect to PostgreSQL"""
        pool_kwargs = {"min_size": 1, "max_size": 2}
        if self._connection_string:
            self._pool = await asyncpg.create_pool(self._connection_string, **pool_kwargs)
        else:
            self._pool = await asyncpg.create_pool(
                host=self._host,
                port=self._port,
                database=self._database,
                user=self._user,
                password=self._password,
                **pool_kwargs,
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

            await conn.execute("""
                CREATE INDEX IF NOT EXISTS idx_risk_upgrade_effects_updated_at
                ON risk_upgrade_effects(updated_at DESC)
            """)

            await conn.execute("""
                CREATE TABLE IF NOT EXISTS feature_values (
                    symbol VARCHAR(50) NOT NULL,
                    feature_name VARCHAR(255) NOT NULL,
                    version VARCHAR(50) NOT NULL,
                    ts_ms BIGINT NOT NULL,
                    value JSONB NOT NULL,
                    meta JSONB DEFAULT '{}',
                    value_hash VARCHAR(16) NOT NULL,
                    ingested_at TIMESTAMP WITH TIME ZONE NOT NULL DEFAULT NOW(),
                    CONSTRAINT uq_feature_values_key UNIQUE (symbol, feature_name, version, ts_ms)
                )
            """)

            await conn.execute("""
                CREATE INDEX IF NOT EXISTS idx_feature_values_symbol_feature_version 
                ON feature_values(symbol, feature_name, version)
            """)

            await conn.execute("""
                CREATE INDEX IF NOT EXISTS idx_feature_values_ts_ms 
                ON feature_values(ts_ms DESC)
            """)

    async def save_event(self, event) -> str:
        """
        Save an event to the event log.
        
        This method provides idempotent event append using stream_key and seq.
        Uses atomic SQL to avoid TOCTOU race condition.
        
        Args:
            event: Event object with event_id, event_type, aggregate_id, 
                   aggregate_type, timestamp, data, metadata attributes
                   
        Returns:
            event_id of the stored event (the same event_id is returned for duplicates)
        """
        import uuid
        
        # Derive stream_key from aggregate_type and aggregate_id
        # If aggregate_id is empty, generate a unique stream_key using event_id to prevent collisions
        if not event.aggregate_id:
            # Generate unique stream_key for events without aggregate_id
            # This maintains backward compatibility while preventing stream collisions
            event_id_for_key = event.event_id or str(uuid.uuid4())
            stream_key = f"{event.aggregate_type}-legacy-{event_id_for_key}"
            logger.warning(
                "EVENT_STORE_MISSING_AGGREGATE_ID",
                extra={
                    "event_id": event.event_id,
                    "event_type": getattr(event, 'event_type', 'unknown'),
                    "generated_stream_key": stream_key,
                },
            )
        else:
            stream_key = f"{event.aggregate_type}-{event.aggregate_id}"
        
        # Prepare event data
        event_id = event.event_id or str(uuid.uuid4())
        event_type = event.event_type.value if hasattr(event.event_type, 'value') else str(event.event_type)
        timestamp = event.timestamp or datetime.now(timezone.utc)
        ts_ms = int(timestamp.timestamp() * 1000)
        
        async with self._pool.acquire() as conn:
            try:
                # Use atomic CTE with advisory lock to prevent race condition
                # Use pg_advisory_xact_lock(hashtext(stream_key)) to ensure exclusive access.
                # This locks the stream_key atomically, preventing concurrent transactions from
                # both calculating the same next_seq when the stream is empty (no rows to FOR UPDATE).
                row = await conn.fetchrow(
                    """
                    WITH lock AS (
                        SELECT pg_advisory_xact_lock(hashtext($2)) AS lock_result
                    ),
                    new_event AS (
                        SELECT COALESCE(MAX(seq), -1) + 1 as next_seq
                        FROM event_log
                        WHERE stream_key = $2
                    ),
                    insert_result AS (
                        INSERT INTO event_log (event_id, stream_key, seq, event_type, aggregate_id, aggregate_type, timestamp, ts_ms, data, metadata, schema_version)
                        SELECT $1, $2, new_event.next_seq, $3, $4, $5, $6, $7, $8, $9, $10
                        FROM new_event
                        ON CONFLICT (stream_key, seq) DO NOTHING
                        RETURNING event_id
                    ),
                    -- If insert succeeded, use that event_id; otherwise query the existing one
                    final AS (
                        SELECT event_id FROM insert_result
                        UNION ALL
                        SELECT e.event_id 
                        FROM event_log e, new_event n
                        WHERE e.stream_key = $2 AND e.seq = n.next_seq
                        AND NOT EXISTS (SELECT 1 FROM insert_result)
                    )
                    SELECT event_id FROM final LIMIT 1
                    """,
                    event_id,
                    stream_key,
                    event_type,
                    event.aggregate_id,
                    event.aggregate_type,
                    timestamp,
                    ts_ms,
                    json.dumps(event.data),
                    json.dumps(event.metadata or {}),
                    1,
                )
                
                stored_event_id = row["event_id"] if row else None
                
                if stored_event_id == event_id:
                    logger.debug(
                        "EVENT_SAVED",
                        extra={"stream_key": stream_key, "event_id": stored_event_id},
                    )
                else:
                    # Conflict occurred - a different event_id was already stored at this seq
                    logger.debug(
                        "EVENT_DUPLICATE_IGNORED",
                        extra={"stream_key": stream_key, "requested_event_id": event_id, "stored_event_id": stored_event_id},
                    )
                    
            except Exception as e:
                logger.error(
                    "PG_SAVE_EVENT_ERROR",
                    extra={
                        "stream_key": stream_key,
                        "error": str(e),
                    },
                )
                raise
        
        # Return the actual stored event_id (may differ from caller's event_id if conflict occurred)
        return stored_event_id

    async def append_event(self, event) -> str:
        """
        Append an event to the event log (legacy method)
        
        .. deprecated::
            This method uses the original schema without stream_key/seq.
            For new implementations, use save_event() instead.
        
        Note:
            This method creates a unique stream_key per event (legacy behavior).
            Prefer save_event() which supports stream-based ordering with seq.
        
        Idempotency:
            Uses ON CONFLICT (event_id) DO NOTHING to ensure duplicate events
            (same event_id) are ignored without error.
        
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
                ON CONFLICT (event_id) DO NOTHING
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
                data=self._decode_json_field(row["data"]),
                metadata=self._decode_json_field(row["metadata"]),
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
                state=self._decode_json_field(row["state"]),
            )
        return None

    async def reconstruct_state(
        self,
        stream_key: str,
        projection_fn: callable = None,
    ) -> Optional[Dict[str, Any]]:
        """
        Reconstruct aggregate state from snapshot + events.
        
        This implements the core event sourcing pattern:
        1. Get latest snapshot by stream_key
        2. Get all events after snapshot timestamp using snapshot's aggregate_id
        3. Apply projection to rebuild state
        
        Args:
            stream_key: The stream key to locate the snapshot
            projection_fn: Optional function to apply events to state.
                          If not provided, returns snapshot state + events for external reconstruction.
        
        Returns:
            Reconstructed state dictionary, or None if no snapshot exists
        """
        snapshot = await self.get_latest_snapshot(stream_key)
        
        if snapshot is None:
            return None
        
        events_after_snapshot = await self.get_events(
            aggregate_id=snapshot.aggregate_id,
            since=snapshot.timestamp,
        )
        
        events_after_snapshot = [
            e for e in events_after_snapshot
            if e.timestamp > snapshot.timestamp
        ]
        
        if projection_fn is None:
            return {
                "snapshot": {
                    "snapshot_id": snapshot.snapshot_id,
                    "aggregate_id": snapshot.aggregate_id,
                    "state": snapshot.state,
                    "timestamp": snapshot.timestamp,
                },
                "events": [
                    {
                        "event_id": e.event_id,
                        "event_type": e.event_type,
                        "data": e.data,
                        "timestamp": e.timestamp,
                    }
                    for e in events_after_snapshot
                ],
                "event_count": len(events_after_snapshot),
            }
        
        current_state = snapshot.state.copy()
        for event in events_after_snapshot:
            current_state = projection_fn(current_state, event)
        
        return current_state

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
        event_id = event_data.get("event_id") or str(uuid.uuid4())
        dedup_key = event_data["dedup_key"]
        scope = event_data.get("scope", "GLOBAL")
        reason = event_data.get("reason", "")
        recommended_level = event_data.get("recommended_level", 0)
        ingested_at = event_data.get("ingested_at") or datetime.now(timezone.utc)
        data = json.dumps(event_data)
        
        async with self._pool.acquire() as conn:
            async with conn.transaction():
                async def _execute_step(step_name: str, operation, **context: Any):
                    try:
                        return await operation()
                    except (asyncpg.PostgresError, asyncpg.InterfaceError) as exc:
                        logger.exception(
                            "%s failed [%s] context=%s",
                            step_name,
                            exc.__class__.__name__,
                            context,
                        )
                        raise

                event_marker = await _execute_step(
                    "Risk event insert",
                    lambda: conn.fetchval(
                        """
                        INSERT INTO risk_events (event_id, dedup_key, scope, reason, recommended_level, ingested_at, data)
                        VALUES ($1, $2, $3, $4, $5, $6, $7)
                        ON CONFLICT (dedup_key) DO NOTHING
                        RETURNING event_id
                        """,
                        event_id, dedup_key, scope, reason, recommended_level, ingested_at, data,
                    ),
                    event_id=event_id,
                    dedup_key=dedup_key,
                    scope=scope,
                    recommended_level=recommended_level,
                )
                created = event_marker is not None
                if not created:
                    existing = await conn.fetchrow(
                        "SELECT event_id FROM risk_events WHERE dedup_key = $1",
                        dedup_key,
                    )
                    if existing:
                        event_id = existing["event_id"]
                
                upgrade_marker = await _execute_step(
                    "Upgrade record insert",
                    lambda: conn.fetchval(
                        """
                        INSERT INTO risk_upgrades (upgrade_key, scope, level, reason, dedup_key, recorded_at)
                        VALUES ($1, $2, $3, $4, $5, NOW())
                        ON CONFLICT (upgrade_key) DO NOTHING
                        RETURNING 1
                        """,
                        upgrade_key, scope, upgrade_level, reason, dedup_key,
                    ),
                    upgrade_key=upgrade_key,
                    dedup_key=dedup_key,
                    scope=scope,
                    upgrade_level=upgrade_level,
                )
                is_first_upgrade = upgrade_marker is not None
                
                effect_marker = await _execute_step(
                    "Effect record insert",
                    lambda: conn.fetchval(
                        """
                        INSERT INTO risk_upgrade_effects (upgrade_key, scope, level, status, attempts, updated_at)
                        VALUES ($1, $2, $3, 'PENDING', 1, NOW())
                        ON CONFLICT (upgrade_key) DO NOTHING
                        RETURNING 1
                        """,
                        upgrade_key, scope, upgrade_level,
                    ),
                    upgrade_key=upgrade_key,
                    scope=scope,
                    upgrade_level=upgrade_level,
                )
                is_first_effect = effect_marker is not None
                
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
                data=self._decode_json_field(row["data"]),
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
            marker = await conn.fetchval(
                """
                INSERT INTO risk_upgrades (upgrade_key, scope, level, reason, dedup_key, recorded_at)
                VALUES ($1, $2, $3, $4, $5, $6)
                ON CONFLICT (upgrade_key) DO NOTHING
                RETURNING 1
                """,
                upgrade_key, scope, level, reason, dedup_key, recorded_at,
            )
            return marker is not None

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
                upgrade_marker = await conn.fetchval(
                    """
                    INSERT INTO risk_upgrades (upgrade_key, scope, level, reason, dedup_key, recorded_at)
                    VALUES ($1, $2, $3, $4, $5, NOW())
                    ON CONFLICT (upgrade_key) DO NOTHING
                    RETURNING 1
                    """,
                    upgrade_key, scope, level, reason, dedup_key,
                )
                is_first_upgrade = upgrade_marker is not None
                
                effect_marker = await conn.fetchval(
                    """
                    INSERT INTO risk_upgrade_effects (upgrade_key, scope, level, status, attempts, updated_at)
                    VALUES ($1, $2, $3, 'PENDING', 1, NOW())
                    ON CONFLICT (upgrade_key) DO NOTHING
                    RETURNING 1
                    """,
                    upgrade_key, scope, level,
                )
                is_first_effect = effect_marker is not None
                
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
        pool = _pool_cache
        _pool_cache = None
        _pool_config_hash = None
        try:
            await pool.close()
        except Exception:
            try:
                pool.terminate()
            except Exception:
                pass


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
