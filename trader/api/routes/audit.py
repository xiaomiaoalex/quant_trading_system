"""
Audit API Routes
================
AI audit log query endpoints (Task 9.6).
"""
from __future__ import annotations

import logging
from datetime import datetime, timezone
from typing import Dict, List, Optional

from fastapi import APIRouter, HTTPException, Path, Query

from insight.ai_audit_log import AuditEntry as DomainAuditEntry
from trader.api.models.schemas import AuditEntry
from trader.api.routes.chat import get_chat_interface

router = APIRouter(prefix="/api/audit", tags=["Audit"])
logger = logging.getLogger(__name__)


# In-memory fallback store used by tests and when no audit provider is available.
_audit_entries: List[AuditEntry] = []


def _iso_z(value: Optional[datetime]) -> Optional[str]:
    if value is None:
        return None
    return value.astimezone(timezone.utc).isoformat().replace("+00:00", "Z")


def _to_api_entry(entry: DomainAuditEntry) -> AuditEntry:
    return AuditEntry(
        entry_id=entry.entry_id,
        strategy_id=entry.strategy_id,
        strategy_name=entry.strategy_name,
        version=entry.version,
        event_type=entry.event_type.value,
        status=entry.status.value,
        prompt=entry.prompt,
        generated_code=entry.generated_code,
        code_hash=entry.code_hash,
        llm_backend=entry.llm_backend,
        llm_model=entry.llm_model,
        execution_result=entry.execution_result,
        approver=entry.approver,
        approval_comment=entry.approval_comment,
        metadata=entry.metadata,
        created_at=_iso_z(entry.created_at),
        updated_at=_iso_z(entry.updated_at),
    )


def _parse_iso_range(name: str, raw_value: Optional[str]) -> Optional[datetime]:
    if raw_value is None:
        return None
    try:
        parsed = datetime.fromisoformat(raw_value.replace("Z", "+00:00"))
    except ValueError:
        raise HTTPException(status_code=400, detail=f"Invalid {name} format: {raw_value}")
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return parsed.astimezone(timezone.utc)


def _parse_entry_time(value: Optional[str]) -> Optional[datetime]:
    if not value:
        return None
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        return None
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return parsed.astimezone(timezone.utc)


async def _collect_audit_entries() -> List[AuditEntry]:
    """
    Aggregate entries from chat audit log and fallback in-memory store.
    """
    entries_by_id: Dict[str, AuditEntry] = {}

    # Source 1: Chat interface -> AI strategy generator -> AIAuditLog
    try:
        interface = get_chat_interface()
        audit_log = interface.get_audit_log() if hasattr(interface, "get_audit_log") else None
        if audit_log is not None:
            # Pull enough entries to support filter/pagination on API side.
            domain_entries = await audit_log.search(limit=5000)
            for domain_entry in domain_entries:
                api_entry = _to_api_entry(domain_entry)
                entries_by_id[api_entry.entry_id] = api_entry
    except Exception:
        # Fail-closed for read path means "return less data but keep endpoint available".
        # API callers still receive fallback entries.
        logger.exception("Failed to collect entries from chat audit log")

    # Source 2: Fallback in-memory audit entries (tests / direct ingestion helpers).
    for entry in _audit_entries:
        entries_by_id[entry.entry_id] = entry

    return list(entries_by_id.values())


@router.get("/entries", response_model=List[AuditEntry])
async def list_audit_entries(
    strategy_id: Optional[str] = Query(None, description="Filter by strategy ID"),
    status: Optional[str] = Query(None, description="Filter by status"),
    event_type: Optional[str] = Query(None, description="Filter by event type"),
    llm_backend: Optional[str] = Query(None, description="Filter by llm backend"),
    since: Optional[str] = Query(None, description="Filter by start time (ISO format)"),
    until: Optional[str] = Query(None, description="Filter by end time (ISO format)"),
    limit: int = Query(100, ge=1, le=500, description="Max results"),
    offset: int = Query(0, ge=0, description="Offset for pagination"),
):
    """
    List AI audit entries (Task 9.6).

    Supports filtering by strategy_id/status/event_type/llm_backend/time-range.
    """
    since_dt = _parse_iso_range("since", since)
    until_dt = _parse_iso_range("until", until)
    if since_dt and until_dt and since_dt > until_dt:
        raise HTTPException(status_code=400, detail="since must be <= until")

    entries = await _collect_audit_entries()

    if strategy_id:
        entries = [entry for entry in entries if entry.strategy_id == strategy_id]
    if status:
        status_norm = status.strip().lower()
        entries = [entry for entry in entries if entry.status and entry.status.lower() == status_norm]
    if event_type:
        event_type_norm = event_type.strip().lower()
        entries = [
            entry
            for entry in entries
            if entry.event_type and entry.event_type.lower() == event_type_norm
        ]
    if llm_backend:
        backend_norm = llm_backend.strip().lower()
        entries = [
            entry for entry in entries if entry.llm_backend and entry.llm_backend.lower() == backend_norm
        ]

    if since_dt:
        entries = [
            entry
            for entry in entries
            if (entry_dt := _parse_entry_time(entry.created_at)) is not None and entry_dt >= since_dt
        ]
    if until_dt:
        entries = [
            entry
            for entry in entries
            if (entry_dt := _parse_entry_time(entry.created_at)) is not None and entry_dt <= until_dt
        ]

    entries.sort(key=lambda item: _parse_entry_time(item.created_at) or datetime.min.replace(tzinfo=timezone.utc), reverse=True)
    return entries[offset : offset + limit]


@router.get("/entries/{entry_id}", response_model=AuditEntry)
async def get_audit_entry(entry_id: str = Path(..., description="Audit entry ID")):
    """
    Get a specific audit entry by ID (Task 9.6).
    """
    entries = await _collect_audit_entries()
    for entry in entries:
        if entry.entry_id == entry_id:
            return entry
    raise HTTPException(status_code=404, detail=f"Audit entry {entry_id} not found")


def add_audit_entry(entry: AuditEntry) -> None:
    """Add an audit entry to fallback in-memory storage."""
    _audit_entries[:] = [existing for existing in _audit_entries if existing.entry_id != entry.entry_id]
    _audit_entries.append(entry)


def clear_audit_entries() -> None:
    """Clear fallback in-memory audit storage (tests helper)."""
    _audit_entries.clear()
