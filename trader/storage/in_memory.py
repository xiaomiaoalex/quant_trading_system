"""
Storage - In-memory storage implementation for the control plane
================================================================
Provides in-memory storage for strategies, deployments, orders, positions, etc.
"""
import uuid
import hashlib
from datetime import datetime, timezone
from typing import Dict, List, Optional, Any, Tuple
from decimal import Decimal
from trader.core.domain.models.order import OrderStatus


class ControlPlaneInMemoryStorage:
    """
    Control Plane In-Memory Storage - 控制面内存存储
    ================================================
    
    职责边界：
    - 用于控制面（Control Plane）数据存储
    - 存储策略（Strategy）、部署（Deployment）、风控规则（Risk Limits）
    - 存储订单视图（OrderView）、持仓视图（PositionView）、PnL
    - 存储事件（Event）、快照（Snapshot）、熔断状态（KillSwitch）
    
    禁止跨用规则：
    - 禁止用于事件溯源（Event Sourcing）领域存储
    - 禁止存储原始领域事件（Domain Events）
    - 核心交易引擎数据应使用 CoreInMemoryStorage
    
    用途：
    - 控制面 API 的内存存储
    - 策略管理、部署管理
    - 风控规则、订单查询、持仓查询
    """

    def __init__(self):
        # Strategies
        self.strategies: Dict[str, Dict[str, Any]] = {}
        self.strategy_versions: Dict[str, List[Dict[str, Any]]] = {}
        self.strategy_params: Dict[str, List[Dict[str, Any]]] = {}
        self.strategy_codes: Dict[str, List[Dict[str, Any]]] = {}

        # Deployments
        self.deployments: Dict[str, Dict[str, Any]] = {}

        # Backtests
        self.backtests: Dict[str, Dict[str, Any]] = {}

        # Risk limits
        self.risk_limits: List[Dict[str, Any]] = []
        self.risk_events_by_key: Dict[str, Dict[str, Any]] = {}
        self.risk_upgrades: Dict[str, Dict[str, Any]] = {}
        self.risk_upgrade_effects: Dict[str, Dict[str, Any]] = {}

        # Orders & Executions
        self.orders: Dict[str, Dict[str, Any]] = {}
        self.executions: List[Dict[str, Any]] = []

        # Positions & PnL
        self.positions: Dict[str, Dict[str, Any]] = {}

        # Events & Snapshots
        self.events: List[Dict[str, Any]] = []
        self.snapshots: Dict[str, List[Dict[str, Any]]] = {}  # List to support history

        # Kill Switch
        self.kill_switch_states: Dict[str, Dict[str, Any]] = {}

        # Brokers
        self.broker_accounts: Dict[str, Dict[str, Any]] = {}

        # Feature Store (Feature Values)
        self.feature_values_by_key: Dict[str, Dict[str, Any]] = {}

        # Event counters
        self._event_counter = 0
        self._snapshot_counter = 0

    # ==================== Strategy Methods ====================

    def create_strategy(self, strategy_data: Dict[str, Any]) -> Dict[str, Any]:
        """Create a new strategy"""
        strategy_id = strategy_data["strategy_id"]
        now = datetime.now(timezone.utc).isoformat() + "Z"
        strategy = {
            **strategy_data,
            "created_at": now,
            "updated_at": now,
        }
        self.strategies[strategy_id] = strategy
        self.strategy_versions[strategy_id] = []
        self.strategy_params[strategy_id] = []
        self.strategy_codes[strategy_id] = []
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
        now = datetime.now(timezone.utc).isoformat() + "Z"

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
        now = datetime.now(timezone.utc).isoformat() + "Z"

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

    def create_strategy_code(self, strategy_id: str, code_data: Dict[str, Any]) -> Dict[str, Any]:
        """Create a new strategy code version"""
        if strategy_id not in self.strategy_codes:
            self.strategy_codes[strategy_id] = []

        codes = self.strategy_codes[strategy_id]
        code_version = len(codes) + 1
        now = datetime.now(timezone.utc).isoformat() + "Z"
        code = str(code_data.get("code", ""))
        checksum = hashlib.sha256(code.encode("utf-8")).hexdigest()

        code_entry = {
            "strategy_id": strategy_id,
            "code_version": code_version,
            "code": code,
            "checksum": checksum,
            "created_at": now,
            "created_by": code_data.get("created_by", "system"),
            "notes": code_data.get("notes"),
        }
        codes.append(code_entry)
        self.strategy_codes[strategy_id] = codes
        return code_entry

    def get_latest_strategy_code(self, strategy_id: str) -> Optional[Dict[str, Any]]:
        """Get latest strategy code version"""
        code_list = self.strategy_codes.get(strategy_id, [])
        if code_list:
            return code_list[-1]
        return None

    def get_strategy_code_version(self, strategy_id: str, code_version: int) -> Optional[Dict[str, Any]]:
        """Get strategy code by version"""
        code_list = self.strategy_codes.get(strategy_id, [])
        for item in code_list:
            if item.get("code_version") == code_version:
                return item
        return None

    def list_strategy_codes(self, strategy_id: str, limit: int = 20) -> List[Dict[str, Any]]:
        """List strategy code versions (latest first)"""
        code_list = list(self.strategy_codes.get(strategy_id, []))
        code_list.sort(key=lambda c: c.get("code_version", 0), reverse=True)
        return code_list[:limit]

    # ==================== Deployment Methods ====================

    def create_deployment(self, deployment_data: Dict[str, Any]) -> Dict[str, Any]:
        """Create a new deployment"""
        deployment_id = deployment_data["deployment_id"]
        now = datetime.now(timezone.utc).isoformat() + "Z"
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
            deployment["updated_at"] = datetime.now(timezone.utc).isoformat() + "Z"
        return deployment

    def update_deployment_params(self, deployment_id: str, params: Dict[str, Any]) -> Optional[Dict[str, Any]]:
        """Update deployment params"""
        deployment = self.deployments.get(deployment_id)
        if deployment:
            deployment["params_version"] = deployment.get("params_version", 0) + 1
            deployment["updated_at"] = datetime.now(timezone.utc).isoformat() + "Z"
        return deployment

    # ==================== Backtest Methods ====================

    def create_backtest(self, backtest_data: Dict[str, Any]) -> Dict[str, Any]:
        """Create a new backtest run"""
        run_id = str(uuid.uuid4())
        now = datetime.now(timezone.utc).isoformat() + "Z"
        backtest = {
            **backtest_data,
            "run_id": run_id,
            "status": "PENDING",
            "created_at": now,
            "progress": 0.0,
            "started_at": None,
            "finished_at": None,
            "error": None,
        }
        self.backtests[run_id] = backtest
        return backtest

    def get_backtest(self, run_id: str) -> Optional[Dict[str, Any]]:
        """Get a backtest run by ID"""
        return self.backtests.get(run_id)

    def list_backtests(
        self,
        status: Optional[str] = None,
        strategy_id: Optional[str] = None,
        limit: int = 100,
    ) -> List[Dict[str, Any]]:
        """List backtest runs with filters (Task 9.4)"""
        backtests = list(self.backtests.values())
        if status:
            backtests = [b for b in backtests if b.get("status") == status]
        if strategy_id:
            backtests = [b for b in backtests if b.get("strategy_id") == strategy_id]
        # 按创建时间倒序
        backtests.sort(key=lambda b: b.get("created_at", ""), reverse=True)
        return backtests[:limit]

    def update_backtest(self, run_id: str, updates: Dict[str, Any]) -> Optional[Dict[str, Any]]:
        """Update backtest status/metrics"""
        backtest = self.backtests.get(run_id)
        if backtest:
            backtest.update(updates)
            backtest["updated_at"] = datetime.now(timezone.utc).isoformat() + "Z"
        return backtest

    # ==================== Risk Methods ====================

    def create_risk_limits(self, risk_data: Dict[str, Any]) -> Dict[str, Any]:
        """Create or update risk limits"""
        scope = risk_data.get("scope", "GLOBAL")
        version = len(self.risk_limits) + 1
        now = datetime.now(timezone.utc).isoformat() + "Z"

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

    def ingest_risk_event(self, event_data: Dict[str, Any]) -> tuple[Dict[str, Any], bool]:
        """Ingest risk event with dedup_key idempotency"""
        dedup_key = event_data["dedup_key"]
        existing = self.risk_events_by_key.get(dedup_key)
        if existing is not None:
            return existing, False

        now = datetime.now(timezone.utc).isoformat() + "Z"
        event = {
            **event_data,
            "ingested_at": now,
        }
        self.risk_events_by_key[dedup_key] = event
        return event, True

    def get_upgrade_record(self, upgrade_key: str) -> Optional[Dict[str, Any]]:
        """Get upgrade record by key"""
        return self.risk_upgrades.get(upgrade_key)

    def record_upgrade(self, upgrade_key: str, upgrade_data: Dict[str, Any]) -> None:
        """Record an upgrade action for idempotency"""
        now = datetime.now(timezone.utc).isoformat() + "Z"
        self.risk_upgrades[upgrade_key] = {
            **upgrade_data,
            "recorded_at": now,
        }

    def try_record_upgrade(self, upgrade_key: str, upgrade_data: Dict[str, Any]) -> bool:
        """
        Try to record an upgrade action. Returns True if first write, False if already exists.
        
        Args:
            upgrade_key: Unique upgrade key
            upgrade_data: Dictionary containing upgrade data
            
        Returns:
            True if this is the first time recording this upgrade_key, False if already exists
        """
        if upgrade_key in self.risk_upgrades:
            return False
        now = datetime.now(timezone.utc).isoformat() + "Z"
        self.risk_upgrades[upgrade_key] = {
            **upgrade_data,
            "recorded_at": now,
        }
        return True

    def try_record_upgrade_with_effect(self, upgrade_key: str, scope: str, level: int,
                                        reason: str, dedup_key: str) -> Tuple[bool, bool]:
        """
        Atomically record upgrade and side-effect intent.
        
        Returns:
            Tuple of (is_first_upgrade, is_first_effect)
        """
        now = datetime.now(timezone.utc).isoformat() + "Z"
        
        is_first_upgrade = upgrade_key not in self.risk_upgrades
        if is_first_upgrade:
            self.risk_upgrades[upgrade_key] = {
                "scope": scope,
                "level": level,
                "reason": reason,
                "dedup_key": dedup_key,
                "recorded_at": now,
            }
        
        is_first_effect = upgrade_key not in self.risk_upgrade_effects
        if is_first_effect:
            self.risk_upgrade_effects[upgrade_key] = {
                "scope": scope,
                "level": level,
                "status": "PENDING",
                "attempts": 1,
                "last_error": None,
                "updated_at": now,
            }
        
        return is_first_upgrade, is_first_effect

    def mark_effect_applied(self, upgrade_key: str) -> None:
        """Mark side-effect as successfully applied"""
        if upgrade_key in self.risk_upgrade_effects:
            self.risk_upgrade_effects[upgrade_key]["status"] = "APPLIED"
            self.risk_upgrade_effects[upgrade_key]["updated_at"] = datetime.now(timezone.utc).isoformat() + "Z"

    def mark_effect_failed(self, upgrade_key: str, error: str) -> None:
        """Mark side-effect as failed with error message"""
        if upgrade_key in self.risk_upgrade_effects:
            self.risk_upgrade_effects[upgrade_key]["status"] = "FAILED"
            self.risk_upgrade_effects[upgrade_key]["last_error"] = error
            self.risk_upgrade_effects[upgrade_key]["attempts"] = self.risk_upgrade_effects[upgrade_key].get("attempts", 0) + 1
            self.risk_upgrade_effects[upgrade_key]["updated_at"] = datetime.now(timezone.utc).isoformat() + "Z"

    def get_pending_effects(self) -> List[Dict[str, Any]]:
        """Get all pending or failed effects for recovery"""
        return [
            {**effect, "upgrade_key": key}
            for key, effect in self.risk_upgrade_effects.items()
            if effect.get("status") in ("PENDING", "FAILED")
        ]

    def ingest_event_with_upgrade(self, event_data: Dict[str, Any], 
                                  upgrade_key: str, upgrade_level: int) -> Tuple[Optional[str], bool, bool, bool]:
        """
        Atomically ingest risk event and record upgrade with effect.
        
        This implements: BEGIN -> dedup -> upgrade record -> side-effect intent -> COMMIT
        
        Args:
            event_data: Dictionary containing full event data
            upgrade_key: The upgrade key
            upgrade_level: Target level for upgrade
            
        Returns:
            Tuple of (event_id, created, is_first_upgrade, is_first_effect)
        """
        event_id = event_data.get("event_id") or str(uuid.uuid4())
        dedup_key = event_data["dedup_key"]
        scope = event_data.get("scope", "GLOBAL")
        reason = event_data.get("reason", "")
        
        created = False
        if dedup_key not in self.risk_events_by_key:
            now = datetime.now(timezone.utc).isoformat() + "Z"
            self.risk_events_by_key[dedup_key] = {
                "event_id": event_id,
                "dedup_key": dedup_key,
                "scope": scope,
                "reason": reason,
                "recommended_level": event_data.get("recommended_level", 0),
                "ingested_at": now,
                "data": event_data,
            }
            created = True
        else:
            event_id = self.risk_events_by_key[dedup_key]["event_id"]
        
        is_first_upgrade = upgrade_key not in self.risk_upgrades
        if is_first_upgrade:
            now = datetime.now(timezone.utc).isoformat() + "Z"
            self.risk_upgrades[upgrade_key] = {
                "scope": scope,
                "level": upgrade_level,
                "reason": reason,
                "dedup_key": dedup_key,
                "recorded_at": now,
            }
        
        is_first_effect = upgrade_key not in self.risk_upgrade_effects
        if is_first_effect:
            now = datetime.now(timezone.utc).isoformat() + "Z"
            self.risk_upgrade_effects[upgrade_key] = {
                "scope": scope,
                "level": upgrade_level,
                "status": "PENDING",
                "attempts": 1,
                "last_error": None,
                "updated_at": now,
            }
        
        return event_id, created, is_first_upgrade, is_first_effect

    # ==================== Order Methods ====================

    def create_order(self, order_data: Dict[str, Any]) -> Dict[str, Any]:
        """Create an order"""
        cl_ord_id = order_data["cl_ord_id"]
        now = datetime.now(timezone.utc)
        order = {
            **order_data,
            "status": order_data.get("status", OrderStatus.SUBMITTED.value),
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
            "updated_ts_ms": int(datetime.now(timezone.utc).timestamp() * 1000),
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
            "updated_ts_ms": int(datetime.now(timezone.utc).timestamp() * 1000),
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
        """Save a snapshot (appends to history)"""
        self._snapshot_counter += 1
        snapshot = {
            **snapshot_data,
            "snapshot_id": self._snapshot_counter,
            "created_at": datetime.now(timezone.utc).isoformat() + "Z",
        }
        stream_key = snapshot.get("stream_key")
        if stream_key not in self.snapshots:
            self.snapshots[stream_key] = []
        self.snapshots[stream_key].append(snapshot)
        return snapshot

    def get_latest_snapshot(self, stream_key: str) -> Optional[Dict[str, Any]]:
        """Get latest snapshot for a stream"""
        history = self.snapshots.get(stream_key)
        if history:
            return history[-1]
        return None

    def list_snapshots(
        self,
        stream_key: str,
        since_ts_ms: Optional[int] = None,
        until_ts_ms: Optional[int] = None,
        limit: int = 100,
    ) -> List[Dict[str, Any]]:
        """List snapshots for a stream with time range filter"""
        history = self.snapshots.get(stream_key, [])
        if since_ts_ms:
            history = [s for s in history if s.get("ts_ms", 0) >= since_ts_ms]
        if until_ts_ms:
            history = [s for s in history if s.get("ts_ms", 0) <= until_ts_ms]
        return history[-limit:] if history else []

    # ==================== KillSwitch Methods ====================

    def set_kill_switch(self, scope: str, level: int, reason: Optional[str], updated_by: str) -> Dict[str, Any]:
        """Set kill switch level"""
        now = datetime.now(timezone.utc).isoformat() + "Z"
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
                "updated_at": datetime.now(timezone.utc).isoformat() + "Z",
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
                "last_heartbeat_ts_ms": int(datetime.now(timezone.utc).timestamp() * 1000),
                "last_error": None,
            }
        return None


# Global storage instance
_storage: Optional[ControlPlaneInMemoryStorage] = None


def get_storage() -> ControlPlaneInMemoryStorage:
    """Get the global storage instance"""
    global _storage
    if _storage is None:
        _storage = ControlPlaneInMemoryStorage()
    return _storage


def reset_storage() -> ControlPlaneInMemoryStorage:
    """Reset the storage (for testing)"""
    global _storage
    _storage = ControlPlaneInMemoryStorage()
    return _storage


InMemoryStorage = ControlPlaneInMemoryStorage
