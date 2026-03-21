"""
Unit tests for Reconciler
"""
from datetime import datetime, timezone, timedelta
import pytest

from trader.core.application.reconciler import (
    Reconciler,
    DriftType,
    LocalOrderSnapshot,
    ExchangeOrderSnapshot,
    ReconcileReport,
)


def make_local(cl_ord_id: str, status: str, created_at: datetime = None, filled_quantity: str = "0.0", **kwargs) -> LocalOrderSnapshot:
    if created_at is None:
        created_at = datetime.now(timezone.utc) - timedelta(seconds=120)
    return LocalOrderSnapshot(
        cl_ord_id=cl_ord_id,
        status=status,
        symbol="BTCUSDT",
        quantity="1.0",
        filled_quantity=filled_quantity,
        created_at=created_at,
        updated_at=datetime.now(timezone.utc),
        **kwargs,
    )


def make_exchange(cl_ord_id: str, status: str, filled_quantity: str = "0.0", **kwargs) -> ExchangeOrderSnapshot:
    return ExchangeOrderSnapshot(
        cl_ord_id=cl_ord_id,
        status=status,
        symbol="BTCUSDT",
        quantity="1.0",
        filled_quantity=filled_quantity,
        updated_at=datetime.now(timezone.utc),
        **kwargs,
    )


class TestReconcilerNoDrift:
    def test_no_orders(self):
        r = Reconciler()
        report = r.reconcile([], [])
        assert report.total_orders_checked == 0
        assert report.ghost_count == 0
        assert report.phantom_count == 0
        assert report.diverged_count == 0

    def test_matching_orders(self):
        r = Reconciler()
        local = [make_local("order-1", "SUBMITTED")]
        exchange = [make_exchange("order-1", "SUBMITTED")]
        report = r.reconcile(local, exchange)
        assert report.total_orders_checked == 2
        assert len(report.drifts) == 0

    def test_matching_orders_with_fill(self):
        r = Reconciler()
        local = [make_local("order-1", "FILLED", filled_quantity="1.0")]
        exchange = [make_exchange("order-1", "FILLED", filled_quantity="1.0")]
        report = r.reconcile(local, exchange)
        assert len(report.drifts) == 0


class TestReconcilerGhost:
    def test_ghost_order_detected(self):
        r = Reconciler(grace_period_sec=10.0)
        local = [make_local("order-1", "SUBMITTED", created_at=datetime.now(timezone.utc) - timedelta(seconds=20))]
        report = r.reconcile(local, [])
        assert report.ghost_count == 1
        assert len(report.drifts) == 1
        assert report.drifts[0].drift_type == DriftType.GHOST
        assert report.drifts[0].cl_ord_id == "order-1"

    def test_ghost_within_grace_period(self):
        r = Reconciler(grace_period_sec=60.0)
        local = [make_local("order-1", "SUBMITTED", created_at=datetime.now(timezone.utc) - timedelta(seconds=30))]
        report = r.reconcile(local, [])
        assert report.ghost_count == 1
        assert report.drifts[0].grace_period_remaining_sec is not None
        assert report.drifts[0].grace_period_remaining_sec > 0


class TestReconcilerPhantom:
    def test_phantom_order_detected(self):
        r = Reconciler()
        exchange = [make_exchange("order-1", "SUBMITTED")]
        report = r.reconcile([], exchange)
        assert report.phantom_count == 1
        assert len(report.drifts) == 1
        assert report.drifts[0].drift_type == DriftType.PHANTOM
        assert report.drifts[0].cl_ord_id == "order-1"


class TestReconcilerDiverged:
    def test_diverged_status(self):
        r = Reconciler(grace_period_sec=10.0)
        local = [make_local("order-1", "SUBMITTED", created_at=datetime.now(timezone.utc) - timedelta(seconds=20))]
        exchange = [make_exchange("order-1", "FILLED")]
        report = r.reconcile(local, exchange)
        assert report.diverged_count == 1
        assert report.drifts[0].drift_type == DriftType.DIVERGED

    def test_diverged_filled_quantity(self):
        r = Reconciler(grace_period_sec=10.0)
        local = [make_local(
            "order-1", "SUBMITTED",
            created_at=datetime.now(timezone.utc) - timedelta(seconds=20),
            filled_quantity="0.5"
        )]
        exchange = [make_exchange("order-1", "SUBMITTED", filled_quantity="1.0")]
        report = r.reconcile(local, exchange)
        assert report.diverged_count == 1
        assert report.drifts[0].filled_quantity == "0.5"
        assert report.drifts[0].exchange_filled_quantity == "1.0"

    def test_diverged_within_grace_period(self):
        r = Reconciler(grace_period_sec=60.0)
        local = [make_local("order-1", "SUBMITTED", created_at=datetime.now(timezone.utc) - timedelta(seconds=30))]
        exchange = [make_exchange("order-1", "FILLED")]
        report = r.reconcile(local, exchange)
        assert report.diverged_count == 1
        assert report.drifts[0].grace_period_remaining_sec is not None
        assert report.drifts[0].grace_period_remaining_sec > 0


class TestReconcilerMixed:
    def test_multiple_drift_types(self):
        r = Reconciler(grace_period_sec=10.0)
        now = datetime.now(timezone.utc)
        local = [
            make_local("order-1", "SUBMITTED", created_at=now - timedelta(seconds=20)),
            make_local("order-2", "FILLED", created_at=now - timedelta(seconds=20), filled_quantity="1.0"),
        ]
        exchange = [
            make_exchange("order-2", "SUBMITTED"),
            make_exchange("order-3", "OPEN"),
        ]
        report = r.reconcile(local, exchange)
        assert report.ghost_count == 1
        assert report.phantom_count == 1
        assert report.diverged_count == 1
        assert report.total_orders_checked == 4


class TestGracePeriodEdge:
    def test_beyond_grace_period(self):
        r = Reconciler(grace_period_sec=60.0)
        created = datetime.now(timezone.utc) - timedelta(seconds=120)
        local = [make_local("order-1", "SUBMITTED", created_at=created)]
        report = r.reconcile(local, [])
        assert report.ghost_count == 1
        assert report.drifts[0].grace_period_remaining_sec is None

    def test_grace_period_at_exactly_zero(self):
        """Test when grace_period_remaining_sec is exactly 0 - should be handled immediately."""
        r = Reconciler(grace_period_sec=60.0)
        # Order created exactly 60 seconds ago
        created = datetime.now(timezone.utc) - timedelta(seconds=60)
        local = [make_local("order-1", "SUBMITTED", created_at=created)]
        report = r.reconcile(local, [])
        assert report.ghost_count == 1
        assert report.drifts[0].grace_period_remaining_sec is not None
        assert report.drifts[0].grace_period_remaining_sec == 0.0

    def test_grace_period_just_before_expiry(self):
        """Test when grace_period_remaining_sec is just below 0 - should be None."""
        r = Reconciler(grace_period_sec=60.0)
        # Order created just over 60 seconds ago
        created = datetime.now(timezone.utc) - timedelta(seconds=60, milliseconds=1)
        local = [make_local("order-1", "SUBMITTED", created_at=created)]
        report = r.reconcile(local, [])
        assert report.ghost_count == 1
        assert report.drifts[0].grace_period_remaining_sec is None
