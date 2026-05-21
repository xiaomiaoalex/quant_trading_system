"""
红测：覆盖 /strategy-candidates/{id}/debug 响应契约和 debug 失败不进入 REJECTED
"""

from __future__ import annotations

from fastapi.testclient import TestClient

from trader.api.main import app
from trader.storage.in_memory import get_storage


def test_debug_success_returns_debug_response_with_candidate():
    """Debug 成功应返回 StrategyCandidateDebugResponse，包含 ok=True 和 candidate"""
    with TestClient(app) as client:
        created = client.post(
            "/v1/strategy-candidates",
            json={
                "strategy_id": "debug_contract_test",
                "name": "Debug Contract Test",
                "code": "\nfrom trader.core.application.strategy_protocol import (\n    MarketData, StrategyResourceLimits, ValidationResult, RiskLevel\n)\nfrom trader.core.domain.models.signal import Signal\n\nclass MinimalStrategy:\n    def __init__(self):\n        self.name = 'MinimalStrategy'\n        self.version = '1.0.0'\n        self.risk_level = RiskLevel.LOW\n        self.resource_limits = StrategyResourceLimits()\n        self.strategy_id = ''\n        self.deployment_id = ''\n        self.symbols = []\n\n    async def initialize(self, config: dict) -> None:\n        pass\n\n    async def on_market_data(self, data: MarketData) -> Signal | None:\n        return None\n\n    async def on_fill(self, fill: dict) -> None:\n        pass\n\n    async def on_cancel(self, cancel: dict) -> None:\n        pass\n\n    async def shutdown(self) -> None:\n        pass\n\n    def validate(self) -> ValidationResult:\n        return ValidationResult.valid()\n\n    def update_config(self, config: dict) -> ValidationResult:\n        return ValidationResult.valid()\n\ndef get_plugin():\n    return MinimalStrategy()\n",
            },
        )
        assert created.status_code == 201
        candidate_id = created.json()["candidate_id"]

        debugged = client.post(
            f"/v1/strategy-candidates/{candidate_id}/debug",
            json={"code": created.json()["code"], "config": {}},
        )
        assert debugged.status_code == 200
        payload = debugged.json()

        # 必须是 StrategyCandidateDebugResponse 结构
        assert "ok" in payload
        assert "syntax_ok" in payload
        assert "protocol_ok" in payload
        assert "signals" in payload
        assert "errors" in payload
        assert "warnings" in payload
        assert "candidate" in payload

        assert payload["ok"] is True
        assert payload["syntax_ok"] is True
        assert payload["protocol_ok"] is True
        assert payload["candidate"] is not None
        assert payload["candidate"]["status"] == "DEBUG_PASSED"


def test_debug_failure_returns_debug_response_and_keeps_draft():
    """Debug 失败应返回 ok=False，candidate 回到 DRAFT，code_version 清空"""
    with TestClient(app) as client:
        created = client.post(
            "/v1/strategy-candidates",
            json={
                "strategy_id": "debug_fail_test",
                "name": "Debug Fail Test",
                "code": "this is not valid python code!!!",
            },
        )
        assert created.status_code == 201
        candidate_id = created.json()["candidate_id"]

        debugged = client.post(
            f"/v1/strategy-candidates/{candidate_id}/debug",
            json={"code": "this is not valid python code!!!", "config": {}},
        )
        assert debugged.status_code == 200
        payload = debugged.json()

        assert payload["ok"] is False
        assert payload["syntax_ok"] is False
        assert len(payload["errors"]) > 0
        assert payload["candidate"] is not None
        # 关键：状态回到 DRAFT，不进入终态 REJECTED
        assert payload["candidate"]["status"] == "DRAFT"
        assert payload["candidate"]["code_version"] is None

        # 验证数据库中也是 DRAFT，且 code_version 被清空
        storage = get_storage()
        candidate = storage.get_strategy_candidate(candidate_id)
        assert candidate is not None
        assert candidate["status"] == "DRAFT"
        assert candidate.get("code_version") is None


def test_debug_failure_after_success_clears_code_version():
    """先 debug 通过，再改坏代码 debug 失败，应清空 code_version 防止旧版本被回测"""
    with TestClient(app) as client:
        code_ok = "\nfrom trader.core.application.strategy_protocol import (\n    MarketData, StrategyResourceLimits, ValidationResult, RiskLevel\n)\nfrom trader.core.domain.models.signal import Signal\n\nclass MinimalStrategy:\n    def __init__(self):\n        self.name = 'MinimalStrategy'\n        self.version = '1.0.0'\n        self.risk_level = RiskLevel.LOW\n        self.resource_limits = StrategyResourceLimits()\n        self.strategy_id = ''\n        self.deployment_id = ''\n        self.symbols = []\n\n    async def initialize(self, config: dict) -> None:\n        pass\n\n    async def on_market_data(self, data: MarketData) -> Signal | None:\n        return None\n\n    async def on_fill(self, fill: dict) -> None:\n        pass\n\n    async def on_cancel(self, cancel: dict) -> None:\n        pass\n\n    async def shutdown(self) -> None:\n        pass\n\n    def validate(self) -> ValidationResult:\n        return ValidationResult.valid()\n\n    def update_config(self, config: dict) -> ValidationResult:\n        return ValidationResult.valid()\n\ndef get_plugin():\n    return MinimalStrategy()\n"
        created = client.post(
            "/v1/strategy-candidates",
            json={
                "strategy_id": "debug_regression_test",
                "name": "Debug Regression Test",
                "code": code_ok,
            },
        )
        assert created.status_code == 201
        candidate_id = created.json()["candidate_id"]

        # 第一次 debug 通过
        debugged_ok = client.post(
            f"/v1/strategy-candidates/{candidate_id}/debug",
            json={"code": code_ok, "config": {}},
        )
        assert debugged_ok.status_code == 200
        assert debugged_ok.json()["ok"] is True
        assert debugged_ok.json()["candidate"]["status"] == "DEBUG_PASSED"
        assert debugged_ok.json()["candidate"]["code_version"] is not None

        # 第二次 debug 失败（改坏代码）
        debugged_fail = client.post(
            f"/v1/strategy-candidates/{candidate_id}/debug",
            json={"code": "this is not valid python code!!!", "config": {}},
        )
        assert debugged_fail.status_code == 200
        payload = debugged_fail.json()
        assert payload["ok"] is False
        assert payload["candidate"]["status"] == "DRAFT"
        assert payload["candidate"]["code_version"] is None

        # 验证回测会被拒绝（code_version 为 None）
        backtest_resp = client.post(
            f"/v1/strategy-candidates/{candidate_id}/backtests",
            json={
                "dataset": {
                    "symbols": ["BTCUSDT"],
                    "start_ts_ms": 1700000000000,
                    "end_ts_ms": 1700000100000,
                    "feature_version": "dev_smoke",
                    "venue": "BINANCE",
                    "initial_capital": 100000,
                    "fee_bps": 10,
                    "slippage_bps": 5,
                    "data_mode": "dev_smoke",
                    "risk_mode": "raw_only",
                },
                "requested_by": "test",
            },
        )
        assert backtest_resp.status_code == 409
        assert (
            "code_version" in backtest_resp.json()["detail"]
            or "debug" in backtest_resp.json()["detail"].lower()
        )
