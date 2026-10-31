"""Transactional, checksummed schema migrations scoped to the current schema."""

import hashlib
from pathlib import Path

MIGRATIONS = ((2, "001_trading.sql"), (3, "003_query_projections.sql"))


async def initialize_trading_schema(connection) -> None:
    async with connection.transaction():
        await connection.execute("SELECT pg_advisory_xact_lock(hashtext(current_schema()), 73103)")
        await connection.execute(
            """CREATE TABLE IF NOT EXISTS trading_schema_migrations (
                version integer PRIMARY KEY, checksum text NOT NULL,
                applied_at timestamptz NOT NULL DEFAULT clock_timestamp()
            )"""
        )
        newest = await connection.fetchval("SELECT max(version) FROM trading_schema_migrations")
        if newest is not None and newest > MIGRATIONS[-1][0]:
            raise RuntimeError(f"Trading schema is newer than this runtime: {newest}")
        for version, filename in MIGRATIONS:
            script = Path(__file__).with_name(filename).read_text(encoding="utf-8")
            checksum = hashlib.sha256(script.encode()).hexdigest()
            installed = await connection.fetchval(
                "SELECT checksum FROM trading_schema_migrations WHERE version=$1", version
            )
            if installed is not None:
                if installed != checksum:
                    raise RuntimeError(f"Trading schema migration checksum mismatch: {version}")
                continue
            await connection.execute(script)
            await connection.execute(
                "INSERT INTO trading_schema_migrations(version,checksum) VALUES($1,$2)",
                version,
                checksum,
            )
