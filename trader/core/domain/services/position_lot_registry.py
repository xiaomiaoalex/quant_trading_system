"""
Position Lot Registry - 全局单例
================================
提供全局唯一的 PositionLedgerManager 实例，供 OMS 和 Portfolio 共用。

用法：
    from trader.core.domain.services.position_lot_registry import (
        get_lot_manager,
        set_lot_manager,
        reset_lot_manager,
    )

    # OMS 初始化时设置：
    set_lot_manager(PositionLedgerManager())

    # PortfolioService 查询时获取：
    manager = get_lot_manager()
"""
from trader.core.domain.models.position_lot_manager import PositionLedgerManager

_manager: PositionLedgerManager | None = None


def get_lot_manager() -> PositionLedgerManager:
    """获取全局 PositionLedgerManager 实例（不存在则创建）"""
    global _manager
    if _manager is None:
        _manager = PositionLedgerManager()
    return _manager


def set_lot_manager(manager: PositionLedgerManager) -> None:
    """设置全局 PositionLedgerManager 实例（通常在 OMS 初始化时调用）"""
    global _manager
    _manager = manager


def reset_lot_manager() -> None:
    """重置全局实例（仅用于测试）"""
    global _manager
    _manager = None
