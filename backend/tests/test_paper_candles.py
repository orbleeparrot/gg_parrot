"""페이퍼 캔들형 세션은 실봉을 구독하고 시작 전에 웜업한다."""
import asyncio
from types import SimpleNamespace

from app import paper
from app.engine import Macro


def _run(coro):
    loop = asyncio.new_event_loop()
    try:
        return loop.run_until_complete(coro)
    finally:
        loop.close()


def _rsi():
    return Macro.model_validate({
        "symbol": "BTCUSDT", "rule_type": "F", "position_side": "long", "market": "spot", "leverage": 1,
        "candle_interval": "5m", "period": {"preset": "3m"},
        "params": {"rsi_period": 2, "entry_threshold": 30, "exit_threshold": 70, "initial_capital": 1000},
        "risk": {"stop_loss_pct": 0, "daily_max_loss_pct": 0, "cooldown_minutes": 0, "max_holding_hours": 0},
        "fees": {"commission_pct": 0, "slippage_pct": 0},
    })


class _FakeFeed:
    def __init__(self):
        self.history_calls, self.subs = [], []

    async def history(self, symbol, interval, market, n):
        self.history_calls.append((symbol, interval, market, n))
        return [(i, 100 - i, 100 - i, 100 - i, 100 - i) for i in range(5)]

    def subscribe(self, symbol, interval, market, callback, **kwargs):
        sub = SimpleNamespace(key=(symbol, interval, market), callback=callback, since_t=kwargs.get("since_t"))
        self.subs.append(sub)
        return sub

    def unsubscribe(self, sub):
        self.subs.remove(sub)


def test_start_session_warms_up_and_subscribes(monkeypatch):
    fake = _FakeFeed()
    monkeypatch.setattr(paper, "feed", fake)
    monkeypatch.setattr(paper, "ensure_spot_available", lambda s: None)
    monkeypatch.setattr(paper, "_create_session", lambda *a: 91)

    async def no_loop(runner):
        return None

    monkeypatch.setattr(paper, "_run_loop", no_loop)
    try:
        _run(paper.start_session(_rsi(), None, "live"))
        runner = paper._running[91]
        assert fake.history_calls == [("BTCUSDT", "5m", "spot", paper.WARMUP_CANDLES)]
        assert [s.key for s in fake.subs] == [("BTCUSDT", "5m", "spot")]
        assert fake.subs[0].since_t == 4  # 웜업 마지막 봉의 t — 중복 배달 방지
        assert runner.sim.inner.rsi._count > 0 and runner.sim.state()["in_position"] is False
        # 피드 콜백이 봉을 밀어 넣으면 다음 틱에서 체결이 나온다.
        _run(fake.subs[0].callback("BTCUSDT", (0, 60, 60, 60, 60)))
        _run(fake.subs[0].callback("BTCUSDT", (1, 60, 60, 60, 60)))
        assert runner.sim.pending() >= 1
        _run(paper._finalize_async(runner))
        assert fake.subs == []  # 종료하면 구독 해제
    finally:
        paper._running.pop(91, None)


def test_tick_type_session_does_not_touch_feed(monkeypatch):
    fake = _FakeFeed()
    monkeypatch.setattr(paper, "feed", fake)
    monkeypatch.setattr(paper, "ensure_spot_available", lambda s: None)
    monkeypatch.setattr(paper, "_create_session", lambda *a: 92)

    async def no_loop(runner):
        return None

    monkeypatch.setattr(paper, "_run_loop", no_loop)
    macro = Macro(symbol="BTCUSDT", rule_type="A", params={"take_profit_pct": 1.0, "initial_capital": 1_000.0})
    try:
        _run(paper.start_session(macro, None, "live"))
        assert fake.history_calls == [] and fake.subs == []
    finally:
        paper._running.pop(92, None)
