"""
AccountStateService - 账户资产状态管理
======================================

职责：
1. 管理账户余额（free / locked）
2. REST snapshot 最终校准 + private stream 低延迟增量
3. stale 标记（fail-closed 语义）
4. 可选 PG 持久化（best-effort）

数据流：
  REST snapshot → apply_rest_snapshot (全量覆盖 + PG 持久化)
  Private WS   → apply_private_account_position (增量更新)
  Balance WS   → apply_balance_update (delta 更新)
"""

from __future__ import annotations

import asyncio
import logging
import time
from dataclasses import dataclass
from decimal import Decimal
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from trader.adapters.persistence.account_state_repository import AccountStateRepository

logger = logging.getLogger(__name__)


@dataclass(slots=True)
class AccountBalance:
    account_id: str
    venue: str
    asset: str
    free: Decimal
    locked: Decimal
    updated_at_ms: int
    source: str  # "rest_snapshot" | "private_stream" | "balance_update"


@dataclass(slots=True)
class StaleInfo:
    """Stale state metadata for an account+venue pair."""

    stale: bool
    reason: str
    timestamp_ms: int


class AccountStateService:
    """In-memory account balance state manager.

    Storage layout:
      _balances: {account_id: {venue: {asset: AccountBalance}}}
      _stale:    {(account_id, venue): StaleInfo}

    Args:
        repository: 可选的 PG 持久化层（best-effort，不可用时不抛异常）
    """

    def __init__(
        self,
        repository: "AccountStateRepository | None" = None,
    ) -> None:
        self._balances: dict[str, dict[str, dict[str, AccountBalance]]] = {}
        self._stale: dict[tuple[str, str], StaleInfo] = {}
        self._repository = repository

    # ------------------------------------------------------------------
    # Write methods
    # ------------------------------------------------------------------

    def apply_rest_snapshot(
        self, account_id: str, venue: str, balances: list[dict], ts_ms: int
    ) -> None:
        """Full calibration from REST. Overwrites all assets for this account+venue
        and clears stale state."""
        venue_map = self._balances.setdefault(account_id, {}).setdefault(venue, {})
        venue_map.clear()
        for item in balances:
            asset = str(item["asset"])
            free = Decimal(str(item.get("free", "0")))
            locked = Decimal(str(item.get("locked", "0")))
            venue_map[asset] = AccountBalance(
                account_id=account_id,
                venue=venue,
                asset=asset,
                free=free,
                locked=locked,
                updated_at_ms=ts_ms,
                source="rest_snapshot",
            )
        # Clear stale on successful calibration
        self._stale.pop((account_id, venue), None)
        # Best-effort PG persistence
        self._persist_balances(account_id, venue, balances, ts_ms, "rest_snapshot")

    def apply_private_account_position(self, account_id: str, venue: str, event: dict) -> None:
        """Incremental update from private stream (accountPosition event).

        Note: intentionally does NOT clear stale state — private stream is an
        incremental source, only REST snapshot provides full calibration.

        Expected event fields:
          B (list[dict]): [{"a": "USDT", "f": "100.0", "l": "10.0"}, ...]
          E (int): event timestamp ms
        """
        ts_ms: int = int(event.get("E", int(time.time() * 1000)))
        balances_raw = event.get("B", [])
        venue_map = self._balances.setdefault(account_id, {}).setdefault(venue, {})
        for item in balances_raw:
            asset = str(item.get("a", ""))
            if not asset:
                continue
            free = Decimal(str(item.get("f", "0")))
            locked = Decimal(str(item.get("l", "0")))
            venue_map[asset] = AccountBalance(
                account_id=account_id,
                venue=venue,
                asset=asset,
                free=free,
                locked=locked,
                updated_at_ms=ts_ms,
                source="private_stream",
            )

    def apply_balance_update(self, account_id: str, venue: str, event: dict) -> None:
        """Delta update from balanceUpdate event.

        Expected event fields:
          a (str): asset
          d (str): delta amount (can be negative)
          E (int): event timestamp ms
        """
        asset = str(event.get("a", ""))
        if not asset:
            return
        delta = Decimal(str(event.get("d", "0")))
        ts_ms: int = int(event.get("E", int(time.time() * 1000)))

        venue_map = self._balances.setdefault(account_id, {}).setdefault(venue, {})
        existing = venue_map.get(asset)
        if existing is not None:
            new_free = existing.free + delta
            venue_map[asset] = AccountBalance(
                account_id=account_id,
                venue=venue,
                asset=asset,
                free=new_free,
                locked=existing.locked,
                updated_at_ms=ts_ms,
                source="balance_update",
            )
        else:
            venue_map[asset] = AccountBalance(
                account_id=account_id,
                venue=venue,
                asset=asset,
                free=delta,
                locked=Decimal("0"),
                updated_at_ms=ts_ms,
                source="balance_update",
            )

    def mark_stale(self, account_id: str, venue: str, reason: str) -> None:
        """Mark account+venue state as stale (fail-closed)."""
        self._stale[(account_id, venue)] = StaleInfo(
            stale=True,
            reason=reason,
            timestamp_ms=int(time.time() * 1000),
        )

    # ------------------------------------------------------------------
    # Read methods
    # ------------------------------------------------------------------

    def get_stale_info(self, account_id: str, venue: str) -> StaleInfo | None:
        """Return stale metadata for observability, or None if not stale."""
        info = self._stale.get((account_id, venue))
        return info if info is not None and info.stale else None

    def get_balance(self, account_id: str, venue: str, asset: str) -> AccountBalance | None:
        """Return balance for the given account+venue+asset, or None if unknown."""
        venue_map = self._balances.get(account_id)
        if venue_map is None:
            return None
        asset_map = venue_map.get(venue)
        if asset_map is None:
            return None
        return asset_map.get(asset)

    def get_spendable(self, account_id: str, venue: str, asset: str) -> Decimal:
        """Return spendable amount = free - locked. Returns Decimal('0') if unknown."""
        bal = self.get_balance(account_id, venue, asset)
        if bal is None:
            return Decimal("0")
        spendable = bal.free - bal.locked
        return spendable if spendable > Decimal("0") else Decimal("0")

    def is_stale(self, account_id: str, venue: str) -> bool:
        """Check if account+venue state is marked stale."""
        info = self._stale.get((account_id, venue))
        return info is not None and info.stale

    # ------------------------------------------------------------------
    # Persistence helpers (best-effort, never raise)
    # ------------------------------------------------------------------

    def _persist_balances(
        self,
        account_id: str,
        venue: str,
        balances: list[dict],
        ts_ms: int,
        source: str,
    ) -> None:
        """Best-effort PG 持久化（fire-and-forget，不阻塞主流程）。"""
        if self._repository is None:
            return
        try:
            loop = asyncio.get_running_loop()
            loop.create_task(
                self._repository.save_balances(account_id, venue, balances, ts_ms, source)
            )
        except RuntimeError:
            # 无 running loop（进程初始化阶段）
            pass
        except Exception as exc:
            logger.warning("[AccountState] _persist_balances failed: %s", exc)

    async def load_from_pg(self, account_id: str, venue: str) -> bool:
        """从 PG 加载余额。Returns True if loaded."""
        if self._repository is None:
            return False
        try:
            rows = await self._repository.load_balances(account_id, venue)
            if rows is None:
                return False
            self.apply_rest_snapshot(account_id, venue, rows, int(time.time() * 1000))
            logger.info(
                "[AccountState] Loaded %d balances from PG: account=%s venue=%s",
                len(rows),
                account_id,
                venue,
            )
            return True
        except Exception as exc:
            logger.warning("[AccountState] load_from_pg failed: %s", exc)
            return False
