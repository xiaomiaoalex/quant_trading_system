"""
单元测试: FeatureStore范围查询扩展
==================================

测试 read_feature_range() 方法的时间范围批量查询功能。

覆盖：
- 正常路径：查询返回正确范围内的数据
- 边界情况：start_time和end_time边界
- 版本过滤
- 空结果
- 无数据情况
"""
import pytest
from unittest.mock import AsyncMock, patch

from trader.adapters.persistence.feature_store import (
    FeatureStore,
    FeaturePoint,
)
from trader.storage.in_memory import ControlPlaneInMemoryStorage


class TestFeatureStoreRange:
    """单元测试: FeatureStore范围查询"""

    def setup_method(self):
        self.storage = ControlPlaneInMemoryStorage()
        self.store = FeatureStore(storage=self.storage)
        # Mock _ensure_postgres to always use in-memory storage
        # This prevents PostgreSQL state pollution between tests
        self._original_ensure_postgres = self.store._ensure_postgres
        self.store._ensure_postgres = AsyncMock(return_value=False)

    def teardown_method(self):
        # Restore original method to prevent test pollution
        self.store._ensure_postgres = self._original_ensure_postgres

    @pytest.mark.asyncio
    async def test_read_feature_range_returns_list_of_feature_points(self):
        """验证返回类型为 List[FeaturePoint]"""
        # Arrange - 写入多个时间点的数据
        base_ts = 1700000000000
        for i in range(3):
            await self.store.write_feature(
                symbol="BTCUSDT",
                feature_name="ema_20",
                version="v1",
                ts_ms=base_ts + i * 1000,
                value=100.0 + i,
            )

        # Act
        result = await self.store.read_feature_range(
            symbol="BTCUSDT",
            feature_name="ema_20",
            start_time=base_ts,
            end_time=base_ts + 2000,
        )

        # Assert
        assert isinstance(result, list)
        assert len(result) == 3
        assert all(isinstance(fp, FeaturePoint) for fp in result)

    @pytest.mark.asyncio
    async def test_read_feature_range_ordered_by_timestamp(self):
        """验证返回结果按ts_ms升序排列"""
        base_ts = 1700000000000
        # 写入顺序打乱
        await self.store.write_feature(
            symbol="BTCUSDT",
            feature_name="ema_20",
            version="v1",
            ts_ms=base_ts + 2000,
            value=200.0,
        )
        await self.store.write_feature(
            symbol="BTCUSDT",
            feature_name="ema_20",
            version="v1",
            ts_ms=base_ts,
            value=100.0,
        )
        await self.store.write_feature(
            symbol="BTCUSDT",
            feature_name="ema_20",
            version="v1",
            ts_ms=base_ts + 1000,
            value=150.0,
        )

        result = await self.store.read_feature_range(
            symbol="BTCUSDT",
            feature_name="ema_20",
            start_time=base_ts,
            end_time=base_ts + 2000,
        )

        assert result[0].ts_ms == base_ts
        assert result[1].ts_ms == base_ts + 1000
        assert result[2].ts_ms == base_ts + 2000
        assert result[0].value == 100.0
        assert result[1].value == 150.0
        assert result[2].value == 200.0

    @pytest.mark.asyncio
    async def test_read_feature_range_start_boundary_inclusive(self):
        """验证start_time边界包含（闭区间）"""
        base_ts = 1700000000000
        await self.store.write_feature(
            symbol="BTCUSDT",
            feature_name="ema_20",
            version="v1",
            ts_ms=base_ts,
            value=100.0,
        )

        result = await self.store.read_feature_range(
            symbol="BTCUSDT",
            feature_name="ema_20",
            start_time=base_ts,
            end_time=base_ts + 1000,
        )

        assert len(result) == 1
        assert result[0].ts_ms == base_ts

    @pytest.mark.asyncio
    async def test_read_feature_range_end_boundary_inclusive(self):
        """验证end_time边界包含（闭区间）"""
        base_ts = 1700000000000
        await self.store.write_feature(
            symbol="BTCUSDT",
            feature_name="ema_20",
            version="v1",
            ts_ms=base_ts + 1000,
            value=150.0,
        )

        result = await self.store.read_feature_range(
            symbol="BTCUSDT",
            feature_name="ema_20",
            start_time=base_ts,
            end_time=base_ts + 1000,
        )

        assert len(result) == 1
        assert result[0].ts_ms == base_ts + 1000

    @pytest.mark.asyncio
    async def test_read_feature_range_empty_result_no_matching_data(self):
        """验证无匹配数据时返回空列表"""
        base_ts = 1700000000000
        # 写入数据但不在查询范围内
        await self.store.write_feature(
            symbol="BTCUSDT",
            feature_name="ema_20",
            version="v1",
            ts_ms=base_ts + 10000,
            value=200.0,
        )

        result = await self.store.read_feature_range(
            symbol="BTCUSDT",
            feature_name="ema_20",
            start_time=base_ts,
            end_time=base_ts + 1000,
        )

        assert result == []

    @pytest.mark.asyncio
    async def test_read_feature_range_with_version_filter(self):
        """验证版本过滤功能"""
        base_ts = 1700000000000
        # 写入v1版本
        await self.store.write_feature(
            symbol="BTCUSDT",
            feature_name="ema_20",
            version="v1",
            ts_ms=base_ts,
            value=100.0,
        )
        await self.store.write_feature(
            symbol="BTCUSDT",
            feature_name="ema_20",
            version="v1",
            ts_ms=base_ts + 1000,
            value=110.0,
        )
        # 写入v2版本
        await self.store.write_feature(
            symbol="BTCUSDT",
            feature_name="ema_20",
            version="v2",
            ts_ms=base_ts,
            value=200.0,
        )
        await self.store.write_feature(
            symbol="BTCUSDT",
            feature_name="ema_20",
            version="v2",
            ts_ms=base_ts + 1000,
            value=210.0,
        )

        # 只查询v1版本
        result = await self.store.read_feature_range(
            symbol="BTCUSDT",
            feature_name="ema_20",
            start_time=base_ts,
            end_time=base_ts + 1000,
            version="v1",
        )

        assert len(result) == 2
        assert all(fp.version == "v1" for fp in result)

    @pytest.mark.asyncio
    async def test_read_feature_range_version_filter_no_match(self):
        """验证版本过滤无匹配时返回空列表"""
        base_ts = 1700000000000
        # 只写入v1版本
        await self.store.write_feature(
            symbol="BTCUSDT",
            feature_name="ema_20",
            version="v1",
            ts_ms=base_ts,
            value=100.0,
        )

        # 查询v2版本
        result = await self.store.read_feature_range(
            symbol="BTCUSDT",
            feature_name="ema_20",
            start_time=base_ts,
            end_time=base_ts + 1000,
            version="v2",
        )

        assert result == []

    @pytest.mark.asyncio
    async def test_read_feature_range_different_symbol_no_cross_pollution(self):
        """验证不同symbol之间不互相污染"""
        base_ts = 1700000000000
        # 写入BTCUSDT数据
        await self.store.write_feature(
            symbol="BTCUSDT",
            feature_name="ema_20",
            version="v1",
            ts_ms=base_ts,
            value=100.0,
        )
        # 写入ETHUSDT数据
        await self.store.write_feature(
            symbol="ETHUSDT",
            feature_name="ema_20",
            version="v1",
            ts_ms=base_ts,
            value=200.0,
        )

        # 只查询BTCUSDT
        result = await self.store.read_feature_range(
            symbol="BTCUSDT",
            feature_name="ema_20",
            start_time=base_ts,
            end_time=base_ts + 1000,
        )

        assert len(result) == 1
        assert result[0].symbol == "BTCUSDT"
        assert result[0].value == 100.0

    @pytest.mark.asyncio
    async def test_read_feature_range_different_feature_name_no_cross_pollution(self):
        """验证不同feature_name之间不互相污染"""
        base_ts = 1700000000000
        # 写入ema_20数据
        await self.store.write_feature(
            symbol="BTCUSDT",
            feature_name="ema_20",
            version="v1",
            ts_ms=base_ts,
            value=100.0,
        )
        # 写入volume_ratio数据
        await self.store.write_feature(
            symbol="BTCUSDT",
            feature_name="volume_ratio",
            version="v1",
            ts_ms=base_ts,
            value=1.5,
        )

        # 只查询ema_20
        result = await self.store.read_feature_range(
            symbol="BTCUSDT",
            feature_name="ema_20",
            start_time=base_ts,
            end_time=base_ts + 1000,
        )

        assert len(result) == 1
        assert result[0].feature_name == "ema_20"
        assert result[0].value == 100.0

    @pytest.mark.asyncio
    async def test_read_feature_range_same_timestamp_different_version(self):
        """验证同一时间戳不同版本都能被查询到"""
        base_ts = 1700000000000
        # 同一时间戳写入v1和v2
        await self.store.write_feature(
            symbol="BTCUSDT",
            feature_name="ema_20",
            version="v1",
            ts_ms=base_ts,
            value=100.0,
        )
        await self.store.write_feature(
            symbol="BTCUSDT",
            feature_name="ema_20",
            version="v2",
            ts_ms=base_ts,
            value=200.0,
        )

        result = await self.store.read_feature_range(
            symbol="BTCUSDT",
            feature_name="ema_20",
            start_time=base_ts,
            end_time=base_ts,
        )

        assert len(result) == 2
        versions = {fp.version for fp in result}
        assert versions == {"v1", "v2"}

    @pytest.mark.asyncio
    async def test_read_feature_range_feature_point_structure(self):
        """验证FeaturePoint结构完整性"""
        base_ts = 1700000000000
        await self.store.write_feature(
            symbol="BTCUSDT",
            feature_name="ema_20",
            version="v1",
            ts_ms=base_ts,
            value=123.45,
        )

        result = await self.store.read_feature_range(
            symbol="BTCUSDT",
            feature_name="ema_20",
            start_time=base_ts,
            end_time=base_ts,
        )

        assert len(result) == 1
        fp = result[0]
        assert fp.symbol == "BTCUSDT"
        assert fp.feature_name == "ema_20"
        assert fp.version == "v1"
        assert fp.ts_ms == base_ts
        assert fp.value == 123.45

    @pytest.mark.asyncio
    async def test_read_feature_range_no_version_param_returns_all_versions(self):
        """验证不指定version参数时返回所有版本"""
        base_ts = 1700000000000
        await self.store.write_feature(
            symbol="BTCUSDT",
            feature_name="ema_20",
            version="v1",
            ts_ms=base_ts,
            value=100.0,
        )
        await self.store.write_feature(
            symbol="BTCUSDT",
            feature_name="ema_20",
            version="v2",
            ts_ms=base_ts + 1000,
            value=200.0,
        )

        result = await self.store.read_feature_range(
            symbol="BTCUSDT",
            feature_name="ema_20",
            start_time=base_ts,
            end_time=base_ts + 1000,
        )

        assert len(result) == 2
        versions = {fp.version for fp in result}
        assert versions == {"v1", "v2"}

    @pytest.mark.asyncio
    async def test_read_feature_range_wide_range_returns_all(self):
        """验证足够宽的范围能返回所有数据"""
        base_ts = 1700000000000
        for i in range(5):
            await self.store.write_feature(
                symbol="BTCUSDT",
                feature_name="ema_20",
                version="v1",
                ts_ms=base_ts + i * 1000,
                value=100.0 + i,
            )

        result = await self.store.read_feature_range(
            symbol="BTCUSDT",
            feature_name="ema_20",
            start_time=0,
            end_time=9999999999999,
        )

        assert len(result) == 5

    @pytest.mark.asyncio
    async def test_read_feature_range_adjacent_ranges(self):
        """验证相邻时间范围不遗漏数据"""
        base_ts = 1700000000000
        await self.store.write_feature(
            symbol="BTCUSDT",
            feature_name="ema_20",
            version="v1",
            ts_ms=base_ts,
            value=100.0,
        )
        await self.store.write_feature(
            symbol="BTCUSDT",
            feature_name="ema_20",
            version="v1",
            ts_ms=base_ts + 1000,
            value=150.0,
        )
        await self.store.write_feature(
            symbol="BTCUSDT",
            feature_name="ema_20",
            version="v1",
            ts_ms=base_ts + 2000,
            value=200.0,
        )

        # 第一个范围
        result1 = await self.store.read_feature_range(
            symbol="BTCUSDT",
            feature_name="ema_20",
            start_time=base_ts,
            end_time=base_ts + 1000,
        )
        # 第二个范围
        result2 = await self.store.read_feature_range(
            symbol="BTCUSDT",
            feature_name="ema_20",
            start_time=base_ts + 1000,
            end_time=base_ts + 2000,
        )

        assert len(result1) == 2
        assert len(result2) == 2
        assert result1[0].ts_ms == base_ts
        assert result1[1].ts_ms == base_ts + 1000
        assert result2[0].ts_ms == base_ts + 1000
        assert result2[1].ts_ms == base_ts + 2000


class TestFeatureStoreRangeMemoryDirect:
    """单元测试: 直接测试内存存储范围查询"""

    def setup_method(self):
        self.storage = ControlPlaneInMemoryStorage()
        self.store = FeatureStore(storage=self.storage)
        self._original_ensure_postgres = self.store._ensure_postgres
        self.store._ensure_postgres = AsyncMock(return_value=False)

    def teardown_method(self):
        self.store._ensure_postgres = self._original_ensure_postgres

    @pytest.mark.asyncio
    async def test_memory_read_feature_range_direct(self):
        """直接调用_memory_read_feature_range方法"""
        base_ts = 1700000000000
        await self.store.write_feature(
            symbol="BTCUSDT",
            feature_name="ema_20",
            version="v1",
            ts_ms=base_ts,
            value=100.0,
        )
        await self.store.write_feature(
            symbol="BTCUSDT",
            feature_name="ema_20",
            version="v1",
            ts_ms=base_ts + 500,
            value=150.0,
        )
        await self.store.write_feature(
            symbol="BTCUSDT",
            feature_name="ema_20",
            version="v1",
            ts_ms=base_ts + 1000,
            value=200.0,
        )

        result = self.store._memory_read_feature_range(
            symbol="BTCUSDT",
            feature_name="ema_20",
            start_time=base_ts + 250,
            end_time=base_ts + 750,
            version=None,
        )

        assert len(result) == 1
        assert result[0].ts_ms == base_ts + 500

    @pytest.mark.asyncio
    async def test_memory_read_feature_range_filters_out_of_range(self):
        """验证内存范围查询正确过滤范围外数据"""
        base_ts = 1700000000000
        await self.store.write_feature(
            symbol="BTCUSDT",
            feature_name="ema_20",
            version="v1",
            ts_ms=base_ts - 1000,  # 范围外
            value=50.0,
        )
        await self.store.write_feature(
            symbol="BTCUSDT",
            feature_name="ema_20",
            version="v1",
            ts_ms=base_ts,  # 范围内
            value=100.0,
        )
        await self.store.write_feature(
            symbol="BTCUSDT",
            feature_name="ema_20",
            version="v1",
            ts_ms=base_ts + 2000,  # 范围外
            value=250.0,
        )

        result = self.store._memory_read_feature_range(
            symbol="BTCUSDT",
            feature_name="ema_20",
            start_time=base_ts,
            end_time=base_ts + 1000,
            version=None,
        )

        assert len(result) == 1
        assert result[0].value == 100.0
