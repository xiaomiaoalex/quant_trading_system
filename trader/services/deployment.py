from typing import List, Optional, Dict, Any

from trader.storage.in_memory import get_storage, InMemoryStorage
from trader.api.models.schemas import (
    Deployment, DeploymentCreateRequest,
    BacktestRequest, BacktestRun,
    ActionResult,
)


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
