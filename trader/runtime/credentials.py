"""Process credential boundaries for the split trading runtime.

Control never receives exchange write credentials. Execution requires them and
refuses to start with a stable reason code when they are absent. Secrets may be
supplied directly or through a Docker secret file; neither path ever logs the
value.

The checks here are deliberately pure ``environ`` lookups so tests can prove
the behaviour without starting a container.
"""

from __future__ import annotations

import os
from dataclasses import dataclass
from typing import Mapping

CONTROL_FORBIDDEN_KEYS = (
    "BINANCE_API_KEY",
    "BINANCE_SECRET_KEY",
    "BINANCE_API_KEY_FILE",
    "BINANCE_SECRET_KEY_FILE",
)
EXECUTION_CREDENTIAL_KEYS = ("BINANCE_API_KEY", "BINANCE_SECRET_KEY")
SANDBOX_ENVIRONMENTS = frozenset({"demo", "testnet", "test"})

BINANCE_CREDENTIALS_MISSING = "BINANCE_CREDENTIALS_MISSING"
BINANCE_CREDENTIALS_UNREADABLE = "BINANCE_CREDENTIALS_UNREADABLE"
BINANCE_ENV_NOT_SANDBOX = "BINANCE_ENV_NOT_SANDBOX"
CONTROL_EXCHANGE_CREDENTIALS_PRESENT = "CONTROL_EXCHANGE_CREDENTIALS_PRESENT"


class CredentialBoundaryError(RuntimeError):
    """A process is assembled with credentials it must not hold."""

    def __init__(self, reason_code: str, detail: str = ""):
        self.reason_code = reason_code
        self.detail = detail
        super().__init__(reason_code if not detail else f"{reason_code}:{detail}")


def _non_empty(value: object) -> bool:
    return isinstance(value, str) and bool(value.strip())


def _present(value: object) -> bool:
    """Any non-empty string is present, including whitespace-only values.

    Control must fail closed on an accidentally configured key even when the
    value looks blank; only a truly absent/empty value is safe.
    """
    return isinstance(value, str) and len(value) > 0


def _read_secret(environ: Mapping[str, str], key: str) -> str:
    """Read a secret from the environment or its ``*_FILE`` variant.

    File contents are stripped of one trailing newline. Errors are converted to
    a stable reason code and never include the path's contents.
    """
    direct = environ.get(key, "")
    if _non_empty(direct):
        return direct.strip()
    path = environ.get(f"{key}_FILE", "")
    if not _non_empty(path):
        raise CredentialBoundaryError(BINANCE_CREDENTIALS_MISSING, key)
    try:
        with open(path, "r", encoding="utf-8") as handle:
            value = handle.read().strip()
    except OSError as exc:
        raise CredentialBoundaryError(BINANCE_CREDENTIALS_UNREADABLE, key) from exc
    if not value:
        raise CredentialBoundaryError(BINANCE_CREDENTIALS_MISSING, key)
    return value


def control_credential_violations(environ: Mapping[str, str] | None = None) -> list[str]:
    """Return the names (never values) of exchange credentials in a Control env."""
    source = os.environ if environ is None else environ
    return [key for key in CONTROL_FORBIDDEN_KEYS if _present(source.get(key))]


def assert_control_credentials_absent(environ: Mapping[str, str] | None = None) -> None:
    violations = control_credential_violations(environ)
    if violations:
        raise CredentialBoundaryError(
            CONTROL_EXCHANGE_CREDENTIALS_PRESENT, ",".join(sorted(violations))
        )


@dataclass(frozen=True)
class ExecutionCredentials:
    api_key: str
    secret_key: str
    environment: str


def execution_credential_reason(environ: Mapping[str, str] | None = None) -> str | None:
    """Return a stable refusal code, or ``None`` when execution may start.

    The venue environment check is intentionally part of the credential gate:
    production/mainnet is not an authorised phase-5 runtime.
    """
    source = os.environ if environ is None else environ
    environment = str(source.get("BINANCE_ENV", "demo") or "demo").strip().lower()
    if environment not in SANDBOX_ENVIRONMENTS:
        return BINANCE_ENV_NOT_SANDBOX
    try:
        _read_secret(source, "BINANCE_API_KEY")
        _read_secret(source, "BINANCE_SECRET_KEY")
    except CredentialBoundaryError as exc:
        return exc.reason_code
    return None


def require_execution_credentials(
    environ: Mapping[str, str] | None = None,
) -> ExecutionCredentials:
    source = os.environ if environ is None else environ
    reason = execution_credential_reason(source)
    if reason is not None:
        raise CredentialBoundaryError(reason)
    environment = str(source.get("BINANCE_ENV", "demo") or "demo").strip().lower()
    return ExecutionCredentials(
        api_key=_read_secret(source, "BINANCE_API_KEY"),
        secret_key=_read_secret(source, "BINANCE_SECRET_KEY"),
        environment=environment,
    )
