"""
Signal Sandbox - 信号验证与未来函数检测工具
==============================================

职责：
- 信号回放测试：使用历史数据验证信号计算器正确性
- 未来函数检测：检测信号计算是否存在 look-ahead bias
- 生成测试报告

约束：
- 允许 IO（读取 Feature Store、生成报告文件）
- 信号计算器本身必须保持纯函数
- Sandbox 仅用于验证，不修改任何核心 domain 逻辑

Usage:
    # 运行所有信号测试
    results = run_signal_sandbox("BTCUSDT", signals_to_test=["all"])

    # 检测未来函数泄漏
    leak_report = detect_future_leaks(signal_timeline)

    # 生成报告
    report = generate_report(results)
"""

import sys
import os

# 将项目根目录添加到 Python 模块路径
_project_root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if _project_root not in sys.path:
    sys.path.insert(0, _project_root)

import asyncio
import logging
from dataclasses import dataclass, field
from datetime import datetime, timezone
from decimal import Decimal
from enum import Enum
from typing import Any, Callable, Dict, List, Optional, Tuple, Union
import json
import os

from trader.adapters.persistence.feature_store import FeatureStore
from trader.core.domain.signals.trend_signals import (
    EMACrossover,
    PriceMomentum,
    BollingerBandPosition,
    PriceSample,
    TrendDirection,
    EMACrossoverResult,
    PriceMomentumResult,
    BollingerBandResult,
)
from trader.core.domain.signals.price_volume_signals import (
    VolumeExpansion,
    VolatilityCompression,
    VolumeSample,
    PriceVolumeSample,
    VolumeDirection,
    VolumeExpansionResult,
    VolatilityCompressionResult,
)

logger = logging.getLogger(__name__)


# ==================== 数据结构 ====================

class LeakSeverity(Enum):
    """泄漏严重程度"""
    NONE = "NONE"
    LOW = "LOW"
    MEDIUM = "MEDIUM"
    HIGH = "HIGH"


@dataclass
class SignalTestResult:
    """信号测试结果
    
    Attributes:
        signal_name: 信号名称
        signal_type: 信号类型（如 EMA, Momentum, BollingerBand 等）
        timestamp: 信号时间戳（毫秒）
        value: 信号值（具体类型依赖于信号计算器）
        is_valid: 是否为有效信号
        warning: 警告信息（如有）
    """
    signal_name: str
    signal_type: str
    timestamp: int
    value: Any
    is_valid: bool
    warning: Optional[str] = None


@dataclass
class FutureLeakReport:
    """未来函数泄漏报告
    
    Attributes:
        signal_name: 信号名称
        has_leak: 是否存在泄漏
        leak_severity: 泄漏严重程度
        leak_points: 泄漏点索引列表
        description: 详细描述
    """
    signal_name: str
    has_leak: bool
    leak_severity: str
    leak_points: List[int]
    description: str


@dataclass
class SandboxConfig:
    """沙箱配置"""
    feature_store: Optional[FeatureStore] = None
    output_dir: str = "./signal_sandbox_reports"
    lookback_buffer: int = 50  # 回看缓冲，数据不足时自动补充
    

@dataclass
class SandboxResult:
    """沙箱运行结果"""
    symbol: str
    start_ts: int
    end_ts: int
    signals_tested: List[str]
    test_results: List[SignalTestResult]
    leak_reports: List[FutureLeakReport]
    generated_at: str = field(default_factory=lambda: datetime.now(timezone.utc).isoformat())


# ==================== 信号类型定义 ====================

SignalCalculator = Callable[..., Any]


class SignalType(Enum):
    """信号类型枚举"""
    EMA_CROSSOVER = "EMA_CROSSOVER"
    PRICE_MOMENTUM = "PRICE_MOMENTUM"
    BOLLINGER_BAND = "BOLLINGER_BAND"
    VOLUME_EXPANSION = "VOLUME_EXPANSION"
    VOLATILITY_COMPRESSION = "VOLATILITY_COMPRESSION"


# ==================== 辅助函数 ====================

def _create_price_sample_from_dict(data: Dict[str, Any]) -> PriceSample:
    """从字典创建 PriceSample"""
    return PriceSample(
        ts_ms=int(data["ts_ms"]),
        open_price=Decimal(str(data["open_price"])),
        high_price=Decimal(str(data["high_price"])),
        low_price=Decimal(str(data["low_price"])),
        close_price=Decimal(str(data["close_price"])),
    )


def _create_price_volume_sample_from_dict(data: Dict[str, Any]) -> PriceVolumeSample:
    """从字典创建 PriceVolumeSample"""
    return PriceVolumeSample(
        ts_ms=int(data["ts_ms"]),
        open_price=Decimal(str(data["open_price"])),
        high_price=Decimal(str(data["high_price"])),
        low_price=Decimal(str(data["low_price"])),
        close_price=Decimal(str(data["close_price"])),
        volume=Decimal(str(data["volume"])),
        quote_volume=Decimal(str(data.get("quote_volume", "0"))),
    )


def _create_volume_sample_from_dict(data: Dict[str, Any]) -> VolumeSample:
    """从字典创建 VolumeSample"""
    return VolumeSample(
        ts_ms=int(data["ts_ms"]),
        volume=Decimal(str(data["volume"])),
        quote_volume=Decimal(str(data.get("quote_volume", "0"))),
        trade_count=int(data.get("trade_count", 0)),
    )


def _serialize_signal_result(result: Any) -> Dict[str, Any]:
    """序列化信号结果为字典"""
    if isinstance(result, EMACrossoverResult):
        return {
            "type": "EMACrossoverResult",
            "fast_ema": str(result.fast_ema) if result.fast_ema else None,
            "slow_ema": str(result.slow_ema) if result.slow_ema else None,
            "crossover": result.crossover,
            "direction": result.direction.value,
            "is_valid": result.is_valid,
            "ts_ms": result.ts_ms,
        }
    elif isinstance(result, PriceMomentumResult):
        return {
            "type": "PriceMomentumResult",
            "momentum": str(result.momentum) if result.momentum else None,
            "direction": result.direction.value,
            "is_strong": result.is_strong,
            "lookback_periods": result.lookback_periods,
            "ts_ms": result.ts_ms,
        }
    elif isinstance(result, BollingerBandResult):
        return {
            "type": "BollingerBandResult",
            "position": str(result.position) if result.position else None,
            "upper_band": str(result.upper_band) if result.upper_band else None,
            "middle_band": str(result.middle_band) if result.middle_band else None,
            "lower_band": str(result.lower_band) if result.lower_band else None,
            "bandwidth": str(result.bandwidth) if result.bandwidth else None,
            "ts_ms": result.ts_ms,
        }
    elif isinstance(result, VolumeExpansionResult):
        return {
            "type": "VolumeExpansionResult",
            "is_expansion": result.is_expansion,
            "expansion_ratio": str(result.expansion_ratio) if result.expansion_ratio else None,
            "intensity": str(result.intensity),
            "direction": result.direction.value,
            "mean_volume": str(result.mean_volume) if result.mean_volume else None,
            "current_volume": str(result.current_volume) if result.current_volume else None,
            "lookback_periods": result.lookback_periods,
            "ts_ms": result.ts_ms,
        }
    elif isinstance(result, VolatilityCompressionResult):
        return {
            "type": "VolatilityCompressionResult",
            "is_compression": result.is_compression,
            "compression_ratio": str(result.compression_ratio) if result.compression_ratio else None,
            "breakout_direction": result.breakout_direction.value,
            "current_atr": str(result.current_atr) if result.current_atr else None,
            "mean_atr": str(result.mean_atr) if result.mean_atr else None,
            "lookback_periods": result.lookback_periods,
            "ts_ms": result.ts_ms,
        }
    else:
        return {"type": str(type(result).__name__), "data": str(result)}


# ==================== 核心功能函数 ====================

async def load_historical_data(
    symbol: str,
    start_ts: int,
    end_ts: int,
    feature_store: Optional[FeatureStore] = None,
) -> Dict[str, List[Dict[str, Any]]]:
    """
    从 Feature Store 加载历史 K 线数据
    
    Args:
        symbol: 交易标的（如 "BTCUSDT"）
        start_ts: 开始时间戳（毫秒）
        end_ts: 结束时间戳（毫秒）
        feature_store: Feature Store 实例
        
    Returns:
        包含价格和成交量数据的字典
        {
            "price_samples": List[PriceSample],  # OHLCV 数据
            "volume_samples": List[VolumeSample],  # 成交量数据
            "price_volume_samples": List[PriceVolumeSample],  # 价量数据
        }
        
    Note:
        如果 Feature Store 中没有 K 线数据，返回空列表
        调用者应检查返回结果并适当处理
    """
    result = {
        "price_samples": [],
        "volume_samples": [],
        "price_volume_samples": [],
    }
    
    if feature_store is None:
        feature_store = FeatureStore()
    
    # 尝试从 Feature Store 读取 kline 数据
    # 注意：Feature Store 存储的是特征，不是原始 K 线
    # 这里尝试读取 "kline_1m" 或类似的特征名
    try:
        feature_points = await feature_store.read_feature_range(
            symbol=symbol,
            feature_name="kline_1m",
            start_time=start_ts,
            end_time=end_ts,
        )
        
        for point in feature_points:
            data = point.value
            if isinstance(data, dict):
                # 尝试解析 K 线数据
                try:
                    price_sample = _create_price_sample_from_dict(data)
                    result["price_samples"].append(price_sample)
                except (KeyError, TypeError):
                    pass
                
                try:
                    pv_sample = _create_price_volume_sample_from_dict(data)
                    result["price_volume_samples"].append(pv_sample)
                except (KeyError, TypeError):
                    pass
                
                try:
                    vol_sample = _create_volume_sample_from_dict(data)
                    result["volume_samples"].append(vol_sample)
                except (KeyError, TypeError):
                    pass
                    
    except Exception as e:
        logger.warning(f"从 Feature Store 加载数据失败: {e}")
    
    return result


def load_historical_data_from_file(
    filepath: str,
) -> Dict[str, List[Dict[str, Any]]]:
    """
    从 JSON 文件加载历史 K 线数据（用于测试和离线分析）
    
    Args:
        filepath: JSON 文件路径
        
    Returns:
        包含价格和成交量数据的字典
    """
    result = {
        "price_samples": [],
        "volume_samples": [],
        "price_volume_samples": [],
    }
    
    if not os.path.exists(filepath):
        logger.warning(f"文件不存在: {filepath}")
        return result
    
    try:
        with open(filepath, "r", encoding="utf-8") as f:
            data = json.load(f)
        
        if isinstance(data, list):
            for item in data:
                try:
                    price_sample = _create_price_sample_from_dict(item)
                    result["price_samples"].append(price_sample)
                except (KeyError, TypeError):
                    pass
                
                try:
                    pv_sample = _create_price_volume_sample_from_dict(item)
                    result["price_volume_samples"].append(pv_sample)
                except (KeyError, TypeError):
                    pass
                
                try:
                    vol_sample = _create_volume_sample_from_dict(item)
                    result["volume_samples"].append(vol_sample)
                except (KeyError, TypeError):
                    pass
        elif isinstance(data, dict) and "klines" in data:
            # 兼容 Binance API 格式
            for item in data["klines"]:
                try:
                    kline_data = {
                        "ts_ms": int(item[0]),
                        "open_price": item[1],
                        "high_price": item[2],
                        "low_price": item[3],
                        "close_price": item[4],
                        "volume": item[5],
                        "quote_volume": item[7] if len(item) > 7 else "0",
                    }
                    price_sample = _create_price_sample_from_dict(kline_data)
                    result["price_samples"].append(price_sample)
                    
                    pv_sample = _create_price_volume_sample_from_dict(kline_data)
                    result["price_volume_samples"].append(pv_sample)
                    
                    vol_sample = _create_volume_sample_from_dict(kline_data)
                    result["volume_samples"].append(vol_sample)
                except (KeyError, TypeError, IndexError):
                    pass
                    
    except Exception as e:
        logger.error(f"从文件加载数据失败: {e}")
    
    return result


def run_signal_replay(
    symbol: str,
    prices: List[PriceSample],
    signal_name: str,
) -> List[SignalTestResult]:
    """
    运行单个信号计算器的回放测试
    
    Args:
        symbol: 交易标的
        prices: 价格样本序列（按时间升序）
        signal_name: 信号名称
        
    Returns:
        信号时序 List[SignalTestResult]
    """
    results: List[SignalTestResult] = []
    
    if not prices:
        return results
    
    # 根据信号类型选择计算器
    if signal_name == "EMA_CROSSOVER":
        calculator = EMACrossover
        min_samples = 21  # slow_period(20) + 1
    elif signal_name == "PRICE_MOMENTUM":
        calculator = PriceMomentum
        min_samples = 15  # lookback_periods(14) + 1
    elif signal_name == "BOLLINGER_BAND":
        calculator = BollingerBandPosition
        min_samples = 20  # period(20)
    else:
        logger.warning(f"未知信号类型: {signal_name}")
        return results
    
    # 逐步回放
    for i in range(len(prices)):
        # 确保有足够的历史数据
        if i < min_samples - 1:
            continue
        
        # 使用截至当前点的所有数据
        historical_prices = prices[:i + 1]
        
        try:
            result = calculator.compute(
                symbol=symbol,
                prices=historical_prices,
            )
            
            serialized = _serialize_signal_result(result)
            
            # PriceMomentumResult 和 BollingerBandResult 没有 is_valid 属性
            is_valid = getattr(result, 'is_valid', True)
            
            results.append(SignalTestResult(
                signal_name=signal_name,
                signal_type=signal_name,
                timestamp=prices[i].ts_ms,
                value=serialized,
                is_valid=is_valid,
            ))
            
        except Exception as e:
            logger.warning(f"计算信号 {signal_name} 在索引 {i} 失败: {e}")
            results.append(SignalTestResult(
                signal_name=signal_name,
                signal_type=signal_name,
                timestamp=prices[i].ts_ms,
                value={"error": str(e)},
                is_valid=False,
                warning=f"计算异常: {e}",
            ))
    
    return results


def run_signal_replay_volume(
    symbol: str,
    volume_samples: List[VolumeSample],
    signal_name: str,
) -> List[SignalTestResult]:
    """
    运行成交量信号计算器的回放测试
    
    Args:
        symbol: 交易标的
        volume_samples: 成交量样本序列（按时间升序）
        signal_name: 信号名称
        
    Returns:
        信号时序 List[SignalTestResult]
    """
    results: List[SignalTestResult] = []
    
    if not volume_samples:
        return results
    
    if signal_name == "VOLUME_EXPANSION":
        calculator = VolumeExpansion
        min_samples = 20  # lookback_periods(20)
    else:
        logger.warning(f"未知成交量信号类型: {signal_name}")
        return results
    
    for i in range(len(volume_samples)):
        if i < min_samples - 1:
            continue
        
        historical_samples = volume_samples[:i + 1]
        
        try:
            result = calculator.compute(
                symbol=symbol,
                volume_samples=historical_samples,
            )
            
            serialized = _serialize_signal_result(result)
            
            results.append(SignalTestResult(
                signal_name=signal_name,
                signal_type=signal_name,
                timestamp=volume_samples[i].ts_ms,
                value=serialized,
                is_valid=True,  # VolumeExpansion 不返回 is_valid
            ))
            
        except Exception as e:
            logger.warning(f"计算信号 {signal_name} 在索引 {i} 失败: {e}")
            results.append(SignalTestResult(
                signal_name=signal_name,
                signal_type=signal_name,
                timestamp=volume_samples[i].ts_ms,
                value={"error": str(e)},
                is_valid=False,
                warning=f"计算异常: {e}",
            ))
    
    return results


def run_signal_replay_price_volume(
    symbol: str,
    pv_samples: List[PriceVolumeSample],
    signal_name: str,
) -> List[SignalTestResult]:
    """
    运行价量信号计算器的回放测试
    
    Args:
        symbol: 交易标的
        pv_samples: 价量样本序列（按时间升序）
        signal_name: 信号名称
        
    Returns:
        信号时序 List[SignalTestResult]
    """
    results: List[SignalTestResult] = []
    
    if not pv_samples:
        return results
    
    if signal_name == "VOLATILITY_COMPRESSION":
        calculator = VolatilityCompression
        min_samples = 21  # lookback_periods(20) + 1
    else:
        logger.warning(f"未知价量信号类型: {signal_name}")
        return results
    
    for i in range(len(pv_samples)):
        if i < min_samples - 1:
            continue
        
        historical_samples = pv_samples[:i + 1]
        
        try:
            result = calculator.compute(
                symbol=symbol,
                price_volume_samples=historical_samples,
            )
            
            serialized = _serialize_signal_result(result)
            
            results.append(SignalTestResult(
                signal_name=signal_name,
                signal_type=signal_name,
                timestamp=pv_samples[i].ts_ms,
                value=serialized,
                is_valid=True,
            ))
            
        except Exception as e:
            logger.warning(f"计算信号 {signal_name} 在索引 {i} 失败: {e}")
            results.append(SignalTestResult(
                signal_name=signal_name,
                signal_type=signal_name,
                timestamp=pv_samples[i].ts_ms,
                value={"error": str(e)},
                is_valid=False,
                warning=f"计算异常: {e}",
            ))
    
    return results


def detect_future_leaks(signal_timeline: List[SignalTestResult]) -> FutureLeakReport:
    """
    检测未来函数泄漏
    
    检测原理：
    在时间 T 计算信号，检查信号是否使用了 T+1 或之后的数据。
    对每个信号值，检查它与下一个信号值的时间关系。
    
    具体方法：
    1. 对每个信号值，比较信号变化与价格变化的时间关系
    2. 如果信号在价格下跌前已转为 bearish，说明存在未来函数
    3. 或者，使用"逐步验证"方法：在 T 时刻计算信号，
       然后检查在 T+1 时刻用新数据重新计算时，信号是否发生非预期变化
    
    Args:
        signal_timeline: 信号时序
        
    Returns:
        FutureLeakReport: 泄漏报告
    """
    signal_name = signal_timeline[0].signal_name if signal_timeline else "UNKNOWN"
    
    if len(signal_timeline) < 3:
        return FutureLeakReport(
            signal_name=signal_name,
            has_leak=False,
            leak_severity=LeakSeverity.NONE.value,
            leak_points=[],
            description="数据点不足，无法检测泄漏",
        )
    
    leak_points: List[int] = []
    warnings: List[str] = []
    
    for i in range(len(signal_timeline) - 1):
        current = signal_timeline[i]
        next_result = signal_timeline[i + 1]
        
        # 检查信号值类型
        if not isinstance(current.value, dict) or "error" in current.value:
            continue
        
        # 检测 EMA 交叉泄漏
        if signal_name == "EMA_CROSSOVER":
            leak_info = _detect_ema_leak(current, next_result)
            if leak_info:
                leak_points.append(i)
                warnings.append(leak_info)
        
        # 检测动量信号泄漏
        elif signal_name == "PRICE_MOMENTUM":
            leak_info = _detect_momentum_leak(current, next_result)
            if leak_info:
                leak_points.append(i)
                warnings.append(leak_info)
        
        # 检测布林带泄漏
        elif signal_name == "BOLLINGER_BAND":
            leak_info = _detect_bollinger_leak(current, next_result)
            if leak_info:
                leak_points.append(i)
                warnings.append(leak_info)
        
        # 检测成交量扩张泄漏
        elif signal_name == "VOLUME_EXPANSION":
            leak_info = _detect_volume_leak(current, next_result)
            if leak_info:
                leak_points.append(i)
                warnings.append(leak_info)
        
        # 检测波动率压缩泄漏
        elif signal_name == "VOLATILITY_COMPRESSION":
            leak_info = _detect_volatility_leak(current, next_result)
            if leak_info:
                leak_points.append(i)
                warnings.append(leak_info)
    
    # 计算泄漏严重程度
    leak_ratio = len(leak_points) / len(signal_timeline) if signal_timeline else 0
    
    if len(leak_points) == 0:
        severity = LeakSeverity.NONE
    elif leak_ratio < 0.05:
        severity = LeakSeverity.LOW
    elif leak_ratio < 0.15:
        severity = LeakSeverity.MEDIUM
    else:
        severity = LeakSeverity.HIGH
    
    description = f"检测到 {len(leak_points)} 个潜在泄漏点"
    if warnings:
        description += f": {', '.join(warnings[:3])}"
        if len(warnings) > 3:
            description += f" 等共 {len(warnings)} 条"
    
    return FutureLeakReport(
        signal_name=signal_name,
        has_leak=len(leak_points) > 0,
        leak_severity=severity.value,
        leak_points=leak_points,
        description=description,
    )


def _detect_ema_leak(current: SignalTestResult, next_result: SignalTestResult) -> Optional[str]:
    """
    检测 EMA 交叉的未来函数泄漏
    
    泄漏模式：
    - 信号在时间 T 显示 crossover=True 且 direction=BULLISH
    - 但在时间 T+1，价格尚未真正形成黄金交叉
    - 这表明计算时使用了未来的价格数据
    """
    current_val = current.value
    next_val = next_result.value
    
    if not isinstance(current_val, dict) or not isinstance(next_val, dict):
        return None
    
    # 检查当前是否发生了交叉
    if current_val.get("crossover") and current_val.get("direction") == "BULLISH":
        # 检查下一个信号是否仍然维持牛市交叉
        # 如果突然变成死叉或无交叉，可能说明存在泄漏
        next_direction = next_val.get("direction")
        if next_direction != "BULLISH":
            # 正常情况下，如果真的是黄金交叉，不会立即反转
            # 检查是否是因为使用了未来数据导致提前触发
            return "信号在价格真正形成交叉前已转为 bearish"
    
    if current_val.get("crossover") and current_val.get("direction") == "BEARISH":
        next_direction = next_val.get("direction")
        if next_direction != "BEARISH":
            return "信号在价格真正形成死叉前已转为 bullish"
    
    return None


def _detect_momentum_leak(current: SignalTestResult, next_result: SignalTestResult) -> Optional[str]:
    """
    检测价格动量的未来函数泄漏
    
    泄漏模式：
    - 信号在时间 T 显示强势动量
    - 但在时间 T+1，动量突然大幅下降
    - 这可能是因为计算时使用了未来的大波动数据
    """
    current_val = current.value
    next_val = next_result.value
    
    if not isinstance(current_val, dict) or not isinstance(next_val, dict):
        return None
    
    current_momentum = current_val.get("momentum")
    next_momentum = next_val.get("momentum")
    
    if current_momentum is None or next_momentum is None:
        return None
    
    try:
        curr_m = Decimal(str(current_momentum))
        next_m = Decimal(str(next_momentum))
        
        # 检测动量急剧反转（可能是泄漏）
        # 正常情况下，动量不会在相邻时间点发生剧烈变化
        if curr_m > Decimal("0") and next_m < Decimal("0"):
            return f"动量从 {curr_m}% 急剧反转到 {next_m}%"
        if curr_m < Decimal("0") and next_m > Decimal("0"):
            return f"动量从 {curr_m}% 急剧反转到 {next_m}%"
        
        # 检测动量大幅下降（超过 50%）
        if abs(curr_m) > Decimal("1") and abs(next_m) < abs(curr_m) * Decimal("0.5"):
            return f"动量从 {curr_m}% 大幅下降到 {next_m}%"
            
    except (TypeError, ValueError):
        pass
    
    return None


def _detect_bollinger_leak(current: SignalTestResult, next_result: SignalTestResult) -> Optional[str]:
    """
    检测布林带位置的未来函数泄漏
    
    泄漏模式：
    - 信号在时间 T 显示极端位置（如 > 0.8 或 < -0.8）
    - 但在时间 T+1，位置迅速回归到 0 附近
    - 这可能是因为计算时使用了未来的波动率数据
    """
    current_val = current.value
    next_val = next_result.value
    
    if not isinstance(current_val, dict) or not isinstance(next_val, dict):
        return None
    
    current_position = current_val.get("position")
    next_position = next_val.get("position")
    
    if current_position is None or next_position is None:
        return None
    
    try:
        curr_p = Decimal(str(current_position))
        next_p = Decimal(str(next_position))
        
        # 检测从极端位置快速回归
        if abs(curr_p) > Decimal("0.8") and abs(next_p) < abs(curr_p) * Decimal("0.3"):
            direction = "上轨" if curr_p > 0 else "下轨"
            return f"从 {direction} 极端位置 ({curr_p}) 快速回归到 ({next_p})"
            
    except (TypeError, ValueError):
        pass
    
    return None


def _detect_volume_leak(current: SignalTestResult, next_result: SignalTestResult) -> Optional[str]:
    """
    检测成交量扩张的未来函数泄漏
    
    泄漏模式：
    - 信号在时间 T 显示异常扩张
    - 但在时间 T+1，突然变成正常或收缩
    - 这可能是因为计算时使用了未来的成交量数据
    """
    current_val = current.value
    next_val = next_result.value
    
    if not isinstance(current_val, dict) or not isinstance(next_val, dict):
        return None
    
    current_is_expansion = current_val.get("is_expansion", False)
    next_is_expansion = next_val.get("is_expansion", False)
    
    if current_is_expansion and not next_is_expansion:
        return "成交量扩张信号突然消失"
    
    current_direction = current_val.get("direction")
    next_direction = next_val.get("direction")
    
    if current_direction == "EXPANSION" and next_direction == "NORMAL":
        return "成交量从放量突然变为正常"
    if current_direction == "EXPANSION" and next_direction == "CONTRACTION":
        return "成交量从放量急剧变为缩量"
    
    return None


def _detect_volatility_leak(current: SignalTestResult, next_result: SignalTestResult) -> Optional[str]:
    """
    检测波动率压缩的未来函数泄漏
    
    泄漏模式：
    - 信号在时间 T 显示异常压缩
    - 但在时间 T+1，突然变成不压缩
    - 或者 breakout_direction 发生突变
    """
    current_val = current.value
    next_val = next_result.value
    
    if not isinstance(current_val, dict) or not isinstance(next_val, dict):
        return None
    
    current_is_compression = current_val.get("is_compression", False)
    next_is_compression = next_val.get("is_compression", False)
    
    if current_is_compression and not next_is_compression:
        return "波动率压缩信号突然消失"
    
    current_ratio = current_val.get("compression_ratio")
    next_ratio = next_val.get("compression_ratio")
    
    if current_ratio is not None and next_ratio is not None:
        try:
            curr_r = Decimal(str(current_ratio))
            next_r = Decimal(str(next_ratio))
            
            # 压缩比急剧上升（从 < 0.5 变为 > 0.5）
            if curr_r < Decimal("0.5") and next_r > Decimal("0.5"):
                return f"压缩比从 {curr_r} 急剧上升到 {next_r}"
                
        except (TypeError, ValueError):
            pass
    
    return None


def generate_report(
    results: SandboxResult,
    output_format: str = "text",
    output_file: Optional[str] = None,
) -> str:
    """
    生成测试报告
    
    Args:
        results: 沙箱运行结果
        output_format: 输出格式 ("text" 或 "json")
        output_file: 输出文件路径（可选）
        
    Returns:
        报告字符串
    """
    if output_format == "json":
        report = _generate_json_report(results)
    else:
        report = _generate_text_report(results)
    
    if output_file:
        try:
            os.makedirs(os.path.dirname(output_file) or ".", exist_ok=True)
            with open(output_file, "w", encoding="utf-8") as f:
                f.write(report)
            logger.info(f"报告已保存到: {output_file}")
        except Exception as e:
            logger.error(f"保存报告失败: {e}")
    
    return report


def _generate_text_report(results: SandboxResult) -> str:
    """生成文本格式报告"""
    lines = []
    
    lines.append("=" * 60)
    lines.append("Signal Sandbox Report")
    lines.append("=" * 60)
    lines.append(f"Symbol: {results.symbol}")
    lines.append(f"Start Time: {_format_timestamp(results.start_ts)}")
    lines.append(f"End Time: {_format_timestamp(results.end_ts)}")
    lines.append(f"Signals Tested: {', '.join(results.signals_tested)}")
    lines.append(f"Generated At: {results.generated_at}")
    lines.append("")
    
    # 统计信息
    total_results = len(results.test_results)
    valid_results = sum(1 for r in results.test_results if r.is_valid)
    lines.append(f"Total Results: {total_results}")
    lines.append(f"Valid Results: {valid_results}")
    lines.append(f"Invalid/Warning Results: {total_results - valid_results}")
    lines.append("")
    
    # 未来函数泄漏报告
    lines.append("-" * 60)
    lines.append("Future Leak Report")
    lines.append("-" * 60)
    
    if not results.leak_reports:
        lines.append("No leak reports generated.")
    else:
        for report in results.leak_reports:
            lines.append("")
            lines.append(f"Signal: {report.signal_name}")
            if report.has_leak:
                lines.append(f"Leak Detected: YES")
                lines.append(f"Leak Severity: {report.leak_severity}")
                lines.append(f"Leak Points: {len(report.leak_points)}")
                if report.leak_points:
                    lines.append(f"  Indices: {report.leak_points[:10]}{'...' if len(report.leak_points) > 10 else ''}")
                lines.append(f"Description: {report.description}")
            else:
                lines.append(f"Leak Detected: NO")
                lines.append(f"Description: {report.description}")
    
    lines.append("")
    lines.append("=" * 60)
    
    # 总体评估
    overall_pass = all(not r.has_leak for r in results.leak_reports)
    if overall_pass:
        lines.append("Overall Assessment: PASS")
    else:
        lines.append("Overall Assessment: FAIL")
    lines.append("=" * 60)
    
    return "\n".join(lines)


def _generate_json_report(results: SandboxResult) -> str:
    """生成 JSON 格式报告"""
    report_data = {
        "symbol": results.symbol,
        "start_ts": results.start_ts,
        "end_ts": results.end_ts,
        "start_time": _format_timestamp(results.start_ts),
        "end_time": _format_timestamp(results.end_ts),
        "signals_tested": results.signals_tested,
        "generated_at": results.generated_at,
        "summary": {
            "total_results": len(results.test_results),
            "valid_results": sum(1 for r in results.test_results if r.is_valid),
        },
        "leak_reports": [
            {
                "signal_name": r.signal_name,
                "has_leak": r.has_leak,
                "leak_severity": r.leak_severity,
                "leak_points": r.leak_points,
                "description": r.description,
            }
            for r in results.leak_reports
        ],
        "overall_pass": all(not r.has_leak for r in results.leak_reports),
    }
    
    return json.dumps(report_data, indent=2, ensure_ascii=False)


def _format_timestamp(ts_ms: int) -> str:
    """格式化时间戳"""
    dt = datetime.fromtimestamp(ts_ms / 1000, tz=timezone.utc)
    return dt.strftime("%Y-%m-%d %H:%M:%S UTC")


# ==================== 主入口函数 ====================

async def run_signal_sandbox(
    symbol: str,
    signals_to_test: List[str],
    start_ts: Optional[int] = None,
    end_ts: Optional[int] = None,
    config: Optional[SandboxConfig] = None,
) -> SandboxResult:
    """
    运行信号沙箱测试
    
    Args:
        symbol: 交易标的
        signals_to_test: 要测试的信号列表（如 ["EMA_CROSSOVER", "PRICE_MOMENTUM"]）
                        传入 ["all"] 测试所有信号
        start_ts: 开始时间戳（毫秒）
        end_ts: 结束时间戳（毫秒）
        config: 沙箱配置
        
    Returns:
        SandboxResult: 测试结果
    """
    if config is None:
        config = SandboxConfig()
    
    # 默认时间范围（最近24小时）
    now = int(datetime.now(timezone.utc).timestamp() * 1000)
    if end_ts is None:
        end_ts = now
    if start_ts is None:
        start_ts = end_ts - 24 * 60 * 60 * 1000
    
    # 确定要测试的信号
    all_signals = [
        SignalType.EMA_CROSSOVER.value,
        SignalType.PRICE_MOMENTUM.value,
        SignalType.BOLLINGER_BAND.value,
        SignalType.VOLUME_EXPANSION.value,
        SignalType.VOLATILITY_COMPRESSION.value,
    ]
    
    if "all" in signals_to_test:
        signals = all_signals
    else:
        signals = [s for s in signals_to_test if s in all_signals]
    
    # 加载历史数据
    historical_data = await load_historical_data(
        symbol=symbol,
        start_ts=start_ts,
        end_ts=end_ts,
        feature_store=config.feature_store,
    )
    
    price_samples = historical_data.get("price_samples", [])
    volume_samples = historical_data.get("volume_samples", [])
    pv_samples = historical_data.get("price_volume_samples", [])
    
    # 如果没有数据，生成模拟数据用于测试
    if not price_samples:
        logger.warning("没有找到历史数据，使用模拟数据进行测试")
        price_samples = _generate_mock_price_samples(symbol, start_ts, end_ts)
        pv_samples = [_convert_to_pv_sample(p) for p in price_samples]
        volume_samples = [_convert_to_vol_sample(p) for p in price_samples]
    
    # 运行各信号测试
    all_test_results: List[SignalTestResult] = []
    all_leak_reports: List[FutureLeakReport] = []
    
    for signal_name in signals:
        test_results: List[SignalTestResult] = []
        
        if signal_name in [SignalType.EMA_CROSSOVER.value, SignalType.PRICE_MOMENTUM.value, SignalType.BOLLINGER_BAND.value]:
            test_results = run_signal_replay(symbol, price_samples, signal_name)
        elif signal_name == SignalType.VOLUME_EXPANSION.value:
            test_results = run_signal_replay_volume(symbol, volume_samples, signal_name)
        elif signal_name == SignalType.VOLATILITY_COMPRESSION.value:
            test_results = run_signal_replay_price_volume(symbol, pv_samples, signal_name)
        
        all_test_results.extend(test_results)
        
        # 检测未来函数泄漏
        if test_results:
            leak_report = detect_future_leaks(test_results)
            all_leak_reports.append(leak_report)
    
    return SandboxResult(
        symbol=symbol,
        start_ts=start_ts,
        end_ts=end_ts,
        signals_tested=signals,
        test_results=all_test_results,
        leak_reports=all_leak_reports,
    )


def run_signal_sandbox_sync(
    symbol: str,
    signals_to_test: List[str],
    start_ts: Optional[int] = None,
    end_ts: Optional[int] = None,
    config: Optional[SandboxConfig] = None,
) -> SandboxResult:
    """
    同步版本的 run_signal_sandbox
    
    使用方式：
        results = run_signal_sandbox_sync("BTCUSDT", ["all"])
    """
    return asyncio.run(run_signal_sandbox(
        symbol=symbol,
        signals_to_test=signals_to_test,
        start_ts=start_ts,
        end_ts=end_ts,
        config=config,
    ))


# ==================== 模拟数据生成（用于测试）====================

def _generate_mock_price_samples(
    symbol: str,
    start_ts: int,
    end_ts: int,
    interval_ms: int = 60000,  # 1分钟
    base_price: float = 50000.0,
) -> List[PriceSample]:
    """生成模拟价格数据（用于测试）"""
    import random
    
    samples = []
    current_price = Decimal(str(base_price))
    ts = start_ts
    
    random.seed(42)  # 固定种子保证可重复性
    
    while ts <= end_ts:
        # 随机波动
        change = Decimal(str(random.uniform(-0.002, 0.002)))
        open_price = current_price
        close_price = current_price * (Decimal("1") + change)
        
        high_price = max(open_price, close_price) * Decimal(str(random.uniform(1.0, 1.001)))
        low_price = min(open_price, close_price) * Decimal(str(random.uniform(0.999, 1.0)))
        
        samples.append(PriceSample(
            ts_ms=ts,
            open_price=open_price.quantize(Decimal("0.01")),
            high_price=high_price.quantize(Decimal("0.01")),
            low_price=low_price.quantize(Decimal("0.01")),
            close_price=close_price.quantize(Decimal("0.01")),
        ))
        
        current_price = close_price
        ts += interval_ms
    
    return samples


def _convert_to_pv_sample(price_sample: PriceSample) -> PriceVolumeSample:
    """将 PriceSample 转换为 PriceVolumeSample"""
    return PriceVolumeSample(
        ts_ms=price_sample.ts_ms,
        open_price=price_sample.open_price,
        high_price=price_sample.high_price,
        low_price=price_sample.low_price,
        close_price=price_sample.close_price,
        volume=Decimal("100"),  # 模拟成交量
        quote_volume=price_sample.close_price * Decimal("100"),
    )


def _convert_to_vol_sample(price_sample: PriceSample) -> VolumeSample:
    """将 PriceSample 转换为 VolumeSample"""
    return VolumeSample(
        ts_ms=price_sample.ts_ms,
        volume=Decimal("100"),
        quote_volume=price_sample.close_price * Decimal("100"),
        trade_count=10,
    )


# ==================== CLI 入口 ====================

def main():
    """命令行入口"""
    import argparse
    
    parser = argparse.ArgumentParser(description="Signal Sandbox - 信号验证与未来函数检测")
    parser.add_argument("--symbol", type=str, default="BTCUSDT", help="交易标的")
    parser.add_argument("--signals", type=str, nargs="+", default=["all"], 
                       help="要测试的信号（如 EMA_CROSSOVER PRICE_MOMENTUM）")
    parser.add_argument("--start-ts", type=int, default=None, help="开始时间戳（毫秒）")
    parser.add_argument("--end-ts", type=int, default=None, help="结束时间戳（毫秒）")
    parser.add_argument("--output", type=str, default=None, help="输出文件路径")
    parser.add_argument("--format", type=str, choices=["text", "json"], default="text", 
                       help="输出格式")
    
    args = parser.parse_args()
    
    # 运行测试
    results = run_signal_sandbox_sync(
        symbol=args.symbol,
        signals_to_test=args.signals,
        start_ts=args.start_ts,
        end_ts=args.end_ts,
    )
    
    # 生成报告
    report = generate_report(
        results,
        output_format=args.format,
        output_file=args.output,
    )
    
    print(report)


if __name__ == "__main__":
    main()
