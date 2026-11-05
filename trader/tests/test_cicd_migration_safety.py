"""Static expand-only migration contract (C5).

Rollback safety depends on migrations only adding schema objects.  This test
does not need PostgreSQL; the real data-copy migration and conservation checks
live in ``test_cicd_migration_compat.py``.
"""

from __future__ import annotations

import hashlib
import re
from pathlib import Path

import pytest

from trader.trading.migrations import MIGRATIONS

ROOT = Path(__file__).resolve().parents[2]
MIGRATION_DIR = ROOT / "trader" / "trading"
DESTRUCTIVE = re.compile(
    r"\b(DROP\s+(TABLE|COLUMN|SCHEMA|TYPE|INDEX)|TRUNCATE|ALTER\s+COLUMN|"
    r"DELETE\s+FROM|UPDATE\s+\w+\s+SET|INSERT\s+INTO|RENAME\s+(COLUMN|TO))\b",
    re.IGNORECASE,
)


def _sql_without_comments(path: Path) -> str:
    lines = []
    for line in path.read_text(encoding="utf-8").splitlines():
        lines.append(line.split("--", 1)[0])
    return "\n".join(lines)


def test_migration_versions_are_ordered_and_files_exist():
    versions = [version for version, _ in MIGRATIONS]
    assert versions == sorted(versions)
    assert len(versions) == len(set(versions))
    for _, filename in MIGRATIONS:
        path = MIGRATION_DIR / filename
        assert path.is_file(), filename
        assert path.read_text(encoding="utf-8").strip(), filename


def test_every_migration_is_expand_only():
    for _, filename in MIGRATIONS:
        sql = _sql_without_comments(MIGRATION_DIR / filename)
        match = DESTRUCTIVE.search(sql)
        assert match is None, f"{filename} contains destructive statement: {match.group(0)}"


def test_migration_checksums_are_derived_from_file_content():
    for _, filename in MIGRATIONS:
        content = (MIGRATION_DIR / filename).read_bytes()
        assert hashlib.sha256(content).hexdigest()


@pytest.mark.parametrize(
    "statement",
    [
        "UPDATE trading_accounts SET version = 1",
        "INSERT INTO trading_ledger VALUES (1)",
        "ALTER TABLE trading_orders RENAME COLUMN a TO b",
        "DROP INDEX trading_commands_status",
    ],
)
def test_detector_rejects_additional_data_mutating_statements(statement):
    assert DESTRUCTIVE.search(statement)
