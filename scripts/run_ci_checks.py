"""Cross-platform entry point for the repository's existing CI checks.

This script orchestrates the same checkers used by ``.github/workflows/ci-gate.yml``;
it deliberately does not reimplement test selection, JUnit, or coverage rules.
Every run writes a JSON record with platform, source revision, scope, argv,
exit codes, timing and expected-evidence status.  A missing report is a
failure: the entry point never reports success on absent evidence.

Supported scopes map to existing manifest groups and job parameters:

* ``manifest`` - test manifest audit only;
* ``p0``, ``offline``, ``loopback``, ``postgres`` - one test group plus its
  existing JUnit gate (offline additionally enforces the coverage floors);
* ``windows-guard`` - Windows-only phase 6 guard subset;
* ``linux-docker`` - Linux-only Compose configuration gate.
"""

from __future__ import annotations

import argparse
import json
import os
import platform
import subprocess
import sys
import time
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
DEFAULT_REPORTS = ROOT / "reports"

SCOPES: dict[str, dict[str, Any]] = {
    "manifest": {"platforms": ("linux", "windows"), "checks": ("manifest",)},
    "research-ai": {
        "platforms": ("linux", "windows"),
        "subset": "research_ai",
        "network": "offline",
        "junit": "reports/junit-research-ai.xml",
        "checks": ("pytest", "junit"),
    },
    "p0": {
        "platforms": ("linux", "windows"),
        "group": "p0",
        "network": "offline",
        "junit": "reports/junit-p0.xml",
        "checks": ("pytest", "junit"),
    },
    "offline": {
        "platforms": ("linux", "windows"),
        "group": "p0,offline",
        "exclude": "external",
        "network": "offline",
        "junit": "reports/junit-offline.xml",
        "coverage": "reports/coverage.xml",
        "checks": ("pytest", "junit", "coverage"),
    },
    "loopback": {
        "platforms": ("linux", "windows"),
        "group": "loopback",
        "network": "loopback",
        "junit": "reports/junit-loopback.xml",
        "checks": ("pytest", "junit"),
    },
    "postgres": {
        "platforms": ("linux", "windows"),
        "group": "postgres",
        "network": "loopback",
        "junit": "reports/junit-postgres.xml",
        "checks": ("pytest", "junit"),
    },
    "windows-guard": {
        "platforms": ("windows",),
        "group": "ops",
        "subset": "phase6_guard",
        "network": "open",
        "junit": "reports/junit-phase6-guard.xml",
        "checks": ("pytest", "junit"),
    },
    "linux-docker": {
        "platforms": ("linux",),
        "checks": ("compose-config",),
    },
    "preview-stack": {
        "platforms": ("linux",),
        "checks": ("preview-stack",),
    },
    "pr-browser-smoke": {
        "platforms": ("linux",),
        "checks": ("browser-smoke",),
    },
}


def _platform() -> str:
    return "windows" if sys.platform.startswith("win") else "linux"


def _git(*args: str) -> str:
    try:
        result = subprocess.run(
            ["git", *args],
            cwd=ROOT,
            capture_output=True,
            text=True,
            encoding="utf-8",
            check=False,
        )
    except OSError as exc:
        return f"UNAVAILABLE: {exc}"
    return (result.stdout if result.returncode == 0 else result.stderr).strip()


def _run(
    argv: list[str],
    *,
    env: dict[str, str],
    cwd: Path,
    timeout: float,
    record: dict[str, Any],
) -> None:
    started = time.monotonic()
    entry: dict[str, Any] = {"argv": argv, "cwd": str(cwd.relative_to(ROOT))}
    record["checks"].append(entry)
    try:
        completed = subprocess.run(
            argv,
            cwd=cwd,
            env=env,
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            timeout=timeout,
            check=False,
        )
        entry.update(
            {
                "exit_code": completed.returncode,
                "stdout": completed.stdout,
                "stderr": completed.stderr,
            }
        )
    except subprocess.TimeoutExpired as exc:
        entry.update(
            {
                "exit_code": 124,
                "timed_out": True,
                "stdout": exc.stdout or "",
                "stderr": (exc.stderr or "") + f"\nCI_CHECK_TIMEOUT: {timeout}s",
            }
        )
    except OSError as exc:
        entry.update({"exit_code": 127, "stderr": f"CI_CHECK_EXEC_FAILED: {exc}"})
    finally:
        entry["duration_seconds"] = round(time.monotonic() - started, 3)


def _evidence(record: dict[str, Any], location: str, *, required: bool) -> None:
    path = _resolve_workspace(Path(location))
    present = path.is_file() and path.stat().st_size > 0
    record["evidence"][str(path.relative_to(ROOT))] = {"present": present, "required": required}


def _finalize_check(record: dict[str, Any], name: str, path: Path) -> None:
    """Expose the effective checker contract in the runner record."""
    record.setdefault("checker_contracts", {})[name] = {
        "script": str(path),
        "zero_skipped": True,
        "zero_xfailed": True,
    }


def _finalize(record: dict[str, Any], path: Path) -> int:
    failures: list[str] = []
    for check in record["checks"]:
        if check.get("exit_code") != 0:
            failures.append(f"CHECK_FAILED: {check['argv'][:3]} exit={check.get('exit_code')}")
    for name, evidence in record["evidence"].items():
        if evidence["required"] and not evidence["present"]:
            failures.append(f"EVIDENCE_MISSING: {name}")
    record["failures"] = failures
    record["ok"] = not failures
    record["ended_at"] = time.strftime("%Y-%m-%dT%H:%M:%S%z")
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(record, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    print(
        json.dumps(
            {"ok": record["ok"], "failures": failures, "record": str(path)}, ensure_ascii=False
        )
    )
    return 0 if record["ok"] else 1


def _resolve_workspace(path: Path) -> Path:
    """Keep all subprocess paths inside the repository workspace."""
    resolved = (path if path.is_absolute() else ROOT / path).resolve()
    if not (resolved == ROOT or ROOT in resolved.parents):
        raise ValueError(f"path outside workspace: {path}")
    return resolved


def _resolve_executable(executable: str) -> str:
    """Accept only repo-relative checker paths or bare executable names."""
    candidate = Path(executable)
    if candidate.is_absolute() and candidate.is_file():
        return str(candidate)
    if candidate.is_absolute() or candidate.parent != Path("."):
        raise ValueError(f"executable outside workspace: {executable}")
    return executable


def _is_python_executable(executable: str) -> bool:
    """Only Python runtimes can execute checker scripts directly."""
    return Path(executable).stem.lower().startswith("python")


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--scope", required=True, choices=sorted(SCOPES))
    parser.add_argument("--reports-dir", type=Path, default=DEFAULT_REPORTS)
    parser.add_argument("--timeout", type=float, default=1800.0)
    parser.add_argument(
        "--python",
        default=sys.executable,
        help="Python executable used for checker subprocesses",
    )
    args = parser.parse_args(argv)
    try:
        args.python = _resolve_executable(args.python)
    except ValueError as exc:
        print(f"CI_CHECK_INVALID_EXECUTABLE: {exc}", file=sys.stderr)
        return 2
    # A caller may supply a script that stands in for a Python executable in
    # contract tests.  On Windows, subprocess cannot execute a bare .py path;
    # run it through the current interpreter so its real exit code is kept.
    if not _is_python_executable(args.python) and Path(args.python).suffix == ".py":
        args.python = f"{sys.executable}|{args.python}"

    scope = SCOPES[args.scope]
    current = _platform()
    try:
        reports_dir = _resolve_workspace(args.reports_dir)
    except ValueError as exc:
        print(f"CI_CHECK_INVALID_REPORTS_DIR: {exc}", file=sys.stderr)
        return 2
    record_path = reports_dir / f"run-ci-checks-{args.scope}.json"
    record: dict[str, Any] = {
        "schema_version": 1,
        "started_at": time.strftime("%Y-%m-%dT%H:%M:%S%z"),
        "scope": args.scope,
        "argv": list(sys.argv[1:]),
        "runner_version": "1",
        "platform": current,
        "platform_required": list(scope["platforms"]),
        "source": {
            "head": _git("rev-parse", "HEAD"),
            "branch": _git("branch", "--show-current"),
            "dirty": bool(_git("status", "--porcelain")),
        },
        "checks": [],
        "evidence": {},
    }

    if current not in scope["platforms"]:
        record["checks"].append(
            {
                "argv": [f"require-platform:{','.join(scope['platforms'])}"],
                "exit_code": 2,
                "stderr": f"CI_CHECK_PLATFORM_UNSUPPORTED: {current} cannot run {args.scope}",
                "duration_seconds": 0,
            }
        )
        return _finalize(record, record_path)

    env = dict(os.environ)
    if "group" in scope:
        env["QTS_TEST_GROUP"] = scope["group"]
    if "subset" in scope:
        env["QTS_TEST_SUBSET"] = scope["subset"]
    if "network" in scope:
        env["QTS_TEST_NETWORK_POLICY"] = scope["network"]
    if "exclude" in scope:
        env["QTS_TEST_EXCLUDE_GROUPS"] = scope["exclude"]

    for name in scope["checks"]:
        if name == "manifest":
            manifest_argv = [
                "scripts/verify_test_manifest.py",
                "--json-out",
                str(reports_dir / "test-manifest.json"),
            ]
            if _is_python_executable(args.python):
                manifest_argv = [args.python, *manifest_argv]
            elif "|" in args.python:
                runtime, script = args.python.split("|", 1)
                manifest_argv = [runtime, script, *manifest_argv]
            _run(
                manifest_argv,
                env=env,
                cwd=ROOT,
                timeout=min(args.timeout, 300),
                record=record,
            )
        elif name == "pytest":
            junit = scope["junit"]
            argv = [
                args.python,
                "-m",
                "pytest",
                "-q",
                "trader/tests",
                "--tb=short",
                "--strict-markers",
                f"--junitxml={junit}",
            ]
            if "coverage" in scope:
                argv.extend(
                    [
                        "--cov=trader",
                        "--cov-branch",
                        f"--cov-report=xml:{scope['coverage']}",
                        "--cov-report=term-missing",
                        "--cov-fail-under=0",
                    ]
                )
            _run(argv, env=env, cwd=ROOT, timeout=args.timeout, record=record)
        elif name == "junit":
            _finalize_check(record, name, Path("scripts/check_junit.py"))
            _run(
                [
                    args.python,
                    "scripts/check_junit.py",
                    scope["junit"],
                    "--require-zero-skipped",
                    "--require-zero-xfailed",
                    "--json-out",
                    str(reports_dir / (Path(scope["junit"]).stem + ".json")),
                ],
                env=env,
                cwd=ROOT,
                timeout=min(args.timeout, 300),
                record=record,
            )
        elif name == "coverage":
            _finalize_check(record, name, Path("scripts/check_coverage.py"))
            _run(
                [
                    args.python,
                    "scripts/check_coverage.py",
                    scope["coverage"],
                    "--json-out",
                    str(reports_dir / "coverage-gate.json"),
                ],
                env=env,
                cwd=ROOT,
                timeout=min(args.timeout, 300),
                record=record,
            )
        elif name == "compose-config":
            script = (
                "import os, pathlib, tempfile; "
                "secrets=pathlib.Path(tempfile.mkdtemp(prefix='qts-ci-secrets-')); "
                "(secrets/'api').write_text('', encoding='utf-8'); "
                "(secrets/'secret').write_text('', encoding='utf-8'); "
                "dr=secrets/'dr'; dr.mkdir(); "
                "env=dict(os.environ); "
                "env.update({'QTS_COMPOSE_PROJECT':'qts_ci_config',"
                "'BINANCE_API_KEY_FILE_PATH':str(secrets/'api'),"
                "'BINANCE_SECRET_KEY_FILE_PATH':str(secrets/'secret'),"
                "'QTS_DR_EVIDENCE_DIR':str(dr)}); "
                "os.execvpe('docker',['docker','compose','-p','qts_ci_config','config','--quiet'],env)"
            )
            _run(
                [args.python, "-c", script],
                env=env,
                cwd=ROOT,
                timeout=min(args.timeout, 300),
                record=record,
            )
        elif name == "preview-stack":
            _run(
                [
                    args.python,
                    "scripts/validate_preview_stack.py",
                    "--compose-file",
                    "compose.yml",
                    "--compose-file",
                    "deploy/compose.preview.yml",
                    "--env-out",
                    str(reports_dir / "preview.env"),
                    "--json-out",
                    str(reports_dir / "preview-stack.json"),
                ],
                env=env,
                cwd=ROOT,
                timeout=min(args.timeout, 300),
                record=record,
            )
            _evidence(record, str(reports_dir / "preview-stack.json"), required=True)
            _evidence(record, str(reports_dir / "preview.env"), required=True)
        elif name == "browser-smoke":
            required = (
                "QTS_PR_PREVIEW_ORIGIN",
                "QTS_PR_PREVIEW_PASSWORD_FILE",
                "QTS_PR_PREVIEW_IMAGE_ID",
                "GITHUB_SHA",
            )
            missing = [key for key in required if not os.getenv(key, "").strip()]
            if missing:
                record["checks"].append(
                    {
                        "argv": ["require-env:" + ",".join(missing)],
                        "exit_code": 2,
                        "stderr": "CI_CHECK_REQUIRED_INPUT_MISSING: " + ",".join(missing),
                        "duration_seconds": 0,
                    }
                )
            else:
                _run(
                    [
                        args.python,
                        "scripts/preview_browser_smoke.py",
                        "--origin",
                        os.environ["QTS_PR_PREVIEW_ORIGIN"],
                        "--password-file",
                        os.environ["QTS_PR_PREVIEW_PASSWORD_FILE"],
                        "--username",
                        os.getenv("QTS_PR_PREVIEW_USERNAME", "owner"),
                        "--evidence",
                        str(reports_dir / "browser-smoke.json"),
                    ],
                    env=env,
                    cwd=ROOT,
                    timeout=min(args.timeout, 600),
                    record=record,
                )
                _evidence(record, str(reports_dir / "browser-smoke.json"), required=True)

    for name in ("junit", "coverage"):
        if name in scope:
            _evidence(record, scope[name], required=True)
    for name in ("junit", "coverage"):
        if name not in scope:
            continue
        json_path = str(reports_dir / (Path(scope[name]).stem + ".json"))
        _evidence(record, json_path, required=True)

    return _finalize(record, record_path)


if __name__ == "__main__":
    raise SystemExit(main())
