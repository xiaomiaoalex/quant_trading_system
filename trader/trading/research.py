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
from trader.trading.jobs import freeze_strategy
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
    """Adapts the async StrategyPlugin protocol to the event runtime."""

    def __init__(self, plugin, symbol: str):
        self.plugin = plugin
        self.symbol = symbol

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
        side = getattr(signal_type, "value", str(signal_type))
        return [
            StrategySignal(
                symbol=self.symbol,
                side=str(side),
                qty=Decimal(str(getattr(signal, "quantity", 0) or 0)),
                order_type="MARKET",
                reason=str(getattr(signal, "reason", "") or ""),
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


class ResearchExecutor:
    def __init__(self, store):
        self.store = store

    async def _verify_provenance(self, manifest: dict, datasets: list[dict]) -> None:
        """核验冻结的 provenance 与当前可信关联一致。

        只要 manifest 冻结了某个数据集的 provenance，执行前就必须重新核对；
        对不上说明数据来源在排队期间发生了变化，直接失败。
        """
        frozen = manifest.get("provenance") or {}
        if not frozen:
            return
        getter = getattr(self.store, "get_dataset_link", None)
        for dataset in datasets:
            dataset_id = dataset["dataset_id"]
            expected = frozen.get(dataset_id)
            if not expected:
                continue
            link = await getter(dataset_id) if getter is not None else None
            if not link or link.get("provenance_hash") != expected:
                raise ValueError(f"Frozen provenance mismatch: {dataset_id}")

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

        datasets = []
        for dataset_id in manifest["dataset_ids"]:
            data = await self.store.get_dataset(dataset_id)
            if data is None:
                raise ValueError(f"Dataset not found: {dataset_id}")
            normalized = freeze_dataset(data["identity"], data["bars"])
            content_id = "ds_" + digest({"identity": data["identity"], "bars": data["bars"]})
            if not dataset_id == data.get("dataset_id") == normalized["dataset_id"] == content_id:
                raise ValueError(f"Dataset content hash mismatch: {dataset_id}")
            datasets.append(data)

        strategy_spec = manifest["strategy"]
        # Revalidate immediately before loading installed code. A queued job or
        # retry must not silently run source different from its frozen manifest.
        if not strategy_spec.get("code_hash"):
            raise ValueError("Frozen strategy code_hash is required")
        freeze_strategy(strategy_spec)
        if record.get("manifest_hash") != digest(manifest):
            raise ValueError("Frozen manifest hash mismatch")
        await self._verify_provenance(manifest, datasets)
        params = strategy_spec.get("params", {})
        adapters = {}
        for data in datasets:
            symbol = data["identity"]["symbol"]
            # Each symbol owns an independent plugin instance so indicator state
            # cannot leak between instruments.
            plugin = load_plugin(strategy_spec["module_path"], params)
            initialize = getattr(plugin, "initialize", None)
            if initialize is not None:
                await initialize(params)
            adapters[symbol] = PluginStrategyAdapter(plugin, symbol)
        runtime = EventBacktestRuntime(
            datasets=datasets,
            strategy=MultiSymbolStrategy(adapters),
            initial_cash=Decimal(str(manifest["initial_cash"])),
            fee_bps=Decimal(str(manifest["fee_bps"])),
            slippage_bps=Decimal(str(manifest["slippage_bps"])),
            purpose=purpose,
            config=manifest.get("risk_config", {}),
        )
        result = await runtime.run_async()
        curve = result.equity_curve
        limitations = ["BAR data cannot validate queue priority or market impact."]
        report = {
            "datasets": [
                {
                    "dataset_id": data["dataset_id"],
                    "identity": data["identity"],
                    "quality": data.get("quality", {}),
                }
                for data in datasets
            ],
            "provenance": manifest.get("provenance", {}),
            "strategy": {
                "module_path": strategy_spec["module_path"],
                "params": strategy_spec.get("params", {}),
                "version": strategy_spec.get("version"),
                "code_hash": strategy_spec.get("code_hash"),
            },
            "runtime": {
                "engine": "event_runtime",
                "runtime_version": SPOT_RUNTIME_VERSION,
                "manifest_hash": record["manifest_hash"],
                "runtime_manifest_hash": result.manifest_hash,
            },
            "costs": {
                "initial_cash": decimal_text(result.initial_cash),
                "fee_bps": decimal_text(Decimal(str(manifest["fee_bps"]))),
                "slippage_bps": decimal_text(Decimal(str(manifest["slippage_bps"]))),
                "total_fees": decimal_text(result.fees),
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
                "execution_rejections": result.execution_rejections,
            },
            "limitations": limitations,
        }
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
            "execution_rejections": result.execution_rejections,
            "closed_trades": [decimal_text(value) for value in result.closed_trades],
            "errors": result.errors,
        }
