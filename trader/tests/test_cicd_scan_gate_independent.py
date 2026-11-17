"""Independent acceptance tests for the release image vulnerability gate.

The expectations in this module are derived from sources that are independent
of the implementation under test:

* the real Trivy SARIF payload of the failing release scan, embedded below as
  ``REAL_SCAN_FINDINGS`` (one row per SARIF result, in report order),
* the committed dependency-lock baseline (git ``HEAD``), and
* the Trivy advisory metadata carried by that payload (its "Fixed Version"
  fields), which define the minimum acceptable package versions.

Contract under test: the release workflow must fail the build when the
published image contains a fixable vulnerability, that failure must be
explainable from the SARIF report alone, and the pinned dependencies must
carry the versions the advisories ask for.
"""

from __future__ import annotations

import ast
import hashlib
import json
import re
import subprocess
import sys
from pathlib import Path

import pytest
import yaml
from packaging.requirements import Requirement
from packaging.version import Version

ROOT = Path(__file__).resolve().parents[2]
RELEASE_WORKFLOW = ROOT / ".github" / "workflows" / "release-build.yml"
DOCKERFILE = ROOT / "Dockerfile"
SUMMARY_SCRIPT = ROOT / "scripts" / "summarize_trivy_sarif.py"
TEST_MANIFEST = ROOT / "ci" / "test_manifest.json"
ENGINEER_TEST_FILE = ROOT / "trader" / "tests" / "test_cicd_scan_gate_diagnostics.py"

ARTIFACT_DIR = ROOT / "artifacts" / "branch-preview-20261112" / "engineer-scan-20261115"
REAL_SARIF_SAMPLE = ARTIFACT_DIR / "trivy-preview.sarif"

sys.path.insert(0, str(ROOT))

from scripts import summarize_trivy_sarif  # noqa: E402

GATE_STEP_NAME = "Fail on critical or high image vulnerabilities"
SUMMARY_STEP_NAME = "Show image vulnerability details when the gate fails"

# The 52 findings of the real Trivy SARIF report that the release gate produced
# for the vulnerable image: (rule id, severity, package, installed, fixed).
REAL_SCAN_FINDINGS: tuple[tuple[str, str, str, str, str], ...] = (
    ("CVE-2026-109518", "HIGH", "apt", "3.0.3", "3.0.4"),
    ("CVE-2026-109518", "HIGH", "libapt-pkg7.0", "3.0.3", "3.0.4"),
    (
        "CVE-2026-76844",
        "HIGH",
        "zlib1g",
        "1:1.3.dfsg+really1.3.1-1+b1",
        "1:1.3.dfsg+really1.3.1-1+deb13u1",
    ),
    (
        "CVE-2026-85091",
        "HIGH",
        "zlib1g",
        "1:1.3.dfsg+really1.3.1-1+b1",
        "1:1.3.dfsg+really1.3.1-1+deb13u1",
    ),
    (
        "CVE-2026-27171",
        "MEDIUM",
        "zlib1g",
        "1:1.3.dfsg+really1.3.1-1+b1",
        "1:1.3.dfsg+really1.3.1-1+deb13u1",
    ),
    ("CVE-2025-69223", "HIGH", "aiohttp", "3.11.16", "3.13.3"),
    ("CVE-2026-111064", "HIGH", "aiohttp", "3.11.16", "3.14.4"),
    ("CVE-2026-111066", "HIGH", "aiohttp", "3.11.16", "3.14.4"),
    ("CVE-2026-123817", "HIGH", "aiohttp", "3.11.16", "3.14.4"),
    ("CVE-2026-69244", "HIGH", "aiohttp", "3.11.16", "3.14.3"),
    ("CVE-2025-69227", "MEDIUM", "aiohttp", "3.11.16", "3.13.3"),
    ("CVE-2025-69228", "MEDIUM", "aiohttp", "3.11.16", "3.13.3"),
    ("CVE-2025-69229", "MEDIUM", "aiohttp", "3.11.16", "3.13.3"),
    ("CVE-2026-111063", "MEDIUM", "aiohttp", "3.11.16", "3.14.4"),
    ("CVE-2026-111065", "MEDIUM", "aiohttp", "3.11.16", "3.14.4"),
    ("CVE-2026-111748", "MEDIUM", "aiohttp", "3.11.16", "3.14.4"),
    ("CVE-2026-111750", "MEDIUM", "aiohttp", "3.11.16", "3.14.4"),
    ("CVE-2026-22815", "MEDIUM", "aiohttp", "3.11.16", "3.13.4"),
    ("CVE-2026-34515", "MEDIUM", "aiohttp", "3.11.16", "3.13.4"),
    ("CVE-2026-34516", "MEDIUM", "aiohttp", "3.11.16", "3.13.4"),
    ("CVE-2026-34525", "MEDIUM", "aiohttp", "3.11.16", "3.13.4"),
    ("CVE-2026-34993", "MEDIUM", "aiohttp", "3.11.16", "3.14.0"),
    ("CVE-2026-47265", "MEDIUM", "aiohttp", "3.11.16", "3.14.0"),
    ("CVE-2026-54273", "MEDIUM", "aiohttp", "3.11.16", "3.14.1"),
    ("CVE-2026-54274", "MEDIUM", "aiohttp", "3.11.16", "3.14.1"),
    ("CVE-2026-54276", "MEDIUM", "aiohttp", "3.11.16", "3.14.1"),
    ("CVE-2026-54277", "MEDIUM", "aiohttp", "3.11.16", "3.14.1"),
    ("CVE-2026-54278", "MEDIUM", "aiohttp", "3.11.16", "3.14.1"),
    ("CVE-2026-59881", "MEDIUM", "aiohttp", "3.11.16", "3.14.2"),
    ("CVE-2026-69243", "MEDIUM", "aiohttp", "3.11.16", "3.14.2"),
    ("CVE-2025-53643", "LOW", "aiohttp", "3.11.16", "3.12.14"),
    ("CVE-2025-69224", "LOW", "aiohttp", "3.11.16", "3.13.3"),
    ("CVE-2025-69225", "LOW", "aiohttp", "3.11.16", "3.13.3"),
    ("CVE-2025-69226", "LOW", "aiohttp", "3.11.16", "3.13.3"),
    ("CVE-2025-69230", "LOW", "aiohttp", "3.11.16", "3.13.3"),
    ("CVE-2026-111749", "LOW", "aiohttp", "3.11.16", "3.14.4"),
    ("CVE-2026-34513", "LOW", "aiohttp", "3.11.16", "3.13.4"),
    ("CVE-2026-34514", "LOW", "aiohttp", "3.11.16", "3.13.4"),
    ("CVE-2026-34517", "LOW", "aiohttp", "3.11.16", "3.13.4"),
    ("CVE-2026-34518", "LOW", "aiohttp", "3.11.16", "3.13.4"),
    ("CVE-2026-34519", "LOW", "aiohttp", "3.11.16", "3.13.4"),
    ("CVE-2026-34520", "LOW", "aiohttp", "3.11.16", "3.13.4"),
    ("CVE-2026-50269", "LOW", "aiohttp", "3.11.16", "3.14.0"),
    ("CVE-2026-54275", "LOW", "aiohttp", "3.11.16", "3.14.1"),
    ("CVE-2026-54279", "LOW", "aiohttp", "3.11.16", "3.14.1"),
    ("CVE-2026-54280", "LOW", "aiohttp", "3.11.16", "3.14.1"),
    ("CVE-2025-8869", "MEDIUM", "pip", "25.0.1", "25.3"),
    ("CVE-2026-13346", "MEDIUM", "pip", "25.0.1", "26.2.0"),
    ("CVE-2026-3219", "MEDIUM", "pip", "25.0.1", "26.1"),
    ("CVE-2026-6357", "MEDIUM", "pip", "25.0.1", "26.1"),
    ("CVE-2026-8643", "MEDIUM", "pip", "25.0.1", "26.1.2"),
    ("CVE-2026-1703", "LOW", "pip", "25.0.1", "26.0"),
)

# Census of that report, derived from the rows above.
EXPECTED_CENSUS = {"total": 52, "HIGH": 9, "MEDIUM": 26, "LOW": 17}
EXPECTED_PACKAGES = {"aiohttp", "apt", "libapt-pkg7.0", "pip", "zlib1g"}

# Dependency-lock baseline (git HEAD == the pre-change snapshots kept by the
# engineer).  The digest covers every package except aiohttp, including its
# pinned version and its full set of wheel hashes.
BASELINE_LOCK_DIGESTS = {
    "trader/requirements-runtime.constraints.txt": (
        "a90ec3eddac7559ef5bb2bbb1b191346c58e14b0606092e4eee9e76ed5bd4ca9"
    ),
    "trader/requirements-ci.constraints.txt": (
        "e0b5f2c329c25aeabb6f2627cb303bf91c4bd4ff5070f7998b304ba73240e468"
    ),
    "trader/requirements-ci.windows.constraints.txt": (
        "44b6b3a1746bac035b68782a5e9377d558d6ee838e0b072adf376e5f83ad60ff"
    ),
}
BASELINE_PACKAGE_COUNTS = {
    "trader/requirements-runtime.constraints.txt": 39,
    "trader/requirements-ci.constraints.txt": 76,
    "trader/requirements-ci.windows.constraints.txt": 77,
}
BASELINE_AIOHTTP_VERSION = "3.11.16"
BASELINE_AIOHTTP_HASH_COUNT = 81

# Base image digests before the change (git HEAD) and after (working tree).
PRE_CHANGE_PYTHON_BASE = "b18b261e1b4974b4f321451ad5251ded85abb22c2b8b262da527dda1d031ab30"
PRE_CHANGE_POSTGRES_BASE = "5db6dac2ef5dcff512f4f00a009c3c332b9f2f28f24277175bf3e4a3d02ef83a"

# Runtime requirements of aiohttp 3.14.5, taken from the installed
# distribution metadata of that release.
AIOHTTP_RUNTIME_REQUIREMENTS = (
    "aiohappyeyeballs>=2.5.0",
    "aiosignal>=1.4.0",
    "attrs>=17.3.0",
    "frozenlist>=1.1.1",
    "multidict>=4.5,<8.0",
    "propcache>=0.2.0",
    "yarl>=1.25.1,<2.0",
)


def _workflow() -> dict:
    return yaml.safe_load(RELEASE_WORKFLOW.read_text(encoding="utf-8"))


def _steps() -> list[dict]:
    return _workflow()["jobs"]["build-image"]["steps"]


def _step(name: str) -> dict:
    matches = [step for step in _steps() if step.get("name") == name]
    assert len(matches) == 1, f"expected exactly one step named {name!r}, got {len(matches)}"
    return matches[0]


def _git_show(path: str) -> str:
    result = subprocess.run(
        ["git", "show", f"HEAD:{path}"],
        cwd=ROOT,
        capture_output=True,
        text=True,
        encoding="utf-8",
    )
    assert result.returncode == 0, f"git show HEAD:{path} failed: {result.stderr}"
    return result.stdout


def _parse_lock(text: str) -> dict[str, dict[str, object]]:
    """Parse a ``uv pip compile --generate-hashes`` lock file."""
    packages: dict[str, dict[str, object]] = {}
    current: str | None = None
    for line in text.replace("\r\n", "\n").split("\n"):
        stripped = line.strip()
        if not stripped or stripped.startswith("#"):
            continue
        header = re.match(r"^([A-Za-z0-9._-]+)==([^\s\\]+)", stripped)
        if header:
            current = header.group(1)
            packages[current] = {"version": header.group(2), "hashes": []}
            continue
        digest = re.search(r"--hash=sha256:([0-9a-f]{64})", stripped)
        if digest and current is not None:
            packages[current]["hashes"].append(digest.group(1))
    return packages


def _normalise(packages: dict[str, dict[str, object]]) -> dict[str, dict[str, object]]:
    return {name.lower().replace("_", "-"): entry for name, entry in packages.items()}


STATUS_FUNCTIONS = ("success", "failure", "always", "cancelled")


class ActionsContext:
    """The state a GitHub Actions step condition can observe.

    ``job_status`` is the status the job carries after the previous steps
    have run (``success``/``failure``/``cancelled``); ``step_outcomes`` holds
    the per-step ``outcome`` values.
    """

    def __init__(
        self,
        job_status: str,
        step_outcomes: dict[str, str] | None = None,
        env: dict[str, str] | None = None,
    ) -> None:
        self.job_status = job_status
        self.step_outcomes = step_outcomes or {}
        self.env = env or {}

    def status(self, name: str) -> bool:
        if name == "success":
            return self.job_status == "success"
        if name == "failure":
            return self.job_status == "failure"
        if name == "always":
            return True
        if name == "cancelled":
            return self.job_status == "cancelled"
        raise AssertionError(f"unknown status function: {name}")

    def lookup(self, path: str) -> str | None:
        parts = path.split(".")
        if parts[0] == "steps" and len(parts) >= 3 and parts[2] in {"outcome", "conclusion"}:
            return self.step_outcomes.get(parts[1])
        if parts[0] == "env":
            return self.env.get(parts[1] if len(parts) > 1 else "")
        return None


def _tokenize(expression: str) -> list[tuple[str, str]]:
    tokens: list[tuple[str, str]] = []
    index = 0
    while index < len(expression):
        char = expression[index]
        if char.isspace():
            index += 1
            continue
        if char in "'\"":
            end = expression.index(char, index + 1)
            tokens.append(("literal", expression[index + 1 : end]))
            index = end + 1
            continue
        operator = expression[index : index + 2]
        if operator in {"&&", "||", "==", "!="}:
            tokens.append(("operator", operator))
            index += 2
            continue
        if char in "!()":
            tokens.append(("operator", char))
            index += 1
            continue
        if char.isdigit():
            start = index
            while index < len(expression) and expression[index].isdigit():
                index += 1
            tokens.append(("literal", expression[start:index]))
            continue
        if char.isalpha() or char == "_":
            start = index
            while index < len(expression) and (
                expression[index].isalnum() or expression[index] in "_-."
            ):
                index += 1
            tokens.append(("word", expression[start:index]))
            continue
        raise AssertionError(f"unsupported character {char!r} in {expression!r}")
    return tokens


class _ConditionParser:
    """Recursive-descent parser for the GitHub Actions ``if:`` subset."""

    def __init__(self, tokens: list[tuple[str, str]]) -> None:
        self.tokens = tokens
        self.position = 0
        self.uses_status_function = False

    def peek(self) -> tuple[str, str] | None:
        if self.position < len(self.tokens):
            return self.tokens[self.position]
        return None

    def take(self) -> tuple[str, str]:
        token = self.peek()
        assert token is not None, "unexpected end of condition"
        self.position += 1
        return token

    def parse(self) -> tuple:
        node = self.parse_or()
        assert self.position == len(self.tokens), "trailing tokens in condition"
        return node

    def parse_or(self) -> tuple:
        node = self.parse_and()
        while (token := self.peek()) is not None and token == ("operator", "||"):
            self.take()
            node = ("or", node, self.parse_and())
        return node

    def parse_and(self) -> tuple:
        node = self.parse_unary()
        while (token := self.peek()) is not None and token == ("operator", "&&"):
            self.take()
            node = ("and", node, self.parse_unary())
        return node

    def parse_unary(self) -> tuple:
        if self.peek() == ("operator", "!"):
            self.take()
            return ("not", self.parse_unary())
        return self.parse_primary()

    def parse_primary(self) -> tuple:
        kind, value = self.take()
        if kind == "operator" and value == "(":
            node = self.parse_or()
            closing = self.take()
            assert closing == ("operator", ")"), "unbalanced parenthesis"
            return node
        if kind != "word":
            raise AssertionError(f"unexpected token {value!r} in condition")
        if self.peek() == ("operator", "("):
            self.take()
            closing = self.take()
            assert closing == ("operator", ")"), f"malformed call to {value}"
            assert value in STATUS_FUNCTIONS, f"unsupported function {value}"
            self.uses_status_function = True
            return ("status", value)
        operator = self.take()
        assert operator[0] == "operator" and operator[1] in {"==", "!="}, (
            f"unsupported comparison in {value}"
        )
        literal = self.take()
        assert literal[0] == "literal", f"unsupported literal {literal}"
        return ("compare", value, operator[1], literal[1])


def _evaluate(node: tuple, context: ActionsContext) -> bool:
    kind = node[0]
    if kind == "or":
        return _evaluate(node[1], context) or _evaluate(node[2], context)
    if kind == "and":
        return _evaluate(node[1], context) and _evaluate(node[2], context)
    if kind == "not":
        return not _evaluate(node[1], context)
    if kind == "status":
        return context.status(node[1])
    if kind == "compare":
        left = context.lookup(node[1])
        return (left == node[3]) if node[2] == "==" else (left != node[3])
    raise AssertionError(f"unknown node {kind}")


def step_condition_holds(expression: str, context: ActionsContext) -> bool:
    """Evaluate a step-level ``if:`` with GitHub Actions semantics.

    A condition that names no status function receives the implicit
    ``success()``: after a failed step the job status is ``failure``, so such
    a step is skipped.
    """
    parser = _ConditionParser(_tokenize(expression))
    node = parser.parse()
    value = _evaluate(node, context)
    if not parser.uses_status_function:
        value = value and context.status("success")
    return value


def _lock_digest(packages: dict[str, dict[str, object]], skip: tuple[str, ...] = ()) -> str:
    lines = []
    for name in sorted(packages):
        if name in skip:
            continue
        entry = packages[name]
        hashes = " ".join(sorted(entry["hashes"]))
        lines.append(f"{name}=={entry['version']} {hashes}")
    return hashlib.sha256("\n".join(lines).encode("utf-8")).hexdigest()


def _trivy_message(rule_id: str, severity: str, package: str, installed: str, fixed: str) -> str:
    """Rebuild the message text that Trivy 0.70.0 writes into a SARIF result."""
    return (
        f"Package: {package}\n"
        f"Installed Version: {installed}\n"
        f"Vulnerability {rule_id}\n"
        f"Severity: {severity}\n"
        f"Fixed Version: {fixed}\n"
        f"Link: [{rule_id}](https://avd.aquasec.com/nvd/{rule_id.lower()})"
    )


def _sarif_payload(rows: tuple[tuple[str, str, str, str, str], ...]) -> dict:
    level_of = {"CRITICAL": "error", "HIGH": "error", "MEDIUM": "warning", "LOW": "note"}
    results = []
    for rule_id, severity, package, installed, fixed in rows:
        results.append(
            {
                "ruleId": rule_id,
                "level": level_of[severity],
                "message": {"text": _trivy_message(rule_id, severity, package, installed, fixed)},
                "locations": [
                    {
                        "physicalLocation": {
                            "artifactLocation": {
                                "uri": "library/qts-runtime",
                                "uriBaseId": "ROOTPATH",
                            },
                            "region": {
                                "startLine": 1,
                                "startColumn": 1,
                                "endLine": 1,
                                "endColumn": 1,
                            },
                        },
                        "message": {"text": f"library/qts-runtime: {package}@{installed}"},
                    }
                ],
            }
        )
    return {
        "version": "2.1.0",
        "runs": [{"tool": {"driver": {"name": "Trivy"}}, "results": results}],
    }


def _independent_fields(message: str) -> dict[str, str]:
    """Parse a Trivy SARIF message without using the implementation."""
    fields: dict[str, str] = {}
    for line in message.split("\n"):
        if ": " in line:
            key, _, value = line.partition(": ")
            fields[key.strip()] = value.strip()
    return fields


def _fixed_versions_for(package: str) -> list[str]:
    return [fixed for _, _, pkg, _, fixed in REAL_SCAN_FINDINGS if pkg == package]


# --------------------------------------------------------------------------- T1


def test_t1_gate_step_keeps_the_strict_scan_parameters() -> None:
    gate = _step(GATE_STEP_NAME)
    with_ = gate["with"]
    assert with_["format"] == "sarif"
    assert with_["output"] == "reports/trivy.sarif"
    assert with_["severity"] == "CRITICAL,HIGH"
    assert with_["exit-code"] == "1"
    assert with_["ignore-unfixed"] is True
    # The gate must be able to fail the job and must never be skipped.
    assert "continue-on-error" not in gate
    assert "if" not in gate


def test_t1_gate_is_not_weaker_than_the_committed_baseline() -> None:
    baseline = yaml.safe_load(_git_show(".github/workflows/release-build.yml"))
    baseline_steps = baseline["jobs"]["build-image"]["steps"]
    baseline_gate = [step for step in baseline_steps if step.get("name") == GATE_STEP_NAME]
    assert len(baseline_gate) == 1, "the committed baseline has no single scan gate step"
    baseline_with = baseline_gate[0]["with"]
    current_with = _step(GATE_STEP_NAME)["with"]
    for key in ("severity", "exit-code", "ignore-unfixed", "format", "output"):
        assert current_with[key] == baseline_with[key], f"gate parameter {key!r} changed"

    baseline_names = [step.get("name") for step in baseline_steps if step.get("name")]
    current_names = [step.get("name") for step in _steps() if step.get("name")]
    missing = [name for name in baseline_names if name not in current_names]
    assert not missing, f"workflow steps disappeared: {missing}"


def test_t1_no_ignore_mechanism_is_wired_into_the_repository() -> None:
    ignored_names = {".trivyignore", ".trivyignore.yaml", ".trivyignore.yml"}
    listed = subprocess.run(
        ["git", "ls-files"],
        cwd=ROOT,
        capture_output=True,
        text=True,
        encoding="utf-8",
        check=True,
    ).stdout.split("\n")
    untracked = subprocess.run(
        ["git", "ls-files", "--others", "--exclude-standard"],
        cwd=ROOT,
        capture_output=True,
        text=True,
        encoding="utf-8",
        check=True,
    ).stdout.split("\n")
    offenders = [
        name
        for name in [item for item in listed + untracked if item]
        if Path(name).name in ignored_names or Path(name).name in {"trivy.yaml", "trivy.yml"}
    ]
    assert not offenders, f"trivy ignore configuration exists: {offenders}"

    workflow_dir = ROOT / ".github" / "workflows"
    for path in sorted(workflow_dir.glob("*.yml")):
        text = path.read_text(encoding="utf-8")
        for marker in ("trivyignores", ".trivyignore", "ignorefile", "TRIVY_IGNORE"):
            assert marker not in text, f"{path.name} wires an ignore mechanism ({marker})"


# --------------------------------------------------------------------------- T2


def test_t2_summary_reports_every_finding_of_the_real_failing_scan() -> None:
    payload = _sarif_payload(REAL_SCAN_FINDINGS)
    findings = summarize_trivy_sarif.parse_findings(payload)

    assert len(findings) == EXPECTED_CENSUS["total"]
    census: dict[str, int] = {}
    for finding in findings:
        census[finding["severity"]] = census.get(finding["severity"], 0) + 1
    for severity in ("HIGH", "MEDIUM", "LOW"):
        assert census.get(severity) == EXPECTED_CENSUS[severity], f"{severity} census mismatch"
    assert {finding["package"] for finding in findings} == EXPECTED_PACKAGES

    # Independent re-derivation of every field from the SARIF message text.
    expected = {}
    for result in payload["runs"][0]["results"]:
        fields = _independent_fields(result["message"]["text"])
        expected[result["ruleId"], fields["Package"]] = (
            fields["Severity"],
            fields["Installed Version"],
            fields["Fixed Version"],
        )
    actual = {
        (finding["id"], finding["package"]): (
            finding["severity"],
            finding["installed"],
            finding["fixed"],
        )
        for finding in findings
    }
    assert actual == expected


def test_t2_embedded_fixture_matches_the_real_trivy_payload_when_available() -> None:
    if not REAL_SARIF_SAMPLE.is_file():
        # No local artefact: the embedded fixture must still be internally
        # consistent with the census it claims to reproduce.
        assert len(REAL_SCAN_FINDINGS) == EXPECTED_CENSUS["total"]
        assert sum(1 for row in REAL_SCAN_FINDINGS if row[1] == "HIGH") == EXPECTED_CENSUS["HIGH"]
        assert {row[2] for row in REAL_SCAN_FINDINGS} == EXPECTED_PACKAGES
        return

    payload = json.loads(REAL_SARIF_SAMPLE.read_text(encoding="utf-8"))
    results = payload["runs"][0]["results"]
    assert len(results) == len(REAL_SCAN_FINDINGS)
    for result, row in zip(results, REAL_SCAN_FINDINGS, strict=True):
        rule_id, severity, package, installed, fixed = row
        assert result["ruleId"] == rule_id
        fields = _independent_fields(result["message"]["text"])
        assert fields["Severity"] == severity
        assert fields["Package"] == package
        assert fields["Installed Version"] == installed
        assert fields["Fixed Version"] == fixed


def test_t2_exit_code_reflects_the_findings(tmp_path: Path) -> None:
    failing = tmp_path / "trivy.sarif"
    failing.write_text(
        json.dumps(_sarif_payload(REAL_SCAN_FINDINGS)),
        encoding="utf-8",
    )
    assert summarize_trivy_sarif.main([str(failing)]) == 1
    assert summarize_trivy_sarif.main([str(failing), "--fail-on", "CRITICAL"]) == 0

    # A report that cannot be read must never look like a clean scan.
    assert summarize_trivy_sarif.main([str(tmp_path / "missing.sarif")]) == 2
    broken = tmp_path / "broken.sarif"
    broken.write_text("{not json", encoding="utf-8")
    assert summarize_trivy_sarif.main([str(broken)]) == 2
    assert summarize_trivy_sarif.main([str(tmp_path)]) == 2

    empty = tmp_path / "empty.sarif"
    empty.write_text(json.dumps({"runs": [{"results": []}]}), encoding="utf-8")
    assert summarize_trivy_sarif.main([str(empty)]) == 0

    medium_only = tmp_path / "medium.sarif"
    medium_rows = tuple(row for row in REAL_SCAN_FINDINGS if row[1] == "MEDIUM")
    medium_only.write_text(json.dumps(_sarif_payload(medium_rows)), encoding="utf-8")
    assert summarize_trivy_sarif.main([str(medium_only)]) == 0
    assert summarize_trivy_sarif.main([str(medium_only), "--fail-on", "MEDIUM"]) == 1


def test_t2_rendered_table_is_ordered_and_contains_the_evidence() -> None:
    payload = _sarif_payload(REAL_SCAN_FINDINGS)
    findings = summarize_trivy_sarif.parse_findings(payload)
    rendered = summarize_trivy_sarif.render(findings)

    rank = {"CRITICAL": 0, "HIGH": 1, "MEDIUM": 2, "LOW": 3, "UNKNOWN": 4}
    ranks = [rank[finding["severity"]] for finding in summarize_trivy_sarif.sort_findings(findings)]
    assert ranks == sorted(ranks), "findings are not ordered by severity"

    assert "Total findings: 52" in rendered
    for package in EXPECTED_PACKAGES:
        assert package in rendered
    for rule_id, severity, package, installed, fixed in REAL_SCAN_FINDINGS:
        assert rule_id in rendered
        assert installed in rendered
        assert fixed in rendered


# --------------------------------------------------------------------------- T3


def test_t3_aiohttp_is_pinned_to_one_version_that_covers_every_advisory() -> None:
    pinned: dict[str, str] = {}
    for path in ("trader/requirements-runtime.txt", "trader/requirements-ci.txt"):
        match = re.search(
            r"^aiohttp==([0-9][0-9.]*)$",
            (ROOT / path).read_text(encoding="utf-8"),
            re.MULTILINE,
        )
        assert match, f"aiohttp is not pinned in {path}"
        pinned[path] = match.group(1)
    for path in BASELINE_LOCK_DIGESTS:
        match = re.search(
            r"^aiohttp==([0-9][0-9.]*)",
            (ROOT / path).read_text(encoding="utf-8"),
            re.MULTILINE,
        )
        assert match, f"aiohttp is not pinned in {path}"
        pinned[path] = match.group(1)
    pyproject = (ROOT / "pyproject.toml").read_text(encoding="utf-8")
    match = re.search(r'"aiohttp==([0-9][0-9.]*)"', pyproject)
    assert match, "aiohttp is not pinned in pyproject.toml"
    pinned["pyproject.toml"] = match.group(1)

    distinct = set(pinned.values())
    assert len(distinct) == 1, f"aiohttp is pinned inconsistently: {pinned}"

    # The advisory floor is derived from the real report: the newest version
    # any aiohttp finding asks for.
    required = max(Version(value) for value in _fixed_versions_for("aiohttp"))
    assert Version(distinct.pop()) >= required, f"aiohttp must be at least {required}"


def test_t3_only_aiohttp_changed_in_the_lock_files() -> None:
    for path, digest in BASELINE_LOCK_DIGESTS.items():
        current = _parse_lock((ROOT / path).read_text(encoding="utf-8"))
        assert len(current) == BASELINE_PACKAGE_COUNTS[path], f"{path}: package count changed"
        assert _lock_digest(current, ("aiohttp",)) == digest, f"{path}: another dependency changed"

        aiohttp = current.get("aiohttp")
        assert aiohttp is not None, f"{path}: aiohttp is missing from the lock"
        assert aiohttp["version"] != BASELINE_AIOHTTP_VERSION
        assert len(aiohttp["hashes"]) != BASELINE_AIOHTTP_HASH_COUNT

        # The working tree must not drift away from the committed lock.
        committed = _parse_lock(_git_show(path))
        assert _lock_digest(committed, ("aiohttp",)) == digest


def test_t3_lock_files_are_wellformed_hash_locks() -> None:
    for path in BASELINE_LOCK_DIGESTS:
        packages = _parse_lock((ROOT / path).read_text(encoding="utf-8"))
        for name, entry in packages.items():
            assert entry["version"], f"{path}: {name} has no version"
            hashes = entry["hashes"]
            assert hashes, f"{path}: {name} has no wheel hash"
            for digest in hashes:
                assert re.fullmatch(r"[0-9a-f]{64}", digest), f"{path}: {name} has a broken hash"
            assert len(hashes) == len(set(hashes)), f"{path}: {name} has duplicate hashes"


def test_t3_direct_requirements_are_satisfied_by_the_locks() -> None:
    pairs = (
        ("trader/requirements-runtime.txt", "trader/requirements-runtime.constraints.txt"),
        ("trader/requirements-ci.txt", "trader/requirements-ci.constraints.txt"),
    )
    for requirements_path, lock_path in pairs:
        packages = _normalise(_parse_lock((ROOT / lock_path).read_text(encoding="utf-8")))
        for line in (ROOT / requirements_path).read_text(encoding="utf-8").split("\n"):
            line = line.strip()
            if not line or line.startswith("#"):
                continue
            requirement = Requirement(line)
            pinned = packages.get(requirement.name.lower().replace("_", "-"))
            assert pinned is not None, f"{requirement.name} is missing from {lock_path}"
            if requirement.specifier:
                assert (
                    Version(str(pinned["version"])) in requirement.specifier
                ), f"{requirement.name}=={pinned['version']} violates {requirement.specifier}"


def test_t3_aiohttp_dependency_closure_is_locked() -> None:
    packages = _normalise(
        _parse_lock(
            (ROOT / "trader/requirements-runtime.constraints.txt").read_text(encoding="utf-8")
        )
    )
    for spec in AIOHTTP_RUNTIME_REQUIREMENTS:
        requirement = Requirement(spec)
        pinned = packages.get(requirement.name.lower().replace("_", "-"))
        assert pinned is not None, f"{requirement.name} is missing from the runtime lock"
        assert (
            Version(str(pinned["version"])) in requirement.specifier
        ), f"{requirement.name}=={pinned['version']} violates {requirement.specifier}"


# --------------------------------------------------------------------------- T4


def test_t4_base_images_are_digest_pinned() -> None:
    from_lines = [
        line.strip()
        for line in DOCKERFILE.read_text(encoding="utf-8").split("\n")
        if line.startswith("FROM ")
    ]
    assert len(from_lines) >= 2
    for line in from_lines:
        assert re.search(r"@sha256:[0-9a-f]{64}(\s+AS\s+\S+)?$", line), line

    digests = {line.split("@")[1].split()[0].removeprefix("sha256:") for line in from_lines}
    assert PRE_CHANGE_PYTHON_BASE not in digests, "the python base image was not bumped"
    assert PRE_CHANGE_POSTGRES_BASE in digests, "the postgres base image changed unexpectedly"


def test_t4_zlib_upgrade_is_targeted_and_matches_the_advisory() -> None:
    text = DOCKERFILE.read_text(encoding="utf-8")
    for pattern in (
        r"apt-get\s+upgrade",
        r"apt-get\s+-y\s+upgrade",
        r"apt-get\s+dist-upgrade",
        r"apt-get\s+full-upgrade",
        r"\bapt\s+upgrade\b",
    ):
        assert not re.search(pattern, text), f"blanket upgrade found: {pattern}"

    upgrades = re.findall(r"apt-get install --only-upgrade[^\n]*", text)
    assert upgrades, "no targeted upgrade in the Dockerfile"
    packages = []
    for line in upgrades:
        tail = line.split("--only-upgrade", 1)[1]
        packages.extend(
            token
            for token in tail.split()
            if not token.startswith("-") and re.fullmatch(r"[a-z0-9][a-z0-9.+-]*", token)
        )
    assert packages == ["zlib1g"], f"more than zlib1g is upgraded: {packages}"

    # The upgrade must target the version the advisory asks for.
    required = max(_fixed_versions_for("zlib1g"))
    assert "deb13u1" in text, "the Dockerfile does not reference the zlib1g security update"
    assert required.endswith("deb13u1"), f"unexpected zlib1g fixed version: {required}"
    installed = {row[3] for row in REAL_SCAN_FINDINGS if row[2] == "zlib1g"}
    assert required not in installed, "the advisory already lists the installed zlib1g version"


def test_t4_pip_is_pinned_to_a_patched_release_and_removed() -> None:
    text = DOCKERFILE.read_text(encoding="utf-8")
    match = re.search(r'"pip==([0-9][0-9.]*)"', text)
    assert match, "pip is not pinned to an exact version"
    pinned = Version(match.group(1))

    required = max(Version(value) for value in _fixed_versions_for("pip"))
    assert pinned >= required, f"pip must be at least {required}"

    lines = [line.strip() for line in text.split("\n")]
    install_index = next(
        index for index, line in enumerate(lines) if "pip install --no-cache-dir --upgrade" in line
    )
    uninstall_index = next(
        index for index, line in enumerate(lines) if "pip uninstall -y pip" in line
    )
    assert uninstall_index > install_index, "pip is uninstalled before it is used"

    after = "\n".join(lines[uninstall_index + 1 :])
    assert not re.search(r"\bpip\s+install\b", after), "pip is installed again after removal"
    assert "site-packages/pip" in text, "the pip package directory is not removed"
    for binary in ("/usr/local/bin/pip", "/usr/local/bin/pip3"):
        assert binary in text, f"{binary} is not removed"

    for line in lines:
        if line.startswith(("CMD", "ENTRYPOINT")):
            assert "pip" not in line, "the runtime entry point still calls pip"


def test_t4_docker_context_excludes_local_artifacts() -> None:
    dockerignore = ROOT / ".dockerignore"
    assert dockerignore.is_file()
    patterns = {
        line.strip()
        for line in dockerignore.read_text(encoding="utf-8").split("\n")
        if line.strip() and not line.strip().startswith("#")
    }
    # The repository keeps local virtualenvs and scan artefacts; they must not
    # be copied into the released image.
    assert "artifacts" in patterns
    assert ".venv" in patterns


# --------------------------------------------------------------------------- T5


def test_t5_summary_script_cannot_write_or_reach_the_network() -> None:
    tree = ast.parse(SUMMARY_SCRIPT.read_text(encoding="utf-8"))
    imported: set[str] = set()
    called: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            imported.update(alias.name.split(".")[0] for alias in node.names)
        elif isinstance(node, ast.ImportFrom):
            if node.module:
                imported.add(node.module.split(".")[0])
        elif isinstance(node, ast.Call):
            if isinstance(node.func, ast.Name):
                called.add(node.func.id)
            elif isinstance(node.func, ast.Attribute):
                called.add(node.func.attr)

    assert imported <= {"__future__", "argparse", "json", "sys", "pathlib"}, imported
    forbidden = {
        "write_text",
        "write_bytes",
        "unlink",
        "mkdir",
        "rmtree",
        "remove",
        "rename",
        "replace",
        "system",
        "popen",
        "run",
        "check_output",
        "check_call",
        "urlopen",
        "connect",
        "send",
        "recv",
        "eval",
        "exec",
    }
    assert not (
        called & forbidden
    ), f"the summary script performs side effects: {called & forbidden}"
    assert "read_text" in called, "the summary script does not read the SARIF report"


def test_t5_summary_step_cannot_change_the_gate_outcome() -> None:
    steps = _steps()
    gate_index = next(
        index for index, step in enumerate(steps) if step.get("name") == GATE_STEP_NAME
    )
    summary_index = next(
        index for index, step in enumerate(steps) if step.get("name") == SUMMARY_STEP_NAME
    )
    assert summary_index > gate_index, "the summary runs before the gate"

    gate = steps[gate_index]
    assert "continue-on-error" not in gate
    assert "if" not in gate

    summary = steps[summary_index]
    condition = summary.get("if")
    assert condition, "the diagnosis step has no condition"

    # The condition is evaluated with GitHub Actions semantics instead of
    # being compared to a literal: a step-level ``if`` that names no status
    # function receives the implicit ``success()`` and is therefore skipped
    # once the job status is ``failure``.
    gate_failed = ActionsContext("failure", {"scan-gate": "failure"})
    gate_passed = ActionsContext("success", {"scan-gate": "success"})
    assert step_condition_holds(condition, gate_failed) is True, (
        "the diagnosis step is skipped exactly when the finding list is needed"
    )
    assert step_condition_holds(condition, gate_passed) is False, (
        "a passing gate must not print a finding list"
    )

    # Control: the form that only inspects the step outcome is skipped by the
    # implicit success() as soon as the gate failed.
    assert (
        step_condition_holds("steps.scan-gate.outcome == 'failure'", gate_failed) is False
    )
    # always() keeps running whatever the job status is.
    assert step_condition_holds("always()", gate_failed) is True
    assert step_condition_holds("always()", gate_passed) is True

    # The condition must key on the gate's own outcome, not only on the job
    # status: a synthetic state with a failed job but a successful gate must
    # not trigger the diagnosis.
    synthetic = ActionsContext("failure", {"scan-gate": "success"})
    assert step_condition_holds(condition, synthetic) is False

    assert summary.get("continue-on-error") is True
    command = summary["run"]
    assert "scripts/summarize_trivy_sarif.py" in command
    assert "reports/trivy.sarif" in command
    for marker in ("exit 0", "trivy image", "trivy --", "::set-output", "git commit"):
        assert marker not in command, f"the summary step can influence the job: {marker}"


def test_t5_evidence_upload_runs_whatever_happened() -> None:
    upload = _step("Upload scan evidence")
    condition = upload.get("if")
    assert condition, "the evidence upload has no condition"
    assert step_condition_holds(condition, ActionsContext("failure", {"scan-gate": "failure"}))
    assert step_condition_holds(condition, ActionsContext("success", {"scan-gate": "success"}))


def test_t5_new_test_files_are_registered_in_the_test_manifest() -> None:
    manifest = json.loads(TEST_MANIFEST.read_text(encoding="utf-8"))
    offline = manifest["groups"]["offline"]
    assert "test_cicd_scan_gate_diagnostics.py" in offline
    assert "test_cicd_scan_gate_independent.py" in offline

    result = subprocess.run(
        [sys.executable, "scripts/verify_test_manifest.py"],
        cwd=ROOT,
        capture_output=True,
        text=True,
        encoding="utf-8",
    )
    assert result.returncode == 0, result.stdout + result.stderr
    report = json.loads(result.stdout)
    assert report["ok"] is True, report["errors"]


def test_t5_delivered_scan_gate_tests_do_not_suppress_failures() -> None:
    skip_call = "pytest." + "skip"
    expected_fail_call = "pytest." + "x" + "fail"
    mark_skip = "@pytest.mark." + "skip"
    expected_fail_word = "x" + "fail"
    for path in (ENGINEER_TEST_FILE, Path(__file__)):
        text = path.read_text(encoding="utf-8")
        for marker in (skip_call, expected_fail_call, mark_skip, expected_fail_word):
            assert marker not in text, f"{path.name} uses {marker}"
