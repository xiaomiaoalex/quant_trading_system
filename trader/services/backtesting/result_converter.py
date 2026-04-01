"""
QuantConnect Lean Backtest Result Converter
============================================

Converts QuantConnect Lean backtest results to internal BacktestReport format.

QuantConnect Lean output format:
- TotalProfit: float
- Statistics: dict (SharpeRatio, MaxDrawdown, WinRate, etc.)
- TradeList: list of trades
- EquityCurve: list of equity points

Architecture:
    Lean JSON Output -> QuantConnectStatistics/Trade/EquityPoint -> BacktestResult -> BacktestReport
"""
from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timezone
from decimal import Decimal
from typing import Any, Dict, List, Optional, Sequence
import math
import statistics


@dataclass(slots=True)
class EquityPoint:
    """Single point in equity curve."""
    timestamp: datetime
    equity: Decimal

    def __post_init__(self):
        if isinstance(self.timestamp, (int, float)):
            object.__setattr__(self, 'timestamp', datetime.fromtimestamp(self.timestamp, tz=timezone.utc))
        elif isinstance(self.timestamp, str):
            object.__setattr__(self, 'timestamp', datetime.fromisoformat(self.timestamp.replace("Z", "+00:00")))
        if isinstance(self.equity, (int, float)):
            object.__setattr__(self, 'equity', Decimal(str(self.equity)))

    def to_dict(self) -> Dict[str, Any]:
        return {
            "timestamp": self.timestamp.isoformat(),
            "equity": str(self.equity),
        }

    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> EquityPoint:
        return cls(
            timestamp=data["timestamp"],
            equity=data["equity"],
        )


@dataclass(slots=True)
class QuantConnectTrade:
    """Raw trade from QuantConnect Lean."""
    id: str
    symbol: str
    quantity: Decimal
    price: Decimal
    direction: str
    entry_time: datetime
    exit_time: Optional[datetime]
    entry_price: Decimal
    exit_price: Optional[Decimal]
    pnl: Decimal
    pnl_percent: Decimal

    def __post_init__(self):
        if isinstance(self.quantity, (int, float)):
            object.__setattr__(self, 'quantity', Decimal(str(self.quantity)))
        if isinstance(self.price, (int, float)):
            object.__setattr__(self, 'price', Decimal(str(self.price)))
        if isinstance(self.entry_price, (int, float)):
            object.__setattr__(self, 'entry_price', Decimal(str(self.entry_price)))
        if isinstance(self.exit_price, (int, float)) and self.exit_price is not None:
            object.__setattr__(self, 'exit_price', Decimal(str(self.exit_price)))
        if isinstance(self.pnl, (int, float)):
            object.__setattr__(self, 'pnl', Decimal(str(self.pnl)))
        if isinstance(self.pnl_percent, (int, float)):
            object.__setattr__(self, 'pnl_percent', Decimal(str(self.pnl_percent)))
        if isinstance(self.entry_time, (int, float)):
            object.__setattr__(self, 'entry_time', datetime.fromtimestamp(self.entry_time, tz=timezone.utc))
        elif isinstance(self.entry_time, str):
            object.__setattr__(self, 'entry_time', datetime.fromisoformat(self.entry_time.replace("Z", "+00:00")))
        if isinstance(self.exit_time, (int, float)) and self.exit_time is not None:
            object.__setattr__(self, 'exit_time', datetime.fromtimestamp(self.exit_time, tz=timezone.utc))
        elif isinstance(self.exit_time, str) and self.exit_time:
            object.__setattr__(self, 'exit_time', datetime.fromisoformat(self.exit_time.replace("Z", "+00:00")))

    def to_dict(self) -> Dict[str, Any]:
        return {
            "id": self.id,
            "symbol": self.symbol,
            "quantity": str(self.quantity),
            "price": str(self.price),
            "direction": self.direction,
            "entry_time": self.entry_time.isoformat() if self.entry_time else None,
            "exit_time": self.exit_time.isoformat() if self.exit_time else None,
            "entry_price": str(self.entry_price),
            "exit_price": str(self.exit_price) if self.exit_price else None,
            "pnl": str(self.pnl),
            "pnl_percent": str(self.pnl_percent),
        }

    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> QuantConnectTrade:
        return cls(
            id=data.get("id", ""),
            symbol=data.get("symbol", ""),
            quantity=data.get("quantity", 0),
            price=data.get("price", 0),
            direction=data.get("direction", "UNKNOWN"),
            entry_time=data.get("entry_time", datetime.now(timezone.utc)),
            exit_time=data.get("exit_time"),
            entry_price=data.get("entry_price", 0),
            exit_price=data.get("exit_price"),
            pnl=data.get("pnl", 0),
            pnl_percent=data.get("pnl_percent", 0),
        )


@dataclass(slots=True)
class QuantConnectStatistics:
    """Raw statistics from QuantConnect Lean Statistics dictionary."""
    total_trades: int
    winning_trades: int
    losing_trades: int
    total_profit: Decimal
    sharpe_ratio: Decimal
    sortino_ratio: Decimal
    calmar_ratio: Decimal
    max_drawdown: Decimal
    max_drawdown_percent: Decimal
    win_rate: Decimal
    profit_factor: Decimal
    average_trade_duration: Decimal
    var_95: Decimal
    turnover: Decimal
    commission: Decimal
    initial_capital: Decimal
    final_capital: Decimal

    def __post_init__(self):
        if isinstance(self.total_profit, (int, float)):
            object.__setattr__(self, 'total_profit', Decimal(str(self.total_profit)))
        if isinstance(self.sharpe_ratio, (int, float)):
            object.__setattr__(self, 'sharpe_ratio', Decimal(str(self.sharpe_ratio)))
        if isinstance(self.sortino_ratio, (int, float)):
            object.__setattr__(self, 'sortino_ratio', Decimal(str(self.sortino_ratio)))
        if isinstance(self.calmar_ratio, (int, float)):
            object.__setattr__(self, 'calmar_ratio', Decimal(str(self.calmar_ratio)))
        if isinstance(self.max_drawdown, (int, float)):
            object.__setattr__(self, 'max_drawdown', Decimal(str(self.max_drawdown)))
        if isinstance(self.max_drawdown_percent, (int, float)):
            object.__setattr__(self, 'max_drawdown_percent', Decimal(str(self.max_drawdown_percent)))
        if isinstance(self.win_rate, (int, float)):
            object.__setattr__(self, 'win_rate', Decimal(str(self.win_rate)))
        if isinstance(self.profit_factor, (int, float)):
            object.__setattr__(self, 'profit_factor', Decimal(str(self.profit_factor)))
        if isinstance(self.average_trade_duration, (int, float)):
            object.__setattr__(self, 'average_trade_duration', Decimal(str(self.average_trade_duration)))
        if isinstance(self.var_95, (int, float)):
            object.__setattr__(self, 'var_95', Decimal(str(self.var_95)))
        if isinstance(self.turnover, (int, float)):
            object.__setattr__(self, 'turnover', Decimal(str(self.turnover)))
        if isinstance(self.commission, (int, float)):
            object.__setattr__(self, 'commission', Decimal(str(self.commission)))
        if isinstance(self.initial_capital, (int, float)):
            object.__setattr__(self, 'initial_capital', Decimal(str(self.initial_capital)))
        if isinstance(self.final_capital, (int, float)):
            object.__setattr__(self, 'final_capital', Decimal(str(self.final_capital)))

    @classmethod
    def from_lean_statistics(cls, stats: Dict[str, Any]) -> QuantConnectStatistics:
        """Create from QuantConnect Lean Statistics dictionary."""
        def get_decimal(key: str, default: float = 0.0) -> Decimal:
            value = stats.get(key, default)
            if isinstance(value, str):
                value = float(value.replace("%", "")) if "%" in value else float(value)
            return Decimal(str(value))

        def get_int(key: str, default: int = 0) -> int:
            value = stats.get(key, default)
            if isinstance(value, str):
                value = int(value.replace(",", ""))
            return int(value)

        return cls(
            total_trades=get_int("Number of Trades", 0),
            winning_trades=get_int("Winning Trades", 0),
            losing_trades=get_int("Losing Trades", 0),
            total_profit=get_decimal("Total Profit", 0.0),
            sharpe_ratio=get_decimal("Sharpe Ratio", 0.0),
            sortino_ratio=get_decimal("Sortino Ratio", 0.0),
            calmar_ratio=get_decimal("Calmar Ratio", 0.0),
            max_drawdown=get_decimal("Maximum Drawdown", 0.0),
            max_drawdown_percent=get_decimal("Max Drawdown", 0.0),
            win_rate=get_decimal("Win Rate", 0.0),
            profit_factor=get_decimal("Profit Factor", 0.0),
            average_trade_duration=get_decimal("Average Trade Duration", 0.0),
            var_95=get_decimal("Value at Risk (VaR) 95%", 0.0),
            turnover=get_decimal("Turnover", 0.0),
            commission=get_decimal("Commission", 0.0),
            initial_capital=get_decimal("Initial Capital", 100000.0),
            final_capital=get_decimal("Final Capital", 100000.0),
        )


@dataclass(slots=True)
class ConversionResult:
    """Result of a conversion operation with any warnings or errors."""
    success: bool
    backtest_result: Optional[Any] = None
    warnings: List[str] = field(default_factory=list)
    errors: List[str] = field(default_factory=list)


class BacktestResultConverter:
    """
    Converts QuantConnect Lean backtest results to internal BacktestReport format.

    Supported conversions:
    - TotalProfit → total_pnl
    - Statistics → sharpe_ratio, max_drawdown, win_rate, etc.
    - TradeList → trades list
    - EquityCurve → equity_curve list

    Metrics calculated:
    - Sharpe Ratio (annualized)
    - Sortino Ratio (annualized)
    - Calmar Ratio
    - Maximum Drawdown
    - Win Rate
    - Profit Factor
    - Average Trade Duration
    - VaR (Value at Risk) 95%
    """

    def __init__(self, trading_days_per_year: int = 252):
        self._trading_days = trading_days_per_year

    def convert(
        self,
        lean_result: Dict[str, Any],
        strategy_name: str,
        config: Optional[Any] = None,
    ) -> ConversionResult:
        """
        Convert QuantConnect Lean result to internal BacktestReport.

        Args:
            lean_result: Raw QuantConnect Lean backtest result dictionary
            strategy_name: Name of the strategy
            config: Optional BacktestConfig

        Returns:
            ConversionResult with converted BacktestReport or errors
        """
        warnings: List[str] = []
        errors: List[str] = []

        try:
            stats_data, stats_warnings = self._convert_statistics(lean_result.get("Statistics", {}))
            warnings.extend(stats_warnings)

            equity_points, equity_warnings = self._convert_equity_curve(lean_result.get("EquityCurve", []))
            warnings.extend(equity_warnings)

            trade_list, trade_warnings = self._convert_trades(lean_result.get("TradeList", []))
            warnings.extend(trade_warnings)

            total_profit = self._extract_total_profit(lean_result)
            initial_capital = stats_data.initial_capital if stats_data else Decimal("100000")
            final_capital = stats_data.final_capital if stats_data else (initial_capital + total_profit)

            total_return = Decimal("0")
            if initial_capital > 0:
                total_return = ((final_capital - initial_capital) / initial_capital) * 100

            num_trades = len(trade_list) if trade_list else 0
            winning_trades = sum(1 for t in trade_list if t.pnl > 0) if trade_list else 0
            losing_trades = sum(1 for t in trade_list if t.pnl < 0) if trade_list else 0

            win_rate = Decimal("0")
            if num_trades > 0:
                win_rate = Decimal(str(winning_trades / num_trades * 100))

            profit_factor = Decimal("0")
            if losing_trades > 0:
                avg_win = sum(t.pnl for t in trade_list if t.pnl > 0) / Decimal(str(winning_trades)) if winning_trades > 0 else Decimal("0")
                avg_loss = abs(sum(t.pnl for t in trade_list if t.pnl < 0) / Decimal(str(losing_trades)))
                if avg_loss > 0:
                    profit_factor = avg_win / avg_loss

            max_dd = stats_data.max_drawdown if stats_data else self._calculate_max_drawdown(equity_points)
            max_dd_pct = stats_data.max_drawdown_percent if stats_data else Decimal("0")

            sharpe = stats_data.sharpe_ratio if stats_data else self._calculate_sharpe_ratio(equity_points, initial_capital)
            sortino = stats_data.sortino_ratio if stats_data else self._calculate_sortino_ratio(equity_points, initial_capital)
            calmar = stats_data.calmar_ratio if stats_data else self._calculate_calmar_ratio(equity_points, initial_capital)
            var_95 = stats_data.var_95 if stats_data else self._calculate_var(equity_points, 0.95)

            avg_duration = stats_data.average_trade_duration if stats_data else self._calculate_avg_trade_duration(trade_list)

            from trader.services.backtesting.ports import BacktestResult, BacktestReport, BacktestConfig

            backtest_result = BacktestResult(
                total_return=total_return,
                sharpe_ratio=sharpe,
                max_drawdown=max_dd_pct,
                win_rate=win_rate,
                profit_factor=profit_factor,
                num_trades=num_trades,
                final_capital=final_capital,
                equity_curve=[p.to_dict() for p in equity_points],
                trades=[t.to_dict() for t in trade_list],
                metrics={
                    "total_pnl": total_profit,
                    "sortino_ratio": sortino,
                    "calmar_ratio": calmar,
                    "max_drawdown": max_dd,
                    "var_95": var_95,
                    "average_trade_duration": avg_duration,
                    "winning_trades": winning_trades,
                    "losing_trades": losing_trades,
                    "turnover": stats_data.turnover if stats_data else Decimal("0"),
                    "commission": stats_data.commission if stats_data else Decimal("0"),
                },
            )

            if config is None:
                config = BacktestConfig(
                    start_date=equity_points[0].timestamp if equity_points else datetime.now(timezone.utc),
                    end_date=equity_points[-1].timestamp if equity_points else datetime.now(timezone.utc),
                    initial_capital=initial_capital,
                    symbol="UNKNOWN",
                )

            import uuid
            report_id = str(uuid.uuid4())

            backtest_report = BacktestReport(
                report_id=report_id,
                strategy_name=strategy_name,
                config=config,
                result=backtest_result,
                framework="quantconnect",
            )

            return ConversionResult(
                success=True,
                backtest_result=backtest_report,
                warnings=warnings,
            )

        except Exception as e:
            errors.append(f"Conversion failed: {str(e)}")
            return ConversionResult(success=False, warnings=warnings, errors=errors)

    def _convert_statistics(self, stats: Dict[str, Any]) -> tuple[QuantConnectStatistics, List[str]]:
        """Convert Statistics dictionary to QuantConnectStatistics."""
        warnings: List[str] = []

        if not stats:
            warnings.append("Empty Statistics dictionary, using defaults")
            return QuantConnectStatistics(
                total_trades=0,
                winning_trades=0,
                losing_trades=0,
                total_profit=Decimal("0"),
                sharpe_ratio=Decimal("0"),
                sortino_ratio=Decimal("0"),
                calmar_ratio=Decimal("0"),
                max_drawdown=Decimal("0"),
                max_drawdown_percent=Decimal("0"),
                win_rate=Decimal("0"),
                profit_factor=Decimal("0"),
                average_trade_duration=Decimal("0"),
                var_95=Decimal("0"),
                turnover=Decimal("0"),
                commission=Decimal("0"),
                initial_capital=Decimal("100000"),
                final_capital=Decimal("100000"),
            ), warnings

        try:
            data = QuantConnectStatistics.from_lean_statistics(stats)
            return data, warnings
        except Exception as e:
            warnings.append(f"Failed to parse some statistics: {str(e)}")
            data = QuantConnectStatistics.from_lean_statistics(stats)
            return data, warnings

    def _convert_equity_curve(self, curve: List[Dict[str, Any]]) -> tuple[List[EquityPoint], List[str]]:
        """Convert EquityCurve list to EquityPoint list."""
        warnings: List[str] = []
        points: List[EquityPoint] = []

        if not curve:
            warnings.append("Empty EquityCurve, using defaults")
            return [], warnings

        for i, point in enumerate(curve):
            try:
                if isinstance(point, dict):
                    ts = point.get("timestamp", point.get("time", point.get("date", 0)))
                    equity = point.get("equity", point.get("value", 0))
                    points.append(EquityPoint(timestamp=ts, equity=equity))
                elif isinstance(point, (int, float)):
                    points.append(EquityPoint(timestamp=datetime.fromtimestamp(point, tz=timezone.utc), equity=Decimal("0")))
                else:
                    warnings.append(f"Unknown equity curve point format at index {i}")
            except Exception as e:
                warnings.append(f"Failed to parse equity point at index {i}: {str(e)}")

        points.sort(key=lambda x: x.timestamp)
        return points, warnings

    def _convert_trades(self, trades: List[Dict[str, Any]]) -> tuple[List[QuantConnectTrade], List[str]]:
        """Convert TradeList to QuantConnectTrade list."""
        warnings: List[str] = []
        converted: List[QuantConnectTrade] = []

        if not trades:
            warnings.append("Empty TradeList")
            return [], warnings

        for i, trade in enumerate(trades):
            try:
                if isinstance(trade, dict):
                    converted_trade = QuantConnectTrade(
                        id=trade.get("Id", trade.get("id", f"trade_{i}")),
                        symbol=trade.get("Symbol", trade.get("symbol", "UNKNOWN")),
                        quantity=Decimal(str(trade.get("Quantity", trade.get("quantity", 0)))),
                        price=Decimal(str(trade.get("Price", trade.get("price", 0)))),
                        direction=trade.get("Direction", trade.get("direction", "UNKNOWN")),
                        entry_time=trade.get("EntryTime", trade.get("entry_time", 0)),
                        exit_time=trade.get("ExitTime", trade.get("exit_time")),
                        entry_price=Decimal(str(trade.get("EntryPrice", trade.get("entry_price", 0)))),
                        exit_price=Decimal(str(trade.get("ExitPrice", trade.get("exit_price", 0)))) if trade.get("ExitPrice", trade.get("exit_price")) else Decimal("0"),
                        pnl=Decimal(str(trade.get("PnL", trade.get("pnl", 0)))),
                        pnl_percent=Decimal(str(trade.get("PnLPercent", trade.get("pnl_percent", 0)))),
                    )
                    converted.append(converted_trade)
                else:
                    warnings.append(f"Unknown trade format at index {i}")
            except Exception as e:
                warnings.append(f"Failed to parse trade at index {i}: {str(e)}")

        return converted, warnings

    def _extract_total_profit(self, lean_result: Dict[str, Any]) -> Decimal:
        """Extract TotalProfit from lean result."""
        total_profit = lean_result.get("TotalProfit", lean_result.get("total_profit", 0))
        if isinstance(total_profit, str):
            total_profit = float(total_profit.replace(",", ""))
        return Decimal(str(total_profit))

    def _calculate_max_drawdown(self, equity_curve: Sequence[EquityPoint]) -> Decimal:
        """Calculate maximum drawdown from equity curve."""
        if not equity_curve:
            return Decimal("0")

        peak = equity_curve[0].equity
        max_dd = Decimal("0")

        for point in equity_curve:
            if point.equity > peak:
                peak = point.equity
            drawdown = peak - point.equity
            if drawdown > max_dd:
                max_dd = drawdown

        return max_dd

    def _calculate_sharpe_ratio(
        self,
        equity_curve: Sequence[EquityPoint],
        initial_capital: Decimal,
    ) -> Decimal:
        """Calculate annualized Sharpe Ratio."""
        if len(equity_curve) < 2 or initial_capital == 0:
            return Decimal("0")

        returns: List[Decimal] = []
        for i in range(1, len(equity_curve)):
            prev_equity = equity_curve[i - 1].equity
            curr_equity = equity_curve[i].equity
            if prev_equity != 0:
                ret = (curr_equity - prev_equity) / prev_equity
                returns.append(ret)

        if not returns:
            return Decimal("0")

        mean_return = sum(returns) / Decimal(str(len(returns)))
        std_return = self._stddev(returns)

        if std_return == 0:
            return Decimal("0")

        sharpe = (mean_return / std_return) * Decimal(str(math.sqrt(self._trading_days)))
        return Decimal(str(sharpe))

    def _calculate_sortino_ratio(
        self,
        equity_curve: Sequence[EquityPoint],
        initial_capital: Decimal,
    ) -> Decimal:
        """Calculate annualized Sortino Ratio."""
        if len(equity_curve) < 2 or initial_capital == 0:
            return Decimal("0")

        returns: List[Decimal] = []
        for i in range(1, len(equity_curve)):
            prev_equity = equity_curve[i - 1].equity
            curr_equity = equity_curve[i].equity
            if prev_equity != 0:
                ret = (curr_equity - prev_equity) / prev_equity
                returns.append(ret)

        if not returns:
            return Decimal("0")

        mean_return = sum(returns) / Decimal(str(len(returns)))

        downside_returns = [r for r in returns if r < 0]
        if not downside_returns:
            return Decimal("0")

        downside_std = self._stddev(downside_returns)
        if downside_std == 0:
            return Decimal("0")

        sortino = (mean_return / downside_std) * Decimal(str(math.sqrt(self._trading_days)))
        return Decimal(str(sortino))

    def _calculate_calmar_ratio(
        self,
        equity_curve: Sequence[EquityPoint],
        initial_capital: Decimal,
    ) -> Decimal:
        """Calculate Calmar Ratio (annualized return / max drawdown)."""
        if len(equity_curve) < 2 or initial_capital == 0:
            return Decimal("0")

        start_equity = equity_curve[0].equity
        end_equity = equity_curve[-1].equity
        total_return = (end_equity - start_equity) / start_equity

        max_dd = self._calculate_max_drawdown(equity_curve)
        if initial_capital > 0:
            max_dd_pct = max_dd / initial_capital
        else:
            max_dd_pct = Decimal("0")

        if max_dd_pct == 0:
            return Decimal("0")

        years = Decimal(str(len(equity_curve) / self._trading_days)) if self._trading_days > 0 else Decimal("1")
        annualized_return = (end_equity / start_equity) ** (Decimal("1") / years) - 1 if years > 0 and start_equity > 0 else total_return

        calmar = annualized_return / max_dd_pct
        return Decimal(str(calmar))

    def _calculate_var(self, equity_curve: Sequence[EquityPoint], confidence: float) -> Decimal:
        """Calculate Value at Risk (VaR) at given confidence level."""
        if len(equity_curve) < 2:
            return Decimal("0")

        returns: List[Decimal] = []
        for i in range(1, len(equity_curve)):
            prev_equity = equity_curve[i - 1].equity
            curr_equity = equity_curve[i].equity
            if prev_equity != 0:
                ret = (curr_equity - prev_equity) / prev_equity
                returns.append(ret)

        if not returns:
            return Decimal("0")

        returns.sort()
        index = int(len(returns) * (1 - confidence))
        index = max(0, min(index, len(returns) - 1))
        var = returns[index]

        return Decimal(str(abs(var)))

    def _calculate_avg_trade_duration(self, trades: Sequence[QuantConnectTrade]) -> Decimal:
        """Calculate average trade duration in seconds."""
        if not trades:
            return Decimal("0")

        durations: List[float] = []
        for trade in trades:
            if trade.exit_time and trade.entry_time:
                duration = (trade.exit_time - trade.entry_time).total_seconds()
                durations.append(duration)

        if not durations:
            return Decimal("0")

        avg_duration = sum(durations) / len(durations)
        return Decimal(str(avg_duration))

    def _stddev(self, values: List[Decimal]) -> Decimal:
        """Calculate standard deviation of decimal values."""
        if not values:
            return Decimal("0")

        float_values = [float(v) for v in values]
        mean = sum(float_values) / len(float_values)
        variance = sum((x - mean) ** 2 for x in float_values) / len(float_values)
        std = math.sqrt(variance)

        return Decimal(str(std))

    def validate_result(self, result: Any) -> ConversionResult:
        """
        Validate converted BacktestReport.

        Args:
            result: BacktestReport to validate

        Returns:
            ConversionResult with validation status and any issues found
        """
        warnings: List[str] = []
        errors: List[str] = []

        if not hasattr(result, 'report_id'):
            errors.append("Missing report_id")

        if not hasattr(result, 'strategy_name'):
            errors.append("Missing strategy_name")

        if not hasattr(result, 'config'):
            errors.append("Missing config")
        else:
            config = result.config
            if not hasattr(config, 'start_date'):
                errors.append("Missing config.start_date")
            if not hasattr(config, 'end_date'):
                errors.append("Missing config.end_date")
            if not hasattr(config, 'initial_capital'):
                errors.append("Missing config.initial_capital")

        if not hasattr(result, 'result'):
            errors.append("Missing result")
            return ConversionResult(success=False, warnings=warnings, errors=errors)

        backtest_result = result.result

        if not hasattr(backtest_result, 'total_return'):
            errors.append("Missing result.total_return")

        if not hasattr(backtest_result, 'num_trades'):
            warnings.append("Missing num_trades field")

        if hasattr(backtest_result, 'equity_curve'):
            if not isinstance(backtest_result.equity_curve, (list, tuple)):
                errors.append("equity_curve must be a list")
            elif len(backtest_result.equity_curve) == 0:
                warnings.append("Empty equity_curve")

        if hasattr(backtest_result, 'trades'):
            if not isinstance(backtest_result.trades, (list, tuple)):
                errors.append("trades must be a list")

        return ConversionResult(
            success=len(errors) == 0,
            backtest_result=result,
            warnings=warnings,
            errors=errors,
        )

    def round_trip_test(self, original: Dict[str, Any], converted: Any) -> ConversionResult:
        """
        Test round-trip conversion.

        Args:
            original: Original QuantConnect Lean result
            converted: Converted BacktestReport

        Returns:
            ConversionResult with test status
        """
        warnings: List[str] = []
        errors: List[str] = []

        try:
            validation = self.validate_result(converted)
            errors.extend(validation.errors)
            warnings.extend(validation.warnings)

            original_profit = self._extract_total_profit(original)
            converted_profit = converted.result.metrics.get("total_pnl", Decimal("0"))

            profit_diff = abs(original_profit - converted_profit)
            if profit_diff > Decimal("0.01"):
                warnings.append(
                    f"Total profit differs: original={original_profit}, converted={converted_profit}"
                )

        except Exception as e:
            errors.append(f"Round-trip test failed: {str(e)}")

        return ConversionResult(
            success=len(errors) == 0,
            backtest_result=converted,
            warnings=warnings,
            errors=errors,
        )
