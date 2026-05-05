"""
Fakes - 测试用伪对象
====================
提供用于单元测试的 Fake 对象，包括：
- FakeClock: 可推进时间
- FakeWebSocket: 多种故障模式
- FakeHTTPClient: 可脚本化返回序列
"""

from trader.tests.fakes.fake_clock import ClockContext, FakeClock, IntervalTracker
from trader.tests.fakes.fake_http import (
    FakeHTTPClient,
    FakeResponse,
    HTTPResponse,
    HTTPResponseType,
    ResponseScript,
    create_rate_limit_script,
    create_server_error_script,
)
from trader.tests.fakes.fake_websocket import (
    ConnectionClosedError,
    FakeWebSocket,
    PingPongScript,
    WebSocketPair,
    WSConfig,
    WSMode,
)

__all__ = [
    "FakeClock",
    "ClockContext",
    "IntervalTracker",
    "FakeWebSocket",
    "WebSocketPair",
    "WSMode",
    "WSConfig",
    "PingPongScript",
    "ConnectionClosedError",
    "FakeHTTPClient",
    "FakeResponse",
    "ResponseScript",
    "HTTPResponse",
    "HTTPResponseType",
    "create_rate_limit_script",
    "create_server_error_script",
]
