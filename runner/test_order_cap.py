import unittest

from runner.test_macro_runner_single_instance import macro_runner


class OrderCapTests(unittest.TestCase):
    def test_krw_cap_is_not_the_usdt_number(self):
        # 100 원은 업비트 최소 주문 금액(5,000 원) 미만이라 전부 거절된다.
        self.assertEqual(macro_runner.order_cap("USDT"), macro_runner.MAX_ORDER_USDT)
        self.assertEqual(macro_runner.order_cap("KRW"), macro_runner.MAX_ORDER_KRW)
        self.assertGreater(macro_runner.order_cap("KRW"), 5000)

    def test_quote_is_read_from_the_symbol(self):
        self.assertEqual(macro_runner.quote_of("BTCUSDT"), "USDT")
        self.assertEqual(macro_runner.quote_of("KRW-BTC"), "KRW")

    def test_unknown_quote_falls_back_to_usdt_cap(self):
        self.assertEqual(macro_runner.order_cap("???"), macro_runner.MAX_ORDER_USDT)

    def test_krw_entry_quantity_uses_the_krw_cap(self):
        # 15,000,000 원짜리 코인을 사면 수량은 작지만 0 이 아니어야 한다.
        qty, notional = macro_runner._order_qty(
            15_000_000.0, 1e-8, 0, 1_000_000.0, 1, "spot", quote="KRW")
        self.assertGreater(qty, 0)
        self.assertLessEqual(notional, macro_runner.MAX_ORDER_KRW)

    def test_usdt_entry_quantity_is_unchanged(self):
        qty, notional = macro_runner._order_qty(100.0, 0.001, 0, 1000.0, 1, "spot")
        self.assertEqual(qty, 1.0)
        self.assertEqual(notional, 100.0)
