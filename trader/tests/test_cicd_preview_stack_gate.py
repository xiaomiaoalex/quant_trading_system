"""Contract tests for the no-side-effect preview stack preflight."""

from __future__ import annotations

import json
import os
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))

from scripts import validate_preview_stack as gate  # noqa: E402


def _values() -> dict[str, str]:
    return {
        "QTS_PREVIEW_COMPOSE_PROJECT": "qts_ci_pr_abc",
        "QTS_PREVIEW_ID": "pr-abc",
        "QTS_PREVIEW_COMMIT": "a" * 40,
        "QTS_PREVIEW_ORIGIN": "http://127.0.0.1:18080",
        "QTS_PREVIEW_HTTP_PORT": "18080",
        "QTS_PREVIEW_RUNTIME_IMAGE": "qts-runtime:pr-abc",
        "QTS_PREVIEW_RUNTIME_IMAGE_ID": "sha256:" + "b" * 64,
        "QTS_PREVIEW_UI_IMAGE": "qts-preview-ui:pr-abc",
        "QTS_PREVIEW_POSTGRES_PASSWORD": "pg_synthetic_1234567890",
        "QTS_PREVIEW_PASSWORD_HASH": "scrypt$" + "c" * 32 + "$" + "d" * 128,
    }


def test_shell_environment_is_the_authoritative_source(monkeypatch):
    monkeypatch.setattr(os, "environ", {"QTS_PREVIEW_COMMIT": "b" * 40})
    values = gate.collect(dict(os.environ))
    assert values["QTS_PREVIEW_COMMIT"] == "b" * 40
    assert "MISSING_VALUE:QTS_PREVIEW_RUNTIME_IMAGE" in gate.validate(values)


def test_registry_digest_cannot_be_disguised_as_an_image_id():
    values = _values()
    values["QTS_PREVIEW_RUNTIME_IMAGE"] = "repo@sha256:" + "e" * 64
    assert "REGISTRY_DIGEST_NOT_AN_IMAGE_ID" in gate.validate(values)


def test_render_rejects_retag_execution_and_non_loopback_ports():
    values = _values()
    rendered = {
        "services": {
            "control": {
                "image": values["QTS_PREVIEW_RUNTIME_IMAGE"] + "@sha256:" + "f" * 64,
                "build": {"context": "."},
                "ports": [{"host_ip": "0.0.0.0", "published": "18080"}],
                "labels": {
                    "com.qts.preview.runtime_image_id": "sha256:" + "0" * 64,
                    "com.qts.preview.commit": "0" * 40,
                },
            },
            "execution": {"image": "qts-runtime:pr-abc"},
        },
        "secrets": {"binance_api_key": {"file": "/tmp/key"}},
    }
    problems, summary = gate.evaluate_rendered(rendered, values, {})
    assert "IMAGE_ID_RETAG:control" in problems
    assert "RESIDUAL_BUILD_PATH:control" in problems
    assert "NON_LOOPBACK_PORT:control" in problems
    assert "FORBIDDEN_SERVICE:execution" in problems
    assert summary["venue_secret_names"] == ["binance_api_key"]


def test_clean_render_produces_a_redacted_summary():
    values = _values()
    rendered = {
        "services": {
            "control": {
                "image": values["QTS_PREVIEW_RUNTIME_IMAGE"],
                "ports": [],
                "labels": {
                    "com.qts.preview.runtime_image_id": values["QTS_PREVIEW_RUNTIME_IMAGE_ID"],
                    "com.qts.preview.commit": values["QTS_PREVIEW_COMMIT"],
                },
            },
            "web": {
                "image": values["QTS_PREVIEW_UI_IMAGE"],
                "ports": [{"host_ip": "127.0.0.1", "published": "18080"}],
                "labels": {},
            },
        },
        "secrets": {},
    }
    problems, summary = gate.evaluate_rendered(rendered, values, {})
    assert problems == []
    assert summary["image_bindings_match"] is True
    assert summary["commit_labels_match"] is True
    assert values["QTS_PREVIEW_PASSWORD_HASH"] not in json.dumps(summary)


def test_skip_docker_never_claims_a_rendered_or_started_stack(tmp_path, monkeypatch):
    for key, value in _values().items():
        monkeypatch.setenv(key, value)
    report = tmp_path / "preview-stack.json"
    env_out = tmp_path / "preview.env"
    code = gate.main(
        [
            "--compose-file",
            "compose.yml",
            "--env-out",
            str(env_out),
            "--json-out",
            str(report),
            "--skip-docker",
        ]
    )
    assert code == 0
    payload = json.loads(report.read_text(encoding="utf-8"))
    assert payload["summary"]["docker_checks"].startswith("SKIPPED")
    assert not env_out.exists()


def test_generated_env_keeps_the_scrypt_hash_literal_for_compose(tmp_path, monkeypatch):
    for key, value in _values().items():
        monkeypatch.setenv(key, value)
    report = tmp_path / "preview-stack.json"
    env_out = tmp_path / "preview.env"
    code = gate.main(
        [
            "--compose-file",
            "compose.yml",
            "--env-out",
            str(env_out),
            "--json-out",
            str(report),
            "--skip-docker",
        ]
    )
    # --skip-docker intentionally never claims a rendered stack or writes env.
    assert code == 0
    assert not env_out.exists()

    values = _values()
    gate.write_env(env_out, values)
    line = next(
        row
        for row in env_out.read_text(encoding="utf-8").splitlines()
        if row.startswith("QTS_PREVIEW_PASSWORD_HASH=")
    )
    raw = line.split("=", 1)[1]
    assert raw.startswith("'") and raw.endswith("'")
    assert raw.count("$") == 2
    assert raw.count("$$") == 0


def test_invalid_shell_value_stops_before_writing_an_env_file(tmp_path, monkeypatch):
    for key, value in _values().items():
        monkeypatch.setenv(key, value)
    monkeypatch.setenv("QTS_PREVIEW_COMMIT", "short")
    report = tmp_path / "preview-stack.json"
    env_out = tmp_path / "preview.env"
    code = gate.main(
        [
            "--compose-file",
            "compose.yml",
            "--env-out",
            str(env_out),
            "--json-out",
            str(report),
        ]
    )
    assert code == 1
    assert "INVALID_COMMIT" in json.loads(report.read_text(encoding="utf-8"))["problems"]
    assert not env_out.exists()
