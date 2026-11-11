"""Pre/post Demo deployment gate counterexamples (parent F-P3)."""

from __future__ import annotations

from scripts.deploy_gate import collect_db_facts, evaluate, parse_boolean, parse_count, run_gate

DIGEST = "sha256:" + "a" * 64
COMMIT = "c6fe450260eeb59d46c9caee591fb1625510b443"
RELEASE_ID = "rel-1"


def _containers():
    services = ("postgres", "alert-sink", "control", "execution", "research", "history", "backup")
    out = {}
    for service in services:
        app = service not in {"postgres", "alert-sink"}
        out[service] = {
            "name": f"qts-{service}-1",
            "present": True,
            "running": True,
            "health": "healthy",
            "image": f"ghcr.io/example/qts@{DIGEST}" if app else "postgres:16",
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
    return out


def _facts(**overrides):
    facts = {
        "new_risk_blocked": True,
        "unknown_commands": 0,
        "queued_commands": 0,
        "sending_commands": 0,
        "outbox_pending": 0,
        "unbooked_fills": 0,
        "lease": {"owner": "exec-1", "fencing_token": "7", "valid": True},
        "runtime": {"owner": "exec-1", "token": "7", "ready": True},
    }
    facts.update(overrides)
    return facts


def _health(trading_ready: bool = True):
    return {
        "live": {"status": 200, "body": {"status": "ok"}},
        "ready": {"status": 200, "body": {"status": "ok"}},
        "trading": {
            "status": 200 if trading_ready else 503,
            "body": {"ready": trading_ready},
        },
    }


def _errors(mode="pre", containers=None, health=None, facts=None):
    return evaluate(
        mode=mode,
        containers=containers or _containers(),
        health=health or _health(),
        facts=facts or _facts(),
        expected_release_id=RELEASE_ID,
        expected_commit=COMMIT,
        expected_digest=DIGEST,
    )


def test_pre_gate_accepts_blocked_converged_single_owner():
    assert _errors("pre", health=_health(trading_ready=False)) == []


def test_pre_gate_requires_risk_block_and_convergence():
    errors = _errors(
        "pre",
        facts=_facts(new_risk_blocked=False, unknown_commands=1, outbox_pending=2),
    )
    assert "NEW_RISK_NOT_BLOCKED" in errors
    assert "UNKNOWN_COMMANDS_PRESENT" in errors
    assert "OUTBOX_PENDING" in errors


def test_pre_gate_requires_matching_valid_lease():
    errors = _errors(
        "pre",
        facts=_facts(
            lease={"owner": "old", "fencing_token": "6", "valid": False},
            runtime={"owner": "new", "token": "7", "ready": True},
        ),
    )
    assert "EXECUTION_LEASE_INVALID" in errors
    assert "EXECUTION_OWNER_NOT_ALIGNED" in errors
    assert "EXECUTION_FENCING_NOT_ALIGNED" in errors


def test_post_gate_requires_healthy_digest_and_ready_trading():
    containers = _containers()
    containers["control"]["health"] = "starting"
    containers["execution"]["labels"]["com.qts.release.id"] = "other"
    containers["execution"]["image"] = "ghcr.io/example/qts@sha256:" + "b" * 64
    errors = _errors(
        "post",
        containers=containers,
        health=_health(trading_ready=False),
        facts=_facts(unbooked_fills=1),
    )
    assert "CONTAINER_NOT_HEALTHY:control:starting" in errors
    assert "LABEL_RELEASE_ID_MISMATCH:execution" in errors
    assert "IMAGE_DIGEST_MISMATCH:execution" in errors
    assert any(error.startswith("HEALTH_TRADING_NOT_READY") for error in errors)
    assert "UNBOOKED_FILLS_PRESENT" in errors


def test_post_gate_accepts_a_fully_healthy_bound_stack():
    assert _errors("post") == []


def test_invalid_mode_is_rejected():
    assert _errors("other") == ["MODE_INVALID"]


def test_post_gate_accepts_only_the_intentional_new_risk_block():
    health = {
        "live": {"status": 200, "body": {"status": "ok"}},
        "ready": {"status": 200, "body": {"status": "ok"}},
        "trading": {
            "status": 503,
            "body": {"ready": False, "reasons": ["NEW_RISK_BLOCKED"]},
        },
    }
    assert _errors("post", health=health) == []
    health["trading"]["body"]["reasons"] = ["NEW_RISK_BLOCKED", "ACCOUNT_SNAPSHOT_STALE"]
    errors = _errors("post", health=health)
    assert any(error.startswith("HEALTH_TRADING_NOT_READY") for error in errors)


def test_pre_gate_requires_command_convergence():
    errors = _errors("pre", facts=_facts(queued_commands=1, sending_commands=1))
    assert "COMMANDS_NOT_CONVERGED:QUEUED" in errors
    assert "COMMANDS_NOT_CONVERGED:SENDING" in errors


def test_pre_gate_rejects_unbooked_fills():
    errors = _errors("pre", facts=_facts(unbooked_fills=1))
    assert "UNBOOKED_FILLS_PRESENT" in errors


def test_missing_facts_fail_closed():
    facts = _facts()
    facts.pop("queued_commands")
    facts.pop("lease")
    errors = _errors("pre", facts=facts)
    assert "FACTS_INVALID:queued_commands" in errors
    assert "FACTS_INVALID:lease" in errors


def test_unknown_new_risk_state_fails_closed():
    errors = _errors("pre", facts=_facts(new_risk_blocked=None))
    assert "NEW_RISK_STATE_UNKNOWN" in errors


def test_psql_boolean_and_count_parsing():
    # `psql -tAc` renders booleans as t/f and counts as plain text.
    assert parse_boolean("t") is True
    assert parse_boolean("f") is False
    assert parse_boolean("true") is True
    assert parse_boolean("") is None
    assert parse_boolean("maybe") is None
    assert parse_count("0") == 0
    assert parse_count("3") == 3
    assert parse_count("") is None
    assert parse_count("abc") is None


def test_post_gate_polls_until_the_stack_converges():
    attempts = {"count": 0}

    def collect_snapshot():
        attempts["count"] += 1
        containers = _containers()
        if attempts["count"] < 3:
            containers["control"]["health"] = "starting"
        return {
            "containers": containers,
            "health": _health(),
            "facts": _facts(),
        }

    def evaluate_snapshot(snapshot):
        return evaluate(
            mode="post",
            containers=snapshot["containers"],
            health=snapshot["health"],
            facts=snapshot["facts"],
            expected_release_id=RELEASE_ID,
            expected_commit=COMMIT,
            expected_digest=DIGEST,
        )

    slept = []
    snapshot, errors = run_gate(
        mode="post",
        collect_snapshot=collect_snapshot,
        evaluate_snapshot=evaluate_snapshot,
        wait_timeout=600,
        poll_interval=15,
        sleep_fn=slept.append,
    )
    assert errors == []
    assert snapshot["attempts"] == 3
    assert slept == [15, 15]


def test_post_gate_stops_at_the_bounded_timeout():
    def collect_snapshot():
        return {
            "containers": _containers(),
            "health": _health(),
            "facts": _facts(unbooked_fills=1),
        }

    def evaluate_snapshot(snapshot):
        return evaluate(
            mode="post",
            containers=snapshot["containers"],
            health=snapshot["health"],
            facts=snapshot["facts"],
            expected_release_id=RELEASE_ID,
            expected_commit=COMMIT,
            expected_digest=DIGEST,
        )

    clock = {"now": 0.0}
    slept = []

    def fake_sleep(seconds):
        slept.append(seconds)
        clock["now"] += seconds

    snapshot, errors = run_gate(
        mode="post",
        collect_snapshot=collect_snapshot,
        evaluate_snapshot=evaluate_snapshot,
        wait_timeout=30,
        poll_interval=15,
        sleep_fn=fake_sleep,
        clock=lambda: clock["now"],
    )
    assert "UNBOOKED_FILLS_PRESENT" in errors
    assert snapshot["attempts"] == 3


def test_pre_gate_never_waits():
    calls = {"count": 0}

    def collect_snapshot():
        calls["count"] += 1
        return {"containers": _containers(), "health": _health(), "facts": _facts(unbooked_fills=1)}

    def evaluate_snapshot(snapshot):
        return evaluate(
            mode="pre",
            containers=snapshot["containers"],
            health=snapshot["health"],
            facts=snapshot["facts"],
            expected_release_id=RELEASE_ID,
            expected_commit=COMMIT,
            expected_digest=DIGEST,
        )

    snapshot, errors = run_gate(
        mode="pre",
        collect_snapshot=collect_snapshot,
        evaluate_snapshot=evaluate_snapshot,
        wait_timeout=600,
        poll_interval=15,
        sleep_fn=lambda seconds: pytest_fail("the pre gate must not sleep"),
    )
    assert calls["count"] == 1
    assert "UNBOOKED_FILLS_PRESENT" in errors


def pytest_fail(message):
    raise AssertionError(message)


def test_collect_db_facts_parses_t_f_and_text_boolean_rendering(monkeypatch):
    # psql -tAc: bare boolean -> t/f; boolean concatenated into text -> true/false.
    values = iter(["t", "1", "1", "1", "1", "1", "exec-1|7|t", "exec-1|7|true"])
    monkeypatch.setattr(
        "scripts.deploy_gate._psql", lambda compose_files, project, query: next(values)
    )
    facts = collect_db_facts(["unused.yml"], "unused", "acct-1")
    assert facts["new_risk_blocked"] is True
    assert facts["queued_commands"] == 1
    assert facts["sending_commands"] == 1
    assert facts["lease"]["valid"] is True
    assert facts["runtime"]["ready"] is True

    values = iter(["f", "0", "0", "0", "0", "0", "exec-1|7|f", "exec-1|7|false"])
    monkeypatch.setattr(
        "scripts.deploy_gate._psql", lambda compose_files, project, query: next(values)
    )
    facts = collect_db_facts(["unused.yml"], "unused", "acct-1")
    assert facts["new_risk_blocked"] is False
    assert facts["lease"]["valid"] is False
    assert facts["runtime"]["ready"] is False
