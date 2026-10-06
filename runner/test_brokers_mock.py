import unittest
from unittest.mock import Mock

from runner import brokers
from runner.test_brokers_domestic import FakeResponse


class MockBrokerTests(unittest.TestCase):
    def inner(self):
        inner = Mock()
        inner.price.return_value = 100.0
        inner.order_rules.return_value = brokers.OrderRules(step=0.001, min_notional=0.0)
        inner.fees = {"bid": 0.0005, "ask": 0.0005}
        inner.rehearse.return_value = (True, "")
        inner.load_market.return_value = True
        inner.fee_from_base_asset = True  # 바이낸스 현물처럼 수수료를 받은 수량에서 뗀다
        return inner

    def test_mock_never_submits_to_the_exchange(self):
        inner = self.inner()
        broker = brokers.MockBroker(inner, log=Mock())
        broker.submit("BUY", notional=1000.0, client_id="ggp-1")
        broker.submit("SELL", base_qty=1.0, client_id="ggp-2")
        inner.submit.assert_not_called()

    def test_mock_fills_at_the_current_price(self):
        broker = brokers.MockBroker(self.inner(), log=Mock())
        order = broker.submit("BUY", notional=1000.0, client_id="ggp-3")
        self.assertEqual(order.status, "FILLED")
        self.assertEqual(order.avg_price, 100.0)
        self.assertTrue(order.fees_known)
        self.assertEqual(order.executed_qty, 10.0)  # 1000 / 100

    def test_mock_applies_the_fee_rate_to_acquired(self):
        broker = brokers.MockBroker(self.inner(), log=Mock())
        order = broker.submit("BUY", notional=1000.0, client_id="ggp-4")
        # 수수료를 무시하면 acquired == executed 라서 첫 줄이 깨진다.
        self.assertLess(order.acquired_qty, order.executed_qty)
        self.assertGreater(order.acquired_qty, order.executed_qty * 0.99)
        self.assertAlmostEqual(order.acquired_qty, 10.0 * (1 - 0.0005))

    def test_mock_sell_executes_the_requested_quantity_without_fee_haircut(self):
        broker = brokers.MockBroker(self.inner(), log=Mock())
        order = broker.submit("SELL", base_qty=2.5, client_id="ggp-5")
        self.assertEqual(order.executed_qty, 2.5)
        self.assertEqual(order.avg_price, 100.0)
        # 매도는 원화/USDT 를 받는 쪽이라 수량이 깎이지 않는다 — 실제 어댑터도 같다.
        self.assertEqual(order.acquired_qty, 2.5)

    def test_mock_buy_by_quantity_when_the_bot_passes_base_qty(self):
        # 봇은 바이낸스식으로 base_qty 를 넘긴다. 매수도 그 수량대로 체결돼야 한다.
        broker = brokers.MockBroker(self.inner(), log=Mock())
        order = broker.submit("BUY", base_qty=3.0, client_id="ggp-6")
        self.assertEqual(order.executed_qty, 3.0)
        self.assertAlmostEqual(order.acquired_qty, 3.0 * (1 - 0.0005))

    def test_mock_futures_buy_is_not_fee_haircut(self):
        inner = self.inner()
        inner.fee_from_base_asset = False  # 선물 · 국내는 수수료를 수량에서 떼지 않는다
        order = brokers.MockBroker(inner, log=Mock()).submit("BUY", base_qty=3.0, client_id="ggp-7")
        self.assertEqual(order.acquired_qty, 3.0)

    def test_mock_closing_buy_is_not_fee_haircut(self):
        order = brokers.MockBroker(self.inner(), log=Mock()).submit(
            "BUY", base_qty=3.0, reduce_only=True, client_id="ggp-8")
        self.assertEqual(order.acquired_qty, 3.0)

    def test_mock_uses_default_fee_when_inner_has_none_or_unloaded(self):
        inner = self.inner()
        del inner.fees  # 바이낸스 어댑터에는 fees 가 없다
        order = brokers.MockBroker(inner, log=Mock()).submit("BUY", notional=1000.0, client_id="ggp-9")
        self.assertAlmostEqual(order.acquired_qty, 10.0 * (1 - 0.0005))
        inner.fees = {"bid": 0.0, "ask": 0.0}  # 국내 어댑터는 키가 없으면 0 인 채로 남는다
        order = brokers.MockBroker(inner, log=Mock()).submit("BUY", notional=1000.0, client_id="ggp-10")
        self.assertAlmostEqual(order.acquired_qty, 10.0 * (1 - 0.0005))

    def test_mock_uses_the_inner_bid_fee_when_loaded(self):
        inner = self.inner()
        inner.fees = {"bid": 0.0025, "ask": 0.0025}
        order = brokers.MockBroker(inner, log=Mock()).submit("BUY", notional=1000.0, client_id="ggp-11")
        self.assertAlmostEqual(order.acquired_qty, 10.0 * (1 - 0.0025))

    def test_mock_rejects_orders_with_no_amount_instead_of_filling_nothing(self):
        broker = brokers.MockBroker(self.inner(), log=Mock())
        with self.assertRaises(ValueError):
            broker.submit("BUY", client_id="ggp-12")
        with self.assertRaises(ValueError):
            broker.submit("SELL", notional=1000.0, client_id="ggp-13")

    def test_mock_still_reads_price_from_the_real_exchange(self):
        inner = self.inner()
        brokers.MockBroker(inner, log=Mock()).price()
        inner.price.assert_called_once()

    def test_mock_reads_the_market_without_the_inner_account_writes(self):
        inner = self.inner()
        broker = brokers.MockBroker(inner, log=Mock())
        self.assertTrue(broker.ensure_ready())
        broker.check_account()
        inner.load_market.assert_called_once()
        inner.check_account.assert_called_once()
        # 안쪽 ensure_ready 는 선물에서 실계정의 레버리지를 바꾼다 — 모의는 읽기 쪽만 부른다.
        inner.ensure_ready.assert_not_called()

    def test_mock_keeps_symbol_not_found_as_false(self):
        inner = self.inner()
        inner.load_market.return_value = False
        self.assertFalse(brokers.MockBroker(inner, log=Mock()).ensure_ready())

    def test_mock_ensure_ready_swallows_auth_failures_once(self):
        inner = self.inner()
        inner.load_market.side_effect = RuntimeError("HTTP 401 no_authorization_ip")
        log = Mock()
        broker = brokers.MockBroker(inner, log=log)
        self.assertTrue(broker.ensure_ready())
        self.assertTrue(broker.ensure_ready())
        self.assertEqual(log.call_count, 1)
        self.assertIn("no_authorization_ip", log.call_args.args[0])

    def test_mock_check_account_swallows_auth_failures_once(self):
        inner = self.inner()
        inner.check_account.side_effect = RuntimeError("HTTP 401 invalid_access_key")
        log = Mock()
        broker = brokers.MockBroker(inner, log=log)
        broker.check_account()
        broker.check_account()
        self.assertEqual(log.call_count, 1)
        self.assertIn("invalid_access_key", log.call_args.args[0])

    def test_mock_tolerates_a_failed_rehearsal(self):
        inner = self.inner()
        inner.rehearse.return_value = (False, "허용 IP 가 등록되지 않았어요")
        log = Mock()
        ok, reason = brokers.MockBroker(inner, log=log).rehearse(notional=1000.0)
        self.assertTrue(ok, "모의는 키 없이도 돌 수 있어야 연습이 된다")
        inner.rehearse.assert_called_once_with(notional=1000.0)
        # 이유가 사라지면 사용자는 왜 실전에서 막힐지 모른다 — 기록과 돌려준 말 양쪽에 남는다.
        logged = [call.args[0] for call in log.call_args_list if call.args]
        self.assertTrue(any("허용 IP 가 등록되지 않았어요" in line for line in logged), logged)
        self.assertIn("허용 IP 가 등록되지 않았어요", reason)

    def test_mock_passes_a_successful_rehearsal_through_quietly(self):
        inner = self.inner()
        inner.rehearse.return_value = (True, "검증 통과")
        log = Mock()
        ok, reason = brokers.MockBroker(inner, log=log).rehearse(notional=1000.0)
        self.assertTrue(ok)
        self.assertEqual(reason, "검증 통과")
        log.assert_not_called()

    def test_mock_rehearse_survives_an_inner_that_raises(self):
        inner = self.inner()
        inner.rehearse.side_effect = RuntimeError("연결 끊김")
        log = Mock()
        ok, reason = brokers.MockBroker(inner, log=log).rehearse(notional=1000.0)
        self.assertTrue(ok)
        self.assertIn("연결 끊김", reason)
        self.assertTrue(any("연결 끊김" in call.args[0] for call in log.call_args_list))

    def test_mock_falls_back_when_order_rules_need_keys(self):
        inner = self.inner()
        inner.order_rules.side_effect = RuntimeError("401")
        rules = brokers.MockBroker(inner, log=Mock()).order_rules()
        self.assertGreaterEqual(rules.min_notional, 0.0)
        self.assertEqual(rules, brokers.OrderRules(step=0.0, min_notional=0.0))

    def test_mock_records_the_order_rules_fallback_once(self):
        inner = self.inner()
        inner.order_rules.side_effect = RuntimeError("401 키가 없어요")
        log = Mock()
        broker = brokers.MockBroker(inner, log=log)
        broker.order_rules()
        broker.order_rules()
        broker.order_rules()
        self.assertEqual(log.call_count, 1)
        self.assertIn("401 키가 없어요", log.call_args.args[0])

    def test_mock_prefers_the_inner_order_rules_when_they_are_readable(self):
        log = Mock()
        rules = brokers.MockBroker(self.inner(), log=log).order_rules()
        self.assertEqual(rules, brokers.OrderRules(step=0.001, min_notional=0.0))
        log.assert_not_called()

    def test_mock_exposes_the_same_method_names_as_the_real_brokers(self):
        for real in (brokers.BinanceBroker, brokers.DomesticBroker):
            for name in ("price", "order_rules", "submit", "rehearse", "ensure_ready", "check_account"):
                self.assertTrue(callable(getattr(brokers.MockBroker, name)), name)
                self.assertTrue(callable(getattr(real, name)), name)

    def test_mock_does_not_hand_out_the_inner_order_client(self):
        # 속성을 통째로 넘겨주는 __getattr__ 이 있으면 raw.create_order 같은 길이 열린다.
        broker = brokers.MockBroker(self.inner(), log=Mock())
        self.assertFalse(hasattr(brokers.MockBroker, "__getattr__"))
        self.assertFalse(hasattr(broker, "raw"))
        self.assertFalse(hasattr(broker, "session"))


def binance_inner(market, *, keys=True):
    raw = Mock()
    raw.futures_symbol_ticker.return_value = {"price": "100"}
    raw.get_symbol_ticker.return_value = {"price": "100"}
    raw.futures_exchange_info.return_value = {"symbols": [{"symbol": "BTCUSDT", "filters": [
        {"filterType": "LOT_SIZE", "stepSize": "0.001"}, {"filterType": "MIN_NOTIONAL", "notional": "5"}]}]}
    raw.get_symbol_info.return_value = {"baseAsset": "BTC", "filters": [
        {"filterType": "LOT_SIZE", "stepSize": "0.001"}, {"filterType": "NOTIONAL", "minNotional": "5"}]}
    raw.futures_account_balance.return_value = [{"asset": "USDT", "balance": "1000"}]
    raw.get_account.return_value = {"balances": [{"asset": "USDT", "free": "1000"}]}
    if not keys:  # 서명이 필요한 읽기만 막힌다 — 시세 · 거래소 정보는 공개다
        raw.get_account.side_effect = RuntimeError("APIError(code=-2015): Invalid API-key, IP, or permissions")
        raw.futures_account_balance.side_effect = RuntimeError("APIError(code=-2015)")
    inner = brokers.BinanceBroker(raw, market=market, symbol="BTCUSDT", side="long",
                                  testnet=False, log=Mock())
    return inner, raw


def domestic_inner(exchange, *, keys=True):
    session = Mock()
    chance = {"bid_fee": "0.0005", "ask_fee": "0.0005", "bid": {"min_total": "5000"}}

    def get(url, **_kwargs):
        if "/v1/ticker" in url:
            return FakeResponse([{"trade_price": 100.0}])
        if not keys:
            return FakeResponse({"error": {"name": "no_authorization_ip", "message": "x"}}, status=401)
        if "/v1/accounts" in url:
            return FakeResponse([{"currency": "KRW", "balance": "100000"}])
        if "/v1/orders/chance" in url:
            return FakeResponse(chance)
        return FakeResponse({})

    session.get.side_effect = get
    session.post.return_value = (FakeResponse({}, status=201) if keys else
                                 FakeResponse({"error": {"name": "no_authorization_ip", "message": "x"}}, status=401))
    inner = brokers.DomesticBroker("acc", "sec", exchange=exchange, symbol="KRW-BTC",
                                   log=Mock(), session=session)
    return inner, session


def order_url(exchange):
    return brokers.DOMESTIC_BASES[exchange] + brokers._DOMESTIC_SPECS[exchange]["order_path"]


class MockNeverReachesTheOrderEndpointTests(unittest.TestCase):
    """주문 입구는 어떤 길로도 열리지 않는다. 단언은 호출이 지나간 바로 그 mock 객체(raw · session)에 건다."""

    BINANCE_ORDER_CALLS = ("create_order", "futures_create_order", "order_market_buy", "order_market_sell",
                           "create_test_order")

    def drive(self, broker):
        broker.check_account()
        broker.ensure_ready()
        broker.order_rules()
        broker.rehearse(notional=1000.0)
        self.assertEqual(broker.price(), 100.0)
        buy = broker.submit("BUY", notional=1000.0, client_id="ggp-m1")
        sell = broker.submit("SELL", base_qty=1.0, client_id="ggp-m2")
        self.assertEqual((buy.status, sell.status), ("FILLED", "FILLED"))

    def assert_binance_never_ordered(self, raw):
        called = {call[0] for call in raw.method_calls}
        self.assertTrue({"futures_symbol_ticker", "get_symbol_ticker"} & called, "시세 읽기는 진짜로 나가야 한다")
        for name in self.BINANCE_ORDER_CALLS:
            getattr(raw, name).assert_not_called()
        # 연습이 실계정 설정을 바꾸면 안 된다 — 레버리지는 열린 실제 포지션의 청산가를 움직인다.
        raw.futures_change_margin_type.assert_not_called()
        raw.futures_change_leverage.assert_not_called()

    def assert_domestic_never_ordered(self, session, exchange):
        posted = [call.args[0] for call in session.post.call_args_list]
        self.assertNotIn(order_url(exchange), posted)
        self.assertTrue(any("/v1/ticker" in call.args[0] for call in session.get.call_args_list),
                        "시세 읽기는 진짜로 나가야 한다")

    def test_harness_detects_an_order_when_the_inner_is_called_directly(self):
        # 대조군 — 이 단언이 실제 주문도 잡아내는지. 못 잡으면 아래 시험들은 아무것도 증명하지 못한다.
        inner, session = domestic_inner("upbit")
        inner.submit("BUY", notional=1000.0, client_id="ggp-ctrl")
        self.assertIn(order_url("upbit"), [call.args[0] for call in session.post.call_args_list])
        binance, raw = binance_inner("spot")
        raw.create_order.return_value = {"status": "FILLED", "executedQty": "1"}
        binance.submit("BUY", base_qty=1.0, client_id="ggp-ctrl2")
        raw.create_order.assert_called()

    def test_harness_detects_account_writes_when_the_inner_is_configured_directly(self):
        # 대조군 — 안쪽 ensure_ready 는 실제로 쓴다. 위 단언이 그걸 잡아내야 모의의 무쓰기 증명이 성립한다.
        inner, raw = binance_inner("futures")
        self.assertTrue(inner.ensure_ready())
        raw.futures_change_margin_type.assert_called_once()
        raw.futures_change_leverage.assert_called_once()

    def test_mock_wrapped_futures_never_changes_margin_or_leverage(self):
        inner, raw = binance_inner("futures")
        broker = brokers.MockBroker(inner, log=Mock())
        self.assertTrue(broker.ensure_ready())
        self.assertEqual(broker.order_rules().step, 0.001)  # 쓰기는 빼도 규격 읽기는 남는다
        raw.futures_change_margin_type.assert_not_called()
        raw.futures_change_leverage.assert_not_called()

    def test_binance_spot(self):
        inner, raw = binance_inner("spot")
        self.drive(brokers.MockBroker(inner, log=Mock()))
        self.assert_binance_never_ordered(raw)

    def test_binance_futures(self):
        inner, raw = binance_inner("futures")
        self.drive(brokers.MockBroker(inner, log=Mock()))
        self.assert_binance_never_ordered(raw)

    def test_upbit(self):
        inner, session = domestic_inner("upbit")
        self.drive(brokers.MockBroker(inner, log=Mock()))
        self.assert_domestic_never_ordered(session, "upbit")

    def test_bithumb(self):
        inner, session = domestic_inner("bithumb")
        self.drive(brokers.MockBroker(inner, log=Mock()))
        self.assert_domestic_never_ordered(session, "bithumb")
        self.assertEqual(session.post.call_count, 0, "빗썸은 리허설도 읽기뿐이다")

    def test_the_inner_submit_is_never_called_for_any_exchange(self):
        for inner, _handle in (binance_inner("spot"), binance_inner("futures"),
                               domestic_inner("upbit"), domestic_inner("bithumb")):
            spy = Mock(wraps=inner.submit)
            inner.submit = spy  # 래핑 전에 심고, 같은 객체로 단언한다
            self.drive(brokers.MockBroker(inner, log=Mock()))
            spy.assert_not_called()


class MockFollowsEachAdaptersFeeModelTests(unittest.TestCase):
    def test_binance_spot_takes_the_fee_from_the_base_asset(self):
        inner, _raw = binance_inner("spot")
        order = brokers.MockBroker(inner, log=Mock()).submit("BUY", base_qty=3.0, client_id="ggp-f1")
        self.assertAlmostEqual(order.acquired_qty, 3.0 * (1 - 0.0005))

    def test_binance_futures_keeps_the_quantity(self):
        inner, _raw = binance_inner("futures")
        order = brokers.MockBroker(inner, log=Mock()).submit("BUY", base_qty=3.0, client_id="ggp-f2")
        self.assertEqual(order.acquired_qty, 3.0)

    def test_domestic_takes_the_fee_from_krw_so_quantity_is_unchanged(self):
        for exchange in ("upbit", "bithumb"):
            inner, _session = domestic_inner(exchange)
            inner.ensure_ready()  # 수수료율 0.0005 가 읽힌 상태에서도 수량은 깎이지 않는다
            order = brokers.MockBroker(inner, log=Mock()).submit("BUY", notional=1000.0, client_id="ggp-f3")
            self.assertEqual(order.executed_qty, 10.0)
            self.assertEqual(order.acquired_qty, order.executed_qty, exchange)


class MockRunsWithoutKeysTests(unittest.TestCase):
    """키가 없어도(국내는 비공개 읽기가 전부 401) 연습은 돌아야 한다 — 그래서 모의가 연습 모드다."""

    def test_keyless_domestic_still_practises(self):
        for exchange in ("upbit", "bithumb"):
            inner, session = domestic_inner(exchange, keys=False)
            broker = brokers.MockBroker(inner, log=Mock())
            with self.assertRaises(Exception):  # 대조 — 안쪽은 정말 키 없이는 실패한다
                inner.check_account()
            with self.assertRaises(Exception):
                inner.ensure_ready()
            broker.check_account()
            self.assertTrue(broker.ensure_ready(), exchange)
            ok, reason = broker.rehearse(notional=1000.0)
            self.assertTrue(ok, exchange)
            self.assertTrue(reason, exchange)
            self.assertEqual(broker.order_rules(), brokers.OrderRules(step=0.0, min_notional=0.0))
            self.assertEqual(broker.price(), 100.0)
            order = broker.submit("BUY", notional=1000.0, client_id="ggp-k1")
            self.assertEqual((order.status, order.executed_qty), ("FILLED", 10.0))
            self.assertNotIn(order_url(exchange), [call.args[0] for call in session.post.call_args_list])


    def test_keyless_binance_mainnet_still_practises(self):
        for market in ("spot", "futures"):
            inner, raw = binance_inner(market, keys=False)
            broker = brokers.MockBroker(inner, log=Mock())
            if market == "spot":
                with self.assertRaises(Exception):  # 대조 — 현물 계정 확인은 서명이 필요해 실패한다
                    inner.check_account()
            broker.check_account()
            self.assertTrue(broker.ensure_ready(), market)
            ok, reason = broker.rehearse(notional=1000.0)
            self.assertTrue(ok, market)
            self.assertTrue(reason, market)
            self.assertEqual(broker.price(), 100.0)
            order = broker.submit("BUY", base_qty=1.0, client_id="ggp-k2")
            self.assertEqual(order.status, "FILLED", market)
            raw.create_order.assert_not_called()
            raw.futures_create_order.assert_not_called()
            raw.futures_change_leverage.assert_not_called()


if __name__ == "__main__":
    unittest.main()
