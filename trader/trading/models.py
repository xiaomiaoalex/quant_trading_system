"""Spot trading contracts. Amounts remain Decimal through every boundary."""

from dataclasses import asdict, dataclass, field
from decimal import Decimal
from enum import Enum
from typing import Any, Protocol


class TradingError(ValueError):
    pass


class Conflict(TradingError):
    pass


class NotReady(TradingError):
    pass


class OrderStatus(str, Enum):
    PENDING_ACCEPTANCE = "PENDING_ACCEPTANCE"
    OPEN = "OPEN"
    PARTIALLY_FILLED = "PARTIALLY_FILLED"
    FILLED = "FILLED"
    CANCELLED = "CANCELLED"
    EXPIRED = "EXPIRED"
    REJECTED = "REJECTED"


TERMINAL = {"FILLED", "CANCELLED", "EXPIRED", "REJECTED"}


@dataclass(frozen=True)
class PlaceOrderCommand:
    command_id: str
    account_id: str
    symbol: str
    base_asset: str
    quote_asset: str
    side: str
    qty: Decimal
    price: Decimal
    deployment_id: str = "manual"
    order_type: str = "LIMIT"
    time_in_force: str = "GTC"
    decision_id: str = ""


@dataclass(frozen=True)
class CancelOrderCommand:
    command_id: str
    account_id: str
    cl_ord_id: str


@dataclass(frozen=True)
class OrderObservation:
    account_id: str
    cl_ord_id: str
    status: str
    cumulative_qty: Decimal
    broker_order_id: str = ""
    observed_at_ms: int = 0
    source: str = "REST"


@dataclass(frozen=True)
class Execution:
    account_id: str
    cl_ord_id: str
    symbol: str
    exec_id: str
    qty: Decimal
    price: Decimal
    fee: Decimal = Decimal("0")
    fee_asset: str = ""
    occurred_at_ms: int = 0
    venue: str = "binance_spot"

    @property
    def key(self) -> str:
        return f"{self.venue}:{self.account_id}:{self.symbol}:{self.exec_id}"

    @property
    def content(self) -> dict[str, Any]:
        return {
            "account_id": self.account_id,
            "cl_ord_id": self.cl_ord_id,
            "symbol": self.symbol,
            "exec_id": self.exec_id,
            "qty": str(self.qty),
            "price": str(self.price),
            "fee": str(self.fee),
            "fee_asset": self.fee_asset,
            "occurred_at_ms": self.occurred_at_ms,
            "venue": self.venue,
        }


class ExchangeOutcome(str, Enum):
    ACCEPTED = "ACCEPTED"
    REJECTED = "REJECTED"
    NOT_FOUND = "NOT_FOUND"
    UNKNOWN = "UNKNOWN"
    UNAVAILABLE = "UNAVAILABLE"


@dataclass(frozen=True)
class ExchangeOrderResult:
    outcome: ExchangeOutcome
    broker_order_id: str = ""
    status: str = "OPEN"
    reason: str = ""
    cumulative_qty: Decimal = Decimal("0")


class ExchangePort(Protocol):
    """Venue boundary. It reports facts; it never mutates local order state."""

    async def place_order(
        self, command: PlaceOrderCommand, cl_ord_id: str
    ) -> ExchangeOrderResult: ...

    async def cancel_order(
        self, account_id: str, cl_ord_id: str, command_id: str, symbol: str
    ) -> ExchangeOrderResult: ...

    async def query_order(
        self, account_id: str, cl_ord_id: str, symbol: str
    ) -> ExchangeOrderResult: ...


@dataclass(frozen=True)
class ReconcileSnapshot:
    """Complete exchange account view. Incomplete snapshots never enable trading."""

    account_id: str
    checked_at_ms: int
    generation: int
    balances: dict[str, Decimal]
    external_locked: dict[str, Decimal] = field(default_factory=dict)
    open_orders: list[OrderObservation] = field(default_factory=list)
    executions: list[Execution] = field(default_factory=list)
    marks: dict[str, dict[str, Any]] = field(default_factory=dict)
    stream_healthy: bool = True
    complete: bool = True


@dataclass(frozen=True)
class CommandReceipt:
    command_id: str
    cl_ord_id: str
    kind: str
    status: str
    order_status: str = ""
    error: str = ""


@dataclass(frozen=True)
class AccountRiskView:
    account_id: str
    status: str
    account_version: int
    generation: int
    checked_at_ms: int | None
    reasons: tuple[str, ...]
    assets: dict[str, dict[str, Decimal]]
    positions: dict[str, Decimal]
    valuation_complete: bool

    @property
    def ready(self) -> bool:
        return self.status == "READY" and not self.reasons


@dataclass
class OrderAggregate:
    cl_ord_id: str
    command: PlaceOrderCommand
    status: str = "PENDING_ACCEPTANCE"
    broker_order_id: str = ""
    booked_qty: Decimal = Decimal("0")
    booked_quote: Decimal = Decimal("0")
    observed_qty: Decimal = Decimal("0")
    observed_at_ms: int = 0

    @property
    def remaining_qty(self) -> Decimal:
        return self.command.qty - self.booked_qty

    def observe(self, observation: OrderObservation) -> None:
        status = OrderStatus(observation.status).value
        if observation.cumulative_qty < 0 or observation.cumulative_qty > self.command.qty:
            raise Conflict("Observed execution quantity outside order bounds")
        if observation.broker_order_id and self.broker_order_id not in (
            "",
            observation.broker_order_id,
        ):
            raise Conflict("Exchange order identity changed")
        if observation.observed_at_ms < self.observed_at_ms:
            return
        if observation.cumulative_qty < self.observed_qty:
            return
        if self.status in TERMINAL and status != self.status:
            if status in TERMINAL:
                raise Conflict("Conflicting terminal order observations")
            return
        if (
            status == "FILLED"
            and self.command.order_type != "MARKET"
            and observation.cumulative_qty != self.command.qty
        ):
            # A LIMIT order must be covered exactly. A MARKET order is settled in
            # quote currency, so the venue may fill a different base quantity.
            raise Conflict("FILLED observation must cover order quantity")
        self.status = status
        self.observed_qty = observation.cumulative_qty
        self.observed_at_ms = observation.observed_at_ms
        self.broker_order_id = observation.broker_order_id or self.broker_order_id

    def apply(self, execution: Execution) -> None:
        if execution.qty <= 0 or execution.price <= 0 or execution.fee < 0:
            raise TradingError("Invalid execution amounts")
        if (
            execution.symbol != self.command.symbol
            or self.booked_qty + execution.qty > self.command.qty
        ):
            raise Conflict("Execution does not match order quantity/symbol")
        if self.status == "REJECTED":
            raise Conflict("Execution received for rejected order")
        self.booked_qty += execution.qty
        self.booked_quote += execution.qty * execution.price
        if self.status not in TERMINAL:
            self.status = "FILLED" if self.booked_qty == self.command.qty else "PARTIALLY_FILLED"


@dataclass
class CommandRecord:
    command_id: str
    cl_ord_id: str
    kind: str
    payload_hash: str
    status: str = "QUEUED"
    error: str = ""
    actor: str = ""


@dataclass
class Hold:
    asset: str
    amount: Decimal


@dataclass
class AccountState:
    account_id: str
    status: str = "RECOVERING"
    version: int = 0
    balances: dict[str, Decimal] = field(default_factory=dict)
    external_locked: dict[str, Decimal] = field(default_factory=dict)
    positions: dict[str, Decimal] = field(default_factory=dict)
    orders: dict[str, OrderAggregate] = field(default_factory=dict)
    commands: dict[str, CommandRecord] = field(default_factory=dict)
    holds: dict[str, Hold] = field(default_factory=dict)
    executions: dict[str, dict[str, Any]] = field(default_factory=dict)
    ledger: list[dict[str, Any]] = field(default_factory=list)
    events: list[dict[str, Any]] = field(default_factory=list)
    outbox: list[dict[str, Any]] = field(default_factory=list)
    control: dict[str, Any] = field(default_factory=dict)
    reconciliation: dict[str, Any] = field(default_factory=dict)


def json_value(value: Any) -> Any:
    if isinstance(value, Decimal):
        return str(value)
    if isinstance(value, Enum):
        return value.value
    if hasattr(value, "__dataclass_fields__"):
        return json_value(asdict(value))
    if isinstance(value, dict):
        return {k: json_value(v) for k, v in value.items()}
    if isinstance(value, (tuple, list)):
        return [json_value(v) for v in value]
    return value
