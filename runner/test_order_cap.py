import unittest

from collections import deque
from unittest.mock import Mock

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
        # 예산 1,000,000 원 → 상한 150,000 원에 걸려 정확히 0.01 개. quote 를 무시하면 100 원(6.67e-6 개)이 된다.
        qty, notional = macro_runner._order_qty(
            15_000_000.0, 1e-8, 0, 1_000_000.0, 1, "spot", quote="KRW")
        self.assertAlmostEqual(qty, 0.01, places=9)
        self.assertAlmostEqual(notional, macro_runner.MAX_ORDER_KRW, places=6)

    def test_usdt_entry_quantity_is_unchanged(self):
        qty, notional = macro_runner._order_qty(100.0, 0.001, 0, 1000.0, 1, "spot")
        self.assertEqual(qty, 1.0)
        self.assertEqual(notional, 100.0)


class DomesticCapWiringTests(unittest.TestCase):
    def test_command_entry_in_krw_is_capped_at_the_krw_limit(self):
        # 서버 명령 진입 경로(_execute_command). 여기서 USDT 상한을 쓰면 원화 주문이 100 원어치로 줄어 전부 거절된다.
        bot = object.__new__(macro_runner.BotThread)
        bot.client = Mock()
        bot.market, bot.symbol, bot.side, bot.quote = "spot", "KRW-BTC", "long", "KRW"
        bot.exchange = "upbit"  # 금액 주문 여부는 거래소로 갈린다
        bot.leverage = 1
        bot.log = Mock()
        bot.in_position, bot.held_qty, bot.entry_price = False, 0.0, 0.0
        bot.realized, bot.step = 0.0, 1e-8
        bot.position_uncertain = False
        bot.capital = 150000.0
        bot._done_command_ids = deque(maxlen=200)
        bot.pending_acks = []
        bot._place = Mock(return_value=True)
        bot._last_fill_qty, bot._last_fill_price = 0.01, 15_000_000.0
        ack = bot._execute_command(
            {"id": 1, "action": "buy", "notional_frac": 1.0, "qty_frac": 0.0, "reason": "진입"},
            price=15_000_000.0)
        self.assertTrue(ack["ok"], ack)
        side, qty = bot._place.call_args.args[:2]
        self.assertEqual(side, "BUY")
        self.assertAlmostEqual(qty * 15_000_000.0, 150000.0, places=3)

    def test_risk_guard_base_falls_back_to_the_given_cap(self):
        self.assertEqual(macro_runner.RiskGuard({}, 0, cap=150000).base, 150000)
        self.assertEqual(macro_runner.RiskGuard({}, 0).base, macro_runner.MAX_ORDER_USDT)
        self.assertEqual(macro_runner.RiskGuard({}, 5000, cap=150000).base, 5000)

    def _new_bot(self, macro):
        keys = {"exchanges": {"binance": {"api_key": "k", "api_secret": "s"}}}
        return macro_runner.BotThread(macro, keys, "mock", Mock(), Mock(), Mock(), Mock())

    def test_bot_thread_reads_quote_and_default_capital_from_symbol(self):
        krw = self._new_bot({"symbol": "krw-btc"})
        self.assertEqual(krw.symbol, "KRW-BTC")
        self.assertEqual(krw.quote, "KRW")
        self.assertEqual(krw.capital, macro_runner.MAX_ORDER_KRW)
        usdt = self._new_bot({"symbol": "BTCUSDT"})
        self.assertEqual(usdt.quote, "USDT")
        self.assertEqual(usdt.capital, macro_runner.MAX_ORDER_USDT)
        explicit = self._new_bot({"symbol": "KRW-BTC", "params": {"initial_capital": 500000}})
        self.assertEqual(explicit.capital, 500000.0)
