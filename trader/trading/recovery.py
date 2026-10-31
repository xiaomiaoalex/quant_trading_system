"""Generation-scoped recovery barrier; exchange coverage is an explicit port input.

This module cannot manufacture coverage for a venue. Its caller must derive the
cursor proof from an authoritative continuity source, never local receive time.
If the adapter cannot prove coverage it must supply None and remain blocked.
"""

import asyncio
import json
from dataclasses import dataclass, replace
from typing import Awaitable, Callable

from .models import Execution, NotReady, OrderObservation, ReconcileSnapshot, json_value


@dataclass(frozen=True)
class RecoveryCoverage:
    stream_generation: int
    source: str
    connected_from: int
    covered_through: int
    buffered_through: int
    contiguous: bool


def _fact_time(fact: "Execution | OrderObservation") -> int:
    if isinstance(fact, Execution):
        return int(fact.occurred_at_ms)
    return int(fact.observed_at_ms)


def _snapshot_economics(snapshot: ReconcileSnapshot) -> str:
    """Compare the ledger-affecting state of two snapshots.

    Live mark prices are deliberately excluded: they move between two
    consecutive collections and would block recovery forever. Only the
    presence of a valuation symbol is compared, so a missing mark still
    marks the collection as unstable.
    """
    value = {
        "balances": snapshot.balances,
        "external_locked": snapshot.external_locked,
        "orders": sorted(
            (o.cl_ord_id, o.status, str(o.cumulative_qty), o.broker_order_id)
            for o in snapshot.open_orders
        ),
        "executions": sorted(
            (e.key, json.dumps(e.content, sort_keys=True)) for e in snapshot.executions
        ),
        "mark_symbols": sorted(snapshot.marks),
    }
    return json.dumps(json_value(value), sort_keys=True)


class RecoveryCoordinator:
    def __init__(
        self,
        account_id: str,
        on_block: Callable[[str], Awaitable[None]],
        apply_snapshot: Callable[[ReconcileSnapshot], Awaitable[None]],
        apply_execution: Callable[[Execution], Awaitable[None]],
        apply_observation: Callable[[OrderObservation], Awaitable[None]],
        on_ready: Callable[[], Awaitable[None]],
        *,
        max_buffer: int = 10_000,
    ):
        if max_buffer <= 0:
            raise ValueError("Recovery buffer must be positive")
        self.account_id = account_id
        self.on_block = on_block
        self.apply_snapshot = apply_snapshot
        self.apply_execution = apply_execution
        self.apply_observation = apply_observation
        self.on_ready = on_ready
        self.max_buffer = max_buffer
        self.generation = 0
        self.ready = False
        self._overflow = False
        self._buffer: list[Execution | OrderObservation] = []
        self._lock = asyncio.Lock()

    async def begin(self) -> int:
        async with self._lock:
            self.ready = False
            self.generation += 1
            self._overflow = False
            self._buffer.clear()
            await self.on_block("PRIVATE_STREAM_RECOVERING")
            return self.generation

    async def pause_alignment(self) -> int:
        """Buffer a fresh REST alignment without changing a continuous socket epoch."""
        async with self._lock:
            if self.ready:
                self.ready = False
                self._buffer.clear()
            await self.on_block("PRIVATE_STREAM_ALIGNING")
            return self.generation

    async def ingest(self, generation: int, fact: Execution | OrderObservation) -> bool:
        async with self._lock:
            if generation != self.generation:
                return False
            if fact.account_id != self.account_id:
                raise ValueError("Recovery account mismatch")
            if self.ready:
                try:
                    await self._apply(fact)
                except BaseException:
                    self.ready = False
                    await self.on_block("PRIVATE_STREAM_APPLY_FAILED")
                    raise
            else:
                if len(self._buffer) >= self.max_buffer:
                    self._overflow = True
                    await self.on_block("PRIVATE_STREAM_BUFFER_OVERFLOW")
                    raise NotReady("PRIVATE_STREAM_BUFFER_OVERFLOW")
                self._buffer.append(fact)
            return True

    @property
    def buffered_through(self) -> int:
        """Latest exchange timestamp already buffered; 0 when the buffer is empty."""
        return max((_fact_time(fact) for fact in self._buffer), default=0)

    async def _apply(self, fact: Execution | OrderObservation) -> None:
        if isinstance(fact, Execution):
            await self.apply_execution(fact)
        else:
            await self.apply_observation(fact)

    async def finish(
        self,
        generation: int,
        first: ReconcileSnapshot,
        last: ReconcileSnapshot,
        proof: RecoveryCoverage | None,
    ) -> None:
        async with self._lock:
            if self.ready:
                raise NotReady("RECOVERY_ALREADY_READY")
            if generation != self.generation or self._overflow:
                raise NotReady("RECOVERY_GENERATION_OR_OVERFLOW")
            if (
                proof is None
                or proof.stream_generation != generation
                or not proof.source
                or not proof.contiguous
                or not proof.connected_from <= proof.buffered_through <= proof.covered_through
                or self.buffered_through > proof.covered_through
            ):
                raise NotReady("PRIVATE_STREAM_COVERAGE_UNPROVEN")
            if (
                first.account_id != self.account_id
                or last.account_id != self.account_id
                or not first.complete
                or not last.complete
                or not first.stream_healthy
                or not last.stream_healthy
                or _snapshot_economics(first) != _snapshot_economics(last)
            ):
                raise NotReady("RECOVERY_SNAPSHOT_UNSTABLE")
            try:
                # Keep the account blocked while replaying; never restore the
                # REST balances again after applying buffered execution facts.
                await self.apply_snapshot(replace(last, stream_healthy=False))
                seen = set()
                for fact in self._buffer:
                    content = json.dumps(json_value(fact), sort_keys=True)
                    if content not in seen:
                        await self._apply(fact)
                        seen.add(content)
                await self.on_ready()
            except BaseException:
                self.ready = False
                await self.on_block("RECOVERY_APPLY_FAILED")
                raise
            self._buffer.clear()
            self.ready = True
