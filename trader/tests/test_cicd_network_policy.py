"""The offline test policy must actually block public sockets (C1).

The guard is installed by ``trader/tests/conftest.py`` for P0/offline CI jobs.
This test installs the same guard explicitly so it also runs in local
development, where the default policy is ``loopback``.
"""

from __future__ import annotations

import socket

import pytest

from trader.tests.conftest import _install_network_guard


def _restore(originals):
    socket.socket.connect = originals["connect"]
    socket.socket.connect_ex = originals["connect_ex"]
    socket.create_connection = originals["create_connection"]
    socket.getaddrinfo = originals["getaddrinfo"]
    socket.socketpair = originals["socketpair"]


def test_offline_policy_blocks_public_connections():
    originals = _install_network_guard("offline")
    try:
        with pytest.raises(PermissionError, match="QTS_NETWORK_BLOCKED"):
            socket.create_connection(("example.com", 443), timeout=0.1)
        with pytest.raises(PermissionError, match="QTS_NETWORK_BLOCKED"):
            socket.getaddrinfo("example.com", 443)
    finally:
        _restore(originals)


def test_loopback_policy_still_blocks_public_but_allows_loopback():
    originals = _install_network_guard("loopback")
    listener = socket.socket()
    listener.bind(("127.0.0.1", 0))
    listener.listen(1)
    try:
        port = listener.getsockname()[1]
        connection = socket.create_connection(("127.0.0.1", port), timeout=1)
        connection.close()
        with pytest.raises(PermissionError, match="QTS_NETWORK_BLOCKED"):
            socket.create_connection(("example.com", 443), timeout=0.1)
    finally:
        listener.close()
        _restore(originals)
