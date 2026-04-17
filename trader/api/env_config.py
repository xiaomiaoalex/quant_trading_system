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

