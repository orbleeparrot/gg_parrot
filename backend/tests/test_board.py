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


def test_author_edits_title_body_and_images_in_place():
    token, _ = _signup()
    created = client.post(
        "/api/board/posts",
        data={"title": "처음", "body": "첫 줄\n[사진1]\n둘째 줄\n[사진2]"},
        files=[("images", ("a.png", io.BytesIO(_PNG), "image/png")), ("images", ("b.png", io.BytesIO(_PNG), "image/png"))],
        headers=_auth(token),
    ).json()
    first, second = created["images"]
    # 첫 장은 지우고, 둘째 장은 남기고, 새 장을 하나 붙인다. 본문의 자리 번호는 화면이 다시 매겨 보낸다.
    r = client.put(
        f"/api/board/posts/{created['id']}",
        data={"title": "고침", "body": "첫 줄\n둘째 줄\n[사진1]\n[사진2]", "keep_image_ids": str(second["id"])},
        files=[("images", ("c.png", io.BytesIO(_PNG), "image/png"))],
        headers=_auth(token),
    )
    assert r.status_code == 200, r.text
    edited = r.json()
    assert edited["title"] == "고침" and edited["body"].startswith("첫 줄\n둘째 줄")
    assert [img["id"] for img in edited["images"]][0] == second["id"] and len(edited["images"]) == 2
    assert client.get(first["url"]).status_code == 404
    assert client.get(edited["images"][1]["url"]).status_code == 200
    listed = next(p for p in client.get("/api/board/posts").json()["items"] if p["id"] == created["id"])
    assert "[사진" not in listed["snippet"]  # 목록 발췌에는 사진 자리가 안 보인다
    # 남의 글은 못 고친다.
    other, _ = _signup()
    assert client.put(f"/api/board/posts/{created['id']}", data={"title": "x", "body": ""}, headers=_auth(other)).status_code == 403
    assert client.put("/api/board/posts/999999", data={"title": "x", "body": ""}, headers=_auth(token)).status_code == 404


def test_html_body_is_sanitized_and_new_images_get_their_urls():
    token, _ = _signup()
    raw = ('<h2 style="text-align: center; position: fixed">제목</h2>'
           '<p><strong>굵게</strong> <span style="color: rgb(200, 30, 51); font-size: 20px; behavior: x">색</span></p>'
           '<img data-key="new:0" alt="" width="320" data-align="center">'
           '<p>외부 <img src="https://evil.example/pixel.png"> 사진은 빠진다</p>'
           '<script>alert(1)</script><p onclick="x()">끝 <a href="javascript:alert(1)">링크</a> <a href="https://example.com">좋은 링크</a></p>')
    r = client.post("/api/board/posts", data={"title": "서식", "body": raw, "body_format": "html"},
                    files=[("images", ("a.png", io.BytesIO(_PNG), "image/png"))], headers=_auth(token))
    assert r.status_code == 200, r.text
    post = r.json()
    assert post["body_format"] == "html"
    html = post["body_html"]
    assert 'style="text-align: center"' in html and "position" not in html
    assert "<strong>굵게</strong>" in html and "color: rgb(200, 30, 51); font-size: 20px" in html and "behavior" not in html
    assert f'src="/api/board/posts/{post["id"]}/images/{post["images"][0]["id"]}"' in html and 'width="320"' in html and 'data-align="center"' in html
    assert "evil.example" not in html and "<script" not in html and "onclick" not in html and "javascript:" not in html
    assert 'href="https://example.com"' in html and 'rel="noopener noreferrer nofollow"' in html
    listed = next(p for p in client.get("/api/board/posts").json()["items"] if p["id"] == post["id"])
    assert listed["snippet"].startswith("제목 굵게") and "<" not in listed["snippet"]


def test_html_edit_keeps_only_images_still_in_the_body():
    token, _ = _signup()
    created = client.post("/api/board/posts", data={"title": "둘", "body": '<p>a</p><img data-key="new:0"><img data-key="new:1">', "body_format": "html"},
                          files=[("images", ("a.png", io.BytesIO(_PNG), "image/png")), ("images", ("b.png", io.BytesIO(_PNG), "image/png"))],
                          headers=_auth(token)).json()
    first, second = created["images"]
    body = f'<p>고침</p><img src="{second["url"]}"><img data-key="new:0">'
    r = client.put(f"/api/board/posts/{created['id']}", data={"title": "둘 고침", "body": body, "body_format": "html"},
                   files=[("images", ("c.png", io.BytesIO(_PNG), "image/png"))], headers=_auth(token))
    assert r.status_code == 200, r.text
    edited = r.json()
    assert [img["id"] for img in edited["images"]][0] == second["id"] and len(edited["images"]) == 2
    assert client.get(first["url"]).status_code == 404  # 본문에서 빠진 사진은 지워진다
    assert edited["body_html"].count("<img") == 2 and edited["images"][1]["url"] in edited["body_html"]


def test_legacy_text_post_is_served_as_html_with_images_in_place():
    token, _ = _signup()
    created = client.post("/api/board/posts", data={"title": "옛 글", "body": "첫 줄\n둘째\n\n[사진1]\n\n끝 <b>"},
                          files=[("images", ("a.png", io.BytesIO(_PNG), "image/png"))], headers=_auth(token)).json()
    assert created["body_format"] == "text"
    assert created["body_html"] == f'<p>첫 줄<br>둘째</p><img src="{created["images"][0]["url"]}" alt=""><p>끝 &lt;b&gt;</p>'


def test_votes_views_sort_search_and_filter():
    author, _ = _signup()
    other, _ = _signup()
    a = client.post("/api/board/posts", data={"title": "비트코인 전략 공유", "body": "본문에 이더리움 이야기"}, headers=_auth(author)).json()
    b = client.post("/api/board/posts", data={"title": "잡담", "body": "사진 있음"},
                    files=[("images", ("p.png", io.BytesIO(_PNG), "image/png"))], headers=_auth(author)).json()
    # 추천: 한 표, 같은 표는 취소, 바꾸면 이동. 로그인 없이는 401.
    assert client.post(f"/api/board/posts/{a['id']}/vote", json={"value": 1}).status_code == 401
    r = client.post(f"/api/board/posts/{a['id']}/vote", json={"value": 1}, headers=_auth(other)).json()
    assert (r["likes"], r["dislikes"], r["my_vote"]) == (1, 0, 1)
    r = client.post(f"/api/board/posts/{a['id']}/vote", json={"value": -1}, headers=_auth(other)).json()
    assert (r["likes"], r["dislikes"], r["my_vote"]) == (0, 1, -1)
    r = client.post(f"/api/board/posts/{a['id']}/vote", json={"value": -1}, headers=_auth(other)).json()
    assert (r["likes"], r["dislikes"], r["my_vote"]) == (0, 0, 0)
    client.post(f"/api/board/posts/{a['id']}/vote", json={"value": 1}, headers=_auth(other))
    client.post(f"/api/board/posts/{a['id']}/vote", json={"value": 1}, headers=_auth(author))
    detail = client.get(f"/api/board/posts/{a['id']}", headers=_auth(other)).json()
    assert detail["likes"] == 2 and detail["my_vote"] == 1
    # 조회수: 같은 방문자는 30분에 한 번. 다른 UA 는 따로 센다.
    v1 = client.get(f"/api/board/posts/{b['id']}", headers={"user-agent": "ua-1"}).json()["views"]
    v2 = client.get(f"/api/board/posts/{b['id']}", headers={"user-agent": "ua-1"}).json()["views"]
    v3 = client.get(f"/api/board/posts/{b['id']}", headers={"user-agent": "ua-2"}).json()["views"]
    assert (v1, v2, v3) == (1, 1, 2)
    # 정렬·검색·필터
    ids = lambda **kw: [p["id"] for p in client.get("/api/board/posts", params=kw, headers=_auth(author)).json()["items"]]
    assert ids(sort="likes")[0] == a["id"]
    assert ids(sort="views")[0] == b["id"]
    assert ids(q="이더리움") == [a["id"]]  # 제목+내용
    assert ids(q="이더리움", field="title") == []  # 제목만
    assert ids(q="잡담") == [b["id"]]
    author_name = client.get(f"/api/board/posts/{a['id']}").json()["author_name"]
    assert set(ids(q=author_name, field="author")) == {a["id"], b["id"]}  # 글쓴이
    listed = client.get("/api/board/posts", params={"q": "잡담"}).json()
    assert listed["total"] == 1 and listed["items"][0]["views"] == 2 and listed["items"][0]["likes"] == 0
    # 댓글에 created_ms 가 실린다
    client.post(f"/api/board/posts/{a['id']}/comments", json={"text": "댓글"}, headers=_auth(other))
    assert isinstance(client.get(f"/api/board/posts/{a['id']}").json()["comments"][0]["created_ms"], int)


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


def test_comments_need_login_and_use_the_account_name():
    author, _ = _signup()
    pid = client.post("/api/board/posts", data={"title": "댓글글", "body": "x"}, headers=_auth(author)).json()["id"]
    assert client.post(f"/api/board/posts/{pid}/comments", json={"text": "익명"}).status_code == 401
    commenter, account = _signup()
    c = client.post(f"/api/board/posts/{pid}/comments", json={"text": "좋아요"}, headers=_auth(commenter))
    assert c.status_code == 200, c.text
    comment = c.json()["comment"]
    assert comment["username"] == account["username"] and comment["author_user_id"] == account["id"]
    assert "password" not in comment and "password_hash" not in comment
    assert client.post(f"/api/board/posts/{pid}/comments", json={"text": "  "}, headers=_auth(commenter)).status_code == 400
    # 남(글쓴이 포함)은 못 지우고, 쓴 사람만 지운다.
    assert client.delete(f"/api/board/comments/{comment['id']}").status_code == 401
    assert client.delete(f"/api/board/comments/{comment['id']}", headers=_auth(author)).status_code == 403
    assert client.delete(f"/api/board/comments/{comment['id']}", headers=_auth(commenter)).status_code == 200
    assert client.get(f"/api/board/posts/{pid}").json()["comments"] == []


def test_replies_edits_and_reports():
    author, _ = _signup()
    pid = client.post("/api/board/posts", data={"title": "답글글", "body": "x"}, headers=_auth(author)).json()["id"]
    other, _ = _signup()
    root = client.post(f"/api/board/posts/{pid}/comments", json={"text": "원댓글"}, headers=_auth(other)).json()["comment"]
    reply = client.post(f"/api/board/posts/{pid}/comments", json={"text": "답글", "parent_id": root["id"]}, headers=_auth(author)).json()["comment"]
    assert reply["parent_id"] == root["id"]
    # 답글의 답글은 같은 원댓글 아래로
    nested = client.post(f"/api/board/posts/{pid}/comments", json={"text": "답글의 답글", "parent_id": reply["id"]}, headers=_auth(other)).json()["comment"]
    assert nested["parent_id"] == root["id"]
    tree = client.get(f"/api/board/posts/{pid}").json()["comments"]
    assert [c["id"] for c in tree] == [root["id"]] and [r["id"] for r in tree[0]["replies"]] == [reply["id"], nested["id"]]
    assert client.post(f"/api/board/posts/{pid}/comments", json={"text": "x", "parent_id": 999999}, headers=_auth(other)).status_code == 404
    # 편집: 본인만, edited 표시
    assert client.put(f"/api/board/comments/{root['id']}", json={"text": "남이 고침"}, headers=_auth(author)).status_code == 403
    edited = client.put(f"/api/board/comments/{root['id']}", json={"text": "고친 원댓글"}, headers=_auth(other)).json()["comment"]
    assert edited["text"] == "고친 원댓글" and edited["edited"] is True and isinstance(edited["updated_ms"], int)
    assert client.put(f"/api/board/comments/{root['id']}", json={"text": " "}, headers=_auth(other)).status_code == 400
    # 신고: 로그인 필수, 내 것은 안 됨, 한 번만
    assert client.post("/api/board/reports", json={"target_type": "post", "target_id": pid, "reason": "spam"}).status_code == 401
    assert client.post("/api/board/reports", json={"target_type": "post", "target_id": pid, "reason": "spam"}, headers=_auth(author)).status_code == 400
    assert client.post("/api/board/reports", json={"target_type": "post", "target_id": pid, "reason": "spam", "detail": "광고"}, headers=_auth(other)).status_code == 200
    assert client.post("/api/board/reports", json={"target_type": "post", "target_id": pid, "reason": "abuse"}, headers=_auth(other)).status_code == 409
    assert client.post("/api/board/reports", json={"target_type": "comment", "target_id": root["id"], "reason": "abuse"}, headers=_auth(author)).status_code == 200
    assert client.post("/api/board/reports", json={"target_type": "comment", "target_id": 999999, "reason": "abuse"}, headers=_auth(author)).status_code == 404
    assert client.post("/api/board/reports", json={"target_type": "post", "target_id": pid, "reason": "nope"}, headers=_auth(other)).status_code == 400
    # 원댓글을 지우면 답글도 사라진다
    assert client.delete(f"/api/board/comments/{root['id']}", headers=_auth(other)).status_code == 200
    assert client.get(f"/api/board/posts/{pid}").json()["comments"] == []


def test_empty_fields_rejected():
    token, _ = _signup()
    # 제목 없음
    assert client.post("/api/board/posts", data={"title": "  ", "body": "x"}, headers=_auth(token)).status_code == 400
    pid = client.post("/api/board/posts", data={"title": "t", "body": "x"}, headers=_auth(token)).json()["id"]
    # 댓글 내용 누락(로그인 상태)
    assert client.post(f"/api/board/posts/{pid}/comments", json={"text": " "}, headers=_auth(token)).status_code == 400


def test_pagination_math():
    r = board.list_posts(page=1, size=5)
    assert r["page"] == 1 and r["size"] == 5
    assert r["pages"] == max(1, (r["total"] + 4) // 5)


def test_support_info_uses_support_email_then_reset_sender(monkeypatch):
    monkeypatch.setenv("SUPPORT_EMAIL", " help@example.com ")
    assert client.get("/api/support/info").json() == {"email": "help@example.com"}
    monkeypatch.delenv("SUPPORT_EMAIL")
    monkeypatch.setenv("RESET_FROM_EMAIL", "noreply@example.com")
    assert client.get("/api/support/info").json() == {"email": "noreply@example.com"}

