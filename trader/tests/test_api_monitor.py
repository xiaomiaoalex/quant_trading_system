"""
Unit Tests - Monitor API Endpoints
=================================
Tests for FastAPI Monitor endpoints using TestClient.
"""
import pytest
from fastapi.testclient import TestClient

from trader.api.main import app
from trader.api.routes import monitor


class TestMonitorEndpoints:
    """Test monitor API endpoints"""

    def setup_method(self):
        """Setup for each test"""
        self.client = TestClient(app)
        # Reset global MonitorService singleton before each test to avoid state pollution
        monitor._monitor_service = None

    def teardown_method(self):
        """Cleanup after each test"""
        # Reset global MonitorService and PortfolioService singletons to clean up state
        monitor._monitor_service = None
        monitor._portfolio_service = None

    def test_get_monitor_snapshot_default(self):
        """Test getting default monitor snapshot"""
        response = self.client.get("/v1/monitor/snapshot")
        assert response.status_code == 200
        data = response.json()
        assert "timestamp" in data
        assert data["total_positions"] == 0
        assert data["total_exposure"] == "0"
        assert data["open_orders_count"] == 0
        assert data["pending_orders_count"] == 0
        assert data["daily_pnl"] == "0"
        assert data["killswitch_level"] == 0
        assert data["killswitch_scope"] == "GLOBAL"
        assert data["adapters"] == {}
        assert data["active_alerts"] == []
        assert data["alert_count_by_severity"] == {}

    def test_get_monitor_snapshot_with_values(self):
        """Test getting monitor snapshot with query parameters"""
        # Note: positions are not fetched from OMS yet (positions=None is hardcoded in route)
        # TODO: Integrate with OMS/PortfolioService to get real positions
        response = self.client.get(
            "/v1/monitor/snapshot",
            params={
                "open_orders_count": 10,
                "pending_orders_count": 3,
                "daily_pnl": "-500.5",
                "daily_pnl_pct": "-2.5",
                "realized_pnl": "1000",
                "unrealized_pnl": "-500",
                "killswitch_level": 1,
                "killswitch_scope": "GLOBAL",
            },
        )
        assert response.status_code == 200
        data = response.json()
        assert data["total_positions"] == 0  # positions not yet integrated
        assert data["open_orders_count"] == 10
        assert data["pending_orders_count"] == 3
        assert data["daily_pnl"] == "-500.5"
        assert data["daily_pnl_pct"] == "-2.5"
        assert data["realized_pnl"] == "1000"
        assert data["unrealized_pnl"] == "-500"
        assert data["killswitch_level"] == 1

    def test_get_active_alerts_empty(self):
        """Test getting active alerts when none exist"""
        response = self.client.get("/v1/monitor/alerts")
        assert response.status_code == 200
        data = response.json()
        assert isinstance(data, list)
        assert len(data) == 0

    def test_add_alert_rule(self):
        """Test adding an alert rule"""
        rule = {
            "rule_name": "test_rule",
            "metric_key": "daily_pnl",
            "threshold": -1000.0,
            "comparison": "lt",
            "severity": "HIGH",
            "cooldown_seconds": 60,
        }
        response = self.client.post("/v1/monitor/rules", json=rule)
        assert response.status_code == 200
        data = response.json()
        assert data["rule_name"] == "test_rule"
        assert data["metric_key"] == "daily_pnl"
        assert data["threshold"] == -1000.0
        assert data["comparison"] == "lt"
        assert data["severity"] == "HIGH"
        assert data["cooldown_seconds"] == 60

    def test_add_and_remove_alert_rule(self):
        """Test adding and removing an alert rule"""
        rule = {
            "rule_name": "temp_rule",
            "metric_key": "open_orders_count",
            "threshold": 50.0,
            "comparison": "gt",
            "severity": "MEDIUM",
            "cooldown_seconds": 30,
        }
        # Add rule
        response = self.client.post("/v1/monitor/rules", json=rule)
        assert response.status_code == 200
        
        # Remove rule
        response = self.client.delete("/v1/monitor/rules/temp_rule")
        assert response.status_code == 200
        data = response.json()
        assert data["ok"] is True
        assert data["rule_name"] == "temp_rule"

    def test_remove_nonexistent_rule(self):
        """Test removing a rule that doesn't exist"""
        response = self.client.delete("/v1/monitor/rules/nonexistent")
        assert response.status_code == 200
        data = response.json()
        assert data["ok"] is False
        assert data["rule_name"] == "nonexistent"

    def test_clear_alert(self):
        """Test clearing a triggered alert"""
        # First add a rule that will trigger when we get snapshot
        rule = {
            "rule_name": "clear_test_rule",
            "metric_key": "daily_pnl",
            "threshold": -1000.0,
            "comparison": "lt",
            "severity": "HIGH",
            "cooldown_seconds": 60,
        }
        response = self.client.post("/v1/monitor/rules", json=rule)
        assert response.status_code == 200
        
        # Get snapshot that triggers the alert (daily_pnl="-1500" < threshold=-1000)
        response = self.client.get(
            "/v1/monitor/snapshot",
            params={"daily_pnl": "-1500"},
        )
        assert response.status_code == 200
        
        # Now clear the triggered alert
        response = self.client.post("/v1/monitor/alerts/clear_test_rule/clear")
        assert response.status_code == 200
        data = response.json()
        assert data["ok"] is True
        assert data["rule_name"] == "clear_test_rule"

    def test_clear_all_alerts(self):
        """Test clearing all alerts"""
        response = self.client.post("/v1/monitor/alerts/clear-all")
        assert response.status_code == 200
        data = response.json()
        assert data["ok"] is True

    def test_snapshot_triggers_alert(self):
        """Test that snapshot correctly triggers alerts based on rules"""
        # Add a rule with a low threshold that will trigger
        rule = {
            "rule_name": "high_loss_rule",
            "metric_key": "daily_pnl",
            "threshold": -100.0,  # Will trigger with -500
            "comparison": "lt",
            "severity": "CRITICAL",
            "cooldown_seconds": 300,
        }
        response = self.client.post("/v1/monitor/rules", json=rule)
        assert response.status_code == 200
        
        # Get snapshot that should trigger the alert
        response = self.client.get(
            "/v1/monitor/snapshot",
            params={"daily_pnl": "-500"},
        )
        assert response.status_code == 200
        data = response.json()
        
        # Should have triggered the alert
        assert len(data["active_alerts"]) >= 1
        alert_names = [a["rule_name"] for a in data["active_alerts"]]
        assert "high_loss_rule" in alert_names

    def test_snapshot_no_alert_when_above_threshold(self):
        """Test that snapshot doesn't trigger alert when above threshold"""
        # Add a rule with a threshold that won't be triggered
        rule = {
            "rule_name": "no_trigger_rule",
            "metric_key": "daily_pnl",
            "threshold": -1000.0,  # Won't trigger with 500
            "comparison": "lt",
            "severity": "HIGH",
            "cooldown_seconds": 60,
        }
        response = self.client.post("/v1/monitor/rules", json=rule)
        assert response.status_code == 200
        
        # Get snapshot that should NOT trigger the alert
        response = self.client.get(
            "/v1/monitor/snapshot",
            params={"daily_pnl": "500"},
        )
        assert response.status_code == 200
        data = response.json()
        
        # Should not have triggered the alert
        alert_names = [a["rule_name"] for a in data["active_alerts"]]
        assert "no_trigger_rule" not in alert_names
