"""Phase-5 credential boundary: Control has none, Execution refuses without."""

from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path

import pytest

from trader.runtime.credentials import (
    BINANCE_CREDENTIALS_MISSING,
    BINANCE_CREDENTIALS_UNREADABLE,
    BINANCE_ENV_NOT_SANDBOX,
    CONTROL_EXCHANGE_CREDENTIALS_PRESENT,
    CONTROL_FORBIDDEN_KEYS,
    CredentialBoundaryError,
    assert_control_credentials_absent,
    control_credential_violations,
    execution_credential_reason,
    require_execution_credentials,
)

ROOT = Path(__file__).resolve().parents[2]


def test_control_rejects_every_forbidden_key_without_echoing_values():
    for key in CONTROL_FORBIDDEN_KEYS:
        with pytest.raises(CredentialBoundaryError) as captured:
            assert_control_credentials_absent({key: "synthetic-secret-value"})
        assert captured.value.reason_code == CONTROL_EXCHANGE_CREDENTIALS_PRESENT
        assert key in str(captured.value)
        assert "synthetic-secret-value" not in str(captured.value)


def test_control_violation_report_contains_names_only():
    violations = control_credential_violations(
        {
            "BINANCE_API_KEY": "alpha-secret",
            "BINANCE_SECRET_KEY": "beta-secret",
            "QTS_ACCOUNT_ID": "acct",
        }
    )
    assert violations == ["BINANCE_API_KEY", "BINANCE_SECRET_KEY"]
    assert "secret" not in repr(violations)


def test_control_accepts_absent_and_empty_credentials():
    assert control_credential_violations({}) == []
    assert control_credential_violations({"BINANCE_API_KEY": "", "BINANCE_SECRET_KEY": ""}) == []
    assert_control_credentials_absent({"BINANCE_API_KEY": ""})


def test_control_treats_a_whitespace_value_as_present():
    with pytest.raises(CredentialBoundaryError) as captured:
        assert_control_credentials_absent({"BINANCE_API_KEY": "   "})
    assert captured.value.reason_code == CONTROL_EXCHANGE_CREDENTIALS_PRESENT


@pytest.mark.asyncio
async def test_control_lifespan_refuses_exchange_credentials(monkeypatch):
    from trader.runtime import control_main

    monkeypatch.setenv("BINANCE_API_KEY", "independent-placeholder")
    with pytest.raises(CredentialBoundaryError) as captured:
        async with control_main.lifespan(control_main.app):
            pass
    assert captured.value.reason_code == CONTROL_EXCHANGE_CREDENTIALS_PRESENT


def test_execution_reports_stable_reason_codes():
    assert execution_credential_reason({}) == BINANCE_CREDENTIALS_MISSING
    assert (
        execution_credential_reason({"BINANCE_API_KEY": "k", "BINANCE_SECRET_KEY": ""})
        == BINANCE_CREDENTIALS_MISSING
    )
    assert (
        execution_credential_reason(
            {"BINANCE_API_KEY": "k", "BINANCE_SECRET_KEY": "s", "BINANCE_ENV": "mainnet"}
        )
        == BINANCE_ENV_NOT_SANDBOX
    )
    assert (
        execution_credential_reason(
            {
                "BINANCE_API_KEY_FILE": "/definitely/missing",
                "BINANCE_SECRET_KEY": "s",
                "BINANCE_ENV": "demo",
            }
        )
        == BINANCE_CREDENTIALS_UNREADABLE
    )


def test_execution_reads_docker_secret_files(tmp_path):
    api_key = tmp_path / "api"
    secret = tmp_path / "secret"
    api_key.write_text("file-key\n", encoding="utf-8")
    secret.write_text("file-secret\n", encoding="utf-8")
    credentials = require_execution_credentials(
        {
            "BINANCE_API_KEY_FILE": str(api_key),
            "BINANCE_SECRET_KEY_FILE": str(secret),
            "BINANCE_ENV": "testnet",
        }
    )
    assert credentials.api_key == "file-key"
    assert credentials.secret_key == "file-secret"
    assert credentials.environment == "testnet"


def _subprocess_env(**overrides: str) -> dict[str, str]:
    env = dict(os.environ)
    for key in (
        "BINANCE_API_KEY",
        "BINANCE_SECRET_KEY",
        "BINANCE_API_KEY_FILE",
        "BINANCE_SECRET_KEY_FILE",
    ):
        env.pop(key, None)
    env["PYTHONPATH"] = str(ROOT)
    env.update(overrides)
    return env


def test_execution_entrypoint_exits_with_reason_code():
    process = subprocess.run(
        [sys.executable, "-c", "from trader.runtime.trading_worker_main import main; main()"],
        cwd=str(ROOT),
        env=_subprocess_env(BINANCE_ENV="demo"),
        capture_output=True,
        text=True,
        timeout=120,
    )
    assert process.returncode == 2
    assert BINANCE_CREDENTIALS_MISSING in (process.stdout + process.stderr)


def test_execution_refusal_never_prints_a_credential_value():
    env = _subprocess_env(
        BINANCE_ENV="mainnet",
        BINANCE_API_KEY="DO-NOT-PRINT-THIS-KEY",
        BINANCE_SECRET_KEY="DO-NOT-PRINT-THIS-SECRET",
    )
    process = subprocess.run(
        [sys.executable, "-c", "from trader.runtime.trading_worker_main import main; main()"],
        cwd=str(ROOT),
        env=env,
        capture_output=True,
        text=True,
        timeout=120,
    )
    combined = process.stdout + process.stderr
    assert process.returncode == 2
    assert BINANCE_ENV_NOT_SANDBOX in combined
    assert "DO-NOT-PRINT-THIS-KEY" not in combined
    assert "DO-NOT-PRINT-THIS-SECRET" not in combined
