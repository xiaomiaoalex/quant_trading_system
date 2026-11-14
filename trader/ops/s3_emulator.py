"""Minimal in-process S3-compatible endpoint for phase-5 fault drills.

The emulator implements the small path-style subset used by the backup code
(create bucket, PUT/HEAD/GET object). It is local to this host and therefore
never satisfies the off-host backup requirement; evidence produced against it
must be marked ``local_emulator``/``off_host=false``.
"""

from __future__ import annotations

import hashlib
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from typing import Any
from urllib.parse import parse_qs, urlsplit


class S3Emulator:
    def __init__(self) -> None:
        self.buckets: set[str] = set()
        self.objects: dict[tuple[str, str], bytes] = {}
        self.metadata: dict[tuple[str, str], dict[str, str]] = {}
        self.lock = threading.Lock()
        self.server: ThreadingHTTPServer | None = None
        self.thread: threading.Thread | None = None

    @property
    def endpoint_url(self) -> str:
        if self.server is None:
            raise RuntimeError("S3_EMULATOR_NOT_STARTED")
        host, port = self.server.server_address[:2]
        return f"http://{host}:{port}"

    def start(self, host: str = "127.0.0.1", port: int = 0) -> "S3Emulator":
        emulator = self

        class Handler(BaseHTTPRequestHandler):
            protocol_version = "HTTP/1.1"

            def log_message(self, format, *args):  # noqa: A002
                return

            def _send(self, status: int, body: bytes = b"", headers: dict[str, str] | None = None):
                headers = dict(headers or {})
                self.send_response(status)
                for key, value in headers.items():
                    self.send_header(key, value)
                if "Content-Length" not in headers:
                    self.send_header("Content-Length", str(len(body)))
                self.end_headers()
                if body and self.command != "HEAD":
                    self.wfile.write(body)

            def _parts(self) -> tuple[str, str, dict[str, list[str]]]:
                split = urlsplit(self.path)
                path = split.path.lstrip("/")
                bucket, _, key = path.partition("/")
                return bucket, key, parse_qs(split.query)

            def do_PUT(self) -> None:  # noqa: N802
                bucket, key, _query = self._parts()
                length = int(self.headers.get("Content-Length", "0"))
                body = self.rfile.read(length) if length else b""
                with emulator.lock:
                    if not key:
                        emulator.buckets.add(bucket)
                        self._send(200, headers={"ETag": '"bucket"'})
                        return
                    if bucket not in emulator.buckets:
                        self._send(404, b"<Error><Code>NoSuchBucket</Code></Error>")
                        return
                    emulator.objects[(bucket, key)] = body
                    emulator.metadata[(bucket, key)] = {
                        str(name).lower(): str(value)
                        for name, value in self.headers.items()
                        if str(name).lower().startswith("x-amz-meta-")
                    }
                etag = hashlib.md5(body).hexdigest()
                self._send(200, headers={"ETag": f'"{etag}"'})

            def do_HEAD(self) -> None:  # noqa: N802
                bucket, key, _query = self._parts()
                with emulator.lock:
                    if not key:
                        self._send(200 if bucket in emulator.buckets else 404)
                        return
                    body = emulator.objects.get((bucket, key))
                    metadata = emulator.metadata.get((bucket, key), {})
                if body is None:
                    self._send(404)
                    return
                headers = {
                    "Content-Type": "application/octet-stream",
                    "Content-Length": str(len(body)),
                    "ETag": '"' + hashlib.md5(body).hexdigest() + '"',
                    **metadata,
                }
                self._send(200, headers=headers)

            def do_GET(self) -> None:  # noqa: N802
                bucket, key, query = self._parts()
                with emulator.lock:
                    if not key:
                        if "location" in query:
                            body = (
                                b'<?xml version="1.0" encoding="UTF-8"?>'
                                b'<LocationConstraint xmlns="http://s3.amazonaws.com/doc/2006-03-01/">'
                                b"us-east-1</LocationConstraint>"
                            )
                            self._send(200, body, {"Content-Type": "application/xml"})
                            return
                        self._send(200 if bucket in emulator.buckets else 404)
                        return
                    body = emulator.objects.get((bucket, key))
                if body is None:
                    self._send(404, b"<Error><Code>NoSuchKey</Code></Error>")
                    return
                self._send(
                    200,
                    body,
                    {
                        "Content-Type": "application/octet-stream",
                        "ETag": '"' + hashlib.md5(body).hexdigest() + '"',
                    },
                )

            def do_DELETE(self) -> None:  # noqa: N802
                bucket, key, _query = self._parts()
                with emulator.lock:
                    if key:
                        emulator.objects.pop((bucket, key), None)
                        emulator.metadata.pop((bucket, key), None)
                    else:
                        emulator.buckets.discard(bucket)
                self._send(204)

        self.server = ThreadingHTTPServer((host, port), Handler)
        self.thread = threading.Thread(target=self.server.serve_forever, daemon=True)
        self.thread.start()
        return self

    def stop(self) -> None:
        if self.server is not None:
            self.server.shutdown()
            self.server.server_close()
        if self.thread is not None:
            self.thread.join(timeout=5)
        self.server = None
        self.thread = None

    def put(
        self, bucket: str, key: str, body: bytes, metadata: dict[str, str] | None = None
    ) -> None:
        with self.lock:
            self.buckets.add(bucket)
            self.objects[(bucket, key)] = body
            self.metadata[(bucket, key)] = dict(metadata or {})

    def get(self, bucket: str, key: str) -> bytes | None:
        with self.lock:
            return self.objects.get((bucket, key))

    def snapshot(self) -> dict[str, Any]:
        with self.lock:
            return {
                "buckets": sorted(self.buckets),
                "object_keys": sorted(key for _bucket, key in self.objects),
                "object_count": len(self.objects),
            }
