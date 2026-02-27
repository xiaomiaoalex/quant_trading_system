"""
Fakes - 测试用伪对象
====================
提供用于单元测试的 Fake 对象，包括：
- FakeClock: 可推进时间
- FakeWebSocket: 多种故障模式
- FakeHTTPClient: 可脚本化返回序列
"""
from trader.tests.fakes.fake_clock import FakeClock, ClockContext, IntervalTracker
from trader.tests.fakes.fake_websocket import FakeWebSocket, WebSocketPair, WSMode, WSConfig, PingPongScript, ConnectionClosedError
from trader.tests.fakes.fake_http import (
    FakeHTTPClient, 
    FakeResponse, 
    ResponseScript, 
    HTTPResponse, 
    HTTPResponseType,
    create_rate_limit_script,
    create_server_error_script
)

__all__ = [
    'FakeClock',
    'ClockContext', 
    'IntervalTracker',
    'FakeWebSocket',
    'WebSocketPair',
    'WSMode',
    'WSConfig',
    'PingPongScript',
    'ConnectionClosedError',
    'FakeHTTPClient',
    'FakeResponse',
    'ResponseScript',
    'HTTPResponse',
    'HTTPResponseType',
    'create_rate_limit_script',
    'create_server_error_script',
]
