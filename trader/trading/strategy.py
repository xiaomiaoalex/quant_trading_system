"""Strategy-to-runtime boundary with stable event identities and one account ledger.

The runner owns plugin scheduling only. It has no broker/OMS, reservation or
committed-exposure counters; the recovered trading runtime owns execution.

Indicator state is never persisted. A restarted process re-collects the
warm-up window (history replay) and warms up again, because plugin indicator
state is neither serializable nor safe to trust across code or config
changes. Until the window is rebuilt the runner suppresses every signal and
reports an explicit warm-up state.
"""

from __future__ import annotations

import asyncio
import hashlib
import inspect
import json
from datetime import timezone
from decimal import Decimal
from enum import Enum
from typing import TYPE_CHECKING, Any

from trader.core.application.strategy_protocol import (
    MarketData,
    MarketDataType,
    describe_plugin_warmup,
)
from trader.core.domain.models.signal import Signal
from trader.trading import books as book_ledger
from trader.trading.datasets import INTERVAL_MS
from trader.trading.models import (
    AccountRiskView,
    CommandReceipt,
    Conflict,
    NotReady,
    TradingError,
    json_value,
)
from trader.trading.order_spec import PRICE_SOURCE_MARKET_CLOSED_BAR, build_order_spec

if TYPE_CHECKING:
    from trader.trading.runtime import TradingRuntime


class WarmupState(str, Enum):
    """Explicit indicator warm-up state of one strategy runner."""

    PENDING = "PENDING"
    WARMING_UP = "WARMING_UP"
    WARM = "WARM"
    BLOCKED = "BLOCKED"


def _hash(value: Any) -> str:
    encoded = json.dumps(json_value(value), sort_keys=True, separators=(",", ":"), allow_nan=False)
    return hashlib.sha256(encoded.encode()).hexdigest()


def _observation_at_ms(data: MarketData) -> int:
    """UTC milliseconds of the observation timestamp."""
    return int(data.timestamp.timestamp() * 1000)


def _runtime_clock_ms(runtime, data: MarketData | None = None) -> int:
    """Decision clock in UTC milliseconds.

    Production runtimes always expose ``clock``. Test doubles that stand in for
    only the admission port fall back to the observation timestamp instead of
    making the visibility contract depend on the fake's attribute set.
    """
    clock = getattr(runtime, "clock", None)
    if callable(clock):
        return int(clock())
    if data is not None:
        return _observation_at_ms(data)
    raise TradingError("STRATEGY_DECISION_CLOCK_REQUIRED")


def _validated_closed_bar(data: MarketData) -> dict[str, Any]:
    """Validate one closed KLINE at the new-stack boundary.

    Missing or contradictory bar fields are refused; the observation timestamp
    and ``data.price`` are never used to invent a BAR_V1 close or availability.
    The accepted representation is an explicit open time plus a supported
    interval, with all supplied close/availability fields equal to
    ``open_time_ms + interval_ms`` and an explicit closing price.
    """
    if data.data_type is not MarketDataType.KLINE:
        raise TradingError("NON_BAR_MARKET_DATA")
    metadata = data.metadata or {}
    if metadata.get("complete") is False or metadata.get("is_closed") is False:
        raise TradingError("UNFINISHED_BAR")
    interval = data.kline_interval or metadata.get("interval")
    interval_ms = INTERVAL_MS.get(str(interval or ""))
    if interval_ms is None:
        raise TradingError("BAR_INTERVAL_REQUIRED")
    open_value = metadata.get("open_time_ms")
    if open_value is None:
        raise TradingError("BAR_OPEN_TIME_REQUIRED")
    open_ms = int(open_value)
    close_ms = open_ms + interval_ms
    supplied_close = [
        int(value)
        for value in (
            data.available_at_ms,
            metadata.get("available_at_ms"),
            metadata.get("close_time_ms"),
        )
        if value is not None
    ]
    if not supplied_close:
        raise TradingError("BAR_CLOSE_TIME_REQUIRED")
    if any(value != close_ms for value in supplied_close):
        raise TradingError("BAR_TIMESTAMP_INCONSISTENT")
    if data.kline_close is None:
        raise TradingError("BAR_CLOSE_PRICE_REQUIRED")
    if not data.kline_close.is_finite() or data.kline_close <= 0:
        raise TradingError("INVALID_BAR_CLOSE")
    bar_id = str(metadata.get("bar_id") or "").strip() or (
        f"bar:{data.symbol}:{interval}:{open_ms}"
    )
    return {
        "open_ms": open_ms,
        "close_ms": close_ms,
        "available_ms": close_ms,
        "bar_id": bar_id,
        "interval": interval,
    }


class UnifiedStrategyRunner:
    def __init__(
        self,
        runtime: TradingRuntime,
        *,
        deployment_id: str,
        plugin,
        instruments: dict[str, tuple[str, str]],
        strategy_id: str = "",
        book_id: str = "",
        warmup_events: int = 0,
        warmup_source=None,
        on_block=None,
        allow_ticker_input: bool | None = None,
    ):
        # Deprecated constructor compatibility for callers written against the
        # previous phase-3 boundary. It is not stored and cannot enable ticker
        # decisions: all non-KLINE observations are refused unconditionally.
        if allow_ticker_input not in (None, False):
            raise TradingError("TICKER_INPUT_IS_NOT_CONFIGURABLE")
        if not deployment_id or not instruments:
            raise ValueError("Strategy deployment and instruments are required")
        if warmup_events < 0:
            raise ValueError("Strategy warmup events must be non-negative")
        self.runtime = runtime
        self.deployment_id = deployment_id
        self.strategy_id = strategy_id
        # ``book_id`` is the stable asset-isolation key, never the deployment.
        # It defaults to the frozen ``book:{strategy_id}`` convention.
        self.book_id = book_id or (book_ledger.book_id_for(strategy_id) if strategy_id else "")
        if not self.book_id:
            raise ValueError("STRATEGY_BOOK_ID_REQUIRED")
        book_ledger.validate_strategy_book_id(self.book_id)
        self.plugin = plugin
        self.instruments = dict(instruments)
        self.plugin_warmup = describe_plugin_warmup(plugin)
        self.stateful = self.plugin_warmup.stateful
        self.configured_warmup_events = int(warmup_events)
        # A deployment may demand more warm-up than the plugin declares, but
        # never less: the plugin's own requirement is the lower bound.
        self.warmup_events = (
            max(self.configured_warmup_events, self.plugin_warmup.events) if self.stateful else 0
        )
        self.warmup_source = warmup_source
        self._on_block = on_block
        self._observations = 0
        self._symbol_observations: dict[str, int] = {}
        # Replayed duplicate bars must never inflate the warm-up count, and a
        # late older bar must never be fed after a newer one for that symbol.
        self._seen_observations: set[str] = set()
        self._last_bar_open_ms: dict[str, int] = {}
        self._state = WarmupState.PENDING if self.stateful else WarmupState.WARM
        self._reason = ""
        if self.stateful and self.warmup_events <= 0:
            # A stateful plugin without a warm-up length can never be trusted
            # after a restart; report it instead of trading cold indicators.
            self._state = WarmupState.BLOCKED
            self._reason = "STRATEGY_WARMUP_NOT_CONFIGURED"
        self._lock = asyncio.Lock()

    @property
    def warm(self) -> bool:
        """False after process restart until the plugin has rebuilt indicator state."""
        return self._state is WarmupState.WARM

    @property
    def warmup_remaining(self) -> int:
        if not self.stateful or self._state is WarmupState.WARM:
            return 0
        counts = self._symbol_counts()
        if not counts:
            return self.warmup_events
        return max(max(0, self.warmup_events - count) for count in counts.values())

    def _symbol_counts(self) -> dict[str, int]:
        return {symbol: self._symbol_observations.get(symbol, 0) for symbol in self.instruments}

    def _count(self, symbol: str) -> None:
        self._observations += 1
        self._symbol_observations[symbol] = self._symbol_observations.get(symbol, 0) + 1

    def _instrument_state(self, symbol: str) -> WarmupState:
        """Per-instrument warm-up state used to decide the runner-wide gate."""
        if self._state is WarmupState.BLOCKED:
            return WarmupState.BLOCKED
        if not self.stateful:
            return WarmupState.WARM
        count = self._symbol_observations.get(symbol, 0)
        if self.warmup_events > 0 and count >= self.warmup_events:
            return WarmupState.WARM
        return WarmupState.WARMING_UP if count else WarmupState.PENDING

    @property
    def instrument_warmup(self) -> dict[str, dict[str, Any]]:
        """Per-instrument count and state; every entry must be WARM."""
        return {
            symbol: {
                "state": self._instrument_state(symbol).value,
                "observations": count,
                "required": self.warmup_events,
            }
            for symbol, count in self._symbol_counts().items()
        }

    def _refresh_warm_state(self) -> None:
        """Warm only when every instrument rebuilt its indicator state."""
        if not self.stateful or self._state is WarmupState.BLOCKED:
            return
        counts = self._symbol_counts()
        if counts and all(self._instrument_state(symbol) is WarmupState.WARM for symbol in counts):
            self._state = WarmupState.WARM
            self._reason = ""
        elif any(count > 0 for count in counts.values()):
            self._state = WarmupState.WARMING_UP
        else:
            self._state = WarmupState.PENDING

    @property
    def warmup_state(self) -> WarmupState:
        return self._state

    @property
    def warmup_reason(self) -> str:
        return self._reason

    @property
    def strategy_ready(self) -> bool:
        """True only when the plugin may produce tradable output."""
        return self._state is WarmupState.WARM

    async def _fail(self, reason: str) -> None:
        self._state = WarmupState.BLOCKED
        self._reason = reason
        if self._on_block is not None:
            await self._on_block(reason)

    async def prepare_warmup(self) -> bool:
        """Re-collect the warm-up window and rebuild plugin indicator state.

        Returns True when the plugin is warm. A stateful plugin without a
        configured length, an incomplete history or a failing source blocks
        the runner instead of trading on cold indicators.
        """
        async with self._lock:
            if self._state is WarmupState.BLOCKED:
                return False
            if self._state is WarmupState.WARM:
                return True
            if not self.stateful:
                self._state = WarmupState.WARM
                return True
            if self.warmup_events <= 0:
                await self._fail("STRATEGY_WARMUP_NOT_CONFIGURED")
                return False
            if not self.runtime.strategy_start_allowed:
                raise NotReady("TRADING_RUNTIME_NOT_READY")
            self._state = WarmupState.WARMING_UP
            if self.warmup_source is None:
                # Fall back to live observations; the state stays explicit.
                return False
            try:
                history = await self.warmup_source()
            except Exception as exc:
                await self._fail(f"STRATEGY_WARMUP_HISTORY_FAILED:{exc}")
                return False
            history = list(history or [])
            if not history:
                await self._fail("STRATEGY_WARMUP_HISTORY_EMPTY")
                return False
            now_ms = _runtime_clock_ms(self.runtime)
            validated: list[tuple[dict[str, Any], MarketData]] = []
            seen: set[str] = set()
            for item in history:
                try:
                    window = _validated_closed_bar(item)
                except TradingError as exc:
                    code = str(exc)
                    if code == "NON_BAR_MARKET_DATA":
                        await self._fail("STRATEGY_WARMUP_NON_BAR_DATA")
                    elif code == "UNFINISHED_BAR":
                        await self._fail("STRATEGY_WARMUP_UNFINISHED_BAR")
                    else:
                        await self._fail(f"STRATEGY_WARMUP_INVALID_BAR:{code}")
                    return False
                if window["available_ms"] > now_ms:
                    # A replayed bar that has not closed yet is invisible.
                    await self._fail("STRATEGY_WARMUP_BAR_NOT_AVAILABLE")
                    return False
                key = window["bar_id"]
                if key in seen:
                    # Duplicate/replayed history must not rebuild extra state.
                    continue
                seen.add(key)
                validated.append((window, item))
            validated.sort(key=lambda pair: (pair[1].symbol, pair[0]["open_ms"]))
            # The window must cover every instrument; a partial window would
            # leave one symbol's indicators cold.
            available: dict[str, int] = {}
            for _, item in validated:
                available[item.symbol] = available.get(item.symbol, 0) + 1
            short = {
                symbol: available.get(symbol, 0)
                for symbol in self.instruments
                if available.get(symbol, 0) < self.warmup_events
            }
            if short:
                await self._fail(f"STRATEGY_WARMUP_HISTORY_INCOMPLETE:{short}")
                return False
            try:
                for window, item in validated:
                    await self._feed(item)
                    self._count(item.symbol)
                    self._seen_observations.add(window["bar_id"])
                    self._last_bar_open_ms[item.symbol] = window["open_ms"]
            except Exception as exc:
                await self._fail(f"STRATEGY_WARMUP_FAILED:{exc}")
                return False
            self._refresh_warm_state()
            if self._state is not WarmupState.WARM:
                missing = {
                    symbol: count
                    for symbol, count in self._symbol_counts().items()
                    if count < self.warmup_events
                }
                await self._fail(f"STRATEGY_WARMUP_HISTORY_INCOMPLETE:{missing}")
                return False
            return True

    async def _feed(self, data: MarketData) -> Signal | None:
        """Feed one observation to the plugin; warm-up failures block trading."""
        try:
            result = self.plugin.on_market_data(data)
            if inspect.isawaitable(result):
                result = await result
            return result
        except Exception as exc:
            if self._state is WarmupState.WARM:
                await self._fail(f"STRATEGY_PLUGIN_ERROR:{exc}")
            else:
                await self._fail(f"STRATEGY_WARMUP_FAILED:{exc}")
            raise

    def command_id_for(self, decision_id: str) -> str:
        if not isinstance(decision_id, str) or not decision_id.strip():
            raise TradingError("STABLE_DECISION_ID_REQUIRED")
        return "strategy-" + _hash([self.deployment_id, decision_id])[:40]

    async def account_view(self) -> AccountRiskView:
        if self.runtime.service is None:
            raise NotReady("TRADING_RUNTIME_NOT_READY")
        return await self.runtime.service.risk_view(self.runtime.account_id)

    async def _ready(self) -> AccountRiskView:
        if not self.runtime.strategy_start_allowed or self.runtime.service is None:
            raise NotReady("TRADING_RUNTIME_NOT_READY")
        await self.runtime.require_ownership(self.runtime.account_id)
        view = await self.account_view()
        if not view.ready:
            raise NotReady("STRATEGY_ACCOUNT_NOT_READY: " + ",".join(view.reasons))
        return view

    async def on_market_data(
        self, data: MarketData, *, event_id: str | None = None
    ) -> CommandReceipt | None:
        if data.symbol not in self.instruments:
            raise TradingError("UNKNOWN_STRATEGY_INSTRUMENT")
        if data.timestamp.tzinfo is None:
            raise TradingError("MARKET_TIMESTAMP_MUST_BE_UTC_AWARE")
        if not data.price.is_finite() or data.price <= 0:
            raise TradingError("INVALID_MARKET_PRICE")
        # The new-stack runner admits fully specified closed KLINE only.
        # Non-bar types, missing fields, contradictory timestamps and missing
        # closes are refused before the plugin or any account effect.
        window = _validated_closed_bar(data)
        decision_at_ms = _runtime_clock_ms(self.runtime, data)
        if window["available_ms"] > decision_at_ms:
            # A bar is invisible before it closes. Refuse before the plugin or
            # any account side effect sees it.
            raise TradingError("BAR_NOT_AVAILABLE_YET")
        fingerprint = _hash(
            {
                "symbol": data.symbol,
                "data_type": data.data_type.value,
                "timestamp": data.timestamp.astimezone(timezone.utc).isoformat(),
                "price": data.price,
                "volume": data.volume,
                "open": data.kline_open,
                "high": data.kline_high,
                "low": data.kline_low,
                "close": data.kline_close,
                "metadata": data.metadata,
            }
        )
        decision_id = event_id or data.metadata.get("event_id") or fingerprint
        command_id = self.command_id_for(decision_id)
        trace = f"{decision_id}:{fingerprint}"
        observation_key = window["bar_id"]
        open_ms = window["open_ms"]
        async with self._lock:
            # Accepted decisions survive runner/process restart. Returning the
            # existing command never re-invokes a stateful plugin or venue IO.
            if self.runtime.service is not None:
                state = await self.runtime.store.read(self.runtime.account_id)
                record = state.commands.get(command_id)
                if record is not None:
                    order = state.orders.get(record.cl_ord_id)
                    if order is None or order.command.decision_id != trace:
                        raise Conflict("STRATEGY_EVENT_ID_REUSED_WITH_DIFFERENT_INPUT")
                    return CommandReceipt(
                        command_id=record.command_id,
                        cl_ord_id=record.cl_ord_id,
                        kind=record.kind,
                        status=record.status,
                        order_status=order.status,
                        error=record.error,
                    )
            view = await self._ready()
            if self._state is WarmupState.BLOCKED:
                raise NotReady(self._reason or "STRATEGY_WARMUP_BLOCKED")
            if observation_key and observation_key in self._seen_observations:
                # The same completed bar replayed by a polling feed rebuilds no
                # extra state and never yields a second decision.
                return None
            if open_ms is not None and open_ms < self._last_bar_open_ms.get(data.symbol, -1):
                # Out-of-order older bars are stale; never feed them after a
                # newer observation for the same instrument.
                return None
            # A restarted plugin starts with empty indicator state. Feed it the
            # configured number of observations before any output may trade;
            # the gate is per-runner, so a restart warms up again.
            was_warm = self._state is WarmupState.WARM
            if self._state is WarmupState.PENDING:
                self._state = WarmupState.WARMING_UP
            # Optional inventory input for plugins; it is a detached query of
            # the same ledger, never a runner-maintained position projection.
            account_callback = getattr(self.plugin, "on_account_view", None)
            if account_callback is not None:
                result = account_callback(view)
                if inspect.isawaitable(result):
                    await result
            signal = await self._feed(data)
            self._count(data.symbol)
            if observation_key:
                self._seen_observations.add(observation_key)
            if open_ms is not None:
                self._last_bar_open_ms[data.symbol] = open_ms
            self._refresh_warm_state()
            if not was_warm:
                # Warm-up observations rebuild state only; they never trade.
                return None
            if signal is None:
                return None
            if signal.symbol != data.symbol:
                raise TradingError("STRATEGY_SIGNAL_INSTRUMENT_MISMATCH")
            # The signal was produced by a closed bar (enforced above), so the
            # reference price is the bar close at its close time. The bar
            # identity travels with the command for audit.
            return await self._submit_signal(
                signal,
                decision_id=decision_id,
                reference_price=(data.kline_close if data.kline_close is not None else data.price),
                reference_price_source=PRICE_SOURCE_MARKET_CLOSED_BAR,
                reference_at_ms=window["available_ms"],
                decision_at_ms=decision_at_ms,
                price_reference_id=window["bar_id"],
                trace=trace,
            )

    async def submit_signal(
        self,
        signal: Signal,
        *,
        decision_id: str,
        reference_price: Decimal,
        reference_price_source: str,
        reference_at_ms: int,
        decision_at_ms: int,
        price_reference_id: str = "",
    ) -> CommandReceipt | None:
        """Retry with the same decision ID; changed economic parameters conflict."""
        if self._state is WarmupState.BLOCKED:
            raise NotReady(self._reason or "STRATEGY_WARMUP_BLOCKED")
        if not self.warm:
            raise NotReady("STRATEGY_WARMUP_NOT_COMPLETE")
        return await self._submit_signal(
            signal,
            decision_id=decision_id,
            reference_price=reference_price,
            reference_price_source=reference_price_source,
            reference_at_ms=reference_at_ms,
            decision_at_ms=decision_at_ms,
            price_reference_id=price_reference_id,
            trace=decision_id,
        )

    async def _submit_signal(
        self,
        signal: Signal,
        *,
        decision_id: str,
        reference_price: Decimal,
        reference_price_source: str,
        reference_at_ms: int,
        decision_at_ms: int,
        trace: str,
        price_reference_id: str = "",
    ) -> CommandReceipt | None:
        side = getattr(signal.signal_type, "value", signal.signal_type)
        if side == "NONE":
            return None
        if side not in {"BUY", "SELL"}:
            raise TradingError("UNSUPPORTED_SPOT_INTENT")
        if signal.stop_loss is not None or signal.take_profit is not None:
            raise TradingError("UNSUPPORTED_SPOT_CONDITIONS")
        if signal.symbol not in self.instruments:
            raise TradingError("UNKNOWN_STRATEGY_INSTRUMENT")
        base, quote = self.instruments[signal.symbol]
        # The strategy declares the order type explicitly on the Signal; the
        # metadata dict is context, never an order-spec source.
        spec = build_order_spec(
            symbol=signal.symbol,
            side=side,
            order_type=signal.order_type,
            qty=signal.quantity,
            limit_price=signal.price,
            reference_price=reference_price,
            reference_price_source=reference_price_source,
            reference_at_ms=reference_at_ms,
            decision_at_ms=decision_at_ms,
            time_in_force=signal.time_in_force,
            book_id=self.book_id,
            price_reference_id=price_reference_id,
        )
        command = spec.to_command(
            command_id=self.command_id_for(decision_id),
            account_id=self.runtime.account_id,
            base_asset=base,
            quote_asset=quote,
            deployment_id=self.deployment_id,
            decision_id=trace,
        )
        return await self.runtime.place(command, actor=f"strategy:{self.deployment_id}")
