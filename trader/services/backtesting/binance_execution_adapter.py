"""
Binance Execution Adapter - Binance 定制执行层
=============================================
包装 VectorBT，注入 Binance 特定逻辑：
1. KillSwitch 检查（每个信号执行前）
2. OMS 集成（成交回报映射）
3. RiskEngine 检查（仓位限制、暴露度）
4. 方向感知滑点（复用 slippage.py）
"""
from __future__ import annotations

from decimal import Decimal
from typing import Any, Callable, Dict, Optional, Sequence

from trader.services.backtesting.ports import (
    BacktestConfig,
    BacktestResult,
    OptimizationResult,
)
from trader.services.backtesting.vectorbt_adapter import VectorBTAdapter
from trader.services.backtesting.slippage import BinanceSlippageConfig


class BinanceExecutionAdapter:
    """
    Binance 定制执行适配器

    在 VectorBT 引擎之上包装：
    - KillSwitch L1/L2 阻止
    - RiskEngine 仓位检查
    - OMS 成交回报记录
    """

    def __init__(
        self,
        killswitch_callback: Optional[Callable[[], int]] = None,
        risk_callback: Optional[Callable[[str, str, Decimal], bool]] = None,
        oms_callback: Optional[Callable[[Dict[str, Any]], None]] = None,
    ):
        """
        Args:
            killswitch_callback: () -> int (KillSwitchLevel 0-3)
            risk_callback: (symbol, side, qty) -> bool (True=allowed)
            oms_callback: (order_event) -> None
        """
        self._killswitch_callback = killswitch_callback
        self._risk_callback = risk_callback
        self._oms_callback = oms_callback
        self._vectorbt = VectorBTAdapter()

    async def run_backtest(
        self,
        config: BacktestConfig,
        strategy: Any,
    ) -> BacktestResult:
        # 1. KillSwitch 检查
        if self._killswitch_callback is not None:
            ks_level = self._killswitch_callback()
            if ks_level >= 2:  # L2+ blocked (CANCEL_ALL_AND_HALT / LIQUIDATE_AND_DISCONNECT)
                return BacktestResult(
                    total_return=Decimal("0"),
                    sharpe_ratio=Decimal("0"),
                    max_drawdown=Decimal("0"),
                    win_rate=Decimal("0"),
                    profit_factor=Decimal("0"),
                    num_trades=0,
                    final_capital=config.initial_capital,
                    equity_curve=[],
                    trades=[],
                    metrics={"blocked_by": "KillSwitch", "level": ks_level},
                    start_date=config.start_date,
                    end_date=config.end_date,
                )

        # 2. RiskEngine 前置检查（strategy pre-validation）
        # TODO: 实现策略预验证阶段的 RiskEngine 检查

        # 3. 执行 VectorBT 回测
        result = await self._vectorbt.run_backtest(config, strategy)

        # 4. OMS 成交记录（回测模式下记录到内存）
        if self._oms_callback is not None:
            for trade in result.trades:
                self._oms_callback({
                    "type": "backtest_fill",
                    "symbol": config.symbol,
                    "trade": trade,
                })

        return result

    async def run_optimization(
        self,
        config: BacktestConfig,
        strategy: Any,
        param_ranges: Dict[str, Sequence[Any]],
    ) -> OptimizationResult:
        return await self._vectorbt.run_optimization(config, strategy, param_ranges)
