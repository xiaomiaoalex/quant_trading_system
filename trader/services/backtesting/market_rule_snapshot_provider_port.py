"""
market_rule_snapshot_provider_port.py - P9.5 市场规则快照提供者端口
====================================================================
Service 层市场规则快照提供者接口，用于回测和模拟环境。

核心协议：
- MarketRuleSnapshotProviderPort: 市场规则快照提供者接口
- FakeMarketRuleSnapshotProvider: 用于测试的假实现
- ChinaStockSnapshotProvider: A 股快照提供者

设计原则：
- 复用 core 层已有枚举 (OrderSide, AssetClass)，不新建同名枚举
- MarketRuleSnapshot 只保留市场无关通用字段
- A 股专属字段放入 metadata 或 ChinaStockMetadata

不接入真实行情、券商或交易所 API。

参考: docs/INTERFACE_CONTRACTS.md P9.5 MarketRuleSnapshotProviderPort 契约
"""

from __future__ import annotations

from abc import abstractmethod
from dataclasses import dataclass, field
from datetime import datetime, timezone
from decimal import Decimal
from enum import Enum
from typing import Any, Protocol, runtime_checkable

from trader.core.domain.models.market_risk import AssetClass
from trader.core.domain.models.market_rules import OrderSide, OrderType


class Venue(str, Enum):
    BINANCE = "BINANCE"
    OKX = "OKX"
    SHANGHAI = "SHANGHAI"
    SHENZHEN = "SHENZHEN"


@dataclass(frozen=True, slots=True)
class ChinaStockMetadata:
    """A 股市场专属元数据"""

    sellable_qty: Decimal = Decimal("0")
    limit_up_rate: float = 0.10
    limit_down_rate: float = 0.10
    is_suspended: bool = False
    trading_phase: str = "CONTINUOUS"
    lot_size: int = 100
    allow_short: bool = False


@dataclass(frozen=True, slots=True)
class MarketRuleSnapshot:
    """
    市场规则快照 - 市场无关抽象

    只保留通用字段，A 股专属字段通过 metadata 承载。
    """

    symbol: str
    asset_class: AssetClass
    venue: str
    timestamp: datetime

    tick_size: Decimal | None = None
    min_notional: Decimal | None = None
    max_qty: Decimal | None = None

    metadata: dict[str, Any] = field(default_factory=dict)


class MarketRuleSnapshotProviderPort(Protocol):
    """
    市场规则快照提供者端口

    定义获取市场规则快照的接口。

    实现要求：
    1. get_snapshot: 获取指定时间的市场规则快照

    示例：
        class ChinaStockSnapshotProvider:
            async def get_snapshot(
                self, symbol: str, dt: datetime
            ) -> MarketRuleSnapshot:
                ...
    """

    async def get_snapshot(self, symbol: str, dt: datetime | None = None) -> MarketRuleSnapshot:
        """获取市场规则快照"""
        ...


class FakeMarketRuleSnapshotProvider(MarketRuleSnapshotProviderPort):
    """Fake 市场规则快照提供者（用于 Crypto 测试）"""

    def __init__(
        self,
        snapshots: dict[str, MarketRuleSnapshot] | None = None,
    ) -> None:
        self._snapshots = snapshots or {}

    async def get_snapshot(self, symbol: str, dt: datetime | None = None) -> MarketRuleSnapshot:
        if symbol in self._snapshots:
            return self._snapshots[symbol]

        return MarketRuleSnapshot(
            symbol=symbol,
            asset_class=AssetClass.CRYPTO,
            venue="binance",
            timestamp=dt or datetime.now(timezone.utc),
            tick_size=Decimal("0.01"),
            min_notional=Decimal("10"),
            max_qty=Decimal("1000000"),
        )

    def set_snapshot(self, symbol: str, snapshot: MarketRuleSnapshot) -> None:
        self._snapshots[symbol] = snapshot


class ChinaStockSnapshotProvider(MarketRuleSnapshotProviderPort):
    """A 股市场规则快照提供者（配置化，无真实数据源）"""

    def __init__(
        self,
        suspended_symbols: list[str] | None = None,
        limit_up_rates: dict[str, float] | None = None,
        limit_down_rates: dict[str, float] | None = None,
    ) -> None:
        self._suspended_symbols = suspended_symbols or []
        self._limit_up_rates = limit_up_rates or {}
        self._limit_down_rates = limit_down_rates or {}

    async def get_snapshot(self, symbol: str, dt: datetime | None = None) -> MarketRuleSnapshot:
        is_suspended = symbol in self._suspended_symbols

        limit_up_rate = self._limit_up_rates.get(symbol, 0.10)
        limit_down_rate = self._limit_down_rates.get(symbol, 0.10)

        china_metadata = ChinaStockMetadata(
            sellable_qty=Decimal("0"),
            limit_up_rate=limit_up_rate,
            limit_down_rate=limit_down_rate,
            is_suspended=is_suspended,
            trading_phase="CONTINUOUS",
            lot_size=100,
            allow_short=False,
        )

        venue = Venue.SHANGHAI if symbol.startswith("60") else Venue.SHENZHEN

        return MarketRuleSnapshot(
            symbol=symbol,
            asset_class=AssetClass.CN_STOCK,
            venue=venue.value,
            timestamp=dt or datetime.now(timezone.utc),
            metadata={"china_stock": china_metadata},
        )

    def set_suspended(self, symbol: str, suspended: bool) -> None:
        if suspended:
            if symbol not in self._suspended_symbols:
                self._suspended_symbols.append(symbol)
        else:
            if symbol in self._suspended_symbols:
                self._suspended_symbols.remove(symbol)

    def set_limit_rates(
        self,
        symbol: str,
        limit_up_rate: float,
        limit_down_rate: float,
    ) -> None:
        self._limit_up_rates[symbol] = limit_up_rate
        self._limit_down_rates[symbol] = limit_down_rate
