"""매크로 파일 서명·출처와 실행 이벤트 로그.

피드백: 로컬에서 파일을 고쳐 돌린 뒤 문의가 오면 원본인지 알 길이 없고, 무슨 주문이
나갔는지 기록도 없었다. 파일에 서명을 동봉하고 세션에 출처를 남기며, 실행기 로그를
heartbeat 로 서버에 쌓는다.
"""
from __future__ import annotations

import json
import secrets

from fastapi.testclient import TestClient

from app import macro_signing
from app import runner as runner_mod
from app.engine import Macro
from app.main import app

client = TestClient(app)

_MACRO = {
    "symbol": "BTCUSDT",
    "rule_type": "A",
    "position_side": "long",
    "leverage": 3,
    "params": {"take_profit_pct": 3.0, "initial_capital": 1000000},
    "risk": {"invest_ratio": 0.5, "stop_loss_pct": 2.0},
    "period": {"preset": "3m"},
}


def _signup() -> str:
    tok = secrets.token_hex(4)
    body = client.post(
        "/api/auth/signup",
        json={"email": f"sig{tok}@ex.com", "username": f"sig_{tok}", "password": "password123"},
    ).json()
    return body["token"]


def _auth(token: str) -> dict:
    return {"Authorization": f"Bearer {token}"}


def _runner_key(token: str) -> dict:
    key = client.get("/api/me/runner/key", headers=_auth(token)).json()["key"]
    return {"X-Runner-Key": key}


def _download_file() -> dict:
    response = client.post("/api/realtrade/macro-file", json={"macro": _MACRO})
    assert response.status_code == 200, response.text
    return json.loads(response.content)


# --- 서명 --------------------------------------------------------------
def test_macro_file_carries_a_signature_that_verifies():
    file = _download_file()
    sig = file["_sig"]
    assert sig["v"] == 1 and sig["alg"] == "HMAC-SHA256" and len(sig["hmac"]) == 64
    macro = Macro.model_validate(file)  # `_sig`·human_summary 는 무시된다
    assert macro_signing.verify(macro, sig)


def test_editing_a_trading_value_breaks_the_signature_but_cosmetic_edits_do_not():
    file = _download_file()
    sig = file["_sig"]

    tampered = {**file, "leverage": 20}
    assert not macro_signing.verify(Macro.model_validate(tampered), sig)

    cosmetic = {**file, "human_summary": "내 마음대로 바꾼 설명"}
    assert macro_signing.verify(Macro.model_validate(cosmetic), sig)


def test_digest_is_stable_for_the_same_macro():
    a = macro_signing.digest(Macro.model_validate(_MACRO))
    b = macro_signing.digest(Macro.model_validate({**_MACRO, "created_at": None}))
    assert a == b and len(a) == 12


# --- 세션 출처 ----------------------------------------------------------
def _start(headers: dict, body: dict) -> dict:
    response = client.post("/api/runner/start", json=body, headers=headers)
    assert response.status_code == 200, response.text
    return response.json()


def _session(token: str, session_id: int) -> dict:
    rows = client.get("/api/me/runner/sessions", headers=_auth(token)).json()
    for item in [*rows.get("active", []), *rows.get("recent", [])]:
        if item["session_id"] == session_id:
            return item
    raise AssertionError("session not listed")


def test_verified_file_session_is_marked_as_original():
    token = _signup()
    file = _download_file()
    started = _start(_runner_key(token), {
        "symbol": "BTCUSDT", "macro": file, "macro_sig": file["_sig"], "runner_version": "7",
    })
    assert started["macro_origin"] == "file_verified"
    assert started["macro_digest"]
    view = _session(token, started["session_id"])
    assert view["macro_origin"] == "file_verified"
    assert view["macro_origin_label"] == "원본 파일(서명 확인)"


def test_modified_file_session_is_marked_as_modified():
    token = _signup()
    file = _download_file()
    tampered = {**file, "risk": {**file["risk"], "stop_loss_pct": 30.0}}
    started = _start(_runner_key(token), {
        "symbol": "BTCUSDT", "macro": tampered, "macro_sig": file["_sig"], "runner_version": "7",
    })
    assert started["macro_origin"] == "file_modified"
    assert started["macro_origin_label"] == "수정된 파일"
    assert _session(token, started["session_id"])["macro_origin"] == "file_modified"


def test_legacy_runner_without_signature_is_unsigned_and_ticket_path_is_web():
    token = _signup()
    started = _start(_runner_key(token), {"symbol": "BTCUSDT", "macro": _MACRO})
    assert started["macro_origin"] == "file_unsigned"

    saved = client.post(
        "/api/me/macros", json={"macro": _MACRO, "name": "티켓"}, headers=_auth(token)
    ).json()["item"]
    ticket = client.post(
        "/api/me/runner/launch-tickets", json={"user_macro_id": saved["id"], "testnet": True},
        headers=_auth(token),
    ).json()
    claimed = client.post(
        "/api/runner/launch-tickets/claim",
        json={"ticket": ticket["launch_url"].split("ticket=")[1], "runner_version": "7"},
    ).json()
    started = _start({"X-Runner-Key": claimed["runner_key"]}, {
        "symbol": "BTCUSDT", "macro": claimed["macro"], "user_macro_id": claimed["user_macro_id"],
    })
    assert started["macro_origin"] == "web"


# --- 실행 이벤트 로그 ----------------------------------------------------
def test_events_flow_from_heartbeat_and_stop_into_the_session_log():
    token = _signup()
    headers = _runner_key(token)
    file = _download_file()
    started = _start(headers, {
        "symbol": "BTCUSDT", "macro": file, "macro_sig": file["_sig"], "runner_version": "7", "testnet": True,
    })
    sid = started["session_id"]

    beat = client.post("/api/runner/heartbeat", json={
        "session_id": sid, "last_price": 100.0,
        "events": [
            {"ts": "2026-09-14T01:00:00Z", "kind": "order", "message": "[진입] 100.0 → BUY 0.01 BTCUSDT"},
            {"ts": "2026-09-14T01:00:01Z", "kind": "weird", "message": "x" * 400},
            {"ts": "", "kind": "info", "message": "   "},  # 빈 메시지는 버린다
        ],
    }, headers=headers)
    assert beat.status_code == 200, beat.text
    assert beat.json()["action"] == "continue"
    # 딕셔너리가 아닌 이벤트는 요청 자체가 거절된다(스키마).
    bad = client.post("/api/runner/heartbeat", json={"session_id": sid, "events": ["x"]}, headers=headers)
    assert bad.status_code == 422

    stopped = client.post("/api/runner/stopped", json={
        "session_id": sid, "status": "stopped", "note": "포지션 없이 종료",
        "events": [{"ts": "2026-09-14T01:05:00Z", "kind": "fill", "message": "손익 +1.20% (+1.2 USDT)"}],
    }, headers=headers)
    assert stopped.status_code == 200
    # 같은 종료 보고를 다시 보내도 종료 이벤트는 한 번만 남는다.
    client.post("/api/runner/stopped", json={"session_id": sid, "status": "stopped", "note": "포지션 없이 종료"}, headers=headers)

    log = client.get(f"/api/me/runner/sessions/{sid}/events", headers=_auth(token)).json()
    assert log["macro_origin"] == "file_verified"
    kinds = [e["kind"] for e in log["events"]]
    assert kinds == ["stop", "fill", "info", "order", "start"]
    messages = [e["message"] for e in log["events"]]
    assert messages[-1].startswith("실행 시작 · 실행기 v7 · 테스트넷 · 매크로 원본 파일(서명 확인) · 지문 ")
    assert messages[0].startswith("종료 · 포지션 없이 종료 · 누적 실현손익 +0.00 USDT")
    assert len(messages[2]) == runner_mod.EVENT_MESSAGE_MAX
    assert all(e["ts_kst"] for e in log["events"])


def test_events_are_private_to_the_owner_and_capped(monkeypatch):
    monkeypatch.setattr(runner_mod, "EVENT_CAP", 5)
    token = _signup()
    headers = _runner_key(token)
    # 2026-09-22: 매크로 없는 구버전 시작은 426 — 이 테스트의 관심사(이벤트 상한·소유권)를 위해 A 매크로를 보낸다.
    sid = _start(headers, {"symbol": "BTCUSDT", "macro": _MACRO})["session_id"]
    for i in range(8):
        client.post("/api/runner/heartbeat", json={
            "session_id": sid, "events": [{"kind": "info", "message": f"tick {i}"}],
        }, headers=headers)

    log = client.get(f"/api/me/runner/sessions/{sid}/events", headers=_auth(token)).json()
    assert len(log["events"]) == 5
    assert [e["message"] for e in log["events"]] == ["tick 7", "tick 6", "tick 5", "tick 4", "tick 3"]

    other = _signup()
    assert client.get(f"/api/me/runner/sessions/{sid}/events", headers=_auth(other)).status_code == 404
