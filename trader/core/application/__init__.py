# Core Application - 核心应用层
"""
核心应用层包含交易系统的关键业务逻辑组件。

核心组件：
- OMS: 订单管理系统，处理订单状态迁移和幂等
- RiskEngine: 风险引擎，交易前/中/后三层风控
- DeterministicLayer: 确定性层，事件回放与恢复
- HITLGovernance: 人机交互治理，AI+人工协作
- StrategyProtocol: 策略插件协议定义

关键原则：
1. Core Plane 无 IO，完全确定性
2. 所有状态变更通过事件记录
3. KillSwitch 机制保障系统安全
"""

from trader.core.application.oms import OMS
from trader.core.application.risk_engine import (
    RiskLevel,
    KillSwitchLevel,
    RejectionReason,
    RiskCheckResult,
    RiskMetrics,
    RiskConfig,
    RiskEngine,
)
from trader.core.application.strategy_protocol import (
    MarketData,
    MarketDataType,
    Signal,
    StrategyPlugin,
    StrategyResourceLimits,
    ValidationResult,
    ValidationStatus,
    ValidationError,
)

__all__ = [
    # OMS
    "OMS",

    # Risk Engine
    "RiskLevel",
    "KillSwitchLevel",
    "RejectionReason",
    "RiskCheckResult",
    "RiskMetrics",
    "RiskConfig",
    "RiskEngine",

    # Strategy Protocol
    "MarketData",
    "MarketDataType",
    "Signal",
    "StrategyPlugin",
    "StrategyResourceLimits",
    "ValidationResult",
    "ValidationStatus",
    "ValidationError",
]
