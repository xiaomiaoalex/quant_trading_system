"""PostgreSQL pool helpers for the trading and research runtime processes."""

from __future__ import annotations

import os

import asyncpg


def connection_string() -> str:
    explicit = os.getenv("POSTGRES_CONNECTION_STRING")
    if explicit:
        return explicit
    host = os.getenv("POSTGRES_HOST", "127.0.0.1")
    port = os.getenv("POSTGRES_PORT", "5432")
    database = os.getenv("POSTGRES_DB", "trading")
    user = os.getenv("POSTGRES_USER", "trader")
    password = os.getenv("POSTGRES_PASSWORD", "trader_pwd")
    return f"postgresql://{user}:{password}@{host}:{port}/{database}"


async def create_pool(min_size: int = 1, max_size: int = 10) -> asyncpg.Pool:
    return await asyncpg.create_pool(
        connection_string(), min_size=min_size, max_size=max_size, command_timeout=30
    )
