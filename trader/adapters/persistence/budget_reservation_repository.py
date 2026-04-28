"""
BudgetReservationRepository - 预算 reservation PG 持久化
==========================================================

遵循 ExecutionRepository 模式：lazy PG init，best-effort 持久化。
"""
from __future__ import annotations

import asyncio
import logging
from typing import Optional

from trader.adapters.persistence.postgres import PostgreSQLStorage, check_postgres_connection
from trader.services.execution_budget import BalanceReservation

logger = logging.getLogger(__name__)

# 只持久化活跃状态
_ACTIVE_STATUSES = ("PENDING_SUBMIT", "ACCEPTED")


class BudgetReservationRepository:
    """持久化活跃 budget reservations，支持 PG best-effort fallback。"""

    def __init__(self) -> None:
        self._postgres_storage: Optional[PostgreSQLStorage] = None
        self._use_postgres = False
        self._init_lock: Optional[asyncio.Lock] = None
        self._loop: Optional[asyncio.AbstractEventLoop] = None

    # ------------------------------------------------------------------
    # PG lifecycle
    # ------------------------------------------------------------------

    async def _ensure_postgres(self) -> bool:
        current_loop = asyncio.get_running_loop()
        if self._loop is not current_loop:
            self._postgres_storage = None
            self._use_postgres = False
            self._init_lock = None
            self._loop = current_loop

        if self._use_postgres and self._postgres_storage is not None:
            return True

        if self._init_lock is None:
            self._init_lock = asyncio.Lock()

        async with self._init_lock:
            if self._use_postgres and self._postgres_storage is not None:
                return True

            ok, msg = await check_postgres_connection(timeout=2.0)
            if not ok:
                logger.debug("[BudgetRepo] PostgreSQL unavailable: %s", msg)
                return False

            try:
                self._postgres_storage = PostgreSQLStorage()
                await self._postgres_storage.connect()
                self._use_postgres = True
                return True
            except Exception as exc:
                logger.warning("[BudgetRepo] PG connect failed: %s", exc)
                self._postgres_storage = None
                self._use_postgres = False
                return False

    async def _ensure_tables(self) -> None:
        async with self._postgres_storage.acquire() as conn:
            await conn.execute("""
                CREATE TABLE IF NOT EXISTS budget_reservations (
                    reservation_id TEXT PRIMARY KEY,
                    account_id TEXT NOT NULL,
                    venue TEXT NOT NULL,
                    asset TEXT NOT NULL,
                    amount TEXT NOT NULL,
                    status TEXT NOT NULL,
                    created_at_ms BIGINT NOT NULL,
                    expires_at_ms BIGINT NOT NULL,
                    persisted_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
                )
            """)
            await conn.execute("""
                CREATE INDEX IF NOT EXISTS idx_budget_reservations_active
                ON budget_reservations(account_id, venue, asset)
                WHERE status IN ('PENDING_SUBMIT', 'ACCEPTED')
            """)

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    async def save_reservation(self, reservation: BalanceReservation) -> bool:
        """Upsert 单条 reservation。Returns True if persisted to PG."""
        if not await self._ensure_postgres():
            return False

        try:
            await self._ensure_tables()

            async with self._postgres_storage.acquire() as conn:
                await conn.execute(
                    """
                    INSERT INTO budget_reservations
                        (reservation_id, account_id, venue, asset, amount, status,
                         created_at_ms, expires_at_ms)
                    VALUES ($1, $2, $3, $4, $5, $6, $7, $8)
                    ON CONFLICT (reservation_id) DO UPDATE SET
                        status = EXCLUDED.status,
                        amount = EXCLUDED.amount
                    """,
                    reservation.reservation_id,
                    reservation.account_id,
                    reservation.venue,
                    reservation.asset,
                    str(reservation.amount),
                    reservation.status,
                    reservation.created_at_ms,
                    reservation.expires_at_ms,
                )
            return True
        except Exception as exc:
            logger.warning("[BudgetRepo] save_reservation failed: %s", exc)
            return False

    async def delete_reservation(self, reservation_id: str) -> bool:
        """删除单条 reservation。Returns True if deleted from PG."""
        if not await self._ensure_postgres():
            return False

        try:
            async with self._postgres_storage.acquire() as conn:
                await conn.execute(
                    "DELETE FROM budget_reservations WHERE reservation_id = $1",
                    reservation_id,
                )
            return True
        except Exception as exc:
            logger.warning("[BudgetRepo] delete_reservation failed: %s", exc)
            return False

    async def load_active(self) -> Optional[list[dict]]:
        """加载所有活跃 reservation。Returns None if PG unavailable."""
        if not await self._ensure_postgres():
            return None

        try:
            async with self._postgres_storage.acquire() as conn:
                rows = await conn.fetch(
                    """
                    SELECT reservation_id, account_id, venue, asset, amount,
                           status, created_at_ms, expires_at_ms
                    FROM budget_reservations
                    WHERE status IN ($1, $2)
                    """,
                    "PENDING_SUBMIT", "ACCEPTED",
                )
            return [dict(r) for r in rows]
        except Exception as exc:
            logger.warning("[BudgetRepo] load_active failed: %s", exc)
            return None
