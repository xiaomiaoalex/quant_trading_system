"""
Model Drift Detector - 模型漂移检测
====================================

职责：
- 检测模型性能漂移
- 监控特征新鲜度
- 监控信号触发率和风控干预率
- 生成告警和报告

约束：
- 本模块位于 scripts/ (研究域)，不直接触发交易
- 漂移检测结果用于决定是否需要回滚或重训
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from datetime import datetime, timezone, timedelta
from typing import Dict, List, Optional, Any, Literal

logger = logging.getLogger(__name__)


# =============================================================================
# Drift Detection Types (漂移检测类型)
# =============================================================================

@dataclass
class DriftMetrics:
    """
    漂移指标
    
    模型性能监控指标
    """
    model_id: str
    timestamp: str
    
    # 性能指标
    current_r2: float
    baseline_r2: float
    r2_decay_pct: float
    
    # 信号统计
    signal_trigger_rate: float  # 信号触发率
    baseline_trigger_rate: float  # 基线触发率
    trigger_rate_decay_pct: float
    
    # 风控干预
    risk_intervention_rate: float  # 风控干预率
    baseline_intervention_rate: float  # 基线干预率
    intervention_increase_pct: float
    
    # 特征新鲜度 (带默认值)
    feature_freshness_seconds: int = 0
    feature_freshness_threshold: int = 3600  # 1小时
    
    # 综合状态 (带默认值)
    drift_detected: bool = False
    drift_severity: Literal["NONE", "LOW", "MEDIUM", "HIGH", "CRITICAL"] = "NONE"
    drift_reasons: List[str] = field(default_factory=list)
    
    def __post_init__(self):
        self._evaluate_drift()
    
    def _evaluate_drift(self) -> None:
        """评估漂移状态"""
        reasons = []
        severity: str = "NONE"
        
        # R2 衰减检测
        if self.r2_decay_pct > 20:
            reasons.append(f"R2 衰减 {self.r2_decay_pct:.1f}% (基线 {self.baseline_r2:.3f})")
            severity = "HIGH"
        elif self.r2_decay_pct > 10:
            reasons.append(f"R2 衰减 {self.r2_decay_pct:.1f}%")
            if severity == "NONE":
                severity = "MEDIUM"
        elif self.r2_decay_pct > 5:
            if severity == "NONE":
                severity = "LOW"
        
        # 特征新鲜度
        if self.feature_freshness_seconds > self.feature_freshness_threshold:
            reasons.append(f"特征过期 {self.feature_freshness_seconds}s > 阈值 {self.feature_freshness_threshold}s")
            if severity == "NONE":
                severity = "MEDIUM"
        
        # 信号触发率衰减
        if self.trigger_rate_decay_pct > 30:
            reasons.append(f"信号触发率衰减 {self.trigger_rate_decay_pct:.1f}%")
            severity = "HIGH"
        elif self.trigger_rate_decay_pct > 15:
            if severity == "NONE":
                severity = "MEDIUM"
        
        # 风控干预率增加
        if self.intervention_increase_pct > 50:
            reasons.append(f"风控干预率增加 {self.intervention_increase_pct:.1f}%")
            severity = "HIGH"
        elif self.intervention_increase_pct > 20:
            if severity == "NONE":
                severity = "MEDIUM"
        
        self.drift_detected = severity != "NONE"
        object.__setattr__(self, 'drift_severity', severity)
        self.drift_reasons = reasons


@dataclass
class DriftAlert:
    """
    漂移告警
    
    当检测到漂移时生成的告警
    """
    alert_id: str
    model_id: str
    severity: str
    timestamp: str
    metrics: DriftMetrics
    recommended_action: Literal[
        "MONITOR",      # 继续监控
        "RETRAIN",      # 需要重训练
        "ROLLBACK",     # 需要回滚
        "STOP",         # 立即停止
    ]
    action_reason: str


# =============================================================================
# Drift Detector (漂移检测器)
# =============================================================================

class ModelDriftDetector:
    """
    模型漂移检测器
    
    核心功能：
    1. 监控模型性能指标
    2. 检测性能漂移
    3. 生成告警
    4. 提供回滚建议
    """
    
    def __init__(
        self,
        baseline_metrics: Optional[Dict[str, float]] = None,
        thresholds: Optional[Dict[str, float]] = None,
    ):
        # 基线指标
        self._baseline = baseline_metrics or {
            "r2": 0.65,
            "trigger_rate": 0.15,
            "intervention_rate": 0.08,
        }
        
        # 阈值配置
        self._thresholds = thresholds or {
            "r2_decay_pct": 10.0,  # R2 衰减超过 10% 告警
            "trigger_rate_decay_pct": 20.0,  # 触发率衰减超过 20%
            "intervention_increase_pct": 30.0,  # 干预率增加超过 30%
            "feature_stale_seconds": 3600,  # 特征过期超过 1 小时
        }
    
    def detect_drift(
        self,
        model_id: str,
        current_metrics: Dict[str, float],
        feature_age_seconds: int = 0,
    ) -> DriftMetrics:
        """
        检测模型漂移
        
        Args:
            model_id: 模型ID
            current_metrics: 当前指标
            feature_age_seconds: 特征年龄（秒）
            
        Returns:
            DriftMetrics
        """
        logger.info(f"Detecting drift for model {model_id}")
        
        # 计算 R2 衰减
        current_r2 = current_metrics.get("r2", 0)
        baseline_r2 = self._baseline.get("r2", 0.65)
        r2_decay_pct = ((baseline_r2 - current_r2) / baseline_r2 * 100) if baseline_r2 > 0 else 0
        
        # 计算信号触发率衰减
        current_trigger = current_metrics.get("trigger_rate", 0)
        baseline_trigger = self._baseline.get("trigger_rate", 0.15)
        trigger_decay_pct = ((baseline_trigger - current_trigger) / baseline_trigger * 100) if baseline_trigger > 0 else 0
        
        # 计算风控干预率增加
        current_intervention = current_metrics.get("intervention_rate", 0)
        baseline_intervention = self._baseline.get("intervention_rate", 0.08)
        intervention_increase_pct = ((current_intervention - baseline_intervention) / baseline_intervention * 100) if baseline_intervention > 0 else 0
        
        # 构建漂移指标
        threshold_val = self._thresholds.get("feature_stale_seconds", 3600)
        metrics = DriftMetrics(
            model_id=model_id,
            timestamp=datetime.now(timezone.utc).isoformat(),
            current_r2=current_r2,
            baseline_r2=baseline_r2,
            r2_decay_pct=max(0, r2_decay_pct),
            feature_freshness_seconds=feature_age_seconds,
            feature_freshness_threshold=int(threshold_val) if threshold_val is not None else 3600,
            signal_trigger_rate=current_trigger,
            baseline_trigger_rate=baseline_trigger,
            trigger_rate_decay_pct=max(0, trigger_decay_pct),
            risk_intervention_rate=current_intervention,
            baseline_intervention_rate=baseline_intervention,
            intervention_increase_pct=max(0, intervention_increase_pct),
        )
        
        logger.info(
            f"Drift detection: r2_decay={metrics.r2_decay_pct:.1f}%, "
            f"trigger_decay={metrics.trigger_rate_decay_pct:.1f}%, "
            f"intervention_increase={metrics.intervention_increase_pct:.1f}%"
        )
        
        return metrics
    
    def generate_alert(self, metrics: DriftMetrics) -> Optional[DriftAlert]:
        """
        生成漂移告警
        
        Args:
            metrics: 漂移指标
            
        Returns:
            DriftAlert 或 None (无告警)
        """
        if not metrics.drift_detected:
            return None
        
        # 确定推荐动作
        if metrics.drift_severity == "CRITICAL":
            action = "STOP"
            reason = "严重漂移，立即停止使用"
        elif metrics.drift_severity == "HIGH":
            action = "ROLLBACK"
            reason = "高漂移，建议回滚到上一稳定版本"
        elif metrics.drift_severity == "MEDIUM":
            action = "RETRAIN"
            reason = "中等漂移，建议重新训练模型"
        else:
            action = "MONITOR"
            reason = "轻微漂移，继续监控"
        
        alert = DriftAlert(
            alert_id=f"alert_{datetime.now(timezone.utc).strftime('%Y%m%d%H%M%S')}",
            model_id=metrics.model_id,
            severity=metrics.drift_severity,
            timestamp=datetime.now(timezone.utc).isoformat(),
            metrics=metrics,
            recommended_action=action,
            action_reason=reason,
        )
        
        logger.warning(
            f"Drift alert: model={metrics.model_id}, severity={metrics.drift_severity}, "
            f"action={action}, reasons={metrics.drift_reasons}"
        )
        
        return alert
    
    def update_baseline(self, new_metrics: Dict[str, float]) -> None:
        """
        更新基线指标
        
        当模型经过重新训练并验证稳定后，调用此方法更新基线
        """
        self._baseline.update(new_metrics)
        logger.info(f"Baseline updated: {self._baseline}")
    
    def should_rollback(self, metrics: DriftMetrics) -> bool:
        """
        判断是否应该回滚
        
        Args:
            metrics: 漂移指标
            
        Returns:
            True = 应该回滚
        """
        return (
            metrics.r2_decay_pct > 20 or
            metrics.drift_severity in ("HIGH", "CRITICAL")
        )


# =============================================================================
# Monitoring Dashboard Data (监控面板数据)
# =============================================================================

@dataclass
class ModelMonitoringSnapshot:
    """
    模型监控快照
    
    供监控面板使用的数据结构
    """
    model_id: str
    feature_version: str
    model_status: str  # "active" | "stale" | "drifting" | "deprecated"
    
    # 性能
    current_r2: float
    baseline_r2: float
    r2_trend: Literal["UP", "STABLE", "DOWN"]
    
    # 特征新鲜度
    last_feature_update: str
    feature_age_seconds: int
    feature_stale: bool
    
    # 信号统计
    total_signals_24h: int
    signal_trigger_rate: float
    signals_blocked: int
    
    # 风控
    interventions_24h: int
    risk_intervention_rate: float
    
    # 漂移状态
    drift_detected: bool
    drift_severity: str
    last_alert_time: Optional[str]
    
    def to_dict(self) -> Dict[str, Any]:
        return {
            "model_id": self.model_id,
            "feature_version": self.feature_version,
            "model_status": self.model_status,
            "current_r2": round(self.current_r2, 4),
            "baseline_r2": round(self.baseline_r2, 4),
            "r2_trend": self.r2_trend,
            "last_feature_update": self.last_feature_update,
            "feature_age_seconds": self.feature_age_seconds,
            "feature_stale": self.feature_stale,
            "total_signals_24h": self.total_signals_24h,
            "signal_trigger_rate": round(self.signal_trigger_rate, 4),
            "signals_blocked": self.signals_blocked,
            "interventions_24h": self.interventions_24h,
            "risk_intervention_rate": round(self.risk_intervention_rate, 4),
            "drift_detected": self.drift_detected,
            "drift_severity": self.drift_severity,
            "last_alert_time": self.last_alert_time,
        }


# =============================================================================
# Main Entry Point (主入口 - 供 Hermes 编排调用)
# =============================================================================

def detect_model_drift(
    model_id: str,
    current_metrics: Dict[str, float],
    feature_age_seconds: int = 0,
) -> DriftMetrics:
    """
    检测模型漂移的主入口函数
    
    供 Hermes 编排脚本调用
    """
    detector = ModelDriftDetector()
    metrics = detector.detect_drift(
        model_id=model_id,
        current_metrics=current_metrics,
        feature_age_seconds=feature_age_seconds,
    )
    
    alert = detector.generate_alert(metrics)
    if alert:
        logger.warning(f"Drift alert generated: {alert.alert_id}")
    
    return metrics


def get_monitoring_snapshot(
    model_id: str,
    metrics: Dict[str, Any],
) -> ModelMonitoringSnapshot:
    """
    获取模型监控快照
    
    供监控面板调用
    """
    from datetime import datetime, timezone
    
    detector = ModelDriftDetector()
    drift_metrics = detector.detect_drift(
        model_id=model_id,
        current_metrics=metrics,
        feature_age_seconds=metrics.get("feature_age_seconds", 0),
    )
    
    # 确定状态
    if drift_metrics.drift_detected:
        status = "drifting"
    elif drift_metrics.feature_freshness_seconds > 3600:
        status = "stale"
    else:
        status = "active"
    
    # R2 趋势
    r2_change = drift_metrics.current_r2 - drift_metrics.baseline_r2
    if r2_change > 0.01:
        r2_trend = "UP"
    elif r2_change < -0.01:
        r2_trend = "DOWN"
    else:
        r2_trend = "STABLE"
    
    return ModelMonitoringSnapshot(
        model_id=model_id,
        feature_version=metrics.get("feature_version", "v1"),
        model_status=status,
        current_r2=drift_metrics.current_r2,
        baseline_r2=drift_metrics.baseline_r2,
        r2_trend=r2_trend,
        last_feature_update=metrics.get("last_feature_update", ""),
        feature_age_seconds=drift_metrics.feature_freshness_seconds,
        feature_stale=drift_metrics.feature_freshness_seconds > drift_metrics.feature_freshness_threshold,
        total_signals_24h=metrics.get("total_signals_24h", 0),
        signal_trigger_rate=metrics.get("trigger_rate", 0),
        signals_blocked=metrics.get("signals_blocked", 0),
        interventions_24h=metrics.get("interventions_24h", 0),
        risk_intervention_rate=metrics.get("intervention_rate", 0),
        drift_detected=drift_metrics.drift_detected,
        drift_severity=drift_metrics.drift_severity,
        last_alert_time=metrics.get("last_alert_time"),
    )


# =============================================================================
# CLI Interface (命令行接口)
# =============================================================================

if __name__ == "__main__":
    # 示例用法
    detector = ModelDriftDetector()
    
    # 模拟当前指标
    current_metrics = {
        "r2": 0.55,  # 比基线 0.65 下降了
        "trigger_rate": 0.10,  # 比基线 0.15 下降了
        "intervention_rate": 0.12,  # 比基线 0.08 增加了
    }
    
    # 检测漂移
    metrics = detector.detect_drift(
        model_id="m240416.abcd",
        current_metrics=current_metrics,
        feature_age_seconds=7200,  # 2小时未更新
    )
    
    print(f"Drift Detection Results:")
    print(f"  Model: {metrics.model_id}")
    print(f"  Drift detected: {metrics.drift_detected}")
    print(f"  Severity: {metrics.drift_severity}")
    print(f"  R2 decay: {metrics.r2_decay_pct:.1f}%")
    print(f"  Trigger rate decay: {metrics.trigger_rate_decay_pct:.1f}%")
    print(f"  Intervention increase: {metrics.intervention_increase_pct:.1f}%")
    
    if metrics.drift_reasons:
        print(f"\nDrift reasons:")
        for reason in metrics.drift_reasons:
            print(f"  - {reason}")
    
    # 生成告警
    alert = detector.generate_alert(metrics)
    if alert:
        print(f"\nAlert generated:")
        print(f"  ID: {alert.alert_id}")
        print(f"  Severity: {alert.severity}")
        print(f"  Action: {alert.recommended_action}")
        print(f"  Reason: {alert.action_reason}")
        print(f"  Should rollback: {detector.should_rollback(metrics)}")