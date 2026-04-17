"""
Committee Router - Specialist Agents 路由
=========================================

根据研究请求自动路由到合适的 Specialist Agent。

设计原则：
1. 基于关键词自动匹配 Specialist
2. 支持多 Agent 并行执行
3. 返回结构化的 AgentOutput 列表
"""

from __future__ import annotations

import logging
import uuid
from typing import Any, Dict, List, Optional, Set

from insight.committee.schemas import (
    AgentOutput,
    SleeveProposal,
    SpecialistType,
)
from insight.committee.specialists import (
    BaseSpecialist,
    SpecialistConfig,
    TrendAgent,
    PriceVolumeAgent,
    FundingOIAgent,
    OnChainAgent,
    EventRegimeAgent,
)

logger = logging.getLogger(__name__)


# 关键词到 Specialist 的映射
KEYWORD_TO_SPECIALIST: Dict[str, Set[SpecialistType]] = {
    # Trend keywords
    "trend": {SpecialistType.TREND},
    "ema": {SpecialistType.TREND},
    "moving average": {SpecialistType.TREND},
    "momentum": {SpecialistType.TREND},
    "bollinger": {SpecialistType.TREND},
    "adx": {SpecialistType.TREND},
    "趋势": {SpecialistType.TREND},
    "动量": {SpecialistType.TREND},
    
    # Price-Volume keywords
    "volume": {SpecialistType.PRICE_VOLUME},
    "volatility": {SpecialistType.PRICE_VOLUME},
    "成交量": {SpecialistType.PRICE_VOLUME},
    "波动率": {SpecialistType.PRICE_VOLUME},
    "背离": {SpecialistType.PRICE_VOLUME},
    "divergence": {SpecialistType.PRICE_VOLUME},
    "open interest": {SpecialistType.PRICE_VOLUME},
    "持仓量": {SpecialistType.PRICE_VOLUME},
    
    # Funding/OI keywords
    "funding": {SpecialistType.FUNDING_OI},
    "资金费率": {SpecialistType.FUNDING_OI},
    "oi": {SpecialistType.FUNDING_OI},
    "多空比": {SpecialistType.FUNDING_OI},
    "long short": {SpecialistType.FUNDING_OI},
    "资金结构": {SpecialistType.FUNDING_OI},
    
    # OnChain keywords
    "onchain": {SpecialistType.ONCHAIN},
    "链上": {SpecialistType.ONCHAIN},
    "exchange": {SpecialistType.ONCHAIN},
    "交易所": {SpecialistType.ONCHAIN},
    "liquidation": {SpecialistType.ONCHAIN},
    "爆仓": {SpecialistType.ONCHAIN},
    "holder": {SpecialistType.ONCHAIN},
    "持币": {SpecialistType.ONCHAIN},
    "whale": {SpecialistType.ONCHAIN},
    
    # Event keywords
    "event": {SpecialistType.EVENT_REGIME},
    "announcement": {SpecialistType.EVENT_REGIME},
    "公告": {SpecialistType.EVENT_REGIME},
    "regime": {SpecialistType.EVENT_REGIME},
    "状态": {SpecialistType.EVENT_REGIME},
    "calendar": {SpecialistType.EVENT_REGIME},
    "效应": {SpecialistType.EVENT_REGIME},
    "macro": {SpecialistType.EVENT_REGIME},
    "宏观": {SpecialistType.EVENT_REGIME},
    "CPI": {SpecialistType.EVENT_REGIME},
    "利率": {SpecialistType.EVENT_REGIME},
}


class CommitteeRouter:
    """
    Specialist Agents 路由器
    
    根据研究请求自动路由到合适的 Specialist Agent。
    """
    
    def __init__(self, config: Optional[SpecialistConfig] = None):
        self.config = config or SpecialistConfig()
        self._agents: Dict[SpecialistType, BaseSpecialist] = {}
        self._init_agents()
    
    def _init_agents(self) -> None:
        """初始化所有 Specialist Agents"""
        self._agents = {
            SpecialistType.TREND: TrendAgent(self.config),
            SpecialistType.PRICE_VOLUME: PriceVolumeAgent(self.config),
            SpecialistType.FUNDING_OI: FundingOIAgent(self.config),
            SpecialistType.ONCHAIN: OnChainAgent(self.config),
            SpecialistType.EVENT_REGIME: EventRegimeAgent(self.config),
        }
    
    def get_agent(self, specialist_type: SpecialistType) -> BaseSpecialist:
        """获取指定类型的 Agent"""
        if specialist_type not in self._agents:
            raise ValueError(f"Unknown specialist type: {specialist_type}")
        return self._agents[specialist_type]
    
    def route(self, research_request: str) -> Set[SpecialistType]:
        """
        根据研究请求路由到合适的 Specialist(s)
        
        Args:
            research_request: 研究请求
            
        Returns:
            匹配的 SpecialistType 集合
        """
        request_lower = research_request.lower()
        matched_types: Set[SpecialistType] = set()
        
        for keyword, specialist_types in KEYWORD_TO_SPECIALIST.items():
            if keyword in request_lower:
                matched_types.update(specialist_types)
        
        # 如果没有匹配到任何 Specialist，返回默认的 TrendAgent
        if not matched_types:
            logger.warning(
                f"No specialist matched for request: {research_request[:50]}... "
                f"Using default specialist."
            )
            matched_types.add(SpecialistType.TREND)
        
        return matched_types
    
    def run_specialists(
        self,
        research_request: str,
        specialist_types: Set[SpecialistType],
        context: Optional[Dict[str, Any]] = None,
    ) -> List[AgentOutput]:
        """
        运行指定的 Specialist Agent(s)
        
        Args:
            research_request: 研究请求
            specialist_types: 要运行的 Specialist 类型集合
            context: 上下文信息
            
        Returns:
            AgentOutput 列表
        """
        outputs: List[AgentOutput] = []
        
        for specialist_type in specialist_types:
            agent = self.get_agent(specialist_type)
            output = agent.research(research_request, context)
            outputs.append(output)
            
            logger.info(
                f"Ran specialist {specialist_type.value}: "
                f"trace_id={output.trace_id}, valid={output.validation_result.is_valid}"
            )
        
        return outputs
    
    def run_all_specialists(
        self,
        research_request: str,
        context: Optional[Dict[str, Any]] = None,
    ) -> List[AgentOutput]:
        """
        运行所有 Specialist Agent
        
        Args:
            research_request: 研究请求
            context: 上下文信息
            
        Returns:
            所有 AgentOutput 列表
        """
        return self.run_specialists(
            research_request,
            set(SpecialistType),
            context,
        )
    
    def route_and_run(
        self,
        research_request: str,
        context: Optional[Dict[str, Any]] = None,
    ) -> List[AgentOutput]:
        """
        自动路由并运行 Specialist(s)
        
        Args:
            research_request: 研究请求
            context: 上下文信息
            
        Returns:
            AgentOutput 列表
        """
        specialist_types = self.route(research_request)
        return self.run_specialists(research_request, specialist_types, context)
    
    def get_proposals_from_outputs(
        self,
        outputs: List[AgentOutput],
    ) -> List[SleeveProposal]:
        """
        从 AgentOutput 列表中提取有效的 SleeveProposal
        
        Args:
            outputs: AgentOutput 列表
            
        Returns:
            有效的 SleeveProposal 列表
        """
        proposals: List[SleeveProposal] = []
        
        for output in outputs:
            if not output.validation_result.is_valid:
                logger.warning(
                    f"Skipping invalid output: trace_id={output.trace_id}, "
                    f"violations={len(output.validation_result.violations)}"
                )
                continue
            
            if not output.content:
                logger.warning(f"Skipping empty output: trace_id={output.trace_id}")
                continue
            
            # 尝试从 content 构建 SleeveProposal
            try:
                proposal_data = output.content
                
                if isinstance(proposal_data, dict):
                    from insight.committee.schemas import SpecialistType
                    proposal = SleeveProposal(
                        proposal_id=proposal_data.get("proposal_id", str(uuid.uuid4())),
                        specialist_type=SpecialistType(proposal_data.get("specialist_type", "trend")),
                        hypothesis=proposal_data.get("hypothesis", ""),
                        required_features=proposal_data.get("required_features", []),
                        regime=proposal_data.get("regime", ""),
                        failure_modes=proposal_data.get("failure_modes", []),
                        evidence_refs=proposal_data.get("evidence_refs", []),
                        feature_version=output.feature_version,
                        prompt_version=output.prompt_version,
                        trace_id=output.trace_id,
                    )
                    proposals.append(proposal)
                elif isinstance(proposal_data, SleeveProposal):
                    proposals.append(proposal_data)
            except Exception as e:
                logger.error(f"Failed to parse proposal: {e}")
                continue
        
        return proposals
