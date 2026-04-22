"""
Monitor API Routes
==================
System monitoring and alerting endpoints.
"""
import asyncio
import logging
from dataclasses import dataclass
from typing import List, Optional
from fastapi import APIRouter, Query

logger = logging.getLogger(__name__)

from trader.api.models.schemas import MonitorSnapshot, Alert, AlertRule, PositionView, PositionDetail
from trader.services import MonitorService, PortfolioService, OrderService, KillSwitchService


router = APIRouter(tags=["Monitor"])


# Singleton instances with proper synchronization
_monitor_service: MonitorService | None = None
_portfolio_service: PortfolioService | None = None
_init_lock: asyncio.Lock = asyncio.Lock()  # Single lock for initialization


@dataclass
class PositionForExposure:
    """
    持仓数据用于计算敞口。
    
    统一接口：quantity（数量）和 current_price（当前价格）
    """
    quantity: float
    current_price: float


async def get_monitor_service() -> MonitorService:
    """
    获取或创建 MonitorService 单例（async-safe）
    
    FastAPI routes are all async, so we only need one async-safe initialization.
    The asyncio.Lock ensures only one coroutine initializes the singleton.
    """
    global _monitor_service
    
    if _monitor_service is not None:
        return _monitor_service
    
    async with _init_lock:
        # Double-check after acquiring lock
        if _monitor_service is None:
            _monitor_service = MonitorService()
    
    return _monitor_service


async def get_portfolio_service() -> PortfolioService:
    """
    获取或创建 PortfolioService 单例（async-safe）
    
    使用与 MonitorService 相同的锁机制保证线程安全。
    虽然 PortfolioService 初始化本身不需要 async 操作，但为了保证
    singleton 的线程安全，需要使用 async 锁来与其他 async 路由协调。
    """
    global _portfolio_service
    
    if _portfolio_service is not None:
        return _portfolio_service
    
    async with _init_lock:
        # Double-check after acquiring lock
        if _portfolio_service is None:
            _portfolio_service = PortfolioService()
    
    return _portfolio_service


@router.get("/v1/monitor/snapshot", response_model=MonitorSnapshot)
async def get_monitor_snapshot(
    open_orders_count: int | None = None,
    pending_orders_count: int | None = None,
    daily_pnl: str | None = None,
    daily_pnl_pct: str | None = None,
    realized_pnl: str | None = None,
    unrealized_pnl: str | None = None,
    killswitch_level: int | None = None,
    killswitch_scope: str | None = None,
) -> MonitorSnapshot:
    """
    获取系统监控快照（真聚合版本 - Task 9.2）。
    
    后端内部聚合 orders/pnl/killswitch/adapters 数据，无需前端传入。
    返回完整的系统状态快照，包括：
    - 持仓信息（从 PortfolioService 获取）
    - 订单信息（从 OrderService 聚合）
    - PnL 信息（从 PortfolioService 获取）
    - KillSwitch 状态（从 KillSwitchService 获取）
    - 适配器健康状态
    - 活跃告警列表
    
    注意：query 参数仅用于测试覆盖，生产环境应省略参数以获取真实数据。
    """
    from datetime import datetime, timezone
    
    service = await get_monitor_service()
    portfolio_svc = await get_portfolio_service()
    order_svc = OrderService()
    killswitch_svc = KillSwitchService()
    
    # 从 PortfolioService 获取持仓列表
    positions_for_exposure: List[PositionForExposure] = []
    positions_detail: List[PositionDetail] = []
    try:
        raw_positions: List[PositionView] = portfolio_svc.list_positions()
        for p in raw_positions:
            qty = float(p.qty) if p.qty and p.qty.strip() else 0.0
            avg_cost = float(p.avg_cost) if p.avg_cost and p.avg_cost.strip() else 0.0
            current_price = float(p.mark_price) if p.mark_price and p.mark_price.strip() else 0.0
            unrealized_pnl_val = float(p.unrealized_pnl) if p.unrealized_pnl and p.unrealized_pnl.strip() else 0.0
            exposure_val = qty * current_price

            positions_for_exposure.append(PositionForExposure(
                quantity=qty,
                current_price=current_price,
            ))
            positions_detail.append(PositionDetail(
                symbol=p.instrument,
                quantity=str(qty),
                avg_cost=str(avg_cost) if avg_cost > 0 else None,
                current_price=str(current_price) if current_price > 0 else None,
                unrealized_pnl=str(unrealized_pnl_val) if unrealized_pnl_val != 0 else None,
                exposure=str(exposure_val),
            ))
    except (ConnectionError, TimeoutError) as e:
        logger.error(
            "Failed to fetch positions for monitor snapshot - connection error, using empty list",
            extra={"error": str(e), "error_type": type(e).__name__}
        )
    except Exception as e:
        logger.error(
            "Unexpected error fetching positions for monitor snapshot, using empty list",
            extra={"error": str(e), "error_type": type(e).__name__}
        )
    
    # 如果测试传入了参数，使用测试参数；否则从服务聚合真实数据
    if open_orders_count is None or pending_orders_count is None:
        # 从 OrderService 聚合订单统计
        open_orders_count = 0
        pending_orders_count = 0
        try:
            all_orders = order_svc.list_orders(limit=10000)
            # NEW = 已提交但未成交
            # SUBMITTED = 已提交
            # PARTIALLY_FILLED = 部分成交
            open_orders_count = sum(
                1 for o in all_orders 
                if o.status in ("NEW", "SUBMITTED", "PARTIALLY_FILLED")
            )
            pending_orders_count = sum(
                1 for o in all_orders 
                if o.status in ("PENDING", "CREATED")
            )
        except Exception as e:
            logger.error(
                "Failed to aggregate order counts for monitor snapshot",
                extra={"error": str(e), "error_type": type(e).__name__}
            )
    
    # 如果测试传入了 PnL 参数，使用测试参数；否则从服务获取真实数据
    if daily_pnl is None or unrealized_pnl is None:
        daily_pnl = "0"
        daily_pnl_pct = "0"
        realized_pnl = "0"
        unrealized_pnl = "0"
        try:
            pnl = portfolio_svc.get_pnl()
            realized_pnl = pnl.realized_pnl
            unrealized_pnl = pnl.unrealized_pnl
            total_pnl = float(pnl.total_pnl) if pnl.total_pnl else 0.0
            daily_pnl = pnl.total_pnl
            
            # 计算百分比：PnL / 总敞口 * 100
            if positions_for_exposure and len(positions_for_exposure) > 0:
                total_exposure_val = sum(
                    p.quantity * p.current_price for p in positions_for_exposure
                )
                if total_exposure_val > 0:
                    daily_pnl_pct = str(round((total_pnl / total_exposure_val) * 100, 2))
                else:
                    daily_pnl_pct = "0"
            else:
                daily_pnl_pct = "0"  # 无敞口时无法计算百分比
        except Exception as e:
            logger.error(
                "Failed to fetch PnL for monitor snapshot",
                extra={"error": str(e), "error_type": type(e).__name__}
            )
    
    # 如果测试传入了 killswitch 参数，使用测试参数；否则从服务获取真实状态
    if killswitch_level is None or killswitch_scope is None:
        killswitch_level = 0
        killswitch_scope = "GLOBAL"
        try:
            ks_state = killswitch_svc.get_state("GLOBAL")
            killswitch_level = ks_state.level
            killswitch_scope = ks_state.scope
        except Exception as e:
            logger.error(
                "Failed to fetch killswitch state for monitor snapshot",
                extra={"error": str(e), "error_type": type(e).__name__}
            )
    
    # 从 BrokerService 获取适配器健康状态
    try:
        from trader.services.broker import BrokerService
        broker_service = BrokerService()
        brokers = broker_service.list_brokers()
        for broker in brokers:
            # 每个注册的 broker 都被视为一个 adapter
            # 健康状态从 broker_service 获取
            try:
                status = broker_service.get_status(broker.account_id)
                service.update_adapter_health(
                    adapter_name=f"broker:{broker.account_id}",
                    status="HEALTHY" if status and status.connected else "DOWN",
                    last_heartbeat_ts_ms=status.last_heartbeat_ts_ms if status else None,
                    error_count=1 if status and status.last_error else 0,
                    message=status.last_error if status else "No status available",
                )
            except Exception as broker_err:
                # 单个 broker 状态获取失败不影响其他 broker
                logger.warning(
                    f"Failed to get status for broker {broker.account_id}",
                    extra={"error": str(broker_err)}
                )
                service.update_adapter_health(
                    adapter_name=f"broker:{broker.account_id}",
                    status="DOWN",
                    last_heartbeat_ts_ms=None,
                    error_count=1,
                    message=f"Status check failed: {broker_err}",
                )
    except Exception as e:
        # 降低日志级别：无 broker 注册是正常情况
        logger.warning(
            "No brokers registered or failed to fetch adapter health for monitor snapshot",
            extra={"error": str(e), "error_type": type(e).__name__}
        )
    
    # Task 19: 从 OMSCallbackHandler 获取可观测性指标
    try:
        from trader.api.routes.strategies import get_oms_metrics
        oms_metrics = get_oms_metrics()
        if oms_metrics:
            snapshot = service.get_snapshot(
                positions=positions_for_exposure,
                open_orders_count=open_orders_count or 0,
                pending_orders_count=pending_orders_count or 0,
                daily_pnl=daily_pnl or "0",
                daily_pnl_pct=daily_pnl_pct or "0",
                realized_pnl=realized_pnl or "0",
                unrealized_pnl=unrealized_pnl or "0",
                killswitch_level=killswitch_level or 0,
                killswitch_scope=killswitch_scope or "GLOBAL",
            )
            # Task 19: 填充 OMS 可观测性指标到快照
            snapshot.order_submit_ok = oms_metrics.get("order_submit_ok", 0)
            snapshot.order_submit_reject = oms_metrics.get("order_submit_reject", 0)
            snapshot.order_submit_error = oms_metrics.get("order_submit_error", 0)
            snapshot.reject_reason_counts = oms_metrics.get("reject_reason_counts", {})
            snapshot.fill_latency_ms_avg = oms_metrics.get("fill_latency_ms_avg")
            snapshot.fill_latency_count = oms_metrics.get("fill_latency_count", 0)
            snapshot.cl_ord_id_dedup_hits = oms_metrics.get("cl_ord_id_dedup_hits", 0)
            snapshot.exec_dedup_hits = oms_metrics.get("exec_dedup_hits", 0)
        else:
            snapshot = service.get_snapshot(
                positions=positions_for_exposure,
                open_orders_count=open_orders_count or 0,
                pending_orders_count=pending_orders_count or 0,
                daily_pnl=daily_pnl or "0",
                daily_pnl_pct=daily_pnl_pct or "0",
                realized_pnl=realized_pnl or "0",
                unrealized_pnl=unrealized_pnl or "0",
                killswitch_level=killswitch_level or 0,
                killswitch_scope=killswitch_scope or "GLOBAL",
            )
    except Exception as e:
        logger.error(
            "Failed to fetch OMS metrics for monitor snapshot",
            extra={"error": str(e), "error_type": type(e).__name__}
        )
        snapshot = service.get_snapshot(
            positions=positions_for_exposure,
            open_orders_count=open_orders_count or 0,
            pending_orders_count=pending_orders_count or 0,
            daily_pnl=daily_pnl or "0",
            daily_pnl_pct=daily_pnl_pct or "0",
            realized_pnl=realized_pnl or "0",
            unrealized_pnl=unrealized_pnl or "0",
            killswitch_level=killswitch_level or 0,
            killswitch_scope=killswitch_scope or "GLOBAL",
        )
    
    # 添加元信息
    snapshot.snapshot_source = "aggregated"
    snapshot.freshness = datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")

    # 添加详细持仓信息
    snapshot.positions = positions_detail

    return snapshot


@router.get("/v1/monitor/alerts", response_model=list[Alert])
async def get_active_alerts() -> list[Alert]:
    """
    获取当前活跃告警列表。
    
    返回所有未过冷却期的告警。
    """
    service = await get_monitor_service()
    return service.get_active_alerts()


@router.post("/v1/monitor/rules", response_model=AlertRule)
async def add_alert_rule(rule: AlertRule) -> AlertRule:
    """
    添加或更新告警规则。
    
    如果规则名已存在，则更新现有规则。
    """
    service = await get_monitor_service()
    service.add_alert_rule(rule)
    return rule


@router.delete("/v1/monitor/rules/{rule_name}")
async def remove_alert_rule(rule_name: str) -> dict:
    """
    移除告警规则。
    """
    service = await get_monitor_service()
    removed = service.remove_alert_rule(rule_name)
    return {"ok": removed, "rule_name": rule_name}


@router.post("/v1/monitor/alerts/{rule_name}/clear")
async def clear_alert(rule_name: str) -> dict:
    """
    清除指定告警规则的触发状态。
    """
    service = await get_monitor_service()
    cleared = service.clear_alert(rule_name)
    return {"ok": cleared, "rule_name": rule_name}


@router.post("/v1/monitor/alerts/clear-all")
async def clear_all_alerts() -> dict:
    """
    清除所有告警触发状态。
    """
    service = await get_monitor_service()
    service.clear_all_alerts()
    return {"ok": True}