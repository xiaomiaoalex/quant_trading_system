"""
Unit Tests - Portfolio Research API Endpoints
=============================================
Tests for Portfolio Research API endpoints using TestClient.
"""
import pytest
from datetime import datetime, timezone
from unittest.mock import AsyncMock, MagicMock, patch
from fastapi.testclient import TestClient

from trader.api.main import app


class MockCommitteeRun:
    """Mock CommitteeRun for testing"""
    def __init__(
        self,
        run_id: str = "run-001",
        trace_id: str = "trace-001",
        research_request: str = "Test request",
        sleeve_proposals: list = None,
        portfolio_proposal: dict = None,
        status: str = "completed",
    ):
        self.run_id = run_id
        self.trace_id = trace_id
        self.research_request = research_request
        self.sleeve_proposals = sleeve_proposals or []
        self.portfolio_proposal = portfolio_proposal
        self.status = status

    def to_dict(self):
        return {
            "run_id": self.run_id,
            "trace_id": self.trace_id,
            "research_request": self.research_request,
            "sleeve_proposals": self.sleeve_proposals,
            "portfolio_proposal": self.portfolio_proposal,
            "status": self.status,
        }


class MockWorkflowResult:
    """Mock WorkflowResult for testing"""
    def __init__(
        self,
        success: bool = True,
        committee_run: MockCommitteeRun = None,
        execution_time_seconds: float = 1.5,
        error_message: str = None,
    ):
        self.success = success
        self.committee_run = committee_run or MockCommitteeRun()
        self.execution_time_seconds = execution_time_seconds
        self.error_message = error_message


class MockPortfolioProposal:
    """Mock PortfolioProposal for testing"""
    def __init__(
        self,
        proposal_id: str = "proposal-001",
        sleeves: list = None,
    ):
        self.proposal_id = proposal_id
        self.sleeves = sleeves or []


class TestPortfolioResearchEndpoints:
    """Test portfolio research API endpoints"""

    def setup_method(self):
        """Setup for each test"""
        self.client = TestClient(app)
        self.mock_workflow = MagicMock()
        self.mock_workflow.run = AsyncMock()
        self.mock_store = MagicMock()
        self.mock_store.list_committee_runs = AsyncMock()
        self.mock_store.get_committee_run = AsyncMock()
        self.mock_store.save_committee_run = AsyncMock()

    def test_run_research_success(self):
        """Test running research workflow successfully"""
        mock_result = MockWorkflowResult(
            success=True,
            committee_run=MockCommitteeRun(
                run_id="run-001",
                trace_id="trace-001",
                sleeve_proposals=[],
                portfolio_proposal=MockPortfolioProposal(),
            ),
        )
        self.mock_workflow.run.return_value = mock_result

        with patch(
            "trader.api.routes.portfolio_research.get_workflow",
            return_value=self.mock_workflow
        ):
            response = self.client.post(
                "/api/portfolio-research/run",
                json={
                    "research_request": "Test request",
                }
            )

        assert response.status_code == 200
        data = response.json()
        assert data["success"] is True

    def test_run_research_failure(self):
        """Test running research workflow failure"""
        mock_result = MockWorkflowResult(
            success=False,
            error_message="Internal error occurred",
        )
        self.mock_workflow.run.return_value = mock_result

        with patch(
            "trader.api.routes.portfolio_research.get_workflow",
            return_value=self.mock_workflow
        ):
            response = self.client.post(
                "/api/portfolio-research/run",
                json={
                    "research_request": "Test request",
                }
            )

        assert response.status_code == 200
        data = response.json()
        assert data["success"] is False
        assert data["error_message"] == "Internal error occurred"

    def test_run_research_exception(self):
        """Test running research with exception"""
        self.mock_workflow.run.side_effect = Exception("Unexpected error")

        with patch(
            "trader.api.routes.portfolio_research.get_workflow",
            return_value=self.mock_workflow
        ):
            response = self.client.post(
                "/api/portfolio-research/run",
                json={
                    "research_request": "Test request",
                }
            )

        assert response.status_code == 500

    def test_list_runs_success(self):
        """Test listing committee runs"""
        mock_runs = [
            {
                "run_id": "run-001",
                "research_request": "Test request 1",
                "status": "completed",
                "sleeve_proposals": [],
                "final_status": "approved",
                "created_at": "2024-01-01T00:00:00Z",
            },
            {
                "run_id": "run-002",
                "research_request": "Test request 2",
                "status": "pending",
                "sleeve_proposals": [],
                "final_status": None,
                "created_at": "2024-01-02T00:00:00Z",
            },
        ]
        self.mock_store.list_committee_runs.return_value = mock_runs

        with patch(
            "trader.adapters.persistence.portfolio_proposal_store.PortfolioProposalStore",
            return_value=self.mock_store
        ):
            response = self.client.get("/api/portfolio-research/runs")

        assert response.status_code == 200
        data = response.json()
        assert len(data) == 2
        assert data[0]["run_id"] == "run-001"
        assert data[1]["run_id"] == "run-002"

    def test_list_runs_with_filter(self):
        """Test listing runs with status filter"""
        mock_runs = [
            {
                "run_id": "run-001",
                "research_request": "Test",
                "status": "completed",
                "sleeve_proposals": [],
                "final_status": "approved",
                "created_at": "2024-01-01T00:00:00Z",
            },
        ]
        self.mock_store.list_committee_runs.return_value = mock_runs

        with patch(
            "trader.adapters.persistence.portfolio_proposal_store.PortfolioProposalStore",
            return_value=self.mock_store
        ):
            response = self.client.get(
                "/api/portfolio-research/runs",
                params={"status": "completed"}
            )

        assert response.status_code == 200
        self.mock_store.list_committee_runs.assert_called_once()

    def test_list_runs_with_pagination(self):
        """Test listing runs with pagination"""
        mock_runs = []
        self.mock_store.list_committee_runs.return_value = mock_runs

        with patch(
            "trader.adapters.persistence.portfolio_proposal_store.PortfolioProposalStore",
            return_value=self.mock_store
        ):
            response = self.client.get(
                "/api/portfolio-research/runs",
                params={"limit": 50, "offset": 10}
            )

        assert response.status_code == 200
        self.mock_store.list_committee_runs.assert_called_once_with(None, 50, 10)

    def test_list_runs_exception(self):
        """Test listing runs with exception"""
        self.mock_store.list_committee_runs.side_effect = Exception("DB error")

        with patch(
            "trader.adapters.persistence.portfolio_proposal_store.PortfolioProposalStore",
            return_value=self.mock_store
        ):
            response = self.client.get("/api/portfolio-research/runs")

        assert response.status_code == 500

    def test_get_run_success(self):
        """Test getting a specific run"""
        mock_run = {
            "run_id": "run-001",
            "research_request": "Test request",
            "status": "completed",
            "sleeve_proposals": [],
        }
        self.mock_store.get_committee_run.return_value = mock_run

        with patch(
            "trader.adapters.persistence.portfolio_proposal_store.PortfolioProposalStore",
            return_value=self.mock_store
        ):
            response = self.client.get("/api/portfolio-research/runs/run-001")

        assert response.status_code == 200
        data = response.json()
        assert data["run_id"] == "run-001"

    def test_get_run_not_found(self):
        """Test getting non-existent run"""
        self.mock_store.get_committee_run.return_value = None

        with patch(
            "trader.adapters.persistence.portfolio_proposal_store.PortfolioProposalStore",
            return_value=self.mock_store
        ):
            response = self.client.get("/api/portfolio-research/runs/nonexistent")

        assert response.status_code == 404

    def test_get_run_exception(self):
        """Test getting run with exception"""
        self.mock_store.get_committee_run.side_effect = Exception("DB error")

        with patch(
            "trader.adapters.persistence.portfolio_proposal_store.PortfolioProposalStore",
            return_value=self.mock_store
        ):
            response = self.client.get("/api/portfolio-research/runs/run-001")

        assert response.status_code == 500

    def test_submit_for_approval_success(self):
        """Test submitting run for approval"""
        mock_run_data = {
            "run_id": "run-001",
            "research_request": "Test",
            "status": "completed",
            "sleeve_proposals": [],
            "trace_id": "trace-001",
            "portfolio_proposal": None,
        }
        self.mock_store.get_committee_run.return_value = mock_run_data

        mock_adapter = MagicMock()
        mock_adapter.submit_for_approval = AsyncMock(return_value={"success": True, "message": "Submitted"})

        with patch(
            "trader.adapters.persistence.portfolio_proposal_store.PortfolioProposalStore",
            return_value=self.mock_store
        ), patch(
            "trader.api.routes.portfolio_research.get_adapter",
            return_value=mock_adapter
        ):
            response = self.client.post("/api/portfolio-research/runs/run-001/submit")

        assert response.status_code == 200
        data = response.json()
        assert data["success"] is True

    def test_submit_for_approval_not_found(self):
        """Test submitting non-existent run for approval"""
        self.mock_store.get_committee_run.return_value = None

        with patch(
            "trader.adapters.persistence.portfolio_proposal_store.PortfolioProposalStore",
            return_value=self.mock_store
        ):
            response = self.client.post("/api/portfolio-research/runs/nonexistent/submit")

        assert response.status_code == 404

    def test_approve_run_success(self):
        """Test approving a run"""
        mock_run_data = {
            "run_id": "run-001",
            "research_request": "Test",
            "status": "completed",
            "sleeve_proposals": [],
            "trace_id": "trace-001",
            "portfolio_proposal": {"proposal_id": "proposal-001"},
        }
        self.mock_store.get_committee_run.return_value = mock_run_data

        mock_adapter = MagicMock()
        mock_adapter.approve_and_create_backtest = AsyncMock(return_value={
            "success": True,
            "strategy_draft_id": "draft-001",
            "backtest_job_id": "job-001",
        })

        with patch(
            "trader.adapters.persistence.portfolio_proposal_store.PortfolioProposalStore",
            return_value=self.mock_store
        ), patch(
            "trader.api.routes.portfolio_research.get_adapter",
            return_value=mock_adapter
        ):
            response = self.client.post(
                "/api/portfolio-research/runs/run-001/approve",
                params={"approver": "user-001"}
            )

        assert response.status_code == 200
        data = response.json()
        assert data["success"] is True

    def test_approve_run_not_found(self):
        """Test approving non-existent run"""
        self.mock_store.get_committee_run.return_value = None

        with patch(
            "trader.adapters.persistence.portfolio_proposal_store.PortfolioProposalStore",
            return_value=self.mock_store
        ):
            response = self.client.post(
                "/api/portfolio-research/runs/nonexistent/approve",
                params={"approver": "user-001"}
            )

        assert response.status_code == 404

    def test_reject_run_success(self):
        """Test rejecting a run"""
        mock_run_data = {
            "run_id": "run-001",
            "research_request": "Test",
            "status": "completed",
            "sleeve_proposals": [],
            "trace_id": "trace-001",
            "portfolio_proposal": {"proposal_id": "proposal-001"},
        }
        self.mock_store.get_committee_run.return_value = mock_run_data

        mock_adapter = MagicMock()
        mock_adapter.reject = AsyncMock(return_value={"success": True})

        with patch(
            "trader.adapters.persistence.portfolio_proposal_store.PortfolioProposalStore",
            return_value=self.mock_store
        ), patch(
            "trader.api.routes.portfolio_research.get_adapter",
            return_value=mock_adapter
        ):
            response = self.client.post(
                "/api/portfolio-research/runs/run-001/reject",
                params={"rejector": "user-001", "reason": "Risk too high"}
            )

        assert response.status_code == 200
        data = response.json()
        assert data["success"] is True

    def test_reject_run_not_found(self):
        """Test rejecting non-existent run"""
        self.mock_store.get_committee_run.return_value = None

        with patch(
            "trader.adapters.persistence.portfolio_proposal_store.PortfolioProposalStore",
            return_value=self.mock_store
        ):
            response = self.client.post(
                "/api/portfolio-research/runs/nonexistent/reject",
                params={"rejector": "user-001", "reason": "Test reason"}
            )

        assert response.status_code == 404


class TestPortfolioResearchEndpointsValidation:
    """Test portfolio research API input validation"""

    def setup_method(self):
        """Setup for each test"""
        self.client = TestClient(app)

    def test_list_runs_invalid_limit(self):
        """Test listing runs with invalid limit"""
        mock_store = MagicMock()

        with patch(
            "trader.adapters.persistence.portfolio_proposal_store.PortfolioProposalStore",
            return_value=mock_store
        ):
            response = self.client.get(
                "/api/portfolio-research/runs",
                params={"limit": 10000}
            )

        assert response.status_code == 422

    def test_list_runs_negative_offset(self):
        """Test listing runs with negative offset"""
        mock_store = MagicMock()

        with patch(
            "trader.adapters.persistence.portfolio_proposal_store.PortfolioProposalStore",
            return_value=mock_store
        ):
            response = self.client.get(
                "/api/portfolio-research/runs",
                params={"offset": -1}
            )

        assert response.status_code == 422

    def test_approve_run_missing_approver(self):
        """Test approving run without approver"""
        mock_store = MagicMock()
        mock_store.get_committee_run.return_value = {"run_id": "run-001"}

        with patch(
            "trader.adapters.persistence.portfolio_proposal_store.PortfolioProposalStore",
            return_value=mock_store
        ):
            response = self.client.post("/api/portfolio-research/runs/run-001/approve")

        assert response.status_code == 422
