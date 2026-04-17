"""
Test Portfolio Research Workflow - 工作流单元测试
=================================================

测试覆盖：
1. WorkflowConfig - 配置类
2. WorkflowResult - 结果类
3. PortfolioResearchWorkflow - 完整工作流
4. CommitteeToLifecycleAdapter - 生命周期适配
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
    ReviewReport,
    ReviewVerdict,
    PortfolioProposal,
    SleeveAssignment,
)
from services.portfolio_research_workflow import (
    PortfolioResearchWorkflow,
    WorkflowConfig,
    WorkflowResult,
)
from services.committee_to_lifecycle_adapter import (
    CommitteeToLifecycleAdapter,
    LifecycleAdapterConfig,
)


# ==================== WorkflowConfig 测试 ====================

class TestWorkflowConfig:
    """WorkflowConfig 测试"""

    def test_default_config(self):
        """测试默认配置"""
        config = WorkflowConfig()
        assert config.feature_version == "v1.0.0"
        assert config.prompt_version == "v1.0.0"
        assert config.context_package_version == "v1.0.0"
        assert config.max_parallel_specialists == 5
        assert config.enable_red_team is True
        assert config.enable_orthogonality_check is True
        assert config.store_results is True

    def test_custom_config(self):
        """测试自定义配置"""
        config = WorkflowConfig(
            feature_version="test_v1",
            enable_red_team=False,
            store_results=False,
        )
        assert config.feature_version == "test_v1"
        assert config.enable_red_team is False
        assert config.store_results is False

    def test_config_slots(self):
        """测试 slots=True"""
        config = WorkflowConfig()
        # slots=True 的 dataclass 不允许随意属性
        with pytest.raises(AttributeError):
            config.nonexistent_attr = "test"


# ==================== WorkflowResult 测试 ====================

class TestWorkflowResult:
    """WorkflowResult 测试"""

    def test_success_result(self):
        """测试成功结果"""
        run = CommitteeRun(run_id="test_run")
        result = WorkflowResult(
            success=True,
            committee_run=run,
            execution_time_seconds=1.5,
        )
        assert result.success is True
        assert result.committee_run.run_id == "test_run"
        assert result.execution_time_seconds == 1.5
        assert result.error_message is None

    def test_failure_result(self):
        """测试失败结果"""
        run = CommitteeRun(run_id="test_run")
        result = WorkflowResult(
            success=False,
            committee_run=run,
            execution_time_seconds=0.5,
            error_message="Test error",
        )
        assert result.success is False
        assert result.error_message == "Test error"


# ==================== PortfolioResearchWorkflow 测试 ====================

class TestPortfolioResearchWorkflow:
    """PortfolioResearchWorkflow 测试"""

    @pytest.fixture
    def workflow(self):
        """创建工作流实例"""
        config = WorkflowConfig(store_results=False)
        return PortfolioResearchWorkflow(config)

    def test_workflow_initialization(self, workflow):
        """测试工作流初始化"""
        assert workflow.config is not None
        assert workflow._router is not None
        assert workflow._portfolio_constructor is not None
        assert workflow._risk_cost_red_team is not None
        assert workflow._orthogonality_agent is not None

    @pytest.mark.asyncio
    async def test_run_research_request(self, workflow):
        """测试研究请求执行"""
        result = await workflow.run(
            research_request="研究 EMA 趋势策略",
            context={},
        )

        assert result.committee_run is not None
        assert result.committee_run.research_request == "研究 EMA 趋势策略"
        assert result.execution_time_seconds >= 0

    @pytest.mark.asyncio
    async def test_run_generates_sleeve_proposals(self, workflow):
        """测试生成 sleeve proposals"""
        result = await workflow.run(
            research_request="研究趋势策略",
        )

        # 至少应该有生成的 proposals
        assert isinstance(result.committee_run.sleeve_proposals, list)

    @pytest.mark.asyncio
    async def test_run_with_total_capital(self, workflow):
        """测试带资金参数运行"""
        result = await workflow.run(
            research_request="研究策略",
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
        assert result.committee_run.run_id is not None

    @pytest.mark.asyncio
    async def test_run_red_team_enabled_by_default(self, workflow):
        """测试默认启用 red team"""
        result = await workflow.run(
            research_request="研究策略",
        )

        # review_results 应该存在
        assert isinstance(result.committee_run.review_results, list)

    @pytest.mark.asyncio
    async def test_run_red_team_disabled(self):
        """测试禁用 red team"""
        config = WorkflowConfig(enable_red_team=False, store_results=False)
        workflow = PortfolioResearchWorkflow(config)

        result = await workflow.run(
            research_request="研究策略",
        )

        assert result.committee_run is not None

    @pytest.mark.asyncio
    async def test_run_error_handling(self):
        """测试错误处理 - 当没有有效 proposals 时"""
        # 使用一个不会生成任何 proposal 的请求
        config = WorkflowConfig(enable_red_team=False, store_results=False)
        workflow = PortfolioResearchWorkflow(config)

        # Mock _run_specialists 返回空列表
        workflow._run_specialists = MagicMock(return_value=[])

        result = await workflow.run(
            research_request="研究策略",
        )

        # 应该失败，因为没有 proposals
        assert result.success is False
        assert result.committee_run.status == CommitteeRunStatus.FAILED

    def test_filter_valid_proposals_all_pass(self, workflow):
        """测试过滤 - 全部通过"""
        proposal = SleeveProposal(
            specialist_type=SpecialistType.TREND,
            hypothesis="测试",
            required_features=["ema"],
        )
        report = ReviewReport(
            report_id="r1",
            proposal_id=proposal.proposal_id,
            reviewer_type="risk_cost",
            verdict=ReviewVerdict.PASS,
        )

        valid = workflow._filter_valid_proposals([proposal], [report])
        assert len(valid) == 1

    def test_filter_valid_proposals_one_fail(self, workflow):
        """测试过滤 - 一个失败"""
        proposal = SleeveProposal(
            specialist_type=SpecialistType.TREND,
            hypothesis="测试",
            required_features=["ema"],
        )
        report = ReviewReport(
            report_id="r1",
            proposal_id=proposal.proposal_id,
            reviewer_type="risk_cost",
            verdict=ReviewVerdict.FAIL,
        )

        valid = workflow._filter_valid_proposals([proposal], [report])
        assert len(valid) == 0

    def test_filter_valid_proposals_conditional(self, workflow):
        """测试过滤 - CONDITIONAL 也算通过"""
        proposal = SleeveProposal(
            specialist_type=SpecialistType.TREND,
            hypothesis="测试",
            required_features=["ema"],
        )
        report = ReviewReport(
            report_id="r1",
            proposal_id=proposal.proposal_id,
            reviewer_type="risk_cost",
            verdict=ReviewVerdict.CONDITIONAL,
        )

        valid = workflow._filter_valid_proposals([proposal], [report])
        assert len(valid) == 1


# ==================== CommitteeToLifecycleAdapter 测试 ====================

class TestCommitteeToLifecycleAdapter:
    """CommitteeToLifecycleAdapter 测试"""

    @pytest.fixture
    def adapter(self):
        """创建适配器实例"""
        lifecycle_manager = MagicMock()
        hitl_governance = MagicMock()
        config = LifecycleAdapterConfig()
        return CommitteeToLifecycleAdapter(lifecycle_manager, hitl_governance, config)

    def test_adapter_initialization(self, adapter):
        """测试适配器初始化"""
        assert adapter._lifecycle_manager is not None
        assert adapter._hitl_governance is not None
        assert adapter._config is not None

    @pytest.mark.asyncio
    async def test_submit_for_approval(self, adapter):
        """测试提交审批"""
        run = CommitteeRun(run_id="test_run", research_request="研究")
        run.sleeve_proposals = [
            SleeveProposal(specialist_type=SpecialistType.TREND, hypothesis="test")
        ]

        result = await adapter.submit_for_approval(run)

        assert result["success"] is True
        assert run.final_status == ProposalStatus.IN_REVIEW

    @pytest.mark.asyncio
    async def test_approve_and_create_backtest(self, adapter):
        """测试审批通过并创建回测"""
        run = CommitteeRun(run_id="test_run", research_request="研究")
        run.sleeve_proposals = [
            SleeveProposal(specialist_type=SpecialistType.TREND, hypothesis="test")
        ]

        result = await adapter.approve_and_create_backtest(
            run, approver="test_user", approval_comment="LGTM"
        )

        assert result["success"] is True
        assert run.human_decision == "APPROVED"
        assert run.approver == "test_user"
        assert run.decision_reason == "LGTM"
        assert run.final_status == ProposalStatus.APPROVED

    @pytest.mark.asyncio
    async def test_reject(self, adapter):
        """测试拒绝"""
        run = CommitteeRun(run_id="test_run", research_request="研究")

        result = await adapter.reject(
            run, rejector="test_user", reason="风险过高"
        )

        assert result["success"] is True
        assert run.human_decision == "REJECTED"
        assert run.approver == "test_user"
        assert run.decision_reason == "风险过高"
        assert run.final_status == ProposalStatus.REJECTED

    def test_generate_strategy_code(self, adapter):
        """测试生成策略代码"""
        run = CommitteeRun(
            run_id="test_12345678",
            research_request="研究趋势策略",
        )
        run.sleeve_proposals = [
            SleeveProposal(specialist_type=SpecialistType.TREND, hypothesis="test"),
            SleeveProposal(specialist_type=SpecialistType.PRICE_VOLUME, hypothesis="test2"),
        ]

        code = adapter._generate_strategy_code(run)

        assert "CommitteeRun test_12345678" in code
        assert "sleeve_count = 2" in code

    def test_generate_backtest_config(self, adapter):
        """测试生成回测配置"""
        run = CommitteeRun(run_id="test_run")
        run.portfolio_proposal = PortfolioProposal(
            proposal_id="p1",
            sleeves=[
                SleeveAssignment(
                    proposal_id="s1",
                    capital_cap=Decimal("1000"),
                    weight=0.5,
                    max_position_size=Decimal("500"),
                )
            ],
        )

        config = adapter._generate_backtest_config(run)

        assert config["run_id"] == "test_run"
        assert len(config["sleeves"]) == 1


# ==================== LifecycleAdapterConfig 测试 ====================

class TestLifecycleAdapterConfig:
    """LifecycleAdapterConfig 测试"""

    def test_default_config(self):
        """测试默认配置"""
        config = LifecycleAdapterConfig()
        assert config.auto_submit_to_hitl is True
        assert config.auto_create_backtest is False
        assert config.default_approval_timeout_seconds == 3600

    def test_custom_config(self):
        """测试自定义配置"""
        config = LifecycleAdapterConfig(
            auto_create_backtest=True,
            default_approval_timeout_seconds=7200,
        )
        assert config.auto_create_backtest is True
        assert config.default_approval_timeout_seconds == 7200


# ==================== 集成测试 ====================

class TestWorkflowIntegration:
    """工作流集成测试"""

    @pytest.mark.asyncio
    async def test_full_workflow_with_real_agents(self):
        """测试完整工作流（使用真实 agents）"""
        config = WorkflowConfig(
            enable_red_team=True,
            enable_orthogonality_check=True,
            store_results=False,
        )
        workflow = PortfolioResearchWorkflow(config)

        result = await workflow.run(
            research_request="研究 EMA 交叉策略",
            context={"data_sources": ["binance"]},
        )

        assert result.success is True
        assert result.committee_run.status == CommitteeRunStatus.COMPLETED
        assert len(result.committee_run.sleeve_proposals) > 0
        assert result.committee_run.portfolio_proposal is not None

    @pytest.mark.asyncio
    async def test_workflow_with_multiple_specialists(self):
        """测试多 specialist 路由"""
        config = WorkflowConfig(store_results=False)
        workflow = PortfolioResearchWorkflow(config)

        # 这个请求应该触发多个 specialist
        result = await workflow.run(
            research_request="研究 EMA 交叉和成交量异常",
        )

        assert result.committee_run is not None
        # 多个 specialist 可能产生多个 proposals
        assert isinstance(result.committee_run.sleeve_proposals, list)
