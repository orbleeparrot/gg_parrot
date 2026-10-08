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


CHANCE = {"bid_fee": "0.0005", "ask_fee": "0.0005", "bid": {"min_total": "5000"}, "ask": {"min_total": "5000"}}
ACCOUNTS = [{"currency": "KRW", "balance": "250000"}]


def exchange_error(name, message="", status=401):
    return FakeResponse({"error": {"name": name, "message": message}}, status=status)


class RehearsalTests(unittest.TestCase):
    """리허설은 돈 없이 키 · IP · 권한 · 최소 금액을 본다. 무엇보다 진짜 주문을 넣으면 안 된다."""

    def broker(self, exchange="upbit"):
        self.session = Mock()
        return brokers.DomesticBroker("acc", "sec", exchange=exchange,
                                      symbol="KRW-BTC", log=Mock(), session=self.session)

    # --- 업비트: 주문 검증 경로 ---------------------------------------------
    def test_upbit_uses_the_test_endpoint_never_the_real_one(self):
        broker = self.broker()
        self.session.post.return_value = FakeResponse({"uuid": "t1", "state": "wait"}, status=201)
        ok, reason = broker.rehearse(notional=10_000.0)
        self.assertTrue(ok, reason)
        self.assertEqual(self.session.post.call_count, 1)
        self.assertEqual(self.session.post.call_args.args[0], "https://api.upbit.com/v1/orders/test")
        self.assertNotIn("https://api.upbit.com/v1/orders", [c.args[0] for c in self.session.post.call_args_list])

    def test_upbit_sends_the_same_body_shape_a_real_buy_would(self):
        broker = self.broker()
        self.session.post.return_value = FakeResponse({"uuid": "t1"}, status=201)
        broker.rehearse(notional=10_000.0)
        rehearsal_body = self.session.post.call_args.kwargs["json"]
        self.session.post.reset_mock()
        self.session.get.return_value = FakeResponse(dict(FILLED))
        broker.submit("BUY", notional=10_000.0, client_id="ggp-1")
        real_body = self.session.post.call_args.kwargs["json"]
        self.assertEqual(list(rehearsal_body), list(real_body))
        self.assertEqual({k: v for k, v in rehearsal_body.items() if k != "identifier"},
                         {k: v for k, v in real_body.items() if k != "identifier"})
        self.assertLessEqual(len(rehearsal_body["identifier"]), 64)

    def test_upbit_signs_the_test_request_over_its_body(self):
        broker = self.broker()
        self.session.post.return_value = FakeResponse({"uuid": "t1"}, status=201)
        broker.rehearse(notional=10_000.0)
        body = self.session.post.call_args.kwargs["json"]
        token = self.session.post.call_args.kwargs["headers"]["Authorization"].removeprefix("Bearer ")
        _header, payload = decode_jwt(token)
        qs = "&".join(f"{k}={v}" for k, v in body.items())
        self.assertEqual(payload["query_hash"], hashlib.sha512(qs.encode()).hexdigest())

    def test_upbit_status_other_than_201_is_not_a_pass(self):
        broker = self.broker()
        self.session.post.return_value = FakeResponse({"ok": True}, status=200)
        ok, reason = broker.rehearse(notional=10_000.0)
        self.assertFalse(ok)
        self.assertIn("200", reason)

    def test_ip_class_errors_name_the_allowed_ip_before_the_key(self):
        for exchange, name in (("upbit", "no_authorization_ip"), ("upbit", "invalid_access_key"),
                               ("bithumb", "NotAllowIP")):
            with self.subTest(exchange=exchange, name=name):
                broker = self.broker(exchange)
                self.session.post.return_value = exchange_error(name, "허용되지 않은 IP")
                self.session.get.return_value = exchange_error(name, "허용되지 않은 IP")
                ok, reason = broker.rehearse(notional=10_000.0)
                self.assertFalse(ok)
                self.assertIn("허용 IP", reason)
                self.assertLess(reason.index("허용 IP"), reason.index("API 키"))

    def test_jwt_verification_names_secret_signature_not_ip_or_key_expiration(self):
        for exchange in ("upbit", "bithumb"):
            with self.subTest(exchange=exchange):
                broker = self.broker(exchange)
                self.session.post.return_value = exchange_error("jwt_verification", status=400)
                self.session.get.return_value = exchange_error("jwt_verification", status=400)
                ok, reason = broker.rehearse(notional=10_000.0)
                self.assertFalse(ok)
                self.assertIn("JWT", reason)
                self.assertIn("시크릿", reason)
                self.assertIn("서명", reason)
                self.assertNotIn("허용 IP", reason)
                self.assertNotIn("만료", reason)
                self.assertTrue(brokers._is_access_error(brokers.DomesticApiError(400, "jwt_verification", "fixture")))

    def test_minimum_order_amount_is_named(self):
        broker = self.broker()
        for name in ("under_min_total_bid", "under_min_total_market_bid"):
            with self.subTest(name=name):
                self.session.post.return_value = exchange_error(name, "Order amount is too small", status=400)
                ok, reason = broker.rehearse(notional=1_000.0)
                self.assertFalse(ok)
                self.assertIn("최소 주문 금액", reason)
                self.assertNotIn("허용 IP", reason)

    def test_missing_order_permission_and_expired_key_are_told_apart_from_ip(self):
        broker = self.broker()
        self.session.post.return_value = exchange_error("out_of_scope", "권한이 부족합니다", status=403)
        ok, reason = broker.rehearse(notional=10_000.0)
        self.assertFalse(ok)
        self.assertIn("주문 권한", reason)
        self.assertNotIn("허용 IP", reason)
        self.session.post.return_value = exchange_error("expired_access_key")
        self.assertIn("만료", broker.rehearse(notional=10_000.0)[1])

    def test_bithumb_expired_jwt_is_not_an_expired_api_key_or_reissue_instruction(self):
        broker = self.broker("bithumb")
        self.session.get.return_value = exchange_error("expired_jwt")
        ok, reason = broker.rehearse(notional=10_000.0)
        self.assertFalse(ok)
        self.assertIn("JWT", reason)
        self.assertIn("timestamp", reason)
        self.assertIn("시각", reason)
        self.assertNotIn("API 키가 만료", reason)
        self.assertNotIn("재발급", reason)
        self.assertNotIn("연장", reason)
        self.session.post.assert_not_called()

    def test_expired_access_key_requires_reissue_not_an_unsupported_extension(self):
        for exchange in ("upbit", "bithumb"):
            with self.subTest(exchange=exchange):
                reason = brokers._explain_domestic_error(exchange, 401, "expired_access_key", "fixture")
                self.assertIn("API 키가 만료", reason)
                self.assertIn("삭제", reason)
                self.assertIn("발급", reason)
                self.assertNotIn("연장", reason)

    def test_unrecognised_error_keeps_the_exchanges_own_words(self):
        broker = self.broker()
        self.session.post.return_value = exchange_error("brand_new_error", "거래소가 한 말", status=400)
        ok, reason = broker.rehearse(notional=10_000.0)
        self.assertFalse(ok)
        self.assertIn("brand_new_error", reason)
        self.assertIn("거래소가 한 말", reason)

    def test_unnamed_error_still_reports_the_status(self):
        broker = self.broker()
        self.session.post.return_value = FakeResponse("<html>blocked</html>", status=403)
        ok, reason = broker.rehearse(notional=10_000.0)
        self.assertFalse(ok)
        self.assertIn("403", reason)

    def test_network_failure_is_a_failure_not_an_exception_and_not_blamed_on_the_key(self):
        broker = self.broker()
        self.session.post.side_effect = TimeoutError("timed out")
        ok, reason = broker.rehearse(notional=10_000.0)
        self.assertFalse(ok)
        self.assertIn("연결", reason)
        self.assertNotIn("허용 IP", reason)

    def test_bad_notional_fails_without_sending_anything(self):
        broker = self.broker()
        ok, _reason = broker.rehearse(notional=0)
        self.assertFalse(ok)
        self.session.post.assert_not_called()

    def test_signature_mismatch_is_our_bug_not_an_allowed_ip_problem(self):
        for exchange in ("upbit", "bithumb"):
            with self.subTest(exchange=exchange):
                broker = self.broker(exchange)
                self.session.post.return_value = exchange_error("invalid_query_payload", "JWT 페이로드 오류")
                self.session.get.return_value = exchange_error("invalid_query_payload", "JWT 페이로드 오류")
                ok, reason = broker.rehearse(notional=10_000.0)
                self.assertFalse(ok)
                self.assertIn("서명", reason)
                self.assertNotIn("집 IP 는 바뀝니다", reason)
                self.assertNotIn("다시 복사", reason)

    # --- 빗썸: 검증 경로가 없어 읽기 호출로 갈음 --------------------------------
    def bithumb_reads(self, accounts=ACCOUNTS, chance=CHANCE):
        def get(url, **_kwargs):
            return FakeResponse(accounts if url.endswith("/v1/accounts") else chance)
        self.session.get.side_effect = get

    def test_bithumb_never_posts_because_it_has_no_test_endpoint(self):
        broker = self.broker("bithumb")
        self.bithumb_reads()
        ok, reason = broker.rehearse(notional=10_000.0)
        self.assertTrue(ok, reason)
        self.session.post.assert_not_called()
        urls = [c.args[0] for c in self.session.get.call_args_list]
        self.assertEqual(urls, ["https://api.bithumb.com/v1/accounts", "https://api.bithumb.com/v1/orders/chance"])

    def test_bithumb_pass_does_not_claim_the_order_permission_was_checked(self):
        broker = self.broker("bithumb")
        self.bithumb_reads()
        _ok, reason = broker.rehearse(notional=10_000.0)
        self.assertIn("주문 권한", reason)
        self.assertIn("첫 주문", reason)

    def test_bithumb_checks_the_minimum_amount_itself(self):
        broker = self.broker("bithumb")
        self.bithumb_reads()
        ok, reason = broker.rehearse(notional=1_000.0)
        self.assertFalse(ok)
        self.assertIn("최소 주문 금액", reason)
        self.assertIn("5000", reason)
        self.session.post.assert_not_called()

    def test_bithumb_unknown_minimum_does_not_block(self):
        broker = self.broker("bithumb")
        self.bithumb_reads(chance={"bid": {}})
        self.assertTrue(broker.rehearse(notional=1.0)[0])

    def test_bithumb_missing_market_is_reported(self):
        broker = self.broker("bithumb")

        def get(url, **_kwargs):
            if url.endswith("/v1/accounts"):
                return FakeResponse(ACCOUNTS)
            return exchange_error("invalid_market", status=404)
        self.session.get.side_effect = get
        ok, reason = broker.rehearse(notional=10_000.0)
        self.assertFalse(ok)
        self.assertIn("마켓", reason)


class BinanceRehearsalTests(unittest.TestCase):
    def broker(self, market="spot", min_notional="5"):
        self.raw = Mock()
        self.raw.get_account.return_value = {"balances": [{"asset": "USDT", "free": "100"}]}
        self.raw.get_symbol_info.return_value = {
            "baseAsset": "BTC",
            "filters": [{"filterType": "LOT_SIZE", "stepSize": "0.001"},
                        {"filterType": "NOTIONAL", "minNotional": min_notional}]}
        return brokers.BinanceBroker(self.raw, market=market, symbol="BTCUSDT",
                                     side="long", testnet=True, log=Mock())

    def test_passes_on_account_and_rules_without_placing_any_order(self):
        broker = self.broker()
        ok, reason = broker.rehearse(notional=10.0)
        self.assertTrue(ok, reason)
        self.raw.get_account.assert_called_once()
        self.raw.get_symbol_info.assert_called_once()
        self.raw.create_order.assert_not_called()
        self.raw.futures_create_order.assert_not_called()

    def test_account_failure_is_a_failure_not_an_exception(self):
        broker = self.broker()
        self.raw.get_account.side_effect = RuntimeError("boom")
        ok, reason = broker.rehearse(notional=10.0)
        self.assertFalse(ok)
        self.assertIn("boom", reason)

    def test_invalid_key_ip_or_permission_code_points_at_all_three(self):
        broker = self.broker()
        error = RuntimeError("APIError(code=-2015): Invalid API-key, IP, or permissions for action")
        error.code = -2015
        self.raw.get_account.side_effect = error
        ok, reason = broker.rehearse(notional=10.0)
        self.assertFalse(ok)
        self.assertIn("허용 IP", reason)

    def test_unknown_symbol_fails(self):
        broker = self.broker()
        self.raw.get_symbol_info.return_value = None
        ok, reason = broker.rehearse(notional=10.0)
        self.assertFalse(ok)
        self.assertIn("BTCUSDT", reason)

    def test_below_the_minimum_notional_fails(self):
        broker = self.broker(min_notional="5")
        ok, reason = broker.rehearse(notional=1.0)
        self.assertFalse(ok)
        self.assertIn("최소 주문 금액", reason)

    # --- 선물: 예외를 삼키는 준비 단계에 기대면 나쁜 키로도 통과한다 ----------------
    def futures_broker(self, min_notional="5"):
        self.raw = Mock()
        self.raw.futures_account_balance.return_value = [{"asset": "USDT", "balance": "100"}]
        self.raw.futures_exchange_info.return_value = {"symbols": [
            {"symbol": "BTCUSDT", "filters": [{"filterType": "LOT_SIZE", "stepSize": "0.001"},
                                              {"filterType": "MIN_NOTIONAL", "notional": min_notional}]}]}
        return brokers.BinanceBroker(self.raw, market="futures", symbol="BTCUSDT",
                                     side="long", testnet=True, log=Mock())

    def test_futures_passes_on_a_signed_read_and_touches_nothing_else(self):
        broker = self.futures_broker()
        ok, reason = broker.rehearse(notional=10.0)
        self.assertTrue(ok, reason)
        self.raw.futures_create_order.assert_not_called()
        self.raw.futures_change_leverage.assert_not_called()
        self.raw.futures_change_margin_type.assert_not_called()

    def test_futures_bad_key_fails_even_though_check_account_would_swallow_it(self):
        broker = self.futures_broker()
        error = RuntimeError("APIError(code=-2015): Invalid API-key, IP, or permissions for action")
        error.code = -2015
        self.raw.futures_account_balance.side_effect = error
        ok, reason = broker.rehearse(notional=10.0)
        self.assertFalse(ok)
        self.assertIn("허용 IP", reason)

    def test_futures_other_balance_failure_fails_too(self):
        broker = self.futures_broker()
        self.raw.futures_account_balance.side_effect = RuntimeError("boom")
        ok, reason = broker.rehearse(notional=10.0)
        self.assertFalse(ok)
        self.assertIn("boom", reason)

    def test_futures_symbol_info_failure_or_missing_symbol_fails(self):
        broker = self.futures_broker()
        self.raw.futures_exchange_info.side_effect = RuntimeError("down")
        self.assertFalse(broker.rehearse(notional=10.0)[0])
        self.raw.futures_exchange_info.side_effect = None
        self.raw.futures_exchange_info.return_value = {"symbols": [{"symbol": "ETHUSDT", "filters": []}]}
        ok, reason = broker.rehearse(notional=10.0)
        self.assertFalse(ok)
        self.assertIn("BTCUSDT", reason)

    def test_futures_below_the_minimum_notional_fails(self):
        broker = self.futures_broker(min_notional="5")
        ok, reason = broker.rehearse(notional=1.0)
        self.assertFalse(ok)
        self.assertIn("최소 주문 금액", reason)


class PostOrderLookupNeverRaisesTests(unittest.TestCase):
    """submit 에서 나간 예외는 '주문을 보내지 않았다' 는 뜻이다(바이낸스 어댑터가 지키는 약속).

    POST 뒤에 터지는 예외는 그 약속을 깨뜨린다 — 봇은 포지션을 불확실로 잡지도 않고 명령을 계속 받는데
    실제로는 원화 시장가 주문이 체결돼 있을 수 있다. 그래서 POST 뒤의 모든 실패는 UNKNOWN 으로 나온다.
    """

    def broker(self, exchange="upbit"):
        self.session = Mock()
        return brokers.DomesticBroker("acc", "sec", exchange=exchange,
                                      symbol="KRW-BTC", log=Mock(), session=self.session)

    @patch("runner.brokers.time.sleep")
    def test_a_non_object_lookup_body_is_unknown_not_an_exception(self, _sleep):
        # HTTP 200 에 객체가 아닌 몸통(목록 · 게이트웨이 안내문)이 실려 온 경우.
        for body in ([{"state": "done"}], "maintenance", 7, None):
            with self.subTest(body=body):
                broker = self.broker()
                self.session.post.return_value = FakeResponse({"uuid": "u"})
                self.session.get.return_value = FakeResponse(body)
                order = broker.submit("BUY", notional=5000.0, client_id="ggp-shape")
                self.assertEqual(order.status, "UNKNOWN")  # 봇은 끝 상태가 아닌 주문을 불확실로 잡는다
                self.assertEqual(self.session.post.call_count, 1)  # 두 번째 시장가 주문은 없다
                # 모양이 틀린 답은 '답이 없다' 로 본다 — 조회는 끝까지 다시 해 본다(한 번에 포기하지 않는다).
                self.assertEqual(self.session.get.call_count, brokers.MAX_RETRIES)

    @patch("runner.brokers.time.sleep")
    def test_trades_that_are_not_objects_do_not_raise_and_stay_uncertain(self, _sleep):
        broker = self.broker()
        self.session.post.return_value = FakeResponse({"uuid": "u"})
        self.session.get.return_value = FakeResponse({"state": "done", "executed_volume": "0.01",
                                                      "trades": ["0.01"]})
        order = broker.submit("BUY", notional=5000.0, client_id="ggp-trades")
        self.assertEqual(order.status, "FILLED")
        self.assertFalse(order.fees_known)  # 체결 내역이 체결 수량을 덮지 못했다 → 봇이 불확실로 본다

    @patch("runner.brokers.time.sleep")
    def test_any_other_failure_after_the_post_is_unknown_never_an_exception(self, _sleep):
        broker = self.broker()
        self.session.post.return_value = FakeResponse({"uuid": "u"})
        self.session.get.return_value = FakeResponse(FILLED)
        broker._normalize = Mock(side_effect=AttributeError("boom"))
        order = broker.submit("BUY", notional=5000.0, client_id="ggp-guard")
        self.assertEqual((order.status, order.raw_status), ("UNKNOWN", "unknown"))
        self.assertTrue(any("확인하지 못했습니다" in str(c) for c in broker.log.call_args_list))

    def test_the_lookup_and_the_normaliser_each_hold_the_shape_on_their_own(self):
        # 바깥 덮개 하나에 기대지 않는다 — 두 자리가 각각 모양을 확인해야 한번에 무너지지 않는다.
        broker = self.broker()
        self.session.get.return_value = FakeResponse([{"state": "done"}])
        self.assertEqual(broker._lookup_order("ggp-x"), {})
        for body in ([{"state": "done"}], "maintenance", None, 7):
            with self.subTest(body=body):
                order = broker._normalize(body)
                self.assertEqual((order.status, order.raw_status), ("UNKNOWN", "unknown"))

    def test_the_pre_post_value_errors_still_raise_and_send_nothing(self):
        # 주문을 보내기 전의 거절은 예외가 맞다 — 그게 '주문이 나가지 않았다' 는 뜻이다.
        broker = self.broker()
        with self.assertRaises(ValueError):
            broker.submit("BUY", notional=0, client_id="ggp-pre")
        with self.assertRaises(ValueError):
            broker.submit("SELL", base_qty=0, client_id="ggp-pre2")
        self.session.post.assert_not_called()


class KrwAmountIsWholeWonTests(unittest.TestCase):
    """원화 금액은 1원 단위로 보낸다. 정수만 받는 거래소라면 소수점이 붙은 금액은 전부 거절된다 —
    서명은 우리가 보낸 글자로 맞춰져 있어 다른 증상이 없고, 모든 국내 진입이 조용히 막힌다."""

    def broker(self, exchange="upbit"):
        self.session = Mock()
        return brokers.DomesticBroker("acc", "sec", exchange=exchange,
                                      symbol="KRW-BTC", log=Mock(), session=self.session)

    def test_a_fractional_won_amount_is_floored_to_an_integer(self):
        broker = self.broker()
        self.session.post.return_value = FakeResponse({"uuid": "u"})
        self.session.get.return_value = FakeResponse(FILLED)
        broker.submit("BUY", notional=100_000 / 3, client_id="ggp-won")  # 33333.333333333336
        self.assertEqual(self.session.post.call_args.kwargs["json"]["price"], "33333")

    def test_the_rehearsal_order_uses_the_same_integer_amount(self):
        broker = self.broker()
        self.session.post.return_value = FakeResponse({"uuid": "t"}, status=201)
        self.assertTrue(broker.rehearse(notional=100_000 / 3)[0])
        self.assertEqual(self.session.post.call_args.kwargs["json"]["price"], "33333")

    def test_an_amount_under_one_won_is_refused_before_the_post(self):
        broker = self.broker()
        with self.assertRaises(ValueError):
            broker.submit("BUY", notional=0.4, client_id="ggp-dust")
        self.session.post.assert_not_called()


class AccessFailureIsNotAMissingMarketTests(unittest.TestCase):
    """준비 단계(load_market)는 서명이 필요한 조회다 — 세션에서 키 · 허용 IP 에 가장 먼저 걸리는 곳이다.

    그 실패를 '마켓 없음'(거짓)으로 접으면 봇이 '심볼을 바꾸세요' 로 보고하고, 정작 고쳐야 할
    허용 IP 안내는 사용자에게 닿지 않는다.
    """

    def broker(self, exchange="upbit"):
        self.session = Mock()
        return brokers.DomesticBroker("acc", "sec", exchange=exchange,
                                      symbol="KRW-BTC", log=Mock(), session=self.session)

    def test_an_ip_error_raises_with_the_ip_first_explanation_even_as_a_400(self):
        for status in (400, 401, 403):
            with self.subTest(status=status):
                broker = self.broker()
                self.session.get.return_value = exchange_error("no_authorization_ip", status=status)
                with self.assertRaises(brokers.DomesticAccessError) as caught:
                    broker.load_market()
                self.assertIn("허용 IP", str(caught.exception))
                self.assertNotIn("심볼", str(caught.exception))

    def test_every_access_class_error_is_told_apart_from_the_market(self):
        for name in ("invalid_access_key", "expired_access_key", "out_of_scope", "jwt_verification"):
            with self.subTest(name=name):
                broker = self.broker()
                self.session.get.return_value = exchange_error(name, status=400)
                with self.assertRaises(brokers.DomesticAccessError):
                    broker.load_market()

    def test_a_real_missing_market_is_still_just_false(self):
        broker = self.broker()
        self.session.get.return_value = exchange_error("invalid_market", status=404)
        self.assertFalse(broker.load_market())
        self.assertFalse(broker.ensure_ready())

    def test_the_access_error_is_still_an_api_error_so_old_callers_keep_working(self):
        broker = self.broker()
        self.session.get.return_value = exchange_error("invalid_access_key", status=401)
        with self.assertRaises(brokers.DomesticApiError):
            broker.ensure_ready()
