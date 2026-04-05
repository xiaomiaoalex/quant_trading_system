"""
Test Portfolio Proposal Store - 组合提案持久化测试
================================================

注意：PostgreSQL 集成测试需要正确的连接配置。
本文件主要测试内存存储功能。

PostgreSQL 测试（需要环境变量配置）：
    POSTGRES_CONNECTION_STRING=postgresql://trader:trader_pwd@127.0.0.1:5432/trading \
    python -m pytest trader/tests/test_portfolio_proposal_store.py -v
"""

import pytest
from datetime import datetime, timezone
from decimal import Decimal

from trader.adapters.persistence.portfolio_proposal_store import (
    DecimalEncoder,
    decimal_decoder,
)


# ==================== Decimal 处理测试 ====================

class TestDecimalEncoding:
    """Decimal 编码测试"""

    def test_decimal_encoder(self):
        """测试 Decimal 编码器"""
        import json
        
        data = {
            "capital_cap": Decimal("1000.50"),
            "weight": 0.5,
        }
        
        encoded = json.dumps(data, cls=DecimalEncoder)
        decoded = json.loads(encoded)
        
        assert decoded["capital_cap"] == "1000.50"
        assert decoded["weight"] == 0.5

    def test_decimal_decoder(self):
        """测试 Decimal 解码器"""
        data = {
            "capital_cap": "1000.50",
            "max_position_size": "500.25",
            "nested": {
                "total_capital_estimate": "2000.00"
            }
        }
        
        decoded = decimal_decoder(data)
        
        assert isinstance(decoded["capital_cap"], Decimal)
        assert decoded["capital_cap"] == Decimal("1000.50")
        assert isinstance(decoded["max_position_size"], Decimal)
        assert isinstance(decoded["nested"]["total_capital_estimate"], Decimal)

    def test_decimal_decoder_non_decimal_fields(self):
        """测试非 Decimal 字段不受影响"""
        data = {
            "name": "test",
            "count": 42,
            "ratio": 0.75,
        }
        
        decoded = decimal_decoder(data)
        
        assert decoded["name"] == "test"
        assert decoded["count"] == 42
        assert decoded["ratio"] == 0.75

    def test_decimal_decoder_empty_dict(self):
        """测试空字典"""
        decoded = decimal_decoder({})
        assert decoded == {}


# ==================== 内存存储测试 ====================

class TestMemoryStorage:
    """内存存储测试（无需 PostgreSQL）"""

    @pytest.fixture
    def store(self):
        """创建 store 实例（使用内存存储）"""
        from trader.adapters.persistence.portfolio_proposal_store import PortfolioProposalStore
        return PortfolioProposalStore()

    @pytest.fixture
    def sample_run(self):
        """创建示例数据"""
        return {
            "run_id": "run_memory_test",
            "research_request": "内存测试",
            "context_package_version": "v1.0.0",
            "sleeve_proposals": [],
            "portfolio_proposal": None,
            "review_results": [],
            "final_status": "pending",
            "feature_version": "v1.0.0",
            "prompt_version": "v1.0.0",
            "trace_id": "trace_memory",
            "status": "pending",
            "created_at": datetime.now(timezone.utc).isoformat(),
            "updated_at": datetime.now(timezone.utc).isoformat(),
        }

    @pytest.mark.asyncio
    async def test_memory_save_and_get(self, store, sample_run):
        """测试内存存储保存和获取"""
        saved_id = await store.save_committee_run(sample_run)
        assert saved_id == "run_memory_test"
        
        retrieved = await store.get_committee_run("run_memory_test")
        assert retrieved is not None
        assert retrieved["research_request"] == "内存测试"

    @pytest.mark.asyncio
    async def test_memory_list(self, store, sample_run):
        """测试内存存储列表"""
        await store.save_committee_run(sample_run)
        
        runs = await store.list_committee_runs(limit=10)
        assert len(runs) >= 1

    @pytest.mark.asyncio
    async def test_memory_save_sleeve_proposal(self, store):
        """测试内存存储保存 SleeveProposal"""
        proposal = {
            "proposal_id": "prop_memory_test",
            "specialist_type": "trend",
            "hypothesis": "测试假设",
            "required_features": ["ema_fast", "ema_slow"],
            "regime": "strong_trend",
            "failure_modes": ["横盘", "反转"],
            "cost_assumptions": {
                "trading_fee_bps": 10.0,
                "slippage_bps": 5.0,
            },
            "evidence_refs": ["ref1"],
            "feature_version": "v1.0.0",
            "prompt_version": "v1.0.0",
            "trace_id": "trace_prop",
            "status": "pending",
            "content_hash": "abc123",
            "created_at": datetime.now(timezone.utc).isoformat(),
            "updated_at": datetime.now(timezone.utc).isoformat(),
        }
        
        saved_id = await store.save_sleeve_proposal(proposal)
        assert saved_id == "prop_memory_test"
        
        retrieved = await store.get_sleeve_proposal(saved_id)
        assert retrieved is not None
        assert retrieved["specialist_type"] == "trend"

    @pytest.mark.asyncio
    async def test_memory_save_review_report(self, store):
        """测试内存存储保存 ReviewReport"""
        report = {
            "report_id": "report_memory_test",
            "proposal_id": "prop_1",
            "reviewer_type": "orthogonality",
            "verdict": "pass",
            "concerns": [],
            "suggestions": ["建议1"],
            "orthogonality_score": 0.8,
            "risk_score": 0.7,
            "cost_score": 0.7,
            "feature_version": "v1.0.0",
            "prompt_version": "v1.0.0",
            "trace_id": "trace_report",
            "created_at": datetime.now(timezone.utc).isoformat(),
        }
        
        saved_id = await store.save_review_report(report)
        assert saved_id == "report_memory_test"


# ==================== Schema 验证测试 ====================

class TestSchemaValidation:
    """Schema 验证测试"""

    def test_committee_run_to_dict(self):
        """测试 CommitteeRun 序列化"""
        from insight.committee.schemas import CommitteeRun, CommitteeRunStatus
        
        run = CommitteeRun(
            run_id="run_123",
            research_request="研究趋势",
            trace_id="trace_456",
        )
        
        run_dict = run.to_dict()
        
        assert run_dict["run_id"] == "run_123"
        assert run_dict["trace_id"] == "trace_456"
        assert "sleeve_proposals" in run_dict
        assert "status" in run_dict

    def test_sleeve_proposal_to_dict(self):
        """测试 SleeveProposal 序列化"""
        from insight.committee.schemas import (
            SleeveProposal, 
            SpecialistType,
            CostAssumptions,
        )
        
        proposal = SleeveProposal(
            specialist_type=SpecialistType.TREND,
            hypothesis="测试假设",
            required_features=["ema_fast"],
            regime="strong_trend",
            failure_modes=["横盘"],
            cost_assumptions=CostAssumptions(),
        )
        
        proposal_dict = proposal.to_dict()
        
        assert proposal_dict["specialist_type"] == "trend"
        assert proposal_dict["hypothesis"] == "测试假设"
        assert "content_hash" in proposal_dict

    def test_proposal_content_hash(self):
        """测试 Proposal 内容哈希"""
        from insight.committee.schemas import SleeveProposal, SpecialistType
        
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
