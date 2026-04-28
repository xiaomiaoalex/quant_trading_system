"""
ExecutionBudgetService - 下单前预算管理
========================================

职责：
1. 下单前检查 spendable 余额是否足够
2. 创建 reservation 占用预算，防止超卖
3. 管理 reservation 生命周期（PENDING_SUBMIT → ACCEPTED → TERMINAL/EXPIRED）
4. 过期清理

数据流：
  策略信号 → reserve_order → 检查余额 → 创建 PENDING_SUBMIT reservation
  Broker 返回 → accept_reservation → ACCEPTED
  成交/取消/拒单 → release_reservation → TERMINAL
  超时 → expire_stale_reservations → EXPIRED
"""
from __future__ import annotations

import logging
import time
from dataclasses import dataclass, field
from decimal import Decimal

from trader.services.account_state import AccountStateService

logger = logging.getLogger(__name__)

# Quote assets — 用于区分 BUY 时扣哪个 asset
QUOTE_ASSETS: frozenset[str] = frozenset(
    ("USDT", "FDUSD", "BUSD", "USDC", "USD", "BTC", "ETH")
)

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


def _parse_symbol(symbol: str) -> tuple[str, str]:
    """从交易对拆出 base/quote assets。

    规则：从右往左匹配已知 quote asset，最长匹配优先。
    例：BTCUSDT → ("BTC", "USDT"), BTCFDUSD → ("BTC", "FDUSD")
    """
    # 按长度降序排列 quote assets，优先最长匹配
    sorted_quotes = sorted(QUOTE_ASSETS, key=len, reverse=True)
    for quote in sorted_quotes:
        if symbol.endswith(quote) and len(symbol) > len(quote):
            base = symbol[: -len(quote)]
            return base, quote
    raise ValueError(f"Cannot parse symbol: {symbol}")


def _resolve_asset(symbol: str, side: str) -> str:
    """根据 symbol 和 side 确定要扣预算的 asset。

    BUY  → 扣 quote asset（买 BTC/USDT 扣 USDT）
    SELL → 扣 base asset（卖 BTC/USDT 扣 BTC）
    """
    base, quote = _parse_symbol(symbol)
    if side.upper() == "BUY":
        return quote
    elif side.upper() == "SELL":
        return base
    else:
        raise ValueError(f"Unknown side: {side}")


class ExecutionBudgetService:
    """In-memory execution budget manager.

    Storage layout:
      _reservations: {cl_ord_id: BalanceReservation}
    """

    def __init__(
        self, account_state: AccountStateService, default_ttl_ms: int = 30_000
    ) -> None:
        self._account_state = account_state
        self._default_ttl_ms = default_ttl_ms
        self._reservations: dict[str, BalanceReservation] = {}

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
        SELL: asset=base_asset, required=qty

        Returns: (approved: bool, reason: str)
        """
        # 幂等：同一 cl_ord_id 不重复创建
        if cl_ord_id in self._reservations:
            return False, f"DUPLICATE_RESERVATION: cl_ord_id={cl_ord_id} already exists"

        # 解析 asset
        try:
            asset = _resolve_asset(symbol, side)
        except ValueError as exc:
            return False, f"SYMBOL_PARSE_ERROR: {exc}"

        # 计算 required amount
        if side.upper() == "BUY":
            required = quantity * reference_price
        else:
            required = quantity

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
        self._reservations[cl_ord_id] = BalanceReservation(
            reservation_id=cl_ord_id,
            account_id=account_id,
            venue=venue,
            asset=asset,
            amount=required,
            status=PENDING_SUBMIT,
            created_at_ms=now_ms,
            expires_at_ms=now_ms + self._default_ttl_ms,
        )
        return True, ""

    def accept_reservation(self, cl_ord_id: str) -> None:
        """Broker 返回成功后，标记 reservation 为 ACCEPTED。"""
        res = self._reservations.get(cl_ord_id)
        if res is None:
            raise KeyError(f"Reservation not found: {cl_ord_id}")
        if res.status != PENDING_SUBMIT:
            raise ValueError(
                f"Cannot accept reservation {cl_ord_id} in status {res.status}"
            )
        res.status = ACCEPTED

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

    def expire_stale_reservations(self, now_ms: int) -> int:
        """清理过期 reservation，返回清理数量。"""
        count = 0
        for res in self._reservations.values():
            if res.status in (PENDING_SUBMIT, ACCEPTED) and res.expires_at_ms <= now_ms:
                res.status = EXPIRED
                count += 1
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
