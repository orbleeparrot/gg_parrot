import unittest

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
