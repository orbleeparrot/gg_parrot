"""Optional real-Tk smoke tests (local virtual display, no keys or network).

v13 화면: 메인 카드 넷(매크로 · 실행 모드 · '{거래소} API 키' · 껄무새 회원 키)과 같은 창 안에서 바뀌는 연결 도우미.
"""
import os
import threading
import time
import unittest
from copy import deepcopy
from unittest.mock import patch

try:
    import tkinter as tk
    from tkinter import ttk
except ImportError:
    tk = None


@unittest.skipUnless(tk is not None and os.environ.get("DISPLAY"), "real Tk and a local test display are required")
class ConnectionTkTests(unittest.TestCase):
    def setUp(self):
        from runner import connection
        from runner.macro_runner import RunnerApp
        self.connection = connection
        for target in ("requests.Session.request", "socket.socket.connect"):
            block = patch(target, side_effect=AssertionError("unmocked network I/O is forbidden"))
            block.start()
            self.addCleanup(block.stop)
        self.root = tk.Tk()
        self.root.withdraw()
        self._tk_errors = []
        self.root.report_callback_exception = lambda _kind, exc, _tb: self._tk_errors.append(exc)
        self.support = patch("runner.macro_runner.credentials_mod.supported", return_value=True)
        self.wizard_support = patch("runner.connection_wizard.credentials.supported", return_value=True)
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
        # 연결 검사는 거래소에 묻지 않는다 — 다음 결과를 테스트가 정한다.
        self.next_check = (True, "", "검증 통과")
        self.check_calls = []
        def fake_check(macro, credentials, mode):
            self.check_calls.append((macro.get("exchange"), mode, deepcopy(credentials.get("exchanges", {}))))
            ok, name, reason = self.next_check
            return connection.PreflightResult(ok, reason, connection.preflight_fingerprint(macro, mode, credentials),
                                              error_name=name)
        self.perform = patch("runner.macro_runner.perform_connection_check", side_effect=fake_check)
        self.browser = patch("runner.macro_runner.webbrowser.open", return_value=True)
        self.browser_mock = self.browser.start()
        for item in (self.support, self.wizard_support, self.load, self.perform):
            item.start()
            self.addCleanup(item.stop)
        self.addCleanup(self.save.stop)
        self.addCleanup(self.browser.stop)
        self.app = RunnerApp(self.root)
        self.root.update_idletasks()

    def tearDown(self):
        self.app._on_window_close()
        self.assertEqual(self._tk_errors, [])

    # --- 도우미 함수 ------------------------------------------------
    def select(self, exchange, symbol=None):
        symbol = symbol or ("BTCUSDT" if exchange == "binance" else "KRW-BTC")
        self.app._apply_local_macro({"exchange": exchange, "symbol": symbol, "position_side": "long", "leverage": 1,
                                     "params": {"initial_capital": 1000000}}, "example.ggm.json")
        self.root.update_idletasks()

    def pump(self, condition=None, seconds=3.0):
        deadline = time.monotonic() + seconds
        while time.monotonic() < deadline:
            self.root.update()
            if condition is not None and condition():
                return True
            threading.Event().wait(.01)
        return condition is None

    def set_keys(self, exchange, key="A" * 40, secret="B" * 40):
        self.app.key_vars[exchange][0].set(key)
        self.app.key_vars[exchange][1].set(secret)

    def open_wizard(self, exchange="upbit"):
        self.select(exchange)
        self.root.deiconify()
        self.app._open_connection_wizard()
        self.root.update()
        return self.app._connection_wizard

    def pump_ip(self):
        self.assertTrue(self.pump(lambda: not self.app._public_ip_busy))
        self.root.update_idletasks()

    def copy_to_clipboard(self, text):
        self.root.clipboard_clear()
        self.root.clipboard_append(text)
        self.root.update()

    def foot_button(self, wizard, text):
        for widget in wizard.foot.winfo_children():
            if isinstance(widget, ttk.Button) and widget.cget("text") == text:
                return widget
        self.fail(f"no foot button {text!r}")

    # --- 메인 화면 --------------------------------------------------
    def test_exchange_selection_shows_only_that_exchange_fields_and_preserves_values(self):
        self.app.key_vars["upbit"][0].set("dummy-upbit")
        self.select("upbit")
        for name, widgets in self.app.key_rows.items():
            self.assertTrue(all(bool(widget.winfo_manager()) == (name == "upbit") for widget in widgets))
        self.assertEqual(self.app.key_title.cget("text"), "업비트 API 키")
        self.assertEqual(self.app.key_rows["upbit"][0].cget("text"), "Access Key")
        self.assertEqual(self.app.wizard_btn.winfo_manager(), "pack")
        self.select("binance")
        self.assertEqual(self.app.key_title.cget("text"), "바이낸스 API 키")
        self.assertEqual(self.app.wizard_btn.winfo_manager(), "")  # 바이낸스는 도우미가 없다
        self.select("upbit")
        self.assertEqual(self.app.key_vars["upbit"][0].get(), "dummy-upbit")

    def test_mode_and_key_traces_invalidate_a_successful_mock_check(self):
        self.select("upbit")
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

    def test_testnet_only_for_binance_and_mode_segments_share_the_width(self):
        self.root.deiconify()
        self.select("upbit"); self.pump(seconds=.2)
        buttons = self.app.mode_buttons
        self.assertEqual(buttons["testnet"].winfo_manager(), "")
        self.assertLessEqual(abs(buttons["mock"].winfo_width() - buttons["live"].winfo_width()), 2)
        self.select("binance"); self.pump(seconds=.2)
        widths = [buttons[name].winfo_width() for name in ("mock", "testnet", "live")]
        self.assertLessEqual(max(widths) - min(widths), 2)

    def test_key_card_status_follows_the_automatic_connection_check(self):
        self.select("upbit")
        self.assertEqual(self.app.key_status.cget("text"), "● 연결 안 됨")
        self.assertEqual(str(self.app.wizard_btn.cget("style")), "Primary.TButton")
        self.set_keys("upbit")
        self.assertEqual(self.app.key_status.cget("text"), "● 확인 중")
        self.assertTrue(self.pump(lambda: self.app.key_status.cget("text") == "✓ 연결됨"))
        self.assertEqual(str(self.app.wizard_btn.cget("style")), "Ghost.TButton")
        self.assertEqual(len(self.check_calls), 1)
        self.assertEqual(self.check_calls[0][1], "live")  # 주문 없는 실거래 쪽 검사
        self.pump(seconds=1.0)
        self.assertEqual(len(self.check_calls), 1)  # 같은 키로 다시 묻지 않는다
        self.next_check = (False, "no_authorization_ip", "업비트 가 요청을 받아주지 않았어요")
        self.app.key_vars["upbit"][1].set("C" * 40)
        self.assertTrue(self.pump(lambda: self.app.key_status.cget("text") == "● 허용 IP 확인 필요"))
        self.assertEqual(str(self.app.wizard_btn.cget("style")), "Primary.TButton")

    def test_binance_failure_reason_is_shown_under_the_opened_keys(self):
        self.root.deiconify()
        self.select("binance")
        self.assertTrue(self.app._key_body_open)  # 키가 없는 바이낸스는 펼쳐 둔다
        self.next_check = (False, "binance_access", "바이낸스가 키를 받아주지 않았어요")
        self.set_keys("binance")
        self.assertTrue(self.pump(lambda: self.app.key_status.cget("text") == "● 연결 안 됨" and not self.app._key_check_busy_fp
                                  and self.app.key_note.winfo_ismapped()))
        self.assertIn("바이낸스가 키를 받아주지 않았어요", self.app.key_note.cget("text"))

    def test_member_key_has_its_own_card_below_the_exchange_keys(self):
        self.assertEqual(str(self.app.member_entry.cget("textvariable")), str(self.app.member_key))
        self.assertFalse(str(self.app.member_entry).startswith(str(self.app.key_card)))
        self.root.deiconify(); self.select("upbit"); self.root.update()
        self.assertGreater(self.app.member_entry.winfo_rooty(), self.app.key_card.winfo_rooty())

    def test_remember_checkbox_saves_now_and_unchecking_deletes_only_this_exchange(self):
        self.select("upbit")
        self.app.remember.set(True); self.app._on_remember_toggled()
        self.save_mock.assert_not_called()  # 키가 다 차기 전에는 저장하지 않는다
        self.set_keys("upbit")
        self.app.remember.set(True); self.app._on_remember_toggled()
        self.assertEqual(self.save_mock.call_args.args[1:3], ("upbit", True))
        self.app.remember.set(False); self.app._on_remember_toggled()
        self.assertEqual(self.save_mock.call_args.args[1:3], ("upbit", False))
        self.assertEqual(self.app.key_vars["upbit"][0].get(), "A" * 40)  # 저장만 지우고 칸은 그대로

    def test_main_start_stop_controls_are_visible_at_minimum_window_size(self):
        self.select("upbit")
        self.root.deiconify(); self.root.geometry("600x620"); self.root.update()
        for button in (self.app.start_btn, self.app.stop_btn, self.app.close_btn):
            self.assertTrue(button.winfo_viewable())
            bottom = button.winfo_rooty() - self.root.winfo_rooty() + button.winfo_height()
            self.assertLessEqual(bottom, self.root.winfo_height())

    def test_main_tab_reveals_selected_key_input_in_small_scroll_area(self):
        self.select("upbit")
        self.app._toggle_key_body(True)
        self.root.deiconify(); self.root.geometry("600x620"); self.root.update()
        self.app.form_canvas.yview_moveto(0); self.root.update()
        target = self.app.key_rows["upbit"][3]
        self.app.key_rows["upbit"][1].focus_force(); self.root.update()
        self.app.key_rows["upbit"][1].event_generate("<Tab>"); self.root.update(); self.root.update()
        self.assertIs(self.root.focus_get(), target)
        top = target.winfo_rooty() - self.app.form_canvas.winfo_rooty()
        self.assertGreaterEqual(top, 0)
        self.assertLessEqual(top + target.winfo_height(), self.app.form_canvas.winfo_height())

    def test_key_inputs_show_text_while_eye_is_on(self):
        self.select("upbit")
        self.app._toggle_key_body(True)
        entries = [w for w in self.app.key_rows["upbit"] if hasattr(w, "eye")] + [self.app.member_entry]
        self.assertEqual(len(entries), 3)
        for entry in entries:
            self.assertEqual(str(entry.cget("show")), "•")
            entry.eye.toggle()
            self.assertEqual(str(entry.cget("show")), "")
            entry.eye.toggle()
            self.assertEqual(str(entry.cget("show")), "•")

    def test_live_mode_is_green_not_red(self):
        self.select("upbit")
        self.app.mode.set("live"); self.app._on_mode_change()
        self.assertEqual(str(self.app.mode_buttons["live"].cget("selectcolor")).upper(), "#15803D")

    def test_start_without_domestic_keys_opens_the_helper_instead_of_an_error(self):
        self.select("upbit")
        self.app.mode.set("live")
        self.app.member_key.set("member-fixture")
        with patch("runner.macro_runner.messagebox.showwarning") as warned:
            self.app._start()
        warned.assert_not_called()
        self.assertIsNotNone(self.app._connection_wizard)
        self.assertEqual(self.app._connection_wizard.stage, "ask")

    # --- 연결 도우미(같은 창) ------------------------------------------
    def test_helper_replaces_the_main_view_in_the_same_window_and_escape_returns(self):
        self.select("upbit")
        self.root.deiconify(); self.root.update()
        size = (self.root.winfo_width(), self.root.winfo_height())
        with patch("runner.connection.detect_public_ipv4", return_value="8.8.8.8"):
            wizard = self.open_wizard()
            self.pump_ip()
        self.assertEqual(self.app.main_view.winfo_manager(), "")
        self.assertTrue(wizard.frame.winfo_ismapped())
        self.assertEqual((self.root.winfo_width(), self.root.winfo_height()), size)  # 창 크기 그대로
        self.assertEqual(self.app.status_lbl.cget("text"), "● 연결 도우미")
        self.assertEqual(str(self.app.start_btn.cget("state")), "disabled")
        self.root.event_generate("<Escape>"); self.root.update()
        self.assertIsNone(self.app._connection_wizard)
        self.assertEqual(self.app.main_view.winfo_manager(), "pack")
        self.assertEqual(self.app.status_lbl.cget("text"), "● 대기 중")

    def test_every_stage_keeps_the_exchange_shortcuts_and_footer_inside_the_minimum_window(self):
        with patch("runner.connection.detect_public_ipv4", return_value="8.8.8.8"):
            wizard = self.open_wizard()
            self.pump_ip()
            self.root.geometry("600x620")
            for stage in ("ask", "ip", "perm", "keys", "existing", "manual", "checking", "result_ok", "result_fail"):
                wizard.path = "existing" if stage == "existing" else "new"
                with patch.object(self.app, "_open_exchange_api_page"):
                    wizard.show(stage)
                self.root.update()
                for button in (wizard.home_btn, wizard.api_btn):
                    self.assertTrue(button.winfo_viewable(), stage)
                for button in wizard.foot.winfo_children():
                    bottom = button.winfo_rooty() - self.root.winfo_rooty() + button.winfo_height()
                    self.assertLessEqual(bottom, self.root.winfo_height(), stage)
                    self.assertTrue(button.winfo_viewable(), stage)
                self.assertGreater(wizard.canvas.winfo_height(), 150, stage)

    def test_helper_queries_the_address_once_and_reopening_reuses_the_cache(self):
        with patch("runner.connection.detect_public_ipv4", return_value="8.8.8.8") as detect:
            wizard = self.open_wizard()
            self.pump_ip()
            self.assertEqual(detect.call_count, 1)
            wizard._choose_new(); self.root.update()
            self.assertEqual(wizard.ip_value.cget("text"), "8.8.8.8")
            self.app._close_connection_wizard()
            self.app._open_connection_wizard(); self.root.update()
            self.app._connection_wizard._choose_new(); self.root.update()
            self.assertEqual(detect.call_count, 1)

    def test_copy_and_open_puts_the_address_on_the_clipboard_and_returning_moves_on(self):
        with patch("runner.connection.detect_public_ipv4", return_value="8.8.8.8"):
            wizard = self.open_wizard()
            self.pump_ip()
            wizard._choose_new(); self.root.update()
            wizard.ip_open.invoke(); self.root.update()
        self.assertEqual(self.root.clipboard_get(), "8.8.8.8")
        self.browser_mock.assert_called_with("https://www.upbit.com/mypage/open_api_management", new=2)
        wizard.on_return(); self.root.update()
        self.assertEqual(wizard.stage, "perm")

    def test_shortcuts_open_only_the_official_exchange_pages(self):
        with patch("runner.connection.detect_public_ipv4", return_value="8.8.8.8"):
            wizard = self.open_wizard("bithumb")
            self.pump_ip()
        wizard.home_btn.invoke(); wizard.api_btn.invoke()
        self.assertEqual([call.args[0] for call in self.browser_mock.call_args_list],
                         ["https://www.bithumb.com/", "https://www.bithumb.com/react/api-support/management-api"])

    def test_failure_and_expiry_never_show_an_old_address(self):
        connection = self.connection
        with patch("runner.connection.detect_public_ipv4", return_value="8.8.8.8"):
            wizard = self.open_wizard()
            self.pump_ip()
            wizard._choose_new(); self.root.update()
        with patch("runner.connection.detect_public_ipv4", side_effect=connection.PublicIPError("ProxyError SECRET")):
            self.app._begin_public_ip(force=True)
            self.assertEqual(self.app._public_ip, "")
            self.pump_ip()
        self.assertEqual(wizard.ip_value.cget("text"), "확인 실패")
        self.assertNotIn("SECRET", wizard.ip_value.cget("text"))
        self.assertEqual(str(wizard.ip_open.cget("state")), "disabled")
        self.assertEqual(wizard.ip_retry.winfo_manager(), "pack")
        result = connection.PublicIPResult(True, "8.8.8.8", "fixture", checked_at=time.monotonic() - 121)
        self.app._public_ip_cache.result = result
        with patch.object(self.app, "_begin_public_ip") as again:
            self.app._expire_public_ip_result(result)
            self.pump(seconds=.2)
        self.assertNotIn("8.8.8.8", wizard.ip_value.cget("text"))
        self.assertEqual(str(wizard.ip_copy.cget("state")), "disabled")
        again.assert_called()  # 이 단계에 있으면 만료된 주소를 저절로 다시 본다

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
            self.pump_ip()
            self.assertEqual(mock.call_count, 1)
            new._choose_new(); self.root.update()
            self.assertEqual(new.ip_value.cget("text"), "8.8.8.8")

    def test_exchange_change_closes_old_helper_and_rejects_its_late_address(self):
        gate, entered = threading.Event(), threading.Event()
        def detect(_http):
            entered.set(); gate.wait(2)
            return "8.8.8.8"
        with patch("runner.connection.detect_public_ipv4", side_effect=detect):
            old = self.open_wizard()
            self.assertTrue(entered.wait(1))
            self.select("bithumb")
            self.assertTrue(old.closed)
            self.assertIsNone(self.app._connection_wizard)
            self.assertEqual(self.app.main_view.winfo_manager(), "pack")
            gate.set(); self.pump_ip()
            self.assertEqual(self.app._public_ip, "")

    def test_copied_keys_fill_in_order_advance_clear_the_clipboard_and_check_once(self):
        with patch("runner.connection.detect_public_ipv4", return_value="8.8.8.8"):
            wizard = self.open_wizard()
            self.pump_ip()
        wizard.path = "new"; wizard.show("perm")
        with patch.object(self.app, "_start") as start:
            self.copy_to_clipboard("A" * 40)
            self.assertTrue(self.pump(lambda: wizard.stage == "keys"))  # 키가 들어오면 3단계로
            self.assertEqual(self.app.key_vars["upbit"][0].get(), "A" * 40)
            self.assertEqual(self.app.key_vars["upbit"][1].get(), "")
            self.assertEqual(wizard.got, {"access": True, "secret": False})
            self.copy_to_clipboard("B" * 40)
            self.assertTrue(self.pump(lambda: wizard.stage == "result_ok"))
            start.assert_not_called()  # 검사 · 완료는 매매 시작이 아니다
        self.assertEqual(self.app.key_vars["upbit"][1].get(), "B" * 40)
        self.assertEqual(len(self.check_calls), 1)
        self.assertEqual(self.check_calls[0][2]["upbit"], {"api_key": "A" * 40, "api_secret": "B" * 40})
        try:
            clipboard = self.root.clipboard_get()
        except tk.TclError:
            clipboard = ""
        self.assertNotIn("B" * 40, clipboard)  # 시크릿은 클립보드에 남기지 않는다

    def test_clipboard_ignores_old_values_addresses_and_sentences(self):
        self.copy_to_clipboard("Z" * 40)  # 도우미를 열기 전부터 있던 값은 받지 않는다
        with patch("runner.connection.detect_public_ipv4", return_value="8.8.8.8"):
            wizard = self.open_wizard()
            self.pump_ip()
        wizard.path = "new"; wizard.show("keys")
        self.pump(seconds=.6)
        for text in ("121.134.55.20", "hello world", "https://upbit.com/x"):
            self.copy_to_clipboard(text)
            self.pump(seconds=.5)
        self.assertEqual(wizard.got, {"access": False, "secret": False})
        self.assertEqual(self.app.key_vars["upbit"][0].get(), "")

    def test_existing_key_path_opens_the_api_page_and_can_switch_to_new_issuance(self):
        with patch("runner.connection.detect_public_ipv4", return_value="8.8.8.8"):
            wizard = self.open_wizard()
            self.pump_ip()
            wizard._choose_existing(); self.root.update()
            self.assertEqual(wizard.stage, "existing")
            self.browser_mock.assert_called_once_with("https://www.upbit.com/mypage/open_api_management", new=2)
            wizard._choose_new(); self.root.update()
        self.assertEqual((wizard.stage, wizard.path), ("ip", "new"))

    def test_failed_check_is_checked_again_when_the_user_comes_back(self):
        self.next_check = (False, "no_authorization_ip", "막힘")
        with patch("runner.connection.detect_public_ipv4", return_value="8.8.8.8"):
            wizard = self.open_wizard()
            self.pump_ip()
        wizard.path = "new"; wizard.show("keys")
        wizard.accept_key("A" * 40); wizard.accept_key("B" * 40)
        self.assertTrue(self.pump(lambda: wizard.stage == "result_fail"))
        self.next_check = (True, "", "검증 통과")
        wizard.on_return()
        self.assertTrue(self.pump(lambda: wizard.stage == "result_ok"))
        self.assertEqual(len(self.check_calls), 2)

    def test_manual_entry_uses_the_same_key_variables(self):
        with patch("runner.connection.detect_public_ipv4", return_value="8.8.8.8"):
            wizard = self.open_wizard("bithumb")
            self.pump_ip()
        wizard.path = "new"; wizard.show("manual"); self.root.update()
        for entry, variable in zip(wizard.manual_entries, self.app.key_vars["bithumb"]):
            self.assertEqual(str(entry.cget("textvariable")), str(variable))
        self.assertEqual(str(wizard.manual_check.cget("state")), "disabled")
        self.set_keys("bithumb"); self.root.update()
        self.assertEqual(str(wizard.manual_check.cget("state")), "normal")

    def test_done_returns_to_the_main_screen_with_a_connected_key_card(self):
        with patch("runner.connection.detect_public_ipv4", return_value="8.8.8.8"):
            wizard = self.open_wizard()
            self.pump_ip()
        wizard.path = "new"; wizard.show("keys")
        wizard.accept_key("A" * 40); wizard.accept_key("B" * 40)
        self.assertTrue(self.pump(lambda: wizard.stage == "result_ok"))
        with patch.object(self.app, "_start") as start:
            self.foot_button(wizard, "완료").invoke(); self.root.update()
        start.assert_not_called()
        self.assertIsNone(self.app._connection_wizard)
        self.assertEqual(self.app.key_status.cget("text"), "✓ 연결됨")

    def test_high_dpi_mode_choices_fit_their_card(self):
        self.root.tk.call("tk", "scaling", 2.0)
        self.root.deiconify()
        self.select("binance")
        self.root.geometry("600x620"); self.root.update()
        for button in self.app.mode_buttons.values():
            self.assertGreaterEqual(button.winfo_width(), button.winfo_reqwidth() - 2)
            right = button.winfo_rootx() + button.winfo_width()
            self.assertLessEqual(right, self.app.form_canvas.winfo_rootx() + self.app.form_canvas.winfo_width())


if __name__ == "__main__":
    unittest.main()
