"""Only temporary local files and mocked HTTP; no trading/network is performed."""
import contextlib
import io
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import Mock, patch

from runner.test_macro_runner_single_instance import macro_runner


class DurableEventClientTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.path = Path(self.temporary.name) / "activity.sqlite3"

    def client(self, key="member-key-fixture", session=7, base="https://example.invalid", **kwargs):
        client = macro_runner.ServerClient(key, base=base, outbox_path=self.path, **kwargs)
        client.session_id = session
        return client

    def reply(self, keys=None):
        response = Mock(status_code=200)
        response.json.return_value = {} if keys is None else {"accepted_event_keys": keys}
        return response

    def test_http_success_without_per_event_ack_retains_original_event_after_restart(self):
        output = io.StringIO()
        with contextlib.redirect_stdout(output):
            first = self.client()
            original = first.push_event("info", "원래 실행한 메시지")
        with patch.object(macro_runner.requests, "post", return_value=self.reply()):
            first.heartbeat({})
        first.close_events()
        recovered = self.client()
        self.addCleanup(recovered.close_events)
        self.assertEqual(recovered._peek_events(), [original])
        self.assertEqual(json.loads(output.getvalue()), original)

    def test_over_100_events_are_batched_without_discard_and_only_sent_confirmed_keys_are_removed(self):
        client = self.client(cli_sink=lambda _event: None)
        self.addCleanup(client.close_events)
        originals = [client.push_event("info", f"원본 사건 {index}") for index in range(137)]
        first_batch = originals[:100]
        with patch.object(macro_runner.requests, "post", return_value=self.reply(
                [event["event_key"] for event in first_batch[:75]] + [originals[-1]["event_key"]])) as post:
            client.heartbeat({})
        self.assertEqual(post.call_args.kwargs["json"]["events"], first_batch)
        self.assertEqual(client._peek_events(), originals[75:])

    def test_same_key_other_session_account_and_server_never_mix(self):
        first = self.client(cli_sink=lambda _event: None)
        own = first.push_event("info", "이 세션")
        second = self.client(session=8, cli_sink=lambda _event: None)
        other = second.push_event("info", "다른 세션")
        account = self.client(key="other-member-key", cli_sink=lambda _event: None)
        server = self.client(base="https://other.example.invalid", cli_sink=lambda _event: None)
        for client in (first, second, account, server):
            self.addCleanup(client.close_events)
        self.assertEqual(first._peek_events(), [own])
        self.assertEqual(second._peek_events(), [other])
        self.assertEqual(account._peek_events(), [])
        self.assertEqual(server._peek_events(), [])

    def test_recovery_uses_only_events_endpoint_and_original_session_without_snapshot(self):
        original = self.client(session=5, cli_sink=lambda _event: None)
        event = original.push_event("error", "이전 세션의 실제 오류")
        original.close_events()
        current = self.client(session=9, cli_sink=lambda _event: None)
        self.addCleanup(current.close_events)
        with patch.object(macro_runner.requests, "post", return_value=self.reply([event["event_key"]])) as post:
            current.recover_events()
        self.assertEqual(post.call_args.args[0], "https://example.invalid/api/runner/sessions/5/events")
        self.assertEqual(post.call_args.kwargs["json"], {"events": [event]})
        self.assertEqual(current._event_outbox.sessions(current._event_namespace), [])

    def test_stopped_failed_final_upload_does_not_delete_events(self):
        client = self.client(cli_sink=lambda _event: None)
        event = client.push_event("stop", "종료 메시지")
        with patch.object(macro_runner.requests, "post", side_effect=TimeoutError()), \
                patch.object(macro_runner.time, "sleep"):
            self.assertFalse(client.stopped())
        client.close_events()
        recovered = self.client(cli_sink=lambda _event: None)
        self.addCleanup(recovered.close_events)
        self.assertEqual(recovered._peek_events(), [event])

    def test_output_and_disk_payload_redact_member_key_and_generic_secrets(self):
        emitted = []
        client = self.client(cli_sink=emitted.append)
        self.addCleanup(client.close_events)
        event = client.push_event("error", "member-key-fixture api_key=example-secret Bearer private-access-token",
                                  details={"error": "password=example-password https://provider.example/order?signature=private-signature",
                                           "api_key": "not-allowed"})
        self.assertEqual(emitted, [event])
        for secret in ("member-key-fixture", "example-secret", "private-access-token", "example-password", "not-allowed", "private-signature"):
            self.assertNotIn(secret, json.dumps(event))
        client.close_events()
        self.assertNotIn(b"member-key-fixture", self.path.read_bytes())

    def test_outbox_failure_is_explicit_and_keeps_memory_events_without_retrying_order(self):
        warnings = []
        client = self.client(cli_sink=lambda _event: None, on_event_warning=warnings.append)
        self.addCleanup(client.close_events)
        with patch.object(client._event_outbox, "append", side_effect=OSError("secret-path-fixture")):
            event = client.push_event("order", "실제 메시지")
        self.assertTrue(warnings)
        self.assertNotIn("secret-path-fixture", warnings[0])
        self.assertEqual(client._peek_events(), [event])

    def test_retries_do_not_regenerate_identity_or_reprint_cli_and_ack_removes_only_confirmed_keys(self):
        emitted = []
        client = self.client(cli_sink=emitted.append)
        self.addCleanup(client.close_events)
        with patch.object(macro_runner.uuid, "uuid4", wraps=macro_runner.uuid.uuid4) as identity:
            original = client.push_event("info", "한 번 실행한 사건")
            with patch.object(macro_runner.requests, "post", side_effect=TimeoutError()):
                client.heartbeat({})
            with patch.object(macro_runner.requests, "post", return_value=self.reply([original["event_key"]])) as post:
                client.heartbeat({})
            identity.assert_called_once()
        self.assertEqual(post.call_args.kwargs["json"]["events"], [original])
        self.assertEqual(emitted, [original])
        self.assertEqual(client._peek_events(), [])

    def test_same_uuid_is_immutable_and_disk_ack_is_account_and_session_scoped(self):
        client = self.client(cli_sink=lambda _event: None)
        self.addCleanup(client.close_events)
        event = client.push_event("info", "변하지 않는 원본")
        journal, scope = client._event_outbox, client._event_namespace
        with self.assertRaisesRegex(ValueError, "immutable"):
            journal.append(scope, 7, {**event, "message": "다른 내용"})
        journal.append(scope, 8, event)
        journal.append("different-account", 7, event)
        client._ack_events([event], {"accepted_event_keys": [event["event_key"]]})
        self.assertEqual(journal.peek(scope, 7), [])
        self.assertEqual(journal.peek(scope, 8), [event])
        self.assertEqual(journal.peek("different-account", 7), [event])

    def test_disk_write_failure_never_trims_overflow_and_only_warns_once(self):
        warnings = []
        client = self.client(cli_sink=lambda _event: None, on_event_warning=warnings.append)
        self.addCleanup(client.close_events)
        with patch.object(client._event_outbox, "append", side_effect=OSError("disk full")):
            originals = [client.push_event("info", f"메모리 보존 {index}") for index in range(130)]
            self.assertEqual(client._peek_events(), originals[:100])
            client._ack_events(originals[:100], {"accepted_event_keys": [event["event_key"] for event in originals[:100]]})
            self.assertEqual(client._peek_events(), originals[100:])
        self.assertEqual(len(warnings), 1)

    def test_deleted_old_session_remains_pending_and_does_not_mutate_current_session(self):
        old = self.client(session=5, cli_sink=lambda _event: None)
        event = old.push_event("error", "이전 세션 오류")
        old.close_events()
        current = self.client(session=9, cli_sink=lambda _event: None)
        self.addCleanup(current.close_events)
        refused = self.reply()
        refused.status_code = 404
        refused.raise_for_status.side_effect = RuntimeError("404")
        with patch.object(macro_runner.requests, "post", return_value=refused) as post:
            current.recover_events()
        self.assertEqual(current.session_id, 9)
        self.assertEqual(current._peek_events(5), [event])
        self.assertEqual(post.call_args.kwargs["json"], {"events": [event]})
        with patch.object(macro_runner.requests, "post") as retried:
            current.recover_events()
        retried.assert_not_called()  # No per-heartbeat hammering of a deleted session.

    def test_two_unstarted_clients_bind_only_their_own_original_events(self):
        first = self.client(session=None, cli_sink=lambda _event: None)
        second = self.client(session=None, cli_sink=lambda _event: None)
        self.addCleanup(first.close_events)
        self.addCleanup(second.close_events)
        first_event = first.push_event("info", "첫 번째 시작 준비")
        second_event = second.push_event("info", "두 번째 시작 준비")
        self.assertEqual(first._peek_events(), [first_event])
        self.assertEqual(second._peek_events(), [second_event])
        first.session_id, second.session_id = 101, 102
        self.assertEqual(first._peek_events(), [first_event])
        self.assertEqual(second._peek_events(), [second_event])

    def test_previous_unassigned_orphan_is_not_guessed_into_a_new_session_after_restart(self):
        abandoned = self.client(session=None, cli_sink=lambda _event: None)
        orphan = abandoned.push_event("error", "서버 시작 실패 전 기록")
        abandoned.close_events()
        new = self.client(session=201, cli_sink=lambda _event: None)
        self.addCleanup(new.close_events)
        event = new.push_event("info", "새로운 실제 세션")
        self.assertEqual(new._peek_events(), [event])
        self.assertEqual(new._event_outbox.peek(new._event_namespace, 0), [orphan])

    def test_successful_stop_without_event_ack_keeps_journal_and_schedules_independent_recovery(self):
        client = self.client(cli_sink=lambda _event: None)
        self.addCleanup(client.close_events)
        event = client.push_event("stop", "종료 상태 보고")
        thread = Mock()
        with patch.object(macro_runner.requests, "post", return_value=self.reply()), \
                patch.object(macro_runner.threading, "Thread", return_value=thread) as spawn:
            self.assertTrue(client.stopped())
        self.assertEqual(client._peek_events(), [event])
        thread.start.assert_called_once()
        self.assertEqual(spawn.call_args.kwargs["target"], client.recover_events)
        self.assertTrue(spawn.call_args.kwargs["kwargs"]["include_current"])

    def test_korean_payloads_obey_actual_ascii_wire_budget_for_every_transport_without_loss(self):
        for transport in ("heartbeat", "stopped", "recovery"):
            with self.subTest(transport=transport):
                client = self.client(session={"heartbeat": 301, "stopped": 302, "recovery": 303}[transport],
                                     cli_sink=lambda _event: None)
                self.addCleanup(client.close_events)
                originals = [client.push_event("error", f"실제 실행 오류 {index} " + "확인" * 120,
                    details={"error": "실패 상세" * 100, "reason": "원인 확인" * 100}) for index in range(100)]
                sent, sizes = [], []
                def accepted(url, **kwargs):
                    # requests' real JSON encoder defaults to ensure_ascii=True.
                    wire = macro_runner.requests.Request("POST", url, json=kwargs["json"]).prepare().body
                    sizes.append(len(wire))
                    self.assertLess(len(wire), 262144)
                    keys = [event["event_key"] for event in kwargs["json"]["events"]]
                    sent.extend(keys)
                    return self.reply(keys)
                with patch.object(macro_runner.requests, "post", side_effect=accepted), \
                        patch.object(client, "_schedule_event_recovery"), patch.object(macro_runner.time, "sleep"):
                    for _batch in range(20):
                        if not client._peek_events():
                            break
                        if transport == "heartbeat":
                            client.heartbeat({"in_position": False, "note": "상태 설명" * 100},
                                acks=[{"command_id": index, "ok": False, "error": "실패" * 90} for index in range(100)])
                        elif transport == "stopped":
                            self.assertTrue(client.stopped(note="종료 설명" * 100, snapshot={"in_position": False}))
                        else:
                            client.recover_events(include_current=True)
                self.assertEqual(sent, [event["event_key"] for event in originals])
                self.assertTrue(all(size <= 200000 for size in sizes), sizes)
                self.assertGreater(len(sizes), 1)
                self.assertEqual(client._peek_events(), [])

    def test_maximum_utf8_journal_record_still_fits_an_ascii_wire_batch(self):
        from runner import event_outbox
        # Two-byte characters become six-byte ASCII escapes in requests JSON.
        event = {"event_key": "runner:" + "a" * 32, "message": "é" * 8100}
        self.assertLessEqual(len(json.dumps(event, ensure_ascii=False).encode()), 16384)
        self.assertEqual(event_outbox.wire_batch([event]), [event])
        wire = macro_runner.requests.Request("POST", "https://example.invalid", json={"events": [event]}).prepare().body
        self.assertLess(len(wire), event_outbox.MAX_WIRE_BODY_BYTES)


if __name__ == "__main__":
    unittest.main()
