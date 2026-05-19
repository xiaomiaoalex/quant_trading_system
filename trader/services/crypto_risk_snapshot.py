from __future__ import annotations

import logging
import time
from dataclasses import dataclass, field
from decimal import Decimal
from typing import TYPE_CHECKING, Any, Awaitable, Callable, List, Optional, Protocol

logger = logging.getLogger(__name__)

if TYPE_CHECKING:
    from trader.adapters.binance.funding_oi_stream import BinanceCurrentFundingOISource

from trader.core.application.plugins.crypto_pre_trade_risk_plugin import (
    CryptoPreTradeRiskConfig,
    CryptoPreTradeRiskPlugin,
    CryptoRiskSnapshotProvider,
)
from trader.core.application.ports import BrokerPort
from trader.core.application.risk_engine import RiskCheckResult, RiskConfig, RiskEngine
from trader.core.domain.models.crypto_risk import (
    CryptoAccountRisk,
    CryptoFundingOIRiskMetrics,
    CryptoInstrumentSpec,
    CryptoMarketType,
    CryptoPositionRisk,
    CryptoRiskBudget,
    CryptoRiskSnapshot,
    LeverageBracket,
    OpenOrderRisk,
)
from trader.core.domain.models.signal import Signal


class CryptoRiskSnapshotUnavailable(RuntimeError):
    """Raised when pre-trade crypto risk inputs cannot be built safely."""


class CryptoRiskDataSource(Protocol):
    async def get_account_risk(self) -> CryptoAccountRisk: ...

    async def get_positions(self, symbols: set[str] | None = None) -> list[CryptoPositionRisk]: ...

    async def get_open_orders(self, symbols: set[str] | None = None) -> list[OpenOrderRisk]: ...

    async def get_instrument_specs(self, symbols: set[str]) -> dict[str, CryptoInstrumentSpec]: ...

    async def get_leverage_brackets(
        self, symbols: set[str]
    ) -> dict[str, list[LeverageBracket]]: ...

    async def get_mark_prices(self, symbols: set[str]) -> dict[str, Decimal]: ...

    async def get_venue_health(self) -> str: ...


@dataclass(frozen=True, slots=True)
class CryptoRiskSnapshotProviderConfig:
    base_symbols: tuple[str, ...] = ()
    risk_budget: CryptoRiskBudget = field(default_factory=CryptoRiskBudget)
    fail_on_missing_brackets: bool = True


class FundingOIMetricsPort(Protocol):
    async def compute_funding_oi_metrics(
        self,
        symbols: set[str],
    ) -> dict[str, CryptoFundingOIRiskMetrics]: ...

    def has_budget_enabled(self) -> bool: ...


def _normalize_symbol(symbol: str) -> str:
    return symbol.upper().replace("-", "").replace("/", "").strip()


class DataSourceCryptoRiskSnapshotProvider:
    def __init__(
        self,
        source: CryptoRiskDataSource,
        config: CryptoRiskSnapshotProviderConfig | None = None,
        funding_oi_metrics: Optional[FundingOIMetricsPort] = None,
    ) -> None:
        self._source = source
        self._config = config or CryptoRiskSnapshotProviderConfig()
        self._funding_oi_metrics = funding_oi_metrics

    async def build(self, signal: Signal) -> CryptoRiskSnapshot:
        target_symbol = _normalize_symbol(signal.symbol)
        if not target_symbol:
            raise CryptoRiskSnapshotUnavailable("signal symbol is required")

        try:
            account = await self._source.get_account_risk()
            positions = await self._source.get_positions(symbols=None)
            open_orders = await self._source.get_open_orders(symbols=None)

            symbols = self._portfolio_symbols(target_symbol, positions, open_orders)
            specs = await self._source.get_instrument_specs(symbols)
            brackets = await self._source.get_leverage_brackets(symbols)
            mark_prices = await self._source.get_mark_prices(symbols)
            venue_health = await self._source.get_venue_health()
        except CryptoRiskSnapshotUnavailable:
            raise
        except Exception as exc:
            raise CryptoRiskSnapshotUnavailable(f"crypto risk data source failed: {exc}") from exc

        self._validate(
            target_symbol=target_symbol,
            symbols=symbols,
            specs=specs,
            brackets=brackets,
            mark_prices=mark_prices,
        )

        funding_oi: dict[str, CryptoFundingOIRiskMetrics] = {}
        if self._funding_oi_metrics is not None and self._funding_oi_metrics.has_budget_enabled():
            try:
                funding_oi = await self._funding_oi_metrics.compute_funding_oi_metrics(symbols)
            except Exception as exc:
                raise CryptoRiskSnapshotUnavailable(
                    f"Funding/OI metrics computation failed: {exc}"
                ) from exc

        return CryptoRiskSnapshot(
            account=account,
            instrument_specs=specs,
            leverage_brackets=brackets,
            positions=positions,
            open_orders=open_orders,
            mark_prices=mark_prices,
            risk_budget=self._config.risk_budget,
            venue_health=venue_health,
            funding_oi_metrics=funding_oi,
        )

    def _portfolio_symbols(
        self,
        target_symbol: str,
        positions: list[CryptoPositionRisk],
        open_orders: list[OpenOrderRisk],
    ) -> set[str]:
        symbols = {target_symbol}
        symbols.update(_normalize_symbol(symbol) for symbol in self._config.base_symbols)
        symbols.update(_normalize_symbol(position.symbol) for position in positions)
        symbols.update(_normalize_symbol(order.symbol) for order in open_orders)
        symbols.discard("")
        return symbols

    def _validate(
        self,
        *,
        target_symbol: str,
        symbols: set[str],
        specs: dict[str, CryptoInstrumentSpec],
        brackets: dict[str, list[LeverageBracket]],
        mark_prices: dict[str, Decimal],
    ) -> None:
        if target_symbol not in specs:
            raise CryptoRiskSnapshotUnavailable(f"missing instrument spec for {target_symbol}")
        missing_marks = sorted(
            symbol for symbol in symbols if mark_prices.get(symbol, Decimal("0")) <= 0
        )
        if missing_marks:
            raise CryptoRiskSnapshotUnavailable(
                f"missing mark price for symbols: {', '.join(missing_marks)}"
            )

        target_spec = specs[target_symbol]
        if (
            self._config.fail_on_missing_brackets
            and target_spec.market_type != CryptoMarketType.SPOT
            and not brackets.get(target_symbol)
        ):
            raise CryptoRiskSnapshotUnavailable(f"missing leverage bracket for {target_symbol}")


def build_crypto_pre_trade_risk_check(
    *,
    broker: BrokerPort,
    snapshot_provider: CryptoRiskSnapshotProvider,
    risk_config: RiskConfig | None = None,
    plugin_config: CryptoPreTradeRiskConfig | None = None,
) -> Callable[[Signal], Awaitable[RiskCheckResult]]:
    engine = RiskEngine(
        broker,
        config=risk_config,
        pre_trade_plugins=[CryptoPreTradeRiskPlugin(snapshot_provider, plugin_config)],
    )
    return engine.check_pre_trade


class BinanceFundingOIMetricsSource:
    """
    Binance Funding/OI Metrics Source - Live Runtime 实现

    从 Binance REST API 拉取当前 Funding Rate 和 OI 数据，
    结合 FeatureStore 历史数据计算 Z-Score 和变化率指标。

    设计原则：
    - 当前值：从 BinanceCurrentFundingOISource 实时拉取
    - 历史值：优先从 FeatureStore 读取，缺失时使用当前值填充（带 stale 标记）
    - fail-closed：数据缺失或过期时设置对应 flags，由 CryptoPreTradeRiskPlugin 决定拒绝
    """

    def __init__(
        self,
        current_source: Optional["BinanceCurrentFundingOISource"] = None,
        feature_store: Optional[Any] = None,
        budget: CryptoRiskBudget = None,
    ) -> None:
        self._current_source = current_source
        self._feature_store = feature_store
        self._budget = budget or CryptoRiskBudget()
        self._cache: dict[str, CryptoFundingOIRiskMetrics] = {}
        self._cache_ts_ms: int = 0
        self._cache_ttl_ms: int = 60_000

    def has_budget_enabled(self) -> bool:
        return self._budget.funding_z_score_enabled or self._budget.oi_change_rate_enabled

    async def compute_funding_oi_metrics(
        self,
        symbols: set[str],
    ) -> dict[str, CryptoFundingOIRiskMetrics]:
        from trader.core.domain.services.funding_oi_window_calculator import (
            FundingOIWindowCalculator,
        )

        now_ms = int(time.time() * 1000)
        if now_ms - self._cache_ts_ms < self._cache_ttl_ms and self._cache:
            return self._cache

        result: dict[str, CryptoFundingOIRiskMetrics] = {}
        funding_window = self._budget.funding_history_window
        oi_window = self._budget.oi_history_window
        funding_min = self._budget.funding_min_periods
        oi_min = self._budget.oi_min_periods

        for sym in symbols:
            current_funding = await self._get_current_funding(sym)
            current_oi = await self._get_current_oi(sym)
            funding_ts = await self._get_latest_funding_ts(sym)
            oi_ts = await self._get_latest_oi_ts(sym)

            funding_history = await self._get_funding_history(sym, funding_window)
            oi_history = await self._get_oi_history(sym, oi_window)

            metrics = FundingOIWindowCalculator.compute_funding_oi_metrics(
                symbol=sym,
                current_funding_rate=(
                    float(current_funding) if current_funding is not None else None
                ),
                current_oi=float(current_oi) if current_oi is not None else None,
                funding_history=funding_history,
                oi_history=oi_history,
                funding_window=funding_window,
                oi_window=oi_window,
                funding_min_periods=funding_min,
                oi_min_periods=oi_min,
                latest_funding_ts_ms=funding_ts,
                latest_oi_ts_ms=oi_ts,
                current_ts_ms=now_ms,
                max_data_age_seconds=self._budget.max_data_age_seconds,
            )
            result[sym] = metrics

        self._cache = result
        self._cache_ts_ms = now_ms
        return result

    async def _get_current_funding(self, symbol: str) -> Optional[Decimal]:
        if self._current_source is not None:
            try:
                return await self._current_source.get_current_funding_rate(symbol)
            except Exception as e:
                logger.warning(f"[FundingOI] _get_current_funding failed for {symbol}: {e}")
                return None
        return None

    async def _get_current_oi(self, symbol: str) -> Optional[Decimal]:
        if self._current_source is not None:
            try:
                return await self._current_source.get_current_open_interest(symbol)
            except Exception as e:
                logger.warning(f"[FundingOI] _get_current_oi failed for {symbol}: {e}")
                return None
        return None

    async def _get_latest_funding_ts(self, symbol: str) -> int:
        if self._current_source is not None:
            try:
                return await self._current_source.get_latest_funding_ts_ms(symbol)
            except Exception as e:
                logger.warning(f"[FundingOI] _get_latest_funding_ts failed for {symbol}: {e}")
                return 0
        return 0

    async def _get_latest_oi_ts(self, symbol: str) -> int:
        if self._current_source is not None:
            try:
                return await self._current_source.get_latest_oi_ts_ms(symbol)
            except Exception as e:
                logger.warning(f"[FundingOI] _get_latest_oi_ts failed for {symbol}: {e}")
                return 0
        return 0

    async def _get_funding_history(self, symbol: str, window: int) -> List[float]:
        if self._feature_store is not None:
            try:
                end_ts = int(time.time() * 1000)
                start_ts = end_ts - (window * 8 * 3600 * 1000)
                if hasattr(self._feature_store, "read_feature_range"):
                    points = await self._feature_store.read_feature_range(
                        symbol=symbol,
                        feature_name="funding_rate",
                        start_time=start_ts,
                        end_time=end_ts,
                        version="v1",
                    )
                    if points and len(points) >= window:
                        return [float(p.value) for p in points[:window]]
                    elif points and len(points) > 0:
                        return [float(p.value) for p in points]
            except Exception as e:
                logger.warning(f"[FundingOI] _get_funding_history failed for {symbol}: {e}")

        return []

    async def _get_oi_history(self, symbol: str, window: int) -> List[float]:
        if self._feature_store is not None:
            try:
                end_ts = int(time.time() * 1000)
                start_ts = end_ts - (window * 5 * 60 * 1000)
                if hasattr(self._feature_store, "read_feature_range"):
                    points = await self._feature_store.read_feature_range(
                        symbol=symbol,
                        feature_name="open_interest",
                        start_time=start_ts,
                        end_time=end_ts,
                        version="v1",
                    )
                    if points and len(points) >= window:
                        return [float(p.value) for p in points[:window]]
                    elif points and len(points) > 0:
                        return [float(p.value) for p in points]
            except Exception as e:
                logger.warning(f"[FundingOI] _get_oi_history failed for {symbol}: {e}")

        return []
