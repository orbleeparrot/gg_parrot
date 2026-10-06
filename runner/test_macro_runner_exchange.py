"""실행기가 매크로의 거래소로 브로커·키·실행 모드를 고른다.

여기서 지키는 것은 넷이다.
  * 거래소 선택과 모의 감싸기 — 국내 매크로가 바이낸스 클라이언트를 만들지 않는다.
  * 키 선택 — 매크로의 거래소 키만 쓰고, 없으면 그 거래소 이름을 넣어 막는다.
  * 리허설 정책 — 실전은 주문 전에 멈추고, 모의는 기록만 남긴다(브로커는 결과만 돌려준다).
  * 원화 진입 크기 — 업비트·빗썸 시장가 매수는 수량이 아니라 금액으로 낸다.
"""
import unittest
from collections import deque
from decimal import ROUND_DOWN, Decimal
from unittest.mock import Mock, patch

from runner.test_macro_runner_single_instance import macro_runner

brokers = macro_runner.brokers


class _Var:
    """tk.StringVar 대역 — GUI 를 띄우지 않고 칸 값만 본다."""

    def __init__(self, value=""):
        self._value = value

    def get(self):
        return self._value

    def set(self, value):
        self._value = value


class _Recorder:
    """브로커 대역. 봇이 어떤 인자로 주문을 내는지 그대로 받아 둔다."""

    market, side = "spot", "long"
    fee_from_base_asset = False

    def __init__(self, *, price=15_000_000.0, step=0.0, rehearsal=(True, "ok"), truncate=False):
        self.symbol = "KRW-BTC"
        self.calls = []
        self.rehearsals = []
        self._price = price
        self._rules = brokers.OrderRules(step=step, min_notional=0.0)
        self._rehearsal = rehearsal
        # 국내 어댑터는 매도 수량을 소수 8자리로 내려 보낸다 — 그래서 체결이 보유보다 조금 적다.
        self._truncate = truncate

    def price(self):
        return self._price

    def order_rules(self):
        return self._rules

    def ensure_ready(self):
        return True

    def rehearse(self, *, notional):
        self.rehearsals.append(notional)
        return self._rehearsal

    def submit(self, side_word, *, base_qty=None, notional=None, reduce_only=False,
               closing=None, client_id):
        self.calls.append({"side": side_word, "base_qty": base_qty, "notional": notional,
                           "reduce_only": reduce_only, "closing": closing})
        qty = base_qty if base_qty else (notional or 0.0) / self._price
        if self._truncate and base_qty:
            qty = float(Decimal(repr(base_qty)).quantize(Decimal("0.00000001"), rounding=ROUND_DOWN))
        return brokers.Order(status="FILLED", executed_qty=qty, avg_price=self._price,
                             acquired_qty=qty, fees_known=True, order_id="x", raw_status="done")


class ExchangeSelectionTests(unittest.TestCase):
    def bot(self, exchange, symbol, mode="live", keys=None):
        bot = object.__new__(macro_runner.BotThread)
        bot.macro = {"exchange": exchange, "symbol": symbol, "rule_type": "A",
                     "params": {"initial_capital": 1_000_000}, "risk": {}}
        bot.symbol, bot.side, bot.leverage = symbol, "long", 1
        bot.exchange, bot.mode = exchange, mode
        bot.quote = macro_runner.quote_of(symbol)
        bot.credentials = keys if keys is not None else {
            "exchanges": {exchange: {"api_key": "k", "api_secret": "s"}}}
        bot.log = Mock()
        return bot

    def test_domestic_macro_builds_the_domestic_broker(self):
        bot = self.bot("upbit", "KRW-BTC")
        self.assertTrue(bot._connect())
        self.assertIsInstance(bot.broker, macro_runner.brokers.DomesticBroker)

    def test_mock_mode_wraps_whichever_broker(self):
        for exchange, symbol in (("upbit", "KRW-BTC"), ("bithumb", "KRW-ETH")):
            bot = self.bot(exchange, symbol, mode="mock")
            self.assertTrue(bot._connect())
            self.assertIsInstance(bot.broker, macro_runner.brokers.MockBroker)

    def test_missing_keys_for_that_exchange_block_the_start(self):
        bot = self.bot("upbit", "KRW-BTC", keys={"exchanges": {"binance": {"api_key": "k", "api_secret": "s"}}})
        self.assertFalse(bot._connect())
        self.assertTrue(any("업비트" in str(c) for c in bot.log.call_args_list))

    def test_exchange_secrets_never_reach_the_server_payload(self):
        # 거래소가 셋이 되어도 비밀키는 이 PC를 떠나지 않는다.
        secrets = {"exchanges": {name: {"api_key": f"KEY-{name}", "api_secret": f"SECRET-{name}"}
                                 for name in ("binance", "upbit", "bithumb")}}
        for exchange, symbol in (("binance", "BTCUSDT"), ("upbit", "KRW-BTC"), ("bithumb", "KRW-ETH")):
            bot = self.bot(exchange, symbol, keys=secrets)
            bot.member_key = Mock(get=Mock(return_value="mk"))
            blob = repr(macro_runner.BotThread._build_start_payload(bot, False))
            for name in ("binance", "upbit", "bithumb"):
                self.assertNotIn(f"SECRET-{name}", blob)
                self.assertNotIn(f"KEY-{name}", blob)

    def test_runner_version_is_ten(self):
        from runner import installation
        self.assertEqual(installation.RUNNER_VERSION, "10")

    # --- 위 다섯은 과제 사양. 아래는 같은 배선의 나머지 ----------------
    def test_binance_macro_builds_the_binance_broker_with_its_own_keys(self):
        bot = self.bot("binance", "BTCUSDT", keys={"exchanges": {
            "binance": {"api_key": "BK", "api_secret": "BS"},
            "upbit": {"api_key": "UK", "api_secret": "US"}}})
        with patch.object(macro_runner.BotThread, "_build_broker",
                          side_effect=lambda pair: pair) as built:
            bot.market = "spot"
            self.assertTrue(bot._connect())
        self.assertEqual(built.call_args.args[0], {"api_key": "BK", "api_secret": "BS"})

    def test_mock_mode_runs_without_any_stored_key(self):
        # 연습이 키를 요구하면 연습이 아니다 — 주문은 MockBroker 가 삼킨다.
        bot = self.bot("upbit", "KRW-BTC", mode="mock", keys={"exchanges": {}})
        self.assertTrue(bot._connect())
        self.assertIsInstance(bot.broker, macro_runner.brokers.MockBroker)

    def test_domestic_macro_refuses_short_or_leverage(self):
        for side, leverage in (("short", 1), ("long", 3)):
            bot = self.bot("upbit", "KRW-BTC")
            bot.side, bot.leverage = side, leverage
            self.assertFalse(bot._connect())
            self.assertTrue(any("원화 현물" in str(c) for c in bot.log.call_args_list))

    def test_domestic_market_is_spot_even_with_leverage_in_the_macro(self):
        self.assertEqual(macro_runner.market_of("upbit", "long", 5), "spot")
        self.assertEqual(macro_runner.market_of("binance", "long", 5), "futures")

    def test_unknown_run_mode_falls_back_to_mock_never_live(self):
        for raw in ("", None, "LIVE-ish", "mainnet", 7):
            self.assertEqual(macro_runner.run_mode_of(raw), "mock")
        self.assertEqual(macro_runner.run_mode_of(" LIVE "), "live")

    def test_unknown_exchange_is_blocked_in_the_runner_by_name(self):
        # 서버는 정해진 세 값만 받는다. 걸러 내지 않으면 사용자는 422 를 '서버 연결 실패' 로만 본다.
        bot = self.bot("upbitt", "KRW-BTC")
        self.assertFalse(bot._connect())
        logged = " ".join(str(c) for c in bot.log.call_args_list)
        self.assertIn("upbitt", logged)
        # 어댑터가 뒤늦게 낸 ValueError 가 아니라, 주문 경로에 들어가기 전의 관문이 낸 말이어야 한다.
        self.assertIn("모르는 거래소", logged)

    def test_unknown_exchange_is_not_quietly_treated_as_binance(self):
        bot = self.bot("upbitt", "KRW-BTC")
        self.assertFalse(bot.domestic)          # 금액 주문으로 새지 않는다
        self.assertFalse(macro_runner.known_exchange("upbitt"))
        self.assertEqual(macro_runner.KNOWN_EXCHANGES, ("binance", "upbit", "bithumb"))

    def test_domestic_is_decided_by_the_exchange_not_the_quote(self):
        self.assertTrue(self.bot("upbit", "KRW-BTC").domestic)
        self.assertTrue(self.bot("bithumb", "KRW-ETH").domestic)
        self.assertFalse(self.bot("binance", "BTCKRW").domestic)  # 원화 호가라도 바이낸스는 수량 주문
        # 거래소를 못 채운 경로도 바이낸스(수량 주문)로 본다 — 클래스 기본값.
        self.assertEqual(macro_runner.BotThread.exchange, "binance")
        self.assertEqual(macro_runner.BotThread.mode, "mock")

    def test_payload_tells_the_server_which_exchange_and_mode(self):
        bot = self.bot("upbit", "KRW-BTC", mode="mock")
        payload = macro_runner.BotThread._build_start_payload(bot, True)
        self.assertEqual(payload["exchange"], "upbit")
        self.assertEqual(payload["mode"], "mock")
        self.assertEqual(payload["market"], "spot")


class RehearsalPolicyTests(unittest.TestCase):
    """리허설은 브로커가 보고하고 멈출지는 봇이 정한다."""

    def bot(self, mode, rehearsal):
        bot = object.__new__(macro_runner.BotThread)
        bot.exchange, bot.mode = "upbit", mode
        bot.market, bot.symbol, bot.side, bot.quote = "spot", "KRW-BTC", "long", "KRW"
        bot.leverage, bot.capital = 1, 1_000_000.0
        bot.log = Mock()
        bot.broker = _Recorder(rehearsal=rehearsal)
        return bot

    def test_live_failure_stops_the_session_before_any_order(self):
        bot = self.bot("live", (False, "허용 IP 를 확인하세요"))
        self.assertFalse(bot._prepare())
        self.assertEqual(bot.broker.calls, [])  # 주문은 한 건도 나가지 않았다
        self.assertTrue(any("허용 IP" in str(c) for c in bot.log.call_args_list))

    def test_mock_failure_is_logged_and_the_session_continues(self):
        bot = self.bot("mock", (False, "키가 없어요"))
        self.assertTrue(bot._prepare())
        self.assertTrue(any("모의 모드라" in str(c) for c in bot.log.call_args_list))

    def test_rehearsal_runs_once_with_the_capped_entry_amount(self):
        bot = self.bot("live", (True, "통과"))
        self.assertTrue(bot._prepare())
        self.assertEqual(bot.broker.rehearsals, [macro_runner.MAX_ORDER_KRW])

    def test_broker_exception_is_a_failure_not_a_crash(self):
        bot = self.bot("live", (True, "통과"))
        bot.broker.rehearse = Mock(side_effect=RuntimeError("boom"))
        self.assertFalse(bot._prepare())


class DomesticOrderSizingTests(unittest.TestCase):
    """원화 시장가 매수는 수량이 아니라 금액으로 낸다. 수량으로 보내면 어댑터가 거절한다."""

    def bot(self, quote, *, symbol, step):
        bot = object.__new__(macro_runner.BotThread)
        bot.exchange = "upbit" if quote == "KRW" else "binance"
        bot.mode, bot.market, bot.side = "live", "spot", "long"
        bot.symbol, bot.quote, bot.leverage = symbol, quote, 1
        bot.in_position, bot.held_qty, bot.entry_price = False, 0.0, 0.0
        bot.realized, bot.step, bot.position_uncertain = 0.0, step, False
        bot.capital, bot.log = 1_000_000.0, Mock()
        bot.broker = _Recorder(price=15_000_000.0 if quote == "KRW" else 100.0, step=step)
        bot._done_command_ids, bot.pending_acks = deque(maxlen=200), []
        return bot

    BUY = {"id": 1, "action": "buy", "notional_frac": 1.0, "qty_frac": 0.0, "reason": "진입"}

    def test_domestic_entry_is_sent_as_won_not_quantity(self):
        bot = self.bot("KRW", symbol="KRW-BTC", step=0.0)
        ack = bot._execute_command(self.BUY, price=15_000_000.0)
        self.assertTrue(ack["ok"], ack)
        call = bot.broker.calls[-1]
        self.assertEqual(call["notional"], macro_runner.MAX_ORDER_KRW)
        self.assertIsNone(call["base_qty"])

    def test_binance_entry_is_still_sent_as_quantity(self):
        bot = self.bot("USDT", symbol="BTCUSDT", step=0.001)
        ack = bot._execute_command(self.BUY, price=100.0)
        self.assertTrue(ack["ok"], ack)
        call = bot.broker.calls[-1]
        self.assertAlmostEqual(call["base_qty"], 1.0)
        self.assertIsNone(call["notional"])

    def test_domestic_close_is_sent_as_quantity(self):
        # 매도는 어느 거래소든 수량이다. 금액으로 내면 반대로 '사는' 주문이 된다.
        bot = self.bot("KRW", symbol="KRW-BTC", step=0.0)
        bot.in_position, bot.held_qty, bot.entry_price = True, 0.01, 15_000_000.0
        self.assertTrue(bot._close_position())
        call = bot.broker.calls[-1]
        self.assertAlmostEqual(call["base_qty"], 0.01)
        self.assertIsNone(call["notional"])

    def test_a_krw_quoted_binance_order_is_never_sent_as_money(self):
        """호가 통화로 갈랐을 때의 구멍 — 원화 호가 바이낸스 주문에서 수량이 빠진다(quantity=None).

        국내 숏은 _connect 가 막지만, 거래소가 binance 면 그 관문을 지나간다. 금액 주문 여부를
        거래소로 갈라야 이 경로가 닫힌다.
        """
        bot = self.bot("KRW", symbol="BTCKRW", step=0.001)
        bot.exchange, bot.side = "binance", "short"
        self.assertTrue(bot._place("SELL", 0.5, notional=150_000.0))
        call = bot.broker.calls[-1]
        self.assertAlmostEqual(call["base_qty"], 0.5)
        self.assertIsNone(call["notional"])

    def test_command_entry_passes_money_only_for_a_domestic_exchange(self):
        """_execute_command 가 _place 에 금액을 넘기는 기준도 거래소다.

        _place 의 분기만 보면 두 자리가 서로를 가려 준다 — 그래서 여기서는 _place 가 받은
        인자를 직접 본다. 원화 호가 바이낸스 심볼에 금액을 넘기면 수량 주문이 금액 주문이 된다.
        """
        for exchange, has_money in (("upbit", True), ("binance", False)):
            bot = self.bot("KRW", symbol="KRW-BTC" if exchange == "upbit" else "BTCKRW", step=0.001)
            bot.exchange = exchange
            bot._place = Mock(return_value=True)
            bot._last_fill_qty, bot._last_fill_price = 0.01, 15_000_000.0
            ack = bot._execute_command(self.BUY, price=15_000_000.0)
            self.assertTrue(ack["ok"], ack)
            kwargs = bot._place.call_args.kwargs
            self.assertEqual("notional" in kwargs, has_money, exchange)
            if has_money:
                self.assertEqual(kwargs["notional"], macro_runner.MAX_ORDER_KRW)

    def test_domestic_close_dust_below_the_volume_limit_is_not_an_error(self):
        """국내는 step 이 0 이라 '팔 수 없는 잔여' 안전망이 꺼져 있었다.

        어댑터가 매도 수량을 8자리로 내리면 1e-8 미만이 남는데, 그걸 미처리 보유로 보면
        세션이 오류로 끝나고 사용자는 티끌 때문에 거래소를 열어 보게 된다.
        """
        bot = self.bot("KRW", symbol="KRW-BTC", step=0.0)
        bot.broker = _Recorder(price=15_000_000.0, step=0.0, truncate=True)
        bot.in_position, bot.held_qty, bot.entry_price = True, 0.012345675, 15_000_000.0
        self.assertTrue(bot._close_position())          # 예외 없이 끝난다
        self.assertFalse(bot.in_position)
        self.assertGreater(bot.position_dust_qty, 0.0)
        self.assertLess(bot.position_dust_qty, macro_runner.DUST_FLOOR)
        self.assertIn("잔여 수량", bot._snapshot()["note"])
        self.assertEqual(macro_runner.DUST_FLOOR, 1e-8)

    def test_zero_step_never_divides_or_blocks_the_order(self):
        # 국내 수량 단위는 0 이다. 0 을 나누거나 '아직 못 읽었다' 로 보면 주문이 막힌다.
        self.assertEqual(macro_runner._round_step(0.0123456789, 0.0), 0.0123456789)
        qty, notional = macro_runner._order_qty(15_000_000.0, 0.0, 0, 1_000_000.0, 1, "spot", quote="KRW")
        self.assertAlmostEqual(qty * 15_000_000.0, macro_runner.MAX_ORDER_KRW, places=3)
        self.assertAlmostEqual(notional, macro_runner.MAX_ORDER_KRW, places=3)


class CredentialShapeTests(unittest.TestCase):
    """자격증명 v2 — 창이 열리고, 저장이 거래소별 쌍을 잃지 않는다."""

    def app(self):
        app = object.__new__(macro_runner.RunnerApp)
        app.api_key, app.api_secret = _Var(), _Var()
        app.key_vars = {"binance": (app.api_key, app.api_secret),
                        "upbit": (_Var(), _Var()), "bithumb": (_Var(), _Var())}
        app.member_key, app.remember = _Var(), _Var(False)
        app._log = Mock()
        return app

    def test_stored_v2_file_fills_each_exchange_without_a_crash(self):
        app = self.app()
        app._apply_remembered_credentials({
            "version": 2, "member_key": "mk",
            "exchanges": {"upbit": {"api_key": "uk", "api_secret": "us"},
                          "kraken": {"api_key": "x", "api_secret": "y"}}})
        self.assertEqual(app.key_vars["upbit"][0].get(), "uk")
        self.assertEqual(app.key_vars["upbit"][1].get(), "us")
        self.assertEqual(app.api_key.get(), "")  # 저장 안 한 거래소는 빈 칸
        self.assertEqual(app.member_key.get(), "mk")
        self.assertTrue(app.remember.get())

    def test_saved_values_keep_every_exchange_pair(self):
        app = self.app()
        app.api_key.set("bk"); app.api_secret.set("bs")
        app.key_vars["upbit"][0].set("uk"); app.key_vars["upbit"][1].set("us")
        app.member_key.set("mk")
        values = app._credential_values()
        self.assertEqual(values["exchanges"]["binance"], {"api_key": "bk", "api_secret": "bs"})
        self.assertEqual(values["exchanges"]["upbit"], {"api_key": "uk", "api_secret": "us"})
        self.assertNotIn("bithumb", values["exchanges"])  # 빈 칸은 저장하지 않는다
        self.assertEqual(values["member_key"], "mk")
        # 저장 함수가 받는 모양 그대로여야 한다 — 평평한 dict 면 거래소가 통째로 비어 저장된다.
        self.assertEqual(macro_runner.credentials_mod._exchanges(values["exchanges"]),
                         values["exchanges"])

    def test_only_the_macro_exchange_key_is_chosen(self):
        values = {"exchanges": {"binance": {"api_key": "bk", "api_secret": "bs"},
                                "upbit": {"api_key": "uk", "api_secret": ""}}}
        self.assertEqual(macro_runner.credential_pair(values, "binance"),
                         {"api_key": "bk", "api_secret": "bs"})
        self.assertIsNone(macro_runner.credential_pair(values, "upbit"))  # 시크릿 빈 칸은 '없음'
        self.assertIsNone(macro_runner.credential_pair(values, "bithumb"))


class RunModeGuiTests(unittest.TestCase):
    def app(self, macro):
        app = object.__new__(macro_runner.RunnerApp)
        app.macro = macro
        app.mode = _Var("testnet")
        app.mode_buttons = {name: Mock(**{"winfo_manager.return_value": "pack"})
                            for name in macro_runner.RUN_MODES}
        app.live_note = Mock()
        app._log = Mock()
        return app

    def test_testnet_is_hidden_and_unselected_for_a_domestic_macro(self):
        app = self.app({"symbol": "KRW-BTC", "exchange": "upbit"})
        app._sync_mode_choices()
        app.mode_buttons["testnet"].pack_forget.assert_called_once_with()
        self.assertEqual(app.mode.get(), "mock")  # 국내엔 테스트넷이 없다 — 실전으로 떨어지지 않는다

    def test_testnet_stays_for_a_binance_macro(self):
        app = self.app({"symbol": "BTCUSDT", "exchange": "binance"})
        app._sync_mode_choices()
        app.mode_buttons["testnet"].pack_forget.assert_not_called()
        self.assertEqual(app.mode.get(), "testnet")

    def test_live_start_without_that_exchange_key_is_blocked_by_name(self):
        app = self.app({"symbol": "KRW-BTC", "exchange": "upbit", "position_side": "long", "leverage": 1})
        app.bot = None
        app._protocol_claim_busy = False
        app.mode = _Var("live")
        app.api_key, app.api_secret = _Var(), _Var()
        app.key_vars = {"binance": (_Var("bk"), _Var("bs")),
                        "upbit": (_Var(), _Var()), "bithumb": (_Var(), _Var())}
        app.member_key = _Var("mk")
        with patch.object(macro_runner.messagebox, "showwarning") as warned, \
             patch.object(macro_runner, "BotThread") as bot:
            app._start()
        bot.assert_not_called()
        self.assertIn("업비트", warned.call_args.args[1])


class StartPayloadContractTests(unittest.TestCase):
    def app(self, macro):
        app = object.__new__(macro_runner.RunnerApp)
        app.macro = macro
        app.user_macro_id, app.macro_sig, app.macro_source = None, None, "file"
        return app

    def test_mode_is_required_so_a_live_session_is_never_recorded_as_mock(self):
        app = self.app({"symbol": "BTCUSDT", "exchange": "binance"})
        with self.assertRaises(TypeError):
            app._build_start_payload(False)
        self.assertEqual(app._build_start_payload(False, "live")["mode"], "live")

    def test_unknown_exchange_never_reaches_the_server(self):
        app = self.app({"symbol": "KRW-BTC", "exchange": "upbitt", "position_side": "long", "leverage": 1})
        app.bot, app._protocol_claim_busy = None, False
        app.mode = _Var("live")
        with patch.object(macro_runner.messagebox, "showwarning") as warned,              patch.object(macro_runner, "ServerClient") as server,              patch.object(macro_runner, "BotThread") as bot:
            app._start()
        server.assert_not_called()
        bot.assert_not_called()
        self.assertIn("upbitt", warned.call_args.args[1])


if __name__ == "__main__":
    unittest.main()
