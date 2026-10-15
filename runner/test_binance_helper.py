"""바이낸스 연결 도우미(v19) — 실행 환경 · 키 검사 판정 · 실거래 키 권한 조회 · 단계 흐름."""
import unittest
from unittest.mock import Mock

from runner import brokers, connection


class _ApiError(Exception):
    def __init__(self, code):
        super().__init__(f"APIError(code={code})")
        self.code = code


SPOT_INFO = {"baseAsset": "BTC", "filters": [{"filterType": "LOT_SIZE", "stepSize": "0.001"},
                                             {"filterType": "MIN_NOTIONAL", "minNotional": "5"}]}


class BinanceEnvTests(unittest.TestCase):
    def test_env_follows_mode_and_market(self):
        self.assertEqual(connection.binance_env("testnet", "spot"), "testnet")
        self.assertEqual(connection.binance_env("mock", "spot"), "testnet")
        self.assertEqual(connection.binance_env("testnet", "futures"), "demo")
        self.assertEqual(connection.binance_env("live", "futures"), "live")

    def test_each_env_opens_its_own_key_page(self):
        self.assertEqual(connection.api_management_url("binance", "testnet"), "https://testnet.binance.vision/")
        self.assertIn("demo.binance.com", connection.api_management_url("binance", "demo"))
        self.assertIn("www.binance.com", connection.api_management_url("binance", "live"))
        self.assertIn("upbit.com", connection.api_management_url("upbit"))
        with self.assertRaises(ValueError):
            connection.api_management_url("binance", "")


class BinanceCheckViewTests(unittest.TestCase):
    def test_live_permissions_fill_all_three_rows(self):
        view = connection.check_view("binance", True, "binance_verified")
        self.assertEqual((view["auth"], view["ip"], view["order"], view["connected"]), ("ok", "ok", "ok", True))

    def test_practice_has_no_ip_row(self):
        view = connection.check_view("binance", True, "binance_test_ok")
        self.assertEqual((view["ip"], view["notes"]["ip"]), ("na", "필요 없음"))
        bad = connection.check_view("binance", False, "binance_access_test")
        self.assertEqual((bad["auth"], bad["problem"]), ("bad", "auth"))

    def test_live_access_error_points_to_the_ip(self):
        view = connection.check_view("binance", False, "binance_access")
        self.assertEqual((view["ip"], view["problem"], view["headline"]), ("bad", "ip", "허용 IP 확인 필요"))

    def test_unrestricted_key_without_trading_asks_for_ip(self):
        view = connection.check_view("binance", False, "binance_ip_unrestricted")
        self.assertEqual((view["auth"], view["ip"], view["order"], view["problem"]), ("ok", "bad", "bad", "ip"))

    def test_trading_switch_off_points_to_the_order_row(self):
        view = connection.check_view("binance", False, "binance_no_futures")
        self.assertEqual((view["order"], view["problem"]), ("bad", "order"))
        self.assertIn("Enable Futures", view["notes"]["order"])

    def test_problem_values_are_ones_the_server_accepts(self):
        from runner import setup_progress
        for name in ("binance_key", "binance_access", "binance_access_test", "binance_ip_unrestricted",
                     "binance_no_trade", "binance_no_futures", "binance_verified", "binance_verified_open", "binance_test_ok"):
            view = connection.check_view("binance", name.endswith(("_ok", "_verified", "_open")), name)
            self.assertIn(view["problem"], setup_progress.PROBLEMS, name)


class BinanceRehearseTests(unittest.TestCase):
    def broker(self, *, testnet, market="spot"):
        self.raw = Mock()
        self.raw.get_account.return_value = {"balances": [{"asset": "USDT", "free": "100"}]}
        self.raw.get_symbol_info.return_value = SPOT_INFO
        return brokers.BinanceBroker(self.raw, market=market, symbol="BTCUSDT", side="long", testnet=testnet, log=Mock())

    def test_practice_success_is_named_without_asking_permissions(self):
        broker = self.broker(testnet=True)
        ok, _ = broker.rehearse(notional=50)
        self.assertTrue(ok)
        self.assertEqual(broker.last_error_name, "binance_test_ok")
        self.raw.get_account_api_permissions.assert_not_called()  # 테스트넷 · 데모에는 그 조회가 없다

    def test_bad_signature_and_access_codes_are_split(self):
        broker = self.broker(testnet=False)
        self.raw.get_account.side_effect = _ApiError(-1022)
        self.assertFalse(broker.rehearse(notional=50)[0])
        self.assertEqual(broker.last_error_name, "binance_key")
        self.raw.get_account.side_effect = _ApiError(-2015)
        broker.rehearse(notional=50)
        self.assertEqual(broker.last_error_name, "binance_access")
        practice = self.broker(testnet=True)
        self.raw.get_account.side_effect = _ApiError(-2015)
        practice.rehearse(notional=50)
        self.assertEqual(practice.last_error_name, "binance_access_test")

    def test_live_reads_the_key_permissions(self):
        broker = self.broker(testnet=False)
        self.raw.get_account_api_permissions.return_value = {"ipRestrict": True, "enableSpotAndMarginTrading": True}
        self.assertTrue(broker.rehearse(notional=50)[0])
        self.assertEqual(broker.last_error_name, "binance_verified")
        self.raw.get_account_api_permissions.return_value = {"ipRestrict": False, "enableSpotAndMarginTrading": False}
        self.assertFalse(broker.rehearse(notional=50)[0])
        self.assertEqual(broker.last_error_name, "binance_ip_unrestricted")
        self.raw.get_account_api_permissions.return_value = {"ipRestrict": True, "enableSpotAndMarginTrading": False}
        self.assertFalse(broker.rehearse(notional=50)[0])
        self.assertEqual(broker.last_error_name, "binance_no_trade")

    def test_trading_allowed_without_ip_restriction_passes(self):
        # Default Security Controls 를 끈 계정은 IP 제한 없이도 주문 권한을 켤 수 있다 — 막지 않는다.
        broker = self.broker(testnet=False)
        self.raw.get_account_api_permissions.return_value = {"ipRestrict": False, "enableSpotAndMarginTrading": True}
        self.assertTrue(broker.rehearse(notional=50)[0])
        self.assertEqual(broker.last_error_name, "binance_verified_open")

    def test_futures_checks_the_futures_switch(self):
        broker = self.broker(testnet=False, market="futures")
        self.raw.futures_exchange_info.return_value = {"symbols": [{"symbol": "BTCUSDT", **SPOT_INFO}]}
        self.raw.get_account_api_permissions.return_value = {"ipRestrict": True, "enableSpotAndMarginTrading": True,
                                                             "enableFutures": False}
        self.assertFalse(broker.rehearse(notional=50)[0])
        self.assertEqual(broker.last_error_name, "binance_no_futures")

    def test_permission_lookup_failure_does_not_block(self):
        broker = self.broker(testnet=False)
        self.raw.get_account_api_permissions.side_effect = RuntimeError("old sdk")
        ok, reason = broker.rehearse(notional=50)
        self.assertTrue(ok)
        self.assertIn("첫 주문", reason)


class ProgressEnvTests(unittest.TestCase):
    def test_env_is_sent_only_for_binance_and_only_when_set(self):
        from runner import setup_progress
        domestic = setup_progress.progress_payload("keys", "upbit")
        self.assertNotIn("env", domestic)  # env 를 모르는 옛 서버(extra=forbid)에도 그대로 통한다
        self.assertEqual(setup_progress.progress_payload("perm", "binance", env="demo")["env"], "demo")
        with self.assertRaises(ValueError):
            setup_progress.progress_payload("perm", "upbit", env="live")
        with self.assertRaises(ValueError):
            setup_progress.progress_payload("perm", "binance", env="mainnet")


if __name__ == "__main__":
    unittest.main()
