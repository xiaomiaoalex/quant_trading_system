from typing import List, Optional

from trader.storage.in_memory import get_storage, InMemoryStorage
from trader.api.models.schemas import (
    PositionView, PnlView,
)


class PortfolioService:
    """Service for portfolio positions and PnL"""

    def __init__(self, storage: Optional[InMemoryStorage] = None):
        self._storage = storage or get_storage()

    def list_positions(
        self,
        account_id: Optional[str] = None,
        venue: Optional[str] = None,
    ) -> List[PositionView]:
        """Get positions"""
        positions = self._storage.list_positions(account_id, venue)
        return [PositionView(**p) for p in positions]

    def get_pnl(
        self,
        account_id: Optional[str] = None,
        venue: Optional[str] = None,
    ) -> PnlView:
        """Get PnL summary"""
        pnl = self._storage.calculate_pnl(account_id, venue)
        return PnlView(**pnl)
