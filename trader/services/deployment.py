import asyncio
import math
import statistics
import threading
import hashlib
import logging
from datetime import datetime, timezone
from decimal import Decimal
from typing import List, Optional, Dict, Any

from trader.storage.in_memory import get_storage, InMemoryStorage
from trader.storage.artifact_storage import get_artifact_storage
from trader.api.models.schemas import (
    Deployment, DeploymentCreateRequest,
    BacktestRequest, BacktestRun,
    ActionResult,
)
from trader.core.application.strategy_protocol import MarketData, MarketDataType
from trader.services.strategy_runner import StrategyRunner
from trader.services.backtesting.binance_execution_adapter import BinanceExecutionAdapter
from trader.services.backtesting.ports import BacktestConfig

logger = logging.getLogger(__name__)


def _utc_now_iso() -> str:
    return datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")


def _to_decimal(value: Any, default: Decimal) -> Decimal:
    if value is None:
        return default
    if isinstance(value, Decimal):
        return value
    return Decimal(str(value))


def _stable_int(value: str) -> int:
    """Stable int hash for repeatable synthetic market data generation."""
    return int(hashlib.sha256(value.encode("utf-8")).hexdigest()[:16], 16)


class DeploymentService:
    """Service for managing deployments"""

    def __init__(self, storage: Optional[InMemoryStorage] = None):
        self._storage = storage or get_storage()

    def create_deployment(self, request: DeploymentCreateRequest) -> Deployment:
        """Create a new deployment"""
        deployment_data = request.model_dump()
        deployment = self._storage.create_deployment(deployment_data)
        return Deployment(**deployment)

    def get_deployment(self, deployment_id: str) -> Optional[Deployment]:
        """Get a deployment by ID"""
        deployment = self._storage.get_deployment(deployment_id)
        if deployment:
            return Deployment(**deployment)
        return None

    def list_deployments(
        self,
        status: Optional[str] = None,
        strategy_id: Optional[str] = None,
        account_id: Optional[str] = None,
        venue: Optional[str] = None,
    ) -> List[Deployment]:
        """List deployments with filters"""
        deployments = self._storage.list_deployments(status, strategy_id, account_id, venue)
        return [Deployment(**d) for d in deployments]

    def start_deployment(self, deployment_id: str) -> ActionResult:
        """Start a deployment"""
        deployment = self._storage.update_deployment_status(deployment_id, "RUNNING")
        if deployment:
            return ActionResult(ok=True, message=f"Deployment {deployment_id} started")
        return ActionResult(ok=False, message=f"Deployment {deployment_id} not found")

    def stop_deployment(self, deployment_id: str) -> ActionResult:
        """Stop a deployment"""
        deployment = self._storage.update_deployment_status(deployment_id, "STOPPED")
        if deployment:
            return ActionResult(ok=True, message=f"Deployment {deployment_id} stopped")
        return ActionResult(ok=False, message=f"Deployment {deployment_id} not found")

    def update_params(self, deployment_id: str, params: Dict[str, Any]) -> Optional[Deployment]:
        """Update deployment params"""
        deployment = self._storage.update_deployment_params(deployment_id, params)
        if deployment:
            return Deployment(**deployment)
        return None


class BacktestService:
    """Service for managing backtests"""

    _tasks: Dict[str, asyncio.Task] = {}
    _task_lock: threading.Lock = threading.Lock()

    def __init__(self, storage: Optional[InMemoryStorage] = None):
        self._storage = storage or get_storage()

    def create_backtest(self, request: BacktestRequest) -> BacktestRun:
        """Trigger a new async backtest run."""
        if request.end_ts_ms <= request.start_ts_ms:
            raise ValueError("end_ts_ms must be greater than start_ts_ms")
        if not request.symbols:
            raise ValueError("symbols cannot be empty")

        strategy = self._storage.get_strategy(request.strategy_id)
        if strategy is None:
            raise ValueError(f"Strategy {request.strategy_id} not found")

        backtest_data = request.model_dump()
        backtest = self._storage.create_backtest(backtest_data)

        try:
            loop = asyncio.get_running_loop()
        except RuntimeError:
            # No running loop (e.g. pure unit tests). Keep PENDING record.
            return BacktestRun(**backtest)

        run_id = str(backtest["run_id"])
        task = loop.create_task(self._run_backtest(run_id, request))
        with self._task_lock:
            self._tasks[run_id] = task
        task.add_done_callback(lambda _t, rid=run_id: self._cleanup_task(rid))

        return BacktestRun(**backtest)

    def get_backtest(self, run_id: str) -> Optional[BacktestRun]:
        """Get backtest run by ID"""
        backtest = self._storage.get_backtest(run_id)
        if backtest:
            return BacktestRun(**backtest)
        return None

    def list_backtests(
        self,
        status: Optional[str] = None,
        strategy_id: Optional[str] = None,
        limit: int = 100,
    ) -> List[BacktestRun]:
        """List backtest runs with filters (Task 9.4)"""
        backtests = self._storage.list_backtests(status=status, strategy_id=strategy_id, limit=limit)
        return [BacktestRun(**b) for b in backtests]

    def complete_backtest(self, run_id: str, metrics: Dict[str, Any], artifact_ref: str) -> Optional[BacktestRun]:
        """Mark backtest as completed"""
        updates = {
            "status": "COMPLETED",
            "metrics": metrics,
            "artifact_ref": artifact_ref,
            "progress": 1.0,
            "finished_at": _utc_now_iso(),
        }
        backtest = self._storage.update_backtest(run_id, updates)
        if backtest:
            return BacktestRun(**backtest)
        return None

    def _cleanup_task(self, run_id: str) -> None:
        with self._task_lock:
            self._tasks.pop(run_id, None)

    async def _run_backtest(self, run_id: str, request: BacktestRequest) -> None:
        self._storage.update_backtest(
            run_id,
            {
                "status": "RUNNING",
                "started_at": _utc_now_iso(),
                "progress": 0.0,
                "error": None,
            },
        )

        runner = StrategyRunner()
        runtime_strategy_id = f"backtest_{run_id}"

        try:
            strategy_meta = self._storage.get_strategy(request.strategy_id) or {}
            code_entry = None

            if request.strategy_code_version is not None:
                code_entry = self._storage.get_strategy_code_version(
                    request.strategy_id, request.strategy_code_version
                )
                if code_entry is None:
                    raise ValueError(
                        f"Strategy code version {request.strategy_code_version} not found "
                        f"for {request.strategy_id}"
                    )

            entrypoint = str(strategy_meta.get("entrypoint", ""))
            if code_entry is None and (entrypoint.startswith("dynamic:") or entrypoint == ""):
                code_entry = self._storage.get_latest_strategy_code(request.strategy_id)
                if code_entry is None:
                    raise ValueError(
                        f"Strategy {request.strategy_id} is dynamic but no code version is saved"
                    )

            if code_entry is not None:
                await runner.load_strategy_from_code(
                    strategy_id=runtime_strategy_id,
                    version=f"v{request.version}",
                    code=code_entry["code"],
                    config=request.params or {},
                )
            else:
                if not entrypoint:
                    raise ValueError(f"Strategy {request.strategy_id} has no entrypoint")
                await runner.load_strategy(
                    strategy_id=runtime_strategy_id,
                    version=f"v{request.version}",
                    module_path=entrypoint,
                    config=request.params or {},
                )

            await runner.start(runtime_strategy_id)

            # Build BacktestConfig for VectorBT
            params = request.params or {}
            initial_capital = _to_decimal(params.get("initial_capital"), Decimal("100000"))
            commission_rate = _to_decimal(params.get("commission_rate"), Decimal("0.001"))
            interval = params.get("interval", "1h")

            config = BacktestConfig(
                start_date=datetime.fromtimestamp(request.start_ts_ms / 1000, tz=timezone.utc),
                end_date=datetime.fromtimestamp(request.end_ts_ms / 1000, tz=timezone.utc),
                initial_capital=initial_capital,
                symbol=request.symbols[0] if request.symbols else "BTCUSDT",
                interval=interval,
                commission_rate=commission_rate,
            )

            # Get strategy plugin from runner
            strategy = runner._plugins[runtime_strategy_id]

            # Run backtest via BinanceExecutionAdapter (VectorBT)
            adapter = BinanceExecutionAdapter(
                killswitch_callback=None,
                risk_callback=None,
                oms_callback=None,
            )
            result = await adapter.run_backtest(config, strategy)

            # Convert BacktestResult to report dict format expected by save_report
            returns = {
                "total_return": float(result.total_return),
                "total_return_pct": float(result.total_return) * 100,
                "annualized_return": float(result.total_return) * 365,
                "sharpe_ratio": float(result.sharpe_ratio),
            }
            risk = {
                "max_drawdown": float(result.max_drawdown),
                "max_drawdown_pct": float(result.max_drawdown) / float(initial_capital) * 100 if initial_capital > 0 else 0,
                "volatility": 0.0,
                "var_95": 0.0,
            }
            report_metrics = {
                "total_return": float(result.total_return),
                "total_return_pct": returns["total_return_pct"],
                "annualized_return": returns["annualized_return"],
                "sharpe_ratio": float(result.sharpe_ratio),
                "max_drawdown": float(result.max_drawdown),
                "max_drawdown_pct": risk["max_drawdown_pct"],
                "volatility": risk["volatility"],
                "var_95": risk["var_95"],
                "trade_count": result.num_trades,
                "winning_trades": 0,
                "losing_trades": 0,
                "win_rate": float(result.win_rate) * 100 if result.win_rate else 0,
                "initial_capital": float(initial_capital),
                "final_equity": float(result.final_capital),
                "returns": returns,
                "risk": risk,
                "trades": list(result.trades) if result.trades else [],
                "equity_curve": list(result.equity_curve) if result.equity_curve else [],
            }

            artifact_ref = get_artifact_storage().save_report(
                run_id=run_id,
                returns=returns,
                risk=risk,
                trades=list(result.trades) if result.trades else [],
                equity_curve=list(result.equity_curve) if result.equity_curve else [],
                metadata={
                    "strategy_id": request.strategy_id,
                    "version": request.version,
                    "venue": request.venue,
                    "requested_by": request.requested_by,
                },
            )
            self._storage.update_backtest(
                run_id,
                {
                    "status": "COMPLETED",
                    "progress": 1.0,
                    "finished_at": _utc_now_iso(),
                    "metrics": report_metrics,
                    "artifact_ref": artifact_ref,
                    "error": None,
                },
            )
        except Exception as exc:
            self._storage.update_backtest(
                run_id,
                {
                    "status": "FAILED",
                    "progress": 1.0,
                    "finished_at": _utc_now_iso(),
                    "error": str(exc),
                },
            )
        finally:
            try:
                await runner.stop(runtime_strategy_id)
            except Exception as e:
                logger.warning(
                    "Backtest cleanup stop failed for %s: %s",
                    runtime_strategy_id,
                    e,
                )
            try:
                await runner.unload_strategy(runtime_strategy_id)
            except Exception as e:
                logger.warning(
                    "Backtest cleanup unload failed for %s: %s",
                    runtime_strategy_id,
                    e,
                )

    def _build_market_data_series(self, request: BacktestRequest) -> List[MarketData]:
        symbols = request.symbols
        if not symbols:
            return []

        start_ms = request.start_ts_ms
        end_ms = request.end_ts_ms
        duration_ms = max(60_000, end_ms - start_ms)
        bar_count = max(80, min(800, int(duration_ms / 3_600_000)))
        step_ms = max(60_000, int(duration_ms / max(1, bar_count - 1)))
        prices: Dict[str, Decimal] = {}

        series: List[MarketData] = []
        for symbol in symbols:
            seed = _stable_int(symbol) % 10_000
            base = Decimal(str(80 + (seed % 4_000) / 10))
            prices[symbol] = base

        for i in range(bar_count):
            ts_ms = start_ms + i * step_ms
            ts = datetime.fromtimestamp(ts_ms / 1000, tz=timezone.utc)

            for symbol in symbols:
                prev = prices[symbol]
                symbol_hash = _stable_int(symbol)
                wave = math.sin((i / 12) + (symbol_hash % 17)) * 0.004
                trend = math.sin((i / 80) + (symbol_hash % 31)) * 0.0015
                jitter_hash = _stable_int(f"{symbol}:{i}")
                jitter = ((jitter_hash % 200) - 100) / 10000.0
                next_price = prev * Decimal(str(max(-0.05, min(0.05, 1 + wave + trend + jitter))))
                if next_price <= Decimal("0.01"):
                    next_price = Decimal("0.01")
                high = max(prev, next_price) * Decimal("1.001")
                low = min(prev, next_price) * Decimal("0.999")
                volume_hash = _stable_int(f"v:{symbol}:{i}")
                volume = Decimal(str(10 + ((volume_hash % 2000) / 10)))

                prices[symbol] = next_price
                series.append(
                    MarketData(
                        symbol=symbol,
                        data_type=MarketDataType.KLINE,
                        price=next_price,
                        volume=volume,
                        timestamp=ts,
                        kline_open=prev,
                        kline_high=high,
                        kline_low=low,
                        kline_close=next_price,
                        kline_interval="1h",
                    )
                )

        return series

    async def _simulate_backtest(
        self,
        runner: StrategyRunner,
        runtime_strategy_id: str,
        run_id: str,
        request: BacktestRequest,
        bars: List[MarketData],
    ) -> Dict[str, Any]:
        params = request.params or {}
        initial_capital = _to_decimal(params.get("initial_capital"), Decimal("100000"))
        default_order_size = _to_decimal(params.get("order_size"), Decimal("1"))
        cash = initial_capital
        positions: Dict[str, Decimal] = {symbol: Decimal("0") for symbol in request.symbols}
        avg_cost: Dict[str, Decimal] = {symbol: Decimal("0") for symbol in request.symbols}
        latest_prices: Dict[str, Decimal] = {symbol: Decimal("0") for symbol in request.symbols}
        closed_trade_pnls: List[Decimal] = []
        trades: List[Dict[str, Any]] = []
        equity_curve: List[Dict[str, Any]] = []

        total = max(1, len(bars))
        progress_step = max(1, total // 40)

        for idx, bar in enumerate(bars, start=1):
            latest_prices[bar.symbol] = bar.price
            signal = await runner.tick(runtime_strategy_id, bar)
            if signal is not None:
                signal_type = signal.signal_type.value if hasattr(signal.signal_type, "value") else str(signal.signal_type)
                quantity = _to_decimal(getattr(signal, "quantity", None), default_order_size)
                if quantity <= 0:
                    quantity = default_order_size

                if signal_type in {"BUY", "LONG"}:
                    max_affordable = cash / bar.price if bar.price > 0 else Decimal("0")
                    exec_qty = min(quantity, max_affordable)
                    if exec_qty > 0:
                        cost = exec_qty * bar.price
                        old_qty = positions[bar.symbol]
                        new_qty = old_qty + exec_qty
                        if new_qty > 0:
                            avg_cost[bar.symbol] = (
                                (avg_cost[bar.symbol] * old_qty + cost) / new_qty
                                if old_qty > 0 else bar.price
                            )
                        positions[bar.symbol] = new_qty
                        cash -= cost
                        trades.append(
                            {
                                "trade_id": f"{run_id}-{len(trades)+1}",
                                "symbol": bar.symbol,
                                "side": "BUY",
                                "price": float(bar.price),
                                "quantity": float(exec_qty),
                                "timestamp": bar.timestamp.isoformat(),
                            }
                        )
                elif signal_type in {"SELL", "CLOSE_LONG", "CLOSE_SHORT"}:
                    current_pos = positions[bar.symbol]
                    exec_qty = min(quantity, current_pos)
                    if exec_qty > 0:
                        revenue = exec_qty * bar.price
                        trade_pnl = (bar.price - avg_cost[bar.symbol]) * exec_qty
                        closed_trade_pnls.append(trade_pnl)
                        positions[bar.symbol] = current_pos - exec_qty
                        if positions[bar.symbol] <= 0:
                            avg_cost[bar.symbol] = Decimal("0")
                        cash += revenue
                        trades.append(
                            {
                                "trade_id": f"{run_id}-{len(trades)+1}",
                                "symbol": bar.symbol,
                                "side": "SELL",
                                "price": float(bar.price),
                                "quantity": float(exec_qty),
                                "timestamp": bar.timestamp.isoformat(),
                                "pnl": float(trade_pnl),
                            }
                        )

            equity = cash
            for symbol, qty in positions.items():
                equity += qty * latest_prices.get(symbol, Decimal("0"))
            equity_curve.append(
                {
                    "timestamp": int(bar.timestamp.timestamp() * 1000),
                    "equity": float(equity),
                }
            )

            if idx % progress_step == 0 or idx == total:
                self._storage.update_backtest(run_id, {"progress": round(idx / total, 4)})

        final_equity = Decimal(str(equity_curve[-1]["equity"])) if equity_curve else initial_capital
        total_return = final_equity - initial_capital
        total_return_pct = float((total_return / initial_capital) * Decimal("100")) if initial_capital > 0 else 0.0
        days = max(1.0, (request.end_ts_ms - request.start_ts_ms) / (1000 * 60 * 60 * 24))
        annualized_return = 0.0
        if initial_capital > 0:
            annualized_return = (pow(float(final_equity / initial_capital), 365.0 / days) - 1.0) * 100.0

        eq_values = [float(p["equity"]) for p in equity_curve]
        period_returns: List[float] = []
        for i in range(1, len(eq_values)):
            prev = eq_values[i - 1]
            cur = eq_values[i]
            if prev > 0:
                period_returns.append((cur - prev) / prev)

        sharpe_ratio = 0.0
        volatility = 0.0
        var_95 = 0.0
        if len(period_returns) >= 2:
            mean_ret = statistics.mean(period_returns)
            std_ret = statistics.pstdev(period_returns)
            if std_ret > 0:
                sharpe_ratio = mean_ret / std_ret * math.sqrt(252)
                volatility = std_ret * math.sqrt(252) * 100
            sorted_returns = sorted(period_returns)
            idx_95 = max(0, min(len(sorted_returns) - 1, int(len(sorted_returns) * 0.05)))
            var_95 = abs(sorted_returns[idx_95]) * 100

        peak = eq_values[0] if eq_values else float(initial_capital)
        max_drawdown = 0.0
        max_drawdown_pct = 0.0
        for value in eq_values:
            if value > peak:
                peak = value
            drawdown = peak - value
            drawdown_pct = (drawdown / peak * 100) if peak > 0 else 0.0
            if drawdown > max_drawdown:
                max_drawdown = drawdown
                max_drawdown_pct = drawdown_pct

        wins = len([p for p in closed_trade_pnls if p > 0])
        losses = len([p for p in closed_trade_pnls if p <= 0])
        win_rate = (wins / len(closed_trade_pnls) * 100) if closed_trade_pnls else 0.0

        returns = {
            "total_return": float(total_return),
            "total_return_pct": total_return_pct,
            "annualized_return": annualized_return,
            "sharpe_ratio": sharpe_ratio,
        }
        risk = {
            "max_drawdown": max_drawdown,
            "max_drawdown_pct": max_drawdown_pct,
            "volatility": volatility,
            "var_95": var_95,
        }

        metrics = {
            "total_return": float(total_return),
            "total_return_pct": total_return_pct,
            "annualized_return": annualized_return,
            "sharpe_ratio": sharpe_ratio,
            "max_drawdown": max_drawdown,
            "max_drawdown_pct": max_drawdown_pct,
            "volatility": volatility,
            "var_95": var_95,
            "trade_count": len(trades),
            "winning_trades": wins,
            "losing_trades": losses,
            "win_rate": win_rate,
            "initial_capital": float(initial_capital),
            "final_equity": float(final_equity),
            "returns": returns,
            "risk": risk,
            "trades": trades,
            "equity_curve": equity_curve,
        }

        return {
            "returns": returns,
            "risk": risk,
            "trades": trades,
            "equity_curve": equity_curve,
            "metrics": metrics,
        }
