"""Fail-closed validation of a runtime release binding and of its window.

A release binding is only trustworthy when the facts it carries are
internally consistent.  A binding that merely *declares* five boolean checks
proves nothing: the inspected containers must be running and healthy, their
image digest and release labels must match the requested release, the
collector must not have recorded an error, the timestamp must be a real
timestamp, and every observation sample must have been served by the same
runtime identity.

The module is pure: it validates data structures, it never collects anything.
``scripts/collect_runtime_release_binding.py`` produces the binding,
``scripts/summarize_observation.py`` uses the validators below to gate a
strict observation window.
"""

from __future__ import annotations

from datetime import datetime
from typing import Any

LABEL_RELEASE_ID = "com.qts.release.id"
LABEL_RELEASE_COMMIT = "com.qts.release.commit"
LABEL_ENVIRONMENT = "com.qts.environment"

# The release-bearing containers a Demo observation window is about.
RUNTIME_SERVICES = ("control", "execution")
REQUIRED_CHECKS = (
    "all_running",
    "all_healthy",
    "labels_match",
    "image_digest_match",
    "trading_ready",
)
IDENTITY_FIELDS = ("release_id", "commit", "image_digest", "environment")

# A binding is collected on the Demo host around the observation window.  It
# may not predate the window (that would be a binding of an earlier release)
# and it may not be far in the future (that would be an unrelated window).
MAX_BINDING_WINDOW_LAG_MS = 3_600_000
MAX_BINDING_CLOCK_SKEW_MS = 60_000
# The identity a sample carries is observed by the same request that produced
# the sample, so it may not drift far from the sample timestamp.
MAX_SAMPLE_IDENTITY_SKEW_MS = 120_000


def normalise_digest(value: object) -> str:
    text = str(value or "").strip()
    return text.rsplit("@", 1)[-1] if "@" in text else text


def parse_timestamp_ms(value: object) -> int | None:
    """Parse an ISO-8601 timestamp with an explicit offset into epoch ms."""
    text = str(value or "").strip()
    if not text:
        return None
    if text.endswith(("Z", "z")):
        text = text[:-1] + "+00:00"
    try:
        parsed = datetime.fromisoformat(text)
    except ValueError:
        return None
    if parsed.tzinfo is None:
        return None
    return int(parsed.timestamp() * 1000)


def validate_binding(
    payload: dict[str, Any],
    *,
    expected_release_id: str = "",
    expected_commit: str = "",
    expected_digest: str = "",
    expected_environment: str = "demo",
) -> list[str]:
    """Validate the actual facts carried by a release binding."""
    blockers: list[str] = []
    if payload.get("runtime_inspected") is not True:
        blockers.append("RELEASE_RUNTIME_BINDING_MISSING")

    if "errors" not in payload:
        # A binding without the collector's error list is not proof that the
        # collection was clean; it is an incomplete binding.
        blockers.append("RELEASE_RUNTIME_ERRORS_MISSING")
    elif not isinstance(payload.get("errors"), list):
        blockers.append("RELEASE_RUNTIME_ERRORS_INVALID")
    else:
        for code in [str(item) for item in payload["errors"] if str(item).strip()][:5]:
            blockers.append(f"RELEASE_RUNTIME_ERROR_PRESENT:{code}")

    checks = payload.get("checks")
    if not isinstance(checks, dict):
        blockers.append("RELEASE_RUNTIME_CHECKS_MISSING")
    else:
        for key in REQUIRED_CHECKS:
            if checks.get(key) is not True:
                blockers.append(f"RELEASE_RUNTIME_CHECK_FAILED:{key}")

    generated_at_ms = parse_timestamp_ms(payload.get("generated_at"))
    if generated_at_ms is None:
        blockers.append("RELEASE_RUNTIME_GENERATED_AT_INVALID")

    release_id = str(payload.get("release_id") or "").strip()
    commit = str(payload.get("commit") or "").strip().lower()
    digest = normalise_digest(payload.get("image_digest"))
    environment = str(payload.get("environment") or "").strip().lower()

    containers = payload.get("containers")
    if not isinstance(containers, dict) or not set(RUNTIME_SERVICES) <= set(containers):
        blockers.append("RELEASE_RUNTIME_CONTAINERS_MISSING")
    else:
        for service in RUNTIME_SERVICES:
            item = containers.get(service)
            if not isinstance(item, dict):
                blockers.append(f"RELEASE_RUNTIME_CONTAINER_MISSING:{service}")
                continue
            if item.get("present") is not True:
                blockers.append(f"RELEASE_RUNTIME_CONTAINER_NOT_PRESENT:{service}")
            if item.get("running") is not True:
                blockers.append(f"RELEASE_RUNTIME_CONTAINER_NOT_RUNNING:{service}")
            if item.get("health") != "healthy":
                blockers.append(
                    f"RELEASE_RUNTIME_CONTAINER_UNHEALTHY:{service}:{item.get('health')}"
                )
            if expected_digest and normalise_digest(item.get("image")) != normalise_digest(
                expected_digest
            ):
                blockers.append(f"RELEASE_RUNTIME_CONTAINER_IMAGE_MISMATCH:{service}")
            raw_labels = item.get("labels")
            labels: dict[str, Any] = raw_labels if isinstance(raw_labels, dict) else {}
            if expected_release_id and labels.get(LABEL_RELEASE_ID) != expected_release_id:
                blockers.append(f"RELEASE_RUNTIME_LABEL_MISMATCH:{service}:release_id")
            if expected_commit and str(labels.get(LABEL_RELEASE_COMMIT) or "").lower() != (
                expected_commit.strip().lower()
            ):
                blockers.append(f"RELEASE_RUNTIME_LABEL_MISMATCH:{service}:commit")
            if expected_environment and str(labels.get(LABEL_ENVIRONMENT) or "").lower() != (
                expected_environment.strip().lower()
            ):
                blockers.append(f"RELEASE_RUNTIME_LABEL_MISMATCH:{service}:environment")

    health = payload.get("health")
    if not isinstance(health, dict) or not health:
        blockers.append("RELEASE_RUNTIME_HEALTH_MISSING")
    else:
        if (health.get("live") or {}).get("status") != 200:
            blockers.append("RELEASE_RUNTIME_HEALTH_NOT_READY:live")
        if (health.get("ready") or {}).get("status") != 200:
            blockers.append("RELEASE_RUNTIME_HEALTH_NOT_READY:ready")
        trading = health.get("trading") or {}
        if trading.get("status") != 200 or not (trading.get("body") or {}).get("ready"):
            blockers.append("RELEASE_RUNTIME_HEALTH_NOT_READY:trading")

    runtime = payload.get("runtime")
    if not isinstance(runtime, dict) or not runtime:
        blockers.append("RELEASE_RUNTIME_IDENTITY_MISSING")
    else:
        expected_identity = {
            "release_id": expected_release_id or release_id,
            "commit": expected_commit.strip().lower() or commit,
            "image_digest": normalise_digest(expected_digest) or digest,
            "environment": expected_environment.strip().lower() or environment,
        }
        for field in IDENTITY_FIELDS:
            value = str(runtime.get(field) or "").strip()
            if not value:
                blockers.append(f"RELEASE_RUNTIME_IDENTITY_MISSING:{field}")
                continue
            if field == "commit":
                value = value.lower()
            if field == "image_digest":
                value = normalise_digest(value)
            if expected_identity.get(field) and value != expected_identity[field]:
                blockers.append(f"RELEASE_RUNTIME_IDENTITY_MISMATCH:{field}")
        if not str(runtime.get("instance_owner") or "").strip():
            blockers.append("RELEASE_RUNTIME_OWNER_MISSING")
    return blockers


def validate_sample_identity(
    sample: dict[str, Any],
    *,
    binding_runtime: dict[str, Any] | None,
) -> list[str]:
    """Validate that a sample was served by the bound runtime."""
    blockers: list[str] = []
    runtime = sample.get("runtime")
    if not isinstance(runtime, dict) or not runtime:
        return ["SAMPLE_RUNTIME_IDENTITY_MISSING"]
    reference = binding_runtime if isinstance(binding_runtime, dict) else {}
    for field in IDENTITY_FIELDS:
        value = str(runtime.get(field) or "").strip()
        if field == "commit":
            value = value.lower()
        if field == "image_digest":
            value = normalise_digest(value)
        if not value:
            blockers.append(f"SAMPLE_RUNTIME_IDENTITY_MISSING:{field}")
            continue
        expected = str(reference.get(field) or "").strip()
        if field == "commit":
            expected = expected.lower()
        if field == "image_digest":
            expected = normalise_digest(expected)
        if expected and value != expected:
            blockers.append(f"SAMPLE_RUNTIME_IDENTITY_MISMATCH:{field}")
    observed = runtime.get("observed_at_ms")
    ts_ms = sample.get("ts_ms")
    if not isinstance(observed, int) or isinstance(observed, bool):
        blockers.append("SAMPLE_RUNTIME_TIMESTAMP_MISSING")
    elif not isinstance(ts_ms, int) or isinstance(ts_ms, bool):
        blockers.append("SAMPLE_RUNTIME_TIMESTAMP_MISSING")
    elif abs(observed - ts_ms) > MAX_SAMPLE_IDENTITY_SKEW_MS:
        blockers.append("SAMPLE_RUNTIME_TIMESTAMP_MISMATCH")
    return blockers


def validate_window_anchor(
    *,
    generated_at_ms: int | None,
    first_sample_ts_ms: int,
    last_sample_ts_ms: int,
) -> list[str]:
    """The binding must describe the same window the samples were taken in."""
    if generated_at_ms is None:
        return []
    if generated_at_ms + MAX_BINDING_CLOCK_SKEW_MS < first_sample_ts_ms:
        return ["RELEASE_BINDING_WINDOW_MISMATCH"]
    if generated_at_ms > last_sample_ts_ms + MAX_BINDING_WINDOW_LAG_MS:
        return ["RELEASE_BINDING_WINDOW_MISMATCH"]
    return []
