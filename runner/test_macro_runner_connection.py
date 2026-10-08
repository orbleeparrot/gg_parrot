"""Startup gate, safe standalone checks, and Tk-free worker delivery."""
import queue
import threading
import time
import unittest
from unittest.mock import Mock, patch

from runner.test_macro_runner_single_instance import macro_runner
from runner.test_macro_runner_exchange import _Var
from runner import connection


class StandaloneCheckTests(unittest.TestCase):
    def macro(self, exchange="upbit"):
        return {"exchange": exchange, "symbol": "KRW-BTC", "position_side": "long", "leverage": 1, "params": {"initial_capital": 10000}}

    def credentials(self):
        return {"exchanges": {"upbit": {"api_key": "key-example", "api_secret": "secret-example"}}}

    def test_mock_is_keyless_and_does_not_build_a_broker_or_contact_a_server(self):
        with patch.object(macro_runner.BotThread, "_build_broker") as broker, patch.object(macro_runner, "ServerClient") as server:
            result = macro_runner.perform_connection_check(self.macro(), {}, "mock")
        self.assertTrue(result.ok)
        self.assertIn("실거래 연결은 검사하지", result.reason)
        broker.assert_not_called()
        server.assert_not_called()

    def test_domestic_short_and_testnet_are_rejected_before_any_network(self):
        macro = self.macro()
        for mode, side in (("live", "short"), ("testnet", "long")):
            macro["position_side"] = side
            with patch.object(macro_runner.BotThread, "_build_broker") as broker:
                result = macro_runner.perform_connection_check(macro, self.credentials(), mode)
            self.assertFalse(result.ok)
            broker.assert_not_called()

    def test_one_rehearsal_and_no_real_order_or_futures_setup(self):
        broker = Mock()
        broker.rehearse.return_value = (True, "업비트 검증 통과")
        with patch.object(macro_runner.BotThread, "_build_broker", return_value=broker):
            result = macro_runner.perform_connection_check(self.macro(), self.credentials(), "live")
        self.assertTrue(result.ok)
        broker.rehearse.assert_called_once_with(notional=10000)
        broker.submit.assert_not_called()
        broker.ensure_ready.assert_not_called()
        self.assertIs(result.broker, broker)

    def test_swallowed_proxy_exception_is_not_displayed_or_logged(self):
        broker = Mock()
        broker.rehearse.return_value = (False, "ProxyError https://username:password@proxy.test?signature=SIGNED")
        with patch.object(macro_runner.BotThread, "_build_broker", return_value=broker):
            result = macro_runner.perform_connection_check(self.macro(), self.credentials(), "live")
        self.assertFalse(result.ok)
        for value in ("username", "password", "SIGNED", "proxy.test"):
            self.assertNotIn(value, result.reason)

    def test_current_preflight_is_reused_instead_of_second_startup_rehearsal(self):
        bot = macro_runner.BotThread(self.macro(), self.credentials(), "live", Mock(), Mock(), Mock(), Mock())
        bot.broker = Mock()
        bot._preflight = connection.PreflightResult(True, "검증 통과", connection.preflight_fingerprint(bot.macro, bot.mode, bot.credentials), broker=bot.broker)
        self.assertTrue(bot._rehearse())
        bot.broker.rehearse.assert_not_called()
        self.assertIsNone(bot._preflight)

    def test_runtime_connection_and_rehearsal_logs_do_not_leak_network_secrets(self):
        log = Mock()
        bot = macro_runner.BotThread(self.macro(), self.credentials(), "live", Mock(), log, Mock(), Mock())
        with patch.object(bot, "_build_broker", side_effect=RuntimeError("https://username:password@proxy.test?signature=SIGNED")):
            self.assertFalse(bot._connect())
        bot.broker = Mock()
        bot.broker.rehearse.return_value = (False, "ProxyError https://username:password@proxy.test?signature=SIGNED")
        self.assertFalse(bot._rehearse())
        for call in log.call_args_list:
            for value in ("username", "password", "SIGNED", "proxy.test"):
                self.assertNotIn(value, call.args[0])


class ConnectionGuiTests(unittest.TestCase):
    def app(self):
        app = object.__new__(macro_runner.RunnerApp)
        app.root = Mock()
        app.bot = None
        app._protocol_claim_busy = False
        app.macro = {"exchange": "upbit", "symbol": "KRW-BTC", "position_side": "long", "leverage": 1, "params": {"initial_capital": 10000}}
        app.mode = _Var("live")
        app.member_key = _Var("member-example")
        app.key_vars = {"upbit": (_Var("key-example"), _Var("secret-example"))}
        app._connection_queue = queue.Queue()
        app._connection_delivery_lock = threading.Lock()
        app._connection_generation = 0
        app._connection_closed = False
        app._connection_polling = False
        app._connection_busy = False
        app._public_ip_busy = False
        app._preflight = None
        app.connection_note = Mock()
        app.connection_btn = Mock()
        app.start_btn = Mock()
        app._log = Mock()
        return app

    def checked(self, app):
        _macro, _mode, _credentials, fingerprint = app._snapshot_for_connection()
        result = connection.PreflightResult(True, "확인", fingerprint)
        app._preflight = result
        return result

    def test_unchecked_live_start_never_creates_a_server_run(self):
        app = self.app()
        app._begin_connection_check = Mock()
        with patch.object(macro_runner, "ServerClient") as server:
            app._start()
        server.assert_not_called()
        app._begin_connection_check.assert_called_once_with(start_after=True)

    def test_expired_result_after_confirmation_never_creates_a_run(self):
        app = self.app()
        result = self.checked(app)
        def approve(*_args):
            object.__setattr__(result, "checked_at", time.monotonic() - 121)
            return True
        with patch.object(macro_runner.messagebox, "askyesno", side_effect=approve), patch.object(macro_runner, "ServerClient") as server:
            app._start()
        server.assert_not_called()
        self.assertIsNone(app._preflight)

    def test_changed_key_during_confirmation_never_creates_a_run(self):
        app = self.app()
        self.checked(app)
        def approve(*_args):
            app.key_vars["upbit"][1].set("changed-secret")
            return True
        with patch.object(macro_runner.messagebox, "askyesno", side_effect=approve), patch.object(macro_runner, "ServerClient") as server:
            app._start()
        server.assert_not_called()

    def test_invalidated_check_during_confirmation_cannot_be_reused_even_if_values_match(self):
        app = self.app()
        self.checked(app)
        def approve(*_args):
            app._invalidate_preflight()
            return True
        with patch.object(macro_runner.messagebox, "askyesno", side_effect=approve), patch.object(macro_runner, "ServerClient") as server:
            app._start()
        server.assert_not_called()

    def test_member_key_change_during_confirmation_cannot_start_a_different_account(self):
        app = self.app()
        self.checked(app)
        def approve(*_args):
            app.member_key.set("changed-member")
            app._invalidate_preflight()  # actual Tk StringVar invokes the installed write trace
            return True
        with patch.object(macro_runner.messagebox, "askyesno", side_effect=approve), patch.object(macro_runner, "ServerClient") as server:
            app._start()
        server.assert_not_called()

    def test_stale_async_result_does_not_resume_start(self):
        app = self.app()
        result = self.checked(app)
        app._invalidate_preflight()
        app._start = Mock()
        app._connection_queue.put(("preflight", 0, result, True))
        app._poll_connection_tasks()
        app._start.assert_not_called()
        self.assertIsNone(app._preflight)

    def test_failed_async_result_does_not_resume_start(self):
        app = self.app()
        _macro, _mode, _credentials, fingerprint = app._snapshot_for_connection()
        app._start = Mock()
        app._connection_queue.put(("preflight", 0, connection.PreflightResult(False, "키 확인 실패", fingerprint), True))
        app._poll_connection_tasks()
        app._start.assert_not_called()
        self.assertIn("키 확인 실패", app.connection_note.config.call_args.kwargs["text"])

    def test_current_async_result_resumes_exactly_once(self):
        app = self.app()
        result = self.checked(app)
        app._start = Mock()
        app._connection_queue.put(("preflight", 0, result, True))
        app._poll_connection_tasks()
        app._start.assert_called_once_with()
        self.assertIs(app._preflight, result)

    def test_selected_macro_shows_only_its_exchange_fields(self):
        app = self.app()
        app.key_rows = {name: [Mock(), Mock()] for name in ("binance", "upbit", "bithumb")}
        app.key_selection_note, app.ip_row = Mock(), Mock()
        app._sync_key_fields()
        for name, widgets in app.key_rows.items():
            for widget in widgets:
                if name == "upbit":
                    widget.grid.assert_called_once_with()
                    widget.grid_remove.assert_not_called()
                else:
                    widget.grid_remove.assert_called_once_with()

    def test_worker_uses_snapshot_and_queue_without_tk_calls(self):
        app = self.app()
        main_thread = threading.get_ident()
        original_snapshot = app._snapshot_for_connection
        def snapshot():
            self.assertEqual(threading.get_ident(), main_thread)
            return original_snapshot()
        app._snapshot_for_connection = snapshot
        seen = []
        finished = threading.Event()
        def perform(macro, credentials, mode):
            seen.append(threading.get_ident())
            result = connection.PreflightResult(True, "검증", connection.preflight_fingerprint(macro, mode, credentials))
            finished.set()
            return result
        with patch.object(macro_runner, "perform_connection_check", side_effect=perform):
            app._begin_connection_check()
            self.assertTrue(finished.wait(2))
        item = app._connection_queue.get(timeout=2)
        app._connection_queue.put(item)
        self.assertNotEqual(seen[0], main_thread)
        app.root.after.assert_called_once_with(100, app._poll_connection_tasks)
        # Widget text does not change until the MAIN thread drains the worker queue.
        self.assertIn("확인 중", app.connection_note.config.call_args.kwargs["text"])
        app._poll_connection_tasks()
        self.assertEqual(app.connection_note.config.call_args.kwargs["text"], "검증")

    def test_close_disposes_an_already_queued_broker(self):
        app = self.app()
        result = self.checked(app)
        app._preflight = None
        app._connection_queue.put(("preflight", 0, result, True))
        with patch.object(macro_runner, "close_connection_broker") as close:
            app._on_window_close()
        self.assertTrue(app._connection_closed)
        close.assert_called_once_with(result.broker)
        app.root.destroy.assert_called_once_with()


if __name__ == "__main__":
    unittest.main()
