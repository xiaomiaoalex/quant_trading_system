from __future__ import annotations

import argparse
import json
import time
from dataclasses import dataclass
from typing import Any, Literal, Protocol, Sequence
from urllib.error import HTTPError, URLError
from urllib.parse import urlencode
from urllib.request import Request, urlopen

IssueSeverity = Literal["error", "warning"]
DEFAULT_SYMBOL = "QTSFAILCLOSEDUSDT"
DEFAULT_BASE_URL = "http://127.0.0.1:8080"


class JsonClient(Protocol):
    def get_json(self, path: str) -> Any: ...

    def post_json(self, path: str, payload: dict[str, Any]) -> Any: ...


@dataclass(frozen=True, slots=True)
class RehearsalIssue:
    severity: IssueSeverity
    code: str
    message: str


@dataclass(frozen=True, slots=True)
class FailClosedRehearsalReport:
    ok: bool
    requested_by: str
    symbols: tuple[str, ...]
    runtime_enabled: bool
    runtime_wired: bool
    runtime_fail_closed: bool
    probe_ok: bool | None
    probe_read_only: bool | None
    failed_checks: tuple[str, ...]
    audit_event_found: bool
    orders_unchanged: bool
    errors: tuple[RehearsalIssue, ...]
    warnings: tuple[RehearsalIssue, ...]


class UrlJsonClient:
    def __init__(self, base_url: str, *, timeout_seconds: float = 30.0) -> None:
        self._base_url = base_url.rstrip("/")
        self._timeout_seconds = timeout_seconds

    def get_json(self, path: str) -> Any:
        return self._request_json("GET", path)

    def post_json(self, path: str, payload: dict[str, Any]) -> Any:
        return self._request_json("POST", path, payload)

    def _request_json(
        self,
        method: str,
        path: str,
        payload: dict[str, Any] | None = None,
    ) -> Any:
        body: bytes | None = None
        headers = {"Accept": "application/json"}
        if payload is not None:
            body = json.dumps(payload).encode("utf-8")
            headers["Content-Type"] = "application/json"

        request = Request(
            f"{self._base_url}{path}",
            data=body,
            headers=headers,
            method=method,
        )
        try:
            with urlopen(request, timeout=self._timeout_seconds) as response:
                raw = response.read().decode("utf-8")
        except HTTPError as exc:
            raw_error = exc.read().decode("utf-8", errors="replace")
            raise RuntimeError(
                f"{method} {path} failed: status={exc.code}, body={raw_error}"
            ) from exc
        except URLError as exc:
            raise RuntimeError(f"{method} {path} failed: {exc}") from exc

        return json.loads(raw) if raw else None


def run_fail_closed_rehearsal(
    *,
    client: JsonClient,
    symbols: tuple[str, ...] = (DEFAULT_SYMBOL,),
    requested_by: str | None = None,
    order_limit: int = 2000,
) -> FailClosedRehearsalReport:
    rehearsal_requested_by = requested_by or f"fail-closed-rehearsal:{int(time.time() * 1000)}"
    started_ts_ms = int(time.time() * 1000)
    errors: list[RehearsalIssue] = []
    warnings: list[RehearsalIssue] = []

    runtime: dict[str, Any] = {}
    before_orders: list[Any] = []
    after_orders: list[Any] = []
    probe: dict[str, Any] | None = None
    events: list[dict[str, Any]] = []

    try:
        runtime = _expect_mapping(client.get_json("/v1/risk/crypto/runtime"), "runtime")
    except Exception as exc:
        errors.append(_error("RUNTIME_QUERY_FAILED", str(exc)))

    runtime_enabled = bool(runtime.get("enabled"))
    runtime_wired = bool(runtime.get("wired"))
    runtime_fail_closed = bool(runtime.get("fail_closed"))
    if runtime and not runtime_enabled:
        errors.append(_error("RUNTIME_DISABLED", "Crypto risk runtime is not enabled."))
    if runtime and not runtime_wired:
        errors.append(_error("RUNTIME_NOT_WIRED", "Crypto risk runtime is not wired."))
    if runtime and runtime_fail_closed:
        errors.append(_error("RUNTIME_ALREADY_FAIL_CLOSED", "Runtime is already fail-closed."))

    try:
        before_orders = _expect_list(
            client.get_json(f"/v1/orders?{urlencode({'limit': order_limit})}"),
            "orders_before",
        )
    except Exception as exc:
        errors.append(_error("ORDERS_BEFORE_QUERY_FAILED", str(exc)))

    try:
        probe = _expect_mapping(
            client.post_json(
                "/v1/risk/crypto/probe",
                {
                    "symbols": list(symbols),
                    "requested_by": rehearsal_requested_by,
                },
            ),
            "probe",
        )
    except Exception as exc:
        errors.append(_error("PROBE_REQUEST_FAILED", str(exc)))

    probe_ok = _optional_bool(probe.get("ok")) if probe is not None else None
    probe_read_only = _optional_bool(probe.get("read_only")) if probe is not None else None
    failed_checks = _failed_probe_checks(probe or {})

    if probe is not None:
        if probe_ok is True:
            errors.append(
                _error(
                    "PROBE_UNEXPECTEDLY_PASSED",
                    "Negative probe unexpectedly returned ok=true.",
                )
            )
        if probe_read_only is not True:
            errors.append(
                _error("PROBE_NOT_READ_ONLY", "Probe response did not keep read_only=true.")
            )
        if not failed_checks:
            errors.append(
                _error(
                    "PROBE_NO_FAILED_CHECKS",
                    "Negative probe returned no failed check; fail-closed path was not exercised.",
                )
            )

    query = urlencode(
        {
            "stream_key": "risk:crypto",
            "event_type": "crypto_risk.probe_run",
            "since_ts_ms": max(0, started_ts_ms - 1000),
            "limit": 100,
        }
    )
    try:
        events = _expect_list(client.get_json(f"/v1/events?{query}"), "events")
    except Exception as exc:
        errors.append(_error("AUDIT_QUERY_FAILED", str(exc)))

    audit_event_found = _find_matching_audit_event(
        events,
        requested_by=rehearsal_requested_by,
        symbols=symbols,
        probe_ok=probe_ok,
    )
    if not audit_event_found:
        if events:
            errors.append(
                _error(
                    "AUDIT_EVENT_MISMATCH",
                    "Probe audit event exists but does not match requested_by/symbols/probe result.",
                )
            )
        else:
            errors.append(
                _error("AUDIT_EVENT_MISSING", "No crypto_risk.probe_run audit event found.")
            )

    try:
        after_orders = _expect_list(
            client.get_json(f"/v1/orders?{urlencode({'limit': order_limit})}"),
            "orders_after",
        )
    except Exception as exc:
        errors.append(_error("ORDERS_AFTER_QUERY_FAILED", str(exc)))

    orders_unchanged = _canonical_json(before_orders) == _canonical_json(after_orders)
    if before_orders and after_orders and not orders_unchanged:
        errors.append(
            _error(
                "ORDER_STATE_CHANGED",
                "Order list changed during read-only fail-closed rehearsal.",
            )
        )
    elif not before_orders and not after_orders:
        orders_unchanged = True

    return FailClosedRehearsalReport(
        ok=not errors,
        requested_by=rehearsal_requested_by,
        symbols=symbols,
        runtime_enabled=runtime_enabled,
        runtime_wired=runtime_wired,
        runtime_fail_closed=runtime_fail_closed,
        probe_ok=probe_ok,
        probe_read_only=probe_read_only,
        failed_checks=failed_checks,
        audit_event_found=audit_event_found,
        orders_unchanged=orders_unchanged,
        errors=tuple(errors),
        warnings=tuple(warnings),
    )


def report_to_dict(report: FailClosedRehearsalReport) -> dict[str, Any]:
    return {
        "ok": report.ok,
        "requested_by": report.requested_by,
        "symbols": list(report.symbols),
        "runtime": {
            "enabled": report.runtime_enabled,
            "wired": report.runtime_wired,
            "fail_closed": report.runtime_fail_closed,
        },
        "probe": {
            "ok": report.probe_ok,
            "read_only": report.probe_read_only,
            "failed_checks": list(report.failed_checks),
        },
        "audit_event_found": report.audit_event_found,
        "orders_unchanged": report.orders_unchanged,
        "errors": [_issue_to_dict(issue) for issue in report.errors],
        "warnings": [_issue_to_dict(issue) for issue in report.warnings],
    }


def _issue_to_dict(issue: RehearsalIssue) -> dict[str, str]:
    return {
        "severity": issue.severity,
        "code": issue.code,
        "message": issue.message,
    }


def _format_human_report(report: FailClosedRehearsalReport) -> str:
    lines = [
        "Crypto Risk Fail-Closed Rehearsal",
        f"status: {'PASS' if report.ok else 'FAIL'}",
        f"requested_by: {report.requested_by}",
        f"symbols: {', '.join(report.symbols)}",
        (
            "runtime: "
            f"enabled={report.runtime_enabled} wired={report.runtime_wired} "
            f"fail_closed={report.runtime_fail_closed}"
        ),
        (
            "probe: "
            f"ok={report.probe_ok} read_only={report.probe_read_only} "
            f"failed_checks={', '.join(report.failed_checks) if report.failed_checks else '<none>'}"
        ),
        f"audit_event_found: {report.audit_event_found}",
        f"orders_unchanged: {report.orders_unchanged}",
    ]
    if report.errors:
        lines.append("errors:")
        lines.extend(f"  - [{issue.code}] {issue.message}" for issue in report.errors)
    if report.warnings:
        lines.append("warnings:")
        lines.extend(f"  - [{issue.code}] {issue.message}" for issue in report.warnings)
    return "\n".join(lines)


def _failed_probe_checks(probe: dict[str, Any]) -> tuple[str, ...]:
    checks = probe.get("checks")
    if not isinstance(checks, dict):
        return ()
    failed = [
        str(name)
        for name, check in checks.items()
        if isinstance(check, dict) and check.get("status") == "failed"
    ]
    return tuple(sorted(failed))


def _find_matching_audit_event(
    events: list[Any],
    *,
    requested_by: str,
    symbols: tuple[str, ...],
    probe_ok: bool | None,
) -> bool:
    expected_symbols = list(symbols)
    for event in events:
        if not isinstance(event, dict):
            continue
        if event.get("event_type") != "crypto_risk.probe_run":
            continue
        payload = event.get("payload")
        if not isinstance(payload, dict):
            continue
        if payload.get("requested_by") != requested_by:
            continue
        if payload.get("symbols") != expected_symbols:
            continue
        if _optional_bool(payload.get("read_only")) is not True:
            continue
        if _optional_bool(payload.get("ok")) != probe_ok:
            continue
        return True
    return False


def _expect_mapping(value: Any, label: str) -> dict[str, Any]:
    if not isinstance(value, dict):
        raise TypeError(f"{label} response must be an object")
    return value


def _expect_list(value: Any, label: str) -> list[Any]:
    if not isinstance(value, list):
        raise TypeError(f"{label} response must be a list")
    return value


def _canonical_json(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))


def _optional_bool(value: Any) -> bool | None:
    return value if isinstance(value, bool) else None


def _error(code: str, message: str) -> RehearsalIssue:
    return RehearsalIssue(severity="error", code=code, message=message)


def _parse_symbols(raw_symbols: Sequence[str]) -> tuple[str, ...]:
    symbols: list[str] = []
    seen: set[str] = set()
    for raw in raw_symbols:
        for item in str(raw).split(","):
            symbol = item.upper().replace("-", "").replace("/", "").strip()
            if not symbol or symbol in seen:
                continue
            seen.add(symbol)
            symbols.append(symbol)
    return tuple(symbols or [DEFAULT_SYMBOL])


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="Run a read-only negative Crypto Risk probe and verify fail-closed evidence."
    )
    parser.add_argument("--base-url", default=DEFAULT_BASE_URL)
    parser.add_argument(
        "--symbol",
        dest="symbols",
        action="append",
        default=[],
        help=f"Invalid symbol to probe. Can be repeated or comma-separated. Default: {DEFAULT_SYMBOL}",
    )
    parser.add_argument("--requested-by", default=None)
    parser.add_argument("--timeout-seconds", type=float, default=30.0)
    parser.add_argument("--json", action="store_true", help="Print machine-readable JSON.")
    args = parser.parse_args(argv)

    report = run_fail_closed_rehearsal(
        client=UrlJsonClient(args.base_url, timeout_seconds=args.timeout_seconds),
        symbols=_parse_symbols(args.symbols),
        requested_by=args.requested_by,
    )
    if args.json:
        print(json.dumps(report_to_dict(report), ensure_ascii=False, indent=2, sort_keys=True))
    else:
        print(_format_human_report(report))
    return 0 if report.ok else 1


if __name__ == "__main__":
    raise SystemExit(main())
