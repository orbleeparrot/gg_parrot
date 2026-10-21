"""체결 기준 장부 — 미결 주문이 있는 동안 전략은 판단하지 않는다(2026-10-21 설계 §9 결정 2).

보고 전 매수가 있는 동안 익절 · 손절을 판단하면 아직 모르는 평단 · 수량으로 판다. 청산 대기 중에 판단하면 물타기를
사거나 두 번째 청산을 낸다. 그래서 미결 주문이 있는 동안 닫힌 봉은 가장 최근 하나만 들고 있다가, 주문이 정리되면
그 봉으로 판단한다(재기동 되살리기와 같은 '가장 최근 하나' 규칙 — 묵은 봉의 신호로 한꺼번에 사고팔지 않는다).
"""
import asyncio
import json

from app import runner_engine as eng
from app.db import RunSession, get_session
from app.engine import Macro
from tests.test_fill_ledger_entries import H_KRW, bar, buy, done, driver, sim_of
from tests.test_fill_ledger_exits import holding, sell


class _Recorder:
    """진입 필터 대역 — 봉마다 갱신된 종가를 적는다(막지는 않는다)."""

    def __init__(self):
        self.closes = []

    def allows(self):
        return True

    def update(self, close, volume=None):
        self.closes.append(close)


def test_candle_during_an_exit_wait_is_judged_after_the_exit_settles():
    d, sim = holding()
    [tp] = bar(d, 1, 500, 511, 499, 505)
    assert bar(d, 2, 505, 512, 504, 508) == []  # 청산 대기 — 판단하지 않는다
    assert sim.ledger.deferred is not None and sim.ledger.deferred[2] == 512  # 2번 봉(고가 512)을 들고 있다
    result = d.fold(resolutions=[done(tp.order_ref, "failed")])  # 안 팔렸다 — 보류한 봉으로 다시 판단
    assert result["judged_deferred"]
    again = d.tick(508)
    assert again is not None and again.side == "sell" and again.order_ref == "o3"
    assert sim.ledger.deferred is None


def test_only_the_latest_waiting_candle_is_judged_but_the_filter_sees_every_close():
    d, sim = holding()
    sim.entry_filter = _Recorder()
    [tp] = bar(d, 1, 500, 511, 499, 505)
    seen = list(sim.entry_filter.closes)
    bar(d, 2, 505, 520, 504, 515)  # 묵은 봉 — 판단하지 않고 버린다
    bar(d, 3, 515, 509, 503, 506)  # 가장 최근 봉 — 익절선(510) 아래
    d.fold(fills=[sell("s1", tp.order_ref, 40.0, 510.0, 10.2)], resolutions=[done(tp.order_ref, "failed")])
    assert d.tick(506) is None  # 3번 봉만 판단 — 2번 봉의 고가 520 으로 팔지 않는다
    assert sim.entry_filter.closes == seen + [515, 506]


def test_pending_buy_holds_the_take_profit_decision_until_its_report():
    d = driver()
    bar(d, 0, 500, 501, 499, 500)  # 기본 매수 o1 — 보고 전
    sim = sim_of(d)
    assert bar(d, 1, 500, 520, 499, 515) == []  # 평단을 모르니 익절을 판단하지 않는다
    d.fold(fills=[buy("b1", "o1", 99.0, 505.0, 25.0)], resolutions=[done("o1")])
    tp = d.tick(515)
    assert tp is not None and tp.side == "sell" and tp.price == 505.0 * 1.02  # 실제 평단 505 의 익절선
    assert sim.avg_entry() == 505.0


def test_waiting_candle_survives_a_restart_and_is_judged_once():
    d, sim = holding()
    [tp] = bar(d, 1, 500, 511, 499, 505)
    bar(d, 2, 505, 512, 504, 508)
    state = json.loads(json.dumps(d.state()))
    again = driver()
    again.restore(state, leg_equity={}, total_equity=sim.equity(508))
    after = sim_of(again)
    assert after.ledger.deferred == sim.ledger.deferred and after.ledger.exit == tp.order_ref
    again.fold(resolutions=[done(tp.order_ref, "failed")])
    first = again.tick(508)
    assert first is not None and first.side == "sell"
    assert again.tick(508) is None


class _Feed:
    def __init__(self, rows):
        self.rows = rows
        self.subs = []

    async def history(self, symbol, interval, market, n, **kwargs):
        return self.rows

    def subscribe(self, symbol, interval, market, cb, **kwargs):
        self.subs.append(cb)
        return ("sub", symbol)

    def unsubscribe(self, sub):
        self.subs.clear()


async def _no_loop(live):
    """틱 루프를 끈다."""


def test_warmup_never_writes_virtual_orders_into_the_ledger(monkeypatch):
    """웜업은 지난 봉으로 지표만 채운다 — 장부가 켜진 채 돌면 가상 주문이 남아 이후 판단이 계속 보류된다."""
    rows = [(i * 900_000, 500 - i, 501 - i, 480 - i, 499 - i) for i in range(30)]  # 매수 · 물타기 · 손절이 나는 흐름
    monkeypatch.setattr(eng, "feed", _Feed(rows))
    monkeypatch.setattr(eng, "_run", _no_loop)
    now = eng._now_iso()
    with get_session() as db:
        row = RunSession(user_id=1, symbol="KRW-GTC", status="running", started_at=now, last_heartbeat_at=now,
                         runner_version="21", exec_policy=eng.FILL_LEDGER_POLICY,
                         macro_json=Macro.model_validate(H_KRW).model_dump_json())
        db.add(row)
        db.commit()
        db.refresh(row)
        sid = row.id

    async def scenario():
        assert await eng.start_driver(sid)
        inner = eng._drivers[sid].driver.legs[0].sim.inner
        assert inner.ledger is not None and inner.ledger.orders == {} and inner.ledger.seq == 0
        assert inner.lots == [] and inner.ledger.deferred is None
        await eng.stop_driver(sid)

    try:
        asyncio.new_event_loop().run_until_complete(scenario())
    finally:
        with get_session() as db:
            db.delete(db.get(RunSession, sid))
            db.commit()
