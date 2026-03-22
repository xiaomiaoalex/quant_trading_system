"""
Monitor API Routes
==================
System monitoring and alerting endpoints.
"""
import asyncio
from fastapi import APIRouter, Query

from trader.api.models.schemas import MonitorSnapshot, Alert, AlertRule
from trader.services import MonitorService


router = APIRouter(tags=["Monitor"])


# Singleton MonitorService instance with proper synchronization
_monitor_service: MonitorService | None = None
_init_lock: asyncio.Lock = asyncio.Lock()  # Single lock for initialization


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


@router.get("/v1/monitor/snapshot", response_model=MonitorSnapshot)
async def get_monitor_snapshot(
    open_orders_count: int = Query(0, description="未成交订单数"),
    pending_orders_count: int = Query(0, description="待处理订单数"),
    daily_pnl: str = Query("0", description="当日盈亏"),
    daily_pnl_pct: str = Query("0", description="当日盈亏百分比"),
    realized_pnl: str = Query("0", description="已实现盈亏"),
    unrealized_pnl: str = Query("0", description="未实现盈亏"),
    killswitch_level: int = Query(0, ge=0, le=3, description="KillSwitch级别"),
    killswitch_scope: str = Query("GLOBAL", description="KillSwitch范围"),
) -> MonitorSnapshot:
    """
    获取系统监控快照。
    
    返回完整的系统状态快照，包括：
    - 持仓信息
    - 订单信息
    - PnL信息
    - KillSwitch状态
    - 适配器健康状态
    - 活跃告警列表
    
    注意：持仓信息（total_positions, total_exposure）需要从 OMS 或 PortfolioService 获取。
    TODO: 集成 OMS/PortfolioService 获取实时持仓数据。
    """
    service = await get_monitor_service()
    
    # TODO: 从 OMS 或 PortfolioService 获取持仓列表
    # positions = await oms_service.get_positions()
    # positions = await portfolio_service.get_positions()
    positions = None  # 暂不传入持仓列表
    
    return service.get_snapshot(
        positions=positions,
        open_orders_count=open_orders_count,
        pending_orders_count=pending_orders_count,
        daily_pnl=daily_pnl,
        daily_pnl_pct=daily_pnl_pct,
        realized_pnl=realized_pnl,
        unrealized_pnl=unrealized_pnl,
        killswitch_level=killswitch_level,
        killswitch_scope=killswitch_scope,
    )


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