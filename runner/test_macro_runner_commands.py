"""실행기 v8 — 서버 명령만 실행하고 결과를 ack 로 돌려준다. 스스로 진입/청산을 판단하지 않는다."""
import unittest
from collections import deque
from unittest.mock import Mock, patch

from runner.test_macro_runner_single_instance import macro_runner


def _bot(in_position=False, held=0.0, entry=0.0):
    bot = object.__new__(macro_runner.BotThread)
    bot.client = Mock()
    bot.market, bot.symbol, bot.side = "spot", "ONEUSDT", "long"
    bot.leverage = 1
    bot.log = Mock()
    bot.in_position, bot.held_qty, bot.entry_price = in_position, held, entry
    bot.realized, bot.step = 0.0, 1.0
    bot.position_uncertain = False
    bot.macro = {"rule_type": "F", "params": {"initial_capital": 32}, "risk": {"invest_ratio": 1.0, "stop_loss_pct": 5}}
    bot.capital = 32.0
    bot._done_command_ids = deque(maxlen=200)
    bot.pending_acks = []
    return bot


class CommandExecutionTests(unittest.TestCase):
    def setUp(self):
        patch.object(macro_runner.time, "sleep").start()
        self.addCleanup(patch.stopall)

    def test_buy_command_spends_capital_fraction(self):
        bot = _bot()
        bot._place = Mock(side_effect=lambda side, qty, reduce_only=False: (setattr(bot, "_last_fill_qty", qty), setattr(bot, "_last_fill_price", 0.01), True)[-1])
        ack = bot._execute_command({"id": 5, "action": "buy", "notional_frac": 0.5, "qty_frac": 0.0, "reason": "RSI 20 ≤ 25 · 진입"}, price=0.01)
        bot._place.assert_called_once_with("BUY", 1600.0)  # 32 * 0.5 / 0.01
        self.assertEqual(ack["command_id"], 5)
        self.assertTrue(ack["ok"])
        self.assertEqual(ack["executed_qty"], 1600.0)

    def test_sell_command_uses_fraction_of_held(self):
        bot = _bot(in_position=True, held=1000.0, entry=0.01)
        bot._place = Mock(return_value=True)
        bot._last_fill_qty, bot._last_fill_price = 500.0, 0.012
        bot._execute_command({"id": 6, "action": "sell", "notional_frac": 0.0, "qty_frac": 0.5, "reason": "부분 청산"}, price=0.012)
        bot._place.assert_called_once_with("SELL", 500.0, reduce_only=False)

    def test_full_exit_uses_close_position(self):
        bot = _bot(in_position=True, held=1000.0, entry=0.01)
        bot._close_position = Mock(return_value=True)
        bot._last_fill_qty, bot._last_fill_price = 1000.0, 0.013
        ack = bot._execute_command({"id": 7, "action": "sell", "notional_frac": 0.0, "qty_frac": 1.0, "reason": "청산"}, price=0.013)
        bot._close_position.assert_called_once_with()
        self.assertTrue(ack["ok"])

    def test_exit_when_flat_is_a_noop_ack(self):
        bot = _bot()
        bot._place = Mock()
        ack = bot._execute_command({"id": 8, "action": "sell", "notional_frac": 0.0, "qty_frac": 1.0, "reason": "청산"}, price=0.013)
        bot._place.assert_not_called()
        self.assertTrue(ack["ok"])
        self.assertEqual(ack["executed_qty"], 0.0)

    def test_duplicate_command_id_is_not_executed_twice(self):
        bot = _bot()
        bot._place = Mock(return_value=True)
        bot._last_fill_qty, bot._last_fill_price = 3200.0, 0.01
        cmd = {"id": 9, "action": "buy", "notional_frac": 1.0, "qty_frac": 0.0, "reason": "진입"}
        bot._execute_command(cmd, price=0.01)
        again = bot._execute_command(cmd, price=0.01)
        self.assertEqual(bot._place.call_count, 1)
        self.assertTrue(again["ok"])

    def test_failed_order_acks_error(self):
        bot = _bot()
        bot._place = Mock(side_effect=RuntimeError("주문 상태 REJECTED"))
        ack = bot._execute_command({"id": 10, "action": "buy", "notional_frac": 1.0, "qty_frac": 0.0, "reason": "진입"}, price=0.01)
        self.assertFalse(ack["ok"])
        self.assertIn("REJECTED", ack["error"])

    def test_add_to_position_averages_entry(self):
        bot = _bot(in_position=True, held=1000.0, entry=0.010)

        def place(side, qty, reduce_only=False):
            bot._last_fill_qty, bot._last_fill_price = qty, 0.012
            bot.held_qty += qty
            return True

        bot._place = Mock(side_effect=place)
        bot._execute_command({"id": 11, "action": "buy", "notional_frac": 0.375, "qty_frac": 0.0, "reason": "격자"}, price=0.012)
        self.assertAlmostEqual(bot.held_qty, 2000.0)
        self.assertAlmostEqual(bot.entry_price, 0.011)

    def test_local_stop_loss_is_the_only_local_exit(self):
        bot = _bot(in_position=True, held=1000.0, entry=0.010)
        self.assertFalse(bot._local_stop_loss(0.0096))
        self.assertTrue(bot._local_stop_loss(0.0094))
        self.assertFalse(hasattr(macro_runner, "_should_enter"))
        self.assertFalse(hasattr(macro_runner, "DEFAULT_TP_PCT"))

    # --- 실제 _place 경로: 추가 진입·부분 청산이 체결 대조 로직과 충돌하지 않는다 -------------
    def test_real_place_add_to_position_keeps_position_and_averages(self):
        bot = _bot(in_position=True, held=1000.0, entry=0.010)
        bot.base_asset = "ONE"
        bot.client.create_order.return_value = {
            "orderId": 1, "status": "FILLED", "executedQty": "1000", "cummulativeQuoteQty": "12",
            "fills": [{"qty": "1000", "price": "0.012", "commission": "0.0001", "commissionAsset": "BNB"}],
        }
        ack = bot._execute_command({"id": 12, "action": "buy", "notional_frac": 0.375, "qty_frac": 0.0, "reason": "격자"}, price=0.012)
        self.assertTrue(ack["ok"], ack)
        self.assertTrue(bot.in_position)
        self.assertAlmostEqual(bot.held_qty, 2000.0)
        self.assertAlmostEqual(bot.entry_price, 0.011)
        self.assertEqual(bot.realized, 0.0)  # 추가 매수는 실현손익이 아니다

    def test_real_place_partial_exit_keeps_remaining_position(self):
        bot = _bot(in_position=True, held=1000.0, entry=0.010)
        bot.client.create_order.return_value = {"orderId": 2, "status": "FILLED", "executedQty": "500", "cummulativeQuoteQty": "6"}
        ack = bot._execute_command({"id": 13, "action": "sell", "notional_frac": 0.0, "qty_frac": 0.5, "reason": "부분 청산"}, price=0.012)
        self.assertTrue(ack["ok"], ack)
        self.assertEqual(ack["executed_qty"], 500.0)
        self.assertTrue(bot.in_position)
        self.assertAlmostEqual(bot.held_qty, 500.0)
        self.assertAlmostEqual(bot.entry_price, 0.010)
        self.assertAlmostEqual(bot.realized, 1.0)

    def test_real_place_full_close_still_requires_flat(self):
        bot = _bot(in_position=True, held=1000.0, entry=0.010)
        bot.client.create_order.return_value = {"orderId": 3, "status": "FILLED", "executedQty": "700", "cummulativeQuoteQty": "8.4"}
        bot.client.get_order.return_value = {"orderId": 3, "status": "FILLED", "executedQty": "700", "cummulativeQuoteQty": "8.4"}
        ack = bot._execute_command({"id": 14, "action": "sell", "notional_frac": 0.0, "qty_frac": 1.0, "reason": "청산"}, price=0.012)
        self.assertFalse(ack["ok"])
        self.assertTrue(bot.in_position)
        self.assertAlmostEqual(bot.held_qty, 300.0)

    def test_uncertain_position_declines_entry_without_order(self):
        bot = _bot()
        bot.position_uncertain = True
        bot._place = Mock()
        ack = bot._execute_command({"id": 15, "action": "buy", "notional_frac": 1.0, "qty_frac": 0.0, "reason": "진입"}, price=0.01)
        bot._place.assert_not_called()
        self.assertFalse(ack["ok"])
        self.assertIn("포지션을 확인하지 못해", ack["error"])

    def test_order_that_leaves_position_uncertain_escapes_the_loop(self):
        bot = _bot()
        bot.client.create_order.side_effect = TimeoutError()
        bot.client.get_order.side_effect = TimeoutError()
        with self.assertRaises(RuntimeError):
            bot._execute_command({"id": 17, "action": "buy", "notional_frac": 1.0, "qty_frac": 0.0, "reason": "진입"}, price=0.01)
        self.assertTrue(bot.position_uncertain)
        self.assertEqual(bot.client.create_order.call_count, 1)

    def test_unknown_action_acks_error(self):
        bot = _bot()
        ack = bot._execute_command({"id": 16, "action": "hold", "notional_frac": 0.0, "qty_frac": 0.0, "reason": ""}, price=0.01)
        self.assertFalse(ack["ok"])
        self.assertIn("알 수 없는 명령", ack["error"])


def _run_bot(heartbeat_replies, in_position=False, held=0.0, entry=0.0):
    """run() 을 heartbeat 응답 수만큼 돌린다(그 다음 루프에서 stop_only 로 종료)."""
    bot = _bot(in_position=in_position, held=held, entry=entry)
    bot.testnet = True
    bot._connect = Mock(return_value=True)
    bot._prepare = Mock(return_value=True)
    bot._price = Mock(return_value=0.01)
    bot._get_command = Mock(side_effect=[None] * len(heartbeat_replies) + ["stop_only"])
    bot.set_command = Mock()
    bot._sleep = Mock()
    bot.server = Mock()
    bot.server.heartbeat.side_effect = list(heartbeat_replies)
    bot.on_status, bot.on_finish = Mock(), Mock()
    bot._offline_logged = False
    return bot


class RunLoopTests(unittest.TestCase):
    def setUp(self):
        patch.object(macro_runner.time, "sleep").start()
        self.addCleanup(patch.stopall)

    def test_no_command_means_no_order(self):
        bot = _run_bot([{"action": "continue", "commands": []}])
        bot._place = Mock()
        bot.run()
        bot._place.assert_not_called()
        self.assertFalse(bot.server.stopped.call_args.kwargs["snapshot"]["in_position"])

    def test_commands_are_executed_in_order_and_acked_next_heartbeat(self):
        bot = _run_bot([
            {"action": "continue", "commands": [
                {"id": 1, "action": "buy", "notional_frac": 0.5, "qty_frac": 0.0, "reason": "진입"},
                {"id": 2, "action": "sell", "notional_frac": 0.0, "qty_frac": 1.0, "reason": "청산"},
            ]},
            {"action": "continue", "commands": []},
        ])
        calls = []

        def execute(cmd, price):
            calls.append(cmd["id"])
            return {"command_id": cmd["id"], "ok": True, "executed_qty": 1.0, "fill_price": price, "error": ""}

        bot._execute_command = Mock(side_effect=execute)
        bot.run()
        self.assertEqual(calls, [1, 2])
        first, second = bot.server.heartbeat.call_args_list
        self.assertEqual(first.kwargs["acks"], [])
        self.assertEqual([a["command_id"] for a in second.kwargs["acks"]], [1, 2])

    def test_stop_action_sets_command_and_skips_commands(self):
        bot = _run_bot([{"action": "close_and_stop", "commands": []}])
        bot._execute_command = Mock()
        bot.run()
        bot.set_command.assert_called_once_with("close_and_stop")
        bot._execute_command.assert_not_called()

    def test_offline_logs_once_and_executes_nothing(self):
        bot = _run_bot([
            {"action": "continue", "commands": [], "offline": True},
            {"action": "continue", "commands": [], "offline": True},
        ])
        bot._execute_command = Mock()
        bot.run()
        bot._execute_command.assert_not_called()
        retry_logs = [c.args[0] for c in bot.log.call_args_list if "서버 연결 재시도 중" in str(c.args[0])]
        self.assertEqual(len(retry_logs), 1)

    def test_entry_command_declined_while_risk_guard_blocks(self):
        bot = _run_bot([
            {"action": "continue", "commands": [{"id": 3, "action": "buy", "notional_frac": 1.0, "qty_frac": 0.0, "reason": "진입"}]},
            {"action": "continue", "commands": []},
        ])
        bot._execute_command = Mock()
        with patch.object(macro_runner.RiskGuard, "entry_blocked", return_value=(True, "일일 최대손실 도달")):
            bot.run()
        bot._execute_command.assert_not_called()
        ack = bot.server.heartbeat.call_args_list[1].kwargs["acks"][0]
        self.assertEqual(ack["command_id"], 3)
        self.assertFalse(ack["ok"])
        self.assertIn("로컬 리스크 보류", ack["error"])

    def test_server_exit_feeds_daily_loss_guard(self):
        bot = _run_bot([
            {"action": "continue", "commands": [{"id": 4, "action": "sell", "notional_frac": 0.0, "qty_frac": 1.0, "reason": "청산"}]},
        ], in_position=True, held=1000.0, entry=0.0102)  # -2%: 로컬 손절(5%)은 안 걸린다

        def close():
            bot.in_position, bot.held_qty, bot.realized = False, 0.0, -0.2
            bot._last_fill_qty, bot._last_fill_price = 1000.0, 0.01
            return True

        bot._close_position = Mock(side_effect=close)
        with patch.object(macro_runner.RiskGuard, "on_exit") as on_exit:
            bot.run()
        on_exit.assert_called_once_with(-0.2, was_stop=False)

    def test_local_stop_loss_closes_before_asking_server(self):
        bot = _run_bot([{"action": "continue", "commands": []}], in_position=True, held=1000.0, entry=0.011)

        def close():
            bot.in_position, bot.held_qty = False, 0.0
            bot._last_fill_qty, bot._last_fill_price = 1000.0, 0.01
            return True

        bot._close_position = Mock(side_effect=close)
        bot.run()
        bot._close_position.assert_called_once_with()
        snap = bot.server.heartbeat.call_args.args[0]
        self.assertFalse(snap["in_position"])

    def test_price_outage_heartbeat_carries_acks_and_honours_stop(self):
        bot = _run_bot([{"action": "stop_only", "commands": []}])
        bot._price.side_effect = RuntimeError("price source offline")
        bot.pending_acks = [{"command_id": 7, "ok": True, "executed_qty": 1.0, "fill_price": 0.01, "error": ""}]
        bot.run()
        bot.server.heartbeat.assert_called_once()
        self.assertEqual(bot.server.heartbeat.call_args.kwargs["acks"][0]["command_id"], 7)
        self.assertEqual(bot.pending_acks, [])
        bot.set_command.assert_called_once_with("stop_only")


class HeartbeatProtocolTests(unittest.TestCase):
    def test_heartbeat_sends_acks_and_returns_commands(self):
        client = object.__new__(macro_runner.ServerClient)
        client.session_id, client.base, client._headers = 3, "http://x", {}
        client._drain_events, client._requeue_events = Mock(return_value=[]), Mock()
        resp = Mock(); resp.json.return_value = {"action": "continue", "commands": [{"id": 1}]}; resp.raise_for_status = Mock()
        with patch.object(macro_runner.requests, "post", return_value=resp) as post:
            out = client.heartbeat({"in_position": False}, acks=[{"command_id": 0, "ok": True}])
        self.assertEqual(out["commands"], [{"id": 1}])
        self.assertEqual(post.call_args.kwargs["json"]["acks"], [{"command_id": 0, "ok": True}])

    def test_heartbeat_offline_keeps_acks(self):
        client = object.__new__(macro_runner.ServerClient)
        client.session_id, client.base, client._headers = 3, "http://x", {}
        client._drain_events, client._requeue_events = Mock(return_value=[]), Mock()
        with patch.object(macro_runner.requests, "post", side_effect=OSError("down")):
            out = client.heartbeat({}, acks=[{"command_id": 2, "ok": True}])
        self.assertEqual(out["action"], "continue")
        self.assertTrue(out["offline"])
        self.assertEqual(client.unsent_acks, [{"command_id": 2, "ok": True}])

    def test_heartbeat_prepends_unsent_acks_next_time(self):
        client = macro_runner.ServerClient("key", base="http://x")
        client.session_id = 3
        with patch.object(macro_runner.requests, "post", side_effect=OSError("down")):
            client.heartbeat({}, acks=[{"command_id": 2, "ok": True}])
        resp = Mock(); resp.json.return_value = {"action": "continue", "commands": []}; resp.raise_for_status = Mock()
        with patch.object(macro_runner.requests, "post", return_value=resp) as post:
            out = client.heartbeat({}, acks=[{"command_id": 3, "ok": False}])
        self.assertEqual([a["command_id"] for a in post.call_args.kwargs["json"]["acks"]], [2, 3])
        self.assertNotIn("offline", out)
        self.assertEqual(client.unsent_acks, [])

    def test_heartbeat_without_session_is_a_noop(self):
        client = macro_runner.ServerClient("key", base="http://x")
        with patch.object(macro_runner.requests, "post") as post:
            out = client.heartbeat({}, acks=[{"command_id": 1, "ok": True}])
        post.assert_not_called()
        self.assertEqual(out, {"action": "continue", "commands": []})


if __name__ == "__main__":
    unittest.main()
