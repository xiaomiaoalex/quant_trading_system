"""
ExecutionBudgetService - 下单前预算管理
========================================

职责：
1. 下单前检查 spendable 余额是否足够
2. 创建 reservation 占用预算，防止超卖
3. 管理 reservation 生命周期（PENDING_SUBMIT → ACCEPTED → TERMINAL/EXPIRED）
4. 过期清理
5. 可选 PG 持久化（best-effort）

数据流：
  策略信号 → reserve_order → 检查余额 → 创建 PENDING_SUBMIT reservation + PG 持久化
  Broker 返回 → accept_reservation → ACCEPTED + PG 持久化
  成交/取消/拒单 → release_reservation → TERMINAL + PG 删除
  超时 → expire_stale_reservations → EXPIRED + PG 删除
"""

from __future__ import annotations

import asyncio
import logging
import time
from dataclasses import dataclass
from decimal import Decimal
from typing import TYPE_CHECKING

from trader.services.account_state import AccountStateService

if TYPE_CHECKING:
    from trader.adapters.persistence.budget_reservation_repository import (
        BudgetReservationRepository,
    )

logger = logging.getLogger(__name__)

# Quote assets — 用于区分 BUY 时扣哪个 asset
QUOTE_ASSETS: frozenset[str] = frozenset(
    (
        "USDT",
        "FDUSD",
        "BUSD",
        "USDC",
        "USD",
        "BTC",
        "ETH",
        "1000SHIB",
        "1000FLOKI",
        "1000LUNC",
        "1000BONK",
        "10000SATS",
        "1000000MOG",
    )
)

# 带数字前缀的 symbol（乘数单位）：symbol → (实际 asset, 乘数)
# 例：1000SHIB 代表 1000 个 SHIB，卖出 1 单 = 实际扣 1000 SHIB
MULTIPLIER_SYMBOLS: dict[str, tuple[str, int]] = {
    "1000SHIB": ("SHIB", 1000),
    "1000FLOKI": ("FLOKI", 1000),
    "1000LUNC": ("LUNC", 1000),
    "1000BONK": ("BONK", 1000),
    "10000SATS": ("SATS", 10000),
    "1000000MOG": ("MOG", 1000000),
}

# Reservation 生命周期状态
PENDING_SUBMIT = "PENDING_SUBMIT"
ACCEPTED = "ACCEPTED"
TERMINAL = "TERMINAL"
EXPIRED = "EXPIRED"


@dataclass(slots=True)
class BalanceReservation:
    reservation_id: str  # cl_ord_id
    account_id: str
    venue: str
    asset: str
    amount: Decimal
    status: str  # PENDING_SUBMIT | ACCEPTED | TERMINAL | EXPIRED
    created_at_ms: int
    expires_at_ms: int


def _parse_symbol(symbol: str) -> tuple[int, str, str]:
    """从交易对拆出 multiplier/base/quote。

    Returns: (multiplier, base, quote)
      multiplier: 乘数（普通 symbol 为 1，1000SHIB 类为 1000）
      base: 基础资产（如 "BTC"、"SHIB"，不是 "1000SHIB"）
      quote: 报价资产（如 "USDT"）

    规则：
      1. 先尝试匹配乘数 symbol（1000SHIB 等），命中则从右剥离 quote
      2. 否则从右往左匹配已知 quote asset，最长匹配优先
      3. 如果 base 是乘数 symbol 本身，解析其真实 base

    例：BTCUSDT → (1, "BTC", "USDT")
        1000SHIBUSDT → (1000, "SHIB", "USDT")
    """
    sorted_quotes = sorted(QUOTE_ASSETS, key=len, reverse=True)
    for quote in sorted_quotes:
        if symbol.endswith(quote) and len(symbol) > len(quote):
            base_raw = symbol[: -len(quote)]
            if base_raw in MULTIPLIER_SYMBOLS:
                real_base, multiplier = MULTIPLIER_SYMBOLS[base_raw]
                return multiplier, real_base, quote
            return 1, base_raw, quote
    raise ValueError(f"Cannot parse symbol: {symbol}")


def _resolve_asset(symbol: str, side: str) -> tuple[str, int]:
    """根据 symbol 和 side 确定要扣预算的 asset 和乘数。

    BUY  → 扣 quote asset（买 BTC/USDT 扣 USDT），乘数不影响
    SELL → 扣 base asset（卖 1000SHIB/USDT 扣 SHIB），乘数计入 required

    Returns: (asset_name, multiplier)
    """
    multiplier, base, quote = _parse_symbol(symbol)
    if side.upper() == "BUY":
        return quote, multiplier
    elif side.upper() == "SELL":
        return base, multiplier
    else:
        raise ValueError(f"Unknown side: {side}")


class ExecutionBudgetService:
    """In-memory execution budget manager.

    Storage layout:
      _reservations: {cl_ord_id: BalanceReservation}

    Args:
        account_state: 账户状态服务（必填）
        default_ttl_ms: reservation 默认过期 TTL（毫秒）
        repository: 可选的 PG 持久化层（best-effort，不可用时不抛异常）
    """

    def __init__(
        self,
        account_state: AccountStateService,
        default_ttl_ms: int = 30_000,
        repository: "BudgetReservationRepository | None" = None,
    ) -> None:
        self._account_state = account_state
        self._default_ttl_ms = default_ttl_ms
        self._reservations: dict[str, BalanceReservation] = {}
        self._repository = repository

    # ------------------------------------------------------------------
    # Write methods
    # ------------------------------------------------------------------

    def reserve_order(
        self,
        account_id: str,
        venue: str,
        cl_ord_id: str,
        symbol: str,
        side: str,
        quantity: Decimal,
        reference_price: Decimal,
    ) -> tuple[bool, str]:
        """申请预算占用。检查 spendable >= required，通过则创建 reservation。

        BUY: asset=quote_asset, required=qty*price
        SELL: asset=base_asset, required=qty*multiplier

        Returns: (approved: bool, reason: str)
        """
        # 幂等：同一 cl_ord_id 不重复创建
        if cl_ord_id in self._reservations:
            return False, f"DUPLICATE_RESERVATION: cl_ord_id={cl_ord_id} already exists"

        # 解析 asset 和乘数
        try:
            asset, multiplier = _resolve_asset(symbol, side)
        except ValueError as exc:
            return False, f"SYMBOL_PARSE_ERROR: {exc}"

        # 计算 required amount（SELL 乘数计入：1000SHIB × qty = 实际 SHIB 数量）
        if side.upper() == "BUY":
            required = quantity * reference_price
        else:
            required = quantity * multiplier

        if required <= Decimal("0"):
            return False, f"INVALID_AMOUNT: required={required} must be positive"

        # 检查可用余额
        account_spendable = self._account_state.get_spendable(account_id, venue, asset)
        reserved = self.get_reserved(account_id, venue, asset)
        spendable = account_spendable - reserved

        if spendable < required:
            return (
                False,
                f"INSUFFICIENT_BALANCE: asset={asset} spendable={spendable} "
                f"required={required} (account={account_spendable} reserved={reserved})",
            )

        # 创建 reservation
        now_ms = int(time.time() * 1000)
        res = BalanceReservation(
            reservation_id=cl_ord_id,
            account_id=account_id,
            venue=venue,
            asset=asset,
            amount=required,
            status=PENDING_SUBMIT,
            created_at_ms=now_ms,
            expires_at_ms=now_ms + self._default_ttl_ms,
        )
        self._reservations[cl_ord_id] = res
        # Best-effort PG 持久化
        self._persist_reservation(res)
        return True, ""

    def accept_reservation(self, cl_ord_id: str) -> None:
        """Broker 返回成功后，标记 reservation 为 ACCEPTED。"""
        res = self._reservations.get(cl_ord_id)
        if res is None:
            raise KeyError(f"Reservation not found: {cl_ord_id}")
        if res.status != PENDING_SUBMIT:
            raise ValueError(f"Cannot accept reservation {cl_ord_id} in status {res.status}")
        res.status = ACCEPTED
        self._persist_reservation(res)

    def release_reservation(self, cl_ord_id: str, reason: str) -> None:
        """释放 reservation（拒单/成交/取消/过期）。"""
        res = self._reservations.get(cl_ord_id)
        if res is None:
            raise KeyError(f"Reservation not found: {cl_ord_id}")
        if res.status in (TERMINAL, EXPIRED):
            raise ValueError(
                f"Cannot release reservation {cl_ord_id} in terminal status {res.status}"
            )
        res.status = TERMINAL
        logger.info(
            f"[ExecBudget] Reservation released: cl_ord_id={cl_ord_id}, "
            f"reason={reason}, asset={res.asset}, amount={res.amount}"
        )
        # 从 PG 删除
        self._delete_reservation(cl_ord_id)

    def expire_stale_reservations(self, now_ms: int) -> int:
        """清理过期 reservation，返回清理数量。"""
        count = 0
        expired_ids: list[str] = []
        for res in self._reservations.values():
            if res.status in (PENDING_SUBMIT, ACCEPTED) and res.expires_at_ms <= now_ms:
                res.status = EXPIRED
                expired_ids.append(res.reservation_id)
                count += 1
        for rid in expired_ids:
            self._delete_reservation(rid)
        return count

    # ------------------------------------------------------------------
    # Read methods
    # ------------------------------------------------------------------

    def get_reserved(self, account_id: str, venue: str, asset: str) -> Decimal:
        """获取某 asset 的总占用金额（PENDING_SUBMIT + ACCEPTED）。"""
        total = Decimal("0")
        for res in self._reservations.values():
            if (
                res.account_id == account_id
                and res.venue == venue
                and res.asset == asset
                and res.status in (PENDING_SUBMIT, ACCEPTED)
            ):
                total += res.amount
        return total

    def get_reservation(self, cl_ord_id: str) -> BalanceReservation | None:
        """获取单个 reservation。"""
        return self._reservations.get(cl_ord_id)

    # ------------------------------------------------------------------
    # Persistence helpers (best-effort, never raise)
    # ------------------------------------------------------------------

    def _persist_reservation(self, res: BalanceReservation) -> None:
        """Best-effort PG 持久化（fire-and-forget）。"""
        if self._repository is None:
            return
        try:
            loop = asyncio.get_running_loop()
            loop.create_task(self._repository.save_reservation(res))
        except RuntimeError:
            pass
        except Exception as exc:
            logger.warning("[ExecBudget] _persist_reservation failed: %s", exc)

    def _delete_reservation(self, reservation_id: str) -> None:
        """Best-effort PG 删除（fire-and-forget）。"""
        if self._repository is None:
            return
        try:
            loop = asyncio.get_running_loop()
            loop.create_task(self._repository.delete_reservation(reservation_id))
        except RuntimeError:
            pass
        except Exception as exc:
            logger.warning("[ExecBudget] _delete_reservation failed: %s", exc)

    async def load_from_pg(self) -> int:
        """从 PG 加载活跃 reservation。Returns loaded count."""
        if self._repository is None:
            return 0
        try:
            rows = await self._repository.load_active()
            if rows is None:
                return 0
            count = 0
            for row in rows:
                rid = row["reservation_id"]
                if rid not in self._reservations:
                    self._reservations[rid] = BalanceReservation(
                        reservation_id=rid,
                        account_id=row["account_id"],
                        venue=row["venue"],
                        asset=row["asset"],
                        amount=Decimal(str(row["amount"])),
                        status=row["status"],
                        created_at_ms=row["created_at_ms"],
                        expires_at_ms=row["expires_at_ms"],
                    )
                    count += 1
            logger.info("[ExecBudget] Loaded %d reservations from PG", count)
            return count
        except Exception as exc:
            logger.warning("[ExecBudget] load_from_pg failed: %s", exc)
            return 0
