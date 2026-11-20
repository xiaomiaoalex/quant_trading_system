import io
import json
import socket
import sys
import unittest
import urllib.error
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import verify_egress as ve  # noqa: E402


class FakeClock:
    """Deterministic monotonic clock; tests never really wait."""

    def __init__(self, start: float = 0.0) -> None:
        self.now = start

    def __call__(self) -> float:
        return self.now

    def advance(self, seconds: float) -> None:
        self.now += seconds


class FakeSocket:
    """Scripted WebSocket peer for pump_websocket()."""

    def __init__(self, clock: FakeClock, script: list[tuple]) -> None:
        self.clock = clock
        self.script = list(script)
        self.sent: list[bytes] = []

    def settimeout(self, _timeout: float) -> None:
        return None

    def sendall(self, data: bytes) -> None:
        self.sent.append(data)

    def recv(self, _size: int) -> bytes:
        if not self.script:
            self.clock.advance(1.0)
            return b""
        action = self.script.pop(0)
        kind = action[0]
        if kind == "advance":
            _, seconds, data = action
            self.clock.advance(seconds)
            return data
        if kind == "timeout":
            _, seconds = action
            self.clock.advance(seconds)
            raise socket.timeout()
        if kind == "eof":
            _, seconds = action
            self.clock.advance(seconds)
            return b""
        raise AssertionError(f"unknown action {kind}")


def text_frame(payload: bytes) -> bytes:
    return bytes([0x81, len(payload)]) + payload


def close_frame() -> bytes:
    return bytes([0x88, 0x00])


class FakeHeaders:
    def __init__(self, headers: dict[str, str]) -> None:
        self._headers = headers

    def items(self):
        return list(self._headers.items())


class FakeResponse:
    def __init__(self, status: int, headers: dict[str, str], body: bytes) -> None:
        self.status = status
        self.headers = FakeHeaders(headers)
        self._body = body

    def read(self, _size: int = -1) -> bytes:
        return self._body

    def __enter__(self):
        return self

    def __exit__(self, *_exc):
        return False


class FakeOpener:
    """Returns canned responses keyed by URL; non-2xx raise HTTPError."""

    def __init__(self, responses: dict[str, tuple[int, dict[str, str], bytes]]):
        self.responses = responses
        self.calls: list[str] = []

    def open(self, request, timeout=None):
        url = request.full_url
        self.calls.append(url)
        status, headers, body = self.responses[url]
        if status >= 400:
            raise urllib.error.HTTPError(
                url, status, "fake", FakeHeaders(headers), io.BytesIO(body)
            )
        return FakeResponse(status, headers, body)


class HandshakeValidationTests(unittest.TestCase):
    def test_valid_handshake_is_accepted(self) -> None:
        key = "dGhlIHNhbXBsZSBub25jZQ=="
        headers = {
            "upgrade": "websocket",
            "connection": "Upgrade",
            "sec-websocket-accept": ve.expected_accept(key),
        }
        result = ve.validate_handshake("HTTP/1.1 101 Switching Protocols", headers, key)
        self.assertTrue(result["ok"])
        self.assertTrue(all(result["checks"].values()))

    def test_wrong_accept_is_rejected(self) -> None:
        key = "dGhlIHNhbXBsZSBub25jZQ=="
        headers = {
            "upgrade": "websocket",
            "connection": "Upgrade",
            "sec-websocket-accept": "not-the-right-accept",
        }
        result = ve.validate_handshake("HTTP/1.1 101 Switching Protocols", headers, key)
        self.assertFalse(result["ok"])
        self.assertFalse(result["checks"]["sec_websocket_accept"])

    def test_missing_upgrade_or_connection_is_rejected(self) -> None:
        key = "dGhlIHNhbXBsZSBub25jZQ=="
        headers = {"sec-websocket-accept": ve.expected_accept(key)}
        result = ve.validate_handshake("HTTP/1.1 101 Switching Protocols", headers, key)
        self.assertFalse(result["ok"])
        self.assertFalse(result["checks"]["upgrade_websocket"])
        self.assertFalse(result["checks"]["connection_upgrade"])

    def test_non_101_status_is_rejected(self) -> None:
        key = "dGhlIHNhbXBsZSBub25jZQ=="
        headers = {
            "upgrade": "websocket",
            "connection": "Upgrade",
            "sec-websocket-accept": ve.expected_accept(key),
        }
        result = ve.validate_handshake("HTTP/1.1 403 Forbidden", headers, key)
        self.assertFalse(result["ok"])
        self.assertFalse(result["checks"]["status_101"])


class WsHoldEvaluationTests(unittest.TestCase):
    def test_full_hold_without_early_close_passes(self) -> None:
        self.assertTrue(ve.evaluate_ws_ok(True, 300.0, 300, False))

    def test_short_hold_fails(self) -> None:
        self.assertFalse(ve.evaluate_ws_ok(True, 299.9, 300, False))
        self.assertFalse(ve.evaluate_ws_ok(True, 100.0, 300, False))

    def test_early_close_fails_even_with_full_elapsed(self) -> None:
        self.assertFalse(ve.evaluate_ws_ok(True, 300.0, 300, True))

    def test_invalid_handshake_fails(self) -> None:
        self.assertFalse(ve.evaluate_ws_ok(False, 300.0, 300, False))


class WsPumpTests(unittest.TestCase):
    def test_hold_timer_excludes_handshake_time(self) -> None:
        clock = FakeClock()
        clock.advance(120.0)  # simulate a slow handshake before pumping
        sock = FakeSocket(
            clock,
            [
                ("advance", 100.0, text_frame(b'{"e":"trade"}')),
                ("advance", 100.0, text_frame(b'{"e":"trade"}')),
                ("advance", 100.0, text_frame(b'{"e":"trade"}')),
                ("advance", 100.0, text_frame(b'{"e":"trade"}')),
            ],
        )
        pump = ve.pump_websocket(sock, 300, clock)
        self.assertEqual(pump["hold_elapsed_s"], 300.0)
        self.assertFalse(pump["early_close"])
        self.assertTrue(ve.evaluate_ws_ok(True, pump["hold_elapsed_s"], 300, pump["early_close"]))

    def test_early_close_frame_fails(self) -> None:
        clock = FakeClock()
        sock = FakeSocket(clock, [("advance", 30.0, close_frame())])
        pump = ve.pump_websocket(sock, 300, clock)
        self.assertTrue(pump["close_seen"])
        self.assertTrue(pump["early_close"])
        self.assertFalse(ve.evaluate_ws_ok(True, pump["hold_elapsed_s"], 300, pump["early_close"]))

    def test_eof_before_deadline_fails(self) -> None:
        clock = FakeClock()
        sock = FakeSocket(clock, [("advance", 100.0, b"")])
        pump = ve.pump_websocket(sock, 300, clock)
        self.assertTrue(pump["early_close"])
        self.assertEqual(pump["error"], "CONN_CLOSED_EARLY")
        self.assertLess(pump["hold_elapsed_s"], 300)
        self.assertFalse(ve.evaluate_ws_ok(True, pump["hold_elapsed_s"], 300, pump["early_close"]))

    def test_ping_is_answered(self) -> None:
        clock = FakeClock()
        ping = bytes([0x89, 0x00])
        sock = FakeSocket(
            clock,
            [
                ("advance", 100.0, ping),
                ("advance", 100.0, text_frame(b"x")),
                ("advance", 100.0, text_frame(b"x")),
                ("advance", 100.0, text_frame(b"x")),
            ],
        )
        pump = ve.pump_websocket(sock, 300, clock)
        self.assertEqual(pump["pings_answered"], 1)
        self.assertTrue(sock.sent)


class HttpSemanticTests(unittest.TestCase):
    def test_empty_github_body_is_not_green(self) -> None:
        opener = FakeOpener(
            {"https://github.com": (200, {"content-type": "text/html"}, b"")}
        )
        result = ve.probe_github(opener, 5)
        self.assertTrue(result["status_ok"])
        self.assertFalse(result["semantic_ok"])
        self.assertFalse(result["ok"])
        self.assertEqual(result["semantic"], "empty_body")

    def test_malformed_binance_server_time_is_not_green(self) -> None:
        opener = FakeOpener(
            {
                "https://demo-api.binance.com/api/v3/time": (
                    200,
                    {"content-type": "application/json"},
                    b'{"serverTime": "not-a-number"}',
                )
            }
        )
        result = ve.probe_binance_rest(opener, 5)
        self.assertTrue(result["status_ok"])
        self.assertFalse(result["semantic_ok"])
        self.assertFalse(result["ok"])

    def test_plausible_server_time_is_green(self) -> None:
        import time as _time

        payload = json.dumps({"serverTime": int(_time.time() * 1000)}).encode()
        opener = FakeOpener(
            {
                "https://demo-api.binance.com/api/v3/time": (
                    200,
                    {"content-type": "application/json"},
                    payload,
                )
            }
        )
        result = ve.probe_binance_rest(opener, 5)
        self.assertTrue(result["ok"])
        self.assertEqual(result["semantic"], "serverTime_plausible")

    def test_docker_manifest_without_digest_is_not_green(self) -> None:
        opener = FakeOpener(
            {
                "https://registry-1.docker.io/v2/": (
                    401,
                    {"www-authenticate": 'Bearer realm="https://auth.docker.io/token"'},
                    b"{}",
                ),
                "https://auth.docker.io/token?service=registry.docker.io&scope=repository:library/alpine:pull": (
                    200,
                    {"content-type": "application/json"},
                    b'{"token": "abc"}',
                ),
                "https://registry-1.docker.io/v2/library/alpine/manifests/latest": (
                    200,
                    {"content-type": "application/json"},
                    b'{"mediaType":"x","schemaVersion":2}',
                ),
            }
        )
        result = ve.probe_docker(opener, 5)
        self.assertTrue(result["challenge"]["ok"])
        self.assertTrue(result["token"]["ok"])
        self.assertFalse(result["manifest"]["semantic_ok"])
        self.assertFalse(result["manifest"]["ok"])
        self.assertEqual(result["manifest"]["semantic"], "digest_missing")

    def test_docker_token_without_token_is_not_green(self) -> None:
        opener = FakeOpener(
            {
                "https://registry-1.docker.io/v2/": (
                    401,
                    {"www-authenticate": 'Bearer realm="https://auth.docker.io/token"'},
                    b"{}",
                ),
                "https://auth.docker.io/token?service=registry.docker.io&scope=repository:library/alpine:pull": (
                    200,
                    {"content-type": "application/json"},
                    b'{"token": ""}',
                ),
            }
        )
        result = ve.probe_docker(opener, 5)
        self.assertFalse(result["token"]["semantic_ok"])
        self.assertFalse(result["token"]["ok"])

    def test_docker_challenge_without_bearer_is_not_green(self) -> None:
        opener = FakeOpener(
            {
                "https://registry-1.docker.io/v2/": (
                    200,
                    {"content-type": "application/json"},
                    b"{}",
                )
            }
        )
        result = ve.probe_docker(opener, 5)
        self.assertFalse(result["challenge"]["status_ok"])
        self.assertFalse(result["challenge"]["ok"])

    def test_run_semantics_requires_every_item(self) -> None:
        good_item = {
            "ok": True,
            "status_ok": True,
            "semantic_ok": True,
            "status": 200,
        }
        run = {
            "github": dict(good_item),
            "docker": {
                "challenge": dict(good_item),
                "token": dict(good_item),
                "manifest": dict(good_item),
            },
            "binance_rest": dict(good_item),
        }
        self.assertTrue(ve.run_semantics_ok(run))
        run["docker"]["manifest"] = dict(good_item, ok=False, semantic_ok=False)
        self.assertFalse(ve.run_semantics_ok(run))


if __name__ == "__main__":
    unittest.main()
