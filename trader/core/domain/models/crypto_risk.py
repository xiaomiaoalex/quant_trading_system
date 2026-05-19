from __future__ import annotations

from dataclasses import dataclass, field
from decimal import Decimal
from enum import Enum
from typing import Optional

from trader.core.domain.models.market_risk import (
    AssetClass,
    MarketAccountRisk,
    MarketInstrumentSpec,
    MarketOpenOrderRisk,
    MarketPositionRisk,
    MarketRiskBudget,
    MarketRiskSnapshot,
)
from trader.core.domain.models.order import OrderSide


def _decimal(value: Decimal | int | float | str | None, default: Decimal = Decimal("0")) -> Decimal:
    if value is None:
        return default
    if isinstance(value, Decimal):
        return value
    return Decimal(str(value))


def _decimal_dict(values: dict[str, Decimal] | None) -> dict[str, Decimal]:
    return {key: _decimal(value) for key, value in (values or {}).items()}


def _normalize_symbol(symbol: str) -> str:
    return str(symbol).upper().replace("-", "").replace("/", "").strip()


def _normalize_cluster(cluster: str) -> str:
    return str(cluster).upper().strip()


def _text_dict(values: dict[str, str] | None) -> dict[str, str]:
    return {
        _normalize_symbol(key): _normalize_cluster(value) for key, value in (values or {}).items()
    }


class CryptoMarketType(str, Enum):
    SPOT = "spot"
    USD_M_FUTURES = "usd_m_futures"
    COIN_M_FUTURES = "coin_m_futures"


class MarginMode(str, Enum):
    CROSS = "cross"
    ISOLATED = "isolated"


@dataclass(slots=True)
class CryptoInstrumentSpec:
    symbol: str
    market_type: CryptoMarketType
    price_tick: Decimal
    qty_step: Decimal
    min_qty: Decimal
    min_notional: Decimal
    max_qty: Decimal | None = None
    max_notional: Decimal | None = None
    base_asset: str = ""
    quote_asset: str = "USDT"

    def __post_init__(self) -> None:
        self.price_tick = _decimal(self.price_tick)
        self.qty_step = _decimal(self.qty_step)
        self.min_qty = _decimal(self.min_qty)
        self.min_notional = _decimal(self.min_notional)
        self.max_qty = None if self.max_qty is None else _decimal(self.max_qty)
        self.max_notional = None if self.max_notional is None else _decimal(self.max_notional)

    def to_market_spec(self, venue: str = "binance") -> MarketInstrumentSpec:
        return MarketInstrumentSpec(
            symbol=self.symbol,
            venue=venue,
            asset_class=AssetClass.CRYPTO,
            price_tick=self.price_tick,
            qty_step=self.qty_step,
            min_qty=self.min_qty,
            min_notional=self.min_notional,
            max_qty=self.max_qty,
            max_notional=self.max_notional,
            base_asset=self.base_asset,
            quote_asset=self.quote_asset,
            metadata={"market_type": self.market_type.value},
        )


@dataclass(slots=True)
class LeverageBracket:
    symbol: str
    notional_floor: Decimal
    notional_cap: Decimal
    initial_leverage: Decimal
    maint_margin_ratio: Decimal
    maint_amount: Decimal = Decimal("0")

    def __post_init__(self) -> None:
        self.notional_floor = _decimal(self.notional_floor)
        self.notional_cap = _decimal(self.notional_cap)
        self.initial_leverage = _decimal(self.initial_leverage)
        self.maint_margin_ratio = _decimal(self.maint_margin_ratio)
        self.maint_amount = _decimal(self.maint_amount)

    def contains(self, notional: Decimal) -> bool:
        return self.notional_floor <= notional <= self.notional_cap


@dataclass(slots=True)
class CryptoAccountRisk:
    equity: Decimal
    available_balance: Decimal
    wallet_balance: Decimal
    margin_balance: Decimal
    total_initial_margin: Decimal = Decimal("0")
    total_maintenance_margin: Decimal = Decimal("0")

    def __post_init__(self) -> None:
        self.equity = _decimal(self.equity)
        self.available_balance = _decimal(self.available_balance)
        self.wallet_balance = _decimal(self.wallet_balance)
        self.margin_balance = _decimal(self.margin_balance)
        self.total_initial_margin = _decimal(self.total_initial_margin)
        self.total_maintenance_margin = _decimal(self.total_maintenance_margin)

    def to_market_account(
        self,
        venue: str = "binance",
        account_id: str = "",
        currency: str = "USDT",
    ) -> MarketAccountRisk:
        return MarketAccountRisk(
            equity=self.equity,
            available_cash=self.available_balance,
            venue=venue,
            asset_class=AssetClass.CRYPTO,
            account_id=account_id,
            currency=currency,
            metadata={
                "wallet_balance": self.wallet_balance,
                "margin_balance": self.margin_balance,
                "total_initial_margin": self.total_initial_margin,
                "total_maintenance_margin": self.total_maintenance_margin,
            },
        )


@dataclass(slots=True)
class CryptoPositionRisk:
    symbol: str
    qty: Decimal
    entry_price: Decimal
    mark_price: Decimal
    leverage: Decimal = Decimal("1")
    margin_mode: MarginMode = MarginMode.CROSS
    liquidation_price: Decimal | None = None

    def __post_init__(self) -> None:
        self.qty = _decimal(self.qty)
        self.entry_price = _decimal(self.entry_price)
        self.mark_price = _decimal(self.mark_price)
        self.leverage = _decimal(self.leverage, Decimal("1"))
        self.liquidation_price = (
            None if self.liquidation_price is None else _decimal(self.liquidation_price)
        )

    @property
    def notional(self) -> Decimal:
        return abs(self.qty) * self.mark_price

    @property
    def risk_price(self) -> Decimal:
        return self.mark_price

    @property
    def liquidation_buffer_ratio(self) -> Decimal | None:
        if self.liquidation_price is None or self.mark_price <= 0 or self.qty == 0:
            return None
        if self.qty > 0:
            return (self.mark_price - self.liquidation_price) / self.mark_price
        return (self.liquidation_price - self.mark_price) / self.mark_price

    def to_market_position(self, venue: str = "binance") -> MarketPositionRisk:
        return MarketPositionRisk(
            symbol=self.symbol,
            venue=venue,
            asset_class=AssetClass.CRYPTO,
            qty=self.qty,
            entry_price=self.entry_price,
            risk_price=self.mark_price,
            metadata={
                "mark_price": self.mark_price,
                "leverage": self.leverage,
                "margin_mode": self.margin_mode.value,
                "liquidation_price": self.liquidation_price,
                "liquidation_buffer_ratio": self.liquidation_buffer_ratio,
            },
        )


@dataclass(slots=True)
class OpenOrderRisk:
    cl_ord_id: str
    symbol: str
    side: OrderSide
    qty: Decimal
    filled_qty: Decimal
    price: Decimal
    reduce_only: bool = False
    status: str = "OPEN"

    def __post_init__(self) -> None:
        self.qty = _decimal(self.qty)
        self.filled_qty = _decimal(self.filled_qty)
        self.price = _decimal(self.price)

    @property
    def remaining_qty(self) -> Decimal:
        return max(Decimal("0"), self.qty - self.filled_qty)

    @property
    def signed_remaining_qty(self) -> Decimal:
        if self.side == OrderSide.BUY:
            return self.remaining_qty
        return -self.remaining_qty

    @property
    def notional(self) -> Decimal:
        return self.remaining_qty * self.price

    def to_market_open_order(self, venue: str = "binance") -> MarketOpenOrderRisk:
        return MarketOpenOrderRisk(
            cl_ord_id=self.cl_ord_id,
            symbol=self.symbol,
            venue=venue,
            asset_class=AssetClass.CRYPTO,
            side=self.side,
            qty=self.qty,
            filled_qty=self.filled_qty,
            price=self.price,
            reduce_only=self.reduce_only,
            status=self.status,
        )


@dataclass(slots=True)
class CryptoFundingOIRiskMetrics:
    symbol: str
    current_funding_rate: Optional[Decimal] = None
    funding_rate_z_score: Optional[float] = None
    funding_rate_mean: float = 0.0
    funding_rate_std: float = 0.0
    funding_history_count: int = 0
    current_open_interest: Optional[Decimal] = None
    open_interest_change_rate: Optional[float] = None
    oi_mean: float = 0.0
    oi_std: float = 0.0
    oi_history_count: int = 0
    funding_data_stale: bool = False
    oi_data_stale: bool = False
    data_age_ms: int = 0
    funding_window_insufficient: bool = False
    oi_window_insufficient: bool = False
    funding_current_missing: bool = False
    oi_current_missing: bool = False
    latest_funding_ts_ms: int = 0
    latest_oi_ts_ms: int = 0

    def __post_init__(self) -> None:
        if self.current_funding_rate is not None:
            self.current_funding_rate = _decimal(self.current_funding_rate)
        if self.current_open_interest is not None:
            self.current_open_interest = _decimal(self.current_open_interest)

    @property
    def data_stale(self) -> bool:
        return self.funding_data_stale or self.oi_data_stale

    @property
    def window_insufficient(self) -> bool:
        return self.funding_window_insufficient or self.oi_window_insufficient

    @property
    def any_funding_missing(self) -> bool:
        return (
            self.funding_current_missing
            or self.funding_window_insufficient
            or self.funding_data_stale
        )

    @property
    def any_oi_missing(self) -> bool:
        return self.oi_current_missing or self.oi_window_insufficient or self.oi_data_stale


@dataclass(slots=True)
class CryptoRiskBudget:
    symbol_notional_caps: dict[str, Decimal] = field(default_factory=dict)
    symbol_clusters: dict[str, str] = field(default_factory=dict)
    cluster_notional_caps: dict[str, Decimal] = field(default_factory=dict)
    total_notional_cap: Decimal = Decimal("0")
    max_margin_ratio: Decimal = Decimal("0.80")
    min_liquidation_buffer_ratio: Decimal = Decimal("0")
    max_abs_funding_rate_z_score: Decimal = Decimal("0")
    max_abs_open_interest_change_rate: Decimal = Decimal("0")
    funding_history_window: int = 20
    oi_history_window: int = 20
    funding_min_periods: int = 10
    oi_min_periods: int = 10
    max_data_age_seconds: int = 24 * 3600

    def __post_init__(self) -> None:
        self.symbol_notional_caps = {
            _normalize_symbol(symbol): value
            for symbol, value in _decimal_dict(self.symbol_notional_caps).items()
        }
        self.symbol_clusters = _text_dict(self.symbol_clusters)
        self.cluster_notional_caps = {
            _normalize_cluster(cluster): value
            for cluster, value in _decimal_dict(self.cluster_notional_caps).items()
        }
        self.total_notional_cap = _decimal(self.total_notional_cap)
        self.max_margin_ratio = _decimal(self.max_margin_ratio)
        self.min_liquidation_buffer_ratio = _decimal(self.min_liquidation_buffer_ratio)
        self.max_abs_funding_rate_z_score = _decimal(self.max_abs_funding_rate_z_score)
        self.max_abs_open_interest_change_rate = _decimal(self.max_abs_open_interest_change_rate)

    def to_market_budget(self) -> MarketRiskBudget:
        return MarketRiskBudget(
            symbol_notional_caps=dict(self.symbol_notional_caps),
            symbol_groups=dict(self.symbol_clusters),
            group_notional_caps=dict(self.cluster_notional_caps),
            total_notional_cap=self.total_notional_cap,
            metadata={
                "max_margin_ratio": self.max_margin_ratio,
                "min_liquidation_buffer_ratio": self.min_liquidation_buffer_ratio,
                "max_abs_funding_rate_z_score": self.max_abs_funding_rate_z_score,
                "max_abs_open_interest_change_rate": self.max_abs_open_interest_change_rate,
            },
        )

    @property
    def funding_z_score_enabled(self) -> bool:
        return self.max_abs_funding_rate_z_score > 0

    @property
    def oi_change_rate_enabled(self) -> bool:
        return self.max_abs_open_interest_change_rate > 0


@dataclass(slots=True)
class CryptoRiskSnapshot:
    account: CryptoAccountRisk
    instrument_specs: dict[str, CryptoInstrumentSpec]
    leverage_brackets: dict[str, list[LeverageBracket]]
    positions: list[CryptoPositionRisk] = field(default_factory=list)
    open_orders: list[OpenOrderRisk] = field(default_factory=list)
    mark_prices: dict[str, Decimal] = field(default_factory=dict)
    risk_budget: CryptoRiskBudget = field(default_factory=CryptoRiskBudget)
    venue_health: str = "HEALTHY"
    funding_oi_metrics: dict[str, CryptoFundingOIRiskMetrics] = field(default_factory=dict)

    def __post_init__(self) -> None:
        self.mark_prices = _decimal_dict(self.mark_prices)

    def to_market_snapshot(
        self,
        venue: str = "binance",
        account_id: str = "",
        currency: str = "USDT",
    ) -> MarketRiskSnapshot:
        return MarketRiskSnapshot(
            account=self.account.to_market_account(
                venue=venue,
                account_id=account_id,
                currency=currency,
            ),
            instrument_specs={
                symbol: spec.to_market_spec(venue=venue)
                for symbol, spec in self.instrument_specs.items()
            },
            positions=[position.to_market_position(venue=venue) for position in self.positions],
            open_orders=[order.to_market_open_order(venue=venue) for order in self.open_orders],
            risk_prices=dict(self.mark_prices),
            risk_budget=self.risk_budget.to_market_budget(),
            venue_health=self.venue_health,
            metadata={
                "source": "crypto",
                "leverage_bracket_symbols": tuple(self.leverage_brackets),
            },
        )
