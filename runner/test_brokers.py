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
