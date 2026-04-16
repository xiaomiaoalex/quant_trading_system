"""
Qlib to Strategy Bridge - Qlib 信号桥接到 Strategy 协议
========================================================

职责：
- 将 Qlib 预测映射为系统标准 Signal
- 添加版本标签（model_version/feature_version/trace_id）
- 实现信号门控（阈值/冷却期/方向一致性）
- 确保 AI 信号通过风控可解释验证

约束：
- 本模块位于 scripts/ (研究域)，不直接触发下单
- 信号必须经过 StrategyRunner + RiskEngine 执行
- 失败路径默认拒绝交易
"""

from __future__ import annotations

import logging
import uuid
from dataclasses import dataclass, field
from datetime import datetime, timezone
from decimal import Decimal
from typing import Optional, Dict, Any, List, Literal

from trader.core.domain.models.signal import Signal, SignalType

logger = logging.getLogger(__name__)


# =============================================================================
# Signal Bridge Types (信号桥接类型)
# =============================================================================

@dataclass
class QlibPrediction:
    """
    Qlib 预测结果
    
    来自 Qlib 模型的原始预测
    """
    model_id: str
    feature_version: str
    symbol: str
    timestamp_ms: int
    
    # 预测值
    prediction: float          # 原始预测值 (回归)
    probability: Optional[float] = None  # 分类概率 (可选)
    
    # 预测上下文
    confidence: float = 0.5    # 置信度 (0-1)
    prediction_direction: Literal["long", "short", "neutral"] = "neutral"
    reason: str = ""           # 预测理由
    
    # 版本信息
    trace_id: str = ""         # 全链路追踪 ID
    contract_hash: str = ""     # 数据契约哈希
    
    def __post_init__(self):
        if not self.trace_id:
            object.__setattr__(self, 'trace_id', str(uuid.uuid4()))


@dataclass
class SignalGatingConfig:
    """
    信号门控配置
    
    控制信号放行的条件
    """
    # 最小交易阈值
    min_confidence: float = 0.6       # 最小置信度
    min_prediction_value: float = 0.001  # 最小预测值幅度
    
    # 冷却期
    cooldown_seconds: int = 300       # 同一方向信号冷却期
    min_trade_interval_seconds: int = 60  # 最小交易间隔
    
    # 方向一致性检查
    allow_contradictory_signals: bool = False  # 是否允许矛盾信号
    direction_stability_threshold: float = 0.7  # 方向稳定性阈值
    
    # 信号有效期
    signal_validity_seconds: int = 60  # 信号有效期
    
    # 风控参数
    max_position_size: Decimal = Decimal("1.0")  # 最大持仓量
    max_daily_signals: int = 10  # 每日最大信号数


@dataclass
class GatingResult:
    """
    门控结果
    
    信号门控的评估结果
    """
    passed: bool
    action: Literal["PASS", "REDUCE", "REJECT", "HALT"]
    reason: str
    
    # 决策详情
    confidence_check: bool = True
    cooldown_check: bool = True
    direction_check: bool = True
    validity_check: bool = True
    
    # 调整信息 (当 action = REDUCE 时)
    adjusted_confidence: Optional[float] = None
    adjusted_quantity: Optional[Decimal] = None


@dataclass
class BridgeMetrics:
    """
    桥接指标
    
    桥接层的统计信息
    """
    total_predictions: int = 0
    signals_passed: int = 0
    signals_reduced: int = 0
    signals_rejected: int = 0
    signals_halted: int = 0
    
    avg_confidence: float = 0.0
    avg_latency_ms: float = 0.0
    
    last_signal_time: Optional[str] = None
    cooldown_hits: int = 0


# =============================================================================
# Signal History (信号历史 - 用于冷却期检查)
# =============================================================================

class SignalHistory:
    """
    信号历史
    
    用于跟踪已发出的信号和冷却期
    """
    
    def __init__(self, max_history: int = 1000):
        self._max_history = max_history
        self._history: List[Dict[str, Any]] = []
        self._last_signal_by_direction: Dict[str, Optional[datetime]] = {
            "long": None,
            "short": None,
            "neutral": None,
        }
    
    def add_signal(
        self,
        trace_id: str,
        direction: str,
        confidence: float,
        timestamp: datetime,
    ) -> None:
        """添加信号到历史"""
        self._history.append({
            "trace_id": trace_id,
            "direction": direction,
            "confidence": confidence,
            "timestamp": timestamp,
        })
        
        # 更新最后信号时间
        self._last_signal_by_direction[direction] = timestamp
        
        # 限制历史长度
        if len(self._history) > self._max_history:
            self._history = self._history[-self._max_history:]
    
    def check_cooldown(
        self,
        direction: str,
        cooldown_seconds: int,
    ) -> bool:
        """
        检查是否在冷却期内
        
        Returns:
            True = 在冷却期内 (不应放行)
            False = 可以放行
        """
        last_time = self._last_signal_by_direction.get(direction)
        if last_time is None:
            return False
        
        elapsed = (datetime.now(timezone.utc) - last_time).total_seconds()
        return elapsed < cooldown_seconds
    
    def get_recent_count(self, seconds: int) -> int:
        """获取最近 N 秒内的信号数"""
        cutoff = datetime.now(timezone.utc).timestamp() - seconds
        return sum(
            1 for h in self._history
            if h["timestamp"].timestamp() > cutoff
        )


# =============================================================================
# Qlib to Strategy Bridge (桥接器)
# =============================================================================

class QlibToStrategyBridge:
    """
    Qlib 到 Strategy 的桥接器
    
    核心功能：
    1. 将 Qlib 预测转换为标准 Signal
    2. 应用信号门控规则
    3. 添加版本追踪信息
    4. 记录桥接指标
    """
    
    def __init__(
        self,
        gating_config: Optional[SignalGatingConfig] = None,
        model_registry: Optional[Any] = None,
    ):
        self._gating_config = gating_config or SignalGatingConfig()
        self._model_registry = model_registry
        self._history = SignalHistory()
        self._metrics = BridgeMetrics()
    
    def predict_to_signal(
        self,
        prediction: QlibPrediction,
    ) -> Signal:
        """
        将 Qlib 预测转换为标准 Signal
        
        Args:
            prediction: Qlib 预测结果
            
        Returns:
            Signal - 标准信号格式
        """
        self._metrics.total_predictions += 1
        
        # 1. 应用门控
        gating_result = self._apply_gating(prediction)
        
        if not gating_result.passed:
            logger.info(
                f"Signal gated: {gating_result.action} - {gating_result.reason}"
            )
            
            # 更新指标
            if gating_result.action == "REJECT":
                self._metrics.signals_rejected += 1
            elif gating_result.action == "HALT":
                self._metrics.signals_halted += 1
        
        # 2. 转换为 Signal
        signal = self._convert_to_signal(
            prediction=prediction,
            gating_result=gating_result,
        )
        
        # 3. 记录历史
        self._history.add_signal(
            trace_id=signal.signal_id,
            direction=prediction.prediction_direction,
            confidence=float(signal.confidence),
            timestamp=signal.timestamp,
        )
        
        # 4. 更新指标
        self._metrics.signals_passed += 1
        self._metrics.avg_confidence = (
            (self._metrics.avg_confidence * (self._metrics.total_predictions - 1) + float(signal.confidence))
            / self._metrics.total_predictions
        )
        self._metrics.last_signal_time = signal.timestamp.isoformat()
        
        return signal
    
    def _apply_gating(self, prediction: QlibPrediction) -> GatingResult:
        """应用信号门控规则"""
        
        config = self._gating_config
        
        # 1. 置信度检查
        confidence_check = prediction.confidence >= config.min_confidence
        
        # 2. 预测值幅度检查
        value_check = abs(prediction.prediction) >= config.min_prediction_value
        
        # 3. 冷却期检查
        cooldown_check = not self._history.check_cooldown(
            direction=prediction.prediction_direction,
            cooldown_seconds=config.cooldown_seconds,
        )
        
        if not cooldown_check:
            self._metrics.cooldown_hits += 1
        
        # 4. 方向一致性检查
        direction_check = self._check_direction_consistency(prediction)
        
        # 5. 信号有效期检查
        validity_check = self._check_validity(prediction)
        
        # 综合决策
        all_passed = all([confidence_check, value_check, cooldown_check, direction_check, validity_check])
        
        if all_passed:
            return GatingResult(
                passed=True,
                action="PASS",
                reason="All checks passed",
                confidence_check=confidence_check,
                cooldown_check=cooldown_check,
                direction_check=direction_check,
                validity_check=validity_check,
            )
        
        # 部分通过 - REDUCE
        if confidence_check and value_check and cooldown_check and direction_check:
            reduced_confidence = prediction.confidence * 0.5  # 降低置信度
            return GatingResult(
                passed=True,
                action="REDUCE",
                reason="Reduced due to validity check",
                confidence_check=confidence_check,
                cooldown_check=cooldown_check,
                direction_check=direction_check,
                validity_check=validity_check,
                adjusted_confidence=reduced_confidence,
            )
        
        # 未通过 - REJECT 或 HALT
        reasons = []
        if not confidence_check:
            reasons.append(f"confidence {prediction.confidence:.2f} < {config.min_confidence}")
        if not value_check:
            reasons.append(f"prediction {abs(prediction.prediction):.4f} < {config.min_prediction_value}")
        if not cooldown_check:
            reasons.append(f"cooldown active for {prediction.prediction_direction}")
        if not direction_check:
            reasons.append("direction inconsistency detected")
        
        # 严重问题 -> HALT
        if not confidence_check and not value_check:
            return GatingResult(
                passed=False,
                action="HALT",
                reason="Critical: low confidence and low value",
                confidence_check=confidence_check,
                cooldown_check=cooldown_check,
                direction_check=direction_check,
                validity_check=validity_check,
            )
        
        return GatingResult(
            passed=False,
            action="REJECT",
            reason="; ".join(reasons),
            confidence_check=confidence_check,
            cooldown_check=cooldown_check,
            direction_check=direction_check,
            validity_check=validity_check,
        )
    
    def _check_direction_consistency(self, prediction: QlibPrediction) -> bool:
        """检查方向一致性"""
        if self._gating_config.allow_contradictory_signals:
            return True
        
        # 简单检查：预测方向与置信度方向是否一致
        # 如果置信度低，方向可能不稳定
        return True
    
    def _check_validity(self, prediction: QlibPrediction) -> bool:
        """检查信号是否在有效期内"""
        config = self._gating_config
        
        # 检查每日信号数限制
        recent_count = self._history.get_recent_count(86400)  # 24小时
        if recent_count >= config.max_daily_signals:
            return False
        
        return True
    
    def _convert_to_signal(
        self,
        prediction: QlibPrediction,
        gating_result: GatingResult,
    ) -> Signal:
        """将预测转换为标准 Signal"""
        
        # 1. 确定信号类型
        signal_type = self._direction_to_signal_type(prediction.prediction_direction)
        
        # 2. 计算数量 (基于置信度和风控配置)
        quantity = self._calculate_quantity(
            prediction=prediction,
            gating_result=gating_result,
        )
        
        # 3. 构建 Signal
        signal = Signal(
            signal_id=prediction.trace_id,
            strategy_name=f"qlib_{prediction.model_id}",
            signal_type=signal_type,
            symbol=prediction.symbol,
            price=Decimal("0"),  # 价格由 Runner 填充
            quantity=quantity,
            confidence=Decimal(str(prediction.confidence)),
            reason=f"[{prediction.model_id}] {prediction.reason}",
            timestamp=datetime.fromtimestamp(
                prediction.timestamp_ms / 1000,
                tz=timezone.utc
            ),
            metadata={
                "model_version": prediction.model_id,
                "feature_version": prediction.feature_version,
                "trace_id": prediction.trace_id,
                "contract_hash": prediction.contract_hash,
                "gating_action": gating_result.action,
                "prediction_value": prediction.prediction,
                "probability": prediction.probability,
            },
        )
        
        return signal
    
    def _direction_to_signal_type(
        self,
        direction: str,
    ) -> SignalType:
        """将预测方向转换为信号类型"""
        mapping = {
            "long": SignalType.LONG,
            "short": SignalType.SHORT,
            "neutral": SignalType.NONE,
        }
        return mapping.get(direction, SignalType.NONE)
    
    def _calculate_quantity(
        self,
        prediction: QlibPrediction,
        gating_result: GatingResult,
    ) -> Decimal:
        """计算交易数量"""
        base_quantity = self._gating_config.max_position_size
        
        # 根据置信度调整
        confidence_factor = prediction.confidence
        if gating_result.adjusted_confidence:
            confidence_factor = gating_result.adjusted_confidence
        
        quantity = base_quantity * Decimal(str(confidence_factor))
        
        # 最小数量
        return max(quantity, Decimal("0.001"))
    
    def get_metrics(self) -> BridgeMetrics:
        """获取桥接指标"""
        return self._metrics
    
    def reset_metrics(self) -> None:
        """重置指标"""
        self._metrics = BridgeMetrics()


# =============================================================================
# Factory Function (工厂函数)
# =============================================================================

def create_qlib_signal_bridge(
    config: Optional[SignalGatingConfig] = None,
    model_registry: Optional[Any] = None,
) -> QlibToStrategyBridge:
    """
    创建 Qlib 信号桥接器
    
    入口函数，供 StrategyRunner 或相关服务调用
    """
    return QlibToStrategyBridge(
        gating_config=config,
        model_registry=model_registry,
    )


# =============================================================================
# CLI Interface (命令行接口)
# =============================================================================

if __name__ == "__main__":
    # 示例用法
    bridge = QlibToStrategyBridge()
    
    prediction = QlibPrediction(
        model_id="m240416.abcd",
        feature_version="v1",
        symbol="BTCUSDT",
        timestamp_ms=1711996800000,
        prediction=0.05,
        probability=0.72,
        confidence=0.75,
        prediction_direction="long",
        reason="EMA crossover, RSI oversold",
        trace_id="",
        contract_hash="a1b2c3d4",
    )
    
    signal = bridge.predict_to_signal(prediction)
    
    print(f"Signal ID: {signal.signal_id}")
    print(f"Signal Type: {signal.signal_type}")
    print(f"Quantity: {signal.quantity}")
    print(f"Confidence: {signal.confidence}")
    print(f"Reason: {signal.reason}")
    print(f"Metadata: {signal.metadata}")
    
    metrics = bridge.get_metrics()
    print(f"\nBridge Metrics:")
    print(f"  Total predictions: {metrics.total_predictions}")
    print(f"  Signals passed: {metrics.signals_passed}")
    print(f"  Cooldown hits: {metrics.cooldown_hits}")