from trader.api.env_config import (
    get_binance_recv_window,
    get_reconciler_exchange_client_order_prefixes,
)


def test_get_binance_recv_window_default_when_missing() -> None:
    assert get_binance_recv_window(env={}) == 5000


def test_get_binance_recv_window_uses_valid_value() -> None:
    assert get_binance_recv_window(env={"BINANCE_RECV_WINDOW": "15000"}) == 15000


def test_get_binance_recv_window_falls_back_on_invalid() -> None:
    assert get_binance_recv_window(env={"BINANCE_RECV_WINDOW": "abc"}) == 5000
    assert get_binance_recv_window(env={"BINANCE_RECV_WINDOW": "-1"}) == 5000


def test_get_binance_recv_window_clamps_too_large_value() -> None:
    assert get_binance_recv_window(env={"BINANCE_RECV_WINDOW": "70000"}) == 60000


def test_get_reconciler_exchange_client_order_prefixes_default_empty() -> None:
    assert get_reconciler_exchange_client_order_prefixes(env={}) == []


def test_get_reconciler_exchange_client_order_prefixes_parses_csv() -> None:
    assert get_reconciler_exchange_client_order_prefixes(
        env={"RECONCILER_EXCHANGE_CLIENT_ORDER_PREFIXES": " fire_test_ , mybot_ "}
    ) == ["fire_test_", "mybot_"]


def test_get_reconciler_exchange_client_order_prefixes_deduplicates_and_ignores_empty() -> None:
    assert get_reconciler_exchange_client_order_prefixes(
        env={"RECONCILER_EXCHANGE_CLIENT_ORDER_PREFIXES": "fire_,,fire_,other_"}
    ) == ["fire_", "other_"]
