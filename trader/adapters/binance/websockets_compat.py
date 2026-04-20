"""
WebSockets Compatibility Layer
==============================

Provide a runtime monkey patch for websocket implementations where
`connection_lost()` may be called before `recv_messages` is initialized.
"""
from __future__ import annotations

import logging
from typing import Any


class _RecvMessagesNoop:
    """Fallback object that satisfies `.close()` used by websockets internals."""

    def close(self) -> None:
        return None


def _patch_connection_lost(cls: type[Any], log: logging.Logger) -> bool:
    original = getattr(cls, "connection_lost", None)
    if original is None:
        return False

    # Idempotent patch: avoid wrapping repeatedly.
    if getattr(original, "_qts_guard_installed", False):
        return True

    def patched_connection_lost(self: Any, exc: BaseException | None) -> Any:
        if not hasattr(self, "recv_messages"):
            log.debug(
                "websockets_compat: recv_messages missing on %s, applying guard",
                cls.__name__,
            )
            try:
                object.__setattr__(self, "recv_messages", _RecvMessagesNoop())
            except Exception:
                # Keep original behavior if setattr is blocked.
                pass
        try:
            return original(self, exc)
        except AttributeError as err:
            if "recv_messages" not in str(err):
                raise
            log.debug(
                "websockets_compat: late recv_messages attr error on %s, retrying with guard",
                cls.__name__,
            )
            try:
                object.__setattr__(self, "recv_messages", _RecvMessagesNoop())
            except Exception:
                raise
            return original(self, exc)

    patched_connection_lost._qts_guard_installed = True  # type: ignore[attr-defined]
    setattr(cls, "connection_lost", patched_connection_lost)
    return True


def install_connection_lost_guard(log: logging.Logger) -> None:
    """
    Install connection_lost guard across known websockets class layouts.

    Compatible with multiple `websockets` versions:
    - websockets.asyncio.connection.Connection
    - websockets.client.ClientConnection (older API surface)
    """
    patched_any = False

    try:
        from websockets.asyncio.connection import Connection  # type: ignore

        patched_any = _patch_connection_lost(Connection, log) or patched_any
    except Exception:
        pass

    try:
        import websockets.client as ws_client  # type: ignore

        client_cls = getattr(ws_client, "ClientConnection", None)
        if client_cls is not None:
            patched_any = _patch_connection_lost(client_cls, log) or patched_any
    except Exception:
        pass

    if patched_any:
        log.debug("websockets_compat: connection_lost guard installed")
    else:
        log.debug("websockets_compat: no compatible connection class found")
