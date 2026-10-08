"""Optional real-Tk smoke tests (local virtual display, no keys or network)."""
import os
import threading
import time
import unittest
from copy import deepcopy
from unittest.mock import patch

try:
    import tkinter as tk
except ImportError:
    tk = None


@unittest.skipUnless(tk is not None and os.environ.get("DISPLAY"), "real Tk and a local test display are required")
class ConnectionTkTests(unittest.TestCase):
    def setUp(self):
        from runner.macro_runner import RunnerApp
        for target in ("requests.Session.request", "socket.socket.connect"):
            block = patch(target, side_effect=AssertionError("unmocked network I/O is forbidden"))
            block.start()
            self.addCleanup(block.stop)
        self.root = tk.Tk()
        self.root.withdraw()
        self._tk_errors = []
        self.root.report_callback_exception = lambda _kind, exc, _tb: self._tk_errors.append(exc)
        self.support = patch("runner.macro_runner.credentials_mod.supported", return_value=True)
        from runner.credentials import CredentialsLoadResult
        self.load = patch("runner.macro_runner.credentials_mod.load_result", return_value=CredentialsLoadResult(None, "missing"))
        def save_exchange(_path, exchange, remember, values, *, previous=None):
            saved = deepcopy(previous or {"version": 2, "member_key": values.get("member_key", ""), "exchanges": {}})
            if remember:
                saved["exchanges"][exchange] = deepcopy(values["exchanges"][exchange])
            else:
                saved["exchanges"].pop(exchange, None)
            return saved
        self.save = patch("runner.macro_runner.credentials_mod.apply_exchange_choice", side_effect=save_exchange)
        self.save_mock = self.save.start()
        self.support.start(); self.load.start()
        self.addCleanup(self.support.stop); self.addCleanup(self.load.stop); self.addCleanup(self.save.stop)
        self.app = RunnerApp(self.root)
        self.root.update_idletasks()

    def tearDown(self):
        self.app._on_window_close()
        self.assertEqual(self._tk_errors, [])

    def select(self, exchange, symbol):
        self.app._apply_local_macro({"exchange": exchange, "symbol": symbol, "position_side": "long", "leverage": 1}, "example.ggm.json")
        self.root.update_idletasks()

    def test_exchange_selection_hides_unused_fields_and_preserves_their_values(self):
        self.app.key_vars["upbit"][0].set("dummy-upbit")
        self.select("upbit", "KRW-BTC")
        for name, widgets in self.app.key_rows.items():
            self.assertTrue(all(bool(widget.winfo_manager()) == (name == "upbit") for widget in widgets))
        self.assertEqual(self.app.ip_row.winfo_manager(), "pack")
        self.select("binance", "BTCUSDT")
        self.assertEqual(self.app.ip_row.winfo_manager(), "")
        self.select("upbit", "KRW-BTC")
        self.assertEqual(self.app.key_vars["upbit"][0].get(), "dummy-upbit")

    def test_mode_and_key_traces_invalidate_a_successful_mock_check(self):
        self.select("upbit", "KRW-BTC")
        self.app._begin_connection_check()
        self.assertTrue(self.app._preflight.ok)
        self.app.key_vars["upbit"][0].set("dummy-changed")
        self.assertIsNone(self.app._preflight)
        self.app._begin_connection_check()
        self.assertTrue(self.app._preflight.ok)
        self.app.mode.set("live")
        self.assertIsNone(self.app._preflight)
        self.app.mode.set("mock")
        self.app._begin_connection_check()
        self.assertTrue(self.app._preflight.ok)
        self.app.member_key.set("dummy-member-change")
        self.assertIsNone(self.app._preflight)

    def test_button_labels_and_ip_copy_are_real_tk_controls(self):
        self.select("bithumb", "KRW-BTC")
        self.assertEqual(self.app.ip_btn.cget("text"), "공인 IPv4 확인")
        self.assertEqual(self.app.ip_copy_btn.cget("text"), "IPv4 복사")
        self.assertEqual(self.app.connection_btn.cget("text"), "연결 검사")
        self.app._public_ip = "8.8.8.8"  # dummy fixture, not an IP-service request
        from runner import connection
        self.app._public_ip_cache.result = connection.PublicIPResult(True, "8.8.8.8", "fixture")
        self.app._copy_public_ip()
        self.assertEqual(self.root.clipboard_get(), "8.8.8.8")

    def pump_until_idle(self):
        deadline = time.monotonic() + 3
        while self.app._public_ip_busy and time.monotonic() < deadline:
            self.root.update()
            threading.Event().wait(.01)
        self.assertFalse(self.app._public_ip_busy)
        self.root.update_idletasks()

    def open_wizard(self, exchange="upbit"):
        self.select(exchange, "KRW-BTC")
        self.root.deiconify()
        self.app._open_connection_wizard()
        self.root.update()
        return self.app._connection_wizard

    def test_wizard_automatically_queries_once_and_reopening_reuses_the_cache(self):
        with patch("runner.connection.detect_public_ipv4", return_value="8.8.8.8") as detect:
            wizard = self.open_wizard()
            self.pump_until_idle()
            self.assertEqual(detect.call_count, 1)
            self.assertIn("8.8.8.8", wizard.ip_status.cget("text"))
            self.app._close_connection_wizard()
            self.app._open_connection_wizard()
            self.root.update()
            self.assertEqual(detect.call_count, 1)

    def test_minimum_size_all_steps_have_visible_navigation_and_scrollable_content(self):
        with patch("runner.connection.detect_public_ipv4", return_value="8.8.8.8"):
            wizard = self.open_wizard()
            self.pump_until_idle()
            for geometry in ("640x650", "560x610"):
                wizard.window.geometry(geometry)
                for step in range(3):
                    self.app.mode.set("live")
                    wizard.go(step)
                    self.root.update()
                    self.assertGreater(wizard.canvas.winfo_height(), 150)
                    for button in (wizard.back_btn, wizard.next_btn):
                        bottom = button.winfo_rooty() - wizard.window.winfo_rooty() + button.winfo_height()
                        self.assertLessEqual(bottom, wizard.window.winfo_height())
                    self.assertTrue(wizard.next_btn.winfo_viewable())
                self.assertTrue(hasattr(wizard, "save_btn"))
                wizard.canvas.yview_moveto(0)
                self.root.update()
                button = wizard.save_btn
                y = button.winfo_rooty() - wizard.body.winfo_rooty()
                wizard.canvas.yview_moveto(y / max(1, wizard.body.winfo_height()))
                self.root.update()
                bottom = button.winfo_rooty() - wizard.canvas.winfo_rooty() + button.winfo_height()
                self.assertLessEqual(bottom, wizard.canvas.winfo_height())
                self.assertGreater(button.winfo_rooty(), wizard.canvas.winfo_rooty())

    def test_mode_choices_remain_fully_readable_at_minimum_width_and_high_dpi(self):
        self.root.tk.call("tk", "scaling", 2.0)
        with patch("runner.connection.detect_public_ipv4", return_value="8.8.8.8"):
            wizard = self.open_wizard()
            self.pump_until_idle()
            wizard.window.geometry("560x610")
            wizard.go(2); self.root.update()
            modes = [widget for widget in wizard.frames[2].winfo_children()
                     if isinstance(widget, tk.ttk.Frame) and any(isinstance(child, tk.ttk.Radiobutton) for child in widget.winfo_children())][0]
            choices = modes.winfo_children()
            self.assertEqual(len(choices), 2)
            for choice in choices:
                self.assertGreaterEqual(choice.winfo_width(), choice.winfo_reqwidth())
                right = choice.winfo_rootx() + choice.winfo_width()
                self.assertLessEqual(right, wizard.canvas.winfo_rootx() + wizard.canvas.winfo_width())

    def test_wizard_reuses_key_variables_and_never_exposes_duplicate_main_key_fields(self):
        with patch("runner.connection.detect_public_ipv4", return_value="8.8.8.8"):
            wizard = self.open_wizard("bithumb")
            self.pump_until_idle()
            self.app.mode.set("live"); wizard.go(2); self.root.update()
            entries = [widget for widget in wizard.key_frame.winfo_children() if isinstance(widget, tk.ttk.Entry)]
            self.assertEqual(len(entries), 2)
            for entry, variable in zip(entries, self.app.key_vars["bithumb"]):
                self.assertEqual(str(entry.cget("textvariable")), str(variable))
            self.assertTrue(all(not widget.winfo_manager() for widgets in self.app.key_rows.values() for widget in widgets))
            entries[0].insert(0, "fixture-local-key")
            self.assertEqual(self.app.key_vars["bithumb"][0].get(), "fixture-local-key")
            self.assertEqual(str(self.app.start_btn.cget("state")), "disabled")
            self.app._close_connection_wizard()
            self.assertTrue(all(widget.winfo_manager() for widget in self.app.key_rows["bithumb"]))

    def test_failure_and_expiry_disable_copy_without_retaining_an_old_visible_address(self):
        from runner import connection
        with patch("runner.connection.detect_public_ipv4", return_value="8.8.8.8"):
            wizard = self.open_wizard()
            self.pump_until_idle()
        with patch("runner.connection.detect_public_ipv4", side_effect=connection.PublicIPError("ProxyError SECRET")):
            self.app._begin_public_ip(force=True)
            self.assertEqual(self.app._public_ip, "")
            self.pump_until_idle()
            self.assertEqual(str(wizard.ip_copy.cget("state")), "disabled")
            self.assertNotIn("8.8.8.8", wizard.ip_status.cget("text"))
            self.assertNotIn("SECRET", wizard.ip_status.cget("text"))
        result = connection.PublicIPResult(True, "8.8.8.8", "fixture", checked_at=time.monotonic() - 121)
        self.app._public_ip_cache.result = result
        self.app._expire_public_ip_result(result)
        self.assertEqual(str(wizard.ip_copy.cget("state")), "disabled")
        self.assertIn("만료", wizard.ip_status.cget("text"))

    def test_close_reopen_while_query_inflight_shares_request_and_eventually_leaves_busy(self):
        gate, entered = threading.Event(), threading.Event()
        def detect(_http):
            entered.set(); gate.wait(2)
            return "8.8.8.8"
        with patch("runner.connection.detect_public_ipv4", side_effect=detect) as mock:
            old = self.open_wizard()
            self.assertTrue(entered.wait(1))
            self.app._close_connection_wizard()
            self.app._open_connection_wizard(); self.root.update()
            new = self.app._connection_wizard
            self.assertIsNot(new, old)
            self.assertTrue(old.closed)
            gate.set()
            self.pump_until_idle()
            self.assertEqual(mock.call_count, 1)
            self.assertIn("8.8.8.8", new.ip_status.cget("text"))

    def test_mode_mock_has_no_key_fields_and_check_or_close_never_starts_a_macro(self):
        with patch("runner.connection.detect_public_ipv4", return_value="8.8.8.8"):
            wizard = self.open_wizard()
            self.pump_until_idle()
            wizard.go(2); self.root.update()
            self.assertEqual(wizard.key_frame.winfo_manager(), "")
            with patch.object(self.app, "_start") as start:
                wizard.check_btn.invoke()
                self.assertIn("검사하지", wizard.check_status.cget("text"))
                self.assertEqual(str(self.app.start_btn.cget("state")), "disabled")
                wizard.next_btn.invoke()
            start.assert_not_called()

    def test_escape_closes_the_real_window_and_key_entry_accepts_keyboard_focus(self):
        with patch("runner.connection.detect_public_ipv4", return_value="8.8.8.8"):
            wizard = self.open_wizard()
            self.pump_until_idle()
            self.app.mode.set("live"); wizard.go(2); self.root.update()
            entry = [w for w in wizard.key_frame.winfo_children() if isinstance(w, tk.ttk.Entry)][0]
            entry.focus_force(); self.root.update()
            self.assertIs(wizard.window.focus_get(), entry)
            entry.event_generate("<Escape>"); self.root.update()
            self.assertIsNone(self.app._connection_wizard)
            self.assertFalse(wizard.window.winfo_exists())

    def test_exchange_change_closes_old_wizard_and_rejects_its_late_ip(self):
        gate, entered = threading.Event(), threading.Event()
        def detect(_http):
            entered.set(); gate.wait(2)
            return "8.8.8.8"
        with patch("runner.connection.detect_public_ipv4", side_effect=detect):
            old = self.open_wizard()
            self.assertTrue(entered.wait(1))
            self.select("bithumb", "KRW-BTC")
            self.assertTrue(old.closed)
            self.assertIsNone(self.app._connection_wizard)
            gate.set(); self.pump_until_idle()
            self.assertEqual(self.app._public_ip, "")
            self.assertEqual(str(self.app.ip_copy_btn.cget("state")), "disabled")

    def test_explicit_optional_storage_and_official_link_use_no_network_or_new_variables(self):
        with patch("runner.connection.detect_public_ipv4", return_value="8.8.8.8"):
            wizard = self.open_wizard("bithumb")
            self.pump_until_idle()
            self.app.mode.set("live"); wizard.go(2); self.root.update()
            self.app.key_vars["bithumb"][0].set("dummy-key")
            self.app.key_vars["bithumb"][1].set("dummy-secret")
            self.app.remember.set(True)
            self.save_mock.assert_not_called()
            wizard.save_btn.invoke()
            self.save_mock.assert_called_once()
            values = self.save_mock.call_args.args[3]
            self.assertEqual(values["version"], 2)
            self.assertEqual(values["exchanges"]["bithumb"]["api_key"], "dummy-key")
            with patch("runner.macro_runner.webbrowser.open", return_value=True) as open_page:
                self.app._open_exchange_api_page()
            open_page.assert_called_once_with("https://www.bithumb.com/react/api-support/management-api", new=2)

    def test_existing_keys_open_check_step_without_any_issuance_steps(self):
        self.app.key_vars["upbit"][0].set("fixture-key")
        self.app.key_vars["upbit"][1].set("fixture-secret")
        with patch("runner.connection.detect_public_ipv4", return_value="8.8.8.8"):
            wizard = self.open_wizard(); self.pump_until_idle()
        self.assertEqual(wizard.step, 2)
        self.assertIn("기존 키", wizard.reuse_status.cget("text"))
        self.assertEqual(self.app._run_mode(), "mock")

    def test_no_key_panel_offers_existing_key_input_without_issuance(self):
        with patch("runner.connection.detect_public_ipv4", return_value="8.8.8.8"):
            wizard = self.open_wizard(); self.pump_until_idle()
            self.assertEqual(wizard.step, 0)
            wizard.existing_key_btn.invoke(); self.root.update()
            self.assertEqual(wizard.step, 2)

    def test_saved_key_dirty_state_and_selected_remember_choice_are_actual_widgets(self):
        saved = {"version": 2, "member_key": "fixture-member", "exchanges": {"upbit": {"api_key": "fixture-key", "api_secret": "fixture-secret"}}}
        self.app._apply_remembered_credentials(saved)
        with patch("runner.connection.detect_public_ipv4", return_value="8.8.8.8"):
            wizard = self.open_wizard(); self.pump_until_idle()
            self.assertEqual(wizard.step, 2)
            self.assertTrue(self.app.remember.get())
            self.assertIn("저장된 키", wizard.reuse_status.cget("text"))
            self.app.key_vars["upbit"][1].set("fixture-edited")
            self.assertIn("저장 필요", wizard.reuse_status.cget("text"))
            self.assertNotIn("저장 완료", wizard.storage_status.cget("text"))
            self.select("bithumb", "KRW-BTC")
            self.assertFalse(self.app.remember.get())
            self.select("upbit", "KRW-BTC")
            self.assertTrue(self.app.remember.get())

    def test_main_start_stop_controls_are_visible_at_minimum_window_size(self):
        self.select("upbit", "KRW-BTC")
        self.root.deiconify(); self.root.geometry("600x620"); self.root.update()
        for button in (self.app.start_btn, self.app.stop_btn, self.app.close_btn):
            self.assertTrue(button.winfo_viewable())
            bottom = button.winfo_rooty() - self.root.winfo_rooty() + button.winfo_height()
            self.assertLessEqual(bottom, self.root.winfo_height())

    def assert_control_inside_canvas(self, control, canvas):
        top = control.winfo_rooty() - canvas.winfo_rooty()
        self.assertGreaterEqual(top, 0)
        self.assertLessEqual(top + control.winfo_height(), canvas.winfo_height())

    def tab_until(self, first, target):
        first.focus_force(); self.root.update()
        for _ in range(30):
            if first.winfo_toplevel().focus_get() is target:
                return
            first.winfo_toplevel().focus_get().event_generate("<Tab>")
            self.root.update()
        self.fail("Tab did not reach the selected exchange input")

    def test_main_tab_reveals_selected_key_input_in_small_scroll_area(self):
        self.select("upbit", "KRW-BTC")
        self.root.deiconify(); self.root.geometry("600x620"); self.root.update()
        self.app.form_canvas.yview_moveto(0); self.root.update()
        target = self.app.key_rows["upbit"][1]
        self.tab_until(self.app.pick_btn, target)
        self.assert_control_inside_canvas(target, self.app.form_canvas)

    def test_wizard_tab_reveals_key_input_without_scrolling_main_window(self):
        with patch("runner.connection.detect_public_ipv4", return_value="8.8.8.8"):
            wizard = self.open_wizard(); self.pump_until_idle()
            self.app.mode.set("live"); wizard.go(2)
            wizard.window.geometry("560x610"); self.root.update()
            wizard.canvas.yview_moveto(0); self.root.update()
            main_view = self.app.form_canvas.yview()
            target = [w for w in wizard.key_frame.winfo_children() if isinstance(w, tk.ttk.Entry)][0]
            self.tab_until(wizard.key_ip_retry, target)
            self.assert_control_inside_canvas(target, wizard.canvas)
            self.assertEqual(self.app.form_canvas.yview(), main_view)


if __name__ == "__main__":
    unittest.main()
