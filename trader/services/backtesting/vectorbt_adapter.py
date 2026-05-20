"""
VectorBT Adapter - 实现 BacktestEnginePort
==========================================
将 VectorBT 向量化回测引擎包装为标准接口。
"""

from __future__ import annotations

import math
from dataclasses import dataclass
from datetime import datetime, timezone
from decimal import Decimal
from typing import Any, Dict, List, Optional, Sequence

from trader.services.backtesting.ports import (
    BacktestConfig,
    BacktestEnginePort,
    BacktestFeature,
    BacktestResult,
    DataProviderPort,
    FrameworkType,
    OptimizationResult,
)


@dataclass
class VectorBTConfig:
    freq: str = "1h"
    direction_aware_slippage: bool = True
    include_commission: bool = True


class VectorBTAdapter:
    """VectorBT 回测引擎适配器，实现 BacktestEnginePort。"""

    def __init__(
        self,
        config: Optional[VectorBTConfig] = None,
        data_provider: DataProviderPort | None = None,
    ):
        self._config = config or VectorBTConfig()
        self._data_provider = data_provider

    @property
    def framework_type(self) -> FrameworkType:
        return FrameworkType.VECTORBT

    def get_supported_features(self) -> List[BacktestFeature]:
        return [
            BacktestFeature.PARAMETER_OPTIMIZATION,
            BacktestFeature.SLIPPAGE_MODEL,
            BacktestFeature.COMMISSION_MODEL,
        ]

    async def run_backtest(
        self,
        config: BacktestConfig,
        strategy: Any,
    ) -> BacktestResult:
        import numpy as np
        import vectorbt as vbt

        klines = await self._get_data_provider().get_klines(
            symbol=config.symbol,
            interval=config.interval,
            start_date=config.start_date,
            end_date=config.end_date,
        )
        if not klines:
            raise ValueError(f"No OHLCV data available for {config.symbol}")

        close_prices = np.array([float(k.close) for k in klines], dtype=float)

        if hasattr(strategy, "generate_signals"):
            signals = await strategy.generate_signals(klines)
        else:
            signals = await strategy(klines)

        signals = np.asarray(signals)
        if len(signals) != len(klines):
            raise ValueError(
                f"Strategy generated {len(signals)} signals for {len(klines)} OHLCV bars"
            )
        if signals.dtype == bool:
            entries = signals.astype(bool)
            exits = ~signals
        else:
            entries = signals > 0
            exits = signals < 0

        commission = float(config.commission_rate)
        slippage = float(config.slippage_rate)

        pf = vbt.Portfolio.from_signals(
            close=close_prices,
            entries=entries,
            exits=exits,
            freq=self._config.freq,
            fees=commission,
            slippage=slippage,
            init_cash=float(config.initial_capital),
            accumulate=True,
        )

        total_return = self._call_metric(pf, "total_return")
        sharpe_ratio = self._call_metric(pf, "sharpe_ratio")
        max_drawdown = abs(self._call_metric(pf, "max_drawdown"))
        annualized_return = self._call_metric(pf, "annualized_return")
        calmar_ratio = self._call_metric(pf, "calmar_ratio")
        win_rate = self._call_trade_metric(pf, "win_rate")
        profit_factor = self._call_trade_metric(pf, "profit_factor")
        final_capital = self._call_metric(
            pf,
            "final_value",
            fallback=self._call_metric(
                pf,
                "final_capital",
                fallback=float(config.initial_capital),
            ),
        )

        return BacktestResult(
            total_return=Decimal(str(round(total_return, 6))),
            sharpe_ratio=Decimal(str(round(sharpe_ratio, 4))),
            max_drawdown=Decimal(str(round(max_drawdown, 6))),
            win_rate=Decimal(str(round(win_rate, 4))),
            profit_factor=Decimal(str(round(profit_factor, 4))),
            num_trades=int(pf.trades.count()),
            final_capital=Decimal(str(round(final_capital, 2))),
            equity_curve=self._extract_equity_curve(pf, klines),
            trades=self._extract_trades(pf, klines, config.symbol),
            metrics={
                "framework": "vectorbt",
                "total_return_pct": total_return * 100,
                "annualized_return": annualized_return,
                "calmar_ratio": calmar_ratio,
                "commission_rate": commission,
                "slippage_rate": slippage,
            },
            start_date=config.start_date,
            end_date=config.end_date,
        )

    def _call_metric(self, pf: Any, name: str, fallback: float = 0.0) -> float:
        method = getattr(pf, name, None)
        if not callable(method):
            return self._safe_float(fallback)
        try:
            return self._safe_float(method(), fallback=fallback)
        except TypeError:
            try:
                return self._safe_float(method(1.0), fallback=fallback)
            except Exception:
                return self._safe_float(fallback)
        except Exception:
            return self._safe_float(fallback)

    def _call_trade_metric(self, pf: Any, name: str, fallback: float = 0.0) -> float:
        trades = getattr(pf, "trades", None)
        method = getattr(trades, name, None)
        if callable(method):
            try:
                return self._safe_float(method(), fallback=fallback)
            except Exception:
                pass
        return self._call_metric(pf, name, fallback=fallback)

    def _safe_float(self, value: Any, fallback: float = 0.0) -> float:
        try:
            number = float(value)
        except (TypeError, ValueError):
            return fallback
        if math.isnan(number) or math.isinf(number):
            return fallback
        return number

    def _extract_equity_curve(self, pf: Any, klines: Sequence[Any]) -> List[Dict[str, Any]]:
        value_method = getattr(pf, "value", None)
        if not callable(value_method):
            return []

        try:
            values = value_method()
            if hasattr(values, "to_list"):
                equity_values = values.to_list()
            elif hasattr(values, "tolist"):
                equity_values = values.tolist()
            else:
                equity_values = list(values)
        except Exception:
            return []

        curve: List[Dict[str, Any]] = []
        for idx, equity in enumerate(equity_values):
            if idx >= len(klines):
                break
            timestamp = klines[idx].timestamp
            curve.append(
                {
                    "timestamp": int(timestamp.timestamp() * 1000),
                    "equity": self._safe_float(equity),
                }
            )
        return curve

    def _extract_trades(
        self,
        pf: Any,
        klines: Sequence[Any],
        symbol: str,
    ) -> List[Dict[str, Any]]:
        readable = getattr(getattr(pf, "trades", None), "records_readable", None)
        if callable(readable):
            readable = readable()
        if readable is not None and hasattr(readable, "to_dict"):
            return self._extract_readable_trades(readable.to_dict("records"), klines, symbol)

        trades = []
        for i, trade in enumerate(getattr(pf, "trades", [])):
            if trade is not None:
                status = getattr(trade, "status", "")
                trades.append(
                    {
                        "trade_id": str(i),
                        "symbol": symbol,
                        "side": "BUY",
                        "entry_idx": int(getattr(trade, "entry_idx", 0)),
                        "exit_idx": int(getattr(trade, "exit_idx", 0)),
                        "price": self._safe_float(getattr(trade, "entry_price", 0.0)),
                        "quantity": self._safe_float(getattr(trade, "size", 0.0)),
                        "pnl": self._safe_float(getattr(trade, "pnl", 0.0)),
                        "return": self._safe_float(getattr(trade, "return_", 0.0)),
                        "status": (status.value if hasattr(status, "value") else str(status)),
                        "timestamp": self._format_vectorbt_timestamp(
                            getattr(trade, "entry_idx", 0),
                            klines,
                        ),
                    }
                )
        return trades

    def _extract_readable_trades(
        self,
        rows: Sequence[Dict[str, Any]],
        klines: Sequence[Any],
        symbol: str,
    ) -> List[Dict[str, Any]]:
        trades: List[Dict[str, Any]] = []
        for i, row in enumerate(rows):
            direction = str(row.get("Direction", "")).upper()
            side = "BUY" if "LONG" in direction or not direction else "SELL"
            timestamp = row.get("Entry Timestamp", row.get("Exit Timestamp", i))
            trades.append(
                {
                    "trade_id": str(row.get("Exit Trade Id", row.get("Trade Id", i))),
                    "symbol": symbol,
                    "side": side,
                    "price": self._safe_float(
                        row.get("Avg Entry Price", row.get("Entry Price", row.get("Price", 0.0)))
                    ),
                    "quantity": self._safe_float(row.get("Size", row.get("Quantity", 0.0))),
                    "timestamp": self._format_vectorbt_timestamp(timestamp, klines),
                    "pnl": self._safe_float(row.get("PnL", row.get("Pnl", 0.0))),
                    "return": self._safe_float(row.get("Return", 0.0)),
                    "status": str(row.get("Status", "")),
                }
            )
        return trades

    def _format_vectorbt_timestamp(self, value: Any, klines: Sequence[Any]) -> str:
        if isinstance(value, (int, float)):
            idx = int(value)
            if 0 <= idx < len(klines):
                return klines[idx].timestamp.isoformat()
        if isinstance(value, datetime):
            return value.isoformat()
        to_pydatetime = getattr(value, "to_pydatetime", None)
        if callable(to_pydatetime):
            try:
                return to_pydatetime().isoformat()
            except Exception:
                pass
        return str(value)

    async def run_optimization(
        self,
        config: BacktestConfig,
        strategy: Any,
        param_ranges: Dict[str, Sequence[Any]],
    ) -> OptimizationResult:
        import itertools

        import numpy as np

        klines = await self._get_data_provider().get_klines(
            symbol=config.symbol,
            interval=config.interval,
            start_date=config.start_date,
            end_date=config.end_date,
        )
        close_prices = np.array([float(k.close) for k in klines], dtype=float)

        param_combinations = list(itertools.product(*param_ranges.values()))
        param_names = list(param_ranges.keys())

        results = []
        best_metrics = None
        best_params = None

        for combo in param_combinations:
            params = dict(zip(param_names, combo))
            signals = await strategy.generate_signals_with_params(klines, params)
            signals = np.asarray(signals)
            entries = signals > 0
            exits = signals < 0

            import vectorbt as vbt

            pf = vbt.Portfolio.from_signals(
                close=close_prices,
                entries=entries,
                exits=exits,
                freq=self._config.freq,
                fees=float(config.commission_rate),
                slippage=float(config.slippage_rate),
                init_cash=float(config.initial_capital),
            )

            result = {
                "params": params,
                "total_return": float(pf.total_return()),
                "sharpe_ratio": float(pf.sharpe_ratio(1.0)),
                "max_drawdown": abs(float(pf.max_drawdown())),
                "num_trades": int(pf.trades.count()),
            }
            results.append(result)

            if best_metrics is None or result["sharpe_ratio"] > best_metrics["sharpe_ratio"]:
                best_metrics = result
                best_params = params

        return OptimizationResult(
            best_params=best_params,
            best_metrics=BacktestResult(
                total_return=Decimal(str(best_metrics["total_return"])),
                sharpe_ratio=Decimal(str(best_metrics["sharpe_ratio"])),
                max_drawdown=Decimal(str(best_metrics["max_drawdown"])),
                win_rate=Decimal("0"),
                profit_factor=Decimal("0"),
                num_trades=best_metrics["num_trades"],
                final_capital=Decimal("0"),
            ),
            all_results=results,
            optimization_time=0.0,
        )

    def _get_data_provider(self) -> DataProviderPort:
        if self._data_provider is None:
            from trader.services.backtesting.binance_data_provider import BinanceDataProvider

            self._data_provider = BinanceDataProvider()
        return self._data_provider
