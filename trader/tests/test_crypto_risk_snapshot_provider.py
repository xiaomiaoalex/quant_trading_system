from decimal import Decimal

import pytest

from trader.core.domain.models.crypto_risk import (
    CryptoAccountRisk,
    CryptoInstrumentSpec,
    CryptoMarketType,
    CryptoPositionRisk,
    CryptoRiskBudget,
    LeverageBracket,
    OpenOrderRisk,
)
from trader.core.domain.models.order import OrderSide
from trader.core.domain.models.signal import Signal, SignalType
from trader.services.crypto_risk_snapshot import (
    CryptoRiskSnapshotProviderConfig,
    CryptoRiskSnapshotUnavailable,
    DataSourceCryptoRiskSnapshotProvider,
)


def d(value: str) -> Decimal:
    return Decimal(value)


def account() -> CryptoAccountRisk:
    return CryptoAccountRisk(
        equity=d("1000"),
        available_balance=d("800"),
        wallet_balance=d("1000"),
        margin_balance=d("1000"),
    )


def spec(symbol: str = "BTCUSDT") -> CryptoInstrumentSpec:
    return CryptoInstrumentSpec(
        symbol=symbol,
        market_type=CryptoMarketType.USD_M_FUTURES,
        price_tick=d("0.10"),
        qty_step=d("0.001"),
        min_qty=d("0.001"),
        min_notional=d("10"),
    )


def bracket(symbol: str = "BTCUSDT") -> LeverageBracket:
    return LeverageBracket(
        symbol=symbol,
        notional_floor=d("0"),
        notional_cap=d("50000"),
        initial_leverage=d("20"),
        maint_margin_ratio=d("0.004"),
    )


class FakeCryptoRiskDataSource:
    def __init__(self) -> None:
        self.account = account()
        self.specs = {"BTCUSDT": spec(), "ETHUSDT": spec("ETHUSDT")}
        self.brackets = {"BTCUSDT": [bracket()], "ETHUSDT": [bracket("ETHUSDT")]}
        self.positions = [
            CryptoPositionRisk(
                symbol="ETHUSDT",
                qty=d("2"),
                entry_price=d("1000"),
                mark_price=d("1100"),
                leverage=d("5"),
            )
        ]
        self.open_orders = [
            OpenOrderRisk(
                cl_ord_id="eth-open",
                symbol="ETHUSDT",
                side=OrderSide.BUY,
                qty=d("1"),
                filled_qty=d("0"),
                price=d("1110"),
            )
        ]
        self.mark_prices = {"BTCUSDT": d("20000"), "ETHUSDT": d("1100")}
        self.requested_mark_symbols: set[str] = set()

    async def get_account_risk(self) -> CryptoAccountRisk:
        return self.account

    async def get_positions(self, symbols: set[str] | None = None) -> list[CryptoPositionRisk]:
        return list(self.positions)

    async def get_open_orders(self, symbols: set[str] | None = None) -> list[OpenOrderRisk]:
        return list(self.open_orders)

    async def get_instrument_specs(self, symbols: set[str]) -> dict[str, CryptoInstrumentSpec]:
        return {symbol: self.specs[symbol] for symbol in symbols if symbol in self.specs}

    async def get_leverage_brackets(self, symbols: set[str]) -> dict[str, list[LeverageBracket]]:
        return {symbol: self.brackets[symbol] for symbol in symbols if symbol in self.brackets}

    async def get_mark_prices(self, symbols: set[str]) -> dict[str, Decimal]:
        self.requested_mark_symbols = set(symbols)
        return {
            symbol: self.mark_prices[symbol] for symbol in symbols if symbol in self.mark_prices
        }

    async def get_venue_health(self) -> str:
        return "HEALTHY"


def long_signal(symbol: str = "BTCUSDT") -> Signal:
    return Signal(
        signal_type=SignalType.LONG,
        symbol=symbol,
        price=d("0"),
        quantity=d("0.1"),
    )


@pytest.mark.asyncio
async def test_snapshot_provider_builds_portfolio_wide_snapshot() -> None:
    source = FakeCryptoRiskDataSource()
    provider = DataSourceCryptoRiskSnapshotProvider(
        source,
        config=CryptoRiskSnapshotProviderConfig(
            risk_budget=CryptoRiskBudget(total_notional_cap=d("100000"))
        ),
    )

    snapshot = await provider.build(long_signal())

    assert snapshot.account.available_balance == d("800")
    assert set(snapshot.instrument_specs) == {"BTCUSDT", "ETHUSDT"}
    assert set(snapshot.leverage_brackets) == {"BTCUSDT", "ETHUSDT"}
    assert snapshot.positions == source.positions
    assert snapshot.open_orders == source.open_orders
    assert snapshot.mark_prices["BTCUSDT"] == d("20000")
    assert snapshot.mark_prices["ETHUSDT"] == d("1100")
    assert source.requested_mark_symbols == {"BTCUSDT", "ETHUSDT"}


@pytest.mark.asyncio
async def test_snapshot_provider_fails_closed_when_portfolio_symbol_mark_missing() -> None:
    source = FakeCryptoRiskDataSource()
    source.mark_prices.pop("ETHUSDT")
    provider = DataSourceCryptoRiskSnapshotProvider(source)

    with pytest.raises(CryptoRiskSnapshotUnavailable, match="mark price"):
        await provider.build(long_signal())


@pytest.mark.asyncio
async def test_snapshot_provider_fails_closed_when_target_bracket_missing() -> None:
    source = FakeCryptoRiskDataSource()
    source.brackets.pop("BTCUSDT")
    provider = DataSourceCryptoRiskSnapshotProvider(source)

    with pytest.raises(CryptoRiskSnapshotUnavailable, match="leverage bracket"):
        await provider.build(long_signal())
