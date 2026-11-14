"""Fail-closed configuration of the designated off-host SSH target.

The off-host address is never hard-coded.  Every caller configures it
explicitly -- ``--remote-host`` on the command line first, then the
``QTS_OFFHOST_HOST`` environment variable -- and the operational scripts stop
before any remote side effect when neither is present.  Guessing an address
would send backup traffic to an unintended machine, so resolution fails closed
instead of falling back to a remembered default.

``StrictHostKeyChecking=yes`` is mandatory and is never relaxed.  When the
designated machine is reached under an address that differs from the identity
recorded in ``known_hosts``, ``--host-key-alias``
(``QTS_OFFHOST_HOST_KEY_ALIAS``) selects the existing known-hosts identity via
``HostKeyAlias`` without disabling host-key verification.
"""

from __future__ import annotations

import os

REMOTE_HOST_ENV = "QTS_OFFHOST_HOST"
HOST_KEY_ALIAS_ENV = "QTS_OFFHOST_HOST_KEY_ALIAS"

# Strict, non-interactive SSH options shared by every off-host command.
SSH_OPTIONS = (
    "BatchMode=yes",
    "StrictHostKeyChecking=yes",
    "ConnectTimeout=15",
)


class RemoteTargetNotConfigured(RuntimeError):
    """Raised when no off-host address was configured explicitly."""


def resolve_remote_host(cli_value: str | None = None) -> str:
    """Return the configured off-host address or fail closed.

    Precedence is command line first, then ``QTS_OFFHOST_HOST``.  There is no
    built-in default address.
    """
    host = (cli_value or "").strip() or os.environ.get(REMOTE_HOST_ENV, "").strip()
    if not host:
        raise RemoteTargetNotConfigured(
            "off-host address is not configured: pass --remote-host <address> or "
            f"set {REMOTE_HOST_ENV}; no default address is assumed"
        )
    return host


def resolve_host_key_alias(cli_value: str | None = None) -> str:
    """Return the optional known-hosts alias used for ``HostKeyAlias``."""
    return (cli_value or "").strip() or os.environ.get(HOST_KEY_ALIAS_ENV, "").strip()


def ssh_base_argv(host_key_alias: str = "") -> list[str]:
    """Return the strict ``ssh`` argv prefix, optionally with a host-key alias."""
    argv = ["ssh"]
    for option in SSH_OPTIONS:
        argv += ["-o", option]
    alias = (host_key_alias or "").strip()
    if alias:
        argv += ["-o", f"HostKeyAlias={alias}"]
    return argv
