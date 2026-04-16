"""
Qlib Data Converter - 研究数据标准化与契约冻结
=================================================

职责：
- 将 FeatureStore / K线数据转换为 Qlib 可训练格式
- 确保"训练与实盘两套宇宙"的数据一致性
- 维护 feature_version 映射规范

约束：
- 本模块位于 scripts/ (研究域)，不进入 Core Plane 执行链路
- 所有转换结果必须确定性可复现
- 缺失数据、异常跳点、对齐失败触发 Fail-Closed 标记
"""

from __future__ import annotations

import hashlib
import logging
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Optional, Dict, Any, List, Tuple, Callable

logger = logging.getLogger(__name__)


# =============================================================================
# Data Contract Types (数据契约定义)
# =============================================================================

@dataclass(frozen=True)
class DataContract:
    """
    数据契约 - 冻结训练与推理的数据语义
    
    所有进入 Qlib 训练的数据必须符合此契约。
    """
    symbol: str
    feature_names: Tuple[str, ...]
    start_time_ms: int
    end_time_ms: int
    version: str  # feature_version 簇
    timezone: str = "UTC"
    resample_rule: str = "1D"  # K线重采样规则
    
    def contract_hash(self) -> str:
        """计算契约哈希，用于版本追踪"""
        content = f"{self.symbol}:{self.feature_names}:{self.version}:{self.resample_rule}"
        return hashlib.sha256(content.encode()).hexdigest()[:16]


@dataclass
class DataQualityReport:
    """
    数据质量报告
    
    用于验证数据转换过程中的质量
    """
    contract_hash: str
    total_rows: int
    missing_rows: int
    gap_count: int  # 时间序列间隙数
    outlier_count: int
    alignment_failures: List[str]  # 对齐失败详情
    
    @property
    def missing_pct(self) -> float:
        return (self.missing_rows / self.total_rows * 100) if self.total_rows > 0 else 0.0
    
    @property
    def is_healthy(self) -> bool:
        """数据健康度检查 - Fail-Closed"""
        return (
            self.missing_pct < 5.0 and  # 缺失率 < 5%
            self.gap_count == 0 and      # 无时间间隙
            self.outlier_count == 0 and   # 无异常跳点
            len(self.alignment_failures) == 0  # 无对齐失败
        )
    
    def raise_on_unhealthy(self) -> None:
        """健康度不达标则抛出异常"""
        if not self.is_healthy:
            raise DataQualityError(
                f"Data quality check failed: "
                f"missing={self.missing_pct:.2f}%, gaps={self.gap_count}, "
                f"outliers={self.outlier_count}, align_failures={len(self.alignment_failures)}"
            )


class DataQualityError(Exception):
    """数据质量不达标异常"""
    pass


@dataclass
class FeatureMapping:
    """
    特征版本映射
    
    维护训练集与线上推理的版本追踪
    """
    feature_name: str
    source_field: str        # 原始字段名
    qlib_field: str          # Qlib 格式字段名
    version: str             # feature version
    transform_fn: Optional[str] = None  # 转换函数名
    description: str = ""


# =============================================================================
# Standard Feature Catalog (标准特征目录)
# =============================================================================

@dataclass
class FeatureCatalog:
    """
    特征目录 - 标准化可复用特征定义
    
    策略：
    1. 基础价量特征 (OHLCV)
    2. 技术指标特征 (EMA, RSI, BOLL)
    3. 资金结构特征 (funding_rate, OI)
    4. 情绪特征 (liquidation, stablecoin_supply)
    """
    
    # 基础价量特征
    OHLCV_FIELDS = ("open", "high", "low", "close", "volume")
    
    # 技术指标特征
    TECHNICAL_FIELDS = (
        "ema_20", "ema_50", "ema_200",
        "rsi_14", "rsi_28",
        "boll_upper", "boll_middle", "boll_lower",
        "volume_ratio", "price_momentum",
    )
    
    # 资金结构特征
    CAPITAL_STRUCTURE_FIELDS = (
        "funding_rate", "funding_rate_zscore",
        "open_interest", "oi_change_rate",
        "long_short_ratio", "long_short_ratio_zscore",
    )
    
    # 情绪特征
    SENTIMENT_FIELDS = (
        "stablecoin_supply", "liquidation_bid_notional",
        "liquidation_ask_notional", "liquidation_net_imbalance",
    )
    
    # 全量字段
    ALL_FIELDS = OHLCV_FIELDS + TECHNICAL_FIELDS + CAPITAL_STRUCTURE_FIELDS + SENTIMENT_FIELDS
    
    @classmethod
    def get_mapping(cls, feature_name: str) -> Optional[FeatureMapping]:
        """获取特征映射"""
        mappings = {
            # OHLCV - 直接映射
            "open": FeatureMapping("open", "open", "open", "v1"),
            "high": FeatureMapping("high", "high", "high", "v1"),
            "low": FeatureMapping("low", "low", "low", "v1"),
            "close": FeatureMapping("close", "close", "close", "v1"),
            "volume": FeatureMapping("volume", "volume", "volume", "v1"),
            
            # 技术指标
            "ema_20": FeatureMapping("ema_20", "ema_20", "EMA20", "v1", "ema_transform"),
            "ema_50": FeatureMapping("ema_50", "ema_50", "EMA50", "v1", "ema_transform"),
            "rsi_14": FeatureMapping("rsi_14", "rsi_14", "RSI14", "v1", "rsi_transform"),
            "boll_upper": FeatureMapping("boll_upper", "boll_upper", "BOLL_UPPER", "v1"),
            "boll_middle": FeatureMapping("boll_middle", "boll_middle", "BOLL_MIDDLE", "v1"),
            "boll_lower": FeatureMapping("boll_lower", "boll_lower", "BOLL_LOWER", "v1"),
            
            # 资金结构
            "funding_rate": FeatureMapping("funding_rate", "funding_rate", "FUNDING_RATE", "v1"),
            "open_interest": FeatureMapping("open_interest", "open_interest", "OI", "v1"),
            "long_short_ratio": FeatureMapping("long_short_ratio", "long_short_ratio", "LS_RATIO", "v1"),
            
            # 情绪
            "stablecoin_supply": FeatureMapping("stablecoin_supply", "stablecoin_supply", "SC_SUPPLY", "v1"),
            "liquidation_bid_notional": FeatureMapping("liquidation_bid_notional", "liquidation_bid_notional", "LIQ_BID", "v1"),
            "liquidation_ask_notional": FeatureMapping("liquidation_ask_notional", "liquidation_ask_notional", "LIQ_ASK", "v1"),
        }
        return mappings.get(feature_name)


# =============================================================================
# Qlib Dataset Handler (Qlib 数据集处理器)
# =============================================================================

class QlibDatasetHandler:
    """
    Qlib 数据集处理器
    
    负责：
    1. 从 FeatureStore 读取原始数据
    2. 按照 DataContract 进行转换
    3. 输出 Qlib 格式 (Pandas DataFrame)
    4. 生成 DataQualityReport
    
    注意：此模块依赖 FeatureStore，属于研究域 (scripts/)，不进入 Core Plane
    """
    
    def __init__(self, feature_store_reader: Optional[Callable] = None):
        """
        Args:
            feature_store_reader: FeatureStore 读取函数，可选（用于注入测试 mock）
        """
        self._feature_store_reader = feature_store_reader
        self._contract: Optional[DataContract] = None
        self._catalog = FeatureCatalog()
    
    def set_contract(self, contract: DataContract) -> None:
        """设置数据契约"""
        self._contract = contract
    
    async def read_from_feature_store(
        self,
        symbol: str,
        feature_names: List[str],
        start_time_ms: int,
        end_time_ms: int,
        version: str,
    ) -> Dict[str, List]:
        """
        从 FeatureStore 读取数据
        
        Returns:
            Dict[str, List] - 特征名 -> 值列表
        """
        from trader.adapters.persistence.feature_store import FeatureStore
        
        store = FeatureStore()
        result: Dict[str, List] = {}
        
        for feature_name in feature_names:
            points = await store.read_feature_range(
                symbol=symbol,
                feature_name=feature_name,
                start_time=start_time_ms,
                end_time=end_time_ms,
                version=version,
            )
            result[feature_name] = [p.value for p in points]
        
        return result
    
    async def convert_to_qlib_format(
        self,
        raw_data: Dict[str, List],
        timestamps: List[int],
    ) -> Dict[str, Any]:
        """
        将原始数据转换为 Qlib 格式
        
        Args:
            raw_data: 原始特征数据
            timestamps: 时间戳列表
            
        Returns:
            Dict containing:
            - df: Pandas DataFrame (Qlib 格式)
            - quality_report: DataQualityReport
        """
        import pandas as pd
        
        # 构建 DataFrame
        df = pd.DataFrame(index=timestamps)
        df.index.name = "datetime"
        
        for feature_name, values in raw_data.items():
            mapping = self._catalog.get_mapping(feature_name)
            if mapping:
                df[mapping.qlib_field] = values
            else:
                df[feature_name] = values
        
        # 生成质量报告
        report = self._generate_quality_report(df, raw_data)
        
        # Fail-Closed: 数据质量不达标则抛异常
        report.raise_on_unhealthy()
        
        return {
            "df": df,
            "quality_report": report,
        }
    
    def _generate_quality_report(
        self,
        df: "pd.DataFrame",
        raw_data: Dict[str, List],
    ) -> DataQualityReport:
        """生成数据质量报告"""
        
        total_rows = len(df)
        missing_rows = int(df.isnull().sum().sum())
        
        # 检测时间序列间隙
        timestamps = df.index.tolist() if hasattr(df.index, 'tolist') else list(df.index)
        gap_count = 0
        if len(timestamps) > 1:
            # 简化的间隙检测逻辑：检查是否超过1天
            for i in range(1, len(timestamps)):
                if timestamps[i] - timestamps[i-1] > 86400000:  # > 1 day
                    gap_count += 1
        
        # 检测异常跳点 (使用 IQR 方法简化)
        outlier_count = 0
        for col in df.columns:
            if df[col].dtype in ['float64', 'int64']:
                Q1 = df[col].quantile(0.25)
                Q3 = df[col].quantile(0.75)
                IQR = Q3 - Q1
                outliers = ((df[col] < Q1 - 3*IQR) | (df[col] > Q3 + 3*IQR)).sum()
                outlier_count += outliers
        
        # 对齐失败记录
        alignment_failures = []
        for feature_name, values in raw_data.items():
            if len(values) != total_rows:
                alignment_failures.append(
                    f"{feature_name}: expected {total_rows}, got {len(values)}"
                )
        
        return DataQualityReport(
            contract_hash=self._contract.contract_hash() if self._contract else "unknown",
            total_rows=total_rows,
            missing_rows=missing_rows,
            gap_count=gap_count,
            outlier_count=outlier_count,
            alignment_failures=alignment_failures,
        )
    
    def resample_klines(
        self,
        df: "pd.DataFrame",
        rule: str = "1D",
    ) -> "pd.DataFrame":
        """
        K线重采样
        
        Args:
            df: 原始 K线 DataFrame
            rule: 重采样规则 (如 "1D", "4H", "1H")
            
        Returns:
            重采样后的 DataFrame
        """
        import pandas as pd  # pylint: disable=import-error
        
        # 确保 index 是 datetime
        if not isinstance(df.index, pd.DatetimeIndex):
            df.index = pd.to_datetime(df.index, unit='ms')
        
        # 重采样 OHLCV
        ohlcv_dict = {}
        for col in ("open", "high", "low", "close", "volume"):
            if col in df.columns:
                if col in ("open", "high", "low", "close"):
                    ohlcv_dict[col] = df[col].resample(rule).last()
                else:
                    ohlcv_dict[col] = df[col].resample(rule).sum()
        
        resampled = pd.DataFrame(ohlcv_dict)
        resampled = resampled.dropna()
        
        return resampled
    
    async def create_dataset(
        self,
        contract: DataContract,
        store_reader: Optional[Callable] = None,
    ) -> Tuple[Dict[str, Any], DataQualityReport]:
        """
        创建 Qlib 数据集
        
        完整流程：
        1. 设置数据契约
        2. 从 FeatureStore 读取数据
        3. 转换格式并验证质量
        4. 返回数据集和质量报告
        """
        self.set_contract(contract)
        
        # 读取原始数据
        raw_data = await self.read_from_feature_store(
            symbol=contract.symbol,
            feature_names=list(contract.feature_names),
            start_time_ms=contract.start_time_ms,
            end_time_ms=contract.end_time_ms,
            version=contract.version,
        )
        
        # 获取时间戳列表
        timestamps = list(range(
            contract.start_time_ms,
            contract.end_time_ms,
            86400000 if contract.resample_rule == "1D" else 3600000
        ))
        
        # 转换格式
        result = await self.convert_to_qlib_format(raw_data, timestamps)
        
        return result, result["quality_report"]
    
    def export_to_csv(self, df: "pd.DataFrame", path: str) -> str:
        """
        导出数据集到 CSV
        
        用于 Qlib 训练流水线输入
        """
        df.to_csv(path)
        logger.info(f"Dataset exported to {path}")
        return path
    
    def get_version_reference(self, contract: DataContract) -> Dict[str, str]:
        """
        获取版本引用信息
        
        用于训练与推理的版本追踪
        """
        return {
            "feature_version": contract.version,
            "contract_hash": contract.contract_hash(),
            "symbol": contract.symbol,
            "resample_rule": contract.resample_rule,
            "timezone": contract.timezone,
        }


# =============================================================================
# Main Entry Point (主入口 - 供 Hermes 编排调用)
# =============================================================================

async def create_qlib_dataset(
    symbol: str,
    start_time_ms: int,
    end_time_ms: int,
    feature_version: str = "v1",
    resample_rule: str = "1D",
    output_path: Optional[str] = None,
) -> Tuple[str, DataQualityReport]:
    """
    创建 Qlib 数据集的主入口函数
    
    供 Hermes 编排脚本调用
    
    Args:
        symbol: 交易标的 (如 "BTCUSDT")
        start_time_ms: 开始时间戳 (毫秒)
        end_time_ms: 结束时间戳 (毫秒)
        feature_version: 特征版本 (如 "v1")
        resample_rule: 重采样规则 (如 "1D")
        output_path: 输出路径 (可选)
        
    Returns:
        Tuple of (dataset_path, quality_report)
    """
    from trader.adapters.persistence.feature_store import FeatureStore
    
    # 创建数据契约
    contract = DataContract(
        symbol=symbol,
        feature_names=FeatureCatalog.ALL_FIELDS,
        start_time_ms=start_time_ms,
        end_time_ms=end_time_ms,
        version=feature_version,
        resample_rule=resample_rule,
    )
    
    # 创建处理器
    handler = QlibDatasetHandler()
    
    # 创建数据集
    result, report = await handler.create_dataset(contract)
    
    df = result["df"]
    
    # 导出到 CSV
    if output_path:
        handler.export_to_csv(df, output_path)
    else:
        # 默认输出路径
        output_path = f"data/qlib/{symbol}_{feature_version}_{start_time_ms}_{end_time_ms}.csv"
        handler.export_to_csv(df, output_path)
    
    logger.info(
        f"Qlib dataset created: {output_path}, "
        f"rows={report.total_rows}, quality={report.is_healthy}"
    )
    
    return output_path, report


# =============================================================================
# CLI Interface (命令行接口)
# =============================================================================

if __name__ == "__main__":
    import asyncio
    import sys
    
    async def main():
        if len(sys.argv) < 5:
            print("Usage: python qlib_data_converter.py <symbol> <start_ms> <end_ms> <feature_version>")
            print("Example: python qlib_data_converter.py BTCUSDT 1704067200000 1711996800000 v1")
            sys.exit(1)
        
        symbol = sys.argv[1]
        start_ms = int(sys.argv[2])
        end_ms = int(sys.argv[3])
        version = sys.argv[4]
        
        output_path, report = await create_qlib_dataset(
            symbol=symbol,
            start_time_ms=start_ms,
            end_time_ms=end_ms,
            feature_version=version,
        )
        
        print(f"Dataset: {output_path}")
        print(f"Quality: healthy={report.is_healthy}, missing={report.missing_pct:.2f}%")
        
        if not report.is_healthy:
            print(f"WARNING: Data quality issues detected!")
            for failure in report.alignment_failures:
                print(f"  - {failure}")
            sys.exit(1)
    
    asyncio.run(main())