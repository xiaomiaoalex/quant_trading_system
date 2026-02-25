"""
Storage - In-memory storage implementation for the control plane
================================================================
Provides in-memory storage for strategies, deployments, orders, positions, etc.
"""
import uuid
from datetime import datetime
from typing import Dict, List, Optional, Any
from decimal import Decimal


class InMemoryStorage:
    """In-memory storage for all control plane data"""

    def __init__(self):
        # Strategies
        self.strategies: Dict[str, Dict[str, Any]] = {}
        self.strategy_versions: Dict[str, List[Dict[str, Any]]] = {}
        self.strategy_params: Dict[str, List[Dict[str, Any]]] = {}

        # Deployments
        self.deployments: Dict[str, Dict[str, Any]] = {}

        # Backtests
        self.backtests: Dict[str, Dict[str, Any]] = {}

        # Risk limits
        self.risk_limits: List[Dict[str, Any]] = []

        # Orders & Executions
        self.orders: Dict[str, Dict[str, Any]] = {}
        self.executions: List[Dict[str, Any]] = []

        # Positions & PnL
        self.positions: Dict[str, Dict[str, Any]] = {}

        # Events & Snapshots
        self.events: List[Dict[str, Any]] = []
        self.snapshots: Dict[str, Dict[str, Any]] = {}

        # Kill Switch
        self.kill_switch_states: Dict[str, Dict[str, Any]] = {}

        # Brokers
        self.broker_accounts: Dict[str, Dict[str, Any]] = {}

        # Event counters
        self._event_counter = 0
        self._snapshot_counter = 0

    # ==================== Strategy Methods ====================

    def create_strategy(self, strategy_data: Dict[str, Any]) -> Dict[str, Any]:
        """Create a new strategy"""
        strategy_id = strategy_data["strategy_id"]
        now = datetime.utcnow().isoformat() + "Z"
        strategy = {
            **strategy_data,
            "created_at": now,
            "updated_at": now,
        }
        self.strategies[strategy_id] = strategy
        self.strategy_versions[strategy_id] = []
        self.strategy_params[strategy_id] = []
        return strategy

    def get_strategy(self, strategy_id: str) -> Optional[Dict[str, Any]]:
        """Get a strategy by ID"""
        return self.strategies.get(strategy_id)

    def list_strategies(self) -> List[Dict[str, Any]]:
        """List all strategies"""
        return list(self.strategies.values())

    def create_strategy_version(self, strategy_id: str, version_data: Dict[str, Any]) -> Dict[str, Any]:
        """Create a new strategy version"""
        if strategy_id not in self.strategy_versions:
            self.strategy_versions[strategy_id] = []

        versions = self.strategy_versions[strategy_id]
        version = version_data.get("version", len(versions) + 1)
        now = datetime.utcnow().isoformat() + "Z"

        version_entry = {
            **version_data,
            "strategy_id": strategy_id,
            "version": version,
            "created_at": now,
        }
        versions.append(version_entry)
        self.strategy_versions[strategy_id] = versions
        return version_entry

    def get_strategy_version(self, strategy_id: str, version: int) -> Optional[Dict[str, Any]]:
        """Get a specific strategy version"""
        versions = self.strategy_versions.get(strategy_id, [])
        for v in versions:
            if v.get("version") == version:
                return v
        return None

    def list_strategy_versions(self, strategy_id: str) -> List[Dict[str, Any]]:
        """List all versions of a strategy"""
        return self.strategy_versions.get(strategy_id, [])

    def create_strategy_params(self, strategy_id: str, params_data: Dict[str, Any]) -> Dict[str, Any]:
        """Create new strategy params version"""
        if strategy_id not in self.strategy_params:
            self.strategy_params[strategy_id] = []

        params_list = self.strategy_params[strategy_id]
        version = len(params_list) + 1
        now = datetime.utcnow().isoformat() + "Z"

        params_entry = {
            "scope": params_data.get("scope", strategy_id),
            "version": version,
            "config": params_data.get("config", {}),
            "created_at": now,
            "created_by": params_data.get("created_by", "system"),
        }
        params_list.append(params_entry)
        self.strategy_params[strategy_id] = params_list
        return params_entry

    def get_latest_strategy_params(self, strategy_id: str) -> Optional[Dict[str, Any]]:
        """Get latest strategy params"""
        params_list = self.strategy_params.get(strategy_id, [])
        if params_list:
            return params_list[-1]
        return None

    # ==================== Deployment Methods ====================

    def create_deployment(self, deployment_data: Dict[str, Any]) -> Dict[str, Any]:
        """Create a new deployment"""
        deployment_id = deployment_data["deployment_id"]
        now = datetime.utcnow().isoformat() + "Z"
        deployment = {
            **deployment_data,
            "status": "STOPPED",
            "created_at": now,
            "updated_at": now,
        }
        self.deployments[deployment_id] = deployment
        return deployment

    def get_deployment(self, deployment_id: str) -> Optional[Dict[str, Any]]:
        """Get a deployment by ID"""
        return self.deployments.get(deployment_id)

    def list_deployments(
        self,
        status: Optional[str] = None,
        strategy_id: Optional[str] = None,
        account_id: Optional[str] = None,
        venue: Optional[str] = None,
    ) -> List[Dict[str, Any]]:
        """List deployments with filters"""
        deployments = list(self.deployments.values())
        if status:
            deployments = [d for d in deployments if d.get("status") == status]
        if strategy_id:
            deployments = [d for d in deployments if d.get("strategy_id") == strategy_id]
        if account_id:
            deployments = [d for d in deployments if d.get("account_id") == account_id]
        if venue:
            deployments = [d for d in deployments if d.get("venue") == venue]
        return deployments

    def update_deployment_status(self, deployment_id: str, status: str) -> Optional[Dict[str, Any]]:
        """Update deployment status"""
        deployment = self.deployments.get(deployment_id)
        if deployment:
            deployment["status"] = status
            deployment["updated_at"] = datetime.utcnow().isoformat() + "Z"
        return deployment

    def update_deployment_params(self, deployment_id: str, params: Dict[str, Any]) -> Optional[Dict[str, Any]]:
        """Update deployment params"""
        deployment = self.deployments.get(deployment_id)
        if deployment:
            deployment["params_version"] = deployment.get("params_version", 0) + 1
            deployment["updated_at"] = datetime.utcnow().isoformat() + "Z"
        return deployment

    # ==================== Backtest Methods ====================

    def create_backtest(self, backtest_data: Dict[str, Any]) -> Dict[str, Any]:
        """Create a new backtest run"""
        run_id = str(uuid.uuid4())
        now = datetime.utcnow().isoformat() + "Z"
        backtest = {
            **backtest_data,
            "run_id": run_id,
            "status": "RUNNING",
            "created_at": now,
        }
        self.backtests[run_id] = backtest
        return backtest

    def get_backtest(self, run_id: str) -> Optional[Dict[str, Any]]:
        """Get a backtest run by ID"""
        return self.backtests.get(run_id)

    def update_backtest(self, run_id: str, updates: Dict[str, Any]) -> Optional[Dict[str, Any]]:
        """Update backtest status/metrics"""
        backtest = self.backtests.get(run_id)
        if backtest:
            backtest.update(updates)
        return backtest

    # ==================== Risk Methods ====================

    def create_risk_limits(self, risk_data: Dict[str, Any]) -> Dict[str, Any]:
        """Create or update risk limits"""
        scope = risk_data.get("scope", "GLOBAL")
        version = len(self.risk_limits) + 1
        now = datetime.utcnow().isoformat() + "Z"

        risk_entry = {
            "scope": scope,
            "version": version,
            "config": risk_data.get("config", {}),
            "created_at": now,
            "created_by": risk_data.get("created_by", "system"),
        }
        self.risk_limits.append(risk_entry)
        return risk_entry

    def get_latest_risk_limits(self, scope: str = "GLOBAL") -> Optional[Dict[str, Any]]:
        """Get latest risk limits for a scope"""
        scope_limits = [r for r in self.risk_limits if r.get("scope") == scope]
        if scope_limits:
            return scope_limits[-1]
        return None

    # ==================== Order Methods ====================

    def create_order(self, order_data: Dict[str, Any]) -> Dict[str, Any]:
        """Create an order"""
        cl_ord_id = order_data["cl_ord_id"]
        now = datetime.utcnow()
        order = {
            **order_data,
            "status": order_data.get("status", "NEW"),
            "created_ts_ms": int(now.timestamp() * 1000),
            "updated_ts_ms": int(now.timestamp() * 1000),
            "filled_qty": order_data.get("filled_qty", "0"),
        }
        self.orders[cl_ord_id] = order
        return order

    def get_order(self, cl_ord_id: str) -> Optional[Dict[str, Any]]:
        """Get an order by client order ID"""
        return self.orders.get(cl_ord_id)

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
    ) -> List[Dict[str, Any]]:
        """List orders with filters"""
        orders = list(self.orders.values())
        if account_id:
            orders = [o for o in orders if o.get("account_id") == account_id]
        if strategy_id:
            orders = [o for o in orders if o.get("strategy_id") == strategy_id]
        if deployment_id:
            orders = [o for o in orders if o.get("deployment_id") == deployment_id]
        if venue:
            orders = [o for o in orders if o.get("venue") == venue]
        if status:
            orders = [o for o in orders if o.get("status") == status]
        if instrument:
            orders = [o for o in orders if o.get("instrument") == instrument]
        if since_ts_ms:
            orders = [o for o in orders if o.get("created_ts_ms", 0) >= since_ts_ms]
        return orders[:limit]

    # ==================== Execution Methods ====================

    def create_execution(self, execution_data: Dict[str, Any]) -> Dict[str, Any]:
        """Create an execution"""
        self.executions.append(execution_data)
        return execution_data

    def list_executions(
        self,
        cl_ord_id: Optional[str] = None,
        deployment_id: Optional[str] = None,
        since_ts_ms: Optional[int] = None,
        limit: int = 500,
    ) -> List[Dict[str, Any]]:
        """List executions with filters"""
        executions = list(self.executions)
        if cl_ord_id:
            executions = [e for e in executions if e.get("cl_ord_id") == cl_ord_id]
        if deployment_id:
            executions = [e for e in executions if e.get("deployment_id") == deployment_id]
        if since_ts_ms:
            executions = [e for e in executions if e.get("ts_ms", 0) >= since_ts_ms]
        return executions[:limit]

    # ==================== Position Methods ====================

    def upsert_position(self, position_data: Dict[str, Any]) -> Dict[str, Any]:
        """Upsert a position"""
        key = f"{position_data.get('account_id')}:{position_data.get('venue')}:{position_data.get('instrument')}"
        position = {
            **position_data,
            "updated_ts_ms": int(datetime.utcnow().timestamp() * 1000),
        }
        self.positions[key] = position
        return position

    def list_positions(
        self,
        account_id: Optional[str] = None,
        venue: Optional[str] = None,
    ) -> List[Dict[str, Any]]:
        """List positions with filters"""
        positions = list(self.positions.values())
        if account_id:
            positions = [p for p in positions if p.get("account_id") == account_id]
        if venue:
            positions = [p for p in positions if p.get("venue") == venue]
        return positions

    def calculate_pnl(
        self,
        account_id: Optional[str] = None,
        venue: Optional[str] = None,
    ) -> Dict[str, Any]:
        """Calculate PnL for account/venue"""
        positions = self.list_positions(account_id, venue)
        total_realized = Decimal("0")
        total_unrealized = Decimal("0")

        for p in positions:
            realized = Decimal(str(p.get("realized_pnl", "0")))
            unrealized = Decimal(str(p.get("unrealized_pnl", "0")))
            total_realized += realized
            total_unrealized += unrealized

        return {
            "account_id": account_id or "AGGREGATE",
            "venue": venue or "AGGREGATE",
            "realized_pnl": str(total_realized),
            "unrealized_pnl": str(total_unrealized),
            "total_pnl": str(total_realized + total_unrealized),
            "updated_ts_ms": int(datetime.utcnow().timestamp() * 1000),
        }

    # ==================== Event Methods ====================

    def append_event(self, event_data: Dict[str, Any]) -> Dict[str, Any]:
        """Append an event"""
        self._event_counter += 1
        event = {
            **event_data,
            "event_id": self._event_counter,
        }
        self.events.append(event)
        return event

    def list_events(
        self,
        stream_key: Optional[str] = None,
        event_type: Optional[str] = None,
        trace_id: Optional[str] = None,
        since_ts_ms: Optional[int] = None,
        limit: int = 2000,
    ) -> List[Dict[str, Any]]:
        """List events with filters"""
        events = list(self.events)
        if stream_key:
            events = [e for e in events if e.get("stream_key") == stream_key]
        if event_type:
            events = [e for e in events if e.get("event_type") == event_type]
        if trace_id:
            events = [e for e in events if e.get("trace_id") == trace_id]
        if since_ts_ms:
            events = [e for e in events if e.get("ts_ms", 0) >= since_ts_ms]
        return events[:limit]

    # ==================== Snapshot Methods ====================

    def save_snapshot(self, snapshot_data: Dict[str, Any]) -> Dict[str, Any]:
        """Save a snapshot"""
        self._snapshot_counter += 1
        snapshot = {
            **snapshot_data,
            "snapshot_id": self._snapshot_counter,
            "created_at": datetime.utcnow().isoformat() + "Z",
        }
        stream_key = snapshot.get("stream_key")
        self.snapshots[stream_key] = snapshot
        return snapshot

    def get_latest_snapshot(self, stream_key: str) -> Optional[Dict[str, Any]]:
        """Get latest snapshot for a stream"""
        return self.snapshots.get(stream_key)

    # ==================== KillSwitch Methods ====================

    def set_kill_switch(self, scope: str, level: int, reason: Optional[str], updated_by: str) -> Dict[str, Any]:
        """Set kill switch level"""
        now = datetime.utcnow().isoformat() + "Z"
        state = {
            "scope": scope,
            "level": level,
            "reason": reason,
            "updated_at": now,
            "updated_by": updated_by,
        }
        self.kill_switch_states[scope] = state
        return state

    def get_kill_switch(self, scope: str = "GLOBAL") -> Dict[str, Any]:
        """Get kill switch state"""
        state = self.kill_switch_states.get(scope)
        if state is None:
            return {
                "scope": scope,
                "level": 0,
                "reason": None,
                "updated_at": datetime.utcnow().isoformat() + "Z",
                "updated_by": "system",
            }
        return state

    # ==================== Broker Methods ====================

    def register_broker(self, broker_data: Dict[str, Any]) -> Dict[str, Any]:
        """Register a broker account"""
        account_id = broker_data["account_id"]
        self.broker_accounts[account_id] = broker_data
        return broker_data

    def list_brokers(self) -> List[Dict[str, Any]]:
        """List all broker accounts"""
        return list(self.broker_accounts.values())

    def get_broker_status(self, account_id: str) -> Optional[Dict[str, Any]]:
        """Get broker connection status"""
        broker = self.broker_accounts.get(account_id)
        if broker:
            return {
                "account_id": account_id,
                "connected": broker.get("status") == "READY",
                "last_heartbeat_ts_ms": int(datetime.utcnow().timestamp() * 1000),
                "last_error": None,
            }
        return None


# Global storage instance
_storage: Optional[InMemoryStorage] = None


def get_storage() -> InMemoryStorage:
    """Get the global storage instance"""
    global _storage
    if _storage is None:
        _storage = InMemoryStorage()
    return _storage


def reset_storage() -> InMemoryStorage:
    """Reset the storage (for testing)"""
    global _storage
    _storage = InMemoryStorage()
    return _storage
