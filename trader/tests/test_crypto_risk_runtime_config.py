from decimal import Decimal
from unittest.mock import MagicMock

import pytest

from trader.api.crypto_risk_runtime import (
    CRYPTO_RISK_BASE_SYMBOLS_ENV,
    CRYPTO_RISK_CLUSTER_NOTIONAL_CAPS_ENV,
    CRYPTO_RISK_ENABLED_ENV,
    CRYPTO_RISK_FUTURES_BASE_URL_ENV,
    CRYPTO_RISK_MAX_MARGIN_RATIO_ENV,
    CRYPTO_RISK_MIN_LIQUIDATION_BUFFER_RATIO_ENV,
    CRYPTO_RISK_SYMBOL_CLUSTERS_ENV,
    CRYPTO_RISK_SYMBOL_NOTIONAL_CAPS_ENV,
    CRYPTO_RISK_TOTAL_NOTIONAL_CAP_ENV,
    build_crypto_risk_setup_failure_check,
    build_crypto_risk_runtime_components,
    get_crypto_risk_runtime_config,
)
from trader.core.application.risk_engine import RejectionReason, RiskLevel
from trader.core.domain.models.signal import Signal, SignalType


def test_crypto_risk_runtime_config_is_disabled_by_default() -> None:
    config = get_crypto_risk_runtime_config({})

    assert config.enabled is False
    assert config.base_symbols == ()
    assert config.risk_budget.total_notional_cap == Decimal("0")
    assert config.risk_budget.max_margin_ratio == Decimal("0.80")


def test_crypto_risk_runtime_config_parses_enabled_budget_and_symbols() -> None:
    config = get_crypto_risk_runtime_config(
        {
            CRYPTO_RISK_ENABLED_ENV: "true",
            CRYPTO_RISK_FUTURES_BASE_URL_ENV: "https://testnet.binancefuture.com/",
            CRYPTO_RISK_BASE_SYMBOLS_ENV: " btc/usdt, ETH-USDT, btcusdt ",
            CRYPTO_RISK_TOTAL_NOTIONAL_CAP_ENV: "25000.5",
            CRYPTO_RISK_SYMBOL_NOTIONAL_CAPS_ENV: "btcusdt=10000, ETH-USDT=5000.25",
            CRYPTO_RISK_SYMBOL_CLUSTERS_ENV: "btcusdt=BTC_BETA, eth-usdt=ETH_BETA",
            CRYPTO_RISK_CLUSTER_NOTIONAL_CAPS_ENV: "BTC_BETA=15000, ETH_BETA=7500",
            CRYPTO_RISK_MAX_MARGIN_RATIO_ENV: "0.65",
            CRYPTO_RISK_MIN_LIQUIDATION_BUFFER_RATIO_ENV: "0.08",
        }
    )

    assert config.enabled is True
    assert config.futures_base_url == "https://testnet.binancefuture.com"
    assert config.base_symbols == ("BTCUSDT", "ETHUSDT")
    assert config.risk_budget.total_notional_cap == Decimal("25000.5")
    assert config.risk_budget.symbol_notional_caps == {
        "BTCUSDT": Decimal("10000"),
        "ETHUSDT": Decimal("5000.25"),
    }
    assert config.risk_budget.symbol_clusters == {
        "BTCUSDT": "BTC_BETA",
        "ETHUSDT": "ETH_BETA",
    }
    assert config.risk_budget.cluster_notional_caps == {
        "BTC_BETA": Decimal("15000"),
        "ETH_BETA": Decimal("7500"),
    }
    assert config.risk_budget.max_margin_ratio == Decimal("0.65")
    assert config.risk_budget.min_liquidation_buffer_ratio == Decimal("0.08")


@pytest.mark.parametrize(
    ("env", "match"),
    [
        ({CRYPTO_RISK_ENABLED_ENV: "maybe"}, "CRYPTO_RISK_ENABLED"),
        (
            {
                CRYPTO_RISK_ENABLED_ENV: "true",
                CRYPTO_RISK_TOTAL_NOTIONAL_CAP_ENV: "not-a-decimal",
            },
            "CRYPTO_RISK_TOTAL_NOTIONAL_CAP",
        ),
        (
            {
                CRYPTO_RISK_ENABLED_ENV: "true",
                CRYPTO_RISK_SYMBOL_NOTIONAL_CAPS_ENV: "BTCUSDT",
            },
            "CRYPTO_RISK_SYMBOL_NOTIONAL_CAPS",
        ),
    ],
)
def test_crypto_risk_runtime_config_rejects_invalid_explicit_values(
    env: dict[str, str],
    match: str,
) -> None:
    with pytest.raises(ValueError, match=match):
        get_crypto_risk_runtime_config(env)


def test_crypto_risk_runtime_components_are_none_when_disabled() -> None:
    components = build_crypto_risk_runtime_components(
        broker=MagicMock(),
        api_key="key",
        secret_key="secret",
        env={CRYPTO_RISK_ENABLED_ENV: "false"},
    )

    assert components is None


def test_crypto_risk_runtime_components_wire_source_provider_and_check() -> None:
    components = build_crypto_risk_runtime_components(
        broker=MagicMock(),
        api_key="key",
        secret_key="secret",
        env={
            CRYPTO_RISK_ENABLED_ENV: "yes",
            CRYPTO_RISK_FUTURES_BASE_URL_ENV: "https://example.test",
            CRYPTO_RISK_BASE_SYMBOLS_ENV: "BTCUSDT",
            CRYPTO_RISK_TOTAL_NOTIONAL_CAP_ENV: "10000",
        },
    )

    assert components is not None
    assert components.source._config.api_key == "key"
    assert components.source._config.secret_key == "secret"
    assert components.source._config.base_url == "https://example.test"
    assert components.snapshot_provider._config.base_symbols == ("BTCUSDT",)
    assert components.snapshot_provider._config.risk_budget.total_notional_cap == Decimal("10000")
    assert callable(components.pre_trade_risk_check)


def test_crypto_risk_runtime_components_require_credentials_when_enabled() -> None:
    with pytest.raises(ValueError, match="BINANCE_API_KEY"):
        build_crypto_risk_runtime_components(
            broker=MagicMock(),
            api_key="",
            secret_key="secret",
            env={CRYPTO_RISK_ENABLED_ENV: "true"},
        )


@pytest.mark.asyncio
async def test_crypto_risk_setup_failure_check_rejects_fail_closed() -> None:
    check = build_crypto_risk_setup_failure_check("missing credentials")

    result = await check(
        Signal(
            signal_type=SignalType.LONG,
            symbol="BTCUSDT",
            quantity=Decimal("0.01"),
            price=Decimal("50000"),
        )
    )

    assert result.passed is False
    assert result.risk_level is RiskLevel.CRITICAL
    assert result.rejection_reason is RejectionReason.RISK_SYSTEM_ERROR
    assert "missing credentials" in result.message
