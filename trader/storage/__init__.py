"""
Storage Module - In-memory storage for control plane
=================================================
Provides in-memory storage for strategies, deployments, orders, positions, etc.

Entry Points:
- ControlPlaneInMemoryStorage: Primary class for control plane storage
- InMemoryStorage: Alias for ControlPlaneInMemoryStorage (backwards compatible)
- get_storage(): Get global control plane storage instance
- reset_storage(): Reset global control plane storage instance
"""
from trader.storage.in_memory import (
    ControlPlaneInMemoryStorage,
    InMemoryStorage,
    get_storage,
    reset_storage,
)

__all__ = [
    "ControlPlaneInMemoryStorage",
    "InMemoryStorage",
    "get_storage",
    "reset_storage",
]
