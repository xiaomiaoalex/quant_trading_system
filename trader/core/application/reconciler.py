from dataclasses import dataclass, field
from datetime import datetime, timezone, timedelta
from decimal import Decimal, InvalidOperation
from enum import Enum
import logging
from typing import List, Optional, Dict, Any, Callable, Set

logger = logging.getLogger(__name__)


def _normalize_quantity(value: str) -> Decimal:
    """Normalize quantity string to Decimal for precise comparison."""
    try:
        return Decimal(str(value))
    except (InvalidOperation, ValueError):
        return Decimal("0")


class DriftType(str, Enum):
    GHOST = "GHOST"
    PHANTOM = "PHANTOM"
    DIVERGED = "DIVERGED"


@dataclass
class OrderDrift:
    cl_ord_id: str
    drift_type: DriftType
    local_status: Optional[str]
    exchange_status: Optional[str]
    detected_at: datetime
    local_updated_at: Optional[datetime] = None
    exchange_updated_at: Optional[datetime] = None
    grace_period_remaining_sec: Optional[float] = None
    symbol: Optional[str] = None
    quantity: Optional[str] = None
    filled_quantity: Optional[str] = None
    exchange_filled_quantity: Optional[str] = None
    # 订单归属类型（用于区分 OWNED/EXTERNAL/UNKNOWN）
    ownership: Optional[str] = None  # "OWNED" / "EXTERNAL" / "UNKNOWN"


@dataclass
class ReconcileReport:
    timestamp: datetime
    total_orders_checked: int
    drifts: List[OrderDrift]
    ghost_count: int = 0
    phantom_count: int = 0
    diverged_count: int = 0
    within_grace_period_count: int = 0
    # 新增统计字段：外部订单数（不触发 PHANTOM 告警）
    external_count: int = 0

    def __post_init__(self):
        self.ghost_count = sum(1 for d in self.drifts if d.drift_type == DriftType.GHOST)
        self.phantom_count = sum(1 for d in self.drifts if d.drift_type == DriftType.PHANTOM)
        self.diverged_count = sum(1 for d in self.drifts if d.drift_type == DriftType.DIVERGED)
        self.within_grace_period_count = sum(
            1 for d in self.drifts if d.grace_period_remaining_sec is not None and d.grace_period_remaining_sec > 0
        )


@dataclass
class LocalOrderSnapshot:
    cl_ord_id: str
    status: str
    symbol: str
    quantity: str
    filled_quantity: str
    created_at: datetime
    updated_at: datetime


@dataclass
class ExchangeOrderSnapshot:
    cl_ord_id: str
    status: str
    symbol: str
    quantity: str
    filled_quantity: str
    updated_at: datetime


class Reconciler:
    DEFAULT_GRACE_PERIOD_SEC = 60.0

    def __init__(self, grace_period_sec: float = DEFAULT_GRACE_PERIOD_SEC):
        self._grace_period = timedelta(seconds=grace_period_sec)

    def reconcile(
        self,
        local_orders: List[LocalOrderSnapshot],
        exchange_orders: List[ExchangeOrderSnapshot],
        external_order_ids: Optional[Set[str]] = None,
    ) -> ReconcileReport:
        """
        执行对账。

        参数:
        - local_orders: 本地订单快照
        - exchange_orders: 交易所订单快照
        - external_order_ids: 外部订单 ID 集合（不触发 PHANTOM 告警）

        返回:
        - ReconcileReport: 包含漂移检测结果和统计信息
        """
        now = datetime.now(timezone.utc)

        local_by_id = {o.cl_ord_id: o for o in local_orders}
        exchange_by_id = {o.cl_ord_id: o for o in exchange_orders}

        drifts: List[OrderDrift] = []
        external_count = 0

        # 外部/unknown订单集合（由调用方根据归属分类传入）
        # 如果为空，表示未启用归属分类（向后兼容旧行为）
        # 如果非空，只跳过在这个集合中的订单
        skipped_external = set() if external_order_ids is None else external_order_ids

        local_ids = set(local_by_id.keys())
        exchange_ids = set(exchange_by_id.keys())

        for cl_ord_id in local_ids - exchange_ids:
            local = local_by_id[cl_ord_id]
            drifts.append(self._create_ghost_drift(local, now))

        for cl_ord_id in exchange_ids - local_ids:
            # 跳过外部/unknown订单（不触发 PHANTOM 告警）
            # 只有当归属分类启用时（非空集合）才跳过
            if skipped_external and cl_ord_id in skipped_external:
                external_count += 1
                logger.debug(
                    f"[Reconciler] skipping PHANTOM for external/unknown order: {cl_ord_id}"
                )
                continue
            exchange = exchange_by_id[cl_ord_id]
            drifts.append(self._create_phantom_drift(exchange, now))

        for cl_ord_id in local_ids & exchange_ids:
            local = local_by_id[cl_ord_id]
            exchange = exchange_by_id[cl_ord_id]
            drift = self._check_diverged(local, exchange, now)
            if drift:
                drifts.append(drift)

        report = ReconcileReport(
            timestamp=now,
            total_orders_checked=len(local_orders) + len(exchange_orders),
            drifts=drifts,
            external_count=external_count,
        )
        return report

    def _create_ghost_drift(self, local: LocalOrderSnapshot, now: datetime) -> OrderDrift:
        grace_remaining = self._calculate_grace_period(local.created_at, now)
        return OrderDrift(
            cl_ord_id=local.cl_ord_id,
            drift_type=DriftType.GHOST,
            local_status=local.status,
            exchange_status=None,
            detected_at=now,
            local_updated_at=local.updated_at,
            symbol=local.symbol,
            quantity=local.quantity,
            filled_quantity=local.filled_quantity,
            grace_period_remaining_sec=grace_remaining,
        )

    def _create_phantom_drift(self, exchange: ExchangeOrderSnapshot, now: datetime) -> OrderDrift:
        return OrderDrift(
            cl_ord_id=exchange.cl_ord_id,
            drift_type=DriftType.PHANTOM,
            local_status=None,
            exchange_status=exchange.status,
            detected_at=now,
            exchange_updated_at=exchange.updated_at,
            symbol=exchange.symbol,
            quantity=exchange.quantity,
            filled_quantity=exchange.filled_quantity,
        )

    def _check_diverged(
        self,
        local: LocalOrderSnapshot,
        exchange: ExchangeOrderSnapshot,
        now: datetime,
    ) -> Optional[OrderDrift]:
        local_qty = _normalize_quantity(local.filled_quantity)
        exchange_qty = _normalize_quantity(exchange.filled_quantity)
        if local.status == exchange.status and local_qty == exchange_qty:
            return None

        grace_remaining = self._calculate_grace_period(local.created_at, now)
        return OrderDrift(
            cl_ord_id=local.cl_ord_id,
            drift_type=DriftType.DIVERGED,
            local_status=local.status,
            exchange_status=exchange.status,
            detected_at=now,
            local_updated_at=local.updated_at,
            exchange_updated_at=exchange.updated_at,
            symbol=local.symbol,
            quantity=local.quantity,
            filled_quantity=local.filled_quantity,
            exchange_filled_quantity=exchange.filled_quantity,
            grace_period_remaining_sec=grace_remaining,
        )

    def _calculate_grace_period(self, created_at: datetime, now: datetime) -> Optional[float]:
        if created_at.tzinfo is None:
            created_at = created_at.replace(tzinfo=timezone.utc)
        if now.tzinfo is None:
            now = now.replace(tzinfo=timezone.utc)

        elapsed = now - created_at
        remaining = self._grace_period - elapsed
        if remaining.total_seconds() < 0:
            return None
        return remaining.total_seconds()
