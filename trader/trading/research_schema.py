"""Access to the Research plane schema file.

The Research schema is owned by the Research plane (``research_schema.sql``).
Both the Research store and the history bridge need it, so the path lives in
one place.
"""

from __future__ import annotations

from pathlib import Path

SCHEMA_PATH = Path(__file__).with_name("research_schema.sql")


def research_schema_sql() -> str:
    """Return the Research plane DDL."""
    return SCHEMA_PATH.read_text(encoding="utf-8")


__all__ = ["SCHEMA_PATH", "research_schema_sql"]
