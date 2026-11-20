"""Phase-5 blocker alert delivery and heartbeat contract."""

from __future__ import annotations

import hashlib
import hmac
import json
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

import pytest

from trader.services.alerting import (
    AlertMonitor,
    AlertSignal,
    DeliveryResult,
    WebhookAlertTransport,
    alert_monitor_from_env,
    signals_from_reasons,
)


class Receiver(BaseHTTPRequestHandler):
    protocol_version = "HTTP/1.1"

    def log_message(self, format, *args):  # noqa: A002
        return

    def do_POST(self):  # noqa: N802
        length = int(self.headers.get("Content-Length", "0"))
        body = self.rfile.read(length)
        payload = json.loads(body.decode("utf-8"))
        self.server.received.append((self.path, payload, dict(self.headers)))
        if self.path not in {"/webhook", "/heartbeat"}:
            encoded = json.dumps({"ack": False, "error": "NOT_FOUND"}).encode("utf-8")
            self.send_response(404)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(encoded)))
            self.end_headers()
            self.wfile.write(encoded)
            return
        if not self.server.ack:
            response = {"ack": False}
            status = 200
        elif self.server.secret:
            expected = (
                "sha256=" + hmac.new(self.server.secret.encode(), body, hashlib.sha256).hexdigest()
            )
            if self.headers.get("X-QTS-Signature") != expected:
                response = {"ack": False, "error": "bad signature"}
                status = 401
            else:
                response = {"ack": True, "alert_id": payload.get("event_id")}
                status = 200
        else:
            response = {
                "ack": True,
                "alert_id": payload.get("event_id"),
                "ack_id": payload.get("heartbeat_id"),
            }
            status = 200
        encoded = json.dumps(response).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(encoded)))
        self.end_headers()
        self.wfile.write(encoded)


@pytest.fixture
def receiver():
    server = ThreadingHTTPServer(("127.0.0.1", 0), Receiver)
    server.received = []
    server.ack = True
    server.secret = ""
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        yield server
    finally:
        server.shutdown()
        server.server_close()


def _url(server: Receiver, path: str) -> str:
    return f"http://127.0.0.1:{server.server_address[1]}{path}"


def _signal(event_id="evt-1", code="UNKNOWN_COMMAND") -> AlertSignal:
    return AlertSignal(event_id, code, "HIGH", "execution", "acct", 1_000, ("reason",))


def test_reason_mapping_covers_required_blockers():
    signals = signals_from_reasons(
        ["UNKNOWN_COMMAND_PENDING", "BALANCE_MISMATCH", "EXECUTION_INSTANCE_CONFLICT"],
        source="control",
        account_id="acct",
        now_ms=1_000,
    )
    assert {signal.code for signal in signals} == {
        "UNKNOWN_COMMAND",
        "BALANCE_MISMATCH",
        "EXECUTION_LEASE_LOST",
    }
    assert len({signal.event_id for signal in signals}) == 3


@pytest.mark.asyncio
async def test_transport_records_delivery_only_with_ack(receiver):
    transport = WebhookAlertTransport(_url(receiver, "/webhook"), max_attempts=2, backoff_seconds=0)
    result = await transport.send_alert(_signal())
    assert result.delivered is True
    assert result.ack_id == "evt-1"
    assert receiver.received[0][1]["event_id"] == "evt-1"
    await transport.close()


@pytest.mark.asyncio
async def test_transport_does_not_count_2xx_without_ack(receiver):
    receiver.ack = False
    transport = WebhookAlertTransport(_url(receiver, "/webhook"), max_attempts=2, backoff_seconds=0)
    result = await transport.send_alert(_signal())
    assert result.delivered is False
    assert result.attempts == 2
    assert result.error == "ALERT_ACK_MISSING"
    await transport.close()


@pytest.mark.asyncio
async def test_failed_transport_never_becomes_delivered(receiver):
    transport = WebhookAlertTransport(_url(receiver, "/missing"), max_attempts=2, backoff_seconds=0)
    result = await transport.send_alert(_signal())
    assert result.delivered is False
    assert result.status_code == 404
    await transport.close()


@pytest.mark.asyncio
async def test_signed_webhook_matches_receiver_hmac(receiver):
    receiver.secret = "synthetic-signing-secret"
    transport = WebhookAlertTransport(
        _url(receiver, "/webhook"),
        signing_secret="synthetic-signing-secret",
        max_attempts=1,
        backoff_seconds=0,
    )
    result = await transport.send_alert(_signal("evt-signed"))
    assert result.delivered is True
    assert receiver.received[0][2]["X-QTS-Signature"].startswith("sha256=")
    await transport.close()

    wrong = WebhookAlertTransport(
        _url(receiver, "/webhook"), signing_secret="wrong", max_attempts=1, backoff_seconds=0
    )
    assert (await wrong.send_alert(_signal("evt-wrong"))).delivered is False
    await wrong.close()


class RecordingTransport:
    def __init__(self, fail: bool = False):
        self.fail = fail
        self.alerts = []
        self.heartbeats = []

    async def send_alert(self, signal):
        if self.fail:
            return DeliveryResult(False, signal.event_id, 1, 503, error="HTTP_503")
        self.alerts.append(signal)
        return DeliveryResult(True, signal.event_id, 1, 200, signal.event_id)

    async def send_heartbeat(self, payload):
        if self.fail:
            return DeliveryResult(False, payload["heartbeat_id"], 1, 503, error="HTTP_503")
        self.heartbeats.append(payload)
        return DeliveryResult(True, payload["heartbeat_id"], 1, 200, "ack")

    async def close(self):
        return None


@pytest.mark.asyncio
async def test_monitor_delivers_every_blocker_and_heartbeat():
    async def readiness():
        return {
            "ready": False,
            "reasons": [
                "UNKNOWN_COMMAND_PENDING",
                "BALANCE_MISMATCH",
                "EXECUTION_INSTANCE_UNAVAILABLE",
            ],
        }

    transport = RecordingTransport()
    monitor = AlertMonitor(
        source="control",
        account_id="acct",
        readiness=readiness,
        transport=transport,
        interval_seconds=60,
        clock=lambda: 1_000,
    )
    scan = await monitor.scan_once()
    assert {signal.code for signal in transport.alerts} == {
        "UNKNOWN_COMMAND",
        "BALANCE_MISMATCH",
        "EXECUTION_LEASE_LOST",
    }
    assert scan["heartbeat"]["delivered"] is True
    assert transport.heartbeats
    assert len(scan["delivered_event_ids"]) == 3
    assert not scan["pending_event_ids"]


@pytest.mark.asyncio
async def test_monitor_failure_keeps_events_pending_and_not_delivered():
    async def readiness():
        return {"ready": False, "reasons": ["BALANCE_MISMATCH"]}

    transport = RecordingTransport(fail=True)
    monitor = AlertMonitor(
        source="execution",
        account_id="acct",
        readiness=readiness,
        transport=transport,
        interval_seconds=60,
        clock=lambda: 1_000,
    )
    scan = await monitor.scan_once()
    assert scan["delivered_event_ids"] == []
    assert len(scan["pending_event_ids"]) == 1
    assert scan["alerts"][0]["delivered"] is False


def test_monitor_is_disabled_without_a_receiver_url():
    async def readiness():
        return {"ready": True, "reasons": []}

    assert (
        alert_monitor_from_env(source="control", account_id="acct", readiness=readiness, environ={})
        is None
    )
    monitor = alert_monitor_from_env(
        source="control",
        account_id="acct",
        readiness=readiness,
        environ={"QTS_ALERT_WEBHOOK_URL": "http://alerts.example/webhook"},
    )
    assert monitor is not None
    assert monitor.transport.target_host == "alerts.example"
