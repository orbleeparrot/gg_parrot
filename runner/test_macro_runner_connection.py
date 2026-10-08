"""Startup gate, safe standalone checks, and Tk-free worker delivery."""
import queue
import threading
import time
import unittest
from pathlib import Path
from types import SimpleNamespace
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
        app._public_ip_cache = connection.PublicIPCache()
        app._public_ip_waiters = set()
        app._public_ip_expiry_id = None
        app._public_ip = ""
        app._connection_wizard = None
        app._wizard_generation = 0
        app.ip_btn, app.ip_copy_btn, app.ip_note = Mock(), Mock(), Mock()
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

    def test_wizard_is_not_permission_to_start_a_macro(self):
        app = self.app()
        app._connection_wizard = Mock()
        with patch.object(macro_runner.messagebox, "showwarning"), patch.object(macro_runner, "ServerClient") as server, patch.object(macro_runner, "BotThread") as bot:
            app._start()
        server.assert_not_called()
        bot.assert_not_called()

    def test_closing_application_during_confirmation_does_not_read_destroyed_tk_variables(self):
        app = self.app()
        self.checked(app)
        def approve(*_args):
            app._connection_closed = True
            app._snapshot_for_connection = Mock(side_effect=AssertionError("destroyed Tk variables"))
            return True
        with patch.object(macro_runner.messagebox, "askyesno", side_effect=approve), patch.object(macro_runner, "ServerClient") as server, patch.object(macro_runner, "BotThread") as bot:
            app._start()
        app._snapshot_for_connection.assert_not_called()
        server.assert_not_called()
        bot.assert_not_called()

    def test_opening_wizard_during_confirmation_never_creates_a_run(self):
        app = self.app()
        self.checked(app)
        def approve(*_args):
            app._connection_wizard = Mock()
            return True
        with patch.object(macro_runner.messagebox, "askyesno", side_effect=approve), patch.object(macro_runner, "ServerClient") as server, patch.object(macro_runner, "BotThread") as bot:
            app._start()
        server.assert_not_called()
        bot.assert_not_called()

    def test_preflight_finishing_while_wizard_is_open_never_resumes_start(self):
        app = self.app()
        result = self.checked(app)
        app._connection_wizard = Mock(closed=False, generation=0, exchange="upbit")
        app._start = Mock()
        app._connection_queue.put(("preflight", 0, result, True))
        app._poll_connection_tasks()
        app._start.assert_not_called()
        self.assertEqual(app.start_btn.config.call_args.kwargs["state"], "disabled")

    def test_public_ip_duplicate_clicks_share_a_worker_and_failures_clear_copy(self):
        app = self.app()
        with patch.object(macro_runner.threading, "Thread") as thread:
            app._begin_public_ip()
            app._begin_public_ip()
        self.assertEqual(thread.call_count, 1)
        token = app._public_ip_cache._active
        app._connection_queue.put(("ip", token, True, "8.8.8.8"))
        app._poll_connection_tasks()
        self.assertEqual(app._public_ip, "8.8.8.8")
        self.assertFalse(app._public_ip_busy)
        with patch.object(macro_runner.threading, "Thread"):
            app._begin_public_ip(force=True)
        self.assertEqual(app._public_ip, "")
        token = app._public_ip_cache._active
        app._connection_queue.put(("ip", token, False, "ProxyError SECRET"))
        app._poll_connection_tasks()
        self.assertEqual(app._public_ip, "")
        self.assertEqual(app.ip_copy_btn.config.call_args.kwargs["state"], "disabled")
        self.assertNotIn("SECRET", app.ip_note.config.call_args.kwargs["text"])

    def test_cached_automatic_success_or_failure_never_starts_a_worker(self):
        app = self.app()
        for ok, address in ((True, "8.8.8.8"), (False, "")):
            app._public_ip_cache.result = connection.PublicIPResult(ok, address, "fixture")
            with patch.object(macro_runner.threading, "Thread") as thread:
                app._begin_public_ip(force=False)
            thread.assert_not_called()
            self.assertEqual(bool(app._public_ip), ok)

    def test_stale_macro_response_cannot_be_copied_or_stick_busy(self):
        app = self.app()
        with patch.object(macro_runner.threading, "Thread"):
            app._begin_public_ip()
        token = app._public_ip_cache._active
        app._public_ip_cache.invalidate()
        app._connection_queue.put(("ip", token, True, "8.8.8.8"))
        app._poll_connection_tasks()
        self.assertFalse(app._public_ip_busy)
        self.assertEqual(app._public_ip, "")
        self.assertEqual(app.ip_copy_btn.config.call_args.kwargs["state"], "disabled")

    def test_copy_checks_expiry_before_touching_clipboard(self):
        app = self.app()
        app._public_ip_cache.result = connection.PublicIPResult(True, "8.8.8.8", "fixture", checked_at=time.monotonic() - 121)
        app._public_ip = "8.8.8.8"
        app._copy_public_ip()
        app.root.clipboard_append.assert_not_called()
        self.assertEqual(app._public_ip, "")

    def test_only_current_wizard_generation_can_start_its_automatic_ip_request(self):
        app = self.app()
        app._wizard_generation = 2
        app._connection_wizard = Mock(closed=False, exchange="upbit", generation=2)
        app._begin_public_ip = Mock()
        app._auto_public_ip_for_wizard(1)
        app._begin_public_ip.assert_not_called()
        app._auto_public_ip_for_wizard(2)
        app._begin_public_ip.assert_called_once_with(force=False)


class FinalStartSnapshotTests(unittest.TestCase):
    app = ConnectionGuiTests.app
    checked = ConnectionGuiTests.checked

    def ready(self):
        app = self.app()
        app.server_base = "https://example.invalid"
        app.user_macro_id, app.macro_sig, app.macro_source = None, None, "file"
        app._log_threadsafe = app._status_threadsafe = app._finish_threadsafe = Mock()
        app._set_running = Mock()
        result = self.checked(app)
        server = Mock(session_id=1, macro_origin="file_modified")
        server.start.return_value = {"session_id": 1}
        return app, result, server

    def test_changed_macro_in_source_warning_cancels_created_run_before_any_bot(self):
        app, _checked, server = self.ready()
        def warning(*_args):
            app.macro["symbol"] = "KRW-ETH"
            app._invalidate_preflight()
        with patch.object(macro_runner.messagebox, "askyesno", return_value=True), patch.object(macro_runner.messagebox, "showwarning", side_effect=warning), patch.object(macro_runner, "ServerClient", return_value=server), patch.object(macro_runner, "BotThread") as bot:
            app._start()
        bot.assert_not_called()
        server.stopped.assert_called_once()
        self.assertEqual(server.stopped.call_args.args[0], "error")
        self.assertEqual(server.start.call_args.args[0]["symbol"], "KRW-BTC")

    def test_member_change_in_source_warning_cancels_even_without_a_variable_trace(self):
        app, _checked, server = self.ready()
        def warning(*_args):
            app.member_key.set("new-member")
        with patch.object(macro_runner.messagebox, "askyesno", return_value=True), patch.object(macro_runner.messagebox, "showwarning", side_effect=warning), patch.object(macro_runner, "ServerClient", return_value=server), patch.object(macro_runner, "BotThread") as bot:
            app._start()
        bot.assert_not_called()
        server.stopped.assert_called_once()

    def test_expiry_in_source_warning_cancels_created_run(self):
        app, checked, server = self.ready()
        def warning(*_args):
            object.__setattr__(checked, "checked_at", time.monotonic() - 121)
        with patch.object(macro_runner.messagebox, "askyesno", return_value=True), patch.object(macro_runner.messagebox, "showwarning", side_effect=warning), patch.object(macro_runner, "ServerClient", return_value=server), patch.object(macro_runner, "BotThread") as bot:
            app._start()
        bot.assert_not_called()
        server.stopped.assert_called_once()

    def test_nested_start_during_warning_cannot_create_a_second_run(self):
        app, _checked, server = self.ready()
        with patch.object(macro_runner.messagebox, "askyesno", return_value=True), patch.object(macro_runner.messagebox, "showwarning", side_effect=lambda *_args: app._start()), patch.object(macro_runner, "ServerClient", return_value=server), patch.object(macro_runner, "BotThread") as bot:
            app._start()
        server.start.assert_called_once()
        server.stopped.assert_not_called()
        bot.assert_called_once()
        macro = bot.call_args.args[0]
        self.assertEqual(macro["symbol"], "KRW-BTC")
        self.assertIsNot(macro, app.macro)

    def test_cancel_notification_failure_is_visible_but_never_starts_a_bot(self):
        app, _checked, server = self.ready()
        server.stopped.return_value = False
        with patch.object(macro_runner.messagebox, "askyesno", return_value=True), patch.object(macro_runner.messagebox, "showwarning", side_effect=lambda *_args: app._invalidate_preflight()), patch.object(macro_runner, "ServerClient", return_value=server), patch.object(macro_runner, "BotThread") as bot:
            app._start()
        bot.assert_not_called()
        self.assertTrue(any("서버 취소 통보에 실패" in call.args[0] for call in app._log.call_args_list))

    def test_opening_wizard_during_source_warning_cancels_run_without_starting(self):
        app, _checked, server = self.ready()
        def warning(*_args):
            app._connection_wizard = Mock(closed=False, generation=0, exchange="upbit")
        with patch.object(macro_runner.messagebox, "askyesno", return_value=True), patch.object(macro_runner.messagebox, "showwarning", side_effect=warning), patch.object(macro_runner, "ServerClient", return_value=server), patch.object(macro_runner, "BotThread") as bot:
            app._start()
        bot.assert_not_called()
        server.stopped.assert_called_once()


class CredentialReuseUiTests(unittest.TestCase):
    def app(self):
        app = ConnectionGuiTests().app()
        app.key_vars["binance"] = (_Var("fixture-binance"), _Var("fixture-bs"))
        app.key_vars["bithumb"] = (_Var("fixture-bithumb"), _Var("fixture-bhs"))
        app.remember = _Var(True)
        app.credentials_path = Path("/tmp/not-written-fixture.dat")
        app._remembered = {"version": 2, "member_key": "member-example", "exchanges": {
            name: {"api_key": pair[0].get(), "api_secret": pair[1].get()} for name, pair in app.key_vars.items()}}
        app._remember_choices = {name: True for name in app.key_vars}
        app._storage_dirty = {name: False for name in app.key_vars}
        app._credentials_load_status = "loaded"
        app._applying_credentials = False
        app._storage_note = ""
        return app

    def test_saved_existing_and_new_key_states_are_distinct_and_no_keys_are_shown(self):
        app = self.app()
        state = app._connection_setup_state()
        self.assertEqual(state["kind"], "saved")
        app.key_vars["upbit"][1].set("fixture-changed")
        app._on_credentials_changed("upbit")
        self.assertEqual(app._connection_setup_state()["kind"], "existing")
        self.assertTrue(app._storage_dirty["upbit"])
        app.key_vars["upbit"][0].set(""); app.key_vars["upbit"][1].set("")
        self.assertEqual(app._connection_setup_state()["kind"], "new")
        for value in ("key-example", "secret-example", "fixture-changed"):
            self.assertNotIn(value, str(app._connection_setup_state()))

    def test_selected_exchange_save_uses_new_api_and_clears_only_its_dirty_flag(self):
        app = self.app()
        app._storage_dirty.update(upbit=True, bithumb=True)
        saved = app._credential_values()
        with patch.object(macro_runner.credentials_mod, "supported", return_value=True), patch.object(macro_runner.credentials_mod, "apply_exchange_choice", return_value=saved) as apply:
            self.assertTrue(app._persist_credentials())
        self.assertEqual(apply.call_args.args[1:3], ("upbit", True))
        self.assertFalse(app._storage_dirty["upbit"])
        self.assertTrue(app._storage_dirty["bithumb"])

    def test_delete_is_selected_only_and_failure_never_clears_any_inputs(self):
        app = self.app()
        with patch.object(macro_runner.credentials_mod, "apply_exchange_choice", side_effect=OSError("SECRET transport")), patch.object(macro_runner.messagebox, "showerror") as error:
            app._forget_credentials()
        self.assertEqual(app.key_vars["upbit"][0].get(), "key-example")
        self.assertNotIn("SECRET", str(error.call_args))
        saved = {"version": 2, "member_key": "member-example", "exchanges": {name: pair for name, pair in app._remembered["exchanges"].items() if name != "upbit"}}
        with patch.object(macro_runner.credentials_mod, "apply_exchange_choice", return_value=saved) as apply:
            app._forget_credentials()
        self.assertEqual(apply.call_args.args[1:3], ("upbit", False))
        self.assertEqual(app.key_vars["upbit"][0].get(), "")
        self.assertEqual(app.key_vars["bithumb"][0].get(), "fixture-bithumb")
        self.assertEqual(app.member_key.get(), "member-example")

    def test_reload_failure_preserves_edited_inputs_and_has_fixed_safe_guidance(self):
        app = self.app()
        with patch.object(macro_runner.credentials_mod, "load_result", return_value=SimpleNamespace(status="decrypt_error", values=None)):
            app._reload_credentials()
        self.assertEqual(app.key_vars["upbit"][0].get(), "key-example")
        self.assertEqual(app._credentials_load_status, "decrypt_error")
        self.assertIn("Windows", app._storage_note)

    def test_reload_while_running_is_never_allowed(self):
        app = self.app(); app.bot = Mock()
        with patch.object(macro_runner.credentials_mod, "load_result") as load, patch.object(macro_runner.messagebox, "showwarning"):
            app._reload_credentials()
        load.assert_not_called()

    def test_restore_dirty_confirmation_decline_preserves_inputs(self):
        app = self.app(); app._storage_dirty["upbit"] = True
        with patch.object(macro_runner.messagebox, "askyesno", return_value=False), patch.object(macro_runner.credentials_mod, "load_result") as load:
            app._reload_credentials()
        load.assert_not_called()
        self.assertEqual(app.key_vars["upbit"][0].get(), "key-example")

    def test_confirmed_reload_replaces_member_input_even_when_saved_member_is_empty(self):
        app = self.app()
        saved = {"version": 2, "member_key": "", "exchanges": {}}
        with patch.object(macro_runner.messagebox, "askyesno", return_value=True), patch.object(macro_runner.credentials_mod, "load_result", return_value=SimpleNamespace(status="loaded", values=saved)):
            app._reload_credentials()
        self.assertEqual(app.member_key.get(), "")

    def test_initial_load_preserves_an_existing_member_key(self):
        app = self.app()
        app._apply_remembered_credentials({"version": 2, "member_key": "saved-other-member", "exchanges": {}})
        self.assertEqual(app.member_key.get(), "member-example")

    def test_recovery_backup_failure_does_not_save_or_clear_inputs(self):
        app = self.app()
        with patch.object(macro_runner.messagebox, "askyesno", return_value=True), patch.object(macro_runner.credentials_mod, "backup_unreadable", side_effect=OSError("SECRET")), patch.object(macro_runner.credentials_mod, "apply_exchange_choice") as save:
            app._recover_credentials_storage()
        save.assert_not_called()
        self.assertEqual(app.key_vars["upbit"][0].get(), "key-example")
        self.assertNotIn("SECRET", app._storage_note)

    def test_recovery_changed_form_in_confirmation_never_moves_previous_file(self):
        app = self.app()
        def confirm(*_args):
            app.macro["exchange"] = "bithumb"
            app._invalidate_preflight()
            return True
        with patch.object(macro_runner.messagebox, "askyesno", side_effect=confirm), patch.object(macro_runner.credentials_mod, "backup_unreadable") as backup, patch.object(macro_runner.credentials_mod, "apply_exchange_choice") as save:
            app._recover_credentials_storage()
        backup.assert_not_called()
        save.assert_not_called()

    def test_recovery_remember_choice_change_in_modal_performs_no_file_io(self):
        app = self.app()
        def confirm(*_args):
            app.remember.set(False); app._on_remember_choice()
            return True
        with patch.object(macro_runner.messagebox, "askyesno", side_effect=confirm), patch.object(macro_runner.credentials_mod, "backup_unreadable") as backup, patch.object(macro_runner.credentials_mod, "apply_exchange_choice") as save:
            app._recover_credentials_storage()
        backup.assert_not_called()
        save.assert_not_called()

    def test_reload_remember_choice_change_in_modal_performs_no_file_io(self):
        app = self.app(); app._storage_dirty["upbit"] = True
        def confirm(*_args):
            app.remember.set(False); app._on_remember_choice()
            return True
        with patch.object(macro_runner.messagebox, "askyesno", side_effect=confirm), patch.object(macro_runner.credentials_mod, "load_result") as load:
            app._reload_credentials()
        load.assert_not_called()

    def test_recovery_save_failure_keeps_backup_and_input_and_reports_failure(self):
        app = self.app()
        with patch.object(macro_runner.messagebox, "askyesno", return_value=True), patch.object(macro_runner.credentials_mod, "supported", return_value=True), patch.object(macro_runner.credentials_mod, "backup_unreadable", return_value=Path("/tmp/credentials.backup-fixture.dat")), patch.object(macro_runner.credentials_mod, "apply_exchange_choice", side_effect=OSError("SECRET")):
            app._recover_credentials_storage()
        self.assertEqual(app.key_vars["upbit"][0].get(), "key-example")
        self.assertIn("백업", app._storage_note)
        self.assertIn("실패", app._storage_note)
        self.assertNotIn("SECRET", app._storage_note)

    def test_successful_history_saves_authoritative_disk_values_not_stale_cache(self):
        app = self.app()
        app._public_ip_cache.result = connection.PublicIPResult(True, "8.8.8.8", "fixture")
        app._preflight = ConnectionGuiTests().checked(app)
        disk = app._credential_values()
        disk["exchanges"]["bithumb"]["api_key"] = "disk-newer-fixture"
        with patch.object(macro_runner.credentials_mod, "supported", return_value=True), patch.object(macro_runner.credentials_mod, "load_result", return_value=SimpleNamespace(status="loaded", values=disk)), patch.object(macro_runner.credentials_mod, "save") as save:
            app._record_successful_ip_history()
        self.assertEqual(save.call_args.args[1]["exchanges"]["bithumb"]["api_key"], "disk-newer-fixture")

    def test_missing_disk_never_resurrects_cached_keys_when_recording_ip_history(self):
        app = self.app()
        app._public_ip_cache.result = connection.PublicIPResult(True, "8.8.8.8", "fixture")
        app._preflight = ConnectionGuiTests().checked(app)
        with patch.object(macro_runner.credentials_mod, "supported", return_value=True), patch.object(macro_runner.credentials_mod, "load_result", return_value=SimpleNamespace(status="missing", values=None)), patch.object(macro_runner.credentials_mod, "save") as save:
            app._record_successful_ip_history()
        save.assert_not_called()

    def test_expired_check_state_is_not_displayed_as_current_success(self):
        app = self.app()
        checked = ConnectionGuiTests().checked(app)
        object.__setattr__(checked, "checked_at", time.monotonic() - 121)
        self.assertIn("만료", app._connection_check_note())


if __name__ == "__main__":
    unittest.main()
