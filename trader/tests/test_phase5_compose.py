"""Phase-5 Compose topology and credential-isolation assertions."""

from __future__ import annotations

import json
import os
import shutil
import subprocess
import sys
from pathlib import Path

import pytest
import yaml

ROOT = Path(__file__).resolve().parents[2]
COMPOSE = ROOT / "compose.yml"
FORBIDDEN_IN_CONTROL = {
    "BINANCE_API_KEY",
    "BINANCE_SECRET_KEY",
    "BINANCE_API_KEY_FILE",
    "BINANCE_SECRET_KEY_FILE",
}


def _document() -> dict:
    return yaml.safe_load(COMPOSE.read_text(encoding="utf-8"))


def _env_keys(service: dict) -> set[str]:
    environment = service.get("environment") or {}
    if isinstance(environment, dict):
        return {str(key) for key in environment}
    return {str(item).split("=", 1)[0] for item in environment}


def test_compose_covers_the_five_required_services():
    services = _document()["services"]
    assert {"postgres", "control", "execution", "research", "history"} <= set(services)
    assert "backup" in services
    assert "alert-sink" in services


def test_no_fixed_container_names_and_parameterised_host_ports():
    document = _document()
    for name, service in document["services"].items():
        assert "container_name" not in service, name
    ports = document["services"]["postgres"]["ports"]
    assert any("QTS_PG_PORT" in str(port) for port in ports)
    assert any("QTS_CONTROL_PORT" in str(port) for port in document["services"]["control"]["ports"])
    assert document["services"]["postgres"]["volumes"] == ["qts_pgdata:/var/lib/postgresql/data"]


def test_backup_cadence_leaves_rpo_margin():
    document = _document()["services"]
    command = " ".join(str(item) for item in document["backup"]["command"])
    assert "${QTS_BACKUP_INTERVAL_SECONDS:-240}" in command
    assert (
        document["control"]["environment"]["QTS_BACKUP_RPO_SECONDS"]
        == "${QTS_BACKUP_RPO_SECONDS:-300}"
    )


def test_alert_signing_secret_reaches_producers_and_receiver():
    document = _document()["services"]
    for name in ("control", "execution", "alert-sink"):
        assert "QTS_ALERT_SIGNING_SECRET" in (document[name].get("environment") or {}), name


def test_control_binds_inside_the_container_for_port_publication():
    control = _document()["services"]["control"]
    environment = control.get("environment") or {}
    assert environment.get("QTS_API_HOST") == "0.0.0.0"


def test_control_holds_no_exchange_credentials():
    control = _document()["services"]["control"]
    assert not (_env_keys(control) & FORBIDDEN_IN_CONTROL)
    assert not control.get("secrets")
    env_files = control.get("env_file") or []
    if isinstance(env_files, str):
        env_files = [env_files]
    assert ".env" not in env_files


def test_execution_gets_only_exchange_secret_files():
    document = _document()
    execution = document["services"]["execution"]
    keys = _env_keys(execution)
    assert "BINANCE_API_KEY" not in keys
    assert "BINANCE_SECRET_KEY" not in keys
    assert "BINANCE_API_KEY_FILE" in keys
    assert "BINANCE_SECRET_KEY_FILE" in keys
    assert set(execution["secrets"]) == {"binance_api_key", "binance_secret_key"}
    assert set(document["secrets"]) == {"binance_api_key", "binance_secret_key"}


def test_backup_and_alert_services_do_not_get_exchange_credentials():
    document = _document()
    for name in ("backup", "alert-sink"):
        assert not (_env_keys(document["services"][name]) & FORBIDDEN_IN_CONTROL)
        assert not document["services"][name].get("secrets")


def test_required_healthchecks_exist():
    document = _document()["services"]
    for name in ("postgres", "control", "execution", "research", "history", "alert-sink"):
        assert "healthcheck" in document[name], name


def test_control_and_execution_do_not_share_an_env_file():
    document = _document()["services"]
    control_files = document["control"].get("env_file") or []
    execution_files = document["execution"].get("env_file") or []
    if isinstance(control_files, str):
        control_files = [control_files]
    if isinstance(execution_files, str):
        execution_files = [execution_files]
    assert not (set(control_files) & set(execution_files))


def test_credential_isolation_script_reports_ok(tmp_path):
    result = subprocess.run(
        [sys.executable, "scripts/check_credential_isolation.py", "--compose", "compose.yml"],
        cwd=str(ROOT),
        capture_output=True,
        text=True,
        timeout=120,
    )
    assert result.returncode == 0, result.stderr
    payload = json.loads(result.stdout)
    assert payload["ok"] is True
    assert payload["reports"][0]["control_env_keys"]


@pytest.mark.skipif(shutil.which("docker") is None, reason="docker CLI not installed")
def test_resolved_compose_config_is_valid_and_does_not_render_secret_values(tmp_path):
    api_file = tmp_path / "api"
    secret_file = tmp_path / "secret"
    api_file.write_text("SYNTHETIC-KEY-MUST-NOT-RENDER", encoding="utf-8")
    secret_file.write_text("SYNTHETIC-SECRET-MUST-NOT-RENDER", encoding="utf-8")
    dr_dir = tmp_path / "dr"
    dr_dir.mkdir()
    env = dict(os.environ)
    env.update(
        {
            "QTS_COMPOSE_PROJECT": "qts_p5_config_test",
            "BINANCE_API_KEY_FILE_PATH": str(api_file),
            "BINANCE_SECRET_KEY_FILE_PATH": str(secret_file),
            "QTS_DR_EVIDENCE_DIR": str(dr_dir),
        }
    )
    result = subprocess.run(
        ["docker", "compose", "-p", "qts_p5_config_test", "config"],
        cwd=str(ROOT),
        env=env,
        capture_output=True,
        text=True,
        timeout=120,
    )
    assert result.returncode == 0, result.stderr
    assert "SYNTHETIC-KEY-MUST-NOT-RENDER" not in result.stdout
    assert "SYNTHETIC-SECRET-MUST-NOT-RENDER" not in result.stdout
    assert "BINANCE_API_KEY_FILE" in result.stdout
