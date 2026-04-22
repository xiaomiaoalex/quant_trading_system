"""
VectorBT Adapter - 实现 BacktestEnginePort
==========================================
将 VectorBT 向量化回测引擎包装为标准接口。
"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
from decimal import Decimal
from typing import Any, Dict, List, Optional, Sequence

from trader.services.backtesting.ports import (
    BacktestConfig,
    BacktestEnginePort,
    BacktestFeature,
    BacktestResult,
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

    def __init__(self, config: Optional[VectorBTConfig] = None):
        self._config = config or VectorBTConfig()

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

        from trader.services.backtesting.binance_data_provider import BinanceDataProvider
        data_provider = BinanceDataProvider()

        klines = await data_provider.get_klines(
            symbol=config.symbol,
            interval=config.interval,
            start_date=config.start_date,
            end_date=config.end_date,
        )

        close_prices = np.array([float(k.close) for k in klines], dtype=float)

        if hasattr(strategy, "generate_signals"):
            signals = await strategy.generate_signals(klines)
        else:
            signals = await strategy(klines)

        signals = np.asarray(signals)
        if signals.dtype == bool:
            entries = signals.astype(bool)
            exits = ~signals
        else:
            entries = signals > 0
            exits = signals < 0

        commission = float(config.commission_rate)

        pf = vbt.Portfolio.from_signals(
            close=close_prices,
            entries=entries,
            exits=exits,
            freq=self._config.freq,
            fees=commission,
            init_capital=float(config.initial_capital),
            accumulate=True,
        )

        return BacktestResult(
            total_return=Decimal(str(round(pf.total_return(), 6))),
            sharpe_ratio=Decimal(str(round(pf.sharpe_ratio(1.0), 4))),
            max_drawdown=Decimal(str(round(abs(pf.max_drawdown()), 6))),
            win_rate=Decimal(str(round(pf.win_rate(), 4))),
            profit_factor=Decimal(str(round(pf.profit_factor(), 4))),
            num_trades=int(pf.trades.count()),
            final_capital=Decimal(str(round(pf.final_capital(), 2))),
            equity_curve=[],
            trades=self._extract_trades(pf),
            metrics={
                "total_return_pct": float(pf.total_return()) * 100,
                "annualized_return": float(pf.annualized_return()),
                "calmar_ratio": float(pf.calmar_ratio()) if pf.calmar_ratio() else 0.0,
            },
            start_date=config.start_date,
            end_date=config.end_date,
        )

    def _extract_trades(self, pf) -> List[Dict[str, Any]]:
        trades = []
        for i, trade in enumerate(pf.trades):
            if trade is not None:
                trades.append({
                    "trade_id": i,
                    "entry_idx": int(trade.entry_idx),
                    "exit_idx": int(trade.exit_idx),
                    "pnl": float(trade.pnl),
                    "return": float(trade.return_),
                    "status": trade.status.value if hasattr(trade.status, "value") else str(trade.status),
                })
        return trades

    async def run_optimization(
        self,
        config: BacktestConfig,
        strategy: Any,
        param_ranges: Dict[str, Sequence[Any]],
    ) -> OptimizationResult:
        import itertools

        import numpy as np
        from trader.services.backtesting.binance_data_provider import BinanceDataProvider
        data_provider = BinanceDataProvider()

        klines = await data_provider.get_klines(
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
                init_capital=float(config.initial_capital),
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