"""
Base Specialist - Specialist Agent 基类
========================================

所有 Specialist Agents 必须继承此基类。

设计原则：
1. 所有 Agent 只输出 SleeveProposal
2. 不输出交易指令，不输出可直接部署代码
3. 必须包含完整的追踪和版本信息
"""

from __future__ import annotations

import logging
from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional

from insight.committee.schemas import (
    AgentOutput,
    CostAssumptions,
    OutputType,
    ProposalStatus,
    SleeveProposal,
    SpecialistType,
    ValidationResult,
    Violation,
    ViolationType,
    generate_trace_id,
)

logger = logging.getLogger(__name__)


@dataclass
class SpecialistConfig:
    """Specialist Agent 配置"""
    # 版本信息
    feature_version: str = "v1.0.0"
    prompt_version: str = "v1.0.0"
    
    # 行为配置
    max_hypothesis_length: int = 1000
    max_failure_modes: int = 10
    min_confidence_threshold: float = 0.6
    
    # 成本假设默认值
    default_trading_fee_bps: float = 10.0
    default_slippage_bps: float = 5.0


class BaseSpecialist(ABC):
    """
    Specialist Agent 基类
    
    所有 Specialist Agents 必须：
    1. 继承此基类
    2. 实现 research() 方法
    3. 不输出交易指令
    4. 所有输出必须包含追踪信息
    """
    
    def __init__(self, config: Optional[SpecialistConfig] = None):
        self.config = config or SpecialistConfig()
        self._initialized_at = datetime.now(timezone.utc)
    
    @property
    @abstractmethod
    def specialist_type(self) -> SpecialistType:
        """返回 Agent 类型"""
        raise NotImplementedError
    
    @property
    def name(self) -> str:
        """返回 Agent 名称"""
        return self.__class__.__name__
    
    @abstractmethod
    def _do_research(self, research_request: str, context: Dict[str, Any]) -> Dict[str, Any]:
        """
        执行具体的研究逻辑
        
        Args:
            research_request: 研究请求
            context: 上下文信息（市场数据、特征等）
            
        Returns:
            研究结果字典，包含：
            - hypothesis: 核心假设
            - required_features: 依赖的特征列表
            - regime: 适用市场状态
            - failure_modes: 失效条件
            - evidence_refs: 证据引用
        """
        raise NotImplementedError
    
    def research(self, research_request: str, context: Optional[Dict[str, Any]] = None) -> AgentOutput:
        """
        执行研究并返回结构化的 Agent 输出
        
        Args:
            research_request: 研究请求
            context: 上下文信息
            
        Returns:
            AgentOutput: 结构化的 Agent 输出
        """
        trace_id = generate_trace_id()
        context = context or {}
        
        try:
            # 执行研究
            research_result = self._do_research(research_request, context)
            
            # 构建 SleeveProposal
            proposal = self._build_proposal(research_result, trace_id)
            
            # 验证输出
            validation_result = self._validate_output(proposal)
            
            # 构建 AgentOutput
            output = AgentOutput(
                output_type=OutputType.SLEEVE_PROPOSAL,
                trace_id=trace_id,
                feature_version=self.config.feature_version,
                prompt_version=self.config.prompt_version,
                validation_result=validation_result,
                content=proposal.to_dict(),
            )
            
            logger.info(
                f"{self.name} completed research: trace_id={trace_id}, "
                f"valid={validation_result.is_valid}"
            )
            
            return output
            
        except Exception as e:
            logger.error(f"{self.name} research failed: {e}", exc_info=True)
            
            # 返回无效输出
            return AgentOutput(
                output_type=OutputType.SLEEVE_PROPOSAL,
                trace_id=trace_id,
                feature_version=self.config.feature_version,
                prompt_version=self.config.prompt_version,
                validation_result=ValidationResult.invalid([
                    Violation(
                        violation_type=ViolationType.DIRECT_ORDER,
                        description=f"Research failed: {str(e)}",
                    )
                ]),
                content={},
            )
    
    def _build_proposal(self, research_result: Dict[str, Any], trace_id: str) -> SleeveProposal:
        """从研究结果构建 SleeveProposal"""
        
        # 构建成本假设
        cost_assumptions = CostAssumptions(
            trading_fee_bps=self.config.default_trading_fee_bps,
            slippage_bps=self.config.default_slippage_bps,
        )
        
        return SleeveProposal(
            specialist_type=self.specialist_type,
            hypothesis=research_result.get("hypothesis", "")[:self.config.max_hypothesis_length],
            required_features=research_result.get("required_features", [])[:self.config.max_failure_modes],
            regime=research_result.get("regime", ""),
            failure_modes=research_result.get("failure_modes", [])[:self.config.max_failure_modes],
            cost_assumptions=cost_assumptions,
            evidence_refs=research_result.get("evidence_refs", []),
            feature_version=self.config.feature_version,
            prompt_version=self.config.prompt_version,
            trace_id=trace_id,
            status=ProposalStatus.PENDING,
        )
    
    def _validate_output(self, proposal: SleeveProposal) -> ValidationResult:
        """验证 Agent 输出"""
        violations: List[Violation] = []
        
        # 检查假设非空
        if not proposal.hypothesis:
            violations.append(Violation(
                violation_type=ViolationType.BYPASS_HITL,
                description="Empty hypothesis in proposal",
            ))
        
        # 检查假设长度
        if len(proposal.hypothesis) > self.config.max_hypothesis_length:
            violations.append(Violation(
                violation_type=ViolationType.BYPASS_HITL,
                description=f"Hypothesis too long: {len(proposal.hypothesis)} chars",
            ))
        
        # 检查特征列表
        if not proposal.required_features:
            violations.append(Violation(
                violation_type=ViolationType.BYPASS_BACKTEST,
                description="No required features specified",
            ))
        
        # 检查版本标签
        if not self.config.feature_version:
            violations.append(Violation(
                violation_type=ViolationType.BYPASS_HITL,
                description="Missing feature_version",
            ))
        
        if not self.config.prompt_version:
            violations.append(Violation(
                violation_type=ViolationType.BYPASS_HITL,
                description="Missing prompt_version",
            ))
        
        if violations:
            return ValidationResult.invalid(violations)
        
        return ValidationResult.valid()
    
    def get_system_prompt(self) -> str:
        """获取系统提示词"""
        return f"""You are a {self.name} ({self.specialist_type.value}) Specialist Agent.

Your role is to research market patterns and generate SleeveProposals for portfolio construction.

Constraints:
1. You MUST output only SleeveProposal objects, NOT trading instructions
2. You MUST include trace_id and version tags in all outputs
3. You CANNOT directly place orders or modify positions
4. All proposals must go through HITL approval before backtesting

Your research domain: {self._get_research_domain_description()}

When researching:
1. Analyze the provided market context
2. Identify patterns and hypotheses
3. Specify required features and regime conditions
4. Document known failure modes
5. Generate a well-structured SleeveProposal
"""
    
    @abstractmethod
    def _get_research_domain_description(self) -> str:
        """返回研究领域描述"""
        raise NotImplementedError
