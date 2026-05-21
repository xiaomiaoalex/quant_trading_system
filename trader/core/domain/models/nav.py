"""NAVPoint — 净资产价值快照领域模型（无 IO，纯数据）。"""

from __future__ import annotations

from dataclasses import asdict, dataclass
from typing import Any, Dict


@dataclass(slots=True)
class NAVPoint:
    """单次 NAV 快照，在每次成交后由 nav_service 写入。"""

    deployment_id: str
    strategy_id: str
    timestamp_ms: int
    equity: float
    cash: float
    unrealized_pnl: float
    realized_pnl: float
    total_pnl: float

    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)
