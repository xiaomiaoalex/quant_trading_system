from typing import List, Optional

from trader.storage.in_memory import get_storage, InMemoryStorage
from trader.api.models.schemas import (
    BrokerAccount, BrokerStatus,
)


class BrokerService:
    """Service for broker management"""

    def __init__(self, storage: Optional[InMemoryStorage] = None):
        self._storage = storage or get_storage()

    def list_brokers(self) -> List[BrokerAccount]:
        """List broker accounts"""
        brokers = self._storage.list_brokers()
        return [BrokerAccount(**b) for b in brokers]

    def get_status(self, account_id: str) -> Optional[BrokerStatus]:
        """Get broker connection status"""
        status = self._storage.get_broker_status(account_id)
        if status:
            return BrokerStatus(**status)
        return None
