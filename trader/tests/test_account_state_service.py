"""Tests for AccountStateService."""
from __future__ import annotations

from decimal import Decimal

from trader.services.account_state import AccountBalance, AccountStateService


def _make_snapshot_data(pairs: list[tuple[str, str, str]]) -> list[dict]:
    """Helper: [(asset, free, locked), ...] → list[dict]."""
    return [{"asset": a, "free": f, "locked": l} for a, f, l in pairs]


class TestApplyRestSnapshot:
    def test_apply_rest_snapshot_updates_balances(self) -> None:
        svc = AccountStateService()
        svc.apply_rest_snapshot(
            "acct1",
            "binance",
            _make_snapshot_data([("USDT", "1000.50", "100.00"), ("BTC", "0.5", "0")]),
            ts_ms=1000,
        )
        usdt = svc.get_balance("acct1", "binance", "USDT")
        assert usdt is not None
        assert usdt.free == Decimal("1000.50")
        assert usdt.locked == Decimal("100.00")
        assert usdt.source == "rest_snapshot"
        assert usdt.updated_at_ms == 1000

        btc = svc.get_balance("acct1", "binance", "BTC")
        assert btc is not None
        assert btc.free == Decimal("0.5")
        assert btc.locked == Decimal("0")

    def test_rest_snapshot_overwrites_stale(self) -> None:
        svc = AccountStateService()
        svc.mark_stale("acct1", "binance", "ws disconnect")
        assert svc.is_stale("acct1", "binance") is True

        svc.apply_rest_snapshot(
            "acct1", "binance", _make_snapshot_data([("USDT", "100", "0")]), ts_ms=2000
        )
        assert svc.is_stale("acct1", "binance") is False

    def test_rest_snapshot_replaces_all_assets(self) -> None:
        svc = AccountStateService()
        svc.apply_rest_snapshot(
            "acct1",
            "binance",
            _make_snapshot_data([("USDT", "100", "0"), ("BTC", "1", "0")]),
            ts_ms=1000,
        )
        # Second snapshot with only USDT — BTC should be gone
        svc.apply_rest_snapshot(
            "acct1", "binance", _make_snapshot_data([("USDT", "200", "0")]), ts_ms=2000
        )
        assert svc.get_balance("acct1", "binance", "BTC") is None
        assert svc.get_balance("acct1", "binance", "USDT").free == Decimal("200")


class TestApplyPrivateAccountPosition:
    def test_apply_private_account_position_updates_asset(self) -> None:
        svc = AccountStateService()
        event = {
            "E": 3000,
            "B": [
                {"a": "USDT", "f": "500.00", "l": "50.00"},
                {"a": "ETH", "f": "2.0", "l": "0"},
            ],
        }
        svc.apply_private_account_position("acct1", "binance", event)

        usdt = svc.get_balance("acct1", "binance", "USDT")
        assert usdt is not None
        assert usdt.free == Decimal("500.00")
        assert usdt.locked == Decimal("50.00")
        assert usdt.source == "private_stream"
        assert usdt.updated_at_ms == 3000

        eth = svc.get_balance("acct1", "binance", "ETH")
        assert eth is not None
        assert eth.free == Decimal("2.0")

    def test_private_stream_ignores_empty_asset(self) -> None:
        svc = AccountStateService()
        event = {"E": 1000, "B": [{"a": "", "f": "100", "l": "0"}]}
        svc.apply_private_account_position("acct1", "binance", event)
        # Should not crash, no balance stored
        assert svc.get_balance("acct1", "binance", "") is None


class TestApplyBalanceUpdate:
    def test_apply_balance_update_adds_delta(self) -> None:
        svc = AccountStateService()
        svc.apply_rest_snapshot(
            "acct1", "binance", _make_snapshot_data([("USDT", "1000", "0")]), ts_ms=1000
        )
        svc.apply_balance_update("acct1", "binance", {"a": "USDT", "d": "500", "E": 2000})
        bal = svc.get_balance("acct1", "binance", "USDT")
        assert bal is not None
        assert bal.free == Decimal("1500")
        assert bal.source == "balance_update"

    def test_apply_balance_update_subtracts_delta(self) -> None:
        svc = AccountStateService()
        svc.apply_rest_snapshot(
            "acct1", "binance", _make_snapshot_data([("USDT", "1000", "0")]), ts_ms=1000
        )
        svc.apply_balance_update("acct1", "binance", {"a": "USDT", "d": "-200", "E": 2000})
        bal = svc.get_balance("acct1", "binance", "USDT")
        assert bal is not None
        assert bal.free == Decimal("800")

    def test_balance_update_creates_new_asset(self) -> None:
        svc = AccountStateService()
        svc.apply_balance_update(
            "acct1", "binance", {"a": "DOGE", "d": "1000", "E": 1000}
        )
        bal = svc.get_balance("acct1", "binance", "DOGE")
        assert bal is not None
        assert bal.free == Decimal("1000")
        assert bal.locked == Decimal("0")

    def test_balance_update_preserves_locked(self) -> None:
        svc = AccountStateService()
        svc.apply_rest_snapshot(
            "acct1", "binance", _make_snapshot_data([("USDT", "1000", "200")]), ts_ms=1000
        )
        svc.apply_balance_update("acct1", "binance", {"a": "USDT", "d": "100", "E": 2000})
        bal = svc.get_balance("acct1", "binance", "USDT")
        assert bal is not None
        assert bal.locked == Decimal("200")

    def test_balance_update_ignores_empty_asset(self) -> None:
        svc = AccountStateService()
        svc.apply_balance_update("acct1", "binance", {"a": "", "d": "100", "E": 1000})
        assert svc.get_balance("acct1", "binance", "") is None


class TestGetSpendable:
    def test_get_spendable_deducts_locked(self) -> None:
        svc = AccountStateService()
        svc.apply_rest_snapshot(
            "acct1", "binance", _make_snapshot_data([("USDT", "1000", "300")]), ts_ms=1000
        )
        assert svc.get_spendable("acct1", "binance", "USDT") == Decimal("700")

    def test_get_spendable_clamps_to_zero(self) -> None:
        svc = AccountStateService()
        svc.apply_rest_snapshot(
            "acct1", "binance", _make_snapshot_data([("USDT", "100", "200")]), ts_ms=1000
        )
        # locked > free → spendable should be 0, not negative
        assert svc.get_spendable("acct1", "binance", "USDT") == Decimal("0")


class TestGetBalance:
    def test_get_balance_returns_none_for_unknown(self) -> None:
        svc = AccountStateService()
        assert svc.get_balance("no_such", "binance", "USDT") is None
        svc.apply_rest_snapshot(
            "acct1", "binance", _make_snapshot_data([("USDT", "100", "0")]), ts_ms=1000
        )
        assert svc.get_balance("acct1", "binance", "BTC") is None
        assert svc.get_balance("acct1", "ftx", "USDT") is None


class TestStale:
    def test_mark_stale_and_is_stale(self) -> None:
        svc = AccountStateService()
        assert svc.is_stale("acct1", "binance") is False

        svc.mark_stale("acct1", "binance", "ws disconnect")
        assert svc.is_stale("acct1", "binance") is True

    def test_stale_is_per_account_venue(self) -> None:
        svc = AccountStateService()
        svc.mark_stale("acct1", "binance", "reason")
        assert svc.is_stale("acct1", "binance") is True
        assert svc.is_stale("acct1", "ftx") is False
        assert svc.is_stale("acct2", "binance") is False

    def test_private_stream_does_not_clear_stale(self) -> None:
        """Private stream is incremental — only REST snapshot clears stale."""
        svc = AccountStateService()
        svc.mark_stale("acct1", "binance", "ws disconnect")
        svc.apply_private_account_position(
            "acct1", "binance",
            {"E": 1000, "B": [{"a": "USDT", "f": "100", "l": "0"}]},
        )
        assert svc.is_stale("acct1", "binance") is True
        info = svc.get_stale_info("acct1", "binance")
        assert info is not None
        assert info.reason == "ws disconnect"

    def test_get_stale_info_returns_none_when_not_stale(self) -> None:
        svc = AccountStateService()
        assert svc.get_stale_info("acct1", "binance") is None


class TestMultipleAccountsAndVenues:
    def test_multiple_accounts_and_venues(self) -> None:
        svc = AccountStateService()
        svc.apply_rest_snapshot(
            "acct1", "binance", _make_snapshot_data([("USDT", "1000", "0")]), ts_ms=1000
        )
        svc.apply_rest_snapshot(
            "acct1", "ftx", _make_snapshot_data([("USDT", "2000", "0")]), ts_ms=1000
        )
        svc.apply_rest_snapshot(
            "acct2", "binance", _make_snapshot_data([("USDT", "3000", "0")]), ts_ms=1000
        )
        assert svc.get_balance("acct1", "binance", "USDT").free == Decimal("1000")
        assert svc.get_balance("acct1", "ftx", "USDT").free == Decimal("2000")
        assert svc.get_balance("acct2", "binance", "USDT").free == Decimal("3000")
        assert svc.get_balance("acct2", "ftx", "USDT") is None
