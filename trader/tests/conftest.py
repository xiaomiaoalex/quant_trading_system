"""Shared pytest fixtures for the trader test-suite.

测试隔离约定（见 docs/INTERFACE_CONTRACTS.md「测试隔离与本地 Postgres」）：

- **绝不隐式加载本地 Postgres DSN**。`.env.postgres` 曾经被版本控制跟踪，
  导致任何开发者 clone 后只要本地 Postgres 可用，单元测试就会静默连上真实库，
  读到真实库里的遗留数据而不是本测试写入的数据（Fail-Open，测试结果不可信）。
- 需要真实 PostgreSQL 的**集成测试**必须显式传入 DSN：

      POSTGRES_CONNECTION_STRING=postgresql://trader:trader_pwd@127.0.0.1:5432/trading \\
          python -m pytest -q trader/tests/test_postgres_storage.py

  CI（.github/workflows/ci-gate.yml 的 postgres-integration job）与
  `run_tests.bat` 都是这样做的。
- `.env`（Binance 演示配置）仍然加载，但其中的敏感键会被 `_restore_tracked_env` 剥离。
"""

import asyncio
import importlib
import json
import logging
import os
import socket
import sys
from collections.abc import Iterator
from pathlib import Path
from types import ModuleType

import pytest
from dotenv import load_dotenv

# 只加载 .env（开发用 Binance 演示配置）。绝不加载 .env.postgres：
# 隐式加载本地 DSN 会让单元测试连上真实库，破坏测试隔离。
load_dotenv()


_SANITIZED_ENV_KEYS = (
    "LIVE_TRADING_ENABLED",
    "BINANCE_API_KEY",
    "BINANCE_SECRET_KEY",
    "BINANCE_PROXY_URL",
    "BINANCE_BACKUP_PROXY_URL",
    "BINANCE_PROXY",
    "BINANCE_PROXY_FAILOVER_THRESHOLD",
    "BINANCE_PROXY_FAILOVER_COOLDOWN_SECONDS",
)
_TRACKED_ENV_KEYS = (
    *_SANITIZED_ENV_KEYS,
    "BINANCE_ENV",
    "POSTGRES_CONNECTION_STRING",
    "POSTGRES_HOST",
    "POSTGRES_PORT",
    "POSTGRES_DB",
    "POSTGRES_USER",
    "POSTGRES_PASSWORD",
)
_BASE_ENV = {
    key: None if key in _SANITIZED_ENV_KEYS else os.environ.get(key) for key in _TRACKED_ENV_KEYS
}
try:
    _REAL_ASYNCPG: ModuleType | None = importlib.import_module("asyncpg")
except Exception:
    _REAL_ASYNCPG = None


def _enable_pytest_log_capture() -> None:
    logging.getLogger("trader").propagate = True


def _restore_tracked_env() -> None:
    for key, value in _BASE_ENV.items():
        if value is None:
            os.environ.pop(key, None)
        else:
            os.environ[key] = value
    _enable_pytest_log_capture()


def _restore_asyncpg_module() -> None:
    if _REAL_ASYNCPG is None:
        sys.modules.pop("asyncpg", None)
    else:
        sys.modules["asyncpg"] = _REAL_ASYNCPG


def _reset_global_test_state() -> None:
    from trader.adapters.binance.proxy_failover import reset_proxy_failover_controller
    from trader.adapters.persistence.execution_repository import reset_execution_repository
    from trader.adapters.persistence.market_risk_audit_repository import (
        reset_market_risk_audit_repository,
    )
    from trader.adapters.persistence.position_repository import reset_position_repository
    from trader.adapters.persistence.risk_repository import reset_risk_event_repository
    from trader.core.domain.services.order_ownership_registry import reset_order_ownership_registry
    from trader.core.domain.services.position_lot_registry import reset_lot_manager
    from trader.services.strategy_event_service import reset_strategy_event_service
    from trader.storage.in_memory import reset_storage

    reset_proxy_failover_controller()
    reset_strategy_event_service()
    reset_lot_manager()
    reset_order_ownership_registry()
    reset_execution_repository()
    reset_market_risk_audit_repository()
    reset_position_repository()
    reset_risk_event_repository()
    reset_storage()
    _restore_tracked_env()
    _restore_asyncpg_module()


@pytest.fixture(autouse=True)
def isolate_global_test_state() -> Iterator[None]:
    _reset_global_test_state()
    yield
    _reset_global_test_state()


_restore_tracked_env()


# --------------------------------------------------------------------------- #
# CI test-group manifest, network policy and strict Postgres enforcement.
#
# ci/test_manifest.json is the auditable source of truth for which tests run
# in which CI job.  A new trader/tests/test_*.py file that is not classified
# fails collection instead of silently escaping every required gate.
# --------------------------------------------------------------------------- #

_ROOT = Path(__file__).resolve().parents[2]
_MANIFEST_PATH = _ROOT / "ci" / "test_manifest.json"
_TEST_PREFIX = "trader/tests/"
_GROUP_ENV = "QTS_TEST_GROUP"
_SUBSET_ENV = "QTS_TEST_SUBSET"
_POLICY_ENV = "QTS_TEST_NETWORK_POLICY"

# Captured once, before any guard is installed, so nested guard installation
# (for example the network-policy self-test) still calls the real socket
# implementation rather than another guard.
_SOCKET_ORIGINALS: dict[str, object] = {
    "connect": socket.socket.connect,
    "connect_ex": socket.socket.connect_ex,
    "create_connection": socket.create_connection,
    "getaddrinfo": socket.getaddrinfo,
    "socketpair": socket.socketpair,
}


def _load_manifest() -> tuple[dict[str, str], dict[str, set[str]]]:
    payload = json.loads(_MANIFEST_PATH.read_text(encoding="utf-8"))
    groups = payload.get("groups")
    if not isinstance(groups, dict):
        raise pytest.UsageError(f"test manifest has no 'groups' object: {_MANIFEST_PATH}")
    mapping: dict[str, str] = {}
    for group, names in groups.items():
        if not isinstance(names, list):
            raise pytest.UsageError(f"test manifest group is not a list: {group}")
        for name in names:
            mapping[f"{_TEST_PREFIX}{name}"] = group
    raw_subsets = payload.get("ci_subsets") or {}
    if not isinstance(raw_subsets, dict):
        raise pytest.UsageError(
            f"test manifest has an invalid 'ci_subsets' object: {_MANIFEST_PATH}"
        )
    subsets: dict[str, set[str]] = {}
    for name, entries in raw_subsets.items():
        if not isinstance(entries, list):
            raise pytest.UsageError(f"test manifest subset is not a list: {name}")
        subsets[name] = {f"{_TEST_PREFIX}{entry}" for entry in entries}
    return mapping, subsets


def _selected_groups() -> set[str]:
    raw = os.getenv(_GROUP_ENV, "")
    return {part.strip() for part in raw.split(",") if part.strip()}


def _selected_subsets() -> set[str]:
    raw = os.getenv(_SUBSET_ENV, "")
    return {part.strip() for part in raw.split(",") if part.strip()}


def pytest_configure(config: pytest.Config) -> None:
    group_map, subsets = _load_manifest()
    config._qts_group_map = group_map  # type: ignore[attr-defined]
    config._qts_subsets = subsets  # type: ignore[attr-defined]
    known = set(group_map.values())
    unknown = sorted(_selected_groups() - known)
    if unknown:
        raise pytest.UsageError(f"unknown {_GROUP_ENV} value(s): {', '.join(unknown)}")
    unknown_subsets = sorted(_selected_subsets() - set(subsets))
    if unknown_subsets:
        raise pytest.UsageError(f"unknown {_SUBSET_ENV} value(s): {', '.join(unknown_subsets)}")
    policy = os.getenv(_POLICY_ENV, "loopback").strip().lower()
    if policy not in {"offline", "loopback", "open"}:
        raise pytest.UsageError(f"{_POLICY_ENV} must be offline, loopback or open; got {policy!r}")
    config._qts_network_policy = policy  # type: ignore[attr-defined]


def pytest_collection_modifyitems(config: pytest.Config, items: list[pytest.Item]) -> None:
    group_map: dict[str, str] = config._qts_group_map  # type: ignore[attr-defined]
    subsets: dict[str, set[str]] = config._qts_subsets  # type: ignore[attr-defined]
    selected = _selected_groups()
    selected_subsets = _selected_subsets()
    subset_files: set[str] | None = (
        set().union(*(subsets[name] for name in selected_subsets)) if selected_subsets else None
    )
    kept: list[pytest.Item] = []
    matched_subset = 0
    for item in items:
        item_path = Path(str(getattr(item, "path", None) or item.fspath)).resolve()
        try:
            key = item_path.relative_to(_ROOT).as_posix()
        except ValueError:
            kept.append(item)
            continue
        if not key.startswith(_TEST_PREFIX) or not item_path.name.startswith("test_"):
            kept.append(item)
            continue
        group = group_map.get(key)
        if group is None:
            raise pytest.UsageError(f"test file is not classified in ci/test_manifest.json: {key}")
        item.add_marker(getattr(pytest.mark, group))
        if selected and group not in selected:
            continue
        if subset_files is not None:
            if key not in subset_files:
                continue
            matched_subset += 1
        kept.append(item)
    items[:] = kept
    if selected_subsets and matched_subset == 0:
        raise pytest.UsageError(
            f"{_SUBSET_ENV} selected {sorted(selected_subsets)} but no classified test file matched"
        )

    if "postgres" in selected and os.getenv("REQUIRE_POSTGRES") == "1":
        if _REAL_ASYNCPG is None:
            raise pytest.UsageError("REQUIRE_POSTGRES=1 but asyncpg is not importable")
        from trader.adapters.persistence.postgres import is_postgres_available

        if not is_postgres_available():
            raise pytest.UsageError(
                "REQUIRE_POSTGRES=1 but no PostgreSQL DSN is configured "
                "(POSTGRES_CONNECTION_STRING or POSTGRES_HOST/POSTGRES_DB/"
                "POSTGRES_USER/POSTGRES_PASSWORD)"
            )
        dsn = os.getenv("POSTGRES_CONNECTION_STRING", "").strip()
        if not dsn:
            host = os.getenv("POSTGRES_HOST", "").strip()
            database = os.getenv("POSTGRES_DB", "").strip()
            user = os.getenv("POSTGRES_USER", "").strip()
            password = os.getenv("POSTGRES_PASSWORD", "")
            port = os.getenv("POSTGRES_PORT", "5432").strip() or "5432"
            dsn = f"postgresql://{user}:{password}@{host}:{port}/{database}"

        async def _probe() -> None:
            connection = await _REAL_ASYNCPG.connect(dsn, timeout=5)
            try:
                await connection.fetchval("SELECT 1")
            finally:
                await connection.close()

        try:
            asyncio.run(_probe())
        except Exception as exc:
            raise pytest.UsageError(
                "REQUIRE_POSTGRES=1 but PostgreSQL is unreachable or the DSN is "
                f"invalid: {type(exc).__name__}: {exc}"
            ) from exc


@pytest.hookimpl(hookwrapper=True)
def pytest_runtest_makereport(item: pytest.Item, call: pytest.CallInfo):
    outcome = yield
    report = outcome.get_result()
    if (
        report.skipped
        and os.getenv("REQUIRE_POSTGRES") == "1"
        and item.get_closest_marker("postgres") is not None
    ):
        report.outcome = "failed"
        report.longrepr = (
            "REQUIRE_POSTGRES=1: a Postgres test was skipped; a required PG job "
            f"must never turn a missing dependency into green: {item.nodeid}"
        )


def _loopback_host(host: object) -> bool:
    if host is None:
        return True
    if isinstance(host, bytes):
        host = host.decode("utf-8", "replace")
    text = str(host).strip().strip("[]").lower()
    return text in {"", "localhost", "127.0.0.1", "::1"} or text.startswith("127.")


def _install_network_guard(policy: str) -> dict[str, object]:
    """Restrict sockets for the test process according to the CI group policy.

    ``offline``  - P0/unit: no socket connect at all, including loopback.
    ``loopback`` - Postgres/local HTTP groups: loopback only, public blocked.
    ``open``     - explicit external/ops runs only: no restriction.
    """
    previous: dict[str, object] = {
        "connect": socket.socket.connect,
        "connect_ex": socket.socket.connect_ex,
        "create_connection": socket.create_connection,
        "getaddrinfo": socket.getaddrinfo,
        "socketpair": socket.socketpair,
    }
    if policy == "open":
        return previous

    def _unwrap(function: object) -> object:
        seen: set[int] = set()
        while getattr(function, "_qts_guard", False) and id(function) not in seen:
            seen.add(id(function))
            function = getattr(function, "_qts_underlying", function)
        return function

    originals = {key: _unwrap(value) for key, value in _SOCKET_ORIGINALS.items()}
    # CPython's socket.socketpair() creates a loopback listener on Windows.
    # That is the asyncio event-loop self-pipe, not test traffic, so it is the
    # only loopback connection allowed in the otherwise fully-offline mode.
    pair_state = {"active": False}

    def _allowed(host: object) -> bool:
        if pair_state["active"] and _loopback_host(host):
            return True
        if policy == "offline":
            return False
        return _loopback_host(host)

    def _host_of(address: object) -> object:
        if isinstance(address, tuple) and address:
            return address[0]
        return address

    def guarded_connect(self, address):  # type: ignore[no-untyped-def]
        host = _host_of(address)
        if not _allowed(host):
            raise PermissionError(
                f"QTS_NETWORK_BLOCKED ({policy}): outbound socket to {host!r} is "
                "forbidden for this test group; use a fake/transport double or "
                "move the test to the explicit external/ops group"
            )
        return originals["connect"](self, address)  # type: ignore[operator]

    def guarded_connect_ex(self, address):  # type: ignore[no-untyped-def]
        host = _host_of(address)
        if not _allowed(host):
            raise PermissionError(f"QTS_NETWORK_BLOCKED ({policy}): {host!r}")
        return originals["connect_ex"](self, address)  # type: ignore[operator]

    def guarded_create_connection(address, *args, **kwargs):  # type: ignore[no-untyped-def]
        host = _host_of(address)
        if not _allowed(host):
            raise PermissionError(f"QTS_NETWORK_BLOCKED ({policy}): {host!r}")
        return originals["create_connection"](address, *args, **kwargs)  # type: ignore[operator]

    def guarded_getaddrinfo(host, *args, **kwargs):  # type: ignore[no-untyped-def]
        if not _allowed(host):
            raise PermissionError(f"QTS_NETWORK_BLOCKED ({policy}): DNS {host!r}")
        return originals["getaddrinfo"](host, *args, **kwargs)  # type: ignore[operator]

    def guarded_socketpair(*args, **kwargs):  # type: ignore[no-untyped-def]
        pair_state["active"] = True
        try:
            return originals["socketpair"](*args, **kwargs)  # type: ignore[operator]
        finally:
            pair_state["active"] = False

    for name, guarded in (
        ("connect", guarded_connect),
        ("connect_ex", guarded_connect_ex),
        ("create_connection", guarded_create_connection),
        ("getaddrinfo", guarded_getaddrinfo),
        ("socketpair", guarded_socketpair),
    ):
        setattr(guarded, "_qts_guard", True)
        setattr(guarded, "_qts_underlying", originals[name])

    socket.socket.connect = guarded_connect  # type: ignore[method-assign]
    socket.socket.connect_ex = guarded_connect_ex  # type: ignore[method-assign]
    socket.create_connection = guarded_create_connection  # type: ignore[assignment]
    socket.getaddrinfo = guarded_getaddrinfo  # type: ignore[assignment]
    socket.socketpair = guarded_socketpair  # type: ignore[assignment]
    return previous


def pytest_sessionstart(session: pytest.Session) -> None:
    policy = getattr(session.config, "_qts_network_policy", "loopback")
    session.config._qts_network_originals = _install_network_guard(policy)  # type: ignore[attr-defined]


def pytest_sessionfinish(session: pytest.Session, exitstatus: int) -> None:
    originals = getattr(session.config, "_qts_network_originals", None)
    if not originals:
        return
    socket.socket.connect = originals["connect"]  # type: ignore[method-assign]
    socket.socket.connect_ex = originals["connect_ex"]  # type: ignore[method-assign]
    socket.create_connection = originals["create_connection"]  # type: ignore[assignment]
    socket.getaddrinfo = originals["getaddrinfo"]  # type: ignore[assignment]
    socket.socketpair = originals["socketpair"]  # type: ignore[assignment]
