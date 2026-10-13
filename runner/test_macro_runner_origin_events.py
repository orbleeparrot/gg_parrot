"""파일 서명 전달과 실행 로그 업로드 — 서버가 원본/수정본을 가르고 로그를 남길 수 있게."""
from __future__ import annotations

import unittest
from unittest.mock import Mock, patch

from runner.test_macro_runner_single_instance import macro_runner
from runner.macro_runner import RunnerApp, ServerClient, _event_kind


def _app() -> RunnerApp:
    app = object.__new__(RunnerApp)
    app.root = Mock()
    app.bot = None
    app.user_macro_id = None
    app.macro_sig = None
    app.macro_source = ""
    app.key_vars = {}  # 거래소별 키 칸 — 이 시험들은 키를 쓰지 않는다
    app.macro_path = Mock()
    app.macro_summary = Mock()
    app._log = Mock()
    return app


class FileSignatureTests(unittest.TestCase):
    def test_file_signature_is_kept_out_of_the_macro_but_sent_with_start(self) -> None:
        app = _app()
        sig = {"v": 1, "alg": "HMAC-SHA256", "hmac": "ab" * 32, "issued_at": "2026-09-14T00:00:00Z"}
        app._apply_local_macro({"symbol": "btcusdt", "leverage": 1, "_sig": sig}, "C:/macro.ggm.json")

        self.assertNotIn("_sig", app.macro)  # 서버로 가는 매크로 본문엔 서명이 섞이지 않는다
        payload = app._build_start_payload(True, "mock")
        self.assertEqual(payload["macro_sig"], sig)
        self.assertEqual(payload["macro_source"], "file")
        self.assertNotIn("_sig", payload["macro"])

    def test_unsigned_file_sends_no_signature_and_logs_it(self) -> None:
        app = _app()
        app._apply_local_macro({"symbol": "btcusdt", "leverage": 1}, "old.json")
        payload = app._build_start_payload(True, "mock")
        self.assertNotIn("macro_sig", payload)
        self.assertEqual(payload["macro_source"], "file")
        self.assertTrue(any("서명이 없어요" in str(c.args[0]) for c in app._log.call_args_list))

    def test_web_claim_clears_any_previous_file_signature(self) -> None:
        app = _app()
        app._apply_local_macro({"symbol": "btcusdt", "leverage": 1, "_sig": {"v": 1, "hmac": "x"}}, "f")
        app._apply_claimed_macro({"symbol": "ethusdt", "leverage": 1}, "웹", 5)
        payload = app._build_start_payload(True, "mock")
        self.assertNotIn("macro_sig", payload)
        self.assertEqual(payload["macro_source"], "web")
        self.assertEqual(payload["user_macro_id"], 5)


class StartRetryTests(unittest.TestCase):
    @staticmethod
    def response(status, body=None, retry_after="1"):
        r = Mock(status_code=status, headers={"Retry-After": retry_after})
        r.json.return_value = body or {}
        r.raise_for_status = Mock()
        return r

    def test_busy_server_is_retried_then_session_starts(self) -> None:
        client = ServerClient("key", base="https://example.invalid")
        busy = self.response(503, {"detail": "서버 저장소가 혼잡해요. 잠시 후 다시 연결합니다.", "code": "database_busy"})
        ok = self.response(200, {"session_id": 42})
        with patch.object(macro_runner.requests, "post", side_effect=[busy, busy, ok]) as post, \
                patch.object(macro_runner.time, "sleep") as sleep:
            data = client.start({"symbol": "KRW-BTC"})
        self.assertEqual(data["session_id"], 42)
        self.assertEqual(post.call_count, 3)
        self.assertEqual(sleep.call_count, 2)

    def test_refusal_shows_server_detail_without_address(self) -> None:
        client = ServerClient("key", base="https://example.invalid")
        refused = self.response(426, {"detail": "실행기 v10 이상으로 업데이트해 주세요."})
        with patch.object(macro_runner.requests, "post", return_value=refused) as post:
            with self.assertRaises(macro_runner.ServerRequestError) as caught:
                client.start({})
        self.assertEqual(post.call_count, 1)
        self.assertIn("실행기 v10 이상으로 업데이트해 주세요.", str(caught.exception))
        self.assertNotIn("https://", str(caught.exception))

    def test_busy_until_the_end_gives_up_after_bounded_attempts(self) -> None:
        client = ServerClient("key", base="https://example.invalid")
        busy = self.response(503, {"detail": "서버 저장소가 혼잡해요."}, retry_after="30")
        with patch.object(macro_runner.requests, "post", return_value=busy) as post, \
                patch.object(macro_runner.time, "sleep") as sleep:
            with self.assertRaises(macro_runner.ServerRequestError) as caught:
                client.start({})
        self.assertEqual(post.call_count, macro_runner.START_ATTEMPTS)
        self.assertTrue(all(call.args[0] <= 3.0 for call in sleep.call_args_list))
        self.assertEqual(caught.exception.status, 503)


class EventUploadTests(unittest.TestCase):
    def test_heartbeat_carries_buffered_events_and_requeues_on_failure(self) -> None:
        client = ServerClient("key", base="https://example.invalid")
        client.session_id = 1
        client.push_event("order", "[진입] 100 → BUY 0.01 BTCUSDT")
        client.push_event("info", "tick")

        ok = Mock(status_code=200)
        ok.raise_for_status = Mock()
        ok.json.return_value = {"action": "continue"}
        with patch.object(macro_runner.requests, "post", return_value=ok) as post:
            client.heartbeat({"last_price": 100.0})
        sent = post.call_args.kwargs["json"]
        self.assertEqual([e["message"] for e in sent["events"]], ["[진입] 100 → BUY 0.01 BTCUSDT", "tick"])
        self.assertEqual(sent["events"][0]["kind"], "order")
        self.assertTrue(sent["events"][0]["ts"].endswith("Z"))

        # 전송 실패면 버렸다가 다음 heartbeat 에 다시 실어 보낸다.
        client.push_event("fill", "손익 +1.00%")
        with patch.object(macro_runner.requests, "post", side_effect=RuntimeError("down")):
            self.assertEqual(client.heartbeat({}), {"action": "continue", "commands": [], "offline": True})
        with patch.object(macro_runner.requests, "post", return_value=ok) as post:
            client.heartbeat({})
        self.assertEqual([e["message"] for e in post.call_args.kwargs["json"]["events"]], ["손익 +1.00%"])

    def test_buffer_keeps_only_the_newest_lines(self) -> None:
        client = ServerClient("key", base="https://example.invalid")
        for i in range(macro_runner.EVENT_BUFFER_MAX + 20):
            client.push_event("info", f"line {i}")
        batch = client._drain_events()
        self.assertEqual(len(batch), macro_runner.EVENT_BUFFER_MAX)
        self.assertEqual(batch[-1]["message"], f"line {macro_runner.EVENT_BUFFER_MAX + 19}")

    def test_stopped_report_flushes_remaining_events(self) -> None:
        client = ServerClient("key", base="https://example.invalid")
        client.session_id = 3
        client.push_event("stop", "종료")
        ok = Mock(status_code=200)
        ok.raise_for_status = Mock()
        with patch.object(macro_runner.requests, "post", return_value=ok) as post:
            self.assertTrue(client.stopped("stopped", "포지션 없이 종료"))
        self.assertEqual(post.call_args.kwargs["json"]["events"][0]["message"], "종료")

    def test_event_kind_classification(self) -> None:
        self.assertEqual(_event_kind("[진입] 100 → BUY 1 BTCUSDT"), "order")
        self.assertEqual(_event_kind("[청산 신호] 101 (진입 100)"), "order")
        self.assertEqual(_event_kind("  손익 +1.00% (+1 USDT)"), "fill")
        self.assertEqual(_event_kind("  일시 오류(시세): boom"), "error")
        self.assertEqual(_event_kind("원격 종료 명령 수신: stop_only"), "stop")
        self.assertEqual(_event_kind("  ⏸ 진입 보류: 일일 손실 한도"), "signal")
        self.assertEqual(_event_kind("공통 리스크: …"), "info")


class ModifiedFileWarningTests(unittest.TestCase):
    def test_start_warns_when_server_says_the_file_was_modified(self) -> None:
        app = _app()
        app.macro = {"symbol": "BTCUSDT", "position_side": "long", "leverage": 1}
        app.macro_sig = {"v": 1, "hmac": "x"}
        app.macro_source = "file"
        app._protocol_claim_busy = False
        for name in ("api_key", "api_secret", "member_key"):
            var = Mock()
            var.get.return_value = "value"
            setattr(app, name, var)
        app.mode = Mock()
        app.mode.get.return_value = "mock"  # 실행 모드 — 기본값(모의)
        app.server_base = "https://example.invalid"
        app._set_running = Mock()
        app._log_threadsafe = app._status_threadsafe = app._finish_threadsafe = Mock()

        server = Mock()
        server.session_id = 9
        server.macro_origin = "file_modified"
        server.start.return_value = {"session_id": 9, "macro_origin": "file_modified", "macro_origin_label": "수정된 파일", "macro_digest": "abc"}
        with patch.object(macro_runner, "ServerClient", return_value=server), \
             patch.object(macro_runner, "BotThread") as bot, \
             patch.object(macro_runner.messagebox, "showwarning") as warn:
            app._start()

        warn.assert_called_once()
        self.assertIn("원본과 내용이 달라요", warn.call_args.args[1])
        self.assertTrue(any("수정된 파일" in str(c.args[0]) for c in app._log.call_args_list))
        bot.assert_called_once()  # 경고는 하되 실행은 막지 않는다


if __name__ == "__main__":
    unittest.main()
