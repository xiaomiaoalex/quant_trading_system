"""
In-Memory Store Tests - 内存存储测试
=====================================

此模块测试 InMemoryPortfolioProposalStore。
"""

import pytest

from trader.adapters.persistence.portfolio_proposals.memory_store import (
    InMemoryPortfolioProposalStore,
)
from trader.adapters.persistence.portfolio_proposals.models import (
    CostAssumptions,
    ProposalModel,
    ProposalStatus,
    ProposalType,
    SleeveData,
)


@pytest.fixture
def store() -> InMemoryPortfolioProposalStore:
    """创建并初始化 store"""
    s = InMemoryPortfolioProposalStore()
    return s


@pytest.fixture
def sleeve_proposal() -> ProposalModel:
    """创建测试用 Sleeve 提案"""
    return ProposalModel.create_sleeve(
        specialist_type="trend",
        hypothesis="Price will rise due to momentum",
        required_features=["ema_fast", "ema_slow"],
        regime="bull_trend",
        failure_modes=["reversal", "low_volume"],
        cost_assumptions=CostAssumptions(
            trading_fee_bps=10.0,
            slippage_bps=5.0,
        ),
        feature_version="v1.0",
        prompt_version="p1.0",
    )


@pytest.fixture
def portfolio_proposal() -> ProposalModel:
    """创建测试用 Portfolio 提案"""
    sleeve = SleeveData(
        sleeve_id="sleeve-1",
        capital_cap=1000.0,
        weight=0.5,
        max_position_size=100.0,
    )
    return ProposalModel.create_portfolio(
        sleeves=[sleeve],
        capital_caps={"sleeve-1": 1000.0},
        risk_explanation="Medium risk",
        feature_version="v1.0",
        prompt_version="p1.0",
    )


class TestInMemoryStoreBasic:
    """基本功能测试"""
    
    @pytest.mark.asyncio
    async def test_initialize_is_noop(self, store):
        """initialize() 应该是 no-op"""
        await store.initialize()
        assert store.is_initialized is True
    
    @pytest.mark.asyncio
    async def test_health_check(self, store):
        """健康检查应该返回 True"""
        await store.initialize()
        assert await store.health_check() is True


class TestInMemoryStoreCRUD:
    """CRUD 测试"""
    
    @pytest.mark.asyncio
    async def test_save_and_get_sleeve(self, store, sleeve_proposal):
        """保存并读取 Sleeve 提案"""
        await store.initialize()
        
        proposal_id = await store.save(sleeve_proposal)
        retrieved = await store.get_by_id(proposal_id)
        
        assert retrieved is not None
        assert retrieved.proposal_id == proposal_id
        assert retrieved.proposal_type == ProposalType.SLEEVE
        assert retrieved.specialist_type == "trend"
        assert retrieved.hypothesis == "Price will rise due to momentum"
        assert retrieved.required_features == ["ema_fast", "ema_slow"]
        assert retrieved.regime == "bull_trend"
        assert retrieved.failure_modes == ["reversal", "low_volume"]
        assert retrieved.cost_assumptions.trading_fee_bps == 10.0
    
    @pytest.mark.asyncio
    async def test_save_and_get_portfolio(self, store, portfolio_proposal):
        """保存并读取 Portfolio 提案"""
        await store.initialize()
        
        proposal_id = await store.save(portfolio_proposal)
        retrieved = await store.get_by_id(proposal_id)
        
        assert retrieved is not None
        assert retrieved.proposal_type == ProposalType.PORTFOLIO
        assert retrieved.risk_explanation == "Medium risk"
        assert len(retrieved.portfolio_sleeves) == 1
        assert retrieved.portfolio_capital_caps["sleeve-1"] == 1000.0
    
    @pytest.mark.asyncio
    async def test_get_nonexistent_returns_none(self, store):
        """查询不存在的 proposal 返回 None"""
        await store.initialize()
        
        result = await store.get_by_id("non-existent-id")
        assert result is None
    
    @pytest.mark.asyncio
    async def test_upsert_updates_existing(self, store, sleeve_proposal):
        """重复保存同一 proposal 应该更新"""
        await store.initialize()
        
        proposal_id = await store.save(sleeve_proposal)
        
        # 修改数据
        sleeve_proposal.payload["hypothesis"] = "Updated hypothesis"
        await store.save(sleeve_proposal)
        
        # 读取
        retrieved = await store.get_by_id(proposal_id)
        assert retrieved.hypothesis == "Updated hypothesis"
        
        # 应该只有一条记录
        count = await store.count()
        assert count == 1
    
    @pytest.mark.asyncio
    async def test_delete_removes_proposal(self, store, sleeve_proposal):
        """删除后提案不再存在"""
        await store.initialize()
        
        proposal_id = await store.save(sleeve_proposal)
        await store.delete(proposal_id)
        
        result = await store.get_by_id(proposal_id)
        assert result is None
    
    @pytest.mark.asyncio
    async def test_delete_nonexistent_succeeds(self, store):
        """删除不存在的 proposal 应该静默成功"""
        await store.initialize()
        
        # 不应该抛出异常
        await store.delete("non-existent-id")


class TestInMemoryStoreQueries:
    """查询测试"""
    
    @pytest.mark.asyncio
    async def test_list_by_status(self, store, sleeve_proposal):
        """按状态列出提案"""
        await store.initialize()
        
        # 创建不同状态的提案
        p1 = sleeve_proposal
        p1.status = ProposalStatus.PENDING
        await store.save(p1)
        
        p2 = ProposalModel.create_sleeve(
            specialist_type="trend",
            hypothesis="Another",
            required_features=["rsi"],
        )
        p2.status = ProposalStatus.APPROVED
        await store.save(p2)
        
        pending = await store.list_by_status(ProposalStatus.PENDING)
        assert len(pending) >= 1
        assert all(p.status == ProposalStatus.PENDING for p in pending)
        
        approved = await store.list_by_status(ProposalStatus.APPROVED)
        assert len(approved) >= 1
        assert all(p.status == ProposalStatus.APPROVED for p in approved)
    
    @pytest.mark.asyncio
    async def test_list_by_type(self, store, sleeve_proposal, portfolio_proposal):
        """按类型列出提案"""
        await store.initialize()
        
        await store.save(sleeve_proposal)
        await store.save(portfolio_proposal)
        
        sleeves = await store.list_by_type(ProposalType.SLEEVE)
        portfolios = await store.list_by_type(ProposalType.PORTFOLIO)
        
        assert all(p.proposal_type == ProposalType.SLEEVE for p in sleeves)
        assert all(p.proposal_type == ProposalType.PORTFOLIO for p in portfolios)
    
    @pytest.mark.asyncio
    async def test_list_by_specialist(self, store):
        """按 Specialist 类型列出提案"""
        await store.initialize()
        
        p1 = ProposalModel.create_sleeve(
            specialist_type="trend",
            hypothesis="Trend hypothesis",
            required_features=["ema"],
        )
        p2 = ProposalModel.create_sleeve(
            specialist_type="price_volume",
            hypothesis="PV hypothesis",
            required_features=["volume"],
        )
        await store.save(p1)
        await store.save(p2)
        
        trend_proposals = await store.list_by_specialist("trend")
        assert all(p.specialist_type == "trend" for p in trend_proposals)
        
        pv_proposals = await store.list_by_specialist("price_volume")
        assert all(p.specialist_type == "price_volume" for p in pv_proposals)
    
    @pytest.mark.asyncio
    async def test_list_ordering_created_at_desc(self, store):
        """list_*() 应该按 created_at DESC 排序"""
        import time
        
        await store.initialize()
        
        p1 = ProposalModel.create_sleeve(
            specialist_type="trend",
            hypothesis="First",
            required_features=["ema"],
        )
        await store.save(p1)
        
        time.sleep(0.01)  # 确保时间戳不同
        
        p2 = ProposalModel.create_sleeve(
            specialist_type="trend",
            hypothesis="Second",
            required_features=["ema"],
        )
        await store.save(p2)
        
        results = await store.list_by_type(ProposalType.SLEEVE)
        
        # 最新的应该在前面
        assert len(results) >= 2
        first_two = results[:2]
        for i in range(len(first_two) - 1):
            assert first_two[i].created_at >= first_two[i + 1].created_at


class TestInMemoryStorePagination:
    """分页测试"""
    
    @pytest.mark.asyncio
    async def test_pagination(self, store):
        """list_*() 应该正确支持分页"""
        await store.initialize()
        
        # 创建 5 个提案
        for i in range(5):
            p = ProposalModel.create_sleeve(
                specialist_type="trend",
                hypothesis=f"Hypothesis {i}",
                required_features=["ema"],
            )
            p.status = ProposalStatus.PENDING
            await store.save(p)
        
        # 限制为 2
        page1 = await store.list_by_status(ProposalStatus.PENDING, limit=2, offset=0)
        page2 = await store.list_by_status(ProposalStatus.PENDING, limit=2, offset=2)
        
        assert len(page1) == 2
        assert len(page2) == 2
        
        # 两页的 ID 不应该重叠
        page1_ids = {p.proposal_id for p in page1}
        page2_ids = {p.proposal_id for p in page2}
        assert page1_ids.isdisjoint(page2_ids)


class TestInMemoryStoreExists:
    """exists() 测试"""
    
    @pytest.mark.asyncio
    async def test_exists_true(self, store, sleeve_proposal):
        """存在的提案 exists() 返回 True"""
        await store.initialize()
        
        proposal_id = await store.save(sleeve_proposal)
        assert await store.exists(proposal_id) is True
    
    @pytest.mark.asyncio
    async def test_exists_false(self, store):
        """不存在的提案 exists() 返回 False"""
        await store.initialize()
        
        assert await store.exists("non-existent") is False


class TestInMemoryStoreEdgeCases:
    """边界情况测试"""
    
    @pytest.mark.asyncio
    async def test_multiple_saves_same_id_different_content(self, store):
        """同一 ID 多次保存不同内容应该全部被保存（独立对象）"""
        await store.initialize()
        
        p1 = ProposalModel.create_sleeve(
            specialist_type="trend",
            hypothesis="First",
            required_features=["ema"],
        )
        id1 = await store.save(p1)
        
        p2 = ProposalModel.create_sleeve(
            specialist_type="trend",
            hypothesis="Second",
            required_features=["rsi"],
        )
        id2 = await store.save(p2)
        
        # 两个不同的 proposal
        assert id1 != id2
        
        # 都能正确读取
        retrieved1 = await store.get_by_id(id1)
        retrieved2 = await store.get_by_id(id2)
        
        assert retrieved1.hypothesis == "First"
        assert retrieved2.hypothesis == "Second"
    
    @pytest.mark.asyncio
    async def test_empty_payload(self, store):
        """空 payload 应该能正常保存"""
        await store.initialize()
        
        p = ProposalModel.create_sleeve(
            specialist_type="trend",
            hypothesis="",
            required_features=[],
        )
        proposal_id = await store.save(p)
        
        retrieved = await store.get_by_id(proposal_id)
        assert retrieved is not None
        assert retrieved.hypothesis == ""
        assert retrieved.required_features == []