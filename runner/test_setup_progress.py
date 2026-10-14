"""웹 가이드에 보내는 진행 상황 — 단계만 가고 키 · 주소 · 오류 문장은 절대 실리지 않는다."""
import json
import threading
import unittest
from unittest.mock import Mock

from runner import setup_progress


class PayloadTests(unittest.TestCase):
    def test_payload_has_only_the_whitelisted_fields(self):
        payload = setup_progress.progress_payload(
            "keys", "upbit", path="new", slots={"access": True, "secret": "SECRET-VALUE", "extra": "x"},
            checks={"auth": "ok", "ip": "bad", "order": "wait"}, problem="ip", connected=False)
        self.assertEqual(set(payload), {"stage", "exchange", "path", "slots", "checks", "problem", "connected"})
        self.assertEqual(payload["slots"], {"access": True, "secret": True})  # 값이 아니라 '찼는가' 만
        self.assertNotIn("SECRET-VALUE", json.dumps(payload))
        self.assertEqual(payload["checks"], {"auth": "ok", "ip": "bad", "order": "wait"})

    def test_unknown_values_are_refused_not_sent(self):
        for kwargs in ({"stage": "typed-key"}, {"exchange": "kraken"}, {"path": "web"}, {"problem": "reason text"}):
            args = {"stage": "ip", "exchange": "upbit", **kwargs}
            with self.subTest(kwargs=kwargs), self.assertRaises(ValueError):
                setup_progress.progress_payload(args.pop("stage"), args.pop("exchange"), **args)
        with self.assertRaises(ValueError):
            setup_progress.progress_payload("ip", "upbit", checks={"auth": "121.134.55.20"})


class ReporterTests(unittest.TestCase):
    def test_latest_state_is_posted_with_the_member_key_header_and_duplicates_are_skipped(self):
        sent = []
        done = threading.Event()

        def post(url, *, json, headers, timeout):
            sent.append((url, json, headers, timeout))
            if json["stage"] == "perm":
                done.set()
            return Mock(status_code=200)

        reporter = setup_progress.SetupProgressReporter("https://api.example/", "ggp_member", 41, post=post)
        first = setup_progress.progress_payload("ip", "upbit", path="new")
        reporter.report(first)
        for _ in range(100):
            if sent:
                break
            threading.Event().wait(.01)
        reporter.report(first)  # 같은 상태는 다시 보내지 않는다
        reporter.report(setup_progress.progress_payload("perm", "upbit", path="new"))
        self.assertTrue(done.wait(2))
        reporter.close()
        self.assertEqual([item[1]["stage"] for item in sent], ["ip", "perm"])
        self.assertEqual(sent[0][0], "https://api.example/api/runner/launch-tickets/41/progress")
        self.assertEqual(sent[0][2], {"X-Runner-Key": "ggp_member"})

    def test_failures_are_silent_and_closing_stops_further_reports(self):
        calls = []

        def post(*_args, **_kwargs):
            calls.append(1)
            raise OSError("https://user:pass@proxy")

        reporter = setup_progress.SetupProgressReporter("https://api.example", "k", 7, post=post)
        reporter.report(setup_progress.progress_payload("ask", "bithumb"))
        reporter.close()
        reporter._thread.join(2)
        self.assertFalse(reporter._thread.is_alive())
        reporter.report(setup_progress.progress_payload("ip", "bithumb"))
        self.assertLessEqual(len(calls), 1)

    def test_finish_sends_the_closed_stage_before_the_window_goes(self):
        sent = []
        post = lambda url, json, headers, timeout: (sent.append(json["stage"]), type("R", (), {"status_code": 200})())[1]
        reporter = setup_progress.SetupProgressReporter("https://api.example", "k", 7, post=post)
        reporter.report(setup_progress.progress_payload("ready", "bithumb"))
        reporter.finish(setup_progress.progress_payload("closed", "bithumb"), wait=2)
        self.assertEqual(sent[-1], "closed")
        reporter.report(setup_progress.progress_payload("ask", "bithumb"))  # 닫힌 뒤에는 보내지 않는다
        self.assertEqual(sent[-1], "closed")

    def test_reports_stop_after_the_window(self):
        now = [0.0]
        post = Mock(return_value=Mock(status_code=200))
        reporter = setup_progress.SetupProgressReporter("https://api.example", "k", 7, post=post, clock=lambda: now[0])
        now[0] = setup_progress.REPORT_WINDOW_SECONDS + 1
        reporter.report(setup_progress.progress_payload("ask", "upbit"))
        reporter.close()
        reporter._thread.join(2)
        post.assert_not_called()

    def test_bad_launch_ids_are_refused(self):
        for bad in (0, -1, True, "7", None):
            with self.subTest(bad=bad), self.assertRaises(ValueError):
                setup_progress.SetupProgressReporter("https://api.example", "k", bad, post=Mock())


if __name__ == "__main__":
    unittest.main()
