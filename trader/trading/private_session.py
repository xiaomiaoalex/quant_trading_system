"""Assemble private stream parsing, generation buffering and durable recovery.

The stream factory may construct the existing Binance PrivateStreamManager.
Coverage is a separate adapter port: a connected socket or REST receive time is
never substituted for proof that the recovery history is complete.
"""

import asyncio
import inspect
import time
from dataclasses import replace

from trader.adapters.binance.stream_base import StreamEvent, StreamState
from trader.services.trading_ingress import ExecutionIngress
from trader.trading.models import NotReady
from trader.trading.recovery import RecoveryCoordinator


class _GenerationSink:
    def __init__(self, session, generation):
        self.session = session
        self.generation = generation

    async def order(self, account_id, cl_ord_id):
        return await self.session.service.order(account_id, cl_ord_id)

    async def record_execution(self, execution):
        return await self.session.coordinator.ingest(self.generation, execution)

    async def observe(self, observation):
        return await self.session.coordinator.ingest(self.generation, observation)


class PrivateTradingSession:
    def __init__(
        self,
        *,
        service,
        account_id,
        stream_factory,
        snapshot_provider,
        on_block,
        on_ready,
        coverage_provider=None,
        exchange_clock=None,
        max_buffer=10_000,
    ):
        self.service = service
        self.account_id = account_id
        self.stream_factory = stream_factory
        self.snapshot_provider = snapshot_provider
        self.coverage_provider = coverage_provider
        # Coverage boundaries must come from the venue, never local receive time.
        if coverage_provider is not None and exchange_clock is None:
            raise ValueError("EXCHANGE_CLOCK_REQUIRED_FOR_COVERAGE")
        self._exchange_clock = exchange_clock or (lambda: int(time.time() * 1000))
        self._connected_from_ms = 0
        self._on_block = on_block
        self._on_ready = on_ready
        self.stream = None
        self.last_reason = "PRIVATE_STREAM_NOT_STARTED"
        self._lost = True
        self._lifecycle = asyncio.Lock()
        self.coordinator = RecoveryCoordinator(
            account_id,
            self._block,
            service.recover,
            service.record_execution,
            service.observe,
            self._finish_ready,
            max_buffer=max_buffer,
        )

    @property
    def ready(self):
        return self.coordinator.ready

    async def _block(self, reason):
        self.last_reason = reason
        await self._on_block(reason)

    async def _finish_ready(self):
        await self._on_ready()
        view = await self.service.risk_view(self.account_id)
        if not view.ready:
            raise NotReady("PRIVATE_RECOVERY_ACCOUNT_BLOCKED: " + ",".join(view.reasons))

    async def _loss(self, generation, reason):
        if generation != self.coordinator.generation:
            return
        self._lost = True
        self._connected_from_ms = 0
        await self.coordinator.begin()
        self.last_reason = str(reason)

    async def _now(self) -> int:
        value = self._exchange_clock()
        if inspect.isawaitable(value):
            value = await value
        return int(value)

    async def _replace_stream(self):
        # Retire callbacks before stopping the old connection: even delayed
        # dispatches retain their immutable old-generation sink.
        generation = await self.coordinator.begin()
        old = self.stream
        self.stream = None
        if old is not None:
            await old.stop()
        stream = self.stream_factory()
        self.stream = stream
        self._lost = False
        ingress = ExecutionIngress(_GenerationSink(self, generation), account_id=self.account_id)

        async def fill(update):
            try:
                result = await ingress.on_fill(update)
                if not result:
                    await self._loss(generation, "PRIVATE_FILL_UNUSABLE")
                return result
            except Exception:
                await self._loss(generation, "PRIVATE_FILL_APPLY_FAILED")
                raise

        async def order(update):
            try:
                result = await ingress.on_order_update(update)
                if not result:
                    await self._loss(generation, "PRIVATE_ORDER_UNUSABLE")
                return result
            except Exception:
                await self._loss(generation, "PRIVATE_ORDER_APPLY_FAILED")
                raise

        async def lost(event, data=None):
            await self._loss(generation, getattr(event, "value", str(event)))

        stream.register_fill_handler(fill)
        stream.register_order_handler(order)
        for event in (
            StreamEvent.DISCONNECTED,
            StreamEvent.RECONNECT,
            StreamEvent.STALE_DETECTED,
            StreamEvent.ERROR,
        ):
            stream.register_handler(event, lost)
        try:
            await stream.start()
            self._connected_from_ms = await self._now()
        except BaseException:
            await self._loss(generation, "PRIVATE_STREAM_START_FAILED")
            raise

    async def _align(self):
        generation = self.coordinator.generation
        try:
            first = await self.snapshot_provider(self.account_id)
            last = await self.snapshot_provider(self.account_id)
            if first is None or last is None:
                raise NotReady("PRIVATE_RECOVERY_SNAPSHOT_MISSING")
            proof = None
            if self.coverage_provider is not None:
                buffered_through = max(self._connected_from_ms, self.coordinator.buffered_through)
                proof = await self.coverage_provider(
                    generation,
                    first,
                    last,
                    self._connected_from_ms,
                    buffered_through,
                )
            if self.stream.state != StreamState.CONNECTED:
                raise NotReady("PRIVATE_STREAM_DISCONNECTED")
            # The proof check remains inside the coordinator; replacing this
            # health bit alone can never enable READY when coverage is missing.
            await self.coordinator.finish(
                generation,
                replace(first, stream_healthy=True),
                replace(last, stream_healthy=True),
                proof,
            )
        except NotReady as exc:
            self.last_reason = str(exc)
            return False
        except BaseException:
            await self._loss(generation, "PRIVATE_RECOVERY_FAILED")
            raise
        self.last_reason = ""
        return self.ready

    async def start(self):
        async with self._lifecycle:
            if self.ready and not self._lost:
                return True
            if self.stream is None or self._lost:
                await self._replace_stream()
            return await self._align()

    async def align(self):
        async with self._lifecycle:
            if self.stream is None or self._lost:
                await self._replace_stream()
            elif self.ready:
                await self.coordinator.pause_alignment()
            return await self._align()

    async def reconnect(self):
        async with self._lifecycle:
            await self._replace_stream()
            return await self._align()

    async def stop(self):
        async with self._lifecycle:
            await self.coordinator.begin()
            self._lost = True
            stream, self.stream = self.stream, None
            if stream is not None:
                await stream.stop()
