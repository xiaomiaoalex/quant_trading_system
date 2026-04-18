"""
API environment configuration helpers.
"""

from __future__ import annotations

import logging
import os
from typing import Mapping

logger = logging.getLogger(__name__)

BINANCE_RECV_WINDOW_ENV = "BINANCE_RECV_WINDOW"
BINANCE_RECV_WINDOW_DEFAULT = 5000
BINANCE_RECV_WINDOW_MIN = 1
BINANCE_RECV_WINDOW_MAX = 60000
RECONCILER_EXCHANGE_CLIENT_ORDER_PREFIXES_ENV = "RECONCILER_EXCHANGE_CLIENT_ORDER_PREFIXES"
SYSTEM_ORDER_NAMESPACE_PREFIX_ENV = "SYSTEM_ORDER_NAMESPACE_PREFIX"
SYSTEM_ORDER_NAMESPACE_PREFIX_DEFAULT = "QTS1_"


def get_binance_recv_window(env: Mapping[str, str] | None = None) -> int:
    source = env if env is not None else os.environ
    raw = source.get(BINANCE_RECV_WINDOW_ENV)

    if raw is None or str(raw).strip() == "":
        return BINANCE_RECV_WINDOW_DEFAULT

    try:
        value = int(str(raw).strip())
    except (TypeError, ValueError):
        logger.warning(
            "[Binance] invalid %s=%r, fallback to default=%s",
            BINANCE_RECV_WINDOW_ENV,
            raw,
            BINANCE_RECV_WINDOW_DEFAULT,
        )
        return BINANCE_RECV_WINDOW_DEFAULT

    if value < BINANCE_RECV_WINDOW_MIN:
        logger.warning(
            "[Binance] %s=%s is below min=%s, fallback to default=%s",
            BINANCE_RECV_WINDOW_ENV,
            value,
            BINANCE_RECV_WINDOW_MIN,
            BINANCE_RECV_WINDOW_DEFAULT,
        )
        return BINANCE_RECV_WINDOW_DEFAULT

    if value > BINANCE_RECV_WINDOW_MAX:
        logger.warning(
            "[Binance] %s=%s is above max=%s, clamped to %s",
            BINANCE_RECV_WINDOW_ENV,
            value,
            BINANCE_RECV_WINDOW_MAX,
            BINANCE_RECV_WINDOW_MAX,
        )
        return BINANCE_RECV_WINDOW_MAX

    return value


def get_reconciler_exchange_client_order_prefixes(
    env: Mapping[str, str] | None = None,
) -> list[str]:
    """
    解析 Reconciler 交易所订单前缀过滤配置。

    环境变量:
    - RECONCILER_EXCHANGE_CLIENT_ORDER_PREFIXES
      逗号分隔前缀列表（如 "fire_test_,mybot_"）
    """
    source = env if env is not None else os.environ
    raw = source.get(RECONCILER_EXCHANGE_CLIENT_ORDER_PREFIXES_ENV)
    if raw is None or str(raw).strip() == "":
        return []

    seen: set[str] = set()
    prefixes: list[str] = []
    for item in str(raw).split(","):
        prefix = item.strip()
        if not prefix:
            continue
        if prefix in seen:
            continue
        seen.add(prefix)
        prefixes.append(prefix)
    return prefixes


def get_system_order_namespace_prefix(
    env: Mapping[str, str] | None = None,
) -> str:
    """
    解析系统订单命名空间前缀配置。

    环境变量:
    - SYSTEM_ORDER_NAMESPACE_PREFIX
      系统级订单前缀（如 "QTS1_"），用于快速识别本系统订单
    """
    source = env if env is not None else os.environ
    raw = source.get(SYSTEM_ORDER_NAMESPACE_PREFIX_ENV)
    if raw is None or str(raw).strip() == "":
        return SYSTEM_ORDER_NAMESPACE_PREFIX_DEFAULT

    prefix = str(raw).strip()
    if not prefix:
        return SYSTEM_ORDER_NAMESPACE_PREFIX_DEFAULT

    return prefix
