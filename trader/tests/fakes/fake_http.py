"""
Fake HTTP Client - 可脚本化返回序列
===================================
用于测试的 HTTP 客户端模拟器，支持可控的响应序列。
"""
import asyncio
import time
from typing import Optional, Any, Dict, List, Callable, Union, TYPE_CHECKING
from dataclasses import dataclass, field
from enum import Enum
from threading import Lock
import asyncio

if TYPE_CHECKING:
    from trader.tests.fakes.fake_clock import FakeClock


class HTTPResponseType(Enum):
    """HTTP 响应类型"""
    OK = 200
    BAD_REQUEST = 400
    UNAUTHORIZED = 401
    FORBIDDEN = 403
    NOT_FOUND = 404
    RATE_LIMITED = 429
    INTERNAL_ERROR = 500
    BAD_GATEWAY = 502
    SERVICE_UNAVAILABLE = 503
    TIMEOUT = 504


@dataclass
class HTTPResponse:
    """HTTP 响应"""
    status: int
    headers: Dict[str, str] = field(default_factory=dict)
    body: Any = None
    error: Optional[Exception] = None


@dataclass
class RequestRecord:
    """请求记录"""
    method: str
    url: str
    headers: Dict[str, str] = field(default_factory=dict)
    timestamp: float = 0.0
    timestamp_ns: int = 0
    duration: float = 0.0
    duration_ns: int = 0


class ResponseScript:
    """响应脚本 - 定义一系列响应"""
    
    def __init__(self):
        self._responses: List[HTTPResponse] = []
        self._callbacks: List[Callable[[RequestRecord], HTTPResponse]] = []
        self._index = 0
        self._lock = Lock()
    
    def add_response(self, response: HTTPResponse) -> 'ResponseScript':
        """添加固定响应"""
        self._responses.append(response)
        return self
    
    def add_ok(self, body: Any = None, headers: Optional[Dict] = None) -> 'ResponseScript':
        """添加 200 OK"""
        return self.add_response(HTTPResponse(200, headers or {}, body))
    
    def add_429(self, retry_after: int = 60, body: Any = None) -> 'ResponseScript':
        """添加 429 响应"""
        return self.add_response(HTTPResponse(
            429, 
            {"Retry-After": str(retry_after)}, 
            body
        ))
    
    def add_5xx(self, status: int = 500, body: Any = None) -> 'ResponseScript':
        """添加 5xx 响应"""
        return self.add_response(HTTPResponse(status, {}, body))
    
    def add_timeout(self) -> 'ResponseScript':
        """添加超时响应"""
        return self.add_response(HTTPResponse(504, {}, None, asyncio.TimeoutError("Request timeout")))
    
    def add_error(self, error: Exception) -> 'ResponseScript':
        """添加错误响应"""
        return self.add_response(HTTPResponse(0, {}, None, error))
    
    def add_callback(self, callback: Callable[[RequestRecord], HTTPResponse]) -> 'ResponseScript':
        """添加回调响应"""
        self._callbacks.append(callback)
        return self
    
    def get_response(self, request: RequestRecord) -> HTTPResponse:
        """获取响应"""
        with self._lock:
            if self._callbacks:
                for callback in self._callbacks:
                    response = callback(request)
                    if response is not None:
                        return response
            
            if self._index < len(self._responses):
                response = self._responses[self._index]
                self._index += 1
                return response
            
            return HTTPResponse(200, {}, {"message": "OK"})
    
    def reset(self) -> None:
        """重置索引"""
        with self._lock:
            self._index = 0
    
    def remaining(self) -> int:
        """剩余响应数量"""
        with self._lock:
            return max(0, len(self._responses) - self._index)


class FakeHTTPClient:
    """
    伪 HTTP 客户端
    
    特性：
    - 可脚本化返回序列
    - 记录调用次数、时间间隔
    - 支持断言"不会风暴"
    - 支持注入 now_fn/clock 以实现时间可控
    """
    
    def __init__(
        self, 
        now_fn: Optional[Callable[[], float]] = None,
        now_fn_ns: Optional[Callable[[], int]] = None,
        clock: Optional["FakeClock"] = None
    ):
        """
        Args:
            now_fn: 可选的时间函数，返回秒级时间戳。
                   如果不提供，默认使用 time.perf_counter()
            now_fn_ns: 可选的纳秒级时间函数。
                      如果不提供，默认使用 time.perf_counter_ns()
            clock: 可选的 FakeClock 实例。
                  如果提供，将使用 clock.time 和 clock.time_ns
        """
        self._scripts: Dict[str, ResponseScript] = {}
        self._lock = Lock()
        
        self._request_history: List[RequestRecord] = []
        self._request_times: List[float] = []
        
        self._total_requests = 0
        self._total_errors = 0
        
        self._closed = False
        
        self._default_script = ResponseScript().add_ok()
        
        if clock is not None:
            self._now_fn = lambda: clock.time
            self._now_fn_ns = lambda: clock.time_ns
        else:
            self._now_fn = now_fn or time.perf_counter
            self._now_fn_ns = now_fn_ns or time.perf_counter_ns
    
    def set_now_fn(self, now_fn: Callable[[], float], now_fn_ns: Optional[Callable[[], int]] = None) -> None:
        """注入时间函数"""
        self._now_fn = now_fn
        self._now_fn_ns = now_fn_ns or now_fn_ns
    
    @property
    def now(self) -> float:
        """获取当前时间（使用注入的 now_fn）"""
        return self._now_fn()
    
    @property
    def now_ns(self) -> int:
        """获取当前时间（纳秒）"""
        return self._now_fn_ns()
    
    def add_script(self, pattern: str, script: ResponseScript) -> None:
        """添加 URL 模式对应的脚本"""
        self._scripts[pattern] = script
    
    def get_or_create_script(self, pattern: str) -> ResponseScript:
        """获取或创建脚本"""
        with self._lock:
            if pattern not in self._scripts:
                self._scripts[pattern] = ResponseScript()
            return self._scripts[pattern]
    
    def add_response_sequence(self, pattern: str, responses: List[HTTPResponse]) -> None:
        """添加响应序列"""
        script = self.get_or_create_script(pattern)
        for resp in responses:
            script.add_response(resp)
    
    async def request(
        self, 
        method: str, 
        url: str, 
        headers: Optional[Dict] = None,
        **kwargs
    ) -> 'FakeResponse':
        """发送请求 - 记录时间戳（使用注入的 now_fn）"""
        if self._closed:
            raise RuntimeError("Client is closed")
        
        start_time = self._now_fn()
        start_time_ns = self._now_fn_ns()
        
        record = RequestRecord(
            method=method,
            url=url,
            headers=headers or {},
            timestamp=start_time,
            timestamp_ns=start_time_ns
        )
        
        with self._lock:
            self._request_history.append(record)
            self._request_times.append(start_time)
            self._total_requests += 1
        
        script = self._find_script(url)
        response = script.get_response(record)
        
        duration = self._now_fn() - start_time
        duration_ns = self._now_fn_ns() - start_time_ns
        record.duration = duration
        record.duration_ns = duration_ns
        
        if response.error:
            with self._lock:
                self._total_errors += 1
            raise response.error
        
        return FakeResponse(response.status, response.headers, response.body)
    
    def _find_script(self, url: str) -> ResponseScript:
        """查找匹配的脚本"""
        with self._lock:
            for pattern, script in self._scripts.items():
                if pattern in url:
                    return script
            return self._default_script
    
    def get_request_count(self, pattern: Optional[str] = None) -> int:
        """获取请求次数"""
        with self._lock:
            if pattern:
                return sum(1 for r in self._request_history if pattern in r.url)
            return self._total_requests
    
    def get_request_interval_stats(self, pattern: Optional[str] = None) -> Dict[str, float]:
        """获取请求间隔统计（秒级）"""
        with self._lock:
            times = self._request_times
            if pattern:
                times = [r.timestamp for r in self._request_history if pattern in r.url]
            
            if len(times) < 2:
                return {"count": len(times), "min": 0, "max": 0, "avg": 0}
            
            intervals = [times[i+1] - times[i] for i in range(len(times)-1)]
            
            return {
                "count": len(times),
                "min": min(intervals),
                "max": max(intervals),
                "avg": sum(intervals) / len(intervals)
            }
    
    def get_request_interval_stats_ns(self, pattern: Optional[str] = None) -> Dict[str, int]:
        """获取请求间隔统计（纳秒级）- 用于精确断言无风暴"""
        with self._lock:
            records = self._request_history
            if pattern:
                records = [r for r in self._request_history if pattern in r.url]
            
            if len(records) < 2:
                return {"count": len(records), "min_ns": 0, "max_ns": 0, "avg_ns": 0}
            
            intervals_ns = [
                records[i+1].timestamp_ns - records[i].timestamp_ns 
                for i in range(len(records)-1)
            ]
            
            return {
                "count": len(records),
                "min_ns": min(intervals_ns),
                "max_ns": max(intervals_ns),
                "avg_ns": sum(intervals_ns) // len(intervals_ns)
            }
    
    def assert_no_request_storm(self, pattern: str, min_interval_ns: int) -> bool:
        """
        断言：请求间隔大于指定阈值（无风暴）
        
        Args:
            pattern: URL 模式
            min_interval_ns: 最小间隔（纳秒）
            
        Returns:
            True if no storm detected
            
        Raises:
            AssertionError: 如果检测到请求风暴
        """
        stats = self.get_request_interval_stats_ns(pattern)
        if stats["min_ns"] < min_interval_ns:
            raise AssertionError(
                f"Request storm detected for {pattern}: "
                f"min interval = {stats['min_ns']}ns < {min_interval_ns}ns"
            )
        return True
    
    def get_request_history(self, pattern: Optional[str] = None) -> List[RequestRecord]:
        """获取请求历史"""
        with self._lock:
            if pattern:
                return [r for r in self._request_history if pattern in r.url]
            return list(self._request_history)
    
    def clear_history(self) -> None:
        """清除历史"""
        with self._lock:
            self._request_history.clear()
            self._request_times.clear()
            self._total_requests = 0
            self._total_errors = 0
    
    def reset_scripts(self) -> None:
        """重置所有脚本"""
        with self._lock:
            for script in self._scripts.values():
                script.reset()
    
    def close(self) -> None:
        """关闭客户端"""
        self._closed = True
    
    @property
    def total_errors(self) -> int:
        return self._total_errors


class FakeResponse:
    """伪响应"""
    
    def __init__(self, status: int, headers: Dict[str, str], body: Any):
        self.status = status
        self.headers = headers
        self._body = body
        self._json = None
    
    def json(self):
        """解析 JSON"""
        if self._json is None:
            import json
            self._json = json.loads(self._body)
        return self._json
    
    async def __aenter__(self):
        return self
    
    async def __aexit__(self, exc_type, exc_val, exc_tb):
        return False


def create_rate_limit_script(retry_after: int = 60) -> ResponseScript:
    """创建限流脚本：先返回 429，然后正常"""
    return ResponseScript().add_429(retry_after).add_ok()


def create_server_error_script(count: int = 3) -> ResponseScript:
    """创建服务器错误脚本：连续 N 次 500"""
    script = ResponseScript()
    for _ in range(count):
        script.add_5xx()
    script.add_ok()
    return script
