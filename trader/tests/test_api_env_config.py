from trader.api.env_config import get_binance_recv_window


def test_get_binance_recv_window_default_when_missing() -> None:
    assert get_binance_recv_window(env={}) == 5000


def test_get_binance_recv_window_uses_valid_value() -> None:
    assert get_binance_recv_window(env={"BINANCE_RECV_WINDOW": "15000"}) == 15000


def test_get_binance_recv_window_falls_back_on_invalid() -> None:
    assert get_binance_recv_window(env={"BINANCE_RECV_WINDOW": "abc"}) == 5000
    assert get_binance_recv_window(env={"BINANCE_RECV_WINDOW": "-1"}) == 5000


def test_get_binance_recv_window_clamps_too_large_value() -> None:
    assert get_binance_recv_window(env={"BINANCE_RECV_WINDOW": "70000"}) == 60000

