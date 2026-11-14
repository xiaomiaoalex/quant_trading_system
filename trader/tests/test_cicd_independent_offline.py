"""Independent offline counterexamples for the CI/CD delivery (workflow v3).

Expectations come from ``docs/CI_CD_WORK_ORDER.md`` (C1-C6), the RFC and the
contracts, not from the engineer's pass report.  Every check drives a real
entry point with adversarial input; a YAML/config parse or a previous green run
is never accepted as behavioural evidence.

This module is pre-classified as the ``offline`` group.  It is intentionally
strict: the offline CI job fails on any skip or xfail, so nothing here may
depend on an optional dependency.
"""

from __future__ import annotations

import asyncio
import copy
import hashlib
import importlib.util
import json
import os
import re
import socket
import subprocess
import sys
import textwrap
from decimal import Decimal
from pathlib import Path
from types import SimpleNamespace

import pytest
from scripts import (
    check_junit,
    compose_smoke,
    deploy_gate,
    deploy_preflight,
    release_manifest,
    summarize_observation,
    verify_release_provenance,
    verify_test_manifest,
)

from trader.ops.dr_evidence import evaluate_dr_evidence
from trader.tests.test_trading_service import FakeExchange, bootstrap, command, make_service
from trader.trading.models import Execution
from trader.trading.store import MemoryTradingStore

ROOT = Path(__file__).resolve().parents[2]
TESTS_DIR = ROOT / "trader" / "tests"
CI_GATE = ROOT / ".github" / "workflows" / "ci-gate.yml"
MANIFEST_PATH = ROOT / "ci" / "test_manifest.json"
CONFTEST_PATH = TESTS_DIR / "conftest.py"

_COMMIT = "c6fe450260eeb59d46c9caee591fb1625510b443"
_DIGEST = "sha256:" + "a" * 64
_PREVIOUS_DIGEST = "sha256:" + "b" * 64
_NOW_MS = 1_760_000_000_000
# 2026-11-04T00:00:00Z: strict windows anchor to real epoch milliseconds so the
# binding's generated_at is validated against the actual sample window.
_WINDOW_START_MS = 1_793_750_400_000
_SECRET_DB_PASSWORD = "Db-S3cret-Value-9911"
_SECRET_ALERT = "Alert-S3cret-Value-9911"
_SECRET_HASH = "$2b$12$HashS3cretValue9911abcdefghijklmnopqrstuvwxyz"
_SECRET_WEBHOOK = "https://alerts.example.com/qts?token=WebhookS3cret9911"

_DESTRUCTIVE_SQL = re.compile(
    r"\b(DROP\s+(TABLE|COLUMN|SCHEMA|TYPE)|TRUNCATE|ALTER\s+COLUMN|DELETE\s+FROM)\b",
    re.IGNORECASE,
)


def _load_tests_conftest():
    spec = importlib.util.spec_from_file_location("qts_tests_conftest_probe", CONFTEST_PATH)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _manifest_payload() -> dict:
    return json.loads(MANIFEST_PATH.read_text(encoding="utf-8"))


def _verify_manifest(manifest: dict, tmp_path: Path) -> tuple[int, dict]:
    manifest_path = tmp_path / "manifest.json"
    manifest_path.write_text(json.dumps(manifest), encoding="utf-8")
    report_path = tmp_path / "report.json"
    code = verify_test_manifest.main(
        [
            "--manifest",
            str(manifest_path),
            "--no-workflow-check",
            "--json-out",
            str(report_path),
        ]
    )
    return code, json.loads(report_path.read_text(encoding="utf-8"))


def _aggregator_source() -> str:
    lines = CI_GATE.read_text(encoding="utf-8").splitlines()
    anchor = next(
        index for index, line in enumerate(lines) if "NEEDS_JSON: ${{ toJSON(needs) }}" in line
    )
    start = next(index for index in range(anchor, len(lines)) if "python - <<'PY'" in lines[index])
    block = []
    for line in lines[start + 1 :]:
        if line.strip() == "PY":
            break
        block.append(line)
    assert block, "CI Required aggregator script not found"
    return textwrap.dedent("\n".join(block))


def _run_aggregator(needs: dict) -> subprocess.CompletedProcess:
    environment = dict(os.environ)
    environment["NEEDS_JSON"] = json.dumps(needs)
    return subprocess.run(
        [sys.executable, "-c", _aggregator_source()],
        cwd=str(ROOT),
        env=environment,
        capture_output=True,
        text=True,
        timeout=120,
    )


def _modified_ci_gate(tmp_path: Path, transform) -> Path:
    text = CI_GATE.read_text(encoding="utf-8")
    gate = verify_test_manifest._job_block(text, "ci-required")
    assert gate is not None
    updated = text.replace(gate, transform(gate), 1)
    path = tmp_path / "ci-gate.yml"
    path.write_text(updated, encoding="utf-8")
    return path


def _check_workflows_with(manifest: dict, tmp_path: Path, transform) -> list[str]:
    path = _modified_ci_gate(tmp_path, transform)
    others = tuple(
        workflow
        for workflow in verify_test_manifest.DEFAULT_WORKFLOWS
        if workflow.name != "ci-gate.yml"
    )
    report = verify_test_manifest.Report()
    verify_test_manifest._check_workflows(manifest, (path, *others), report)
    return report.errors


# --------------------------------------------------------------------------- #
# C1: an unclassified test file must never escape every required gate.
# --------------------------------------------------------------------------- #


def test_unclassified_test_file_fails_the_manifest_gate(tmp_path):
    manifest = _manifest_payload()
    victim = "test_cicd_independent_offline.py"
    for entries in manifest["groups"].values():
        if victim in entries:
            entries.remove(victim)
    code, report = _verify_manifest(manifest, tmp_path)
    assert code == 1
    assert "TEST_FILE_UNCLASSIFIED: " + victim in report["errors"]


def test_duplicate_classification_is_rejected(tmp_path):
    manifest = _manifest_payload()
    victim = manifest["groups"]["p0"][0]
    manifest["groups"]["offline"].append(victim)
    code, report = _verify_manifest(manifest, tmp_path)
    assert code == 1
    assert any(error.startswith("TEST_FILE_DUPLICATE: " + victim) for error in report["errors"])


def test_stale_manifest_entry_is_rejected(tmp_path):
    manifest = _manifest_payload()
    manifest["groups"]["offline"].append("test_ci_cd_ghost_file.py")
    code, report = _verify_manifest(manifest, tmp_path)
    assert code == 1
    assert "TEST_FILE_STALE: offline: test_ci_cd_ghost_file.py" in report["errors"]


def test_missing_and_empty_groups_are_rejected(tmp_path):
    manifest = _manifest_payload()
    del manifest["groups"]["ops"]
    code, report = _verify_manifest(manifest, tmp_path)
    assert code == 1
    assert "TEST_GROUP_MISSING: ops" in report["errors"]

    manifest = _manifest_payload()
    manifest["groups"]["external"] = []
    code, report = _verify_manifest(manifest, tmp_path)
    assert code == 1
    assert "TEST_GROUP_EMPTY: external" in report["errors"]


def test_group_without_ci_job_and_stale_subset_are_rejected(tmp_path):
    manifest = _manifest_payload()
    del manifest["ci_jobs"]["postgres-integration"]
    code, report = _verify_manifest(manifest, tmp_path)
    assert code == 1
    assert "TEST_GROUP_WITHOUT_CI_JOB: postgres" in report["errors"]

    manifest = _manifest_payload()
    manifest["ci_subsets"]["research_ai"].append("test_ghost_subset_entry.py")
    code, report = _verify_manifest(manifest, tmp_path)
    assert code == 1
    assert "CI_SUBSET_STALE: research_ai: test_ghost_subset_entry.py" in report["errors"]


def test_invalid_manifest_json_is_rejected(tmp_path):
    manifest_path = tmp_path / "manifest.json"
    manifest_path.write_text("{not valid json", encoding="utf-8")
    report_path = tmp_path / "report.json"
    code = verify_test_manifest.main(
        ["--manifest", str(manifest_path), "--no-workflow-check", "--json-out", str(report_path)]
    )
    payload = json.loads(report_path.read_text(encoding="utf-8"))
    assert code == 1
    assert any(error.startswith("TEST_MANIFEST_INVALID_JSON") for error in payload["errors"])


def test_repository_manifest_classifies_every_file_exactly_once(tmp_path, capsys):
    code = verify_test_manifest.main(["--json-out", str(tmp_path / "manifest.json")])
    captured = capsys.readouterr()
    payload = json.loads(captured.out)
    assert code == 0, payload["errors"]
    discovered = sorted(path.name for path in TESTS_DIR.glob("test_*.py"))
    assert payload["total_test_files"] == len(discovered)
    # The count is derived from disk (integration re-derivation after phase 6):
    # every classified file is accounted for in exactly one group.
    assert payload["total_test_files"] == sum(payload["groups"].values())
    assert payload["total_test_files"] > 0
    for group in ("p0", "offline", "loopback", "postgres", "ops", "external"):
        assert payload["groups"].get(group, 0) > 0


def test_workflow_raw_file_selection_and_k_filter_are_rejected(tmp_path):
    path = tmp_path / "ci-gate.yml"
    path.write_text(
        "\n".join(
            [
                "jobs:",
                "  p0-gate:",
                "    steps:",
                "      - run: python -m pytest -q trader/tests/test_binance_connector.py",
                "  offline-gate:",
                "    steps:",
                "      - run: python -m pytest -q trader/tests -k balance",
                "  ci-required:",
                "    needs:",
                "      - p0-gate",
                "",
            ]
        ),
        encoding="utf-8",
    )
    report = verify_test_manifest.Report()
    verify_test_manifest._check_workflows({"ci_jobs": {}}, (path,), report)
    assert any(error.startswith("CI_RAW_TEST_SELECTION:") for error in report.errors)
    assert any(error.startswith("CI_SELECTION_NARROWING:") for error in report.errors)


def test_aggregate_gate_must_depend_on_every_required_non_test_job(tmp_path):
    manifest = _manifest_payload()
    target = "frontend-gate"
    errors = _check_workflows_with(
        manifest, tmp_path, lambda gate: gate.replace("      - " + target + "\n", "", 1)
    )
    missing = [error for error in errors if error.startswith("CI_REQUIRED_GATE_MISSING_NEED:")]
    assert missing == ["CI_REQUIRED_GATE_MISSING_NEED: " + target]


def test_aggregate_gate_must_not_include_optional_jobs(tmp_path):
    manifest = _manifest_payload()
    errors = _check_workflows_with(
        manifest,
        tmp_path,
        lambda gate: gate.replace("    needs:\n", "    needs:\n      - optional-ops-drills\n", 1),
    )
    assert "CI_REQUIRED_GATE_OPTIONAL_NEED: optional-ops-drills" in errors


def test_aggregate_gate_flags_non_test_job_missing_from_workflow(tmp_path):
    manifest = _manifest_payload()
    text = CI_GATE.read_text(encoding="utf-8")
    block = verify_test_manifest._job_block(text, "frontend-gate")
    assert block is not None
    path = tmp_path / "ci-gate.yml"
    path.write_text(text.replace(block, "", 1), encoding="utf-8")
    others = tuple(
        workflow
        for workflow in verify_test_manifest.DEFAULT_WORKFLOWS
        if workflow.name != "ci-gate.yml"
    )
    report = verify_test_manifest.Report()
    verify_test_manifest._check_workflows(manifest, (path, *others), report)
    assert "CI_REQUIRED_NON_TEST_JOB_MISSING: frontend-gate" in report.errors
    assert "CI_REQUIRED_GATE_MISSING_NEED: frontend-gate" not in report.errors


def test_aggregate_gate_flags_invalid_non_test_job_declaration(tmp_path):
    manifest = _manifest_payload()
    manifest["ci_required_non_test_jobs"] = "frontend-gate"
    errors = _check_workflows_with(manifest, tmp_path, lambda gate: gate)
    assert "CI_REQUIRED_NON_TEST_JOBS_INVALID" in errors


def test_aggregate_gate_is_always_evaluated_and_needs_every_job():
    text = CI_GATE.read_text(encoding="utf-8")
    block = verify_test_manifest._job_block(text, "ci-required")
    assert block is not None
    assert "if: always()" in block
    for job in _manifest_payload()["ci_required_non_test_jobs"]:
        assert "      - " + job in block


def test_ci_required_aggregator_rejects_any_non_success_result():
    base = {
        "p0-gate": {"result": "success"},
        "postgres-integration": {"result": "success"},
    }
    assert _run_aggregator(base).returncode == 0
    for bad in ("failure", "skipped", "cancelled", None):
        needs = dict(base)
        needs["frontend-gate"] = {"result": bad}
        completed = _run_aggregator(needs)
        combined = completed.stdout + completed.stderr
        assert completed.returncode != 0, bad
        assert "CI_REQUIRED_FAILED" in combined, bad
        assert "frontend-gate" in combined, bad


def test_new_unclassified_file_makes_pytest_exit_4():
    assert os.environ.get("QTS_TEST_NETWORK_POLICY") == "offline"
    probe = TESTS_DIR / "test_zz_qts_unclassified_probe.py"
    assert not probe.exists()
    probe.write_text("def test_probe():\n    assert True\n", encoding="utf-8")
    try:
        completed = subprocess.run(
            [sys.executable, "-m", "pytest", str(probe), "-q", "-p", "no:cacheprovider"],
            cwd=str(ROOT),
            env=dict(os.environ),
            capture_output=True,
            text=True,
            timeout=600,
        )
    finally:
        probe.unlink(missing_ok=True)
    assert not probe.exists()
    combined = completed.stdout + completed.stderr
    assert completed.returncode == 4, combined
    assert "not classified in ci/test_manifest.json" in combined


def test_collection_hook_rejects_unclassified_items(monkeypatch):
    conftest = _load_tests_conftest()
    monkeypatch.setenv("QTS_TEST_GROUP", "")
    monkeypatch.setenv("QTS_TEST_SUBSET", "")
    victim = TESTS_DIR / "test_zz_not_in_manifest.py"
    fake_item = SimpleNamespace(
        path=victim,
        fspath=str(victim),
        add_marker=lambda marker: None,
    )
    config = SimpleNamespace(_qts_group_map=conftest._load_manifest()[0], _qts_subsets={})
    with pytest.raises(pytest.UsageError, match="not classified"):
        conftest.pytest_collection_modifyitems(config, [fake_item])


def test_unknown_test_group_is_a_usage_error(monkeypatch):
    conftest = _load_tests_conftest()
    monkeypatch.setenv("QTS_TEST_GROUP", "not-a-real-group")
    with pytest.raises(pytest.UsageError, match="unknown QTS_TEST_GROUP"):
        conftest.pytest_configure(SimpleNamespace())


def test_comma_separated_group_selection_is_supported(monkeypatch):
    conftest = _load_tests_conftest()
    monkeypatch.setenv("QTS_TEST_GROUP", "p0,offline")
    monkeypatch.setenv("QTS_TEST_SUBSET", "")
    monkeypatch.setenv("QTS_TEST_NETWORK_POLICY", "offline")
    config = SimpleNamespace()
    conftest.pytest_configure(config)
    assert set(conftest._selected_groups()) == {"p0", "offline"}

    offline_item = SimpleNamespace(
        path=TESTS_DIR / "test_cicd_independent_offline.py",
        fspath=str(TESTS_DIR / "test_cicd_independent_offline.py"),
        add_marker=lambda marker: None,
    )
    postgres_item = SimpleNamespace(
        path=TESTS_DIR / "test_cicd_independent_postgres.py",
        fspath=str(TESTS_DIR / "test_cicd_independent_postgres.py"),
        add_marker=lambda marker: None,
    )
    items = [offline_item, postgres_item]
    conftest.pytest_collection_modifyitems(config, items)
    assert items == [offline_item]


# --------------------------------------------------------------------------- #
# C1: offline socket policy must block public traffic, DNS and loopback.
# --------------------------------------------------------------------------- #


def test_offline_policy_blocks_public_and_loopback_connects():
    assert os.environ.get("QTS_TEST_NETWORK_POLICY") == "offline"
    with pytest.raises(PermissionError, match="QTS_NETWORK_BLOCKED"):
        socket.create_connection(("93.184.216.34", 443), timeout=0.2)
    listener = socket.socket()
    try:
        listener.bind(("127.0.0.1", 0))
        listener.listen(1)
        port = listener.getsockname()[1]
        with pytest.raises(PermissionError, match="QTS_NETWORK_BLOCKED"):
            socket.create_connection(("127.0.0.1", port), timeout=0.2)
    finally:
        listener.close()


def test_offline_policy_blocks_dns_resolution():
    assert os.environ.get("QTS_TEST_NETWORK_POLICY") == "offline"
    with pytest.raises(PermissionError, match="QTS_NETWORK_BLOCKED"):
        socket.getaddrinfo("example.com", 443)


def test_offline_policy_keeps_the_asyncio_self_pipe_working():
    left, right = socket.socketpair()
    try:
        left.sendall(b"qts")
        assert right.recv(3) == b"qts"
    finally:
        left.close()
        right.close()


# --------------------------------------------------------------------------- #
# C1: JUnit evidence must never turn skips/xfails/zero tests into green.
# --------------------------------------------------------------------------- #


def _junit(path: Path, *, tests: int, skipped: int = 0, xfailed: int = 0, failures: int = 0):
    cases = [f'<testcase classname="c" name="t{index}" />' for index in range(tests)]
    if skipped:
        cases.append('<testcase classname="c" name="skip"><skipped /></testcase>')
    if xfailed:
        cases.append('<testcase classname="c" name="xf"><skipped type="pytest.xfail" /></testcase>')
    if failures:
        cases.append('<testcase classname="c" name="bad"><failure /></testcase>')
    path.write_text(
        "<testsuites><testsuite "
        f'tests="{tests + skipped + xfailed + failures}" failures="{failures}" '
        f'errors="0" skipped="{skipped + xfailed}">' + "".join(cases) + "</testsuite></testsuites>",
        encoding="utf-8",
    )
    return path


def _check_junit(monkeypatch, path: Path) -> tuple[int, dict]:
    output = path.with_suffix(".json")
    monkeypatch.setattr(
        sys,
        "argv",
        [
            "check_junit",
            str(path),
            "--require-zero-skipped",
            "--require-zero-xfailed",
            "--json-out",
            str(output),
        ],
    )
    code = check_junit.main()
    return code, json.loads(output.read_text(encoding="utf-8"))


def test_junit_rejects_skipped_and_xfailed_evidence(tmp_path, monkeypatch):
    code, payload = _check_junit(
        monkeypatch, _junit(tmp_path / "skips.xml", tests=2, skipped=1, xfailed=1)
    )
    assert code == 1
    assert payload["skipped"] == 2
    assert payload["xfailed"] == 1
    assert any(error.startswith("JUNIT_SKIPPED_NOT_ALLOWED") for error in payload["errors"])
    assert any(error.startswith("JUNIT_XFAILED_NOT_ALLOWED") for error in payload["errors"])


def test_junit_rejects_zero_tests_and_failures(tmp_path, monkeypatch):
    code, payload = _check_junit(monkeypatch, _junit(tmp_path / "empty.xml", tests=0))
    assert code == 1
    assert any(error.startswith("JUNIT_TOO_FEW_TESTS") for error in payload["errors"])

    code, payload = _check_junit(monkeypatch, _junit(tmp_path / "red.xml", tests=3, failures=1))
    assert code == 1
    assert any(error.startswith("JUNIT_NOT_GREEN") for error in payload["errors"])


def test_junit_accepts_a_green_zero_skip_run(tmp_path, monkeypatch):
    code, payload = _check_junit(monkeypatch, _junit(tmp_path / "green.xml", tests=5))
    assert code == 0
    assert payload["skipped"] == 0


# --------------------------------------------------------------------------- #
# C1: WS/REST same-fill concurrency through the unified trading service.
# Phase 6 removed the legacy OMS callback entry point; both WS and REST deliver
# executions to ``TradingService.record_execution``, so the adversarial input
# drives that single entry point against a real in-memory service.
# --------------------------------------------------------------------------- #

_ACCOUNT_ID = "acct"


def _execution(cl_ord_id: str, exec_id: str, *, qty: str, price: str, fee: str = "0") -> Execution:
    return Execution(
        _ACCOUNT_ID,
        cl_ord_id,
        "BTCUSDT",
        exec_id,
        Decimal(qty),
        Decimal(price),
        Decimal(fee),
        "USDT" if Decimal(fee) > 0 else "",
        1_000_100,
    )


def _net_fill_deltas(state) -> dict:
    """Net per-asset ledger movement produced by the fills booked after setup."""
    deltas: dict[str, Decimal] = {}
    for entry in state.ledger:
        if entry["kind"] not in {"trade", "fee"}:
            continue
        deltas[entry["asset"]] = deltas.get(entry["asset"], Decimal("0")) + Decimal(entry["delta"])
    return deltas


async def _service_with_open_order(*, qty: str) -> tuple:
    store = MemoryTradingStore()
    service = make_service(FakeExchange(), store=store)
    await bootstrap(service)
    receipt = await service.place(command(qty=Decimal(qty), price=Decimal("100")))
    return store, service, receipt.cl_ord_id


@pytest.mark.asyncio
async def test_ws_and_rest_same_execution_is_booked_exactly_once():
    store, service, cl_ord_id = await _service_with_open_order(qty="1")

    fill = _execution(cl_ord_id, "exec-1", qty="0.4", price="100", fee="0.1")
    results = await asyncio.gather(*(service.record_execution(fill) for _ in range(6)))

    assert results.count(True) == 1, results
    state = await store.read(_ACCOUNT_ID)
    assert list(state.executions) == [fill.key]
    assert Decimal(str(state.orders[cl_ord_id].booked_qty)) == Decimal("0.4")
    assert state.orders[cl_ord_id].status == "PARTIALLY_FILLED"
    # One fill with a USDT fee books exactly two trade legs and one fee leg.
    legs = [entry for entry in state.ledger if entry["kind"] in {"trade", "fee"}]
    assert len(legs) == 3
    assert _net_fill_deltas(state) == {"USDT": Decimal("-40.1"), "BTC": Decimal("0.4")}
    assert "EXECUTION_KEY_CONFLICT" not in [
        entry["code"] for entry in state.reconciliation["unresolved"]
    ]


@pytest.mark.asyncio
async def test_concurrent_distinct_executions_are_not_lost():
    store, service, cl_ord_id = await _service_with_open_order(qty="0.24")

    updates = []
    for index in range(12):
        updates.append(_execution(cl_ord_id, f"exec-a{index}", qty="0.01", price="100"))
        updates.append(_execution(cl_ord_id, f"exec-b{index}", qty="0.01", price="105"))
    booked_before = len((await store.read(_ACCOUNT_ID)).ledger)
    results = await asyncio.gather(*(service.record_execution(update) for update in updates))
    state = await store.read(_ACCOUNT_ID)

    assert results == [True] * len(updates), results
    assert len(state.executions) == 24
    assert set(state.executions) == {update.key for update in updates}
    assert Decimal(str(state.orders[cl_ord_id].booked_qty)) == Decimal("0.24")
    assert state.orders[cl_ord_id].status == "FILLED"
    # Ledger conservation over the fills only: 0.12 BTC at 100 and 0.12 BTC at
    # 105 spend exactly 24.6 USDT and receive 0.24 BTC (zero fee) - nothing
    # duplicated, nothing lost.
    fill_entries = state.ledger[booked_before:]
    deltas: dict[str, Decimal] = {}
    for entry in fill_entries:
        deltas[entry["asset"]] = deltas.get(entry["asset"], Decimal("0")) + Decimal(entry["delta"])
    assert deltas == {"USDT": Decimal("-24.6"), "BTC": Decimal("0.24")}


@pytest.mark.asyncio
async def test_replayed_execution_after_completion_is_ignored():
    store, service, cl_ord_id = await _service_with_open_order(qty="1")

    fill = _execution(cl_ord_id, "exec-replay", qty="0.4", price="100", fee="0.1")
    assert await service.record_execution(fill) is True
    ledger_before = len((await store.read(_ACCOUNT_ID)).ledger)
    assert await service.record_execution(fill) is False

    state = await store.read(_ACCOUNT_ID)
    assert list(state.executions) == [fill.key]
    assert Decimal(str(state.orders[cl_ord_id].booked_qty)) == Decimal("0.4")
    assert state.orders[cl_ord_id].status == "PARTIALLY_FILLED"
    # The replay booked no additional ledger entries.
    assert len(state.ledger) == ledger_before
    assert "EXECUTION_KEY_CONFLICT" not in [
        entry["code"] for entry in state.reconciliation["unresolved"]
    ]


# --------------------------------------------------------------------------- #
# C3: the Compose smoke verdict must fail on every degraded observation.
# --------------------------------------------------------------------------- #


def _smoke_evidence() -> dict:
    return {
        "started_containers_expected": ["control", "postgres"],
        "health": {"control": "healthy", "postgres": "healthy"},
        "health_semantics_ok": True,
        "control_credential_violations": [],
        "execution_credential_reference": ["BINANCE_API_KEY_FILE"],
        "credential_refusal": {"exit_code": 2, "reason_code_present": True},
        "non_demo_refusal": {"exit_code": 2, "reason_code_present": True},
        "alert_delivery_ok": True,
        "existing_resources_untouched": True,
        "image_digest_ok": True,
        "cleanup_exit_code": 0,
        "remaining_containers": {},
        "remaining_volumes": {},
    }


def test_compose_smoke_accepts_only_a_fully_clean_drill():
    assert compose_smoke.evaluate_evidence(_smoke_evidence()) == []


def test_compose_smoke_rejects_unhealthy_and_unmonitored_containers():
    evidence = _smoke_evidence()
    evidence["health"]["control"] = "starting"
    evidence["health"]["postgres"] = "none"
    reasons = compose_smoke.evaluate_evidence(evidence)
    assert "CONTAINER_NOT_HEALTHY:control:starting" in reasons
    assert "CONTAINER_NOT_HEALTHY:postgres:none" in reasons

    evidence = _smoke_evidence()
    del evidence["health"]["control"]
    assert "CONTAINER_MISSING_HEALTH:control" in compose_smoke.evaluate_evidence(evidence)


def test_compose_smoke_rejects_cleanup_residue_and_identity_change():
    evidence = _smoke_evidence()
    evidence["cleanup_exit_code"] = 1
    evidence["remaining_containers"] = {"qts_p5_x-control-1": "control"}
    evidence["remaining_volumes"] = {"qts_p5_x_qts_pgdata": {"present": True}}
    evidence["existing_resources_untouched"] = False
    reasons = compose_smoke.evaluate_evidence(evidence)
    assert "CLEANUP_FAILED:1" in reasons
    assert "CONTAINERS_LEAKED:qts_p5_x-control-1" in reasons
    assert "VOLUMES_LEAKED:qts_p5_x_qts_pgdata" in reasons
    assert "EXISTING_RESOURCES_CHANGED" in reasons


def test_compose_smoke_rejects_invalid_digest_and_refusals():
    evidence = _smoke_evidence()
    evidence["image_digest_ok"] = False
    evidence["credential_refusal"] = {"exit_code": 0, "reason_code_present": False}
    evidence["non_demo_refusal"] = {"exit_code": 1, "reason_code_present": True}
    reasons = compose_smoke.evaluate_evidence(evidence)
    assert "IMAGE_DIGEST_INVALID" in reasons
    assert "EXECUTION_CREDENTIAL_REFUSAL_FAILED" in reasons
    assert "NON_DEMO_REFUSAL_FAILED" in reasons


def test_compose_smoke_rejects_credential_leak_and_missing_reference():
    evidence = _smoke_evidence()
    evidence["control_credential_violations"] = ["BINANCE_SECRET_KEY"]
    evidence["execution_credential_reference"] = []
    reasons = compose_smoke.evaluate_evidence(evidence)
    assert "CONTROL_HAS_EXCHANGE_CREDENTIALS:BINANCE_SECRET_KEY" in reasons
    assert "EXECUTION_CREDENTIAL_REFERENCE_MISSING" in reasons


def test_compose_smoke_early_reason_does_not_mask_further_failures():
    evidence = _smoke_evidence()
    evidence["reason"] = "DOCKER_COMPOSE_UNAVAILABLE"
    evidence["health_semantics_ok"] = False
    evidence["alert_delivery_ok"] = False
    reasons = compose_smoke.evaluate_evidence(evidence)
    assert "DOCKER_COMPOSE_UNAVAILABLE" in reasons
    assert "HEALTH_SEMANTICS_FAILED" in reasons
    assert "ALERT_DELIVERY_FAILED" in reasons


# --------------------------------------------------------------------------- #
# C4: fail-closed Demo preflight with stable reason codes and no secret output.
# --------------------------------------------------------------------------- #


def _dr_evidence() -> dict:
    digest = "c" * 64
    return {
        "schema_version": 1,
        "backup": {
            "completed_at_ms": _NOW_MS - 1_000,
            "cadence_seconds": 300,
            "sha256": digest,
            "object_key": "qts/backups/probe.dump",
            "target": {
                "kind": "s3_compatible",
                "endpoint_host": "backup.example.internal",
                "off_host": True,
                "identity_verified": True,
                "attested_by": "operator",
            },
        },
        "restore": {
            "completed_at_ms": _NOW_MS - 1_000,
            "rto_seconds": 600,
            "backup_sha256": digest,
            "verified": True,
            "checks": {"facts_match": True, "invariants_ok": True},
            "source_database": {"host": "db-a", "database": "trading"},
            "target_database": {"host": "db-b", "database": "qts_restore_probe"},
        },
    }


def _preflight_env(tmp_path: Path) -> dict:
    dr = tmp_path / "dr.json"
    dr.write_text(json.dumps(_dr_evidence()), encoding="utf-8")
    schema = tmp_path / "schema.json"
    schema.write_text(
        json.dumps({"expand_only": True, "old_runtime_compatible": True, "schema_version": 3}),
        encoding="utf-8",
    )
    rollback = tmp_path / "rollback.json"
    rollback.write_text(
        json.dumps(
            {
                "compatible": True,
                "verified": True,
                "schema_version": 3,
                "previous_image_digest": _PREVIOUS_DIGEST,
                "release_image_digest": _DIGEST,
            }
        ),
        encoding="utf-8",
    )
    return {
        "BINANCE_ENV": "demo",
        "POSTGRES_PASSWORD": _SECRET_DB_PASSWORD,
        "QTS_ALERT_SIGNING_SECRET": _SECRET_ALERT,
        "QTS_AUTH_REQUIRED": "1",
        "QTS_PASSWORD_HASH": _SECRET_HASH,
        "QTS_ACCOUNT_ID": "binance_spot_demo",
        "QTS_SYMBOLS": "BTCUSDT,ETHUSDT",
        "QTS_INSTRUMENTS": json.dumps({"BTCUSDT": ["BTC", "USDT"], "ETHUSDT": ["ETH", "USDT"]}),
        "QTS_STRATEGY_MODULE": "trader.strategies.demo",
        "QTS_STRATEGY_WARMUP_EVENTS": "200",
        "QTS_EXTERNAL_ALERT_WEBHOOK_URL": _SECRET_WEBHOOK,
        "QTS_EXTERNAL_ALERT_HEARTBEAT_URL": _SECRET_WEBHOOK,
        "QTS_DR_REQUIRED": "1",
        "QTS_BACKUP_RPO_SECONDS": "300",
        "QTS_BACKUP_OFF_HOST_ATTESTED": "1",
        "QTS_BACKUP_ATTESTED_BY": "operator",
        "QTS_DR_EVIDENCE_PATH": str(dr),
        "QTS_RELEASE_ID": "rel-1",
        "QTS_RELEASE_COMMIT": _COMMIT,
        "QTS_RELEASE_IMAGE": "ghcr.io/example/qts@" + _DIGEST,
        "QTS_EXPECTED_DIGEST": _DIGEST,
        "QTS_SCHEMA_VERSION": "3",
        "QTS_SCHEMA_COMPAT_EVIDENCE_PATH": str(schema),
        "QTS_PREVIOUS_IMAGE_DIGEST": _PREVIOUS_DIGEST,
        "QTS_ROLLBACK_COMPAT_EVIDENCE_PATH": str(rollback),
    }


def _preflight(environ: dict, manifest: dict | None = None):
    return deploy_preflight.evaluate(environ, now_ms=_NOW_MS, release_manifest=manifest)


def _codes(report) -> set:
    return {(issue.code, issue.key) for issue in report.errors}


def test_preflight_accepts_a_fully_bound_demo_release(tmp_path):
    report = _preflight(_preflight_env(tmp_path))
    assert report.ok is True, report.to_dict()["errors"]
    assert report.release_digest == _DIGEST
    assert report.release_commit == _COMMIT


def test_preflight_rejects_non_demo_and_default_or_placeholder_secrets(tmp_path):
    environ = _preflight_env(tmp_path)
    environ["BINANCE_ENV"] = "production"
    environ["POSTGRES_PASSWORD"] = "trader_pwd"
    environ["QTS_ALERT_SIGNING_SECRET"] = "REPLACE_WITH_ALERT_SECRET"
    environ["QTS_AUTH_REQUIRED"] = "0"
    report = _preflight(environ)
    codes = _codes(report)
    assert ("BINANCE_ENV_NOT_DEMO", "BINANCE_ENV") in codes
    assert ("POSTGRES_PASSWORD_DEFAULT_OR_MISSING", "POSTGRES_PASSWORD") in codes
    assert ("ALERT_SIGNING_SECRET_MISSING", "QTS_ALERT_SIGNING_SECRET") in codes
    assert ("AUTH_REQUIRED_NOT_ENABLED", "QTS_AUTH_REQUIRED") in codes

    environ = _preflight_env(tmp_path)
    environ["POSTGRES_PASSWORD"] = ""
    assert ("POSTGRES_PASSWORD_DEFAULT_OR_MISSING", "POSTGRES_PASSWORD") in _codes(
        _preflight(environ)
    )


def test_preflight_rejects_wrong_account_symbols_and_warmup(tmp_path):
    environ = _preflight_env(tmp_path)
    environ["QTS_ACCOUNT_ID"] = "binance_spot"
    environ["QTS_INSTRUMENTS"] = json.dumps({"BTCUSDT": ["BTC", "USDT"]})
    environ["QTS_STRATEGY_WARMUP_EVENTS"] = "0"
    codes = _codes(_preflight(environ))
    assert ("ACCOUNT_ID_NOT_DEDICATED", "QTS_ACCOUNT_ID") in codes
    assert ("INSTRUMENT_SYMBOL_MISMATCH", "QTS_INSTRUMENTS") in codes
    assert ("STRATEGY_WARMUP_MISSING", "QTS_STRATEGY_WARMUP_EVENTS") in codes


def test_preflight_rejects_internal_alert_and_missing_dr(tmp_path):
    environ = _preflight_env(tmp_path)
    environ["QTS_EXTERNAL_ALERT_WEBHOOK_URL"] = "http://alert-sink:8085/events"
    environ["QTS_DR_REQUIRED"] = "0"
    environ["QTS_BACKUP_RPO_SECONDS"] = "3600"
    environ["QTS_BACKUP_OFF_HOST_ATTESTED"] = "0"
    codes = _codes(_preflight(environ))
    assert ("EXTERNAL_ALERT_MISSING", "QTS_EXTERNAL_ALERT_WEBHOOK_URL") in codes
    assert ("DR_REQUIRED_NOT_ENABLED", "QTS_DR_REQUIRED") in codes
    assert ("BACKUP_RPO_EXCEEDED", "QTS_BACKUP_RPO_SECONDS") in codes
    assert ("OFF_HOST_BACKUP_NOT_ATTESTED", "QTS_BACKUP_OFF_HOST_ATTESTED") in codes


def test_preflight_requires_an_external_https_heartbeat(tmp_path):
    environ = _preflight_env(tmp_path)
    environ.pop("QTS_EXTERNAL_ALERT_HEARTBEAT_URL")
    assert ("EXTERNAL_ALERT_HEARTBEAT_MISSING", "QTS_EXTERNAL_ALERT_HEARTBEAT_URL") in _codes(
        _preflight(environ)
    )

    for bad_url in ("http://alerts.example.internal/beat", "https://127.0.0.1/beat"):
        environ = _preflight_env(tmp_path)
        environ["QTS_EXTERNAL_ALERT_HEARTBEAT_URL"] = bad_url
        assert ("EXTERNAL_ALERT_HEARTBEAT_MISSING", "QTS_EXTERNAL_ALERT_HEARTBEAT_URL") in _codes(
            _preflight(environ)
        ), bad_url


def test_preflight_rejects_wrong_digest_and_mutable_tag(tmp_path):
    environ = _preflight_env(tmp_path)
    environ["QTS_EXPECTED_DIGEST"] = "sha256:" + "d" * 64
    assert ("RELEASE_IMAGE_DIGEST_MISMATCH", "QTS_EXPECTED_DIGEST") in _codes(_preflight(environ))

    environ = _preflight_env(tmp_path)
    environ["QTS_RELEASE_IMAGE"] = "ghcr.io/example/qts:latest"
    assert ("RELEASE_IMAGE_DIGEST_MISSING", "QTS_RELEASE_IMAGE") in _codes(_preflight(environ))


def test_preflight_rejects_missing_or_unbound_schema_and_rollback_evidence(tmp_path):
    environ = _preflight_env(tmp_path)
    environ["QTS_SCHEMA_COMPAT_EVIDENCE_PATH"] = str(tmp_path / "missing-schema.json")
    codes = _codes(_preflight(environ))
    assert ("SCHEMA_COMPAT_EVIDENCE_MISSING", "QTS_SCHEMA_COMPAT_EVIDENCE_PATH") in codes
    assert ("SCHEMA_COMPAT_EVIDENCE_INVALID", "QTS_SCHEMA_COMPAT_EVIDENCE_PATH") in codes

    environ = _preflight_env(tmp_path)
    schema = tmp_path / "not-expand-only.json"
    schema.write_text(
        json.dumps({"expand_only": False, "old_runtime_compatible": True, "schema_version": 3}),
        encoding="utf-8",
    )
    environ["QTS_SCHEMA_COMPAT_EVIDENCE_PATH"] = str(schema)
    assert ("SCHEMA_COMPAT_EVIDENCE_INVALID", "QTS_SCHEMA_COMPAT_EVIDENCE_PATH") in _codes(
        _preflight(environ)
    )

    environ = _preflight_env(tmp_path)
    rollback = tmp_path / "wrong-rollback.json"
    rollback.write_text(
        json.dumps(
            {
                "compatible": True,
                "verified": True,
                "schema_version": 3,
                "previous_image_digest": "sha256:" + "e" * 64,
                "release_image_digest": _DIGEST,
            }
        ),
        encoding="utf-8",
    )
    environ["QTS_ROLLBACK_COMPAT_EVIDENCE_PATH"] = str(rollback)
    assert ("ROLLBACK_DIGEST_MISMATCH", "QTS_ROLLBACK_COMPAT_EVIDENCE_PATH") in _codes(
        _preflight(environ)
    )


def test_preflight_rejects_release_manifest_mismatches(tmp_path):
    environ = _preflight_env(tmp_path)
    manifest = {
        "commit": "0" * 40,
        "image_digest": "sha256:" + "f" * 64,
        "release_id": "other-release",
    }
    codes = _codes(_preflight(environ, manifest))
    assert ("RELEASE_MANIFEST_COMMIT_MISMATCH", "QTS_RELEASE_MANIFEST") in codes
    assert ("RELEASE_MANIFEST_DIGEST_MISMATCH", "QTS_RELEASE_MANIFEST") in codes
    assert ("RELEASE_MANIFEST_ID_MISMATCH", "QTS_RELEASE_MANIFEST") in codes


def test_preflight_host_evidence_modes_cannot_impersonate_each_other(tmp_path):
    environ = _preflight_env(tmp_path)
    environ["QTS_DR_EVIDENCE_PATH"] = str(tmp_path / "missing-dr.json")
    for key in ("QTS_SCHEMA_COMPAT_EVIDENCE_PATH", "QTS_ROLLBACK_COMPAT_EVIDENCE_PATH"):
        environ.pop(key, None)

    remote = deploy_preflight.evaluate(environ, now_ms=_NOW_MS, host_evidence=False)
    assert remote.ok is True, remote.to_dict()["errors"]
    assert remote.host_evidence_evaluated is False
    warnings = {warning.code for warning in remote.warnings}
    # No release manifest is handed to the gate here, so the new advisory
    # warning must appear next to the deferred-host-evidence notice.
    assert {"HOST_EVIDENCE_DEFERRED", "RELEASE_MANIFEST_NOT_PROVIDED"} <= warnings

    full = deploy_preflight.evaluate(environ, now_ms=_NOW_MS)
    assert full.ok is False
    assert full.host_evidence_evaluated is True
    codes = {issue.code for issue in full.errors}
    assert {"DR_EVIDENCE_MISSING", "SCHEMA_COMPAT_EVIDENCE_MISSING"} <= codes
    assert "ROLLBACK_COMPAT_EVIDENCE_MISSING" in codes
    assert {warning.code for warning in full.warnings} == {"RELEASE_MANIFEST_NOT_PROVIDED"}

    # 13.17 distinguishes "path not configured" from "configured but missing".
    unconfigured = deploy_preflight.evaluate(
        {**environ, "QTS_DR_EVIDENCE_PATH": ""}, now_ms=_NOW_MS
    )
    assert "DR_EVIDENCE_PATH_MISSING" in {issue.code for issue in unconfigured.errors}


def test_preflight_never_serialises_secret_values(tmp_path, capsys, monkeypatch):
    environ = _preflight_env(tmp_path)
    report = _preflight(environ)
    serialised = json.dumps(report.to_dict())
    for secret in (_SECRET_DB_PASSWORD, _SECRET_ALERT, _SECRET_HASH, _SECRET_WEBHOOK):
        assert secret not in serialised

    environ["BINANCE_ENV"] = "production"
    env_file = tmp_path / "demo.env"
    env_file.write_text(
        "\n".join(f"{key}={value}" for key, value in environ.items()) + "\n", encoding="utf-8"
    )
    monkeypatch.setattr(
        sys,
        "argv",
        [
            "deploy_preflight",
            "--env-file",
            str(env_file),
            "--now-ms",
            str(_NOW_MS),
            "--json-out",
            str(tmp_path / "out.json"),
        ],
    )
    assert deploy_preflight.main() == 1
    captured = capsys.readouterr()
    output = captured.out + captured.err
    for secret in (_SECRET_DB_PASSWORD, _SECRET_ALERT, _SECRET_HASH, _SECRET_WEBHOOK):
        assert secret not in output
    assert "BINANCE_ENV_NOT_DEMO" in output


def test_dr_evidence_is_fail_closed_on_stale_or_unverified_backup():
    base = _dr_evidence()

    assert evaluate_dr_evidence(base, now_ms=_NOW_MS, rpo_seconds=300).ready is True

    stale = copy.deepcopy(base)
    stale["backup"]["completed_at_ms"] = _NOW_MS - 601_000
    result = evaluate_dr_evidence(stale, now_ms=_NOW_MS, rpo_seconds=300)
    assert result.ready is False
    assert "DR_BACKUP_STALE" in result.reasons

    cadence = copy.deepcopy(base)
    cadence["backup"]["cadence_seconds"] = 600
    assert (
        "DR_BACKUP_CADENCE_EXCEEDS_RPO"
        in evaluate_dr_evidence(cadence, now_ms=_NOW_MS, rpo_seconds=300).reasons
    )

    unverified = copy.deepcopy(base)
    unverified["restore"]["verified"] = False
    assert (
        "DR_RESTORE_UNVERIFIED"
        in evaluate_dr_evidence(unverified, now_ms=_NOW_MS, rpo_seconds=300).reasons
    )

    bare_verified = copy.deepcopy(base)
    del bare_verified["restore"]["checks"]
    assert (
        "DR_RESTORE_FACTS_UNVERIFIED"
        in evaluate_dr_evidence(bare_verified, now_ms=_NOW_MS, rpo_seconds=300).reasons
    )

    mismatched = copy.deepcopy(base)
    mismatched["restore"]["backup_sha256"] = "d" * 64
    assert (
        "DR_RESTORE_BACKUP_MISMATCH"
        in evaluate_dr_evidence(mismatched, now_ms=_NOW_MS, rpo_seconds=300).reasons
    )

    same_target = copy.deepcopy(base)
    same_target["restore"]["target_database"] = dict(same_target["restore"]["source_database"])
    assert (
        "DR_RESTORE_TARGET_NOT_SEPARATE"
        in evaluate_dr_evidence(same_target, now_ms=_NOW_MS, rpo_seconds=300).reasons
    )

    rto = copy.deepcopy(base)
    rto["restore"]["rto_seconds"] = 7200
    assert "DR_RTO_EXCEEDED" in evaluate_dr_evidence(rto, now_ms=_NOW_MS, rpo_seconds=300).reasons

    on_host = copy.deepcopy(base)
    on_host["backup"]["target"]["endpoint_host"] = "127.0.0.1"
    assert (
        "DR_TARGET_NOT_VERIFIED_OFF_HOST"
        in evaluate_dr_evidence(on_host, now_ms=_NOW_MS, rpo_seconds=300).reasons
    )


# --------------------------------------------------------------------------- #
# C5: observation gates must block degraded or unbound evidence.
# --------------------------------------------------------------------------- #


_MARKERS = [
    {"ts_ms": 30_000, "marker": "runtime_restart"},
    {"ts_ms": 60_000, "marker": "runtime_restart_end"},
    {"ts_ms": 90_000, "marker": "active_reconnect"},
]


def _metrics(**overrides) -> dict:
    metrics = {
        "reconciliation_age_ms": 1_000,
        "outbox_backlog": 0,
        "unbooked_fills": 0,
        "unknown_commands": 0,
        "balance_anomalies": 0,
        "unresolved": [],
    }
    metrics.update(overrides)
    return metrics


def _runtime_identity(**overrides) -> dict:
    identity = {
        "release_id": "rel-1",
        "commit": _COMMIT,
        "image_digest": _DIGEST,
        "environment": "demo",
        "instance_owner": "qts-demo-runner",
        "lease_token": "lease-1",
    }
    identity.update(overrides)
    return identity


def _binding_containers() -> dict:
    def container() -> dict:
        return {
            "present": True,
            "running": True,
            "health": "healthy",
            "image": "ghcr.io/example/qts@" + _DIGEST,
            "labels": {
                "com.qts.release.id": "rel-1",
                "com.qts.release.commit": _COMMIT,
                "com.qts.environment": "demo",
            },
        }

    return {"control": container(), "execution": container()}


def _sample(
    ts_ms: int,
    *,
    ready: bool = True,
    metrics: dict | None = None,
    runtime: dict | None = None,
) -> dict:
    payload = {"ts_ms": ts_ms, "health": {"body": {"ready": ready}}}
    if metrics is not None:
        payload["metrics"] = metrics
    if runtime is not None:
        payload["runtime"] = runtime
    return payload


def _window() -> list:
    return [_sample(ts, metrics=_metrics()) for ts in (0, 60_000, 120_000, 180_000, 240_000)]


def _anchored_sample(ts_ms: int) -> dict:
    return _sample(
        ts_ms, metrics=_metrics(), runtime={**_runtime_identity(), "observed_at_ms": ts_ms}
    )


def _strict_window() -> list:
    # Exactly 24h apart, anchored to real epoch milliseconds; every sample is
    # served by the same runtime identity the binding describes.
    return [_anchored_sample(_WINDOW_START_MS + offset) for offset in (0, 43_200_000, 86_400_000)]


def _strict_markers() -> list:
    return [
        {"ts_ms": _WINDOW_START_MS + 30_000, "marker": "runtime_restart"},
        {"ts_ms": _WINDOW_START_MS + 60_000, "marker": "runtime_restart_end"},
        {"ts_ms": _WINDOW_START_MS + 90_000, "marker": "active_reconnect"},
    ]


def _observe(
    monkeypatch,
    tmp_path: Path,
    samples: list,
    *,
    extra_args: tuple = (),
    max_gap: str = "180",
    markers: list | None = None,
):
    observation = tmp_path / "observation.jsonl"
    lines = [json.dumps(marker) for marker in (markers if markers is not None else _MARKERS)]
    lines.extend(json.dumps(sample) for sample in samples)
    observation.write_text("\n".join(lines) + "\n", encoding="utf-8")
    summary_path = tmp_path / "summary.json"
    argv = [
        "summarize_observation",
        "--input",
        str(observation),
        "--min-hours",
        "0.01",
        "--max-gap-seconds",
        max_gap,
        "--json-out",
        str(summary_path),
        *extra_args,
    ]
    monkeypatch.setattr(sys, "argv", argv)
    code = summarize_observation.main()
    return code, json.loads(summary_path.read_text(encoding="utf-8"))


def test_observation_accepts_a_clean_bound_window(monkeypatch, tmp_path):
    code, summary = _observe(monkeypatch, tmp_path, _window())
    assert code == 0
    assert summary["blockers"] == []
    assert summary["ready_ratio"] == 1.0


def test_observation_blocks_low_ready_ratio(monkeypatch, tmp_path):
    samples = [
        _sample(0, metrics=_metrics()),
        _sample(60_000, ready=False, metrics=_metrics()),
        _sample(120_000, ready=False, metrics=_metrics()),
        _sample(180_000, ready=False, metrics=_metrics()),
        _sample(240_000, metrics=_metrics()),
    ]
    code, summary = _observe(monkeypatch, tmp_path, samples)
    assert code == 1
    assert "READY_RATIO_BELOW_THRESHOLD" in summary["blockers"]


def test_observation_blocks_unknown_reconciliation_age(monkeypatch, tmp_path):
    samples = [
        _sample(0, metrics=_metrics()),
        _sample(60_000, metrics=_metrics()),
        _sample(120_000, metrics=_metrics()),
        _sample(180_000, metrics={"outbox_backlog": 0, "unbooked_fills": 0, "unresolved": []}),
        _sample(240_000, metrics={"outbox_backlog": 0, "unbooked_fills": 0, "unresolved": []}),
    ]
    code, summary = _observe(monkeypatch, tmp_path, samples)
    assert code == 1
    assert "RECONCILIATION_AGE_UNKNOWN" in summary["blockers"]


def test_observation_blocks_exceeded_reconciliation_age(monkeypatch, tmp_path):
    samples = _window()
    samples[2]["metrics"] = _metrics(reconciliation_age_ms=90_000)
    code, summary = _observe(monkeypatch, tmp_path, samples)
    assert code == 1
    assert "RECONCILIATION_AGE_EXCEEDED" in summary["blockers"]


def test_observation_blocks_final_outbox_and_unbooked_fills(monkeypatch, tmp_path):
    samples = _window()
    samples[-1]["metrics"] = _metrics(outbox_backlog=2, unbooked_fills=1)
    code, summary = _observe(monkeypatch, tmp_path, samples)
    assert code == 1
    assert "OUTBOX_BACKLOG_AT_END" in summary["blockers"]
    assert "UNBOOKED_FILLS_AT_END" in summary["blockers"]


def test_observation_blocks_duplicate_resend_and_overwrite_evidence(monkeypatch, tmp_path):
    samples = _window()
    samples[1]["metrics"] = _metrics(duplicate_fills=1)
    samples[3]["metrics"] = _metrics(unresolved=["RESEND_DETECTED"])
    code, summary = _observe(monkeypatch, tmp_path, samples)
    assert code == 1
    assert "DUPLICATE_OR_RESEND_EVIDENCE" in summary["blockers"]


def test_observation_normalises_string_and_boolean_counters(monkeypatch, tmp_path):
    samples = _window()
    samples[1]["metrics"] = _metrics(duplicate_fills="1")
    code, summary = _observe(monkeypatch, tmp_path, samples)
    assert code == 1
    assert "DUPLICATE_OR_RESEND_EVIDENCE" in summary["blockers"]

    samples = _window()
    samples[2]["metrics"] = _metrics(overwrite_detected=True)
    code, summary = _observe(monkeypatch, tmp_path, samples)
    assert code == 1
    assert "DUPLICATE_OR_RESEND_EVIDENCE" in summary["blockers"]

    samples = _window()
    samples[1]["metrics"] = _metrics(duplicate_fills="0")
    code, summary = _observe(monkeypatch, tmp_path, samples)
    assert code == 0, summary["blockers"]

    samples = _window()
    samples[-1]["metrics"] = _metrics(outbox_backlog="2", unbooked_fills="1")
    code, summary = _observe(monkeypatch, tmp_path, samples)
    assert code == 1
    assert "OUTBOX_BACKLOG_AT_END" in summary["blockers"]
    assert "UNBOOKED_FILLS_AT_END" in summary["blockers"]
    assert summary["final_outbox_backlog"] == 2


def test_observation_blocks_non_monotonic_samples_and_gaps(monkeypatch, tmp_path):
    samples = [
        _sample(0, metrics=_metrics()),
        _sample(0, metrics=_metrics()),
        _sample(60_000, metrics=_metrics()),
        _sample(120_000, metrics=_metrics()),
        _sample(180_000, metrics=_metrics()),
    ]
    code, summary = _observe(monkeypatch, tmp_path, samples)
    assert code == 1
    assert "NON_MONOTONIC_SAMPLES" in summary["blockers"]

    gapped = [
        _sample(0, metrics=_metrics()),
        _sample(600_000, metrics=_metrics()),
        _sample(660_000, metrics=_metrics()),
        _sample(720_000, metrics=_metrics()),
        _sample(780_000, metrics=_metrics()),
    ]
    code, summary = _observe(monkeypatch, tmp_path, gapped)
    assert code == 1
    assert "SAMPLE_GAP_EXCEEDED" in summary["blockers"]


def test_observation_blocks_short_window_and_final_not_ready(monkeypatch, tmp_path):
    samples = [
        _sample(0, metrics=_metrics()),
        _sample(60_000, metrics=_metrics()),
        _sample(120_000, metrics=_metrics()),
        _sample(180_000, metrics=_metrics()),
        _sample(240_000, ready=False, metrics=_metrics()),
    ]
    code, summary = _observe(monkeypatch, tmp_path, samples, extra_args=("--min-hours", "1.0"))
    assert code == 1
    assert "WINDOW_SHORTER_THAN_MIN" in summary["blockers"]
    assert "NOT_READY_AT_END" in summary["blockers"]


def test_observation_requires_completed_restart_marker(monkeypatch, tmp_path):
    observation = tmp_path / "observation.jsonl"
    lines = [json.dumps({"ts_ms": 90_000, "marker": "active_reconnect"})]
    lines.extend(json.dumps(sample) for sample in _window())
    observation.write_text("\n".join(lines) + "\n", encoding="utf-8")
    summary_path = tmp_path / "summary.json"
    monkeypatch.setattr(
        sys,
        "argv",
        [
            "summarize_observation",
            "--input",
            str(observation),
            "--min-hours",
            "0.01",
            "--json-out",
            str(summary_path),
        ],
    )
    assert summarize_observation.main() == 1
    summary = json.loads(summary_path.read_text(encoding="utf-8"))
    assert "MISSING_MARKER:runtime_restart" in summary["blockers"]


def test_observation_thresholds_are_explicit_and_auditable(monkeypatch, tmp_path):
    samples = _window()
    samples[-1]["metrics"] = _metrics(outbox_backlog=3)
    code, summary = _observe(
        monkeypatch,
        tmp_path,
        samples,
        extra_args=("--max-outbox-backlog", "5"),
    )
    assert code == 0
    assert summary["max_allowed_outbox_backlog"] == 5
    assert summary["final_outbox_backlog"] == 3


def test_observation_historical_compatibility_does_not_invent_blockers(monkeypatch, tmp_path):
    legacy = [
        _sample(ts, metrics={"outbox_backlog": 0, "unbooked_fills": 0, "unresolved": []})
        for ts in (0, 60_000, 120_000, 180_000, 240_000)
    ]
    code, summary = _observe(monkeypatch, tmp_path, legacy)
    assert code == 0
    assert summary["reconciliation_age_evidence_present"] is False
    assert "RECONCILIATION_AGE_UNKNOWN" not in summary["blockers"]


def _release_evidence(tmp_path: Path, drop: tuple = (), **overrides) -> Path:
    payload = {
        "release_id": "rel-1",
        "commit": _COMMIT,
        "image_digest": _DIGEST,
        "environment": "demo",
        "runtime_inspected": True,
        "errors": [],
        "checks": {
            "all_running": True,
            "all_healthy": True,
            "labels_match": True,
            "image_digest_match": True,
            "trading_ready": True,
        },
        "containers": _binding_containers(),
        "health": {
            "live": {"status": 200},
            "ready": {"status": 200},
            "trading": {"status": 200, "body": {"ready": True}},
        },
        "runtime": _runtime_identity(),
        # One hour after the 24h window that ends 2026-11-05T00:00:00Z.
        "generated_at": "2026-11-05T00:30:00Z",
    }
    payload.update(overrides)
    for key in drop:
        payload.pop(key, None)
    path = tmp_path / "release.json"
    path.write_text(json.dumps(payload), encoding="utf-8")
    return path


def _strict_args(evidence: Path) -> tuple:
    return (
        "--strict-release",
        "--release-evidence",
        str(evidence),
        "--expected-commit",
        _COMMIT,
        "--expected-digest",
        _DIGEST,
    )


def test_strict_release_requires_evidence(monkeypatch, tmp_path):
    code, summary = _observe(
        monkeypatch,
        tmp_path,
        _strict_window(),
        extra_args=("--strict-release",),
        max_gap="90000",
        markers=_strict_markers(),
    )
    assert code == 1
    assert "RELEASE_EVIDENCE_MISSING" in summary["blockers"]
    assert "STRICT_METRICS_MISSING:reconciliation_age_ms" not in summary["blockers"]


def test_strict_release_blocks_commit_digest_and_environment_mismatch(monkeypatch, tmp_path):
    evidence = _release_evidence(
        tmp_path,
        commit="0" * 40,
        image_digest="sha256:" + "e" * 64,
        environment="production",
    )
    code, summary = _observe(
        monkeypatch,
        tmp_path,
        _strict_window(),
        extra_args=_strict_args(evidence),
        max_gap="90000",
        markers=_strict_markers(),
    )
    assert code == 1
    blockers = set(summary["blockers"])
    assert {"RELEASE_COMMIT_MISMATCH", "RELEASE_DIGEST_MISMATCH"} <= blockers
    assert "RELEASE_ENVIRONMENT_NOT_DEMO" in blockers


def test_strict_release_requires_a_release_id(monkeypatch, tmp_path):
    evidence = _release_evidence(tmp_path, release_id="")
    code, summary = _observe(
        monkeypatch,
        tmp_path,
        _strict_window(),
        extra_args=_strict_args(evidence),
        max_gap="90000",
        markers=_strict_markers(),
    )
    assert code == 1
    assert "RELEASE_ID_MISSING" in summary["blockers"]


def test_strict_release_accepts_matching_runtime_bound_evidence(monkeypatch, tmp_path):
    evidence = _release_evidence(tmp_path)
    code, summary = _observe(
        monkeypatch,
        tmp_path,
        _strict_window(),
        extra_args=_strict_args(evidence),
        max_gap="90000",
        markers=_strict_markers(),
    )
    assert code == 0, summary["blockers"]
    binding = summary["release_binding"]
    assert binding["matched_expected_commit"] is True
    assert binding["matched_expected_digest"] is True
    assert binding["runtime_inspected"] is True
    assert binding["runtime"]["instance_owner"] == "qts-demo-runner"
    assert binding["generated_at"] == "2026-11-05T00:30:00Z"
    assert summary["sample_identity_checked"] is True
    assert summary["strict_min_hours"] == 24.0
    assert summary["effective_min_hours"] == 24.0


def _merge_binding_container(service: str, mutation: dict) -> dict:
    containers = _binding_containers()
    containers[service].update(mutation)
    return containers


def test_strict_release_rejects_evidence_not_bound_to_the_live_runtime(monkeypatch, tmp_path):
    tampered_labels = _binding_containers()
    tampered_labels["execution"]["labels"]["com.qts.release.commit"] = "0" * 40
    wrong_image = _binding_containers()
    wrong_image["execution"]["image"] = "ghcr.io/example/qts@sha256:" + "c" * 64
    cases = (
        ({"runtime_inspected": False}, "RELEASE_RUNTIME_BINDING_MISSING"),
        ({"errors": ["COLLECT_TIMEOUT"]}, "RELEASE_RUNTIME_ERROR_PRESENT:COLLECT_TIMEOUT"),
        ({"generated_at": "not-a-timestamp"}, "RELEASE_RUNTIME_GENERATED_AT_INVALID"),
        (("checks",), "RELEASE_RUNTIME_CHECKS_MISSING"),
        (
            {"checks": {"all_running": False, "all_healthy": True}},
            "RELEASE_RUNTIME_CHECK_FAILED:all_running",
        ),
        (("containers",), "RELEASE_RUNTIME_CONTAINERS_MISSING"),
        (
            {"containers": _merge_binding_container("control", {"running": False})},
            "RELEASE_RUNTIME_CONTAINER_NOT_RUNNING:control",
        ),
        (
            {"containers": _merge_binding_container("execution", {"present": False})},
            "RELEASE_RUNTIME_CONTAINER_NOT_PRESENT:execution",
        ),
        (
            {"containers": _merge_binding_container("execution", {"health": "restarting"})},
            "RELEASE_RUNTIME_CONTAINER_UNHEALTHY:execution:restarting",
        ),
        ({"containers": wrong_image}, "RELEASE_RUNTIME_CONTAINER_IMAGE_MISMATCH:execution"),
        ({"containers": tampered_labels}, "RELEASE_RUNTIME_LABEL_MISMATCH:execution:commit"),
        (("health",), "RELEASE_RUNTIME_HEALTH_MISSING"),
        (
            {"health": {"live": {"status": 200}, "ready": {"status": 503}}},
            "RELEASE_RUNTIME_HEALTH_NOT_READY:ready",
        ),
        (("runtime",), "RELEASE_RUNTIME_IDENTITY_MISSING"),
        (
            {"runtime": _runtime_identity(commit="0" * 40)},
            "RELEASE_RUNTIME_IDENTITY_MISMATCH:commit",
        ),
        ({"runtime": _runtime_identity(instance_owner="")}, "RELEASE_RUNTIME_OWNER_MISSING"),
    )
    for override, expected in cases:
        if isinstance(override, tuple):
            evidence = _release_evidence(tmp_path, drop=override)
        else:
            evidence = _release_evidence(tmp_path, **override)
        code, summary = _observe(
            monkeypatch,
            tmp_path,
            _strict_window(),
            extra_args=_strict_args(evidence),
            max_gap="90000",
            markers=_strict_markers(),
        )
        assert code == 1, expected
        assert expected in summary["blockers"], (expected, summary["blockers"])

    evidence = _release_evidence(tmp_path, drop=("runtime_inspected", "checks", "containers"))
    code, summary = _observe(
        monkeypatch,
        tmp_path,
        _strict_window(),
        extra_args=_strict_args(evidence),
        max_gap="90000",
        markers=_strict_markers(),
    )
    blockers = set(summary["blockers"])
    assert {
        "RELEASE_RUNTIME_BINDING_MISSING",
        "RELEASE_RUNTIME_CHECKS_MISSING",
        "RELEASE_RUNTIME_CONTAINERS_MISSING",
    } <= blockers


def test_strict_release_requires_every_sample_to_carry_the_binding_identity(monkeypatch, tmp_path):
    samples = _strict_window()
    samples[1].pop("runtime")
    code, summary = _observe(
        monkeypatch,
        tmp_path,
        samples,
        extra_args=_strict_args(_release_evidence(tmp_path)),
        max_gap="90000",
        markers=_strict_markers(),
    )
    assert code == 1
    assert "SAMPLE_RUNTIME_IDENTITY_MISSING" in summary["blockers"]

    samples = _strict_window()
    samples[-1]["runtime"] = _runtime_identity(commit="0" * 40, observed_at_ms=samples[-1]["ts_ms"])
    code, summary = _observe(
        monkeypatch,
        tmp_path,
        samples,
        extra_args=_strict_args(_release_evidence(tmp_path)),
        max_gap="90000",
        markers=_strict_markers(),
    )
    assert code == 1
    assert "SAMPLE_RUNTIME_IDENTITY_MISMATCH:commit" in summary["blockers"]

    samples = _strict_window()
    samples[0]["runtime"]["observed_at_ms"] = samples[0]["ts_ms"] + 600_000
    code, summary = _observe(
        monkeypatch,
        tmp_path,
        samples,
        extra_args=_strict_args(_release_evidence(tmp_path)),
        max_gap="90000",
        markers=_strict_markers(),
    )
    assert code == 1
    assert "SAMPLE_RUNTIME_TIMESTAMP_MISMATCH" in summary["blockers"]


def test_strict_release_binds_the_binding_to_the_observed_window(monkeypatch, tmp_path):
    evidence = _release_evidence(tmp_path, generated_at="2026-11-03T23:00:00Z")
    code, summary = _observe(
        monkeypatch,
        tmp_path,
        _strict_window(),
        extra_args=_strict_args(evidence),
        max_gap="90000",
        markers=_strict_markers(),
    )
    assert code == 1
    assert "RELEASE_BINDING_WINDOW_MISMATCH" in summary["blockers"]

    evidence = _release_evidence(tmp_path, generated_at="2026-11-09T00:30:00Z")
    code, summary = _observe(
        monkeypatch,
        tmp_path,
        _strict_window(),
        extra_args=_strict_args(evidence),
        max_gap="90000",
        markers=_strict_markers(),
    )
    assert code == 1
    assert "RELEASE_BINDING_WINDOW_MISMATCH" in summary["blockers"]


def test_strict_release_requires_every_sample_to_carry_explicit_metrics(monkeypatch, tmp_path):
    samples = _strict_window()
    samples[1]["metrics"].pop("outbox_backlog")
    code, summary = _observe(
        monkeypatch,
        tmp_path,
        samples,
        extra_args=_strict_args(_release_evidence(tmp_path)),
        max_gap="90000",
        markers=_strict_markers(),
    )
    assert code == 1
    assert "STRICT_METRICS_MISSING:outbox_backlog" in summary["blockers"]

    samples = _strict_window()
    samples[-1]["metrics"]["unbooked_fills"] = "2"
    code, summary = _observe(
        monkeypatch,
        tmp_path,
        samples,
        extra_args=_strict_args(_release_evidence(tmp_path)),
        max_gap="90000",
        markers=_strict_markers(),
    )
    assert code == 1
    assert "STRICT_METRICS_INVALID:unbooked_fills" in summary["blockers"]

    samples = _strict_window()
    samples[0]["metrics"] = None
    code, summary = _observe(
        monkeypatch,
        tmp_path,
        samples,
        extra_args=_strict_args(_release_evidence(tmp_path)),
        max_gap="90000",
        markers=_strict_markers(),
    )
    assert code == 1
    assert "STRICT_METRICS_MISSING:reconciliation_age_ms" in summary["blockers"]


def test_strict_release_enforces_the_24h_window_floor(monkeypatch, tmp_path):
    short = [_anchored_sample(_WINDOW_START_MS + offset) for offset in (0, 7_200_000, 14_400_000)]
    code, summary = _observe(
        monkeypatch,
        tmp_path,
        short,
        extra_args=_strict_args(_release_evidence(tmp_path)),
        max_gap="90000",
        markers=_strict_markers(),
    )
    assert code == 1
    blockers = set(summary["blockers"])
    assert {"STRICT_WINDOW_SHORTER_THAN_24H", "WINDOW_SHORTER_THAN_MIN"} <= blockers

    argv = [
        "summarize_observation",
        "--input",
        str(tmp_path / "observation.jsonl"),
        "--strict-release",
        "--strict-min-hours",
        "12",
    ]
    monkeypatch.setattr(sys, "argv", argv)
    with pytest.raises(SystemExit) as excinfo:
        summarize_observation.main()
    assert excinfo.value.code == 2


# --------------------------------------------------------------------------- #
# C5: migrations are expand-only; the detector is not vacuous.
# --------------------------------------------------------------------------- #


def test_destructive_statement_detector_is_not_vacuous():
    for statement in (
        "DROP TABLE trading_events;",
        "drop column payload",
        "TRUNCATE trading_ledger",
        "ALTER COLUMN qty TYPE numeric",
        "DELETE FROM trading_executions",
        "DROP SCHEMA public CASCADE",
    ):
        assert _DESTRUCTIVE_SQL.search(statement), statement


def test_every_migration_is_ordered_and_expand_only():
    from trader.trading.migrations import MIGRATIONS

    versions = [version for version, _ in MIGRATIONS]
    assert versions == sorted(versions)
    assert len(versions) == len(set(versions))
    assert versions == [2, 3]
    for _, filename in MIGRATIONS:
        sql = (ROOT / "trader" / "trading" / filename).read_text(encoding="utf-8")
        stripped = "\n".join(line.split("--", 1)[0] for line in sql.splitlines())
        assert "CREATE TABLE" in stripped, filename
        assert _DESTRUCTIVE_SQL.search(stripped) is None, filename


# --------------------------------------------------------------------------- #
# C3: release manifest, SBOM evidence and hashed dependency locks.
# --------------------------------------------------------------------------- #


def _startup_smoke(tmp_path: Path, **overrides) -> Path:
    payload = {
        "passed": True,
        "image": "ghcr.io/example/qts@" + _DIGEST,
        "image_digest_ok": True,
        "health": {"control": "healthy", "execution": "healthy"},
    }
    payload.update(overrides)
    path = tmp_path / "startup-smoke.json"
    path.write_text(json.dumps(payload), encoding="utf-8")
    return path


def _release_args(tmp_path: Path) -> SimpleNamespace:
    ci = tmp_path / "ci.txt"
    ci.write_text("pytest==8.3.4\n", encoding="utf-8")
    runtime = tmp_path / "runtime.txt"
    runtime.write_text("fastapi==0.1\n", encoding="utf-8")
    junit = _junit(tmp_path / "junit.xml", tests=2)
    coverage = tmp_path / "coverage.json"
    coverage.write_text('{"totals": {"percent_covered": 79.5}}\n', encoding="utf-8")
    sbom = tmp_path / "sbom.json"
    sbom.write_text('{"bomFormat": "CycloneDX"}\n', encoding="utf-8")
    scan = tmp_path / "scan.sarif"
    scan.write_text('{"version": "2.1.0"}\n', encoding="utf-8")
    frontend = tmp_path / "dist"
    frontend.mkdir(exist_ok=True)
    (frontend / "index.txt").write_text("qts-demo-bundle\n", encoding="utf-8")
    return SimpleNamespace(
        release_id="rel-1",
        commit=_COMMIT,
        ref="refs/heads/main",
        workflow="release-build",
        workflow_run="123",
        image_digest="ghcr.io/example/qts@" + _DIGEST,
        tree_clean=True,
        schema_version=3,
        environment="demo",
        ci_constraints=str(ci),
        runtime_constraints=str(runtime),
        junit=[str(junit)],
        coverage=str(coverage),
        frontend=str(frontend),
        sbom=str(sbom),
        scan=str(scan),
        startup_smoke=str(_startup_smoke(tmp_path)),
        allow_missing_evidence=False,
    )


def test_release_manifest_binds_real_dependency_hashes(tmp_path):
    args = _release_args(tmp_path)
    manifest = release_manifest.build_manifest(args)
    expected = hashlib.sha256((tmp_path / "ci.txt").read_bytes()).hexdigest()
    assert manifest["dependencies"]["ci_constraints"]["sha256"] == expected
    assert manifest["dependencies"]["runtime_constraints"]["present"] is True
    assert manifest["image_digest"] == _DIGEST
    assert manifest["commit"] == _COMMIT
    assert manifest["evidence"]["coverage"]["present"] is True
    assert manifest["evidence"]["sbom"]["present"] is True
    assert manifest["evidence"]["scan"]["present"] is True
    assert manifest["startup_smoke_summary"]["passed"] is True
    assert manifest["startup_smoke_summary"]["image"].endswith(_DIGEST)
    assert manifest["non_claims"]
    serialised = json.dumps(manifest).lower()
    assert "private key" not in serialised
    assert "binance_secret" not in serialised


def test_release_manifest_requires_complete_evidence_and_a_passing_smoke(tmp_path):
    args = _release_args(tmp_path)
    args.coverage = ""
    with pytest.raises(SystemExit, match="RELEASE_EVIDENCE_MISSING: coverage"):
        release_manifest.build_manifest(args)

    args = _release_args(tmp_path)
    args.startup_smoke = str(_startup_smoke(tmp_path, passed=False))
    with pytest.raises(SystemExit, match="RELEASE_STARTUP_SMOKE_NOT_PASSED"):
        release_manifest.build_manifest(args)

    args = _release_args(tmp_path)
    args.startup_smoke = str(_startup_smoke(tmp_path, image_digest_ok=False))
    with pytest.raises(SystemExit, match="RELEASE_STARTUP_SMOKE_DIGEST_NOT_VERIFIED"):
        release_manifest.build_manifest(args)

    args = _release_args(tmp_path)
    args.startup_smoke = str(
        _startup_smoke(tmp_path, image="ghcr.io/example/qts@sha256:" + "c" * 64)
    )
    with pytest.raises(SystemExit, match="RELEASE_STARTUP_SMOKE_DIGEST_MISMATCH"):
        release_manifest.build_manifest(args)

    args = _release_args(tmp_path)
    args.coverage = ""
    args.sbom = ""
    args.scan = ""
    args.startup_smoke = ""
    args.allow_missing_evidence = True
    manifest = release_manifest.build_manifest(args)
    assert manifest["evidence"]["coverage"] is None
    assert "startup_smoke_summary" not in manifest


def test_release_manifest_rejects_mutable_tag_and_short_commit(tmp_path):
    args = _release_args(tmp_path)
    args.image_digest = "ghcr.io/example/qts:latest"
    with pytest.raises(SystemExit):
        release_manifest.build_manifest(args)

    args = _release_args(tmp_path)
    args.commit = "abc123"
    with pytest.raises(SystemExit):
        release_manifest.build_manifest(args)


def test_release_manifest_directory_digest_is_content_addressed(tmp_path):
    tree = tmp_path / "dist"
    tree.mkdir()
    (tree / "app.js").write_text("console.log(1)\n", encoding="utf-8")
    args = _release_args(tmp_path)
    args.frontend = str(tree)
    first = release_manifest.build_manifest(args)["evidence"]["frontend"]["sha256"]
    second = release_manifest.build_manifest(args)["evidence"]["frontend"]["sha256"]
    assert first == second
    assert len(first) == 64

    (tree / "extra.js").write_text("console.log(2)\n", encoding="utf-8")
    third = release_manifest.build_manifest(args)["evidence"]["frontend"]["sha256"]
    assert third != first


def _requirement_lines(path: Path) -> list:
    text = path.read_text(encoding="utf-8").replace("\\\n", " ")
    return [
        line.strip()
        for line in text.splitlines()
        if line.strip() and not line.strip().startswith("#")
    ]


def test_repository_constraints_are_hash_pinned_not_hand_written():
    for name in (
        "trader/requirements-ci.constraints.txt",
        "trader/requirements-runtime.constraints.txt",
    ):
        path = ROOT / name
        assert path.is_file(), name
        lines = _requirement_lines(path)
        pinned = [line for line in lines if re.match(r"^[A-Za-z0-9_.\[\]-]+==", line)]
        assert len(pinned) >= 10, name
        for line in pinned:
            assert re.search(r"--hash=sha256:[0-9a-f]{64}\b", line), (name, line[:60])
        text = path.read_text(encoding="utf-8")
        assert "REPLACE_WITH" not in text
        assert "sha256:REPLACE_WITH" not in text


# --------------------------------------------------------------------------- #
# C2/C3/C4/C6: workflow and contract consistency (static, but anti-pattern).
# --------------------------------------------------------------------------- #


def test_ruleset_requires_only_the_aggregate_gate_with_strict_sync():
    ruleset = json.loads(
        (ROOT / "deploy" / "github-ruleset.ci-required.json").read_text(encoding="utf-8")
    )
    rules = {rule["type"]: rule for rule in ruleset["rules"]}
    checks = rules["required_status_checks"]["parameters"]
    assert checks["strict_required_status_checks_policy"] is True
    assert [item["context"] for item in checks["required_status_checks"]] == ["CI Required"]
    review = rules["pull_request"]["parameters"]
    assert review["required_approving_review_count"] >= 1
    assert review["dismiss_stale_reviews_on_push"] is True
    assert "deletion" in rules
    assert "non_fast_forward" in rules


def test_ruleset_migration_states_the_remote_fact_as_unexecuted():
    text = (ROOT / "deploy" / "RULESET_MIGRATION.md").read_text(encoding="utf-8")
    assert "未修改远端规则" in text
    assert "13369928" in text
    assert "CI Required" in text


def test_demo_compose_overlay_forbids_rebuild_and_pins_demo_environment():
    text = (ROOT / "deploy" / "compose.demo.yml").read_text(encoding="utf-8")
    assert "build: !reset null" in text
    assert "${QTS_RELEASE_IMAGE:?" in text
    assert "BINANCE_ENV: demo" in text
    assert "QTS_COMPOSE_PROJECT:?" in text

    example = (ROOT / "deploy" / "demo.env.example").read_text(encoding="utf-8")
    for line in example.splitlines():
        if line.startswith(
            ("POSTGRES_PASSWORD=", "QTS_PASSWORD_HASH=", "QTS_ALERT_SIGNING_SECRET=")
        ):
            assert "REPLACE_WITH" in line, line
    assert "$2b$" not in example


def test_deploy_workflow_defaults_to_dry_run_and_protects_the_host_step():
    text = (ROOT / ".github" / "workflows" / "deploy-demo.yml").read_text(encoding="utf-8")
    assert re.search(r"dry_run:\n(?:\s+.*\n)+?\s+default: true\n", text), text
    assert re.search(r"confirm_deploy:\n(?:\s+.*\n)+?\s+default: false\n", text), text
    assert "if: ${{ !inputs.dry_run && inputs.confirm_deploy }}" in text
    assert "runs-on: [self-hosted, qts-demo]" in text
    assert "environment: demo" in text

    preflight = verify_test_manifest._job_block(text, "preflight")
    deploy = verify_test_manifest._job_block(text, "deploy")
    assert preflight is not None and deploy is not None
    # Remote dry-run defers host-local evidence; the self-hosted deploy job must
    # re-run the full preflight before starting anything on the host.
    assert "--no-host-evidence" in preflight
    assert "--no-host-evidence" not in deploy


def test_optional_gates_do_not_swallow_failures():
    text = (ROOT / ".github" / "workflows" / "optional-gates.yml").read_text(encoding="utf-8")
    assert "continue-on-error" not in text
    assert "workflow_dispatch" in text or "schedule" in text


def _workflow_input_lines_stay_out_of_shells(text: str) -> list:
    # Every `${{ inputs.* }}` occurrence must sit in an env/with mapping or a
    # YAML metadata key; a bare shell command line carrying operator input is a
    # step injection and must be flagged.
    env_or_with = re.compile(r"^\s*(?:-\s+|[A-Za-z_][\w-]*\s*:)")
    return [
        line for line in text.splitlines() if "${{ inputs." in line and not env_or_with.match(line)
    ]


def test_demo_evidence_workflow_binds_inputs_to_the_release_manifest():
    text = (ROOT / ".github" / "workflows" / "demo-evidence.yml").read_text(encoding="utf-8")
    assert "release_build_run_id:" in text
    assert "run-id: ${{ inputs.release_build_run_id }}" in text
    # Operator input is validated in a dedicated step and only ever reaches a
    # shell through step environment variables (13.19).
    assert "Validate the operator inputs" in text
    for code in (
        "RELEASE_BUILD_RUN_ID_INVALID",
        "RELEASE_COMMIT_INVALID",
        "RELEASE_IMAGE_DIGEST_INVALID",
        "RELEASE_ID_INVALID",
        "OBSERVATION_HOURS_INVALID",
    ):
        assert code in text, code
    assert not _workflow_input_lines_stay_out_of_shells(text)
    # Release evidence is downloaded outside the checkout directory so the
    # second checkout cannot delete it; paths flow through RELEASE_EVIDENCE_DIR.
    assert "path: ${{ runner.temp }}/qts-release" in text
    assert "RELEASE_EVIDENCE_DIR: ${{ runner.temp }}/qts-release" in text
    assert '--manifest "$RELEASE_EVIDENCE_DIR/release-manifest.json"' in text
    # Provenance gate runs on the trusted default branch BEFORE the release
    # commit is checked out; the old inline EVIDENCE_MANIFEST_MISMATCH snippet
    # was replaced by scripts/verify_release_provenance.py.
    assert "scripts/verify_release_provenance.py" in text
    assert "ref: ${{ github.event.repository.default_branch }}" in text
    assert text.index("verify_release_provenance.py") < text.index("ref: ${{ inputs.commit }}")
    assert "collect_runtime_release_binding.py" in text
    assert "--strict-release" in text
    assert "--release-evidence artifacts/demo/release-binding.json" in text
    assert "--expected-commit" in text and "--expected-digest" in text
    assert "OBSERVATION_HOURS_BELOW_24" in text
    assert "runs-on: [self-hosted, qts-demo]" in text
    assert "BINANCE_ENV: demo" in text
    assert "QTS_DEMO_CONTROL_PORT" in text
    assert text.count("http://127.0.0.1:${QTS_DEMO_CONTROL_PORT:-18090}") >= 2
    # Demo secrets are injected per step, never exported job-wide.
    assert "QTS_E2E_PASSWORD: ${{ secrets.DEMO_E2E_PASSWORD }}" in text
    secrets = set(re.findall(r"secrets\.([A-Z_]+)", text))
    assert secrets and all(name.startswith("DEMO_") or name == "GITHUB_TOKEN" for name in secrets)


def _provenance_run(**overrides) -> dict:
    run = {
        "id": "123",
        "repository": {"full_name": "example/quant_trading_system"},
        "path": ".github/workflows/release-build.yml",
        "event": "workflow_run",
        "head_branch": "main",
        "status": "completed",
        "conclusion": "success",
        "head_sha": _COMMIT,
    }
    run.update(overrides)
    return run


def _provenance_manifest(**overrides) -> dict:
    manifest = {
        "release_id": "rel-1",
        "commit": _COMMIT,
        "image_digest": _DIGEST,
        "environment": "demo",
    }
    manifest.update(overrides)
    return manifest


def _provenance_errors(run: dict, manifest: dict) -> list:
    return verify_release_provenance.evaluate(
        run=run,
        manifest=manifest,
        repository="example/quant_trading_system",
        run_id="123",
        commit=_COMMIT,
        digest=_DIGEST,
        release_id="rel-1",
    )


def test_release_provenance_accepts_a_successful_main_workflow_run():
    assert _provenance_errors(_provenance_run(), _provenance_manifest()) == []


def test_release_provenance_rejects_untrusted_or_unbound_runs():
    cases = (
        ({"_run": {"id": "999"}}, "PROVENANCE_RUN_ID_MISMATCH"),
        ({"_run": {"path": ".github/workflows/other.yml"}}, "PROVENANCE_WORKFLOW_MISMATCH"),
        ({"_run": {"event": "push"}}, "PROVENANCE_EVENT_MISMATCH"),
        ({"_run": {"head_branch": "feature/x"}}, "PROVENANCE_BRANCH_MISMATCH"),
        ({"_run": {"status": "in_progress"}}, "PROVENANCE_RUN_NOT_COMPLETED"),
        ({"_run": {"conclusion": "failure"}}, "PROVENANCE_RUN_NOT_SUCCESSFUL"),
        ({"_run": {"head_sha": "0" * 40}}, "PROVENANCE_HEAD_SHA_MISMATCH"),
        ({"_manifest": {"commit": "0" * 40}}, "MANIFEST_COMMIT_MISMATCH"),
        ({"_manifest": {"image_digest": "sha256:" + "e" * 64}}, "MANIFEST_DIGEST_MISMATCH"),
        ({"_manifest": {"release_id": "rel-2"}}, "MANIFEST_RELEASE_ID_MISMATCH"),
        ({"_manifest": {"environment": "staging"}}, "MANIFEST_ENVIRONMENT_NOT_DEMO"),
    )
    for override, expected in cases:
        run = _provenance_run(**override.get("_run", {}))
        manifest = _provenance_manifest(**override.get("_manifest", {}))
        errors = _provenance_errors(run, manifest)
        assert expected in errors, (expected, errors)

    invalid = verify_release_provenance.evaluate(
        run=_provenance_run(),
        manifest=_provenance_manifest(),
        repository="example/quant_trading_system",
        run_id="123",
        commit="abc123",
        digest="ghcr.io/example/qts:latest",
        release_id="rel-1",
    )
    assert {"EXPECTED_COMMIT_INVALID", "EXPECTED_DIGEST_INVALID"} <= set(invalid)


def test_deploy_workflow_verifies_provenance_before_checking_out_the_release():
    text = (ROOT / ".github" / "workflows" / "deploy-demo.yml").read_text(encoding="utf-8")
    assert text.count("scripts/verify_release_provenance.py") >= 2
    assert "Validate the operator inputs" in text
    assert "RELEASE_EVIDENCE_DIR" in text
    assert not _workflow_input_lines_stay_out_of_shells(text)
    assert "ref: ${{ github.event.repository.default_branch }}" in text
    for job in ("preflight", "deploy"):
        block = verify_test_manifest._job_block(text, job)
        assert block is not None, job
        assert "verify_release_provenance.py" in block
    assert text.index("verify_release_provenance.py") < text.index("ref: ${{ inputs.commit }}")


# --------------------------------------------------------------------------- #
# F-P3: the deploy gate requires every convergence fact, never defaults to 0.
# --------------------------------------------------------------------------- #


def _gate_snapshot() -> dict:
    def container() -> dict:
        return {
            "present": True,
            "running": True,
            "health": "healthy",
            "image": "ghcr.io/example/qts@" + _DIGEST,
            "labels": {
                "com.qts.release.id": "rel-1",
                "com.qts.release.commit": _COMMIT,
                "com.qts.environment": "demo",
            },
        }

    containers = {service: container() for service in deploy_gate.EXPECTED_SERVICES}
    health = {
        "live": {"status": 200},
        "ready": {"status": 200},
        "trading": {"status": 200, "body": {"ready": True}},
    }
    facts = {
        "new_risk_blocked": True,
        "unknown_commands": 0,
        "queued_commands": 0,
        "sending_commands": 0,
        "outbox_pending": 0,
        "unbooked_fills": 0,
        "lease": {"owner": "runner-1", "fencing_token": "42", "valid": True},
        "runtime": {"owner": "runner-1", "token": "42", "ready": True},
    }
    return {"containers": containers, "health": health, "facts": facts}


def _gate_errors(snapshot: dict, *, mode: str = "pre") -> list:
    return deploy_gate.evaluate(
        mode=mode,
        containers=snapshot["containers"],
        health=snapshot["health"],
        facts=snapshot["facts"],
        expected_release_id="rel-1",
        expected_commit=_COMMIT,
        expected_digest=_DIGEST,
    )


def test_deploy_gate_requires_converged_facts_and_one_owner():
    assert _gate_errors(_gate_snapshot()) == []

    snapshot = _gate_snapshot()
    snapshot["facts"]["queued_commands"] = 2
    snapshot["facts"]["sending_commands"] = None
    snapshot["facts"].pop("unbooked_fills")
    errors = set(_gate_errors(snapshot))
    assert {
        "COMMANDS_NOT_CONVERGED:QUEUED",
        "FACTS_INVALID:sending_commands",
        "FACTS_INVALID:unbooked_fills",
    } <= errors

    snapshot = _gate_snapshot()
    snapshot["facts"]["lease"]["owner"] = "runner-2"
    assert "EXECUTION_OWNER_NOT_ALIGNED" in _gate_errors(snapshot)

    snapshot = _gate_snapshot()
    snapshot["facts"]["new_risk_blocked"] = False
    assert "NEW_RISK_NOT_BLOCKED" in _gate_errors(snapshot)

    snapshot = _gate_snapshot()
    snapshot["containers"]["control"]["labels"]["com.qts.release.commit"] = "0" * 40
    snapshot["containers"]["execution"]["image"] = "ghcr.io/example/qts@sha256:" + "c" * 64
    errors = set(_gate_errors(snapshot, mode="post"))
    assert {"LABEL_RELEASE_COMMIT_MISMATCH:control", "IMAGE_DIGEST_MISMATCH:execution"} <= errors

    snapshot = _gate_snapshot()
    snapshot["health"]["trading"] = {
        "status": 503,
        "body": {"ready": False, "reasons": ["DB_DOWN"]},
    }
    assert "HEALTH_TRADING_NOT_READY:DB_DOWN" in _gate_errors(snapshot, mode="post")

    snapshot = _gate_snapshot()
    snapshot["health"]["trading"] = {
        "status": 503,
        "body": {"ready": False, "reasons": ["NEW_RISK_BLOCKED"]},
    }
    assert "HEALTH_TRADING_NOT_READY" not in "".join(_gate_errors(snapshot, mode="post"))


def test_ci_gate_pins_the_postgres_integration_service_image():
    text = CI_GATE.read_text(encoding="utf-8")
    assert re.search(r"image:\s*postgres:16@sha256:[0-9a-f]{64}", text), text


def test_ci_gate_has_no_publish_permission_and_is_read_only():
    text = CI_GATE.read_text(encoding="utf-8")
    code_only = "\n".join(line for line in text.splitlines() if not line.lstrip().startswith("#"))
    assert "packages: write" not in code_only
    assert "permissions:" in code_only
    assert "contents: read" in code_only
    assert not re.search(r"^\s+packages:\s*write\s*$", code_only, re.MULTILINE)


def test_runbook_and_rfc_record_unexecuted_work_instead_of_claiming_it():
    runbook = (ROOT / "docs" / "CI_CD_RELEASE_RUNBOOK.md").read_text(encoding="utf-8")
    assert "--strict-release" in runbook
    assert "禁止自动回滚" in runbook
    rfc = (ROOT / "docs" / "CI_CD_IMPROVEMENT_RFC.md").read_text(encoding="utf-8")
    assert "### 6.3 未执行" in rfc
    assert "未执行项不得按通过处理" in rfc
