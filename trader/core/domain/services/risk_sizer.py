"""
RiskSizer - 统一仓位大小计算器
===============================
纯计算、确定性、无 IO 的仓位大小决策模块。

将分散在以下模块的 sizing 逻辑统一到单一决策点：
- risk_engine (time_window coefficient)
- position_risk_constructor (exposure limits, regime discount)
- time_window_policy (time-based position coefficient)
- killswitch (KillSwitch levels L0-L3)

目标公式：
    final_size
    = min(size_by_stop, strategy_cap, symbol_exposure_cap, total_exposure_cap, liquidity_cap)
      * time_coef * drawdown_coef * venue_health_coef * regime_coef

约束：
- Core Plane 禁止 IO
- Fail-Closed：任何不一致必须拒绝
- 确定性：相同输入始终产生相同输出
"""
from __future__ import annotations

import math
from dataclasses import dataclass, field


@dataclass(slots=True)
class SizerInputs:
    """Sizer 的所有输入"""
    size_by_stop: float
    strategy_cap: float
    symbol_exposure_cap: float
    total_exposure_cap: float
    liquidity_cap: float
    time_coef: float
    drawdown_coef: float
    venue_health_coef: float
    regime_coef: float


@dataclass(slots=True)
class SizerResult:
    """Sizer 的输出结果"""
    approved_size: float
    limiting_factor: str
    coefficients: dict[str, float]
    is_rejected: bool
    rejection_reason: str | None


@dataclass(slots=True)
class SizerConfig:
    """Sizer 配置"""
    min_size: float = 0.0
    fail_closed: bool = True


_CAP_FIELDS: list[tuple[str, float]] = [
    ("size_by_stop", 0.0),
    ("strategy_cap", 0.0),
    ("symbol_exposure_cap", 0.0),
    ("total_exposure_cap", 0.0),
    ("liquidity_cap", 0.0),
]

_COEF_FIELDS: list[tuple[str, float, float]] = [
    ("time_coef", 0.0, 1.0),
    ("drawdown_coef", 0.0, 1.0),
    ("venue_health_coef", 0.0, 1.0),
    ("regime_coef", 0.0, 1.0),
]


def _is_finite(value: float) -> bool:
    return math.isfinite(value)


def _validate_inputs(inputs: SizerInputs, config: SizerConfig) -> tuple[bool, str | None]:
    """验证所有输入。返回 (is_valid, rejection_reason)。"""
    for name, min_val in _CAP_FIELDS:
        value = getattr(inputs, name)
        if not _is_finite(value):
            return False, f"{name} is not finite: {value}"
        if value < min_val:
            return False, f"{name} is negative: {value}"

    if inputs.size_by_stop <= 0.0:
        return False, "size_by_stop must be > 0"

    for name, lo, hi in _COEF_FIELDS:
        value = getattr(inputs, name)
        if not _is_finite(value):
            return False, f"{name} is not finite: {value}"
        if value < lo or value > hi:
            return False, f"{name} out of range [{lo}, {hi}]: {value}"

    return True, None


class RiskSizer:
    """
    统一仓位大小计算器。

    纯计算、无 IO、完全确定性。
    """

    def __init__(self, config: SizerConfig | None = None) -> None:
        self._config = config or SizerConfig()

    @property
    def config(self) -> SizerConfig:
        return self._config

    def compute(self, inputs: SizerInputs) -> SizerResult:
        """
        计算最终批准的仓位大小。

        Args:
            inputs: 所有 sizing 输入

        Returns:
            SizerResult: 包含批准大小、限制因子、系数和拒绝原因
        """
        valid, reason = _validate_inputs(inputs, self._config)
        if not valid:
            return SizerResult(
                approved_size=0.0,
                limiting_factor="validation",
                coefficients={
                    "time_coef": inputs.time_coef,
                    "drawdown_coef": inputs.drawdown_coef,
                    "venue_health_coef": inputs.venue_health_coef,
                    "regime_coef": inputs.regime_coef,
                },
                is_rejected=True,
                rejection_reason=reason,
            )

        caps: dict[str, float] = {
            "size_by_stop": inputs.size_by_stop,
            "strategy_cap": inputs.strategy_cap,
            "symbol_exposure_cap": inputs.symbol_exposure_cap,
            "total_exposure_cap": inputs.total_exposure_cap,
            "liquidity_cap": inputs.liquidity_cap,
        }

        limiting_factor = min(caps, key=lambda k: caps[k])
        base = caps[limiting_factor]

        coefs = {
            "time_coef": inputs.time_coef,
            "drawdown_coef": inputs.drawdown_coef,
            "venue_health_coef": inputs.venue_health_coef,
            "regime_coef": inputs.regime_coef,
        }

        if self._config.fail_closed and any(v == 0.0 for v in coefs.values()):
            zero_coefs = [k for k, v in coefs.items() if v == 0.0]
            return SizerResult(
                approved_size=0.0,
                limiting_factor="zero_coefficient",
                coefficients=coefs,
                is_rejected=True,
                rejection_reason=f"Zero coefficient(s): {', '.join(zero_coefs)}",
            )

        final = base * inputs.time_coef * inputs.drawdown_coef * inputs.venue_health_coef * inputs.regime_coef

        if base > 0.0 and final < self._config.min_size:
            return SizerResult(
                approved_size=0.0,
                limiting_factor="min_size",
                coefficients=coefs,
                is_rejected=True,
                rejection_reason=f"Final size {final} < min_size {self._config.min_size}",
            )

        return SizerResult(
            approved_size=final,
            limiting_factor=limiting_factor,
            coefficients=coefs,
            is_rejected=False,
            rejection_reason=None,
        )
