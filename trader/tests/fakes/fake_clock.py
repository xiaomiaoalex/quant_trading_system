"""
Fake Clock - 可推进时间
=======================
用于测试的时间模拟器，替代 time.time() 和 asyncio.sleep。
"""
import asyncio
import time
from typing import Optional, Callable, Dict, Any, List
from dataclasses import dataclass, field
from threading import Lock


class FakeClock:
    """
    可控时间模拟器
    
    用法:
    1. 替换 time.time() -> clock.time()
    2. 替换 asyncio.sleep() -> await clock.sleep()
    3. 通过 clock.advance() 推进时间
    
    核心特性：
    - 按 wake_at 顺序精确唤醒
    - 支持批量 advance
    - 线程安全
    """
    
    def __init__(self, start_time: float = 1000.0):
        self._current_time = start_time
        self._lock = Lock()
        self._scheduled_tasks: List[Dict[str, Any]] = []
        self._time_offset = 0.0
        self._advance_count = 0
        self._total_awakened = 0
        
    @property
    def time(self) -> float:
        """获取当前时间"""
        with self._lock:
            return self._current_time
    
    @property
    def time_ns(self) -> int:
        """获取当前时间（纳秒）"""
        return int(self.time * 1e9)
    
    @property
    def now_ms(self) -> int:
        """获取当前时间（毫秒）"""
        return int(self.time * 1000)
    
    def now_us(self) -> int:
        """获取当前时间（微秒）"""
        return int(self.time * 1e6)
    
    @property
    def advance_count(self) -> int:
        """获取 advance 调用次数"""
        with self._lock:
            return self._advance_count
    
    @property
    def total_awakened(self) -> int:
        """获取总唤醒任务数"""
        with self._lock:
            return self._total_awakened
    
    async def sleep(self, seconds: float) -> None:
        """异步睡眠 - 由 advance 驱动"""
        if seconds <= 0:
            return
        
        with self._lock:
            current_time = self._current_time
            wake_at = current_time + seconds
        
        future = asyncio.Future()
        
        task_info = {
            'wake_at': wake_at,
            'future': future,
            'sleep_duration': seconds,
            'scheduled_at': current_time
        }
        
        with self._lock:
            self._scheduled_tasks.append(task_info)
            self._scheduled_tasks.sort(key=lambda x: x['wake_at'])
        
        try:
            await future
        except asyncio.CancelledError:
            future.cancel()
            raise
    
    async def sleep_ms(self, milliseconds: int) -> None:
        """异步睡眠（毫秒）- 由 advance 驱动"""
        await self.sleep(milliseconds / 1000.0)
    
    def advance(self, seconds: float = 0.1) -> List[Dict[str, Any]]:
        """
        推进时间并精准唤醒到期任务
        
        按 wake_at 顺序唤醒，保证任务按预期顺序执行。
        
        Args:
            seconds: 要推进的秒数
            
        Returns:
            被唤醒的任务信息列表（按唤醒顺序）
        """
        with self._lock:
            self._current_time += seconds
            self._time_offset += seconds
            self._advance_count += 1
            
            awakened = []
            remaining = []
            
            for task in self._scheduled_tasks:
                if task['wake_at'] <= self._current_time:
                    if not task['future'].done():
                        task['future'].set_result(None)
                        self._total_awakened += 1
                    awakened.append({
                        'wake_at': task['wake_at'],
                        'sleep_duration': task['sleep_duration'],
                        'was_scheduled_at': task['scheduled_at'],
                        'actual_delay': self._current_time - task['scheduled_at'] - task['sleep_duration']
                    })
                else:
                    remaining.append(task)
            
            self._scheduled_tasks = remaining
            return awakened
    
    def get_scheduled_tasks(self) -> List[Dict[str, Any]]:
        """获取当前待执行任务（不修改状态）"""
        with self._lock:
            return [
                {
                    'wake_at': t['wake_at'],
                    'sleep_duration': t['sleep_duration'],
                    'remaining': t['wake_at'] - self._current_time
                }
                for t in self._scheduled_tasks
            ]
    
    def advance_to(self, target_time: float) -> List[Dict[str, Any]]:
        """推进到指定时间"""
        delta = target_time - self._current_time
        if delta > 0:
            return self.advance(delta)
        return []
    
    def cancel_all(self) -> None:
        """取消所有待执行任务"""
        with self._lock:
            for task in self._scheduled_tasks:
                if not task['future'].done():
                    task['future'].cancel()
            self._scheduled_tasks.clear()
    
    def scheduled_count(self) -> int:
        """获取待执行任务数量"""
        with self._lock:
            return len(self._scheduled_tasks)


class ClockContext:
    """时钟上下文管理器，用于 patch 全局时间函数"""
    
    def __init__(self, clock: FakeClock):
        self._clock = clock
        self._original_time = None
        self._original_time_ns = None
        self._original_sleep = None
        self._real_asyncio_sleep = None
        
    def __enter__(self):
        import asyncio
        
        self._original_time = time.time
        self._original_time_ns = time.time_ns
        self._original_sleep = asyncio.sleep
        
        self._real_asyncio_sleep = asyncio.sleep
        
        time.time = lambda: self._clock.time
        time.time_ns = lambda: self._clock.time_ns
        
        async def patched_sleep(seconds: float):
            await self._clock.sleep(seconds)
            
        asyncio.sleep = patched_sleep
        
        return self._clock
    
    def real_sleep(self, seconds: float):
        """提供真正的 asyncio.sleep（不被 patch）"""
        return self._real_asyncio_sleep(seconds)
    
    def __exit__(self, exc_type, exc_val, exc_tb):
        import time
        import asyncio
        
        if self._original_time:
            time.time = self._original_time
            time.time_ns = self._original_time_ns
            asyncio.sleep = self._original_sleep
        
        return False
    
    async def __aenter__(self):
        return self.__enter__()
    
    async def __aexit__(self, exc_type, exc_val, exc_tb):
        return self.__exit__(exc_type, exc_val, exc_tb)


@dataclass
class IntervalTracker:
    """时间间隔追踪器"""
    intervals: list = field(default_factory=list)
    _last_time: float = 0.0
    
    def record(self, current_time: float) -> None:
        if self._last_time > 0:
            self.intervals.append(current_time - self._last_time)
        self._last_time = current_time
    
    def average(self) -> float:
        if not self.intervals:
            return 0.0
        return sum(self.intervals) / len(self.intervals)
    
    def max(self) -> float:
        return max(self.intervals) if self.intervals else 0.0
    
    def min(self) -> float:
        return min(self.intervals) if self.intervals else 0.0
    
    def reset(self) -> None:
        self.intervals.clear()
        self._last_time = 0.0
