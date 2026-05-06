from __future__ import annotations

import argparse
import json
import os
import sys
from dataclasses import dataclass
from decimal import Decimal
from pathlib import Path
from typing import Literal, Mapping, Sequence

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from trader.adapters.binance.crypto_risk_source import BINANCE_USD_M_FUTURES_BASE_URL
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
    get_crypto_risk_runtime_config,
)
from trader.api.env_config import BINANCE_ENV_DEMO, BINANCE_ENV_ENV

IssueSeverity = Literal["error", "warning"]

_TRUE_VALUES = {"1", "true", "yes", "on"}
_PLACEHOLDER_VALUES = {
    "",
    "changeme",
    "change_me",
    "your_api_key",
    "your_secret_key",
    "your_demo_api_key_here",
    "your_demo_secret_key_here",
    "your_testnet_api_key_here",
    "your_testnet_secret_key_here",
}


@dataclass(frozen=True, slots=True)
class DemoPreflightIssue:
    severity: IssueSeverity
    code: str
    message: str


@dataclass(frozen=True, slots=True)
class DemoPreflightReport:
    ok: bool
    execution_env: str
    risk_source_mode: str
    symbols: tuple[str, ...]
    errors: tuple[DemoPreflightIssue, ...]
    warnings: tuple[DemoPreflightIssue, ...]


def build_demo_preflight_report(env: Mapping[str, str]) -> DemoPreflightReport:
    issues: list[DemoPreflightIssue] = []
    execution_env = _normalize_text(env.get(BINANCE_ENV_ENV)) or "<missing>"
    risk_source_mode = "missing"
    symbols: tuple[str, ...] = ()

    _check_execution_env(env, issues)
    _check_credentials(env, issues)
    _check_crypto_risk_enabled(env, issues)
    risk_source_mode = _check_futures_source(env, issues)

    try:
        config = get_crypto_risk_runtime_config(env)
    except ValueError as exc:
        issues.append(
            DemoPreflightIssue(
                severity="error",
                code="CRYPTO_RISK_CONFIG_INVALID",
                message=str(exc),
            )
        )
    else:
        symbols = config.base_symbols
        if config.enabled:
            _check_symbols(symbols, issues)
            _check_budget(config.risk_budget, symbols, issues)

    errors = tuple(issue for issue in issues if issue.severity == "error")
    warnings = tuple(issue for issue in issues if issue.severity == "warning")
    return DemoPreflightReport(
        ok=not errors,
        execution_env=execution_env,
        risk_source_mode=risk_source_mode,
        symbols=symbols,
        errors=errors,
        warnings=warnings,
    )


def load_env_file(path: Path) -> dict[str, str]:
    values: dict[str, str] = {}
    if not path.exists():
        return values

    for raw_line in path.read_text(encoding="utf-8").splitlines():
        line = raw_line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, raw_value = line.split("=", 1)
        key = key.strip()
        value = raw_value.strip().strip('"').strip("'")
        if key:
            values[key] = value
    return values


def report_to_dict(report: DemoPreflightReport) -> dict[str, object]:
    return {
        "ok": report.ok,
        "execution_env": report.execution_env,
        "risk_source_mode": report.risk_source_mode,
        "symbols": list(report.symbols),
        "errors": [_issue_to_dict(issue) for issue in report.errors],
        "warnings": [_issue_to_dict(issue) for issue in report.warnings],
    }


def _issue_to_dict(issue: DemoPreflightIssue) -> dict[str, str]:
    return {
        "severity": issue.severity,
        "code": issue.code,
        "message": issue.message,
    }


def _check_execution_env(env: Mapping[str, str], issues: list[DemoPreflightIssue]) -> None:
    raw = _normalize_text(env.get(BINANCE_ENV_ENV))
    if raw is None:
        issues.append(
            DemoPreflightIssue(
                severity="error",
                code="BINANCE_ENV_MISSING",
                message="BINANCE_ENV must be explicitly set to demo for demo risk rehearsal.",
            )
        )
        return

    if raw != BINANCE_ENV_DEMO:
        issues.append(
            DemoPreflightIssue(
                severity="error",
                code="BINANCE_ENV_NOT_DEMO",
                message=f"BINANCE_ENV must be demo for this runbook, got {raw!r}.",
            )
        )


def _check_credentials(env: Mapping[str, str], issues: list[DemoPreflightIssue]) -> None:
    for name in ("BINANCE_API_KEY", "BINANCE_SECRET_KEY"):
        value = _normalize_text(env.get(name)) or ""
        if value.lower() in _PLACEHOLDER_VALUES:
            issues.append(
                DemoPreflightIssue(
                    severity="error",
                    code=f"{name}_MISSING",
                    message=f"{name} must be set to a real demo credential; value is never printed.",
                )
            )


def _check_crypto_risk_enabled(
    env: Mapping[str, str],
    issues: list[DemoPreflightIssue],
) -> None:
    raw = _normalize_text(env.get(CRYPTO_RISK_ENABLED_ENV))
    if raw not in _TRUE_VALUES:
        issues.append(
            DemoPreflightIssue(
                severity="error",
                code="CRYPTO_RISK_DISABLED",
                message="CRYPTO_RISK_ENABLED must be true/1/yes/on before running readiness probe.",
            )
        )


def _check_futures_source(
    env: Mapping[str, str],
    issues: list[DemoPreflightIssue],
) -> str:
    raw = _normalize_text(env.get(CRYPTO_RISK_FUTURES_BASE_URL_ENV))
    if raw is None:
        issues.append(
            DemoPreflightIssue(
                severity="error",
                code="CRYPTO_RISK_FUTURES_URL_MISSING",
                message=(
                    "CRYPTO_RISK_FUTURES_BASE_URL must be explicit; the default live USD-M URL "
                    "is too easy to confuse with demo execution."
                ),
            )
        )
        return "missing"

    mode = classify_futures_source_mode(raw)
    if mode == "testnet":
        issues.append(
            DemoPreflightIssue(
                severity="error",
                code="CRYPTO_RISK_FUTURES_URL_TESTNET",
                message="CRYPTO_RISK_FUTURES_BASE_URL points at testnet, not demo.",
            )
        )
    elif mode == "spot_demo_invalid":
        issues.append(
            DemoPreflightIssue(
                severity="error",
                code="CRYPTO_RISK_FUTURES_URL_SPOT_DEMO",
                message=(
                    "CRYPTO_RISK_FUTURES_BASE_URL points at Spot Demo. "
                    "USD-M demo risk source should use https://demo-fapi.binance.com."
                ),
            )
        )
    elif mode == "live":
        issues.append(
            DemoPreflightIssue(
                severity="error",
                code="CRYPTO_RISK_FUTURES_URL_LIVE",
                message=(
                    "CRYPTO_RISK_FUTURES_BASE_URL points at the live USD-M endpoint; "
                    "demo rehearsal must use an explicit demo/custom read-only source."
                ),
            )
        )
    elif mode == "custom":
        issues.append(
            DemoPreflightIssue(
                severity="warning",
                code="CRYPTO_RISK_FUTURES_URL_CUSTOM",
                message=(
                    "CRYPTO_RISK_FUTURES_BASE_URL is custom. Verify it is the intended "
                    "read-only demo-compatible USD-M risk source before probing."
                ),
            )
        )
    return mode


def classify_futures_source_mode(base_url: str) -> str:
    normalized = base_url.rstrip("/").lower()
    if "testnet" in normalized:
        return "testnet"
    if normalized.startswith("https://demo-api.binance.com"):
        return "spot_demo_invalid"
    if "demo" in normalized:
        return "demo"
    if normalized == BINANCE_USD_M_FUTURES_BASE_URL.lower():
        return "live"
    return "custom"


def _check_symbols(symbols: tuple[str, ...], issues: list[DemoPreflightIssue]) -> None:
    if not symbols:
        issues.append(
            DemoPreflightIssue(
                severity="error",
                code="CRYPTO_RISK_BASE_SYMBOLS_MISSING",
                message=f"{CRYPTO_RISK_BASE_SYMBOLS_ENV} must include at least one probe symbol.",
            )
        )


def _check_budget(
    budget: object, symbols: tuple[str, ...], issues: list[DemoPreflightIssue]
) -> None:
    total_cap = getattr(budget, "total_notional_cap")
    symbol_caps = getattr(budget, "symbol_notional_caps")
    symbol_clusters = getattr(budget, "symbol_clusters")
    cluster_caps = getattr(budget, "cluster_notional_caps")
    max_margin_ratio = getattr(budget, "max_margin_ratio")
    min_liquidation_buffer_ratio = getattr(budget, "min_liquidation_buffer_ratio")

    if total_cap <= Decimal("0") and not symbol_caps and not cluster_caps:
        issues.append(
            DemoPreflightIssue(
                severity="error",
                code="CRYPTO_RISK_BUDGET_MISSING",
                message=(
                    f"Set {CRYPTO_RISK_TOTAL_NOTIONAL_CAP_ENV}, "
                    f"{CRYPTO_RISK_SYMBOL_NOTIONAL_CAPS_ENV}, or "
                    f"{CRYPTO_RISK_CLUSTER_NOTIONAL_CAPS_ENV} before probing."
                ),
            )
        )

    if not (Decimal("0") < max_margin_ratio <= Decimal("1")):
        issues.append(
            DemoPreflightIssue(
                severity="error",
                code="CRYPTO_RISK_MAX_MARGIN_RATIO_INVALID",
                message=f"{CRYPTO_RISK_MAX_MARGIN_RATIO_ENV} must be > 0 and <= 1.",
            )
        )

    if not (Decimal("0") <= min_liquidation_buffer_ratio <= Decimal("1")):
        issues.append(
            DemoPreflightIssue(
                severity="error",
                code="CRYPTO_RISK_MIN_LIQUIDATION_BUFFER_RATIO_INVALID",
                message=f"{CRYPTO_RISK_MIN_LIQUIDATION_BUFFER_RATIO_ENV} must be >= 0 and <= 1.",
            )
        )

    if cluster_caps:
        for symbol in symbols:
            cluster = symbol_clusters.get(symbol)
            if not cluster:
                issues.append(
                    DemoPreflightIssue(
                        severity="error",
                        code="CRYPTO_RISK_CLUSTER_SYMBOL_UNMAPPED",
                        message=(
                            f"{symbol} is in {CRYPTO_RISK_BASE_SYMBOLS_ENV} but missing from "
                            f"{CRYPTO_RISK_SYMBOL_CLUSTERS_ENV} while cluster caps are enabled."
                        ),
                    )
                )
                continue
            if cluster not in cluster_caps:
                issues.append(
                    DemoPreflightIssue(
                        severity="error",
                        code="CRYPTO_RISK_CLUSTER_CAP_MISSING",
                        message=(
                            f"{symbol} maps to {cluster}, but that cluster has no cap in "
                            f"{CRYPTO_RISK_CLUSTER_NOTIONAL_CAPS_ENV}."
                        ),
                    )
                )


def _normalize_text(value: str | None) -> str | None:
    if value is None:
        return None
    normalized = str(value).strip().lower()
    return normalized or None


def _format_human_report(report: DemoPreflightReport) -> str:
    lines = [
        "Crypto Risk Demo Preflight",
        f"status: {'PASS' if report.ok else 'FAIL'}",
        f"execution_env: {report.execution_env}",
        f"risk_source_mode: {report.risk_source_mode}",
        f"symbols: {', '.join(report.symbols) if report.symbols else '<none>'}",
    ]
    if report.errors:
        lines.append("errors:")
        lines.extend(f"  - [{issue.code}] {issue.message}" for issue in report.errors)
    if report.warnings:
        lines.append("warnings:")
        lines.extend(f"  - [{issue.code}] {issue.message}" for issue in report.warnings)
    return "\n".join(lines)


def _load_effective_env(env_file: Path | None) -> dict[str, str]:
    values = dict(os.environ)
    if env_file is not None:
        values.update(load_env_file(env_file))
    return values


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="Validate Binance demo + Crypto Risk readiness-probe environment."
    )
    parser.add_argument(
        "--env-file",
        default=".env",
        help="Env file to merge over current process environment. Use an empty value to skip.",
    )
    parser.add_argument("--json", action="store_true", help="Print machine-readable JSON.")
    parser.add_argument(
        "--strict",
        action="store_true",
        help="Exit non-zero when warnings are present.",
    )
    args = parser.parse_args(argv)

    env_file = Path(args.env_file) if args.env_file else None
    report = build_demo_preflight_report(_load_effective_env(env_file))

    if args.json:
        print(json.dumps(report_to_dict(report), ensure_ascii=False, indent=2, sort_keys=True))
    else:
        print(_format_human_report(report))

    if not report.ok:
        return 1
    if args.strict and report.warnings:
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
