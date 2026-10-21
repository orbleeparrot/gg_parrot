"""체결 기준 장부 끝-끝 시나리오 — 설계 §5 완료 기준을 실제 하트비트 API · 서버 전략 루프로 확인한다(2026-10-21 과제 12).

가짜 실행기(Runner)는 실행기처럼 하트비트로 명령을 받아 '체결'하고, 다음 하트비트에 ack(주문 사실 orders) ·
로컬 체결(local_fills)을 보낸다. 서버(Server)는 가짜 봉 피드로 마감봉을 넣고 틱을 직접 돌린다.

설계 §5 의 '정책 없는 세션 · 레벨 20 세션은 재기동 뒤에도 옛 경로' 는 test_fill_ledger_policy ·
test_runner_entry_rollback(옛 되돌리기)이, '백테스트 결과 같다' 는 test_fill_ledger_golden 이 지킨다.
"""
import asyncio
import json

import pytest
from fastapi.testclient import TestClient
from sqlmodel import select

from app import runner_engine as eng
from app.db import RunnerCommand, RunnerFill, RunSession, get_session
from app.main import app
from tests.test_fill_ledger_entries import H_KRW
from tests.test_runner import _auth, _signup

client = TestClient(app)
STEP = 900_000
STOP = {**H_KRW, "risk": {"invest_ratio": 1.0, "stop_loss_pct": 5.0, "cooldown_minutes": 30}}
ACTUAL = 50000 / 1.0005 / 427  # 실행기: 수수료 포함 50,000원 → 427원에 117.03…개


class _Feed:
    def __init__(self, t0):
        self.t0 = t0
        self.subs = []

    async def history(self, symbol, interval, market, n, **kwargs):
        return [(self.t0 - (20 - i) * STEP, 500, 500, 500, 500) for i in range(20)]

    def subscribe(self, symbol, interval, market, cb, **kwargs):
        self.subs.append(cb)
        return ("sub", symbol)

    def unsubscribe(self, sub):
        self.subs.clear()


async def _no_loop(live):
    """틱 루프를 끈다 — 시험이 틱을 직접 돌린다."""


class Server:
    """서버 전략 루프 대역 — 마감봉을 넣고 틱을 돌린다(틱 하나에 큐의 체결 하나 · 체결 기록표 접기)."""

    def __init__(self, monkeypatch, sid):
        self.sid = sid
        self.loop = asyncio.new_event_loop()
        self.t0 = (eng._now_ms() // STEP + 2) * STEP  # 봉 시각을 지금 뒤로 — 진입 대기(지금 + 몇 분)에 막히지 않게
        self.feed = _Feed(self.t0)
        self.price = 500.0
        self.i = 0
        monkeypatch.setattr(eng, "feed", self.feed)
        monkeypatch.setattr(eng, "_run", _no_loop)
        monkeypatch.setattr(eng, "get_ticker_price_cached", lambda s, **kw: self.price)
        assert self.loop.run_until_complete(eng.start_driver(sid))

    @property
    def sim(self):
        return eng._drivers[self.sid].driver.legs[0].sim.inner

    def bar(self, o, h, l, c):
        t = self.t0 + self.i * STEP
        self.i += 1
        self.price = c
        self.loop.run_until_complete(self.feed.subs[0]("KRW-GTC", (t, o, h, l, c)))
        self.tick()

    def tick(self, n=3):
        for _ in range(n):
            self.loop.run_until_complete(eng._tick_once(eng._drivers[self.sid]))

    def restart(self):
        self.loop.run_until_complete(eng.stop_driver(self.sid))
        assert self.loop.run_until_complete(eng.start_driver(self.sid))

    def close(self):
        self.loop.run_until_complete(eng.stop_driver(self.sid))
        self.loop.close()


class Runner:
    """실행기 대역 — 자기 체결로 보유를 세고, 하트비트마다 ack · 로컬 체결을 보낸다."""

    def __init__(self, key, sid):
        self.key, self.sid = key, sid
        self.held = self.cost = 0.0
        self.acks, self.local = [], []
        self.n = 0
        self.last = None

    def beat(self):
        # 실행기는 팔 수 없는 잔량(먼지)만 남으면 무포지션으로 보고하고 수량 칸에 그 잔량을 싣는다.
        body = {"session_id": self.sid, "in_position": self.held > 0.5, "position_qty": max(self.held, 0.0),
                "entry_price": self.cost / self.held if self.held > 1e-9 else 0.0, "acks": self.acks,
                "local_fills": self.local}
        self.last = body
        self.acks, self.local = [], []
        return self._post(body)

    def resend(self):
        """응답이 유실됐다 — 서버는 처리했지만 실행기는 모르니 같은 본문을 다시 보낸다."""
        return self._post(self.last)

    def _post(self, body):
        r = client.post("/api/runner/heartbeat", json=body, headers={"X-Runner-Key": self.key})
        assert r.status_code == 200, r.text
        return r.json()["commands"]

    def _order(self, side, qty, price, fee, **extra):
        self.n += 1
        if side == "buy":
            self.held += qty
            self.cost += qty * price
        elif self.held > 0:
            self.cost -= self.cost * min(1.0, qty / self.held)
            self.held -= qty
        return {"client_order_id": f"ggp-{self.sid}-{self.n}", "exchange_order_id": f"ex-{self.n}",
                "status": "FILLED", "side": side, "executed_qty": qty, "acquired_qty": qty, "fill_price": price,
                "fee_quote": fee, "fee_known": True, "ts_ms": eng._now_ms(), **extra}

    def execute(self, cmd, qty, price=0.0, fee=0.0, *, ok=True, error=""):
        side = "sell" if cmd["action"] == "sell" else "buy"
        orders = [self._order(side, qty, price, fee)] if qty > 0 else []
        outcome = "execution_reported" if ok else ("partially_executed" if qty > 0 else "execution_failed")
        self.acks.append({"command_id": cmd["id"], "ok": ok, "executed_qty": qty, "fill_price": price if qty else 0.0,
                          "error": error, "outcome": outcome, "orders": orders})

    def nothing_held(self, cmd):
        self.acks.append({"command_id": cmd["id"], "ok": True, "executed_qty": 0.0, "fill_price": 0.0,
                          "outcome": "no_position_no_order", "orders": []})

    def local_sell(self, qty, price, fee=0.0, reason="stop_loss"):
        self.local.append(self._order("sell", qty, price, fee, reason=reason))


def _start(key, macro):
    body = {"symbol": "KRW-GTC", "exchange": "upbit", "position_side": "long", "leverage": 1, "market": "spot",
            "testnet": False, "mode": "live", "human_summary": "t", "macro": macro, "runner_version": "21"}
    r = client.post("/api/runner/start", json=body, headers={"X-Runner-Key": key})
    assert r.status_code == 200, r.text
    return r.json()["session_id"]


@pytest.fixture
def flow(monkeypatch):
    eng._drivers.clear()
    made = []

    def make(macro=H_KRW):
        key = client.get("/api/me/runner/key", headers=_auth(_signup())).json()["key"]
        sid = _start(key, macro)
        server = Server(monkeypatch, sid)
        made.append((key, sid, server))
        return server, Runner(key, sid)

    yield make
    for key, sid, server in made:
        server.close()
        client.post("/api/runner/stopped", json={"session_id": sid, "status": "stopped"}, headers={"X-Runner-Key": key})
    eng._drivers.clear()


def holding(server, runner, price=500.0, qty=100.0):
    """기본 매수를 price 에 qty 개 실제로 산 상태로."""
    server.bar(price, price + 1, price - 1, price)
    [base] = runner.beat()
    runner.execute(base, qty, price, 25.0)
    assert runner.beat() == []
    server.tick()
    assert server.sim.total_qty() == pytest.approx(qty) and server.sim.ledger.orders == {}


def _fills(sid):
    with get_session() as db:
        return list(db.exec(select(RunnerFill).where(RunnerFill.session_id == sid)).all())


# 1. 시장가 진입 · 청산이 실제 체결가로 장부를 갱신한다(가상 평단 426 → 실제 427).
def test_actual_fill_price_drives_the_take_profit_line(flow):
    server, runner = flow()
    server.bar(426, 427, 425, 426)
    [base] = runner.beat()
    assert base["action"] == "buy"
    runner.execute(base, ACTUAL, 427.0, 24.99)
    assert runner.beat() == []
    server.tick()
    assert server.sim.avg_entry() == pytest.approx(427.0)
    server.bar(427, 435.0, 426, 430)  # 가상 평단 426 이면 익절(434.52)이었다
    assert runner.beat() == []
    server.bar(430, 436.0, 429, 433)
    [tp] = runner.beat()
    assert tp["action"] == "sell" and tp["signal_price"] == pytest.approx(427.0 * 1.02) and tp["qty_frac"] == 1.0
    runner.execute(tp, ACTUAL, 435.6, 25.48)
    runner.beat()
    server.tick()
    assert not server.sim.in_position()
    assert server.sim.closed_trades[-1] == pytest.approx(ACTUAL * (435.6 - 427.0) - 25.48)


# 2. 청산 부분 체결: 10 중 4 → 서버 6 · 청산 대기 유지 → 남은 6 체결. 무체결 확인 → 보유 그대로.
def test_partial_exit_keeps_waiting_and_sells_the_rest(flow):
    server, runner = flow()
    holding(server, runner)
    server.bar(500, 511, 499, 505)
    [tp] = runner.beat()
    runner.execute(tp, 40.0, 510.0, 10.2, ok=False, error="주문이 일부만 체결되고 끝났어요")
    [again] = runner.beat()
    assert again["id"] == tp["id"]  # 남은 수량을 다시 판다
    server.tick()
    assert server.sim.total_qty() == pytest.approx(60.0) and server.sim.ledger.exit is not None
    server.bar(505, 506, 504, 505)  # 청산 대기 중 — 판단하지 않는다
    runner.execute(again, 60.0, 509.0, 15.27)
    assert runner.beat() == []
    server.tick()
    assert server.sim.ledger.exit is None and server.sim.ledger.epoch == 1  # 포지션이 끝났다
    assert server.sim.closed_trades[-2:] == [pytest.approx(40 * 10.0 - 10.2), pytest.approx(60 * 9.0 - 15.27)]
    [rebuy] = runner.beat()  # 들고 있던 봉으로 판단 — 비었으니 마틴게일이 다시 첫 매수
    assert rebuy["action"] == "buy" and rebuy["signal_price"] == pytest.approx(505.0)


def test_exit_that_never_fills_keeps_the_position_and_is_judged_again(flow):
    server, runner = flow()
    holding(server, runner)
    server.bar(500, 511, 499, 505)
    [tp] = runner.beat()
    for _ in range(eng.EXIT_RETRY_MAX):
        runner.execute(tp, 0.0, ok=False, error="거래소가 주문을 받지 않았어요")
        runner.beat()
    server.tick()
    assert server.sim.total_qty() == pytest.approx(100.0) and server.sim.ledger.exit is None
    server.bar(505, 512, 504, 508)
    [again] = runner.beat()
    assert again["action"] == "sell" and again["id"] != tp["id"]


# 3. 물타기 부분 체결 · 무체결 — 차수 잔예산 · 최초 매수가 · 전체 예산.
def test_safety_order_partial_fill_reuses_only_the_rest_at_its_line(flow):
    server, runner = flow()
    holding(server, runner)
    assert server.sim._base_price == pytest.approx(500.0)
    server.bar(500, 500, 494.0, 495)  # 1차선 495
    [so] = runner.beat()
    assert so["action"] == "buy" and so["signal_price"] == pytest.approx(495.0)
    runner.execute(so, 30000 / 495, 495.0, 15.0, ok=False, error="주문이 일부만 체결되고 끝났어요")
    runner.beat()
    server.tick()
    left = 50000 - 30000 - 15.0
    assert server.sim._reopen[1]["remaining"] == pytest.approx(left)
    server.bar(495, 497, 495.5, 496)  # 1차선 위 — 쫓아 사지 않는다
    assert runner.beat() == []
    server.bar(496, 496, 494.5, 495)
    [rest] = runner.beat()
    assert rest["notional_frac"] == pytest.approx(left / 300000)


def test_safety_order_that_never_filled_is_reopened_and_waits_out_the_hold(flow):
    server, runner = flow()
    holding(server, runner)
    server.bar(500, 500, 494.0, 495)
    [so] = runner.beat()
    runner.execute(so, 0.0, ok=False, error="잔고가 모자라요")
    runner.beat()
    server.tick()
    assert server.sim._reopen[1]["remaining"] == pytest.approx(50000)
    assert eng._drivers[server.sid].driver.entry_hold_until_ms  # 잔고 부족을 봉마다 두드리지 않는다


# 4. 매수가 정리된 뒤에 청산 수량이 정해진다(결정 2).
def test_take_profit_waits_for_the_buy_report_and_uses_the_actual_average(flow):
    server, runner = flow()
    server.bar(500, 501, 499, 500)
    [base] = runner.beat()
    runner.execute(base, 99.0, 505.0, 25.0)  # 실행기는 샀지만 보고는 다음 하트비트
    server.bar(500, 520, 499, 515)  # 평단을 모른다 — 판단 보류
    assert server.sim.ledger.deferred is not None
    assert runner.beat() == []
    server.tick()
    [tp] = runner.beat()
    assert tp["action"] == "sell" and tp["signal_price"] == pytest.approx(505.0 * 1.02) and tp["qty_frac"] == 1.0


# 5. 먼지 → '먼지 보유' → 다음 진입 평단에 합침. 손절 대기 · 일일 손실은 그대로.
def test_dust_after_an_exit_joins_the_next_position(flow):
    server, runner = flow()
    holding(server, runner)
    server.bar(500, 511, 499, 505)
    [tp] = runner.beat()
    runner.execute(tp, 99.99, 510.0, 25.5)
    runner.beat()
    server.tick()
    assert not server.sim.in_position() and server.sim.ledger.dust["qty"] == pytest.approx(0.01)
    server.bar(505, 506, 504, 505)
    [base] = runner.beat()
    runner.execute(base, 99.0, 505.0, 25.0)
    runner.beat()
    server.tick()
    assert server.sim.total_qty() == pytest.approx(99.01) and server.sim._base_price == pytest.approx(505.0)


# 7. 실행기 로컬 손절 → 서버 장부 무포지션, 남은 익절 · 재진입 명령 취소.
def test_local_stop_loss_ends_the_position_and_cancels_what_was_waiting(flow):
    server, runner = flow(STOP)
    holding(server, runner)
    server.bar(500, 511, 499, 505)  # 익절 명령이 생겼지만 아직 실행기에 가지 않았다
    with get_session() as db:
        waiting = [c.id for c in db.exec(select(RunnerCommand).where(RunnerCommand.session_id == server.sid,
                                                                     RunnerCommand.status == "pending")).all()]
    assert waiting
    runner.local_sell(100.0, 474.0, 23.7)
    assert runner.beat() == []
    server.tick()
    assert not server.sim.in_position() and server.sim._cooldown_until is not None
    with get_session() as db:
        assert {db.get(RunnerCommand, cid).status for cid in waiting} == {"cancelled"}


# 8. 익절 일부 체결 뒤 로컬 손절이 나머지를 팔았다 — 둘 다 실제 체결이니 둘 다 반영, 남은 명령은 취소.
def test_take_profit_and_local_stop_both_count_once(flow):
    server, runner = flow(STOP)
    holding(server, runner)
    server.bar(500, 511, 499, 505)
    [tp] = runner.beat()
    runner.execute(tp, 40.0, 510.0, 10.2, ok=False, error="주문이 일부만 체결되고 끝났어요")
    runner.local_sell(60.0, 474.0, 14.2)
    assert runner.beat() == []  # 실행기가 비었다 — 익절 재시도는 보내지 않는다
    server.tick()
    assert not server.sim.in_position() and len(_fills(server.sid)) == 3  # 매수 · 익절 40 · 손절 60
    with get_session() as db:
        assert db.get(RunnerCommand, tp["id"]).status == "cancelled"


# 9. 응답 유실 · 중복 보고 · 주문 직후 서버 재기동 → 체결 한 번, 잔량 일치.
def test_lost_reply_and_duplicate_report_count_once(flow):
    server, runner = flow()
    server.bar(426, 427, 425, 426)
    [base] = runner.beat()
    runner.execute(base, ACTUAL, 427.0, 24.99)
    runner.beat()
    runner.resend()  # 같은 ack 가 다시 온다
    server.tick()
    assert len(_fills(server.sid)) == 1
    assert server.sim.total_qty() == pytest.approx(ACTUAL) == pytest.approx(runner.held)
    assert server.sim.cash == pytest.approx(300000 - ACTUAL * 427.0 - 24.99)


def test_server_restart_right_after_the_order_folds_once(flow):
    server, runner = flow()
    server.bar(426, 427, 425, 426)
    [base] = runner.beat()
    runner.execute(base, ACTUAL, 427.0, 24.99)
    runner.beat()
    server.restart()  # 접기 전에 배포 · 재기동
    server.tick()
    assert server.sim.total_qty() == pytest.approx(ACTUAL) and server.sim.avg_entry() == pytest.approx(427.0)
    with get_session() as db:
        state = json.loads(db.get(RunSession, server.sid).state_json)
    assert state["legs"][0]["strategy"]["ledger"]["keys"] == [f["client_order_id"]
                                                             for a in runner.last["acks"] for f in a["orders"]]


# 10. 실행기 재시작(새 세션) — 옛 세션 장부는 마지막 보고까지, 새 세션과 섞이지 않는다.
def test_runner_restart_starts_a_separate_ledger(flow):
    server, runner = flow()
    holding(server, runner)
    with get_session() as db:
        before = db.get(RunSession, server.sid).state_json
    other, runner2 = flow()  # 실행기 앱을 다시 켜면 새 세션으로 시작한다
    assert other.sim.lots == [] and other.sim.ledger.orders == {} and other.sim.ledger.keys == []
    with get_session() as db:
        assert db.get(RunSession, server.sid).state_json == before


# --- 2단계: 지정가 IOC(설계 §6) ---------------------------------------------------------------
def ioc(runner, cmd, qty=0.0, price=0.0, fee=0.0):
    """실행기가 지정가 IOC 를 냈다 — qty 0 이면 지정가보다 유리한 상대가 없어 체결 없이 끝났다(정상 결과)."""
    side = "sell" if cmd["action"] == "sell" else "buy"
    orders = [runner._order(side, qty, price, fee)] if qty > 0 else []
    outcome = "ioc_unfilled" if qty == 0 else "ioc_partial"
    runner.acks.append({"command_id": cmd["id"], "ok": True, "executed_qty": qty, "fill_price": price if qty else 0.0,
                        "error": "", "outcome": outcome, "orders": orders})


def _events(sid, name):
    from app.db import RunSessionEvent
    with get_session() as db:
        return [e for e in db.exec(select(RunSessionEvent).where(RunSessionEvent.session_id == sid)).all()
                if e.event_name == name]


def test_tail_touch_no_longer_sells_below_the_take_profit_line(flow):
    """세션 321 재현: 봉 고가만 익절선을 넘고 종가는 아래 — 지정가 IOC 는 팔리지 않고 보유가 그대로다."""
    server, runner = flow()
    holding(server, runner)
    server.bar(500, 511, 495, 498)  # 고가 511 ≥ 익절선 510, 종가 498
    [tp] = runner.beat()
    assert tp["order_type"] == "limit_ioc" and tp["limit_price"] == pytest.approx(510.0)
    ioc(runner, tp)  # 지금 매수 호가 498 — 510 이상에 살 사람이 없다
    assert runner.beat() == []
    server.tick()
    assert server.sim.total_qty() == pytest.approx(100.0) and server.sim.ledger.exit is None
    assert runner.held == pytest.approx(100.0) and server.sim.closed_trades == []  # 손해 매도 없음
    with get_session() as db:
        assert db.get(RunnerCommand, tp["id"]).status == "acked"  # 실패 · 재시도가 아니다
        row = db.get(RunSession, server.sid)
        assert row.note == ""
    [event] = _events(server.sid, "engine.ledger_exit_unfilled")
    assert event.message.startswith("지정가 IOC — 510 KRW 이상에 팔 수 있는 상대가 없어 팔지 않았어요")
    server.bar(498, 512, 497, 511)  # 다음 봉 — 다시 판단
    [again] = runner.beat()
    assert again["action"] == "sell" and again["order_type"] == "limit_ioc" and again["id"] != tp["id"]
    ioc(runner, again, 100.0, 511.0, 25.55)  # 이번엔 지정가 이상에 다 팔렸다
    runner.acks[-1]["outcome"] = "execution_reported"
    runner.beat()
    server.tick()
    assert server.sim.closed_trades[-1] == pytest.approx(100 * (511.0 - 500.0) - 25.55)


def test_ioc_partial_exit_leaves_the_rest_to_the_strategy(flow):
    server, runner = flow()
    holding(server, runner)
    server.bar(500, 511, 499, 505)
    [tp] = runner.beat()
    ioc(runner, tp, 30.0, 510.0, 7.65)
    runner.beat()
    server.tick()
    assert server.sim.total_qty() == pytest.approx(70.0) and server.sim.ledger.exit is None
    [event] = _events(server.sid, "engine.ledger_exit_partial")
    assert "남은 70개는 다음 봉에서 다시 판단해요" in event.message


def test_ioc_safety_order_without_a_seller_reopens_its_line_without_a_hold(flow):
    server, runner = flow()
    holding(server, runner)
    server.bar(500, 500, 494.0, 497)  # 저가만 1차선 495 아래 — 종가 497
    [so] = runner.beat()
    assert so["order_type"] == "limit_ioc" and so["limit_price"] == pytest.approx(495.0)
    ioc(runner, so)
    runner.beat()
    server.tick()
    assert server.sim._reopen[1]["remaining"] == pytest.approx(50000)
    assert eng._drivers[server.sid].driver.entry_hold_until_ms is None  # 오류가 아니다 — 기다리지 않는다
    [event] = _events(server.sid, "engine.ledger_entry_unfilled")
    assert event.message.startswith("물타기 1차 — 495 KRW 이하에 살 수 있는 물량이 없어 사지 않았어요")


def test_price_unit_error_is_still_an_error(flow):
    server, runner = flow()
    holding(server, runner)
    server.bar(500, 511, 499, 505)
    [tp] = runner.beat()
    runner.execute(tp, 0.0, ok=False, error="주문 가격 단위가 맞지 않아요")
    [again] = runner.beat()
    assert again["id"] == tp["id"]  # 지금처럼 다시 보낸다(청산 실패 1/3)
    with get_session() as db:
        assert db.get(RunSession, server.sid).note == eng.EXIT_FAIL_NOTE


def test_legacy_level_20_session_never_gets_limit_orders(monkeypatch):
    key = client.get("/api/me/runner/key", headers=_auth(_signup())).json()["key"]
    body = {"symbol": "KRW-GTC", "exchange": "upbit", "position_side": "long", "leverage": 1, "market": "spot",
            "testnet": False, "mode": "live", "human_summary": "t", "macro": H_KRW, "runner_version": "20"}
    sid = client.post("/api/runner/start", json=body, headers={"X-Runner-Key": key}).json()["session_id"]
    eng._drivers.clear()
    server = Server(monkeypatch, sid)
    try:
        server.bar(500, 501, 499, 500)
        runner = Runner(key, sid)
        [base] = runner.beat()
        runner.execute(base, 100.0, 500.0, 25.0)
        runner.beat()
        server.bar(500, 511, 499, 505)
        commands = runner.beat()
        assert commands and all("order_type" not in c for c in commands)
    finally:
        server.close()
        client.post("/api/runner/stopped", json={"session_id": sid, "status": "stopped"}, headers={"X-Runner-Key": key})
        eng._drivers.clear()
