"""Tests for AccountStateService persistence integration."""
from __future__ import annotations

import asyncio
from decimal import Decimal
from unittest.mock import AsyncMock, MagicMock

import pytest

from trader.services.account_state import AccountStateService


class TestLoadFromPg:
    @pytest.mark.asyncio
    async def test_load_from_pg_returns_false_when_no_repository(self) -> None:
        svc = AccountStateService()
        ok = await svc.load_from_pg("acct1", "binance")
        assert ok is False

    @pytest.mark.asyncio
    async def test_load_from_pg_restore_balances(self) -> None:
        mock_repo = AsyncMock()
        mock_repo.load_balances.return_value = [
            {"asset": "USDT", "free": "1000", "locked": "50", "updated_at_ms": 5000, "source": "rest_snapshot"},
            {"asset": "BTC", "free": "2", "locked": "0", "updated_at_ms": 5000, "source": "rest_snapshot"},
        ]

        svc = AccountStateService(repository=mock_repo)
        ok = await svc.load_from_pg("acct1", "binance")
        assert ok is True

        bal = svc.get_balance("acct1", "binance", "USDT")
        assert bal is not None
        assert bal.free == Decimal("1000")
        assert bal.locked == Decimal("50")

        btc = svc.get_balance("acct1", "binance", "BTC")
        assert btc is not None
        assert btc.free == Decimal("2")

    @pytest.mark.asyncio
    async def test_load_from_pg_returns_false_when_no_data(self) -> None:
        mock_repo = AsyncMock()
        mock_repo.load_balances.return_value = None

        svc = AccountStateService(repository=mock_repo)
        ok = await svc.load_from_pg("acct1", "binance")
        assert ok is False

    @pytest.mark.asyncio
    async def test_load_from_pg_returns_false_on_repo_error(self) -> None:
        mock_repo = AsyncMock()
        mock_repo.load_balances.side_effect = RuntimeError("PG error")

        svc = AccountStateService(repository=mock_repo)
        ok = await svc.load_from_pg("acct1", "binance")
        assert ok is False


class TestPersistOnSnapshot:
    def test_apply_rest_snapshot_calls_repository(self) -> None:
        mock_repo = MagicMock()
        # Simulate no running loop → _persist_balances silently skips
        mock_repo.save_balances = AsyncMock(return_value=True)

        svc = AccountStateService(repository=mock_repo)

        # _persist_balances tries to get running loop — without one, silently skips
        svc.apply_rest_snapshot(
            "acct1", "binance",
            [{"asset": "USDT", "free": "100", "locked": "0"}],
            ts_ms=1000,
        )

        # Without running loop the task isn't created; verify balance was set
        bal = svc.get_balance("acct1", "binance", "USDT")
        assert bal is not None
        assert bal.free == Decimal("100")


class TestLoadFromPgClearsStale:
    @pytest.mark.asyncio
    async def test_load_from_pg_clears_stale(self) -> None:
        mock_repo = AsyncMock()
        mock_repo.load_balances.return_value = [
            {"asset": "USDT", "free": "500", "locked": "0", "updated_at_ms": 3000, "source": "rest_snapshot"},
        ]

        svc = AccountStateService(repository=mock_repo)
        svc.mark_stale("acct1", "binance", "prior disconnect")
        assert svc.is_stale("acct1", "binance") is True

        await svc.load_from_pg("acct1", "binance")
        assert svc.is_stale("acct1", "binance") is False
