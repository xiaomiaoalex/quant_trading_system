"""Tests for ExecutionBudgetService."""
from __future__ import annotations

import time
from decimal import Decimal

import pytest

from trader.services.account_state import AccountStateService
from trader.services.execution_budget import (
    ACCEPTED,
    EXPIRED,
    PENDING_SUBMIT,
    TERMINAL,
    BalanceReservation,
    ExecutionBudgetService,
    _parse_symbol,
    _resolve_asset,
)


def _make_account(
    balances: list[tuple[str, str, str]],
    account_id: str = "acct1",
    venue: str = "binance",
) -> AccountStateService:
    """Helper: create AccountStateService with given balances."""
    svc = AccountStateService()
    svc.apply_rest_snapshot(
        account_id,
        venue,
        [{"asset": a, "free": f, "locked": l} for a, f, l in balances],
        ts_ms=1000,
    )
    return svc


def _make_budget(
    account_state: AccountStateService | None = None,
    default_ttl_ms: int = 30_000,
) -> ExecutionBudgetService:
    if account_state is None:
        account_state = _make_account([("USDT", "10000", "0"), ("BTC", "1", "0")])
    return ExecutionBudgetService(account_state, default_ttl_ms=default_ttl_ms)


# ------------------------------------------------------------------
# Symbol parsing
# ------------------------------------------------------------------


class TestSymbolParsing:
    def test_parse_usdt_pair(self) -> None:
        base, quote = _parse_symbol("BTCUSDT")
        assert base == "BTC"
        assert quote == "USDT"

    def test_parse_fdusd_pair(self) -> None:
        base, quote = _parse_symbol("ETHFDUSD")
        assert base == "ETH"
        assert quote == "FDUSD"

    def test_parse_btc_pair(self) -> None:
        base, quote = _parse_symbol("ETHBTC")
        assert base == "ETH"
        assert quote == "BTC"

    def test_buy_resolves_to_quote(self) -> None:
        assert _resolve_asset("BTCUSDT", "BUY") == "USDT"
        assert _resolve_asset("ETHBTC", "BUY") == "BTC"

    def test_sell_resolves_to_base(self) -> None:
        assert _resolve_asset("BTCUSDT", "SELL") == "BTC"
        assert _resolve_asset("ETHBTC", "SELL") == "ETH"

    def test_parse_symbol_equal_to_quote_raises(self) -> None:
        with pytest.raises(ValueError, match="Cannot parse symbol"):
            _parse_symbol("USDT")

    def test_parse_unknown_symbol_raises(self) -> None:
        with pytest.raises(ValueError, match="Cannot parse symbol"):
            _parse_symbol("UNKNOWNPAIR")

    def test_resolve_invalid_side_raises(self) -> None:
        with pytest.raises(ValueError, match="Unknown side"):
            _resolve_asset("BTCUSDT", "HOLD")


# ------------------------------------------------------------------
# reserve_order
# ------------------------------------------------------------------


class TestReserveOrder:
    def test_reserve_order_approved(self) -> None:
        """余额充足时 reservation 通过。"""
        svc = _make_budget()
        approved, reason = svc.reserve_order(
            account_id="acct1",
            venue="binance",
            cl_ord_id="ord1",
            symbol="BTCUSDT",
            side="BUY",
            quantity=Decimal("0.1"),
            reference_price=Decimal("50000"),
        )
        assert approved is True
        assert reason == ""

        res = svc.get_reservation("ord1")
        assert res is not None
        assert res.status == PENDING_SUBMIT
        assert res.asset == "USDT"
        assert res.amount == Decimal("5000")  # 0.1 * 50000

    def test_reserve_order_insufficient_balance(self) -> None:
        """余额不足时拒绝。"""
        # USDT 只有 1000，要买 1 BTC @ 50000 = 50000 USDT
        account_state = _make_account([("USDT", "1000", "0")])
        svc = _make_budget(account_state)

        approved, reason = svc.reserve_order(
            account_id="acct1",
            venue="binance",
            cl_ord_id="ord1",
            symbol="BTCUSDT",
            side="BUY",
            quantity=Decimal("1"),
            reference_price=Decimal("50000"),
        )
        assert approved is False
        assert "INSUFFICIENT_BALANCE" in reason
        assert svc.get_reservation("ord1") is None

    def test_reserve_order_accounts_for_existing_reservations(self) -> None:
        """已有占用金额被扣除。"""
        # USDT 20000，先占用 18000，再申请 5000 → 被拒
        account_state = _make_account([("USDT", "20000", "0")])
        svc = _make_budget(account_state)

        # 第一笔占用 18000 USDT
        ok1, _ = svc.reserve_order(
            account_id="acct1",
            venue="binance",
            cl_ord_id="ord1",
            symbol="BTCUSDT",
            side="BUY",
            quantity=Decimal("0.36"),
            reference_price=Decimal("50000"),
        )
        assert ok1 is True

        # 第二笔需要 5000 USDT，但只剩 2000
        ok2, reason2 = svc.reserve_order(
            account_id="acct1",
            venue="binance",
            cl_ord_id="ord2",
            symbol="BTCUSDT",
            side="BUY",
            quantity=Decimal("0.1"),
            reference_price=Decimal("50000"),
        )
        assert ok2 is False
        assert "INSUFFICIENT_BALANCE" in reason2

    def test_duplicate_reservation_rejected(self) -> None:
        """同一 cl_ord_id 重复 reservation 被拒绝。"""
        svc = _make_budget()

        ok1, _ = svc.reserve_order(
            account_id="acct1",
            venue="binance",
            cl_ord_id="ord1",
            symbol="BTCUSDT",
            side="BUY",
            quantity=Decimal("0.01"),
            reference_price=Decimal("50000"),
        )
        assert ok1 is True

        ok2, reason2 = svc.reserve_order(
            account_id="acct1",
            venue="binance",
            cl_ord_id="ord1",  # same cl_ord_id
            symbol="BTCUSDT",
            side="BUY",
            quantity=Decimal("0.01"),
            reference_price=Decimal("50000"),
        )
        assert ok2 is False
        assert "DUPLICATE_RESERVATION" in reason2


# ------------------------------------------------------------------
# accept_reservation
# ------------------------------------------------------------------


class TestAcceptReservation:
    def test_accept_reservation_changes_status(self) -> None:
        """ACCEPTED 状态转换。"""
        svc = _make_budget()
        svc.reserve_order(
            account_id="acct1",
            venue="binance",
            cl_ord_id="ord1",
            symbol="BTCUSDT",
            side="BUY",
            quantity=Decimal("0.1"),
            reference_price=Decimal("50000"),
        )
        svc.accept_reservation("ord1")
        res = svc.get_reservation("ord1")
        assert res is not None
        assert res.status == ACCEPTED

    def test_accept_nonexistent_raises(self) -> None:
        svc = _make_budget()
        try:
            svc.accept_reservation("nope")
            assert False, "Should have raised KeyError"
        except KeyError:
            pass

    def test_accept_terminal_raises(self) -> None:
        svc = _make_budget()
        svc.reserve_order(
            account_id="acct1",
            venue="binance",
            cl_ord_id="ord1",
            symbol="BTCUSDT",
            side="BUY",
            quantity=Decimal("0.1"),
            reference_price=Decimal("50000"),
        )
        svc.release_reservation("ord1", "cancelled")
        try:
            svc.accept_reservation("ord1")
            assert False, "Should have raised ValueError"
        except ValueError:
            pass


# ------------------------------------------------------------------
# release_reservation
# ------------------------------------------------------------------


class TestReleaseReservation:
    def test_release_reservation_removes(self) -> None:
        """release 后占用金额释放。"""
        account_state = _make_account([("USDT", "10000", "0")])
        svc = _make_budget(account_state)

        svc.reserve_order(
            account_id="acct1",
            venue="binance",
            cl_ord_id="ord1",
            symbol="BTCUSDT",
            side="BUY",
            quantity=Decimal("0.1"),
            reference_price=Decimal("50000"),
        )
        assert svc.get_reserved("acct1", "binance", "USDT") == Decimal("5000")

        svc.release_reservation("ord1", "cancelled by broker")
        assert svc.get_reserved("acct1", "binance", "USDT") == Decimal("0")

        res = svc.get_reservation("ord1")
        assert res is not None
        assert res.status == TERMINAL

    def test_release_nonexistent_raises(self) -> None:
        svc = _make_budget()
        try:
            svc.release_reservation("nope", "reason")
            assert False, "Should have raised KeyError"
        except KeyError:
            pass

    def test_release_already_terminal_raises(self) -> None:
        svc = _make_budget()
        svc.reserve_order(
            account_id="acct1",
            venue="binance",
            cl_ord_id="ord1",
            symbol="BTCUSDT",
            side="BUY",
            quantity=Decimal("0.1"),
            reference_price=Decimal("50000"),
        )
        svc.release_reservation("ord1", "first")
        try:
            svc.release_reservation("ord1", "second")
            assert False, "Should have raised ValueError"
        except ValueError:
            pass


# ------------------------------------------------------------------
# expire_stale_reservations
# ------------------------------------------------------------------


class TestExpireStaleReservations:
    def test_expire_stale_reservations(self) -> None:
        """过期 reservation 清理。"""
        svc = _make_budget(default_ttl_ms=5000)
        svc.reserve_order(
            account_id="acct1",
            venue="binance",
            cl_ord_id="ord1",
            symbol="BTCUSDT",
            side="BUY",
            quantity=Decimal("0.1"),
            reference_price=Decimal("50000"),
        )

        res = svc.get_reservation("ord1")
        assert res is not None

        # 刚创建，未过期
        count = svc.expire_stale_reservations(now_ms=res.created_at_ms + 1000)
        assert count == 0
        assert res.status == PENDING_SUBMIT

        # 已过期
        count = svc.expire_stale_reservations(now_ms=res.expires_at_ms + 1)
        assert count == 1
        assert res.status == EXPIRED

    def test_expire_does_not_affect_terminal(self) -> None:
        svc = _make_budget(default_ttl_ms=1000)
        svc.reserve_order(
            account_id="acct1",
            venue="binance",
            cl_ord_id="ord1",
            symbol="BTCUSDT",
            side="BUY",
            quantity=Decimal("0.1"),
            reference_price=Decimal("50000"),
        )
        res = svc.get_reservation("ord1")
        assert res is not None
        svc.release_reservation("ord1", "filled")

        count = svc.expire_stale_reservations(now_ms=res.expires_at_ms + 1000)
        assert count == 0
        assert res.status == TERMINAL


# ------------------------------------------------------------------
# get_reserved
# ------------------------------------------------------------------


class TestGetReserved:
    def test_get_reserved_sums_pending_and_accepted(self) -> None:
        """get_reserved 统计 PENDING+ACCEPTED。"""
        account_state = _make_account([("USDT", "100000", "0")])
        svc = _make_budget(account_state)

        # ord1: PENDING_SUBMIT, 5000 USDT
        svc.reserve_order(
            account_id="acct1",
            venue="binance",
            cl_ord_id="ord1",
            symbol="BTCUSDT",
            side="BUY",
            quantity=Decimal("0.1"),
            reference_price=Decimal("50000"),
        )
        # ord2: ACCEPTED, 3000 USDT
        svc.reserve_order(
            account_id="acct1",
            venue="binance",
            cl_ord_id="ord2",
            symbol="BTCUSDT",
            side="BUY",
            quantity=Decimal("0.06"),
            reference_price=Decimal("50000"),
        )
        svc.accept_reservation("ord2")

        # ord3: TERMINAL, 不计入
        svc.reserve_order(
            account_id="acct1",
            venue="binance",
            cl_ord_id="ord3",
            symbol="BTCUSDT",
            side="BUY",
            quantity=Decimal("0.02"),
            reference_price=Decimal("50000"),
        )
        svc.release_reservation("ord3", "cancelled")

        assert svc.get_reserved("acct1", "binance", "USDT") == Decimal("8000")  # 5000 + 3000

    def test_get_reserved_different_assets_separated(self) -> None:
        """不同 asset 的占用分开统计。"""
        account_state = _make_account([("USDT", "10000", "0"), ("BTC", "2", "0")])
        svc = _make_budget(account_state)

        svc.reserve_order(
            account_id="acct1",
            venue="binance",
            cl_ord_id="ord1",
            symbol="BTCUSDT",
            side="BUY",
            quantity=Decimal("0.1"),
            reference_price=Decimal("50000"),
        )
        svc.reserve_order(
            account_id="acct1",
            venue="binance",
            cl_ord_id="ord2",
            symbol="BTCUSDT",
            side="SELL",
            quantity=Decimal("0.5"),
            reference_price=Decimal("50000"),
        )

        assert svc.get_reserved("acct1", "binance", "USDT") == Decimal("5000")
        assert svc.get_reserved("acct1", "binance", "BTC") == Decimal("0.5")


# ------------------------------------------------------------------
# BUY vs SELL asset resolution
# ------------------------------------------------------------------


class TestBuyVsSellAssetResolution:
    def test_buy_vs_sell_asset_resolution(self) -> None:
        """BUY 用 quote asset，SELL 用 base asset。"""
        account_state = _make_account([("USDT", "10000", "0"), ("BTC", "1", "0")])
        svc = _make_budget(account_state)

        # BUY BTCUSDT → 扣 USDT
        ok_buy, _ = svc.reserve_order(
            account_id="acct1",
            venue="binance",
            cl_ord_id="buy1",
            symbol="BTCUSDT",
            side="BUY",
            quantity=Decimal("0.1"),
            reference_price=Decimal("50000"),
        )
        assert ok_buy is True
        res_buy = svc.get_reservation("buy1")
        assert res_buy is not None
        assert res_buy.asset == "USDT"
        assert res_buy.amount == Decimal("5000")  # 0.1 * 50000

        # SELL BTCUSDT → 扣 BTC
        ok_sell, _ = svc.reserve_order(
            account_id="acct1",
            venue="binance",
            cl_ord_id="sell1",
            symbol="BTCUSDT",
            side="SELL",
            quantity=Decimal("0.5"),
            reference_price=Decimal("50000"),
        )
        assert ok_sell is True
        res_sell = svc.get_reservation("sell1")
        assert res_sell is not None
        assert res_sell.asset == "BTC"
        assert res_sell.amount == Decimal("0.5")  # SELL: qty only
