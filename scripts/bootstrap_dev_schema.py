"""Explicit development schema bootstrap.

Refuses to touch a target unless it is declared as development, and requires
an explicit --drop before deleting the development trading/research tables.
"""

from __future__ import annotations

import argparse
import asyncio
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from trader.trading.migrations import initialize_trading_schema
from trader.trading.pg import create_pool
from trader.trading.postgres import PostgresTradingStore

TRADING_SQL = ROOT / "trader/trading/001_trading.sql"
RESEARCH_SQL = ROOT / "trader/trading/research_schema.sql"

DROP_TRADING = """
DROP TABLE IF EXISTS trading_order_projection, trading_schema_migrations,
    trading_outbox, trading_events, trading_ledger,
    trading_executions, trading_commands, trading_runtime_leases, trading_accounts CASCADE;
"""
DROP_RESEARCH = "DROP SCHEMA IF EXISTS research CASCADE;"


async def bootstrap(target: str, drop: bool, rebuild_account: str | None = None) -> None:
    if target != "dev":
        raise SystemExit("Refusing to modify a non-development target; pass --target dev")
    if drop and rebuild_account:
        raise SystemExit("Cannot combine --drop and --rebuild-account")
    pool = await create_pool(min_size=1, max_size=2)
    try:
        async with pool.acquire() as conn:
            if drop:
                await conn.execute(DROP_TRADING)
                await conn.execute(DROP_RESEARCH)
                print("dropped development trading/research schema")
            await initialize_trading_schema(conn)
            await conn.execute(RESEARCH_SQL.read_text(encoding="utf-8"))
            required = (
                "trading_accounts",
                "trading_commands",
                "trading_executions",
                "trading_ledger",
                "trading_events",
                "trading_outbox",
                "trading_runtime_leases",
                "trading_order_projection",
                "trading_schema_migrations",
            )
            missing = [
                name
                for name in required
                if not await conn.fetchval("SELECT to_regclass($1) IS NOT NULL", name)
            ]
            if missing or not await conn.fetchval(
                "SELECT to_regclass('research.backtest_jobs') IS NOT NULL"
            ):
                raise SystemExit(f"schema validation failed: missing={missing}")
        if rebuild_account:
            counts = await PostgresTradingStore(pool).rebuild_query_projections(rebuild_account)
            print(f"query projections rebuilt: {counts}")
        print("development schema ready and validated")
    finally:
        await pool.close()


def main() -> None:
    parser = argparse.ArgumentParser(description="Create the versioned development schema")
    parser.add_argument("--target", required=True, choices=["dev"], help="must be dev")
    parser.add_argument("--drop", action="store_true", help="drop development tables first")
    parser.add_argument("--rebuild-account", help="rebuild only derived queries for this account")
    args = parser.parse_args()
    if args.drop and args.target != "dev":
        sys.exit("--drop requires --target dev")
    asyncio.run(bootstrap(args.target, args.drop, args.rebuild_account))


if __name__ == "__main__":
    main()
