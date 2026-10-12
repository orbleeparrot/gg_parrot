"""연결 도우미가 보이는 것 — 검사 세 줄(키 인증 · 허용 IP · 주문 권한), 클립보드 키, 거래소 바로가기."""
import unittest
from unittest.mock import Mock

from runner import brokers, connection
from runner.test_brokers_domestic import FakeResponse


def exchange_error(name, message="fixture", status=401):
    return FakeResponse({"error": {"name": name, "message": message}}, status=status)


class CheckViewTests(unittest.TestCase):
    def test_success_marks_every_row_but_order_permission_only_where_it_can_be_proven(self):
        upbit = connection.check_view("upbit", True)
        self.assertEqual((upbit["auth"], upbit["ip"], upbit["order"]), ("ok", "ok", "ok"))
        for exchange in ("bithumb", "binance"):
            view = connection.check_view(exchange, True)
            # 빗썸 · 바이낸스는 주문 없이 주문 권한을 확인할 길이 없다 — '확인 불가' 로 둔다.
            self.assertEqual((view["auth"], view["ip"], view["order"]), ("ok", "ok", "na"))
            self.assertTrue(view["connected"])
            self.assertEqual(view["headline"], "연결 완료")

    def test_each_exchange_error_points_at_the_row_that_blocked(self):
        cases = {
            "no_authorization_ip": ("ok", "bad", "wait", "ip", "허용 IP 확인 필요"),
            "NotAllowIP": ("ok", "bad", "wait", "ip", "허용 IP 확인 필요"),
            "invalid_access_key": ("bad", "wait", "wait", "auth", "키 확인 필요"),
            "jwt_verification": ("bad", "wait", "wait", "auth", "키 확인 필요"),
            "expired_access_key": ("bad", "wait", "wait", "auth", "키 확인 필요"),
            "out_of_scope": ("ok", "ok", "bad", "order", "주문 권한 확인 필요"),
            "network": ("wait", "wait", "wait", "network", "연결 확인 필요"),
            "something_new": ("wait", "wait", "wait", "other", "연결 확인 필요"),
        }
        for name, (auth, ip, order, problem, headline) in cases.items():
            with self.subTest(name=name):
                view = connection.check_view("upbit", False, name)
                self.assertEqual((view["auth"], view["ip"], view["order"]), (auth, ip, order))
                self.assertEqual((view["problem"], view["headline"]), (problem, headline))
                self.assertFalse(view["connected"])

    def test_order_size_failures_still_mean_the_keys_are_connected(self):
        for name in ("insufficient_funds_bid", "under_min_total", "under_min_total_market_bid"):
            with self.subTest(name=name):
                view = connection.check_view("upbit", False, name)
                self.assertTrue(view["connected"])
                self.assertEqual(view["problem"], "order_size")

    def test_broker_rehearsal_leaves_the_exchange_word_for_the_view(self):
        for exchange, name, row in (("upbit", "no_authorization_ip", "ip"), ("bithumb", "NotAllowIP", "ip"),
                                    ("upbit", "invalid_access_key", "auth"), ("upbit", "out_of_scope", "order")):
            with self.subTest(exchange=exchange, name=name):
                session = Mock()
                session.post.return_value = exchange_error(name)
                session.get.return_value = exchange_error(name)
                broker = brokers.DomesticBroker("acc", "sec", exchange=exchange, symbol="KRW-BTC", log=Mock(),
                                                session=session)
                ok, _reason = broker.rehearse(notional=10_000.0)
                self.assertFalse(ok)
                self.assertEqual(broker.last_error_name, name)
                self.assertEqual(connection.check_view(exchange, ok, broker.last_error_name)["problem"], row)

    def test_network_failure_and_small_order_have_their_own_names(self):
        session = Mock()
        session.post.side_effect = OSError("connect timeout https://user:pass@proxy")
        broker = brokers.DomesticBroker("acc", "sec", exchange="upbit", symbol="KRW-BTC", log=Mock(), session=session)
        self.assertFalse(broker.rehearse(notional=10_000.0)[0])
        self.assertEqual(broker.last_error_name, "network")
        session = Mock()
        session.get.side_effect = [FakeResponse([{"currency": "KRW", "balance": "1"}]),
                                   FakeResponse({"bid_fee": "0.0025", "ask_fee": "0.0025", "bid": {"min_total": "5000"}})]
        broker = brokers.DomesticBroker("acc", "sec", exchange="bithumb", symbol="KRW-BTC", log=Mock(), session=session)
        ok, _reason = broker.rehearse(notional=1_000.0)
        self.assertFalse(ok)
        self.assertEqual(broker.last_error_name, "under_min_total")
        self.assertTrue(connection.check_view("bithumb", ok, broker.last_error_name)["connected"])

    def test_a_successful_rehearsal_clears_an_old_error_word(self):
        session = Mock()
        session.post.return_value = exchange_error("no_authorization_ip")
        broker = brokers.DomesticBroker("acc", "sec", exchange="upbit", symbol="KRW-BTC", log=Mock(), session=session)
        broker.rehearse(notional=10_000.0)
        session.post.return_value = FakeResponse({"uuid": "t"}, status=201)
        self.assertTrue(broker.rehearse(notional=10_000.0)[0])
        self.assertEqual(broker.last_error_name, "")


class ClipboardKeyTests(unittest.TestCase):
    def test_exchange_keys_are_accepted_as_copied(self):
        for value in ("a8Kq3Zt0" + "x" * 32, "6b2c78fd" * 4, "ZDkyNGRkYWY0NTg2NDk0ZDk5YzE0NDRjYzE4YjJhZDc=", "  " + "K" * 40 + "\n"):
            with self.subTest(value=value):
                self.assertEqual(connection.clipboard_key(value), value.strip())

    def test_addresses_urls_sentences_and_short_values_are_ignored(self):
        for value in ("121.134.55.20", "https://upbit.com/mypage", "API Key 복사", "short", "", None, "a b" * 20, "가" * 30):
            with self.subTest(value=value):
                self.assertEqual(connection.clipboard_key(value), "")


class ExchangePageTests(unittest.TestCase):
    def test_shortcuts_are_a_static_official_allowlist(self):
        self.assertEqual(connection.exchange_home_url("upbit"), "https://upbit.com/")
        self.assertEqual(connection.exchange_home_url("bithumb"), "https://www.bithumb.com/")
        for bad in ("binance", "https://evil.example", None):
            with self.subTest(bad=bad), self.assertRaises(ValueError):
                connection.exchange_home_url(bad)


if __name__ == "__main__":
    unittest.main()
