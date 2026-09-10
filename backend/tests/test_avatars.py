"""Profile uploads and current account photos across existing community content."""
from datetime import datetime, timezone
from io import BytesIO
import secrets

from fastapi.testclient import TestClient
from PIL import Image
import pytest
from sqlalchemy import event
from sqlmodel import select

from app import auth, avatars, board, chat, db as db_mod
from app.db import BoardComment, ChatMessage, User, UserAvatar, get_session
from app.main import app


@pytest.fixture
def api():
    return TestClient(app)


@pytest.fixture
def members():
    with get_session() as db:
        users = [User(
            email=f"avatar-{secrets.token_hex(8)}@example.com",
            username=f"avatar_{secrets.token_hex(4)}", password_hash="unused",
            created_at="2026-09-10T00:00:00Z",
        ) for _ in range(2)]
        db.add_all(users)
        db.commit()
        for user in users:
            db.refresh(user)
        return users


def headers(user):
    return {"Authorization": f"Bearer {auth.make_token(user.id)}"}


def picture(fmt="PNG", color="red", size=(400, 300), **kwargs):
    image = Image.new("RGB", size, color)
    output = BytesIO()
    image.save(output, format=fmt, **kwargs)
    return output.getvalue()


def upload(api, user, data=None, **kwargs):
    return api.post("/api/me/avatar", headers=headers(user),
                    files={"image": ("avatar.png", data if data is not None else picture(), "image/png")},
                    **kwargs)


@pytest.mark.parametrize("fmt", ["PNG", "JPEG", "WEBP"])
def test_supported_photos_are_normalized_and_public_without_private_metadata(api, members, fmt):
    user = members[0]
    exif = Image.Exif()
    exif[0x010E] = "private photo description"
    response = upload(api, user, picture(fmt, exif=exif))
    assert response.status_code == 200
    public = response.json()["user"]
    assert public["id"] == user.id
    assert "image_data" not in public and "password_hash" not in public
    url = public["avatar_url"]
    assert url.startswith(f"/api/avatars/{user.id}?v=")
    served = api.get(url)
    assert served.status_code == 200
    assert served.headers["content-type"] == "image/webp"
    assert served.headers["x-content-type-options"] == "nosniff"
    assert len(served.content) < 100_000
    with Image.open(BytesIO(served.content)) as result:
        assert result.size == (256, 256)
        assert not result.getexif()
        assert "icc_profile" not in result.info
    assert api.get(url, headers={"If-None-Match": served.headers["etag"]}).status_code == 304
    assert api.get("/api/auth/me", headers=headers(user)).json()["user"]["avatar_url"] == url
    assert api.get("/api/me/dashboard", headers=headers(user)).json()["user"]["avatar_url"] == url


def test_upload_delete_require_auth_and_cannot_target_another_account(api, members):
    first, second = members
    for request_headers in ({}, {"Authorization": "Bearer invalid"}):
        assert api.post("/api/me/avatar", headers=request_headers,
                        files={"image": ("a.png", picture(), "image/png")}).status_code == 401
        assert api.delete("/api/me/avatar", headers=request_headers).status_code == 401
    second_url = upload(api, second).json()["user"]["avatar_url"]
    first_result = api.post(
        "/api/me/avatar", headers=headers(first), data={"user_id": str(second.id)},
        files={"image": ("photo.png", picture(color="blue"), "image/png")},
    )
    assert first_result.status_code == 200
    assert first_result.json()["user"]["avatar_url"].startswith(f"/api/avatars/{first.id}?")
    assert avatars.avatar_url(second.id) == second_url
    assert api.delete(f"/api/me/avatar?user_id={second.id}", headers=headers(first)).status_code == 200
    assert avatars.avatar_url(first.id) is None
    assert api.get(second_url).status_code == 200
    assert api.delete("/api/me/avatar", headers=headers(first)).status_code == 200


@pytest.mark.parametrize("data", [
    b"", b"not an image", b"\x89PNG\r\n\x1a\n" + b"broken" * 20,
    b'<svg xmlns="http://www.w3.org/2000/svg"><script>alert(1)</script></svg>',
    picture("GIF"),
])
def test_invalid_content_is_rejected_without_replacing_existing_photo(api, members, data):
    user = members[0]
    original = upload(api, user).json()["user"]["avatar_url"]
    assert upload(api, user, data).status_code == 400
    assert avatars.avatar_url(user.id) == original


def test_upload_is_bounded_by_byte_and_decoded_pixel_limits(api, members):
    user = members[0]
    assert upload(api, user, b"x" * (avatars.MAX_IMAGE_BYTES + 1)).status_code == 413
    assert upload(api, user, picture(size=(4001, 4000))).status_code == 400
    assert avatars.avatar_url(user.id) is None


def test_changes_and_deletion_update_historical_chat_and_posts_by_account_id(api, members):
    user, other = members
    posted = api.post("/api/chat", headers=headers(user), json={"text": "before upload"}).json()["message"]
    post = api.post("/api/board/posts", headers=headers(user), data={"title": "before upload"}).json()
    now_ms = int(datetime.now(timezone.utc).timestamp() * 1000)
    with get_session() as db:
        # A legacy author or anonymous commenter can claim the same display name.
        anonymous = ChatMessage(username=user.username, user_id=None, text="same name",
                                created_at=user.created_at, created_ms=now_ms)
        comment = BoardComment(post_id=post["id"], username=user.username, text="same name",
                               created_at=user.created_at, created_ms=now_ms)
        db.add_all([anonymous, comment])
        db.commit()
        db.refresh(anonymous)
        anonymous_id = anonymous.id
    other_url = upload(api, other, picture(color="green")).json()["user"]["avatar_url"]
    reply = api.post("/api/chat", headers=headers(other),
                     json={"text": f"[reply:{posted['id']}] 답장"}).json()["message"]
    anonymous_reply = api.post("/api/chat", headers=headers(other),
                               json={"text": f"[reply:{anonymous_id}] 이전 메시지에 답장"}).json()["message"]
    assert reply["reply_to"]["user_id"] == user.id
    assert reply["reply_to"]["avatar_url"] is None

    def assert_surfaces(expected):
        messages = {m["id"]: m for m in api.get("/api/chat").json()["items"]}
        assert messages[posted["id"]]["avatar_url"] == expected
        assert messages[anonymous_id]["avatar_url"] is None
        assert messages[reply["id"]]["reply_to"]["avatar_url"] == expected
        assert messages[anonymous_reply["id"]]["reply_to"]["user_id"] is None
        assert messages[anonymous_reply["id"]]["reply_to"]["avatar_url"] is None
        detail = api.get(f'/api/board/posts/{post["id"]}').json()
        assert detail["author_avatar_url"] == expected
        assert detail["comments"][0]["author_avatar_url"] is None
        listed = {p["id"]: p for p in api.get("/api/board/posts?size=30").json()["items"]}
        assert listed[post["id"]]["author_avatar_url"] == expected
        dashboard = api.get("/api/me/dashboard", headers=headers(user)).json()
        assert dashboard["user"]["avatar_url"] == expected
        assert dashboard["my_posts"][0]["author_avatar_url"] == expected
        assert avatars.avatar_url(other.id) == other_url

    first_url = upload(api, user).json()["user"]["avatar_url"]
    assert_surfaces(first_url)
    second_url = upload(api, user, picture(color="blue")).json()["user"]["avatar_url"]
    assert first_url != second_url
    assert api.get(first_url).status_code == 404
    assert_surfaces(second_url)
    assert api.delete("/api/me/avatar", headers=headers(user)).json()["user"]["avatar_url"] is None
    assert api.get(second_url).status_code == 404
    assert_surfaces(None)
    with get_session() as db:
        assert db.exec(select(UserAvatar).where(UserAvatar.user_id == user.id)).first() is None


def test_list_queries_join_versions_without_loading_image_bytes_or_n_plus_one(api, members):
    user = members[0]
    upload(api, user)
    for i in range(3):
        board.create_post(user, f"query {i}", "", None, "")
        chat.add_message(user, f"query {i}")
    originals = chat.list_messages()["items"][-3:]
    for original in originals:
        chat.add_message(members[1], f"[reply:{original['id']}] 답장")
    statements = []

    def record(_conn, _cursor, statement, _params, _context, _executemany):
        if statement.lstrip().upper().startswith("SELECT"):
            statements.append(statement.lower())

    event.listen(db_mod._engine, "before_cursor_execute", record)
    try:
        board.list_posts()
        assert len(statements) == 1
        assert "useravatar.version" in statements[0]
        assert "useravatar.image_data" not in statements[0]
        statements.clear()
        chat.list_messages()
        assert len(statements) == 3  # All quoted authors share one metadata query.
        assert "useravatar.version" in statements[1]
        assert "useravatar.version" in statements[2]
        assert all("useravatar.image_data" not in statement for statement in statements)
    finally:
        event.remove(db_mod._engine, "before_cursor_execute", record)
