import asyncio
import hashlib
import logging
import math
import statistics
import threading
from datetime import datetime, timezone
from decimal import Decimal
from typing import Any, Dict, List, Optional

from trader.adapters.broker.testing.fake_broker import FakeBroker, FakeBrokerConfig
from trader.adapters.persistence.feature_store import get_feature_store
from trader.api.models.schemas import (
    ActionResult,
    BacktestRequest,
    BacktestRun,
    Deployment,
    DeploymentCreateRequest,
)
from trader.core.application.risk_engine import RiskConfig, RiskEngine
from trader.core.application.strategy_protocol import MarketData, MarketDataType
from trader.services.backtesting.backtest_risk_integration import BacktestRiskIntegration
from trader.services.backtesting.event_driven_risk_replay import EventDrivenRiskReplay
from trader.services.backtesting.feature_store_data_provider import FeatureStoreOHLCVDataProvider
from trader.services.backtesting.ports import (
    OHLCV,
    BacktestConfig,
    BacktestResult,
    DataProviderPort,
)
from trader.services.backtesting.vectorbt_adapter import VectorBTAdapter, VectorBTConfig
from trader.services.backtesting.vectorbt_risk_adapter import (
    VectorBTAdapterWithRisk,
    VectorBTRiskAdapterConfig,
)
from trader.services.strategy_candidate import StrategyCandidateService
from trader.services.strategy_runner import StrategyRunner
from trader.storage.artifact_storage import get_artifact_storage
from trader.storage.in_memory import InMemoryStorage, get_storage

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


class _DevSmokeOHLCVProvider(DataProviderPort):
    """Deterministic no-network OHLCV provider for API smoke backtests."""

    def __init__(self, service: "BacktestService", request: BacktestRequest):
        self._service = service
        self._request = request

    async def get_klines(
        self,
        symbol: str,
        interval: str,
        start_date: datetime,
        end_date: datetime,
    ) -> List[OHLCV]:
        del start_date, end_date
        return self._service._build_ohlcv_series(
            self._request,
            symbol=symbol,
            interval=interval,
        )


class _StrategyRunnerVectorBTBridge:
    """Adapts StrategyRunner ticks to VectorBT numeric entry/exit signals."""

    def __init__(
        self,
        runner: StrategyRunner,
        runtime_strategy_id: str,
        symbol: str,
        interval: str,
    ):
        self._runner = runner
        self._runtime_strategy_id = runtime_strategy_id
        self._symbol = symbol
        self._interval = interval

    async def generate_signals(self, klines: List[OHLCV]) -> List[int]:
        signals: List[int] = []
        for kline in klines:
            market_data = MarketData(
                symbol=self._symbol,
                data_type=MarketDataType.KLINE,
                price=kline.close,
                volume=kline.volume,
                timestamp=kline.timestamp,
                kline_open=kline.open,
                kline_high=kline.high,
                kline_low=kline.low,
                kline_close=kline.close,
                kline_interval=self._interval,
            )
            signal = await self._runner.tick(self._runtime_strategy_id, market_data)
            if signal is None:
                signals.append(0)
                continue

            signal_type = (
                signal.signal_type.value
                if hasattr(signal.signal_type, "value")
                else str(signal.signal_type)
            )
            if signal_type in {"BUY", "LONG"}:
                signals.append(1)
            elif signal_type in {"SELL", "SHORT", "CLOSE_LONG", "CLOSE_SHORT"}:
                signals.append(-1)
            else:
                signals.append(0)
        return signals


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
        backtests = self._storage.list_backtests(
            status=status, strategy_id=strategy_id, limit=limit
        )
        return [BacktestRun(**b) for b in backtests]

    def complete_backtest(
        self, run_id: str, metrics: Dict[str, Any], artifact_ref: str
    ) -> Optional[BacktestRun]:
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
            await self._load_strategy_for_backtest(runner, runtime_strategy_id, request)
            await runner.start(runtime_strategy_id)

            if request.engine == "vectorbt":
                simulation = await self._run_vectorbt_backtest(
                    runner=runner,
                    runtime_strategy_id=runtime_strategy_id,
                    request=request,
                )
            else:
                bars = self._build_market_data_series(request)
                simulation = await self._simulate_backtest(
                    runner=runner,
                    runtime_strategy_id=runtime_strategy_id,
                    run_id=run_id,
                    request=request,
                    bars=bars,
                )
            returns = simulation["returns"]
            risk = simulation["risk"]
            report_metrics = simulation["metrics"]

            artifact_ref = get_artifact_storage().save_report(
                run_id=run_id,
                returns=returns,
                risk=risk,
                trades=simulation["trades"],
                equity_curve=simulation["equity_curve"],
                metadata={
                    "strategy_id": request.strategy_id,
                    "version": request.version,
                    "engine": request.engine,
                    "venue": request.venue,
                    "requested_by": request.requested_by,
                    "data_mode": request.data_mode,
                    "feature_version": request.feature_version,
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

            # Post-process: generate QuantStats HTML tearsheet (non-blocking, best-effort)
            equity_curve_data = simulation.get("equity_curve") or []
            asyncio.create_task(
                self._generate_tearsheet_async(run_id, equity_curve_data, request.strategy_id)
            )

            if request.candidate_id:
                try:
                    StrategyCandidateService().mark_backtest_passed(request.candidate_id)
                except Exception as cand_exc:
                    logger.warning(
                        "Auto mark_backtest_passed failed for candidate %s: %s",
                        request.candidate_id,
                        cand_exc,
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
            if request.candidate_id:
                try:
                    StrategyCandidateService().mark_backtest_failed(
                        request.candidate_id, reason=f"backtest_failed: {exc}"
                    )
                except Exception as cand_exc:
                    logger.warning(
                        "Auto mark_backtest_failed failed for candidate %s: %s",
                        request.candidate_id,
                        cand_exc,
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

    async def _generate_tearsheet_async(
        self,
        run_id: str,
        equity_curve: list[dict],
        strategy_name: str,
    ) -> None:
        """Post-process: generate QuantStats HTML tearsheet and store as artifact. Best-effort."""
        from trader.services.backtesting.quantstats_report import generate_tearsheet

        try:
            html_path = generate_tearsheet(equity_curve, run_id=run_id, strategy_name=strategy_name)
            if html_path:
                tearsheet_ref = get_artifact_storage().save_tearsheet(run_id, html_path)
                self._storage.update_backtest(run_id, {"tearsheet_ref": tearsheet_ref})
                logger.info("Tearsheet stored for run %s: %s", run_id, tearsheet_ref)
        except Exception as exc:
            logger.warning("Tearsheet async generation failed for run %s: %s", run_id, exc)

    async def _load_strategy_for_backtest(
        self,
        runner: StrategyRunner,
        runtime_strategy_id: str,
        request: BacktestRequest,
    ) -> None:
        strategy_meta = self._storage.get_strategy(request.strategy_id) or {}
        code_entry = None

        if request.strategy_code_version is not None:
            code_entry = self._storage.get_strategy_code_version(
                request.strategy_id,
                request.strategy_code_version,
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
                strategy_id=request.strategy_id,
                version=f"v{request.version}",
                code=code_entry["code"],
                config=request.params or {},
                deployment_id=runtime_strategy_id,
                symbols=request.symbols,
                account_id="backtest",
                venue=request.venue,
                mode="backtest",
            )
            return

        if not entrypoint:
            raise ValueError(f"Strategy {request.strategy_id} has no entrypoint")
        await runner.load_strategy(
            strategy_id=request.strategy_id,
            version=f"v{request.version}",
            module_path=entrypoint,
            config=request.params or {},
            deployment_id=runtime_strategy_id,
            symbols=request.symbols,
            account_id="backtest",
            venue=request.venue,
            mode="backtest",
        )

    async def _run_vectorbt_backtest(
        self,
        runner: StrategyRunner,
        runtime_strategy_id: str,
        request: BacktestRequest,
    ) -> Dict[str, Any]:
        symbol = request.symbols[0]
        params = request.params or {}
        interval = str(params.get("interval", "1h"))
        config = BacktestConfig(
            start_date=datetime.fromtimestamp(request.start_ts_ms / 1000, tz=timezone.utc),
            end_date=datetime.fromtimestamp(request.end_ts_ms / 1000, tz=timezone.utc),
            initial_capital=Decimal(str(request.initial_capital)),
            symbol=symbol,
            interval=interval,
            benchmark=request.benchmark,
            commission_rate=Decimal(str(request.fee_bps)) / Decimal("10000"),
            slippage_rate=Decimal(str(request.slippage_bps)) / Decimal("10000"),
        )
        if request.data_mode == "real_feature_store":
            data_provider: DataProviderPort = FeatureStoreOHLCVDataProvider(
                feature_store=get_feature_store(),
                feature_version=request.feature_version,
            )
        else:
            data_provider = _DevSmokeOHLCVProvider(self, request)

        strategy = _StrategyRunnerVectorBTBridge(
            runner=runner,
            runtime_strategy_id=runtime_strategy_id,
            symbol=symbol,
            interval=interval,
        )

        # 创建回测用 RiskEngine（使用 FakeBroker 作为底层，避免网络依赖）
        fake_broker = FakeBroker(FakeBrokerConfig(latency_ms=0))
        await fake_broker.connect()
        risk_engine = RiskEngine(
            broker=fake_broker,
            config=RiskConfig(
                max_daily_loss_percent=5.0,
                max_drawdown_percent=10.0,
                max_positions=10,
                max_order_rate=60,
            ),
        )

        if request.risk_mode == "event_replay":
            klines = await data_provider.get_klines(
                symbol=config.symbol,
                interval=config.interval,
                start_date=config.start_date,
                end_date=config.end_date,
            )
            # 直接通过 runner.tick 获取 Signal 对象，而不是整数信号
            signals: list[Any] = []
            equity_timestamps_ms: list[int] = []
            for kline in klines:
                equity_timestamps_ms.append(int(kline.timestamp.timestamp() * 1000))
                market_data = MarketData(
                    symbol=config.symbol,
                    data_type=MarketDataType.KLINE,
                    price=kline.close,
                    volume=kline.volume,
                    timestamp=kline.timestamp,
                    kline_open=kline.open,
                    kline_high=kline.high,
                    kline_low=kline.low,
                    kline_close=kline.close,
                    kline_interval=config.interval,
                )
                signal = await runner.tick(runtime_strategy_id, market_data)
                if signal is not None:
                    signals.append(signal)
            risk_integration = BacktestRiskIntegration(risk_engine)
            replay = EventDrivenRiskReplay(risk_integration)
            replay_result = await replay.replay(signals)
            data_quality_summary = getattr(data_provider, "last_quality_summary", None)
            return self._event_replay_result_to_simulation(
                replay_result,
                request,
                data_quality_summary=data_quality_summary,
                equity_timestamps_ms=equity_timestamps_ms,
            )

        if request.risk_mode == "risk_adjusted":
            base_adapter = VectorBTAdapter(
                config=VectorBTConfig(freq=interval),
                data_provider=data_provider,
            )
            risk_adapter = VectorBTAdapterWithRisk(
                base_adapter=base_adapter,
                config=VectorBTRiskAdapterConfig(
                    enable_risk_adjustment=True,
                    include_raw_metrics=True,
                    include_risk_adjusted_metrics=True,
                    freq=interval,
                ),
                data_provider=data_provider,
                risk_engine=risk_engine,
            )
            result = await risk_adapter.run_backtest_with_risk(config, strategy)
            data_quality_summary = getattr(data_provider, "last_quality_summary", None)
            return self._vectorbt_result_to_simulation(
                result,
                request,
                data_quality_summary=data_quality_summary,
                risk_mode="risk_adjusted",
            )

        adapter = VectorBTAdapter(
            config=VectorBTConfig(freq=interval),
            data_provider=data_provider,
        )
        result = await adapter.run_backtest(config, strategy)
        data_quality_summary = getattr(data_provider, "last_quality_summary", None)
        return self._vectorbt_result_to_simulation(
            result,
            request,
            data_quality_summary=data_quality_summary,
            risk_mode="raw_only",
        )

    def _build_ohlcv_series(
        self,
        request: BacktestRequest,
        symbol: str,
        interval: str = "1h",
    ) -> List[OHLCV]:
        bars = self._build_market_data_series(request)
        series: List[OHLCV] = []
        for bar in bars:
            if bar.symbol != symbol:
                continue
            close = bar.kline_close or bar.price
            series.append(
                OHLCV(
                    timestamp=bar.timestamp,
                    open=bar.kline_open or bar.price,
                    high=bar.kline_high or close,
                    low=bar.kline_low or close,
                    close=close,
                    volume=bar.volume,
                )
            )
        if not series:
            raise ValueError(f"No dev_smoke OHLCV data generated for {symbol}/{interval}")
        return series

    def _vectorbt_result_to_simulation(
        self,
        result: BacktestResult,
        request: BacktestRequest,
        data_quality_summary: Optional[Dict[str, Any]] = None,
        risk_mode: str = "raw_only",
    ) -> Dict[str, Any]:
        initial_capital = Decimal(str(request.initial_capital))
        final_equity = result.final_capital
        total_return = final_equity - initial_capital
        total_return_ratio = float(result.total_return)
        total_return_pct = float(result.metrics.get("total_return_pct", total_return_ratio * 100))
        max_drawdown_ratio = float(result.max_drawdown)
        max_drawdown_pct = max_drawdown_ratio * 100
        win_rate_ratio = float(result.win_rate)
        win_rate_pct = win_rate_ratio * 100 if win_rate_ratio <= 1 else win_rate_ratio
        equity_curve = list(result.equity_curve)
        trades = list(result.trades)

        returns = {
            "total_return": float(total_return),
            "total_return_pct": total_return_pct,
            "annualized_return": float(result.metrics.get("annualized_return", 0.0)),
            "sharpe_ratio": float(result.sharpe_ratio),
        }
        risk = {
            "max_drawdown": max_drawdown_ratio,
            "max_drawdown_pct": max_drawdown_pct,
            "volatility": float(result.metrics.get("volatility", 0.0)),
            "var_95": float(result.metrics.get("var_95", 0.0)),
        }
        metrics = {
            "backtest_engine": "vectorbt",
            "framework": "vectorbt",
            "risk_mode": risk_mode,
            "total_return": float(total_return),
            "total_return_pct": total_return_pct,
            "annualized_return": returns["annualized_return"],
            "sharpe_ratio": float(result.sharpe_ratio),
            "max_drawdown": max_drawdown_ratio,
            "max_drawdown_pct": max_drawdown_pct,
            "volatility": risk["volatility"],
            "var_95": risk["var_95"],
            "trade_count": result.num_trades,
            "winning_trades": None,
            "losing_trades": None,
            "win_rate": win_rate_pct,
            "profit_factor": float(result.profit_factor),
            "initial_capital": float(initial_capital),
            "final_equity": float(final_equity),
            "returns": returns,
            "risk": risk,
            "trades": trades,
            "equity_curve": equity_curve,
            "backtest_data_mode": request.data_mode,
            "feature_version": request.feature_version,
            "fee_bps": request.fee_bps,
            "slippage_bps": request.slippage_bps,
            "benchmark": request.benchmark,
            "data_quality_summary": data_quality_summary
            or {
                "quality_score": 0.0,
                "missing_data": True,
                "source": "deterministic_dev_smoke",
            },
            # 风控报告字段
            "approved_orders": list(result.approved_orders),
            "clipped_orders": list(result.clipped_orders),
            "rejected_orders": list(result.rejected_orders),
            "rejection_reason_counts": dict(result.rejection_reason_counts),
            "max_drawdown_before_risk": (
                float(result.max_drawdown_before_risk)
                if result.max_drawdown_before_risk is not None
                else None
            ),
            "max_drawdown_after_risk": (
                float(result.max_drawdown_after_risk)
                if result.max_drawdown_after_risk is not None
                else None
            ),
            "risk_adjusted_metrics": dict(result.risk_adjusted_metrics),
            "risk_adjusted_equity_curve": list(result.risk_adjusted_equity_curve),
        }

        return {
            "returns": returns,
            "risk": risk,
            "trades": trades,
            "equity_curve": equity_curve,
            "metrics": metrics,
        }

    def _event_replay_result_to_simulation(
        self,
        result: Any,
        request: BacktestRequest,
        data_quality_summary: Optional[Dict[str, Any]] = None,
        equity_timestamps_ms: Optional[List[int]] = None,
    ) -> Dict[str, Any]:
        initial_capital = Decimal(str(request.initial_capital))
        final_equity = result.equity_curve[-1] if result.equity_curve else initial_capital
        total_return = final_equity - initial_capital
        total_return_pct = (
            float((total_return / initial_capital) * Decimal("100")) if initial_capital > 0 else 0.0
        )
        max_drawdown = float(result.max_drawdown)
        max_drawdown_pct = max_drawdown * 100
        fallback_step_ms = 60_000
        if equity_timestamps_ms and len(equity_timestamps_ms) > 1:
            fallback_step_ms = max(1, equity_timestamps_ms[1] - equity_timestamps_ms[0])
        equity_curve = []
        for i, equity in enumerate(result.equity_curve):
            if equity_timestamps_ms and i < len(equity_timestamps_ms):
                timestamp_ms = equity_timestamps_ms[i]
            else:
                timestamp_ms = request.start_ts_ms + i * fallback_step_ms
            equity_curve.append({"timestamp": timestamp_ms, "equity": float(equity)})
        approved_count = len(result.approved_orders)
        clipped_count = len(result.clipped_orders)
        rejected_count = len(result.rejected_orders)
        total_orders = approved_count + clipped_count + rejected_count
        win_rate = (approved_count / total_orders * 100) if total_orders > 0 else 0.0

        returns = {
            "total_return": float(total_return),
            "total_return_pct": total_return_pct,
            "annualized_return": 0.0,
            "sharpe_ratio": 0.0,
        }
        risk = {
            "max_drawdown": max_drawdown,
            "max_drawdown_pct": max_drawdown_pct,
            "volatility": 0.0,
            "var_95": 0.0,
        }
        metrics = {
            "backtest_engine": "vectorbt",
            "framework": "event_driven_risk_replay",
            "risk_mode": "event_replay",
            "total_return": float(total_return),
            "total_return_pct": total_return_pct,
            "annualized_return": 0.0,
            "sharpe_ratio": 0.0,
            "max_drawdown": max_drawdown,
            "max_drawdown_pct": max_drawdown_pct,
            "volatility": 0.0,
            "var_95": 0.0,
            "trade_count": total_orders,
            "winning_trades": approved_count,
            "losing_trades": rejected_count,
            "win_rate": win_rate,
            "profit_factor": 0.0,
            "initial_capital": float(initial_capital),
            "final_equity": float(final_equity),
            "returns": returns,
            "risk": risk,
            "trades": [],
            "equity_curve": equity_curve,
            "backtest_data_mode": request.data_mode,
            "feature_version": request.feature_version,
            "fee_bps": request.fee_bps,
            "slippage_bps": request.slippage_bps,
            "benchmark": request.benchmark,
            "data_quality_summary": data_quality_summary
            or {
                "quality_score": 0.0,
                "missing_data": True,
                "source": "deterministic_dev_smoke",
            },
            "risk_replay": {
                "approved_order_count": approved_count,
                "clipped_order_count": clipped_count,
                "rejected_order_count": rejected_count,
                "approved_orders": [self._replay_order_to_dict(o) for o in result.approved_orders],
                "clipped_orders": [self._replay_order_to_dict(o) for o in result.clipped_orders],
                "rejected_orders": [self._replay_order_to_dict(o) for o in result.rejected_orders],
                "rejection_reason_counts": result.rejection_reason_counts,
            },
        }

        return {
            "returns": returns,
            "risk": risk,
            "trades": [],
            "equity_curve": equity_curve,
            "metrics": metrics,
        }

    @staticmethod
    def _replay_order_to_dict(order: Any) -> Dict[str, Any]:
        """将 ReplayOrder / ReplayRiskDecision 转为可序列化 dict"""
        return {
            "symbol": order.symbol,
            "side": str(order.side),
            "qty": str(order.qty),
            "price": str(order.price),
            "timestamp_ms": order.timestamp_ms,
            "decision": str(order.decision),
            "normalized_qty": str(order.normalized_qty),
            "normalized_price": str(order.normalized_price),
            "rejection_reason": order.rejection_reason,
        }

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
        initial_capital = _to_decimal(
            params.get("initial_capital"),
            Decimal(str(request.initial_capital)),
        )
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
                signal_type = (
                    signal.signal_type.value
                    if hasattr(signal.signal_type, "value")
                    else str(signal.signal_type)
                )
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
                                if old_qty > 0
                                else bar.price
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
        total_return_pct = (
            float((total_return / initial_capital) * Decimal("100")) if initial_capital > 0 else 0.0
        )
        days = max(1.0, (request.end_ts_ms - request.start_ts_ms) / (1000 * 60 * 60 * 24))
        annualized_return = 0.0
        if initial_capital > 0:
            annualized_return = (
                pow(float(final_equity / initial_capital), 365.0 / days) - 1.0
            ) * 100.0

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
            "backtest_engine": "strategy_runner",
            "framework": "strategy_runner",
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
            "backtest_data_mode": request.data_mode,
            "feature_version": request.feature_version,
            "fee_bps": request.fee_bps,
            "slippage_bps": request.slippage_bps,
            "benchmark": request.benchmark,
            "data_quality_summary": {
                "quality_score": 0.0 if request.data_mode == "dev_smoke" else 1.0,
                "missing_data": request.data_mode == "dev_smoke",
            },
        }

        return {
            "returns": returns,
            "risk": risk,
            "trades": trades,
            "equity_curve": equity_curve,
            "metrics": metrics,
        }
