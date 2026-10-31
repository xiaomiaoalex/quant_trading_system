"""REST-backed coverage proof for the Binance spot private stream.

Binance user-data streams expose no account-wide cursor. Coverage therefore has
to be proven from an authoritative REST backfill over the whole recovery window:
complete pagination, every trade mapped to one of our orders, two consecutive
backfills agreeing, and every buffered stream fact inside the proven interval.

Any uncertainty returns ``None`` so the recovery coordinator stays blocked. Local
receive time is never used as evidence; the boundary comes from exchange server
time and trade timestamps.
"""

from __future__ import annotations

import asyncio
import inspect
import logging
from dataclasses import dataclass
from typing import Any, Callable

from trader.core.application.ports import BrokerOrder
from trader.trading.models import Execution, ReconcileSnapshot
from trader.trading.recovery import RecoveryCoverage

logger = logging.getLogger(__name__)


class _Unproven(Exception):
    """Internal signal: this adapter cannot prove contiguous coverage."""


@dataclass(frozen=True)
class _Backfill:
    orders: tuple[tuple[str, str, str, str, str, str], ...]
    trades: tuple[tuple[str, str, str, str, str, str, str, int], ...]


def _trade_key(trade: dict[str, Any]) -> tuple[str, str, str, str, str, str, str, int]:
    return (
        str(trade.get("symbol", "")),
        str(trade.get("id", "")),
        str(trade.get("orderId", "")),
        str(trade.get("qty", "")),
        str(trade.get("price", "")),
        str(trade.get("commission", "")),
        str(trade.get("commissionAsset", "")),
        int(trade.get("time", 0) or 0),
    )


def _order_key(order: BrokerOrder) -> tuple[str, str, str, str, str, str]:
    return (
        str(order.symbol).upper(),
        str(order.broker_order_id),
        str(order.cl_ord_id),
        str(getattr(order.status, "value", order.status)),
        str(order.qty),
        str(order.filled_qty),
    )


class BinanceSpotCoverageProver:
    """Derive a ``RecoveryCoverage`` from complete REST history.

    ``rest`` must expose ``fetch_orders(symbol, start_time_ms)`` and
    ``fetch_trades(symbol, start_time_ms)`` returning ``(items, complete)``;
    :class:`BinanceSpotSnapshotProvider` provides both.
    """

    def __init__(
        self,
        *,
        rest,
        symbols: list[str],
        exchange_clock: Callable[[], Any],
        lookback_ms: int = 86_400_000,
        settle_ms: int = 250,
        order_prefix: str = "qts-",
        venue: str = "binance_spot",
    ):
        if lookback_ms < 0 or settle_ms < 0:
            raise ValueError("Coverage windows must be non-negative")
        if not symbols:
            raise ValueError("Coverage proof requires explicit symbols")
        self.rest = rest
        self.symbols = tuple(sorted({symbol.upper() for symbol in symbols}))
        self.exchange_clock = exchange_clock
        self.lookback_ms = lookback_ms
        self.settle_ms = settle_ms
        self.order_prefix = order_prefix
        self.venue = venue

    async def __call__(
        self,
        generation: int,
        first: ReconcileSnapshot,
        last: ReconcileSnapshot,
        connected_from_ms: int,
        buffered_through_ms: int,
    ) -> RecoveryCoverage | None:
        try:
            return await self._prove(
                generation, first, last, connected_from_ms, buffered_through_ms
            )
        except _Unproven as exc:
            logger.warning("Private-stream coverage unproven: %s", exc)
            return None
        except asyncio.CancelledError:
            raise
        except Exception:
            logger.exception("Private-stream coverage proof failed closed")
            return None

    async def _prove(
        self,
        generation: int,
        first: ReconcileSnapshot,
        last: ReconcileSnapshot,
        connected_from_ms: int,
        buffered_through_ms: int,
    ) -> RecoveryCoverage:
        if first.account_id != last.account_id:
            raise _Unproven("ACCOUNT_MISMATCH")
        if first.complete is not True or last.complete is not True:
            raise _Unproven("SNAPSHOT_INCOMPLETE")
        if connected_from_ms <= 0:
            raise _Unproven("STREAM_CONNECTION_TIME_UNKNOWN")
        if buffered_through_ms < connected_from_ms:
            raise _Unproven("BUFFER_PRECEDES_CONNECTION")
        if first.checked_at_ms < connected_from_ms or last.checked_at_ms < connected_from_ms:
            raise _Unproven("SNAPSHOT_PRECEDES_CONNECTION")

        covered_through = await self._now()
        if covered_through < connected_from_ms:
            raise _Unproven("EXCHANGE_TIME_BEFORE_CONNECTION")
        if buffered_through_ms > covered_through:
            raise _Unproven("BUFFER_AFTER_PROVEN_WINDOW")

        start = connected_from_ms - self.lookback_ms
        # The proven window must cover the whole snapshot lookback window, not
        # only the part after the socket connected.
        if start > last.checked_at_ms - self.lookback_ms:
            raise _Unproven("PROVEN_WINDOW_DOES_NOT_COVER_SNAPSHOT_LOOKBACK")
        first_pass = await self._collect(start, covered_through)
        if self.settle_ms:
            # A second complete pass exposes venue-side late application.
            await asyncio.sleep(self.settle_ms / 1000)
        second_pass = await self._collect(start, covered_through)
        if first_pass != second_pass:
            raise _Unproven("BACKFILL_CHANGED_BETWEEN_PASSES")

        backfilled = {(trade[0], trade[1]) for trade in second_pass.trades}
        for snapshot in (first, last):
            for execution in snapshot.executions:
                if (str(execution.symbol).upper(), execution.exec_id) not in backfilled:
                    raise _Unproven("SNAPSHOT_EXECUTION_NOT_IN_BACKFILL")
                occurred = int(execution.occurred_at_ms)
                if occurred < start or occurred > covered_through:
                    raise _Unproven("SNAPSHOT_EXECUTION_OUTSIDE_PROVEN_WINDOW")

        return RecoveryCoverage(
            stream_generation=generation,
            source="binance_spot_rest_backfill",
            connected_from=connected_from_ms,
            covered_through=covered_through,
            buffered_through=buffered_through_ms,
            contiguous=True,
        )

    async def _collect(self, start_ms: int, end_ms: int) -> _Backfill:
        orders: dict[tuple[str, str], BrokerOrder] = {}
        trades: list[dict[str, Any]] = []
        for symbol in self.symbols:
            page, complete = await self.rest.fetch_orders(symbol, start_ms)
            if not complete:
                raise _Unproven(f"ORDER_PAGINATION_INCOMPLETE:{symbol}")
            for order in page:
                order_symbol = str(order.symbol).upper()
                order_id = str(order.broker_order_id)
                if not order_id:
                    raise _Unproven(f"ORDER_WITHOUT_ID:{symbol}")
                if order_symbol != symbol:
                    raise _Unproven(f"ORDER_SYMBOL_MISMATCH:{symbol}")
                if (order_symbol, order_id) in orders:
                    raise _Unproven(f"DUPLICATE_ORDER:{symbol}")
                orders[(order_symbol, order_id)] = order

            page, complete = await self.rest.fetch_trades(symbol, start_ms)
            if not complete:
                raise _Unproven(f"TRADE_PAGINATION_INCOMPLETE:{symbol}")
            last_time = None
            for trade in page:
                if str(trade.get("symbol", symbol)).upper() != symbol:
                    raise _Unproven(f"TRADE_SYMBOL_MISMATCH:{symbol}")
                item = {**trade, "symbol": symbol}
                key = _trade_key(item)
                if not key[1]:
                    raise _Unproven(f"TRADE_WITHOUT_ID:{symbol}")
                occurred = key[-1]
                if occurred < start_ms or occurred > end_ms:
                    raise _Unproven(f"TRADE_OUTSIDE_PROVEN_WINDOW:{symbol}")
                if last_time is not None and occurred < last_time:
                    # Venue timestamps must not move backwards inside one
                    # backfill; a regression means the history is inconsistent.
                    raise _Unproven(f"TRADE_TIME_REGRESSION:{symbol}")
                last_time = occurred
                order = orders.get((symbol, key[2]))
                if order is None:
                    raise _Unproven(f"UNMAPPED_EXTERNAL_TRADE:{symbol}")
                if not str(order.cl_ord_id).startswith(self.order_prefix):
                    raise _Unproven(f"FOREIGN_ORDER_TRADE:{symbol}")
                trades.append(item)

        seen: set[tuple[str, str]] = set()
        for trade in trades:
            trade_key = (str(trade.get("symbol", "")), str(trade.get("id", "")))
            if trade_key in seen:
                raise _Unproven("DUPLICATE_TRADE_ID")
            seen.add(trade_key)

        return _Backfill(
            orders=tuple(sorted(_order_key(order) for order in orders.values())),
            trades=tuple(sorted(_trade_key(trade) for trade in trades)),
        )

    async def _now(self) -> int:
        value = self.exchange_clock()
        if inspect.isawaitable(value):
            value = await value
        return int(value)
