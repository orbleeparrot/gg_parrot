import unittest
from unittest.mock import Mock, patch

from runner.test_macro_runner_single_instance import macro_runner


class RunnerExecutionTests(unittest.TestCase):
    def setUp(self):
        sleeper = patch.object(macro_runner.time, "sleep")
        sleeper.start()
        self.addCleanup(sleeper.stop)

    def bot(self):
        bot = object.__new__(macro_runner.BotThread)
        bot.client = Mock()
        bot.market, bot.symbol, bot.side = "futures", "BTCUSDT", "long"
        bot.leverage = 1
        bot.log = Mock()
        bot.in_position, bot.held_qty, bot.entry_price = True, 2.0, 100.0
        bot.realized, bot.step = 0.0, 0.001
        bot._price = Mock(return_value=105.0)
        return bot

    def prepare_run(self, bot):
        bot._connect = Mock(return_value=True)
        bot._prepare = Mock(return_value=True)
        bot.macro = {"rule_type": "A", "params": {"initial_capital": 1000, "take_profit_pct": 3}, "risk": {"invest_ratio": .5}}
        bot._get_command = Mock(side_effect=[None, "stop_only"])
        bot.set_command = Mock()
        bot._sleep = Mock()
        bot.server = Mock()
        bot.server.heartbeat.return_value = "continue"
        bot.on_status, bot.on_finish = Mock(), Mock()

    def test_ack_is_not_a_confirmed_fill(self):
        bot = self.bot()
        bot.client.futures_create_order.return_value = {"orderId": 1, "status": "NEW"}
        bot.client.futures_get_order.return_value = {"orderId": 1, "status": "NEW"}
        with self.assertRaises(RuntimeError):
            bot._close_position()
        self.assertTrue(bot.in_position)
        self.assertEqual(bot.client.futures_create_order.call_count, 1)

    def test_uncertain_submission_is_reconciled_without_second_order(self):
        bot = self.bot()
        bot.client.futures_create_order.side_effect = TimeoutError()
        bot.client.futures_get_order.return_value = {"status": "FILLED", "executedQty": "2", "avgPrice": "105"}
        self.assertTrue(bot._close_position())
        self.assertFalse(bot.in_position)
        self.assertEqual(bot.client.futures_create_order.call_count, 1)

    def test_close_records_fill_and_clears_quantity(self):
        bot = self.bot()
        bot.client.futures_create_order.return_value = {"status": "FILLED", "executedQty": "2", "avgPrice": "106"}
        self.assertEqual(bot._finish_position("close_and_stop"), "청산 완료 후 종료")
        self.assertEqual(bot.held_qty, 0)
        self.assertEqual(bot.realized, 12)

    def test_entry_snapshot_uses_exchange_fill_instead_of_quote_and_requested_qty(self):
        bot = self.bot()
        bot.in_position, bot.held_qty, bot.entry_price = False, 0, 0
        self.prepare_run(bot)
        bot.client.futures_create_order.return_value = {"status": "FILLED", "executedQty": "0.75", "avgPrice": "106"}
        with patch.object(macro_runner, "_should_enter", return_value=True):
            bot.run()
        snapshot = bot.server.stopped.call_args.kwargs["snapshot"]
        self.assertTrue(snapshot["in_position"])
        self.assertEqual(snapshot["entry_price"], 106)
        self.assertEqual(snapshot["position_qty"], .75)

    def test_automatic_exit_uses_confirmed_fill_for_final_pnl(self):
        bot = self.bot()
        self.prepare_run(bot)
        bot.client.futures_create_order.return_value = {"status": "FILLED", "executedQty": "2", "avgPrice": "106"}
        with patch.object(macro_runner, "_should_exit", return_value=True):
            bot.run()
        snapshot = bot.server.stopped.call_args.kwargs["snapshot"]
        self.assertFalse(snapshot["in_position"])
        self.assertEqual(snapshot["position_qty"], 0)
        self.assertEqual(snapshot["realized_pnl"], 12)

    def test_ambiguous_entry_never_reports_flat_and_never_submits_again(self):
        bot = self.bot()
        bot.in_position, bot.held_qty, bot.entry_price = False, 0, 0
        self.prepare_run(bot)
        bot.client.futures_create_order.side_effect = TimeoutError()
        bot.client.futures_get_order.side_effect = TimeoutError()
        with patch.object(macro_runner, "_should_enter", return_value=True):
            bot.run()
        self.assertEqual(bot.server.stopped.call_args.args[0], "error")
        snapshot = bot.server.stopped.call_args.kwargs["snapshot"]
        self.assertTrue(snapshot["in_position"])
        self.assertTrue(snapshot["position_uncertain"])
        self.assertEqual(snapshot["position_qty"], 0)
        self.assertEqual(bot.client.futures_create_order.call_count, 1)
        self.assertEqual(bot.client.futures_get_order.call_count, macro_runner.MAX_RETRIES)
        client_id = bot.client.futures_create_order.call_args.kwargs["newClientOrderId"]
        self.assertTrue(all(call.kwargs["origClientOrderId"] == client_id for call in bot.client.futures_get_order.call_args_list))

    def test_partial_entry_retains_confirmed_quantity_and_pending_risk(self):
        bot = self.bot()
        bot.in_position, bot.held_qty, bot.entry_price = False, 0, 0
        bot.client.futures_create_order.return_value = {"status": "PARTIALLY_FILLED", "executedQty": ".75", "avgPrice": "106"}
        bot.client.futures_get_order.side_effect = TimeoutError()
        with self.assertRaises(RuntimeError):
            bot._place("BUY", 2)
        self.assertEqual(bot.held_qty, .75)
        self.assertEqual(bot.entry_price, 106)
        self.assertTrue(bot.position_uncertain)
        with self.assertRaises(RuntimeError):
            bot._close_position()
        self.assertEqual(bot.client.futures_create_order.call_count, 1)

    def test_partial_terminal_close_records_only_confirmed_execution(self):
        bot = self.bot()
        bot.client.futures_create_order.return_value = {"status": "EXPIRED", "executedQty": ".75", "avgPrice": "106"}
        with self.assertRaises(RuntimeError):
            bot._close_position()
        self.assertEqual(bot.held_qty, 1.25)
        self.assertEqual(bot.entry_price, 100)
        self.assertEqual(bot.realized, 4.5)
        self.assertTrue(bot.in_position)
        self.assertFalse(bot.position_uncertain)

    def test_ambiguous_close_preserves_last_known_position_and_pnl(self):
        bot = self.bot()
        bot.client.futures_create_order.side_effect = TimeoutError()
        bot.client.futures_get_order.side_effect = TimeoutError()
        with self.assertRaises(RuntimeError):
            bot._close_position()
        self.assertEqual(bot.held_qty, 2)
        self.assertEqual(bot.entry_price, 100)
        self.assertEqual(bot.realized, 0)
        self.assertTrue(bot.in_position)
        self.assertTrue(bot.position_uncertain)

    def test_market_order_reconciliation_waits_for_fill_without_resubmission(self):
        bot = self.bot()
        bot.client.futures_create_order.return_value = {"status": "NEW"}
        bot.client.futures_get_order.side_effect = [
            {"status": "NEW"},
            {"status": "FILLED", "executedQty": "2", "avgPrice": "106"},
        ]
        self.assertTrue(bot._close_position())
        self.assertEqual(bot.realized, 12)
        self.assertEqual(bot.client.futures_create_order.call_count, 1)

    def test_spot_fill_uses_confirmed_quote_quantity(self):
        bot = self.bot()
        bot.market = "spot"
        bot.in_position, bot.held_qty, bot.entry_price = False, 0, 0
        bot.client.create_order.return_value = {"status": "FILLED", "executedQty": ".75", "cummulativeQuoteQty": "79.5", "fills": [{"qty": ".75", "commission": "0.0001", "commissionAsset": "BNB"}]}
        self.assertTrue(bot._place("BUY", .75))
        self.assertEqual(bot.entry_price, 106)
        self.assertEqual(bot.held_qty, .75)

    def test_spot_base_fee_is_deducted_and_close_reports_unsellable_dust(self):
        bot = self.bot()
        bot.market = "spot"
        bot.in_position, bot.held_qty, bot.entry_price = False, 0, 0
        bot.client.create_order.side_effect = [
            {"status": "FILLED", "executedQty": ".75", "cummulativeQuoteQty": "79.5", "fills": [{"qty": ".75", "commission": ".00075", "commissionAsset": "BTC"}]},
            {"status": "FILLED", "executedQty": ".749", "cummulativeQuoteQty": "80.892"},
        ]
        self.assertTrue(bot._place("BUY", .75))
        self.assertAlmostEqual(bot.held_qty, .74925)
        self.assertTrue(bot._close_position())
        self.assertAlmostEqual(bot.client.create_order.call_args.kwargs["quantity"], .749)
        self.assertFalse(bot.in_position)
        self.assertAlmostEqual(bot.held_qty, .00025)
        self.assertAlmostEqual(bot.position_dust_qty, .00025)
        self.assertAlmostEqual(bot._snapshot()["position_qty"], .00025)
        self.assertIn("잔여 수량", bot._snapshot()["note"])
        self.assertAlmostEqual(bot.realized, .749 * (108 - 106))

    def test_reconciled_spot_buy_fetches_only_its_order_fees(self):
        bot = self.bot()
        bot.market = "spot"
        bot.in_position, bot.held_qty, bot.entry_price = False, 0, 0
        bot.client.create_order.side_effect = TimeoutError()
        bot.client.get_order.return_value = {"orderId": 42, "status": "FILLED", "executedQty": ".75", "cummulativeQuoteQty": "79.5"}
        bot.client.get_my_trades.return_value = [
            {"orderId": 42, "qty": ".75", "commission": ".00075", "commissionAsset": "BTC"},
            {"orderId": 99, "qty": "10", "commission": ".01", "commissionAsset": "BTC"},
        ]
        self.assertTrue(bot._place("BUY", .75))
        self.assertAlmostEqual(bot.held_qty, .74925)
        bot.client.get_my_trades.assert_called_once_with(symbol="BTCUSDT", orderId=42, limit=1000)
        self.assertEqual(bot.client.create_order.call_count, 1)

    def test_missing_spot_fee_confirmation_preserves_position_risk(self):
        bot = self.bot()
        bot.market = "spot"
        bot.in_position, bot.held_qty, bot.entry_price = False, 0, 0
        bot.client.create_order.return_value = {"orderId": 42, "status": "FILLED", "executedQty": ".75", "cummulativeQuoteQty": "79.5"}
        bot.client.get_my_trades.side_effect = TimeoutError()
        with self.assertRaises(RuntimeError):
            bot._place("BUY", .75)
        self.assertTrue(bot.position_uncertain)
        self.assertTrue(bot.in_position)
        self.assertEqual(bot.held_qty, .75)

    def test_price_outage_still_receives_remote_stop(self):
        bot = self.bot()
        bot._connect = Mock(return_value=True)
        bot._prepare = Mock(return_value=True)
        bot.macro = {"rule_type": "A", "params": {"initial_capital": 1000, "take_profit_pct": 3}, "risk": {"invest_ratio": .5}}
        bot._get_command = Mock(side_effect=[None, "stop_only"])
        bot.set_command = Mock()
        bot._price.side_effect = RuntimeError("price source offline")
        bot._sleep = Mock()
        bot.server = Mock()
        bot.server.heartbeat.return_value = "stop_only"
        bot.on_status, bot.on_finish = Mock(), Mock()
        bot.run()
        bot.server.heartbeat.assert_called_once()
        bot.set_command.assert_called_once_with("stop_only")
        bot.server.stopped.assert_called_once()


class RunnerTerminalReportTests(unittest.TestCase):
    def client(self):
        client = macro_runner.ServerClient("test-key", base="https://example.invalid")
        client.session_id = 42
        return client

    def test_retries_http_failure_with_identical_final_snapshot(self):
        failed = Mock(status_code=503)
        failed.raise_for_status.side_effect = RuntimeError("deploy in progress")
        complete = Mock(status_code=200)
        snapshot = {"in_position": False, "realized_pnl": 12}
        with patch.object(macro_runner.requests, "post", side_effect=[failed, complete]) as post, patch.object(macro_runner.time, "sleep"):
            self.assertTrue(self.client().stopped(snapshot=snapshot))
        self.assertEqual(post.call_count, 2)
        self.assertEqual(post.call_args_list[0], post.call_args_list[1])

    def test_unreachable_terminal_report_has_bounded_retries(self):
        with patch.object(macro_runner.requests, "post", side_effect=TimeoutError()) as post, patch.object(macro_runner.time, "sleep"):
            self.assertFalse(self.client().stopped(snapshot={"in_position": True}))
        self.assertEqual(post.call_count, macro_runner.MAX_RETRIES)

    def test_permanent_auth_failure_is_not_retried(self):
        with patch.object(macro_runner.requests, "post", return_value=Mock(status_code=401)) as post:
            self.assertFalse(self.client().stopped())
        self.assertEqual(post.call_count, 1)
