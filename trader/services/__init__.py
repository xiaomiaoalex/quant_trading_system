"""
Services - Business logic layer for the control plane
==================================================
Provides service classes that implement business logic for each API domain.
"""
from trader.services.strategy import StrategyService
from trader.services.deployment import DeploymentService, BacktestService
from trader.services.risk import RiskService
from trader.services.order import OrderService
from trader.services.portfolio import PortfolioService
from trader.services.event import EventService
from trader.services.killswitch import KillSwitchService
from trader.services.broker import BrokerService
from trader.services.monitor_service import MonitorService
from trader.services.capital_allocator import (
    CapitalAllocator,
    CapitalAllocatorConfig,
    AllocationDecision,
    StrategyAllocationRequest,
    AllocationResult,
    PortfolioStateProviderPort,
    SimplePortfolioState,
)
