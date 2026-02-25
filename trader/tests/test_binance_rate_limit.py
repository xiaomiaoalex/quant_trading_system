"""
Rate Budget Unit Tests
=======================
测试 Token Bucket 限流器的功能。
"""
import asyncio
import time
import pytest

from trader.adapters.binance.rate_limit import (
    RestRateBudget,
    Priority,
    RateBudgetConfig,
)


class TestRestRateBudget:
    """Rate Budget 测试"""

    def test_initial_state(self):
        """测试初始状态"""
        budget = RestRateBudget()
        state = budget.get_state()

        assert state["current_tokens"] == pytest.approx(20.0, rel=0.1)
        assert state["refill_rate"] == 10.0
        assert state["bucket_size"] == 20.0
        assert state["is_degraded"] is False

    def test_acquire_success(self):
        """测试成功获取 token"""
        budget = RestRateBudget()
        result = budget.acquire(cost=1)

        assert result is True
        state = budget.get_state()
        assert state["current_tokens"] < 20.0

    def test_acquire_insufficient_tokens(self):
        """测试 token 不足"""
        budget = RestRateBudget(RateBudgetConfig(initial_bucket_size=0.5))
        result = budget.acquire(cost=1)

        assert result is False

    def test_p0_allowed_when_degraded(self):
        """测试降级模式下 P0 仍然允许"""
        budget = RestRateBudget()
        budget.degrade_to_p0_only(cooldown_s=60)

        assert budget.is_p0_allowed() is True

    def test_on_429_degrades(self):
        """测试 429 错误导致降级"""
        budget = RestRateBudget()
        initial_rate = budget.get_state()["refill_rate"]

        budget.on_429(retry_after=10)
        state = budget.get_state()

        assert state["is_degraded"] is True
        assert state["refill_rate"] < initial_rate

    def test_on_418_extreme_degrade(self):
        """测试 418 错误进入极端降级"""
        budget = RestRateBudget()
        budget.on_418()
        state = budget.get_state()

        assert state["is_degraded"] is True
        assert state["refill_rate"] == 1.0

    def test_degrade_to_p0_only(self):
        """测试降级到仅 P0"""
        budget = RestRateBudget()
        initial_rate = budget.get_state()["refill_rate"]

        budget.degrade_to_p0_only(cooldown_s=60)
        state = budget.get_state()

        assert state["is_degraded"] is True
        assert state["refill_rate"] < initial_rate

    def test_reset(self):
        """测试重置"""
        budget = RestRateBudget()
        budget.on_429()
        budget.reset()
        state = budget.get_state()

        assert state["refill_rate"] == 10.0
        assert state["is_degraded"] is False
        assert state["429_count"] == 0

    @pytest.mark.asyncio
    async def test_acquire_async_success(self):
        """测试异步获取成功"""
        budget = RestRateBudget()
        result = await budget.acquire_async(cost=1, priority=Priority.P0, timeout=1.0)

        assert result is True

    @pytest.mark.asyncio
    async def test_acquire_async_timeout(self):
        """测试异步获取超时"""
        budget = RestRateBudget(RateBudgetConfig(initial_bucket_size=0.0, initial_refill_rate=0.0))
        result = await budget.acquire_async(cost=1, priority=Priority.P0, timeout=0.5)

        assert result is False

    def test_priority_behavior(self):
        """测试优先级行为"""
        budget = RestRateBudget(RateBudgetConfig(initial_bucket_size=1.0))

        p2_result = budget.acquire(cost=1, priority=Priority.P2)
        assert p2_result is True

        p0_result = budget.acquire(cost=1, priority=Priority.P0)
        assert p0_result is True


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
