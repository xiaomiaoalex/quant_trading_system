from unittest.mock import MagicMock

from trader.api.routes import strategies


def test_set_pre_trade_risk_check_late_binds_existing_oms_handler() -> None:
    strategies.reset_strategy_route_state_for_tests()
    handler = MagicMock()
    check = MagicMock()
    strategies._oms_handler_instance = handler

    strategies.set_pre_trade_risk_check(check)

    assert strategies._pre_trade_risk_check is check
    handler.set_pre_trade_risk_check.assert_called_once_with(check)


def test_get_oms_broker_returns_existing_broker_without_creating_new_one() -> None:
    strategies.reset_strategy_route_state_for_tests()
    broker = MagicMock()
    strategies._broker_instance = broker

    assert strategies.get_oms_broker() is broker
