"""Continuously observe the unified trading runtime as reviewable JSONL.

Each line is one durable sample: wall and monotonic timestamps, the health
status/reasons, the authoritative account metrics (including execution/ledger/
event identity digests and repeated-send facts), private-WS coverage proof,
strategy/warmup state and the risk view. Restart, reconnect and health-state
transitions are appended as explicit markers so a reviewer can reconstruct the
window instead of trusting a single exit code.

Credentials are read from a file or environment and are never printed or put on
the command line. The observer only performs GET requests plus login.
"""

from __future__ import annotations

import argparse
import asyncio
import json
import os
import sys
import tempfile
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

import httpx  # noqa: E402

_READY_STATES = {"ready", "degraded"}


def _utc_now() -> str:
    return time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())


def _append(path: Path, payload: dict) -> None:
    """Append one line and fsync so a host crash cannot silently drop samples."""
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a", encoding="utf-8") as handle:
        handle.write(json.dumps(payload, default=str, ensure_ascii=False) + "\n")
        handle.flush()
        os.fsync(handle.fileno())


def _read_password(explicit: str, username: str) -> str:
    if explicit:
        return Path(explicit).read_text(encoding="utf-8").strip()
    env = os.environ.get("QTS_E2E_PASSWORD", "")
    if env:
        return env
    candidate = Path(tempfile.gettempdir()) / "qts_operator_password.txt"
    if candidate.exists():
        return candidate.read_text(encoding="utf-8").strip()
    return ""


async def _login(client: httpx.AsyncClient, username: str, password: str) -> str:
    response = await client.post(
        "/v1/auth/login", json={"username": username, "password": password}
    )
    response.raise_for_status()
    return str(response.json().get("csrf_token", ""))


async def _get(client: httpx.AsyncClient, path: str, params: dict | None = None):
    return await client.get(path, params=params)


async def _sample(client, args, password: str, state: dict, seq: int) -> dict:
    line: dict = {
        "kind": "sample",
        "seq": seq,
        "ts_ms": int(time.time() * 1000),
        "utc": _utc_now(),
        "monotonic_ms": time.monotonic_ns() // 1_000_000,
        "sample_gap_ms": None,
        "attempts": [],
    }
    if state.get("last_monotonic_ms") is not None:
        line["sample_gap_ms"] = line["monotonic_ms"] - state["last_monotonic_ms"]
    state["last_monotonic_ms"] = line["monotonic_ms"]

    errors = []
    for attempt in range(1, args.retries + 1):
        try:
            if not state.get("csrf"):
                state["csrf"] = await _login(client, args.username, password)
            health = await _get(client, "/health/trading")
            line["health"] = {"status_code": health.status_code, "body": health.json()}
            metrics = await _get(
                client, "/v1/trading/metrics", params={"account_id": args.account_id}
            )
            line["metrics_status_code"] = metrics.status_code
            if metrics.status_code != 200:
                # Any non-200 is a failed attempt, not a silent success. A
                # bounded control session expires with a 401 response, so the
                # token is dropped and the same sample is retried with a fresh
                # login; the sample then still carries its evidence body.
                if metrics.status_code in (401, 403):
                    state["csrf"] = ""
                line["attempts"].append(
                    {"attempt": attempt, "ok": False, "error": f"HTTP {metrics.status_code}"}
                )
                if attempt < args.retries:
                    await asyncio.sleep(min(2.0 * attempt, 10.0))
                    continue
            if metrics.status_code == 200:
                body = metrics.json()
                line["metrics"] = {
                    "status": body.get("status"),
                    "unknown_commands": body.get("unknown_commands"),
                    "unbooked_fills": body.get("unbooked_fills"),
                    "balance_anomalies": body.get("balance_anomalies"),
                    "active_holds": body.get("active_holds"),
                    "reconciliation_age_ms": body.get("reconciliation_age_ms"),
                    "outbox_backlog": body.get("outbox_backlog"),
                    "unresolved": body.get("unresolved"),
                    "unresolved_entries": body.get("unresolved_entries"),
                    "balance_differences": body.get("balance_differences"),
                    "evidence": body.get("evidence"),
                    "coverage": body.get("coverage"),
                    "runtime": body.get("runtime"),
                    "strategy": body.get("strategy"),
                    "book_count": body.get("book_count"),
                    "books": body.get("books"),
                }
            risk = await _get(client, f"/v1/trading/accounts/{args.account_id}/risk")
            line["risk_status_code"] = risk.status_code
            if risk.status_code != 200:
                if risk.status_code in (401, 403):
                    state["csrf"] = ""
                line["attempts"].append(
                    {"attempt": attempt, "ok": False, "error": f"HTTP {risk.status_code}"}
                )
                if attempt < args.retries:
                    await asyncio.sleep(min(2.0 * attempt, 10.0))
                    continue
            if risk.status_code == 200:
                body = risk.json()
                line["risk"] = {
                    "status": body.get("status"),
                    "reasons": body.get("reasons"),
                    "generation": body.get("generation"),
                    "checked_at_ms": body.get("checked_at_ms"),
                    "account_version": body.get("account_version"),
                    "valuation_complete": body.get("valuation_complete"),
                    "positions": body.get("positions"),
                    "balance_differences": body.get("balance_differences"),
                }
            line["attempts"].append({"attempt": attempt, "ok": True})
            break
        except Exception as exc:
            line["attempts"].append(
                {"attempt": attempt, "ok": False, "error": f"{type(exc).__name__}: {exc}"}
            )
            errors.append(f"{type(exc).__name__}: {exc}")
            state["csrf"] = "" if "401" in str(exc) or "403" in str(exc) else state.get("csrf", "")
            if attempt < args.retries:
                await asyncio.sleep(min(2.0 * attempt, 10.0))
            else:
                line["error"] = errors[-1]

    health_status = line.get("health", {}).get("status_code")
    ready = health_status == 200
    line["ready"] = ready
    previous = state.get("last_ready")
    if previous is not None and previous != ready:
        reasons = []
        if isinstance(line.get("health"), dict):
            reasons = line["health"].get("body", {}).get("reasons") or []
        _append(
            args.out_path,
            {
                "kind": "transition",
                "ts_ms": int(time.time() * 1000),
                "utc": _utc_now(),
                "from_ready": previous,
                "to_ready": ready,
                "health_status_code": health_status,
                "reasons": reasons,
            },
        )
    state["last_ready"] = ready
    return line


async def main() -> int:
    parser = argparse.ArgumentParser(description="Observe the unified trading runtime")
    parser.add_argument("--base-url", default=os.getenv("QTS_CONTROL_URL", "http://127.0.0.1:8090"))
    parser.add_argument("--username", default=os.getenv("QTS_USERNAME", "owner"))
    parser.add_argument(
        "--password-file",
        default=os.getenv("QTS_OPERATOR_PASSWORD_FILE", ""),
        help="file containing the operator password; never pass the secret in argv",
    )
    parser.add_argument("--account-id", default=os.getenv("QTS_ACCOUNT_ID", "binance_spot"))
    parser.add_argument("--interval", type=float, default=30.0)
    parser.add_argument("--duration", type=float, default=24 * 3600.0)
    parser.add_argument("--duration-hours", type=float, default=None)
    parser.add_argument("--retries", type=int, default=3)
    parser.add_argument("--out", default="artifacts/foundation/phase6/observation.jsonl")
    parser.add_argument("--marker", default="")
    args = parser.parse_args()
    if args.duration_hours is not None:
        args.duration = args.duration_hours * 3600.0
    args.out_path = Path(args.out)

    password = _read_password(args.password_file, args.username)
    if not password:
        print(json.dumps({"ok": False, "reason": "OPERATOR_PASSWORD_MISSING"}))
        return 2

    # The monotonic clock is the authority for the actual window duration;
    # wall-clock markers can slew by seconds over 24h (NTP). Record both and
    # let the supervisor compute the duration from the monotonic origin.
    start_monotonic_ns = time.monotonic_ns()
    _append(
        args.out_path,
        {
            "kind": "marker",
            "ts_ms": int(time.time() * 1000),
            "monotonic_ms": start_monotonic_ns // 1_000_000,
            "utc": _utc_now(),
            "marker": args.marker or "observer_started",
            "base_url": args.base_url,
            "interval_seconds": args.interval,
            "duration_seconds": args.duration,
            "account_id": args.account_id,
        },
    )

    state: dict = {"csrf": "", "last_ready": None, "last_monotonic_ms": None}
    deadline = start_monotonic_ns / 1_000_000_000 + args.duration
    seq = 0
    samples = 0
    ready_samples = 0
    async with httpx.AsyncClient(base_url=args.base_url, timeout=30.0) as client:
        while time.monotonic() < deadline:
            seq += 1
            line = await _sample(client, args, password, state, seq)
            _append(args.out_path, line)
            samples += 1
            if line.get("ready"):
                ready_samples += 1
            print(
                json.dumps(
                    {
                        "seq": seq,
                        "ready": line.get("ready"),
                        "health": line.get("health", {}).get("status_code"),
                        "reasons": line.get("health", {}).get("body", {}).get("reasons"),
                    },
                    default=str,
                )
            )
            sleep_for = max(0.0, min(args.interval, deadline - time.monotonic()))
            if sleep_for:
                await asyncio.sleep(sleep_for)

    finish_monotonic_ns = time.monotonic_ns()
    _append(
        args.out_path,
        {
            "kind": "marker",
            "ts_ms": int(time.time() * 1000),
            "monotonic_ms": finish_monotonic_ns // 1_000_000,
            "elapsed_monotonic_ms": (finish_monotonic_ns - start_monotonic_ns) // 1_000_000,
            "utc": _utc_now(),
            "marker": args.marker or "observer_finished",
            "samples": samples,
            "ready_samples": ready_samples,
        },
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
