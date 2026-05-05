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
- Degraded Cascade Controller: 级联保护控制器
"""

from trader.adapters.binance.backoff import BackoffConfig, BackoffController, BackoffControllerAsync
from trader.adapters.binance.connector import (
    AdapterHealth,
    AdapterHealthReport,
    BinanceConnector,
    BinanceConnectorConfig,
)
from trader.adapters.binance.crypto_risk_source import (
    BINANCE_USD_M_FUTURES_BASE_URL,
    BinanceFuturesRiskDataSource,
    BinanceFuturesRiskDataSourceConfig,
    BinanceFuturesRiskDataSourceError,
)
from trader.adapters.binance.degraded_cascade import (
    CascadeConfig,
    CascadeMetrics,
    CascadeState,
    DegradedCascadeController,
)
from trader.adapters.binance.environmental_risk import (
    EnvironmentalRiskEvent,
    LocalEventLog,
    RecommendedLevel,
    RiskScope,
    RiskSeverity,
)
from trader.adapters.binance.private_stream import (
    BinanceCredentials,
    PrivateStreamConfig,
    PrivateStreamManager,
    RawFillUpdate,
    RawOrderUpdate,
)
from trader.adapters.binance.proxy_failover import (
    ProxyFailoverConfig,
    ProxyFailoverController,
    get_proxy_failover_controller,
)
from trader.adapters.binance.public_stream import (
    MarketEvent,
    PublicStreamConfig,
    PublicStreamManager,
)
from trader.adapters.binance.rate_limit import Priority, RateBudgetConfig, RestRateBudget
from trader.adapters.binance.rest_alignment import (
    AlignmentConfig,
    AlignmentMetrics,
    RESTAlignmentCoordinator,
    RestAlignmentSnapshot,
)
from trader.adapters.binance.stream_base import (
    StreamConfig,
    StreamEvent,
    StreamMetrics,
    StreamState,
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
    # Environmental Risk
    "EnvironmentalRiskEvent",
    "LocalEventLog",
    "RiskSeverity",
    "RiskScope",
    "RecommendedLevel",
    # Degraded Cascade
    "DegradedCascadeController",
    "CascadeConfig",
    "CascadeMetrics",
    "CascadeState",
    # Proxy Failover
    "ProxyFailoverConfig",
    "ProxyFailoverController",
    "get_proxy_failover_controller",
    # Crypto Risk Source
    "BINANCE_USD_M_FUTURES_BASE_URL",
    "BinanceFuturesRiskDataSource",
    "BinanceFuturesRiskDataSourceConfig",
    "BinanceFuturesRiskDataSourceError",
]
