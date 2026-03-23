"""
On-Chain Market Data Adapter
============================
从公开区块链数据源采集链上和宏观市场数据，写入 Feature Store。

主要组件：
- OnChainMarketDataAdapter：主适配器类
- OnChainMarketDataConfig：配置数据类
- LiquidationRecord：爆仓记录
- ExchangeFlowRecord：交易所流量记录
- StablecoinSupplyRecord：稳定币供应记录

使用示例：
    from trader.adapters.onchain import OnChainMarketDataAdapter, get_onchain_adapter

    adapter = OnChainMarketDataAdapter()
    await adapter.start(symbols=["BTCUSDT", "ETHUSDT"])
"""

from trader.adapters.onchain.onchain_market_data_stream import (
    OnChainMarketDataAdapter,
    OnChainMarketDataConfig,
    LiquidationRecord,
    ExchangeFlowRecord,
    StablecoinSupplyRecord,
    get_onchain_adapter,
    get_onchain_adapter_async,
    start_onchain_service,
    stop_onchain_service,
    reset_onchain_adapter,
    BINANCE_FUTURES_BASE_URL,
    GLASSNODE_BASE_URL,
)

__all__ = [
    "OnChainMarketDataAdapter",
    "OnChainMarketDataConfig",
    "LiquidationRecord",
    "ExchangeFlowRecord",
    "StablecoinSupplyRecord",
    "get_onchain_adapter",
    "get_onchain_adapter_async",
    "start_onchain_service",
    "stop_onchain_service",
    "reset_onchain_adapter",
    "BINANCE_FUTURES_BASE_URL",
    "GLASSNODE_BASE_URL",
]
