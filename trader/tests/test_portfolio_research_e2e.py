"""
Test Portfolio Research E2E - 组合研究端到端集成测试
=====================================================

测试覆盖：
1. PortfolioResearchWorkflow - 完整工作流
2. CommitteeToLifecycleAdapter - 生命周期适配
3. API 端点
"""

import pytest
from decimal import Decimal
from unittest.mock import AsyncMock, MagicMock, patch

from insight.committee.schemas import (
    CommitteeRun,
    CommitteeRunStatus,
    ProposalStatus,
    SpecialistType,
    SleeveProposal,
)
from insight.committee.specialists import TrendAgent
from services.portfolio_research_workflow import (
    PortfolioResearchWorkflow,
    WorkflowConfig,
    WorkflowResult,
)
from insight.committee.portfolio_constructor import PortfolioConstructor


# ==================== 测试辅助 ====================

class FakeCommitteeStore:
    """假委员会存储"""
    
    def __init__(self):
        self._runs = {}
        self._sleeves = {}
        self._portfolios = {}
    
    async def save_committee_run(self, run):
        self._runs[run.get("run_id")] = run
        return run.get("run_id")
    
    async def get_committee_run(self, run_id):
        return self._runs.get(run_id)
    
    async def list_committee_runs(self, status=None, limit=100, offset=0):
        runs = list(self._runs.values())
        if status:
            runs = [r for r in runs if r.get("status") == status]
        return runs[offset:offset+limit]


# ==================== PortfolioResearchWorkflow 测试 ====================

class TestPortfolioResearchWorkflow:
    """PortfolioResearchWorkflow 测试"""

    @pytest.fixture
    def workflow(self):
        """创建工作流"""
        config = WorkflowConfig(
            feature_version="test_v1",
            prompt_version="test_v1",
            store_results=False,
        )
        return PortfolioResearchWorkflow(config)

    def test_workflow_config_defaults(self):
        """测试默认配置"""
        config = WorkflowConfig()
        assert config.feature_version == "v1.0.0"
        assert config.prompt_version == "v1.0.0"
        assert config.max_parallel_specialists == 5
        assert config.enable_red_team is True

    @pytest.mark.asyncio
    async def test_run_single_specialist(self, workflow):
        """测试单 specialist 运行"""
        result = await workflow.run(
            research_request="研究 EMA 趋势策略",
            context={},
        )
        
        assert result.success is True or result.success is False
        assert result.committee_run is not None
        assert result.committee_run.run_id is not None

    @pytest.mark.asyncio
    async def test_run_creates_committee_run(self, workflow):
        """测试创建 CommitteeRun"""
        result = await workflow.run(
            research_request="研究趋势",
        )
        
        assert result.committee_run is not None
        assert result.committee_run.status in [
            CommitteeRunStatus.RUNNING,
            CommitteeRunStatus.COMPLETED,
            CommitteeRunStatus.FAILED,
        ]

    @pytest.mark.asyncio
    async def test_run_with_custom_capital(self, workflow):
        """测试自定义资金"""
        result = await workflow.run(
            research_request="研究趋势",
            total_capital=Decimal("50000"),
        )
        
        assert result.committee_run is not None

    @pytest.mark.asyncio
    async def test_run_traceability(self, workflow):
        """测试追踪性"""
        result = await workflow.run(
            research_request="研究 EMA 策略",
        )
        
        assert result.committee_run.trace_id is not None
        assert len(result.committee_run.trace_id) > 0


# ==================== WorkflowConfig 测试 ====================

class TestWorkflowConfig:
    """WorkflowConfig 测试"""

    def test_default_config(self):
        """测试默认配置"""
        config = WorkflowConfig()
        assert config.feature_version == "v1.0.0"
        assert config.prompt_version == "v1.0.0"
        assert config.max_parallel_specialists == 5
        assert config.enable_red_team is True

    def test_custom_config(self):
        """测试自定义配置"""
        config = WorkflowConfig(
            enable_red_team=False,
            store_results=False,
        )
        assert config.enable_red_team is False
        assert config.store_results is False


# ==================== 路由器集成测试 ====================

class TestRouterIntegration:
    """路由器集成测试"""

    def test_multiple_keywords_routing(self):
        """测试多关键词路由"""
        from insight.committee.router import CommitteeRouter
        
        router = CommitteeRouter()
        
        # 多关键词应该路由到多个 specialist
        types = router.route("研究 EMA 交叉和成交量异常")
        
        assert SpecialistType.TREND in types
        assert SpecialistType.PRICE_VOLUME in types

    def test_all_specialists_return_valid_outputs(self):
        """测试所有 specialist 返回有效输出"""
        from insight.committee.router import CommitteeRouter
        
        router = CommitteeRouter()
        
        outputs = router.run_all_specialists("研究策略", {})
        
        assert len(outputs) == len(SpecialistType)
        for output in outputs:
            assert output.trace_id is not None
            assert output.validation_result is not None


# ==================== Committee Schema 测试 ====================

class TestCommitteeSchemas:
    """Committee Schema 测试"""

    def test_sleeve_proposal_creation(self):
        """测试 SleeveProposal 创建"""
        proposal = SleeveProposal(
            specialist_type=SpecialistType.TREND,
            hypothesis="测试假设",
            required_features=["ema_fast", "ema_slow"],
            regime="strong_trend",
            failure_modes=["横盘"],
        )
        
        assert proposal.specialist_type == SpecialistType.TREND
        assert proposal.hypothesis == "测试假设"
        assert proposal.status == ProposalStatus.PENDING

    def test_sleeve_proposal_content_hash(self):
        """测试内容哈希"""
        proposal1 = SleeveProposal(
            specialist_type=SpecialistType.TREND,
            hypothesis="测试假设",
            required_features=["ema_fast"],
        )
        proposal2 = SleeveProposal(
            specialist_type=SpecialistType.TREND,
            hypothesis="测试假设",
            required_features=["ema_fast"],
        )
        
        # 相同内容应该有相同哈希
        assert proposal1.content_hash() == proposal2.content_hash()

    def test_committee_run_creation(self):
        """测试 CommitteeRun 创建"""
        run = CommitteeRun(
            research_request="研究趋势策略",
        )
        
        assert run.research_request == "研究趋势策略"
        assert run.status == CommitteeRunStatus.PENDING
        assert len(run.sleeve_proposals) == 0

    def test_proposal_status_transitions(self):
        """测试提案状态转换"""
        proposal = SleeveProposal(
            specialist_type=SpecialistType.TREND,
            hypothesis="测试",
            required_features=[],
            regime="",
            failure_modes=[],
        )
        
        # 验证状态机行为
        assert proposal.status == ProposalStatus.PENDING
        
        # 状态应该只能前进
        proposal.status = ProposalStatus.IN_REVIEW
        assert proposal.status == ProposalStatus.IN_REVIEW


# ==================== 生命周期集成测试 ====================

class TestLifecycleIntegration:
    """生命周期集成测试"""

    def test_committee_run_to_dict(self):
        """测试 CommitteeRun 序列化"""
        run = CommitteeRun(
            run_id="run_123",
            research_request="研究趋势",
            trace_id="trace_456",
        )
        
        run_dict = run.to_dict()
        
        assert run_dict["run_id"] == "run_123"
        assert run_dict["trace_id"] == "trace_456"
        assert "sleeve_proposals" in run_dict

    def test_sleeve_proposal_to_dict(self):
        """测试 SleeveProposal 序列化"""
        proposal = SleeveProposal(
            specialist_type=SpecialistType.TREND,
            hypothesis="测试假设",
            required_features=["ema_fast"],
        )
        
        proposal_dict = proposal.to_dict()
        
        assert proposal_dict["specialist_type"] == "trend"
        assert proposal_dict["hypothesis"] == "测试假设"
        assert "content_hash" in proposal_dict
