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
from trader.trading.models import (
    AccountRiskView,
    CommandReceipt,
    Conflict,
    NotReady,
    PlaceOrderCommand,
    TradingError,
    json_value,
)

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


class UnifiedStrategyRunner:
    def __init__(
        self,
        runtime: TradingRuntime,
        *,
        deployment_id: str,
        plugin,
        instruments: dict[str, tuple[str, str]],
        strategy_id: str = "",
        warmup_events: int = 0,
        warmup_source=None,
        on_block=None,
    ):
        if not deployment_id or not instruments:
            raise ValueError("Strategy deployment and instruments are required")
        if warmup_events < 0:
            raise ValueError("Strategy warmup events must be non-negative")
        self.runtime = runtime
        self.deployment_id = deployment_id
        self.strategy_id = strategy_id
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

    def _refresh_warm_state(self) -> None:
        """Warm only when every instrument rebuilt its indicator state."""
        if not self.stateful or self._state is WarmupState.BLOCKED:
            return
        counts = self._symbol_counts()
        if counts and all(count >= self.warmup_events for count in counts.values()):
            self._state = WarmupState.WARM
            self._reason = ""
        elif self._state is WarmupState.PENDING:
            self._state = WarmupState.WARMING_UP

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
            # The window must cover every instrument; a partial window would
            # leave one symbol's indicators cold.
            available: dict[str, int] = {}
            for item in history:
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
                for item in history:
                    await self._feed(item)
                    self._count(item.symbol)
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
        if data.data_type == MarketDataType.KLINE and (
            data.metadata.get("complete") is False or data.metadata.get("is_closed") is False
        ):
            raise TradingError("UNFINISHED_BAR")
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
            self._refresh_warm_state()
            if not was_warm:
                # Warm-up observations rebuild state only; they never trade.
                return None
            if signal is None:
                return None
            if signal.symbol != data.symbol:
                raise TradingError("STRATEGY_SIGNAL_INSTRUMENT_MISMATCH")
            return await self._submit_signal(
                signal, decision_id=decision_id, reference_price=data.price, trace=trace
            )

    async def submit_signal(
        self, signal: Signal, *, decision_id: str, reference_price: Decimal
    ) -> CommandReceipt | None:
        """Retry with the same decision ID; changed economic parameters conflict."""
        if self._state is WarmupState.BLOCKED:
            raise NotReady(self._reason or "STRATEGY_WARMUP_BLOCKED")
        if not self.warm:
            raise NotReady("STRATEGY_WARMUP_NOT_COMPLETE")
        return await self._submit_signal(
            signal, decision_id=decision_id, reference_price=reference_price, trace=decision_id
        )

    async def _submit_signal(
        self, signal: Signal, *, decision_id: str, reference_price: Decimal, trace: str
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
        order_type = signal.metadata.get("order_type", "MARKET")
        price = signal.price if order_type == "LIMIT" else reference_price
        command = PlaceOrderCommand(
            command_id=self.command_id_for(decision_id),
            account_id=self.runtime.account_id,
            symbol=signal.symbol,
            base_asset=base,
            quote_asset=quote,
            side=side,
            qty=Decimal(str(signal.quantity)),
            price=Decimal(str(price)),
            deployment_id=self.deployment_id,
            order_type=order_type,
            time_in_force=signal.metadata.get("time_in_force", "GTC"),
            decision_id=trace,
        )
        return await self.runtime.place(command, actor=f"strategy:{self.deployment_id}")
