"""
Insight Committee Package - 多 Agent 策略组合开发委员会
========================================================

此模块提供多 Agent 策略组合研究所需的核心 schema 定义。

核心组件：
- SleeveProposal: 单策略研究提案
- PortfolioProposal: 组合级提案
- ReviewReport: 评审报告
- CommitteeRun: 委员会运行记录

设计原则：
1. Agent 只做研究与 proposal，不直接下单
2. 必须经过 HITL 审批
3. 所有操作可审计回放
4. 采用追加式事件日志

硬边界约束：
- agent 只做研究与 proposal
- agent 不能直接下单
- agent 不能绕过 HITL
- proposal 进入现有 lifecycle / backtest / approval 流水线
"""

from insight.committee.schemas import (
    # Enums
    SpecialistType,
    ProposalStatus,
    ReviewVerdict,
    CommitteeRunStatus,
    # Core Data Classes
    SleeveProposal,
    SleeveAssignment,
    PortfolioProposal,
    ReviewReport,
    CommitteeRun,
    ReviewResult,
    CostAssumptions,
    RegimeCondition,
    ConflictResolution,
    # Validation
    ValidationResult,
    Violation,
    AgentOutput,
    OutputType,
)

__all__ = [
    # Enums
    "SpecialistType",
    "ProposalStatus",
    "ReviewVerdict",
    "CommitteeRunStatus",
    # Core Data Classes
    "SleeveProposal",
    "SleeveAssignment",
    "PortfolioProposal",
    "ReviewReport",
    "CommitteeRun",
    "ReviewResult",
    "CostAssumptions",
    "RegimeCondition",
    "ConflictResolution",
    # Validation
    "ValidationResult",
    "Violation",
    "AgentOutput",
    "OutputType",
]
