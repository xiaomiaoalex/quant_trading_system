from __future__ import annotations

from dataclasses import dataclass, field
from decimal import Decimal
from enum import Enum
from typing import Any

from trader.core.domain.models.order import OrderSide


def _decimal(value: Decimal | int | float | str | None, default: Decimal = Decimal("0")) -> Decimal:
    if value is None:
        return default
    if isinstance(value, Decimal):
        return value
    return Decimal(str(value))


def _decimal_dict(values: dict[str, Decimal] | None) -> dict[str, Decimal]:
    return {_normalize_symbol(key): _decimal(value) for key, value in (values or {}).items()}


def _text_dict(values: dict[str, str] | None) -> dict[str, str]:
    return {
        _normalize_symbol(key): str(value).upper().strip() for key, value in (values or {}).items()
    }


def _normalize_symbol(symbol: str) -> str:
    return str(symbol).upper().strip()


def _normalize_venue(venue: str) -> str:
    return str(venue).lower().strip()


class AssetClass(str, Enum):
    CRYPTO = "crypto"
    CN_STOCK = "cn_stock"
    FUTURES = "futures"
    FX = "fx"
    FUND = "fund"
    CASH = "cash"
    UNKNOWN = "unknown"


@dataclass(slots=True)
class MarketInstrumentSpec:
    symbol: str
    venue: str
    asset_class: AssetClass
    price_tick: Decimal
    qty_step: Decimal
    min_qty: Decimal
    min_notional: Decimal
    max_qty: Decimal | None = None
    max_notional: Decimal | None = None
    base_asset: str = ""
    quote_asset: str = ""
    metadata: dict[str, Any] = field(default_factory=dict)

    def __post_init__(self) -> None:
        self.symbol = _normalize_symbol(self.symbol)
        self.venue = _normalize_venue(self.venue)
        self.price_tick = _decimal(self.price_tick)
        self.qty_step = _decimal(self.qty_step)
        self.min_qty = _decimal(self.min_qty)
        self.min_notional = _decimal(self.min_notional)
        self.max_qty = None if self.max_qty is None else _decimal(self.max_qty)
        self.max_notional = None if self.max_notional is None else _decimal(self.max_notional)


@dataclass(slots=True)
class MarketAccountRisk:
    equity: Decimal
    available_cash: Decimal
    venue: str
    asset_class: AssetClass
    account_id: str = ""
    currency: str = ""
    metadata: dict[str, Any] = field(default_factory=dict)

    def __post_init__(self) -> None:
        self.equity = _decimal(self.equity)
        self.available_cash = _decimal(self.available_cash)
        self.venue = _normalize_venue(self.venue)
        self.currency = self.currency.upper().strip()


@dataclass(slots=True)
class MarketPositionRisk:
    symbol: str
    venue: str
    asset_class: AssetClass
    qty: Decimal
    entry_price: Decimal
    risk_price: Decimal
    metadata: dict[str, Any] = field(default_factory=dict)

    def __post_init__(self) -> None:
        self.symbol = _normalize_symbol(self.symbol)
        self.venue = _normalize_venue(self.venue)
        self.qty = _decimal(self.qty)
        self.entry_price = _decimal(self.entry_price)
        self.risk_price = _decimal(self.risk_price)

    @property
    def notional(self) -> Decimal:
        return abs(self.qty) * self.risk_price


@dataclass(slots=True)
class MarketOpenOrderRisk:
    cl_ord_id: str
    symbol: str
    venue: str
    asset_class: AssetClass
    side: OrderSide
    qty: Decimal
    filled_qty: Decimal
    price: Decimal
    reduce_only: bool = False
    status: str = "OPEN"
    metadata: dict[str, Any] = field(default_factory=dict)

    def __post_init__(self) -> None:
        self.symbol = _normalize_symbol(self.symbol)
        self.venue = _normalize_venue(self.venue)
        self.qty = _decimal(self.qty)
        self.filled_qty = _decimal(self.filled_qty)
        self.price = _decimal(self.price)
        self.status = self.status.upper().strip()

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
class MarketRiskBudget:
    symbol_notional_caps: dict[str, Decimal] = field(default_factory=dict)
    symbol_groups: dict[str, str] = field(default_factory=dict)
    group_notional_caps: dict[str, Decimal] = field(default_factory=dict)
    total_notional_cap: Decimal = Decimal("0")
    metadata: dict[str, Any] = field(default_factory=dict)

    def __post_init__(self) -> None:
        self.symbol_notional_caps = _decimal_dict(self.symbol_notional_caps)
        self.symbol_groups = _text_dict(self.symbol_groups)
        self.group_notional_caps = {
            str(group).upper().strip(): _decimal(value)
            for group, value in (self.group_notional_caps or {}).items()
        }
        self.total_notional_cap = _decimal(self.total_notional_cap)


@dataclass(slots=True)
class MarketRiskSnapshot:
    account: MarketAccountRisk
    instrument_specs: dict[str, MarketInstrumentSpec]
    positions: list[MarketPositionRisk] = field(default_factory=list)
    open_orders: list[MarketOpenOrderRisk] = field(default_factory=list)
    risk_prices: dict[str, Decimal] = field(default_factory=dict)
    risk_budget: MarketRiskBudget = field(default_factory=MarketRiskBudget)
    venue_health: str = "HEALTHY"
    metadata: dict[str, Any] = field(default_factory=dict)

    def __post_init__(self) -> None:
        self.instrument_specs = {
            _normalize_symbol(symbol): spec for symbol, spec in self.instrument_specs.items()
        }
        self.risk_prices = _decimal_dict(self.risk_prices)
        self.venue_health = self.venue_health.upper().strip()


@dataclass(frozen=True, slots=True)
class MarketRiskAuditEvent:
    event_type: str
    trace_id: str
    ts_ms: int
    asset_class: AssetClass
    venue: str
    account_id: str
    payload: dict[str, Any]
    stream_key: str = "risk:market"
    schema_version: int = 1

    def to_record(self) -> dict[str, Any]:
        return {
            "stream_key": self.stream_key,
            "event_type": self.event_type,
            "schema_version": self.schema_version,
            "trace_id": self.trace_id,
            "ts_ms": self.ts_ms,
            "asset_class": self.asset_class.value,
            "venue": _normalize_venue(self.venue),
            "account_id": self.account_id,
            "payload": self.payload,
        }
