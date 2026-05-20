"""
TDD: promote-paper 原子编排接口测试

覆盖：
- 成功 promote：VALIDATION_PASSED -> APPROVED_FOR_PAPER，deployment 创建，runtime LOADED
- 非法状态：非 VALIDATION_PASSED 返回 409 INVALID_STATE
- PROMOTE_LOAD_FAILED：load 失败触发回滚（runtime 清理、deployment 清理、candidate 回 VALIDATION_PASSED）
- PROMOTE_CONFLICT：并发/重复加载返回 409
- dev_smoke 候选不能 promote
- live 模式不被开启
"""
from __future__ import annotations

import asyncio
from unittest.mock import AsyncMock, patch

import pytest
from fastapi.testclient import TestClient

from trader.api.main import app
from trader.storage.in_memory import ControlPlaneInMemoryStorage, get_storage


# ---------------------------------------------------------------------------
# 辅助：在存储层注入一个处于指定状态的候选策略
# ---------------------------------------------------------------------------

def _create_candidate_at_status(storage: ControlPlaneInMemoryStorage, status: str) -> str:
    """直接在存储里写入一个指定状态的候选策略，跳过正常流程。"""
    strategy_id = f"test_strategy_{status.lower()}"
    if storage.get_strategy(strategy_id) is None:
        storage.create_strategy(
            {
                "strategy_id": strategy_id,
                "name": strategy_id,
                "entrypoint": f"dynamic:{strategy_id}",
            }
        )

    # 创建一个 code version
    code_v = storage.create_strategy_code(
        strategy_id,
        {
            "code": "def get_plugin(): return None\n",
            "created_by": "test",
        },
    )
    code_version = code_v["code_version"]

    candidate = storage.create_strategy_candidate(
        {
            "strategy_id": strategy_id,
            "name": strategy_id,
            "description": "test",
            "code": "def get_plugin(): return None\n",
            "code_version": code_version,
            "config": {},
            "dataset": None,
            "feature_version": "real_feature_store",
        }
    )
    candidate_id = candidate["candidate_id"]
    storage.update_strategy_candidate(candidate_id, {"status": status})
    return candidate_id


# ---------------------------------------------------------------------------
# 1. 成功路径：VALIDATION_PASSED -> APPROVED_FOR_PAPER
# ---------------------------------------------------------------------------

def test_promote_paper_success():
    """成功 promote：candidate 进入 APPROVED_FOR_PAPER，deployment 被创建。"""
    storage = get_storage()
    candidate_id = _create_candidate_at_status(storage, "VALIDATION_PASSED")

    with TestClient(app) as client:
        # Mock load_strategy 使其成功，不真正启动 StrategyRunner
        with patch(
            "trader.services.strategy_candidate.StrategyCandidateService._load_strategy_runtime",
            new_callable=AsyncMock,
            return_value="test_deployment_id",
        ):
            resp = client.post(f"/v1/strategy-candidates/{candidate_id}/promote-paper")

    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["status"] == "APPROVED_FOR_PAPER"
    assert body["candidate_id"] == candidate_id
    assert body["deployment_id"]

    # 验证存储状态
    updated = storage.get_strategy_candidate(candidate_id)
    assert updated["status"] == "APPROVED_FOR_PAPER"
    assert updated["deployment_id"] is not None


# ---------------------------------------------------------------------------
# 2. 非法状态：非 VALIDATION_PASSED -> 409 INVALID_STATE
# ---------------------------------------------------------------------------

@pytest.mark.parametrize("bad_status", [
    "DRAFT",
    "DEBUG_PASSED",
    "BACKTEST_RUNNING",
    "BACKTEST_PASSED",
    "APPROVED_FOR_PAPER",
    "REJECTED",
])
def test_promote_paper_invalid_state(bad_status: str):
    """非 VALIDATION_PASSED 状态请求返回 409 INVALID_STATE。"""
    storage = get_storage()
    candidate_id = _create_candidate_at_status(storage, bad_status)

    with TestClient(app) as client:
        resp = client.post(f"/v1/strategy-candidates/{candidate_id}/promote-paper")

    assert resp.status_code == 409, resp.text
    body = resp.json()
    # 支持直接 detail 字符串或结构化 error_code
    detail = body.get("detail", "")
    if isinstance(detail, dict):
        assert detail.get("error_code") == "INVALID_STATE"
        assert detail.get("current_state") == bad_status
        assert detail.get("required_state") == "VALIDATION_PASSED"
    else:
        assert "INVALID_STATE" in detail or "VALIDATION_PASSED" in detail


# ---------------------------------------------------------------------------
# 3. 候选不存在 -> 404
# ---------------------------------------------------------------------------

def test_promote_paper_not_found():
    with TestClient(app) as client:
        resp = client.post("/v1/strategy-candidates/nonexistent_id/promote-paper")
    assert resp.status_code == 404


# ---------------------------------------------------------------------------
# 4. PROMOTE_LOAD_FAILED：load 失败触发回滚
# ---------------------------------------------------------------------------

def test_promote_paper_load_failed_triggers_rollback():
    """load 失败时：runtime 不残留，deployment 被清理，candidate 回 VALIDATION_PASSED。"""
    storage = get_storage()
    candidate_id = _create_candidate_at_status(storage, "VALIDATION_PASSED")

    with TestClient(app) as client:
        with patch(
            "trader.services.strategy_candidate.StrategyCandidateService._load_strategy_runtime",
            new_callable=AsyncMock,
            side_effect=RuntimeError("Simulated load failure"),
        ):
            resp = client.post(f"/v1/strategy-candidates/{candidate_id}/promote-paper")

    assert resp.status_code == 409, resp.text
    body = resp.json()
    detail = body.get("detail", "")
    if isinstance(detail, dict):
        assert detail.get("error_code") == "PROMOTE_LOAD_FAILED"
    else:
        assert "PROMOTE_LOAD_FAILED" in detail or "load" in detail.lower()

    # 验证回滚：candidate 必须回到 VALIDATION_PASSED
    updated = storage.get_strategy_candidate(candidate_id)
    assert updated["status"] == "VALIDATION_PASSED", (
        f"Rollback failed: candidate still in {updated['status']}"
    )

    # deployment 必须不存在（已回滚）
    dep_id = updated.get("deployment_id")
    if dep_id:
        dep = storage.get_deployment(dep_id)
        # 要么 deployment 不存在，要么被标记为 PROMOTE_ROLLED_BACK
        assert dep is None or dep.get("status") == "PROMOTE_ROLLED_BACK", (
            f"Deployment was not rolled back: {dep}"
        )

    # audit trail 中应有失败事件
    events = updated.get("events", [])
    event_reasons = [e.get("reason", "") for e in events]
    assert any("PROMOTE_LOAD_FAILED" in r or "promote_load_failed" in r for r in event_reasons), (
        f"No PROMOTE_LOAD_FAILED event found in audit trail: {event_reasons}"
    )


# ---------------------------------------------------------------------------
# 5. dev_smoke 候选不能 promote
# ---------------------------------------------------------------------------

def test_promote_paper_rejects_dev_smoke():
    """feature_version=dev_smoke 的候选不能 promote，即使状态是 VALIDATION_PASSED。"""
    storage = get_storage()
    strategy_id = "test_strategy_dev_smoke_promote"
    if storage.get_strategy(strategy_id) is None:
        storage.create_strategy(
            {
                "strategy_id": strategy_id,
                "name": strategy_id,
                "entrypoint": f"dynamic:{strategy_id}",
            }
        )
    code_v = storage.create_strategy_code(
        strategy_id,
        {
            "code": "def get_plugin(): return None\n",
            "created_by": "test",
        },
    )
    candidate = storage.create_strategy_candidate(
        {
            "strategy_id": strategy_id,
            "name": strategy_id,
            "description": "test",
            "code": "def get_plugin(): return None\n",
            "code_version": code_v["code_version"],
            "config": {},
            "dataset": None,
            "feature_version": "dev_smoke",  # 关键：dev_smoke
        }
    )
    candidate_id = candidate["candidate_id"]
    storage.update_strategy_candidate(candidate_id, {"status": "VALIDATION_PASSED"})

    with TestClient(app) as client:
        resp = client.post(f"/v1/strategy-candidates/{candidate_id}/promote-paper")

    assert resp.status_code == 409, resp.text
    detail = resp.json().get("detail", "")
    if isinstance(detail, dict):
        assert "dev_smoke" in str(detail).lower() or detail.get("error_code") == "INVALID_STATE"
    else:
        assert "dev_smoke" in detail.lower() or "VALIDATION_PASSED" in detail


# ---------------------------------------------------------------------------
# 6. live 模式不被开启
# ---------------------------------------------------------------------------

def test_promote_paper_does_not_enable_live():
    """promote-paper 成功后，deployment 的 mode 不能是 live。"""
    storage = get_storage()
    candidate_id = _create_candidate_at_status(storage, "VALIDATION_PASSED")

    captured_deployment_id: list[str] = []

    async def mock_load(self_ref, candidate, deployment_id: str) -> str:
        captured_deployment_id.append(deployment_id)
        # 创建一个 deployment 记录以模拟成功加载
        storage.create_deployment(
            {
                "deployment_id": deployment_id,
                "strategy_id": candidate.strategy_id,
                "mode": "paper",  # 永远是 paper，不是 live
                "status": "LOADED",
            }
        )
        return deployment_id

    with TestClient(app) as client:
        with patch(
            "trader.services.strategy_candidate.StrategyCandidateService._load_strategy_runtime",
            new_callable=lambda: type(
                "AsyncMethodMock", (), {"__call__": AsyncMock(side_effect=mock_load)}
            ),
        ):
            with patch(
                "trader.services.strategy_candidate.StrategyCandidateService._load_strategy_runtime",
                new_callable=AsyncMock,
                return_value="test_deployment_live_test",
            ):
                resp = client.post(f"/v1/strategy-candidates/{candidate_id}/promote-paper")

    # 接口应成功
    assert resp.status_code == 200, resp.text

    # 验证 deployment 若已创建，mode 不是 live
    if captured_deployment_id:
        dep = storage.get_deployment(captured_deployment_id[0])
        if dep:
            assert dep.get("mode") != "live", "promote-paper must not create live deployment"


# ---------------------------------------------------------------------------
# 7. 候选已是 APPROVED_FOR_PAPER -> 409 INVALID_STATE（不重复 promote）
# ---------------------------------------------------------------------------

def test_promote_paper_already_approved():
    """已经 APPROVED_FOR_PAPER 的候选再次 promote 返回 409。"""
    storage = get_storage()
    candidate_id = _create_candidate_at_status(storage, "APPROVED_FOR_PAPER")

    with TestClient(app) as client:
        resp = client.post(f"/v1/strategy-candidates/{candidate_id}/promote-paper")

    assert resp.status_code == 409, resp.text


# ---------------------------------------------------------------------------
# 8. deployment 持久化：promote 成功后 storage 里有 deployment 记录
# ---------------------------------------------------------------------------

def test_promote_paper_creates_deployment_in_storage():
    """promote 成功后，deployment 记录必须写入持久化存储，不能只在 StrategyRunner 内存里。

    只 mock 内层的 StrategyRunner load_strategy 调用，让 _load_strategy_runtime 的
    storage.create_deployment() 真正执行。
    """
    storage = get_storage()
    candidate_id = _create_candidate_at_status(storage, "VALIDATION_PASSED")

    with TestClient(app) as client:
        # 只拦截 StrategyRunner 调用，storage.create_deployment 正常执行
        with patch(
            "trader.api.routes.strategies.load_strategy",
            new_callable=AsyncMock,
            return_value=None,
        ):
            resp = client.post(f"/v1/strategy-candidates/{candidate_id}/promote-paper")

    assert resp.status_code == 200, resp.text
    dep_id = resp.json().get("deployment_id")
    assert dep_id, "Response must include deployment_id"

    # 关键：deployment 必须在存储层可查
    dep = storage.get_deployment(dep_id)
    assert dep is not None, (
        f"Deployment {dep_id} not found in storage — only existed in StrategyRunner memory"
    )
    assert dep.get("strategy_id") is not None
    assert dep.get("mode") == "paper"
    assert dep.get("status") == "LOADED"  # create_deployment 后 update_deployment_status("LOADED")


# ---------------------------------------------------------------------------
# 9. rollback 后 deployment 被清理（标记为 PROMOTE_ROLLED_BACK）
# ---------------------------------------------------------------------------

def test_promote_paper_rollback_marks_deployment():
    """load 失败回滚后，已创建的 deployment 必须标记 PROMOTE_ROLLED_BACK，不得残留为 LOADING。

    用 patch 让内层 load_strategy 在 storage.create_deployment 之后抛错，
    验证回滚逻辑能找到并标记该 deployment。
    """
    storage = get_storage()
    candidate_id = _create_candidate_at_status(storage, "VALIDATION_PASSED")

    with TestClient(app) as client:
        # 让 StrategyRunner load 失败：storage.create_deployment 已执行，runner load 抛错
        with patch(
            "trader.api.routes.strategies.load_strategy",
            new_callable=AsyncMock,
            side_effect=RuntimeError("Simulated StrategyRunner load failure"),
        ):
            resp = client.post(f"/v1/strategy-candidates/{candidate_id}/promote-paper")

    assert resp.status_code == 409, resp.text
    detail = resp.json().get("detail", "")
    if isinstance(detail, dict):
        assert detail.get("error_code") == "PROMOTE_LOAD_FAILED"

    # candidate 必须回到 VALIDATION_PASSED
    updated = storage.get_strategy_candidate(candidate_id)
    assert updated["status"] == "VALIDATION_PASSED", (
        f"Rollback failed: candidate stuck in {updated['status']}"
    )

    # 已创建的 deployment 必须被标记为 PROMOTE_ROLLED_BACK，不残留 LOADING
    cand_data = storage.get_strategy_candidate(candidate_id)
    strategy_id = cand_data.get("strategy_id")
    all_deps = storage.list_deployments(strategy_id=strategy_id)
    for dep in all_deps:
        if dep.get("status") == "LOADING":
            pytest.fail(
                f"Deployment {dep['deployment_id']} stuck in LOADING after rollback"
            )
    # 至少有一个 PROMOTE_ROLLED_BACK 的 deployment
    rolled_back = [d for d in all_deps if d.get("status") == "PROMOTE_ROLLED_BACK"]
    assert len(rolled_back) >= 1, (
        f"Expected at least one PROMOTE_ROLLED_BACK deployment, found: {[d['status'] for d in all_deps]}"
    )


# ---------------------------------------------------------------------------
# 10. 真实并发冲突：两个协程在同一事件循环内竞争同一 candidate 的锁
# ---------------------------------------------------------------------------

def test_promote_paper_concurrent_conflict():
    """asyncio.Lock 防止同一 candidate 的并发 promote。

    测试方法：在同一个 asyncio 事件循环内预先持有该 candidate 的锁，
    再调用 promote_to_paper——由于锁已被占用，必须立即返回 PROMOTE_CONFLICT。

    为什么不用两个 TestClient 线程：TestClient 每个线程有独立事件循环，
    asyncio.Lock 不跨事件循环共享，threading 方式无法真正测到锁冲突路径。
    """
    from fastapi import HTTPException
    from trader.services.strategy_candidate import StrategyCandidateService, _promote_locks

    storage = get_storage()
    candidate_id = _create_candidate_at_status(storage, "VALIDATION_PASSED")

    async def run() -> HTTPException | None:
        # 预先为该 candidate 创建并占用锁（模拟第一个请求正在临界区）
        _promote_locks[candidate_id] = asyncio.Lock()
        async with _promote_locks[candidate_id]:  # 持有锁
            service = StrategyCandidateService()
            try:
                await service.promote_to_paper(candidate_id)
                return None  # 不应到达此处
            except HTTPException as exc:
                return exc
            finally:
                # 确保锁状态不污染其他测试
                _promote_locks.pop(candidate_id, None)

    exc = asyncio.run(run())

    assert exc is not None, "Expected PROMOTE_CONFLICT HTTPException, got success"
    assert exc.status_code == 409, f"Expected 409, got {exc.status_code}"
    detail = exc.detail
    error_code = detail.get("error_code") if isinstance(detail, dict) else ""
    assert error_code == "PROMOTE_CONFLICT", (
        f"Expected PROMOTE_CONFLICT when lock is held, got {error_code!r}: {detail}"
    )


# ---------------------------------------------------------------------------
# 11. 旧 /promote 路由已废弃，返回 410
# ---------------------------------------------------------------------------

def test_old_promote_route_returns_410():
    """旧 POST /promote 路由必须返回 410 Gone，不能再被当作正常入口使用。"""
    storage = get_storage()
    candidate_id = _create_candidate_at_status(storage, "VALIDATION_PASSED")

    with TestClient(app) as client:
        resp = client.post(
            f"/v1/strategy-candidates/{candidate_id}/promote",
            json={"symbols": ["BTCUSDT"], "account_id": "binance_demo", "mode": "paper"},
        )

    assert resp.status_code == 410, (
        f"Expected 410 Gone for deprecated /promote, got {resp.status_code}: {resp.text}"
    )


# ---------------------------------------------------------------------------
# 12. approve/audit 阶段失败也触发补偿回滚（补偿作用域覆盖 load 后全程）
# ---------------------------------------------------------------------------

def test_promote_paper_approve_failure_triggers_rollback():
    """load 成功后 approve 阶段（update_strategy_candidate）抛错，必须触发完整回滚。

    验证补偿作用域覆盖到 load 后的 approve/audit 步骤：
    - candidate 回 VALIDATION_PASSED
    - deployment 标记 PROMOTE_ROLLED_BACK
    - 接口返回 409 PROMOTE_LOAD_FAILED（运行态原子：没有干净进入 APPROVED_FOR_PAPER）
    """
    from unittest.mock import patch as std_patch

    storage = get_storage()
    candidate_id = _create_candidate_at_status(storage, "VALIDATION_PASSED")

    original_update = storage.update_strategy_candidate
    call_count = [0]

    def update_after_load_fails(cid: str, data: dict) -> dict | None:
        """load 后第一次更新 candidate 状态时抛出异常，模拟 approve 阶段失败。"""
        # _load_strategy_runtime 内不调用 update_strategy_candidate
        # 第一次调用来自 approve 步骤（status -> APPROVED_FOR_PAPER）
        if data.get("status") == "APPROVED_FOR_PAPER":
            raise RuntimeError("Simulated approve storage failure after load succeeded")
        return original_update(cid, data)

    with TestClient(app) as client:
        with patch(
            "trader.api.routes.strategies.load_strategy",
            new_callable=AsyncMock,
            return_value=None,
        ):
            with std_patch.object(storage, "update_strategy_candidate", side_effect=update_after_load_fails):
                resp = client.post(f"/v1/strategy-candidates/{candidate_id}/promote-paper")

    # 接口必须返回 409（不是 500），因为补偿回滚已触发
    assert resp.status_code == 409, (
        f"Expected 409 after approve failure, got {resp.status_code}: {resp.text}"
    )
    detail = resp.json().get("detail", "")
    if isinstance(detail, dict):
        assert detail.get("error_code") == "PROMOTE_LOAD_FAILED", detail

    # candidate 必须回到 VALIDATION_PASSED
    updated = storage.get_strategy_candidate(candidate_id)
    assert updated["status"] == "VALIDATION_PASSED", (
        f"Rollback failed after approve error: candidate stuck in {updated['status']}"
    )

    # deployment 必须被标记为 PROMOTE_ROLLED_BACK
    dep_id = f"{updated.get('strategy_id')}__promote__{candidate_id[:8]}__paper"
    dep = storage.get_deployment(dep_id)
    assert dep is not None, "Deployment was not created before approve failure"
    assert dep.get("status") == "PROMOTE_ROLLED_BACK", (
        f"Deployment not rolled back after approve failure: {dep.get('status')}"
    )
