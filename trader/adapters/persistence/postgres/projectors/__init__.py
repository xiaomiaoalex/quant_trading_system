"""
PostgreSQL Projectors - 投影层实现
===================================
将事件流投影为 PostgreSQL 读模型，支持：
- PositionProjector（持仓投影）
- OrderProjector（订单投影）
- RiskProjector（风控投影）

投影层架构：
1. Projectable 接口：定义投影基类
2. 幂等更新语义：使用 version 进行乐观锁
3. 同步/异步更新：支持实时和事件驱动更新
4. 投影重建：从 event_log 重放事件重建投影

依赖：
- PostgreSQL 数据库
- asyncpg 包
- event_store 作为数据源
"""
from trader.adapters.persistence.postgres.projectors.base import (
    Projectable,
    ProjectorSnapshot,
    ProjectionVersion,
)
from trader.adapters.persistence.postgres.projectors.position_projector import (
    PositionProjector,
    PositionProjection,
)
from trader.adapters.persistence.postgres.projectors.order_projector import (
    OrderProjector,
    OrderProjection,
)
from trader.adapters.persistence.postgres.projectors.risk_projector import (
    RiskProjector,
    RiskStateProjection,
)

__all__ = [
    # Base
    "Projectable",
    "ProjectorSnapshot",
    "ProjectionVersion",
    # Position
    "PositionProjector",
    "PositionProjection",
    # Order
    "OrderProjector",
    "OrderProjection",
    # Risk
    "RiskProjector",
    "RiskStateProjection",
]
