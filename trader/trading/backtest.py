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

from trader.trading import books as book_ledger
from trader.trading import core as trading_core
from trader.trading.datasets import INTERVAL_MS, decimal_text, digest
from trader.trading.models import (
    SETTLEMENT_BASE,
    AccountState,
    Execution,
    TradingError,
    advance_order_status,
)
from trader.trading.order_spec import (
    PRICE_SOURCE_BACKTEST_CLOSED_BAR,
    PRICE_SOURCE_SIGNAL_LIMIT,
    build_order_spec,
)
from trader.trading.risk import available_balance
from trader.trading.version import SPOT_RUNTIME_VERSION


class RejectedIntent(ValueError):
    pass


@dataclass(frozen=True)
class StrategySignal:
    """One strategy intent for the simulator.

    ``order_type`` is explicit: the simulator never guesses it. ``strategy_id``
    selects the strategy book in a joint run; it may stay empty only when the
    run has exactly one configured book.
    """

    symbol: str
    side: str
    qty: Decimal
    order_type: str
    limit_price: Decimal | None = None
    reason: str = ""
    strategy_id: str = ""
    time_in_force: str = "GTC"


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
    equity_points: list[dict[str, Any]] = field(default_factory=list)
    """带明确估值时间的权益点（at_ms 为该根 K 线的收盘时间）。"""
    books: list[dict[str, Any]] = field(default_factory=list)
    """逐 book 的余额、持仓、预约、可用预算与权益（阶段 4 联合回测读取）。"""
    book_fees: dict[str, Decimal] = field(default_factory=dict)
    """逐 book 的实际成交手续费，与 ``fees`` 汇总守恒。"""
    book_allocations: dict[str, Decimal] = field(default_factory=dict)
    """本次模拟的初始 book 资金划分。"""
    rejected_intents: list[dict[str, Any]] = field(default_factory=list)
    """被拒绝的意图及其策略/book 归属，便于逐策略审计。"""

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
        *,
        strategy_books: dict[str, str] | None = None,
        initial_allocations: dict[str, Decimal] | None = None,
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
        # The simulator declares its books explicitly. A single-strategy run keeps
        # one simulator book; a joint run maps each strategy to its own stable
        # ``book:{strategy_id}`` and the operator states the initial split.
        self.strategy_books = {
            str(key): str(value) for key, value in (strategy_books or {}).items()
        }
        owners: dict[str, str] = {}
        for strategy_id, book_id in self.strategy_books.items():
            if not strategy_id.strip():
                raise ValueError("Strategy id must not be empty")
            if not book_ledger.is_strategy_book(book_id):
                raise ValueError(f"Invalid book id for strategy {strategy_id}: {book_id}")
            previous = owners.get(book_id)
            if previous is not None:
                # Two strategies sharing one book would silently merge their
                # budgets and risk limits. Refuse the configuration before any
                # account, allocation or event exists.
                raise ValueError(
                    f"Duplicate strategy book mapping: {book_id} is assigned to both "
                    f"{previous} and {strategy_id}; each strategy needs its own book"
                )
            owners[book_id] = strategy_id
        self.book_id = "book:backtest"
        self.configured_books = (
            set(self.strategy_books.values()) if self.strategy_books else {self.book_id}
        )
        allocations = {
            str(book_id): Decimal(amount) for book_id, amount in (initial_allocations or {}).items()
        }
        if self.strategy_books:
            if not allocations:
                raise ValueError("A joint backtest needs explicit initial allocations")
            for book_id in allocations:
                if book_id not in self.configured_books:
                    raise ValueError(f"Allocation for an unconfigured book: {book_id}")
        else:
            # Compatibility path: the single simulator book owns the whole
            # initial cash of this one-strategy run.
            allocations = {self.book_id: self.initial_cash}
        if sum(allocations.values(), Decimal(0)) > self.initial_cash:
            raise ValueError("Initial allocations exceed the initial cash")
        self.initial_allocations = allocations
        self.account = AccountState(
            account_id="backtest", status="READY", balances={self.quote_asset: self.initial_cash}
        )
        self.account.positions = {data["identity"]["symbol"]: Decimal(0) for data in self.datasets}
        for identity in self.instruments.values():
            self.account.balances[identity["base_asset"]] = Decimal(0)
        book_ledger.migrate_book_ledger(self.account)
        for book_id, amount in sorted(allocations.items()):
            book_ledger.allocate(self.account, book_id, self.quote_asset, amount)
        self.book_fees = {book_id: Decimal(0) for book_id in self.account.books}
        self._intent_seq = 0
        self._event_ms = 0
        self.average_cost: dict[tuple[str, str], Decimal] = {
            (self.book_id, data["identity"]["symbol"]): Decimal(0) for data in self.datasets
        }
        self.fees = Decimal(0)
        self.fills: list[dict[str, Any]] = []
        self.closed_trades: list[Decimal] = []
        self.rejections: dict[str, int] = {}
        self.rejected_intents: list[dict[str, Any]] = []
        self.execution_rejections: dict[str, int] = {}
        self.risk = {"approved": 0, "rejected": 0}
        self.equity_curve: list[Decimal] = []
        self.equity_points: list[dict[str, Any]] = []
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
                "book_id": book_ledger.UNALLOCATED,
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
            equity_points=[
                {"at_ms": int(point["at_ms"]), "equity": point["equity"]}
                for point in self.equity_points
            ],
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
            books=self._book_snapshots(),
            book_fees=dict(self.book_fees),
            book_allocations=dict(self.initial_allocations),
            rejected_intents=list(self.rejected_intents),
        )

    def _book_snapshots(self) -> list[dict[str, Any]]:
        """Per-book equity and budget, computed by the shared book snapshot.

        The simulator never invents its own valuation: it reuses
        ``books.book_snapshot`` on the same account state the live runtime
        owns, so a joint backtest and a live session report the same numbers.
        The initial allocation and the actual fees are per-book facts of this
        historical run and are added next to the shared snapshot.
        """
        marks = self.account.reconciliation.get("marks", {}) or {}
        snapshots = []
        for book_id in sorted(self.account.books):
            snapshot = book_ledger.book_snapshot(
                self.account,
                self.account.books[book_id],
                marks=marks,
                now_ms=self._event_ms,
                max_mark_age_ms=self.policy.max_mark_age_ms,
            )
            # The research export is a canonical decimal-text document, like
            # every other number in the backtest result.
            for field in ("balances", "held", "available"):
                snapshot[field] = {
                    asset: decimal_text(value) for asset, value in snapshot[field].items()
                }
            snapshot["positions"] = {
                symbol: decimal_text(qty) for symbol, qty in snapshot["positions"].items()
            }
            if snapshot["equity"] is not None:
                snapshot["equity"] = decimal_text(snapshot["equity"])
            snapshot["allocation"] = decimal_text(self.initial_allocations.get(book_id, Decimal(0)))
            snapshot["fees"] = decimal_text(self.book_fees.get(book_id, Decimal(0)))
            snapshots.append(snapshot)
        return snapshots

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
                "strategy_books": dict(sorted(self.strategy_books.items())),
                "initial_allocations": {
                    book_id: decimal_text(amount)
                    for book_id, amount in sorted(self.initial_allocations.items())
                },
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

    def _resolve_book(self, candidate: StrategySignal) -> tuple[str, str]:
        """Resolve the strategy book of one signal.

        A joint run routes every signal to the book of its strategy. A signal
        without a strategy id is only acceptable while exactly one book is
        configured, so an intent can never land in a neighbouring book's budget.
        """
        if candidate.strategy_id:
            book_id = self.strategy_books.get(candidate.strategy_id)
            if book_id is None:
                return "", "UNKNOWN_STRATEGY_BOOK"
            return book_id, ""
        if len(self.configured_books) == 1:
            return next(iter(self.configured_books)), ""
        return "", "STRATEGY_ID_REQUIRED"

    def _admit(
        self, index: int, symbol: str, candidate: StrategySignal, bar: dict[str, Any]
    ) -> None:
        def reject(code: str, resolved_book: str = "") -> None:
            self.rejections[code] = self.rejections.get(code, 0) + 1
            self.risk["rejected"] += 1
            self.rejected_intents.append(
                {
                    "strategy_id": candidate.strategy_id,
                    "book_id": resolved_book,
                    "symbol": candidate.symbol,
                    "side": candidate.side,
                    "qty": candidate.qty,
                    "order_type": candidate.order_type,
                    "code": code,
                }
            )

        if candidate.symbol != symbol:
            reject("UNKNOWN_SYMBOL")
            return
        book_id, book_error = self._resolve_book(candidate)
        if book_error:
            reject(book_error)
            return
        is_limit = candidate.order_type == "LIMIT"
        reference = candidate.limit_price if is_limit else Decimal(str(bar["close"]))
        self._intent_seq += 1
        cl_ord_id = f"bar-{index}-{symbol}-{self._intent_seq}"
        instrument = self.instruments[symbol]
        decision_at_ms = int(bar["open_time_ms"])
        reference_at_ms = int(bar["close_time_ms"])
        # The simulator uses the same order-spec factory as the live path.
        try:
            spec = build_order_spec(
                symbol=symbol,
                side=candidate.side,
                order_type=candidate.order_type,
                qty=candidate.qty,
                limit_price=candidate.limit_price if is_limit else None,
                reference_price=Decimal(str(bar["close"])) if not is_limit else None,
                reference_price_source=PRICE_SOURCE_BACKTEST_CLOSED_BAR,
                reference_at_ms=reference_at_ms,
                decision_at_ms=decision_at_ms,
                time_in_force=candidate.time_in_force,
                book_id=book_id,
                price_source=(
                    PRICE_SOURCE_SIGNAL_LIMIT if is_limit else PRICE_SOURCE_BACKTEST_CLOSED_BAR
                ),
                # The simulator fills exactly the requested base quantity at the
                # simulated market price; it is not a quote-settled venue.
                settlement=SETTLEMENT_BASE,
            )
            command = spec.to_command(
                command_id=cl_ord_id,
                account_id=self.account.account_id,
                base_asset=instrument["base_asset"],
                quote_asset=instrument["quote_asset"],
                deployment_id="backtest",
                decision_id=cl_ord_id,
            )
        except TradingError as exc:
            # An unsupported specification is refused, exactly like a refused
            # admission: the run records the reason and keeps going.
            reject(str(exc), book_id)
            return
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
            reject(code, book_id)
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
                "book_id": book_id,
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
        aggregate = self.account.orders[order["cl_ord_id"]]

        def reject(code: str) -> None:
            self.execution_rejections[code] = self.execution_rejections.get(code, 0) + 1
            self.errors.append(f"{code}:{symbol}")
            # An execution-stage refusal is still an attributed intent: the
            # strategy, book and requested size travel with the reason code so
            # no refused fill disappears into a bare counter.
            self.rejected_intents.append(
                {
                    "strategy_id": order["signal"].strategy_id,
                    "book_id": order["book_id"],
                    "symbol": symbol,
                    "side": order["side"],
                    "qty": qty,
                    "order_type": order["order_type"],
                    "code": code,
                }
            )
            advance_order_status(aggregate, "REJECTED")
            trading_core.release_order_hold(self.account, aggregate)

        hold = self.account.holds[order["cl_ord_id"]]
        available = available_balance(self.account, hold.asset) + hold.amount
        # The owning book must fund the fill with its own budget. The shared
        # book rule never lets a book borrow from the account total or from a
        # neighbouring book, so an adverse fill price that no longer fits the
        # reserved budget is refused instead of overrunning the book.
        book_budget = trading_core.book_budget_available(
            self.account, order["book_id"], hold.asset, order["cl_ord_id"]
        )
        if order["side"] == "BUY":
            if available < notional + fee:
                reject("INSUFFICIENT_CASH")
                return
            if book_budget < notional + fee:
                reject("BOOK_INSUFFICIENT_AVAILABLE_BALANCE")
                return
        else:
            if available < qty:
                reject("INSUFFICIENT_POSITION")
                return
            if book_budget < qty:
                reject("BOOK_INSUFFICIENT_AVAILABLE_BALANCE")
                return

        previous = self.positions.get(symbol, Decimal(0))
        book = self.account.books[order["book_id"]]
        previous_book_position = book.positions.get(symbol, Decimal(0))
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
        cost_key = (order["book_id"], symbol)
        if order["side"] == "BUY":
            total_cost = (
                self.average_cost.get(cost_key, Decimal(0)) * previous_book_position
                + notional
                + fee
            )
            self.average_cost[cost_key] = total_cost / book.positions[symbol]
        else:
            cost_basis = self.average_cost.get(cost_key, Decimal(0)) * qty
            pnl = notional - fee - cost_basis
            self.closed_trades.append(pnl)
            if book.positions[symbol] == 0:
                self.average_cost[cost_key] = Decimal(0)
        self.fees += fee
        self.book_fees[order["book_id"]] = self.book_fees.get(order["book_id"], Decimal(0)) + fee
        self.fills.append(
            {
                "symbol": symbol,
                "side": order["side"],
                "qty": qty,
                "price": price,
                "fee": fee,
                "bar_index": index,
                "book_id": order["book_id"],
                "strategy_id": order["signal"].strategy_id,
                "cl_ord_id": order["cl_ord_id"],
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
        self.equity_points.append({"at_ms": self._event_ms, "equity": self.equity})

    def _win_rate(self) -> Decimal:
        if not self.closed_trades:
            return Decimal(0)
        wins = sum(1 for pnl in self.closed_trades if pnl > 0)
        return Decimal(wins) / Decimal(len(self.closed_trades))
