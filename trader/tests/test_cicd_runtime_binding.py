"""Runtime release-binding collector counterexamples (parent F-P2).

A strict observation window must be bound to facts inspected on the actual
Demo host, not to a hand-written JSON that merely repeats operator inputs.
"""

from __future__ import annotations

from scripts.collect_runtime_release_binding import build_binding, runtime_identity_from_health

from trader.api.routes.health import _release_identity

DIGEST = "sha256:" + "a" * 64
COMMIT = "c6fe450260eeb59d46c9caee591fb1625510b443"
RELEASE_ID = "rel-1"


def _containers():
    services = ("postgres", "alert-sink", "control", "execution", "research", "history", "backup")
    containers = {}
    for service in services:
        app = service not in {"postgres", "alert-sink"}
        containers[service] = {
            "name": f"qts-{service}-1",
            "present": True,
            "running": True,
            "health": "healthy",
            "image": (f"ghcr.io/example/qts@{DIGEST}" if app else "postgres:16@sha256:" + "b" * 64),
            "image_id": "sha256:" + "c" * 64,
            "labels": (
                {
                    "com.qts.release.id": RELEASE_ID,
                    "com.qts.release.commit": COMMIT,
                    "com.qts.environment": "demo",
                }
                if app
                else {}
            ),
        }
    return containers


def _health():
    return {
        "live": {"status": 200, "body": {"status": "ok"}},
        "ready": {"status": 200, "body": {"status": "ok"}},
        "trading": {"status": 200, "body": {"ready": True, "reasons": []}},
    }


def _runtime():
    return {
        "release_id": RELEASE_ID,
        "commit": COMMIT,
        "image_digest": DIGEST,
        "environment": "demo",
        "instance_owner": "exec-1",
        "lease_token": "7",
    }


def _build(**overrides):
    return build_binding(
        project="qts_demo",
        containers=overrides.pop("containers", _containers()),
        health=overrides.pop("health", _health()),
        expected_release_id=overrides.pop("release_id", RELEASE_ID),
        expected_commit=overrides.pop("commit", COMMIT),
        expected_digest=overrides.pop("digest", DIGEST),
        runtime=overrides.pop("runtime", _runtime()),
    )


def test_complete_runtime_binding_is_accepted():
    binding = _build()
    assert binding["errors"] == []
    assert binding["runtime_inspected"] is True
    assert all(binding["checks"].values())
    assert binding["containers"]["control"]["labels"]["com.qts.release.id"] == RELEASE_ID


def test_missing_container_and_unhealthy_container_are_rejected():
    containers = _containers()
    containers.pop("execution")
    containers["control"]["health"] = "starting"
    binding = _build(containers=containers)
    assert "CONTAINER_MISSING:execution" in binding["errors"]
    assert "CONTAINER_NOT_HEALTHY:control:starting" in binding["errors"]
    assert binding["checks"]["all_healthy"] is False


def test_label_and_digest_mismatches_are_rejected():
    containers = _containers()
    containers["control"]["labels"]["com.qts.release.commit"] = "0" * 40
    containers["execution"]["image"] = "ghcr.io/example/qts@sha256:" + "d" * 64
    binding = _build(containers=containers)
    assert "LABEL_RELEASE_COMMIT_MISMATCH:control" in binding["errors"]
    assert "IMAGE_DIGEST_MISMATCH:execution" in binding["errors"]
    assert binding["checks"]["labels_match"] is False
    assert binding["checks"]["image_digest_match"] is False


def test_trading_not_ready_is_rejected():
    health = _health()
    health["trading"] = {
        "status": 503,
        "body": {"ready": False, "reasons": ["DR_EVIDENCE_MISSING"]},
    }
    binding = _build(health=health)
    assert "HEALTH_TRADING_NOT_READY" in binding["errors"]
    assert binding["checks"]["trading_ready"] is False


def test_missing_runtime_identity_is_rejected():
    binding = _build(runtime={})
    assert "RUNTIME_IDENTITY_MISSING" in binding["errors"]


def test_runtime_identity_of_another_release_is_rejected():
    runtime = dict(_runtime(), image_digest="sha256:" + "d" * 64)
    binding = _build(runtime=runtime)
    assert "RUNTIME_IDENTITY_MISMATCH:image_digest" in binding["errors"]


def test_runtime_identity_without_owner_is_rejected():
    runtime = dict(_runtime(), instance_owner="")
    binding = _build(runtime=runtime)
    assert "RUNTIME_IDENTITY_MISSING:instance_owner" in binding["errors"]


def test_runtime_identity_is_read_from_the_health_response():
    health = _health()
    health["trading"]["body"] = {
        "ready": True,
        "release": {
            "release_id": RELEASE_ID,
            "commit": COMMIT,
            "image_digest": DIGEST,
            "environment": "demo",
        },
        "instance_owner": "exec-1",
        "lease_token": "7",
    }
    identity = runtime_identity_from_health(health)
    assert identity["release_id"] == RELEASE_ID
    assert identity["commit"] == COMMIT
    assert identity["image_digest"] == DIGEST
    assert identity["environment"] == "demo"
    assert identity["instance_owner"] == "exec-1"
    assert identity["lease_token"] == "7"


def test_health_release_identity_comes_from_the_process_environment(monkeypatch):
    monkeypatch.setenv("QTS_RELEASE_ID", RELEASE_ID)
    monkeypatch.setenv("QTS_RELEASE_COMMIT", COMMIT.upper())
    monkeypatch.setenv("QTS_RELEASE_IMAGE", "ghcr.io/example/qts@" + DIGEST)
    monkeypatch.setenv("BINANCE_ENV", "DEMO")
    assert _release_identity() == {
        "release_id": RELEASE_ID,
        "commit": COMMIT,
        "image_digest": DIGEST,
        "environment": "demo",
    }
