#!/usr/bin/env python3
"""Verify outbound HTTP/TLS/WebSocket reachability through an explicit proxy.

Every request uses certificate verification.  The proxy credential is read from
a mode-0600 file and never printed.  The JSON report contains only timings,
status codes, failure classes, and non-secret metadata (for example the Binance
``serverTime`` value and the Docker manifest digest header).

Acceptance rules implemented here:

* HTTP items are only ``ok`` when the status code matches *and* the response is
  semantically valid (Bearer challenge present, anonymous token present, public
  manifest digest present, plausible ``serverTime``, non-empty body).  An empty
  or malformed ``200`` is reported as a failure, not as a green result.
* The WebSocket session must complete a valid RFC 6455 handshake
  (``101`` + ``Upgrade: websocket`` + ``Connection: Upgrade`` +
  matching ``Sec-WebSocket-Accept``).
* The hold timer starts *after* the handshake completes and must cover the full
  requested duration.  An early close or EOF fails the check; the measured
  ``hold_elapsed_s`` is always reported.

Examples:
    python3 verify_egress.py --proxy-url-file proxy-urls.txt --proxy-key host
    python3 verify_egress.py --direct --skip-ws --allow-failures
    python3 verify_egress.py --proxy-url-file proxy-urls.txt --proxy-key container
"""

from __future__ import annotations

import argparse
import base64
import hashlib
import json
import os
import socket
import ssl
import sys
import time
import urllib.error
import urllib.parse
import urllib.request
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable

DOCKER_MANIFEST_ACCEPT = (
    "application/vnd.docker.distribution.manifest.list.v2+json, "
    "application/vnd.oci.image.index.v1+json, "
    "application/vnd.docker.distribution.manifest.v2+json"
)
WS_GUID = "258EAFA5-E914-47DA-95CA-C5AB0DC85B11"
SERVER_TIME_MAX_SKEW_MS = 24 * 60 * 60 * 1000


def utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()


def classify_reason(reason: BaseException) -> str:
    if isinstance(reason, (socket.timeout, TimeoutError)):
        return "TIMEOUT"
    if isinstance(reason, ssl.SSLError):
        return "TLS_ERROR"
    if isinstance(reason, socket.gaierror):
        return "DNS_ERROR"
    if isinstance(reason, ConnectionRefusedError):
        return "CONN_REFUSED"
    if isinstance(reason, ConnectionResetError):
        return "CONN_RESET"
    if isinstance(reason, OSError):
        errno = getattr(reason, "errno", None)
        return {
            101: "NETWORK_UNREACHABLE",
            110: "TIMEOUT",
            111: "CONN_REFUSED",
            113: "NO_ROUTE",
        }.get(errno, "OS_ERROR")
    return "URL_ERROR_" + type(reason).__name__


def classify_error(exc: BaseException) -> str:
    if isinstance(exc, urllib.error.URLError):
        return classify_reason(exc.reason)
    if isinstance(exc, (socket.timeout, TimeoutError)):
        return "TIMEOUT"
    if isinstance(exc, ssl.SSLError):
        return "TLS_ERROR"
    if isinstance(exc, ConnectionResetError):
        return "CONN_RESET"
    if isinstance(exc, ConnectionRefusedError):
        return "CONN_REFUSED"
    if isinstance(exc, ConnectionError):
        return "CONNECT_ERROR"
    return type(exc).__name__


def read_proxy_url(path: Path, key: str) -> str:
    for raw_line in path.read_text(encoding="utf-8").splitlines():
        line = raw_line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        name, value = line.split("=", 1)
        if name.strip() == key:
            return value.strip()
    raise SystemExit(f"proxy key not found: {key}")


def build_opener(proxy_url: str | None) -> urllib.request.OpenerDirector:
    if proxy_url:
        handler = urllib.request.ProxyHandler(
            {"http": proxy_url, "https": proxy_url}
        )
    else:
        handler = urllib.request.ProxyHandler({})
    return urllib.request.build_opener(handler)


def http_request(
    opener: urllib.request.OpenerDirector,
    url: str,
    timeout: float,
    headers: dict[str, str] | None = None,
    max_body: int = 65536,
) -> dict[str, Any]:
    start = utc_now()
    started = time.monotonic()
    status: int | None = None
    response_headers: dict[str, str] = {}
    body = b""
    error: str | None = None
    try:
        request = urllib.request.Request(url, headers=headers or {})
        with opener.open(request, timeout=timeout) as response:
            status = response.status
            response_headers = {
                key.lower(): value for key, value in response.headers.items()
            }
            body = response.read(max_body)
    except urllib.error.HTTPError as exc:
        status = exc.code
        response_headers = {
            key.lower(): value for key, value in exc.headers.items()
        }
        try:
            body = exc.read(max_body)
        except OSError:
            body = b""
    except Exception as exc:  # noqa: BLE001 - report class only
        error = classify_error(exc)
    elapsed_ms = round((time.monotonic() - started) * 1000, 1)
    return {
        "start_utc": start,
        "end_utc": utc_now(),
        "elapsed_ms": elapsed_ms,
        "status": status,
        "headers": response_headers,
        "body": body,
        "error": error,
    }


def safe_result(
    result: dict[str, Any],
    expected: set[int],
    semantic_ok: bool,
    semantic_detail: str,
    extra: dict[str, Any] | None = None,
) -> dict[str, Any]:
    status_ok = result["error"] is None and result["status"] in expected
    ok = status_ok and semantic_ok
    safe = {
        "start_utc": result["start_utc"],
        "end_utc": result["end_utc"],
        "elapsed_ms": result["elapsed_ms"],
        "status": result["status"],
        "expected": sorted(expected),
        "status_ok": status_ok,
        "semantic_ok": semantic_ok,
        "semantic": semantic_detail,
        "ok": ok,
        "partial": result["status"] in expected and result["error"] is not None,
        "error": result["error"],
    }
    if extra:
        safe.update(extra)
    return safe


def probe_github(
    opener: urllib.request.OpenerDirector, timeout: float
) -> dict[str, Any]:
    result = http_request(opener, "https://github.com", timeout, max_body=1024)
    content_ok = len(result["body"]) > 0
    return safe_result(
        result,
        {200},
        content_ok,
        "body_present" if content_ok else "empty_body",
        {"body_prefix_bytes": len(result["body"])},
    )


def probe_docker(
    opener: urllib.request.OpenerDirector, timeout: float
) -> dict[str, Any]:
    challenge = http_request(
        opener, "https://registry-1.docker.io/v2/", timeout, max_body=1024
    )
    auth_header = challenge["headers"].get("www-authenticate", "")
    bearer_ok = "bearer" in auth_header.lower()
    challenge_result = safe_result(
        challenge,
        {401},
        bearer_ok,
        "bearer_challenge" if bearer_ok else "no_bearer_challenge",
        {
            "bearer_challenge": bearer_ok,
            "body_prefix_bytes": len(challenge["body"]),
        },
    )

    token_result = http_request(
        opener,
        "https://auth.docker.io/token"
        "?service=registry.docker.io"
        "&scope=repository:library/alpine:pull",
        timeout,
    )
    token_value = ""
    if token_result["status"] == 200:
        try:
            token_value = str(
                json.loads(token_result["body"]).get("token") or ""
            )
        except (json.JSONDecodeError, UnicodeDecodeError):
            token_value = ""
    token_present = bool(token_value)
    token_safe = safe_result(
        token_result,
        {200},
        token_present,
        "token_present" if token_present else "token_missing",
        {"token_present": token_present},
    )

    manifest_result = http_request(
        opener,
        "https://registry-1.docker.io/v2/library/alpine/manifests/latest",
        timeout,
        headers={
            "Authorization": f"Bearer {token_value}",
            "Accept": DOCKER_MANIFEST_ACCEPT,
        },
        max_body=65536,
    )
    digest = manifest_result["headers"].get("docker-content-digest")
    manifest_parsed = False
    if manifest_result["status"] == 200:
        try:
            payload = json.loads(manifest_result["body"])
            manifest_parsed = bool(
                isinstance(payload, dict)
                and payload.get("mediaType")
                and payload.get("schemaVersion")
            )
        except (json.JSONDecodeError, UnicodeDecodeError):
            manifest_parsed = False
    manifest_ok = bool(digest) and manifest_parsed
    if manifest_ok:
        semantic_detail = "digest_and_manifest_present"
    elif not digest:
        semantic_detail = "digest_missing"
    else:
        semantic_detail = "manifest_body_unparsable"
    manifest_safe = safe_result(
        manifest_result,
        {200},
        manifest_ok,
        semantic_detail,
        {
            "digest": digest,
            "content_type": manifest_result["headers"].get("content-type"),
            "manifest_parsed": manifest_parsed,
            "body_prefix_bytes": len(manifest_result["body"]),
        },
    )
    return {
        "challenge": challenge_result,
        "token": token_safe,
        "manifest": manifest_safe,
    }


def probe_binance_rest(
    opener: urllib.request.OpenerDirector, timeout: float
) -> dict[str, Any]:
    result = http_request(
        opener,
        "https://demo-api.binance.com/api/v3/time",
        timeout,
        max_body=1024,
    )
    server_time: Any = None
    server_time_iso: str | None = None
    server_time_ok = False
    if result["status"] == 200:
        try:
            payload = json.loads(result["body"])
            server_time = payload.get("serverTime")
            if isinstance(server_time, (int, float)) and not isinstance(
                server_time, bool
            ):
                now_ms = time.time() * 1000
                server_time_ok = (
                    abs(float(server_time) - now_ms) <= SERVER_TIME_MAX_SKEW_MS
                )
                server_time_iso = datetime.fromtimestamp(
                    float(server_time) / 1000, timezone.utc
                ).isoformat()
        except (json.JSONDecodeError, UnicodeDecodeError):
            server_time = None
    if server_time_ok:
        semantic_detail = "serverTime_plausible"
    elif server_time is None:
        semantic_detail = "serverTime_missing_or_malformed"
    else:
        semantic_detail = "serverTime_implausible"
    return safe_result(
        result,
        {200},
        server_time_ok,
        semantic_detail,
        {"serverTime": server_time, "serverTime_iso": server_time_iso},
    )


def parse_proxy(proxy_url: str) -> tuple[str, int, str | None, str | None]:
    parsed = urllib.parse.urlsplit(proxy_url)
    if not parsed.hostname:
        raise ValueError("proxy URL has no hostname")
    username = (
        urllib.parse.unquote(parsed.username) if parsed.username else None
    )
    password = (
        urllib.parse.unquote(parsed.password) if parsed.password else None
    )
    return parsed.hostname, parsed.port or 8080, username, password


def expected_accept(key: str) -> str:
    digest = hashlib.sha1((key + WS_GUID).encode("ascii")).digest()
    return base64.b64encode(digest).decode("ascii")


def parse_handshake_headers(raw: bytes) -> dict[str, str]:
    head = raw.split(b"\r\n\r\n", 1)[0].decode("latin1")
    lines = head.split("\r\n")
    headers: dict[str, str] = {}
    for line in lines[1:]:
        if ":" in line:
            name, value = line.split(":", 1)
            headers[name.strip().lower()] = value.strip()
    return headers


def validate_handshake(
    status_line: str, headers: dict[str, str], key: str
) -> dict[str, Any]:
    checks = {
        "status_101": " 101 " in status_line,
        "upgrade_websocket": "websocket"
        in headers.get("upgrade", "").lower(),
        "connection_upgrade": "upgrade"
        in headers.get("connection", "").lower(),
        "sec_websocket_accept": headers.get("sec-websocket-accept")
        == expected_accept(key),
    }
    return {
        "ok": all(checks.values()),
        "checks": checks,
        "accept_present": bool(headers.get("sec-websocket-accept")),
    }


def parse_frame(
    buffer: bytearray,
) -> tuple[tuple[int, bytes, bool] | None, int]:
    if len(buffer) < 2:
        return None, 0
    first, second = buffer[0], buffer[1]
    fin = bool(first & 0x80)
    opcode = first & 0x0F
    masked = bool(second & 0x80)
    length = second & 0x7F
    index = 2
    if length == 126:
        if len(buffer) < 4:
            return None, 0
        length = int.from_bytes(buffer[2:4], "big")
        index = 4
    elif length == 127:
        if len(buffer) < 10:
            return None, 0
        length = int.from_bytes(buffer[2:10], "big")
        index = 10
    mask = b""
    if masked:
        if len(buffer) < index + 4:
            return None, 0
        mask = bytes(buffer[index : index + 4])
        index += 4
    if len(buffer) < index + length:
        return None, 0
    payload = bytes(buffer[index : index + length])
    if masked and mask:
        payload = bytes(
            payload[i] ^ mask[i % 4] for i in range(len(payload))
        )
    return (opcode, payload, fin), index + length


def client_frame(opcode: int, payload: bytes) -> bytes:
    mask = os.urandom(4)
    masked = bytes(
        payload[i] ^ mask[i % 4] for i in range(len(payload))
    )
    first = 0x80 | opcode
    length = len(payload)
    if length < 126:
        header = bytes([first, 0x80 | length])
    elif length < 65536:
        header = bytes([first, 0x80 | 126]) + length.to_bytes(2, "big")
    else:
        header = bytes([first, 0x80 | 127]) + length.to_bytes(8, "big")
    return header + mask + masked


def pump_websocket(
    tls_socket: Any,
    hold_seconds: int,
    clock: Callable[[], float] = time.monotonic,
) -> dict[str, Any]:
    """Read frames until the hold deadline measured from *now* (post-handshake)."""
    started = clock()
    deadline = started + max(0, hold_seconds)
    frames = 0
    pings_answered = 0
    text_messages = 0
    close_seen = False
    early_close = False
    error: str | None = None
    buffer = bytearray()
    while clock() < deadline:
        parsed_any = True
        while parsed_any:
            parsed_any = False
            frame, consumed = parse_frame(buffer)
            if frame is None:
                break
            del buffer[:consumed]
            frames += 1
            parsed_any = True
            opcode, payload, _fin = frame
            if opcode == 0x9:
                pings_answered += 1
                tls_socket.sendall(client_frame(0xA, payload))
            elif opcode == 0x1:
                text_messages += 1
            elif opcode == 0x8:
                close_seen = True
                early_close = True
                break
        if close_seen:
            break
        remaining = deadline - clock()
        if remaining <= 0:
            break
        try:
            tls_socket.settimeout(min(1.0, remaining))
            chunk = tls_socket.recv(4096)
        except socket.timeout:
            continue
        except OSError as exc:
            early_close = True
            error = classify_error(exc)
            break
        if not chunk:
            early_close = True
            error = "CONN_CLOSED_EARLY"
            break
        buffer.extend(chunk)
    hold_elapsed = round(clock() - started, 3)
    return {
        "frames": frames,
        "text_messages": text_messages,
        "pings_answered": pings_answered,
        "close_seen": close_seen,
        "early_close": early_close,
        "hold_elapsed_s": hold_elapsed,
        "error": error,
    }


def evaluate_ws_ok(
    handshake_ok: bool,
    hold_elapsed_s: float,
    hold_required_s: int,
    early_close: bool,
) -> bool:
    if not handshake_ok or early_close:
        return False
    if hold_required_s <= 0:
        return True
    return hold_elapsed_s >= hold_required_s


def websocket_session(
    proxy_url: str | None,
    host: str,
    path: str,
    hold_seconds: int,
    timeout: float,
    clock: Callable[[], float] = time.monotonic,
) -> dict[str, Any]:
    start = utc_now()
    started = clock()
    handshake_status: str | None = None
    handshake_checks: dict[str, Any] = {}
    error: str | None = None
    pump: dict[str, Any] = {}
    tls_socket: Any = None
    try:
        if proxy_url:
            proxy_host, proxy_port, proxy_user, proxy_password = parse_proxy(
                proxy_url
            )
            raw = socket.create_connection(
                (proxy_host, proxy_port), timeout=timeout
            )
            connect_headers = [
                f"CONNECT {host}:443 HTTP/1.1",
                f"Host: {host}:443",
            ]
            if proxy_user is not None:
                credential = base64.b64encode(
                    f"{proxy_user}:{proxy_password or ''}".encode("utf-8")
                ).decode("ascii")
                connect_headers.append(
                    "Proxy-Authorization: Basic " + credential
                )
            raw.sendall(
                ("\r\n".join(connect_headers) + "\r\n\r\n").encode("ascii")
            )
            response = b""
            while b"\r\n\r\n" not in response:
                chunk = raw.recv(4096)
                if not chunk:
                    raise ConnectionError(
                        "proxy closed connection during CONNECT"
                    )
                response += chunk
            connect_status = response.split(b"\r\n", 1)[0].decode("latin1")
            if " 200 " not in connect_status:
                raise ConnectionError(
                    "proxy CONNECT failed: " + connect_status
                )
            context = ssl.create_default_context()
            tls_socket = context.wrap_socket(raw, server_hostname=host)
        else:
            raw = socket.create_connection((host, 443), timeout=timeout)
            context = ssl.create_default_context()
            tls_socket = context.wrap_socket(raw, server_hostname=host)

        key = base64.b64encode(os.urandom(16)).decode("ascii")
        request = (
            f"GET {path} HTTP/1.1\r\n"
            f"Host: {host}\r\n"
            "Upgrade: websocket\r\n"
            "Connection: Upgrade\r\n"
            f"Sec-WebSocket-Key: {key}\r\n"
            "Sec-WebSocket-Version: 13\r\n"
            "User-Agent: qts-egress-probe\r\n\r\n"
        )
        tls_socket.sendall(request.encode("ascii"))
        response = b""
        while b"\r\n\r\n" not in response:
            chunk = tls_socket.recv(4096)
            if not chunk:
                raise ConnectionError("closed during WebSocket handshake")
            response += chunk
        handshake_status = response.split(b"\r\n", 1)[0].decode("latin1")
        headers = parse_handshake_headers(response)
        handshake_checks = validate_handshake(
            handshake_status, headers, key
        )
        if not handshake_checks["ok"]:
            error = "WS_HANDSHAKE_INVALID"
            pump = {
                "frames": 0,
                "text_messages": 0,
                "pings_answered": 0,
                "close_seen": False,
                "early_close": False,
                "hold_elapsed_s": 0.0,
                "error": None,
            }
        else:
            pump = pump_websocket(tls_socket, hold_seconds, clock)
    except Exception as exc:  # noqa: BLE001 - report class only
        error = classify_error(exc)
        pump = {
            "frames": 0,
            "text_messages": 0,
            "pings_answered": 0,
            "close_seen": False,
            "early_close": False,
            "hold_elapsed_s": 0.0,
            "error": None,
        }
    finally:
        if tls_socket is not None:
            try:
                tls_socket.close()
            except OSError:
                pass
    hold_elapsed = float(pump.get("hold_elapsed_s", 0.0))
    handshake_ok = bool(handshake_checks.get("ok"))
    early_close = bool(pump.get("early_close"))
    ok = evaluate_ws_ok(
        handshake_ok, hold_elapsed, hold_seconds, early_close
    ) and error is None
    return {
        "start_utc": start,
        "end_utc": utc_now(),
        "elapsed_s": round(clock() - started, 1),
        "handshake_status": handshake_status,
        "handshake_checks": handshake_checks.get("checks", {}),
        "handshake_ok": handshake_ok,
        "hold_required_s": hold_seconds,
        "hold_elapsed_s": hold_elapsed,
        "early_close": early_close,
        "close_seen": bool(pump.get("close_seen")),
        "frames": pump.get("frames", 0),
        "text_messages": pump.get("text_messages", 0),
        "pings_answered": pump.get("pings_answered", 0),
        "ok": ok,
        "error": error or pump.get("error"),
    }


def run_semantics_ok(run: dict[str, Any]) -> bool:
    docker = run.get("docker") or {}
    return bool(
        run["github"]["ok"]
        and docker.get("challenge", {}).get("ok")
        and docker.get("token", {}).get("ok")
        and docker.get("manifest", {}).get("ok")
        and run["binance_rest"]["ok"]
    )


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--proxy-url-file", type=Path)
    parser.add_argument("--proxy-key", default="host")
    parser.add_argument("--direct", action="store_true")
    parser.add_argument("--runs", type=int, default=3)
    parser.add_argument("--timeout", type=float, default=10.0)
    parser.add_argument("--skip-http", action="store_true")
    parser.add_argument("--skip-ws", action="store_true")
    parser.add_argument("--ws-hold", type=int, default=300)
    parser.add_argument("--ws-host", default="demo-stream.binance.com")
    parser.add_argument("--ws-path", default="/ws/btcusdt@trade")
    parser.add_argument("--output", type=Path)
    parser.add_argument("--allow-failures", action="store_true")
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    args = parse_args(argv)
    if not args.direct and not args.proxy_url_file:
        raise SystemExit("--proxy-url-file is required unless --direct is used")
    proxy_url = None
    if not args.direct:
        assert args.proxy_url_file is not None
        proxy_url = read_proxy_url(args.proxy_url_file, args.proxy_key)
    opener = build_opener(proxy_url)

    report: dict[str, Any] = {
        "generated_utc": utc_now(),
        "mode": "direct" if args.direct else "proxy",
        "proxy_key": None if args.direct else args.proxy_key,
        "tls_verification": "enabled",
        "acceptance_rules": {
            "http": "status matches AND semantic payload valid",
            "ws_handshake": "101 + Upgrade + Connection + Sec-WebSocket-Accept",
            "ws_hold": "full hold measured after handshake, no early close",
        },
        "http_runs": [],
        "ws": None,
    }

    if not args.skip_http:
        for run in range(1, args.runs + 1):
            entry = {
                "run": run,
                "github": probe_github(opener, args.timeout),
                "docker": probe_docker(opener, args.timeout),
                "binance_rest": probe_binance_rest(opener, args.timeout),
            }
            entry["semantics_ok"] = run_semantics_ok(entry)
            report["http_runs"].append(entry)

    if not args.skip_ws:
        report["ws"] = websocket_session(
            proxy_url,
            args.ws_host,
            args.ws_path,
            args.ws_hold,
            args.timeout,
        )

    http_ok = all(run["semantics_ok"] for run in report["http_runs"])
    ws_ok = report["ws"]["ok"] if report["ws"] else True
    report["summary"] = {
        "http_ok": http_ok,
        "ws_ok": ws_ok,
        "all_ok": http_ok and ws_ok,
        "http_runs": len(report["http_runs"]),
    }

    output = json.dumps(report, indent=2, sort_keys=True) + "\n"
    if args.output:
        args.output.write_text(output, encoding="utf-8")
        try:
            os.chmod(args.output, 0o600)
        except OSError:
            pass
    sys.stdout.write(output)
    if args.allow_failures:
        return 0
    return 0 if report["summary"]["all_ok"] else 1


if __name__ == "__main__":
    sys.exit(main())
