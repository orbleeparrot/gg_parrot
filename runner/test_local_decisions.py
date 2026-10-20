"""실행기가 스스로 판단하는 바퀴 (2026-10-20).

서버가 '네가 판단해라' 고 한 세션에서 실행기 루프가 실제로 봉을 받아 사고파는지, 그리고
그 과정에서 지켜야 할 것들을 못 박는다:

* 주문은 서버 명령과 **같은 길**로 나간다(주문 상한 · 최소 수량 · 수수료 보정이 똑같이 걸린다).
* 로컬 리스크(일일 손실 · 보유시간)가 로컬 판단에도 걸린다.
* 체결은 서버에 보고되고, **보낸 뒤에만** 지운다 — 못 보낸 체결은 리더보드에서 사라진다.
* 서버 명령이 섞여 와도 실행하지 않는다 — 같은 신호로 두 번 산다.
* 서버가 꺼 둔 세션에서는 아무것도 바뀌지 않는다.
"""
import unittest
from unittest.mock import Mock, patch

# 저장소의 다른 실행기 시험과 **같은 경로로** 집어 온다. top-level ``macro_runner`` 와
# ``runner.macro_runner`` 는 서로 다른 모듈 객체라, 엉뚱한 쪽에 patch 를 걸면 조용히
# 아무 일도 일어나지 않는다(이 시험이 한 번 그렇게 통과할 뻔했다).
from runner.test_macro_runner_commands import _bot
from runner.test_macro_runner_single_instance import macro_runner

HOUR = 3_600_000
T0 = 1_760_000_000_000 // HOUR * HOUR

MACRO = {
    "symbol": "ONEUSDT", "exchange": "binance", "rule_type": "F", "position_side": "long",
    "market": "spot", "leverage": 1, "candle_interval": "1h", "period": {"preset": "1w"},
    "params": {"rsi_period": 7, "entry_threshold": 30, "exit_threshold": 70,
               "initial_capital": 32},
    "risk": {"invest_ratio": 1.0},
    "fees": {"commission_pct": 0.1, "slippage_pct": 0.05},
}


def _candles(n=600, start=T0):
    """지표를 세울 만큼의 봉. 끝으로 갈수록 떨어져 RSI 가 낮아진다."""
    out, price = [], 100.0
    for i in range(n):
        price *= 1.002 if i < n - 40 else 0.97
        out.append((start + i * HOUR, price, price * 1.01, price * 0.99, price * 1.005, 10.0))
    return out


def _local_bot(replies, *, candles=None, decides="local"):
    """로컬 판단 모드로 run() 을 ``replies`` 수만큼 돌린다."""
    bot = _bot()
    bot.exchange, bot.mode = "binance", "testnet"
    bot.macro = dict(MACRO)
    bot._connect = Mock(return_value=True)
    bot._prepare = Mock(return_value=True)
    bot._price = Mock(return_value=100.0)
    bot._get_command = Mock(side_effect=[None] * len(replies) + ["stop_only"])
    bot.set_command = Mock()
    bot._sleep = Mock()
    bot.server = Mock()
    bot.server.decides = decides
    bot.server.heartbeat.side_effect = list(replies)
    bot.on_status, bot.on_finish = Mock(), Mock()
    bot._offline_logged = False
    bot._local_fills = []
    bot.strategy = None
    bot.candles = None

    rows = _candles() if candles is None else candles

    class FakeSource:
        step_ms = HOUR

        def recent_closed(self, limit):
            return rows[-limit:]

    bot._fake_rows = rows
    patcher = patch.object(macro_runner, "CandleSource", lambda *a, **k: FakeSource())
    patcher.start()
    bot._stop_patch = patcher.stop
    return bot


class StartupTests(unittest.TestCase):
    def setUp(self):
        patch.object(macro_runner.time, "sleep").start()
        self.addCleanup(patch.stopall)

    def test_서버가_켜_주면_로컬_엔진을_세운다(self):
        bot = _local_bot([{"action": "continue", "commands": []}])
        bot.run()
        self.assertTrue(bot.decides_locally)
        self.assertIsNotNone(bot.strategy)

    def test_서버가_안_켜면_아무것도_바뀌지_않는다(self):
        """지금까지처럼 서버 명령만 실행한다 — 기본 경로가 흔들리면 안 된다."""
        bot = _local_bot([{"action": "continue", "commands": []}], decides="server")
        bot.run()
        self.assertFalse(bot.decides_locally)
        self.assertIsNone(bot.strategy)

    def test_봉을_못_받으면_오류로_끝낸다(self):
        """여기서 조용히 넘어가면 **아무도 판단하지 않는다** — 서버도 드라이버를 안 띄웠다."""
        bot = _local_bot([{"action": "continue", "commands": []}])

        class Broken:
            step_ms = HOUR

            def recent_closed(self, limit):
                raise macro_runner.CandleFetchError("거래소 응답 없음")

        with patch.object(macro_runner, "CandleSource", lambda *a, **k: Broken()):
            bot.run()
        self.assertIsNone(bot.strategy)
        status = bot.on_finish.call_args.args[0]
        self.assertEqual(status, "error")

    def test_지표를_세운_뒤_묵은_봉으로_사지_않는다(self):
        """시작하자마자 지난 봉의 신호로 사면 사용자가 모르는 자리에서 진입한다."""
        bot = _local_bot([{"action": "continue", "commands": []}])
        bot._execute_command = Mock()
        bot.run()
        bot._execute_command.assert_not_called()


class RoundTests(unittest.TestCase):
    def setUp(self):
        patch.object(macro_runner.time, "sleep").start()
        self.addCleanup(patch.stopall)

    def _bot_with_signal(self, replies):
        """웜업 뒤 새 봉이 하나 더 들어와 진입 신호가 서는 상황."""
        rows = _candles()
        bot = _local_bot(replies, candles=rows)
        original = macro_runner.CandleSource

        class Growing:
            step_ms = HOUR

            def __init__(self):
                self.calls = 0

            def recent_closed(self, limit):
                self.calls += 1
                if self.calls == 1:
                    return rows[:-1][-limit:]        # 웜업: 마지막 봉을 뺀다
                return rows[-limit:]                  # 폴: 마지막 봉이 새로 마감됐다

        patch.object(macro_runner, "CandleSource", lambda *a, **k: Growing()).start()
        del original
        return bot

    def test_새_마감봉으로_스스로_판단해_주문을_낸다(self):
        bot = self._bot_with_signal([{"action": "continue", "commands": []}])
        executed = []

        def execute(cmd, price):
            executed.append(cmd)
            return {"command_id": cmd["id"], "ok": True, "executed_qty": 1.0,
                    "fill_price": price, "error": ""}

        bot._execute_command = Mock(side_effect=execute)
        bot.run()
        self.assertTrue(executed, "새 봉이 왔는데 판단이 없었다")
        self.assertTrue(str(executed[0]["id"]).startswith("local-"))
        self.assertEqual(executed[0]["source"], "local")

    def test_체결을_서버에_보고하고_보낸_뒤에_지운다(self):
        """못 보낸 체결은 리더보드 · 수익률에서 영영 사라진다."""
        bot = self._bot_with_signal([{"action": "continue", "commands": []},
                                     {"action": "continue", "commands": []}])
        bot._execute_command = Mock(side_effect=lambda cmd, price: {
            "command_id": cmd["id"], "ok": True, "executed_qty": 1.0,
            "fill_price": price, "error": ""})
        bot.run()
        snaps = [call.args[0] for call in bot.server.heartbeat.call_args_list]
        reported = [s for s in snaps if s.get("local_fills")]
        self.assertTrue(reported, "체결을 서버에 보고하지 않았다")
        self.assertEqual(reported[0]["local_fills"][0]["action"], "buy")
        self.assertEqual(bot.local_fills, [], "보고한 체결이 남아 있다")

    def test_서버가_못_받으면_체결을_지우지_않는다(self):
        bot = self._bot_with_signal([{"action": "continue", "commands": [], "offline": True}])
        bot._execute_command = Mock(side_effect=lambda cmd, price: {
            "command_id": cmd["id"], "ok": True, "executed_qty": 1.0,
            "fill_price": price, "error": ""})
        bot.run()
        self.assertTrue(bot.local_fills, "서버가 못 받았는데 체결을 지웠다")

    def test_전략_상태를_매_하트비트에_올린다(self):
        """마이페이지 차트의 가격선이 이걸 읽는다."""
        bot = _local_bot([{"action": "continue", "commands": []}])
        bot.run()
        snap = bot.server.heartbeat.call_args.args[0]
        self.assertIn("strategy_state", snap)
        self.assertIn("legs", snap["strategy_state"])

    def test_서버_명령이_섞여_와도_실행하지_않는다(self):
        """같은 신호로 두 번 산다."""
        bot = _local_bot([{"action": "continue", "commands": [
            {"id": 7, "action": "buy", "notional_frac": 1.0, "qty_frac": 0.0, "reason": "서버"}]}])
        bot._execute_command = Mock()
        bot.run()
        for call in bot._execute_command.call_args_list:
            self.assertNotEqual(call.args[0].get("id"), 7, "서버 명령을 실행했다")

    def test_원격_종료는_로컬_판단_중에도_듣는다(self):
        """이 구조의 전제 — 판단은 로컬, 종료 · 청산은 원격으로도 된다."""
        bot = _local_bot([{"action": "close_and_stop", "commands": []}])
        bot.run()
        bot.set_command.assert_called_with("close_and_stop")

    def test_봉을_못_받아도_멈추지_않는다(self):
        """일시적 네트워크 문제로 세션이 끝나면 안 된다 — 다음 바퀴에 다시 받는다."""
        rows = _candles()
        bot = _local_bot([{"action": "continue", "commands": []}], candles=rows)

        class Flaky:
            step_ms = HOUR

            def __init__(self):
                self.calls = 0

            def recent_closed(self, limit):
                self.calls += 1
                if self.calls == 1:
                    return rows[-limit:]
                raise macro_runner.CandleFetchError("일시 오류")

        with patch.object(macro_runner, "CandleSource", lambda *a, **k: Flaky()):
            bot.run()
        self.assertEqual(bot.on_finish.call_args.args[0], "stopped")

    def test_로컬_리스크가_로컬_진입도_막는다(self):
        """일일 손실 한도에 걸렸으면 서버 신호든 로컬 신호든 진입하지 않는다."""
        bot = self._bot_with_signal([{"action": "continue", "commands": []}])
        bot.macro = {**MACRO, "risk": {"invest_ratio": 1.0, "daily_max_loss_pct": 1.0}}
        bot._execute_command = Mock()
        with patch.object(macro_runner.RiskGuard, "entry_blocked",
                          lambda self: (True, "일일 손실 한도")):
            bot.run()
        bot._execute_command.assert_not_called()


if __name__ == "__main__":
    unittest.main()
