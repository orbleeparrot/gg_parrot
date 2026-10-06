"""주문 결과를 사람에게 보고하는 문구와 현물 수수료 분기.

둘 다 브로커로 옮기면서 조용히 달라질 수 있는 자리다. 상태는 판정용으로 접지만
사람이 읽는 문구는 거래소가 쓴 낱말이어야 하고, 수수료 분기는 봇의 '청산인가' 판단을 따라야 한다.
"""
import unittest
from unittest.mock import Mock, patch

from runner.test_macro_runner_single_instance import macro_runner


def _bot(market="futures", *, in_position=True, held=2.0, entry=100.0):
    bot = object.__new__(macro_runner.BotThread)
    bot.client = Mock()  # 날 python-binance 클라이언트
    bot.market, bot.symbol, bot.side = market, "BTCUSDT", "long"
    bot.quote = "USDT"
    bot.leverage = 1
    bot.log = Mock()
    bot.in_position, bot.held_qty, bot.entry_price = in_position, held, entry
    bot.realized, bot.step = 0.0, 0.001
    bot.broker = macro_runner.brokers.BinanceBroker(
        bot.client, market=market, symbol=bot.symbol, side=bot.side, testnet=True, log=bot.log)
    return bot


class OrderStatusReportTests(unittest.TestCase):
    """실패 문구는 로그로만 끝나지 않는다 — 서버 ack 의 error 로 올라가고 사람이 거래소에서 대조한다."""

    def setUp(self):
        sleeper = patch.object(macro_runner.time, "sleep")
        sleeper.start()
        self.addCleanup(sleeper.stop)

    def test_expired_order_is_reported_with_the_exchange_word(self):
        bot = _bot()
        bot.client.futures_create_order.return_value = {"status": "EXPIRED", "executedQty": ".75", "avgPrice": "106"}
        with self.assertRaises(RuntimeError) as caught:
            bot._close_position()
        self.assertIn("주문 상태 EXPIRED", str(caught.exception))
        self.assertNotIn("CANCELED", str(caught.exception))
        self.assertFalse(bot.position_uncertain)  # 판정은 접은 상태(EXPIRED→CANCELED=터미널)를 그대로 쓴다

    def test_unfinished_order_is_reported_with_the_exchange_word(self):
        bot = _bot()
        bot.client.futures_create_order.return_value = {"status": "NEW"}
        bot.client.futures_get_order.return_value = {"status": "PARTIALLY_FILLED", "executedQty": ".5", "avgPrice": "106"}
        with self.assertRaises(RuntimeError) as caught:
            bot._close_position()
        self.assertIn("주문 상태 PARTIALLY_FILLED", str(caught.exception))
        self.assertNotIn("OPEN", str(caught.exception))
        self.assertTrue(bot.position_uncertain)

    def test_unanswered_order_is_reported_as_unknown(self):
        bot = _bot()
        bot.client.futures_create_order.side_effect = TimeoutError()
        bot.client.futures_get_order.side_effect = TimeoutError()
        with self.assertRaises(RuntimeError) as caught:
            bot._close_position()
        self.assertIn("주문 상태 unknown", str(caught.exception))
        self.assertTrue(bot.position_uncertain)


class SpotFeeBranchFollowsTheBotTests(unittest.TestCase):
    def test_spot_sell_with_no_position_still_deducts_the_base_asset_fee(self):
        """봇의 '청산인가' 는 보유를 본다. 주문 방향만 보면 이 매도가 청산으로 접혀 수수료가 안 빠진다.

        _place 가 브로커에 closing 을 넘기는 유일한 이유라서, 그 인자를 지우면 이 시험이 깨져야 한다.
        """
        bot = _bot(market="spot", in_position=False, held=0.0, entry=0.0)
        bot.client.create_order.return_value = {
            "orderId": 5, "status": "FILLED", "executedQty": "2", "avgPrice": "100",
            "fills": [{"price": "100", "qty": "2", "commission": "0.002", "commissionAsset": "BTC"}],
        }
        self.assertTrue(bot._place("SELL", 2.0))
        self.assertAlmostEqual(bot.held_qty, 1.998)  # 체결 2 − 기초자산 수수료 0.002
        self.assertAlmostEqual(bot._last_fill_qty, 2.0)
        self.assertFalse(bot.position_uncertain)


if __name__ == "__main__":
    unittest.main()
