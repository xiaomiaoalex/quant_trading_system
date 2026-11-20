"""Research execution: frozen datasets plus installed strategy plugins.

Only the event-driven runtime may produce admission evidence. VectorBT results
are deliberately rejected before execution.
"""

from __future__ import annotations

import importlib
from datetime import datetime, timezone
from decimal import Decimal
from typing import Any

from trader.core.application.strategy_protocol import MarketData, MarketDataType
from trader.trading.backtest import EventBacktestRuntime, StrategySignal
from trader.trading.datasets import decimal_text, digest, freeze_dataset
from trader.trading.eligibility import link_is_trusted
from trader.trading.jobs import freeze_strategy, normalize_backtest_manifest
from trader.trading.models import TradingError
from trader.trading.version import SPOT_RUNTIME_VERSION


def load_plugin(module_path: str, params: dict[str, Any]):
    if not module_path.startswith("trader.strategies."):
        raise ValueError("Strategy must be an installed trader.strategies module")
    module = importlib.import_module(module_path)
    factory = getattr(module, "create_plugin", None) or getattr(module, "get_plugin", None)
    if factory is None:
        raise ValueError("Strategy module does not expose create_plugin/get_plugin")
    plugin = factory(**params)
    return plugin


class PluginStrategyAdapter:
    """Adapts the async StrategyPlugin protocol to the event runtime.

    The adapter mirrors the live runner's command semantics: the plugin's
    explicit order type is preserved, a LIMIT signal carries its declared
    limit price, and a MARKET signal only provides a reference price that the
    simulator takes from the closed bar. ``strategy_id`` selects the strategy
    book in a joint run.
    """

    def __init__(self, plugin, symbol: str, strategy_id: str = ""):
        self.plugin = plugin
        self.symbol = symbol
        self.strategy_id = strategy_id

    async def on_bar_async(self, context, bar: dict[str, Any]) -> list[StrategySignal]:
        market = MarketData(
            symbol=self.symbol,
            data_type=MarketDataType.KLINE,
            price=Decimal(str(bar["close"])),
            volume=Decimal(str(bar.get("volume", "0"))),
            timestamp=datetime.fromtimestamp(bar["close_time_ms"] / 1000, tz=timezone.utc),
            kline_open=Decimal(str(bar["open"])),
            kline_high=Decimal(str(bar["high"])),
            kline_low=Decimal(str(bar["low"])),
            kline_close=Decimal(str(bar["close"])),
        )
        signal = await self.plugin.on_market_data(market)
        if signal is None:
            return []
        signal_type = getattr(signal, "signal_type", None)
        side = str(getattr(signal_type, "value", signal_type))
        if side == "NONE":
            return []
        order_type = getattr(signal, "order_type", None)
        if order_type is None:
            # The plugin declares the order type; the simulator never defaults
            # it to a market order behind the plugin's back.
            raise TradingError("ORDER_TYPE_REQUIRED")
        order_kind = str(getattr(order_type, "value", order_type)).strip().upper()
        if (
            getattr(signal, "stop_loss", None) is not None
            or getattr(signal, "take_profit", None) is not None
        ):
            # The live runner refuses these spot conditions before admission;
            # a backtest must not silently execute what live would reject.
            raise TradingError("UNSUPPORTED_SPOT_CONDITIONS")
        limit_price = None
        if order_kind == "LIMIT":
            price = getattr(signal, "price", None)
            if price is None or Decimal(str(price)) <= 0:
                raise TradingError("LIMIT_PRICE_REQUIRED")
            limit_price = Decimal(str(price))
        return [
            StrategySignal(
                symbol=self.symbol,
                side=side,
                qty=Decimal(str(getattr(signal, "quantity", 0) or 0)),
                order_type=order_kind,
                limit_price=limit_price,
                reason=str(getattr(signal, "reason", "") or ""),
                strategy_id=self.strategy_id,
                time_in_force=str(getattr(signal, "time_in_force", "GTC") or "GTC"),
            )
        ]


class MultiSymbolStrategy:
    def __init__(self, adapters: dict[str, PluginStrategyAdapter]):
        self.adapters = adapters

    async def on_bar_async(self, context, bar):
        adapter = self.adapters.get(bar.get("symbol"))
        if adapter is None:
            return []
        return await adapter.on_bar_async(context, bar)


class JointStrategy:
    """Run several strategies against the same bar calendar.

    Every signal keeps the strategy id of the plugin that produced it, so the
    runtime routes it to that strategy's own book. Iteration is sorted by
    strategy id to keep identical frozen input byte-for-byte deterministic.
    """

    def __init__(self, strategies: dict[str, MultiSymbolStrategy]):
        self.strategies = dict(sorted(strategies.items()))

    async def on_bar_async(self, context, bar):
        signals: list[StrategySignal] = []
        for strategy_id in self.strategies:
            signals.extend(await self.strategies[strategy_id].on_bar_async(context, bar))
        return signals


class ResearchExecutor:
    def __init__(self, store):
        self.store = store

    async def _verify_provenance(self, manifest: dict, datasets: list[dict]) -> None:
        """核验冻结的 provenance 与当前可信关联一致。

        每个数据集在执行前都必须有冻结的 provenance，并与当前可信关联一致；
        缺失或对不上说明数据来源在排队期间发生了变化或从未可信，直接失败。
        可信状态本身也在此复核：关联若在排队期间失去官方资格（保留同一
        provenance hash），执行必须 fail-closed，而不是沿用旧的可信判定。
        """
        frozen = manifest.get("provenance")
        if not isinstance(frozen, dict) or not frozen:
            raise ValueError("Frozen provenance is required")
        getter = getattr(self.store, "get_dataset_link", None)
        for dataset in datasets:
            dataset_id = dataset["dataset_id"]
            expected = frozen.get(dataset_id)
            if not expected:
                raise ValueError(f"Frozen provenance is required: {dataset_id}")
            link = await getter(dataset_id) if getter is not None else None
            if not link or link.get("provenance_hash") != expected:
                raise ValueError(f"Frozen provenance mismatch: {dataset_id}")
            if not link_is_trusted(link):
                raise ValueError(f"Frozen provenance mismatch: {dataset_id}")

    @staticmethod
    def _strategy_specs(manifest: dict) -> list[dict]:
        if "strategies" in manifest:
            return [dict(item) for item in manifest["strategies"]]
        return [dict(manifest["strategy"])]

    @staticmethod
    def _strategy_reports(manifest: dict) -> list[dict]:
        specs = ResearchExecutor._strategy_specs(manifest)
        reports = []
        for spec in specs:
            reports.append(
                {
                    "strategy_id": spec.get("strategy_id", ""),
                    "book_id": spec.get("book_id", "book:backtest"),
                    "module_path": spec["module_path"],
                    "params": spec.get("params", {}),
                    "version": spec.get("version"),
                    "code_hash": spec.get("code_hash"),
                }
            )
        return reports

    @staticmethod
    def _book_reports(result) -> list[dict[str, Any]]:
        return [
            {
                "book_id": item["book_id"],
                "allocation": item.get("allocation", "0"),
                "equity": item.get("equity"),
                "valuation_complete": bool(item.get("valuation_complete")),
                "balances": item.get("balances", {}),
                "positions": item.get("positions", {}),
                "held": item.get("held", {}),
                "available": item.get("available", {}),
                "fees": item.get("fees", "0"),
                "valuations": item.get("valuations", {}),
            }
            for item in result.books
        ]

    async def _build_adapters(self, spec: dict, datasets: list[dict]):
        params = spec.get("params", {})
        adapters = {}
        for data in datasets:
            symbol = data["identity"]["symbol"]
            # Each symbol owns an independent plugin instance so indicator state
            # cannot leak between instruments.
            plugin = load_plugin(spec["module_path"], params)
            initialize = getattr(plugin, "initialize", None)
            if initialize is not None:
                await initialize(params)
            adapters[symbol] = PluginStrategyAdapter(plugin, symbol, spec.get("strategy_id", ""))
        return MultiSymbolStrategy(adapters)

    async def execute(self, record: dict[str, Any]) -> dict[str, Any]:
        manifest = record["manifest"]
        engine = manifest.get("engine", "event_runtime")
        purpose = manifest.get("purpose", "research_screening")
        if engine != "event_runtime":
            raise ValueError("Only the event runtime may execute frozen backtests")
        if purpose == "admission_evidence" and engine != "event_runtime":
            raise ValueError("VectorBT results cannot be admission evidence")
        if manifest.get("runtime_version") != SPOT_RUNTIME_VERSION:
            raise ValueError("Legacy runtime version; submit a new frozen job manifest")
        for raw_spec in manifest.get("strategies") or [manifest.get("strategy", {})]:
            if not isinstance(raw_spec, dict) or not raw_spec.get("code_hash"):
                raise ValueError("Frozen strategy code_hash is required")

        # The stored document passes through the same normalizer as submission.
        # A mutated manifest (including nested params or book allocations) is
        # refused instead of being executed under the old hash.
        frozen = normalize_backtest_manifest(manifest)
        if frozen.get("runtime_version") != SPOT_RUNTIME_VERSION:
            raise ValueError("Legacy runtime version; submit a new frozen job manifest")
        stored_hash = record.get("manifest_hash")
        if stored_hash != digest(manifest) or stored_hash != digest(frozen):
            raise ValueError("Frozen manifest hash mismatch")

        datasets = []
        for dataset_id in frozen["dataset_ids"]:
            data = await self.store.get_dataset(dataset_id)
            if data is None:
                raise ValueError(f"Dataset not found: {dataset_id}")
            # The dataset id *is* the content hash. Compare it first, so a
            # document that no longer hashes to its id is reported as a content
            # mismatch instead of a structural parse error.
            content_id = "ds_" + digest({"identity": data["identity"], "bars": data["bars"]})
            if dataset_id != content_id or data.get("dataset_id") != content_id:
                raise ValueError(f"Dataset content hash mismatch: {dataset_id}")
            normalized = freeze_dataset(data["identity"], data["bars"])
            if normalized["dataset_id"] != content_id:
                raise ValueError(f"Dataset content hash mismatch: {dataset_id}")
            datasets.append(data)
        from trader.trading.backtest import validate_dataset_set

        validate_dataset_set(datasets)

        strategy_specs = self._strategy_specs(frozen)
        for spec in strategy_specs:
            # Revalidate immediately before loading installed code. A queued job
            # or retry must not silently run source different from its frozen
            # manifest.
            if not spec.get("code_hash"):
                raise ValueError("Frozen strategy code_hash is required")
            freeze_strategy(spec)
        await self._verify_provenance(frozen, datasets)

        if "strategies" in frozen:
            joint = {}
            for spec in strategy_specs:
                joint[spec["strategy_id"]] = await self._build_adapters(spec, datasets)
            runtime = EventBacktestRuntime(
                datasets=datasets,
                strategy=JointStrategy(joint),
                initial_cash=Decimal(str(frozen["initial_cash"])),
                fee_bps=Decimal(str(frozen["fee_bps"])),
                slippage_bps=Decimal(str(frozen["slippage_bps"])),
                purpose=purpose,
                config=frozen.get("risk_config", {}),
                strategy_books={spec["strategy_id"]: spec["book_id"] for spec in strategy_specs},
                initial_allocations={
                    book_id: Decimal(str(amount))
                    for book_id, amount in frozen["book_allocations"].items()
                },
            )
        else:
            runtime = EventBacktestRuntime(
                datasets=datasets,
                strategy=await self._build_adapters(strategy_specs[0], datasets),
                initial_cash=Decimal(str(frozen["initial_cash"])),
                fee_bps=Decimal(str(frozen["fee_bps"])),
                slippage_bps=Decimal(str(frozen["slippage_bps"])),
                purpose=purpose,
                config=frozen.get("risk_config", {}),
            )
        result = await runtime.run_async()
        curve = result.equity_curve
        limitations = ["BAR data cannot validate queue priority or market impact."]
        strategy_reports = self._strategy_reports(frozen)
        book_reports = self._book_reports(result)
        report = {
            "datasets": [
                {
                    "dataset_id": data["dataset_id"],
                    "identity": data["identity"],
                    "quality": data.get("quality", {}),
                    "provenance_hash": frozen.get("provenance", {}).get(data["dataset_id"]),
                }
                for data in datasets
            ],
            "provenance": frozen.get("provenance", {}),
            "strategies": strategy_reports,
            "book_allocations": frozen.get("book_allocations", {}),
            "books": book_reports,
            "runtime": {
                "engine": "event_runtime",
                "runtime_version": SPOT_RUNTIME_VERSION,
                "manifest_hash": record["manifest_hash"],
                "runtime_manifest_hash": result.manifest_hash,
            },
            "costs": {
                "initial_cash": decimal_text(result.initial_cash),
                "fee_bps": decimal_text(Decimal(str(frozen["fee_bps"]))),
                "slippage_bps": decimal_text(Decimal(str(frozen["slippage_bps"]))),
                "total_fees": decimal_text(result.fees),
                "book_fees": {
                    book_id: decimal_text(value)
                    for book_id, value in sorted(result.book_fees.items())
                },
            },
            "performance": {
                "net_value": decimal_text(result.net_value),
                "total_return": decimal_text(
                    (result.net_value - result.initial_cash) / result.initial_cash
                ),
                "max_drawdown_pct": decimal_text(result.max_drawdown_pct),
                "max_drawdown_amount": decimal_text(result.max_drawdown_amount),
                "win_rate": decimal_text(result.win_rate),
            },
            "executions": {
                "fill_count": len(result.fills),
                "rejections": result.rejections,
                "rejected_intents": [
                    {**item, "qty": decimal_text(item["qty"])} for item in result.rejected_intents
                ],
                "execution_rejections": result.execution_rejections,
            },
            "limitations": limitations,
        }
        if len(strategy_reports) == 1:
            # Keep the single-strategy report shape for existing consumers.
            report["strategy"] = strategy_reports[0]
        return {
            "engine": "event_runtime",
            "runtime_version": SPOT_RUNTIME_VERSION,
            "purpose": purpose,
            "admissible": result.admissible,
            "manifest_hash": record["manifest_hash"],
            "runtime_manifest_hash": result.manifest_hash,
            "limitations": limitations,
            "report": report,
            "dataset_ids": result.dataset_ids,
            "dataset_provenance": frozen.get("provenance", {}),
            "strategies": strategy_reports,
            "book_allocations": frozen.get("book_allocations", {}),
            "books": book_reports,
            "book_fees": {
                book_id: decimal_text(value) for book_id, value in sorted(result.book_fees.items())
            },
            "initial_cash": decimal_text(result.initial_cash),
            "cash": decimal_text(result.cash),
            "net_value": decimal_text(result.net_value),
            "total_return": decimal_text(
                (result.net_value - result.initial_cash) / result.initial_cash
            ),
            "total_fees": decimal_text(result.fees),
            "win_rate": decimal_text(result.win_rate),
            "max_drawdown": decimal_text(result.max_drawdown_pct),
            "max_drawdown_pct": decimal_text(result.max_drawdown_pct),
            "max_drawdown_amount": decimal_text(result.max_drawdown_amount),
            "equity_curve": [decimal_text(value) for value in curve],
            "equity_points": [
                {"at_ms": int(point["at_ms"]), "equity": decimal_text(point["equity"])}
                for point in result.equity_points
            ],
            "positions": {
                symbol: decimal_text(value) for symbol, value in result.positions.items()
            },
            "fills": [
                {
                    key: (decimal_text(value) if isinstance(value, Decimal) else value)
                    for key, value in fill.items()
                }
                for fill in result.fills
            ],
            "risk": result.risk,
            "rejections": result.rejections,
            "rejected_intents": [
                {**item, "qty": decimal_text(item["qty"])} for item in result.rejected_intents
            ],
            "execution_rejections": result.execution_rejections,
            "closed_trades": [decimal_text(value) for value in result.closed_trades],
            "errors": result.errors,
        }
