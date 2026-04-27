"""PG-first position repository."""
import asyncio
import json
import logging
from datetime import datetime
from decimal import Decimal
from typing import Any, Dict, List, Optional

from trader.adapters.persistence.postgres import PostgreSQLStorage, check_postgres_connection
from trader.storage.in_memory import InMemoryStorage, get_storage

logger = logging.getLogger(__name__)


class PositionRepository:
    """Persist lots, projections, and reconciliation logs."""

    def __init__(self, storage: Optional[InMemoryStorage] = None):
        self._memory_storage = storage or get_storage()
        self._postgres_storage: Optional[PostgreSQLStorage] = None
        self._use_postgres = False
        self._init_lock: Optional[asyncio.Lock] = None
        self._loop: Optional[asyncio.AbstractEventLoop] = None

    def _clear_postgres_state(self) -> None:
        self._postgres_storage = None
        self._use_postgres = False
        self._init_lock = None
        self._loop = None

    async def _reset_postgres_connection(self) -> None:
        if self._postgres_storage is not None:
            try:
                await self._postgres_storage.disconnect()
            except Exception:
                pool = getattr(self._postgres_storage, "_pool", None)
                if pool is not None:
                    pool.terminate()
        self._clear_postgres_state()

    def _terminate_postgres_connection(self) -> None:
        if self._postgres_storage is not None:
            pool = getattr(self._postgres_storage, "_pool", None)
            if pool is not None:
                pool.terminate()
            self._postgres_storage._pool = None
            self._postgres_storage._connected = False
        self._clear_postgres_state()

    async def _ensure_postgres(self) -> bool:
        current_loop = asyncio.get_running_loop()
        if self._loop is not current_loop:
            await self._reset_postgres_connection()
            self._loop = current_loop
            self._init_lock = asyncio.Lock()
        if self._use_postgres and self._postgres_storage is not None:
            return True
        if self._init_lock is None:
            self._init_lock = asyncio.Lock()
        async with self._init_lock:
            if self._use_postgres and self._postgres_storage is not None:
                return True
            ok, msg = await check_postgres_connection(timeout=2.0)
            if not ok:
                logger.debug("PostgreSQL unavailable for positions: %s", msg)
                return False
            try:
                self._postgres_storage = PostgreSQLStorage()
                await self._postgres_storage.connect()
                await self._ensure_tables()
                self._use_postgres = True
                return True
            except Exception as exc:
                logger.warning("Failed to connect PostgreSQL for positions: %s", exc)
                self._clear_postgres_state()
                return False

    async def _ensure_tables(self) -> None:
        async with self._postgres_storage._pool.acquire() as conn:
            await conn.execute(
                """
                CREATE TABLE IF NOT EXISTS position_lots (
                    lot_id TEXT PRIMARY KEY,
                    position_id TEXT NOT NULL,
                    strategy_id TEXT NOT NULL,
                    symbol TEXT NOT NULL,
                    original_qty NUMERIC(36,18) NOT NULL,
                    remaining_qty NUMERIC(36,18) NOT NULL,
                    fill_price NUMERIC(36,18) NOT NULL,
                    fee_qty NUMERIC(36,18) NOT NULL DEFAULT 0,
                    fee_asset TEXT,
                    realized_pnl NUMERIC(36,18) NOT NULL DEFAULT 0,
                    filled_at TIMESTAMPTZ NOT NULL,
                    closed_at TIMESTAMPTZ,
                    is_closed BOOLEAN NOT NULL DEFAULT FALSE,
                    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
                )
                """
            )
            await conn.execute("CREATE INDEX IF NOT EXISTS idx_lots_strategy_symbol ON position_lots(strategy_id, symbol)")
            await conn.execute("CREATE INDEX IF NOT EXISTS idx_lots_open ON position_lots(strategy_id, symbol) WHERE NOT is_closed")
            await conn.execute(
                """
                CREATE TABLE IF NOT EXISTS strategy_positions_proj (
                    aggregate_id TEXT PRIMARY KEY,
                    state JSONB NOT NULL DEFAULT '{}',
                    version INT NOT NULL DEFAULT 1,
                    last_event_seq INT NOT NULL DEFAULT 0,
                    updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
                )
                """
            )
            await conn.execute("CREATE INDEX IF NOT EXISTS idx_sp_strategy ON strategy_positions_proj ((state->>'strategy_id'))")
            await conn.execute(
                """
                CREATE TABLE IF NOT EXISTS reconciliation_log (
                    id BIGSERIAL PRIMARY KEY,
                    symbol TEXT NOT NULL,
                    broker_qty NUMERIC(36,18) NOT NULL,
                    oms_total_qty NUMERIC(36,18) NOT NULL,
                    historical_qty NUMERIC(36,18) NOT NULL DEFAULT 0,
                    difference NUMERIC(36,18) NOT NULL,
                    tolerance NUMERIC(36,18) NOT NULL,
                    status TEXT NOT NULL,
                    resolution TEXT,
                    details JSONB DEFAULT '{}',
                    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
                )
                """
            )
            await conn.execute("CREATE INDEX IF NOT EXISTS idx_recon_status_created ON reconciliation_log(status, created_at DESC)")

    async def save_lot(self, lot_data: Dict[str, Any]) -> bool:
        if await self._ensure_postgres():
            async with self._postgres_storage._pool.acquire() as conn:
                await conn.execute(
                    """
                    INSERT INTO position_lots
                    (lot_id, position_id, strategy_id, symbol, original_qty, remaining_qty,
                     fill_price, fee_qty, fee_asset, realized_pnl, filled_at, closed_at, is_closed)
                    VALUES ($1,$2,$3,$4,$5,$6,$7,$8,$9,$10,$11,$12,$13)
                    ON CONFLICT (lot_id) DO UPDATE SET
                        remaining_qty = EXCLUDED.remaining_qty,
                        realized_pnl = EXCLUDED.realized_pnl,
                        is_closed = EXCLUDED.is_closed,
                        closed_at = EXCLUDED.closed_at
                    """,
                    lot_data["lot_id"],
                    lot_data["position_id"],
                    lot_data["strategy_id"],
                    lot_data["symbol"],
                    str(lot_data["original_qty"]),
                    str(lot_data["remaining_qty"]),
                    str(lot_data["fill_price"]),
                    str(lot_data.get("fee_qty", "0")),
                    lot_data.get("fee_asset"),
                    str(lot_data.get("realized_pnl", "0")),
                    lot_data["filled_at"],
                    lot_data.get("closed_at"),
                    lot_data.get("is_closed", False),
                )
            return True
        return False

    async def update_lot_on_reduce(
        self,
        lot_id: str,
        remaining_qty: Decimal,
        realized_pnl: Decimal,
        is_closed: bool = False,
        closed_at: Optional[datetime] = None,
    ) -> bool:
        if await self._ensure_postgres():
            async with self._postgres_storage._pool.acquire() as conn:
                await conn.execute(
                    """
                    UPDATE position_lots
                    SET remaining_qty=$2, realized_pnl=$3, is_closed=$4, closed_at=$5
                    WHERE lot_id=$1
                    """,
                    lot_id,
                    str(remaining_qty),
                    str(realized_pnl),
                    is_closed,
                    closed_at,
                )
            return True
        return False

    async def list_lots(self, strategy_id: str, symbol: str, open_only: bool = True) -> List[Dict[str, Any]]:
        if not await self._ensure_postgres():
            return []
        query = """
            SELECT lot_id, position_id, strategy_id, symbol, original_qty, remaining_qty,
                   fill_price, fee_qty, fee_asset, realized_pnl, filled_at, closed_at, is_closed
            FROM position_lots
            WHERE strategy_id=$1 AND symbol=$2
        """
        if open_only:
            query += " AND NOT is_closed"
        query += " ORDER BY filled_at ASC"
        async with self._postgres_storage._pool.acquire() as conn:
            rows = await conn.fetch(query, strategy_id, symbol)
        return [dict(row) for row in rows]

    async def save_position_projection(
        self,
        aggregate_id: str,
        state: Dict[str, Any],
        version: int = 1,
        last_event_seq: int = 0,
    ) -> bool:
        if await self._ensure_postgres():
            async with self._postgres_storage._pool.acquire() as conn:
                await conn.execute(
                    """
                    INSERT INTO strategy_positions_proj
                    (aggregate_id, state, version, last_event_seq, updated_at)
                    VALUES ($1, $2::jsonb, $3, $4, NOW())
                    ON CONFLICT (aggregate_id) DO UPDATE SET
                        state=EXCLUDED.state,
                        version=EXCLUDED.version,
                        last_event_seq=EXCLUDED.last_event_seq,
                        updated_at=NOW()
                    """,
                    aggregate_id,
                    json.dumps(state, default=str),
                    version,
                    last_event_seq,
                )
            return True
        return False

    async def get_position_projection(self, aggregate_id: str) -> Optional[Dict[str, Any]]:
        if not await self._ensure_postgres():
            return None
        async with self._postgres_storage._pool.acquire() as conn:
            row = await conn.fetchrow(
                "SELECT aggregate_id, state, version, last_event_seq, updated_at FROM strategy_positions_proj WHERE aggregate_id=$1",
                aggregate_id,
            )
        if not row:
            return None
        state = row["state"]
        if isinstance(state, str):
            state = json.loads(state)
        return {
            "aggregate_id": row["aggregate_id"],
            "state": state or {},
            "version": row["version"],
            "last_event_seq": row["last_event_seq"],
            "updated_at": row["updated_at"],
        }

    async def list_position_projections(self, strategy_id: Optional[str] = None) -> List[Dict[str, Any]]:
        if not await self._ensure_postgres():
            return []
        query = "SELECT aggregate_id, state, version, last_event_seq, updated_at FROM strategy_positions_proj WHERE 1=1"
        params: list[Any] = []
        if strategy_id:
            query += " AND state->>'strategy_id' = $1"
            params.append(strategy_id)
        query += " ORDER BY updated_at DESC"
        async with self._postgres_storage._pool.acquire() as conn:
            rows = await conn.fetch(query, *params)
        results = []
        for row in rows:
            state = row["state"]
            if isinstance(state, str):
                state = json.loads(state)
            results.append({**dict(row), "state": state or {}})
        return results

    async def save_reconciliation(
        self,
        symbol: str,
        broker_qty: Decimal,
        oms_total_qty: Decimal,
        difference: Decimal,
        tolerance: Decimal,
        status: str,
        resolution: Optional[str] = None,
        historical_qty: Decimal = Decimal("0"),
        details: Optional[Dict[str, Any]] = None,
    ) -> bool:
        if await self._ensure_postgres():
            async with self._postgres_storage._pool.acquire() as conn:
                await conn.execute(
                    """
                    INSERT INTO reconciliation_log
                    (symbol, broker_qty, oms_total_qty, historical_qty, difference, tolerance, status, resolution, details)
                    VALUES ($1,$2,$3,$4,$5,$6,$7,$8,$9::jsonb)
                    """,
                    symbol,
                    str(broker_qty),
                    str(oms_total_qty),
                    str(historical_qty),
                    str(difference),
                    str(tolerance),
                    status,
                    resolution,
                    json.dumps(details or {}, default=str),
                )
            return True
        return False

    async def list_reconciliations(
        self,
        symbol: Optional[str] = None,
        status: Optional[str] = None,
        limit: int = 100,
    ) -> List[Dict[str, Any]]:
        if not await self._ensure_postgres():
            return []
        query = """
            SELECT id, symbol, broker_qty, oms_total_qty, historical_qty, difference,
                   tolerance, status, resolution, details, created_at
            FROM reconciliation_log
            WHERE 1=1
        """
        params: list[Any] = []
        if symbol:
            params.append(symbol)
            query += f" AND symbol = ${len(params)}"
        if status:
            params.append(status)
            query += f" AND status = ${len(params)}"
        query += f" ORDER BY created_at DESC LIMIT ${len(params) + 1}"
        params.append(limit)
        async with self._postgres_storage._pool.acquire() as conn:
            rows = await conn.fetch(query, *params)
        results: List[Dict[str, Any]] = []
        for row in rows:
            item = dict(row)
            details = item.get("details")
            if isinstance(details, str):
                item["details"] = json.loads(details)
            elif details is None:
                item["details"] = {}
            results.append(item)
        return results


_repository_instance: Optional[PositionRepository] = None


def get_position_repository() -> PositionRepository:
    global _repository_instance
    if _repository_instance is None:
        _repository_instance = PositionRepository()
    return _repository_instance


def reset_position_repository() -> None:
    global _repository_instance
    if _repository_instance is None:
        return
    repo = _repository_instance
    if repo._postgres_storage is not None:
        try:
            asyncio.get_running_loop()
            repo._terminate_postgres_connection()
        except RuntimeError:
            try:
                asyncio.run(repo._reset_postgres_connection())
            except RuntimeError:
                repo._terminate_postgres_connection()
    _repository_instance = None
