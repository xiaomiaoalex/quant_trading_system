"""Minimal external alert receiver for isolated phase-5 rehearsals.

This is the counterparty that makes webhook delivery observable: it persists
every accepted event and heartbeat to a journal, acknowledges only after the
write, and exposes the acknowledgement state for evidence collection. It is a
test/rehearsal service, not a production alerting vendor integration.
"""

from __future__ import annotations

import argparse
import hashlib
import hmac
import json
import os
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any
from urllib.parse import urlsplit


class AlertStore:
    def __init__(self, journal_path: str, signing_secret: str = ""):
        self.path = Path(journal_path)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.signing_secret = signing_secret
        self.lock = threading.Lock()
        self.events: dict[str, dict[str, Any]] = {}
        self.heartbeats: list[dict[str, Any]] = []
        self._load()

    def _load(self) -> None:
        if not self.path.is_file():
            return
        for line in self.path.read_text(encoding="utf-8").splitlines():
            try:
                record = json.loads(line)
            except ValueError:
                continue
            self._remember(record)

    def _remember(self, record: dict[str, Any]) -> None:
        if record.get("kind") == "event":
            self.events.setdefault(str(record.get("event_id", "")), record)
        elif record.get("kind") == "heartbeat":
            self.heartbeats.append(record)

    def verify(self, body: bytes, signature: str) -> bool:
        if not self.signing_secret:
            return True
        expected = (
            "sha256=" + hmac.new(self.signing_secret.encode(), body, hashlib.sha256).hexdigest()
        )
        return hmac.compare_digest(expected, signature or "")

    def record_event(self, event: dict[str, Any]) -> bool:
        """Return True when the event was newly recorded (False = duplicate)."""
        event_id = str(event.get("event_id", ""))
        with self.lock:
            if event_id in self.events:
                return False
            record = {
                "kind": "event",
                "received_at_ms": int(time.time() * 1000),
                **event,
            }
            self.events[event_id] = record
            with self.path.open("a", encoding="utf-8") as handle:
                handle.write(json.dumps(record, sort_keys=True, ensure_ascii=False) + "\n")
                handle.flush()
                os.fsync(handle.fileno())
            return True

    def record_heartbeat(self, heartbeat: dict[str, Any]) -> None:
        with self.lock:
            record = {
                "kind": "heartbeat",
                "received_at_ms": int(time.time() * 1000),
                **heartbeat,
            }
            self.heartbeats.append(record)
            with self.path.open("a", encoding="utf-8") as handle:
                handle.write(json.dumps(record, sort_keys=True, ensure_ascii=False) + "\n")
                handle.flush()
                os.fsync(handle.fileno())

    def status(self, heartbeat_timeout_seconds: float) -> dict[str, Any]:
        with self.lock:
            now_ms = int(time.time() * 1000)
            last_heartbeat = self.heartbeats[-1]["received_at_ms"] if self.heartbeats else None
            age = None if last_heartbeat is None else max(0.0, (now_ms - last_heartbeat) / 1000)
            event_times = [record["received_at_ms"] for record in self.events.values()]
            return {
                "healthy": age is not None and age <= heartbeat_timeout_seconds,
                "events_received": len(self.events),
                "heartbeats_received": len(self.heartbeats),
                "last_event_at_ms": max(event_times) if event_times else None,
                "last_heartbeat_at_ms": last_heartbeat,
                "heartbeat_age_seconds": age,
                "heartbeat_timeout_seconds": heartbeat_timeout_seconds,
                "codes": sorted({str(record.get("code")) for record in self.events.values()}),
            }

    def event_list(self) -> list[dict[str, Any]]:
        with self.lock:
            return sorted(self.events.values(), key=lambda item: item["received_at_ms"])


def build_handler(store: AlertStore, heartbeat_timeout_seconds: float):
    class Handler(BaseHTTPRequestHandler):
        server_version = "QTSAlertReceiver/1"
        protocol_version = "HTTP/1.1"

        def log_message(self, format: str, *args: Any) -> None:  # noqa: A002
            # Keep the receiver log free of query strings and header values.
            print(f"alert-receiver {self.command} {urlsplit(self.path).path}")

        def _send(self, status: int, payload: dict[str, Any]) -> None:
            body = json.dumps(payload, sort_keys=True).encode("utf-8")
            self.send_response(status)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

        def _body(self) -> bytes:
            length = int(self.headers.get("Content-Length", "0"))
            return self.rfile.read(length) if length else b""

        def do_GET(self) -> None:  # noqa: N802
            path = urlsplit(self.path).path
            if path == "/health":
                self._send(200, {"status": "ok"})
            elif path == "/status":
                self._send(200, store.status(heartbeat_timeout_seconds))
            elif path == "/events":
                self._send(200, {"events": store.event_list()})
            elif path == "/heartbeats":
                with store.lock:
                    self._send(200, {"heartbeats": list(store.heartbeats)})
            else:
                self._send(404, {"error": "NOT_FOUND"})

        def do_POST(self) -> None:  # noqa: N802
            path = urlsplit(self.path).path
            body = self._body()
            if not store.verify(body, self.headers.get("X-QTS-Signature", "")):
                self._send(401, {"ack": False, "error": "SIGNATURE_INVALID"})
                return
            try:
                payload = json.loads(body.decode("utf-8"))
            except (ValueError, UnicodeDecodeError):
                self._send(400, {"ack": False, "error": "INVALID_JSON"})
                return
            if not isinstance(payload, dict):
                self._send(400, {"ack": False, "error": "INVALID_PAYLOAD"})
                return
            if path == "/webhook":
                event_id = str(payload.get("event_id", ""))
                if not event_id:
                    self._send(400, {"ack": False, "error": "EVENT_ID_REQUIRED"})
                    return
                created = store.record_event(payload)
                self._send(
                    200,
                    {
                        "ack": True,
                        "alert_id": event_id,
                        "duplicate": not created,
                        "received_at_ms": int(time.time() * 1000),
                    },
                )
            elif path == "/heartbeat":
                heartbeat_id = str(payload.get("heartbeat_id", ""))
                if not heartbeat_id:
                    self._send(400, {"ack": False, "error": "HEARTBEAT_ID_REQUIRED"})
                    return
                store.record_heartbeat(payload)
                self._send(
                    200,
                    {
                        "ack": True,
                        "ack_id": heartbeat_id,
                        "received_at_ms": int(time.time() * 1000),
                    },
                )
            else:
                self._send(404, {"ack": False, "error": "NOT_FOUND"})

    return Handler


def main() -> None:
    parser = argparse.ArgumentParser(description="QTS rehearsal alert receiver")
    parser.add_argument("--host", default="0.0.0.0")
    parser.add_argument("--port", type=int, default=8085)
    parser.add_argument("--journal", default="/data/alerts.jsonl")
    parser.add_argument(
        "--heartbeat-timeout-seconds",
        type=float,
        default=float(os.getenv("QTS_ALERT_HEARTBEAT_TIMEOUT_SECONDS", "30")),
    )
    args = parser.parse_args()
    store = AlertStore(args.journal, os.getenv("QTS_ALERT_SIGNING_SECRET", ""))
    server = ThreadingHTTPServer(
        (args.host, args.port), build_handler(store, args.heartbeat_timeout_seconds)
    )
    print(f"alert receiver listening on {args.host}:{args.port} journal={args.journal}")
    server.serve_forever()


if __name__ == "__main__":
    main()
