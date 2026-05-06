from decimal import Decimal

from trader.core.domain.models.crypto_risk import (
    CryptoAccountRisk,
    CryptoInstrumentSpec,
    CryptoMarketType,
    CryptoPositionRisk,
    CryptoRiskBudget,
    CryptoRiskSnapshot,
    OpenOrderRisk,
)
from trader.core.domain.models.market_risk import (
    AssetClass,
    MarketInstrumentSpec,
    MarketOpenOrderRisk,
    MarketPositionRisk,
    MarketRiskAuditEvent,
)
from trader.core.domain.models.order import OrderSide
from trader.core.domain.services.exchange_rule_guard import ExchangeRuleGuard
from trader.core.domain.services.open_order_exposure import OpenOrderExposureCalculator
from trader.core.domain.services.portfolio_exposure_aggregator import PortfolioExposureAggregator


def d(value: str) -> Decimal:
    return Decimal(value)


def test_market_instrument_spec_drives_exchange_rule_guard_without_crypto_type() -> None:
    spec = MarketInstrumentSpec(
        symbol="600000.SH",
        venue="sse",
        asset_class=AssetClass.CN_STOCK,
        price_tick=d("0.01"),
        qty_step=d("100"),
        min_qty=d("100"),
        min_notional=d("1000"),
        metadata={"lot_size": "100"},
    )

    result = ExchangeRuleGuard().check_order(
        spec=spec,
        side=OrderSide.BUY,
        qty=d("250"),
        price=d("10.239"),
    )

    assert result.ok is True
    assert result.normalized_qty == d("200")
    assert result.normalized_price == d("10.23")
    assert result.notional == d("2046.00")


def test_crypto_risk_snapshot_can_project_to_market_risk_snapshot() -> None:
    crypto_snapshot = CryptoRiskSnapshot(
        account=CryptoAccountRisk(
            equity=d("1000"),
            available_balance=d("800"),
            wallet_balance=d("1000"),
            margin_balance=d("950"),
        ),
        instrument_specs={
            "BTCUSDT": CryptoInstrumentSpec(
                symbol="BTCUSDT",
                market_type=CryptoMarketType.USD_M_FUTURES,
                price_tick=d("0.10"),
                qty_step=d("0.001"),
                min_qty=d("0.001"),
                min_notional=d("10"),
            )
        },
        leverage_brackets={},
        positions=[
            CryptoPositionRisk(
                symbol="BTCUSDT",
                qty=d("0.1"),
                entry_price=d("60000"),
                mark_price=d("61000"),
                leverage=d("5"),
                liquidation_price=d("50000"),
            )
        ],
        open_orders=[
            OpenOrderRisk(
                cl_ord_id="open-1",
                symbol="BTCUSDT",
                side=OrderSide.BUY,
                qty=d("0.05"),
                filled_qty=d("0.01"),
                price=d("60500"),
            )
        ],
        mark_prices={"BTCUSDT": d("61000")},
        risk_budget=CryptoRiskBudget(
            symbol_notional_caps={"BTCUSDT": d("10000")},
            symbol_clusters={"BTCUSDT": "BTC_BETA"},
            cluster_notional_caps={"BTC_BETA": d("20000")},
            total_notional_cap=d("30000"),
        ),
    )

    market_snapshot = crypto_snapshot.to_market_snapshot(venue="binance", account_id="acct-1")

    assert market_snapshot.account.asset_class == AssetClass.CRYPTO
    assert market_snapshot.account.available_cash == d("800")
    assert market_snapshot.instrument_specs["BTCUSDT"].asset_class == AssetClass.CRYPTO
    assert market_snapshot.positions[0].risk_price == d("61000")
    assert market_snapshot.positions[0].metadata["liquidation_price"] == d("50000")
    assert market_snapshot.open_orders[0].remaining_qty == d("0.04")
    assert market_snapshot.risk_budget.symbol_groups["BTCUSDT"] == "BTC_BETA"
    assert market_snapshot.risk_budget.group_notional_caps["BTC_BETA"] == d("20000")


def test_market_risk_audit_event_uses_platform_level_schema() -> None:
    event = MarketRiskAuditEvent(
        event_type="risk.pre_trade_rejected",
        trace_id="trace-1",
        ts_ms=1710000000000,
        asset_class=AssetClass.CRYPTO,
        venue="binance",
        account_id="acct-1",
        payload={"reason": "limit"},
    )

    assert event.stream_key == "risk:market"
    assert event.schema_version == 1
    assert event.to_record()["asset_class"] == "crypto"
    assert event.to_record()["payload"] == {"reason": "limit"}


def test_open_order_exposure_accepts_market_risk_dtos() -> None:
    position = MarketPositionRisk(
        symbol="600000.SH",
        venue="sse",
        asset_class=AssetClass.CN_STOCK,
        qty=d("200"),
        entry_price=d("9.80"),
        risk_price=d("10"),
    )
    open_order = MarketOpenOrderRisk(
        cl_ord_id="cn-open-1",
        symbol="600000.SH",
        venue="sse",
        asset_class=AssetClass.CN_STOCK,
        side=OrderSide.BUY,
        qty=d("100"),
        filled_qty=d("0"),
        price=d("10.10"),
    )

    exposure = OpenOrderExposureCalculator().calculate_symbol_exposure(
        symbol="600000.SH",
        positions=[position],
        open_orders=[open_order],
        mark_price=d("10"),
    )

    assert exposure.current_qty == d("200")
    assert exposure.current_notional == d("2000")
    assert exposure.pending_open_qty == d("100")
    assert exposure.pending_open_notional == d("1010.00")
    assert exposure.total_risk_notional == d("3010.00")


def test_portfolio_exposure_aggregator_accepts_market_group_mapping() -> None:
    exposures = PortfolioExposureAggregator().calculate_cluster_exposures(
        positions=[
            MarketPositionRisk(
                symbol="600000.SH",
                venue="sse",
                asset_class=AssetClass.CN_STOCK,
                qty=d("200"),
                entry_price=d("9.80"),
                risk_price=d("10"),
            )
        ],
        open_orders=[
            MarketOpenOrderRisk(
                cl_ord_id="cn-open-1",
                symbol="600000.SH",
                venue="sse",
                asset_class=AssetClass.CN_STOCK,
                side=OrderSide.BUY,
                qty=d("100"),
                filled_qty=d("0"),
                price=d("10.10"),
            )
        ],
        mark_prices={"600000.SH": d("10")},
        symbol_clusters={"600000.SH": "BANK"},
    )

    assert exposures["BANK"].symbols == ("600000.SH",)
    assert exposures["BANK"].total_risk_notional == d("3010.00")
