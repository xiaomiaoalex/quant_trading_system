"""Isolated Linux coverage for the sandbox's Unix resource-limit guards.

``CodeSandbox.execute`` temporarily lowers process-wide ``RLIMIT_AS`` and
``RLIMIT_CPU`` soft limits and installs ``SIGALRM``/``SIGXCPU`` handlers.  A
mistake there does not just fail one test: it can permanently degrade (exit
137) the shared pytest process.  These tests therefore run every scenario in a
child interpreter so the host process is never touched, and they assert the
observable kernel state (``getrlimit``/``getitimer``) rather than internal
counters.

Linux only: the guard reads ``/proc/self/statm`` and the limits are enforced
by ``RLIMIT_AS``.  On other platforms this module is skipped at collection, so
it adds no skip to the Windows subset job (the conftest restricts collection
to the selected group/subset before import).
"""

from __future__ import annotations

import json
import subprocess
import sys
import textwrap
from pathlib import Path

import pytest

pytestmark = pytest.mark.skipif(
    sys.platform != "linux",
    reason="needs /proc/self/statm and RLIMIT_AS in an isolated Linux subprocess",
)

ROOT = Path(__file__).resolve().parents[2]
MIB = 1024 * 1024

_PREAMBLE = """
import json
import os
import resource
import signal

from insight.code_sandbox import CodeSandbox, SandboxConfig

MIB = 1024 * 1024

def emit(**payload):
    print(json.dumps(payload))

def limits():
    return {
        "as": list(resource.getrlimit(resource.RLIMIT_AS)),
        "cpu": list(resource.getrlimit(resource.RLIMIT_CPU)),
    }

def current_address_space():
    with open("/proc/self/statm", "r", encoding="ascii") as handle:
        return int(handle.read().split()[0]) * os.sysconf("SC_PAGE_SIZE")

def make_sandbox(timeout=5.0, memory=256):
    return CodeSandbox(SandboxConfig(timeout_seconds=timeout, max_memory_mb=memory))
"""


def _run_child(body: str, timeout: int = 180) -> dict:
    """Run one scenario in a fresh interpreter rooted at the repository."""
    script = _PREAMBLE + textwrap.dedent(body)
    proc = subprocess.run(
        [sys.executable, "-c", script],
        cwd=str(ROOT),
        capture_output=True,
        text=True,
        timeout=timeout,
    )
    assert proc.returncode == 0, f"child failed rc={proc.returncode}:\n{proc.stderr}"
    return json.loads(proc.stdout.strip().splitlines()[-1])


def test_limits_are_active_during_execution_and_restored_after():
    out = _run_child(
        """
        sandbox = make_sandbox()
        before = limits()
        seen = {}
        context = {
            "record_as": lambda: seen.update(
                as_limit=list(resource.getrlimit(resource.RLIMIT_AS))
            )
        }
        result = sandbox.execute("record_as()", context=context)
        emit(
            status=result.status.name,
            error=result.error,
            before=before,
            after=limits(),
            during=seen.get("as_limit"),
        )
        """
    )
    assert out["status"] == "SUCCESS", out["error"]
    assert out["after"] == out["before"], "process limits were not restored"
    during = out["during"]
    assert during is not None, "the sandbox body did not run"
    assert during[0] != out["before"]["as"][0], "RLIMIT_AS was never lowered"
    assert during[0] > 0, "RLIMIT_AS soft limit is not finite during execution"
    assert during[1] == out["before"]["as"][1], "the hard RLIMIT_AS was modified"


def test_limits_are_restored_when_the_sandboxed_body_raises():
    out = _run_child(
        """
        sandbox = make_sandbox()
        before = limits()
        result = sandbox.execute("raise ValueError('boom')")
        emit(status=result.status.name, before=before, after=limits())
        """
    )
    assert out["status"] == "RUNTIME_ERROR"
    assert out["after"] == out["before"]


def test_timeout_is_reported_and_limits_are_restored():
    out = _run_child(
        """
        sandbox = make_sandbox(timeout=1.0)
        before = limits()
        result = sandbox.execute("while True:\\n    pass")
        emit(status=result.status.name, before=before, after=limits())
        """
    )
    assert out["status"] == "TIMEOUT"
    assert out["after"] == out["before"]


def test_unmeasurable_address_space_fails_closed_without_touching_limits():
    out = _run_child(
        """
        sandbox = make_sandbox()
        before = limits()

        def boom(self):
            raise OSError("statm unavailable")

        CodeSandbox._current_address_space_bytes = boom
        result = sandbox.execute("result = 1")
        emit(status=result.status.name, error=result.error, before=before, after=limits())
        """
    )
    assert out["status"] == "RESOURCE_EXCEEDED"
    assert "PROCESS_ADDRESS_SPACE_UNKNOWN" in out["error"]
    assert out["after"] == out["before"]


def test_stricter_caller_soft_limits_are_never_loosened():
    out = _run_child(
        """
        sandbox = make_sandbox(memory=256)
        base = current_address_space()
        strict_as = base + 128 * MIB
        resource.setrlimit(resource.RLIMIT_AS, (strict_as, resource.RLIM_INFINITY))
        strict = limits()
        seen = {}
        context = {
            "record": lambda: seen.update(
                as_limit=list(resource.getrlimit(resource.RLIMIT_AS)),
                cpu_limit=list(resource.getrlimit(resource.RLIMIT_CPU)),
            )
        }
        result = sandbox.execute("record()", context=context)
        emit(
            status=result.status.name,
            error=result.error,
            strict=strict,
            during=seen,
            after=limits(),
        )
        """
    )
    assert out["status"] == "SUCCESS", out["error"]
    assert out["during"]["as_limit"][0] <= out["strict"]["as"][0]
    assert out["after"] == out["strict"]


def test_a_pre_existing_alarm_is_restored_after_execution():
    out = _run_child(
        """
        sandbox = make_sandbox()
        signal.setitimer(signal.ITIMER_REAL, 30.0)
        result = sandbox.execute("result = 1")
        remaining = signal.getitimer(signal.ITIMER_REAL)
        emit(status=result.status.name, remaining=list(remaining))
        """
    )
    assert out["status"] == "SUCCESS"
    assert out["remaining"][0] > 20.0, "the caller's alarm was cancelled instead of restored"


def test_a_failing_guard_installation_does_not_destroy_the_caller_timer():
    """A failure while installing the handlers must not cancel the caller's timer.

    The caller's timer has to be captured before anything is touched: if the
    guard installation fails halfway, the finally still has to put it back.
    """
    out = _run_child(
        """
        import insight.code_sandbox as code_sandbox

        real_signal = signal

        class FailingSignal:
            SIGALRM = real_signal.SIGALRM
            SIGXCPU = real_signal.SIGXCPU
            SIG_IGN = real_signal.SIG_IGN
            ITIMER_REAL = real_signal.ITIMER_REAL

            def signal(self, signum, handler):
                if signum == real_signal.SIGALRM:
                    raise ValueError("handler installation failed")
                return real_signal.signal(signum, handler)

            def setitimer(self, *args):
                return real_signal.setitimer(*args)

            def getitimer(self, *args):
                return real_signal.getitimer(*args)

        code_sandbox.signal = FailingSignal()
        code_sandbox.UNIX_AVAILABLE = True

        sandbox = make_sandbox()
        real_signal.setitimer(real_signal.ITIMER_REAL, 30.0)
        before = real_signal.getitimer(real_signal.ITIMER_REAL)[0]
        result = sandbox.execute("result = 1")
        after = real_signal.getitimer(real_signal.ITIMER_REAL)[0]
        emit(
            status=result.status.name,
            error=result.error,
            before=before,
            after=after,
        )
        """
    )
    assert out["status"] == "RESOURCE_EXCEEDED", out
    assert "SANDBOX_TIMEOUT_GUARD_UNAVAILABLE" in out["error"]
    assert out["after"] > 20.0, "the caller's timer was destroyed by a failed guard setup"
