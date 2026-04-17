"""Built-in strategy plugins."""

from trader.strategies.dca_btc import DcaBtcStrategy, get_plugin as get_dca_btc_plugin
from trader.strategies.ema_cross_btc import (
    EmaCrossBtcStrategy,
    get_plugin as get_ema_cross_btc_plugin,
)
from trader.strategies.fire_test import FireTestStrategy, get_plugin as get_fire_test_plugin
from trader.strategies.rsi_grid import RsiGridStrategy, get_plugin as get_rsi_grid_plugin

__all__ = [
    "EmaCrossBtcStrategy",
    "RsiGridStrategy",
    "DcaBtcStrategy",
    "FireTestStrategy",
    "get_ema_cross_btc_plugin",
    "get_rsi_grid_plugin",
    "get_dca_btc_plugin",
    "get_fire_test_plugin",
]
