"""
Binance Adapter
================
Binance WebSocket 和 REST API 适配器。

主要组件：
- Rate Budget: Token Bucket 限流器
- Backoff Controller: 指数退避控制器
- Public Stream Manager: 公有流状态机
- Private Stream Manager: 私有流状态机
- REST Alignment Coordinator: REST 对齐协调器
- Binance Connector: 统一连接协调器
"""
from trader.adapters.binance.rate_limit import (
    RestRateBudget,
    Priority,
    RateBudgetConfig,
)
from trader.adapters.binance.backoff import (
    BackoffController,
    BackoffConfig,
    BackoffControllerAsync,
)
from trader.adapters.binance.stream_base import (
    StreamState,
    StreamEvent,
    StreamConfig,
    StreamMetrics,
)
from trader.adapters.binance.public_stream import (
    PublicStreamManager,
    PublicStreamConfig,
    MarketEvent,
)
from trader.adapters.binance.private_stream import (
    PrivateStreamManager,
    PrivateStreamConfig,
    BinanceCredentials,
    RawOrderUpdate,
    RawFillUpdate,
)
from trader.adapters.binance.rest_alignment import (
    RESTAlignmentCoordinator,
    AlignmentConfig,
    AlignmentMetrics,
    RestAlignmentSnapshot,
)
from trader.adapters.binance.connector import (
    BinanceConnector,
    BinanceConnectorConfig,
    AdapterHealth,
    AdapterHealthReport,
)


__all__ = [
    # Rate Limit
    "RestRateBudget",
    "Priority",
    "RateBudgetConfig",
    # Backoff
    "BackoffController",
    "BackoffConfig",
    "BackoffControllerAsync",
    # Stream Base
    "StreamState",
    "StreamEvent",
    "StreamConfig",
    "StreamMetrics",
    # Public Stream
    "PublicStreamManager",
    "PublicStreamConfig",
    "MarketEvent",
    # Private Stream
    "PrivateStreamManager",
    "PrivateStreamConfig",
    "BinanceCredentials",
    "RawOrderUpdate",
    "RawFillUpdate",
    # REST Alignment
    "RESTAlignmentCoordinator",
    "AlignmentConfig",
    "AlignmentMetrics",
    "RestAlignmentSnapshot",
    # Connector
    "BinanceConnector",
    "BinanceConnectorConfig",
    "AdapterHealth",
    "AdapterHealthReport",
]
