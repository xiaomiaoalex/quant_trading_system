"""
Services - Business logic layer for the control plane
==================================================
Provides service classes that implement business logic for each API domain.
"""
from typing import List, Optional, Dict, Any
from datetime import datetime
import uuid

from trader.storage.in_memory import get_storage, InMemoryStorage
from trader.api.models.schemas import (
    Strategy, StrategyRegisterRequest, StrategyVersion, StrategyVersionCreateRequest,
    VersionedConfig, VersionedConfigUpsertRequest,
    Deployment, DeploymentCreateRequest,
    BacktestRequest, BacktestRun,
    OrderView, ExecutionView,
    PositionView, PnlView,
    EventEnvelope, SnapshotEnvelope, ReplayRequest,
    KillSwitchState, KillSwitchSetRequest,
    BrokerAccount, BrokerStatus,
    ActionResult,
)


class StrategyService:
    """Service for managing strategies"""

    def __init__(self, storage: Optional[InMemoryStorage] = None):
        self._storage = storage or get_storage()

    def register_strategy(self, request: StrategyRegisterRequest) -> Strategy:
        """Register a new strategy"""
        strategy_data = request.model_dump()
        strategy = self._storage.create_strategy(strategy_data)
        return Strategy(**strategy)

    def get_strategy(self, strategy_id: str) -> Optional[Strategy]:
        """Get a strategy by ID"""
        strategy = self._storage.get_strategy(strategy_id)
        if strategy:
            return Strategy(**strategy)
        return None

    def list_strategies(self) -> List[Strategy]:
        """List all strategies"""
        strategies = self._storage.list_strategies()
        return [Strategy(**s) for s in strategies]

    def create_version(self, strategy_id: str, request: StrategyVersionCreateRequest) -> StrategyVersion:
        """Create a new strategy version"""
        version_data = request.model_dump()
        version = self._storage.create_strategy_version(strategy_id, version_data)
        return StrategyVersion(**version)

    def get_version(self, strategy_id: str, version: int) -> Optional[StrategyVersion]:
        """Get a specific strategy version"""
        version = self._storage.get_strategy_version(strategy_id, version)
        if version:
            return StrategyVersion(**version)
        return None

    def list_versions(self, strategy_id: str) -> List[StrategyVersion]:
        """List all versions of a strategy"""
        versions = self._storage.list_strategy_versions(strategy_id)
        return [StrategyVersion(**v) for v in versions]

    def get_latest_params(self, strategy_id: str) -> Optional[VersionedConfig]:
        """Get latest strategy params"""
        params = self._storage.get_latest_strategy_params(strategy_id)
        if params:
            return VersionedConfig(**params)
        return None

    def create_params(self, strategy_id: str, request: VersionedConfigUpsertRequest) -> VersionedConfig:
        """Create new strategy params version"""
        params_data = request.model_dump()
        params = self._storage.create_strategy_params(strategy_id, params_data)
        return VersionedConfig(**params)


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

    def __init__(self, storage: Optional[InMemoryStorage] = None):
        self._storage = storage or get_storage()

    def create_backtest(self, request: BacktestRequest) -> BacktestRun:
        """Trigger a new backtest run"""
        backtest_data = request.model_dump()
        backtest = self._storage.create_backtest(backtest_data)
        return BacktestRun(**backtest)

    def get_backtest(self, run_id: str) -> Optional[BacktestRun]:
        """Get backtest run by ID"""
        backtest = self._storage.get_backtest(run_id)
        if backtest:
            return BacktestRun(**backtest)
        return None

    def complete_backtest(self, run_id: str, metrics: Dict[str, Any], artifact_ref: str) -> Optional[BacktestRun]:
        """Mark backtest as completed"""
        updates = {
            "status": "COMPLETED",
            "metrics": metrics,
            "artifact_ref": artifact_ref,
        }
        backtest = self._storage.update_backtest(run_id, updates)
        if backtest:
            return BacktestRun(**backtest)
        return None


class RiskService:
    """Service for managing risk limits"""

    def __init__(self, storage: Optional[InMemoryStorage] = None):
        self._storage = storage or get_storage()

    def get_limits(self, scope: str = "GLOBAL") -> Optional[VersionedConfig]:
        """Get latest risk limits"""
        limits = self._storage.get_latest_risk_limits(scope)
        if limits:
            return VersionedConfig(**limits)
        return None

    def set_limits(self, request: VersionedConfigUpsertRequest) -> VersionedConfig:
        """Set new risk limits"""
        risk_data = request.model_dump()
        limits = self._storage.create_risk_limits(risk_data)
        return VersionedConfig(**limits)


class OrderService:
    """Service for querying orders and executions"""

    def __init__(self, storage: Optional[InMemoryStorage] = None):
        self._storage = storage or get_storage()

    def get_order(self, cl_ord_id: str) -> Optional[OrderView]:
        """Get order by client order ID"""
        order = self._storage.get_order(cl_ord_id)
        if order:
            return OrderView(**order)
        return None

    def list_orders(
        self,
        account_id: Optional[str] = None,
        strategy_id: Optional[str] = None,
        deployment_id: Optional[str] = None,
        venue: Optional[str] = None,
        status: Optional[str] = None,
        instrument: Optional[str] = None,
        since_ts_ms: Optional[int] = None,
        limit: int = 200,
    ) -> List[OrderView]:
        """Query orders with filters"""
        orders = self._storage.list_orders(
            account_id, strategy_id, deployment_id, venue, status, instrument, since_ts_ms, limit
        )
        return [OrderView(**o) for o in orders]

    def cancel_order(self, cl_ord_id: str) -> ActionResult:
        """Cancel an order"""
        order = self._storage.get_order(cl_ord_id)
        if order:
            order["status"] = "CANCELLED"
            order["updated_ts_ms"] = int(datetime.utcnow().timestamp() * 1000)
            return ActionResult(ok=True, message=f"Order {cl_ord_id} cancellation requested")
        return ActionResult(ok=False, message=f"Order {cl_ord_id} not found")

    def list_executions(
        self,
        cl_ord_id: Optional[str] = None,
        deployment_id: Optional[str] = None,
        since_ts_ms: Optional[int] = None,
        limit: int = 500,
    ) -> List[ExecutionView]:
        """Query executions"""
        executions = self._storage.list_executions(cl_ord_id, deployment_id, since_ts_ms, limit)
        return [ExecutionView(**e) for e in executions]


class PortfolioService:
    """Service for portfolio positions and PnL"""

    def __init__(self, storage: Optional[InMemoryStorage] = None):
        self._storage = storage or get_storage()

    def list_positions(
        self,
        account_id: Optional[str] = None,
        venue: Optional[str] = None,
    ) -> List[PositionView]:
        """Get positions"""
        positions = self._storage.list_positions(account_id, venue)
        return [PositionView(**p) for p in positions]

    def get_pnl(
        self,
        account_id: Optional[str] = None,
        venue: Optional[str] = None,
    ) -> PnlView:
        """Get PnL summary"""
        pnl = self._storage.calculate_pnl(account_id, venue)
        return PnlView(**pnl)


class EventService:
    """Service for events and snapshots"""

    def __init__(self, storage: Optional[InMemoryStorage] = None):
        self._storage = storage or get_storage()

    def list_events(
        self,
        stream_key: Optional[str] = None,
        event_type: Optional[str] = None,
        trace_id: Optional[str] = None,
        since_ts_ms: Optional[int] = None,
        limit: int = 2000,
    ) -> List[EventEnvelope]:
        """Query events"""
        events = self._storage.list_events(stream_key, event_type, trace_id, since_ts_ms, limit)
        return [EventEnvelope(**e) for e in events]

    def get_latest_snapshot(self, stream_key: str) -> Optional[SnapshotEnvelope]:
        """Get latest snapshot for a stream"""
        snapshot = self._storage.get_latest_snapshot(stream_key)
        if snapshot:
            return SnapshotEnvelope(**snapshot)
        return None

    def trigger_replay(self, request: ReplayRequest) -> ActionResult:
        """Trigger a replay"""
        # In a real implementation, this would trigger a replay job
        return ActionResult(ok=True, message=f"Replay triggered for stream {request.stream_key}")


class KillSwitchService:
    """Service for kill switch management"""

    def __init__(self, storage: Optional[InMemoryStorage] = None):
        self._storage = storage or get_storage()

    def get_state(self, scope: str = "GLOBAL") -> KillSwitchState:
        """Get kill switch state"""
        state = self._storage.get_kill_switch(scope)
        return KillSwitchState(**state)

    def set_state(self, request: KillSwitchSetRequest) -> KillSwitchState:
        """Set kill switch level"""
        state = self._storage.set_kill_switch(
            request.scope, request.level, request.reason, request.updated_by
        )
        return KillSwitchState(**state)


class BrokerService:
    """Service for broker management"""

    def __init__(self, storage: Optional[InMemoryStorage] = None):
        self._storage = storage or get_storage()

    def list_brokers(self) -> List[BrokerAccount]:
        """List broker accounts"""
        brokers = self._storage.list_brokers()
        return [BrokerAccount(**b) for b in brokers]

    def get_status(self, account_id: str) -> Optional[BrokerStatus]:
        """Get broker connection status"""
        status = self._storage.get_broker_status(account_id)
        if status:
            return BrokerStatus(**status)
        return None
