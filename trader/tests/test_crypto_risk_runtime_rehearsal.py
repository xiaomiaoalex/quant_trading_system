from __future__ import annotations

import importlib.util
import sys
from pathlib import Path
from types import ModuleType


def _load_script_module() -> ModuleType:
    script_path = (
        Path(__file__).resolve().parents[2] / "scripts" / "rehearse_crypto_risk_runtime.py"
    )
    spec = importlib.util.spec_from_file_location(
        "rehearse_crypto_risk_runtime",
        script_path,
    )
    assert spec is not None
    module = importlib.util.module_from_spec(spec)
    assert spec.loader is not None
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


def test_runtime_rehearsal_covers_all_p8_fail_closed_scenarios() -> None:
    module = _load_script_module()

    report = module.asyncio.run(module.run_runtime_rehearsal())

    assert report.ok is True
    assert {scenario.name for scenario in report.scenarios} == {
        "mark_price_missing",
        "leverage_bracket_missing",
        "open_orders_spike",
        "funding_oi_data_stale",
        "binance_source_timeout",
        "continuous_duplicate_signal",
        "close_only_open_signal",
        "pg_audit_unavailable",
    }


def test_runtime_rehearsal_never_attempts_order_when_scenario_fails_closed() -> None:
    module = _load_script_module()

    report = module.asyncio.run(module.run_runtime_rehearsal())

    assert all(scenario.passed is False for scenario in report.scenarios)
    assert all(scenario.order_attempted is False for scenario in report.scenarios)


def test_runtime_rehearsal_requires_audit_event_except_pg_outage() -> None:
    module = _load_script_module()

    report = module.asyncio.run(module.run_runtime_rehearsal())
    audit_by_name = {scenario.name: scenario.audit_event_found for scenario in report.scenarios}

    assert audit_by_name.pop("pg_audit_unavailable") is False
    assert all(audit_by_name.values())
    pg_scenario = next(s for s in report.scenarios if s.name == "pg_audit_unavailable")
    assert pg_scenario.evidence["audit_append_attempted"] is True
    assert pg_scenario.evidence["audit_append_attempts"] == 1
    assert pg_scenario.evidence["audit_append_failed"] is True
    assert pg_scenario.evidence["audit_append_failures"] == 1


def test_runtime_rehearsal_proves_funding_oi_and_close_only_rejections() -> None:
    module = _load_script_module()

    report = module.asyncio.run(module.run_runtime_rehearsal())
    by_name = {scenario.name: scenario for scenario in report.scenarios}

    assert by_name["funding_oi_data_stale"].rejection_reason == "CRYPTO_FUNDING_OI_RISK"
    assert by_name["funding_oi_data_stale"].evidence["details"]["funding_data_stale"] is True
    assert by_name["close_only_open_signal"].rejection_reason == "RISK_MODE_CLOSE_ONLY"
    assert by_name["continuous_duplicate_signal"].rejection_reason == "MAX_ORDER_RATE"


def test_runtime_rehearsal_report_is_json_serializable() -> None:
    module = _load_script_module()

    report = module.asyncio.run(module.run_runtime_rehearsal())
    payload = module.report_to_dict(report)

    encoded = module.json.dumps(payload, ensure_ascii=False, sort_keys=True)
    assert '"ok": true' in encoded
    assert "mark_price_missing" in encoded
