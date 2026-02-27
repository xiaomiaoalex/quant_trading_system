"""
Backoff Controller Unit Tests
==============================
测试指数退避控制器的功能。
"""
import time
import pytest

from trader.adapters.binance.backoff import (
    BackoffController,
    BackoffConfig,
    BackoffControllerAsync,
)
from unittest.mock import patch


class TestBackoffController:
    """Backoff Controller 测试"""

    def test_initial_state(self):
        """测试初始状态"""
        controller = BackoffController()
        state = controller.get_state()

        assert "tasks" in state
        assert len(state["tasks"]) == 0

    def test_next_delay_increases(self):
        """测试延迟递增"""
        config = BackoffConfig(initial_delay=1.0, multiplier=2.0, jitter_range=0.0)
        controller = BackoffController(config)

        delay1 = controller.next_delay("test_task")
        delay2 = controller.next_delay("test_task")

        assert delay2 >= delay1

    def test_max_delay_cap(self):
        """测试最大延迟上限"""
        controller = BackoffController(BackoffConfig(max_delay=10.0, initial_delay=1.0))

        for _ in range(10):
            controller.next_delay("test_task")

        delay = controller.get_delay("test_task")
        assert delay <= 10.0

    def test_reset(self):
        """测试重置"""
        controller = BackoffController()

        controller.next_delay("test_task")
        controller.next_delay("test_task")

        controller.reset("test_task")
        delay = controller.get_delay("test_task")
        retry_count = controller.get_retry_count("test_task")

        assert retry_count == 0

    def test_reset_all(self):
        """测试全部重置"""
        controller = BackoffController()

        controller.next_delay("task1")
        controller.next_delay("task2")

        controller.reset_all()

        assert controller.get_retry_count("task1") == 0
        assert controller.get_retry_count("task2") == 0

    def test_retry_after_override(self):
        """测试 Retry-After 覆盖"""
        controller = BackoffController(BackoffConfig(initial_delay=1.0))

        delay = controller.next_delay("test_task", retry_after_s=10.0)

        assert delay >= 10.0

    def test_reconnect_storm_detection(self):
        """测试重连风暴检测"""
        controller = BackoffController(BackoffConfig())
        controller._reconnect_storm_threshold = 5

        for _ in range(5):
            controller.next_delay("reconnect")

        assert controller.is_reconnect_storm() is True

    def test_reconnect_storm_after_window(self):
        """测试窗口期后重连风暴清除"""
        controller = BackoffController()
        controller._reconnect_storm_threshold = 2

        controller.next_delay("reconnect")
        controller.next_delay("reconnect")

        assert controller.is_reconnect_storm() is True

        controller._reconnect_timestamps = []
        assert controller.is_reconnect_storm() is False

    def test_jitter_variance(self):
        """测试 Jitter 变化"""
        controller = BackoffController(BackoffConfig(jitter_range=0.5))

        delays = [controller.next_delay("test_task") for _ in range(10)]

        assert len(set(delays)) > 1

    def test_task_isolation(self):
        """测试任务隔离"""
        controller = BackoffController()

        controller.next_delay("task1")
        controller.next_delay("task1")

        controller.next_delay("task2")

        assert controller.get_retry_count("task1") == 2
        assert controller.get_retry_count("task2") == 1


class TestBackoffControllerAsync:
    """Async Backoff Controller 测试"""

    @pytest.mark.asyncio
    async def test_execute_with_backoff_success(self):
        """测试成功执行"""
        controller = BackoffControllerAsync()

        async def success_task():
            return "success"

        result = await controller.execute_with_backoff("test", success_task)
        assert result == "success"

    @pytest.mark.asyncio
    async def test_execute_with_backoff_retry(self):
        """测试重试后成功"""
        controller = BackoffControllerAsync()
        attempt_count = {"count": 0}

        async def flaky_task():
            attempt_count["count"] += 1
            if attempt_count["count"] < 2:
                raise Exception("Temporary error")
            return "success"

        result = await controller.execute_with_backoff(
            "test",
            flaky_task,
            max_retries=3
        )
        assert result == "success"
        assert attempt_count["count"] == 2

    @pytest.mark.asyncio
    async def test_execute_with_backoff_all_fail(self):
        """测试全部失败"""
        controller = BackoffControllerAsync()

        async def failing_task():
            raise Exception("Permanent error")

        with pytest.raises(Exception, match="Permanent error"):
            await controller.execute_with_backoff(
                "test",
                failing_task,
                max_retries=2
            )


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
