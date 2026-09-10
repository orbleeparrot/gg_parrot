"""Chat regressions through real routes against the isolated SQLite test DB."""
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timedelta, timezone
import secrets

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import delete, inspect
from sqlmodel import create_engine, select

from app import auth, chat, db as db_mod
from app.db import ChatMessage, ChatReadState, User, get_session
from app.main import app


@pytest.fixture(autouse=True)
def empty_chat():
    with get_session() as db:
        db.exec(delete(ChatReadState))
        db.exec(delete(ChatMessage))
        db.commit()


@pytest.fixture
def members():
    with get_session() as db:
        accounts = [User(email=f"chat-{secrets.token_hex(8)}@example.com",
                         username=f"chat_{secrets.token_hex(4)}", password_hash="unused",
                         created_at="2026-01-01T00:00:00Z") for _ in range(2)]
        for account in accounts:
            db.add(account)
        db.commit()
        for account in accounts:
            db.refresh(account)
        return accounts


def headers(account):
    return {"Authorization": f"Bearer {auth.make_token(account.id)}"}


def client(ip="192.0.2.1"):
    return TestClient(app, client=(ip, 12345))


def seed_messages(count, *, account=None, created_ms=None):
    now_ms = int(datetime.now(timezone.utc).timestamp() * 1000)
    with get_session() as db:
        rows = [ChatMessage(user_id=account.id if account else None,
                            username=account.username if account else "legacy",
                            text=f"message {i}", created_at="2026-01-01T00:00:00Z",
                            created_ms=created_ms if created_ms is not None else now_ms)
                for i in range(count)]
        db.add_all(rows)
        db.commit()
        for row in rows:
            db.refresh(row)
        return rows


def test_write_requires_valid_auth_and_ignores_claimed_author(members):
    a, b = members
    api = client()
    for auth_headers in ({}, {"Authorization": "Bearer invalid"}):
        assert api.post("/api/chat", headers=auth_headers,
                        json={"username": a.username, "text": "spoof"}).status_code == 401
        assert api.put("/api/chat/read", headers=auth_headers,
                       json={"last_seen_id": 999}).status_code == 401
    posted = api.post("/api/chat", headers=headers(b), json={
        "username": a.username, "user_id": a.id, "text": "  real author  ",
    })
    assert posted.status_code == 200
    message = posted.json()["message"]
    assert (message["user_id"], message["username"], message["text"]) == (
        b.id, b.username, "real author",
    )
    assert api.get("/api/chat").json()["items"] == [message]


def test_account_limits_do_not_share_ip_or_reset_with_new_client(members):
    a, b = members
    api = client()
    for i in range(5):
        assert api.post("/api/chat", headers=headers(a), json={"text": str(i)}).status_code == 200
    assert api.post("/api/chat", headers=headers(b), json={"text": "same IP"}).status_code == 200
    assert client("198.51.100.2").post(
        "/api/chat", headers=headers(a), json={"text": "new IP"},
    ).status_code == 429
    # The next request sees persisted messages even after all connections recycle.
    db_mod._engine.dispose()
    assert client().post("/api/chat", headers=headers(a), json={"text": "reconnected"}).status_code == 429


def test_concurrent_posts_cannot_exceed_account_limit(members, monkeypatch):
    frozen = datetime.now(timezone.utc)
    monkeypatch.setattr(chat, "_now_utc", lambda: frozen)
    account = members[0]
    auth_headers = headers(account)

    def send(i):
        return client(f"192.0.2.{i + 1}").post(
            "/api/chat", headers=auth_headers, json={"text": str(i)},
        ).status_code

    with ThreadPoolExecutor(max_workers=8) as pool:
        statuses = list(pool.map(send, range(8)))
    assert sorted(statuses) == [200] * 5 + [429] * 3
    assert len(client().get("/api/chat").json()["items"]) == 5


def test_rate_window_expires_and_invalid_text_uses_no_allowance(members, monkeypatch):
    api, account = client(), members[0]
    now = datetime.now(timezone.utc)
    monkeypatch.setattr(chat, "_now_utc", lambda: now)
    for _ in range(6):
        assert api.post("/api/chat", headers=headers(account), json={"text": "  "}).status_code == 400
    for _ in range(5):
        assert api.post("/api/chat", headers=headers(account), json={"text": "x"}).status_code == 200
    assert api.post("/api/chat", headers=headers(account), json={"text": "x"}).status_code == 429
    monkeypatch.setattr(chat, "_now_utc", lambda: now + timedelta(seconds=10))
    assert api.post("/api/chat", headers=headers(account), json={"text": "x"}).status_code == 200


def test_empty_first_visit_initializes_zero_and_next_message_is_unread(members):
    a, b = members
    api = client()
    assert api.get("/api/chat").json()["seen_id"] is None
    first = api.get("/api/chat", headers=headers(a)).json()
    assert (first["seen_id"], first["latest_id"], first["unseen_count"]) == (0, 0, 0)
    message = api.post("/api/chat", headers=headers(b), json={"text": "first arrival"}).json()["message"]
    later = api.get("/api/chat", headers=headers(a)).json()
    assert later["seen_id"] == 0
    assert later["unseen_count"] == 1
    assert later["latest_id"] == message["id"]


def test_first_member_visit_baselines_history_once_and_read_state_is_account_scoped(members):
    a, b = members
    api = client()
    old = seed_messages(1)[0]
    for account in members:
        initial = api.get("/api/chat", headers=headers(account)).json()
        assert (initial["seen_id"], initial["unseen_count"]) == (old.id, 0)
    new = seed_messages(1)[0]
    assert api.put("/api/chat/read", headers=headers(a), json={"last_seen_id": new.id}).json() == {
        "seen_id": new.id,
    }
    assert api.get("/api/chat", headers=headers(a)).json()["unseen_count"] == 0
    assert api.get("/api/chat", headers=headers(b)).json()["unseen_count"] == 1
    assert client("198.51.100.9").get("/api/chat", headers=headers(a)).json()["seen_id"] == new.id


def test_read_cursor_is_monotonic_clamped_and_counts_other_authors_only(members):
    a, b = members
    api = client()
    api.get("/api/chat", headers=headers(a))
    own = api.post("/api/chat", headers=headers(a), json={"text": "own"}).json()["message"]
    other = api.post("/api/chat", headers=headers(b), json={"text": "other"}).json()["message"]
    legacy = seed_messages(1)[0]
    assert api.get("/api/chat", headers=headers(a)).json()["unseen_count"] == 2
    supplied = api.get("/api/chat", headers=headers(a), params={"seen_id": other["id"]}).json()
    assert (supplied["seen_id"], supplied["unseen_count"]) == (other["id"], 1)
    assert supplied["server_seen_id"] == 0
    # A GET can reconcile local progress for counts, but only PUT persists reads.
    assert api.get("/api/chat", headers=headers(a)).json()["seen_id"] == 0
    assert api.put("/api/chat/read", headers=headers(a), json={"last_seen_id": 10**12}).json()["seen_id"] == legacy.id
    for stale in (own["id"], 0, -1):
        assert api.put("/api/chat/read", headers=headers(a), json={"last_seen_id": stale}).json()["seen_id"] == legacy.id
    assert api.get("/api/chat", headers=headers(a), params={"seen_id": 0}).json()["unseen_count"] == 0


def test_concurrent_read_updates_never_move_backwards(members):
    account = members[0]
    rows = seed_messages(12)
    auth_headers = headers(account)

    def read(row):
        response = client().put("/api/chat/read", headers=auth_headers, json={"last_seen_id": row.id})
        assert response.status_code == 200

    with ThreadPoolExecutor(max_workers=8) as pool:
        list(pool.map(read, reversed(rows)))
    assert client().get("/api/chat", headers=auth_headers).json()["seen_id"] == rows[-1].id


def test_pagination_preserves_global_metadata_and_full_unread_count(members):
    api = client()
    api.get("/api/chat", headers=headers(members[0]))
    rows = seed_messages(chat.MAX_LIST + 5)
    first = api.get("/api/chat", headers=headers(members[0])).json()
    assert len(first["items"]) == 200
    assert first["has_more"] is True
    assert first["unseen_count"] == 205
    assert first["oldest_id"] == rows[5].id
    assert first["latest_id"] == rows[-1].id
    second = api.get("/api/chat", headers=headers(members[0]), params={"before_id": first["oldest_id"]}).json()
    assert [item["id"] for item in second["items"]] == [row.id for row in rows[:5]]
    assert second["has_more"] is False
    assert second["latest_id"] == first["latest_id"]
    assert second["unseen_count"] == 205
    assert second["seen_id"] == first["seen_id"]
    assert second["day_start_ms"] == first["day_start_ms"]


def test_day_rollover_hides_yesterday_without_resetting_cursor(members, monkeypatch):
    api, account = client(), members[0]
    start = chat.today_start_ms()
    yesterday = seed_messages(1, created_ms=start - 1)[0]
    first = api.get("/api/chat", headers=headers(account)).json()
    assert (first["latest_id"], first["seen_id"], first["items"]) == (0, yesterday.id, [])
    today = seed_messages(1, created_ms=start)[0]
    current = api.get("/api/chat", headers=headers(account)).json()
    assert (current["latest_id"], current["unseen_count"]) == (today.id, 1)
    monkeypatch.setattr(chat, "today_start_ms", lambda: start + 86_400_000)
    tomorrow = api.get("/api/chat", headers=headers(account)).json()
    assert (tomorrow["latest_id"], tomorrow["unseen_count"], tomorrow["items"]) == (0, 0, [])
    assert tomorrow["seen_id"] == yesterday.id


def test_list_and_unread_are_bounded_by_the_same_latest_snapshot(monkeypatch):
    first = seed_messages(1)[0]
    original_latest = chat._latest_id
    inserted = []

    def latest_then_concurrent_post(db, *, start_ms=None):
        latest = original_latest(db, start_ms=start_ms)
        if start_ms is not None and not inserted:
            inserted.extend(seed_messages(1))
        return latest

    monkeypatch.setattr(chat, "_latest_id", latest_then_concurrent_post)
    result = client().get("/api/chat", params={"seen_id": 0}).json()
    assert result["latest_id"] == first.id
    assert result["unseen_count"] == 1
    assert [message["id"] for message in result["items"]] == [first.id]
    assert inserted[0].id > result["latest_id"]


def test_anonymous_cursor_does_not_write_member_state_and_parameters_are_validated():
    rows = seed_messages(2)
    api = client()
    result = api.get("/api/chat", params={"seen_id": rows[0].id}).json()
    assert result["unseen_count"] == 1
    assert result["items"][0]["user_id"] is None
    with get_session() as db:
        assert db.exec(select(ChatReadState)).all() == []
    assert api.get("/api/chat", params={"seen_id": -1}).status_code == 422
    assert api.get("/api/chat", params={"before_id": 0}).status_code == 422
    assert api.get("/api/chat", params={"before_id": 10**100}).status_code == 422


def test_existing_sqlite_messages_gain_nullable_author_without_data_loss(tmp_path, monkeypatch):
    engine = create_engine(f"sqlite:///{tmp_path / 'legacy.db'}")
    with engine.begin() as conn:
        conn.exec_driver_sql("CREATE TABLE chatmessage (id INTEGER PRIMARY KEY, username TEXT NOT NULL, text TEXT NOT NULL, created_at TEXT NOT NULL, created_ms BIGINT NOT NULL)")
        conn.exec_driver_sql("INSERT INTO chatmessage VALUES (1, 'existing member name', 'preserved', '2026-01-01T00:00:00Z', 0)")
    monkeypatch.setattr(db_mod, "_engine", engine)
    try:
        db_mod.init_db()
        db_mod.init_db()
        with get_session() as db:
            legacy = db.get(ChatMessage, 1)
            assert (legacy.user_id, legacy.username, legacy.text) == (None, "existing member name", "preserved")
        assert "chatreadstate" in inspect(engine).get_table_names()
        assert "ix_chatmessage_user_created_ms" in {index["name"] for index in inspect(engine).get_indexes("chatmessage")}
    finally:
        engine.dispose()


def test_postgres_chat_migration_preserves_legacy_authors_and_secures_read_state():
    state = {
        "tables": {"chatmessage": False, "chatreadstate": False},
        "columns": {}, "indexes": set(),
        "grants": {("chatreadstate", "PUBLIC"), ("chatreadstate", "anon")},
    }
    statements = db_mod._pg_migration_statements(state)
    assert statements == [
        "ALTER TABLE chatmessage ADD COLUMN IF NOT EXISTS user_id INTEGER",
        "CREATE INDEX IF NOT EXISTS ix_chatmessage_user_created_ms ON chatmessage (user_id, created_ms)",
        "ALTER TABLE chatmessage ENABLE ROW LEVEL SECURITY",
        "ALTER TABLE chatreadstate ENABLE ROW LEVEL SECURITY",
        "REVOKE ALL PRIVILEGES ON TABLE chatreadstate FROM PUBLIC",
        "REVOKE ALL PRIVILEGES ON TABLE chatreadstate FROM anon",
    ]


def _entry(owner_user_id=None, symbol="BTCUSDT", summary="1일봉 · 20일선 돌파"):
    from app.db import LeaderboardEntry
    now_ms = int(datetime.now(timezone.utc).timestamp() * 1000)
    with get_session() as db:
        row = LeaderboardEntry(user_id="anon", nickname="tester", username="tester",
                               owner_user_id=owner_user_id, symbol=symbol, macro_json="{}",
                               human_summary=summary, created_at="2026-01-01T00:00:00Z", created_ms=now_ms)
        db.add(row)
        db.commit()
        db.refresh(row)
        return row.id


def test_macro_mention_becomes_a_card_and_hides_locked_strategy(members):
    author, other = members
    free_id = _entry()                       # 주인 없는 옛 항목 — 누구나 본다
    owned_id = _entry(owner_user_id=other.id)  # 주인 있는 항목 — 언락 전엔 잠김
    posted = client().post("/api/chat", json={"text": f"이거 봐 [macro:{free_id}] 랑 [macro:{owned_id}]"},
                           headers=headers(author))
    assert posted.status_code == 200, posted.text
    cards = posted.json()["message"]["macros"]
    assert [c["entry_id"] for c in cards] == [free_id, owned_id]
    assert cards[0]["locked"] is False and cards[0]["human_summary"] == "1일봉 · 20일선 돌파"
    assert cards[0]["symbol"] == "BTCUSDT" and cards[0]["username"] == "tester"
    assert cards[1]["locked"] is True and cards[1]["human_summary"] == ""  # 잠긴 전략은 새지 않는다

    listed = client().get("/api/chat", headers=headers(author)).json()["items"][-1]
    assert [c["entry_id"] for c in listed["macros"]] == [free_id, owned_id]
    assert listed["text"].count("[macro:") == 2  # 본문은 토큰 그대로 — 화면이 자리에 카드를 그린다

    # 주인이 보면 자기 매크로는 열려 있다.
    owner_view = client().get("/api/chat", headers=headers(other)).json()["items"][-1]
    assert owner_view["macros"][1]["locked"] is False

    # 없는 매크로 번호는 카드가 붙지 않는다(본문 글자만 남는다).
    empty = client().post("/api/chat", json={"text": "[macro:999999] 없음"}, headers=headers(author))
    assert empty.json()["message"]["macros"] == []

