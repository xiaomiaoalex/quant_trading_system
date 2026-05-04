from __future__ import annotations

from decimal import Decimal
from unittest.mock import MagicMock

import pytest

from trader.api.crypto_risk_runtime import (
    CryptoRiskRuntimeComponents,
    CryptoRiskRuntimeConfig,
    CryptoRiskRuntimeManager,
)
from trader.core.domain.models.crypto_risk import CryptoRiskBudget


class FakeRiskSource:
    def __init__(self) -> None:
        self.start_count = 0
        self.close_count = 0

    async def start(self) -> None:
        self.start_count += 1

    async def close(self) -> None:
        self.close_count += 1


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
            max_margin_ratio=Decimal("0.70"),
            min_liquidation_buffer_ratio=Decimal("0.05"),
        ),
        updated_by="operator",
    )

    assert status.wired is True
    assert status.updated_by == "operator"
    assert status.risk_budget.total_notional_cap == Decimal("25000")
    assert status.risk_budget.symbol_notional_caps == {"ETHUSDT": Decimal("5000")}
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
