"""Local onboarding contracts: no credentials leave the runner, no real orders."""
import unittest
from unittest.mock import Mock, patch

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


class PublicIPCacheTests(unittest.TestCase):
    def test_restarted_cache_compares_a_saved_observation_without_claiming_registration(self):
        cache = connection.PublicIPCache()
        self.assertTrue(cache.seed_previous({"public_ipv4": "8.8.8.8", "observed_at": 1710000000}))
        _kind, token = cache.begin(now=100)
        result = cache.finish(token, True, "1.1.1.1", now=101)
        self.assertTrue(result.changed)
        self.assertIn("이전", connection.public_ip_status(result, now=102))
        self.assertNotIn("등록 완료", connection.public_ip_status(result, now=102))

    def test_invalid_saved_observations_do_not_replace_the_process_baseline(self):
        cache = connection.PublicIPCache()
        cache.seed_previous({"public_ipv4": "8.8.8.8", "observed_at": 1710000000})
        for history in (None, {}, {"public_ipv4": "192.168.1.2", "observed_at": 1710000000},
                        {"public_ipv4": "1.1.1.1", "observed_at": float("nan")},
                        {"public_ipv4": "1.1.1.1", "observed_at": float("inf")},
                        {"public_ipv4": "1.1.1.1", "observed_at": "invalid"},
                        {"public_ipv4": "1.1.1.1", "observed_at": True},
                        {"public_ipv4": "1.1.1.1", "observed_at": 1e15},
                        {"public_ipv4": "1.1.1.1", "observed_at": 10**1000}):
            with self.subTest(history=history):
                self.assertFalse(cache.seed_previous(history))
        _kind, token = cache.begin(now=100)
        self.assertFalse(cache.finish(token, True, "8.8.8.8", now=101).changed)

    def test_duplicate_requests_share_one_inflight_token(self):
        cache = connection.PublicIPCache()
        first, token = cache.begin(now=100)
        second, shared = cache.begin(now=101)
        self.assertEqual((first, second), ("request", "shared"))
        self.assertEqual(token, shared)
        self.assertTrue(cache.busy)

    def test_success_is_reused_for_120_seconds_then_rechecked(self):
        cache = connection.PublicIPCache()
        _kind, token = cache.begin(now=100)
        result = cache.finish(token, True, "8.8.8.8", now=100)
        self.assertTrue(result.ok)
        self.assertEqual(cache.begin(now=219)[0], "cached")
        self.assertEqual(cache.begin(now=220)[0], "request")
        self.assertIsNone(cache.result)

    def test_failure_is_cached_without_retaining_a_previous_copyable_ip(self):
        cache = connection.PublicIPCache()
        _kind, token = cache.begin(now=100)
        cache.finish(token, True, "8.8.8.8", now=100)
        _kind, token = cache.begin(force=True, now=110)
        self.assertIsNone(cache.result)
        result = cache.finish(token, False, "ProxyError secret", now=111)
        self.assertFalse(result.ok)
        self.assertEqual(result.address, "")
        self.assertNotIn("secret", result.reason)
        self.assertEqual(cache.begin(now=112)[0], "cached")
        self.assertEqual(cache.begin(force=True, now=113)[0], "request")

    def test_stale_exchange_generation_response_is_discarded(self):
        cache = connection.PublicIPCache()
        _kind, token = cache.begin(now=100)
        cache.invalidate()
        self.assertEqual(cache.begin(now=101)[0], "shared")
        self.assertIsNone(cache.finish(token, True, "8.8.8.8", now=102))
        self.assertIsNone(cache.result)
        self.assertFalse(cache.busy)

    def test_old_duplicate_response_cannot_overwrite_a_new_request(self):
        cache = connection.PublicIPCache()
        _kind, old = cache.begin(now=100)
        cache.finish(old, True, "8.8.8.8", now=101)
        _kind, current = cache.begin(force=True, now=102)
        self.assertIsNone(cache.finish(old, True, "1.1.1.1", now=103))
        self.assertTrue(cache.busy)
        self.assertEqual(cache.finish(current, True, "9.9.9.9", now=104).address, "9.9.9.9")

    def test_changed_ip_compares_observations_without_claiming_registration(self):
        cache = connection.PublicIPCache()
        _kind, token = cache.begin(now=100)
        self.assertFalse(cache.finish(token, True, "8.8.8.8", now=100).changed)
        cache.invalidate()
        _kind, token = cache.begin(now=101)
        result = cache.finish(token, True, "1.1.1.1", now=102)
        self.assertTrue(result.changed)
        self.assertEqual(result.address, "1.1.1.1")
        self.assertNotIn("등록 완료", connection.public_ip_status(result, now=102))
        self.assertIn("달라", connection.public_ip_status(result, now=102))

    def test_cached_expiry_and_routing_caveats_are_truthful(self):
        result = connection.PublicIPResult(True, "8.8.8.8", "확인", checked_at=100)
        self.assertIn("조회 당시", connection.public_ip_status(result, now=101))
        self.assertIn("등록", connection.PUBLIC_IP_CAVEAT)
        self.assertIn("VPN", connection.PUBLIC_IP_CAVEAT)
        self.assertIn("프록시", connection.PUBLIC_IP_CAVEAT)
        self.assertIn("다를", connection.PUBLIC_IP_CAVEAT)
        self.assertIn("만료", connection.public_ip_status(result, now=220))

    def test_official_api_management_pages_are_a_static_domestic_allowlist(self):
        self.assertEqual(connection.api_management_url("upbit"), "https://www.upbit.com/mypage/open_api_management")
        self.assertEqual(connection.api_management_url("bithumb"), "https://www.bithumb.com/react/api-support/management-api")
        for exchange in ("binance", "https://evil.test", "upbit?key=secret"):
            with self.assertRaises(ValueError):
                connection.api_management_url(exchange)


class ConnectionHistoryTests(unittest.TestCase):
    def setUp(self):
        self.macro = {"exchange": "upbit", "symbol": "KRW-BTC"}
        self.saved = {"version": 2, "member_key": "fixture-member", "exchanges": {
            "upbit": {"api_key": "fixture-key", "api_secret": "fixture-secret"},
            "bithumb": {"api_key": "other-key", "api_secret": "other-secret"},
        }, "connection_history": {"bithumb": {"public_ipv4": "9.9.9.9", "observed_at": 1700000000}}}
        self.ip = connection.PublicIPResult(True, "8.8.8.8", "조회", checked_at=100, observed_at=1710000000)
        self.check = connection.PreflightResult(True, "검사", connection.preflight_fingerprint(self.macro, "live", self.saved), checked_at=100)

    def updated(self, *, saved=None, current=None, ip=None, check=None, mode="live"):
        with patch.object(connection.time, "monotonic", return_value=101):
            return connection.with_successful_ip_history(
                self.saved if saved is None else saved, self.macro, mode,
                self.saved if current is None else current, self.ip if ip is None else ip,
                self.check if check is None else check,
            )

    def test_success_adds_only_an_observation_preserving_all_saved_keys_and_other_history(self):
        updated = self.updated()
        self.assertEqual(updated["exchanges"], self.saved["exchanges"])
        self.assertEqual(updated["member_key"], "fixture-member")
        self.assertEqual(updated["connection_history"]["bithumb"], self.saved["connection_history"]["bithumb"])
        self.assertEqual(updated["connection_history"]["upbit"], {"public_ipv4": "8.8.8.8", "observed_at": 1710000000})
        self.assertNotIn("upbit", self.saved["connection_history"])
        self.assertNotIn("registered", updated["connection_history"]["upbit"])

    def test_unsaved_edited_mock_failed_or_stale_checks_cannot_create_history(self):
        cases = [
            {"saved": {}}, {"current": {"exchanges": {"upbit": {"api_key": "new", "api_secret": "new"}}}},
            {"mode": "mock"},
            {"check": connection.PreflightResult(False, "실패", self.check.fingerprint, checked_at=100)},
            {"check": connection.PreflightResult(True, "이전", self.check.fingerprint, checked_at=-100)},
            {"ip": connection.PublicIPResult(True, "8.8.8.8", "이전", checked_at=-100)},
            {"ip": connection.PublicIPResult(False, "", "실패", checked_at=100)},
            {"ip": connection.PublicIPResult(True, "192.168.1.2", "private", checked_at=100)},
            {"ip": connection.PublicIPResult(True, "8.8.8.8", "invalid", checked_at=100, observed_at=float("nan"))},
        ]
        for case in cases:
            with self.subTest(case=case):
                self.assertIsNone(self.updated(**case))

    def test_absent_ip_or_preflight_does_not_persist_an_assumed_success(self):
        self.assertIsNone(connection.with_successful_ip_history(self.saved, self.macro, "live", self.saved, None, self.check))
        self.assertIsNone(connection.with_successful_ip_history(self.saved, self.macro, "live", self.saved, self.ip, None))


if __name__ == "__main__":
    unittest.main()
