"""
Committee Schemas - 多 Agent 策略组合开发委员会核心 Schema
==========================================================

此模块定义委员会中使用的所有核心数据类型。

核心原则：
1. 所有输出必须包含 trace_id 用于追踪
2. 必须标注 feature_version 和 prompt_version
3. 遇到无法解析的情况必须 Fail-Closed

版本：1.0.0
"""

from __future__ import annotations

import uuid
import hashlib
import json
from dataclasses import dataclass, field
from datetime import datetime, timezone
from decimal import Decimal
from enum import Enum
from typing import Any, Dict, List, Optional


class SpecialistType(str, Enum):
    """ Specialist Agent 类型 """
    TREND = "trend"
    PRICE_VOLUME = "price_volume"
    FUNDING_OI = "funding_oi"
    ONCHAIN = "onchain"
    EVENT_REGIME = "event_regime"


class ProposalStatus(str, Enum):
    """ 提案状态 """
    PENDING = "pending"           # 等待委员会审查
    IN_REVIEW = "in_review"       # 正在被 specialist 或 red team 审查
    PASSED = "passed"             # 通过审查，进入 backtest
    REJECTED = "rejected"         # 被 red team 否决
    APPROVED = "approved"         # 人工审批通过
    ARCHIVED = "archived"         # 已废弃或被淘汰


class ReviewVerdict(str, Enum):
    """ 评审结论 """
    PASS = "pass"                 # 通过
    FAIL = "fail"                 # 否决
    CONDITIONAL = "conditional"   # 有条件通过
    SKIP = "skip"                 # 跳过


class CommitteeRunStatus(str, Enum):
    """ 委员会运行状态 """
    PENDING = "pending"           # 等待开始
    RUNNING = "running"           # 运行中
    COMPLETED = "completed"       # 完成
    FAILED = "failed"             # 失败
    CANCELLED = "cancelled"       # 取消


class OutputType(str, Enum):
    """ Agent 输出类型 """
    SLEEVE_PROPOSAL = "sleeve_proposal"
    REVIEW_REPORT = "review_report"
    PORTFOLIO_PROPOSAL = "portfolio_proposal"
    ORTHOGONALITY_REPORT = "orthogonality_report"
    RISK_COST_REVIEW = "risk_cost_review"
    RESEARCH_OUTPUT = "research_output"
    REGIME_ANALYSIS = "regime_analysis"


class ViolationType(str, Enum):
    """ 边界违规类型 """
    DIRECT_ORDER = "direct_order"             # Agent 直接生成订单
    BYPASS_HITL = "bypass_hitl"              # 绕过 HITL 审批
    BYPASS_BACKTEST = "bypass_backtest"       # 跳过回测
    MANIPULATE_POSITION = "manipulate_position"  # 直接修改持仓
    ACCESS_SECRET = "access_secret"          # 访问 API keys


# ============================================================================
# Cost Assumptions
# ============================================================================

@dataclass(slots=True)
class CostAssumptions:
    """ 成本假设 """
    trading_fee_bps: float = 10.0          # 交易费率 (basis points)
    slippage_bps: float = 5.0              # 滑点 (basis points)
    market_impact_bps: float = 2.0         # 市场冲击 (basis points)
    funding_rate_annual: float = 0.0       # 年化资金费率
    borrow_rate_annual: float = 0.0        # 年化借贷利率
    liquidation_risk_bps: float = 0.0      # 清算风险 (basis points)
    estimated_turnover_per_day: float = 1.0  # 日换手率


# ============================================================================
# Regime Conditions
# ============================================================================

@dataclass(slots=True)
class RegimeCondition:
    """ 市场状态启停条件 """
    regime_name: str                         # 状态名称 (e.g., "bull_trend", "high_volatility")
    entry_conditions: List[str]             # 入场条件
    exit_conditions: List[str]              # 退场条件
    min_duration_minutes: int = 60           # 最短持续时间（分钟）
    confidence_threshold: float = 0.7       # 置信度阈值


# ============================================================================
# Conflict Resolution
# ============================================================================

@dataclass(slots=True)
class ConflictResolution:
    """ 冲突优先级解决方案 """
    conflict_type: str                       # 冲突类型 (e.g., "directional", "regime")
    higher_priority_sleeve: str              # 更高优先级的 sleeve ID
    lower_priority_sleeve: str               # 更低优先级的 sleeve ID
    resolution_rule: str                     # 解决规则
    capital_adjustment: Optional[Decimal]   # 资金调整


# ============================================================================
# Sleeve Assignment
# ============================================================================

@dataclass(slots=True)
class SleeveAssignment:
    """ 组合中的 Sleeve 分配 """
    proposal_id: str                         # SleeveProposal ID
    capital_cap: Decimal                     # 资金上限
    weight: float                           # 权重 (0.0 - 1.0)
    max_position_size: Decimal               # 最大持仓
    regime_enabled: bool = True             # 是否启用状态机


# ============================================================================
# Sleeve Proposal
# ============================================================================

@dataclass(slots=True)
class SleeveProposal:
    """
    单策略研究提案
    
    每个 Specialist Agent 输出一个 SleeveProposal，
    包含核心假设、依赖特征、市场状态和失效条件。
    """
    # 身份字段
    proposal_id: str = field(default_factory=lambda: str(uuid.uuid4()))
    specialist_type: SpecialistType = SpecialistType.TREND
    
    # 核心内容
    hypothesis: str = ""                     # 核心假设
    required_features: List[str] = field(default_factory=list)  # 依赖的特征
    regime: str = ""                         # 适用市场状态
    failure_modes: List[str] = field(default_factory=list)    # 已知失效条件
    cost_assumptions: CostAssumptions = field(default_factory=CostAssumptions)
    
    # 证据与引用
    evidence_refs: List[str] = field(default_factory=list)     # 证据引用
    
    # 版本追踪
    feature_version: str = ""                # 特征版本
    prompt_version: str = ""                # prompt 版本
    
    # 可追踪性
    trace_id: str = field(default_factory=lambda: str(uuid.uuid4()))
    
    # 元数据
    status: ProposalStatus = ProposalStatus.PENDING
    created_at: datetime = field(default_factory=lambda: datetime.now(timezone.utc))
    updated_at: datetime = field(default_factory=lambda: datetime.now(timezone.utc))
    
    def content_hash(self) -> str:
        """生成内容哈希，用于去重和版本控制"""
        content = {
            "specialist_type": self.specialist_type.value,
            "hypothesis": self.hypothesis,
            "required_features": self.required_features,
            "regime": self.regime,
            "failure_modes": self.failure_modes,
            "feature_version": self.feature_version,
            "prompt_version": self.prompt_version,
        }
        content_str = json.dumps(content, sort_keys=True, default=str)
        return hashlib.sha256(content_str.encode()).hexdigest()[:16]
    
    def to_dict(self) -> Dict[str, Any]:
        """转换为字典"""
        return {
            "proposal_id": self.proposal_id,
            "specialist_type": self.specialist_type.value,
            "hypothesis": self.hypothesis,
            "required_features": self.required_features,
            "regime": self.regime,
            "failure_modes": self.failure_modes,
            "cost_assumptions": {
                "trading_fee_bps": self.cost_assumptions.trading_fee_bps,
                "slippage_bps": self.cost_assumptions.slippage_bps,
                "market_impact_bps": self.cost_assumptions.market_impact_bps,
                "funding_rate_annual": self.cost_assumptions.funding_rate_annual,
                "borrow_rate_annual": self.cost_assumptions.borrow_rate_annual,
                "liquidation_risk_bps": self.cost_assumptions.liquidation_risk_bps,
                "estimated_turnover_per_day": self.cost_assumptions.estimated_turnover_per_day,
            },
            "evidence_refs": self.evidence_refs,
            "feature_version": self.feature_version,
            "prompt_version": self.prompt_version,
            "trace_id": self.trace_id,
            "status": self.status.value,
            "created_at": self.created_at.isoformat(),
            "updated_at": self.updated_at.isoformat(),
            "content_hash": self.content_hash(),
        }


# ============================================================================
# Portfolio Proposal
# ============================================================================

@dataclass(slots=True)
class PortfolioProposal:
    """
    组合级提案
    
    Portfolio Constructor 将多个 SleeveProposal 组合成
    可测试的组合候选。
    """
    proposal_id: str = field(default_factory=lambda: str(uuid.uuid4()))
    
    # 组合构成
    sleeves: List[SleeveAssignment] = field(default_factory=list)
    
    # 资金约束
    capital_caps: Dict[str, Decimal] = field(default_factory=dict)  # 每个 sleeve 的资金上限
    
    # 状态机
    regime_conditions: Dict[str, RegimeCondition] = field(default_factory=dict)
    
    # 冲突解决
    conflict_priorities: List[ConflictResolution] = field(default_factory=list)
    
    # 风险说明
    risk_explanation: str = ""
    
    # 评估任务
    evaluation_task_id: str = ""
    
    # 版本追踪
    feature_version: str = ""
    prompt_version: str = ""
    
    # 可追踪性
    trace_id: str = field(default_factory=lambda: str(uuid.uuid4()))
    
    # 元数据
    status: ProposalStatus = ProposalStatus.PENDING
    created_at: datetime = field(default_factory=lambda: datetime.now(timezone.utc))
    updated_at: datetime = field(default_factory=lambda: datetime.now(timezone.utc))
    
    def total_capital_estimate(self) -> Decimal:
        """估算总资金需求"""
        return sum(self.capital_caps.values(), Decimal("0"))
    
    def content_hash(self) -> str:
        """生成内容哈希"""
        sleeves_content = [
            {
                "proposal_id": s.proposal_id,
                "capital_cap": str(s.capital_cap),
                "weight": s.weight,
            }
            for s in self.sleeves
        ]
        content = {
            "sleeves": sleeves_content,
            "capital_caps": {k: str(v) for k, v in self.capital_caps.items()},
            "risk_explanation": self.risk_explanation,
            "feature_version": self.feature_version,
            "prompt_version": self.prompt_version,
        }
        content_str = json.dumps(content, sort_keys=True, default=str)
        return hashlib.sha256(content_str.encode()).hexdigest()[:16]
    
    def to_dict(self) -> Dict[str, Any]:
        """转换为字典"""
        return {
            "proposal_id": self.proposal_id,
            "sleeves": [
                {
                    "proposal_id": s.proposal_id,
                    "capital_cap": str(s.capital_cap),
                    "weight": s.weight,
                    "max_position_size": str(s.max_position_size),
                    "regime_enabled": s.regime_enabled,
                }
                for s in self.sleeves
            ],
            "capital_caps": {k: str(v) for k, v in self.capital_caps.items()},
            "regime_conditions": {
                k: {
                    "regime_name": v.regime_name,
                    "entry_conditions": v.entry_conditions,
                    "exit_conditions": v.exit_conditions,
                    "min_duration_minutes": v.min_duration_minutes,
                    "confidence_threshold": v.confidence_threshold,
                }
                for k, v in self.regime_conditions.items()
            },
            "conflict_priorities": [
                {
                    "conflict_type": c.conflict_type,
                    "higher_priority_sleeve": c.higher_priority_sleeve,
                    "lower_priority_sleeve": c.lower_priority_sleeve,
                    "resolution_rule": c.resolution_rule,
                    "capital_adjustment": str(c.capital_adjustment) if c.capital_adjustment else None,
                }
                for c in self.conflict_priorities
            ],
            "risk_explanation": self.risk_explanation,
            "evaluation_task_id": self.evaluation_task_id,
            "feature_version": self.feature_version,
            "prompt_version": self.prompt_version,
            "trace_id": self.trace_id,
            "status": self.status.value,
            "created_at": self.created_at.isoformat(),
            "updated_at": self.updated_at.isoformat(),
            "content_hash": self.content_hash(),
        }


# ============================================================================
# Review Report
# ============================================================================

@dataclass(slots=True)
class ReviewReport:
    """
    评审报告
    
    记录 Red Team Agents 的评审结果。
    """
    report_id: str = field(default_factory=lambda: str(uuid.uuid4()))
    proposal_id: str = ""                   # 被评审的 proposal ID
    reviewer_type: str = ""                 # 评审者类型 (orthogonality / risk_cost)
    
    # 评审结论
    verdict: ReviewVerdict = ReviewVerdict.SKIP
    concerns: List[str] = field(default_factory=list)   # 担忧事项
    suggestions: List[str] = field(default_factory=list)  # 建议
    
    # 评分
    orthogonality_score: Optional[float] = None   # 正交性得分 (0.0 - 1.0)
    risk_score: Optional[float] = None           # 风险得分 (0.0 - 1.0)
    cost_score: Optional[float] = None           # 成本得分 (0.0 - 1.0)
    
    # 版本追踪
    feature_version: str = ""
    prompt_version: str = ""
    
    # 可追踪性
    trace_id: str = field(default_factory=lambda: str(uuid.uuid4()))
    
    # 元数据
    created_at: datetime = field(default_factory=lambda: datetime.now(timezone.utc))
    
    def to_dict(self) -> Dict[str, Any]:
        """转换为字典"""
        return {
            "report_id": self.report_id,
            "proposal_id": self.proposal_id,
            "reviewer_type": self.reviewer_type,
            "verdict": self.verdict.value,
            "concerns": self.concerns,
            "suggestions": self.suggestions,
            "orthogonality_score": self.orthogonality_score,
            "risk_score": self.risk_score,
            "cost_score": self.cost_score,
            "feature_version": self.feature_version,
            "prompt_version": self.prompt_version,
            "trace_id": self.trace_id,
            "created_at": self.created_at.isoformat(),
        }


# ============================================================================
# Review Result (简化版，用于 CommitteeRun)
# ============================================================================

@dataclass(slots=True)
class ReviewResult:
    """ 简化的评审结果 """
    reviewer_type: str              # orthogonality / risk_cost
    verdict: ReviewVerdict
    concerns: List[str] = field(default_factory=list)
    suggestions: List[str] = field(default_factory=list)
    scores: Dict[str, float] = field(default_factory=dict)


# ============================================================================
# Committee Run
# ============================================================================

@dataclass(slots=True)
class CommitteeRun:
    """
    委员会运行记录
    
    记录完整的 Committee 运行过程，
    包括输入、Agent 输出、Review 结果和最终决策。
    """
    run_id: str = field(default_factory=lambda: str(uuid.uuid4()))
    
    # 输入
    research_request: str = ""              # 原始研究需求
    context_package_version: str = ""       # 使用的上下文包版本
    
    # Agent 输出
    sleeve_proposals: List[SleeveProposal] = field(default_factory=list)
    portfolio_proposal: Optional[PortfolioProposal] = None
    
    # Review 结果
    review_results: List[ReviewResult] = field(default_factory=list)
    
    # 最终决策
    human_decision: Optional[str] = None   # APPROVED / REJECTED
    approver: Optional[str] = None         # 审批人
    decision_reason: Optional[str] = None  # 决策理由
    
    # 关联的 backtest job
    backtest_job_id: Optional[str] = None
    
    # 最终结论
    final_status: ProposalStatus = ProposalStatus.PENDING
    
    # 版本追踪
    feature_version: str = ""
    prompt_version: str = ""
    
    # 可追踪性
    trace_id: str = field(default_factory=lambda: str(uuid.uuid4()))
    
    # 元数据
    status: CommitteeRunStatus = CommitteeRunStatus.PENDING
    created_at: datetime = field(default_factory=lambda: datetime.now(timezone.utc))
    updated_at: datetime = field(default_factory=lambda: datetime.now(timezone.utc))
    
    def to_dict(self) -> Dict[str, Any]:
        """转换为字典"""
        return {
            "run_id": self.run_id,
            "research_request": self.research_request,
            "context_package_version": self.context_package_version,
            "sleeve_proposals": [p.to_dict() for p in self.sleeve_proposals],
            "portfolio_proposal": self.portfolio_proposal.to_dict() if self.portfolio_proposal else None,
            "review_results": [
                {
                    "reviewer_type": r.reviewer_type,
                    "verdict": r.verdict.value,
                    "concerns": r.concerns,
                    "suggestions": r.suggestions,
                    "scores": r.scores,
                }
                for r in self.review_results
            ],
            "human_decision": self.human_decision,
            "approver": self.approver,
            "decision_reason": self.decision_reason,
            "backtest_job_id": self.backtest_job_id,
            "final_status": self.final_status.value,
            "feature_version": self.feature_version,
            "prompt_version": self.prompt_version,
            "trace_id": self.trace_id,
            "status": self.status.value,
            "created_at": self.created_at.isoformat(),
            "updated_at": self.updated_at.isoformat(),
        }


# ============================================================================
# Validation
# ============================================================================

@dataclass(slots=True)
class Violation:
    """ 边界违规记录 """
    violation_type: ViolationType
    description: str
    context: Dict[str, Any] = field(default_factory=dict)


@dataclass(slots=True)
class ValidationResult:
    """ 验证结果 """
    is_valid: bool
    violations: List[Violation] = field(default_factory=list)
    
    @classmethod
    def valid(cls) -> ValidationResult:
        """ 创建有效的验证结果 """
        return cls(is_valid=True, violations=[])
    
    @classmethod
    def invalid(cls, violations: List[Violation]) -> ValidationResult:
        """ 创建无效的验证结果 """
        return cls(is_valid=False, violations=violations)


@dataclass(slots=True)
class AgentOutput:
    """
    Agent 输出封装
    
    每个 Agent 的输出必须封装为此类型，
    以确保包含必要的追踪和验证信息。
    """
    output_type: OutputType
    trace_id: str
    feature_version: str
    prompt_version: str
    validation_result: ValidationResult
    content: Dict[str, Any] = field(default_factory=dict)
    created_at: datetime = field(default_factory=lambda: datetime.now(timezone.utc))
    
    def to_dict(self) -> Dict[str, Any]:
        """转换为字典"""
        return {
            "output_type": self.output_type.value,
            "trace_id": self.trace_id,
            "feature_version": self.feature_version,
            "prompt_version": self.prompt_version,
            "validation_result": {
                "is_valid": self.validation_result.is_valid,
                "violations": [
                    {
                        "violation_type": v.violation_type.value,
                        "description": v.description,
                        "context": v.context,
                    }
                    for v in self.validation_result.violations
                ],
            },
            "content": self.content,
            "created_at": self.created_at.isoformat(),
        }


# ============================================================================
# Utility Functions
# ============================================================================

def generate_trace_id() -> str:
    """生成追踪 ID"""
    return str(uuid.uuid4())


def validate_proposal_output(output: AgentOutput) -> ValidationResult:
    """
    验证 Agent 输出是否合法
    
    检查点：
    1. trace_id 是否存在
    2. 版本标签是否存在
    3. 输出类型是否在允许列表中
    4. 是否包含实际内容
    """
    violations: List[Violation] = []
    
    # 检查 trace_id
    if not output.trace_id:
        violations.append(Violation(
            violation_type=ViolationType.DIRECT_ORDER,  # 使用最严重的违规类型
            description="Missing trace_id in agent output",
        ))
    
    # 检查版本标签
    if not output.feature_version:
        violations.append(Violation(
            violation_type=ViolationType.BYPASS_HITL,
            description="Missing feature_version in agent output",
        ))
    
    if not output.prompt_version:
        violations.append(Violation(
            violation_type=ViolationType.BYPASS_HITL,
            description="Missing prompt_version in agent output",
        ))
    
    # 检查输出类型
    allowed_types = [OutputType.SLEEVE_PROPOSAL, OutputType.RESEARCH_OUTPUT]
    if output.output_type not in allowed_types:
        violations.append(Violation(
            violation_type=ViolationType.DIRECT_ORDER,
            description=f"Invalid output type: {output.output_type}",
        ))
    
    # 检查内容
    if not output.content:
        violations.append(Violation(
            violation_type=ViolationType.BYPASS_BACKTEST,
            description="Empty content in agent output",
        ))
    
    if violations:
        return ValidationResult.invalid(violations)
    
    return ValidationResult.valid()
