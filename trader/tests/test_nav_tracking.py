"""
NAV Tracking Tests (Stage 6)
=============================
全 mock，不依赖真实 PostgreSQL。
"""

from __future__ import annotations

import time
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from trader.core.domain.models.nav import NAVPoint
from trader.storage.in_memory import ControlPlaneInMemoryStorage

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _make_storage() -> ControlPlaneInMemoryStorage:
    return ControlPlaneInMemoryStorage()


def _make_nav_point(deployment_id: str = "dep-1", ts: int = 1000) -> dict:
    return {
        "deployment_id": deployment_id,
        "strategy_id": "strat-1",
        "timestamp_ms": ts,
        "equity": 100_500.0,
        "cash": 99_000.0,
        "unrealized_pnl": 300.0,
        "realized_pnl": 200.0,
        "total_pnl": 500.0,
    }


# ---------------------------------------------------------------------------
# 1. append_nav_point 写入可读取
# ---------------------------------------------------------------------------


def test_append_nav_point_readable():
    storage = _make_storage()
    nav = _make_nav_point()
    storage.append_nav_point("dep-1", nav)
    result = storage.get_nav_series("dep-1")
    assert len(result) == 1
    assert result[0]["equity"] == 100_500.0


# ---------------------------------------------------------------------------
# 2. get_nav_series since_ms 过滤
# ---------------------------------------------------------------------------


def test_get_nav_series_since_ms_filter():
    storage = _make_storage()
    storage.append_nav_point("dep-1", _make_nav_point(ts=1000))
    storage.append_nav_point("dep-1", _make_nav_point(ts=2000))
    storage.append_nav_point("dep-1", _make_nav_point(ts=3000))

    result = storage.get_nav_series("dep-1", since_ms=2000)
    assert len(result) == 2
    assert all(p["timestamp_ms"] >= 2000 for p in result)


# ---------------------------------------------------------------------------
# 3. 滚动窗口截断（1001 条 → 1000）
# ---------------------------------------------------------------------------


def test_append_nav_point_rolling_window():
    storage = _make_storage()
    for i in range(1001):
        storage.append_nav_point("dep-1", _make_nav_point(ts=i))

    series = storage.get_nav_series("dep-1")
    assert len(series) == 1000
    # 最旧的一条 (ts=0) 应已被淘汰
    assert series[0]["timestamp_ms"] == 1


# ---------------------------------------------------------------------------
# 4. 无 positions 时 equity = 100_000
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_record_nav_snapshot_no_positions_equity():
    storage = _make_storage()

    with (
        patch("trader.storage.nav_store.append_nav_point_pg", new_callable=AsyncMock),
        patch("trader.api.routes.sse.get_sse_manager") as mock_sse,
    ):
        mock_sse.return_value.broadcast = AsyncMock()

        from trader.services.nav_service import record_nav_snapshot

        await record_nav_snapshot("strat-1", storage, deployment_id="dep-1")

    from trader.services.nav_service import _DEFAULT_INITIAL_CAPITAL

    series = storage.get_nav_series("dep-1")
    assert len(series) == 1
    assert series[0]["equity"] == _DEFAULT_INITIAL_CAPITAL
    assert series[0]["total_pnl"] == 0.0


# ---------------------------------------------------------------------------
# 5. 有 realized_pnl 时 equity 正确
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_record_nav_snapshot_with_pnl():
    storage = _make_storage()
    storage.upsert_position(
        {
            "strategy_id": "strat-1",
            "symbol": "BTCUSDT",
            "qty": 0.01,
            "avg_cost": 50000.0,
            "mark_price": 51000.0,
            "realized_pnl": 20.0,
            "deployment_id": "dep-1",
        },
    )

    with (
        patch("trader.storage.nav_store.append_nav_point_pg", new_callable=AsyncMock),
        patch("trader.api.routes.sse.get_sse_manager") as mock_sse,
    ):
        mock_sse.return_value.broadcast = AsyncMock()

        from trader.services.nav_service import record_nav_snapshot

        await record_nav_snapshot("strat-1", storage, deployment_id="dep-1")

    series = storage.get_nav_series("dep-1")
    assert len(series) == 1
    nav = series[0]
    assert nav["equity"] == pytest.approx(100_030.0)  # 100_000 + 10 + 20
    assert nav["total_pnl"] == pytest.approx(30.0)


# ---------------------------------------------------------------------------
# 6. PG 写入失败不抛异常
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_record_nav_snapshot_pg_failure_silent():
    storage = _make_storage()

    with (
        patch(
            "trader.storage.nav_store.append_nav_point_pg",
            new_callable=AsyncMock,
            side_effect=RuntimeError("PG down"),
        ),
        patch("trader.api.routes.sse.get_sse_manager") as mock_sse,
    ):
        mock_sse.return_value.broadcast = AsyncMock()

        from trader.services.nav_service import record_nav_snapshot

        # 不应抛出异常
        await record_nav_snapshot("strat-1", storage, deployment_id="dep-1")

    # 内存写入仍然成功
    assert len(storage.get_nav_series("dep-1")) == 1


# ---------------------------------------------------------------------------
# 7. SSE broadcast 触发
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_record_nav_snapshot_sse_broadcast():
    storage = _make_storage()
    mock_broadcast = AsyncMock()

    with (
        patch("trader.storage.nav_store.append_nav_point_pg", new_callable=AsyncMock),
        patch("trader.api.routes.sse.get_sse_manager") as mock_sse,
    ):
        mock_sse.return_value.broadcast = mock_broadcast

        from trader.services.nav_service import record_nav_snapshot

        await record_nav_snapshot("strat-1", storage, deployment_id="dep-99")

    assert mock_broadcast.call_count == 2
    first_call = mock_broadcast.call_args_list[0]
    assert first_call[0][0] == "nav:dep-99"
    assert first_call[0][1] == "nav_update"
    second_call = mock_broadcast.call_args_list[1]
    assert second_call[0][0] == "nav:strat-1"
    assert second_call[0][1] == "nav_update"


# ---------------------------------------------------------------------------
# 8. NAVPointSchema 字段校验
# ---------------------------------------------------------------------------


def test_nav_point_schema_fields():
    from trader.api.models.schemas import NAVPointSchema

    schema = NAVPointSchema(
        deployment_id="dep-1",
        strategy_id="strat-1",
        timestamp_ms=1_000_000,
        equity=100_000.0,
        cash=99_000.0,
        unrealized_pnl=500.0,
        realized_pnl=500.0,
        total_pnl=1000.0,
    )
    assert schema.equity == 100_000.0
    assert schema.deployment_id == "dep-1"


def test_nav_point_schema_rejects_missing_fields():
    from pydantic import ValidationError

    from trader.api.models.schemas import NAVPointSchema

    with pytest.raises(ValidationError):
        NAVPointSchema(deployment_id="dep-1")  # type: ignore[call-arg]


# ---------------------------------------------------------------------------
# 9. GET /v1/deployments/{id}/nav 返回内存数据
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_get_deployment_nav_endpoint_returns_memory_data():
    from httpx import ASGITransport, AsyncClient

    storage = _make_storage()
    storage.append_nav_point("dep-1", _make_nav_point(ts=1000))
    storage.append_nav_point("dep-1", _make_nav_point(ts=2000))

    with patch("trader.storage.in_memory.get_storage", return_value=storage):
        from trader.api.main import app

        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
            resp = await client.get("/v1/deployments/dep-1/nav")

    assert resp.status_code == 200
    data = resp.json()
    assert len(data) == 2
    assert data[0]["timestamp_ms"] == 1000


# ---------------------------------------------------------------------------
# 10. GET /v1/deployments/{id}/nav?since_ms=X 过滤
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_get_deployment_nav_endpoint_since_ms_filter():
    from httpx import ASGITransport, AsyncClient

    storage = _make_storage()
    for ts in [1000, 2000, 3000]:
        storage.append_nav_point("dep-2", _make_nav_point(deployment_id="dep-2", ts=ts))

    with patch("trader.storage.in_memory.get_storage", return_value=storage):
        from trader.api.main import app

        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
            resp = await client.get("/v1/deployments/dep-2/nav?since_ms=2000")

    assert resp.status_code == 200
    data = resp.json()
    assert len(data) == 2
    assert all(p["timestamp_ms"] >= 2000 for p in data)


# ---------------------------------------------------------------------------
# 11. GET /v1/deployments/{id}/nav 内存为空时降级查 PG
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_get_deployment_nav_fallback_to_pg():
    from httpx import ASGITransport, AsyncClient

    storage = _make_storage()  # 空 storage

    with (
        patch("trader.storage.in_memory.get_storage", return_value=storage),
        patch(
            "trader.storage.nav_store.get_nav_series_pg",
            new_callable=AsyncMock,
            return_value=[_make_nav_point(ts=5000)],
        ) as mock_pg,
    ):
        from trader.api.main import app

        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
            resp = await client.get("/v1/deployments/dep-1/nav")

    assert resp.status_code == 200
    data = resp.json()
    assert len(data) == 1
    assert data[0]["timestamp_ms"] == 5000
    mock_pg.assert_awaited_once()


# ---------------------------------------------------------------------------
# 12. GET /v1/deployments/{id}/nav limit 截断
# ---------------------------------------------------------------------------


def test_get_nav_series_limit_truncation():
    storage = _make_storage()
    for ts in [1000, 2000, 3000, 4000, 5000]:
        storage.append_nav_point("dep-3", _make_nav_point(deployment_id="dep-3", ts=ts))

    # 模拟路由层截断逻辑（直接测试内存截断）
    series = storage.get_nav_series("dep-3")
    limit = 2
    if len(series) > limit:
        series = series[-limit:]

    assert len(series) == 2
    assert series[0]["timestamp_ms"] == 4000
    assert series[1]["timestamp_ms"] == 5000


# ---------------------------------------------------------------------------
# 13. SSE broadcast 单频道（eff_deployment_id == strategy_id 时只广播一次）
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_record_nav_snapshot_single_broadcast_when_ids_match():
    storage = _make_storage()
    mock_broadcast = AsyncMock()

    with (
        patch("trader.storage.nav_store.append_nav_point_pg", new_callable=AsyncMock),
        patch("trader.api.routes.sse.get_sse_manager") as mock_sse,
    ):
        mock_sse.return_value.broadcast = mock_broadcast

        from trader.services.nav_service import record_nav_snapshot

        # 不传入 deployment_id，eff_deployment_id 会等于 strategy_id
        await record_nav_snapshot("strat-1", storage, deployment_id=None)

    assert mock_broadcast.call_count == 1
    assert mock_broadcast.call_args[0][0] == "nav:strat-1"
    assert mock_broadcast.call_args[0][1] == "nav_update"


# ---------------------------------------------------------------------------
# 14. 多 deployment 持仓时 NAV 归属第一个遇到的 deployment
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_record_nav_snapshot_mixed_deployment_ids():
    """验证同一 strategy 持有多个 deployment 持仓时，NAV 归属第一个遇到的 deployment。

    这是已知的语义约束：NAV 按 strategy 级别计算，但记录到 positions dict 中第一个
    遇到的 deployment。调用方需要注意这个行为，必要时在应用层按 deployment_id 分别调用。
    """
    storage = _make_storage()

    # 添加两个不同 deployment 的持仓
    # 注意：upsert_position key = account_id:venue:strategy_id:instrument
    # 两个持仓使用不同 instrument 确保不会互相覆盖
    storage.upsert_position(
        {
            "strategy_id": "strat-1",
            "symbol": "ETHUSDT",
            "instrument": "ETHUSDT",
            "account_id": "acc-1",
            "venue": "BINANCE",
            "qty": 0.5,
            "avg_cost": 3000.0,
            "mark_price": 3100.0,
            "realized_pnl": 20.0,
            "deployment_id": "dep-1",  # 第一个插入
        }
    )
    storage.upsert_position(
        {
            "strategy_id": "strat-1",
            "symbol": "BTCUSDT",
            "instrument": "BTCUSDT",
            "account_id": "acc-1",
            "venue": "BINANCE",
            "qty": 0.01,
            "avg_cost": 50000.0,
            "mark_price": 51000.0,
            "realized_pnl": 10.0,
            "deployment_id": "dep-2",  # 第二个插入
        }
    )

    mock_broadcast = AsyncMock(return_value=1)

    with (
        patch("trader.storage.nav_store.append_nav_point_pg", new_callable=AsyncMock),
        patch("trader.api.routes.sse.get_sse_manager") as mock_sse,
    ):
        mock_sse.return_value.broadcast = mock_broadcast

        from trader.services.nav_service import record_nav_snapshot

        # deployment_id=None 时，eff_deployment_id 从 positions 推断
        await record_nav_snapshot("strat-1", storage, deployment_id=None)

    # First-wins: dep-1 是第一个遇到的 deployment
    series_dep1 = storage.get_nav_series("dep-1")
    assert len(series_dep1) == 1, "NAV should be recorded to first-found deployment (dep-1)"
    assert series_dep1[0]["deployment_id"] == "dep-1"

    # dep-2 应该没有 NAV（持仓不在它的 series 里）
    series_dep2 = storage.get_nav_series("dep-2")
    assert len(series_dep2) == 0, "dep-2 should have no NAV since positions are not in its series"

    # 验证广播：双频道设计，应该广播到 nav:dep-1 和 nav:strat-1
    assert mock_broadcast.call_count == 2, "Should broadcast to both nav:dep-1 and nav:strat-1"

    # 第一个广播到 nav:dep-1
    call1_channel = mock_broadcast.call_args_list[0][0][0]
    call1_payload = mock_broadcast.call_args_list[0][0][2]  # data is 3rd arg
    assert call1_channel == "nav:dep-1"
    assert call1_payload["deployment_id"] == "dep-1"
    assert call1_payload["strategy_id"] == "strat-1"

    # 第二个广播到 nav:strat-1
    call2_channel = mock_broadcast.call_args_list[1][0][0]
    call2_payload = mock_broadcast.call_args_list[1][0][2]  # data is 3rd arg
    assert call2_channel == "nav:strat-1"
    assert call2_payload["deployment_id"] == "dep-1"
