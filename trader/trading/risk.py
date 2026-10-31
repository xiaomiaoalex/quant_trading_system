"""Pure spot admission rules; called inside the account's storage transaction."""

from dataclasses import dataclass
from decimal import Decimal, DecimalException

from trader.trading.models import AccountState, Hold, NotReady, PlaceOrderCommand, TradingError


class RiskRejected(TradingError):
    pass


@dataclass(frozen=True)
class RiskPolicy:
    max_reconciliation_age_ms: int = 60_000
    max_mark_age_ms: int = 60_000
    fee_rate: Decimal = Decimal("0.001")
    max_gross_notional: Decimal | None = None
    max_symbol_notional: Decimal | None = None

    def __post_init__(self):
        if not self.fee_rate.is_finite() or self.fee_rate < 0:
            raise ValueError("Invalid fee rate")
        if self.max_reconciliation_age_ms <= 0 or self.max_mark_age_ms <= 0:
            raise ValueError("Freshness bounds must be positive")
        for limit in (self.max_gross_notional, self.max_symbol_notional):
            if limit is not None and (not limit.is_finite() or limit < 0):
                raise ValueError("Invalid notional limit")


def available_balance(state: AccountState, asset: str) -> Decimal:
    """Total inventory less each order hold once; never subtract exchange free again."""
    total = state.balances.get(asset, Decimal(0))
    external = state.external_locked.get(asset, Decimal(0))
    holds = [h.amount for h in state.holds.values() if h.asset == asset]
    if any(not x.is_finite() or x < 0 for x in [total, external, *holds]):
        raise RiskRejected("INVALID_ACCOUNT_AMOUNTS")
    return total - external - sum(holds, Decimal(0))


class SpotRiskEngine:
    def __init__(self, policy: RiskPolicy | None = None):
        self.policy = policy or RiskPolicy()

    def evaluate(self, state: AccountState, command: PlaceOrderCommand, now_ms: int) -> Hold:
        if state.account_id != command.account_id:
            raise RiskRejected("ACCOUNT_MISMATCH")
        if state.control.get("new_risk_blocked"):
            raise NotReady("NEW_RISK_BLOCKED")
        checked = state.reconciliation.get("checked_at_ms")
        if state.status != "READY" or checked is None:
            raise NotReady("ACCOUNT_NOT_RECONCILED")
        if not 0 <= now_ms - int(checked) <= self.policy.max_reconciliation_age_ms:
            raise NotReady("ACCOUNT_SNAPSHOT_STALE")
        if state.reconciliation.get("stream_healthy", True) is not True:
            raise NotReady("PRIVATE_STREAM_UNHEALTHY")
        if state.reconciliation.get("unresolved", []):
            raise NotReady("UNRESOLVED_ACCOUNT_DIFFERENCES")
        if state.control.get("new_risk_blocked"):
            raise NotReady("NEW_RISK_BLOCKED")
        if command.side not in ("BUY", "SELL"):
            raise RiskRejected("UNSUPPORTED_SPOT_INTENT")
        if (
            not command.base_asset
            or not command.quote_asset
            or command.base_asset == command.quote_asset
        ):
            raise RiskRejected("INVALID_INSTRUMENT")
        if (
            not command.qty.is_finite()
            or not command.price.is_finite()
            or command.qty <= 0
            or command.price <= 0
        ):
            raise RiskRejected("INVALID_ORDER_AMOUNTS")
        if command.order_type not in ("LIMIT", "MARKET"):
            raise RiskRejected("UNSUPPORTED_ORDER_TYPE")
        if command.order_type == "LIMIT" and command.time_in_force != "GTC":
            raise RiskRejected("UNSUPPORTED_TIME_IN_FORCE")
        amount = command.qty
        asset = command.base_asset
        if command.side == "BUY":
            asset = command.quote_asset
            amount = command.qty * command.price * (Decimal(1) + self.policy.fee_rate)
        if available_balance(state, asset) < amount:
            raise RiskRejected("INSUFFICIENT_AVAILABLE_BALANCE")
        if command.side == "BUY":
            self._check_valuation(state, command, now_ms)
            self._check_exposure(state, command, now_ms)
        return Hold(asset, amount)

    def _check_valuation(self, state: AccountState, command: PlaceOrderCommand, now_ms: int):
        if not valuation_complete(state, now_ms, self.policy.max_mark_age_ms):
            raise NotReady("VALUATION_STALE_OR_MISSING")
        marks = state.reconciliation.get("marks", {})
        symbols = {symbol for symbol, qty in state.positions.items() if qty != 0}
        for order in state.orders.values():
            if state.balances.get(order.command.base_asset, Decimal(0)) != 0:
                symbols.add(order.command.symbol)
        for symbol in symbols:
            mark = marks.get(symbol)
            if mark is None or not 0 <= now_ms - int(mark["at_ms"]) <= self.policy.max_mark_age_ms:
                raise NotReady("VALUATION_STALE_OR_MISSING")
            price = Decimal(str(mark["price"]))
            if not price.is_finite() or price <= 0:
                raise NotReady("INVALID_VALUATION")

    def _check_exposure(self, state: AccountState, command: PlaceOrderCommand, now_ms: int):
        if self.policy.max_gross_notional is None and self.policy.max_symbol_notional is None:
            return
        instruments = {o.command.symbol: o.command for o in state.orders.values()}
        instruments[command.symbol] = command
        marks = state.reconciliation.get("marks", {})
        exposure: dict[str, Decimal] = {}
        represented_assets = {command.quote_asset}
        for symbol, instrument in instruments.items():
            if instrument.quote_asset != command.quote_asset:
                raise RiskRejected("UNSUPPORTED_CROSS_QUOTE_VALUATION")
            represented_assets.add(instrument.base_asset)
            quantity = state.balances.get(instrument.base_asset, Decimal(0))
            if quantity:
                mark = marks.get(symbol)
                if (
                    mark is None
                    or not 0 <= now_ms - int(mark["at_ms"]) <= self.policy.max_mark_age_ms
                ):
                    raise NotReady("VALUATION_STALE_OR_MISSING")
                price = Decimal(str(mark["price"]))
                if not price.is_finite() or price <= 0:
                    raise NotReady("INVALID_VALUATION")
                exposure[symbol] = quantity * price
        if any(
            qty != 0 and asset not in represented_assets for asset, qty in state.balances.items()
        ):
            raise NotReady("UNVALUED_ASSET")
        for order_id, order in state.orders.items():
            if order.command.side == "BUY" and order_id in state.holds:
                exposure[order.command.symbol] = (
                    exposure.get(order.command.symbol, Decimal(0))
                    + order.remaining_qty * order.command.price
                )
        exposure[command.symbol] = (
            exposure.get(command.symbol, Decimal(0)) + command.qty * command.price
        )
        if (
            self.policy.max_symbol_notional is not None
            and exposure[command.symbol] > self.policy.max_symbol_notional
        ):
            raise RiskRejected("SYMBOL_EXPOSURE_LIMIT")
        if (
            self.policy.max_gross_notional is not None
            and sum(exposure.values(), Decimal(0)) > self.policy.max_gross_notional
        ):
            raise RiskRejected("GROSS_EXPOSURE_LIMIT")


def evaluate(state: AccountState, command: PlaceOrderCommand, now_ms: int) -> Hold:
    return SpotRiskEngine().evaluate(state, command, now_ms)


def valuation_complete(state: AccountState, now_ms: int, max_age_ms: int) -> bool:
    """Every nonzero asset requires a finite, fresh USDT conversion mark."""
    marks = state.reconciliation.get("marks", {})
    symbols = {symbol for symbol, qty in state.positions.items() if qty != 0}
    symbols.update(
        asset + "USDT" for asset, qty in state.balances.items() if asset != "USDT" and qty != 0
    )
    try:
        for symbol in symbols:
            mark = marks.get(symbol)
            if mark is None or not 0 <= now_ms - int(mark["at_ms"]) <= max_age_ms:
                return False
            price = Decimal(str(mark["price"]))
            if not price.is_finite() or price <= 0:
                return False
    except (DecimalException, KeyError, TypeError, ValueError, OverflowError):
        return False
    return all(amount.is_finite() for amount in state.balances.values())
