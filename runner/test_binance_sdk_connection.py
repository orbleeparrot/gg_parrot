"""Run against actual minimum/latest SDKs with every HTTP transport mocked."""
import unittest
from unittest.mock import Mock, patch

from runner.test_macro_runner_single_instance import macro_runner
from runner import connection

try:
    from binance.client import Client
except ImportError:
    Client = None


@unittest.skipIf(Client is None, "optional Binance SDK is not installed in this test environment")
class ActualBinanceSDKTests(unittest.TestCase):
    def setUp(self):
        self.transport = patch("requests.sessions.Session.request", side_effect=AssertionError("HTTP is forbidden"))
        self.sockets = patch("socket.socket.connect", side_effect=AssertionError("socket I/O is forbidden"))
        self.transport.start()
        self.sockets.start()
        self.addCleanup(self.transport.stop)
        self.addCleanup(self.sockets.stop)

    def bot(self):
        return macro_runner.BotThread(
            {"exchange": "binance", "symbol": "BTCUSDT", "position_side": "long", "leverage": 1},
            {"exchanges": {"binance": {"api_key": "dummy-key", "api_secret": "dummy-secret"}}},
            "live", Mock(), Mock(), Mock(), Mock(),
        )

    def test_preflight_construction_never_sends_automatic_http_ping(self):
        with patch("requests.sessions.Session.request", side_effect=AssertionError("HTTP is forbidden")) as transport:
            broker = self.bot()._build_broker({"api_key": "dummy-key", "api_secret": "dummy-secret"}, preflight_only=True)
        transport.assert_not_called()
        self.assertEqual(broker.raw._get_request_kwargs("get", False)["timeout"], (3, 8))
        broker.raw.close_connection()

    def test_reused_preflight_restores_original_live_timeout_without_http(self):
        bot = self.bot()
        with patch("requests.sessions.Session.request", side_effect=AssertionError("HTTP is forbidden")) as transport:
            broker = bot._build_broker({"api_key": "dummy-key", "api_secret": "dummy-secret"}, preflight_only=True)
            bot._preflight = connection.PreflightResult(True, "확인", connection.preflight_fingerprint(bot.macro, bot.mode, bot.credentials), broker=broker)
            self.assertTrue(bot._connect())
        transport.assert_not_called()
        self.assertIsNone(broker.raw._requests_params)
        self.assertEqual(broker.raw._get_request_kwargs("get", False)["timeout"], Client.REQUEST_TIMEOUT)
        self.assertEqual(Client.REQUEST_TIMEOUT, 10)
        broker.raw.close_connection()

    def test_normal_client_construction_keeps_original_sdk_ping_behavior(self):
        with patch.object(Client, "ping", return_value={}) as ping, patch("requests.sessions.Session.request", side_effect=AssertionError("HTTP is forbidden")) as transport:
            broker = self.bot()._build_broker({"api_key": "dummy-key", "api_secret": "dummy-secret"})
        ping.assert_called_once_with()
        transport.assert_not_called()
        self.assertIsNone(broker.raw._requests_params)
        broker.raw.close_connection()

    def test_preflight_client_delegates_explicit_ping_after_construction(self):
        broker = self.bot()._build_broker({"api_key": "dummy-key", "api_secret": "dummy-secret"}, preflight_only=True)
        with patch.object(Client, "ping", return_value={"normal": True}) as ping:
            self.assertEqual(broker.raw.ping(), {"normal": True})
        ping.assert_called_once_with()
        broker.raw.close_connection()


if __name__ == "__main__":
    unittest.main()
