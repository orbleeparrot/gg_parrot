"""껄무새 게시판 — 로그인 작성 게이트, 페이징, 이미지 검증, 일회성 댓글."""
from __future__ import annotations

import io
import secrets

from fastapi.testclient import TestClient

from app import board
from app.main import app

client = TestClient(app)

# 최소 PNG / JPEG 바이트(매직 넘버 검증용).
_PNG = bytes.fromhex(
    "89504e470d0a1a0a0000000d49484452000000010000000108060000001f15c489"
    "0000000d4944415478da63f8cfc0f01f0005010102a2b2f7350000000049454e44ae426082"
)
_JPEG = b"\xff\xd8\xff\xe0" + b"\x00" * 20
_GIF = b"GIF89a" + b"\x00" * 20


def _signup():
    tok = secrets.token_hex(4)
    body = client.post("/api/auth/signup", json={
        "email": f"bd{tok}@ex.com", "username": f"bd_{tok}", "password": "password123",
    }).json()
    return body["token"], body["user"]


def _auth(t):
    return {"Authorization": f"Bearer {t}"}


def test_post_requires_login():
    r = client.post("/api/board/posts", data={"title": "제목", "body": "내용"})
    assert r.status_code == 401


def test_create_list_and_detail():
    token, user = _signup()
    r = client.post("/api/board/posts", data={"title": "안녕 게시판", "body": "첫 글이에요"}, headers=_auth(token))
    assert r.status_code == 200
    pid = r.json()["id"]

    lst = client.get("/api/board/posts?page=1&size=10").json()
    assert lst["total"] >= 1
    assert any(it["id"] == pid for it in lst["items"])

    detail = client.get(f"/api/board/posts/{pid}").json()
    assert detail["title"] == "안녕 게시판"
    assert detail["author_name"] == user["username"]
    assert detail["comments"] == []


def test_image_upload_and_serve():
    token, _ = _signup()
    r = client.post(
        "/api/board/posts",
        data={"title": "사진글", "body": ""},
        files={"image": ("p.png", io.BytesIO(_PNG), "image/png")},
        headers=_auth(token),
    )
    assert r.status_code == 200
    pid = r.json()["id"]
    assert r.json()["has_image"] is True

    img = client.get(f"/api/board/posts/{pid}/image")
    assert img.status_code == 200
    assert img.headers["content-type"] == "image/png"
    assert img.content == _PNG


def test_multiple_images_upload_in_order_and_serve():
    token, _ = _signup()
    r = client.post(
        "/api/board/posts",
        data={"title": "사진 여러 장", "body": ""},
        files=[("images", ("a.png", io.BytesIO(_PNG), "image/png")), ("images", ("b.png", io.BytesIO(_PNG), "image/png"))],
        headers=_auth(token),
    )
    assert r.status_code == 200, r.text
    post = r.json()
    assert post["has_image"] is True and len(post["images"]) == 2
    assert post["image_url"] == post["images"][0]["url"]  # 옛 화면 호환 — 첫 장
    for img in post["images"]:
        got = client.get(img["url"])
        assert got.status_code == 200 and got.headers["content-type"] == "image/png" and got.content == _PNG
    # 목록·상세 모두 사진 표식이 붙고, 상세는 순서대로 준다.
    assert client.get(f"/api/board/posts/{post['id']}").json()["images"] == post["images"]
    listed = next(p for p in client.get("/api/board/posts").json()["items"] if p["id"] == post["id"])
    assert listed["has_image"] is True
    # 다른 글 id 로는 사진을 못 본다.
    assert client.get(f"/api/board/posts/{post['id'] + 1000}/images/{post['images'][0]['id']}").status_code == 404
    # 삭제하면 사진도 같이 지워진다.
    assert client.delete(f"/api/board/posts/{post['id']}", headers=_auth(token)).status_code == 200
    assert client.get(post["images"][0]["url"]).status_code == 404


def test_too_many_images_rejected():
    token, _ = _signup()
    files = [("images", (f"{i}.png", io.BytesIO(_PNG), "image/png")) for i in range(11)]
    r = client.post("/api/board/posts", data={"title": "많다", "body": ""}, files=files, headers=_auth(token))
    assert r.status_code == 400 and "10장" in r.json()["detail"]


def test_gif_rejected():
    token, _ = _signup()
    r = client.post(
        "/api/board/posts",
        data={"title": "gif", "body": ""},
        files={"image": ("a.gif", io.BytesIO(_GIF), "image/gif")},
        headers=_auth(token),
    )
    assert r.status_code == 400


def test_only_author_can_delete():
    a_token, _ = _signup()
    b_token, _ = _signup()
    pid = client.post("/api/board/posts", data={"title": "내글", "body": "x"}, headers=_auth(a_token)).json()["id"]
    # 다른 사람은 삭제 불가
    assert client.delete(f"/api/board/posts/{pid}", headers=_auth(b_token)).status_code == 403
    # 작성자는 삭제 가능
    assert client.delete(f"/api/board/posts/{pid}", headers=_auth(a_token)).status_code == 200
    assert client.get(f"/api/board/posts/{pid}").status_code == 404


def test_comment_oneoff_id_password_and_delete():
    token, _ = _signup()
    pid = client.post("/api/board/posts", data={"title": "댓글글", "body": "x"}, headers=_auth(token)).json()["id"]

    # 댓글: 계정 없이 이름+비번(로그인 헤더 없음)
    c = client.post(f"/api/board/posts/{pid}/comments", json={
        "username": "행인", "password": "pw123", "text": "좋아요",
    })
    assert c.status_code == 200
    cid = c.json()["comment"]["id"]
    # 응답에 비밀번호/해시가 새어나오지 않음
    assert "password" not in c.json()["comment"]
    assert "password_hash" not in c.json()["comment"]

    # 틀린 비번은 삭제 실패, 맞으면 성공
    assert client.request("DELETE", f"/api/board/comments/{cid}", json={"password": "nope"}).status_code == 403
    assert client.request("DELETE", f"/api/board/comments/{cid}", json={"password": "pw123"}).status_code == 200


def test_empty_fields_rejected():
    token, _ = _signup()
    # 제목 없음
    assert client.post("/api/board/posts", data={"title": "  ", "body": "x"}, headers=_auth(token)).status_code == 400
    pid = client.post("/api/board/posts", data={"title": "t", "body": "x"}, headers=_auth(token)).json()["id"]
    # 댓글 이름/내용 누락
    assert client.post(f"/api/board/posts/{pid}/comments", json={"username": "", "password": "p", "text": "hi"}).status_code == 400
    assert client.post(f"/api/board/posts/{pid}/comments", json={"username": "n", "password": "p", "text": " "}).status_code == 400


def test_pagination_math():
    r = board.list_posts(page=1, size=5)
    assert r["page"] == 1 and r["size"] == 5
    assert r["pages"] == max(1, (r["total"] + 4) // 5)
