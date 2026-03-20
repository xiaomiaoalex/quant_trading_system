import pytest
from unittest.mock import AsyncMock, MagicMock, patch

from trader.adapters.persistence.feature_store import (
    FeatureStore,
    FeatureVersionConflictError,
    get_feature_store,
)
from trader.storage.in_memory import ControlPlaneInMemoryStorage


class TestFeatureStore:
    """Unit tests for FeatureStore"""

    def setup_method(self):
        self.storage = ControlPlaneInMemoryStorage()
        self.store = FeatureStore(storage=self.storage)

    def test_make_key(self):
        key = self.store._make_key("BTCUSDT", "ema_20", "v1", 1700000000000)
        assert key == "BTCUSDT:ema_20:v1:1700000000000"

    def test_make_value_hash_deterministic(self):
        hash1 = self.store._make_value_hash({"value": 123.45})
        hash2 = self.store._make_value_hash({"value": 123.45})
        assert hash1 == hash2

    def test_make_value_hash_different_values(self):
        hash1 = self.store._make_value_hash({"value": 123.45})
        hash2 = self.store._make_value_hash({"value": 678.90})
        assert hash1 != hash2

    @pytest.mark.asyncio
    async def test_write_feature_new(self):
        created, is_dup = await self.store.write_feature(
            symbol="BTCUSDT",
            feature_name="ema_20",
            version="v1",
            ts_ms=1700000000000,
            value=123.45,
            meta={"source": "test"},
        )
        assert created is True
        assert is_dup is False

    @pytest.mark.asyncio
    async def test_write_feature_idempotent_same_value(self):
        await self.store.write_feature(
            symbol="BTCUSDT",
            feature_name="ema_20",
            version="v1",
            ts_ms=1700000000000,
            value=123.45,
        )
        created, is_dup = await self.store.write_feature(
            symbol="BTCUSDT",
            feature_name="ema_20",
            version="v1",
            ts_ms=1700000000000,
            value=123.45,
        )
        assert created is False
        assert is_dup is True

    @pytest.mark.asyncio
    async def test_write_feature_conflict_different_value(self):
        await self.store.write_feature(
            symbol="BTCUSDT",
            feature_name="ema_20",
            version="v1",
            ts_ms=1700000000000,
            value=123.45,
        )
        with pytest.raises(FeatureVersionConflictError) as exc_info:
            await self.store.write_feature(
                symbol="BTCUSDT",
                feature_name="ema_20",
                version="v1",
                ts_ms=1700000000000,
                value=678.90,
            )
        assert "Feature version conflict" in str(exc_info.value)

    @pytest.mark.asyncio
    async def test_read_feature_exists(self):
        await self.store.write_feature(
            symbol="BTCUSDT",
            feature_name="ema_20",
            version="v1",
            ts_ms=1700000000000,
            value=123.45,
            meta={"source": "test"},
        )
        result = await self.store.read_feature(
            symbol="BTCUSDT",
            feature_name="ema_20",
            version="v1",
            ts_ms=1700000000000,
        )
        assert result is not None
        assert result["symbol"] == "BTCUSDT"
        assert result["feature_name"] == "ema_20"
        assert result["version"] == "v1"
        assert result["value"] == 123.45

    @pytest.mark.asyncio
    async def test_read_feature_not_exists(self):
        result = await self.store.read_feature(
            symbol="BTCUSDT",
            feature_name="ema_20",
            version="v1",
            ts_ms=1700000000000,
        )
        assert result is None

    @pytest.mark.asyncio
    async def test_list_versions(self):
        await self.store.write_feature(
            symbol="BTCUSDT",
            feature_name="ema_20",
            version="v1",
            ts_ms=1700000000000,
            value=123.45,
        )
        await self.store.write_feature(
            symbol="BTCUSDT",
            feature_name="ema_20",
            version="v2",
            ts_ms=1700000001000,
            value=234.56,
        )
        await self.store.write_feature(
            symbol="BTCUSDT",
            feature_name="volume_ratio",
            version="v1",
            ts_ms=1700000002000,
            value=1.5,
        )

        versions = await self.store.list_versions(
            symbol="BTCUSDT",
            feature_name="ema_20",
        )
        assert len(versions) == 2
        version_names = [v["version"] for v in versions]
        assert "v1" in version_names
        assert "v2" in version_names

    @pytest.mark.asyncio
    async def test_list_versions_empty(self):
        versions = await self.store.list_versions(
            symbol="BTCUSDT",
            feature_name="ema_20",
        )
        assert versions == []

    @pytest.mark.asyncio
    async def test_write_feature_different_timestamps_same_version(self):
        await self.store.write_feature(
            symbol="BTCUSDT",
            feature_name="ema_20",
            version="v1",
            ts_ms=1700000000000,
            value=123.45,
        )
        created, is_dup = await self.store.write_feature(
            symbol="BTCUSDT",
            feature_name="ema_20",
            version="v1",
            ts_ms=1700000001000,
            value=234.56,
        )
        assert created is True
        assert is_dup is False

    @pytest.mark.asyncio
    async def test_cross_version_isolation(self):
        await self.store.write_feature(
            symbol="BTCUSDT",
            feature_name="ema_20",
            version="v1",
            ts_ms=1700000000000,
            value=123.45,
        )
        created, is_dup = await self.store.write_feature(
            symbol="BTCUSDT",
            feature_name="ema_20",
            version="v2",
            ts_ms=1700000000000,
            value=123.45,
        )
        assert created is True
        assert is_dup is False

    @pytest.mark.asyncio
    async def test_get_feature_store_singleton(self):
        store1 = get_feature_store()
        store2 = get_feature_store()
        assert store1 is store2


class TestFeatureStorePostgresFallback:
    """Test PostgreSQL fallback behavior"""

    def setup_method(self):
        self.storage = ControlPlaneInMemoryStorage()
        self.store = FeatureStore(storage=self.storage)

    @pytest.mark.asyncio
    async def test_fallback_when_postgres_unavailable(self):
        with patch.object(self.store, "_ensure_postgres", new_callable=AsyncMock) as mock_ensure:
            mock_ensure.return_value = False
            created, is_dup = await self.store.write_feature(
                symbol="BTCUSDT",
                feature_name="ema_20",
                version="v1",
                ts_ms=1700000000000,
                value=123.45,
            )
            assert created is True
            assert is_dup is False
