"""Trusted dataset eligibility for research backtest submission.

A dataset is eligible only when a trusted provenance link exists and the linked
historical dataset is published, persisted and backtest-eligible. The caller's
``source`` string is never evidence. If the history store cannot answer, the
submission is refused with ``HISTORY_STORE_UNAVAILABLE`` instead of silently
skipping the gate.
"""

from __future__ import annotations

from typing import Any

HISTORY_STORE_UNAVAILABLE = "HISTORY_STORE_UNAVAILABLE"
DATASET_PROVENANCE_UNTRUSTED = "DATASET_PROVENANCE_UNTRUSTED"
DATASET_PROVENANCE_MISMATCH = "DATASET_PROVENANCE_MISMATCH"
DATASET_ELIGIBILITY_GATE_UNAVAILABLE = "DATASET_ELIGIBILITY_GATE_UNAVAILABLE"


def link_is_trusted(link: dict[str, Any] | None) -> bool:
    """A trusted link carries an official eligibility flag and a provenance hash."""
    return bool(
        link
        and link.get("official_eligible")
        and str(link.get("provenance_hash") or "").strip()
        and str(link.get("history_dataset_id") or "").strip()
    )


async def require_published_dataset(dataset_id: str, *, bridge, history_store) -> bool:
    """Return True only for a trusted, published, persisted history dataset.

    Any storage error while answering is converted to the stable fail-closed
    reason so a research submission can never treat unavailability as eligible.
    """
    try:
        link = await bridge.find_link_by_research_dataset(dataset_id)
        if not link_is_trusted(link):
            return False
        history_dataset_id = str(link.get("history_dataset_id") or "")
        row = await history_store.get_dataset(history_dataset_id)
        if row is None:
            return False
        return (
            row.get("status") == "PUBLISHED"
            and bool(row.get("persistence_ok"))
            and bool(row.get("backtest_eligible"))
        )
    except Exception as exc:
        raise ValueError(HISTORY_STORE_UNAVAILABLE) from exc


__all__ = [
    "DATASET_ELIGIBILITY_GATE_UNAVAILABLE",
    "DATASET_PROVENANCE_MISMATCH",
    "DATASET_PROVENANCE_UNTRUSTED",
    "HISTORY_STORE_UNAVAILABLE",
    "link_is_trusted",
    "require_published_dataset",
]
