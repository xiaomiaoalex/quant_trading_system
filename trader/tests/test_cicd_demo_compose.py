"""Demo Compose overlay contract (C4).

The overlay must reference an immutable digest, must never rebuild on the
target host, must hard-code ``BINANCE_ENV=demo`` and must keep its project,
ports and volumes separate from the shared local stack.
"""

from __future__ import annotations

import json
import os
import shutil
import subprocess
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
OVERLAY = ROOT / "deploy" / "compose.demo.yml"
ENV_EXAMPLE = ROOT / "deploy" / "demo.env.example"
PROJECT = "qts_demo_contract_test"
SECRET_KEYS = (
    "POSTGRES_PASSWORD",
    "QTS_ALERT_SIGNING_SECRET",
    "QTS_PASSWORD_HASH",
)


def _overlay_text() -> str:
    return OVERLAY.read_text(encoding="utf-8")


def test_overlay_hardcodes_demo_and_immutable_image():
    text = _overlay_text()
    assert "BINANCE_ENV: demo" in text
    assert "BINANCE_ENV: ${" not in text
    assert "QTS_RELEASE_IMAGE:?" in text
    assert "build: !reset null" in text
    assert ":latest" not in text
    assert "name: ${QTS_COMPOSE_PROJECT:?" in text


def test_env_example_contains_no_real_secret_values():
    values = {}
    for line in ENV_EXAMPLE.read_text(encoding="utf-8").splitlines():
        stripped = line.strip()
        if not stripped or stripped.startswith("#") or "=" not in stripped:
            continue
        key, value = stripped.split("=", 1)
        values[key.strip()] = value.strip()
    for key in SECRET_KEYS:
        assert "REPLACE_WITH" in values[key], key
    assert values["BINANCE_ENV"] == "demo"
    assert "REPLACE_WITH" in values["QTS_RELEASE_IMAGE"]
    assert values["QTS_DR_REQUIRED"] == "1"
    assert values["QTS_SCHEMA_VERSION"] == "3"
    assert values["QTS_EXTERNAL_ALERT_WEBHOOK_URL"].startswith("https://")
    assert values["QTS_EXTERNAL_ALERT_HEARTBEAT_URL"].startswith("https://")


@pytest.mark.skipif(shutil.which("docker") is None, reason="docker CLI not installed")
def test_merged_config_is_isolated_and_never_builds():
    env = dict(os.environ)
    env["QTS_COMPOSE_PROJECT"] = PROJECT
    # The release values come from the checked-in non-secret example file.
    result = subprocess.run(
        [
            "docker",
            "compose",
            "-f",
            "compose.yml",
            "-f",
            "deploy/compose.demo.yml",
            "--env-file",
            "deploy/demo.env.example",
            "-p",
            PROJECT,
            "config",
            "--format",
            "json",
        ],
        cwd=str(ROOT),
        env=env,
        capture_output=True,
        text=True,
        timeout=120,
    )
    assert result.returncode == 0, result.stderr
    config = json.loads(result.stdout)
    assert config["name"] == PROJECT
    services = config["services"]
    execution = services["execution"]
    assert execution["environment"]["BINANCE_ENV"] == "demo"
    for name, service in services.items():
        assert "build" not in service, name
        assert service.get("image"), name
        assert "@sha256:" in service["image"], name
    control_env = services["control"]["environment"]
    execution_env = services["execution"]["environment"]
    for env in (control_env, execution_env):
        assert env["QTS_ALERT_WEBHOOK_URL"] == "https://alerts.example.com/qts"
        assert env["QTS_ALERT_HEARTBEAT_URL"] == "https://alerts.example.com/qts/heartbeat"
        assert "alert-sink" not in env["QTS_ALERT_WEBHOOK_URL"]
        assert "alert-sink" not in env["QTS_ALERT_HEARTBEAT_URL"]
    assert services["control"]["ports"][0]["published"] == "18090"
    assert services["postgres"]["ports"][0]["published"] == "65432"
    assert services["alert-sink"]["ports"][0]["published"] == "18085"
    for volume in config["volumes"].values():
        assert volume["name"].startswith(f"{PROJECT}_")
    labels = services["control"]["labels"]
    assert labels["com.qts.environment"] == "demo"
    assert labels["com.qts.release.id"] == "REPLACE_WITH_RELEASE_ID"
