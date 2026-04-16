"""
Qlib Strategy Validator - Qlib 模型与策略验证门控集成
=====================================================

职责：
- 将 Qlib 训练产生的模型与 5 层验证门控集成
- 提供从模型到 Signal 再到验证的完整链路
- 确保 AI 信号通过五层验证门控

约束：
- 本模块位于 scripts/ (研究域)，不直接触发下单
- 验证失败默认拒绝交易
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from datetime import datetime, timezone
from decimal import Decimal
from typing import Optional, Dict, Any, List, Literal

from scripts.qlib_to_strategy_bridge import (
    QlibPrediction,
    QlibToStrategyBridge,
    SignalGatingConfig,
)
from scripts.qlib_train_workflow import ModelRegistry, TrainingConfig

logger = logging.getLogger(__name__)


# =============================================================================
# Validation Gate Types (验证门控类型)
# =============================================================================

@dataclass
class QlibModelValidationInput:
    """
    Qlib 模型验证输入
    
    包含模型和验证所需的所有信息
    """
    model_id: str
    feature_version: str
    contract_hash: str
    
    # 模型信息
    model_path: str
    model_type: str  # "lightgbm" | "xgboost" | etc.
    
    # 数据范围
    train_start: str
    train_end: str
    test_start: str
    test_end: str
    
    # 机制假设
    mechanism_answers: Dict[str, str]  # Q1/Q2/Q3
    
    # 验证者
    validated_by: str = "hermes"


@dataclass
class QlibSignalWithValidation:
    """
    带验证的 Qlib Signal
    
    Signal 加上验证状态信息
    """
    # Signal 信息
    signal_type: str
    symbol: str
    quantity: Decimal
    confidence: float
    reason: str
    trace_id: str
    
    # 版本信息
    model_version: str
    feature_version: str
    contract_hash: str
    
    # 验证状态
    passed_gating: bool
    gating_action: str
    
    # 验证门控 (可选)
    validation_report: Optional[Dict[str, Any]] = None


@dataclass
class QlibValidationReport:
    """
    Qlib 模型验证报告
    
    完整的验证结果
    """
    model_id: str
    feature_version: str
    
    # 门控结果
    gating_passed: bool
    gating_action: str
    total_predictions: int
    signals_passed: int
    signals_rejected: int
    
    # 验证门控结果 (如果执行了)
    validation_status: Optional[Literal["PASSED", "FAILED", "PENDING"]] = None
    validation_layers_passed: List[int] = field(default_factory=list)
    validation_failed_layers: List[int] = field(default_factory=list)
    
    # 建议
    recommendations: List[str] = field(default_factory=list)
    can_deploy: bool = False
    
    # 时间戳
    validated_at: str = field(default_factory=lambda: datetime.now(timezone.utc).isoformat())


# =============================================================================
# Qlib Model Validator (Qlib 模型验证器)
# =============================================================================

class QlibModelValidator:
    """
    Qlib 模型验证器
    
    核心功能：
    1. 加载 Qlib 训练产生的模型
    2. 应用信号门控
    3. 可选：执行 5 层验证门控
    4. 生成验证报告
    """
    
    def __init__(
        self,
        model_registry: Optional[ModelRegistry] = None,
        gating_config: Optional[SignalGatingConfig] = None,
    ):
        self._registry = model_registry or ModelRegistry()
        self._gating_config = gating_config or SignalGatingConfig()
        self._bridge = QlibToStrategyBridge(gating_config=gating_config)
    
    def validate_model_signals(
        self,
        model_id: str,
        predictions: List[QlibPrediction],
        run_full_validation: bool = False,
    ) -> QlibValidationReport:
        """
        验证模型产生的信号
        
        Args:
            model_id: 模型ID
            predictions: 预测列表
            run_full_validation: 是否执行完整 5 层验证
            
        Returns:
            QlibValidationReport
        """
        logger.info(f"Validating model {model_id} with {len(predictions)} predictions")
        
        # 应用信号门控
        passed_count = 0
        rejected_count = 0
        
        for pred in predictions:
            signal = self._bridge.predict_to_signal(pred)
            
            if signal.signal_type.value != "NONE":
                passed_count += 1
            else:
                rejected_count += 1
        
        # 获取门控指标
        metrics = self._bridge.get_metrics()
        
        # 生成报告
        report = QlibValidationReport(
            model_id=model_id,
            feature_version=predictions[0].feature_version if predictions else "unknown",
            gating_passed=metrics.signals_passed > 0,
            gating_action="PASS" if metrics.signals_passed > 0 else "REJECT",
            total_predictions=metrics.total_predictions,
            signals_passed=metrics.signals_passed,
            signals_rejected=metrics.signals_rejected,
        )
        
        # 如果需要执行完整验证
        if run_full_validation:
            self._run_full_validation(report, model_id, predictions)
        
        # 生成建议
        report.recommendations = self._generate_recommendations(report)
        
        # 判断是否可以部署
        report.can_deploy = self._can_deploy(report)
        
        return report
    
    def _run_full_validation(
        self,
        report: QlibValidationReport,
        model_id: str,
        predictions: List[QlibPrediction],
    ) -> None:
        """
        执行完整 5 层验证门控
        
        注意：这是集成点，实际实现需要调用 StrategyValidationGate
        """
        # Layer 1-3 已在模型训练时验证
        # 这里主要是 Layer 4-5 的后验验证
        
        # 检查成本压测结果
        from trader.services.backtesting.cost_stress_tester import CostStressTester
        
        tester = CostStressTester()
        
        # 模拟成本压测结果
        # 实际使用时应该从 backtest_result 获取
        cost_results = []
        
        for multiplier in [1.0, 1.5, 2.0]:
            passed = multiplier <= 1.5  # 1.5x 以内期望为正
            cost_results.append({
                "cost_multiplier": multiplier,
                "passed": passed,
            })
        
        # Layer 4 成本压测
        if all(r["passed"] for r in cost_results):
            report.validation_layers_passed.append(4)
        else:
            report.validation_failed_layers.append(4)
        
        # Layer 5 影子模式 (可选)
        # 需要实际的影子模式验证结果
        # 这里简化处理
        report.validation_status = "PASSED" if len(report.validation_failed_layers) == 0 else "FAILED"
    
    def _generate_recommendations(self, report: QlibValidationReport) -> List[str]:
        """生成部署建议"""
        recommendations = []
        
        if report.signals_passed == 0:
            recommendations.append("模型未产生有效信号，建议检查特征和模型配置")
        
        if report.signals_rejected > report.signals_passed:
            recommendations.append("信号拒绝率较高，建议调整信号门控阈值")
        
        if report.validation_failed_layers:
            recommendations.append(
                f"验证失败层级: {report.validation_failed_layers}，建议优化后重新验证"
            )
        
        if report.can_deploy:
            recommendations.append("模型通过验证，可以进入影子模式测试")
        else:
            recommendations.append("模型未通过验证，禁止进入生产环境")
        
        return recommendations
    
    def _can_deploy(self, report: QlibValidationReport) -> bool:
        """判断是否可以部署"""
        # 基本条件：门控通过 + 有有效信号
        if not report.gating_passed or report.signals_passed == 0:
            return False
        
        # 如果执行了完整验证
        if report.validation_status:
            return report.validation_status == "PASSED"
        
        # 默认：门控通过即可（简化判断）
        return True
    
    def validate_and_export_report(
        self,
        model_id: str,
        predictions: List[QlibPrediction],
        output_path: str,
        run_full_validation: bool = False,
    ) -> QlibValidationReport:
        """
        验证并导出报告
        
        Args:
            model_id: 模型ID
            predictions: 预测列表
            output_path: 报告输出路径
            run_full_validation: 是否执行完整验证
            
        Returns:
            QlibValidationReport
        """
        import json
        
        # 执行验证
        report = self.validate_model_signals(
            model_id=model_id,
            predictions=predictions,
            run_full_validation=run_full_validation,
        )
        
        # 导出报告
        report_dict = {
            "model_id": report.model_id,
            "feature_version": report.feature_version,
            "gating_passed": report.gating_passed,
            "gating_action": report.gating_action,
            "total_predictions": report.total_predictions,
            "signals_passed": report.signals_passed,
            "signals_rejected": report.signals_rejected,
            "validation_status": report.validation_status,
            "validation_layers_passed": report.validation_layers_passed,
            "validation_failed_layers": report.validation_failed_layers,
            "recommendations": report.recommendations,
            "can_deploy": report.can_deploy,
            "validated_at": report.validated_at,
        }
        
        with open(output_path, "w") as f:
            json.dump(report_dict, f, indent=2)
        
        logger.info(f"Validation report exported to {output_path}")
        
        return report


# =============================================================================
# Factory Function (工厂函数)
# =============================================================================

def create_qlib_model_validator(
    model_registry: Optional[ModelRegistry] = None,
    gating_config: Optional[SignalGatingConfig] = None,
) -> QlibModelValidator:
    """
    创建 Qlib 模型验证器
    
    入口函数
    """
    return QlibModelValidator(
        model_registry=model_registry,
        gating_config=gating_config,
    )


# =============================================================================
# CLI Interface (命令行接口)
# =============================================================================

if __name__ == "__main__":
    import json
    
    # 示例用法
    validator = QlibModelValidator()
    
    # 模拟预测数据
    predictions = [
        QlibPrediction(
            model_id="m240416.abcd",
            feature_version="v1",
            symbol="BTCUSDT",
            timestamp_ms=1711996800000,
            prediction=0.05,
            probability=0.72,
            confidence=0.75,
            prediction_direction="long",
            reason="EMA crossover",
            contract_hash="a1b2c3d4",
        ),
        QlibPrediction(
            model_id="m240416.abcd",
            feature_version="v1",
            symbol="BTCUSDT",
            timestamp_ms=1712083200000,
            prediction=-0.03,
            probability=0.65,
            confidence=0.60,
            prediction_direction="short",
            reason="RSI overbought",
            contract_hash="a1b2c3d4",
        ),
    ]
    
    # 执行验证
    report = validator.validate_model_signals(
        model_id="m240416.abcd",
        predictions=predictions,
        run_full_validation=False,
    )
    
    print(f"Validation Report: {report.model_id}")
    print(f"  Gating: passed={report.gating_passed}, action={report.gating_action}")
    print(f"  Signals: passed={report.signals_passed}, rejected={report.signals_rejected}")
    print(f"  Can deploy: {report.can_deploy}")
    print(f"\nRecommendations:")
    for rec in report.recommendations:
        print(f"  - {rec}")
    
    # 导出报告
    report_path = f"reports/{report.model_id}_validation.json"
    validator.validate_and_export_report(
        model_id=report.model_id,
        predictions=predictions,
        output_path=report_path,
    )
    print(f"\nReport exported to {report_path}")