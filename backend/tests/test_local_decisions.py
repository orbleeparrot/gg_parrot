"""판단을 실행기가 하는 세션 — 서버는 비추기만 한다 (2026-10-20).

우리 서버가 잠깐 멈추면 사용자는 자기 PC 가 멀쩡한데도 매수 · 매도 시점을 놓친다. 그 귀책을
없애려고 판단을 실행기로 내린다. 서버 쪽에서 지켜야 할 것은 셋이다:

* **둘이 동시에 판단하지 않는다.** 서버 드라이버가 같이 돌면 같은 신호로 두 번 산다.
* **체결 기록이 새지 않는다.** 실행기가 판단하면 그 보고가 리더보드 · 수익률 · 활동 기록의
  유일한 입구다. 서버가 판단할 때는 '냈던 명령' 이라도 알지만, 이제는 아무것도 모른다.
* **운영 스위치가 꺼져 있으면 아무 일도 안 일어난다.** 거래 흐름을 바꾸는 전환이라
  코드가 다 들어간 뒤에도 한 번 더 켜야 한다.
"""
import pytest
from fastapi.testclient import TestClient

from app import runner as runner_mod
from app import runner_engine as eng
from app.db import RunnerCommand, RunSession, get_session
from app.main import app
from sqlmodel import select
from tests.test_runner import _auth, _signup
from tests.test_runner_signals_api import A, RSI, _key, _stop, _ticket

client = TestClient(app)


@pytest.fixture
def local_on(monkeypatch):
    monkeypatch.setattr(runner_mod, "LOCAL_DECISIONS_ENABLED", True)


@pytest.fixture
def no_driver(monkeypatch):
    started = []
    monkeypatch.setattr(eng, "schedule_start", lambda sid: started.append(sid))
    return started


def _start(key, macro, *, decides=None, version="20"):
    # 거래소 · 시장은 매크로를 따라간다 — 서버가 요청과 매크로가 어긋나면 422 로 막는다.
    body = {"symbol": macro["symbol"], "exchange": macro.get("exchange", "binance"),
            "position_side": macro.get("position_side", "long"), "leverage": 1,
            "market": macro.get("market", "spot"), "testnet": True, "human_summary": "t",
            "macro": macro, "runner_version": version}
    if decides is not None:
        body["decides"] = decides
    return client.post("/api/runner/start", json=body, headers={"X-Runner-Key": key})


def _beat(key, sid, **extra):
    body = {"session_id": sid, "in_position": False, "last_price": 0.01, **extra}
    return client.post("/api/runner/heartbeat", json=body, headers={"X-Runner-Key": key})


# --- 운영 스위치 -------------------------------------------------------------
def test_스위치가_꺼져_있으면_실행기가_요청해도_서버가_판단한다(no_driver):
    """코드가 다 들어가도 거래 흐름은 운영에서 한 번 더 켜야 바뀐다."""
    assert runner_mod.LOCAL_DECISIONS_ENABLED is False, "기본값은 꺼짐이어야 한다"
    key = _key(_signup())
    r = _start(key, RSI, decides="local")
    assert r.status_code == 200 and r.json()["decides"] == "server"
    sid = r.json()["session_id"]
    with get_session() as db:
        assert db.get(RunSession, sid).decides_locally is False
    assert no_driver == [sid], "서버 드라이버가 떠야 한다"
    _stop(key, sid)


def test_스위치가_켜져도_실행기가_말하지_않으면_서버가_판단한다(local_on, no_driver):
    """옛 실행기는 'decides' 를 보내지 않는다 — 그 실행기는 그대로 돌아야 한다."""
    key = _key(_signup())
    r = _start(key, RSI)
    assert r.json()["decides"] == "server"
    assert no_driver, "서버 드라이버가 떠야 한다"
    _stop(key, r.json()["session_id"])


def test_둘_다_켜지면_실행기가_판단하고_서버_드라이버는_뜨지_않는다(local_on, no_driver):
    """같이 돌면 같은 신호로 두 번 산다."""
    key = _key(_signup())
    r = _start(key, RSI, decides="local")
    assert r.status_code == 200 and r.json()["decides"] == "local"
    sid = r.json()["session_id"]
    with get_session() as db:
        assert db.get(RunSession, sid).decides_locally is True
    assert no_driver == [], "서버 드라이버가 뜨면 안 된다"
    _stop(key, sid)


def test_모르는_값은_서버로_읽는다(local_on, no_driver):
    key = _key(_signup())
    for value in ("", "LOCAL ", "원격", "true", "server"):
        r = _start(key, RSI, decides=value)
        expected = "local" if value.strip().lower() == "local" else "server"
        assert r.json()["decides"] == expected, value
        _stop(key, r.json()["session_id"])


# --- 명령을 주지 않는다 -------------------------------------------------------
def test_로컬_세션에는_주문_명령을_주지_않는다(local_on, no_driver):
    key = _key(_signup())
    sid = _start(key, RSI, decides="local").json()["session_id"]
    with get_session() as db:
        row = db.get(RunSession, sid)
        eng.insert_command(db, row, {"action": "buy", "notional_frac": 1.0, "qty_frac": 0.0,
                                     "signal_price": 0.01, "reason": "섞여 들어온 명령"},
                           now_ms=eng._now_ms())
        db.commit()
    assert _beat(key, sid).json()["commands"] == []
    _stop(key, sid)


def test_원격_종료는_그대로_간다(local_on, no_driver):
    """판단이 로컬로 가도 원격 종료 · 청산은 서버가 내려보낸다 — 그게 이 구조의 전제다."""
    token = _signup()
    key = _key(token)
    sid = _start(key, RSI, decides="local").json()["session_id"]
    client.post(f"/api/me/runner/sessions/{sid}/request-stop",
                json={"mode": "close_and_stop"}, headers=_auth(token))
    assert _beat(key, sid).json()["action"] == "close_and_stop"
    _stop(key, sid)


# --- 적립식 · SAR: 로컬 판단에서만 열린다 -------------------------------------
DCA = {"symbol": "KRW-BTC", "exchange": "upbit", "quote_currency": "KRW", "rule_type": "C",
       "position_side": "long", "market": "spot", "leverage": 1, "candle_interval": "1d",
       "period": {"preset": "3m"},
       "params": {"amount_per_buy": 5000.0, "interval_days": 1, "initial_capital": 100000},
       "risk": {"invest_ratio": 1.0}}
SAR = {"symbol": "BTCUSDT", "exchange": "binance", "rule_type": "K", "position_side": "long",
       "market": "futures", "leverage": 1, "candle_interval": "1h", "period": {"preset": "3m"},
       "params": {"drop_trigger_pct": 8, "partial_exit_pct": 50, "flip_to_short": True,
                  "short_take_profit_pct": 5, "short_stop_loss_pct": 4, "initial_capital": 1000},
       "risk": {"invest_ratio": 1.0}}


@pytest.mark.parametrize("macro", [DCA, SAR], ids=["적립식", "SAR"])
def test_서버가_판단하면_적립식과_SAR_은_거절된다(no_driver, macro):
    """막은 이유가 **서버 명령 모델**에 있다 — C 는 3초 틱을 세는 규칙이라 봉·명령 모델과
    맞지 않고, K 는 롱↔숏 전환을 명령으로 표현할 수 없다."""
    key = _key(_signup())
    r = _start(key, macro)
    assert r.status_code == 422
    assert r.json()["detail"] == runner_mod.UNSUPPORTED_RULE_DETAIL


@pytest.mark.parametrize("macro", [DCA, SAR], ids=["적립식", "SAR"])
def test_실행기가_판단하면_적립식과_SAR_도_열린다(local_on, no_driver, macro):
    """실행기는 자기 시계로 날짜를 세고 방향 전환도 자기 장부 안에서 한다 —
    명령 모델을 거치지 않으므로 표현할 수 없던 것이 없다."""
    key = _key(_signup())
    r = _start(key, macro, decides="local")
    assert r.status_code == 200, r.json()
    assert r.json()["decides"] == "local"
    sid = r.json()["session_id"]
    with get_session() as db:
        assert db.get(RunSession, sid).decides_locally is True
    assert no_driver == [], "로컬 판단 세션에 서버 드라이버가 뜨면 안 된다"
    _stop(key, sid)


def test_여러_종목_매크로는_로컬_판단으로도_열리지_않는다(local_on, no_driver):
    """실행기는 종목 하나만 읽는다 — 로컬 판단이어도 분산이 그대로 사라진다."""
    key = _key(_signup())
    r = _start(key, {**A, "symbols": ["BTCUSDT", "ETHUSDT"]}, decides="local")
    assert r.status_code == 422
    assert r.json()["detail"] == runner_mod.PORTFOLIO_UNSUPPORTED_DETAIL


def test_티켓_청구도_판단_의사를_본다(local_on, no_driver):
    token = _signup()
    _, ticket = _ticket(token, DCA)
    refused = client.post("/api/runner/launch-tickets/claim",
                          json={"ticket": ticket, "runner_version": "20"})
    assert refused.status_code == 422, "서버가 판단하면 적립식은 거절"
    ok = client.post("/api/runner/launch-tickets/claim",
                     json={"ticket": ticket, "runner_version": "20", "decides": "local"})
    assert ok.status_code == 200, ok.json()
    assert ok.json()["macro"]["rule_type"] == "C"


# --- 체결 기록 ---------------------------------------------------------------
def _commands(sid):
    with get_session() as db:
        return list(db.exec(select(RunnerCommand).where(RunnerCommand.session_id == sid)
                            .order_by(RunnerCommand.seq)).all())


def test_실행기가_올린_체결이_서버_장부에_남는다(local_on, no_driver):
    """이 경로가 없으면 리더보드 · 수익률 · 활동 기록이 통째로 빈다."""
    key = _key(_signup())
    sid = _start(key, RSI, decides="local").json()["session_id"]
    _beat(key, sid, local_fills=[
        {"action": "buy", "notional_frac": 1.0, "qty_frac": 0.0, "signal_price": 0.01,
         "reason": "RSI 23 ≤ 30 · 진입", "ok": True, "executed_qty": 3000.0, "fill_price": 0.0101},
        {"action": "sell", "notional_frac": 0.0, "qty_frac": 1.0, "signal_price": 0.013,
         "reason": "RSI 72 ≥ 70 · 청산", "ok": True, "executed_qty": 3000.0, "fill_price": 0.0129},
    ])
    rows = _commands(sid)
    assert [r.action for r in rows] == ["buy", "sell"]
    assert [r.status for r in rows] == ["acked", "acked"]
    assert [r.seq for r in rows] == [1, 2], "순서가 화면에 그대로 간다"
    assert rows[0].executed_qty == 3000.0 and rows[0].fill_price == 0.0101
    assert rows[0].reason == "RSI 23 ≤ 30 · 진입"
    _stop(key, sid)


def test_두_번째_하트비트도_순서를_이어_붙인다(local_on, no_driver):
    key = _key(_signup())
    sid = _start(key, RSI, decides="local").json()["session_id"]
    for price in (0.01, 0.02, 0.03):
        _beat(key, sid, local_fills=[{"action": "buy", "signal_price": price, "ok": True,
                                      "executed_qty": 1.0, "fill_price": price}])
    assert [r.seq for r in _commands(sid)] == [1, 2, 3]
    _stop(key, sid)


def test_실패한_체결도_남긴다(local_on, no_driver):
    """주문이 안 나간 것도 기록이다 — 문의가 오면 이 줄로 답한다."""
    key = _key(_signup())
    sid = _start(key, RSI, decides="local").json()["session_id"]
    _beat(key, sid, local_fills=[{"action": "buy", "ok": False, "executed_qty": 0.0,
                                  "error": "주문이 체결되지 않았어요.", "signal_price": 0.01}])
    row = _commands(sid)[0]
    assert row.status == "failed" and row.error == "주문이 체결되지 않았어요."
    _stop(key, sid)


def test_수량이_0_이면_성공으로_적지_않는다(local_on, no_driver):
    """ok 만 믿으면 '샀다고 적혔는데 수량 0' 인 줄이 남는다."""
    key = _key(_signup())
    sid = _start(key, RSI, decides="local").json()["session_id"]
    _beat(key, sid, local_fills=[{"action": "buy", "ok": True, "executed_qty": 0.0}])
    assert _commands(sid)[0].status == "failed"
    _stop(key, sid)


def test_이상한_보고는_버린다(local_on, no_driver):
    """망가진(또는 악의적인) 실행기가 장부를 더럽히지 못하게."""
    key = _key(_signup())
    sid = _start(key, RSI, decides="local").json()["session_id"]
    _beat(key, sid, local_fills=[
        {"action": "hold", "ok": True, "executed_qty": 1.0},            # 모르는 동작
        {"ok": True, "executed_qty": 1.0},                               # 동작 없음
        {"action": "buy", "ok": True, "executed_qty": "숫자 아님"},      # 읽을 수 없는 수량
        {"action": "buy", "ok": True, "executed_qty": 1.0, "signal_price": None},
    ])
    rows = _commands(sid)
    # 수량을 못 읽은 줄은 failed 로, 마지막 줄만 체결로 남는다. 한 줄이 이상해도 **나머지는
    # 살린다** — 통째로 버리면 멀쩡한 체결이 리더보드에서 사라진다.
    assert [r.action for r in rows] == ["buy", "buy"]
    assert [r.status for r in rows] == ["failed", "acked"]
    assert rows[1].signal_price == 0.0
    _stop(key, sid)


def test_모양이_아예_다른_보고는_요청_자체가_거절된다(local_on, no_driver):
    """목록 안에 객체가 아닌 값이 섞이면 422 — 실행기 쪽 버그이므로 조용히 삼키지 않는다."""
    key = _key(_signup())
    sid = _start(key, RSI, decides="local").json()["session_id"]
    r = _beat(key, sid, local_fills=["buy"])
    assert r.status_code == 422
    assert _commands(sid) == []
    _stop(key, sid)


def test_한_번에_올릴_수_있는_체결_수에_상한이_있다(local_on, no_driver):
    key = _key(_signup())
    sid = _start(key, RSI, decides="local").json()["session_id"]
    flood = [{"action": "buy", "ok": True, "executed_qty": 1.0}] * (eng.MAX_LOCAL_FILLS + 40)
    _beat(key, sid, local_fills=flood)
    assert len(_commands(sid)) == eng.MAX_LOCAL_FILLS
    _stop(key, sid)


def test_서버가_판단하는_세션은_로컬_보고를_받지_않는다(no_driver):
    """로컬 보고를 아무 세션에나 받으면 서버 명령과 겹쳐 같은 체결이 두 줄이 된다."""
    key = _key(_signup())
    sid = _start(key, RSI).json()["session_id"]
    _beat(key, sid, local_fills=[{"action": "buy", "ok": True, "executed_qty": 1.0}])
    assert _commands(sid) == []
    _stop(key, sid)


def test_화면이_누가_판단했는지_알_수_있다(local_on, no_driver):
    """숫자가 멈췄을 때 '서버가 계산해 주던 값' 인지 '실행기가 올린 사본' 인지 갈린다."""
    token = _signup()
    key = _key(token)
    local_sid = _start(key, RSI, decides="local").json()["session_id"]
    server_sid = _start(key, A).json()["session_id"]
    body = client.get("/api/me/runner/sessions", headers=_auth(token)).json()
    seen = {s["session_id"]: s["decides_locally"] for s in body["active"]}
    assert seen[local_sid] is True and seen[server_sid] is False
    _stop(key, local_sid)
    _stop(key, server_sid)


# --- 전략 상태 비추기 ---------------------------------------------------------
def test_실행기가_올린_전략_상태를_비춘다(local_on, no_driver):
    """마이페이지 차트의 가격선이 이걸 읽는다 — 없으면 화면이 멈춘다."""
    key = _key(_signup())
    sid = _start(key, RSI, decides="local").json()["session_id"]
    state = {"legs": [{"symbol": "ONEUSDT", "strategy": {"pending": "enter"}}], "checkpoint_ms": 7}
    _beat(key, sid, strategy_state=state)
    with get_session() as db:
        import json
        assert json.loads(db.get(RunSession, sid).state_json) == state
    _stop(key, sid)


def test_읽을_수_없는_상태는_옛_값을_지우지_않는다(local_on, no_driver):
    """반쪽 상태로 덮으면 화면이 없는 포지션을 그린다."""
    key = _key(_signup())
    sid = _start(key, RSI, decides="local").json()["session_id"]
    good = {"legs": [{"symbol": "ONEUSDT"}]}
    _beat(key, sid, strategy_state=good)
    for junk in ({}, None):
        _beat(key, sid, strategy_state=junk)
    # 모양이 다른 값은 요청 단계에서 거절된다 — 옛 값은 그대로다.
    assert _beat(key, sid, strategy_state="엉뚱한 문자열").status_code == 422
    with get_session() as db:
        import json
        assert json.loads(db.get(RunSession, sid).state_json) == good
    _stop(key, sid)


def test_너무_큰_상태는_저장하지_않는다(local_on, no_driver):
    key = _key(_signup())
    sid = _start(key, RSI, decides="local").json()["session_id"]
    assert eng.store_local_state(RunSession(), {"x": "가" * eng.MAX_LOCAL_STATE_CHARS}) is False
    _stop(key, sid)


# --- 가격선 비추기 -------------------------------------------------------------
def test_실행기가_올린_가격선을_화면이_받는다(local_on, no_driver):
    """실행기가 판단하면 **실행기만** 그 선을 안다 — 서버는 봉도 지표도 안 들고 있다."""
    token = _signup()
    key = _key(token)
    sid = _start(key, RSI, decides="local").json()["session_id"]
    levels = {"entry_target": 3.012, "exit_target": None, "stop_loss": 2.5,
              "trail_stop": None, "trail_activation": None, "armed": False,
              "reference": 2.3632, "band_upper": None, "band_lower": None,
              "next_buy_ms": None, "indicator": None, "pending": False}
    _beat(key, sid, strategy_state={"levels": levels, "checkpoint_ms": 7})
    body = client.get("/api/me/runner/sessions", headers=_auth(token)).json()
    mine = next(s for s in body["active"] if s["session_id"] == sid)
    assert mine["strategy_levels"] == levels
    _stop(key, sid)


def test_서버가_판단하는_세션은_서버_선을_쓴다(no_driver):
    """로컬 사본이 없는 세션까지 빈칸으로 만들면 안 된다 — 지금 돌고 있는 세션들이다."""
    token = _signup()
    key = _key(token)
    sid = _start(key, A).json()["session_id"]
    body = client.get("/api/me/runner/sessions", headers=_auth(token)).json()
    mine = next(s for s in body["active"] if s["session_id"] == sid)
    # A 규칙은 서버 엔진이 선을 내지 않는다 — 빈 칸이 오고, 그게 '관심 없음' 의 뜻이다.
    assert set(mine["strategy_levels"]) >= {"entry_target", "trail_stop", "pending"}
    _stop(key, sid)


def test_이상한_가격선은_비추지_않는다(local_on, no_driver):
    """실행기가 보낸 것을 그대로 믿되, 모양이 아니면 비추지 않는다."""
    token = _signup()
    key = _key(token)
    sid = _start(key, RSI, decides="local").json()["session_id"]
    _beat(key, sid, strategy_state={"levels": "엉뚱한 값", "checkpoint_ms": 7})
    body = client.get("/api/me/runner/sessions", headers=_auth(token)).json()
    mine = next(s for s in body["active"] if s["session_id"] == sid)
    assert isinstance(mine["strategy_levels"], dict)
    _stop(key, sid)


# --- 티켓 경로 ----------------------------------------------------------------
def test_빠른_실행_티켓도_로컬_판단으로_시작할_수_있다(local_on, no_driver):
    token = _signup()
    _, ticket = _ticket(token, A)
    claim = client.post("/api/runner/launch-tickets/claim",
                        json={"ticket": ticket, "runner_version": "20", "engine_version": "1"})
    assert claim.status_code == 200
    key = _key(token)
    r = _start(key, claim.json()["macro"], decides="local")
    assert r.json()["decides"] == "local"
    _stop(key, r.json()["session_id"])
