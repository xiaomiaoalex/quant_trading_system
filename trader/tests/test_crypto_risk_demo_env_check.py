from __future__ import annotations

import importlib.util
import sys
from pathlib import Path
from types import ModuleType


def _load_script_module() -> ModuleType:
    script_path = Path(__file__).resolve().parents[2] / "scripts" / "check_crypto_risk_demo_env.py"
    spec = importlib.util.spec_from_file_location("check_crypto_risk_demo_env", script_path)
    assert spec is not None
    module = importlib.util.module_from_spec(spec)
    assert spec.loader is not None
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


def _base_demo_env() -> dict[str, str]:
    return {
        "BINANCE_ENV": "demo",
        "BINANCE_API_KEY": "real_demo_key",
        "BINANCE_SECRET_KEY": "real_demo_secret",
        "CRYPTO_RISK_ENABLED": "true",
        "CRYPTO_RISK_FUTURES_BASE_URL": "https://demo-api.binance.com/fapi",
        "CRYPTO_RISK_BASE_SYMBOLS": "btcusdt,eth-usdt",
        "CRYPTO_RISK_TOTAL_NOTIONAL_CAP": "10000",
        "CRYPTO_RISK_SYMBOL_CLUSTERS": "BTCUSDT=BTC_BETA,ETHUSDT=ETH_BETA",
        "CRYPTO_RISK_CLUSTER_NOTIONAL_CAPS": "BTC_BETA=8000,ETH_BETA=4000",
        "CRYPTO_RISK_MAX_MARGIN_RATIO": "0.60",
        "CRYPTO_RISK_MIN_LIQUIDATION_BUFFER_RATIO": "0.08",
    }


def test_demo_preflight_accepts_explicit_demo_risk_env() -> None:
    module = _load_script_module()

    report = module.build_demo_preflight_report(_base_demo_env())

    assert report.ok is True
    assert report.execution_env == "demo"
    assert report.risk_source_mode == "demo"
    assert report.symbols == ("BTCUSDT", "ETHUSDT")
    assert report.errors == ()


def test_demo_preflight_rejects_testnet_execution_env_and_source() -> None:
    module = _load_script_module()
    env = _base_demo_env() | {
        "BINANCE_ENV": "testnet",
        "CRYPTO_RISK_FUTURES_BASE_URL": "https://testnet.binancefuture.com",
    }

    report = module.build_demo_preflight_report(env)

    codes = {issue.code for issue in report.errors}
    assert "BINANCE_ENV_NOT_DEMO" in codes
    assert "CRYPTO_RISK_FUTURES_URL_TESTNET" in codes


def test_demo_preflight_requires_explicit_source_and_budget() -> None:
    module = _load_script_module()
    env = {
        "BINANCE_ENV": "demo",
        "BINANCE_API_KEY": "real_demo_key",
        "BINANCE_SECRET_KEY": "real_demo_secret",
        "CRYPTO_RISK_ENABLED": "true",
        "CRYPTO_RISK_BASE_SYMBOLS": "BTCUSDT",
    }

    report = module.build_demo_preflight_report(env)

    codes = {issue.code for issue in report.errors}
    assert "CRYPTO_RISK_FUTURES_URL_MISSING" in codes
    assert "CRYPTO_RISK_BUDGET_MISSING" in codes


def test_demo_preflight_rejects_cluster_cap_with_unmapped_symbol() -> None:
    module = _load_script_module()
    env = _base_demo_env() | {
        "CRYPTO_RISK_SYMBOL_CLUSTERS": "BTCUSDT=BTC_BETA",
        "CRYPTO_RISK_CLUSTER_NOTIONAL_CAPS": "BTC_BETA=8000",
    }

    report = module.build_demo_preflight_report(env)

    codes = {issue.code for issue in report.errors}
    assert "CRYPTO_RISK_CLUSTER_SYMBOL_UNMAPPED" in codes
