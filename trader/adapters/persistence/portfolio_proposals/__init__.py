"""
Portfolio Proposals Persistence Package
=======================================

此包提供组合提案的持久化存储，支持多种后端实现。

目录结构：
- models.py          : 统一领域模型
- store_protocol.py  : 业务存储接口（Protocol）
- memory_store.py    : 内存实现
- postgres_store.py  : PostgreSQL 实现
- tests/             : 测试套件

使用方式：
```python
from trader.adapters.persistence.portfolio_proposals import (
    PortfolioProposalStore,
    InMemoryPortfolioProposalStore,
    PostgresPortfolioProposalStore,
)

# 开发/测试用内存存储
store: PortfolioProposalStore = InMemoryPortfolioProposalStore()

# 生产环境 PostgreSQL 存储
store: PortfolioProposalStore = PostgresPortfolioProposalStore(
    postgres_storage=pg_storage
)
```
"""

from trader.adapters.persistence.portfolio_proposals.models import (
    ProposalModel,
    ProposalStatus,
)
from trader.adapters.persistence.portfolio_proposals.store_protocol import (
    PortfolioProposalStore,
)
from trader.adapters.persistence.portfolio_proposals.memory_store import (
    InMemoryPortfolioProposalStore,
)
from trader.adapters.persistence.portfolio_proposals.postgres_store import (
    PostgresPortfolioProposalStore,
)

__all__ = [
    # Models
    "ProposalModel",
    "ProposalStatus",
    # Protocol
    "PortfolioProposalStore",
    # Implementations
    "InMemoryPortfolioProposalStore",
    "PostgresPortfolioProposalStore",
]