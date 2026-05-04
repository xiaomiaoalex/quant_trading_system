from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal

from trader.core.domain.models.crypto_risk import CryptoPositionRisk, OpenOrderRisk
from trader.core.domain.services.open_order_exposure import OpenOrderExposureCalculator


@dataclass(frozen=True, slots=True)
class ClusterExposure:
    cluster: str
    symbols: tuple[str, ...]
    total_risk_notional: Decimal


class PortfolioExposureAggregator:
    def __init__(self) -> None:
        self._open_order_exposure = OpenOrderExposureCalculator()

    def calculate_cluster_exposures(
        self,
        *,
        positions: list[CryptoPositionRisk],
        open_orders: list[OpenOrderRisk],
        mark_prices: dict[str, Decimal],
        symbol_clusters: dict[str, str],
    ) -> dict[str, ClusterExposure]:
        symbols = {self._normalize_symbol(position.symbol) for position in positions}
        symbols.update(self._normalize_symbol(order.symbol) for order in open_orders)
        symbols.discard("")

        cluster_symbols: dict[str, set[str]] = {}
        totals: dict[str, Decimal] = {}
        for symbol in sorted(symbols):
            cluster = self._normalize_cluster(symbol_clusters.get(symbol, ""))
            if not cluster:
                continue
            mark_price = mark_prices.get(symbol, Decimal("0"))
            if mark_price <= 0:
                continue

            exposure = self._open_order_exposure.calculate_symbol_exposure(
                symbol=symbol,
                positions=positions,
                open_orders=open_orders,
                mark_price=mark_price,
            )
            cluster_symbols.setdefault(cluster, set()).add(symbol)
            totals[cluster] = totals.get(cluster, Decimal("0")) + exposure.total_risk_notional

        return {
            cluster: ClusterExposure(
                cluster=cluster,
                symbols=tuple(sorted(cluster_symbols[cluster])),
                total_risk_notional=totals[cluster],
            )
            for cluster in sorted(totals)
        }

    def _normalize_symbol(self, symbol: str) -> str:
        return str(symbol).upper().replace("-", "").replace("/", "").strip()

    def _normalize_cluster(self, cluster: str) -> str:
        return str(cluster).upper().strip()
