"""
china_stock_market_rule_plugin.py - A 股市场规则插件
====================================================
P9 A 股规则插件：实现 T+1、100 股、涨跌停、停牌、不可做空、交易阶段检查。
A 股规则只在该插件中实现，不污染通用层。

参考: docs/INTERFACE_CONTRACTS.md 8.11.4 ChinaStockMarketRulePlugin 边界
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from decimal import ROUND_FLOOR, Decimal
from enum import Enum
from typing import TYPE_CHECKING, Any

from trader.core.domain.models.market_risk import AssetClass, MarketRiskSnapshot
from trader.core.domain.models.market_rules import (
    MarketRuleCheckResult,
    MarketRuleIntent,
    MarketRulePlugin,
    MarketRuleViolation,
    OrderSide,
)

if TYPE_CHECKING:
    from trader.core.domain.models.order import OrderSide as OrderSideFromOrder

logger = logging.getLogger(__name__)


class ChinaStockTradingPhase(str, Enum):
    CONTINUOUS_AUCTION = "CONTINUOUS_AUCTION"
    CALL_AUCTION_OPEN = "CALL_AUCTION_OPEN"
    CALL_AUCTION_CLOSE = "CALL_AUCTION_CLOSE"
    CLOSED = "CLOSED"
    SUSPENDED = "SUSPENDED"
    UNKNOWN = "UNKNOWN"


@dataclass(frozen=True, slots=True)
class ChinaStockMarketRulePluginConfig:
    """
    A 股市场规则插件配置

    require_market_state: 是否要求完整市场状态数据（默认 True，缺失数据时 fail-closed）
    default_lot_size: 默认每手股数（默认 100）
    default_allow_short: 默认是否允许做空（默认 False，A 股现货不可做空）
    """

    require_market_state: bool = True
    default_lot_size: Decimal = Decimal("100")
    default_allow_short: bool = False


def _parse_required_bool(
    value: Any, field_name: str, required: bool, default: bool
) -> tuple[bool, MarketRuleViolation | None]:
    """
    解析布尔字段，缺失或无法识别时返回 violation

    Args:
        value: 要解析的值
        field_name: 字段名（用于 violation）
        required: 字段是否必填
        default: 字段非必填且缺失时使用的默认值

    Returns:
        (解析后的布尔值, violation 或 None)
        - value=None 且 required=True: 返回 (False, MARKET_STATE_MISSING)
        - value=None 且 required=False: 返回 (default, None)
        - value 可解析: 返回 (解析后的布尔值, None)
        - value 无法解析: 返回 (default, INVALID_BOOL)
    """
    if value is None:
        if required:
            return False, MarketRuleViolation(
                code="MARKET_STATE_MISSING",
                message=f"Required boolean field '{field_name}' is missing",
                field=field_name,
                expected="true/false, 1/0, yes/no, on/off, True/False",
                actual="None",
            )
        return default, None

    if isinstance(value, bool):
        return value, None

    if isinstance(value, str):
        normalized = value.lower().strip()
        if normalized in ("true", "1", "yes", "on"):
            return True, None
        if normalized in ("false", "0", "no", "off"):
            return False, None
        return default, MarketRuleViolation(
            code="INVALID_BOOL",
            message=f"Boolean field '{field_name}' has invalid value '{value}'",
            field=field_name,
            expected="true/false, 1/0, yes/no, on/off, True/False",
            actual=str(value),
        )

    if isinstance(value, (int, float)):
        if value in (1, 1.0):
            return True, None
        if value in (0, 0.0):
            return False, None
        return default, MarketRuleViolation(
            code="INVALID_BOOL",
            message=f"Boolean field '{field_name}' has invalid numeric value '{value}'",
            field=field_name,
            expected="0, 1 or boolean",
            actual=str(value),
        )

    return default, MarketRuleViolation(
        code="INVALID_BOOL",
        message=f"Boolean field '{field_name}' must be bool or string, got {type(value).__name__}",
        field=field_name,
        expected="bool or string",
        actual=type(value).__name__,
    )


def _parse_trading_phase(
    value: Any, required: bool, default: str | None = None
) -> tuple[str | None, MarketRuleViolation | None]:
    """
    解析交易阶段，缺失时返回 violation

    Args:
        value: 要解析的值
        required: 字段是否必填
        default: 字段非必填且缺失时使用的默认值（枚举值字符串）

    Returns:
        (解析后的交易阶段字符串, violation 或 None)
    """
    if value is None:
        if required:
            return None, MarketRuleViolation(
                code="MARKET_STATE_MISSING",
                message="Trading phase is required but not provided",
                field="trading_phase",
                expected="valid trading phase string or enum",
                actual="None",
            )
        if default is not None:
            return default, None
        return None, None

    if isinstance(value, ChinaStockTradingPhase):
        return value.value, None
    if isinstance(value, str):
        upper = value.upper().strip()
        try:
            return ChinaStockTradingPhase(upper).value, None
        except ValueError:
            return None, MarketRuleViolation(
                code="MARKET_STATE_INVALID",
                message=f"Unknown trading phase '{value}'",
                field="trading_phase",
                expected=f"one of {[p.value for p in ChinaStockTradingPhase]}",
                actual=str(value),
            )
    return None, MarketRuleViolation(
        code="MARKET_STATE_INVALID",
        message=f"Trading phase must be string or enum, got {type(value).__name__}",
        field="trading_phase",
        expected="string or ChinaStockTradingPhase enum",
        actual=type(value).__name__,
    )


class ChinaStockMarketRulePlugin:
    """
    A 股市场规则插件

    实现了以下 A 股专属规则（全部通过 metadata 读取）：
    - 100 股手数：qty 必须是 lot_size 的整数倍
    - T+1 可卖数量：卖出数量不得超过 sellable_qty
    - 涨跌停：price 必须在 limit_down <= price <= limit_up
    - 停牌：is_suspended=true 时拒绝
    - 不可做空：无可卖数量时卖出拒绝（默认 false）
    - 交易阶段：非连续竞价/允许交易阶段拒绝

    参考: docs/INTERFACE_CONTRACTS.md 8.11.4 ChinaStockMarketRulePlugin 边界
    """

    SUPPORTED_ASSET_CLASSES = frozenset({AssetClass.CN_STOCK})
    SUPPORTED_VENUES = frozenset({"sse", "szse", "bjse", "cn"})

    def __init__(self, config: ChinaStockMarketRulePluginConfig | None = None) -> None:
        self._config = config or ChinaStockMarketRulePluginConfig()

    def supports(self, asset_class: AssetClass, venue: str) -> bool:
        """
        判断该插件是否支持 A 股市场

        Args:
            asset_class: 资产类别
            venue: 交易场所

        Returns:
            True 如果支持 A 股
        """
        return (
            asset_class in self.SUPPORTED_ASSET_CLASSES and venue.lower() in self.SUPPORTED_VENUES
        )

    def check(
        self,
        intent: MarketRuleIntent,
        snapshot: MarketRiskSnapshot,
    ) -> MarketRuleCheckResult:
        """
        执行 A 股市场规则检查

        Args:
            intent: 规则检查输入
            snapshot: 市场风险快照

        Returns:
            MarketRuleCheckResult: 通过/拒绝结果
        """
        metadata = intent.metadata
        violations: list[MarketRuleViolation] = []
        normalized_qty = intent.qty
        normalized_price = intent.price
        actual_lot_size: Decimal | None = None

        lot_size_result, lot_violation = self._parse_and_check_lot_size(intent.qty, metadata)
        if lot_violation:
            violations.append(lot_violation)
        if lot_size_result is not None:
            normalized_qty = lot_size_result
            actual_lot_size = Decimal(str(metadata.get("lot_size", self._config.default_lot_size)))

        is_suspended, suspension_violation = self._check_suspension(metadata)
        if suspension_violation:
            violations.append(suspension_violation)

        trading_phase, phase_violation = _parse_trading_phase(
            metadata.get("trading_phase"),
            required=self._config.require_market_state,
            default=(
                ChinaStockTradingPhase.CONTINUOUS_AUCTION.value
                if not self._config.require_market_state
                else None
            ),
        )
        if phase_violation:
            violations.append(phase_violation)
        elif trading_phase is not None:
            phase_check = self._check_trading_phase(trading_phase)
            if phase_check:
                violations.append(phase_check)

        limit_result = self._check_price_limit(metadata, intent.price)
        if limit_result:
            violations.append(limit_result)

        side_result = _validate_side(intent.side)
        if side_result.violation:
            violations.append(side_result.violation)
            return MarketRuleCheckResult.reject(
                violations=violations,
                normalized_qty=normalized_qty,
                normalized_price=normalized_price,
                details=self._build_details(violations, metadata, actual_lot_size),
            )

        if side_result.is_sell:
            sell_violations = self._check_sell_rules(intent.qty, metadata)
            violations.extend(sell_violations)

        if violations:
            return MarketRuleCheckResult.reject(
                violations=violations,
                normalized_qty=normalized_qty,
                normalized_price=normalized_price,
                details=self._build_details(violations, metadata, actual_lot_size),
            )

        return MarketRuleCheckResult.approve(
            normalized_qty=normalized_qty,
            normalized_price=normalized_price,
            details={
                "plugin": "ChinaStockMarketRulePlugin",
                "lot_size": str(actual_lot_size or self._config.default_lot_size),
            },
        )

    def _parse_and_check_lot_size(
        self, qty: Decimal, metadata: dict
    ) -> tuple[Decimal | None, MarketRuleViolation | None]:
        """解析 lot_size 并检查数量"""
        lot_size_raw = metadata.get("lot_size", self._config.default_lot_size)
        try:
            lot_size = Decimal(str(lot_size_raw))
        except Exception:
            return None, MarketRuleViolation(
                code="INVALID_LOT_SIZE",
                message=f"Lot size must be decimal, got {type(lot_size_raw).__name__}",
                field="lot_size",
                expected="decimal number",
                actual=str(lot_size_raw),
            )

        return self._check_lot_size(qty, lot_size)

    def _check_lot_size(
        self, qty: Decimal, lot_size: Decimal
    ) -> tuple[Decimal | None, MarketRuleViolation | None]:
        """检查手数：qty 必须是 lot_size 的整数倍

        违规时仍返回 normalized_qty，供后续复用（如 P9.3/P9.4 裁剪）
        """
        if qty <= 0:
            return None, MarketRuleViolation(
                code="INVALID_QTY",
                message="Quantity must be positive",
                field="qty",
                expected="> 0",
                actual=str(qty),
            )

        if lot_size <= 0:
            return None, MarketRuleViolation(
                code="INVALID_LOT_SIZE",
                message="Lot size must be positive",
                field="lot_size",
                expected="> 0",
                actual=str(lot_size),
            )

        lots = (qty / lot_size).to_integral_value(rounding=ROUND_FLOOR)
        normalized_qty = lots * lot_size

        if normalized_qty <= 0:
            return None, MarketRuleViolation(
                code="LOT_SIZE",
                message=f"Quantity must be multiple of {lot_size}",
                field="qty",
                expected=f"multiple of {lot_size}",
                actual=str(qty),
            )

        remainder = qty - normalized_qty
        if remainder > 0:
            return normalized_qty, MarketRuleViolation(
                code="LOT_SIZE",
                message=f"Quantity adjusted to {normalized_qty} (must be multiple of {lot_size})",
                field="qty",
                expected=f"multiple of {lot_size}",
                actual=f"{qty}, normalized to {normalized_qty}",
            )

        return normalized_qty, None

    def _check_suspension(self, metadata: dict) -> tuple[bool, MarketRuleViolation | None]:
        """检查停牌状态（必填字段，缺失或非法值均 fail-closed）"""
        is_suspended, violation = _parse_required_bool(
            metadata.get("is_suspended"),
            field_name="is_suspended",
            required=self._config.require_market_state,
            default=False,
        )
        if violation:
            return False, violation
        if is_suspended:
            return True, MarketRuleViolation(
                code="SUSPENDED",
                message="Security is suspended, trading not allowed",
                field="is_suspended",
                expected="false",
                actual="true",
            )
        return False, None

    def _check_trading_phase(self, trading_phase: str) -> MarketRuleViolation | None:
        """检查交易阶段：只允许 CONTINUOUS_AUCTION 和 CALL_AUCTION_CLOSE"""
        allowed_phases = frozenset(
            {
                ChinaStockTradingPhase.CONTINUOUS_AUCTION.value,
                ChinaStockTradingPhase.CALL_AUCTION_CLOSE.value,
            }
        )

        if trading_phase not in allowed_phases:
            return MarketRuleViolation(
                code="TRADING_PHASE",
                message=f"Trading phase '{trading_phase}' not allowed for orders",
                field="trading_phase",
                expected=f"one of {sorted(allowed_phases)}",
                actual=str(trading_phase),
            )
        return None

    def _check_price_limit(self, metadata: dict, price: Decimal) -> MarketRuleViolation | None:
        """检查涨跌停"""
        limit_up_raw = metadata.get("limit_up")
        limit_down_raw = metadata.get("limit_down")

        if limit_up_raw is None or limit_down_raw is None:
            if self._config.require_market_state:
                missing_fields = []
                if limit_up_raw is None:
                    missing_fields.append("limit_up")
                if limit_down_raw is None:
                    missing_fields.append("limit_down")
                return MarketRuleViolation(
                    code="MARKET_STATE_MISSING",
                    message=f"Price limit fields required but missing: {missing_fields}",
                    field=", ".join(missing_fields),
                    expected="decimal number",
                    actual="None",
                )
            return None

        try:
            limit_up = Decimal(str(limit_up_raw))
            limit_down = Decimal(str(limit_down_raw))
        except Exception:
            return MarketRuleViolation(
                code="INVALID_PRICE_LIMIT",
                message="Price limits must be decimal numbers",
                field="limit_up/limit_down",
                expected="decimal number",
                actual=f"limit_up={limit_up_raw}, limit_down={limit_down_raw}",
            )

        if price <= 0:
            return MarketRuleViolation(
                code="INVALID_PRICE",
                message="Price must be positive",
                field="price",
                expected="> 0",
                actual=str(price),
            )

        if limit_up > 0 and price > limit_up:
            return MarketRuleViolation(
                code="PRICE_LIMIT_UP",
                message=f"Price {price} exceeds limit up {limit_up}",
                field="price",
                expected=f"<= {limit_up}",
                actual=str(price),
            )

        if limit_down > 0 and price < limit_down:
            return MarketRuleViolation(
                code="PRICE_LIMIT_DOWN",
                message=f"Price {price} below limit down {limit_down}",
                field="price",
                expected=f">= {limit_down}",
                actual=str(price),
            )

        return None

    def _check_sell_rules(self, qty: Decimal, metadata: dict) -> list[MarketRuleViolation]:
        """检查卖出规则：T+1 可卖数量、不可做空"""
        violations: list[MarketRuleViolation] = []

        sellable_qty_raw = metadata.get("sellable_qty")
        if sellable_qty_raw is None:
            if self._config.require_market_state:
                return [
                    MarketRuleViolation(
                        code="MARKET_STATE_MISSING",
                        message="Sellable quantity required for SELL orders but not provided",
                        field="sellable_qty",
                        expected="decimal number",
                        actual="None",
                    )
                ]
            sellable_qty = Decimal("0")
        else:
            try:
                sellable_qty = Decimal(str(sellable_qty_raw))
            except Exception:
                violations.append(
                    MarketRuleViolation(
                        code="INVALID_SELLABLE_QTY",
                        message="Sellable quantity must be decimal",
                        field="sellable_qty",
                        expected="decimal number",
                        actual=str(sellable_qty_raw),
                    )
                )
                return violations

        allow_short, allow_short_violation = _parse_required_bool(
            metadata.get("allow_short"),
            field_name="allow_short",
            required=False,
            default=self._config.default_allow_short,
        )
        if allow_short_violation:
            violations.append(allow_short_violation)

        if qty > sellable_qty and not allow_short:
            violations.append(
                MarketRuleViolation(
                    code="T1_SELL_LIMIT",
                    message=f"Cannot sell {qty} shares, only {sellable_qty} available (T+1)",
                    field="sellable_qty",
                    expected=f">= {qty}",
                    actual=str(sellable_qty),
                )
            )

        if not allow_short and sellable_qty <= 0:
            violations.append(
                MarketRuleViolation(
                    code="NO_SHORT",
                    message="Short selling not allowed and no shares available to sell",
                    field="allow_short",
                    expected="true or sellable_qty > 0",
                    actual=f"allow_short={allow_short}, sellable_qty={sellable_qty}",
                )
            )

        return violations

    def _build_details(
        self, violations: list[MarketRuleViolation], metadata: dict, lot_size: Decimal | None
    ) -> dict:
        """构建详情字典"""
        return {
            "plugin": "ChinaStockMarketRulePlugin",
            "violation_count": len(violations),
            "violation_codes": [v.code for v in violations],
            "metadata_summary": {
                "lot_size": str(
                    lot_size or metadata.get("lot_size", self._config.default_lot_size)
                ),
                "sellable_qty": str(metadata.get("sellable_qty", "0")),
                "limit_up": str(metadata.get("limit_up", "0")),
                "limit_down": str(metadata.get("limit_down", "0")),
                "is_suspended": str(metadata.get("is_suspended", False)),
                "trading_phase": str(metadata.get("trading_phase", "CONTINUOUS_AUCTION")),
                "allow_short": str(metadata.get("allow_short", self._config.default_allow_short)),
            },
        }


@dataclass(frozen=True)
class SideValidationResult:
    """Side 验证结果"""

    is_sell: bool
    violation: MarketRuleViolation | None = None


def _validate_side(side: OrderSide | "OrderSideFromOrder") -> SideValidationResult:
    """
    验证 side 参数

    未知/无法识别的 side 必须 fail-closed，不能默认 BUY。
    """
    if isinstance(side, OrderSide):
        return SideValidationResult(is_sell=side == OrderSide.SELL)

    if hasattr(side, "value"):
        try:
            normalized = OrderSide(side.value)
            return SideValidationResult(is_sell=normalized == OrderSide.SELL)
        except ValueError:
            return SideValidationResult(
                is_sell=False,
                violation=MarketRuleViolation(
                    code="INVALID_SIDE",
                    message=f"Unknown order side '{side.value}'",
                    field="side",
                    expected=f"one of {[s.value for s in OrderSide]}",
                    actual=str(side.value),
                ),
            )

    return SideValidationResult(
        is_sell=False,
        violation=MarketRuleViolation(
            code="INVALID_SIDE",
            message=f"Unknown order side type '{type(side).__name__}'",
            field="side",
            expected=f"OrderSide enum or object with .value",
            actual=f"{type(side).__name__}: {side}",
        ),
    )
