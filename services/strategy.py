from typing import List, Optional, Dict, Any
from datetime import datetime, timezone

from trader.storage.in_memory import get_storage, InMemoryStorage
from trader.api.models.schemas import (
    Strategy, StrategyRegisterRequest, StrategyVersion, StrategyVersionCreateRequest,
    VersionedConfig, VersionedConfigUpsertRequest,
)


class StrategyService:
    """Service for managing strategies"""

    def __init__(self, storage: Optional[InMemoryStorage] = None):
        self._storage = storage or get_storage()

    def register_strategy(self, request: StrategyRegisterRequest) -> Strategy:
        """Register a new strategy"""
        strategy_data = request.model_dump()
        strategy = self._storage.create_strategy(strategy_data)
        return Strategy(**strategy)

    def get_strategy(self, strategy_id: str) -> Optional[Strategy]:
        """Get a strategy by ID"""
        strategy = self._storage.get_strategy(strategy_id)
        if strategy:
            return Strategy(**strategy)
        return None

    def list_strategies(self) -> List[Strategy]:
        """List all strategies"""
        strategies = self._storage.list_strategies()
        return [Strategy(**s) for s in strategies]

    def create_version(self, strategy_id: str, request: StrategyVersionCreateRequest) -> StrategyVersion:
        """Create a new strategy version"""
        version_data = request.model_dump()
        version = self._storage.create_strategy_version(strategy_id, version_data)
        return StrategyVersion(**version)

    def get_version(self, strategy_id: str, version: int) -> Optional[StrategyVersion]:
        """Get a specific strategy version"""
        version = self._storage.get_strategy_version(strategy_id, version)
        if version:
            return StrategyVersion(**version)
        return None

    def list_versions(self, strategy_id: str) -> List[StrategyVersion]:
        """List all versions of a strategy"""
        versions = self._storage.list_strategy_versions(strategy_id)
        return [StrategyVersion(**v) for v in versions]

    def get_latest_params(self, strategy_id: str) -> Optional[VersionedConfig]:
        """Get latest strategy params"""
        params = self._storage.get_latest_strategy_params(strategy_id)
        if params:
            return VersionedConfig(**params)
        return None

    def create_params(self, strategy_id: str, request: VersionedConfigUpsertRequest) -> VersionedConfig:
        """Create new strategy params version"""
        params_data = request.model_dump()
        params = self._storage.create_strategy_params(strategy_id, params_data)
        return VersionedConfig(**params)
