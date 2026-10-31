"""Destructive schema bootstrap must not run underneath a live execution process."""

import pytest
from scripts.bootstrap_dev_schema import ensure_no_live_runtime


class FakeConnection:
    def __init__(self, table_exists: bool, rows: list[dict]):
        self.table_exists = table_exists
        self.rows = rows
        self.fetched = 0

    async def fetchval(self, sql, *args):
        return self.table_exists

    async def fetch(self, sql, *args):
        self.fetched += 1
        return self.rows


@pytest.mark.asyncio
async def test_refuses_to_drop_while_a_runtime_holds_the_lease():
    conn = FakeConnection(
        True,
        [{"scope": "execution:binance_spot", "owner": "host:1234", "expires_at": None}],
    )

    with pytest.raises(SystemExit) as excinfo:
        await ensure_no_live_runtime(conn)

    assert "host:1234" in str(excinfo.value)


@pytest.mark.asyncio
async def test_allows_the_drop_when_the_lease_table_does_not_exist():
    conn = FakeConnection(False, [])

    await ensure_no_live_runtime(conn)

    assert conn.fetched == 0


@pytest.mark.asyncio
async def test_allows_the_drop_when_no_lease_is_live():
    conn = FakeConnection(True, [])

    await ensure_no_live_runtime(conn)
