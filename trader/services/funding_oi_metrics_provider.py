"""
Funding/OI Metrics Provider - Service 层数据提供器
==================================================

职责：
- 从 FeatureStore 读取历史 Funding Rate 和 OI 数据
- 从 CryptoRiskDataSource 获取当前 Funding Rate 和 OI
- 调用 Core 纯计算服务计算派生指标
- 提供数据过期检测

禁止：
- 禁止修改 Core 计算逻辑
- 禁止直接修改 snapshot
"""

from __future__ import annotations

import logging
import time
from dataclasses import dataclass
from decimal import Decimal
from typing import TYPE_CHECKING, Dict, List, Optional, Protocol, Set

from trader.core.domain.models.crypto_risk import CryptoFundingOIRiskMetrics, CryptoRiskBudget
from trader.core.domain.services.funding_oi_window_calculator import FundingOIWindowCalculator

if TYPE_CHECKING:
    from trader.adapters.persistence.feature_store import FeaturePoint, FeatureStore

logger = logging.getLogger(__name__)


class FundingOIHistoryPort(Protocol):
    async def read_funding_history(
        self,
        symbol: str,
        start_ts_ms: int,
        end_ts_ms: int,
        version: str = "v1",
    ) -> List[FeaturePoint]: ...

    async def read_oi_history(
        self,
        symbol: str,
        start_ts_ms: int,
        end_ts_ms: int,
        version: str = "v1",
    ) -> List[FeaturePoint]: ...


class CurrentFundingOIPort(Protocol):
    async def get_current_funding_rate(self, symbol: str) -> Optional[Decimal]: ...

    async def get_current_open_interest(self, symbol: str) -> Optional[Decimal]: ...

    async def get_latest_funding_ts_ms(self, symbol: str) -> int: ...

    async def get_latest_oi_ts_ms(self, symbol: str) -> int: ...


@dataclass
class FundingOIMetricsProviderConfig:
    feature_version: str = "v1"
    default_window: int = 20
    default_min_periods: int = 10
    max_data_age_seconds: int = 24 * 3600
    funding_history_days: int = 7
    oi_history_days: int = 7


class FundingOIMetricsProvider:
    def __init__(
        self,
        funding_oi_history: FundingOIHistoryPort,
        current_funding_oi: CurrentFundingOIPort,
        config: Optional[FundingOIMetricsProviderConfig] = None,
    ) -> None:
        self._history = funding_oi_history
        self._current = current_funding_oi
        self._config = config or FundingOIMetricsProviderConfig()

    async def compute_metrics(
        self,
        symbol: str,
        budget: CryptoRiskBudget,
        symbols: Optional[Set[str]] = None,
    ) -> Dict[str, CryptoFundingOIRiskMetrics]:
        """
        计算 Funding/OI 风险指标

        Args:
            symbol: 目标交易对
            budget: 风险预算配置
            symbols: 需要计算的所有交易对（用于获取完整历史窗口）

        Returns:
            Dict[str, CryptoFundingOIRiskMetrics]: 各 symbol 的指标
        """
        if symbols is None:
            symbols = {symbol}

        now_ms = int(time.time() * 1000)
        result: Dict[str, CryptoFundingOIRiskMetrics] = {}

        for sym in symbols:
            metrics = await self._compute_single_symbol(
                symbol=sym,
                budget=budget,
                current_ts_ms=now_ms,
            )
            result[sym] = metrics

        return result

    async def _compute_single_symbol(
        self,
        symbol: str,
        budget: CryptoRiskBudget,
        current_ts_ms: int,
    ) -> CryptoFundingOIRiskMetrics:
        current_funding = await self._current.get_current_funding_rate(symbol)
        current_oi = await self._current.get_current_open_interest(symbol)
        latest_funding_ts = await self._current.get_latest_funding_ts_ms(symbol)
        latest_oi_ts = await self._current.get_latest_oi_ts_ms(symbol)

        current_funding_val = float(current_funding) if current_funding is not None else None
        current_oi_val = float(current_oi) if current_oi is not None else None

        funding_window = budget.funding_history_window
        oi_window = budget.oi_history_window
        funding_min = budget.funding_min_periods
        oi_min = budget.oi_min_periods
        max_age = budget.max_data_age_seconds or self._config.max_data_age_seconds

        history_days = self._config.funding_history_days
        funding_end_ts = latest_funding_ts if latest_funding_ts > 0 else current_ts_ms
        funding_start_ts = funding_end_ts - (history_days * 24 * 3600 * 1000)

        oi_history_days = self._config.oi_history_days
        oi_end_ts = latest_oi_ts if latest_oi_ts > 0 else current_ts_ms
        oi_start_ts = oi_end_ts - (oi_history_days * 24 * 3600 * 1000)

        funding_history = await self._history.read_funding_history(
            symbol=symbol,
            start_ts_ms=funding_start_ts,
            end_ts_ms=funding_end_ts,
            version=self._config.feature_version,
        )

        oi_history = await self._history.read_oi_history(
            symbol=symbol,
            start_ts_ms=oi_start_ts,
            end_ts_ms=oi_end_ts,
            version=self._config.feature_version,
        )

        funding_values = [float(f.value) for f in funding_history if f.value is not None]
        oi_values = [float(f.value) for f in oi_history if f.value is not None]

        return FundingOIWindowCalculator.compute_funding_oi_metrics(
            symbol=symbol,
            current_funding_rate=current_funding_val,
            current_oi=current_oi_val,
            funding_history=funding_values,
            oi_history=oi_values,
            funding_window=funding_window,
            oi_window=oi_window,
            funding_min_periods=funding_min,
            oi_min_periods=oi_min,
            latest_funding_ts_ms=latest_funding_ts,
            latest_oi_ts_ms=latest_oi_ts,
            current_ts_ms=current_ts_ms,
            max_data_age_seconds=max_age,
        )


class FeatureStoreFundingOIMetricsProvider(FundingOIMetricsProvider):
    def __init__(
        self,
        feature_store: "FeatureStore",
        current_funding_oi: CurrentFundingOIPort,
        config: Optional[FundingOIMetricsProviderConfig] = None,
    ) -> None:
        self._feature_store = feature_store
        super().__init__(
            funding_oi_history=_FeatureStoreHistoryAdapter(feature_store),
            current_funding_oi=current_funding_oi,
            config=config,
        )


class _FeatureStoreHistoryAdapter:
    def __init__(self, feature_store: "FeatureStore") -> None:
        self._feature_store = feature_store

    async def read_funding_history(
        self,
        symbol: str,
        start_ts_ms: int,
        end_ts_ms: int,
        version: str = "v1",
    ) -> List["FeaturePoint"]:
        return await self._feature_store.read_feature_range(
            symbol=symbol,
            feature_name="funding_rate",
            start_time=start_ts_ms,
            end_time=end_ts_ms,
            version=version,
        )

    async def read_oi_history(
        self,
        symbol: str,
        start_ts_ms: int,
        end_ts_ms: int,
        version: str = "v1",
    ) -> List["FeaturePoint"]:
        return await self._feature_store.read_feature_range(
            symbol=symbol,
            feature_name="open_interest",
            start_time=start_ts_ms,
            end_time=end_ts_ms,
            version=version,
        )
