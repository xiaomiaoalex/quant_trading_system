"""
crypto_market_rule_plugin.py - Crypto 市场规则插件
===================================================
P9.3 Crypto 规则插件：包装现有 ExchangeRuleGuard 的 tick/step/minNotional/maxQty 语义。
Crypto 规则只在该插件中实现，不污染通用层，不引入 A 股字段。

参考: docs/INTERFACE_CONTRACTS.md 8.11.5 CryptoMarketRulePlugin 边界
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from decimal import ROUND_FLOOR, Decimal
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
    from trader.core.domain.services.exchange_rule_guard import InstrumentSpecLike

logger = logging.getLogger(__name__)


@dataclass(frozen=True, slots=True)
class CryptoMarketRulePluginConfig:
    """
    Crypto 市场规则插件配置

    require_market_state: 是否要求完整市场状态数据（默认 True，缺失数据时 fail-closed）
    default_price_tick: 默认价格步进（默认 0.01）
    default_qty_step: 默认数量步进（默认 0.001）
    """

    require_market_state: bool = True
    default_price_tick: Decimal = Decimal("0.01")
    default_qty_step: Decimal = Decimal("0.001")


def _parse_decimal(
    value: Any,
    field_name: str,
    required: bool,
    default: Decimal | None = None,
) -> tuple[Decimal | None, MarketRuleViolation | None]:
    """解析 Decimal 字段"""
    if value is None:
        if required:
            return None, MarketRuleViolation(
                code="MARKET_STATE_MISSING",
                message=f"Required field '{field_name}' is missing",
                field=field_name,
                expected="decimal number",
                actual="None",
            )
        return default, None

    try:
        return Decimal(str(value)), None
    except Exception:
        return None, MarketRuleViolation(
            code="INVALID_DECIMAL",
            message=f"Field '{field_name}' must be decimal, got {type(value).__name__}",
            field=field_name,
            expected="decimal number",
            actual=str(value),
        )


def _floor_to_step(value: Decimal, step: Decimal) -> Decimal:
    """将 value 向下取整到 step 的整数倍"""
    units = (value / step).to_integral_value(rounding=ROUND_FLOOR)
    return units * step


def _validate_side(
    side: OrderSide | "OrderSideFromOrder",
) -> tuple[bool, MarketRuleViolation | None]:
    """验证 side 参数"""
    if isinstance(side, OrderSide):
        return side == OrderSide.SELL, None

    if hasattr(side, "value"):
        try:
            normalized = OrderSide(side.value)
            return normalized == OrderSide.SELL, None
        except ValueError:
            return False, MarketRuleViolation(
                code="INVALID_SIDE",
                message=f"Unknown order side '{side.value}'",
                field="side",
                expected=f"one of {[s.value for s in OrderSide]}",
                actual=str(side.value),
            )

    return False, MarketRuleViolation(
        code="INVALID_SIDE",
        message=f"Unknown order side type '{type(side).__name__}'",
        field="side",
        expected=f"OrderSide enum or object with .value",
        actual=f"{type(side).__name__}: {side}",
    )


class CryptoMarketRulePlugin:
    """
    Crypto 市场规则插件

    包装现有 ExchangeRuleGuard 的以下规则（全部通过 metadata 读取）：
    - price_tick：价格步进
    - qty_step：数量步进
    - min_qty：最小数量
    - min_notional：最小名义金额
    - max_qty：最大数量（可选）
    - max_notional：最大名义金额（可选）

    不读取 A 股字段（sellable_qty、limit_up、limit_down、trading_phase 等）。

    参考: docs/INTERFACE_CONTRACTS.md 8.11.5 CryptoMarketRulePlugin 边界
    """

    SUPPORTED_ASSET_CLASSES = frozenset({AssetClass.CRYPTO})
    SUPPORTED_VENUES = frozenset({"binance", "okx", "bybit", "coinbase", "kraken", "crypto"})

    def __init__(self, config: CryptoMarketRulePluginConfig | None = None) -> None:
        self._config = config or CryptoMarketRulePluginConfig()

    def supports(self, asset_class: AssetClass, venue: str) -> bool:
        """判断该插件是否支持 Crypto 市场"""
        return (
            asset_class in self.SUPPORTED_ASSET_CLASSES and venue.lower() in self.SUPPORTED_VENUES
        )

    def check(
        self,
        intent: MarketRuleIntent,
        snapshot: MarketRiskSnapshot,
    ) -> MarketRuleCheckResult:
        """
        执行 Crypto 市场规则检查

        Args:
            intent: 规则检查输入
            snapshot: 市场风险快照（当前未使用，字段从 metadata 读取）

        Returns:
            MarketRuleCheckResult: 通过/拒绝结果
        """
        metadata = intent.metadata
        violations: list[MarketRuleViolation] = []
        normalized_qty = intent.qty
        normalized_price = intent.price

        price_tick, price_tick_violation = _parse_decimal(
            metadata.get("price_tick"),
            field_name="price_tick",
            required=self._config.require_market_state,
            default=self._config.default_price_tick,
        )
        if price_tick_violation:
            violations.append(price_tick_violation)

        qty_step, qty_step_violation = _parse_decimal(
            metadata.get("qty_step"),
            field_name="qty_step",
            required=self._config.require_market_state,
            default=self._config.default_qty_step,
        )
        if qty_step_violation:
            violations.append(qty_step_violation)

        min_qty, min_qty_violation = _parse_decimal(
            metadata.get("min_qty"),
            field_name="min_qty",
            required=False,
            default=Decimal("0"),
        )
        if min_qty_violation:
            violations.append(min_qty_violation)

        min_notional, min_notional_violation = _parse_decimal(
            metadata.get("min_notional"),
            field_name="min_notional",
            required=False,
            default=Decimal("0"),
        )
        if min_notional_violation:
            violations.append(min_notional_violation)

        max_qty, max_qty_violation = _parse_decimal(
            metadata.get("max_qty"),
            field_name="max_qty",
            required=False,
            default=None,
        )
        if max_qty_violation:
            violations.append(max_qty_violation)

        max_notional, max_notional_violation = _parse_decimal(
            metadata.get("max_notional"),
            field_name="max_notional",
            required=False,
            default=None,
        )
        if max_notional_violation:
            violations.append(max_notional_violation)

        is_sell, side_violation = _validate_side(intent.side)
        if side_violation:
            violations.append(side_violation)
            return MarketRuleCheckResult.reject(
                violations=violations,
                normalized_qty=normalized_qty,
                normalized_price=normalized_price,
                details=self._build_details(violations, metadata),
            )

        if violations:
            return MarketRuleCheckResult.reject(
                violations=violations,
                normalized_qty=normalized_qty,
                normalized_price=normalized_price,
                details=self._build_details(violations, metadata),
            )

        assert price_tick is not None, "price_tick must not be None after _parse_decimal"
        assert qty_step is not None, "qty_step must not be None after _parse_decimal"
        assert min_qty is not None, "min_qty must not be None after _parse_decimal"
        assert min_notional is not None, "min_notional must not be None after _parse_decimal"

        result = self._check_exchange_rules(
            price_tick=price_tick,
            qty_step=qty_step,
            min_qty=min_qty,
            min_notional=min_notional,
            max_qty=max_qty,
            max_notional=max_notional,
            qty=intent.qty,
            price=intent.price,
            normalized_qty=normalized_qty,
            normalized_price=normalized_price,
        )
        if result.violation:
            violations.append(result.violation)
            return MarketRuleCheckResult.reject(
                violations=violations,
                normalized_qty=result.normalized_qty,
                normalized_price=result.normalized_price,
                details=self._build_details(violations, metadata),
            )

        return MarketRuleCheckResult.approve(
            normalized_qty=result.normalized_qty,
            normalized_price=result.normalized_price,
            details={
                "plugin": "CryptoMarketRulePlugin",
                "price_tick": str(price_tick),
                "qty_step": str(qty_step),
            },
        )

    def _check_exchange_rules(
        self,
        price_tick: Decimal,
        qty_step: Decimal,
        min_qty: Decimal,
        min_notional: Decimal,
        max_qty: Decimal | None,
        max_notional: Decimal | None,
        qty: Decimal,
        price: Decimal,
        normalized_qty: Decimal,
        normalized_price: Decimal,
    ) -> _ExchangeCheckResult:
        """检查交易所规则"""
        if price_tick <= 0 or qty_step <= 0:
            return _ExchangeCheckResult(
                normalized_qty=normalized_qty,
                normalized_price=normalized_price,
                violation=MarketRuleViolation(
                    code="INVALID_INSTRUMENT_SPEC",
                    message=f"Invalid instrument spec: price_tick={price_tick}, qty_step={qty_step}",
                    field="price_tick/qty_step",
                    expected="> 0",
                    actual=f"price_tick={price_tick}, qty_step={qty_step}",
                ),
            )

        if qty <= 0:
            return _ExchangeCheckResult(
                normalized_qty=Decimal("0"),
                normalized_price=normalized_price,
                violation=MarketRuleViolation(
                    code="INVALID_QTY",
                    message="Quantity must be positive",
                    field="qty",
                    expected="> 0",
                    actual=str(qty),
                ),
            )

        if price <= 0:
            return _ExchangeCheckResult(
                normalized_qty=normalized_qty,
                normalized_price=Decimal("0"),
                violation=MarketRuleViolation(
                    code="INVALID_PRICE",
                    message="Price must be positive",
                    field="price",
                    expected="> 0",
                    actual=str(price),
                ),
            )

        norm_qty = _floor_to_step(qty, qty_step)
        norm_price = _floor_to_step(price, price_tick)
        notional = norm_qty * norm_price

        if norm_qty <= 0:
            return _ExchangeCheckResult(
                normalized_qty=norm_qty,
                normalized_price=norm_price,
                violation=MarketRuleViolation(
                    code="INVALID_QTY",
                    message=f"Quantity too small after step normalization: {norm_qty}",
                    field="qty",
                    expected=f">= {qty_step}",
                    actual=str(norm_qty),
                ),
            )

        if norm_qty < min_qty:
            return _ExchangeCheckResult(
                normalized_qty=norm_qty,
                normalized_price=norm_price,
                violation=MarketRuleViolation(
                    code="MIN_QTY",
                    message=f"Quantity {norm_qty} below minimum {min_qty}",
                    field="qty",
                    expected=f">= {min_qty}",
                    actual=str(norm_qty),
                ),
            )

        if max_qty is not None and norm_qty > max_qty:
            return _ExchangeCheckResult(
                normalized_qty=norm_qty,
                normalized_price=norm_price,
                violation=MarketRuleViolation(
                    code="MAX_QTY",
                    message=f"Quantity {norm_qty} exceeds maximum {max_qty}",
                    field="qty",
                    expected=f"<= {max_qty}",
                    actual=str(norm_qty),
                ),
            )

        if notional < min_notional:
            return _ExchangeCheckResult(
                normalized_qty=norm_qty,
                normalized_price=norm_price,
                violation=MarketRuleViolation(
                    code="MIN_NOTIONAL",
                    message=f"Notional {notional} below minimum {min_notional}",
                    field="notional",
                    expected=f">= {min_notional}",
                    actual=str(notional),
                ),
            )

        if max_notional is not None and notional > max_notional:
            return _ExchangeCheckResult(
                normalized_qty=norm_qty,
                normalized_price=norm_price,
                violation=MarketRuleViolation(
                    code="MAX_NOTIONAL",
                    message=f"Notional {notional} exceeds maximum {max_notional}",
                    field="notional",
                    expected=f"<= {max_notional}",
                    actual=str(notional),
                ),
            )

        return _ExchangeCheckResult(
            normalized_qty=norm_qty,
            normalized_price=norm_price,
            violation=None,
        )

    def _build_details(self, violations: list[MarketRuleViolation], metadata: dict) -> dict:
        """构建详情字典"""
        return {
            "plugin": "CryptoMarketRulePlugin",
            "violation_count": len(violations),
            "violation_codes": [v.code for v in violations],
            "metadata_summary": {
                "price_tick": str(metadata.get("price_tick", self._config.default_price_tick)),
                "qty_step": str(metadata.get("qty_step", self._config.default_qty_step)),
                "min_qty": str(metadata.get("min_qty", "0")),
                "min_notional": str(metadata.get("min_notional", "0")),
                "max_qty": str(metadata.get("max_qty", "None")),
                "max_notional": str(metadata.get("max_notional", "None")),
            },
        }


@dataclass(frozen=True)
class _ExchangeCheckResult:
    """内部交换规则检查结果"""

    normalized_qty: Decimal
    normalized_price: Decimal
    violation: MarketRuleViolation | None = None
