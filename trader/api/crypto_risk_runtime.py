from __future__ import annotations

import os
from dataclasses import dataclass, field
from decimal import Decimal, InvalidOperation
from typing import Awaitable, Callable, Mapping

from trader.adapters.binance.crypto_risk_source import (
    BINANCE_USD_M_FUTURES_BASE_URL,
    BinanceFuturesRiskDataSource,
    BinanceFuturesRiskDataSourceConfig,
)
from trader.api.env_config import get_binance_recv_window
from trader.core.application.ports import BrokerPort
from trader.core.application.risk_engine import RejectionReason, RiskCheckResult, RiskLevel
from trader.core.domain.models.crypto_risk import CryptoRiskBudget
from trader.core.domain.models.signal import Signal
from trader.services.crypto_risk_snapshot import (
    CryptoRiskSnapshotProviderConfig,
    DataSourceCryptoRiskSnapshotProvider,
    build_crypto_pre_trade_risk_check,
)

CRYPTO_RISK_ENABLED_ENV = "CRYPTO_RISK_ENABLED"
CRYPTO_RISK_FUTURES_BASE_URL_ENV = "CRYPTO_RISK_FUTURES_BASE_URL"
CRYPTO_RISK_BASE_SYMBOLS_ENV = "CRYPTO_RISK_BASE_SYMBOLS"
CRYPTO_RISK_TOTAL_NOTIONAL_CAP_ENV = "CRYPTO_RISK_TOTAL_NOTIONAL_CAP"
CRYPTO_RISK_SYMBOL_NOTIONAL_CAPS_ENV = "CRYPTO_RISK_SYMBOL_NOTIONAL_CAPS"
CRYPTO_RISK_MAX_MARGIN_RATIO_ENV = "CRYPTO_RISK_MAX_MARGIN_RATIO"
CRYPTO_RISK_MIN_LIQUIDATION_BUFFER_RATIO_ENV = "CRYPTO_RISK_MIN_LIQUIDATION_BUFFER_RATIO"
CRYPTO_RISK_TIMEOUT_SECONDS_ENV = "CRYPTO_RISK_TIMEOUT_SECONDS"
CRYPTO_RISK_PROXY_URL_ENV = "CRYPTO_RISK_PROXY_URL"
CRYPTO_RISK_MAX_RETRIES_ENV = "CRYPTO_RISK_MAX_RETRIES"

_TRUE_VALUES = {"1", "true", "yes", "on"}
_FALSE_VALUES = {"0", "false", "no", "off"}


@dataclass(frozen=True, slots=True)
class CryptoRiskRuntimeConfig:
    enabled: bool = False
    futures_base_url: str = BINANCE_USD_M_FUTURES_BASE_URL
    base_symbols: tuple[str, ...] = ()
    risk_budget: CryptoRiskBudget = field(default_factory=CryptoRiskBudget)
    timeout_seconds: float = 10.0
    recv_window_ms: int = 5000
    proxy_url: str | None = None
    max_retries: int = 2


@dataclass(frozen=True, slots=True)
class CryptoRiskRuntimeComponents:
    source: BinanceFuturesRiskDataSource
    snapshot_provider: DataSourceCryptoRiskSnapshotProvider
    pre_trade_risk_check: Callable[[Signal], Awaitable[RiskCheckResult]]


def get_crypto_risk_runtime_config(
    env: Mapping[str, str] | None = None,
) -> CryptoRiskRuntimeConfig:
    source = env if env is not None else os.environ
    enabled = _parse_enabled(source.get(CRYPTO_RISK_ENABLED_ENV))
    if not enabled:
        return CryptoRiskRuntimeConfig(
            enabled=False, recv_window_ms=get_binance_recv_window(source)
        )

    return CryptoRiskRuntimeConfig(
        enabled=True,
        futures_base_url=_parse_base_url(source.get(CRYPTO_RISK_FUTURES_BASE_URL_ENV)),
        base_symbols=_parse_symbol_list(source.get(CRYPTO_RISK_BASE_SYMBOLS_ENV)),
        risk_budget=CryptoRiskBudget(
            symbol_notional_caps=_parse_symbol_decimal_map(
                source.get(CRYPTO_RISK_SYMBOL_NOTIONAL_CAPS_ENV),
                CRYPTO_RISK_SYMBOL_NOTIONAL_CAPS_ENV,
            ),
            total_notional_cap=_parse_decimal(
                source.get(CRYPTO_RISK_TOTAL_NOTIONAL_CAP_ENV),
                CRYPTO_RISK_TOTAL_NOTIONAL_CAP_ENV,
                default=Decimal("0"),
                min_value=Decimal("0"),
            ),
            max_margin_ratio=_parse_decimal(
                source.get(CRYPTO_RISK_MAX_MARGIN_RATIO_ENV),
                CRYPTO_RISK_MAX_MARGIN_RATIO_ENV,
                default=Decimal("0.80"),
                min_value=Decimal("0"),
            ),
            min_liquidation_buffer_ratio=_parse_decimal(
                source.get(CRYPTO_RISK_MIN_LIQUIDATION_BUFFER_RATIO_ENV),
                CRYPTO_RISK_MIN_LIQUIDATION_BUFFER_RATIO_ENV,
                default=Decimal("0"),
                min_value=Decimal("0"),
            ),
        ),
        timeout_seconds=_parse_float(
            source.get(CRYPTO_RISK_TIMEOUT_SECONDS_ENV),
            CRYPTO_RISK_TIMEOUT_SECONDS_ENV,
            default=10.0,
            min_value=0.001,
        ),
        recv_window_ms=get_binance_recv_window(source),
        proxy_url=_parse_optional_text(source.get(CRYPTO_RISK_PROXY_URL_ENV)),
        max_retries=_parse_int(
            source.get(CRYPTO_RISK_MAX_RETRIES_ENV),
            CRYPTO_RISK_MAX_RETRIES_ENV,
            default=2,
            min_value=1,
        ),
    )


def build_crypto_risk_runtime_components(
    *,
    broker: BrokerPort,
    api_key: str | None,
    secret_key: str | None,
    env: Mapping[str, str] | None = None,
    config: CryptoRiskRuntimeConfig | None = None,
) -> CryptoRiskRuntimeComponents | None:
    runtime_config = config or get_crypto_risk_runtime_config(env)
    if not runtime_config.enabled:
        return None

    if not api_key or not secret_key:
        raise ValueError("CRYPTO_RISK_ENABLED=true requires BINANCE_API_KEY and BINANCE_SECRET_KEY")

    source = BinanceFuturesRiskDataSource(
        BinanceFuturesRiskDataSourceConfig(
            api_key=api_key,
            secret_key=secret_key,
            base_url=runtime_config.futures_base_url,
            timeout=runtime_config.timeout_seconds,
            recv_window_ms=runtime_config.recv_window_ms,
            proxy_url=runtime_config.proxy_url,
            max_retries=runtime_config.max_retries,
        )
    )
    snapshot_provider = DataSourceCryptoRiskSnapshotProvider(
        source,
        config=CryptoRiskSnapshotProviderConfig(
            base_symbols=runtime_config.base_symbols,
            risk_budget=runtime_config.risk_budget,
        ),
    )
    pre_trade_risk_check = build_crypto_pre_trade_risk_check(
        broker=broker,
        snapshot_provider=snapshot_provider,
    )
    return CryptoRiskRuntimeComponents(
        source=source,
        snapshot_provider=snapshot_provider,
        pre_trade_risk_check=pre_trade_risk_check,
    )


def build_crypto_risk_setup_failure_check(
    reason: str,
) -> Callable[[Signal], Awaitable[RiskCheckResult]]:
    async def _reject(_signal: Signal) -> RiskCheckResult:
        return RiskCheckResult(
            passed=False,
            risk_level=RiskLevel.CRITICAL,
            rejection_reason=RejectionReason.RISK_SYSTEM_ERROR,
            message=f"Crypto risk runtime unavailable: {reason}",
        )

    return _reject


def _parse_enabled(raw: str | None) -> bool:
    if raw is None or str(raw).strip() == "":
        return False

    value = str(raw).strip().lower()
    if value in _TRUE_VALUES:
        return True
    if value in _FALSE_VALUES:
        return False
    raise ValueError(
        f"{CRYPTO_RISK_ENABLED_ENV} must be one of "
        f"{sorted(_TRUE_VALUES | _FALSE_VALUES)}, got {raw!r}"
    )


def _parse_base_url(raw: str | None) -> str:
    value = _parse_optional_text(raw)
    if value is None:
        return BINANCE_USD_M_FUTURES_BASE_URL
    return value.rstrip("/")


def _parse_symbol_list(raw: str | None) -> tuple[str, ...]:
    if raw is None or str(raw).strip() == "":
        return ()

    seen: set[str] = set()
    symbols: list[str] = []
    for item in str(raw).split(","):
        symbol = _normalize_symbol(item)
        if not symbol or symbol in seen:
            continue
        seen.add(symbol)
        symbols.append(symbol)
    return tuple(symbols)


def _parse_symbol_decimal_map(raw: str | None, env_name: str) -> dict[str, Decimal]:
    if raw is None or str(raw).strip() == "":
        return {}

    values: dict[str, Decimal] = {}
    for item in str(raw).split(","):
        part = item.strip()
        if not part:
            continue
        if "=" not in part:
            raise ValueError(f"{env_name} item must use SYMBOL=DECIMAL format: {part!r}")
        symbol_raw, value_raw = part.split("=", 1)
        symbol = _normalize_symbol(symbol_raw)
        if not symbol:
            raise ValueError(f"{env_name} contains empty symbol in item: {part!r}")
        values[symbol] = _parse_decimal(
            value_raw,
            env_name,
            default=Decimal("0"),
            min_value=Decimal("0"),
        )
    return values


def _parse_decimal(
    raw: str | None,
    env_name: str,
    *,
    default: Decimal,
    min_value: Decimal | None = None,
) -> Decimal:
    if raw is None or str(raw).strip() == "":
        return default

    try:
        value = Decimal(str(raw).strip())
    except (InvalidOperation, ValueError) as exc:
        raise ValueError(f"{env_name} must be a decimal, got {raw!r}") from exc

    if min_value is not None and value < min_value:
        raise ValueError(f"{env_name} must be >= {min_value}, got {value}")
    return value


def _parse_float(
    raw: str | None,
    env_name: str,
    *,
    default: float,
    min_value: float | None = None,
) -> float:
    if raw is None or str(raw).strip() == "":
        return default
    try:
        value = float(str(raw).strip())
    except ValueError as exc:
        raise ValueError(f"{env_name} must be a float, got {raw!r}") from exc
    if min_value is not None and value < min_value:
        raise ValueError(f"{env_name} must be >= {min_value}, got {value}")
    return value


def _parse_int(
    raw: str | None,
    env_name: str,
    *,
    default: int,
    min_value: int | None = None,
) -> int:
    if raw is None or str(raw).strip() == "":
        return default
    try:
        value = int(str(raw).strip())
    except ValueError as exc:
        raise ValueError(f"{env_name} must be an integer, got {raw!r}") from exc
    if min_value is not None and value < min_value:
        raise ValueError(f"{env_name} must be >= {min_value}, got {value}")
    return value


def _parse_optional_text(raw: str | None) -> str | None:
    if raw is None:
        return None
    value = str(raw).strip()
    return value or None


def _normalize_symbol(symbol: str) -> str:
    return symbol.upper().replace("-", "").replace("/", "").strip()
