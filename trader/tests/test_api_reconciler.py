"""
Unit Tests - Reconciler API Endpoints
=====================================
Tests for FastAPI Reconciler endpoints using TestClient.
"""
import pytest
from fastapi.testclient import TestClient
from datetime import datetime, timezone, timedelta

from trader.api.main import app
from trader.api.routes import reconciler


class TestReconcilerAPIEndpoints:
    """Test Reconciler API endpoints."""

    def setup_method(self):
        """Setup for each test."""
        self.client = TestClient(app)
        # Reset global ReconcilerService and Reconciler singletons before each test
        reconciler.get_reconciler_service.cache_clear()
        reconciler.get_reconciler.cache_clear()

    def teardown_method(self):
        """Cleanup after each test."""
        reconciler.get_reconciler_service.cache_clear()
        reconciler.get_reconciler.cache_clear()

    def test_get_reconciler_report_no_report_available(self):
        """Test getting report when no reconciliation has been run."""
        response = self.client.get("/v1/reconciler/report")
        assert response.status_code == 404
        data = response.json()
        assert data["detail"] == "No reconciliation report available"

    def test_trigger_reconciliation_no_drifts(self):
        """Test triggering reconciliation with matching orders (no drifts)."""
        request_body = {
            "local_orders": [
                {
                    "client_order_id": "order-1",
                    "status": "FILLED",
                    "symbol": "BTCUSDT",
                    "quantity": "1.0",
                    "filled_quantity": "1.0",
                    "created_at": datetime.now(timezone.utc).isoformat(),
                    "updated_at": datetime.now(timezone.utc).isoformat(),
                }
            ],
            "exchange_orders": [
                {
                    "client_order_id": "order-1",
                    "status": "FILLED",
                    "symbol": "BTCUSDT",
                    "quantity": "1.0",
                    "filled_quantity": "1.0",
                    "updated_at": datetime.now(timezone.utc).isoformat(),
                }
            ],
        }

        response = self.client.post("/v1/reconciler/trigger", json=request_body)
        assert response.status_code == 200
        data = response.json()
        assert data["total_orders_checked"] == 2
        assert data["ghost_count"] == 0
        assert data["phantom_count"] == 0
        assert data["diverged_count"] == 0
        assert data["drifts"] == []

    def test_trigger_reconciliation_ghost_order(self):
        """Test triggering reconciliation with GHOST order (local exists, exchange doesn't)."""
        old_time = (datetime.now(timezone.utc) - timedelta(seconds=120)).isoformat()
        request_body = {
            "local_orders": [
                {
                    "client_order_id": "ghost-order-1",
                    "status": "SUBMITTED",
                    "symbol": "BTCUSDT",
                    "quantity": "1.0",
                    "filled_quantity": "0.0",
                    "created_at": old_time,
                    "updated_at": old_time,
                }
            ],
            "exchange_orders": [],
        }

        response = self.client.post("/v1/reconciler/trigger", json=request_body)
        assert response.status_code == 200
        data = response.json()
        assert data["ghost_count"] == 1
        assert data["phantom_count"] == 0
        assert data["diverged_count"] == 0
        assert len(data["drifts"]) == 1
        drift = data["drifts"][0]
        assert drift["cl_ord_id"] == "ghost-order-1"
        assert drift["drift_type"] == "GHOST"
        assert drift["local_status"] == "SUBMITTED"
        assert drift["exchange_status"] is None

    def test_trigger_reconciliation_phantom_order(self):
        """Test triggering reconciliation with PHANTOM order (exchange exists, local doesn't)."""
        request_body = {
            "local_orders": [],
            "exchange_orders": [
                {
                    "client_order_id": "phantom-order-1",
                    "status": "OPEN",
                    "symbol": "BTCUSDT",
                    "quantity": "1.0",
                    "filled_quantity": "0.0",
                    "updated_at": datetime.now(timezone.utc).isoformat(),
                }
            ],
        }

        response = self.client.post("/v1/reconciler/trigger", json=request_body)
        assert response.status_code == 200
        data = response.json()
        assert data["phantom_count"] == 1
        assert data["drifts"][0]["drift_type"] == "PHANTOM"

    def test_trigger_reconciliation_diverged_order(self):
        """Test triggering reconciliation with DIVERGED order (status mismatch)."""
        old_time = (datetime.now(timezone.utc) - timedelta(seconds=120)).isoformat()
        request_body = {
            "local_orders": [
                {
                    "client_order_id": "diverged-order-1",
                    "status": "SUBMITTED",
                    "symbol": "BTCUSDT",
                    "quantity": "1.0",
                    "filled_quantity": "0.5",
                    "created_at": old_time,
                    "updated_at": old_time,
                }
            ],
            "exchange_orders": [
                {
                    "client_order_id": "diverged-order-1",
                    "status": "FILLED",
                    "symbol": "BTCUSDT",
                    "quantity": "1.0",
                    "filled_quantity": "1.0",
                    "updated_at": datetime.now(timezone.utc).isoformat(),
                }
            ],
        }

        response = self.client.post("/v1/reconciler/trigger", json=request_body)
        assert response.status_code == 200
        data = response.json()
        assert data["diverged_count"] == 1
        drift = data["drifts"][0]
        assert drift["drift_type"] == "DIVERGED"
        assert drift["local_status"] == "SUBMITTED"
        assert drift["exchange_status"] == "FILLED"
        assert drift["filled_quantity"] == "0.5"
        assert drift["exchange_filled_quantity"] == "1.0"

    def test_get_report_after_trigger(self):
        """Test that getting report after trigger returns the stored report."""
        # First trigger a reconciliation
        old_time = (datetime.now(timezone.utc) - timedelta(seconds=120)).isoformat()
        request_body = {
            "local_orders": [
                {
                    "client_order_id": "test-order-1",
                    "status": "SUBMITTED",
                    "symbol": "BTCUSDT",
                    "quantity": "1.0",
                    "filled_quantity": "0.0",
                    "created_at": old_time,
                    "updated_at": old_time,
                }
            ],
            "exchange_orders": [],
        }

        trigger_response = self.client.post("/v1/reconciler/trigger", json=request_body)
        assert trigger_response.status_code == 200

        # Then get the report
        report_response = self.client.get("/v1/reconciler/report")
        assert report_response.status_code == 200
        data = report_response.json()
        assert data["ghost_count"] == 1
        assert data["drifts"][0]["cl_ord_id"] == "test-order-1"

    def test_multiple_drifts_in_single_reconciliation(self):
        """Test reconciliation with multiple drift types in one call."""
        old_time = (datetime.now(timezone.utc) - timedelta(seconds=120)).isoformat()
        request_body = {
            "local_orders": [
                {
                    "client_order_id": "ghost-1",
                    "status": "SUBMITTED",
                    "symbol": "BTCUSDT",
                    "quantity": "1.0",
                    "filled_quantity": "0.0",
                    "created_at": old_time,
                    "updated_at": old_time,
                },
                {
                    "client_order_id": "local-matched",
                    "status": "FILLED",
                    "symbol": "ETHUSDT",
                    "quantity": "2.0",
                    "filled_quantity": "2.0",
                    "created_at": old_time,
                    "updated_at": old_time,
                },
            ],
            "exchange_orders": [
                {
                    "client_order_id": "local-matched",
                    "status": "FILLED",
                    "symbol": "ETHUSDT",
                    "quantity": "2.0",
                    "filled_quantity": "2.0",
                    "updated_at": datetime.now(timezone.utc).isoformat(),
                },
                {
                    "client_order_id": "phantom-1",
                    "status": "OPEN",
                    "symbol": "BNBUSDT",
                    "quantity": "10.0",
                    "filled_quantity": "0.0",
                    "updated_at": datetime.now(timezone.utc).isoformat(),
                },
            ],
        }

        response = self.client.post("/v1/reconciler/trigger", json=request_body)
        assert response.status_code == 200
        data = response.json()
        assert data["ghost_count"] == 1
        assert data["phantom_count"] == 1
        assert data["diverged_count"] == 0
        assert data["total_orders_checked"] == 4
        assert len(data["drifts"]) == 2

    def test_grace_period_within_grace(self):
        """Test that recent orders within grace period are reported but grace_period_remaining_sec > 0."""
        recent_time = (datetime.now(timezone.utc) - timedelta(seconds=30)).isoformat()
        request_body = {
            "local_orders": [
                {
                    "client_order_id": "recent-ghost",
                    "status": "SUBMITTED",
                    "symbol": "BTCUSDT",
                    "quantity": "1.0",
                    "filled_quantity": "0.0",
                    "created_at": recent_time,
                    "updated_at": recent_time,
                }
            ],
            "exchange_orders": [],
        }

        response = self.client.post("/v1/reconciler/trigger", json=request_body)
        assert response.status_code == 200
        data = response.json()
        assert data["ghost_count"] == 1
        assert data["within_grace_period_count"] == 1
        drift = data["drifts"][0]
        assert drift["grace_period_remaining_sec"] is not None
        assert drift["grace_period_remaining_sec"] > 0

    def test_grace_period_beyond_grace(self):
        """Test that old orders beyond grace period have grace_period_remaining_sec = None."""
        old_time = (datetime.now(timezone.utc) - timedelta(seconds=120)).isoformat()
        request_body = {
            "local_orders": [
                {
                    "client_order_id": "old-ghost",
                    "status": "SUBMITTED",
                    "symbol": "BTCUSDT",
                    "quantity": "1.0",
                    "filled_quantity": "0.0",
                    "created_at": old_time,
                    "updated_at": old_time,
                }
            ],
            "exchange_orders": [],
        }

        response = self.client.post("/v1/reconciler/trigger", json=request_body)
        assert response.status_code == 200
        data = response.json()
        assert data["ghost_count"] == 1
        assert data["within_grace_period_count"] == 0
        drift = data["drifts"][0]
        assert drift["grace_period_remaining_sec"] is None

    def test_trigger_with_empty_orders(self):
        """Test triggering reconciliation with empty order lists."""
        request_body = {
            "local_orders": [],
            "exchange_orders": [],
        }

        response = self.client.post("/v1/reconciler/trigger", json=request_body)
        assert response.status_code == 200
        data = response.json()
        assert data["total_orders_checked"] == 0
        assert data["ghost_count"] == 0
        assert data["phantom_count"] == 0
        assert data["diverged_count"] == 0
        assert data["drifts"] == []

    def test_trigger_with_optional_fields_missing(self):
        """Test that missing optional fields are handled gracefully."""
        request_body = {
            "local_orders": [
                {
                    "client_order_id": "minimal-order",
                    "status": "NEW",
                }
            ],
            "exchange_orders": [],
        }

        response = self.client.post("/v1/reconciler/trigger", json=request_body)
        assert response.status_code == 200
        data = response.json()
        # Minimal order should still be processed
        assert data["total_orders_checked"] == 1

    def test_response_timestamp_format(self):
        """Test that response timestamp is in ISO format."""
        request_body = {
            "local_orders": [
                {
                    "client_order_id": "order-ts-test",
                    "status": "FILLED",
                    "filled_quantity": "1.0",
                    "created_at": datetime.now(timezone.utc).isoformat(),
                    "updated_at": datetime.now(timezone.utc).isoformat(),
                }
            ],
            "exchange_orders": [
                {
                    "client_order_id": "order-ts-test",
                    "status": "FILLED",
                    "filled_quantity": "1.0",
                    "updated_at": datetime.now(timezone.utc).isoformat(),
                }
            ],
        }

        response = self.client.post("/v1/reconciler/trigger", json=request_body)
        assert response.status_code == 200
        data = response.json()
        # Verify timestamp is parseable as ISO format
        assert "T" in data["timestamp"]  # ISO format contains 'T'
        assert "+" in data["timestamp"] or "Z" in data["timestamp"]  # Contains timezone

    def test_report_persistence_across_calls(self):
        """Test that report is persisted and retrievable after multiple triggers."""
        old_time = (datetime.now(timezone.utc) - timedelta(seconds=120)).isoformat()

        # First trigger - ghost order
        request_body_1 = {
            "local_orders": [
                {
                    "client_order_id": "first-order",
                    "status": "SUBMITTED",
                    "created_at": old_time,
                    "updated_at": old_time,
                }
            ],
            "exchange_orders": [],
        }
        self.client.post("/v1/reconciler/trigger", json=request_body_1)

        # Second trigger - phantom order (should overwrite report)
        request_body_2 = {
            "local_orders": [],
            "exchange_orders": [
                {
                    "client_order_id": "second-order",
                    "status": "OPEN",
                    "updated_at": datetime.now(timezone.utc).isoformat(),
                }
            ],
        }
        self.client.post("/v1/reconciler/trigger", json=request_body_2)

        # Get report - should have phantom from second trigger
        response = self.client.get("/v1/reconciler/report")
        data = response.json()
        assert data["phantom_count"] == 1
        assert data["ghost_count"] == 0
        assert data["drifts"][0]["cl_ord_id"] == "second-order"


def test_matches_client_order_id_prefix_allows_all_when_prefixes_empty():
    assert reconciler._matches_client_order_id_prefix("any-order", []) is True
    assert reconciler._matches_client_order_id_prefix(None, []) is True


def test_matches_client_order_id_prefix_filters_by_prefixes():
    prefixes = ["fire_test_", "mybot_"]
    assert reconciler._matches_client_order_id_prefix("fire_test_123", prefixes) is True
    assert reconciler._matches_client_order_id_prefix("mybot_abc", prefixes) is True
    assert reconciler._matches_client_order_id_prefix("legacy_order_1", prefixes) is False
    assert reconciler._matches_client_order_id_prefix(None, prefixes) is False
