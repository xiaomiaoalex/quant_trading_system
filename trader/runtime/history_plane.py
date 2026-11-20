"""Fail-closed assembly of the history -> research plane.

Both process entry points that expose backtest submission share this builder.
It never returns an application whose eligibility gate is absent: constructing
the history store and bridge may fail for many environment reasons, and every
one of those reasons must disable submission with a stable code instead of
falling back to ``eligibility=None``.
"""

from __future__ import annotations

from trader.adapters.persistence.history_dataset_store import HistoryDatasetStore
from trader.adapters.persistence.research_history_bridge_store import ResearchHistoryBridgeStore
from trader.services.history_data_ingestion import HistoryIngestionService
from trader.trading.eligibility import require_published_dataset
from trader.trading.jobs import BacktestApplication, UnavailableBacktestApplication


async def build_history_plane(
    research,
    *,
    history_store_factory=HistoryDatasetStore,
    bridge_factory=ResearchHistoryBridgeStore,
    service_factory=HistoryIngestionService,
):
    """Build the history store, ingestion service and gated application.

    On any assembly failure the caller receives the fail-closed application and
    no history service. The research store stays the durable queue; only the
    ability to submit is withdrawn.
    """
    try:
        history_store = history_store_factory()
        await history_store.connect()
        bridge = bridge_factory(storage=history_store.storage)
        service = service_factory(store=history_store, bridge_store=bridge)

        async def dataset_eligible(dataset_id: str) -> bool:
            """官方资格来自可信关联 + 已发布的历史数据集，绝不只看 source 字符串。"""
            return await require_published_dataset(
                dataset_id, bridge=bridge, history_store=history_store
            )

        application = BacktestApplication(research, eligibility=dataset_eligible)
        return history_store, service, application
    except Exception:
        return None, None, UnavailableBacktestApplication(research)


__all__ = ["build_history_plane"]
