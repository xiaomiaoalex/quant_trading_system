from __future__ import annotations

import importlib.util
import sys
from pathlib import Path
from types import ModuleType
from typing import Any


def _load_script_module() -> ModuleType:
    script_path = (
        Path(__file__).resolve().parents[2] / "scripts" / "rehearse_crypto_risk_demo_fail_closed.py"
    )
    spec = importlib.util.spec_from_file_location(
        "rehearse_crypto_risk_demo_fail_closed",
        script_path,
    )
    assert spec is not None
    module = importlib.util.module_from_spec(spec)
    assert spec.loader is not None
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


class FakeClient:
    def __init__(
        self,
        *,
        runtime: dict[str, Any] | None = None,
        probe: dict[str, Any] | None = None,
        events: list[dict[str, Any]] | None = None,
        orders_before: list[dict[str, Any]] | None = None,
        orders_after: list[dict[str, Any]] | None = None,
    ) -> None:
        self.runtime = runtime or {
            "enabled": True,
            "wired": True,
            "fail_closed": False,
            "execution_env": "demo",
        }
        self.probe = probe or {
            "ok": False,
            "read_only": True,
            "requested_by": "fail-closed-test",
            "symbols": ["QTSFAILCLOSEDUSDT"],
            "checks": {
                "mark_prices": {"status": "failed"},
                "instrument_specs": {"status": "failed"},
                "venue_health": {"status": "passed"},
            },
        }
        self.events = (
            events
            if events is not None
            else [
                {
                    "event_type": "crypto_risk.probe_run",
                    "payload": {
                        "ok": False,
                        "read_only": True,
                        "requested_by": "fail-closed-test",
                        "symbols": ["QTSFAILCLOSEDUSDT"],
                    },
                }
            ]
        )
        self.orders_before = orders_before or [{"cl_ord_id": "existing"}]
        self.orders_after = orders_after if orders_after is not None else list(self.orders_before)
        self.calls: list[tuple[str, str, dict[str, Any] | None]] = []
        self._orders_calls = 0

    def get_json(self, path: str) -> Any:
        self.calls.append(("GET", path, None))
        if path.startswith("/v1/risk/crypto/runtime"):
            return self.runtime
        if path.startswith("/v1/orders"):
            self._orders_calls += 1
            return self.orders_before if self._orders_calls == 1 else self.orders_after
        if path.startswith("/v1/events"):
            return self.events
        raise AssertionError(f"unexpected GET {path}")

    def post_json(self, path: str, payload: dict[str, Any]) -> Any:
        self.calls.append(("POST", path, payload))
        if path == "/v1/risk/crypto/probe":
            self.probe["requested_by"] = payload["requested_by"]
            self.probe["symbols"] = payload["symbols"]
            return self.probe
        raise AssertionError(f"unexpected POST {path}")


def test_fail_closed_rehearsal_passes_when_failed_probe_is_audited() -> None:
    module = _load_script_module()
    client = FakeClient()

    report = module.run_fail_closed_rehearsal(
        client=client,
        symbols=("QTSFAILCLOSEDUSDT",),
        requested_by="fail-closed-test",
    )

    assert report.ok is True
    assert report.probe_ok is False
    assert report.audit_event_found is True
    assert report.orders_unchanged is True
    assert report.failed_checks == ("instrument_specs", "mark_prices")
    assert report.errors == ()
    assert all(not call[1].startswith("/v1/orders/") for call in client.calls)


def test_fail_closed_rehearsal_fails_if_negative_probe_passes() -> None:
    module = _load_script_module()
    client = FakeClient(
        probe={
            "ok": True,
            "read_only": True,
            "checks": {"venue_health": {"status": "passed"}},
        }
    )

    report = module.run_fail_closed_rehearsal(
        client=client,
        symbols=("QTSFAILCLOSEDUSDT",),
        requested_by="fail-closed-test",
    )

    assert report.ok is False
    assert {issue.code for issue in report.errors} == {
        "PROBE_UNEXPECTEDLY_PASSED",
        "PROBE_NO_FAILED_CHECKS",
        "AUDIT_EVENT_MISMATCH",
    }


def test_fail_closed_rehearsal_requires_probe_audit_event() -> None:
    module = _load_script_module()
    client = FakeClient(events=[])

    report = module.run_fail_closed_rehearsal(
        client=client,
        symbols=("QTSFAILCLOSEDUSDT",),
        requested_by="fail-closed-test",
    )

    assert report.ok is False
    assert {issue.code for issue in report.errors} == {"AUDIT_EVENT_MISSING"}


def test_fail_closed_rehearsal_detects_order_state_change() -> None:
    module = _load_script_module()
    client = FakeClient(orders_after=[{"cl_ord_id": "existing"}, {"cl_ord_id": "new"}])

    report = module.run_fail_closed_rehearsal(
        client=client,
        symbols=("QTSFAILCLOSEDUSDT",),
        requested_by="fail-closed-test",
    )

    assert report.ok is False
    assert {issue.code for issue in report.errors} == {"ORDER_STATE_CHANGED"}
