"""Real korcen checks and write/rollback regressions on conftest's isolated DB."""
import secrets
import unicodedata

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import func
from sqlmodel import select

from app import auth, board
from app.db import BoardComment, BoardImage, BoardPost, ChatMessage, User, get_session
from app.main import app
from app.moderation import require_clean_text


@pytest.mark.parametrize("text", [
    "씨발", "병신", "개새끼", "좆같네", "지랄", "ㅅㅂ", "ㅆㅂ", "ㅂㅅ",
    "씨.발", "씨 발", "씨\u200b발", "시@발", "시1발", "씨１발", "병1신",
    unicodedata.normalize("NFD", "시발"), "시바이누 시발", "시발점 씨발",
])
def test_korean_profanity_and_obfuscation_are_rejected(text):
    with pytest.raises(ValueError, match="메시지에 비속어가 포함되어 있어요. 표현을 수정해 주세요."):
        require_clean_text(text, "메시지")


@pytest.mark.parametrize("text", [
    "안녕하세요", "시바이누 매수했어요", "시바 이누 거래량", "SHIB SHIBA INU BTC SOL XRP DOT USDT",
    "바이낸스 API 키와 시크릿", "시발점부터 확인해요", "보지도 못했어요",
    "시가총액과 거래량", "18시 18개 매수", "18,000원에 매수했어요", "개미들 손절하지 마세요",
    "급락장 존버", "엑시인피니티 베이비도지", "비트코인 ETF 승인", "국회의원 시장 규제 발표",
    "대통령의 가상자산 정책", "중국 거래소 규제", "[sticker:critical]", "[sticker:calm]",
    "https://example.com/씨발.png", "https://example.com/병신 자료 링크예요",
])
def test_trading_terms_and_links_are_allowed(text):
    require_clean_text(text, "메시지")


@pytest.mark.parametrize("body", [
    "<p>씨<strong>발</strong></p>", "<p>씨</p><p>발</p>",
    "<p>&#50472;&#48156;</p>", '<a href="https://example.com">병신</a>',
    '<img src="/api/board/posts/1/images/1" alt="씨발">',
])
def test_visible_html_text_is_checked(body):
    with pytest.raises(ValueError):
        require_clean_text(body, "본문", html=True)


def test_html_attributes_are_not_treated_as_prose():
    require_clean_text('<p><a href="https://example.com/씨발">차트</a></p>', "본문", html=True)


@pytest.fixture
def member():
    with get_session() as db:
        suffix = secrets.token_hex(6)
        user = User(email=f"moderation-{suffix}@example.invalid", username=f"mod-{suffix}",
                    password_hash="", created_at="2026-09-10T00:00:00Z")
        db.add(user)
        db.commit()
        db.refresh(user)
        return user


@pytest.fixture
def api(member):
    return TestClient(app, headers={"Authorization": f"Bearer {auth.make_token(member.id)}"})


def counts():
    with get_session() as db:
        return tuple(db.exec(select(func.count()).select_from(model)).one()
                     for model in (BoardPost, BoardImage, BoardComment, ChatMessage))


def rejected(response, field):
    assert response.status_code == 400, response.text
    assert response.json() == {"detail": f"{field}에 비속어가 포함되어 있어요. 표현을 수정해 주세요."}


def create(api, **data):
    response = api.post("/api/board/posts", data={"title": "매매 기록", "body": "오늘의 기록", **data})
    assert response.status_code == 200, response.text
    return response.json()


def test_chat_blocks_without_consuming_rate_limit_and_accepts_correction(api, member):
    before = counts()
    for _ in range(6):
        rejected(api.post("/api/chat", json={"text": "씨발"}), "메시지")
    assert counts() == before
    for i in range(5):
        response = api.post("/api/chat", json={"text": f"시바이누 매매 기록 {i}"})
        assert response.status_code == 200, response.text
        assert response.json()["message"]["user_id"] == member.id
    assert api.post("/api/chat", json={"text": "추가 메시지"}).status_code == 429


@pytest.mark.parametrize("data,field", [
    ({"title": "씨발"}, "제목"), ({"body": "병신"}, "본문"),
    ({"body": "<p>씨<strong>발</strong></p>", "body_format": "html"}, "본문"),
])
def test_post_creation_rejects_before_persisting_anything(api, data, field):
    before = counts()
    rejected(api.post("/api/board/posts", data={"title": "매매 기록", **data}), field)
    assert counts() == before
    assert create(api)["title"] == "매매 기록"


_PNG = b"\x89PNG\r\n\x1a\n" + b"\x00" * 32  # Existing upload validation checks the signature.


def test_html_rejection_rolls_back_flushed_post_and_image(api):
    before = counts()
    rejected(api.post("/api/board/posts", data={
        "title": "사진 기록", "body_format": "html",
        "body": '<p>씨발</p><img data-key="new:0">',
    }, files={"images": ("chart.png", _PNG, "image/png")}), "본문")
    assert counts() == before


@pytest.mark.parametrize("data,field", [
    ({"title": "씨발", "body": "새 본문"}, "제목"),
    ({"title": "새 제목", "body": "병신"}, "본문"),
    ({"title": "새 제목", "body": '<p>씨<strong>발</strong></p><img data-key="new:0">',
      "body_format": "html"}, "본문"),
])
def test_rejected_post_edit_preserves_original_text_and_images(api, data, field):
    response = api.post("/api/board/posts", data={"title": "원래 제목", "body": "원래 본문"},
                        files={"images": ("old.png", _PNG, "image/png")})
    assert response.status_code == 200, response.text
    post = response.json()
    before = counts()
    rejected(api.put(f"/api/board/posts/{post['id']}", data=data,
                     files={"images": ("new.png", _PNG, "image/png")}), field)
    assert counts() == before
    persisted = api.get(f"/api/board/posts/{post['id']}").json()
    for key in ("title", "body", "images"):
        assert persisted[key] == post[key]
    assert api.get(post["images"][0]["url"]).content == _PNG


def test_comments_replies_and_edits_reject_and_allow_correction(api, member):
    post = create(api)
    path = f"/api/board/posts/{post['id']}/comments"
    before = counts()
    for _ in range(board._RATE_MAX + 1):
        rejected(api.post(path, json={"text": "병신"}), "댓글")
    assert counts() == before
    assert not board._recent.get(f"user:{member.id}")
    response = api.post(path, json={"text": "전략 공유 감사합니다"})
    assert response.status_code == 200, response.text
    comment = response.json()["comment"]
    before = counts()
    rejected(api.post(path, json={"text": "씨발", "parent_id": comment["id"]}), "댓글")
    rejected(api.put(f"/api/board/comments/{comment['id']}", json={"text": "씨발"}), "댓글")
    assert counts() == before
    with get_session() as db:
        assert db.get(BoardComment, comment["id"]).text == "전략 공유 감사합니다"
    assert api.post(path, json={"text": "시바이누 매매 기록", "parent_id": comment["id"]}).status_code == 200
    response = api.put(f"/api/board/comments/{comment['id']}", json={"text": "수정한 댓글"})
    assert response.status_code == 200 and response.json()["comment"]["text"] == "수정한 댓글"


def test_historical_content_is_not_rewritten_by_reading(api, member):
    with get_session() as db:
        post = BoardPost(author_user_id=member.id, author_name=member.username, title="옛 게시글",
                         body="씨발", created_at="2026-09-10T00:00:00Z", created_ms=1)
        db.add(post)
        db.commit()
        db.refresh(post)
        pid = post.id
    assert api.get(f"/api/board/posts/{pid}").json()["body"] == "씨발"
