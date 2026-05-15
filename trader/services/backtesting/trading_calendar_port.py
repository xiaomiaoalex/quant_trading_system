"""
trading_calendar_port.py - P9.5 交易市场日历端口
================================================
Service 层交易日历接口，用于回测和模拟环境。

核心协议：
- TradingCalendarPort: 交易市场日历查询接口
- FakeTradingCalendar: 用于测试的假实现

不接入真实行情、券商或交易所 API。

参考: docs/INTERFACE_CONTRACTS.md P9.5 TradingCalendarPort 契约
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass
from datetime import datetime, timezone
from enum import Enum
from typing import Protocol, runtime_checkable


class TradingPhase(Enum):
    """交易时段"""

    PRE_OPEN = "PRE_OPEN"
    CALL_AUCTION = "CALL_AUCTION"
    CONTINUOUS = "CONTINUOUS"
    CLOSING_AUCTION = "CLOSING_AUCTION"
    POST_CLOSE = "POST_CLOSE"
    SUSPENDED = "SUSPENDED"
    CLOSED = "CLOSED"


@dataclass(frozen=True, slots=True)
class TradingSession:
    """单个交易会话"""

    date: datetime
    open_time: datetime
    close_time: datetime
    phase: TradingPhase
    is_trading_day: bool


@dataclass(frozen=True, slots=True)
class TradingCalendarSnapshot:
    """交易日历快照"""

    symbol: str
    session: TradingSession
    is_suspended: bool = False
    limit_up: float | None = None
    limit_down: float | None = None


class TradingCalendarPort(Protocol):
    """
    交易市场日历端口

    定义交易市场日历查询的接口。

    实现要求：
    1. is_trading_day: 查询指定日期是否为交易日
    2. get_trading_phase: 获取指定时间的交易时段
    3. get_trading_session: 获取指定日期的交易会话

    示例：
        class ChinaStockCalendar:
            async def is_trading_day(self, symbol: str, dt: datetime) -> bool:
                ...

            async def get_trading_phase(self, symbol: str, dt: datetime) -> TradingPhase:
                ...

            async def get_trading_session(
                self, symbol: str, date: datetime
            ) -> TradingSession:
                ...
    """

    async def is_trading_day(self, symbol: str, dt: datetime) -> bool:
        """查询指定日期是否为交易日"""
        ...

    async def get_trading_phase(self, symbol: str, dt: datetime) -> TradingPhase:
        """获取指定时间的交易时段"""
        ...

    async def get_trading_session(self, symbol: str, date: datetime) -> TradingSession:
        """获取指定日期的交易会话"""
        ...

    async def get_calendar_snapshot(self, symbol: str, dt: datetime) -> TradingCalendarSnapshot:
        """获取交易日历快照（包含停牌、涨跌停等信息）"""
        ...


class FakeTradingCalendar(TradingCalendarPort):
    """Fake 交易市场日历（用于测试）"""

    def __init__(
        self,
        trading_days: list[datetime] | None = None,
        always_open: bool = True,
    ) -> None:
        self._trading_days = trading_days or []
        self._always_open = always_open

    async def is_trading_day(self, symbol: str, dt: datetime) -> bool:
        if self._always_open:
            return True
        return dt.date() in [d.date() for d in self._trading_days]

    async def get_trading_phase(self, symbol: str, dt: datetime) -> TradingPhase:
        if not self._always_open:
            if dt.date() not in [d.date() for d in self._trading_days]:
                return TradingPhase.CLOSED
        return TradingPhase.CONTINUOUS

    async def get_trading_session(self, symbol: str, date: datetime) -> TradingSession:
        is_trading = await self.is_trading_day(symbol, date)
        return TradingSession(
            date=date,
            open_time=datetime(2024, 1, 1, 9, 30, tzinfo=timezone.utc),
            close_time=datetime(2024, 1, 1, 15, 0, tzinfo=timezone.utc),
            phase=await self.get_trading_phase(symbol, date),
            is_trading_day=is_trading,
        )

    async def get_calendar_snapshot(self, symbol: str, dt: datetime) -> TradingCalendarSnapshot:
        session = await self.get_trading_session(symbol, dt)
        return TradingCalendarSnapshot(
            symbol=symbol,
            session=session,
            is_suspended=False,
        )


class ChinaStockCalendar(FakeTradingCalendar):
    """A 股交易日历（配置化，无真实数据源）"""

    WEEKEND_TRADING: bool = False

    def __init__(
        self,
        trading_days: list[datetime] | None = None,
        suspended_symbols: list[str] | None = None,
    ) -> None:
        super().__init__(trading_days=trading_days, always_open=False)
        self._suspended_symbols = suspended_symbols or []

    async def is_trading_day(self, symbol: str, dt: datetime) -> bool:
        weekday = dt.weekday()
        if weekday >= 5:
            return False
        if self._trading_days:
            return dt.date() in [d.date() for d in self._trading_days]
        return True

    async def get_trading_phase(self, symbol: str, dt: datetime) -> TradingPhase:
        if not await self.is_trading_day(symbol, dt):
            return TradingPhase.CLOSED
        if symbol in self._suspended_symbols:
            return TradingPhase.SUSPENDED

        hour = dt.hour
        minute = dt.minute

        if hour < 9 or (hour == 9 and minute < 30):
            return TradingPhase.PRE_OPEN
        if hour == 9 and minute < 45:
            return TradingPhase.CALL_AUCTION
        if (hour == 11 and minute >= 30) or (hour == 12):
            return TradingPhase.CLOSED
        if hour >= 15:
            return TradingPhase.POST_CLOSE
        return TradingPhase.CONTINUOUS

    async def get_calendar_snapshot(self, symbol: str, dt: datetime) -> TradingCalendarSnapshot:
        is_trading = await self.is_trading_day(symbol, dt)
        phase = await self.get_trading_phase(symbol, dt)
        is_suspended = symbol in self._suspended_symbols

        return TradingCalendarSnapshot(
            symbol=symbol,
            session=TradingSession(
                date=dt,
                open_time=datetime(dt.year, dt.month, dt.day, 9, 30, tzinfo=timezone.utc),
                close_time=datetime(dt.year, dt.month, dt.day, 15, 0, tzinfo=timezone.utc),
                phase=phase,
                is_trading_day=is_trading,
            ),
            is_suspended=is_suspended,
        )
