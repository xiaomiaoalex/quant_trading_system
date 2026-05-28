"""
NAV Store — PostgreSQL 持久化层（Stage 6）
==========================================
提供 nav_points 表的读写函数，独立于其他 Repository，
使用懒初始化 PostgreSQLStorage 实例。

所有函数均 best-effort：异常只记 warning，不向上抛出。
"""

from __future__ import annotations

import asyncio
import logging
import time
from typing import TYPE_CHECKING, Any, Dict, List, Optional

if TYPE_CHECKING:
    from trader.core.domain.models.nav import NAVPoint

logger = logging.getLogger(__name__)

# 模块级懒初始化 PG storage
_pg_storage: Optional[Any] = None
_pg_init_lock: Optional[asyncio.Lock] = None
_pg_available: Optional[bool] = None  # None=未知, True=可用, False=不可用
_pg_last_failure_ts: float = 0.0
_pg_notified_once: bool = False  # D1: 首次失败打 INFO，后续只打 DEBUG
_PG_RETRY_INTERVAL: float = 60.0  # 失败后 60 秒允许重试


async def _ensure_pg() -> Optional[Any]:
    """懒初始化 PostgreSQL 连接，失败后按退避间隔重试。"""
    global _pg_storage, _pg_init_lock, _pg_available, _pg_last_failure_ts, _pg_notified_once

    if _pg_available is False:
        if time.monotonic() - _pg_last_failure_ts < _PG_RETRY_INTERVAL:
            return None
        # 超过退避间隔，允许重试
        _pg_available = None

    if _pg_storage is not None and _pg_available is True:
        return _pg_storage

    if _pg_init_lock is None:
        _pg_init_lock = asyncio.Lock()

    async with _pg_init_lock:
        if _pg_available is False:
            if time.monotonic() - _pg_last_failure_ts < _PG_RETRY_INTERVAL:
                return None
            _pg_available = None

        if _pg_storage is not None and _pg_available is True:
            return _pg_storage
        try:
            from trader.adapters.persistence.postgres import PostgreSQLStorage

            storage = PostgreSQLStorage()
            await storage.connect()
            _pg_storage = storage
            _pg_available = True
            _pg_notified_once = False  # 重置，后续失败重新提示
            logger.info("NAV store: PostgreSQL connected")
            return storage
        except Exception as exc:
            _pg_available = False
            _pg_last_failure_ts = time.monotonic()
            if not _pg_notified_once:
                logger.info(
                    "NAV store: PostgreSQL not available (%s), using memory only. "
                    "Subsequent failures will be logged at DEBUG level.",
                    exc,
                )
                _pg_notified_once = True
            else:
                logger.debug(
                    "NAV store: PostgreSQL not available (%s), using memory only",
                    exc,
                )
            return None


async def append_nav_point_pg(nav: "NAVPoint") -> None:
    """将单个 NAVPoint 插入 PostgreSQL nav_points 表。失败只 warning。"""
    storage = await _ensure_pg()
    if storage is None:
        return
    try:
        async with storage.acquire() as conn:
            await conn.execute(
                """
                INSERT INTO nav_points
                    (deployment_id, strategy_id, timestamp_ms, equity,
                     cash, unrealized_pnl, realized_pnl, total_pnl)
                VALUES ($1, $2, $3, $4, $5, $6, $7, $8)
                """,
                nav.deployment_id,
                nav.strategy_id,
                nav.timestamp_ms,
                nav.equity,
                nav.cash,
                nav.unrealized_pnl,
                nav.realized_pnl,
                nav.total_pnl,
            )
    except Exception as exc:
        logger.warning("nav_points PG write failed: %s", exc)


async def get_nav_series_pg(
    deployment_id: str,
    since_ms: Optional[int] = None,
    limit: int = 500,
) -> List[Dict[str, Any]]:
    """从 PostgreSQL 查询 NAV 历史；失败时降级返回空列表。"""
    storage = await _ensure_pg()
    if storage is None:
        return []
    try:
        async with storage.acquire() as conn:
            if since_ms is not None:
                rows = await conn.fetch(
                    """
                    SELECT deployment_id, strategy_id, timestamp_ms,
                           equity, cash, unrealized_pnl, realized_pnl, total_pnl
                    FROM nav_points
                    WHERE deployment_id = $1 AND timestamp_ms >= $2
                    ORDER BY timestamp_ms ASC
                    LIMIT $3
                    """,
                    deployment_id,
                    since_ms,
                    limit,
                )
            else:
                rows = await conn.fetch(
                    """
                    SELECT deployment_id, strategy_id, timestamp_ms,
                           equity, cash, unrealized_pnl, realized_pnl, total_pnl
                    FROM nav_points
                    WHERE deployment_id = $1
                    ORDER BY timestamp_ms DESC
                    LIMIT $2
                    """,
                    deployment_id,
                    limit,
                )
                rows = list(reversed(rows))

        return [
            {
                "deployment_id": r["deployment_id"],
                "strategy_id": r["strategy_id"],
                "timestamp_ms": r["timestamp_ms"],
                "equity": float(r["equity"]),
                "cash": float(r["cash"]),
                "unrealized_pnl": float(r["unrealized_pnl"]),
                "realized_pnl": float(r["realized_pnl"]),
                "total_pnl": float(r["total_pnl"]),
            }
            for r in rows
        ]
    except Exception as exc:
        logger.warning("nav_points PG read failed: %s", exc)
        return []
