"""매크로 실행기 연동 엔드포인트.

회원 키 발급/재발급, 실행기 세션 start→heartbeat→원격 종료(stop_mode)→stopped,
그리고 소유권/인증 경계(남의 세션 조작 불가, 잘못된 키 401)를 검증한다.
SQLite(테스트)로 돈다.
"""
from __future__ import annotations

import hashlib
import json
import secrets
from concurrent.futures import ThreadPoolExecutor
from threading import Barrier
from urllib.parse import parse_qs, urlsplit

from fastapi import HTTPException
from fastapi.testclient import TestClient

from app import runner as runner_mod
from app.agent_features.position_news import service as position_news_service
from app.db import RunnerLaunchTicket, get_session
from app.main import app

client = TestClient(app)


def _fresh(prefix="run"):
    tok = secrets.token_hex(4)
    return f"{prefix}{tok}@ex.com", f"{prefix}_{tok}"


def _signup() -> str:
    email, username = _fresh()
    body = client.post(
        "/api/auth/signup",
        json={"email": email, "username": username, "password": "password123"},
    ).json()
    return body["token"]


def _auth(token: str) -> dict:
    return {"Authorization": f"Bearer {token}"}


def _save_macro(token: str, symbol: str = "BTCUSDT") -> dict:
    macro = {
        "symbol": symbol,
        "rule_type": "A",
        "position_side": "long",
        "params": {"take_profit_pct": 3.0, "initial_capital": 1000000},
        "risk": {"invest_ratio": 0.5, "stop_loss_pct": 2.0},
        "period": {"preset": "3m"},
    }
    response = client.post(
        "/api/me/macros",
        json={"macro": macro, "name": "빠른 실행 테스트"},
        headers=_auth(token),
    )
    assert response.status_code == 200, response.text
    return response.json()["item"]


# --- 회원 키 ------------------------------------------------------------
def test_key_is_issued_once_and_stable():
    token = _signup()
    k1 = client.get("/api/me/runner/key", headers=_auth(token)).json()["key"]
    k2 = client.get("/api/me/runner/key", headers=_auth(token)).json()["key"]
    assert k1 and k1 == k2  # 계정당 1개, 재조회해도 동일


def test_key_regenerate_changes_and_invalidates_old():
    token = _signup()
    old = client.get("/api/me/runner/key", headers=_auth(token)).json()["key"]
    new = client.post("/api/me/runner/key/regenerate", headers=_auth(token)).json()["key"]
    assert new != old
    # 옛 키로 세션 시작 시 401
    r = client.post("/api/runner/start", json={"symbol": "BTCUSDT"}, headers={"X-Runner-Key": old})
    assert r.status_code == 401
    # 새 키는 동작
    r2 = client.post("/api/runner/start", json={"symbol": "BTCUSDT"}, headers={"X-Runner-Key": new})
    assert r2.status_code == 200


def test_start_requires_valid_key():
    assert client.post("/api/runner/start", json={"symbol": "BTCUSDT"}).status_code == 422 or \
        client.post("/api/runner/start", json={"symbol": "BTCUSDT"},
                    headers={"X-Runner-Key": ""}).status_code == 401
    r = client.post("/api/runner/start", json={"symbol": "BTCUSDT"}, headers={"X-Runner-Key": "nope"})
    assert r.status_code == 401


# --- 브라우저 -> 로컬 실행기 1회용 티켓 -------------------------------
def test_launch_ticket_create_claim_and_status_contract():
    token = _signup()
    saved = _save_macro(token)

    created = client.post(
        "/api/me/runner/launch-tickets",
        json={"user_macro_id": saved["id"], "testnet": True},
        headers=_auth(token),
    )
    assert created.status_code == 200, created.text
    assert created.headers["cache-control"] == "no-store"
    payload = created.json()
    assert payload["status"] == "ready"
    assert payload["expires_at"]
    # Include the slash Windows CreateUri adds for an authority with an empty
    # path, so the browser and runner agree on one canonical URI shape.
    assert payload["launch_url"].startswith("ggparrot://launch/?")

    parsed_launch = urlsplit(payload["launch_url"])
    assert parsed_launch.netloc == "launch"
    assert parsed_launch.path == "/"
    query = parse_qs(parsed_launch.query)
    assert query["v"] == ["2"]
    assert query["env"] == ["production"]
    ticket = query["ticket"][0]

    # Only the digest is durable; the bearer itself never enters the DB row.
    with get_session() as db:
        row = db.get(RunnerLaunchTicket, payload["launch_id"])
        assert row is not None
        assert row.token_hash == hashlib.sha256(ticket.encode("ascii")).hexdigest()
        assert ticket not in row.token_hash
        assert not hasattr(row, "ticket")

    ready = client.get(
        f"/api/me/runner/launch-tickets/{payload['launch_id']}",
        headers=_auth(token),
    )
    assert ready.status_code == 200
    assert ready.headers["cache-control"] == "no-store"
    assert ready.json()["status"] == "ready"

    claimed = client.post(
        "/api/runner/launch-tickets/claim",
        json={"ticket": ticket, "runner_version": "5"},
    )
    assert claimed.status_code == 200, claimed.text
    assert claimed.headers["cache-control"] == "no-store"
    body = claimed.json()
    assert body["launch_id"] == payload["launch_id"]
    assert body["user_macro_id"] == saved["id"]
    assert body["name"] == "빠른 실행 테스트"
    assert body["symbol"] == "BTCUSDT"
    assert body["macro"]["symbol"] == "BTCUSDT"
    assert body["runner_key"].startswith("ggp_")
    assert body["testnet"] is True

    after = client.get(
        f"/api/me/runner/launch-tickets/{payload['launch_id']}",
        headers=_auth(token),
    )
    assert after.json()["status"] == "claimed"

    replay = client.post(
        "/api/runner/launch-tickets/claim",
        json={"ticket": ticket, "runner_version": "5"},
    )
    assert replay.status_code == 409
    assert replay.headers["cache-control"] == "no-store"


def test_launch_ticket_uses_fixed_local_environment_for_loopback_web():
    token = _signup()
    saved = _save_macro(token)
    loopback = TestClient(app, base_url="http://localhost")
    created = loopback.post(
        "/api/me/runner/launch-tickets",
        json={"user_macro_id": saved["id"], "testnet": True},
        headers=_auth(token),
    )
    assert created.status_code == 200, created.text
    parsed_launch = urlsplit(created.json()["launch_url"])
    assert parsed_launch.netloc == "launch"
    assert parsed_launch.path == "/"
    query = parse_qs(parsed_launch.query)
    assert query["v"] == ["2"]
    assert query["env"] == ["local"]


def test_launch_ticket_rejects_old_runner_without_consuming_ticket():
    token = _signup()
    saved = _save_macro(token)
    created = client.post(
        "/api/me/runner/launch-tickets",
        json={"user_macro_id": saved["id"], "testnet": True},
        headers=_auth(token),
    ).json()
    ticket = parse_qs(urlsplit(created["launch_url"]).query)["ticket"][0]

    for body in ({"ticket": ticket}, {"ticket": ticket, "runner_version": "4"}):
        response = client.post("/api/runner/launch-tickets/claim", json=body)
        assert response.status_code == 426
        assert response.headers["cache-control"] == "no-store"

    status = client.get(
        f"/api/me/runner/launch-tickets/{created['launch_id']}",
        headers=_auth(token),
    )
    assert status.json()["status"] == "ready"

    current = client.post(
        "/api/runner/launch-tickets/claim",
        json={"ticket": ticket, "runner_version": "5"},
    )
    assert current.status_code == 200


def test_launch_ticket_enforces_macro_ownership_and_testnet():
    owner = _signup()
    stranger = _signup()
    saved = _save_macro(owner, "ETHUSDT")

    denied = client.post(
        "/api/me/runner/launch-tickets",
        json={"user_macro_id": saved["id"], "testnet": True},
        headers=_auth(stranger),
    )
    assert denied.status_code == 404
    assert denied.headers["cache-control"] == "no-store"

    live = client.post(
        "/api/me/runner/launch-tickets",
        json={"user_macro_id": saved["id"], "testnet": False},
        headers=_auth(owner),
    )
    assert live.status_code == 422

    made = client.post(
        "/api/me/runner/launch-tickets",
        json={"user_macro_id": saved["id"], "testnet": True},
        headers=_auth(owner),
    ).json()
    hidden = client.get(
        f"/api/me/runner/launch-tickets/{made['launch_id']}",
        headers=_auth(stranger),
    )
    assert hidden.status_code == 404
    assert hidden.headers["cache-control"] == "no-store"


def test_launch_ticket_expires_and_cannot_be_claimed():
    token = _signup()
    saved = _save_macro(token)
    created = client.post(
        "/api/me/runner/launch-tickets",
        json={"user_macro_id": saved["id"], "testnet": True},
        headers=_auth(token),
    ).json()
    ticket = parse_qs(urlsplit(created["launch_url"]).query)["ticket"][0]

    with get_session() as db:
        row = db.get(RunnerLaunchTicket, created["launch_id"])
        row.expires_ms = 0
        db.add(row)
        db.commit()

    status = client.get(
        f"/api/me/runner/launch-tickets/{created['launch_id']}",
        headers=_auth(token),
    )
    assert status.json()["status"] == "expired"
    claim = client.post(
        "/api/runner/launch-tickets/claim",
        json={"ticket": ticket, "runner_version": "5"},
    )
    assert claim.status_code == 410
    assert claim.headers["cache-control"] == "no-store"


def test_launch_ticket_claim_is_atomic_under_concurrency():
    token = _signup()
    saved = _save_macro(token)
    created = client.post(
        "/api/me/runner/launch-tickets",
        json={"user_macro_id": saved["id"], "testnet": True},
        headers=_auth(token),
    ).json()
    ticket = parse_qs(urlsplit(created["launch_url"]).query)["ticket"][0]
    gate = Barrier(2)

    def attempt():
        gate.wait()
        try:
            runner_mod.claim_launch_ticket(ticket)
            return 200
        except HTTPException as exc:
            return exc.status_code

    with ThreadPoolExecutor(max_workers=2) as pool:
        statuses = sorted(pool.map(lambda _: attempt(), range(2)))
    assert statuses == [200, 409]


def test_runner_download_info_advertises_launch_capability_safely():
    response = client.get("/api/runner/download/info")
    assert response.status_code == 200
    info = response.json()
    assert isinstance(info["supports_launch"], bool)
    if info["supports_launch"]:
        assert info["launch_scheme"] == "ggparrot"
        assert info["min_runner_version"]
        if "/runner-v5/" in info["url"]:
            assert int(info["min_runner_version"]) >= 5
            assert int(info["version"]) >= 5
    else:
        assert info["launch_scheme"] == ""
        assert info["min_runner_version"] == ""


# --- 세션 수명주기 + 원격 종료 -----------------------------------------
def test_full_lifecycle_stop_only():
    token = _signup()
    key = client.get("/api/me/runner/key", headers=_auth(token)).json()["key"]

    # start (숏 매크로 → 선물로 결정되는지 확인)
    start = client.post(
        "/api/runner/start",
        json={"symbol": "ethusdt", "position_side": "short", "leverage": 3,
              "human_summary": "테스트 매크로"},
        headers={"X-Runner-Key": key},
    ).json()
    sid = start["session_id"]
    assert start["poll_seconds"] >= 1

    # 첫 heartbeat: 종료명령 없음
    hb = client.post(
        "/api/runner/heartbeat",
        json={"session_id": sid, "in_position": True, "last_price": 3000.0,
              "entry_price": 2950.0, "position_qty": 0.1, "realized_pnl": 1.5,
              "unrealized_pct": -1.7},
        headers={"X-Runner-Key": key},
    ).json()
    assert hb["action"] == "continue"

    # 마이페이지 목록에 활성으로 보이고 스냅샷이 반영됨
    sessions = client.get("/api/me/runner/sessions", headers=_auth(token)).json()
    assert sessions["active"] and sessions["active"][0]["session_id"] == sid
    view = sessions["active"][0]
    assert view["market"] == "futures" and view["symbol"] == "ETHUSDT"
    assert view["in_position"] is True and view["last_price"] == 3000.0

    # 원격 종료 요청(매크로만)
    client.post(f"/api/me/runner/sessions/{sid}/request-stop",
                json={"mode": "stop_only"}, headers=_auth(token))

    # 다음 heartbeat 가 종료명령을 받아감
    hb2 = client.post("/api/runner/heartbeat", json={"session_id": sid},
                      headers={"X-Runner-Key": key}).json()
    assert hb2["action"] == "stop_only"

    # 실행기가 종료 확정 보고
    client.post("/api/runner/stopped",
                json={"session_id": sid, "status": "stopped", "note": "매크로만 종료 — 포지션 유지"},
                headers={"X-Runner-Key": key})

    after = client.get("/api/me/runner/sessions", headers=_auth(token)).json()
    assert not after["active"]
    assert any(s["session_id"] == sid and s["status"] == "stopped" for s in after["recent"])


def test_saved_macro_id_is_validated_persisted_and_returned_in_session_view():
    token = _signup()
    saved = _save_macro(token)
    key = client.get("/api/me/runner/key", headers=_auth(token)).json()["key"]

    started = client.post(
        "/api/runner/start",
        json={
            "user_macro_id": saved["id"],
            # Redundant transport fields are intentionally contradictory: an
            # ID-bound session must derive identity from the stored macro.
            "symbol": "ETHUSDT",
            "position_side": "short",
            "leverage": 3,
            "human_summary": "신뢰하면 안 되는 실행기 요약",
            "macro": saved["macro"],
        },
        headers={"X-Runner-Key": key},
    )
    assert started.status_code == 200, started.text

    sessions = client.get("/api/me/runner/sessions", headers=_auth(token)).json()
    session = next(
        item for item in sessions["active"]
        if item["session_id"] == started.json()["session_id"]
    )
    assert session["user_macro_id"] == saved["id"]
    assert session["macro"] == saved["macro"]
    assert session["symbol"] == saved["symbol"]
    assert session["position_side"] == saved["position_side"]
    assert session["leverage"] == saved["macro"]["leverage"]
    assert session["human_summary"] == saved["human_summary"]


def test_saved_macro_id_rejects_missing_macro_mismatch_and_foreign_owner():
    owner = _signup()
    stranger = _signup()
    saved = _save_macro(owner)
    owner_key = client.get("/api/me/runner/key", headers=_auth(owner)).json()["key"]
    stranger_key = client.get("/api/me/runner/key", headers=_auth(stranger)).json()["key"]

    missing = client.post(
        "/api/runner/start",
        json={"user_macro_id": saved["id"], "symbol": saved["symbol"]},
        headers={"X-Runner-Key": owner_key},
    )
    assert missing.status_code == 422

    changed_macro = json.loads(json.dumps(saved["macro"]))
    changed_macro["symbol"] = "ETHUSDT"
    mismatch = client.post(
        "/api/runner/start",
        json={
            "user_macro_id": saved["id"],
            "symbol": "ETHUSDT",
            "macro": changed_macro,
        },
        headers={"X-Runner-Key": owner_key},
    )
    assert mismatch.status_code == 409

    foreign = client.post(
        "/api/runner/start",
        json={
            "user_macro_id": saved["id"],
            "symbol": saved["symbol"],
            "macro": saved["macro"],
        },
        headers={"X-Runner-Key": stranger_key},
    )
    assert foreign.status_code == 404


def test_legacy_runner_can_start_without_saved_macro_id():
    token = _signup()
    key = client.get("/api/me/runner/key", headers=_auth(token)).json()["key"]
    started = client.post(
        "/api/runner/start",
        json={"symbol": "BTCUSDT"},
        headers={"X-Runner-Key": key},
    )
    assert started.status_code == 200
    sessions = client.get("/api/me/runner/sessions", headers=_auth(token)).json()
    session = next(
        item for item in sessions["active"]
        if item["session_id"] == started.json()["session_id"]
    )
    assert session["user_macro_id"] is None


def test_session_stream_token_is_scoped_and_websocket_pushes_updates():
    token = _signup()
    saved = _save_macro(token)
    key = client.get("/api/me/runner/key", headers=_auth(token)).json()["key"]
    issued = client.post(
        "/api/me/runner/sessions/stream-token",
        headers=_auth(token),
    )
    assert issued.status_code == 200
    assert issued.headers["cache-control"] == "no-store"
    scoped = issued.json()["token"]
    assert 15 <= issued.json()["expires_in"] <= 300

    # A purpose token cannot impersonate the normal login Bearer session.
    ordinary = client.get("/api/auth/me", headers=_auth(scoped))
    assert ordinary.status_code == 401

    protocols = ["ggparrot.sessions.v1", f"ggp-auth.{scoped}"]
    with client.websocket_connect(
        "/api/me/runner/sessions/stream",
        subprotocols=protocols,
    ) as websocket:
        assert websocket.accepted_subprotocol == "ggparrot.sessions.v1"
        initial = websocket.receive_json()
        assert initial["type"] == "sessions.snapshot"
        assert initial["reason"] == "initial"

        started = client.post(
            "/api/runner/start",
            json={
                "user_macro_id": saved["id"],
                "symbol": saved["symbol"],
                "macro": saved["macro"],
            },
            headers={"X-Runner-Key": key},
        )
        assert started.status_code == 200, started.text
        pushed = websocket.receive_json()
        assert pushed["type"] == "sessions.snapshot"
        assert pushed["reason"] == "update"
        assert any(
            item["session_id"] == started.json()["session_id"]
            and item["user_macro_id"] == saved["id"]
            for item in pushed["data"]["active"]
        )

        session_id = started.json()["session_id"]
        heartbeat = client.post(
            "/api/runner/heartbeat",
            json={"session_id": session_id, "last_price": 64123.5},
            headers={"X-Runner-Key": key},
        )
        assert heartbeat.status_code == 200
        heartbeat_push = websocket.receive_json()
        live = next(
            item for item in heartbeat_push["data"]["active"]
            if item["session_id"] == session_id
        )
        assert heartbeat_push["reason"] == "update"
        assert live["last_price"] == 64123.5

        stop = client.post(
            f"/api/me/runner/sessions/{session_id}/request-stop",
            json={"mode": "stop_only"},
            headers=_auth(token),
        )
        assert stop.status_code == 200
        stopping_push = websocket.receive_json()
        stopping = next(
            item for item in stopping_push["data"]["active"]
            if item["session_id"] == session_id
        )
        assert stopping["stopping"] is True
        assert stopping["stop_mode"] == "stop_only"

        stopped = client.post(
            "/api/runner/stopped",
            json={"session_id": session_id, "status": "stopped"},
            headers={"X-Runner-Key": key},
        )
        assert stopped.status_code == 200
        stopped_push = websocket.receive_json()
        assert all(item["session_id"] != session_id for item in stopped_push["data"]["active"])
        assert any(item["session_id"] == session_id for item in stopped_push["data"]["recent"])


def test_session_list_limit_never_hides_running_sessions():
    token = _signup()
    key = client.get("/api/me/runner/key", headers=_auth(token)).json()["key"]
    session_ids = []
    for index in range(4):
        response = client.post(
            "/api/runner/start",
            json={"symbol": f"RUN{index}USDT"},
            headers={"X-Runner-Key": key},
        )
        assert response.status_code == 200
        session_ids.append(response.json()["session_id"])

    limited = runner_mod.list_sessions(
        client.get("/api/auth/me", headers=_auth(token)).json()["user"]["id"],
        limit=1,
    )
    assert {item["session_id"] for item in limited["active"]}.issuperset(session_ids)


def test_request_stop_close_and_stop_action():
    token = _signup()
    key = client.get("/api/me/runner/key", headers=_auth(token)).json()["key"]
    sid = client.post("/api/runner/start", json={"symbol": "BTCUSDT"},
                      headers={"X-Runner-Key": key}).json()["session_id"]
    client.post(f"/api/me/runner/sessions/{sid}/request-stop",
                json={"mode": "close_and_stop"}, headers=_auth(token))
    hb = client.post("/api/runner/heartbeat", json={"session_id": sid},
                     headers={"X-Runner-Key": key}).json()
    assert hb["action"] == "close_and_stop"


def test_invalid_stop_mode_rejected():
    token = _signup()
    key = client.get("/api/me/runner/key", headers=_auth(token)).json()["key"]
    sid = client.post("/api/runner/start", json={"symbol": "BTCUSDT"},
                      headers={"X-Runner-Key": key}).json()["session_id"]
    r = client.post(f"/api/me/runner/sessions/{sid}/request-stop",
                    json={"mode": "nuke"}, headers=_auth(token))
    assert r.status_code == 400


def test_cannot_stop_another_users_session():
    # A 가 세션을 만들고, B 가 종료를 시도하면 404(소유권 경계).
    token_a = _signup()
    key_a = client.get("/api/me/runner/key", headers=_auth(token_a)).json()["key"]
    sid = client.post("/api/runner/start", json={"symbol": "BTCUSDT"},
                      headers={"X-Runner-Key": key_a}).json()["session_id"]
    token_b = _signup()
    r = client.post(f"/api/me/runner/sessions/{sid}/request-stop",
                    json={"mode": "stop_only"}, headers=_auth(token_b))
    assert r.status_code == 404


def test_position_news_uses_owned_session_symbol_and_registered_direction(monkeypatch):
    token = _signup()
    key = client.get("/api/me/runner/key", headers=_auth(token)).json()["key"]
    sid = client.post(
        "/api/runner/start",
        json={"symbol": "ETHUSDT", "position_side": "short"},
        headers={"X-Runner-Key": key},
    ).json()["session_id"]
    captured = {}

    def fake_position_news(session, *, requester_id=None):
        captured.update(session)
        captured["requester_id"] = requester_id
        return {"feature_key": "position_news", "context": session, "items": []}

    monkeypatch.setattr(position_news_service, "get_position_news", fake_position_news)
    response = client.get(
        f"/api/me/agents/sessions/{sid}/position-news",
        headers=_auth(token),
    )

    assert response.status_code == 200, response.text
    assert response.headers["cache-control"] == "private, no-store"
    assert captured["symbol"] == "ETHUSDT"
    assert captured["position_side"] == "short"
    assert captured["requester_id"] is not None


def test_position_news_hides_another_users_session(monkeypatch):
    owner = _signup()
    owner_key = client.get("/api/me/runner/key", headers=_auth(owner)).json()["key"]
    sid = client.post(
        "/api/runner/start",
        json={"symbol": "BTCUSDT", "position_side": "long"},
        headers={"X-Runner-Key": owner_key},
    ).json()["session_id"]
    stranger = _signup()
    called = []
    monkeypatch.setattr(
        position_news_service,
        "get_position_news",
        lambda session, **_kwargs: called.append(session),
    )

    response = client.get(
        f"/api/me/agents/sessions/{sid}/position-news",
        headers=_auth(stranger),
    )

    assert response.status_code == 404
    assert called == []


def test_heartbeat_on_missing_session_tells_runner_to_stop():
    token = _signup()
    key = client.get("/api/me/runner/key", headers=_auth(token)).json()["key"]
    hb = client.post("/api/runner/heartbeat", json={"session_id": 999999},
                     headers={"X-Runner-Key": key}).json()
    assert hb["action"] == "stop_only"


# --- 매크로 파일 다운로드 ----------------------------------------------
def test_macro_file_download():
    macro = {
        "symbol": "BTCUSDT", "rule_type": "A", "position_side": "long",
        "params": {"take_profit_pct": 3.0, "initial_capital": 1000000},
        "risk": {"invest_ratio": 0.5, "stop_loss_pct": 2.0},
        "period": {"preset": "3m"},
    }
    r = client.post("/api/realtrade/macro-file", json={"macro": macro})
    assert r.status_code == 200, r.text
    assert "attachment" in r.headers.get("content-disposition", "")
    body = r.json()
    assert body["symbol"] == "BTCUSDT" and "human_summary" in body
