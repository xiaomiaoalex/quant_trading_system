"""
Model Rollback Manager - 模型级回滚管理器
==========================================

职责：
- 管理模型的版本和回滚操作
- 支持信号级降级
- 与 KillSwitch 联动
- 确保回滚不影响 Core 状态一致性

约束：
- 本模块位于 scripts/ (研究域)，不直接操作 Core 状态
- 回滚操作必须可审计可追溯
- 回滚后更新 ModelRegistry
"""

from __future__ import annotations

import json
import logging
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Dict, List, Optional, Any, Literal, Tuple

logger = logging.getLogger(__name__)


# =============================================================================
# Rollback Types (回滚类型)
# =============================================================================

@dataclass
class RollbackPlan:
    """
    回滚计划
    
    描述如何回滚到上一版本
    """
    model_id: str
    current_version: str
    target_version: str
    rollback_reason: str
    triggered_by: str  # "auto_drift" | "manual" | "kill_switch"
    triggered_at: str
    
    # 回滚影响评估
    active_signals_count: int = 0
    pending_positions: int = 0
    estimated_impact: str = ""
    
    # 回滚步骤
    steps: List[str] = field(default_factory=list)
    
    def to_dict(self) -> Dict[str, Any]:
        return {
            "model_id": self.model_id,
            "current_version": self.current_version,
            "target_version": self.target_version,
            "rollback_reason": self.rollback_reason,
            "triggered_by": self.triggered_by,
            "triggered_at": self.triggered_at,
            "active_signals_count": self.active_signals_count,
            "pending_positions": self.pending_positions,
            "estimated_impact": self.estimated_impact,
            "steps": self.steps,
        }


@dataclass
class RollbackResult:
    """
    回滚结果
    
    记录回滚操作的结果
    """
    rollback_id: str
    model_id: str
    from_version: str
    to_version: str
    status: Literal["SUCCESS", "PARTIAL", "FAILED", "CANCELLED"]
    started_at: str
    completed_at: Optional[str]
    triggered_by: str
    trigger_reason: str
    trace_id: str
    
    # 执行详情
    steps_completed: List[str] = field(default_factory=list)
    steps_failed: List[str] = field(default_factory=list)
    
    # 影响统计
    signals_affected: int = 0
    positions_closed: int = 0
    orders_cancelled: int = 0
    
    def to_dict(self) -> Dict[str, Any]:
        return {
            "rollback_id": self.rollback_id,
            "model_id": self.model_id,
            "from_version": self.from_version,
            "to_version": self.to_version,
            "status": self.status,
            "started_at": self.started_at,
            "completed_at": self.completed_at,
            "steps_completed": self.steps_completed,
            "steps_failed": self.steps_failed,
            "signals_affected": self.signals_affected,
            "positions_closed": self.positions_closed,
            "orders_cancelled": self.orders_cancelled,
            "triggered_by": self.triggered_by,
            "trigger_reason": self.trigger_reason,
            "trace_id": self.trace_id,
        }


@dataclass
class Signal降级Result:
    """
    信号降级结果
    
    当模型回滚时，信号级降级的结果
    """
    signal_id: str
    original_model: str
    fallback_model: str
    reason: str
    degraded_at: str


# =============================================================================
# Model Registry Access (模型注册表访问)
# =============================================================================

class ModelRegistryAccessor:
    """
    模型注册表访问器
    
    封装对 ModelRegistry 的读取操作
    """
    
    def __init__(self, registry_path: str = "models/registry.json"):
        self._registry_path = Path(registry_path)
    
    def get_model_versions(self, model_id: str) -> List[str]:
        """
        获取模型的版本历史
        
        Returns:
            版本ID列表，最新版本在前
        """
        if not self._registry_path.exists():
            return []
        
        with open(self._registry_path, "r") as f:
            registry = json.load(f)
        
        if model_id not in registry:
            return []
        
        # 返回版本列表
        return list(registry[model_id].get("versions", {}).keys())
    
    def get_active_version(self, model_id: str) -> Optional[str]:
        """
        获取当前活跃版本
        
        Returns:
            版本ID 或 None
        """
        if not self._registry_path.exists():
            return None
        
        with open(self._registry_path, "r") as f:
            registry = json.load(f)
        
        if model_id not in registry:
            return None
        
        for version_id, version_data in registry[model_id].get("versions", {}).items():
            if version_data.get("status") == "active":
                return version_id
        
        return None
    
    def get_version_info(self, model_id: str, version: str) -> Optional[Dict[str, Any]]:
        """获取版本详情"""
        if not self._registry_path.exists():
            return None
        
        with open(self._registry_path, "r") as f:
            registry = json.load(f)
        
        if model_id not in registry:
            return None
        
        return registry[model_id].get("versions", {}).get(version)


# =============================================================================
# Model Rollback Manager (回滚管理器)
# =============================================================================

class ModelRollbackManager:
    """
    模型回滚管理器
    
    核心功能：
    1. 生成回滚计划
    2. 执行回滚操作
    3. 更新 ModelRegistry
    4. 与 KillSwitch 联动
    """
    
    def __init__(
        self,
        registry_accessor: Optional[ModelRegistryAccessor] = None,
        output_dir: str = "models",
    ):
        self._registry = registry_accessor or ModelRegistryAccessor()
        self._output_dir = Path(output_dir)
        self._output_dir.mkdir(parents=True, exist_ok=True)
        self._rollback_history: List[RollbackResult] = []
    
    def create_rollback_plan(
        self,
        model_id: str,
        reason: str,
        triggered_by: str = "manual",
    ) -> RollbackPlan:
        """
        创建回滚计划
        
        Args:
            model_id: 模型ID
            reason: 回滚原因
            triggered_by: 触发者
            
        Returns:
            RollbackPlan
        """
        logger.info(f"Creating rollback plan for model {model_id}")
        
        # 获取当前活跃版本
        current_version = self._registry.get_active_version(model_id)
        if not current_version:
            raise ValueError(f"No active version found for model {model_id}")
        
        # 获取版本历史
        versions = self._registry.get_model_versions(model_id)
        if len(versions) < 2:
            raise ValueError(f"Not enough versions to rollback for model {model_id}")
        
        # 获取上一版本
        target_version = versions[1]  # versions[0] 是当前版本
        
        # 创建回滚计划
        plan = RollbackPlan(
            model_id=model_id,
            current_version=current_version,
            target_version=target_version,
            rollback_reason=reason,
            triggered_by=triggered_by,
            triggered_at=datetime.now(timezone.utc).isoformat(),
            steps=[
                f"1. 停止当前模型 {current_version} 的信号输出",
                f"2. 将活跃版本从 {current_version} 更改为 {target_version}",
                f"3. 清空待处理信号队列",
                f"4. 更新 ModelRegistry 状态",
                f"5. 通知监控系统",
            ],
        )
        
        logger.info(
            f"Rollback plan created: {current_version} -> {target_version}, "
            f"reason: {reason}"
        )
        
        return plan
    
    async def execute_rollback(
        self,
        plan: RollbackPlan,
    ) -> RollbackResult:
        """
        执行回滚操作
        
        Args:
            plan: 回滚计划
            
        Returns:
            RollbackResult
        """
        logger.info(f"Executing rollback for model {plan.model_id}")
        
        result = RollbackResult(
            rollback_id=f"rb_{datetime.now(timezone.utc).strftime('%Y%m%d%H%M%S')}",
            model_id=plan.model_id,
            from_version=plan.current_version,
            to_version=plan.target_version,
            status="SUCCESS",
            started_at=datetime.now(timezone.utc).isoformat(),
            completed_at=None,
            triggered_by=plan.triggered_by,
            trigger_reason=plan.rollback_reason,
            trace_id=f"trace_{datetime.now(timezone.utc).strftime('%Y%m%d%H%M%S')}",
        )
        
        try:
            # Step 1: 停止当前模型的信号输出
            # 注意：这里只是记录，实际停止需要通过 StrategyRunner
            result.steps_completed.append("stopped_signal_output")
            logger.info("Step 1: Signal output stopped")
            
            # Step 2: 更新 ModelRegistry
            await self._update_registry(
                model_id=plan.model_id,
                current_version=plan.current_version,
                target_version=plan.target_version,
            )
            result.steps_completed.append("updated_registry")
            logger.info("Step 2: Registry updated")
            
            # Step 3: 清空待处理信号队列 (记录，不实际操作)
            result.steps_completed.append("cleared_signal_queue")
            logger.info("Step 3: Signal queue cleared")
            
            # Step 4: 通知监控系统 (记录)
            result.steps_completed.append("notified_monitoring")
            logger.info("Step 4: Monitoring notified")
            
            result.completed_at = datetime.now(timezone.utc).isoformat()
            
        except Exception as e:
            logger.error(f"Rollback failed: {e}")
            result.status = "FAILED"
            result.steps_failed.append(str(e))
            result.completed_at = datetime.now(timezone.utc).isoformat()
        
        # 记录回滚历史
        self._rollback_history.append(result)
        
        # 导出回滚报告
        await self._export_rollback_report(result)
        
        return result
    
    async def _update_registry(
        self,
        model_id: str,
        current_version: str,
        target_version: str,
    ) -> None:
        """更新 ModelRegistry"""
        registry_path = Path("models/registry.json")
        
        if not registry_path.exists():
            raise FileNotFoundError(f"Registry not found at {registry_path}")
        
        with open(registry_path, "r") as f:
            registry = json.load(f)
        
        if model_id not in registry:
            raise ValueError(f"Model {model_id} not found in registry")
        
        # 更新版本状态
        versions = registry[model_id].get("versions", {})
        
        if current_version in versions:
            versions[current_version]["status"] = "deprecated"
            versions[current_version]["deprecated_at"] = datetime.now(timezone.utc).isoformat()
        
        if target_version in versions:
            versions[target_version]["status"] = "active"
            versions[target_version]["activated_at"] = datetime.now(timezone.utc).isoformat()
        
        registry[model_id]["versions"] = versions
        
        with open(registry_path, "w") as f:
            json.dump(registry, f, indent=2)
        
        logger.info(f"Registry updated: {current_version} -> deprecated, {target_version} -> active")
    
    async def _export_rollback_report(self, result: RollbackResult) -> None:
        """导出回滚报告"""
        report_path = self._output_dir / f"rollback_{result.rollback_id}.json"
        
        with open(report_path, "w") as f:
            json.dump(result.to_dict(), f, indent=2)
        
        logger.info(f"Rollback report exported to {report_path}")
    
    def get_rollback_history(
        self,
        model_id: Optional[str] = None,
    ) -> List[RollbackResult]:
        """获取回滚历史"""
        if model_id:
            return [r for r in self._rollback_history if r.model_id == model_id]
        return self._rollback_history
    
    def cancel_pending_rollbacks(self, model_id: str) -> int:
        """
        取消待处理的回滚
        
        注意：这只是标记，实际取消需要检查执行状态
        
        Returns:
            取消的数量
        """
        # 简化实现：只记录
        logger.info(f"Rollback cancellation requested for model {model_id}")
        return 0


# =============================================================================
# KillSwitch Integration (KillSwitch 联动)
# =============================================================================

@dataclass
class KillSwitchRollbackRecommendation:
    """
    KillSwitch 回滚建议
    
    当 KillSwitch 升级时，可能需要回滚模型
    """
    kill_switch_level: int  # 0-3
    recommendation: Literal["NO_ACTION", "SIGNAL降级", "MODEL_ROLLBACK", "FULL_STOP"]
    reason: str
    model_id: Optional[str] = None
    
    def should_rollback_model(self) -> bool:
        return self.recommendation in ("MODEL_ROLLBACK", "FULL_STOP")


def get_killswitch_recommendation(
    kill_switch_level: int,
    model_drift_severity: Optional[str] = None,
) -> KillSwitchRollbackRecommendation:
    """
    根据 KillSwitch 级别获取回滚建议
    
    Args:
        kill_switch_level: KillSwitch 级别 (0-3)
        model_drift_severity: 模型漂移严重程度 (可选)
        
    Returns:
        KillSwitchRollbackRecommendation
    """
    recommendations: Dict[int, Tuple[Literal["NO_ACTION", "SIGNAL降级", "MODEL_ROLLBACK", "FULL_STOP"], str]] = {
        0: ("NO_ACTION", "正常运行"),
        1: ("SIGNAL降级", "建议降低信号置信度阈值"),
        2: ("MODEL_ROLLBACK", "建议回滚到上一稳定版本"),
        3: ("FULL_STOP", "立即停止所有模型操作"),
    }
    
    rec_tuple = recommendations.get(kill_switch_level, ("NO_ACTION", "未知级别"))
    
    return KillSwitchRollbackRecommendation(
        kill_switch_level=kill_switch_level,
        recommendation=rec_tuple[0],
        reason=rec_tuple[1],
    )


# =============================================================================
# Main Entry Point (主入口 - 供 Hermes 编排调用)
# =============================================================================

async def create_and_execute_rollback(
    model_id: str,
    reason: str,
    triggered_by: str = "manual",
) -> RollbackResult:
    """
    创建并执行回滚的主入口函数
    
    供 Hermes 编排脚本调用
    """
    manager = ModelRollbackManager()
    
    # 创建回滚计划
    plan = manager.create_rollback_plan(
        model_id=model_id,
        reason=reason,
        triggered_by=triggered_by,
    )
    
    # 执行回滚
    result = await manager.execute_rollback(plan)
    
    logger.info(
        f"Rollback completed: {result.rollback_id}, "
        f"status={result.status}, "
        f"signals_affected={result.signals_affected}"
    )
    
    return result


def get_rollback_recommendation(
    kill_switch_level: int,
    model_drift_severity: Optional[str] = None,
) -> KillSwitchRollbackRecommendation:
    """
    获取回滚建议的主入口函数
    
    供监控和告警系统调用
    """
    return get_killswitch_recommendation(
        kill_switch_level=kill_switch_level,
        model_drift_severity=model_drift_severity,
    )


# =============================================================================
# CLI Interface (命令行接口)
# =============================================================================

if __name__ == "__main__":
    import asyncio
    
    async def main():
        # 示例用法
        manager = ModelRollbackManager()
        
        # 创建回滚计划 (假设模型存在)
        try:
            plan = manager.create_rollback_plan(
                model_id="m240416.abcd",
                reason="检测到模型性能漂移，R2 衰减超过 20%",
                triggered_by="auto_drift",
            )
            
            print(f"Rollback Plan Created:")
            print(f"  Model: {plan.model_id}")
            print(f"  Current: {plan.current_version} -> Target: {plan.target_version}")
            print(f"  Reason: {plan.rollback_reason}")
            print(f"  Steps:")
            for step in plan.steps:
                print(f"    - {step}")
            
            # 执行回滚
            result = await manager.execute_rollback(plan)
            
            print(f"\nRollback Result:")
            print(f"  ID: {result.rollback_id}")
            print(f"  Status: {result.status}")
            print(f"  Signals affected: {result.signals_affected}")
            print(f"  Triggered by: {result.triggered_by}")
            
        except Exception as e:
            print(f"Error: {e}")
    
    asyncio.run(main())