"""Catalogue of installed research strategies.

The Research runtime only executes installed ``trader.strategies.*`` modules
whose source hash is frozen into the manifest. This module exposes exactly the
set a user may pick from the UI.
"""

from __future__ import annotations

import dataclasses
import importlib
from typing import Any, Dict, List

INSTALLED_STRATEGY_MODULES = ("dca_btc", "ema_cross_btc", "fire_test", "rsi_grid")

_NON_PARAMETER_FIELDS = frozenset(
    {"strategy_id", "name", "version", "risk_level", "resource_limits"}
)


def _parameters(plugin: Any) -> List[Dict[str, Any]]:
    if not dataclasses.is_dataclass(plugin):
        return []
    parameters: List[Dict[str, Any]] = []
    for item in dataclasses.fields(plugin):
        if item.name.startswith("_") or item.name in _NON_PARAMETER_FIELDS:
            continue
        if item.default is dataclasses.MISSING:
            default = None
            required = True
        else:
            default = str(item.default)
            required = False
        parameters.append(
            {
                "name": item.name,
                "type": str(item.type),
                "default": default,
                "required": required,
            }
        )
    return parameters


def list_installed_strategies() -> List[Dict[str, Any]]:
    """List the installed research strategies and their parameters."""
    from trader.trading.jobs import freeze_strategy

    records: List[Dict[str, Any]] = []
    for name in INSTALLED_STRATEGY_MODULES:
        module_path = f"trader.strategies.{name}"
        frozen = freeze_strategy({"module_path": module_path})
        module = importlib.import_module(module_path)
        factory = getattr(module, "create_plugin", None) or getattr(module, "get_plugin")
        plugin = factory()
        records.append(
            {
                "module_path": module_path,
                "strategy_id": str(getattr(plugin, "strategy_id", name)),
                "name": str(getattr(plugin, "name", name)),
                "version": str(getattr(plugin, "version", "1")),
                "code_hash": frozen["code_hash"],
                "params": _parameters(plugin),
            }
        )
    return records


__all__ = ["INSTALLED_STRATEGY_MODULES", "list_installed_strategies"]
