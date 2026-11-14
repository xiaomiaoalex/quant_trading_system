"""Regression tests for the isolated Compose smoke gate.

The original smoke script reported ``passed`` without checking that every
started container actually became healthy, and without proving that the
task-owned containers and volumes were cleaned up.  A container stuck in
``starting`` and a leaked volume could therefore coexist with a green result.
These tests pin the contract used by the release workflow.
"""

from __future__ import annotations

from scripts.compose_smoke import compose_argv, evaluate_evidence, resource_identity


def _evidence(**overrides):
    evidence = {
        "health": {"postgres": "healthy", "control": "healthy", "alert-sink": "healthy"},
        "started_containers_expected": ["postgres", "control", "alert-sink"],
        "health_semantics_ok": True,
        "control_credential_violations": [],
        "execution_credential_reference": ["BINANCE_API_KEY_FILE", "BINANCE_SECRET_KEY_FILE"],
        "credential_refusal": {"exit_code": 2, "reason_code_present": True},
        "non_demo_refusal": {"exit_code": 2, "reason_code_present": True},
        "alert_delivery_ok": True,
        "existing_resources_untouched": True,
        "cleanup_exit_code": 0,
        "remaining_containers": {},
        "remaining_volumes": {},
        "image_digest_ok": True,
    }
    evidence.update(overrides)
    return evidence


def test_all_started_containers_must_be_healthy():
    reasons = evaluate_evidence(_evidence(health={"postgres": "healthy", "control": "starting"}))
    assert "CONTAINER_NOT_HEALTHY:control:starting" in reasons
    # A container that was started but has no health observation cannot pass.
    assert "CONTAINER_MISSING_HEALTH:alert-sink" in reasons
    assert "CONTAINER_MISSING_HEALTH:postgres" not in reasons


def test_container_without_a_healthcheck_cannot_pass():
    reasons = evaluate_evidence(
        _evidence(
            health={"postgres": "healthy", "control": "healthy", "alert-sink": "none"},
        )
    )
    assert "CONTAINER_NOT_HEALTHY:alert-sink:none" in reasons


def test_cleanup_failure_and_leaked_resources_fail_the_drill():
    reasons = evaluate_evidence(
        _evidence(
            cleanup_exit_code=1,
            remaining_containers={"control": "qts_p5_deadbeef-control-1"},
            remaining_volumes={"qts_p5_deadbeef_qts_pgdata": "vol-1"},
        )
    )
    assert "CLEANUP_FAILED:1" in reasons
    assert "CONTAINERS_LEAKED:control" in reasons
    assert "VOLUMES_LEAKED:qts_p5_deadbeef_qts_pgdata" in reasons


def test_untouched_existing_resources_must_match_exactly():
    reasons = evaluate_evidence(_evidence(existing_resources_untouched=False))
    assert "EXISTING_RESOURCES_CHANGED" in reasons


def test_fixed_image_must_carry_a_verified_digest():
    reasons = evaluate_evidence(_evidence(image_digest_ok=False))
    assert "IMAGE_DIGEST_INVALID" in reasons


def test_non_demo_execution_refusal_is_required():
    reasons = evaluate_evidence(
        _evidence(non_demo_refusal={"exit_code": 2, "reason_code_present": False})
    )
    assert "NON_DEMO_REFUSAL_FAILED" in reasons


def test_clean_evidence_has_no_reasons():
    assert evaluate_evidence(_evidence()) == []


def test_compose_argv_keeps_every_overlay_in_order():
    argv = compose_argv(
        ["compose.yml", "deploy/compose.demo.yml"], "qts_demo_test", "config", "--quiet"
    )
    assert argv == [
        "docker",
        "compose",
        "-f",
        "compose.yml",
        "-f",
        "deploy/compose.demo.yml",
        "-p",
        "qts_demo_test",
        "config",
        "--quiet",
    ]


def test_resource_identity_records_absence_instead_of_skipping(monkeypatch):
    from scripts import compose_smoke

    calls: list[list[str]] = []

    def fake_run(argv, *, env, cwd=None, timeout=600):
        calls.append(argv)

        class Result:
            returncode = 1
            stdout = ""
            stderr = "not found"

        return Result()

    monkeypatch.setattr(compose_smoke, "_run", fake_run)
    identity = resource_identity({}, "qts_test_project")
    assert identity["container"] == {"present": False}
    assert identity["volume"] == {"present": False}
    assert identity["compose_volumes"] == {}
    assert any("qts-postgres" in " ".join(argv) for argv in calls)
