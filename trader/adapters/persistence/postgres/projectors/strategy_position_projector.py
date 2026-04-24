"""
StrategyPositionProjector - 策略级持仓投影器
==========================================
消费 Lot 级事件，写入 strategy_positions_proj 和 position_lots 表。

事件类型：
- POSITION_LOT_OPENED: 新批次开仓
- POSITION_LOT_REDUCED: 批次部分平仓
- POSITION_LOT_CLOSED: 批次完全平仓
- STRATEGY_POSITION_UPDATED: 策略持仓汇总变更

投影表：
- strategy_positions_proj: aggregate_id = {strategy_id}:{symbol}
- position_lots: 一笔买入 = 一条记录
"""
import json
import logging
from datetime import datetime, timezone
from decimal import Decimal
from typing import Any, Dict, List, Optional, TYPE_CHECKING

from trader.adapters.persistence.postgres.projectors.base import Projectable

if TYPE_CHECKING:
    import asyncpg
    from trader.core.domain.models.events import DomainEvent
    from trader.adapters.persistence.postgres.event_store import StreamEvent

logger = logging.getLogger(__name__)


# ==================== 辅助函数 ====================

def _dec(val: Any) -> Decimal:
    if isinstance(val, Decimal):
        return val
    if val is None:
        return Decimal("0")
    return Decimal(str(val))


# ==================== StrategyPositionProjector ====================

class StrategyPositionProjector(Projectable):
    """
    策略级持仓投影器。

    将 Lot 级事件投影为可查询的读模型，写入两张表：
    - strategy_positions_proj: 策略持仓汇总
    - position_lots: 批次明细
    """

    EVENT_TYPES = {
        "POSITION_LOT_OPENED",
        "POSITION_LOT_REDUCED",
        "POSITION_LOT_CLOSED",
        "STRATEGY_POSITION_UPDATED",
    }

    def __init__(self, pool: "asyncpg.Pool"):
        super().__init__(
            pool=pool,
            table_name="strategy_positions_proj",
            snapshot_table_name="strategy_position_snapshots",
            event_types=[et for et in self.EVENT_TYPES],
        )
        self._lots_table = "position_lots"

    def get_projection_id_field(self) -> str:
        return "aggregate_id"

    def extract_aggregate_id(self, event: "StreamEvent") -> str:
        # aggregate_id = {strategy_id}:{symbol}
        return event.aggregate_id

    # ==================== 事件应用 ====================

    def _init_state(self) -> Dict[str, Any]:
        return {
            "strategy_id": "",
            "symbol": "",
            "total_qty": "0",
            "avg_cost": "0",
            "realized_pnl": "0",
            "unrealized_pnl": "0",
            "status": "ACTIVE",
            "lot_count": 0,
            "updated_at": datetime.now(timezone.utc).isoformat(),
        }

    def _apply_lot_opened(
        self,
        state: Dict[str, Any],
        data: Dict[str, Any],
    ) -> Dict[str, Any]:
        """处理批次开仓：写入 position_lots 表，汇总更新"""
        # 更新汇总
        state["strategy_id"] = data.get("strategy_id", "")
        state["symbol"] = data.get("symbol", "")
        state["updated_at"] = datetime.now(timezone.utc).isoformat()
        # lot_count 由独立查询维护，这里只标记需要刷新
        return state

    def _apply_lot_reduced(
        self,
        state: Dict[str, Any],
        data: Dict[str, Any],
    ) -> Dict[str, Any]:
        """处理批次减仓：更新 position_lots 表"""
        state["updated_at"] = datetime.now(timezone.utc).isoformat()
        return state

    def _apply_lot_closed(
        self,
        state: Dict[str, Any],
        data: Dict[str, Any],
    ) -> Dict[str, Any]:
        """处理批次完全平仓"""
        state["updated_at"] = datetime.now(timezone.utc).isoformat()
        return state

    def _apply_strategy_position_updated(
        self,
        state: Dict[str, Any],
        data: Dict[str, Any],
    ) -> Dict[str, Any]:
        """处理策略持仓汇总更新"""
        state["strategy_id"] = data.get("strategy_id", "")
        state["symbol"] = data.get("symbol", "")
        state["total_qty"] = str(_dec(data.get("total_qty", "0")))
        state["avg_cost"] = str(_dec(data.get("avg_cost", "0")))
        state["realized_pnl"] = str(_dec(data.get("realized_pnl", "0")))
        state["unrealized_pnl"] = str(_dec(data.get("unrealized_pnl", "0")))
        state["updated_at"] = datetime.now(timezone.utc).isoformat()
        return state

    def compute_projection(
        self,
        aggregate_id: str,
        events: List["StreamEvent"],
    ) -> Dict[str, Any]:
        state = self._init_state()
        state["position_id"] = aggregate_id
        # 解析 strategy_id 和 symbol
        parts = aggregate_id.split(":", 1)
        if len(parts) == 2:
            state["strategy_id"] = parts[0]
            state["symbol"] = parts[1]

        for event in events:
            data = event.data if isinstance(event.data, dict) else {}
            et = event.event_type
            if et == "POSITION_LOT_OPENED":
                state = self._apply_lot_opened(state, data)
            elif et == "POSITION_LOT_REDUCED":
                state = self._apply_lot_reduced(state, data)
            elif et == "POSITION_LOT_CLOSED":
                state = self._apply_lot_closed(state, data)
            elif et == "STRATEGY_POSITION_UPDATED":
                state = self._apply_strategy_position_updated(state, data)

        return state

    # ==================== Lot 明细写入 ====================

    async def upsert_lot(self, data: Dict[str, Any]) -> None:
        """
        写入或更新 position_lots 表。

        处理两类事件：
        - POSITION_LOT_OPENED: 插入新记录
        - POSITION_LOT_REDUCED: 更新 remaining_qty
        - POSITION_LOT_CLOSED: 更新 is_closed=True
        """
        lot_id = data.get("lot_id")
        if not lot_id:
            return

        async with self._pool.acquire() as conn:
            if data.get("is_new", False):
                # 插入新批次
                await conn.execute(
                    f"""
                    INSERT INTO {self._lots_table} (
                        lot_id, position_id, strategy_id, symbol,
                        original_qty, remaining_qty, fill_price,
                        fee_qty, fee_asset, realized_pnl,
                        filled_at, is_closed
                    ) VALUES ($1,$2,$3,$4,$5,$6,$7,$8,$9,$10,$11,$12)
                    ON CONFLICT (lot_id) DO NOTHING
                    """,
                    lot_id,
                    data.get("position_id", ""),
                    data.get("strategy_id", ""),
                    data.get("symbol", ""),
                    float(_dec(data.get("original_qty", "0"))),
                    float(_dec(data.get("remaining_qty", "0"))),
                    float(_dec(data.get("fill_price", "0"))),
                    float(_dec(data.get("fee_qty", "0"))),
                    data.get("fee_asset"),
                    float(_dec(data.get("realized_pnl", "0"))),
                    data.get("filled_at") or datetime.now(timezone.utc),
                    data.get("is_closed", False),
                )
            elif data.get("is_closed"):
                # 批次关闭
                await conn.execute(
                    f"""
                    UPDATE {self._lots_table}
                    SET remaining_qty = $2,
                        realized_pnl = $3,
                        closed_at = $4,
                        is_closed = TRUE
                    WHERE lot_id = $1
                    """,
                    lot_id,
                    float(_dec(data.get("remaining_qty", "0"))),
                    float(_dec(data.get("realized_pnl", "0"))),
                    datetime.now(timezone.utc),
                )
            else:
                # 部分平仓：更新 remaining_qty 和 realized_pnl
                await conn.execute(
                    f"""
                    UPDATE {self._lots_table}
                    SET remaining_qty = $2,
                        realized_pnl = realized_pnl + $3
                    WHERE lot_id = $1
                    """,
                    lot_id,
                    float(_dec(data.get("remaining_qty", "0"))),
                    float(_dec(data.get("realized_pnl", "0"))),
                )

    # ==================== 查询接口 ====================

    async def get_strategy_position(
        self,
        strategy_id: str,
        symbol: str,
    ) -> Optional[Dict[str, Any]]:
        aggregate_id = f"{strategy_id}:{symbol}"
        return await self.get_projection(aggregate_id)

    async def list_open_lots(
        self,
        strategy_id: str,
        symbol: str,
    ) -> List[Dict[str, Any]]:
        """查询某策略某标的的未关闭批次"""
        async with self._pool.acquire() as conn:
            rows = await conn.fetch(
                f"""
                SELECT * FROM {self._lots_table}
                WHERE strategy_id = $1 AND symbol = $2 AND NOT is_closed
                ORDER BY filled_at ASC
                """,
                strategy_id,
                symbol,
            )
        return [dict(r) for r in rows]

    async def list_all_lots(
        self,
        strategy_id: str,
        symbol: str,
    ) -> List[Dict[str, Any]]:
        """查询某策略某标的的所有批次（含已关闭）"""
        async with self._pool.acquire() as conn:
            rows = await conn.fetch(
                f"""
                SELECT * FROM {self._lots_table}
                WHERE strategy_id = $1 AND symbol = $2
                ORDER BY filled_at ASC
                """,
                strategy_id,
                symbol,
            )
        return [dict(r) for r in rows]

    async def list_strategy_positions(
        self,
        strategy_id: Optional[str] = None,
        symbol: Optional[str] = None,
        status: Optional[str] = None,
        limit: int = 100,
    ) -> List[Dict[str, Any]]:
        """列出策略持仓，支持过滤"""
        async with self._pool.acquire() as conn:
            where_parts = []
            params = []
            idx = 1

            if strategy_id:
                where_parts.append(f"(state->>'strategy_id') = ${idx}")
                params.append(strategy_id)
                idx += 1
            if symbol:
                where_parts.append(f"(state->>'symbol') = ${idx}")
                params.append(symbol)
                idx += 1
            if status:
                where_parts.append(f"(state->>'status') = ${idx}")
                params.append(status)
                idx += 1

            where_clause = " AND ".join(where_parts) if where_parts else "1=1"

            rows = await conn.fetch(
                f"""
                SELECT aggregate_id, state, version, last_event_seq, updated_at
                FROM {self._table_name}
                WHERE {where_clause}
                ORDER BY updated_at DESC
                LIMIT ${idx} OFFSET ${idx + 1}
                """,
                *params,
                limit,
                0,
            )

        results = []
        for row in rows:
            state = row["state"]
            if isinstance(state, str):
                state = json.loads(state)
            results.append({
                "aggregate_id": row["aggregate_id"],
                "state": state,
                "version": row["version"],
                "last_event_seq": row["last_event_seq"],
                "updated_at": row["updated_at"],
            })
        return results
