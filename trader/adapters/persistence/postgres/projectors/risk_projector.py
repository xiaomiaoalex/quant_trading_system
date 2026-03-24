"""
Risk Projector - 风控投影
=========================
将风控相关事件投影为 PostgreSQL 读模型。

事件类型：
- RISK_CHECK_PASSED: 风控检查通过
- RISK_CHECK_FAILED: 风控检查拒绝

投影表结构：
- aggregate_id: 风控范围 (scope)，如 "GLOBAL", "strategy:{name}", "account:{id}"
- state: JSONB 存储完整风控状态
- version: 版本号（乐观锁）
- last_event_seq: 最后处理的事件序列号
- updated_at: 更新时间

索引：
- 主键: aggregate_id
- scope 索引（用于按范围查询）
- level 索引（用于按级别过滤）
- updated_at 索引（用于时间排序）
"""
from dataclasses import dataclass
from datetime import datetime, timezone
from decimal import Decimal
from typing import Dict, List, Any, Optional

from trader.adapters.persistence.postgres.projectors.base import (
    Projectable,
)


# ==================== 数据类型 ====================

@dataclass
class RiskStateProjection:
    """
    风控状态投影数据类
    
    用于类型化的投影结果访问。
    """
    scope: str
    current_level: int
    last_check_result: str
    last_check_at: Optional[datetime]
    total_checks: int
    passed_checks: int
    failed_checks: int
    total_rejections: int
    max_drawdown_rejections: int
    position_limit_rejections: int
    exposure_rejections: int
    other_rejections: int
    last_rejection_reason: Optional[str]
    last_rejection_at: Optional[datetime]
    consecutive_rejections: int
    avg_rejection_rate: float
    updated_at: datetime
    version: int
    last_event_seq: int
    
    @classmethod
    def from_state(cls, scope: str, state: Dict[str, Any]) -> "RiskStateProjection":
        """从状态字典创建 RiskStateProjection"""
        last_check_at = state.get("last_check_at")
        if isinstance(last_check_at, str):
            last_check_at = datetime.fromisoformat(last_check_at)
        
        last_rejection_at = state.get("last_rejection_at")
        if isinstance(last_rejection_at, str):
            last_rejection_at = datetime.fromisoformat(last_rejection_at)
        
        updated_at = state.get("updated_at")
        if isinstance(updated_at, str):
            updated_at = datetime.fromisoformat(updated_at)
        elif updated_at is None:
            updated_at = datetime.now(timezone.utc)
        
        return cls(
            scope=scope,
            current_level=state.get("current_level", 0),
            last_check_result=state.get("last_check_result", "UNKNOWN"),
            last_check_at=last_check_at,
            total_checks=state.get("total_checks", 0),
            passed_checks=state.get("passed_checks", 0),
            failed_checks=state.get("failed_checks", 0),
            total_rejections=state.get("total_rejections", 0),
            max_drawdown_rejections=state.get("max_drawdown_rejections", 0),
            position_limit_rejections=state.get("position_limit_rejections", 0),
            exposure_rejections=state.get("exposure_rejections", 0),
            other_rejections=state.get("other_rejections", 0),
            last_rejection_reason=state.get("last_rejection_reason"),
            last_rejection_at=last_rejection_at,
            consecutive_rejections=state.get("consecutive_rejections", 0),
            avg_rejection_rate=state.get("avg_rejection_rate", 0.0),
            updated_at=updated_at,
            version=state.get("_version", 1),
            last_event_seq=state.get("_last_event_seq", 0),
        )
    
    def to_dict(self) -> Dict[str, Any]:
        """转换为字典"""
        return {
            "scope": self.scope,
            "current_level": self.current_level,
            "last_check_result": self.last_check_result,
            "last_check_at": self.last_check_at.isoformat() if isinstance(self.last_check_at, datetime) else self.last_check_at,
            "total_checks": self.total_checks,
            "passed_checks": self.passed_checks,
            "failed_checks": self.failed_checks,
            "total_rejections": self.total_rejections,
            "max_drawdown_rejections": self.max_drawdown_rejections,
            "position_limit_rejections": self.position_limit_rejections,
            "exposure_rejections": self.exposure_rejections,
            "other_rejections": self.other_rejections,
            "last_rejection_reason": self.last_rejection_reason,
            "last_rejection_at": self.last_rejection_at.isoformat() if isinstance(self.last_rejection_at, datetime) else self.last_rejection_at,
            "consecutive_rejections": self.consecutive_rejections,
            "avg_rejection_rate": self.avg_rejection_rate,
            "updated_at": self.updated_at.isoformat() if isinstance(self.updated_at, datetime) else self.updated_at,
            "version": self.version,
            "last_event_seq": self.last_event_seq,
        }
    
    @property
    def rejection_rate(self) -> float:
        """拒绝率"""
        if self.total_checks == 0:
            return 0.0
        return self.failed_checks / self.total_checks
    
    @property
    def is_healthy(self) -> bool:
        """风控状态是否健康（无连续拒绝）"""
        return self.consecutive_rejections < 3 and self.current_level == 0


# ==================== Rejection Reason Classification ====================

def classify_rejection_reason(reason: str) -> str:
    """
    分类拒绝原因
    
    Args:
        reason: 拒绝原因描述
        
    Returns:
        分类结果: max_drawdown | position_limit | exposure | other
    """
    reason_lower = reason.lower()
    
    if "drawdown" in reason_lower or "max loss" in reason_lower or "daily loss" in reason_lower:
        return "max_drawdown"
    elif "position limit" in reason_lower or "position size" in reason_lower or "max position" in reason_lower:
        return "position_limit"
    elif "exposure" in reason_lower or "margin" in reason_lower or "leverage" in reason_lower:
        return "exposure"
    else:
        return "other"


# ==================== RiskProjector ====================

class RiskProjector(Projectable):
    """
    风控投影
    
    将风控事件投影为可查询的读模型。
    """
    
    # 该投影处理的事件类型
    EVENT_TYPES = {
        "RISK_CHECK_PASSED",
        "RISK_CHECK_FAILED",
    }
    
    def __init__(self, pool: "asyncpg.Pool"):
        super().__init__(
            pool=pool,
            table_name="risk_states_proj",
            snapshot_table_name="risk_snapshots",
            event_types=list(self.EVENT_TYPES),
        )
    
    def get_projection_id_field(self) -> str:
        """主键字段名"""
        return "aggregate_id"
    
    def extract_aggregate_id(self, event: "StreamEvent") -> str:
        """从事件中提取风控范围"""
        # 风控事件使用 aggregate_id 作为 scope
        # 可能格式: "GLOBAL", "strategy:{name}", "account:{id}"
        return event.aggregate_id or "GLOBAL"
    
    def _init_risk_state(self) -> Dict[str, Any]:
        """初始化风控状态"""
        now = datetime.now(timezone.utc)
        return {
            "scope": "",
            "current_level": 0,
            "last_check_result": "UNKNOWN",
            "last_check_at": None,
            "total_checks": 0,
            "passed_checks": 0,
            "failed_checks": 0,
            "total_rejections": 0,
            "max_drawdown_rejections": 0,
            "position_limit_rejections": 0,
            "exposure_rejections": 0,
            "other_rejections": 0,
            "last_rejection_reason": None,
            "last_rejection_at": None,
            "consecutive_rejections": 0,
            "avg_rejection_rate": 0.0,
            "updated_at": now.isoformat(),
        }
    
    def _apply_risk_check_passed(self, state: Dict[str, Any], data: Dict[str, Any]) -> Dict[str, Any]:
        """应用风控检查通过事件"""
        state["last_check_result"] = "PASSED"
        state["last_check_at"] = data.get("timestamp") or datetime.now(timezone.utc).isoformat()
        state["total_checks"] += 1
        state["passed_checks"] += 1
        state["consecutive_rejections"] = 0  # 重置连续拒绝计数
        
        # 重新计算拒绝率
        if state["total_checks"] > 0:
            state["avg_rejection_rate"] = state["failed_checks"] / state["total_checks"]
        
        state["updated_at"] = datetime.now(timezone.utc).isoformat()
        return state
    
    def _apply_risk_check_failed(self, state: Dict[str, Any], data: Dict[str, Any]) -> Dict[str, Any]:
        """应用风控检查拒绝事件"""
        reason = data.get("reason", "Unknown")
        rejection_type = data.get("rejection_type")
        
        # 如果没有 rejection_type，尝试从 reason 分类
        if not rejection_type:
            rejection_type = classify_rejection_reason(reason)
        
        state["last_check_result"] = "FAILED"
        state["last_check_at"] = data.get("timestamp") or datetime.now(timezone.utc).isoformat()
        state["total_checks"] += 1
        state["failed_checks"] += 1
        state["total_rejections"] += 1
        state["consecutive_rejections"] += 1
        state["last_rejection_reason"] = reason
        state["last_rejection_at"] = datetime.now(timezone.utc).isoformat()
        
        # 按类型统计
        if rejection_type == "max_drawdown":
            state["max_drawdown_rejections"] += 1
        elif rejection_type == "position_limit":
            state["position_limit_rejections"] += 1
        elif rejection_type == "exposure":
            state["exposure_rejections"] += 1
        else:
            state["other_rejections"] += 1
        
        # 重新计算拒绝率
        if state["total_checks"] > 0:
            state["avg_rejection_rate"] = state["failed_checks"] / state["total_checks"]
        
        state["updated_at"] = datetime.now(timezone.utc).isoformat()
        return state
    
    def compute_projection(
        self,
        aggregate_id: str,
        events: List["StreamEvent"],
    ) -> Dict[str, Any]:
        """
        计算风控投影
        
        通过重放事件流来计算当前风控状态。
        
        Args:
            aggregate_id: 风控范围（scope）
            events: 按时间顺序排列的事件列表
            
        Returns:
            风控投影状态
        """
        state = self._init_risk_state()
        state["scope"] = aggregate_id
        
        for event in events:
            data = event.data if isinstance(event.data, dict) else {}
            
            if event.event_type == "RISK_CHECK_PASSED":
                state = self._apply_risk_check_passed(state, data)
            elif event.event_type == "RISK_CHECK_FAILED":
                state = self._apply_risk_check_failed(state, data)
        
        return state
    
    async def get_risk_state(
        self,
        scope: str = "GLOBAL",
    ) -> Optional[RiskStateProjection]:
        """
        获取风控状态投影
        
        Args:
            scope: 风控范围，默认 GLOBAL
            
        Returns:
            RiskStateProjection 或 None
        """
        projection = await self.get_projection(scope)
        if projection is None:
            return None
        
        state = projection["state"]
        return RiskStateProjection.from_state(scope, state)
    
    async def list_risk_states(
        self,
        min_level: Optional[int] = None,
        has_consecutive_rejections: Optional[bool] = None,
        limit: int = 100,
        offset: int = 0,
    ) -> List[RiskStateProjection]:
        """
        列出风控状态投影
        
        Args:
            min_level: 最小 KillSwitch 级别过滤
            has_consecutive_rejections: 是否有连续拒绝
            limit: 最大返回数量
            offset: 偏移量
            
        Returns:
            RiskStateProjection 列表
        """
        projections = await self.list_projections(limit=limit * 2, offset=offset)
        
        results = []
        for proj in projections:
            risk = RiskStateProjection.from_state(proj["aggregate_id"], proj["state"])
            
            # 应用过滤器
            if min_level is not None and risk.current_level < min_level:
                continue
            if has_consecutive_rejections is not None:
                has_consecutive = risk.consecutive_rejections >= 3
                if has_consecutive != has_consecutive_rejections:
                    continue
            
            results.append(risk)
            
            if len(results) >= limit:
                break
        
        return results
    
    async def get_risk_summary(self) -> Dict[str, Any]:
        """
        获取风控汇总
        
        Returns:
            风控汇总信息
        """
        projections = await self.list_projections(limit=10000)
        
        total_checks = 0
        total_passed = 0
        total_failed = 0
        total_rejections = 0
        by_level: Dict[int, int] = {}
        unhealthy_scopes = []
        
        for proj in projections:
            risk = RiskStateProjection.from_state(proj["aggregate_id"], proj["state"])
            total_checks += risk.total_checks
            total_passed += risk.passed_checks
            total_failed += risk.failed_checks
            total_rejections += risk.total_rejections
            
            if risk.current_level > 0:
                by_level[risk.current_level] = by_level.get(risk.current_level, 0) + 1
            
            if not risk.is_healthy:
                unhealthy_scopes.append(risk.scope)
        
        return {
            "total_checks": total_checks,
            "total_passed": total_passed,
            "total_failed": total_failed,
            "total_rejections": total_rejections,
            "overall_rejection_rate": total_failed / total_checks if total_checks > 0 else 0.0,
            "scopes_with_alerts": len(unhealthy_scopes),
            "unhealthy_scopes": unhealthy_scopes,
            "by_level": by_level,
        }


# 类型注解循环依赖处理
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from trader.adapters.persistence.postgres.event_store import StreamEvent
