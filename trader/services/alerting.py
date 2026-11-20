"""Blocker alert delivery and external heartbeat monitoring.

The trading processes cannot prove an alert was noticed by writing a log line.
This module sends a signed webhook to a receiving service and only records a
delivery after that service acknowledges the event. Failed transports stay
pending and are retried; they are never reported as delivered.

The same monitor also sends periodic heartbeats to the receiver. The receiver
can then detect a dead process (missing heartbeat), which is the failure mode a
self-reported log cannot cover.
"""

from __future__ import annotations

import asyncio
import hashlib
import hmac
import json
import os
import time
from dataclasses import dataclass, field
from typing import Any, Awaitable, Callable, Iterable, Mapping
from urllib.parse import urlsplit

import httpx

# Reason code -> (alert code, severity). Order matters: longer codes first so
# UNKNOWN_COMMAND_PENDING is not reported as a bare UNKNOWN_COMMAND.
BLOCKER_REASON_CODES: tuple[tuple[str, str, str], ...] = (
    ("UNKNOWN_COMMAND_PENDING", "UNKNOWN_COMMAND", "HIGH"),
    ("UNKNOWN_COMMAND", "UNKNOWN_COMMAND", "HIGH"),
    ("BALANCE_MISMATCH", "BALANCE_MISMATCH", "CRITICAL"),
    ("EXECUTION_INSTANCE_UNAVAILABLE", "EXECUTION_LEASE_LOST", "CRITICAL"),
    ("EXECUTION_INSTANCE_CONFLICT", "EXECUTION_LEASE_LOST", "CRITICAL"),
    ("EXECUTION_OWNER_NOT_ALIGNED", "EXECUTION_LEASE_LOST", "CRITICAL"),
    ("DR_BACKUP_STALE", "DR_EVIDENCE_INSUFFICIENT", "CRITICAL"),
    ("DR_EVIDENCE_MISSING", "DR_EVIDENCE_INSUFFICIENT", "CRITICAL"),
    ("DR_EVIDENCE_INVALID", "DR_EVIDENCE_INSUFFICIENT", "CRITICAL"),
    ("DR_RESTORE_UNVERIFIED", "DR_EVIDENCE_INSUFFICIENT", "CRITICAL"),
)


def _now_ms() -> int:
    return int(time.time() * 1000)


def _stable_event_id(source: str, code: str, account_id: str, details: dict[str, Any]) -> str:
    canonical = json.dumps(
        {"source": source, "code": code, "account_id": account_id, "details": details},
        sort_keys=True,
        separators=(",", ":"),
        ensure_ascii=False,
        default=str,
    )
    return "evt-" + hashlib.sha256(canonical.encode("utf-8")).hexdigest()[:32]


@dataclass(frozen=True)
class AlertSignal:
    event_id: str
    code: str
    severity: str
    source: str
    account_id: str
    occurred_at_ms: int
    reasons: tuple[str, ...] = ()
    details: dict[str, Any] = field(default_factory=dict)

    def payload(self) -> dict[str, Any]:
        return {
            "event_id": self.event_id,
            "code": self.code,
            "severity": self.severity,
            "source": self.source,
            "account_id": self.account_id,
            "occurred_at_ms": self.occurred_at_ms,
            "reasons": list(self.reasons),
            "details": self.details,
        }


@dataclass(frozen=True)
class DeliveryResult:
    delivered: bool
    event_id: str
    attempts: int
    status_code: int | None = None
    ack_id: str = ""
    error: str = ""

    def as_dict(self) -> dict[str, Any]:
        return {
            "delivered": self.delivered,
            "event_id": self.event_id,
            "attempts": self.attempts,
            "status_code": self.status_code,
            "ack_id": self.ack_id,
            "error": self.error,
        }


def signals_from_reasons(
    reasons: Iterable[str],
    *,
    source: str,
    account_id: str,
    now_ms: int | None = None,
    details: dict[str, Any] | None = None,
) -> list[AlertSignal]:
    """Map readiness reasons to deduplicated blocker signals."""
    current_ms = _now_ms() if now_ms is None else int(now_ms)
    reason_list = tuple(dict.fromkeys(str(reason) for reason in reasons if str(reason)))
    found: dict[str, tuple[str, str]] = {}
    for reason in reason_list:
        for needle, code, severity in BLOCKER_REASON_CODES:
            if needle in reason:
                found.setdefault(code, (code, severity))
                break
    signals: list[AlertSignal] = []
    for code, severity in found.values():
        base_details = dict(details or {})
        base_details["reasons"] = list(reason_list)
        signals.append(
            AlertSignal(
                event_id=_stable_event_id(source, code, account_id, base_details),
                code=code,
                severity=severity,
                source=source,
                account_id=account_id,
                occurred_at_ms=current_ms,
                reasons=reason_list,
                details=base_details,
            )
        )
    return signals


class WebhookAlertTransport:
    """Signed webhook transport requiring an explicit receiver acknowledgment."""

    def __init__(
        self,
        webhook_url: str,
        *,
        heartbeat_url: str = "",
        signing_secret: str = "",
        max_attempts: int = 3,
        timeout_seconds: float = 5.0,
        backoff_seconds: float = 0.5,
        client_factory: Callable[[], httpx.AsyncClient] | None = None,
    ):
        if not webhook_url:
            raise ValueError("ALERT_WEBHOOK_URL_REQUIRED")
        self.webhook_url = webhook_url
        self.heartbeat_url = heartbeat_url
        self.signing_secret = signing_secret
        self.max_attempts = max(1, int(max_attempts))
        self.timeout_seconds = float(timeout_seconds)
        self.backoff_seconds = max(0.0, float(backoff_seconds))
        self._client_factory = client_factory
        self._client: httpx.AsyncClient | None = None

    @property
    def target_host(self) -> str:
        return urlsplit(self.webhook_url).hostname or ""

    def _client_instance(self) -> httpx.AsyncClient:
        if self._client is None:
            if self._client_factory is not None:
                self._client = self._client_factory()
            else:
                self._client = httpx.AsyncClient(timeout=self.timeout_seconds)
        return self._client

    async def close(self) -> None:
        if self._client is not None:
            await self._client.aclose()
            self._client = None

    def _headers(self, event_id: str, body: bytes) -> dict[str, str]:
        headers = {
            "Content-Type": "application/json",
            "X-QTS-Event-Id": event_id,
        }
        if self.signing_secret:
            digest = hmac.new(self.signing_secret.encode(), body, hashlib.sha256).hexdigest()
            headers["X-QTS-Signature"] = f"sha256={digest}"
        return headers

    async def _post(self, url: str, payload: dict[str, Any], event_id: str) -> DeliveryResult:
        body = json.dumps(payload, sort_keys=True, separators=(",", ":")).encode("utf-8")
        headers = self._headers(event_id, body)
        last_status: int | None = None
        last_error = ""
        for attempt in range(1, self.max_attempts + 1):
            try:
                response = await self._client_instance().post(url, content=body, headers=headers)
                last_status = response.status_code
                if 200 <= response.status_code < 300:
                    try:
                        data = response.json()
                    except ValueError:
                        data = {}
                    if data.get("ack") is True:
                        return DeliveryResult(
                            delivered=True,
                            event_id=event_id,
                            attempts=attempt,
                            status_code=response.status_code,
                            ack_id=str(data.get("alert_id") or data.get("ack_id") or ""),
                        )
                    last_error = "ALERT_ACK_MISSING"
                else:
                    last_error = f"HTTP_{response.status_code}"
            except Exception as exc:  # Transport errors never echo a URL or payload.
                last_error = f"TRANSPORT_{type(exc).__name__}"
            if attempt < self.max_attempts and self.backoff_seconds:
                await asyncio.sleep(self.backoff_seconds * attempt)
        return DeliveryResult(
            delivered=False,
            event_id=event_id,
            attempts=self.max_attempts,
            status_code=last_status,
            error=last_error or "ALERT_NOT_DELIVERED",
        )

    async def send_alert(self, signal: AlertSignal) -> DeliveryResult:
        return await self._post(self.webhook_url, signal.payload(), signal.event_id)

    async def send_heartbeat(self, payload: dict[str, Any]) -> DeliveryResult:
        if not self.heartbeat_url:
            return DeliveryResult(
                delivered=False,
                event_id=str(payload.get("heartbeat_id", "")),
                attempts=0,
                error="ALERT_HEARTBEAT_URL_NOT_CONFIGURED",
            )
        event_id = str(payload.get("heartbeat_id", ""))
        return await self._post(self.heartbeat_url, payload, event_id)


class AlertMonitor:
    """Poll readiness, deliver every blocker, and heartbeat the receiver."""

    def __init__(
        self,
        *,
        source: str,
        account_id: str,
        readiness: Callable[[], Awaitable[Mapping[str, Any]]],
        transport: WebhookAlertTransport,
        interval_seconds: float = 5.0,
        clock: Callable[[], int] = _now_ms,
        heartbeat_id_factory: Callable[[], str] | None = None,
    ):
        self.source = source
        self.account_id = account_id
        self.readiness = readiness
        self.transport = transport
        self.interval_seconds = max(0.1, float(interval_seconds))
        self.clock = clock
        self.heartbeat_id_factory = heartbeat_id_factory or (lambda: f"hb-{source}-{self.clock()}")
        self.delivered: dict[str, DeliveryResult] = {}
        self.pending: dict[str, AlertSignal] = {}
        self.last_heartbeat: DeliveryResult | None = None
        self.last_scan: dict[str, Any] = {}
        self._stop = asyncio.Event()

    async def scan_once(self) -> dict[str, Any]:
        now_ms = self.clock()
        try:
            status = dict(await self.readiness())
        except Exception:
            status = {
                "ready": False,
                "reasons": ["READINESS_CHECK_FAILED"],
                "account_id": self.account_id,
            }
        reasons = [str(reason) for reason in status.get("reasons") or []]
        signals = signals_from_reasons(
            reasons,
            source=self.source,
            account_id=self.account_id,
            now_ms=now_ms,
            details={
                "account_id": self.account_id,
                "instance_owner": status.get("instance_owner", ""),
                "status_ready": bool(status.get("ready")),
            },
        )
        deliveries: list[dict[str, Any]] = []
        for signal in signals:
            if signal.event_id in self.delivered:
                continue
            self.pending[signal.event_id] = signal
            result = await self.transport.send_alert(signal)
            deliveries.append(
                {
                    **result.as_dict(),
                    "code": signal.code,
                    "severity": signal.severity,
                }
            )
            if result.delivered:
                # Only an acknowledged receipt counts as delivery.
                self.delivered[signal.event_id] = result
                self.pending.pop(signal.event_id, None)
        heartbeat_payload = {
            "heartbeat_id": self.heartbeat_id_factory(),
            "source": self.source,
            "account_id": self.account_id,
            "sent_at_ms": now_ms,
            "ready": bool(status.get("ready")),
            "reasons": reasons,
        }
        heartbeat = await self.transport.send_heartbeat(heartbeat_payload)
        if heartbeat.delivered:
            self.last_heartbeat = heartbeat
        self.last_scan = {
            "at_ms": now_ms,
            "ready": bool(status.get("ready")),
            "reasons": reasons,
            "alerts": deliveries,
            "heartbeat": heartbeat.as_dict(),
            "delivered_event_ids": sorted(self.delivered),
            "pending_event_ids": sorted(self.pending),
        }
        return self.last_scan

    async def run_forever(self, stop_event: asyncio.Event | None = None) -> None:
        stop = stop_event or self._stop
        while not stop.is_set():
            try:
                await self.scan_once()
            except asyncio.CancelledError:
                raise
            except Exception:
                # Monitoring must not kill the trading process. Retry next tick.
                pass
            try:
                await asyncio.wait_for(stop.wait(), timeout=self.interval_seconds)
            except asyncio.TimeoutError:
                continue

    def stop(self) -> None:
        self._stop.set()

    async def close(self) -> None:
        await self.transport.close()


def _secret_from_env(environ: Mapping[str, str], key: str) -> str:
    direct = environ.get(key, "")
    if direct:
        return direct.strip()
    path = environ.get(f"{key}_FILE", "")
    if not path:
        return ""
    try:
        with open(path, "r", encoding="utf-8") as handle:
            return handle.read().strip()
    except OSError:
        return ""


def alert_monitor_from_env(
    *,
    source: str,
    account_id: str,
    readiness: Callable[[], Awaitable[Mapping[str, Any]]],
    environ: Mapping[str, str] | None = None,
) -> AlertMonitor | None:
    """Build a monitor from environment, or ``None`` when no receiver is set."""
    env = os.environ if environ is None else environ
    webhook_url = str(env.get("QTS_ALERT_WEBHOOK_URL", "") or "").strip()
    if not webhook_url:
        return None
    heartbeat_url = str(env.get("QTS_ALERT_HEARTBEAT_URL", "") or "").strip()
    transport = WebhookAlertTransport(
        webhook_url,
        heartbeat_url=heartbeat_url,
        signing_secret=_secret_from_env(env, "QTS_ALERT_SIGNING_SECRET"),
        max_attempts=int(env.get("QTS_ALERT_MAX_ATTEMPTS", "3")),
        timeout_seconds=float(env.get("QTS_ALERT_TIMEOUT_SECONDS", "5")),
        backoff_seconds=float(env.get("QTS_ALERT_BACKOFF_SECONDS", "0.5")),
    )
    return AlertMonitor(
        source=source,
        account_id=account_id,
        readiness=readiness,
        transport=transport,
        interval_seconds=float(env.get("QTS_ALERT_INTERVAL_SECONDS", "5")),
    )
