"""Transactional stores share the same isolated account unit of work."""

import asyncio
from contextlib import asynccontextmanager
from copy import deepcopy
from decimal import Decimal

from .models import AccountState, CommandRecord, Hold, OrderAggregate, PlaceOrderCommand


def decode_state(data: dict) -> AccountState:
    state = AccountState(data["account_id"])
    for key in (
        "status",
        "version",
        "executions",
        "ledger",
        "events",
        "outbox",
        "control",
        "reconciliation",
    ):
        if key in data:
            setattr(state, key, data[key])
    for key in ("balances", "positions", "external_locked"):
        setattr(state, key, {k: Decimal(v) for k, v in data.get(key, {}).items()})
    state.holds = {
        k: Hold(v["asset"], Decimal(v["amount"])) for k, v in data.get("holds", {}).items()
    }
    state.commands = {k: CommandRecord(**v) for k, v in data.get("commands", {}).items()}
    for key, value in data.get("orders", {}).items():
        fields = dict(value)
        command = dict(fields.pop("command"))
        command["qty"], command["price"] = Decimal(command["qty"]), Decimal(command["price"])
        for name in ("booked_qty", "booked_quote", "observed_qty"):
            fields[name] = Decimal(fields[name])
        state.orders[key] = OrderAggregate(command=PlaceOrderCommand(**command), **fields)
    return state


class MemoryTradingStore:
    """Deterministic test store; rollback and account serialization match PG semantics."""

    def __init__(self):
        self._states = {}
        self._locks = {}
        self._leases = {}
        self.fail_transactions = False

    async def ping(self):
        return not self.fail_transactions

    async def acquire_lease(self, scope, owner, lease_seconds=30):
        current = self._leases.get(scope)
        if current is not None and current["owner"] != owner and not current["expired"]:
            return None
        token = 1 if current is None or current["owner"] != owner else current["token"]
        self._leases[scope] = {"owner": owner, "token": token, "expired": False}
        return token

    async def renew_lease(self, scope, owner, token, lease_seconds=30):
        current = self._leases.get(scope)
        return bool(
            current
            and current["owner"] == owner
            and current["token"] == token
            and not current["expired"]
        )

    async def release_lease(self, scope, owner, token):
        current = self._leases.get(scope)
        if current and current["owner"] == owner and current["token"] == token:
            self._leases.pop(scope, None)

    def expire_lease(self, scope):
        if scope in self._leases:
            self._leases[scope]["expired"] = True

    @asynccontextmanager
    async def transaction(self, account_id):
        lock = self._locks.setdefault(account_id, asyncio.Lock())
        async with lock:
            state = deepcopy(self._states.get(account_id, AccountState(account_id)))
            yield state
            state.version += 1
            self._states[account_id] = deepcopy(state)

    async def read(self, account_id):
        lock = self._locks.setdefault(account_id, asyncio.Lock())
        async with lock:
            return deepcopy(self._states.get(account_id, AccountState(account_id)))

    async def pending_outbox(self, account_id):
        lock = self._locks.setdefault(account_id, asyncio.Lock())
        async with lock:
            state = self._states.get(account_id, AccountState(account_id))
            return [
                {"event_id": item["event_id"], "data": deepcopy(item["data"])}
                for item in state.outbox
                if not item.get("delivered")
            ]

    async def mark_outbox_delivered(self, account_id, event_ids):
        lock = self._locks.setdefault(account_id, asyncio.Lock())
        async with lock:
            state = self._states.get(account_id)
            if state is None:
                return
            wanted = set(event_ids)
            for item in state.outbox:
                if item["event_id"] in wanted:
                    item["delivered"] = True
            state.version += 1
            self._states[account_id] = deepcopy(state)

    async def initialize(self):
        return None
