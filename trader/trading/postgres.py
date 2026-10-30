"""Postgres account UoW: commands, executions, ledger and event outbox commit atomically."""

import json
from contextlib import asynccontextmanager
from pathlib import Path

from .models import AccountState, json_value
from .store import decode_state


class PostgresTradingStore:
    def __init__(self, pool):
        self.pool = pool

    async def initialize(self):
        async with self.pool.acquire() as connection:
            await connection.execute(Path(__file__).with_name("001_trading.sql").read_text())

    async def ping(self) -> bool:
        try:
            async with self.pool.acquire() as connection:
                return await connection.fetchval("SELECT 1") == 1
        except Exception:
            return False

    @asynccontextmanager
    async def transaction(self, account_id):
        async with self.pool.acquire() as conn:
            async with conn.transaction():
                await conn.execute(
                    "INSERT INTO trading_accounts(account_id,state) VALUES($1,$2::jsonb) ON CONFLICT DO NOTHING",
                    account_id,
                    json.dumps(json_value(AccountState(account_id))),
                )
                raw = await conn.fetchval(
                    "SELECT state FROM trading_accounts WHERE account_id=$1 FOR UPDATE", account_id
                )
                state = decode_state(json.loads(raw) if isinstance(raw, str) else raw)
                old_execs = set(state.executions)
                old_ledger = len(state.ledger)
                old_events = len(state.events)
                yield state
                state.version += 1
                for record in state.commands.values():
                    await conn.execute(
                        "INSERT INTO trading_commands(account_id,command_id,cl_ord_id,kind,status,payload_hash,data) VALUES($1,$2,$3,$4,$5,$6,$7::jsonb) ON CONFLICT(account_id,command_id) DO UPDATE SET status=EXCLUDED.status,data=EXCLUDED.data",
                        account_id,
                        record.command_id,
                        record.cl_ord_id,
                        record.kind,
                        record.status,
                        record.payload_hash,
                        json.dumps(json_value(record)),
                    )
                for key in set(state.executions) - old_execs:
                    execution = state.executions[key]
                    await conn.execute(
                        "INSERT INTO trading_executions(execution_key,account_id,cl_ord_id,data) VALUES($1,$2,$3,$4::jsonb)",
                        key,
                        account_id,
                        execution["cl_ord_id"],
                        json.dumps(execution),
                    )
                for entry in state.ledger[old_ledger:]:
                    await conn.execute(
                        "INSERT INTO trading_ledger(account_id,entry_id,data) VALUES($1,$2,$3::jsonb)",
                        account_id,
                        entry["entry_id"],
                        json.dumps(entry),
                    )
                pending = {item["event_id"] for item in state.outbox if not item.get("delivered")}
                for event in state.events[old_events:]:
                    payload = json.dumps(json_value(event))
                    await conn.execute(
                        "INSERT INTO trading_events(account_id,event_id,data) VALUES($1,$2,$3::jsonb) ON CONFLICT DO NOTHING",
                        account_id,
                        event["event_id"],
                        payload,
                    )
                    if event["event_id"] in pending:
                        await conn.execute(
                            "INSERT INTO trading_outbox(account_id,event_id,data) VALUES($1,$2,$3::jsonb) ON CONFLICT DO NOTHING",
                            account_id,
                            event["event_id"],
                            payload,
                        )
                await conn.execute(
                    "UPDATE trading_accounts SET version=$2,state=$3::jsonb WHERE account_id=$1",
                    account_id,
                    state.version,
                    json.dumps(json_value(state)),
                )

    async def pending_outbox(self, account_id):
        async with self.pool.acquire() as conn:
            rows = await conn.fetch(
                "SELECT event_id,data FROM trading_outbox WHERE account_id=$1 AND NOT delivered ORDER BY seq",
                account_id,
            )
        return [
            {
                "event_id": row["event_id"],
                "data": json.loads(row["data"]) if isinstance(row["data"], str) else row["data"],
            }
            for row in rows
        ]

    async def mark_outbox_delivered(self, account_id, event_ids):
        if not event_ids:
            return
        async with self.pool.acquire() as conn:
            await conn.execute(
                "UPDATE trading_outbox SET delivered=true WHERE account_id=$1 AND event_id = ANY($2::text[])",
                account_id,
                list(event_ids),
            )

    async def acquire_lease(self, scope: str, owner: str, lease_seconds: int = 30):
        async with self.pool.acquire() as conn:
            await conn.execute(
                """INSERT INTO trading_runtime_leases(scope,owner,fencing_token,expires_at)
                   VALUES($1,$2,1,clock_timestamp()+$3*interval '1 second')
                   ON CONFLICT (scope) DO NOTHING""",
                scope,
                owner,
                lease_seconds,
            )
            row = await conn.fetchrow(
                """UPDATE trading_runtime_leases SET
                     fencing_token=CASE WHEN owner=$2 THEN fencing_token ELSE fencing_token+1 END,
                     owner=$2, expires_at=clock_timestamp()+$3*interval '1 second'
                   WHERE scope=$1 AND (owner=$2 OR expires_at < clock_timestamp())
                   RETURNING fencing_token""",
                scope,
                owner,
                lease_seconds,
            )
        return None if row is None else int(row["fencing_token"])

    async def renew_lease(
        self, scope: str, owner: str, token: int, lease_seconds: int = 30
    ) -> bool:
        async with self.pool.acquire() as conn:
            row = await conn.fetchval(
                """UPDATE trading_runtime_leases SET expires_at=clock_timestamp()+$4*interval '1 second'
                   WHERE scope=$1 AND owner=$2 AND fencing_token=$3 AND expires_at >= clock_timestamp()
                   RETURNING 1""",
                scope,
                owner,
                token,
                lease_seconds,
            )
        return bool(row)

    async def release_lease(self, scope: str, owner: str, token: int) -> None:
        async with self.pool.acquire() as conn:
            await conn.execute(
                "DELETE FROM trading_runtime_leases WHERE scope=$1 AND owner=$2 AND fencing_token=$3",
                scope,
                owner,
                token,
            )

    async def read(self, account_id):
        async with self.pool.acquire() as conn:
            raw = await conn.fetchval(
                "SELECT state FROM trading_accounts WHERE account_id=$1", account_id
            )
        return (
            decode_state(json.loads(raw) if isinstance(raw, str) else raw)
            if raw is not None
            else AccountState(account_id)
        )
