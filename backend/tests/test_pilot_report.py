"""관리자 '파일럿 성과' — 체결 기록으로 실현손익을 다시 계산하고(수수료 차감 · 평균단가), 기간 · 모드 · 계정 범위를 지키며,
확인 필요 문장을 이벤트에서 뽑고, 관리자만 볼 수 있다.
"""
from __future__ import annotations

import json
import secrets

import pytest
from fastapi.testclient import TestClient

from app import pilot_report
from app.db import RunSession, RunSessionEvent, User, get_session
from app.main import app

client = TestClient(app)
START = "2026-10-13T23:00:00Z"
END = "2026-10-16T08:00:00Z"


@pytest.fixture(autouse=True)
def _fresh_cache():
    pilot_report._cache.clear()
    yield
    pilot_report._cache.clear()


def _signup(prefix="p"):
    tok = secrets.token_hex(4)
    body = client.post("/api/auth/signup", json={"email": f"{prefix}{tok}@ex.com", "username": f"{prefix}_{tok}", "password": "password123"}).json()
    return body["token"], body["user"]["id"]


def _fill(action, qty, price):
    return {"action": action, "executed_qty": qty, "fill_price": price, "acquired_qty": qty}


def _session(user_id, *, mode="live", exchange="upbit", symbol="KRW-WAL", started="2026-10-14T01:00:00Z", status="stopped",
             last_price=0.0, realized_pnl=0.0, invested=1000.0, fills=(), events=()):
    with get_session() as db:
        row = RunSession(user_id=user_id, symbol=symbol, mode=mode, status=status, started_at=started, last_price=last_price,
                         stopped_at=None if status == "running" else "2026-10-14T05:00:00Z", realized_pnl=realized_pnl,
                         invested_usdt=invested, macro_json=json.dumps({"exchange": exchange, "rule_type": "I"}))
        db.add(row)
        db.commit()
        db.refresh(row)
        for i, (ts, detail) in enumerate(fills):
            db.add(RunSessionEvent(session_id=row.id, user_id=user_id, ts=ts, kind="fill", event_name="runner.order_result",
                                   message="주문 결과", details_json=json.dumps(detail), created_ms=i))
        for kind, name, message in events:
            db.add(RunSessionEvent(session_id=row.id, user_id=user_id, ts=started, kind=kind, event_name=name, message=message))
        db.commit()
        return row.id


def test_session_pnl_charges_fee_on_both_sides_and_values_the_rest_at_last_price():
    row = RunSession(user_id=1, symbol="KRW-WAL", started_at=START, last_price=12.0)
    pnl = pilot_report.session_pnl(row, "upbit", [_fill("BUY", 100, 10), _fill("SELL", 60, 11)])
    # 매수 1000 + 수수료 0.5 → 평균단가 10.005, 60개를 11 에 팔면 660 − 0.33 − 600.3
    assert pnl["realized"] == pytest.approx(59.37, abs=0.01)
    assert pnl["open_qty"] == pytest.approx(40)
    assert pnl["unrealized"] == pytest.approx(40 * 12 - 400.2, abs=0.01)
    assert pnl["fees"] == pytest.approx(0.83, abs=0.01)
    assert (pnl["buys"], pnl["sells"], pnl["fallback"]) == (1, 1, False)


def test_session_without_fill_records_falls_back_to_runner_reported_pnl():
    row = RunSession(user_id=1, symbol="KRW-TIA", started_at=START, realized_pnl=-901.0)
    pnl = pilot_report.session_pnl(row, "upbit", [])
    assert pnl["fallback"] is True
    assert pnl["realized"] == -901.0 and pnl["net"] == -901.0


def test_build_keeps_period_mode_and_account_scope(monkeypatch):
    _, winner = _signup("w")
    _, loser = _signup("l")
    _, idle = _signup("i")
    _, outsider = _signup("o")
    monkeypatch.setenv("PILOT_USER_IDS", f"{winner},{loser},{idle}")
    monkeypatch.setenv("PILOT_START", START)
    monkeypatch.setenv("PILOT_END", END)

    _session(winner, fills=[("2026-10-14T01:10:00Z", _fill("BUY", 100, 10)), ("2026-10-14T02:00:00Z", _fill("SELL", 100, 12)),
                            # 기간이 끝난 뒤의 체결은 세지 않는다
                            ("2026-10-16T09:00:00Z", _fill("BUY", 100, 10))])
    _session(winner, mode="mock", fills=[("2026-10-14T01:10:00Z", _fill("BUY", 100, 10))])
    _session(winner, started="2026-10-13T20:00:00Z", fills=[("2026-10-13T20:10:00Z", _fill("BUY", 100, 10))])  # 기간 전
    _session(loser, status="running", last_price=8.0, fills=[("2026-10-14T01:10:00Z", _fill("BUY", 100, 10))],
             events=[("error", "command.failed", "⚠ 진입 실패 · 주문할 원화 잔고가 모자라요(insufficient_funds_bid)."),
                     ("error", "command.failed", "⚠ 진입 실패 · 로컬 리스크 보류: 신호 가격과 지금 가격이 달라 진입하지 않았어요."),
                     ("warn", "engine.position_mismatch", "서버 전략은 포지션 보유 중인데 실행기는 비어 있어요"),
                     ("warn", "runner.connection_lost", "서버 연결 재시도 중")])
    _session(idle, exchange="binance", symbol="BTCUSDT",
             events=[("warn", "runner.order_result", "실전 거래소 어댑터 주문 결과 · status=unknown · id=미기록")])
    _session(outsider, fills=[("2026-10-14T01:10:00Z", _fill("BUY", 100, 10))])

    with get_session() as db:
        data = pilot_report.build(db)

    names = [a["user_id"] for a in data["accounts"]]
    assert names == [winner, loser, idle]  # 합계순, 체결 없는 계정은 뒤로; 범위 밖 계정은 없다
    win = data["accounts"][0]
    assert win["live_sessions"] == 1 and win["mock_sessions"] == 1
    assert win["realized"] == pytest.approx(1200 - 0.6 - 1000.5, abs=0.01)
    assert win["email"].startswith("w***@")
    lose = data["accounts"][1]
    assert lose["running_sessions"] == 1
    assert lose["unrealized"] == pytest.approx(800 - 1000.5, abs=0.01)
    assert lose["issues"] == ["잔고 부족으로 진입 주문이 1번 나가지 않았어요.",
                              "서버는 보유 중인데 실행기는 비어 있다는 경고가 1번 있었어요. 거래소 보유 수량 확인이 필요해요."]
    idle_row = data["accounts"][2]
    assert idle_row["traded_sessions"] == 0 and idle_row["sessions"] == []
    assert "결과 확인 불가" in idle_row["issues"][0]
    totals = data["totals"]
    assert (totals["testers"], totals["winners"], totals["losers"], totals["no_trades"]) == (3, 1, 1, 1)
    assert totals["net"] == pytest.approx(win["net"] + lose["net"], abs=0.01)


def test_endpoint_is_admin_only(monkeypatch):
    token, user_id = _signup("a")
    monkeypatch.setenv("PILOT_USER_IDS", str(user_id))
    headers = {"Authorization": f"Bearer {token}"}
    assert client.get("/api/admin/pilot", headers=headers).status_code == 403
    with get_session() as db:
        user = db.get(User, user_id)
        user.is_admin = True
        db.add(user)
        db.commit()
    res = client.get("/api/admin/pilot", headers=headers)
    assert res.status_code == 200
    body = res.json()
    assert body["totals"]["testers"] == 1 and body["accounts"][0]["user_id"] == user_id
