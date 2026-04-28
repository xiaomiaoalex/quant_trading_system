"""
AccountStateRepository - 账户余额 PG 持久化
============================================

遵循 ExecutionRepository 模式：lazy PG init，best-effort 持久化。
"""
from __future__ import annotations

import asyncio
import json
import logging
from typing import Optional

from trader.adapters.persistence.postgres import PostgreSQLStorage, check_postgres_connection

logger = logging.getLogger(__name__)


class AccountStateRepository:
    """持久化账户余额，支持 PG best-effort fallback。"""

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
                logger.debug("[AccountRepo] PostgreSQL unavailable: %s", msg)
                return False

            try:
                self._postgres_storage = PostgreSQLStorage()
                await self._postgres_storage.connect()
                self._use_postgres = True
                return True
            except Exception as exc:
                logger.warning("[AccountRepo] PG connect failed: %s", exc)
                self._postgres_storage = None
                self._use_postgres = False
                return False

    async def _ensure_tables(self) -> None:
        async with self._postgres_storage.acquire() as conn:
            await conn.execute("""
                CREATE TABLE IF NOT EXISTS account_balances (
                    account_id TEXT NOT NULL,
                    venue TEXT NOT NULL,
                    asset TEXT NOT NULL,
                    free TEXT NOT NULL,
                    locked TEXT NOT NULL,
                    updated_at_ms BIGINT NOT NULL,
                    source TEXT NOT NULL,
                    persisted_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
                    PRIMARY KEY (account_id, venue, asset)
                )
            """)
            await conn.execute("""
                CREATE INDEX IF NOT EXISTS idx_account_balances_account_venue
                ON account_balances(account_id, venue)
            """)

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    async def save_balances(
        self,
        account_id: str,
        venue: str,
        balances: list[dict],
        ts_ms: int,
        source: str = "rest_snapshot",
    ) -> bool:
        """全量保存账户余额（DELETE + batch INSERT）。

        Returns True if persisted to PG, False if skipped (PG unavailable).
        """
        if not await self._ensure_postgres():
            return False

        try:
            await self._ensure_tables()

            async with self._postgres_storage.acquire() as conn:
                # 删除旧数据（全量覆盖语义）
                await conn.execute(
                    "DELETE FROM account_balances WHERE account_id = $1 AND venue = $2",
                    account_id, venue,
                )
                # 批量插入新数据
                rows = [
                    {
                        "account_id": account_id,
                        "venue": venue,
                        "asset": str(b["asset"]),
                        "free": str(b.get("free", "0")),
                        "locked": str(b.get("locked", "0")),
                        "updated_at_ms": ts_ms,
                        "source": source,
                    }
                    for b in balances
                ]
                if rows:
                    await conn.copy_records_to_table(
                        "account_balances",
                        records=[tuple(r.values()) for r in rows],
                        columns=list(rows[0].keys()),
                    )
            return True
        except Exception as exc:
            logger.warning("[AccountRepo] save_balances failed: %s", exc)
            return False

    async def load_balances(
        self,
        account_id: str,
        venue: str,
    ) -> Optional[list[dict]]:
        """从 PG 加载余额。Returns None if PG unavailable or no data."""
        if not await self._ensure_postgres():
            return None

        try:
            async with self._postgres_storage.acquire() as conn:
                rows = await conn.fetch(
                    """
                    SELECT asset, free, locked, updated_at_ms, source
                    FROM account_balances
                    WHERE account_id = $1 AND venue = $2
                    ORDER BY asset
                    """,
                    account_id, venue,
                )
            if not rows:
                return None
            return [dict(r) for r in rows]
        except Exception as exc:
            logger.warning("[AccountRepo] load_balances failed: %s", exc)
            return None
