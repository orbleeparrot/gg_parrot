import base64
import hashlib
import hmac
import json
import unittest
from unittest.mock import Mock, patch

from runner import brokers


class FakeResponse:
    def __init__(self, payload, status=200):
        self._payload = payload
        self.status_code = status

    def json(self):
        return self._payload

    def raise_for_status(self):
        if self.status_code >= 400:
            raise RuntimeError(f"HTTP {self.status_code}")


def decode_jwt(token):
    def part(raw):
        raw += "=" * (-len(raw) % 4)
        return json.loads(base64.urlsafe_b64decode(raw))

    header, payload, _signature = token.split(".")
    return part(header), part(payload)


FILLED = {"state": "done", "executed_volume": "0.01",
          "trades": [{"price": "100000000", "volume": "0.01", "funds": "1000000"}]}


class DomesticBrokerTests(unittest.TestCase):
    def broker(self, exchange="upbit"):
        self.session = Mock()
        return brokers.DomesticBroker("acc", "sec", exchange=exchange,
                                      symbol="KRW-BTC", log=Mock(), session=self.session)

    def test_market_buy_sends_krw_total_not_volume(self):
        broker = self.broker()
        self.session.post.return_value = FakeResponse({"uuid": "u1", "state": "wait"})
        self.session.get.return_value = FakeResponse(
            {"state": "done", "executed_volume": "0.01",
             "trades": [{"price": "100000000", "volume": "0.01", "funds": "1000000"}]})
        broker.submit("BUY", notional=1_000_000.0, client_id="ggp-1")
        body = self.session.post.call_args.kwargs["json"]
        self.assertEqual(body["side"], "bid")
        self.assertEqual(body["ord_type"], "price")
        self.assertEqual(float(body["price"]), 1_000_000.0)
        self.assertNotIn("volume", body)

    def test_market_sell_sends_volume_not_price(self):
        broker = self.broker()
        self.session.post.return_value = FakeResponse({"uuid": "u2", "state": "wait"})
        self.session.get.return_value = FakeResponse(
            {"state": "done", "executed_volume": "0.01",
             "trades": [{"price": "100000000", "volume": "0.01", "funds": "1000000"}]})
        broker.submit("SELL", base_qty=0.01, client_id="ggp-2")
        body = self.session.post.call_args.kwargs["json"]
        self.assertEqual(body["side"], "ask")
        self.assertEqual(body["ord_type"], "market")
        self.assertEqual(float(body["volume"]), 0.01)
        self.assertNotIn("price", body)

    def test_timeout_reconciles_by_identifier_without_a_second_order(self):
        broker = self.broker()
        self.session.post.side_effect = TimeoutError()
        self.session.get.return_value = FakeResponse(
            {"state": "done", "executed_volume": "0.01",
             "trades": [{"price": "100000000", "volume": "0.01", "funds": "1000000"}]})
        order = broker.submit("BUY", notional=1_000_000.0, client_id="ggp-3")
        self.assertEqual(order.status, "FILLED")
        self.assertEqual(self.session.post.call_count, 1)
        self.assertEqual(self.session.get.call_args.kwargs["params"], {"identifier": "ggp-3"})

    def test_acquired_is_not_reduced_by_fees(self):
        # 업비트는 수수료를 원화에서 뗀다 — 기초자산을 깎으면 실제보다 적게 들고 있다고 믿는다.
        broker = self.broker()
        self.session.post.return_value = FakeResponse({"uuid": "u4", "state": "wait"})
        self.session.get.return_value = FakeResponse(
            {"state": "done", "executed_volume": "0.01", "paid_fee": "500",
             "trades": [{"price": "100000000", "volume": "0.01", "funds": "1000000"}]})
        order = broker.submit("BUY", notional=1_000_000.0, client_id="ggp-4")
        self.assertEqual(order.acquired_qty, order.executed_qty)
        self.assertEqual(order.acquired_qty, 0.01)

    def test_average_price_comes_from_trades(self):
        broker = self.broker()
        self.session.post.return_value = FakeResponse({"uuid": "u5", "state": "wait"})
        self.session.get.return_value = FakeResponse(
            {"state": "done", "executed_volume": "0.02",
             "trades": [{"price": "100", "volume": "0.01", "funds": "1.0"},
                        {"price": "300", "volume": "0.01", "funds": "3.0"}]})
        order = broker.submit("BUY", notional=4.0, client_id="ggp-5")
        self.assertEqual(order.avg_price, 200.0)   # (1.0 + 3.0) / 0.02

    def test_partial_trade_coverage_is_reported_as_unknown_fees(self):
        broker = self.broker()
        self.session.post.return_value = FakeResponse({"uuid": "u6", "state": "wait"})
        self.session.get.return_value = FakeResponse(
            {"state": "done", "executed_volume": "0.02",
             "trades": [{"price": "100", "volume": "0.01", "funds": "1.0"}]})
        order = broker.submit("BUY", notional=2.0, client_id="ggp-6")
        self.assertFalse(order.fees_known)

    def test_bithumb_uses_its_own_base_url_and_order_endpoint(self):
        # 빗썸 주문은 /v2/orders · order_type · client_order_id 다(공식 문서). 읽기 경로만 업비트와 같다.
        broker = self.broker(exchange="bithumb")
        self.session.post.return_value = FakeResponse({"order_id": "u7"})
        self.session.get.return_value = FakeResponse(
            {"state": "done", "executed_volume": "0.01",
             "trades": [{"price": "100", "volume": "0.01", "funds": "1.0"}]})
        broker.submit("BUY", notional=1.0, client_id="ggp-7")
        url = self.session.post.call_args.args[0]
        self.assertTrue(url.startswith("https://api.bithumb.com"))
        self.assertTrue(url.endswith("/v2/orders"))
        body = self.session.post.call_args.kwargs["json"]
        self.assertEqual(body["order_type"], "price")
        self.assertEqual(body["client_order_id"], "ggp-7")
        self.assertNotIn("ord_type", body)
        self.assertNotIn("identifier", body)
        self.assertEqual(self.session.get.call_args.args[0], "https://api.bithumb.com/v1/order")
        self.assertEqual(self.session.get.call_args.kwargs["params"], {"client_order_id": "ggp-7"})

    def test_futures_methods_refuse_loudly(self):
        broker = self.broker()
        with self.assertRaises(Exception):
            broker.set_leverage(3)

    # --- 인자 검증: 필요한 인자가 없으면 보내지 않고 예외 -------------------
    def test_buy_without_notional_raises_and_sends_nothing(self):
        broker = self.broker()
        for kwargs in ({}, {"notional": 0}, {"notional": float("nan")}, {"base_qty": 0.01}):
            with self.subTest(kwargs=kwargs):
                with self.assertRaises(ValueError):
                    broker.submit("BUY", client_id="ggp-x", **kwargs)
        self.session.post.assert_not_called()

    def test_sell_without_quantity_raises_and_sends_nothing(self):
        broker = self.broker()
        for kwargs in ({}, {"base_qty": 0}, {"base_qty": 1e-12}, {"notional": 1_000_000.0}):
            with self.subTest(kwargs=kwargs):
                with self.assertRaises(ValueError):
                    broker.submit("SELL", client_id="ggp-x", **kwargs)
        self.session.post.assert_not_called()

    def test_reduce_only_buy_and_unknown_side_and_bad_client_id_raise(self):
        broker = self.broker()
        with self.assertRaises(ValueError):
            broker.submit("BUY", notional=5000.0, reduce_only=True, client_id="ggp-x")
        with self.assertRaises(ValueError):
            broker.submit("HOLD", notional=5000.0, client_id="ggp-x")
        with self.assertRaises(ValueError):
            broker.submit("BUY", notional=5000.0, client_id="")
        with self.assertRaises(ValueError):
            self.broker("bithumb").submit("BUY", notional=5000.0, client_id="x" * 37)
        self.session.post.assert_not_called()

    def test_numbers_are_sent_as_plain_strings_the_signature_can_match(self):
        broker = self.broker()
        self.session.post.return_value = FakeResponse({"uuid": "u"})
        self.session.get.return_value = FakeResponse(FILLED)
        broker.submit("SELL", base_qty=0.1 + 0.2, client_id="ggp-8")   # 0.30000000000000004
        body = self.session.post.call_args.kwargs["json"]
        self.assertEqual(body["volume"], "0.3")   # 8 자리 아래로 내림 — 거래소가 매도를 거절하지 않게
        broker.submit("BUY", notional=5000.0, client_id="ggp-9")
        self.assertEqual(self.session.post.call_args.kwargs["json"]["price"], "5000")

    def test_order_signature_hashes_the_body_in_the_order_it_is_sent(self):
        broker = self.broker()
        self.session.post.return_value = FakeResponse({"uuid": "u"})
        self.session.get.return_value = FakeResponse(FILLED)
        broker.submit("BUY", notional=5000.0, client_id="ggp-10")
        kwargs = self.session.post.call_args.kwargs
        _header, payload = decode_jwt(kwargs["headers"]["Authorization"].removeprefix("Bearer "))
        query = "&".join(f"{k}={v}" for k, v in kwargs["json"].items())
        self.assertEqual(payload["query_hash"], hashlib.sha512(query.encode()).hexdigest())

    # --- 상태 접기와 거절 --------------------------------------------------
    @patch("runner.brokers.time.sleep")
    def test_state_is_folded_and_the_exchange_word_is_kept(self, _sleep):
        cases = [({"state": "done", "executed_volume": "0"}, "FILLED", "done"),
                 ({"state": "cancel", "executed_volume": "0"}, "CANCELED", "cancel"),
                 ({"state": "wait", "executed_volume": "0"}, "OPEN", "wait"),
                 ({"state": "watch", "executed_volume": "0"}, "OPEN", "watch"),
                 # 시장가 매수에서 쓰지 못한 원화만 취소돼도 거래소는 cancel 로 끝낼 수 있다 — 체결은 체결이다.
                 (dict(FILLED, state="cancel"), "FILLED", "cancel")]
        for payload, status, raw in cases:
            with self.subTest(raw=raw):
                broker = self.broker()
                self.session.post.return_value = FakeResponse({"uuid": "u"})
                self.session.get.return_value = FakeResponse(payload)
                order = broker.submit("BUY", notional=5000.0, client_id="ggp-11")
                self.assertEqual((order.status, order.raw_status), (status, raw))

    @patch("runner.brokers.time.sleep")
    def test_no_answer_at_all_is_unknown_and_never_resends(self, _sleep):
        broker = self.broker()
        self.session.post.side_effect = TimeoutError()
        self.session.get.side_effect = TimeoutError()
        order = broker.submit("BUY", notional=5000.0, client_id="ggp-12")
        self.assertEqual((order.status, order.raw_status), ("UNKNOWN", "unknown"))
        self.assertEqual(self.session.post.call_count, 1)
        self.assertEqual(self.session.get.call_count, brokers.MAX_RETRIES)

    @patch("runner.brokers.time.sleep")
    def test_server_error_on_post_is_reconciled_not_resent(self, _sleep):
        broker = self.broker()
        self.session.post.return_value = FakeResponse({"error": {"name": "server_error"}}, status=500)
        self.session.get.return_value = FakeResponse(FILLED)
        order = broker.submit("BUY", notional=1_000_000.0, client_id="ggp-13")
        self.assertEqual(order.status, "FILLED")
        self.assertEqual(self.session.post.call_count, 1)

    def test_client_error_on_post_is_a_rejection_in_the_exchanges_words(self):
        broker = self.broker()
        self.session.post.return_value = FakeResponse(
            {"error": {"name": "insufficient_funds_bid", "message": "잔고 부족"}}, status=400)
        self.session.get.return_value = FakeResponse({"error": {"name": "order_not_found"}}, status=404)
        order = broker.submit("BUY", notional=1_000_000.0, client_id="ggp-14")
        self.assertEqual((order.status, order.raw_status), ("REJECTED", "insufficient_funds_bid"))
        self.assertEqual(order.executed_qty, 0.0)
        self.assertEqual(self.session.post.call_count, 1)

    @patch("runner.brokers.time.sleep")
    def test_unnamed_client_error_gets_the_full_reconcile_not_a_rejection(self, _sleep):
        # 이름 없는 4xx 는 게이트웨이가 거래소의 접수 뒤에 낸 것일 수 있다 — 타임아웃과 같이 끝까지 조회한다.
        broker = self.broker()
        self.session.post.return_value = FakeResponse("<html>Forbidden</html>", status=403)
        self.session.get.side_effect = [FakeResponse({"error": {"name": "order_not_found"}}, status=404),
                                        FakeResponse({"state": "wait", "executed_volume": "0"}),
                                        FakeResponse(FILLED)]
        order = broker.submit("BUY", notional=1_000_000.0, client_id="ggp-17")
        self.assertEqual(order.status, "FILLED")
        self.assertEqual(self.session.get.call_count, 3)
        self.assertEqual(self.session.post.call_count, 1)

    @patch("runner.brokers.time.sleep")
    def test_unnamed_client_error_with_nothing_found_is_unknown_not_rejected(self, _sleep):
        broker = self.broker()
        self.session.post.return_value = FakeResponse(None, status=403)
        self.session.get.return_value = FakeResponse({"error": {"name": "order_not_found"}}, status=404)
        order = broker.submit("BUY", notional=1_000_000.0, client_id="ggp-18")
        self.assertEqual(order.status, "UNKNOWN")
        self.assertEqual(self.session.get.call_count, brokers.MAX_RETRIES)

    def test_client_error_but_order_found_trusts_the_lookup(self):
        broker = self.broker()
        self.session.post.return_value = FakeResponse({"error": {"name": "too_many_requests"}}, status=429)
        self.session.get.return_value = FakeResponse(FILLED)
        order = broker.submit("BUY", notional=1_000_000.0, client_id="ggp-15")
        self.assertEqual(order.status, "FILLED")

    def test_order_id_is_the_exchanges_uuid(self):
        broker = self.broker()
        self.session.post.return_value = FakeResponse({"uuid": "u"})
        self.session.get.return_value = FakeResponse(dict(FILLED, uuid="abc-123"))
        self.assertEqual(broker.submit("BUY", notional=1_000_000.0, client_id="ggp-16").order_id, "abc-123")

    # --- 준비 · 시세 -------------------------------------------------------
    def test_order_rules_and_fees_come_from_chance(self):
        broker = self.broker()
        self.session.get.return_value = FakeResponse(
            {"bid_fee": "0.0005", "ask_fee": "0.0005",
             "bid": {"min_total": "5000"}, "ask": {"min_total": "5000"}})
        self.assertTrue(broker.ensure_ready())
        rules = broker.order_rules()
        self.assertEqual((rules.step, rules.min_notional), (0.0, 5000.0))
        self.assertEqual(broker.fees, {"bid": 0.0005, "ask": 0.0005})
        self.assertEqual(self.session.get.call_args.args[0], "https://api.upbit.com/v1/orders/chance")
        self.assertEqual(self.session.get.call_args.kwargs["params"], {"market": "KRW-BTC"})

    def test_ensure_ready_is_false_for_a_missing_market_but_raises_on_bad_keys(self):
        broker = self.broker()
        self.session.get.return_value = FakeResponse({"error": {"name": "invalid_market"}}, status=404)
        self.assertFalse(broker.ensure_ready())
        self.session.get.return_value = FakeResponse({"error": {"name": "invalid_access_key"}}, status=401)
        with self.assertRaises(brokers.DomesticApiError):
            broker.ensure_ready()

    def test_price_is_public_and_unsigned(self):
        broker = self.broker()
        self.session.get.return_value = FakeResponse([{"market": "KRW-BTC", "trade_price": 101500000.0}])
        self.assertEqual(broker.price(), 101500000.0)
        self.assertEqual(self.session.get.call_args.kwargs["params"], {"markets": "KRW-BTC"})
        self.assertIsNone(self.session.get.call_args.kwargs["headers"])

    def test_check_account_reads_the_krw_balance(self):
        broker = self.broker()
        self.session.get.return_value = FakeResponse(
            [{"currency": "BTC", "balance": "0.1"}, {"currency": "KRW", "balance": "250000"}])
        broker.check_account()
        self.assertIn("250000", broker.log.call_args.args[0])
        self.assertEqual(self.session.get.call_args.args[0], "https://api.upbit.com/v1/accounts")
        self.assertIn("Bearer ", self.session.get.call_args.kwargs["headers"]["Authorization"])

    def test_every_futures_method_refuses(self):
        broker = self.broker()
        for call in (lambda: broker.set_leverage(3), lambda: broker.set_margin_type("ISOLATED")):
            with self.assertRaises(RuntimeError):
                call()


class JwtTests(unittest.TestCase):
    def test_token_has_three_parts_and_a_fresh_nonce(self):
        first = brokers.jwt_token("acc", "sec")
        second = brokers.jwt_token("acc", "sec")
        self.assertEqual(len(first.split(".")), 3)
        self.assertNotEqual(first, second)   # nonce 가 요청마다 새로워야 한다

    def test_query_hash_is_present_only_with_a_query(self):
        def payload(token):
            raw = token.split(".")[1]
            raw += "=" * (-len(raw) % 4)
            return json.loads(base64.urlsafe_b64decode(raw))

        self.assertNotIn("query_hash", payload(brokers.jwt_token("acc", "sec")))
        body = payload(brokers.jwt_token("acc", "sec", {"market": "KRW-BTC"}))
        self.assertIn("query_hash", body)
        self.assertEqual(body["query_hash_alg"], "SHA512")

    def test_signature_is_plain_hmac_over_header_and_payload(self):
        # 라이브러리 없이 만든 서명이 규격대로인지 — 같은 입력을 hmac 으로 따로 계산해 맞춘다.
        for alg, digest in (("HS512", hashlib.sha512), ("HS256", hashlib.sha256)):
            with self.subTest(alg=alg):
                token = brokers.jwt_token("acc", "sec", {"market": "KRW-BTC"}, alg=alg)
                head, body, signature = token.split(".")
                expected = hmac.new(b"sec", f"{head}.{body}".encode(), digest).digest()
                self.assertEqual(signature, base64.urlsafe_b64encode(expected).decode().rstrip("="))
                self.assertEqual(decode_jwt(token)[0], {"alg": alg, "typ": "JWT"})

    def test_query_hash_is_sha512_of_the_unencoded_query_string(self):
        _h, payload = decode_jwt(brokers.jwt_token("acc", "sec", {"market": "KRW-BTC", "identifier": "ggp-1"}))
        self.assertEqual(payload["query_hash"],
                         hashlib.sha512(b"market=KRW-BTC&identifier=ggp-1").hexdigest())

    def test_bithumb_token_is_hs256_with_a_millisecond_timestamp(self):
        header, payload = decode_jwt(brokers.jwt_token("acc", "sec", alg="HS256", timestamp=True))
        self.assertEqual(header["alg"], "HS256")
        self.assertGreater(payload["timestamp"], 1_700_000_000_000)
        _h, upbit = decode_jwt(brokers.jwt_token("acc", "sec"))
        self.assertNotIn("timestamp", upbit)

    def test_broker_signs_with_its_exchanges_algorithm(self):
        for exchange, alg in (("upbit", "HS512"), ("bithumb", "HS256")):
            with self.subTest(exchange=exchange):
                session = Mock()
                session.get.return_value = FakeResponse([{"currency": "KRW", "balance": "1"}])
                broker = brokers.DomesticBroker("acc", "sec", exchange=exchange, symbol="KRW-BTC",
                                                log=Mock(), session=session)
                broker.check_account()
                token = session.get.call_args.kwargs["headers"]["Authorization"].removeprefix("Bearer ")
                self.assertEqual(decode_jwt(token)[0]["alg"], alg)
