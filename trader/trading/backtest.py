"""Deterministic event-driven spot BAR simulator for research.

The runtime consumes only completed bars (visible at ``available_at_ms``),
values the portfolio every bar and fills orders no earlier than the next bar
open. Admission, asset ledger, position and hold calculations use the same pure
spot core as live account transactions. It never reads the machine clock.
"""

from __future__ import annotations

import asyncio
from dataclasses import dataclass, field
from decimal import Decimal
from typing import Any, Callable, Iterable

from trader.trading import core as trading_core
from trader.trading.datasets import INTERVAL_MS, decimal_text, digest
from trader.trading.models import AccountState, Execution, PlaceOrderCommand, TradingError
from trader.trading.risk import available_balance
from trader.trading.version import SPOT_RUNTIME_VERSION


class RejectedIntent(ValueError):
    pass


@dataclass(frozen=True)
class StrategySignal:
    symbol: str
    side: str
    qty: Decimal
    order_type: str = "MARKET"
    limit_price: Decimal | None = None
    reason: str = ""


@dataclass
class BacktestResult:
    dataset_ids: list[str]
    manifest_hash: str
    initial_cash: Decimal
    cash: Decimal
    positions: dict[str, Decimal]
    fees: Decimal
    equity_curve: list[Decimal]
    fills: list[dict[str, Any]]
    risk: dict[str, Any]
    rejections: dict[str, int]
    execution_rejections: dict[str, int]
    closed_trades: list[Decimal]
    win_rate: Decimal
    max_drawdown_amount: Decimal = Decimal("0")
    max_drawdown_pct: Decimal = Decimal("0")
    errors: list[str] = field(default_factory=list)
    admissible: bool = True

    @property
    def total_fees(self) -> Decimal:
        return self.fees

    @property
    def net_value(self) -> Decimal:
        return self.equity_curve[-1] if self.equity_curve else self.initial_cash


def validate_dataset_set(datasets: Iterable[dict]) -> list[dict]:
    datasets = list(datasets)
    if not datasets:
        raise ValueError("At least one dataset is required")
    first = datasets[0]
    first_identity = first.get("identity", {})
    calendar = [(bar["open_time_ms"], bar["close_time_ms"]) for bar in first.get("bars", [])]
    if not calendar:
        raise ValueError("Dataset has no bars")
    symbols = set()
    for data in datasets:
        identity = data.get("identity", {})
        if identity.get("market_type") != "SPOT":
            raise ValueError("Only SPOT data is supported")
        if identity.get("venue") != first_identity.get("venue"):
            raise ValueError("Datasets must share a venue")
        if identity.get("quote_asset") != first_identity.get("quote_asset"):
            raise ValueError("Datasets must share a quote asset")
        if identity.get("interval") != first_identity.get("interval"):
            raise ValueError("Datasets must share an interval")
        symbol = identity.get("symbol")
        if not symbol or symbol in symbols:
            raise ValueError("Duplicate or missing instrument")
        symbols.add(symbol)
        bars = data.get("bars", [])
        if [(bar["open_time_ms"], bar["close_time_ms"]) for bar in bars] != calendar:
            raise ValueError("Datasets must share the same completed-bar calendar")
        interval_ms = INTERVAL_MS[first_identity["interval"]]
        for bar in bars:
            if bar["available_at_ms"] != bar["close_time_ms"]:
                raise ValueError("Unfinished bars are not visible to the runtime")
            if bar["close_time_ms"] - bar["open_time_ms"] != interval_ms:
                raise ValueError("Dataset bars do not match the declared interval")
    return datasets


class StrategyContext:
    def __init__(self, runtime: "EventBacktestRuntime", bar_index: int):
        self._runtime = runtime
        self.bar_index = bar_index

    def position(self, symbol: str) -> Decimal:
        return self._runtime.positions.get(symbol, Decimal(0))

    @property
    def cash(self) -> Decimal:
        return self._runtime.cash

    @property
    def equity(self) -> Decimal:
        return self._runtime.equity


class EventBacktestRuntime:
    def __init__(
        self,
        datasets: Iterable[dict],
        strategy: Callable[[StrategyContext, dict[str, Any]], list[StrategySignal]] | Any,
        initial_cash: Decimal = Decimal("10000"),
        fee_bps: Decimal = Decimal("10"),
        slippage_bps: Decimal = Decimal("0"),
        purpose: str = "research_screening",
        config: dict[str, Any] | None = None,
    ):
        self.datasets = validate_dataset_set(datasets)
        self.strategy = strategy
        self.initial_cash = Decimal(initial_cash)
        self.fee_bps = Decimal(fee_bps)
        self.slippage_bps = Decimal(slippage_bps)
        self.purpose = purpose
        self.config = dict(config or {})
        if self.initial_cash <= 0:
            raise ValueError("Initial cash must be positive")
        if not 0 <= self.fee_bps <= 100 or not 0 <= self.slippage_bps <= 1000:
            raise ValueError("Invalid execution costs")
        self.fee_rate = self.fee_bps / Decimal(10000)
        self.slippage_rate = self.slippage_bps / Decimal(10000)
        self._validate_costs()

        self.policy = trading_core.policy_from_config(self.config, self.fee_rate)
        self.quote_asset = self.datasets[0]["identity"]["quote_asset"]
        if self.quote_asset != "USDT":
            raise ValueError("BAR portfolio reporting requires USDT quote datasets")
        self.instruments = {data["identity"]["symbol"]: data["identity"] for data in self.datasets}
        self.account = AccountState(
            account_id="backtest", status="READY", balances={self.quote_asset: self.initial_cash}
        )
        self.account.positions = {data["identity"]["symbol"]: Decimal(0) for data in self.datasets}
        for identity in self.instruments.values():
            self.account.balances[identity["base_asset"]] = Decimal(0)
        self._intent_seq = 0
        self._event_ms = 0
        self.average_cost: dict[str, Decimal] = {
            data["identity"]["symbol"]: Decimal(0) for data in self.datasets
        }
        self.fees = Decimal(0)
        self.fills: list[dict[str, Any]] = []
        self.closed_trades: list[Decimal] = []
        self.rejections: dict[str, int] = {}
        self.execution_rejections: dict[str, int] = {}
        self.risk = {"approved": 0, "rejected": 0}
        self.equity_curve: list[Decimal] = []
        self.errors: list[str] = []
        self._pending: list[dict[str, Any]] = []
        self._bars = self._calendar()
        self._record_opening_balance()

    @property
    def cash(self) -> Decimal:
        return self.account.balances[self.quote_asset]

    @property
    def positions(self) -> dict[str, Decimal]:
        return self.account.positions

    def _validate_costs(self) -> None:
        if self.purpose == "admission_evidence" and (self.fee_bps <= 0 or self.slippage_bps <= 0):
            raise ValueError("Admission evidence requires fees and slippage")

    def _record_opening_balance(self) -> None:
        """Start the historical ledger from an explicit opening balance.

        The live ledger books the venue's opening holdings the same way, so a
        simulation and a live session expose the same ledger structure.
        """
        first_bar = self._bars[0] if self._bars else {}
        occurred_at_ms = 0
        if first_bar:
            occurred_at_ms = int(next(iter(first_bar.values()))["open_time_ms"])
        self.account.ledger.append(
            {
                "entry_id": f"opening:{self.quote_asset}",
                "execution_key": "",
                "account_id": self.account.account_id,
                "cl_ord_id": "",
                "asset": self.quote_asset,
                "delta": str(self.initial_cash),
                "kind": "opening_balance",
                "occurred_at_ms": occurred_at_ms,
            }
        )

    def _calendar(self) -> list[dict[str, dict[str, Any]]]:
        calendar: list[dict[str, dict[str, Any]]] = []
        by_symbol = {data["identity"]["symbol"]: data["bars"] for data in self.datasets}
        length = len(next(iter(by_symbol.values())))
        for index in range(length):
            calendar.append({symbol: bars[index] for symbol, bars in by_symbol.items()})
        for index in range(1, length):
            first = next(iter(calendar[index].values()))
            if any(
                bar["open_time_ms"] != first["open_time_ms"] for bar in calendar[index].values()
            ):
                raise ValueError("Dataset timestamps are not aligned")
        return calendar

    @property
    def equity(self) -> Decimal:
        value = self.cash
        for symbol, qty in self.positions.items():
            if qty == 0:
                continue
            bar = self._last_close.get(symbol)
            if bar is None:
                raise ValueError("Missing valuation price")
            value += qty * bar
        return value

    _last_close: dict[str, Decimal]

    def run(self) -> BacktestResult:
        self._last_close = {}
        for index, bars in enumerate(self._bars):
            self._market_event(bars, "open", "open_time_ms")
            self._fill_pending(index, bars)
            self._mark(bars)
            for symbol, bar in bars.items():
                if not self._visible(index, bar):
                    continue
                signals = self._on_bar(index, symbol, bar)
                for candidate in signals:
                    self._admit(index, symbol, candidate, bar)
        return self._result()

    async def run_async(self) -> BacktestResult:
        """Same semantics as ``run`` for plugins with async market-data callbacks."""
        self._last_close = {}
        for index, bars in enumerate(self._bars):
            if index % 256 == 0:
                # Synchronous plugins may never suspend. Keep worker lease and
                # cancellation tasks schedulable during long historical runs.
                await asyncio.sleep(0)
            self._market_event(bars, "open", "open_time_ms")
            self._fill_pending(index, bars)
            self._mark(bars)
            for symbol, bar in bars.items():
                if not self._visible(index, bar):
                    continue
                signals = await self._on_bar_async(index, symbol, bar)
                for candidate in signals:
                    self._admit(index, symbol, candidate, bar)
        return self._result()

    def _result(self) -> BacktestResult:
        return BacktestResult(
            dataset_ids=sorted(data["dataset_id"] for data in self.datasets),
            manifest_hash=self.manifest_hash(),
            initial_cash=self.initial_cash,
            cash=self.cash,
            positions=dict(self.positions),
            fees=self.fees,
            equity_curve=list(self.equity_curve),
            fills=list(self.fills),
            risk=dict(self.risk),
            rejections=dict(self.rejections),
            execution_rejections=dict(self.execution_rejections),
            closed_trades=list(self.closed_trades),
            win_rate=self._win_rate(),
            **self._drawdowns(),
            errors=list(self.errors),
            admissible=(
                self.purpose == "admission_evidence"
                and not self.errors
                and not self.execution_rejections
            ),
        )

    def _drawdowns(self) -> dict[str, Decimal]:
        peak: Decimal | None = None
        worst_amount = Decimal(0)
        worst_pct = Decimal(0)
        for value in self.equity_curve:
            peak = value if peak is None or value > peak else peak
            if peak is None or peak <= 0:
                continue
            worst_amount = max(worst_amount, peak - value)
            worst_pct = max(worst_pct, (peak - value) / peak)
        return {"max_drawdown_amount": worst_amount, "max_drawdown_pct": worst_pct}

    def manifest_hash(self) -> str:
        return digest(
            {
                "dataset_ids": sorted(data["dataset_id"] for data in self.datasets),
                "initial_cash": decimal_text(self.initial_cash),
                "fee_bps": decimal_text(self.fee_bps),
                "slippage_bps": decimal_text(self.slippage_bps),
                "runtime_version": SPOT_RUNTIME_VERSION,
                "config": self.config,
            }
        )

    def _visible(self, index: int, bar: dict[str, Any]) -> bool:
        # The bar is usable only once its availability moment is at or before the
        # decision point represented by the next bar open (or the final close).
        if index + 1 < len(self._bars):
            decision_at = next(iter(self._bars[index + 1].values()))["open_time_ms"]
        else:
            decision_at = bar["close_time_ms"]
        return bar["available_at_ms"] <= decision_at

    def _on_bar(self, index: int, symbol: str, bar: dict[str, Any]) -> list[StrategySignal]:
        context = StrategyContext(self, index)
        callback = self.strategy
        if not callable(callback):
            callback = getattr(callback, "on_bar")
        values = callback(context, dict(bar, symbol=symbol))
        if values is None:
            return []
        return list(values)

    async def _on_bar_async(
        self, index: int, symbol: str, bar: dict[str, Any]
    ) -> list[StrategySignal]:
        context = StrategyContext(self, index)
        callback = getattr(self.strategy, "on_bar_async", None) or getattr(
            self.strategy, "on_bar", self.strategy
        )
        result = callback(context, dict(bar, symbol=symbol))
        if hasattr(result, "__await__"):
            result = await result
        if result is None:
            return []
        return list(result)

    def _admit(
        self, index: int, symbol: str, candidate: StrategySignal, bar: dict[str, Any]
    ) -> None:
        def reject(code: str) -> None:
            self.rejections[code] = self.rejections.get(code, 0) + 1
            self.risk["rejected"] += 1

        if candidate.symbol != symbol:
            reject("UNKNOWN_SYMBOL")
            return
        reference = (
            candidate.limit_price if candidate.order_type == "LIMIT" else Decimal(str(bar["close"]))
        )
        self._intent_seq += 1
        cl_ord_id = f"bar-{index}-{symbol}-{self._intent_seq}"
        instrument = self.instruments[symbol]
        command = PlaceOrderCommand(
            command_id=cl_ord_id,
            account_id=self.account.account_id,
            symbol=symbol,
            base_asset=instrument["base_asset"],
            quote_asset=instrument["quote_asset"],
            side=candidate.side,
            qty=candidate.qty,
            price=reference if reference is not None else Decimal(0),
            deployment_id="backtest",
            order_type=candidate.order_type,
            decision_id=cl_ord_id,
        )
        try:
            trading_core.reserve_order(
                self.account, command, cl_ord_id, self.policy, self._event_ms
            )
        except TradingError as exc:
            code = str(exc)
            # Keep legacy report labels while the economic decision is produced
            # by the live risk engine and never implemented separately here.
            if code == "INSUFFICIENT_AVAILABLE_BALANCE":
                code = "INSUFFICIENT_CASH" if candidate.side == "BUY" else "INSUFFICIENT_POSITION"
            reject(code)
            return

        self.risk["approved"] += 1
        self._pending.append(
            {
                "signal": candidate,
                "symbol": symbol,
                "side": candidate.side,
                "qty": candidate.qty,
                "order_type": candidate.order_type,
                "limit_price": candidate.limit_price,
                "reference": reference,
                "cl_ord_id": cl_ord_id,
                "queued_at_index": index,
            }
        )

    def _fill_pending(self, index: int, bars: dict[str, dict[str, Any]]) -> None:
        remaining: list[dict[str, Any]] = []
        for order in self._pending:
            if order["queued_at_index"] >= index:
                remaining.append(order)
                continue
            bar = bars.get(order["symbol"])
            if bar is None:
                remaining.append(order)
                continue
            price = self._fill_price(order, bar)
            if price is None:
                remaining.append(order)
                continue
            self._execute(order, price, index)
        self._pending = remaining

    def _fill_price(self, order: dict[str, Any], bar: dict[str, Any]) -> Decimal | None:
        open_price = Decimal(str(bar["open"]))
        if order["order_type"] == "MARKET":
            if order["side"] == "BUY":
                return open_price * (Decimal(1) + self.slippage_rate)
            return open_price * (Decimal(1) - self.slippage_rate)
        limit = order["limit_price"]
        if order["side"] == "BUY":
            if open_price <= limit:
                return open_price
            if Decimal(str(bar["low"])) <= limit:
                return limit
            return None
        if open_price >= limit:
            return open_price
        if Decimal(str(bar["high"])) >= limit:
            return limit
        return None

    def _execute(self, order: dict[str, Any], price: Decimal, index: int) -> None:
        qty = order["qty"]
        notional = qty * price
        fee = notional * self.fee_rate
        symbol = order["symbol"]

        def reject(code: str) -> None:
            self.execution_rejections[code] = self.execution_rejections.get(code, 0) + 1
            self.errors.append(f"{code}:{symbol}")
            self.account.orders[order["cl_ord_id"]].status = "REJECTED"
            self.account.holds.pop(order["cl_ord_id"], None)

        aggregate = self.account.orders[order["cl_ord_id"]]
        hold = self.account.holds[order["cl_ord_id"]]
        available = available_balance(self.account, hold.asset) + hold.amount
        if order["side"] == "BUY":
            if available < notional + fee:
                reject("INSUFFICIENT_CASH")
                return
        else:
            if available < qty:
                reject("INSUFFICIENT_POSITION")
                return

        previous = self.positions.get(symbol, Decimal(0))
        execution = Execution(
            account_id=self.account.account_id,
            cl_ord_id=order["cl_ord_id"],
            symbol=symbol,
            exec_id=order["cl_ord_id"],
            qty=qty,
            price=price,
            fee=fee,
            fee_asset=aggregate.command.quote_asset,
            occurred_at_ms=self._event_ms,
            venue="bar_simulator",
        )
        trading_core.book_execution(self.account, execution, self.policy)
        if order["side"] == "BUY":
            total_cost = self.average_cost.get(symbol, Decimal(0)) * previous + notional + fee
            self.average_cost[symbol] = total_cost / self.positions[symbol]
        else:
            cost_basis = self.average_cost.get(symbol, Decimal(0)) * qty
            pnl = notional - fee - cost_basis
            self.closed_trades.append(pnl)
            if self.positions[symbol] == 0:
                self.average_cost[symbol] = Decimal(0)
        self.fees += fee
        self.fills.append(
            {
                "symbol": symbol,
                "side": order["side"],
                "qty": qty,
                "price": price,
                "fee": fee,
                "bar_index": index,
            }
        )

    def _market_event(self, bars: dict[str, dict[str, Any]], price_key: str, time_key: str) -> None:
        """Only the open is visible to old fills; close marks precede decisions."""
        self._event_ms = int(next(iter(bars.values()))[time_key])
        marks = {}
        for symbol, bar in bars.items():
            price = Decimal(str(bar[price_key]))
            self._last_close[symbol] = price
            marks[symbol] = {"price": price, "at_ms": self._event_ms}
        self.account.reconciliation.update(
            checked_at_ms=self._event_ms, marks=marks, stream_healthy=True, unresolved=[]
        )

    def _mark(self, bars: dict[str, dict[str, Any]]) -> None:
        self._market_event(bars, "close", "close_time_ms")
        self.equity_curve.append(self.equity)

    def _win_rate(self) -> Decimal:
        if not self.closed_trades:
            return Decimal(0)
        wins = sum(1 for pnl in self.closed_trades if pnl > 0)
        return Decimal(wins) / Decimal(len(self.closed_trades))
