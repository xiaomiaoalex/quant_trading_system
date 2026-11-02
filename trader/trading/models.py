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

# Venue settlement semantics of an order.
SETTLEMENT_QUOTE = "QUOTE"
SETTLEMENT_BASE = "BASE"
SETTLEMENTS = {SETTLEMENT_QUOTE, SETTLEMENT_BASE}


@dataclass(frozen=True)
class PlaceOrderCommand:
    """The single order specification consumed by every admission boundary.

    ``book_id`` is the stable strategy sub-ledger key. ``price_source`` names
    where the economic price came from and ``price_at_ms`` records when that
    price was observed; neither is a free-form exchange parameter.
    """

    command_id: str
    account_id: str
    symbol: str
    base_asset: str
    quote_asset: str
    side: str
    qty: Decimal
    price: Decimal
    deployment_id: str = "manual"
    order_type: str = ""
    time_in_force: str = "GTC"
    decision_id: str = ""
    book_id: str = ""
    price_source: str = ""
    price_at_ms: int = -1
    # How the venue settles this order:
    #   QUOTE - a market buy spends ``qty * price`` of quote and the venue
    #           chooses the base quantity it buys with that quote;
    #   BASE  - the venue fills exactly the requested base quantity at its
    #           own price (the historical bar simulator).
    settlement: str = "QUOTE"


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
class VenueOrder:
    """Venue-side facts about an order inside our own client-order namespace.

    The venue is authoritative for the order state. When the local ledger lost
    the order intent (a rebuilt ledger or a restore), these facts let recovery
    rebuild the mapping instead of treating our own history as foreign
    activity.
    """

    cl_ord_id: str
    broker_order_id: str
    symbol: str
    side: str
    order_type: str
    qty: Decimal
    price: Decimal
    filled_qty: Decimal
    status: str
    created_at_ms: int = 0
    base_asset: str = ""
    quote_asset: str = ""


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
    venue_orders: list[VenueOrder] = field(default_factory=list)
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
    # Venue balances are observations, not the authoritative balance. An
    # unexplained difference stays visible here instead of being absorbed.
    venue_balances: dict[str, str] = field(default_factory=dict)
    balance_differences: dict[str, Any] = field(default_factory=dict)

    @property
    def ready(self) -> bool:
        return self.status == "READY" and not self.reasons


# Business-order status advances through one monotonic entry. Non-terminal
# statuses only move forward; a terminal status is final. A venue observation
# that arrives late (older timestamp or smaller cumulative quantity) is ignored
# before it reaches this table.
_STATUS_RANK = {
    "PENDING_ACCEPTANCE": 0,
    "OPEN": 1,
    "PARTIALLY_FILLED": 2,
    "FILLED": 3,
    "CANCELLED": 3,
    "EXPIRED": 3,
    "REJECTED": 3,
}


def advance_order_status(order: "OrderAggregate", status: str) -> bool:
    """The only transition entry for an order's business status.

    Returns True when the status changed, False for an idempotent or stale
    observation. Conflicting terminal states raise ``Conflict`` instead of
    silently picking one.
    """
    target = OrderStatus(status).value
    current = order.status
    if target == current:
        return False
    if current in TERMINAL:
        if target in TERMINAL:
            raise Conflict("Conflicting terminal order observations")
        return False
    if target in TERMINAL:
        order.status = target
        return True
    if _STATUS_RANK[target] < _STATUS_RANK[current]:
        return False
    order.status = target
    return True


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
        """Base quantity still requested.

        A quote-settled MARKET BUY may be filled with more base than requested;
        the residual request is then zero, never negative.
        """
        residual = self.command.qty - self.booked_qty
        return residual if residual > 0 else Decimal(0)

    def is_quote_settled_market_buy(self) -> bool:
        """A quote-settled MARKET BUY.

        The live adapter sends ``quoteOrderQty = qty * price``, so the venue
        chooses how much base that quote buys: a better fill yields more base
        than requested while spending no more than the quote budget. A
        base-settled order (the bar simulator) is bounded by the requested
        quantity as before.
        """
        return (
            self.command.order_type == "MARKET"
            and self.command.side == "BUY"
            and self.command.settlement == SETTLEMENT_QUOTE
        )

    @property
    def quote_budget(self) -> Decimal:
        """Quote amount a quote-settled MARKET BUY may spend in total."""
        return self.command.qty * self.command.price

    def observe(self, observation: OrderObservation) -> None:
        status = OrderStatus(observation.status).value
        if observation.cumulative_qty < 0:
            raise Conflict("Observed execution quantity outside order bounds")
        if not self.is_quote_settled_market_buy() and observation.cumulative_qty > self.command.qty:
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
        if (
            status == "FILLED"
            and self.command.order_type != "MARKET"
            and observation.cumulative_qty != self.command.qty
        ):
            # A LIMIT order must be covered exactly. A MARKET order is settled in
            # quote currency, so the venue may fill a different base quantity.
            raise Conflict("FILLED observation must cover order quantity")
        advance_order_status(self, status)
        self.observed_qty = observation.cumulative_qty
        self.observed_at_ms = observation.observed_at_ms
        self.broker_order_id = observation.broker_order_id or self.broker_order_id

    def apply(self, execution: Execution) -> None:
        if execution.qty <= 0 or execution.price <= 0 or execution.fee < 0:
            raise TradingError("Invalid execution amounts")
        if execution.symbol != self.command.symbol:
            raise Conflict("Execution does not match order quantity/symbol")
        if self.is_quote_settled_market_buy():
            # The venue may return more base than requested, but only inside the
            # quote budget the order was admitted with.
            if self.booked_quote + execution.qty * execution.price > self.quote_budget:
                raise Conflict("Execution exceeds the market quote budget")
        elif self.booked_qty + execution.qty > self.command.qty:
            raise Conflict("Execution does not match order quantity/symbol")
        if self.status == "REJECTED":
            raise Conflict("Execution received for rejected order")
        self.booked_qty += execution.qty
        self.booked_quote += execution.qty * execution.price
        if self.status not in TERMINAL:
            if self.is_quote_settled_market_buy():
                # The venue was asked to spend a quote budget, so covering the
                # requested base quantity is not proof of completion. Only an
                # exhausted quote budget (an explicit economic fact: the venue
                # can spend no more) or a terminal venue observation completes
                # this order.
                advance_order_status(
                    self,
                    "FILLED" if self.booked_quote >= self.quote_budget else "PARTIALLY_FILLED",
                )
            else:
                advance_order_status(
                    self, "FILLED" if self.booked_qty == self.command.qty else "PARTIALLY_FILLED"
                )


@dataclass
class CommandRecord:
    command_id: str
    cl_ord_id: str
    kind: str
    payload_hash: str
    status: str = "QUEUED"
    error: str = ""
    actor: str = ""
    # Durable request payload for commands that have no order aggregate (for
    # example an explicit book allocation). Empty for PLACE/CANCEL.
    request: dict[str, Any] = field(default_factory=dict)


@dataclass
class Hold:
    asset: str
    amount: Decimal


@dataclass
class BookLedger:
    """One stable strategy sub-ledger inside the single account.

    ``balances`` are owned by this book; ``holds`` are the book layer of the
    two-layer order reservation. Positions stay per book so strategy equity and
    budget can be queried without borrowing another book's assets.
    """

    book_id: str
    balances: dict[str, Decimal] = field(default_factory=dict)
    positions: dict[str, Decimal] = field(default_factory=dict)
    holds: dict[str, Hold] = field(default_factory=dict)


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
    books: dict[str, BookLedger] = field(default_factory=dict)
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
