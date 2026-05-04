from __future__ import annotations

from dataclasses import dataclass, field
from decimal import Decimal
from enum import Enum

from trader.core.domain.models.order import OrderSide


def _decimal(value: Decimal | int | float | str | None, default: Decimal = Decimal("0")) -> Decimal:
    if value is None:
        return default
    if isinstance(value, Decimal):
        return value
    return Decimal(str(value))


def _decimal_dict(values: dict[str, Decimal] | None) -> dict[str, Decimal]:
    return {key: _decimal(value) for key, value in (values or {}).items()}


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
    def liquidation_buffer_ratio(self) -> Decimal | None:
        if self.liquidation_price is None or self.mark_price <= 0 or self.qty == 0:
            return None
        if self.qty > 0:
            return (self.mark_price - self.liquidation_price) / self.mark_price
        return (self.liquidation_price - self.mark_price) / self.mark_price


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


@dataclass(slots=True)
class CryptoRiskBudget:
    symbol_notional_caps: dict[str, Decimal] = field(default_factory=dict)
    total_notional_cap: Decimal = Decimal("0")
    max_margin_ratio: Decimal = Decimal("0.80")
    min_liquidation_buffer_ratio: Decimal = Decimal("0")

    def __post_init__(self) -> None:
        self.symbol_notional_caps = _decimal_dict(self.symbol_notional_caps)
        self.total_notional_cap = _decimal(self.total_notional_cap)
        self.max_margin_ratio = _decimal(self.max_margin_ratio)
        self.min_liquidation_buffer_ratio = _decimal(self.min_liquidation_buffer_ratio)


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

    def __post_init__(self) -> None:
        self.mark_prices = _decimal_dict(self.mark_prices)
