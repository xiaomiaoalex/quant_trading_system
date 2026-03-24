"""
Integration Tests - ReconcilerService with Storage Layer
========================================================
Tests for ReconcilerService integration with InMemoryStorage,
including drift event persistence, handler registration, and
periodic reconciliation loop.

These tests verify the complete flow from drift detection
through event publication to storage.
"""
import asyncio
from datetime import datetime, timezone, timedelta
from typing import List, Dict, Any
import pytest

from trader.services.reconciler_service import ReconcilerService
from trader.storage.in_memory import ControlPlaneInMemoryStorage, get_storage, reset_storage
from trader.core.application.reconciler import DriftType


def _safe_stop_service(service) -> None:
    """
    安全停止服务，处理 event loop 已关闭的情况

    在 pytest 环境中，teardown 时 event loop 可能已被关闭，
    直接使用 run_until_complete 会抛出 RuntimeError。
    """
    if service._running:
        try:
            loop = asyncio.get_event_loop()
            if not loop.is_closed():
                loop.run_until_complete(service.stop())
        except RuntimeError:
            # Loop 已关闭，忽略
            service._running = False


class TestReconcilerServiceStorageIntegration:
    """Test ReconcilerService integration with storage layer."""

    def setup_method(self):
        """Setup fresh storage and service for each test."""
        self.storage = reset_storage()
        self.service = ReconcilerService(storage=self.storage)

    def teardown_method(self):
        """Cleanup service."""
        _safe_stop_service(self.service)

    def _make_local_orders(self, orders: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
        """Helper to create local order format."""
        return [
            {
                "client_order_id": o["cl_ord_id"],
                "status": o["status"],
                "symbol": o.get("symbol", "BTCUSDT"),
                "quantity": o.get("quantity", "1.0"),
                "filled_quantity": o.get("filled_quantity", "0.0"),
                "created_at": o.get("created_at", datetime.now(timezone.utc)),
                "updated_at": o.get("updated_at", datetime.now(timezone.utc)),
            }
            for o in orders
        ]

    def _make_exchange_orders(self, orders: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
        """Helper to create exchange order format."""
        return [
            {
                "client_order_id": o["cl_ord_id"],
                "status": o["status"],
                "symbol": o.get("symbol", "BTCUSDT"),
                "quantity": o.get("quantity", "1.0"),
                "filled_quantity": o.get("filled_quantity", "0.0"),
                "updated_at": o.get("updated_at", datetime.now(timezone.utc)),
            }
            for o in orders
        ]

    @pytest.mark.asyncio
    async def test_trigger_reconcile_publishes_drift_event_to_storage(self):
        """Test that drift events are published to storage when reconcile detects drifts."""
        # Setup: Local has order that doesn't exist on exchange (GHOST)
        old_time = datetime.now(timezone.utc) - timedelta(seconds=120)
        local_orders = self._make_local_orders([
            {
                "cl_ord_id": "ghost-order-1",
                "status": "SUBMITTED",
                "created_at": old_time,
            }
        ])
        exchange_orders = self._make_exchange_orders([])

        async def local_getter():
            return local_orders

        async def exchange_getter():
            return exchange_orders

        # Execute reconciliation
        report = await self.service.trigger_reconcile(local_getter, exchange_getter)

        # Verify report has ghost drift
        assert report.ghost_count == 1
        assert report.drifts[0].cl_ord_id == "ghost-order-1"
        assert report.drifts[0].drift_type == DriftType.GHOST

        # Verify drift event was published to storage
        events = self.storage.list_events(stream_key="order_drifts")
        assert len(events) == 1
        assert events[0]["event_type"] == "ORDER_DRIFT_DETECTED"
        assert events[0]["aggregate_id"] == "ghost-order-1"
        assert events[0]["data"]["drift_type"] == "GHOST"

    @pytest.mark.asyncio
    async def test_trigger_reconcile_publishes_phantom_drift_event(self):
        """Test that PHANTOM drifts (exchange-only orders) trigger event publication."""
        # Setup: Exchange has order that doesn't exist locally
        exchange_orders = self._make_exchange_orders([
            {
                "cl_ord_id": "phantom-order-1",
                "status": "OPEN",
            }
        ])
        local_orders = self._make_local_orders([])

        async def local_getter():
            return local_orders

        async def exchange_getter():
            return exchange_orders

        # Execute reconciliation
        report = await self.service.trigger_reconcile(local_getter, exchange_getter)

        # Verify report has phantom drift (PHANTOM has no grace period, handled immediately)
        assert report.phantom_count == 1
        assert report.drifts[0].drift_type == DriftType.PHANTOM

        # Verify event was published
        events = self.storage.list_events(stream_key="order_drifts")
        assert len(events) == 1
        assert events[0]["data"]["drift_type"] == "PHANTOM"

    @pytest.mark.asyncio
    async def test_trigger_reconcile_publishes_diverged_drift_event(self):
        """Test that DIVERGED drifts (status mismatch) trigger event publication."""
        old_time = datetime.now(timezone.utc) - timedelta(seconds=120)
        local_orders = self._make_local_orders([
            {
                "cl_ord_id": "diverged-order-1",
                "status": "SUBMITTED",
                "filled_quantity": "0.5",
                "created_at": old_time,
            }
        ])
        exchange_orders = self._make_exchange_orders([
            {
                "cl_ord_id": "diverged-order-1",
                "status": "FILLED",
                "filled_quantity": "1.0",
            }
        ])

        async def local_getter():
            return local_orders

        async def exchange_getter():
            return exchange_orders

        # Execute reconciliation
        report = await self.service.trigger_reconcile(local_getter, exchange_getter)

        # Verify report has diverged drift
        assert report.diverged_count == 1
        assert report.drifts[0].drift_type == DriftType.DIVERGED

        # Verify event was published
        events = self.storage.list_events(stream_key="order_drifts")
        assert len(events) == 1
        assert events[0]["data"]["drift_type"] == "DIVERGED"

    @pytest.mark.asyncio
    async def test_no_drift_no_event_published(self):
        """Test that when no drifts are detected, no events are published."""
        local_orders = self._make_local_orders([
            {
                "cl_ord_id": "matched-order-1",
                "status": "FILLED",
                "filled_quantity": "1.0",
                "created_at": datetime.now(timezone.utc),
            }
        ])
        exchange_orders = self._make_exchange_orders([
            {
                "cl_ord_id": "matched-order-1",
                "status": "FILLED",
                "filled_quantity": "1.0",
            }
        ])

        async def local_getter():
            return local_orders

        async def exchange_getter():
            return exchange_orders

        # Execute reconciliation
        report = await self.service.trigger_reconcile(local_getter, exchange_getter)

        # Verify no drifts
        assert report.ghost_count == 0
        assert report.phantom_count == 0
        assert report.diverged_count == 0

        # Verify no events published
        events = self.storage.list_events(stream_key="order_drifts")
        assert len(events) == 0

    @pytest.mark.asyncio
    async def test_grace_period_blocks_event_publication(self):
        """Test that orders within grace period do NOT trigger immediate event publication."""
        # Setup: Recent order (within 60s grace period)
        recent_time = datetime.now(timezone.utc) - timedelta(seconds=30)
        local_orders = self._make_local_orders([
            {
                "cl_ord_id": "recent-ghost-order",
                "status": "SUBMITTED",
                "created_at": recent_time,
            }
        ])
        exchange_orders = self._make_exchange_orders([])

        async def local_getter():
            return local_orders

        async def exchange_getter():
            return exchange_orders

        # Execute reconciliation
        report = await self.service.trigger_reconcile(local_getter, exchange_getter)

        # Verify ghost detected but within grace period
        assert report.ghost_count == 1
        assert report.drifts[0].grace_period_remaining_sec is not None
        assert report.drifts[0].grace_period_remaining_sec > 0

        # Verify NO event was published (grace period blocks it)
        events = self.storage.list_events(stream_key="order_drifts")
        assert len(events) == 0

    @pytest.mark.asyncio
    async def test_drift_handler_called_on_publish(self):
        """Test that registered drift handlers are called when drift is published."""
        handler_called = []

        async def test_handler(drift):
            handler_called.append(drift.cl_ord_id)

        self.service.register_drift_handler(test_handler)

        old_time = datetime.now(timezone.utc) - timedelta(seconds=120)
        local_orders = self._make_local_orders([
            {"cl_ord_id": "handler-test-order", "status": "SUBMITTED", "created_at": old_time}
        ])

        async def local_getter():
            return local_orders

        async def exchange_getter():
            return []

        await self.service.trigger_reconcile(local_getter, exchange_getter)

        # Verify handler was called
        assert len(handler_called) == 1
        assert handler_called[0] == "handler-test-order"

    @pytest.mark.asyncio
    async def test_multiple_drifts_publish_multiple_events(self):
        """Test that multiple drifts result in multiple events published."""
        old_time = datetime.now(timezone.utc) - timedelta(seconds=120)
        local_orders = self._make_local_orders([
            {"cl_ord_id": "ghost-1", "status": "SUBMITTED", "created_at": old_time},
            {"cl_ord_id": "ghost-2", "status": "SUBMITTED", "created_at": old_time},
        ])

        exchange_orders = self._make_exchange_orders([
            {"cl_ord_id": "phantom-1", "status": "OPEN"},
        ])

        async def local_getter():
            return local_orders

        async def exchange_getter():
            return exchange_orders

        report = await self.service.trigger_reconcile(local_getter, exchange_getter)

        # Verify 2 ghosts + 1 phantom = 3 drifts (all handled immediately due to age)
        assert report.ghost_count == 2
        assert report.phantom_count == 1

        # Verify 3 events published
        events = self.storage.list_events(stream_key="order_drifts")
        assert len(events) == 3

    @pytest.mark.asyncio
    async def test_get_last_report_returns_none_initially(self):
        """Test that get_last_report returns None before any reconciliation."""
        assert self.service.get_last_report() is None

    @pytest.mark.asyncio
    async def test_set_last_report_works(self):
        """Test that set_last_report stores the report."""
        old_time = datetime.now(timezone.utc) - timedelta(seconds=120)
        local_orders = self._make_local_orders([
            {"cl_ord_id": "test-order", "status": "SUBMITTED", "created_at": old_time}
        ])

        async def local_getter():
            return local_orders

        async def exchange_getter():
            return []

        await self.service.trigger_reconcile(local_getter, exchange_getter)

        # Verify report is stored
        report = self.service.get_last_report()
        assert report is not None
        assert report.ghost_count == 1

    @pytest.mark.asyncio
    async def test_phantom_always_handled_immediately(self):
        """Test that PHANTOM orders are always handled immediately regardless of age."""
        # PHANTOM orders have no created_at, so grace period doesn't apply
        exchange_orders = self._make_exchange_orders([
            {"cl_ord_id": "phantom-no-grace", "status": "OPEN"},
        ])

        async def local_getter():
            return self._make_local_orders([])

        async def exchange_getter():
            return exchange_orders

        report = await self.service.trigger_reconcile(local_getter, exchange_getter)

        # Verify phantom was handled immediately
        assert report.phantom_count == 1
        assert report.drifts[0].drift_type == DriftType.PHANTOM

        # Verify event was published
        events = self.storage.list_events(stream_key="order_drifts")
        assert len(events) == 1
        assert events[0]["data"]["drift_type"] == "PHANTOM"


class TestReconcilerServicePeriodicReconciliation:
    """Test ReconcilerService periodic reconciliation loop."""

    def setup_method(self):
        """Setup fresh storage and service for each test."""
        self.storage = reset_storage()
        # Use short interval for testing
        self.service = ReconcilerService(storage=self.storage, interval_sec=0.1)

    def teardown_method(self):
        """Cleanup service."""
        _safe_stop_service(self.service)

    @pytest.mark.asyncio
    async def test_start_and_stop(self):
        """Test service can be started and stopped."""
        assert not self.service._running
        assert self.service._task is None

        await self.service.start()
        assert self.service._running
        assert self.service._task is not None

        await self.service.stop()
        assert not self.service._running

    @pytest.mark.asyncio
    async def test_periodic_reconciliation_executes(self):
        """Test that periodic reconciliation runs at configured interval."""
        call_count = [0]
        old_time = datetime.now(timezone.utc) - timedelta(seconds=120)

        async def local_getter():
            call_count[0] += 1
            return [
                {
                    "client_order_id": f"periodic-order-{call_count[0]}",
                    "status": "SUBMITTED",
                    "symbol": "BTCUSDT",
                    "quantity": "1.0",
                    "filled_quantity": "0.0",
                    "created_at": old_time,
                    "updated_at": datetime.now(timezone.utc),
                }
            ]

        async def exchange_getter():
            return []

        self.service.configure_periodic_reconciliation(local_getter, exchange_getter)
        await self.service.start()

        # Wait for at least 2 cycles (interval=0.1s, wait 0.35s for buffer)
        await asyncio.sleep(0.35)

        await self.service.stop()

        # Verify reconciliation ran multiple times
        assert call_count[0] >= 2

        # Verify events were published
        events = self.storage.list_events(stream_key="order_drifts")
        assert len(events) >= 2

    @pytest.mark.asyncio
    async def test_stop_without_start_is_noop(self):
        """Test that stopping without starting doesn't raise."""
        await self.service.stop()  # Should not raise


class TestReconcilerServiceEdgeCases:
    """Test edge cases and error handling."""

    def setup_method(self):
        """Setup fresh storage and service for each test."""
        self.storage = reset_storage()
        self.service = ReconcilerService(storage=self.storage)

    @pytest.mark.asyncio
    async def test_handler_exception_does_not_propagate(self):
        """Test that handler exceptions are logged but don't stop processing."""
        async def failing_handler(drift):
            raise RuntimeError("Handler failed")

        self.service.register_drift_handler(failing_handler)

        old_time = datetime.now(timezone.utc) - timedelta(seconds=120)
        local_orders = [
            {
                "client_order_id": "robust-test-order",
                "status": "SUBMITTED",
                "symbol": "BTCUSDT",
                "quantity": "1.0",
                "filled_quantity": "0.0",
                "created_at": old_time,
                "updated_at": datetime.now(timezone.utc),
            }
        ]

        async def local_getter():
            return local_orders

        async def exchange_getter():
            return []

        # Should not raise despite handler failing
        report = await self.service.trigger_reconcile(local_getter, exchange_getter)

        # Report should still be generated
        assert report.ghost_count == 1

    @pytest.mark.asyncio
    async def test_register_drift_handler_validates_callable(self):
        """Test that register_drift_handler rejects non-callable handlers."""
        with pytest.raises(TypeError, match="handler must be callable"):
            self.service.register_drift_handler("not a callable")

    @pytest.mark.asyncio
    async def test_empty_orders_lists(self):
        """Test reconciliation with empty order lists."""
        async def local_getter():
            return []

        async def exchange_getter():
            return []

        report = await self.service.trigger_reconcile(local_getter, exchange_getter)

        assert report.total_orders_checked == 0
        assert report.ghost_count == 0
        assert report.phantom_count == 0
        assert report.diverged_count == 0

        events = self.storage.list_events(stream_key="order_drifts")
        assert len(events) == 0
