from __future__ import annotations

from decimal import Decimal
from unittest.mock import AsyncMock, MagicMock

import pytest

from trader.api.crypto_risk_runtime import (
    CryptoRiskRuntimeComponents,
    CryptoRiskRuntimeConfig,
    CryptoRiskRuntimeManager,
)
from trader.core.application.risk_engine import RejectionReason, RiskCheckResult
from trader.core.domain.models.crypto_risk import (
    CryptoAccountRisk,
    CryptoInstrumentSpec,
    CryptoMarketType,
    CryptoPositionRisk,
    CryptoRiskBudget,
    LeverageBracket,
)
from trader.core.domain.models.signal import Signal, SignalType
from trader.storage.in_memory import get_storage


class FakeRiskSource:
    def __init__(self) -> None:
        self.start_count = 0
        self.close_count = 0

    async def start(self) -> None:
        self.start_count += 1

    async def close(self) -> None:
        self.close_count += 1


class ProbeRiskSource(FakeRiskSource):
    def __init__(self) -> None:
        super().__init__()
        self.calls: list[str] = []

    async def get_account_risk(self) -> CryptoAccountRisk:
        self.calls.append("account")
        return CryptoAccountRisk(
            equity=Decimal("1000"),
            available_balance=Decimal("800"),
            wallet_balance=Decimal("1000"),
            margin_balance=Decimal("1000"),
        )

    async def get_instrument_specs(
        self,
        symbols: set[str],
    ) -> dict[str, CryptoInstrumentSpec]:
        self.calls.append("instrument_specs")
        return {
            symbol: CryptoInstrumentSpec(
                symbol=symbol,
                market_type=CryptoMarketType.USD_M_FUTURES,
                price_tick=Decimal("0.10"),
                qty_step=Decimal("0.001"),
                min_qty=Decimal("0.001"),
                min_notional=Decimal("10"),
            )
            for symbol in symbols
        }

    async def get_leverage_brackets(self, symbols: set[str]) -> dict[str, list[LeverageBracket]]:
        self.calls.append("leverage_brackets")
        return {
            symbol: [
                LeverageBracket(
                    symbol=symbol,
                    notional_floor=Decimal("0"),
                    notional_cap=Decimal("50000"),
                    initial_leverage=Decimal("20"),
                    maint_margin_ratio=Decimal("0.004"),
                )
            ]
            for symbol in symbols
        }

    async def get_mark_prices(self, symbols: set[str]) -> dict[str, Decimal]:
        self.calls.append("mark_prices")
        return {symbol: Decimal("50000") for symbol in symbols}

    async def get_positions(self, symbols: set[str] | None = None) -> list[CryptoPositionRisk]:
        self.calls.append("positions")
        return [
            CryptoPositionRisk(
                symbol="BTCUSDT",
                qty=Decimal("0.01"),
                entry_price=Decimal("49000"),
                mark_price=Decimal("50000"),
                leverage=Decimal("10"),
            )
        ]

    async def get_open_orders(self, symbols: set[str] | None = None) -> list[object]:
        self.calls.append("open_orders")
        return []

    async def get_venue_health(self) -> str:
        self.calls.append("venue_health")
        return "HEALTHY"


class RejectingRiskSource(ProbeRiskSource):
    async def get_positions(self, symbols: set[str] | None = None) -> list[CryptoPositionRisk]:
        self.calls.append("positions")
        return []


def _component_builder(source: FakeRiskSource, check: MagicMock):
    def _build(**_kwargs) -> CryptoRiskRuntimeComponents:
        return CryptoRiskRuntimeComponents(
            source=source,
            snapshot_provider=MagicMock(),
            pre_trade_risk_check=check,
        )

    return _build


def _config() -> CryptoRiskRuntimeConfig:
    return CryptoRiskRuntimeConfig(
        enabled=True,
        execution_env="demo",
        futures_base_url="https://example.test",
        base_symbols=("BTCUSDT",),
        risk_budget=CryptoRiskBudget(total_notional_cap=Decimal("10000")),
    )


@pytest.mark.asyncio
async def test_runtime_manager_configure_wires_check_and_status() -> None:
    source = FakeRiskSource()
    initial_check = MagicMock()
    setter = MagicMock()
    manager = CryptoRiskRuntimeManager(
        pre_trade_setter=setter,
        component_builder=_component_builder(source, initial_check),
    )

    status = await manager.configure(
        broker=MagicMock(),
        api_key="key",
        secret_key="secret",
        config=_config(),
        updated_by="test",
    )

    assert status.enabled is True
    assert status.wired is True
    assert status.fail_closed is False
    assert status.risk_budget.total_notional_cap == Decimal("10000")
    assert source.start_count == 1
    setter.assert_called_once_with(initial_check)


@pytest.mark.asyncio
async def test_runtime_manager_hot_updates_budget_and_replaces_check_without_restart() -> None:
    source = FakeRiskSource()
    initial_check = MagicMock()
    setter = MagicMock()
    manager = CryptoRiskRuntimeManager(
        pre_trade_setter=setter,
        component_builder=_component_builder(source, initial_check),
    )
    await manager.configure(
        broker=MagicMock(),
        api_key="key",
        secret_key="secret",
        config=_config(),
        updated_by="test",
    )

    status = await manager.update_budget(
        CryptoRiskBudget(
            total_notional_cap=Decimal("25000"),
            symbol_notional_caps={"ETHUSDT": Decimal("5000")},
            symbol_clusters={"ETHUSDT": "ETH_BETA"},
            cluster_notional_caps={"ETH_BETA": Decimal("12000")},
            max_margin_ratio=Decimal("0.70"),
            min_liquidation_buffer_ratio=Decimal("0.05"),
        ),
        updated_by="operator",
    )

    assert status.wired is True
    assert status.updated_by == "operator"
    assert status.risk_budget.total_notional_cap == Decimal("25000")
    assert status.risk_budget.symbol_notional_caps == {"ETHUSDT": Decimal("5000")}
    assert status.risk_budget.symbol_clusters == {"ETHUSDT": "ETH_BETA"}
    assert status.risk_budget.cluster_notional_caps == {"ETH_BETA": Decimal("12000")}
    assert status.risk_budget.max_margin_ratio == Decimal("0.70")
    assert status.risk_budget.min_liquidation_buffer_ratio == Decimal("0.05")
    assert source.start_count == 1
    assert setter.call_count == 2
    assert setter.call_args_list[-1].args[0] is not initial_check


@pytest.mark.asyncio
async def test_runtime_manager_budget_update_requires_wired_runtime() -> None:
    manager = CryptoRiskRuntimeManager(pre_trade_setter=MagicMock())

    with pytest.raises(RuntimeError, match="not wired"):
        await manager.update_budget(
            CryptoRiskBudget(total_notional_cap=Decimal("1")),
            updated_by="operator",
        )


@pytest.mark.asyncio
async def test_runtime_manager_probe_requires_wired_runtime() -> None:
    manager = CryptoRiskRuntimeManager(pre_trade_setter=MagicMock())

    with pytest.raises(RuntimeError, match="not wired"):
        await manager.probe(symbols=("BTCUSDT",), requested_by="operator")


@pytest.mark.asyncio
async def test_runtime_manager_probe_checks_wired_source_read_only() -> None:
    source = ProbeRiskSource()
    manager = CryptoRiskRuntimeManager(
        pre_trade_setter=MagicMock(),
        component_builder=_component_builder(source, MagicMock()),
    )
    await manager.configure(
        broker=MagicMock(),
        api_key="key",
        secret_key="secret",
        config=_config(),
        updated_by="test",
    )

    result = await manager.probe(symbols=("btc/usdt",), requested_by="operator")

    assert result.ok is True
    assert result.read_only is True
    assert result.mode == "custom"
    assert result.execution_env == "demo"
    assert result.symbols == ("BTCUSDT",)
    assert result.requested_by == "operator"
    assert result.checks["account"].status == "passed"
    assert result.checks["instrument_specs"].status == "passed"
    assert result.checks["leverage_brackets"].status == "passed"
    assert result.checks["mark_prices"].details["mark_prices"] == {"BTCUSDT": "50000"}
    assert result.checks["positions"].details["count"] == 1
    assert result.checks["open_orders"].details["count"] == 0
    assert source.calls == [
        "venue_health",
        "mark_prices",
        "instrument_specs",
        "leverage_brackets",
        "account",
        "positions",
        "open_orders",
    ]


@pytest.mark.asyncio
async def test_runtime_manager_probe_labels_demo_source_without_testnet() -> None:
    source = ProbeRiskSource()
    manager = CryptoRiskRuntimeManager(
        pre_trade_setter=MagicMock(),
        component_builder=_component_builder(source, MagicMock()),
    )
    await manager.configure(
        broker=MagicMock(),
        api_key="key",
        secret_key="secret",
        config=CryptoRiskRuntimeConfig(
            enabled=True,
            execution_env="demo",
            futures_base_url="https://demo-api.binance.com/fapi",
            base_symbols=("BTCUSDT",),
            risk_budget=CryptoRiskBudget(total_notional_cap=Decimal("10000")),
        ),
        updated_by="test",
    )

    result = await manager.probe(symbols=("BTCUSDT",), requested_by="operator")

    assert result.mode == "demo"
    assert result.execution_env == "demo"


@pytest.mark.asyncio
async def test_runtime_pre_trade_rejection_writes_market_audit_event(monkeypatch) -> None:
    source = RejectingRiskSource()
    setter = MagicMock()
    manager = CryptoRiskRuntimeManager(pre_trade_setter=setter)
    monkeypatch.setattr(
        "trader.api.crypto_risk_runtime.BinanceFuturesRiskDataSource", lambda _c: source
    )

    await manager.configure(
        broker=_risk_engine_broker(),
        api_key="key",
        secret_key="secret",
        config=CryptoRiskRuntimeConfig(
            enabled=True,
            execution_env="demo",
            futures_base_url="https://example.test",
            base_symbols=("BTCUSDT",),
            risk_budget=CryptoRiskBudget(symbol_notional_caps={"BTCUSDT": Decimal("100")}),
        ),
        updated_by="test",
    )

    check = setter.call_args.args[0]
    result: RiskCheckResult = await check(
        Signal(
            signal_id="signal-audit-1",
            strategy_name="momentum",
            signal_type=SignalType.LONG,
            symbol="BTCUSDT",
            quantity=Decimal("1"),
            price=Decimal("50000"),
            metadata={
                "strategy_id": "strategy-live-1",
                "decision_trace_id": "decision-trace-1",
                "trace_id": "legacy-trace-ignored",
            },
        )
    )

    assert result.passed is False
    assert result.rejection_reason == RejectionReason.CRYPTO_OPEN_ORDER_EXPOSURE
    events = get_storage().list_events(
        stream_key="risk:crypto",
        event_type="crypto_risk.pre_trade_rejected",
        trace_id="decision-trace-1",
    )
    assert len(events) == 1
    payload = events[0]["payload"]
    assert payload["signal_id"] == "signal-audit-1"
    assert payload["decision_trace_id"] == "decision-trace-1"
    assert payload["strategy_id"] == "strategy-live-1"
    assert payload["strategy_name"] == "momentum"
    assert payload["symbol"] == "BTCUSDT"
    assert payload["signal_type"] == "LONG"
    assert payload["qty"] == "1"
    assert payload["price"] == "50000"
    assert payload["rejection_reason"] == "CRYPTO_OPEN_ORDER_EXPOSURE"
    assert payload["risk_level"] == "HIGH"
    assert payload["recommended_killswitch_level"] == 1
    assert payload["details"]["symbol_cap"] == "100"


@pytest.mark.asyncio
async def test_runtime_manager_fail_closed_closes_existing_source() -> None:
    source = FakeRiskSource()
    manager = CryptoRiskRuntimeManager(
        pre_trade_setter=MagicMock(),
        component_builder=_component_builder(source, MagicMock()),
    )
    await manager.configure(
        broker=MagicMock(),
        api_key="key",
        secret_key="secret",
        config=_config(),
        updated_by="test",
    )

    status = await manager.set_fail_closed("wiring failed", config=_config())

    assert status.fail_closed is True
    assert status.wired is False
    assert status.last_error == "wiring failed"
    assert source.close_count == 1


def _risk_engine_broker() -> MagicMock:
    account = MagicMock()
    account.total_equity = Decimal("100000")
    account.available_cash = Decimal("100000")
    broker = MagicMock()
    broker.get_account = AsyncMock(return_value=account)
    broker.get_positions = AsyncMock(return_value=[])
    return broker
