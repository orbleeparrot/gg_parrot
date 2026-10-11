"""누적 실현손익의 돈 단위가 거래소를 따라간다 (2026-10-12).

왜 생겼나: 업비트 KRW-ORCA 실거래가 끝난 뒤 알림이 "누적 실현손익 **+978.26 USDT**" 로 떴다.
978 원이었다. 거래소를 보지 않고 "USDT" 가 글자로 박혀 있었고, 업비트·빗썸 세션은 전부
그렇게 찍혔다. 화면 본문(+978 KRW)은 처음부터 맞았다 — 틀린 자리는 알림과 실행 로그 둘.
"""
import pytest

from app.db import RunSession
from app.runner import realized_pnl_text


def _row(symbol, pnl):
    return RunSession(user_id=1, symbol=symbol, realized_pnl=pnl, started_at="")


@pytest.mark.parametrize("symbol, pnl, expected", [
    ("KRW-ORCA", 978.26, "+978 KRW"),      # 제보된 그 세션
    ("KRW-NMR", 636.4, "+636 KRW"),
    ("KRW-BTC", -1_234_567.0, "-1,234,567 KRW"),  # 원화는 자리수가 커진다 — 쉼표를 쓴다
    ("krw-eth", 0.0, "+0 KRW"),            # 소문자 종목도 국내다
    ("BTCUSDT", 12.3456, "+12.35 USDT"),
    ("ETHUSDT", -0.5, "-0.50 USDT"),
])
def test_label_follows_the_exchange(symbol, pnl, expected):
    assert realized_pnl_text(_row(symbol, pnl)) == expected


def test_korean_sessions_never_say_usdt():
    for symbol in ("KRW-ORCA", "KRW-NMR", "KRW-BTC", "KRW-DOGE"):
        assert "USDT" not in realized_pnl_text(_row(symbol, 1000.0))
    # 해외는 그대로 USDT 여야 한다 — 전부 KRW 로 바꾸면 반대로 틀린다.
    assert "USDT" in realized_pnl_text(_row("BTCUSDT", 10.0))


def test_korean_amounts_have_no_decimals():
    """원화에 소수점을 붙이면 화면 본문(+978 KRW)과 어긋나 보인다."""
    assert realized_pnl_text(_row("KRW-ORCA", 978.26)) == "+978 KRW"
    assert "." not in realized_pnl_text(_row("KRW-ORCA", 978.26))
    assert "." in realized_pnl_text(_row("BTCUSDT", 978.26)), "USDT 는 소수 둘째 자리까지"


def test_symbol_is_a_safe_stand_in_for_the_exchange():
    """RunSession 에 거래소 칸이 없어 종목명으로 가른다 — 스키마가 그 모양을 보장한다."""
    from app.engine.schema import validate_symbol
    validate_symbol("KRW-BTC", "upbit")                 # 국내는 KRW- 만
    with pytest.raises(ValueError):
        validate_symbol("BTCUSDT", "upbit")
    with pytest.raises(ValueError):
        validate_symbol("KRW-BTC", "binance")           # 바이낸스는 그 접두사를 못 쓴다


def test_missing_symbol_falls_back_to_usdt():
    """종목이 비어 있어도(옛 행) 터지지 않는다."""
    assert realized_pnl_text(_row("", 1.0)) == "+1.00 USDT"


# --- 끝에서 끝까지: 알림과 실행 로그가 실제로 그 단위를 쓴다 ----------------
# 헬퍼만 시험하면 호출부를 되돌려도 초록이다(실제로 돌연변이 둘이 살아남았다).
# 사용자가 본 자리가 바로 호출부이므로 여기까지 본다.

import secrets

from fastapi.testclient import TestClient

from app.main import app

client = TestClient(app)

_KRW_MACRO = {
    "symbol": "KRW-ORCA", "exchange": "upbit", "quote_currency": "KRW", "rule_type": "A",
    "position_side": "long", "leverage": 1, "market": "spot",
    "params": {"take_profit_pct": 8.0, "initial_capital": 50_000},
    "risk": {"stop_loss_pct": 4.0}, "period": {"preset": "3m"},
}


def _signup():
    tok = secrets.token_hex(4)
    body = client.post("/api/auth/signup", json={
        "email": f"pnl{tok}@ex.com", "username": f"pnl_{tok}", "password": "password123"}).json()
    return body["token"]


def _finish_session(macro, symbol, runner_version, realized):
    # 시작 요청은 거래소를 따로 싣는다(세션 행에는 거래소 칸이 없다) — 안 보내면 바이낸스로 읽힌다.
    exchange = macro.get("exchange", "binance")
    token = _signup()
    key = client.get("/api/me/runner/key", headers={"Authorization": f"Bearer {token}"}).json()["key"]
    headers = {"X-Runner-Key": key}
    started = client.post("/api/runner/start", json={
        "symbol": symbol, "macro": macro, "exchange": exchange,
        "runner_version": runner_version}, headers=headers)
    assert started.status_code == 200, started.text
    sid = started.json()["session_id"]
    beat = client.post("/api/runner/heartbeat", json={
        "session_id": sid, "last_price": 3748.0, "realized_pnl": realized}, headers=headers)
    assert beat.status_code == 200, beat.text
    stop = client.post("/api/runner/stopped", json={
        "session_id": sid, "status": "stopped", "note": "청산 완료 후 종료"}, headers=headers)
    assert stop.status_code == 200, stop.text
    log = client.get(f"/api/me/runner/sessions/{sid}/events",
                     headers={"Authorization": f"Bearer {token}"}).json()
    notes = client.get("/api/me/notifications", headers={"Authorization": f"Bearer {token}"}).json()
    items = notes["items"] if isinstance(notes, dict) else notes
    return log["events"], items


def test_upbit_session_reports_krw_in_the_log_and_the_notification():
    """제보된 그 화면 — KRW-ORCA 978원이 'USDT' 로 떴다."""
    events, notifications = _finish_session(_KRW_MACRO, "KRW-ORCA", "10", 978.26)
    stop = next(e["message"] for e in events if e["kind"] == "stop")
    assert "+978 KRW" in stop and "USDT" not in stop, stop
    body = " ".join(str(n.get("body", "")) for n in notifications)
    assert "+978 KRW" in body and "USDT" not in body, body


def test_binance_session_still_reports_usdt():
    """국내를 고치면서 해외를 반대로 틀리지 않았는지."""
    binance = {**_KRW_MACRO, "symbol": "BTCUSDT", "exchange": "binance",
               "quote_currency": "USDT", "leverage": 1,
               "params": {"take_profit_pct": 8.0, "initial_capital": 1000}}
    events, notifications = _finish_session(binance, "BTCUSDT", "10", 12.3456)
    stop = next(e["message"] for e in events if e["kind"] == "stop")
    assert "+12.35 USDT" in stop and "KRW" not in stop, stop
    body = " ".join(str(n.get("body", "")) for n in notifications)
    assert "+12.35 USDT" in body, body
