"""Optional real-Tk smoke tests (local virtual display, no keys or network)."""
import os
import unittest
from unittest.mock import patch

try:
    import tkinter as tk
except ImportError:
    tk = None


@unittest.skipUnless(tk is not None and os.environ.get("DISPLAY"), "real Tk and a local test display are required")
class ConnectionTkTests(unittest.TestCase):
    def setUp(self):
        from runner.macro_runner import RunnerApp
        self.root = tk.Tk()
        self.root.withdraw()
        with patch("runner.macro_runner.credentials_mod.supported", return_value=True), patch("runner.macro_runner.credentials_mod.load", return_value=None):
            self.app = RunnerApp(self.root)
        self.root.update_idletasks()

    def tearDown(self):
        self.root.destroy()

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
        self.app._copy_public_ip()
        self.assertEqual(self.root.clipboard_get(), "8.8.8.8")


if __name__ == "__main__":
    unittest.main()
