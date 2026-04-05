"""
PostgreSQL Store Tests - PostgreSQL 存储集成测试
================================================

此模块测试 PostgresPortfolioProposalStore。

前置条件：
- PostgreSQL 数据库运行中
- 环境变量 POSTGRES_CONNECTION_STRING 已设置
- 或 POSTGRES_HOST, POSTGRES_DB, POSTGRES_USER, POSTGRES_PASSWORD 已设置

运行方式：
```bash
# 启动 PostgreSQL (Docker)
docker compose up -d

# 运行测试
POSTGRES_CONNECTION_STRING=postgresql://trader:trader_pwd@127.0.0.1:5432/trading \
    python -m pytest trader/adapters/persistence/portfolio_proposals/tests/test_postgres_store.py -v
```
"""

import os
import pytest

from trader.adapters.persistence.portfolio_proposals.models import (
    CostAssumptions,
    ProposalModel,
    ProposalStatus,
    ProposalType,
    SleeveData,
)
from trader.adapters.persistence.portfolio_proposals.postgres_store import (
    PostgresPortfolioProposalStore,
)


# 检查是否配置了 PostgreSQL
_postgres_configured = bool(
    os.getenv("POSTGRES_CONNECTION_STRING")
    or (os.getenv("POSTGRES_HOST") and os.getenv("POSTGRES_DB"))
)


def pytest_collection_modifyitems(items):
    """如果有 PostgreSQL 配置，跳过所有测试；否则跳过 Postgres 测试"""
    if not _postgres_configured:
        skip_postgres = pytest.mark.skip(
            reason="PostgreSQL not configured"
        )
        for item in items:
            if "postgres" in item.nodeid.lower():
                item.add_marker(skip_postgres)


@pytest.fixture
def postgres_storage():
    """创建 PostgreSQLStorage 实例（如果可用）"""
    if not _postgres_configured:
        pytest.skip("PostgreSQL not configured")
    
    from trader.adapters.persistence.postgres import PostgreSQLStorage
    return PostgreSQLStorage()


@pytest.fixture
def store(postgres_storage) -> PostgresPortfolioProposalStore:
    """创建并初始化 store"""
    s = PostgresPortfolioProposalStore(
        postgres_storage=postgres_storage,
        auto_initialize=False,
    )
    return s


@pytest.fixture
async def initialized_store(store) -> PostgresPortfolioProposalStore:
    """初始化并返回 store"""
    await store.initialize()
    yield store
    # cleanup
    await _cleanup_store(store)


async def _cleanup_store(store: PostgresPortfolioProposalStore) -> None:
    """清理 store 中的所有数据"""
    try:
        all_sleeves = await store.list_by_type(ProposalType.SLEEVE, limit=1000, offset=0)
        for p in all_sleeves:
            await store.delete(p.proposal_id)
        
        all_portfolios = await store.list_by_type(ProposalType.PORTFOLIO, limit=1000, offset=0)
        for p in all_portfolios:
            await store.delete(p.proposal_id)
    except Exception:
        pass


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


class TestPostgresStoreBasic:
    """基本功能测试"""
    
    @pytest.mark.asyncio
    async def test_initialize_creates_table(self, postgres_storage):
        """initialize() 应该创建表"""
        store = PostgresPortfolioProposalStore(
            postgres_storage=postgres_storage,
            auto_initialize=False,
        )
        await store.initialize()
        
        assert store.is_initialized is True
        
        # 验证表存在
        async with postgres_storage.acquire() as conn:
            result = await conn.fetchval(
                "SELECT EXISTS (SELECT FROM information_schema.tables WHERE table_name = 'portfolio_proposals')"
            )
            assert result is True
    
    @pytest.mark.asyncio
    async def test_initialize_creates_indexes(self, postgres_storage):
        """initialize() 应该创建索引"""
        store = PostgresPortfolioProposalStore(
            postgres_storage=postgres_storage,
            auto_initialize=False,
        )
        await store.initialize()
        
        # 验证索引存在（至少有一个）
        async with postgres_storage.acquire() as conn:
            result = await conn.fetchval(
                """
                SELECT COUNT(*) > 0 FROM pg_indexes 
                WHERE tablename = 'portfolio_proposals'
                """
            )
            assert result is True
    
    @pytest.mark.asyncio
    async def test_health_check(self, initialized_store):
        """健康检查应该返回 True"""
        assert await initialized_store.health_check() is True


class TestPostgresStoreCRUD:
    """CRUD 测试"""
    
    @pytest.mark.asyncio
    async def test_save_and_get_sleeve(self, initialized_store, sleeve_proposal):
        """保存并读取 Sleeve 提案"""
        proposal_id = await initialized_store.save(sleeve_proposal)
        retrieved = await initialized_store.get_by_id(proposal_id)
        
        assert retrieved is not None
        assert retrieved.proposal_id == proposal_id
        assert retrieved.proposal_type == ProposalType.SLEEVE
        assert retrieved.specialist_type == "trend"
        assert retrieved.hypothesis == "Price will rise due to momentum"
        assert retrieved.required_features == ["ema_fast", "ema_slow"]
    
    @pytest.mark.asyncio
    async def test_save_and_get_portfolio(self, initialized_store, portfolio_proposal):
        """保存并读取 Portfolio 提案"""
        proposal_id = await initialized_store.save(portfolio_proposal)
        retrieved = await initialized_store.get_by_id(proposal_id)
        
        assert retrieved is not None
        assert retrieved.proposal_type == ProposalType.PORTFOLIO
        assert retrieved.risk_explanation == "Medium risk"
        assert len(retrieved.portfolio_sleeves) == 1
    
    @pytest.mark.asyncio
    async def test_get_nonexistent_returns_none(self, initialized_store):
        """查询不存在的 proposal 返回 None"""
        result = await initialized_store.get_by_id("non-existent-id")
        assert result is None
    
    @pytest.mark.asyncio
    async def test_upsert_updates_existing(self, initialized_store, sleeve_proposal):
        """重复保存同一 proposal 应该更新"""
        proposal_id = await initialized_store.save(sleeve_proposal)
        
        # 修改数据
        sleeve_proposal.payload["hypothesis"] = "Updated hypothesis"
        await initialized_store.save(sleeve_proposal)
        
        # 读取
        retrieved = await initialized_store.get_by_id(proposal_id)
        assert retrieved.hypothesis == "Updated hypothesis"
        
        # 应该只有一条记录
        count = await initialized_store.count()
        assert count == 1
    
    @pytest.mark.asyncio
    async def test_delete_removes_proposal(self, initialized_store, sleeve_proposal):
        """删除后提案不再存在"""
        proposal_id = await initialized_store.save(sleeve_proposal)
        await initialized_store.delete(proposal_id)
        
        result = await initialized_store.get_by_id(proposal_id)
        assert result is None
    
    @pytest.mark.asyncio
    async def test_delete_nonexistent_succeeds(self, initialized_store):
        """删除不存在的 proposal 应该静默成功"""
        # 不应该抛出异常
        await initialized_store.delete("non-existent-id")


class TestPostgresStoreQueries:
    """查询测试"""
    
    @pytest.mark.asyncio
    async def test_list_by_status(self, initialized_store, sleeve_proposal):
        """按状态列出提案"""
        p1 = sleeve_proposal
        p1.status = ProposalStatus.PENDING
        await initialized_store.save(p1)
        
        p2 = ProposalModel.create_sleeve(
            specialist_type="trend",
            hypothesis="Another",
            required_features=["rsi"],
        )
        p2.status = ProposalStatus.APPROVED
        await initialized_store.save(p2)
        
        pending = await initialized_store.list_by_status(ProposalStatus.PENDING)
        assert len(pending) >= 1
        assert all(p.status == ProposalStatus.PENDING for p in pending)
    
    @pytest.mark.asyncio
    async def test_list_by_type(self, initialized_store, sleeve_proposal, portfolio_proposal):
        """按类型列出提案"""
        await initialized_store.save(sleeve_proposal)
        await initialized_store.save(portfolio_proposal)
        
        sleeves = await initialized_store.list_by_type(ProposalType.SLEEVE)
        portfolios = await initialized_store.list_by_type(ProposalType.PORTFOLIO)
        
        assert all(p.proposal_type == ProposalType.SLEEVE for p in sleeves)
        assert all(p.proposal_type == ProposalType.PORTFOLIO for p in portfolios)
    
    @pytest.mark.asyncio
    async def test_list_by_specialist(self, initialized_store):
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
        await initialized_store.save(p1)
        await initialized_store.save(p2)
        
        trend_proposals = await initialized_store.list_by_specialist("trend")
        assert all(p.specialist_type == "trend" for p in trend_proposals)
    
    @pytest.mark.asyncio
    async def test_list_ordering_created_at_desc(self, initialized_store):
        """list_*() 应该按 created_at DESC 排序"""
        import time
        
        p1 = ProposalModel.create_sleeve(
            specialist_type="trend",
            hypothesis="First",
            required_features=["ema"],
        )
        await initialized_store.save(p1)
        
        time.sleep(0.01)
        
        p2 = ProposalModel.create_sleeve(
            specialist_type="trend",
            hypothesis="Second",
            required_features=["ema"],
        )
        await initialized_store.save(p2)
        
        results = await initialized_store.list_by_type(ProposalType.SLEEVE)
        
        assert len(results) >= 2
        first_two = results[:2]
        for i in range(len(first_two) - 1):
            assert first_two[i].created_at >= first_two[i + 1].created_at


class TestPostgresStorePagination:
    """分页测试"""
    
    @pytest.mark.asyncio
    async def test_pagination(self, initialized_store):
        """list_*() 应该正确支持分页"""
        for i in range(5):
            p = ProposalModel.create_sleeve(
                specialist_type="trend",
                hypothesis=f"Hypothesis {i}",
                required_features=["ema"],
            )
            p.status = ProposalStatus.PENDING
            await initialized_store.save(p)
        
        page1 = await initialized_store.list_by_status(ProposalStatus.PENDING, limit=2, offset=0)
        page2 = await initialized_store.list_by_status(ProposalStatus.PENDING, limit=2, offset=2)
        
        assert len(page1) == 2
        assert len(page2) == 2
        
        page1_ids = {p.proposal_id for p in page1}
        page2_ids = {p.proposal_id for p in page2}
        assert page1_ids.isdisjoint(page2_ids)


class TestPostgresStoreJSONMapping:
    """JSON 字段映射测试"""
    
    @pytest.mark.asyncio
    async def test_payload_with_nested_objects(self, initialized_store):
        """测试嵌套对象的 payload"""
        p = ProposalModel.create_portfolio(
            sleeves=[
                SleeveData(
                    sleeve_id="s1",
                    capital_cap=1000.0,
                    weight=0.5,
                    max_position_size=100.0,
                ),
                SleeveData(
                    sleeve_id="s2",
                    capital_cap=2000.0,
                    weight=0.5,
                    max_position_size=200.0,
                ),
            ],
            capital_caps={"s1": 1000.0, "s2": 2000.0},
            risk_explanation="Diversified portfolio",
        )
        
        await initialized_store.save(p)
        retrieved = await initialized_store.get_by_id(p.proposal_id)
        
        assert retrieved is not None
        assert len(retrieved.portfolio_sleeves) == 2
        assert retrieved.portfolio_capital_caps["s1"] == 1000.0
        assert retrieved.portfolio_capital_caps["s2"] == 2000.0
    
    @pytest.mark.asyncio
    async def test_cost_assumptions_decimal_fields(self, initialized_store):
        """测试成本假设中的 Decimal 字段"""
        p = ProposalModel.create_sleeve(
            specialist_type="trend",
            hypothesis="Test",
            required_features=["ema"],
            cost_assumptions=CostAssumptions(
                trading_fee_bps=15.5,
                slippage_bps=7.25,
            ),
        )
        
        await initialized_store.save(p)
        retrieved = await initialized_store.get_by_id(p.proposal_id)
        
        assert retrieved.cost_assumptions.trading_fee_bps == 15.5
        assert retrieved.cost_assumptions.slippage_bps == 7.25


class TestPostgresStoreContentHash:
    """内容哈希测试"""
    
    @pytest.mark.asyncio
    async def test_content_hash_consistency(self, initialized_store):
        """内容哈希应该一致"""
        p = ProposalModel.create_sleeve(
            specialist_type="trend",
            hypothesis="Test hypothesis",
            required_features=["ema"],
        )
        
        await initialized_store.save(p)
        retrieved = await initialized_store.get_by_id(p.proposal_id)
        
        # content_hash 应该在序列化/反序列化后保持一致
        assert retrieved.content_hash == p.content_hash
    
    @pytest.mark.asyncio
    async def test_different_content_different_hash(self, initialized_store):
        """不同内容应该产生不同的哈希"""
        p1 = ProposalModel.create_sleeve(
            specialist_type="trend",
            hypothesis="First",
            required_features=["ema"],
        )
        p2 = ProposalModel.create_sleeve(
            specialist_type="trend",
            hypothesis="Second",
            required_features=["ema"],
        )
        
        await initialized_store.save(p1)
        await initialized_store.save(p2)
        
        assert p1.content_hash != p2.content_hash