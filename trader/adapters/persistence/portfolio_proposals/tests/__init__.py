"""
Contract Tests - 共享契约测试
=============================

此模块定义所有 PortfolioProposalStore 实现都必须通过的测试。

使用方式：
```python
import pytest
from trader.adapters.persistence.portfolio_proposals.tests.contract_tests import (
    run_contract_tests,
)

# 在你的测试类中调用
class TestMyStore:
    @pytest.fixture
    def store(self):
        # 创建你的 store 实例
        ...
    
    def test_contract_save_and_get(self, store):
        run_contract_tests.test_save_and_get(store)
```

这样可以确保 InMemory 和 Postgres 两个实现的行为完全一致。
"""

import pytest
from typing import Callable, List, TypeVar, Generic

from trader.adapters.persistence.portfolio_proposals.models import (
    CostAssumptions,
    ProposalModel,
    ProposalStatus,
    ProposalType,
    SleeveData,
)
from trader.adapters.persistence.portfolio_proposals.store_protocol import (
    PortfolioProposalStore,
)

T = TypeVar("T", bound=PortfolioProposalStore)


def run_contract_tests(store_factory: Callable[[], PortfolioProposalStore]) -> None:
    """
    运行所有契约测试
    
    Args:
        store_factory: 返回 store 实例的工厂函数
    """
    
    @pytest.fixture
    def store() -> PortfolioProposalStore:
        """创建并初始化 store"""
        s = store_factory()
        import asyncio
        asyncio.get_event_loop().run_until_complete(s.initialize())
        yield s
        # cleanup
        import asyncio
        asyncio.get_event_loop().run_until_complete(s._clear_all() if hasattr(s, '_clear_all') else _cleanup(s))
    
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
    
    # ========================================================================
    # 测试：save() 后可 get_by_id() 读回
    # ========================================================================
    
    @pytest.mark.asyncio
    async def test_save_and_get_sleeve(store, sleeve_proposal):
        """保存 Sleeve 提案后可以正确读取"""
        proposal_id = await store.save(sleeve_proposal)
        
        retrieved = await store.get_by_id(proposal_id)
        
        assert retrieved is not None
        assert retrieved.proposal_id == proposal_id
        assert retrieved.proposal_type == ProposalType.SLEEVE
        assert retrieved.specialist_type == "trend"
        assert retrieved.hypothesis == "Price will rise due to momentum"
        assert retrieved.required_features == ["ema_fast", "ema_slow"]
    
    @pytest.mark.asyncio
    async def test_save_and_get_portfolio(store, portfolio_proposal):
        """保存 Portfolio 提案后可以正确读取"""
        proposal_id = await store.save(portfolio_proposal)
        
        retrieved = await store.get_by_id(proposal_id)
        
        assert retrieved is not None
        assert retrieved.proposal_id == proposal_id
        assert retrieved.proposal_type == ProposalType.PORTFOLIO
        assert retrieved.risk_explanation == "Medium risk"
    
    # ========================================================================
    # 测试：查不存在对象返回 None
    # ========================================================================
    
    @pytest.mark.asyncio
    async def test_get_nonexistent_returns_none(store):
        """查询不存在的 proposal 返回 None"""
        result = await store.get_by_id("non-existent-id")
        assert result is None
    
    # ========================================================================
    # 测试：同一个 proposal_id 的重复 save() 表现为 upsert
    # ========================================================================
    
    @pytest.mark.asyncio
    async def test_upsert_updates_existing(store, sleeve_proposal):
        """重复保存同一 proposal 应该更新，而不是创建新记录"""
        proposal_id = await store.save(sleeve_proposal)
        
        # 修改数据
        sleeve_proposal.payload["hypothesis"] = "Updated hypothesis"
        await store.save(sleeve_proposal)
        
        # 读取应该返回更新后的数据
        retrieved = await store.get_by_id(proposal_id)
        assert retrieved is not None
        assert retrieved.hypothesis == "Updated hypothesis"
        
        # 计数应该只有 1
        count = await store.count()
        assert count == 1
    
    # ========================================================================
    # 测试：list_by_status() 正确返回同状态的 proposals
    # ========================================================================
    
    @pytest.mark.asyncio
    async def test_list_by_status(store, sleeve_proposal):
        """按状态列出提案"""
        # 创建多个不同状态的提案
        p1 = sleeve_proposal
        p1.status = ProposalStatus.PENDING
        await store.save(p1)
        
        p2 = ProposalModel.create_sleeve(
            specialist_type="trend",
            hypothesis="Another hypothesis",
            required_features=["rsi"],
        )
        p2.status = ProposalStatus.APPROVED
        await store.save(p2)
        
        # 按 PENDING 状态查询
        pending = await store.list_by_status(ProposalStatus.PENDING)
        assert len(pending) >= 1
        assert all(p.status == ProposalStatus.PENDING for p in pending)
    
    # ========================================================================
    # 测试：list_by_type() 正确返回同类型的 proposals
    # ========================================================================
    
    @pytest.mark.asyncio
    async def test_list_by_type(store, sleeve_proposal, portfolio_proposal):
        """按类型列出提案"""
        await store.save(sleeve_proposal)
        await store.save(portfolio_proposal)
        
        sleeves = await store.list_by_type(ProposalType.SLEEVE)
        portfolios = await store.list_by_type(ProposalType.PORTFOLIO)
        
        assert all(p.proposal_type == ProposalType.SLEEVE for p in sleeves)
        assert all(p.proposal_type == ProposalType.PORTFOLIO for p in portfolios)
    
    # ========================================================================
    # 测试：list_by_specialist() 正确返回同 specialist 的 proposals
    # ========================================================================
    
    @pytest.mark.asyncio
    async def test_list_by_specialist(store):
        """按 Specialist 类型列出提案"""
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
    
    # ========================================================================
    # 测试：返回顺序符合约定（created_at DESC）
    # ========================================================================
    
    @pytest.mark.asyncio
    async def test_list_ordering(store):
        """list_*() 返回应该按 created_at DESC 排序"""
        import time
        
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
        # 检查创建时间递减
        for i in range(len(first_two) - 1):
            assert first_two[i].created_at >= first_two[i + 1].created_at
    
    # ========================================================================
    # 测试：delete() 后对象不存在
    # ========================================================================
    
    @pytest.mark.asyncio
    async def test_delete_removes_proposal(store, sleeve_proposal):
        """删除后提案不再存在"""
        proposal_id = await store.save(sleeve_proposal)
        
        await store.delete(proposal_id)
        
        result = await store.get_by_id(proposal_id)
        assert result is None
    
    # ========================================================================
    # 测试：删除不存在对象不会报错
    # ========================================================================
    
    @pytest.mark.asyncio
    async def test_delete_nonexistent_succeeds(store):
        """删除不存在的 proposal 应该静默成功"""
        # 不应该抛出异常
        await store.delete("non-existent-id")
    
    # ========================================================================
    # 测试：分页功能
    # ========================================================================
    
    @pytest.mark.asyncio
    async def test_pagination(store):
        """list_*() 应该正确支持分页"""
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
    
    # ========================================================================
    # 测试：exists() 方法
    # ========================================================================
    
    @pytest.mark.asyncio
    async def test_exists(store, sleeve_proposal):
        """exists() 应该正确判断提案是否存在"""
        proposal_id = await store.save(sleeve_proposal)
        
        assert await store.exists(proposal_id) is True
        assert await store.exists("non-existent") is False
    
    # ========================================================================
    # 返回测试函数列表（供外部运行）
    # ========================================================================
    
    return [
        test_save_and_get_sleeve,
        test_save_and_get_portfolio,
        test_get_nonexistent_returns_none,
        test_upsert_updates_existing,
        test_list_by_status,
        test_list_by_type,
        test_list_by_specialist,
        test_list_ordering,
        test_delete_removes_proposal,
        test_delete_nonexistent_succeeds,
        test_pagination,
        test_exists,
    ]


# 异步辅助函数
async def _cleanup(store: PortfolioProposalStore) -> None:
    """清理 store 中的所有数据"""
    if hasattr(store, '_clear_all'):
        store._clear_all()
    else:
        # 对于 PostgresStore，获取所有 ID 然后删除
        import asyncio
        try:
            all_proposals = await store.list_by_type(ProposalType.SLEEVE, limit=1000, offset=0)
            for p in all_proposals:
                await store.delete(p.proposal_id)
            
            all_portfolios = await store.list_by_type(ProposalType.PORTFOLIO, limit=1000, offset=0)
            for p in all_portfolios:
                await store.delete(p.proposal_id)
        except Exception:
            pass


# 直接运行的测试（使用 pytest 参数化）
class TestContractSuite:
    """
    契约测试套件
    
    使用方式：
    ```python
    # conftest.py
    import pytest
    from trader.adapters.persistence.portfolio_proposals.memory_store import InMemoryPortfolioProposalStore
    from trader.adapters.persistence.portfolio_proposals.tests.contract_tests import TestContractSuite
    
    @pytest.fixture
    def store():
        return InMemoryPortfolioProposalStore()
    
    # 将 store 注入测试类
    TestContractSuite.__init_subclass__(store=InMemoryPortfolioProposalStore)
    ```
    """
    
    @pytest.fixture
    def store(self) -> PortfolioProposalStore:
        raise NotImplementedError("Subclass must provide store fixture")
    
    @pytest.fixture
    def sleeve_proposal(self) -> ProposalModel:
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
    def portfolio_proposal(self) -> ProposalModel:
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
    
    @pytest.mark.asyncio
    async def test_save_and_get_sleeve(self, store, sleeve_proposal):
        proposal_id = await store.save(sleeve_proposal)
        retrieved = await store.get_by_id(proposal_id)
        
        assert retrieved is not None
        assert retrieved.proposal_id == proposal_id
        assert retrieved.proposal_type == ProposalType.SLEEVE
        assert retrieved.specialist_type == "trend"
    
    @pytest.mark.asyncio
    async def test_save_and_get_portfolio(self, store, portfolio_proposal):
        proposal_id = await store.save(portfolio_proposal)
        retrieved = await store.get_by_id(proposal_id)
        
        assert retrieved is not None
        assert retrieved.proposal_type == ProposalType.PORTFOLIO
    
    @pytest.mark.asyncio
    async def test_get_nonexistent_returns_none(self, store):
        result = await store.get_by_id("non-existent-id")
        assert result is None
    
    @pytest.mark.asyncio
    async def test_upsert_updates_existing(self, store, sleeve_proposal):
        proposal_id = await store.save(sleeve_proposal)
        
        sleeve_proposal.payload["hypothesis"] = "Updated"
        await store.save(sleeve_proposal)
        
        retrieved = await store.get_by_id(proposal_id)
        assert retrieved.hypothesis == "Updated"
        assert await store.count() == 1
    
    @pytest.mark.asyncio
    async def test_delete_nonexistent_succeeds(self, store):
        await store.delete("non-existent-id")  # 不应抛出异常