"""Atomic, non-blocking startup guard for the phase-6 sandbox supervisor.

The phase-6 window uses one Demo account and one host. Two supervisors that
start concurrently would create two Compose stacks against that account. A
process scan followed by a launch has a race; a PID file is evidence, not
exclusive ownership.

This module provides an OS advisory exclusive lock on a fixed host-level file.
The lock identity does not include the run id, so every entry path (manual
launch, watcher launch, direct supervisor execution) contends on the same
resource. The supervisor acquires it before any Demo/account/Compose/DR side
effect and holds it until its cleanup is complete. A losing contender exits
before side effects.

The lock is released automatically if the owner process dies. Automatic
release must not permit a second stack alongside an orphan, so the new owner
runs an orphan probe after taking the lock and fails closed if a surviving
task observer/DR tunnel process or a running task container is found. Stale
metadata is never authority.

A live supervisor process is not by itself an orphan: the OS lock is the
authority for supervisor ownership. A process that is still starting and has
not taken the lock is a contender, and a process that has already released
the lock is finishing; both are side-effect-free with respect to a new owner.
Detecting them as orphans would make the exactly-one-owner property timing
dependent. Real survivors of a crash are detected by their side effects:
running task containers, the window observer, and the strict-key DR tunnel.
"""

from __future__ import annotations

import datetime
import json
import os
import socket
from pathlib import Path
from typing import Callable, Iterable


class StartupGuardError(RuntimeError):
    """Base class for guard acquisition failures."""

    exit_code = 3


class GuardHeldError(StartupGuardError):
    """Another live owner holds the guard."""

    exit_code = 3


class OrphanDetectedError(StartupGuardError):
    """The lock was free but surviving task processes/containers were found."""

    exit_code = 4


class OrphanCheckUnavailable(StartupGuardError):
    """The orphan probe could not establish the absence of survivors."""

    exit_code = 5


def default_guard_path() -> Path:
    """Return the host-level guard path, stable across run ids and entries."""

    base = os.environ.get("LOCALAPPDATA")
    if not base:
        base = os.environ.get("XDG_RUNTIME_DIR") or str(Path.home() / ".local" / "state")
    return Path(base) / "qts" / "phase6-sandbox-start.lock"


def _utc_now() -> str:
    return datetime.datetime.now(datetime.timezone.utc).isoformat(timespec="seconds")


# The Windows byte-range lock must not cover the metadata region: a lock on
# bytes 0..N blocks other handles from reading them, and truncating the file
# can invalidate a lock that sits past EOF. Lock one byte at 1 MiB instead and
# keep metadata fixed-size at offset 0. POSIX flock is advisory and does not
# block reads.
_LOCK_OFFSET = 1 << 20
_METADATA_BYTES = 4096


def _try_lock(fd: int) -> None:
    if os.name == "nt":
        import msvcrt

        os.lseek(fd, _LOCK_OFFSET, os.SEEK_SET)
        msvcrt.locking(fd, msvcrt.LK_NBLCK, 1)
    else:
        import fcntl

        fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)


def _unlock(fd: int) -> None:
    if os.name == "nt":
        import msvcrt

        os.lseek(fd, _LOCK_OFFSET, os.SEEK_SET)
        msvcrt.locking(fd, msvcrt.LK_UNLCK, 1)
    else:
        import fcntl

        fcntl.flock(fd, fcntl.LOCK_UN)


def _read_metadata(fd: int) -> dict:
    try:
        os.lseek(fd, 0, os.SEEK_SET)
        raw = os.read(fd, _METADATA_BYTES).decode("utf-8", "replace")
        if not raw.strip():
            return {}
        value = json.loads(raw)
        return value if isinstance(value, dict) else {}
    except Exception:
        return {}


def _write_metadata(fd: int, metadata: dict) -> None:
    payload = json.dumps(metadata, sort_keys=True).encode("utf-8")
    if len(payload) > _METADATA_BYTES - 1:
        raise RuntimeError("STARTUP_GUARD_METADATA_TOO_LARGE")
    os.lseek(fd, 0, os.SEEK_SET)
    os.write(fd, payload + b" " * (_METADATA_BYTES - len(payload)))
    os.fsync(fd)


def _list_processes() -> list[tuple[int, str, str]]:
    import psutil

    rows: list[tuple[int, str, str]] = []
    for proc in psutil.process_iter(["pid", "name", "cmdline"]):
        try:
            cmd = " ".join(proc.info["cmdline"] or [])
        except (psutil.NoSuchProcess, psutil.AccessDenied):
            continue
        rows.append((int(proc.info["pid"]), str(proc.info["name"] or "").lower(), cmd))
    return rows


def _is_python(name: str) -> bool:
    return name in {"python", "python.exe", "pythonw", "pythonw.exe", "py", "py.exe"}


def _list_running_containers() -> list[str]:
    import subprocess

    try:
        result = subprocess.run(
            ["docker", "ps", "--format", "{{.Names}}"],
            capture_output=True,
            text=True,
            timeout=60,
        )
    except Exception as exc:  # noqa: BLE001 - fail closed on any probe failure
        raise OrphanCheckUnavailable(
            f"docker_ps_failed:{type(exc).__name__}: {exc}"[-500:]
        ) from exc
    if result.returncode != 0:
        detail = ((result.stderr or "") + (result.stdout or "")).strip()[-400:]
        raise OrphanCheckUnavailable(f"docker_ps_exit_{result.returncode}:{detail}")
    return [line.strip() for line in (result.stdout or "").splitlines() if line.strip()]


def _is_dr_tunnel(name: str, cmd: str) -> bool:
    """Match this task's strict-key DR tunnel, not unrelated infrastructure.

    The supervisor's SSH prefix is BatchMode + StrictHostKeyChecking=yes +
    ConnectTimeout=15 (see scripts/sandbox_supervisor.py ``SSH``). Other
    long-lived tunnels on the host (for example the WorkBuddy remote helper
    forwarding 127.0.0.1:9119 with ConnectTimeout=10) must not block a window.
    """

    if "ssh" not in name:
        return False
    return (
        "-L" in cmd
        and "127.0.0.1:" in cmd
        and "StrictHostKeyChecking=yes" in cmd
        and "ConnectTimeout=15" in cmd
    )


def probe_orphans(
    process_lister: Callable[[], Iterable[tuple[int, str, str]]] | None = None,
    container_lister: Callable[[], Iterable[str]] | None = None,
    self_pid: int | None = None,
) -> list[str]:
    """Return surviving side effects that block a new owner.

    Supervisor ownership is decided by the OS lock, not by a process scan: a
    live non-owner supervisor is either a contender (exits ``GUARD_HELD``) or a
    finishing owner, and both are harmless to a new owner. Survivors of a
    crash are identified by their side effects: a window observer process, the
    strict-key DR tunnel, or a running task container. The probe is read-only.
    Any failure to inspect Docker is surfaced as ``OrphanCheckUnavailable`` so
    the caller fails closed instead of starting a second stack next to an
    unverified survivor.
    """

    own_pid = os.getpid() if self_pid is None else self_pid
    findings: list[str] = []
    processes = (process_lister or _list_processes)()
    for pid, name, cmd in processes:
        if int(pid) == int(own_pid):
            continue
        if _is_python(name) and "observe_trading_runtime.py" in cmd:
            findings.append(f"observer_process:{pid}")
        elif _is_dr_tunnel(name, cmd):
            findings.append(f"dr_tunnel_process:{pid}")
    containers = (container_lister or _list_running_containers)()
    for name in containers:
        if "qts_p6_sandbox" in str(name):
            findings.append(f"container:{name}")
    return findings


class StartupGuard:
    """One host/Demo-account owner at a time."""

    def __init__(
        self,
        path: Path | None,
        run_id: str,
        entry: str,
        orphan_probe: Callable[[], list[str]] | None = None,
    ) -> None:
        self.path = Path(path) if path is not None else default_guard_path()
        self.run_id = run_id
        self.entry = entry
        self._orphan_probe = orphan_probe or probe_orphans
        self._fd: int | None = None
        self.metadata: dict = {}
        self.acquired_at_utc: str | None = None

    # ------------------------------------------------------------- lifecycle
    def acquire(self) -> None:
        """Take the exclusive lock or raise before any side effect."""

        self.path.parent.mkdir(parents=True, exist_ok=True)
        fd = os.open(self.path, os.O_RDWR | os.O_CREAT, 0o600)
        try:
            _try_lock(fd)
        except OSError as exc:
            owner = _read_metadata(fd)
            try:
                os.close(fd)
            finally:
                raise GuardHeldError(
                    json.dumps(
                        {
                            "reason": "STARTUP_GUARD_HELD",
                            "path": str(self.path),
                            "errno": exc.errno,
                            "owner": owner,
                        },
                        sort_keys=True,
                    )
                ) from None

        self._fd = fd
        # Publish the owner before the probe so a rejected contender sees the
        # current owner instead of the previous run's stale metadata.
        self.acquired_at_utc = _utc_now()
        self.metadata = {
            "pid": os.getpid(),
            "run_id": self.run_id,
            "entry": self.entry,
            "started_at_utc": self.acquired_at_utc,
            "host": socket.gethostname(),
            "cwd": os.getcwd(),
        }
        _write_metadata(fd, self.metadata)
        try:
            findings = self._orphan_probe()
        except OrphanCheckUnavailable:
            self._release_locked(fd)
            raise
        if findings:
            self._release_locked(fd)
            raise OrphanDetectedError(
                json.dumps(
                    {"reason": "STARTUP_ORPHAN_DETECTED", "findings": findings},
                    sort_keys=True,
                )
            )

    def release(self) -> None:
        """Release the OS lock. Metadata is retained as non-authoritative history."""

        fd = self._fd
        if fd is None:
            return
        self._release_locked(fd)

    def _release_locked(self, fd: int) -> None:
        try:
            _unlock(fd)
        finally:
            try:
                os.close(fd)
            finally:
                self._fd = None

    @property
    def held(self) -> bool:
        return self._fd is not None

    def summary(self) -> dict:
        """Metadata suitable for window evidence; never contains credentials."""

        return {
            "path": str(self.path),
            "run_id": self.run_id,
            "entry": self.entry,
            "pid": self.metadata.get("pid"),
            "acquired_at_utc": self.acquired_at_utc,
            "host": self.metadata.get("host"),
        }


def acquire_default(
    run_id: str, entry: str, orphan_probe: Callable[[], list[str]] | None = None
) -> StartupGuard:
    """Convenience helper for the supervisor and its diagnostics."""

    guard = StartupGuard(None, run_id=run_id, entry=entry, orphan_probe=orphan_probe)
    guard.acquire()
    return guard
