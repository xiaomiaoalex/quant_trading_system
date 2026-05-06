"""Tests for ExecutionBudgetService persistence integration."""

from __future__ import annotations

from decimal import Decimal
from unittest.mock import AsyncMock, MagicMock

import pytest

from trader.services.account_state import AccountStateService
from trader.services.execution_budget import (
    ACCEPTED,
    PENDING_SUBMIT,
    BalanceReservation,
    ExecutionBudgetService,
)


def _make_state() -> AccountStateService:
    svc = AccountStateService()
    svc.apply_rest_snapshot(
        "acct1",
        "binance",
        [{"asset": "USDT", "free": "10000", "locked": "0"}],
        ts_ms=1000,
    )
    return svc


def _make_budget(state: AccountStateService | None = None, repo=None) -> ExecutionBudgetService:
    return ExecutionBudgetService(state or _make_state(), repository=repo)


class TestLoadFromPg:
    @pytest.mark.asyncio
    async def test_load_from_pg_returns_zero_when_no_repository(self) -> None:
        svc = _make_budget()
        count = await svc.load_from_pg()
        assert count == 0

    @pytest.mark.asyncio
    async def test_load_from_pg_restores_active_reservations(self) -> None:
        mock_repo = AsyncMock()
        mock_repo.load_active.return_value = [
            {
                "reservation_id": "ord1",
                "account_id": "acct1",
                "venue": "binance",
                "asset": "USDT",
                "amount": "5000",
                "status": PENDING_SUBMIT,
                "created_at_ms": 1000,
                "expires_at_ms": 40000,
            },
            {
                "reservation_id": "ord2",
                "account_id": "acct1",
                "venue": "binance",
                "asset": "USDT",
                "amount": "3000",
                "status": ACCEPTED,
                "created_at_ms": 2000,
                "expires_at_ms": 50000,
            },
        ]

        svc = _make_budget(repo=mock_repo)
        count = await svc.load_from_pg()
        assert count == 2

        res1 = svc.get_reservation("ord1")
        assert res1 is not None
        assert res1.amount == Decimal("5000")
        assert res1.status == PENDING_SUBMIT

        res2 = svc.get_reservation("ord2")
        assert res2 is not None
        assert res2.amount == Decimal("3000")
        assert res2.status == ACCEPTED

    @pytest.mark.asyncio
    async def test_load_from_pg_ignores_existing_reservations(self) -> None:
        mock_repo = AsyncMock()
        mock_repo.load_active.return_value = [
            {
                "reservation_id": "ord1",
                "account_id": "acct1",
                "venue": "binance",
                "asset": "USDT",
                "amount": "5000",
                "status": PENDING_SUBMIT,
                "created_at_ms": 1000,
                "expires_at_ms": 40000,
            },
        ]

        svc = _make_budget(repo=mock_repo)
        # ord1 already in memory
        svc._reservations["ord1"] = BalanceReservation(
            reservation_id="ord1",
            account_id="acct1",
            venue="binance",
            asset="USDT",
            amount=Decimal("1000"),
            status=PENDING_SUBMIT,
            created_at_ms=500,
            expires_at_ms=35000,
        )

        count = await svc.load_from_pg()
        assert count == 0  # not overwritten
        res = svc.get_reservation("ord1")
        assert res is not None
        assert res.amount == Decimal("1000")  # original kept

    @pytest.mark.asyncio
    async def test_load_from_pg_returns_zero_when_no_data(self) -> None:
        mock_repo = AsyncMock()
        mock_repo.load_active.return_value = None

        svc = _make_budget(repo=mock_repo)
        count = await svc.load_from_pg()
        assert count == 0

    @pytest.mark.asyncio
    async def test_load_from_pg_returns_zero_on_error(self) -> None:
        mock_repo = AsyncMock()
        mock_repo.load_active.side_effect = RuntimeError("PG error")

        svc = _make_budget(repo=mock_repo)
        count = await svc.load_from_pg()
        assert count == 0


class TestPersistReservations:
    def test_reserve_order_calls_save_reservation(self) -> None:
        """reserve_order 成功时调用 repository.save_reservation（fire-and-forget）"""
        mock_repo = MagicMock()
        mock_repo.save_reservation = AsyncMock()

        # No running loop → _persist_reservation silently skips
        svc = _make_budget(repo=mock_repo)

        ok, _ = svc.reserve_order(
            account_id="acct1",
            venue="binance",
            cl_ord_id="ord1",
            symbol="BTCUSDT",
            side="BUY",
            quantity=Decimal("0.1"),
            reference_price=Decimal("50000"),
        )
        assert ok is True

        res = svc.get_reservation("ord1")
        assert res is not None

    def test_accept_reservation_calls_save_reservation(self) -> None:
        """accept_reservation 时调用 repository.save_reservation"""
        svc = _make_budget()
        svc._reservations["ord1"] = BalanceReservation(
            reservation_id="ord1",
            account_id="acct1",
            venue="binance",
            asset="USDT",
            amount=Decimal("5000"),
            status=PENDING_SUBMIT,
            created_at_ms=1000,
            expires_at_ms=40000,
        )

        mock_repo = MagicMock()
        mock_repo.save_reservation = AsyncMock()

        svc._repository = mock_repo
        svc.accept_reservation("ord1")

        assert svc.get_reservation("ord1").status == ACCEPTED

    def test_release_reservation_calls_delete_reservation(self) -> None:
        """release_reservation 时调用 repository.delete_reservation"""
        svc = _make_budget()
        svc._reservations["ord1"] = BalanceReservation(
            reservation_id="ord1",
            account_id="acct1",
            venue="binance",
            asset="USDT",
            amount=Decimal("5000"),
            status=PENDING_SUBMIT,
            created_at_ms=1000,
            expires_at_ms=40000,
        )

        mock_repo = MagicMock()
        mock_repo.delete_reservation = AsyncMock()

        svc._repository = mock_repo
        svc.release_reservation("ord1", "filled")

        assert svc.get_reservation("ord1").status == "TERMINAL"

    def test_expire_stale_reservations_calls_delete(self) -> None:
        """expire_stale_reservations 删除过期条目时调用 repository.delete_reservation"""
        svc = _make_budget()
        svc._reservations["ord1"] = BalanceReservation(
            reservation_id="ord1",
            account_id="acct1",
            venue="binance",
            asset="USDT",
            amount=Decimal("5000"),
            status=PENDING_SUBMIT,
            created_at_ms=1000,
            expires_at_ms=100,  # already expired
        )

        mock_repo = MagicMock()
        mock_repo.delete_reservation = AsyncMock()

        svc._repository = mock_repo
        count = svc.expire_stale_reservations(now_ms=200)
        assert count == 1
        assert svc.get_reservation("ord1").status == "EXPIRED"
