"""헤더 알림: 퀘스트·매크로 판매/등록·댓글/답글·에이전트·관리자 메시지/공지가 쌓이고 읽힌다."""
from __future__ import annotations

import secrets

import pytest
from fastapi.testclient import TestClient

from sqlalchemy import delete
from sqlmodel import select

from app import notifications
from app.db import MacroUnlock, NotificationMessage, User, get_session
from app.main import app

client = TestClient(app)

_MACRO = {
    "symbol": "BTCUSDT", "rule_type": "A", "candle_interval": "1d",
    "params": {"take_profit_pct": 5, "initial_capital": 1_000_000},
    "risk": {"stop_loss_pct": 3}, "period": {"preset": "3m"},
}


@pytest.fixture(autouse=True)
def _stub_engine(monkeypatch):
    """시세 없이 — 리더보드 등록의 페이퍼 시작과 백테스트를 가짜로 둔다."""
    from app.engine import BacktestResult

    def fake_run_any(macro):
        result = BacktestResult(
            initial_capital=1.0, final_equity=1.0, final_return_pct=0.0, mdd_pct=0.0,
            win_rate_pct=0.0, total_trades=0, trades=[], equity_curve=[],
        )
        return result, [], "test", "3m"

    async def fake_start(macro, symbol, mode):
        return {"session_id": 1, "symbol": macro.symbol, "mode": mode, "status": "running"}

    monkeypatch.setattr("app.main._run_any", fake_run_any)
    monkeypatch.setattr("app.main.paper_mod.start_session", fake_start)


def _signup():
    tok = secrets.token_hex(4)
    body = client.post("/api/auth/signup", json={
        "email": f"nt{tok}@ex.com", "username": f"nt_{tok}", "password": "password123",
    }).json()
    return body["token"], body["user"]


def _make_admin(user_id: int):
    """관리자 권한의 근거는 DB 컬럼 하나뿐이다(auth.is_admin) — 테스트도 같은 문으로 들어간다."""
    with get_session() as db:
        account = db.get(User, user_id)
        account.is_admin = True
        db.add(account)
        db.commit()


def _auth(token):
    return {"Authorization": f"Bearer {token}"}


def _runner_key(token):
    key = client.get("/api/me/runner/key", headers=_auth(token)).json()["key"]
    return {"X-Runner-Key": key}


def _items(token, **params):
    response = client.get("/api/me/notifications", headers=_auth(token), params=params)
    assert response.status_code == 200, response.text
    return response.json()


def _personal(token, **params):
    """개인 알림만 — 다른 테스트가 보낸 전체 공지(30일 창)는 뺀다."""
    page = _items(token, **params)
    return {**page, "items": [it for it in page["items"] if it["scope"] == "personal"]}


def _post(token, title="알림 테스트 글"):
    return client.post("/api/board/posts", data={"title": title, "body": "본문입니다"}, headers=_auth(token)).json()


def test_requires_login():
    assert client.get("/api/me/notifications").status_code == 401
    assert client.get("/api/me/notifications/unread").status_code == 401
    assert client.post("/api/me/notifications/read", json={"all": True}).status_code == 401


def test_comment_notifies_author_and_pays_the_quest_to_the_commenter():
    author_token, author = _signup()
    commenter_token, commenter = _signup()
    post = _post(author_token, "비트 전략 질문")
    r = client.post(f"/api/board/posts/{post['id']}/comments", json={"text": "열 글자가 넘는 댓글이에요"}, headers=_auth(commenter_token))
    assert r.status_code == 200, r.text
    comment_id = r.json()["comment"]["id"]

    # 글쓴이: 댓글 알림 하나, 안 읽음
    listing = _personal(author_token)
    [item] = listing["items"]
    assert item["kind"] == "comment" and item["read"] is False and item["scope"] == "personal"
    assert commenter["username"] in item["title"] and "댓글" in item["title"]
    assert item["link"] == f"/board/{post['id']}"
    assert item["data"]["post_id"] == post["id"] and item["data"]["comment_id"] == comment_id
    assert "비트 전략 질문" in item["body"]

    # 댓글 쓴 사람: 퀘스트 완료 알림(+10 P). 자기 댓글 알림은 없다.
    mine = _personal(commenter_token)
    kinds = [it["kind"] for it in mine["items"]]
    assert kinds == ["quest"]
    assert mine["items"][0]["data"]["points"] == 10
    assert "퀘스트 완료" in mine["items"][0]["title"]

    # 글쓴이가 그 댓글에 답글 → 댓글 주인에게 답글 알림. 글쓴이 자신에겐 댓글 알림이 없다.
    r = client.post(f"/api/board/posts/{post['id']}/comments", json={"text": "답글이에요 고마워요"}, headers=_auth(author_token))
    assert r.status_code == 200
    r = client.post(f"/api/board/posts/{post['id']}/comments", json={"text": "답글 달아요", "parent_id": comment_id}, headers=_auth(author_token))
    assert r.status_code == 200, r.text
    mine = _personal(commenter_token)
    assert [it["kind"] for it in mine["items"]] == ["reply", "quest"]
    assert mine["items"][0]["title"] == f"{author['username']} 님이 내 댓글에 답글을 남겼어요"
    # 글쓴이에게는 처음 댓글 알림 + 자기 긴 댓글로 받은 퀘스트 알림뿐(자기 답글 알림은 없다)
    assert [it["kind"] for it in _personal(author_token)["items"]] == ["quest", "comment"]


def test_mark_read_some_then_all():
    author_token, _ = _signup()
    other_token, _ = _signup()
    post = _post(author_token)
    for text in ("첫 번째 댓글입니다요", "두 번째 댓글입니다요", "세 번째 댓글입니다요"):
        assert client.post(f"/api/board/posts/{post['id']}/comments", json={"text": text}, headers=_auth(other_token)).status_code == 200
    listing = _personal(author_token)
    notices = sum(it["scope"] == "notice" and not it["read"] for it in _items(author_token)["items"])
    assert listing["unread"] == 3 + notices and len(listing["items"]) == 3
    light = client.get("/api/me/notifications/unread", headers=_auth(author_token)).json()
    assert light["unread"] == 3 + notices and light["latest_id"] == listing["items"][0]["id"]

    first = listing["items"][0]["id"]
    r = client.post("/api/me/notifications/read", json={"ids": [first, 999_999]}, headers=_auth(author_token))
    assert r.json() == {"unread": 2 + notices}
    listing = _personal(author_token)
    assert [it["read"] for it in listing["items"]] == [True, False, False]

    assert client.post("/api/me/notifications/read", json={"all": True}, headers=_auth(author_token)).json() == {"unread": 0}
    assert all(it["read"] for it in _items(author_token)["items"])
    # 다른 계정의 것은 못 읽는다
    assert any(not it["read"] for it in _personal(other_token)["items"])


def test_paging_by_before_ms():
    token, user = _signup()
    with get_session() as db:
        for index in range(4):
            row = notifications.notify(db, user["id"], "admin", f"메시지 {index}")
            row.created_ms = 1_000 + index  # 순서를 확실히
            db.add(row)
        db.commit()
    page = _personal(token, limit=3, before=100_000)  # 다른 테스트의 공지(현재 시각)보다 앞선 것만
    assert [it["title"] for it in page["items"]] == ["메시지 3", "메시지 2", "메시지 1"]
    assert page["next_before"] == 1_001
    rest = _personal(token, limit=3, before=page["next_before"])
    assert [it["title"] for it in rest["items"]] == ["메시지 0"]
    assert rest["next_before"] is None


def test_after_id_returns_only_newer_items_oldest_first():
    token, user = _signup()
    with get_session() as db:
        first = notifications.notify(db, user["id"], "admin", "첫 번째")
        db.commit()
        first_id = first.id
    assert _items(token, after=first_id)["items"] == []
    with get_session() as db:
        notifications.notify(db, user["id"], "admin", "두 번째")
        notifications.notify(db, user["id"], "admin", "세 번째")
        assert notifications.latest_id_for(db, user["id"]) >= first_id
        db.commit()
    page = _items(token, after=first_id)
    assert [it["title"] for it in page["items"]] == ["두 번째", "세 번째"]
    assert page["next_before"] is None
    assert page["unread"] >= 3
    with get_session() as db:
        assert notifications.list_after(db, user["id"], 0, limit=2) and len(notifications.list_after(db, user["id"], 0, limit=2)) == 2


def test_macro_registered_and_sold_notifications():
    seller_token, seller = _signup()
    buyer_token, buyer = _signup()
    r = client.post("/api/leaderboard/register", json={"macro": _MACRO, "username": "", "password": ""}, headers=_auth(seller_token))
    assert r.status_code == 200, r.text
    entry_id = r.json()["entry"]["id"]
    listing = _personal(seller_token)
    assert [it["kind"] for it in listing["items"]] == ["macro_registered"]
    assert listing["items"][0]["title"] == "BTCUSDT 매크로를 리더보드에 등록했어요"
    assert listing["items"][0]["link"] == "/leaderboard"
    assert listing["items"][0]["data"]["entry_id"] == entry_id

    r = client.post(f"/api/leaderboard/{entry_id}/unlock", headers=_auth(buyer_token))
    assert r.status_code == 200, r.text
    listing = _personal(seller_token)
    sold = listing["items"][0]
    assert sold["kind"] == "macro_sold" and sold["title"] == "BTCUSDT 매크로가 팔렸어요"
    assert sold["data"]["points"] == 70 and sold["data"]["buyer"] == buyer["username"]
    assert buyer["username"] in sold["body"]
    # 사는 사람에겐 판매 알림이 없다
    assert all(it["kind"] != "macro_sold" for it in _items(buyer_token)["items"])
    # 다시 언락(무료)해도 알림은 한 번뿐
    assert client.post(f"/api/leaderboard/{entry_id}/unlock", headers=_auth(buyer_token)).status_code == 200
    assert sum(it["kind"] == "macro_sold" for it in _items(seller_token)["items"]) == 1
    # 이 테스트가 남긴 구매 행은 지운다 — 탈퇴 테스트가 게시글 id 를 엔트리 id 로 빌려 쓰며 개수를 센다.
    with get_session() as db:
        db.exec(delete(MacroUnlock).where(MacroUnlock.entry_id == entry_id))
        db.commit()


def test_withdrawal_removes_the_members_notifications_only():
    leaver_token, leaver = _signup()
    stayer_token, stayer = _signup()
    with get_session() as db:
        notifications.notify(db, leaver["id"], "admin", "떠나는 회원의 알림")
        notifications.notify(db, stayer["id"], "admin", "남는 회원의 알림")
        notice = notifications.notify(db, None, "notice", "탈퇴 테스트 공지")
        db.commit()
        notice_id = notice.id
    client.post("/api/me/notifications/read", json={"ids": [notice_id]}, headers=_auth(leaver_token))
    r = client.request("DELETE", "/api/me/account", headers=_auth(leaver_token), json={"confirmation": "탈퇴", "password": "password123"})
    assert r.status_code == 200, r.text
    with get_session() as db:
        assert db.exec(select(NotificationMessage).where(NotificationMessage.user_id == leaver["id"])).first() is None
        assert db.exec(select(NotificationMessage).where(NotificationMessage.user_id == stayer["id"])).first() is not None
        assert db.get(NotificationMessage, notice_id) is not None  # 공지 자체는 남는다
        from app.db import NotificationReceipt
        assert db.exec(select(NotificationReceipt).where(NotificationReceipt.user_id == leaver["id"])).first() is None
    assert any(it["title"] == "남는 회원의 알림" for it in _personal(stayer_token)["items"])


def test_runner_session_lifecycle_and_events_become_agent_notifications():
    token, user = _signup()
    key = _runner_key(token)
    r = client.post("/api/runner/start", json={"symbol": "ATOMUSDT", "macro": {**_MACRO, "symbol": "ATOMUSDT"}}, headers=key)
    assert r.status_code == 200, r.text
    session_id = r.json()["session_id"]
    listing = _personal(token)
    assert [it["kind"] for it in listing["items"]] == ["agent"]
    assert listing["items"][0]["title"] == "ATOMUSDT 매크로 실행 시작"
    assert listing["items"][0]["session_id"] == session_id and listing["items"][0]["data"]["event"] == "start"
    assert "테스트넷" in listing["items"][0]["body"]

    r = client.post("/api/runner/heartbeat", json={"session_id": session_id, "events": [
        {"kind": "info", "message": "로그일 뿐"},
        {"kind": "fill", "message": "매수 체결 1.5 ATOM"},
        {"kind": "error", "message": "주문 거부: 잔고 부족"},
    ]}, headers=key)
    assert r.status_code == 200, r.text
    titles = [it["title"] for it in _personal(token)["items"]]
    assert titles[:2] == ["ATOMUSDT 매크로 · 오류", "ATOMUSDT 매크로 · 체결"]
    assert len(titles) == 3  # info 는 알림이 아니다

    # 실행 중 세션의 주인에게 에이전트 소식(기사·대형 체결) — 자산 또는 종목으로 고르고 ref 로 중복을 막는다
    with get_session() as db:
        assert notifications.notify_running_sessions(db, asset="ATOM", title="ATOM 새 기사 2건", body="첫 기사", ref="news:ATOM:7") == 1
        assert notifications.notify_running_sessions(db, asset="ATOM", title="ATOM 새 기사 2건", body="첫 기사", ref="news:ATOM:7") == 0
        assert notifications.notify_running_sessions(db, symbol="ATOMUSDT", market="futures", title="x") == 0  # 현물 세션
        assert notifications.notify_running_sessions(db, symbol="atomusdt", market="spot", title="ATOMUSDT 대형 체결 1건", ref="whale:1") == 1
        assert notifications.notify_running_sessions(db, title="대상 없음") == 0
        db.commit()
    latest = _personal(token)["items"][0]
    assert latest["title"] == "ATOMUSDT 대형 체결 1건" and latest["session_id"] == session_id
    assert latest["data"]["symbol"] == "ATOMUSDT"

    r = client.post("/api/runner/stopped", json={"session_id": session_id, "status": "stopped", "note": "포지션 없이 종료"}, headers=key)
    assert r.status_code == 200, r.text
    stop = _personal(token)["items"][0]
    assert stop["title"] == "ATOMUSDT 매크로 실행 종료" and stop["data"]["event"] == "stop"
    assert "포지션 없이 종료" in stop["body"] and "누적 실현손익" in stop["body"]
    # 종료 보고를 다시 보내도 종료 알림은 한 번
    client.post("/api/runner/stopped", json={"session_id": session_id, "status": "stopped", "note": "포지션 없이 종료"}, headers=key)
    assert sum(it["title"].endswith("실행 종료") for it in _personal(token)["items"]) == 1
    # 멈춘 세션은 더 이상 에이전트 소식을 받지 않는다
    with get_session() as db:
        assert notifications.notify_running_sessions(db, asset="ATOM", title="늦은 기사") == 0


def test_whale_trades_notify_running_sessions_only_after_the_first_observation():
    from datetime import datetime, timezone
    from app.agent_features.whale_activity import repository as whales_repo

    token, user = _signup()
    key = _runner_key(token)
    r = client.post("/api/runner/start", json={"symbol": "INJUSDT", "macro": {**_MACRO, "symbol": "INJUSDT"}}, headers=key)
    assert r.status_code == 200, r.text
    session_id = r.json()["session_id"]

    def trade(agg, notional, side, at_ms):
        return {"id": f"spot:INJUSDT:{agg}", "price": 20.0, "quantity": notional / 20.0, "notional": notional,
                "side": side, "occurred_at": datetime.fromtimestamp(at_ms / 1000, timezone.utc).isoformat()}

    def payload(items):
        return {"status": "ready", "symbol": "INJUSDT", "market": "spot", "items": items, "threshold_quote": 100_000}

    now = 1_800_000_000_000
    tok = whales_repo.claim_collection("INJUSDT", "spot", now_ms=now)
    assert tok and whales_repo.store_result("INJUSDT", "spot", tok, payload([trade(1, 150_000, "sell", now - 5_000)]), now_ms=now)
    agent = [it for it in _personal(token)["items"] if it["kind"] == "agent"]
    assert [it["data"]["event"] for it in agent] == ["start"], "첫 관측은 지난 체결이 한꺼번에 울리지 않게 건너뛴다"

    later = now + 31_000
    tok = whales_repo.claim_collection("INJUSDT", "spot", now_ms=later)
    assert tok and whales_repo.store_result("INJUSDT", "spot", tok, payload([
        trade(1, 150_000, "sell", now - 5_000), trade(2, 260_000, "buy", later - 3_000)]), now_ms=later)
    latest = _personal(token)["items"][0]
    assert latest["title"] == "INJUSDT 대형 체결 1건" and latest["session_id"] == session_id
    assert "260,000" in latest["body"] and "매수" in latest["body"] and latest["data"]["event"] == "whale"

    again = later + 31_000  # 같은 체결을 다시 봐도 새 알림은 없다
    tok = whales_repo.claim_collection("INJUSDT", "spot", now_ms=again)
    assert tok and whales_repo.store_result("INJUSDT", "spot", tok, payload([trade(2, 260_000, "buy", later - 3_000)]), now_ms=again)
    assert sum(it["data"].get("event") == "whale" for it in _personal(token)["items"]) == 1
    client.post("/api/runner/stopped", json={"session_id": session_id, "status": "stopped", "note": "포지션 없이 종료"}, headers=key)


def test_admin_message_and_notice():
    admin_token, admin = _signup()
    member_token, member = _signup()
    other_token, _ = _signup()
    body = {"title": "점검 안내", "body": "오늘 밤 서버 점검이 있어요.", "link": "/guide"}
    assert client.post("/api/admin/notifications", json=body, headers=_auth(member_token)).status_code == 403
    _make_admin(admin["id"])

    r = client.post("/api/admin/notifications", json=body, headers=_auth(admin_token))
    assert r.status_code == 200, r.text
    assert r.json()["recipient"] == "all" and r.json()["message"]["kind"] == "notice"
    for token in (member_token, other_token, admin_token):
        listing = _items(token)
        assert listing["unread"] >= 1
        assert listing["items"][0]["kind"] == "notice" and listing["items"][0]["scope"] == "notice"
        assert listing["items"][0]["title"] == "점검 안내" and listing["items"][0]["read"] is False
        assert listing["items"][0]["data"]["from"] == admin["username"]

    # 한 회원만 읽음 처리해도 다른 회원의 공지는 그대로 안 읽음
    assert client.post("/api/me/notifications/read", json={"all": True}, headers=_auth(member_token)).json() == {"unread": 0}
    assert _items(member_token)["items"][0]["read"] is True
    assert _items(other_token)["unread"] >= 1
    assert _items(other_token)["items"][0]["read"] is False

    # 개인 메시지
    r = client.post("/api/admin/notifications", json={"title": "안녕하세요", "username": member["username"]}, headers=_auth(admin_token))
    assert r.status_code == 200 and r.json()["recipient"] == member["username"] and r.json()["message"]["kind"] == "admin"
    assert [it["kind"] for it in _items(member_token)["items"][:2]] == ["admin", "notice"]
    assert _items(member_token)["unread"] == 1
    assert all(it["kind"] != "admin" for it in _items(other_token)["items"])
    assert client.post("/api/admin/notifications", json={"title": "없는 사람", "username": "no_such_user_x"}, headers=_auth(admin_token)).status_code == 404
    assert client.post("/api/admin/notifications", json={"title": "  "}, headers=_auth(admin_token)).status_code == 400


def test_personal_notifications_are_trimmed_and_kinds_validated(monkeypatch):
    token, user = _signup()
    monkeypatch.setattr(notifications, "KEEP_PER_USER", 3)
    with get_session() as db:
        for index in range(5):
            row = notifications.notify(db, user["id"], "admin", f"메시지 {index}")
            row.created_ms = 10 + index
            db.add(row)
            db.flush()
        with pytest.raises(ValueError):
            notifications.notify(db, user["id"], "bogus", "x")
        db.commit()
    listing = _personal(token)
    assert [it["title"] for it in listing["items"]] == ["메시지 4", "메시지 3", "메시지 2"]


def test_pnl_moves_notify_by_step_or_sudden_jump_and_reset_when_the_position_closes():
    from app import runner as runner_mod

    token, user = _signup()
    key = _runner_key(token)
    r = client.post("/api/runner/start", json={"symbol": "LTCUSDT", "macro": {**_MACRO, "symbol": "LTCUSDT"}}, headers=key)
    assert r.status_code == 200, r.text
    sid = r.json()["session_id"]

    def beat(pct, in_position=True):
        r = client.post("/api/runner/heartbeat", json={
            "session_id": sid, "in_position": in_position, "entry_price": 100.0, "last_price": round(100 * (1 + pct / 100), 2),
            "position_qty": 3.0, "unrealized_pct": pct,
        }, headers=key)
        assert r.status_code == 200, r.text

    def pnl_alerts():
        return [it for it in _personal(token)["items"] if it["kind"] == "agent" and it["data"].get("event") == "pnl"]

    beat(0.4); beat(1.2); beat(1.9)
    assert pnl_alerts() == [], "기준(0)에서 2%p 미만이고 급변도 아니면 조용하다"
    beat(2.3)
    alerts = pnl_alerts()
    assert len(alerts) == 1 and alerts[0]["title"] == "LTCUSDT 평가손익 +2.30%" and alerts[0]["data"]["direction"] == "up"
    assert "마지막 알림보다 +2.30%p 상승" in alerts[0]["body"] and alerts[0]["session_id"] == sid
    beat(2.9); beat(3.6)
    assert len(pnl_alerts()) == 1, "기준이 2.3 으로 옮겨져 2%p 를 더 움직이기 전엔 조용하다"
    beat(2.5)  # 3.6 → 2.5: 한 heartbeat 사이 -1.1%p 급락
    alerts = pnl_alerts()
    assert len(alerts) == 2 and alerts[0]["data"]["sudden"] is True and alerts[0]["data"]["direction"] == "down"
    assert "-1.10%p 급락" in alerts[0]["body"]
    beat(0.3)  # 2.5 → 0.3: -2.2%p 이동(기준 2.5)
    assert len(pnl_alerts()) == 3 and pnl_alerts()[0]["title"] == "LTCUSDT 평가손익 +0.30%"
    beat(0.0, in_position=False)  # 포지션 닫힘 → 기준 0 으로
    beat(0.5); assert len(pnl_alerts()) == 3
    beat(-2.4)  # 새 포지션에서 -2.4 (기준 0 → 2%p 넘음, 급변이기도 하다)
    assert len(pnl_alerts()) == 4 and pnl_alerts()[0]["data"]["direction"] == "down"
    # 종료 보고 때 마지막 포지션이 결과 화면용으로 남는다
    r = client.post("/api/runner/stopped", json={"session_id": sid, "status": "stopped", "note": "청산 완료 후 종료"}, headers=key)
    assert r.status_code == 200
    rows = client.get("/api/me/runner/sessions", headers=_auth(token)).json()
    view = next(it for it in [*rows.get("active", []), *rows.get("recent", [])] if it["session_id"] == sid)
    assert view["in_position"] is False and view["position_qty"] == 0 and view["entry_price"] == 0
    assert view["final_entry_price"] == 100.0 and view["final_position_qty"] == 3.0 and view["final_unrealized_pct"] == -2.4
