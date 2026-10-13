"""Telemetry only: fixtures cannot access an exchange or a real server."""
import unittest
from unittest.mock import Mock, patch

from runner.test_macro_runner_single_instance import macro_runner


class StructuredLogTests(unittest.TestCase):
    def test_retry_preserves_event_id_and_metadata_snapshot(self):
        client = macro_runner.ServerClient("test-key", base="https://example.invalid")
        client.session_id = 1
        details = {"executed_qty": 2, "fill_price": 12.6}
        client.push_event("fill", "주문 결과 보고", severity="INFO", event_name="runner.order_result",
                          operation_id="command:7", details=details)
        details["executed_qty"] = 999
        with patch.object(macro_runner.requests, "post", side_effect=TimeoutError()) as failed:
            client.heartbeat({})
            original = failed.call_args.kwargs["json"]["events"][0]
        response = Mock()
        response.json.return_value = {}
        with patch.object(macro_runner.requests, "post", return_value=response) as retried:
            client.heartbeat({})
            repeated = retried.call_args.kwargs["json"]["events"][0]
        self.assertEqual(original, repeated)
        self.assertTrue(original["event_key"].startswith("runner:"))
        self.assertEqual(original["details"]["executed_qty"], 2)
        self.assertEqual(original["operation_id"], "command:7")

    def test_repeated_messages_are_distinct_emissions_not_string_deduplication(self):
        client = macro_runner.ServerClient("test-key")
        client.push_event("info", "대기")
        client.push_event("info", "대기")
        first, second = client._drain_events()
        self.assertNotEqual(first["event_key"], second["event_key"])
        self.assertEqual(first["severity"], "UNKNOWN")

    def test_log_metadata_does_not_change_windows_message(self):
        bot = object.__new__(macro_runner.BotThread)
        bot.server, bot.on_log = Mock(), Mock()
        bot.log("기존 매매 표시", kind="fill", severity="INFO", event_name="runner.order_result",
                details={"executed_qty": 2})
        bot.on_log.assert_called_once_with("기존 매매 표시")
        self.assertEqual(bot.server.push_event.call_args.args, ("fill", "기존 매매 표시"))
        self.assertEqual(bot.server.push_event.call_args.kwargs["details"], {"executed_qty": 2})

    def test_telemetry_exception_never_escapes_the_trading_log(self):
        bot = object.__new__(macro_runner.BotThread)
        bot.server, bot.on_log = Mock(), Mock()
        bot.server.push_event.side_effect = RuntimeError("sink down")
        bot.log("기존 매매 표시", severity="INFO", event_name="runner.order_result")
        bot.on_log.assert_called_once_with("기존 매매 표시")

    def _bot(self):
        server = macro_runner.ServerClient("test-member-key", base="https://example.invalid")
        bot = macro_runner.BotThread(
            {"symbol": "BTCUSDT", "exchange": "binance", "position_side": "long", "leverage": 1,
             "params": {"initial_capital": 100}},
            {"member_key": "test-member-key", "exchanges": {
                "binance": {"api_key": "private-exchange-key", "api_secret": "private-exchange-secret"}}},
            "mock", server, Mock(), Mock(), Mock(),
        )
        bot.broker = Mock()
        bot.broker.submit.return_value = macro_runner.brokers.Order(
            status="FILLED", executed_qty=2, avg_price=12.6, acquired_qty=1.99,
            fees_known=True, order_id="mock-order-7", raw_status="MOCK_FILLED",
        )
        return bot

    def test_actual_adapter_request_result_are_linked_without_extra_windows_messages(self):
        bot = self._bot()
        self.assertTrue(bot._place("BUY", 2))
        events = bot.server._drain_events()
        request, result = events[:2]
        self.assertEqual(request["event_name"], "runner.order_requested")
        self.assertEqual(result["event_name"], "runner.order_result")
        self.assertEqual(request["operation_id"], result["operation_id"])
        self.assertTrue(request["operation_id"].startswith("order:ggp-"))
        self.assertEqual(request["details"]["requested_qty"], 2)
        self.assertNotIn("requested_notional", request["details"])
        self.assertEqual(result["details"]["exchange_order_id"], "mock-order-7")
        self.assertEqual(result["details"]["acquired_qty"], 1.99)
        self.assertTrue(result["details"]["fees_known"])
        self.assertNotIn("fee", result["details"])
        bot.broker.submit.assert_called_once()
        bot.on_log.assert_called_once()  # Original successful fill text only.

    def test_broken_telemetry_never_causes_second_order_or_changes_fill_state(self):
        bot = self._bot()
        bot.server.push_event = Mock(side_effect=RuntimeError("log sink down"))
        self.assertTrue(bot._place("BUY", 2))
        bot.broker.submit.assert_called_once()
        self.assertAlmostEqual(bot.held_qty, 1.99)
        self.assertAlmostEqual(bot.entry_price, 12.6)

    def test_adapter_exception_is_logged_then_reraised_without_retrying_order(self):
        bot = self._bot()
        bot.broker.submit.side_effect = RuntimeError("exchange error private-exchange-secret")
        with self.assertRaisesRegex(RuntimeError, "exchange error"):
            bot._place("BUY", 2)
        request, error = bot.server._drain_events()
        self.assertEqual(error["event_name"], "runner.order_failed")
        self.assertEqual(request["operation_id"], error["operation_id"])
        self.assertNotIn("private-exchange-secret", error["details"]["error"])
        bot.broker.submit.assert_called_once()

    def test_command_stages_keep_command_identity_and_no_reexecution_on_duplicate(self):
        bot = self._bot()
        bot._place = Mock(return_value=True)
        bot._last_fill_qty, bot._last_fill_price = 2, 12.6
        command = {"id": 7, "seq": 1, "action": "buy", "notional_frac": .25,
                   "qty_frac": 0, "signal_price": 12.5, "reason": "RSI 23 ≤ 25"}
        self.assertTrue(bot._execute_command(command, 12.5)["ok"])
        events = bot.server._drain_events()
        stages = [event for event in events if event["event_name"] != "runner.message"]
        self.assertEqual([event["event_name"] for event in stages],
                         ["runner.command_received", "runner.command_reported"])
        self.assertTrue(all(event["operation_id"] == "command:7" for event in stages))
        self.assertEqual(stages[1]["details"]["fill_price"], 12.6)
        self.assertTrue(bot._execute_command(command, 12.5)["ok"])
        self.assertEqual(bot.server._drain_events(), [])
        bot._place.assert_called_once()


if __name__ == "__main__":
    unittest.main()
