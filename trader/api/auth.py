"""Single-operator authentication; sessions survive API restarts in PostgreSQL."""

from __future__ import annotations

import argparse
import getpass
import hashlib
import hmac
import os
import secrets
import time
from dataclasses import dataclass

from fastapi import Request
from starlette.middleware.base import BaseHTTPMiddleware
from starlette.responses import JSONResponse


def hash_password(password: str, salt: str | None = None) -> str:
    if len(password) < 12:
        raise ValueError("Password must contain at least 12 characters")
    salt = salt or secrets.token_hex(16)
    digest = hashlib.scrypt(password.encode(), salt=bytes.fromhex(salt), n=16384, r=8, p=1)
    return f"scrypt${salt}${digest.hex()}"


def verify_password(password: str, encoded: str) -> bool:
    try:
        algorithm, salt, expected = encoded.split("$")
        if algorithm != "scrypt" or len(salt) != 32 or len(expected) != 128:
            return False
        actual = hashlib.scrypt(password.encode(), salt=bytes.fromhex(salt), n=16384, r=8, p=1)
        return hmac.compare_digest(actual.hex(), expected)
    except (ValueError, TypeError):
        return False


def csrf_for(token: str) -> str:
    return hmac.new(token.encode(), b"qts-csrf-v1", hashlib.sha256).hexdigest()


@dataclass(frozen=True)
class AuthSettings:
    username: str = "owner"
    password_hash: str = ""
    secure_cookie: bool = True
    origin: str = "https://localhost"
    lifetime_seconds: int = 43200
    require_auth: bool = False

    @property
    def enabled(self) -> bool:
        return bool(self.password_hash) or self.require_auth

    @classmethod
    def from_env(cls):
        origin = os.getenv("QTS_ORIGIN", "http://127.0.0.1:5173")
        return cls(
            username=os.getenv("QTS_USERNAME", "owner"),
            password_hash=os.getenv("QTS_PASSWORD_HASH", ""),
            secure_cookie=not origin.startswith("http://127.0.0.1:")
            and not origin.startswith("http://localhost:"),
            origin=origin.rstrip("/"),
            require_auth=os.getenv("QTS_AUTH_REQUIRED", "").lower() in {"1", "true", "yes", "on"},
        )


class PostgresSessionStore:
    def __init__(self, pool):
        self.pool = pool

    async def initialize(self):
        async with self.pool.acquire() as conn:
            await conn.execute(
                """
                CREATE SCHEMA IF NOT EXISTS qts_control;
                CREATE TABLE IF NOT EXISTS qts_control.sessions (
                    token_hash text PRIMARY KEY, actor text NOT NULL, expires_at double precision NOT NULL
                );
                CREATE TABLE IF NOT EXISTS qts_control.login_limits (
                    key text PRIMARY KEY, window_start double precision NOT NULL, attempts integer NOT NULL
                );
            """
            )

    async def permit_login(self, key, now):
        row = await self.pool.fetchrow(
            """
            INSERT INTO qts_control.login_limits(key,window_start,attempts) VALUES($1,$2,1)
            ON CONFLICT(key) DO UPDATE SET
              attempts=CASE WHEN qts_control.login_limits.window_start < $2-60 THEN 1
                            ELSE qts_control.login_limits.attempts+1 END,
              window_start=CASE WHEN qts_control.login_limits.window_start < $2-60 THEN $2
                                ELSE qts_control.login_limits.window_start END
            RETURNING attempts
        """,
            key,
            now,
        )
        return row["attempts"] <= 5

    async def create(self, token, actor, expires):
        await self.pool.execute(
            "INSERT INTO qts_control.sessions VALUES($1,$2,$3)",
            hashlib.sha256(token.encode()).hexdigest(),
            actor,
            expires,
        )

    async def reset_login(self, key):
        await self.pool.execute("DELETE FROM qts_control.login_limits WHERE key=$1", key)

    async def actor(self, token, now):
        return await self.pool.fetchval(
            "SELECT actor FROM qts_control.sessions WHERE token_hash=$1 AND expires_at>$2",
            hashlib.sha256(token.encode()).hexdigest(),
            now,
        )

    async def revoke(self, token):
        await self.pool.execute(
            "DELETE FROM qts_control.sessions WHERE token_hash=$1",
            hashlib.sha256(token.encode()).hexdigest(),
        )


class MemorySessionStore:
    """Session store for tests and single-process smoke runs."""

    def __init__(self):
        self.sessions: dict[str, tuple[str, float]] = {}
        self.attempts: dict[str, tuple[float, int]] = {}

    async def initialize(self):
        return None

    async def permit_login(self, key, now):
        window_start, attempts = self.attempts.get(key, (now, 0))
        if window_start < now - 60:
            window_start, attempts = now, 0
        attempts += 1
        self.attempts[key] = (window_start, attempts)
        return attempts <= 5

    async def reset_login(self, key):
        self.attempts.pop(key, None)

    async def create(self, token, actor, expires):
        self.sessions[hashlib.sha256(token.encode()).hexdigest()] = (actor, expires)

    async def actor(self, token, now):
        value = self.sessions.get(hashlib.sha256(token.encode()).hexdigest())
        if value is None or value[1] <= now:
            return None
        return value[0]

    async def revoke(self, token):
        self.sessions.pop(hashlib.sha256(token.encode()).hexdigest(), None)


class SessionMiddleware(BaseHTTPMiddleware):
    async def dispatch(self, request: Request, call_next):
        public = {
            "/health",
            "/health/live",
            "/health/ready",
            "/health/trading",
            "/v1/auth/login",
            "/docs",
            "/openapi.json",
        }
        settings = request.app.state.auth_settings
        if request.method not in {"GET", "HEAD", "OPTIONS"}:
            origin = request.headers.get("origin")
            if origin and origin.rstrip("/") != settings.origin:
                return JSONResponse({"detail": "Origin not allowed"}, 403)
        if request.url.path in public:
            return await call_next(request)
        if not settings.enabled:
            # Development mode: no password configured. Never used for real funds.
            request.state.actor = "dev"
            return await call_next(request)
        sessions = getattr(request.app.state, "sessions", None)
        if sessions is None:
            return JSONResponse({"detail": "Authentication storage unavailable"}, 503)
        token = request.cookies.get("qts_session", "")
        try:
            actor = await sessions.actor(token, time.time()) if token else None
        except Exception:
            return JSONResponse({"detail": "Authentication storage unavailable"}, 503)
        if not actor:
            return JSONResponse({"detail": "Authentication required"}, 401)
        if request.method not in {"GET", "HEAD", "OPTIONS"}:
            supplied = request.headers.get("x-csrf-token", "")
            if not supplied or not hmac.compare_digest(supplied, csrf_for(token)):
                return JSONResponse({"detail": "CSRF validation failed"}, 403)
        request.state.actor = actor
        return await call_next(request)


if __name__ == "__main__":
    argparse.ArgumentParser(
        description="Print an owner password hash for QTS_PASSWORD_HASH"
    ).parse_args()
    print(hash_password(getpass.getpass("Owner password (at least 12 characters): ")))
