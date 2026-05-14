"""
market_rule_engine.py - 市场无关规则引擎
=========================================
P9 核心：根据 asset_class / venue 调度对应 MarketRulePlugin，聚合多个插件结果。
插件异常时 fail-closed。不做 IO，不包含任何 A 股或 Binance 专属硬编码。

参考: docs/INTERFACE_CONTRACTS.md 8.11 P9 市场规则与 EventDrivenRiskReplay 契约冻结
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from decimal import Decimal
from typing import TYPE_CHECKING, Any, Union

from trader.core.domain.models.market_rules import (
    MarketRuleCheckResult,
    MarketRuleIntent,
    MarketRulePlugin,
    MarketRuleViolation,
)

if TYPE_CHECKING:
    from trader.core.domain.models.market_risk import MarketRiskSnapshot


logger = logging.getLogger(__name__)


@dataclass(frozen=True, slots=True)
class MarketRuleEngineConfig:
    """
    市场规则引擎配置

    fail_closed_on_no_plugin: 当没有匹配的插件时是否 fail-closed（默认 True）
    fail_closed_on_check_error: 当 plugin.check() 执行异常时是否 fail-closed（默认 True）
                                  注意：plugin.supports() 异常永远是 fail-closed，不受此配置控制。
    """

    fail_closed_on_no_plugin: bool = True
    fail_closed_on_check_error: bool = True


class MarketRuleEngine:
    """
    市场无关规则引擎

    职责：
    - 根据 asset_class / venue 调度对应 MarketRulePlugin
    - 聚合多个插件结果
    - 插件异常时 fail-closed
    - 不包含任何 A 股或 Binance 专属硬编码
    - 不做 IO

    通用层只允许包含这些概念：
    - intent
    - snapshot
    - plugin registry
    - violation
    - normalized price / qty
    - pass / reject
    - fail-closed details

    参考: docs/INTERFACE_CONTRACTS.md P9.1 市场无关规则框架
    """

    def __init__(
        self,
        plugins: list[MarketRulePlugin] | None = None,
        config: MarketRuleEngineConfig | None = None,
    ) -> None:
        self._plugins: list[MarketRulePlugin] = list(plugins or [])
        self._config = config or MarketRuleEngineConfig()

    def register_plugin(self, plugin: MarketRulePlugin) -> None:
        """注册规则插件"""
        self._plugins.append(plugin)

    def check(
        self, intent: MarketRuleIntent, snapshot: "MarketRiskSnapshot"
    ) -> MarketRuleCheckResult:
        """
        执行市场规则检查

        根据 intent.asset_class 和 intent.venue 找到匹配的插件并执行。
        所有匹配的插件结果会被聚合。

        Args:
            intent: 规则检查输入
            snapshot: 市场风险快照

        Returns:
            MarketRuleCheckResult: 通过/拒绝/裁剪结果。
                                  注意：plugin.supports() 抛异常时返回 fail_closed 结果，不 raise。

        参考: docs/INTERFACE_CONTRACTS.md 8.11.3 MarketRulePlugin
        """
        if not self._plugins:
            return self._handle_no_plugin(intent)

        result = self._find_matching_plugins(intent)

        if isinstance(result, MarketRuleCheckResult):
            return result

        matching_plugins: list[MarketRulePlugin] = result

        if not matching_plugins:
            return self._handle_no_plugin(intent)

        return self._aggregate_results(intent, snapshot, matching_plugins)

    def _find_matching_plugins(
        self,
        intent: MarketRuleIntent,
    ) -> Union[list[MarketRulePlugin], MarketRuleCheckResult]:
        """找到支持该市场的所有插件

        注意：plugin.supports() 异常会直接返回 MarketRuleCheckResult.fail_closed()，
        由调用方 check() 处理异常并返回给外层。
        """
        matching: list[MarketRulePlugin] = []
        for plugin in self._plugins:
            try:
                if plugin.supports(intent.asset_class, intent.venue):
                    matching.append(plugin)
            except Exception as exc:
                logger.error(
                    "[MarketRuleEngine] plugin.supports() raised exception: %s",
                    exc,
                )
                return MarketRuleCheckResult.fail_closed(
                    reason=f"plugin {type(plugin).__name__}.supports() exception: {exc}",
                    normalized_qty=intent.qty,
                    normalized_price=intent.price,
                )
        return matching

    def _aggregate_results(
        self,
        intent: MarketRuleIntent,
        snapshot: "MarketRiskSnapshot",
        plugins: list[MarketRulePlugin],
    ) -> MarketRuleCheckResult:
        """
        聚合多个插件结果

        规则：
        - 任何插件拒绝则整体拒绝
        - 所有插件通过才通过
        - 聚合 normalized_qty / normalized_price（取最小值）
        - 聚合 violations
        """
        all_violations: list[MarketRuleViolation] = []
        final_qty = intent.qty
        final_price = intent.price

        for plugin in plugins:
            try:
                result = plugin.check(intent, snapshot)
            except Exception as exc:
                logger.error(
                    "[MarketRuleEngine] plugin.check() raised exception: %s",
                    exc,
                )
                if self._config.fail_closed_on_check_error:
                    return MarketRuleCheckResult.fail_closed(
                        reason=f"plugin {type(plugin).__name__} exception: {exc}",
                        normalized_qty=final_qty,
                        normalized_price=final_price,
                    )
                continue

            if not result.passed:
                all_violations.extend(result.violations)

            if not result.passed:
                reject_details: dict[str, Any] = {"rejected_by": type(plugin).__name__}
                if result.details:
                    reject_details["plugin_details"] = result.details
                return MarketRuleCheckResult.reject(
                    violations=all_violations,
                    normalized_qty=(
                        result.normalized_qty if result.normalized_qty > 0 else final_qty
                    ),
                    normalized_price=(
                        result.normalized_price if result.normalized_price > 0 else final_price
                    ),
                    details=reject_details,
                )

            if result.normalized_qty > 0:
                final_qty = min(final_qty, result.normalized_qty)
            if result.normalized_price > 0:
                final_price = min(final_price, result.normalized_price)

        return MarketRuleCheckResult.approve(
            normalized_qty=final_qty,
            normalized_price=final_price,
            details={"checked_by": [type(p).__name__ for p in plugins]},
        )

    def _handle_no_plugin(self, intent: MarketRuleIntent) -> MarketRuleCheckResult:
        """处理没有匹配插件的情况"""
        if self._config.fail_closed_on_no_plugin:
            logger.warning(
                "[MarketRuleEngine] No plugin found for %s/%s, fail-closed",
                intent.asset_class,
                intent.venue,
            )
            return MarketRuleCheckResult.fail_closed(
                reason=f"No plugin for {intent.asset_class.value}/{intent.venue}",
                normalized_qty=intent.qty,
                normalized_price=intent.price,
            )
        return MarketRuleCheckResult.approve(
            normalized_qty=intent.qty,
            normalized_price=intent.price,
            details={"no_plugin": True, "fallback": "pass"},
        )
