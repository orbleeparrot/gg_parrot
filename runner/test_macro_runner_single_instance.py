from __future__ import annotations

import unittest
from unittest.mock import Mock, patch

from runner.protocol import ProtocolLaunch
from runner.single_instance import InstanceAck

try:
    from runner import macro_runner
    from runner.macro_runner import RunnerApp
except ModuleNotFoundError as exc:
    if exc.name != "tkinter":
        raise
    macro_runner = None
    RunnerApp = object


@unittest.skipIf(macro_runner is None, "Tk is not installed on this build host")
class RunnerActivationSafetyTests(unittest.TestCase):
    def setUp(self) -> None:
        self.launch = ProtocolLaunch(
            ticket="A" * 21 + "_" + "b" * 21,
            environment="local",
            version=2,
        )

    def app(self) -> RunnerApp:
        app = object.__new__(RunnerApp)
        app.root = Mock()
        app.bot = None
        app._protocol_claim_busy = False
        app._log = Mock()
        app._begin_protocol_claim = Mock()
        return app

    def test_external_launch_restores_existing_window_then_claims(self) -> None:
        app = self.app()
        with patch.object(macro_runner, "_bring_window_to_front") as foreground:
            app.handle_external_activation(self.launch)

        foreground.assert_called_once_with(app.root)
        app._begin_protocol_claim.assert_called_once_with(self.launch)

    def test_running_macro_is_never_replaced_by_external_launch(self) -> None:
        app = self.app()
        app.bot = object()
        with patch.object(macro_runner, "_bring_window_to_front") as foreground:
            app.handle_external_activation(self.launch)

        foreground.assert_called_once_with(app.root)
        app._begin_protocol_claim.assert_not_called()
        app._log.assert_called_once()
        app.root.bell.assert_called_once_with()

    def test_late_claim_response_cannot_overwrite_running_macro(self) -> None:
        app = self.app()
        app.bot = object()
        app._protocol_claim_busy = True
        app._apply_macro = Mock()
        app._set_running = Mock()

        app._apply_protocol_claim({"symbol": "BTCUSDT"}, "member-key", "https://example")

        self.assertFalse(app._protocol_claim_busy)
        app._apply_macro.assert_not_called()
        app._set_running.assert_called_once_with(True)

    def test_start_guard_blocks_a_second_bot(self) -> None:
        app = self.app()
        app.bot = object()
        with patch.object(macro_runner.messagebox, "showwarning") as warning:
            app._start()
        warning.assert_called_once()

    def test_ticket_claim_identifies_v5_without_sending_exchange_keys(self) -> None:
        app = self.app()
        app.server_base = "https://example.invalid"
        response = Mock()
        response.json.return_value = {
            "macro": {"symbol": "BTCUSDT"},
            "runner_key": "ggp_member",
        }
        fake_requests = Mock()
        fake_requests.post.return_value = response

        with patch.object(macro_runner, "requests", fake_requests):
            app._claim_protocol_ticket(self.launch)

        sent = fake_requests.post.call_args.kwargs["json"]
        self.assertEqual(sent["runner_version"], "5")
        self.assertEqual(sent["ticket"], self.launch.ticket)
        self.assertNotIn("api_key", sent)
        self.assertNotIn("api_secret", sent)

    def test_running_state_disables_start_and_macro_replacement(self) -> None:
        app = self.app()
        app.start_btn = Mock()
        app.pick_btn = Mock()
        app.stop_btn = Mock()
        app.close_btn = Mock()

        app._set_running(True)

        app.start_btn.config.assert_called_once_with(state="disabled")
        app.pick_btn.config.assert_called_once_with(state="disabled")
        app.stop_btn.config.assert_called_once_with(state="normal")
        app.close_btn.config.assert_called_once_with(state="normal")

    def test_secondary_protocol_process_exits_before_constructing_tk(self) -> None:
        secondary = Mock()
        secondary.is_primary = False
        secondary.handoff.return_value = InstanceAck(True)
        uri = (
            "ggparrot://launch/?v=2&env=local&ticket="
            + self.launch.ticket
        )

        with (
            patch.object(macro_runner.sys, "platform", "win32"),
            patch.object(macro_runner.sys, "frozen", True, create=True),
            patch.object(macro_runner.sys, "argv", ["runner.exe", "--protocol", uri]),
            patch.object(macro_runner.RunnerSingleInstance, "acquire", return_value=secondary),
            patch.object(macro_runner.tk, "Tk") as tk_constructor,
        ):
            macro_runner.main()

        tk_constructor.assert_not_called()
        secondary.handoff.assert_called_once()
        forwarded = secondary.handoff.call_args.args[0]
        self.assertEqual(forwarded.launch, self.launch)

    def test_manual_secondary_reopen_repairs_protocol_then_focuses_primary(self) -> None:
        secondary = Mock()
        secondary.is_primary = False
        secondary.handoff.return_value = InstanceAck(True)

        with (
            patch.object(macro_runner.sys, "platform", "win32"),
            patch.object(macro_runner.sys, "frozen", True, create=True),
            patch.object(macro_runner.sys, "argv", ["runner.exe"]),
            patch.object(macro_runner.RunnerSingleInstance, "acquire", return_value=secondary),
            patch.object(
                macro_runner,
                "_install_protocol_handler_for_current_user",
                return_value=True,
            ) as repair,
            patch.object(macro_runner.tk, "Tk") as tk_constructor,
        ):
            macro_runner.main()

        repair.assert_called_once_with()
        tk_constructor.assert_not_called()
        secondary.handoff.assert_called_once()
        self.assertIsNone(secondary.handoff.call_args.args[0].launch)


if __name__ == "__main__":
    unittest.main()
