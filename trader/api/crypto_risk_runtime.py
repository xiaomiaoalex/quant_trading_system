from __future__ import annotations

import asyncio
import os
import time
from dataclasses import dataclass, field, replace
from datetime import datetime, timezone
from decimal import Decimal, InvalidOperation
from typing import Any, Awaitable, Callable, Mapping

from trader.adapters.binance.crypto_risk_source import (
    BINANCE_USD_M_FUTURES_BASE_URL,
    BinanceFuturesRiskDataSource,
    BinanceFuturesRiskDataSourceConfig,
)
from trader.api.env_config import get_binance_env, get_binance_recv_window
from trader.core.application.ports import BrokerPort
from trader.core.application.risk_engine import RejectionReason, RiskCheckResult, RiskLevel
from trader.core.domain.models.crypto_risk import CryptoRiskBudget
from trader.core.domain.models.signal import Signal
from trader.services.crypto_pre_trade_risk_audit import build_audited_crypto_pre_trade_risk_check
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
CRYPTO_RISK_SYMBOL_CLUSTERS_ENV = "CRYPTO_RISK_SYMBOL_CLUSTERS"
CRYPTO_RISK_CLUSTER_NOTIONAL_CAPS_ENV = "CRYPTO_RISK_CLUSTER_NOTIONAL_CAPS"
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
    execution_env: str = "demo"
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


@dataclass(frozen=True, slots=True)
class CryptoRiskRuntimeStatusData:
    enabled: bool = False
    wired: bool = False
    fail_closed: bool = False
    execution_env: str = "demo"
    futures_base_url: str | None = None
    base_symbols: tuple[str, ...] = ()
    risk_budget: CryptoRiskBudget = field(default_factory=CryptoRiskBudget)
    last_error: str | None = None
    updated_at: str | None = None
    updated_by: str | None = None


@dataclass(frozen=True, slots=True)
class CryptoRiskProbeCheck:
    status: str
    latency_ms: float
    message: str
    details: dict[str, Any] = field(default_factory=dict)


@dataclass(frozen=True, slots=True)
class CryptoRiskProbeResult:
    ok: bool
    read_only: bool
    mode: str
    execution_env: str
    futures_base_url: str | None
    symbols: tuple[str, ...]
    requested_by: str
    started_at: str
    finished_at: str
    duration_ms: float
    checks: dict[str, CryptoRiskProbeCheck]


ComponentBuilder = Callable[..., CryptoRiskRuntimeComponents | None]
PreTradeSetter = Callable[[Callable[[Signal], Awaitable[RiskCheckResult]] | None], None]


class CryptoRiskRuntimeManager:
    def __init__(
        self,
        *,
        pre_trade_setter: PreTradeSetter | None = None,
        component_builder: ComponentBuilder | None = None,
    ) -> None:
        self._pre_trade_setter = pre_trade_setter
        self._component_builder = component_builder or build_crypto_risk_runtime_components
        self._config = CryptoRiskRuntimeConfig()
        self._components: CryptoRiskRuntimeComponents | None = None
        self._broker: BrokerPort | None = None
        self._status = CryptoRiskRuntimeStatusData(risk_budget=self._config.risk_budget)
        self._lock = asyncio.Lock()

    def bind_pre_trade_setter(self, setter: PreTradeSetter) -> None:
        self._pre_trade_setter = setter

    def status(self) -> CryptoRiskRuntimeStatusData:
        return self._status

    async def configure(
        self,
        *,
        broker: BrokerPort,
        api_key: str | None,
        secret_key: str | None,
        config: CryptoRiskRuntimeConfig,
        updated_by: str = "lifespan",
    ) -> CryptoRiskRuntimeStatusData:
        async with self._lock:
            if not config.enabled:
                old_components = self._components
                self._config = config
                self._broker = None
                self._components = None
                self._status = self._build_status(
                    config=config,
                    wired=False,
                    fail_closed=False,
                    last_error=None,
                    updated_by=updated_by,
                )
                self._apply_pre_trade_check(None)
                if old_components is not None:
                    await old_components.source.close()
                return self._status

            components = self._component_builder(
                broker=broker,
                api_key=api_key,
                secret_key=secret_key,
                config=config,
            )
            if components is None:
                raise RuntimeError("crypto risk runtime returned no components")

            await components.source.start()
            old_components = self._components
            self._config = config
            self._broker = broker
            self._components = components
            self._apply_pre_trade_check(components.pre_trade_risk_check)
            self._status = self._build_status(
                config=config,
                wired=True,
                fail_closed=False,
                last_error=None,
                updated_by=updated_by,
            )

            if old_components is not None and old_components.source is not components.source:
                await old_components.source.close()

            return self._status

    async def set_fail_closed(
        self,
        reason: str,
        *,
        config: CryptoRiskRuntimeConfig | None = None,
        updated_by: str = "lifespan",
    ) -> CryptoRiskRuntimeStatusData:
        async with self._lock:
            runtime_config = config or self._config
            old_components = self._components
            self._config = runtime_config
            self._components = None
            self._apply_pre_trade_check(
                build_audited_crypto_pre_trade_risk_check(
                    build_crypto_risk_setup_failure_check(reason)
                )
            )
            self._status = self._build_status(
                config=runtime_config,
                wired=False,
                fail_closed=True,
                last_error=reason,
                updated_by=updated_by,
            )
            if old_components is not None:
                await old_components.source.close()
            return self._status

    async def update_budget(
        self,
        risk_budget: CryptoRiskBudget,
        *,
        updated_by: str,
    ) -> CryptoRiskRuntimeStatusData:
        async with self._lock:
            if not self._config.enabled or self._components is None or self._broker is None:
                raise RuntimeError("crypto risk runtime is not wired")

            new_config = replace(self._config, risk_budget=risk_budget)
            snapshot_provider = DataSourceCryptoRiskSnapshotProvider(
                self._components.source,
                config=CryptoRiskSnapshotProviderConfig(
                    base_symbols=new_config.base_symbols,
                    risk_budget=risk_budget,
                ),
            )
            raw_pre_trade_risk_check = build_crypto_pre_trade_risk_check(
                broker=self._broker,
                snapshot_provider=snapshot_provider,
            )
            pre_trade_risk_check = build_audited_crypto_pre_trade_risk_check(
                raw_pre_trade_risk_check
            )
            self._components = CryptoRiskRuntimeComponents(
                source=self._components.source,
                snapshot_provider=snapshot_provider,
                pre_trade_risk_check=pre_trade_risk_check,
            )
            self._config = new_config
            self._apply_pre_trade_check(pre_trade_risk_check)
            self._status = self._build_status(
                config=new_config,
                wired=True,
                fail_closed=False,
                last_error=None,
                updated_by=updated_by,
            )
            return self._status

    async def probe(
        self,
        *,
        symbols: tuple[str, ...] | None = None,
        requested_by: str,
    ) -> CryptoRiskProbeResult:
        async with self._lock:
            if not self._config.enabled or self._components is None:
                raise RuntimeError("crypto risk runtime is not wired")
            source = self._components.source
            config = self._config

        requested_symbols = _normalize_symbol_tuple(symbols or config.base_symbols)
        if not requested_symbols:
            requested_symbols = ("BTCUSDT",)
        symbol_set = set(requested_symbols)

        started = datetime.now(timezone.utc)
        started_perf = time.perf_counter()
        checks: dict[str, CryptoRiskProbeCheck] = {}

        checks["venue_health"] = await _probe_check(
            "venue health",
            lambda: _probe_venue_health(source),
        )
        checks["mark_prices"] = await _probe_check(
            "mark prices",
            lambda: _probe_mark_prices(source, symbol_set),
        )
        checks["instrument_specs"] = await _probe_check(
            "instrument specs",
            lambda: _probe_instrument_specs(source, symbol_set),
        )
        checks["leverage_brackets"] = await _probe_check(
            "leverage brackets",
            lambda: _probe_leverage_brackets(source, symbol_set),
        )
        checks["account"] = await _probe_check(
            "account risk",
            lambda: _probe_account(source),
        )
        checks["positions"] = await _probe_check(
            "positions",
            lambda: _probe_positions(source),
        )
        checks["open_orders"] = await _probe_check(
            "open orders",
            lambda: _probe_open_orders(source),
        )

        finished = datetime.now(timezone.utc)
        return CryptoRiskProbeResult(
            ok=all(check.status == "passed" for check in checks.values()),
            read_only=True,
            mode=_crypto_risk_mode(config.futures_base_url),
            execution_env=config.execution_env,
            futures_base_url=config.futures_base_url,
            symbols=requested_symbols,
            requested_by=requested_by,
            started_at=started.isoformat(),
            finished_at=finished.isoformat(),
            duration_ms=round((time.perf_counter() - started_perf) * 1000, 3),
            checks=checks,
        )

    async def close(self) -> None:
        async with self._lock:
            components = self._components
            self._components = None
            self._broker = None
            self._apply_pre_trade_check(None)
            self._status = self._build_status(
                config=self._config,
                wired=False,
                fail_closed=False,
                last_error=None,
                updated_by="shutdown",
            )
            if components is not None:
                await components.source.close()

    def reset_for_tests(self) -> None:
        self._config = CryptoRiskRuntimeConfig()
        self._components = None
        self._broker = None
        self._pre_trade_setter = None
        self._status = CryptoRiskRuntimeStatusData(risk_budget=self._config.risk_budget)
        self._lock = asyncio.Lock()

    def set_runtime_for_tests(
        self,
        *,
        risk_budget: CryptoRiskBudget,
        source: Any | None = None,
        base_symbols: tuple[str, ...] = (),
        futures_base_url: str = BINANCE_USD_M_FUTURES_BASE_URL,
        execution_env: str = "demo",
        pre_trade_setter: PreTradeSetter | None = None,
    ) -> None:
        class _NoopSource:
            async def start(self) -> None:
                return None

            async def close(self) -> None:
                return None

        if pre_trade_setter is not None:
            self._pre_trade_setter = pre_trade_setter
        runtime_source = source or _NoopSource()
        self._config = CryptoRiskRuntimeConfig(
            enabled=True,
            execution_env=execution_env,
            futures_base_url=futures_base_url,
            base_symbols=base_symbols,
            risk_budget=risk_budget,
        )
        self._broker = object()  # type: ignore[assignment]
        self._components = CryptoRiskRuntimeComponents(
            source=runtime_source,  # type: ignore[arg-type]
            snapshot_provider=DataSourceCryptoRiskSnapshotProvider(runtime_source),  # type: ignore[arg-type]
            pre_trade_risk_check=build_crypto_risk_setup_failure_check("test runtime"),
        )
        self._status = self._build_status(
            config=self._config,
            wired=True,
            fail_closed=False,
            last_error=None,
            updated_by="test",
        )

    def _apply_pre_trade_check(
        self,
        check: Callable[[Signal], Awaitable[RiskCheckResult]] | None,
    ) -> None:
        if self._pre_trade_setter is not None:
            self._pre_trade_setter(check)

    def _build_status(
        self,
        *,
        config: CryptoRiskRuntimeConfig,
        wired: bool,
        fail_closed: bool,
        last_error: str | None,
        updated_by: str | None,
    ) -> CryptoRiskRuntimeStatusData:
        return CryptoRiskRuntimeStatusData(
            enabled=config.enabled,
            wired=wired,
            fail_closed=fail_closed,
            execution_env=config.execution_env,
            futures_base_url=config.futures_base_url if config.enabled else None,
            base_symbols=config.base_symbols,
            risk_budget=config.risk_budget,
            last_error=last_error,
            updated_at=datetime.now(timezone.utc).isoformat(),
            updated_by=updated_by,
        )


def get_crypto_risk_runtime_config(
    env: Mapping[str, str] | None = None,
) -> CryptoRiskRuntimeConfig:
    source = env if env is not None else os.environ
    enabled = _parse_enabled(source.get(CRYPTO_RISK_ENABLED_ENV))
    if not enabled:
        return CryptoRiskRuntimeConfig(
            enabled=False,
            execution_env=get_binance_env(source),
            recv_window_ms=get_binance_recv_window(source),
        )

    return CryptoRiskRuntimeConfig(
        enabled=True,
        execution_env=get_binance_env(source),
        futures_base_url=_parse_base_url(source.get(CRYPTO_RISK_FUTURES_BASE_URL_ENV)),
        base_symbols=_parse_symbol_list(source.get(CRYPTO_RISK_BASE_SYMBOLS_ENV)),
        risk_budget=CryptoRiskBudget(
            symbol_notional_caps=_parse_symbol_decimal_map(
                source.get(CRYPTO_RISK_SYMBOL_NOTIONAL_CAPS_ENV),
                CRYPTO_RISK_SYMBOL_NOTIONAL_CAPS_ENV,
            ),
            symbol_clusters=_parse_symbol_text_map(
                source.get(CRYPTO_RISK_SYMBOL_CLUSTERS_ENV),
                CRYPTO_RISK_SYMBOL_CLUSTERS_ENV,
            ),
            cluster_notional_caps=_parse_text_decimal_map(
                source.get(CRYPTO_RISK_CLUSTER_NOTIONAL_CAPS_ENV),
                CRYPTO_RISK_CLUSTER_NOTIONAL_CAPS_ENV,
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
    raw_pre_trade_risk_check = build_crypto_pre_trade_risk_check(
        broker=broker,
        snapshot_provider=snapshot_provider,
    )
    pre_trade_risk_check = build_audited_crypto_pre_trade_risk_check(raw_pre_trade_risk_check)
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


_crypto_risk_runtime_manager = CryptoRiskRuntimeManager()


def get_crypto_risk_runtime_manager() -> CryptoRiskRuntimeManager:
    return _crypto_risk_runtime_manager


def crypto_risk_runtime_status_to_dict(status: CryptoRiskRuntimeStatusData) -> dict[str, Any]:
    return {
        "enabled": status.enabled,
        "wired": status.wired,
        "fail_closed": status.fail_closed,
        "execution_env": status.execution_env,
        "futures_base_url": status.futures_base_url,
        "base_symbols": list(status.base_symbols),
        "risk_budget": crypto_risk_budget_to_dict(status.risk_budget),
        "last_error": status.last_error,
        "updated_at": status.updated_at,
        "updated_by": status.updated_by,
    }


def crypto_risk_probe_result_to_dict(result: CryptoRiskProbeResult) -> dict[str, Any]:
    return {
        "ok": result.ok,
        "read_only": result.read_only,
        "mode": result.mode,
        "execution_env": result.execution_env,
        "futures_base_url": result.futures_base_url,
        "symbols": list(result.symbols),
        "requested_by": result.requested_by,
        "started_at": result.started_at,
        "finished_at": result.finished_at,
        "duration_ms": result.duration_ms,
        "checks": {
            name: {
                "status": check.status,
                "latency_ms": check.latency_ms,
                "message": check.message,
                "details": check.details,
            }
            for name, check in sorted(result.checks.items())
        },
    }


def crypto_risk_budget_to_dict(budget: CryptoRiskBudget) -> dict[str, Any]:
    return {
        "symbol_notional_caps": {
            symbol: str(value) for symbol, value in sorted(budget.symbol_notional_caps.items())
        },
        "symbol_clusters": {
            symbol: cluster for symbol, cluster in sorted(budget.symbol_clusters.items())
        },
        "cluster_notional_caps": {
            cluster: str(value) for cluster, value in sorted(budget.cluster_notional_caps.items())
        },
        "total_notional_cap": str(budget.total_notional_cap),
        "max_margin_ratio": str(budget.max_margin_ratio),
        "min_liquidation_buffer_ratio": str(budget.min_liquidation_buffer_ratio),
        "max_abs_funding_rate_z_score": str(budget.max_abs_funding_rate_z_score),
        "max_abs_open_interest_change_rate": str(budget.max_abs_open_interest_change_rate),
        "funding_history_window": budget.funding_history_window,
        "oi_history_window": budget.oi_history_window,
        "funding_min_periods": budget.funding_min_periods,
        "oi_min_periods": budget.oi_min_periods,
        "max_data_age_seconds": budget.max_data_age_seconds,
    }


def merge_crypto_risk_budget(
    current: CryptoRiskBudget,
    *,
    symbol_notional_caps: Mapping[str, str] | None = None,
    symbol_clusters: Mapping[str, str] | None = None,
    cluster_notional_caps: Mapping[str, str] | None = None,
    total_notional_cap: str | None = None,
    max_margin_ratio: str | None = None,
    min_liquidation_buffer_ratio: str | None = None,
    max_abs_funding_rate_z_score: str | None = None,
    max_abs_open_interest_change_rate: str | None = None,
    funding_history_window: int | None = None,
    oi_history_window: int | None = None,
    funding_min_periods: int | None = None,
    oi_min_periods: int | None = None,
    max_data_age_seconds: int | None = None,
) -> CryptoRiskBudget:
    return CryptoRiskBudget(
        symbol_notional_caps=(
            _parse_symbol_decimal_map_from_mapping(
                symbol_notional_caps,
                "symbol_notional_caps",
            )
            if symbol_notional_caps is not None
            else dict(current.symbol_notional_caps)
        ),
        symbol_clusters=(
            _parse_symbol_text_map_from_mapping(symbol_clusters, "symbol_clusters")
            if symbol_clusters is not None
            else dict(current.symbol_clusters)
        ),
        cluster_notional_caps=(
            _parse_text_decimal_map_from_mapping(
                cluster_notional_caps,
                "cluster_notional_caps",
            )
            if cluster_notional_caps is not None
            else dict(current.cluster_notional_caps)
        ),
        total_notional_cap=_parse_decimal(
            total_notional_cap,
            "total_notional_cap",
            default=current.total_notional_cap,
            min_value=Decimal("0"),
        ),
        max_margin_ratio=_parse_decimal(
            max_margin_ratio,
            "max_margin_ratio",
            default=current.max_margin_ratio,
            min_value=Decimal("0"),
        ),
        min_liquidation_buffer_ratio=_parse_decimal(
            min_liquidation_buffer_ratio,
            "min_liquidation_buffer_ratio",
            default=current.min_liquidation_buffer_ratio,
            min_value=Decimal("0"),
        ),
        max_abs_funding_rate_z_score=_parse_decimal(
            max_abs_funding_rate_z_score,
            "max_abs_funding_rate_z_score",
            default=current.max_abs_funding_rate_z_score,
            min_value=Decimal("0"),
        ),
        max_abs_open_interest_change_rate=_parse_decimal(
            max_abs_open_interest_change_rate,
            "max_abs_open_interest_change_rate",
            default=current.max_abs_open_interest_change_rate,
            min_value=Decimal("0"),
        ),
        funding_history_window=_parse_positive_int(
            funding_history_window,
            "funding_history_window",
            default=current.funding_history_window,
        ),
        oi_history_window=_parse_positive_int(
            oi_history_window,
            "oi_history_window",
            default=current.oi_history_window,
        ),
        max_data_age_seconds=_parse_positive_int(
            max_data_age_seconds,
            "max_data_age_seconds",
            default=current.max_data_age_seconds,
        ),
        funding_min_periods=_validate_min_periods_against_final_window(
            funding_min_periods if funding_min_periods is not None else current.funding_min_periods,
            (
                funding_history_window
                if funding_history_window is not None
                else current.funding_history_window
            ),
            "funding_min_periods",
        ),
        oi_min_periods=_validate_min_periods_against_final_window(
            oi_min_periods if oi_min_periods is not None else current.oi_min_periods,
            oi_history_window if oi_history_window is not None else current.oi_history_window,
            "oi_min_periods",
        ),
    )


def _validate_min_periods_against_final_window(
    final_min_periods: int,
    final_window: int,
    field_name: str,
) -> int:
    if final_min_periods <= 0:
        raise ValueError(f"{field_name} must be a positive integer, got {final_min_periods}")
    if final_min_periods > final_window:
        raise ValueError(f"{field_name} must be <= {final_window}, got {final_min_periods}")
    return final_min_periods


def _parse_symbol_decimal_map_from_mapping(
    raw: Mapping[str, str],
    field_name: str,
) -> dict[str, Decimal]:
    values: dict[str, Decimal] = {}
    for symbol_raw, value_raw in raw.items():
        symbol = _normalize_symbol(symbol_raw)
        if not symbol:
            raise ValueError(f"{field_name} contains empty symbol")
        values[symbol] = _parse_decimal(
            value_raw,
            field_name,
            default=Decimal("0"),
            min_value=Decimal("0"),
        )
    return values


def _parse_symbol_text_map_from_mapping(
    raw: Mapping[str, str],
    field_name: str,
) -> dict[str, str]:
    values: dict[str, str] = {}
    for symbol_raw, value_raw in raw.items():
        symbol = _normalize_symbol(symbol_raw)
        if not symbol:
            raise ValueError(f"{field_name} contains empty symbol")
        cluster = _normalize_cluster(value_raw)
        if not cluster:
            raise ValueError(f"{field_name} contains empty cluster for {symbol}")
        values[symbol] = cluster
    return values


def _parse_text_decimal_map_from_mapping(
    raw: Mapping[str, str],
    field_name: str,
) -> dict[str, Decimal]:
    values: dict[str, Decimal] = {}
    for key_raw, value_raw in raw.items():
        key = _normalize_cluster(key_raw)
        if not key:
            raise ValueError(f"{field_name} contains empty key")
        values[key] = _parse_decimal(
            value_raw,
            field_name,
            default=Decimal("0"),
            min_value=Decimal("0"),
        )
    return values


def _normalize_symbol_tuple(symbols: tuple[str, ...]) -> tuple[str, ...]:
    seen: set[str] = set()
    normalized_symbols: list[str] = []
    for item in symbols:
        symbol = _normalize_symbol(item)
        if not symbol or symbol in seen:
            continue
        seen.add(symbol)
        normalized_symbols.append(symbol)
    return tuple(normalized_symbols)


def _crypto_risk_mode(base_url: str) -> str:
    normalized = base_url.rstrip("/").lower()
    if "demo" in normalized:
        return "demo"
    if normalized == BINANCE_USD_M_FUTURES_BASE_URL.lower():
        return "live"
    return "custom"


async def _probe_check(
    label: str,
    fn: Callable[[], Awaitable[tuple[str, dict[str, Any]]]],
) -> CryptoRiskProbeCheck:
    started = time.perf_counter()
    try:
        message, details = await fn()
        return CryptoRiskProbeCheck(
            status="passed",
            latency_ms=round((time.perf_counter() - started) * 1000, 3),
            message=message,
            details=details,
        )
    except Exception as exc:
        return CryptoRiskProbeCheck(
            status="failed",
            latency_ms=round((time.perf_counter() - started) * 1000, 3),
            message=f"{label} failed: {exc}",
            details={},
        )


async def _probe_venue_health(source: Any) -> tuple[str, dict[str, Any]]:
    status = await source.get_venue_health()
    if not status:
        raise RuntimeError("empty venue health")
    return "Venue health reachable", {"venue_health": status}


async def _probe_mark_prices(source: Any, symbols: set[str]) -> tuple[str, dict[str, Any]]:
    mark_prices = await source.get_mark_prices(symbols)
    missing = sorted(symbol for symbol in symbols if mark_prices.get(symbol, Decimal("0")) <= 0)
    if missing:
        raise RuntimeError(f"missing mark prices for {', '.join(missing)}")
    return "Mark prices reachable", {
        "count": len(mark_prices),
        "mark_prices": {symbol: str(price) for symbol, price in sorted(mark_prices.items())},
    }


async def _probe_instrument_specs(source: Any, symbols: set[str]) -> tuple[str, dict[str, Any]]:
    specs = await source.get_instrument_specs(symbols)
    missing = sorted(symbol for symbol in symbols if symbol not in specs)
    if missing:
        raise RuntimeError(f"missing instrument specs for {', '.join(missing)}")
    return "Instrument specs reachable", {"count": len(specs), "symbols": sorted(specs)}


async def _probe_leverage_brackets(source: Any, symbols: set[str]) -> tuple[str, dict[str, Any]]:
    brackets = await source.get_leverage_brackets(symbols)
    missing = sorted(symbol for symbol in symbols if not brackets.get(symbol))
    if missing:
        raise RuntimeError(f"missing leverage brackets for {', '.join(missing)}")
    return "Leverage brackets reachable", {
        "count": len(brackets),
        "symbols": sorted(brackets),
    }


async def _probe_account(source: Any) -> tuple[str, dict[str, Any]]:
    account = await source.get_account_risk()
    return "Account risk reachable", {
        "equity": str(account.equity),
        "available_balance": str(account.available_balance),
        "margin_balance": str(account.margin_balance),
        "total_initial_margin": str(account.total_initial_margin),
        "total_maintenance_margin": str(account.total_maintenance_margin),
    }


async def _probe_positions(source: Any) -> tuple[str, dict[str, Any]]:
    positions = await source.get_positions(symbols=None)
    nonzero_symbols = sorted(position.symbol for position in positions if position.qty != 0)
    return "Positions reachable", {"count": len(positions), "nonzero_symbols": nonzero_symbols}


async def _probe_open_orders(source: Any) -> tuple[str, dict[str, Any]]:
    open_orders = await source.get_open_orders(symbols=None)
    return "Open orders reachable", {"count": len(open_orders)}


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


def _parse_symbol_text_map(raw: str | None, env_name: str) -> dict[str, str]:
    if raw is None or str(raw).strip() == "":
        return {}

    values: dict[str, str] = {}
    for item in str(raw).split(","):
        part = item.strip()
        if not part:
            continue
        if "=" not in part:
            raise ValueError(f"{env_name} item must use SYMBOL=CLUSTER format: {part!r}")
        symbol_raw, cluster_raw = part.split("=", 1)
        symbol = _normalize_symbol(symbol_raw)
        cluster = _normalize_cluster(cluster_raw)
        if not symbol:
            raise ValueError(f"{env_name} contains empty symbol in item: {part!r}")
        if not cluster:
            raise ValueError(f"{env_name} contains empty cluster in item: {part!r}")
        values[symbol] = cluster
    return values


def _parse_text_decimal_map(raw: str | None, env_name: str) -> dict[str, Decimal]:
    if raw is None or str(raw).strip() == "":
        return {}

    values: dict[str, Decimal] = {}
    for item in str(raw).split(","):
        part = item.strip()
        if not part:
            continue
        if "=" not in part:
            raise ValueError(f"{env_name} item must use KEY=DECIMAL format: {part!r}")
        key_raw, value_raw = part.split("=", 1)
        key = _normalize_cluster(key_raw)
        if not key:
            raise ValueError(f"{env_name} contains empty key in item: {part!r}")
        values[key] = _parse_decimal(
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


def _parse_positive_int(
    raw: int | None,
    env_name: str,
    *,
    default: int,
) -> int:
    if raw is None:
        return default
    if not isinstance(raw, int) or raw <= 0:
        raise ValueError(f"{env_name} must be a positive integer, got {raw}")
    return raw


def _parse_optional_text(raw: str | None) -> str | None:
    if raw is None:
        return None
    value = str(raw).strip()
    return value or None


def _normalize_symbol(symbol: str) -> str:
    return symbol.upper().replace("-", "").replace("/", "").strip()


def _normalize_cluster(cluster: str) -> str:
    return str(cluster).upper().strip()
