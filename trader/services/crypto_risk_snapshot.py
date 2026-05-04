from __future__ import annotations

from dataclasses import dataclass, field
from decimal import Decimal
from typing import Awaitable, Callable, Protocol

from trader.core.application.plugins.crypto_pre_trade_risk_plugin import (
    CryptoPreTradeRiskConfig,
    CryptoPreTradeRiskPlugin,
    CryptoRiskSnapshotProvider,
)
from trader.core.application.ports import BrokerPort
from trader.core.application.risk_engine import RiskCheckResult, RiskConfig, RiskEngine
from trader.core.domain.models.crypto_risk import (
    CryptoAccountRisk,
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


def _normalize_symbol(symbol: str) -> str:
    return symbol.upper().replace("-", "").replace("/", "").strip()


class DataSourceCryptoRiskSnapshotProvider:
    def __init__(
        self,
        source: CryptoRiskDataSource,
        config: CryptoRiskSnapshotProviderConfig | None = None,
    ) -> None:
        self._source = source
        self._config = config or CryptoRiskSnapshotProviderConfig()

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

        return CryptoRiskSnapshot(
            account=account,
            instrument_specs=specs,
            leverage_brackets=brackets,
            positions=positions,
            open_orders=open_orders,
            mark_prices=mark_prices,
            risk_budget=self._config.risk_budget,
            venue_health=venue_health,
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
