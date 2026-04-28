"""
AccountStreamBridge - Private Stream → AccountStateService 桥接
===============================================================

职责：
1. 接收 private stream 的 outboundAccountPosition / balanceUpdate 事件
2. 调用 AccountStateService 更新余额状态
3. 周期性 REST snapshot 校准（防漂移）
4. 断连时标记 stale（fail-closed）

数据流：
  Private WS → on_account_update → AccountStateService.apply_private_account_position
  Private WS → on_balance_update → AccountStateService.apply_balance_update
  定时器    → fetch_and_apply_rest_snapshot → AccountStateService.apply_rest_snapshot
  断连     → on_private_stream_disconnect → AccountStateService.mark_stale
"""
from __future__ import annotations

import asyncio
import logging
import time
from dataclasses import dataclass
from typing import Awaitable, Callable, Optional

from trader.services.account_state import AccountStateService

logger = logging.getLogger(__name__)


@dataclass(slots=True)
class AccountStreamBridgeConfig:
    account_id: str = "binance_demo"
    venue: str = "binance_demo"
    rest_snapshot_interval_s: float = 60.0


class AccountStreamBridge:
    """桥接 private stream 事件与 AccountStateService。

    不直接依赖 broker——通过 callbacks 获取余额数据。
    """

    def __init__(
        self,
        account_state: AccountStateService,
        config: AccountStreamBridgeConfig | None = None,
    ) -> None:
        self._account_state = account_state
        self._config = config or AccountStreamBridgeConfig()
        self._calibration_task: Optional[asyncio.Task] = None
        self._fetch_balances_fn: Optional[Callable[[], Awaitable[list[dict]]]] = None

    # ------------------------------------------------------------------
    # WS event handlers (sync — called from connector dispatch loop)
    # ------------------------------------------------------------------

    def on_account_update(self, event: dict) -> None:
        """Handler for outboundAccountPosition events."""
        self._account_state.apply_private_account_position(
            self._config.account_id, self._config.venue, event
        )

    def on_balance_update(self, event: dict) -> None:
        """Handler for balanceUpdate events."""
        self._account_state.apply_balance_update(
            self._config.account_id, self._config.venue, event
        )

    def on_private_stream_disconnect(self, reason: str) -> None:
        """标记 stale — WS 断连后余额不再可靠。"""
        self._account_state.mark_stale(
            self._config.account_id, self._config.venue, reason
        )
        logger.warning(
            "[AccountBridge] Account state marked stale: account=%s venue=%s reason=%s",
            self._config.account_id, self._config.venue, reason,
        )

    # ------------------------------------------------------------------
    # REST snapshot calibration
    # ------------------------------------------------------------------

    async def fetch_and_apply_rest_snapshot(
        self,
        fetch_balances: Callable[[], Awaitable[list[dict]]],
    ) -> bool:
        """拉取 REST 余额快照并应用。返回 True on success。

        Args:
            fetch_balances: 异步回调，返回 [{"asset":"USDT","free":"1000","locked":"0"}, ...]
        """
        try:
            balances = await fetch_balances()
            ts_ms = int(time.time() * 1000)
            self._account_state.apply_rest_snapshot(
                self._config.account_id, self._config.venue, balances, ts_ms
            )
            logger.info(
                "[AccountBridge] REST snapshot applied: account=%s venue=%s assets=%d",
                self._config.account_id, self._config.venue, len(balances),
            )
            return True
        except Exception as exc:
            logger.error("[AccountBridge] REST snapshot failed: %s", exc)
            self._account_state.mark_stale(
                self._config.account_id, self._config.venue,
                f"rest_snapshot_failed: {exc}",
            )
            return False

    # ------------------------------------------------------------------
    # Periodic calibration
    # ------------------------------------------------------------------

    def start_periodic_calibration(
        self,
        fetch_balances: Callable[[], Awaitable[list[dict]]],
    ) -> None:
        """启动后台周期校准任务。"""
        if self._calibration_task is not None and not self._calibration_task.done():
            logger.debug("[AccountBridge] Periodic calibration already running")
            return
        self._fetch_balances_fn = fetch_balances
        self._calibration_task = asyncio.create_task(
            self._calibration_loop(),
            name="account-calibration",
        )
        logger.info(
            "[AccountBridge] Periodic calibration started: interval=%.0fs",
            self._config.rest_snapshot_interval_s,
        )

    async def _calibration_loop(self) -> None:
        while True:
            await asyncio.sleep(self._config.rest_snapshot_interval_s)
            if self._fetch_balances_fn is not None:
                await self.fetch_and_apply_rest_snapshot(self._fetch_balances_fn)

    async def stop(self) -> None:
        """停止后台任务。"""
        if self._calibration_task is not None and not self._calibration_task.done():
            self._calibration_task.cancel()
            try:
                await self._calibration_task
            except asyncio.CancelledError:
                pass
            self._calibration_task = None
            logger.info("[AccountBridge] Periodic calibration stopped")
