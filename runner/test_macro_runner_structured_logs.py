"""Telemetry only: fixtures cannot access an exchange or a real server."""
import unittest
from unittest.mock import Mock, patch

from runner.test_macro_runner_single_instance import macro_runner


class StructuredLogTests(unittest.TestCase):
    def take(self, client):
        events = client._peek_events()
        client._ack_events(events, {"accepted_event_keys": [event["event_key"] for event in events]})
        return events

    def test_retry_preserves_event_id_and_metadata_snapshot(self):
        client = macro_runner.ServerClient("test-key", base="https://example.invalid", outbox_path=":memory:", cli_sink=lambda _event: None)
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
        client = macro_runner.ServerClient("test-key", outbox_path=":memory:", cli_sink=lambda _event: None)
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
        server = macro_runner.ServerClient("test-member-key", base="https://example.invalid", outbox_path=":memory:", cli_sink=lambda _event: None)
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
        events = self.take(bot.server)
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
        self.assertEqual(len(events), 2)  # Request and one canonical result, not a second legacy fill.
        self.assertEqual([event["event_name"] for event in events].count("runner.order_result"), 1)
        self.assertFalse(any(event["event_name"] == "runner.message" and event["kind"] == "fill" for event in events))
        self.assertIn("mock-order-7", result["message"])
        self.assertIn("12.6", result["message"])
        bot.broker.submit.assert_called_once()
        bot.on_log.assert_called_once()  # Original successful fill text only.
        self.assertIn("BUY 체결: id=mock-order-7 수량=2", bot.on_log.call_args.args[0])

    def test_plain_log_reuses_explicit_current_command_context_without_guessing_from_text(self):
        bot = self._bot()
        bot._active_log_operation_id = "command:29"
        bot.log("실제 명령 처리 중 기록")
        self.assertEqual(self.take(bot.server)[0]["operation_id"], "command:29")
        bot.log("다른 명시적 작업", operation_id="order:ggp-" + "a" * 28)
        self.assertEqual(self.take(bot.server)[0]["operation_id"], "order:ggp-" + "a" * 28)
        bot._active_log_operation_id = ""
        bot.log("command:999라는 문장만 있는 옛 로그")
        self.assertEqual(self.take(bot.server)[0]["operation_id"], "")

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
        request, error = self.take(bot.server)
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
        events = self.take(bot.server)
        stages = [event for event in events if event["event_name"] != "runner.message"]
        self.assertEqual([event["event_name"] for event in stages],
                         ["runner.command_received", "runner.command_reported"])
        self.assertTrue(all(event["operation_id"] == "command:7" for event in stages))
        self.assertEqual(stages[1]["details"]["fill_price"], 12.6)
        self.assertTrue(bot._execute_command(command, 12.5)["ok"])
        self.assertEqual(bot.server._drain_events(), [])
        bot._place.assert_called_once()

    def test_no_position_and_duplicate_acks_report_no_order_instead_of_zero_fill(self):
        bot = self._bot()
        command = {"id": 19, "seq": 1, "action": "sell", "qty_frac": 1, "reason": "청산"}
        first = bot._execute_command(command, 12.5)
        self.assertTrue(first["ok"])
        self.assertEqual(first["outcome"], "no_position_no_order")
        self.assertEqual(first["mode"], "mock")
        bot.broker.submit.assert_not_called()
        self.take(bot.server)
        repeated = bot._execute_command(command, 12.5)
        self.assertTrue(repeated["ok"])
        self.assertEqual(repeated["outcome"], "duplicate_command_no_reexecution")
        bot.broker.submit.assert_not_called()
        self.assertEqual(bot.server._drain_events(), [])

    def test_executed_ack_explicitly_carries_mode_and_reported_outcome(self):
        bot = self._bot()
        ack = bot._execute_command({"id": 20, "action": "buy", "notional_frac": .25}, 12.5)
        self.assertTrue(ack["ok"])
        self.assertEqual(ack["outcome"], "execution_reported")
        self.assertEqual(ack["mode"], "mock")
        self.assertEqual(ack["executed_qty"], 2)
        self.assertEqual(ack["fill_price"], 12.6)
        report = [event for event in bot.server._drain_events()
                  if event["event_name"] == "runner.command_reported"][0]
        self.assertEqual(report["details"]["outcome"], "execution_reported")
        self.assertEqual(report["details"]["mode"], "mock")
        self.assertIn("모의", report["message"])

    def test_simulated_testnet_and_live_adapter_logs_are_visibly_distinct(self):
        for mode, label in (("mock", "모의"), ("testnet", "테스트넷"), ("live", "실전")):
            with self.subTest(mode=mode):
                bot = self._bot()
                bot.mode = mode
                self.assertTrue(bot._place("BUY", 2))
                request, result = bot.server._drain_events()[:2]
                self.assertIn(label, request["message"])
                self.assertIn(label, result["message"])
                self.assertEqual(result["details"]["mode"], mode)
                self.assertEqual(result["details"]["verification"], "runner_reported_not_exchange_verified")

    def test_missing_instance_mode_is_unknown_not_the_class_safety_default(self):
        bot = self._bot()
        del bot.mode
        self.assertEqual(bot._log_mode(), "")
        bot.mode = "invented_mode"
        self.assertEqual(bot._log_mode(), "")

    def test_legacy_fee_flag_is_scoped_without_changing_reconciliation_risk_logic(self):
        cases = (("mock", "binance", "spot", "simulated_fee_model"),
                 ("live", "upbit", "spot", "domestic_trade_quantity_coverage"),
                 ("live", "bithumb", "spot", "domestic_trade_quantity_coverage"),
                 ("testnet", "binance", "spot", "binance_spot_fill_fee_coverage"),
                 ("live", "binance", "futures", "binance_futures_quantity_only"))
        for mode, exchange, market, scope in cases:
            with self.subTest(mode=mode, exchange=exchange, market=market):
                bot = self._bot()
                bot.mode, bot.exchange, bot.market = mode, exchange, market
                self.assertTrue(bot._place("BUY", 2))
                result = bot.server._drain_events()[1]
                self.assertEqual(result["details"]["reconciliation_scope"], scope)
                self.assertTrue(result["details"]["fees_known"])
                self.assertFalse(bot.position_uncertain)

    def test_adapter_filled_status_without_reconciled_fill_is_not_successful_fill_log(self):
        for status, qty, price, reconciled in (("FILLED", 0, 0, True), ("FILLED", 2, 12.6, False),
                                                ("OPEN", 2, 12.6, True)):
            with self.subTest(status=status, qty=qty, price=price, reconciled=reconciled):
                bot = self._bot()
                bot.broker.submit.return_value = macro_runner.brokers.Order(
                    status=status, executed_qty=qty, avg_price=price, acquired_qty=qty,
                    fees_known=reconciled, order_id="mock-order", raw_status=status)
                with self.assertRaises(RuntimeError):
                    bot._place("BUY", 2)
                result = bot.server._drain_events()[1]
                self.assertEqual(result["kind"], "warn")
                self.assertEqual(result["severity"], "WARN")
                self.assertTrue(result["details"]["position_uncertain"])
                bot.broker.submit.assert_called_once()

    def test_failure_and_uncertain_messages_are_not_classified_as_fills(self):
        for message in ("명령 실행 실패: 체결 완료를 확인하지 못했습니다.",
                        "[청산] 체결 확인 실패", "체결 오류: 응답 누락"):
            with self.subTest(message=message):
                self.assertEqual(macro_runner._event_kind(message), "error")
        self.assertEqual(macro_runner._event_kind("주문 상태 UNKNOWN — 체결 완료를 확인하지 못했습니다."), "warn")
        self.assertEqual(macro_runner._event_kind("  ✓ BUY 체결: id=123 수량=2"), "fill")


if __name__ == "__main__":
    unittest.main()
