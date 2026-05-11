"""
Funding/OI Window Calculator - Core 纯计算服务
==============================================

Core Plane 无 IO 纯函数实现。

职责：
- 计算 funding rate Z-Score（相对历史窗口均值）
- 计算 OI change rate（相对历史窗口均值）
- 窗口不足判断
- 数据过期判断

禁止：
- 任何 IO 操作
- 网络请求
- 数据库访问
- 依赖外部时间（除 ts_ms 参数外）
"""

from __future__ import annotations

import math
from dataclasses import dataclass
from decimal import Decimal
from typing import List, Optional

from trader.core.domain.models.crypto_risk import CryptoFundingOIRiskMetrics


@dataclass(frozen=True)
class FundingRateZScoreResult:
    symbol: str
    z_score: Optional[float]
    mean: float
    std: float
    sample_count: int


@dataclass(frozen=True)
class OIChangeRateResult:
    symbol: str
    change_rate: Optional[float]
    mean: float
    std: float
    sample_count: int


class FundingOIWindowCalculator:
    """
    Funding/OI 历史窗口纯计算器

    纯函数实现，无任何 IO 操作。
    所有计算基于输入参数，不依赖外部状态。
    """

    @staticmethod
    def compute_funding_z_score(
        symbol: str,
        current_funding_rate: Optional[float],
        history: List[float],
        window: int = 20,
        min_periods: int = 10,
    ) -> FundingRateZScoreResult:
        """
        计算资金费率 Z-Score

        Args:
            symbol: 交易对
            current_funding_rate: 当前资金费率（可为 None 表示缺失）
            history: 历史资金费率列表（按时间升序）
            window: 滚动窗口大小
            min_periods: 最小样本数

        Returns:
            FundingRateZScoreResult: 计算结果
        """
        if current_funding_rate is None or len(history) < min_periods:
            return FundingRateZScoreResult(
                symbol=symbol,
                z_score=None,
                mean=0.0,
                std=0.0,
                sample_count=len(history),
            )

        window_history = history[-window:] if len(history) >= window else history
        rates = list(window_history)

        mean = sum(rates) / len(rates)
        variance = sum((r - mean) ** 2 for r in rates) / len(rates)
        std = math.sqrt(variance)

        if std == 0:
            z_score: Optional[float] = None
        else:
            z_score = (current_funding_rate - mean) / std

        return FundingRateZScoreResult(
            symbol=symbol,
            z_score=z_score,
            mean=mean,
            std=std,
            sample_count=len(window_history),
        )

    @staticmethod
    def compute_oi_change_rate(
        symbol: str,
        current_oi: Optional[float],
        history: List[float],
        window: int = 20,
        min_periods: int = 10,
    ) -> OIChangeRateResult:
        """
        计算 OI 相对历史窗口的变化率

        计算当前 OI 相对于历史窗口均值的百分比变化率：
        change_rate = (current_oi - mean) / mean * 100

        Args:
            symbol: 交易对
            current_oi: 当前 OI（可为 None 表示缺失）
            history: 历史 OI 列表（按时间升序）
            window: 滚动窗口大小
            min_periods: 最小样本数

        Returns:
            OIChangeRateResult: 计算结果
        """
        if current_oi is None or len(history) < min_periods:
            return OIChangeRateResult(
                symbol=symbol,
                change_rate=None,
                mean=0.0,
                std=0.0,
                sample_count=len(history),
            )

        window_history = history[-window:] if len(history) >= window else history
        oi_values = list(window_history)

        mean = sum(oi_values) / len(oi_values)

        if mean == 0:
            change_rate: Optional[float] = None
        else:
            change_rate = (current_oi - mean) / mean * 100

        return OIChangeRateResult(
            symbol=symbol,
            change_rate=change_rate,
            mean=mean,
            std=0.0,
            sample_count=len(window_history),
        )

    @staticmethod
    def compute_funding_oi_metrics(
        symbol: str,
        current_funding_rate: Optional[float],
        current_oi: Optional[float],
        funding_history: List[float],
        oi_history: List[float],
        funding_window: int = 20,
        oi_window: int = 20,
        funding_min_periods: int = 10,
        oi_min_periods: int = 10,
        latest_funding_ts_ms: int = 0,
        latest_oi_ts_ms: int = 0,
        current_ts_ms: int = 0,
        max_data_age_seconds: int = 86400,
    ) -> CryptoFundingOIRiskMetrics:
        """
        计算 Funding/OI 风险指标

        综合计算 funding rate Z-Score 和 OI change rate。

        Args:
            symbol: 交易对
            current_funding_rate: 当前资金费率（可为 None 表示缺失）
            current_oi: 当前 OI（可为 None 表示缺失）
            funding_history: 历史资金费率列表
            oi_history: 历史 OI 列表
            funding_window: funding Z-Score 窗口大小
            oi_window: OI change rate 窗口大小
            funding_min_periods: funding 最小样本数
            oi_min_periods: OI 最小样本数
            latest_funding_ts_ms: 最新 funding 数据时间戳
            latest_oi_ts_ms: 最新 OI 数据时间戳
            current_ts_ms: 当前时间戳
            max_data_age_seconds: 数据最大有效期（秒）

        Returns:
            CryptoFundingOIRiskMetrics: 综合指标
        """
        funding_result = FundingOIWindowCalculator.compute_funding_z_score(
            symbol=symbol,
            current_funding_rate=current_funding_rate,
            history=funding_history,
            window=funding_window,
            min_periods=funding_min_periods,
        )

        oi_result = FundingOIWindowCalculator.compute_oi_change_rate(
            symbol=symbol,
            current_oi=current_oi,
            history=oi_history,
            window=oi_window,
            min_periods=oi_min_periods,
        )

        funding_stale = (
            latest_funding_ts_ms > 0
            and current_ts_ms > 0
            and (current_ts_ms - latest_funding_ts_ms) > max_data_age_seconds * 1000
        )
        oi_stale = (
            latest_oi_ts_ms > 0
            and current_ts_ms > 0
            and (current_ts_ms - latest_oi_ts_ms) > max_data_age_seconds * 1000
        )

        latest_ts = max(latest_funding_ts_ms, latest_oi_ts_ms)
        data_age_ms = (
            (current_ts_ms - latest_ts) if (latest_ts > 0 and current_ts_ms > latest_ts) else 0
        )

        funding_window_insufficient = funding_result.sample_count < funding_min_periods
        oi_window_insufficient = oi_result.sample_count < oi_min_periods

        funding_current_missing = current_funding_rate is None
        oi_current_missing = current_oi is None

        return CryptoFundingOIRiskMetrics(
            symbol=symbol,
            current_funding_rate=(
                Decimal(str(current_funding_rate)) if current_funding_rate is not None else None
            ),
            funding_rate_z_score=funding_result.z_score,
            funding_rate_mean=funding_result.mean,
            funding_rate_std=funding_result.std,
            funding_history_count=funding_result.sample_count,
            current_open_interest=Decimal(str(current_oi)) if current_oi is not None else None,
            open_interest_change_rate=oi_result.change_rate,
            oi_mean=oi_result.mean,
            oi_std=oi_result.std,
            oi_history_count=oi_result.sample_count,
            funding_data_stale=funding_stale,
            oi_data_stale=oi_stale,
            data_age_ms=data_age_ms,
            funding_window_insufficient=funding_window_insufficient,
            oi_window_insufficient=oi_window_insufficient,
            funding_current_missing=funding_current_missing,
            oi_current_missing=oi_current_missing,
            latest_funding_ts_ms=latest_funding_ts_ms,
            latest_oi_ts_ms=latest_oi_ts_ms,
        )
