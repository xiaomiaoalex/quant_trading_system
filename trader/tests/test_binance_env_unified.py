"""
Test Binance Environment Unified Configuration (Task 16)
=====================================================

Tests:
1. BINANCE_ENV=demo → all URLs match demo config
2. BINANCE_ENV=testnet → all URLs match testnet config
3. No mixed environments (REST demo + WS testnet)
4. Invalid BINANCE_ENV falls back to demo
5. get_binance_env_config returns all required URL fields
"""

import os
import pytest

from trader.api.env_config import (
    get_binance_env,
    get_binance_env_config,
    is_valid_binance_env,
    BINANCE_ENV_DEMO,
    BINANCE_ENV_TESTNET,
    VALID_BINANCE_ENVS,
    BINANCE_ENV_URL_CONFIGS,
)
from trader.adapters.broker.binance_spot_demo_broker import (
    BinanceSpotDemoBrokerConfig,
)


class TestBinanceEnvConfig:
    """Tests for unified Binance environment configuration."""

    def setup_method(self):
        """Clear BINANCE_ENV before each test."""
        self._original_env = os.environ.get("BINANCE_ENV")
        if "BINANCE_ENV" in os.environ:
            del os.environ["BINANCE_ENV"]

    def teardown_method(self):
        """Restore original BINANCE_ENV."""
        if self._original_env is not None:
            os.environ["BINANCE_ENV"] = self._original_env
        elif "BINANCE_ENV" in os.environ:
            del os.environ["BINANCE_ENV"]

    def test_get_binance_env_default_is_demo(self):
        """Default BINANCE_ENV should be 'demo' when not set."""
        assert get_binance_env() == "demo"

    def test_get_binance_env_demo(self):
        """BINANCE_ENV=demo should return 'demo'."""
        os.environ["BINANCE_ENV"] = "demo"
        assert get_binance_env() == "demo"

    def test_get_binance_env_testnet(self):
        """BINANCE_ENV=testnet should return 'testnet'."""
        os.environ["BINANCE_ENV"] = "testnet"
        assert get_binance_env() == "testnet"

    def test_get_binance_env_invalid_falls_back_to_demo(self):
        """Invalid BINANCE_ENV should fall back to 'demo'."""
        os.environ["BINANCE_ENV"] = "invalid_env"
        assert get_binance_env() == "demo"

    def test_get_binance_env_uppercase_normalized(self):
        """BINANCE_ENV values should be normalized to lowercase."""
        os.environ["BINANCE_ENV"] = "DEMO"
        assert get_binance_env() == "demo"
        os.environ["BINANCE_ENV"] = "TESTNET"
        assert get_binance_env() == "testnet"

    def test_get_binance_env_config_demo(self):
        """get_binance_env_config for demo should return correct URLs."""
        os.environ["BINANCE_ENV"] = "demo"
        config = get_binance_env_config()
        
        assert config["env"] == "demo"
        assert config["rest_base"] == "https://demo-api.binance.com/api"
        assert config["public_ws_base"] == "wss://demo-stream.binance.com/ws"
        assert config["private_ws_base"] == "wss://demo-stream.binance.com/ws"
        assert config["listenkey_rest_base"] == "https://demo-api.binance.com/api"

    def test_get_binance_env_config_testnet(self):
        """get_binance_env_config for testnet should return correct URLs."""
        os.environ["BINANCE_ENV"] = "testnet"
        config = get_binance_env_config()
        
        assert config["env"] == "testnet"
        assert config["rest_base"] == "https://testnet.binance.vision/api"
        assert config["public_ws_base"] == "wss://stream.testnet.binance.vision/ws"
        assert config["private_ws_base"] == "wss://stream.testnet.binance.vision/ws"
        assert config["listenkey_rest_base"] == "https://testnet.binance.vision/api"

    def test_get_binance_env_config_contains_all_required_fields(self):
        """get_binance_env_config should return all required URL fields."""
        required_fields = {"env", "rest_base", "public_ws_base", "private_ws_base", "listenkey_rest_base"}
        
        os.environ["BINANCE_ENV"] = "demo"
        config = get_binance_env_config()
        assert required_fields.issubset(config.keys()), f"Missing fields: {required_fields - config.keys()}"

    def test_is_valid_binance_env(self):
        """is_valid_binance_env should correctly validate environments."""
        assert is_valid_binance_env("demo") is True
        assert is_valid_binance_env("testnet") is True
        assert is_valid_binance_env("DEMO") is True  # case insensitive
        assert is_valid_binance_env("TESTNET") is True
        assert is_valid_binance_env("invalid") is False
        assert is_valid_binance_env("production") is False
        assert is_valid_binance_env("") is False


class TestBinanceSpotDemoBrokerConfigForEnv:
    """Tests for BinanceSpotDemoBrokerConfig.for_env() factory method."""

    def setup_method(self):
        """Clear BINANCE_ENV before each test."""
        self._original_env = os.environ.get("BINANCE_ENV")
        if "BINANCE_ENV" in os.environ:
            del os.environ["BINANCE_ENV"]

    def teardown_method(self):
        """Restore original BINANCE_ENV."""
        if self._original_env is not None:
            os.environ["BINANCE_ENV"] = self._original_env
        elif "BINANCE_ENV" in os.environ:
            del os.environ["BINANCE_ENV"]

    def test_for_env_demo(self):
        """for_env('demo') should create demo config."""
        config = BinanceSpotDemoBrokerConfig.for_env(
            api_key="test_key",
            secret_key="test_secret",
            env="demo",
        )
        
        assert config.broker_name == "binance_spot_demo"
        assert config.base_url == "https://demo-api.binance.com/api"

    def test_for_env_testnet(self):
        """for_env('testnet') should create testnet config."""
        config = BinanceSpotDemoBrokerConfig.for_env(
            api_key="test_key",
            secret_key="test_secret",
            env="testnet",
        )
        
        assert config.broker_name == "binance_spot_testnet"
        assert config.base_url == "https://testnet.binance.vision/api"

    def test_for_env_invalid_raises(self):
        """for_env with invalid env should raise ValueError."""
        with pytest.raises(ValueError, match="Unsupported env"):
            BinanceSpotDemoBrokerConfig.for_env(
                api_key="test_key",
                secret_key="test_secret",
                env="invalid",
            )

    def test_for_env_case_insensitive(self):
        """for_env should be case insensitive."""
        config_lower = BinanceSpotDemoBrokerConfig.for_env(
            api_key="test_key",
            secret_key="test_secret",
            env="DEMO",
        )
        assert config_lower.broker_name == "binance_spot_demo"

        config_upper = BinanceSpotDemoBrokerConfig.for_env(
            api_key="test_key",
            secret_key="test_secret",
            env="TESTNET",
        )
        assert config_upper.broker_name == "binance_spot_testnet"

    def test_for_env_passes_through_parameters(self):
        """for_env should pass through timeout, recv_window, etc."""
        config = BinanceSpotDemoBrokerConfig.for_env(
            api_key="test_key",
            secret_key="test_secret",
            env="demo",
            timeout=30.0,
            max_retries=5,
            recv_window=10000,
            proxy_url="http://proxy:8080",
            verify_ssl=False,
        )
        
        assert config.timeout == 30.0
        assert config.max_retries == 5
        assert config.recv_window == 10000
        assert config.proxy_url == "http://proxy:8080"
        assert config.verify_ssl is False


class TestBinanceEnvConsistency:
    """Tests for REST/WS environment consistency."""

    def setup_method(self):
        """Clear BINANCE_ENV before each test."""
        self._original_env = os.environ.get("BINANCE_ENV")
        if "BINANCE_ENV" in os.environ:
            del os.environ["BINANCE_ENV"]

    def teardown_method(self):
        """Restore original BINANCE_ENV."""
        if self._original_env is not None:
            os.environ["BINANCE_ENV"] = self._original_env
        elif "BINANCE_ENV" in os.environ:
            del os.environ["BINANCE_ENV"]

    def test_demo_env_all_urls_consistent(self):
        """Demo environment should have consistent URLs across all endpoints."""
        os.environ["BINANCE_ENV"] = "demo"
        config = get_binance_env_config()
        
        # All REST URLs should be demo-api.binance.com
        assert config["rest_base"].startswith("https://demo-api.binance.com")
        assert config["listenkey_rest_base"].startswith("https://demo-api.binance.com")
        
        # All WS URLs should be demo-stream.binance.com
        assert config["public_ws_base"].startswith("wss://demo-stream.binance.com")
        assert config["private_ws_base"].startswith("wss://demo-stream.binance.com")

    def test_testnet_env_all_urls_consistent(self):
        """Testnet environment should have consistent URLs across all endpoints."""
        os.environ["BINANCE_ENV"] = "testnet"
        config = get_binance_env_config()
        
        # All REST URLs should be testnet.binance.vision
        assert config["rest_base"].startswith("https://testnet.binance.vision")
        assert config["listenkey_rest_base"].startswith("https://testnet.binance.vision")
        
        # All WS URLs should be stream.testnet.binance.vision
        assert config["public_ws_base"].startswith("wss://stream.testnet.binance.vision")
        assert config["private_ws_base"].startswith("wss://stream.testnet.binance.vision")

    def test_no_mixed_rest_ws_env(self):
        """REST and WS URLs should not be mixed across environments."""
        os.environ["BINANCE_ENV"] = "demo"
        demo_config = get_binance_env_config()
        
        # Public WS should not be testnet when REST is demo
        assert "testnet" not in demo_config["public_ws_base"]
        assert "testnet" not in demo_config["private_ws_base"]
        
        os.environ["BINANCE_ENV"] = "testnet"
        testnet_config = get_binance_env_config()
        
        # Public WS should not be demo when REST is testnet
        assert "demo" not in testnet_config["public_ws_base"]
        assert "demo" not in testnet_config["private_ws_base"]


class TestBinanceEnvURLConfigs:
    """Tests for the URL configuration constants."""

    def test_demo_urls_are_valid(self):
        """Demo URLs should be well-formed and use correct domain."""
        demo_urls = BINANCE_ENV_URL_CONFIGS[BINANCE_ENV_DEMO]
        
        assert demo_urls["rest_base"] == "https://demo-api.binance.com/api"
        assert demo_urls["public_ws_base"] == "wss://demo-stream.binance.com/ws"
        assert demo_urls["private_ws_base"] == "wss://demo-stream.binance.com/ws"
        assert demo_urls["listenkey_rest_base"] == "https://demo-api.binance.com/api"

    def test_testnet_urls_are_valid(self):
        """Testnet URLs should be well-formed and use correct domain."""
        testnet_urls = BINANCE_ENV_URL_CONFIGS[BINANCE_ENV_TESTNET]
        
        assert testnet_urls["rest_base"] == "https://testnet.binance.vision/api"
        assert testnet_urls["public_ws_base"] == "wss://stream.testnet.binance.vision/ws"
        assert testnet_urls["private_ws_base"] == "wss://stream.testnet.binance.vision/ws"
        assert testnet_urls["listenkey_rest_base"] == "https://testnet.binance.vision/api"

    def test_valid_envs_set(self):
        """VALID_BINANCE_ENVS should contain exactly demo and testnet."""
        assert VALID_BINANCE_ENVS == {"demo", "testnet"}
