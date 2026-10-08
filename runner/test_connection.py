"""Local onboarding contracts: no credentials leave the runner, no real orders."""
import unittest
from unittest.mock import Mock

from runner import connection


class PublicIPTests(unittest.TestCase):
    def test_detects_one_global_ipv4_with_a_bounded_request(self):
        http = Mock()
        http.get.return_value.status_code = 200
        http.get.return_value.json.return_value = {"ip": "8.8.8.8"}
        self.assertEqual(connection.detect_public_ipv4(http), "8.8.8.8")
        http.get.assert_called_once_with(
            "https://api4.ipify.org?format=json", timeout=(3, 5), allow_redirects=False,
        )

    def test_refuses_private_ipv6_and_malformed_addresses(self):
        for value in ("192.168.1.1", "127.0.0.1", "224.0.0.1", "239.255.255.250", "::1", "2606:4700::1111", "key=secret", ""):
            with self.subTest(value=value), self.assertRaises(connection.PublicIPError):
                connection.validate_public_ipv4(value)

    def test_failure_is_safe_and_not_retried(self):
        http = Mock()
        http.get.side_effect = RuntimeError("Authorization: SECRET")
        with self.assertRaises(connection.PublicIPError) as caught:
            connection.detect_public_ipv4(http)
        self.assertNotIn("SECRET", str(caught.exception))
        http.get.assert_called_once()

    def test_rejects_redirects_and_bad_json(self):
        for status in (301, 429, 500):
            http = Mock()
            http.get.return_value.status_code = status
            with self.assertRaises(connection.PublicIPError):
                connection.detect_public_ipv4(http)


class PreflightStateTests(unittest.TestCase):
    def fingerprint(self, macro=None, mode="live", key="key", secret="secret"):
        return connection.preflight_fingerprint(
            macro or {"exchange": "upbit", "symbol": "KRW-BTC", "params": {"initial_capital": 10000}},
            mode, {"exchanges": {"upbit": {"api_key": key, "api_secret": secret}}},
        )

    def test_setting_key_mode_and_notional_changes_invalidate_the_result(self):
        original = self.fingerprint()
        self.assertNotEqual(original, self.fingerprint(mode="mock"))
        self.assertNotEqual(original, self.fingerprint(key="other"))
        self.assertNotEqual(original, self.fingerprint(secret="other"))
        self.assertNotEqual(original, self.fingerprint(macro={"exchange": "upbit", "symbol": "KRW-BTC", "params": {"initial_capital": 20000}}))
        self.assertNotIn("secret", original)

    def test_other_exchange_keys_are_not_part_of_the_selected_check(self):
        macro = {"exchange": "upbit", "symbol": "KRW-BTC"}
        credentials = {"exchanges": {"upbit": {"api_key": "k", "api_secret": "s"}}}
        original = connection.preflight_fingerprint(macro, "live", credentials)
        credentials["exchanges"]["bithumb"] = {"api_key": "another", "api_secret": "other"}
        self.assertEqual(original, connection.preflight_fingerprint(macro, "live", credentials))

    def test_success_is_valid_only_for_the_current_snapshot_for_two_minutes(self):
        result = connection.PreflightResult(True, "확인", "fp", checked_at=100)
        self.assertTrue(result.matches("fp", now=219))
        self.assertFalse(result.matches("changed", now=101))
        self.assertFalse(result.matches("fp", now=220))
        self.assertFalse(result.matches("fp", now=99))
        self.assertFalse(connection.PreflightResult(False, "실패", "fp", checked_at=100).matches("fp", now=101))

    def test_error_redaction_never_displays_selected_keys(self):
        reason = "key ABC and secret XYZ failed"
        credentials = {"exchanges": {"upbit": {"api_key": "ABC", "api_secret": "XYZ"}}, "member_key": "MEMBER"}
        self.assertNotIn("ABC", connection.safe_preflight_reason(reason, credentials))
        self.assertNotIn("XYZ", connection.safe_preflight_reason(reason, credentials))
        self.assertNotIn("MEMBER", connection.safe_preflight_reason("MEMBER", credentials))

    def test_signed_urls_proxy_credentials_and_jwt_are_not_rendered(self):
        for reason in (
            "ProxyError https://proxy-user:proxy-password@example.test",
            "failure https://api.test?signature=SIGNED&timestamp=1",
            "Authorization: Bearer eyJtoken.payload.signature",
            "proxy-user:proxy-password@example.test refused",
        ):
            safe = connection.safe_preflight_reason(reason, {})
            for forbidden in ("proxy-user", "proxy-password", "SIGNED", "eyJtoken", "https://"):
                self.assertNotIn(forbidden, safe)


if __name__ == "__main__":
    unittest.main()
