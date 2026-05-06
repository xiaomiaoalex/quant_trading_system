"""Tests for AccountStreamBridge."""

from __future__ import annotations

import asyncio
from decimal import Decimal

import pytest

from trader.services.account_state import AccountStateService
from trader.services.account_stream_bridge import AccountStreamBridge, AccountStreamBridgeConfig


def _make_state() -> AccountStateService:
    return AccountStateService()


def _make_bridge(
    account_state: AccountStateService | None = None,
    account_id: str = "acct1",
    venue: str = "binance",
    interval_s: float = 60.0,
) -> AccountStreamBridge:
    state = account_state or _make_state()
    config = AccountStreamBridgeConfig(
        account_id=account_id, venue=venue, rest_snapshot_interval_s=interval_s
    )
    return AccountStreamBridge(state, config)


class TestOnAccountUpdate:
    def test_applies_outbound_account_position(self) -> None:
        state = _make_state()
        bridge = _make_bridge(state, account_id="acct1", venue="binance")

        bridge.on_account_update(
            {
                "e": "outboundAccountPosition",
                "E": 3000,
                "B": [
                    {"a": "USDT", "f": "500.00", "l": "50.00"},
                    {"a": "BTC", "f": "1.0", "l": "0"},
                ],
            }
        )

        bal_usdt = state.get_balance("acct1", "binance", "USDT")
        assert bal_usdt is not None
        assert bal_usdt.free == Decimal("500.00")
        assert bal_usdt.locked == Decimal("50.00")
        assert bal_usdt.source == "private_stream"

        bal_btc = state.get_balance("acct1", "binance", "BTC")
        assert bal_btc is not None
        assert bal_btc.free == Decimal("1.0")

    def test_overwrites_existing_balance(self) -> None:
        state = _make_state()
        bridge = _make_bridge(state)
        state.apply_private_account_position(
            "acct1", "binance", {"E": 1000, "B": [{"a": "USDT", "f": "100", "l": "0"}]}
        )

        bridge.on_account_update(
            {
                "E": 2000,
                "B": [{"a": "USDT", "f": "999", "l": "0"}],
            }
        )

        bal = state.get_balance("acct1", "binance", "USDT")
        assert bal is not None
        assert bal.free == Decimal("999")
        assert bal.updated_at_ms == 2000


class TestOnBalanceUpdate:
    def test_delta_positive(self) -> None:
        state = _make_state()
        bridge = _make_bridge(state)
        state.apply_private_account_position(
            "acct1", "binance", {"E": 1000, "B": [{"a": "USDT", "f": "100", "l": "0"}]}
        )

        bridge.on_balance_update(
            {
                "e": "balanceUpdate",
                "a": "USDT",
                "d": "50",
                "E": 2000,
            }
        )

        bal = state.get_balance("acct1", "binance", "USDT")
        assert bal is not None
        assert bal.free == Decimal("150")

    def test_delta_negative(self) -> None:
        state = _make_state()
        bridge = _make_bridge(state)
        state.apply_private_account_position(
            "acct1", "binance", {"E": 1000, "B": [{"a": "USDT", "f": "100", "l": "0"}]}
        )

        bridge.on_balance_update(
            {
                "e": "balanceUpdate",
                "a": "USDT",
                "d": "-30",
                "E": 2000,
            }
        )

        bal = state.get_balance("acct1", "binance", "USDT")
        assert bal is not None
        assert bal.free == Decimal("70")

    def test_delta_creates_new_asset(self) -> None:
        state = _make_state()
        bridge = _make_bridge(state)

        bridge.on_balance_update(
            {
                "e": "balanceUpdate",
                "a": "ETH",
                "d": "5",
                "E": 3000,
            }
        )

        bal = state.get_balance("acct1", "binance", "ETH")
        assert bal is not None
        assert bal.free == Decimal("5")


class TestOnPrivateStreamDisconnect:
    def test_marks_stale(self) -> None:
        state = _make_state()
        bridge = _make_bridge(state)

        assert state.is_stale("acct1", "binance") is False

        bridge.on_private_stream_disconnect("ws_timeout")

        assert state.is_stale("acct1", "binance") is True
        info = state.get_stale_info("acct1", "binance")
        assert info is not None
        assert "ws_timeout" in info.reason

    def test_multiple_disconnects_update_reason(self) -> None:
        state = _make_state()
        bridge = _make_bridge(state)
        bridge.on_private_stream_disconnect("first_reason")

        bridge.on_private_stream_disconnect("second_reason")

        info = state.get_stale_info("acct1", "binance")
        assert info is not None
        assert "second_reason" in info.reason


class TestFetchAndApplyRestSnapshot:
    @pytest.mark.asyncio
    async def test_success(self) -> None:
        state = _make_state()
        bridge = _make_bridge(state, account_id="acct1", venue="binance")

        async def fake_fetch() -> list[dict]:
            return [
                {"asset": "USDT", "free": "1000", "locked": "50"},
                {"asset": "BTC", "free": "2", "locked": "0"},
            ]

        ok = await bridge.fetch_and_apply_rest_snapshot(fake_fetch)
        assert ok is True

        bal = state.get_balance("acct1", "binance", "USDT")
        assert bal is not None
        assert bal.free == Decimal("1000")
        assert bal.locked == Decimal("50")
        assert bal.source == "rest_snapshot"

        btc = state.get_balance("acct1", "binance", "BTC")
        assert btc is not None
        assert btc.free == Decimal("2")

    @pytest.mark.asyncio
    async def test_failure_marks_stale(self) -> None:
        state = _make_state()
        bridge = _make_bridge(state)

        async def bad_fetch() -> list[dict]:
            raise RuntimeError("network error")

        ok = await bridge.fetch_and_apply_rest_snapshot(bad_fetch)
        assert ok is False
        assert state.is_stale("acct1", "binance") is True

    @pytest.mark.asyncio
    async def test_clears_stale_on_success(self) -> None:
        state = _make_state()
        bridge = _make_bridge(state)
        state.mark_stale("acct1", "binance", "prior_reason")

        async def fake_fetch() -> list[dict]:
            return [{"asset": "USDT", "free": "500", "locked": "0"}]

        await bridge.fetch_and_apply_rest_snapshot(fake_fetch)

        assert state.is_stale("acct1", "binance") is False


class TestPeriodicCalibration:
    @pytest.mark.asyncio
    async def test_start_creates_task(self) -> None:
        state = _make_state()
        bridge = _make_bridge(state, interval_s=0.1)

        async def fake_fetch() -> list[dict]:
            return [{"asset": "USDT", "free": "100", "locked": "0"}]

        bridge.start_periodic_calibration(fake_fetch)
        assert bridge._calibration_task is not None

        await asyncio.sleep(0.25)

        bal = state.get_balance("acct1", "binance", "USDT")
        assert bal is not None
        assert bal.free == Decimal("100")

        await bridge.stop()

    @pytest.mark.asyncio
    async def test_stop_cancels_task(self) -> None:
        state = _make_state()
        bridge = _make_bridge(state, interval_s=60.0)

        async def fake_fetch() -> list[dict]:
            return []

        bridge.start_periodic_calibration(fake_fetch)
        assert bridge._calibration_task is not None
        task = bridge._calibration_task

        await bridge.stop()

        assert task.done()
        assert bridge._calibration_task is None

    @pytest.mark.asyncio
    async def test_start_idempotent(self) -> None:
        state = _make_state()
        bridge = _make_bridge(state, interval_s=0.1)

        async def fake_fetch() -> list[dict]:
            return []

        bridge.start_periodic_calibration(fake_fetch)
        task1 = bridge._calibration_task

        bridge.start_periodic_calibration(fake_fetch)
        assert bridge._calibration_task is task1

        await bridge.stop()
