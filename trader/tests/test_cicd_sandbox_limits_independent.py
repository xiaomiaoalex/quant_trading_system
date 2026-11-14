"""Independent T1 contract probes; execute in Linux, never change pytest limits.

Linux is an explicit prerequisite, not a skip condition. Windows required jobs
select their own subset before import. Expectations come from kernel state and
the work order, not the sandbox's limit calculation.
"""

from __future__ import annotations

import json
import os
import subprocess
import sys
import textwrap
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
PRELUDE = """
import builtins
import io
import math
import mmap
import os
import resource
import signal
import threading
import time
from unittest.mock import patch
from insight.code_sandbox import CodeSandbox, SandboxConfig, SandboxStatus

MIB = 1024 * 1024

def limits():
    return tuple(resource.getrlimit(k) for k in (resource.RLIMIT_AS, resource.RLIMIT_CPU))

def vm():
    with open('/proc/self/statm', encoding='ascii') as handle:
        return int(handle.read().split()[0]) * os.sysconf('SC_PAGE_SIZE')

def sandbox(timeout=2.0, memory=256):
    return CodeSandbox(SandboxConfig(timeout_seconds=timeout, max_memory_mb=memory))

def rejected(result, seen, before):
    assert result.status == SandboxStatus.RESOURCE_EXCEEDED, (result.status, result.error)
    assert result.error
    assert not seen, seen
    assert limits() == before, (limits(), before)
"""


def run_probe(body: str) -> None:
    assert sys.platform == "linux", "T1 requires an isolated Linux execution environment"
    result = subprocess.run(
        [sys.executable, "-B", "-c", PRELUDE + textwrap.dedent(body)],
        cwd=ROOT,
        capture_output=True,
        text=True,
        timeout=15,
    )
    assert (
        result.returncode == 0
    ), f"Linux probe exit={result.returncode}\nstdout={result.stdout}\nstderr={result.stderr}"


def test_sparse_mapping_uses_virtual_size_and_restores_kernel_limits():
    run_probe(
        """
        reservation = mmap.mmap(-1, 256 * MIB)
        before = limits()
        box = sandbox()
        with open('/proc/self/statm', encoding='ascii') as handle:
            fields = [int(x) for x in handle.read().split()]
        assert fields[0] > fields[1] + 128 * MIB // os.sysconf('SC_PAGE_SIZE')
        assert box._current_address_space_bytes() == vm()
        seen = []
        def observe():
            seen.append((limits(), vm()))
        result = box.execute('observe()', context={'observe': observe})
        assert result.status == SandboxStatus.SUCCESS, result.error
        assert len(seen) == 1
        during, size = seen[0]
        assert during[0][0] >= size, (during, size)
        for actual, original in zip(during, before):
            assert actual[1] == original[1]
            assert actual[0] != resource.RLIM_INFINITY
        assert limits() == before
        reservation.close()
        """
    )


@pytest.mark.parametrize("failure", ["unreadable", "empty"])
def test_statm_failure_refuses_execution(failure):
    run_probe(
        f"failure = {failure!r}\n"
        + textwrap.dedent(
            """
            before = limits()
            seen = []
            real_open = builtins.open
            def fault(path, *args, **kwargs):
                if path == '/proc/self/statm':
                    if failure == 'unreadable':
                        raise OSError('independent statm fault')
                    return io.StringIO('')
                return real_open(path, *args, **kwargs)
            with patch('builtins.open', fault):
                result = sandbox().execute('observe()', context={'observe': lambda: seen.append(1)})
            rejected(result, seen, before)
            """
        )
    )


def test_existing_tighter_soft_limits_are_never_raised():
    run_probe(
        """
        original = limits()
        usage = resource.getrusage(resource.RUSAGE_SELF)
        resource.setrlimit(resource.RLIMIT_AS, (vm() + 64 * MIB, original[0][1]))
        cpu = math.ceil(usage.ru_utime + usage.ru_stime) + 20
        resource.setrlimit(resource.RLIMIT_CPU, (cpu, original[1][1]))
        before = limits()
        seen = []
        result = sandbox(timeout=60, memory=512).execute(
            'observe()', context={'observe': lambda: seen.append(limits())})
        assert result.status == SandboxStatus.SUCCESS, result.error
        assert len(seen) == 1
        for actual, prior in zip(seen[0], before):
            assert actual[0] <= prior[0]
            assert actual[1] == prior[1]
        assert limits() == before
        """
    )


def test_existing_soft_below_current_vm_must_refuse_execution():
    run_probe(
        """
        reservation = mmap.mmap(-1, 256 * MIB)
        box = sandbox()
        seen = []
        context = {'observe': lambda: seen.append(limits())}
        original = limits()
        # Linux permits a soft limit below existing mappings. Both T1.1 and
        # T1.4 can then hold only by refusing the execution.
        resource.setrlimit(resource.RLIMIT_AS, (vm() - 32 * MIB, original[0][1]))
        before = limits()
        result = box.execute('observe()', context=context)
        after = limits()
        resource.setrlimit(resource.RLIMIT_AS, original[0])
        assert after == before
        assert result.status == SandboxStatus.RESOURCE_EXCEEDED, (result.status, seen)
        assert not seen, 'body executed with AS soft below existing VmSize'
        reservation.close()
        """
    )


@pytest.mark.parametrize("failure", ["xcpu_handler", "alarm_handler", "timer"])
def test_partial_initialization_restores_existing_periodic_timer(failure):
    run_probe(
        f"failure = {failure!r}\n"
        + textwrap.dedent(
            """
            before = limits()
            seen = []
            def caller_handler(signum, frame):
                raise AssertionError('unexpected caller timer expiry')
            signal.signal(signal.SIGALRM, caller_handler)
            signal.signal(signal.SIGXCPU, caller_handler)
            signal.setitimer(signal.ITIMER_REAL, 30, 7)
            real_signal = signal.signal
            real_timer = signal.setitimer
            injected = []
            def fault_signal(kind, handler):
                target = signal.SIGXCPU if failure == 'xcpu_handler' else signal.SIGALRM
                if failure != 'timer' and kind == target and not injected:
                    injected.append(True)
                    raise OSError('independent handler installation fault')
                return real_signal(kind, handler)
            def fault_timer(kind, seconds, interval=0):
                if failure == 'timer' and seconds > 0 and not injected:
                    injected.append(True)
                    raise OSError('independent timer installation fault')
                return real_timer(kind, seconds, interval)
            with patch.object(signal, 'signal', fault_signal), patch.object(
                signal, 'setitimer', fault_timer
            ):
                result = sandbox().execute('observe()', context={'observe': lambda: seen.append(1)})
            after_timer = signal.getitimer(signal.ITIMER_REAL)
            after_handlers = (signal.getsignal(signal.SIGALRM), signal.getsignal(signal.SIGXCPU))
            signal.setitimer(signal.ITIMER_REAL, 0)
            assert injected
            rejected(result, seen, before)
            assert after_handlers == (caller_handler, caller_handler), after_handlers
            assert 0 < after_timer[0] <= 30, ('caller timer lost', after_timer)
            assert after_timer[1] == 7, after_timer
            """
        )
    )


def test_success_preserves_timer_and_fractional_timeout_is_enforced():
    run_probe(
        """
        before = limits()
        signal.signal(signal.SIGALRM, lambda signum, frame: None)
        original_handler = signal.getsignal(signal.SIGALRM)
        signal.setitimer(signal.ITIMER_REAL, 30, 7)
        result = sandbox().execute('result = 42')
        timer = signal.getitimer(signal.ITIMER_REAL)
        signal.setitimer(signal.ITIMER_REAL, 0)
        assert result.status == SandboxStatus.SUCCESS
        assert result.output == 42
        assert 0 < timer[0] <= 30 and timer[1] == 7, timer
        assert signal.getsignal(signal.SIGALRM) == original_handler
        assert limits() == before
        started = time.monotonic()
        result = sandbox(timeout=0.15).execute('while True:\\n    pass')
        elapsed = time.monotonic() - started
        assert result.status == SandboxStatus.TIMEOUT, (result.status, result.error)
        assert 0.05 <= elapsed < 1.5, elapsed
        assert limits() == before
        assert signal.getitimer(signal.ITIMER_REAL) == (0.0, 0.0)
        """
    )


def test_non_main_thread_refuses_without_kernel_mutation():
    run_probe(
        """
        before = limits()
        seen = []
        results = []
        def worker():
            results.append(sandbox().execute('observe()', context={'observe': lambda: seen.append(1)}))
        thread = threading.Thread(target=worker)
        thread.start()
        thread.join(timeout=5)
        assert not thread.is_alive()
        assert len(results) == 1
        rejected(results[0], seen, before)
        assert signal.getitimer(signal.ITIMER_REAL) == (0.0, 0.0)
        """
    )


def test_timer_snapshot_failure_preserves_caller_state():
    run_probe(
        """
        before = limits()
        seen = []
        signal.signal(signal.SIGALRM, lambda signum, frame: None)
        old_handlers = (signal.getsignal(signal.SIGALRM), signal.getsignal(signal.SIGXCPU))
        signal.setitimer(signal.ITIMER_REAL, 30, 7)
        with patch.object(signal, 'getitimer', side_effect=OSError('timer snapshot fault')):
            result = sandbox().execute('observe()', context={'observe': lambda: seen.append(1)})
        after_timer = signal.getitimer(signal.ITIMER_REAL)
        after_handlers = (signal.getsignal(signal.SIGALRM), signal.getsignal(signal.SIGXCPU))
        signal.setitimer(signal.ITIMER_REAL, 0)
        assert limits() == before
        assert not seen
        assert after_handlers == old_handlers
        assert 0 < after_timer[0] <= 30 and after_timer[1] == 7, after_timer
        rejected(result, seen, before)
        """
    )


def collection_env(**overrides):
    env = dict(os.environ)
    for key in ("QTS_TEST_GROUP", "QTS_TEST_SUBSET", "QTS_TEST_EXCLUDE_GROUPS"):
        env.pop(key, None)
    env.update(PYTHONDONTWRITEBYTECODE="1", QTS_TEST_NETWORK_POLICY="offline")
    env.update(overrides)
    return env


@pytest.mark.parametrize("group", ["p0", "offline", "loopback", "postgres", "ops"])
def test_required_group_or_subset_cannot_be_excluded(group):
    result = subprocess.run(
        [sys.executable, "-B", "-m", "pytest", "trader/tests", "--collect-only", "-q"],
        cwd=ROOT,
        env=collection_env(QTS_TEST_EXCLUDE_GROUPS=group),
        capture_output=True,
        text=True,
        timeout=90,
    )
    assert result.returncode == 4, result.stdout + result.stderr
    assert "required CI group(s) cannot be excluded" in result.stderr
    assert group in result.stderr


@pytest.mark.parametrize(
    ("overrides", "message"),
    [
        ({"QTS_TEST_GROUP": "not_a_group"}, "unknown QTS_TEST_GROUP"),
        ({"QTS_TEST_EXCLUDE_GROUPS": "not_a_group"}, "unknown QTS_TEST_EXCLUDE_GROUPS"),
        (
            {"QTS_TEST_GROUP": "external", "QTS_TEST_EXCLUDE_GROUPS": "external"},
            "both select: external",
        ),
    ],
)
def test_invalid_group_requests_are_rejected(overrides, message):
    result = subprocess.run(
        [sys.executable, "-B", "-m", "pytest", "trader/tests", "--collect-only", "-q"],
        cwd=ROOT,
        env=collection_env(**overrides),
        capture_output=True,
        text=True,
        timeout=90,
    )
    assert result.returncode == 4, result.stdout + result.stderr
    assert message in result.stderr


def test_excluded_modules_are_not_imported_at_collection():
    script = """
import importlib.abc
import sys
import pytest

forbidden = {'test_backtesting_vectorbt_adapter', 'test_vectorbt_risk_adapter'}
class ImportTripwire(importlib.abc.MetaPathFinder):
    def find_spec(self, fullname, path=None, target=None):
        if fullname.rsplit('.', 1)[-1] in forbidden:
            raise AssertionError('EXCLUDED_MODULE_IMPORTED:' + fullname)
        return None
sys.meta_path.insert(0, ImportTripwire())
code = pytest.main(['trader/tests', '--collect-only', '-q', '-p', 'no:cacheprovider'])
assert code == 0, code
assert not any(name.rsplit('.', 1)[-1] in forbidden for name in sys.modules)
"""
    result = subprocess.run(
        [sys.executable, "-B", "-c", script],
        cwd=ROOT,
        env=collection_env(QTS_TEST_GROUP="p0", QTS_TEST_EXCLUDE_GROUPS="external"),
        capture_output=True,
        text=True,
        timeout=90,
    )
    assert result.returncode == 0, result.stdout + result.stderr
    assert "EXCLUDED_MODULE_IMPORTED" not in result.stdout + result.stderr


SYNTHETIC_HASH = "scrypt$" + "a1" * 16 + "$" + "b2" * 64


def preview_values():
    return {
        "QTS_PREVIEW_ID": "independent-r1",
        "QTS_PREVIEW_COMMIT": "ab" * 20,
        "QTS_PREVIEW_RUNTIME_IMAGE": "qts-independent:runtime",
        "QTS_PREVIEW_RUNTIME_IMAGE_ID": "sha256:" + "cd" * 32,
        "QTS_PREVIEW_UI_IMAGE": "qts-independent:ui",
        "QTS_PREVIEW_POSTGRES_PASSWORD": "Synthetic-Only-7xQn9!",
        "QTS_PREVIEW_PASSWORD_HASH": "'" + SYNTHETIC_HASH + "'",
        "QTS_PREVIEW_ORIGIN": "http://127.0.0.1:18500",
    }


def validate_preview(tmp_path, values, expected):
    path = tmp_path / "synthetic.env"
    path.write_text("\n".join(f"{key}={value}" for key, value in values.items()) + "\n")
    result = subprocess.run(
        [sys.executable, "-B", "scripts/validate_preview_env.py", "--env-file", str(path)],
        cwd=ROOT,
        capture_output=True,
        text=True,
        timeout=30,
    )
    payload = json.loads(result.stdout)
    assert result.returncode == (0 if expected else 1), result.stdout + result.stderr
    assert payload["ok"] is expected
    assert bool(payload["problems"]) is (not expected)
    return payload


@pytest.mark.parametrize("form", ["unquoted", "single_quoted", "escaped"])
def test_preview_hash_forms_follow_compose_contract(tmp_path, form):
    values = preview_values()
    values["QTS_PREVIEW_PASSWORD_HASH"] = {
        "unquoted": SYNTHETIC_HASH,
        "single_quoted": "'" + SYNTHETIC_HASH + "'",
        "escaped": SYNTHETIC_HASH.replace("$", "$$"),
    }[form]
    validate_preview(tmp_path, values, form != "unquoted")


@pytest.mark.parametrize(
    ("key", "value"),
    [
        ("QTS_PREVIEW_ID", "CHANGE_ME"),
        ("QTS_PREVIEW_POSTGRES_PASSWORD", "password"),
        ("QTS_PREVIEW_POSTGRES_PASSWORD", "trader_pwd"),
        ("QTS_PREVIEW_ORIGIN", "http://192.0.2.10:8500"),
        ("QTS_PREVIEW_ORIGIN", "http://127.0.0.1.evil.invalid"),
        ("QTS_PREVIEW_COMMIT", "a" * 39),
        ("QTS_PREVIEW_COMMIT", "g" * 40),
        ("QTS_PREVIEW_RUNTIME_IMAGE_ID", "sha256:" + "a" * 63),
        ("QTS_PREVIEW_RUNTIME_IMAGE_ID", "sha256:" + "z" * 64),
    ],
)
def test_preview_rejects_invalid_metadata_and_defaults(tmp_path, key, value):
    values = preview_values()
    values[key] = value
    report = validate_preview(tmp_path, values, False)
    assert any(problem.startswith(key + ":") for problem in report["problems"])
