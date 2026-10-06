import unittest
from unittest.mock import Mock

from runner import brokers


class OrderShapeTests(unittest.TestCase):
    def test_order_carries_fill_and_fee_confidence(self):
        order = brokers.Order(status="FILLED", executed_qty=2.0, avg_price=105.0,
                              acquired_qty=1.998, fees_known=True)
        self.assertEqual(order.status, "FILLED")
        self.assertEqual(order.acquired_qty, 1.998)
        self.assertTrue(order.fees_known)

    def test_unknown_order_is_not_terminal(self):
        self.assertIn("FILLED", brokers.TERMINAL_STATUSES)
        self.assertIn("CANCELED", brokers.TERMINAL_STATUSES)
        self.assertIn("REJECTED", brokers.TERMINAL_STATUSES)
        self.assertNotIn("OPEN", brokers.TERMINAL_STATUSES)

    def test_order_rules_default_to_no_minimum(self):
        rules = brokers.OrderRules(step=0.001, min_notional=0.0)
        self.assertEqual(rules.step, 0.001)
        self.assertEqual(rules.min_notional, 0.0)


class BinanceBrokerTests(unittest.TestCase):
    def broker(self, market="spot"):
        self.raw = Mock()
        return brokers.BinanceBroker(self.raw, market=market, symbol="BTCUSDT",
                                     side="long", testnet=True, log=Mock())

    def test_spot_base_fee_is_deducted_from_acquired(self):
        broker = self.broker()
        self.raw.create_order.return_value = {
            "orderId": 7, "status": "FILLED", "executedQty": "2", "avgPrice": "100",
            "fills": [{"price": "100", "qty": "2", "commission": "0.002", "commissionAsset": "BTC"}],
        }
        order = broker.submit("BUY", base_qty=2.0, client_id="ggp-1")
        self.assertEqual(order.executed_qty, 2.0)
        self.assertAlmostEqual(order.acquired_qty, 1.998)
        self.assertTrue(order.fees_known)

    def test_timeout_is_reconciled_by_client_id_without_a_second_order(self):
        broker = self.broker(market="futures")
        self.raw.futures_create_order.side_effect = TimeoutError()
        self.raw.futures_get_order.return_value = {"status": "FILLED", "executedQty": "2", "avgPrice": "105"}
        order = broker.submit("SELL", base_qty=2.0, client_id="ggp-2")
        self.assertEqual(order.status, "FILLED")
        self.assertEqual(self.raw.futures_create_order.call_count, 1)
        self.raw.futures_get_order.assert_called_with(symbol="BTCUSDT", origClientOrderId="ggp-2")

    def test_missing_fee_confirmation_is_reported_not_guessed(self):
        broker = self.broker()
        self.raw.create_order.return_value = {
            "orderId": 8, "status": "FILLED", "executedQty": "2", "avgPrice": "100", "fills": [],
        }
        self.raw.get_my_trades.return_value = []
        order = broker.submit("BUY", base_qty=2.0, client_id="ggp-3")
        self.assertFalse(order.fees_known)


class FuturesAccountSetupTests(unittest.TestCase):
    """선물 준비는 제품에서 계정을 가장 크게 바꾸는 두 호출이다 — 격리 마진과 레버리지.

    조용히 안 불리면 아무것도 빨개지지 않고 모든 선물 세션이 계정 기본 레버리지로 거래된다.
    그래서 '부른다' 가 아니라 '어떤 인자로 부른다' 까지 못박는다(병합 기준 코드와 같은 인자다).
    """

    SYMBOL_INFO = {"symbols": [{"symbol": "BTCUSDT", "filters": [
        {"filterType": "LOT_SIZE", "stepSize": "0.001"},
        {"filterType": "MIN_NOTIONAL", "minNotional": "5"}]}]}

    def broker(self, *, leverage):
        self.raw = Mock()
        self.raw.futures_exchange_info.return_value = self.SYMBOL_INFO
        return brokers.BinanceBroker(self.raw, market="futures", symbol="BTCUSDT",
                                     side="long", testnet=True, log=Mock(), leverage=leverage)

    def test_futures_prepare_sets_isolated_margin_and_the_macro_leverage(self):
        broker = self.broker(leverage=7)
        self.assertTrue(broker.ensure_ready())
        self.raw.futures_change_margin_type.assert_called_once_with(symbol="BTCUSDT", marginType="ISOLATED")
        self.raw.futures_change_leverage.assert_called_once_with(symbol="BTCUSDT", leverage=7)

    def test_leverage_is_still_set_when_the_margin_type_is_already_isolated(self):
        # 이미 ISOLATED 면 거래소가 거절한다 — 원하는 상태라 넘어가지만 레버리지는 반드시 남아야 한다.
        broker = self.broker(leverage=3)
        self.raw.futures_change_margin_type.side_effect = RuntimeError("No need to change margin type.")
        self.assertTrue(broker.ensure_ready())
        self.raw.futures_change_leverage.assert_called_once_with(symbol="BTCUSDT", leverage=3)

    def test_a_failed_leverage_change_is_reported_not_swallowed_in_silence(self):
        broker = self.broker(leverage=5)
        self.raw.futures_change_leverage.side_effect = RuntimeError("nope")
        self.assertTrue(broker.ensure_ready())
        self.assertTrue(any("레버리지" in str(call) for call in broker.log.call_args_list))

    def test_spot_never_touches_margin_or_leverage(self):
        raw = Mock()
        raw.get_symbol_info.return_value = {"baseAsset": "BTC", "filters": []}
        spot = brokers.BinanceBroker(raw, market="spot", symbol="BTCUSDT", side="long",
                                     testnet=True, log=Mock(), leverage=5)
        self.assertTrue(spot.ensure_ready())
        raw.futures_change_margin_type.assert_not_called()
        raw.futures_change_leverage.assert_not_called()
