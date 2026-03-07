import warnings
from unittest.mock import AsyncMock, MagicMock

import pytest

import trader.adapters.persistence.risk_repository as risk_repository


class _FakeStorage:
    def __init__(self):
        self.disconnect = AsyncMock()
        self._pool = MagicMock()
        self._connected = True


def test_reset_risk_event_repository_sync_context_awaits_disconnect():
    repo = risk_repository.RiskEventRepository()
    fake_storage = _FakeStorage()
    repo._postgres_storage = fake_storage
    repo._use_postgres = True
    risk_repository._repository_instance = repo

    risk_repository.reset_risk_event_repository()

    assert fake_storage.disconnect.await_count == 1
    fake_storage._pool.terminate.assert_not_called()
    assert risk_repository._repository_instance is None
    assert repo._postgres_storage is None
    assert repo._use_postgres is False


@pytest.mark.asyncio
async def test_reset_risk_event_repository_async_context_terminates_without_warning():
    repo = risk_repository.RiskEventRepository()
    fake_storage = _FakeStorage()
    fake_pool = fake_storage._pool
    repo._postgres_storage = fake_storage
    repo._use_postgres = True
    risk_repository._repository_instance = repo

    with warnings.catch_warnings(record=True) as caught:
        warnings.simplefilter("always")
        risk_repository.reset_risk_event_repository()

    assert fake_storage.disconnect.await_count == 0
    fake_pool.terminate.assert_called_once()
    assert risk_repository._repository_instance is None
    assert repo._postgres_storage is None
    assert repo._use_postgres is False
    assert repo._loop is None
    assert repo._init_lock is None
    assert not [
        warning for warning in caught
        if "was never awaited" in str(warning.message)
    ]
