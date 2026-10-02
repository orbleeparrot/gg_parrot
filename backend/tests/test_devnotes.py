"""개발자 노트 — 관리자 [공지]를 AI 로 노트 양식에 맞춘다. 아이콘·링크는 허용 목록 밖이면 버리고, 실패해도 글은 남는다."""
import json
from datetime import datetime
from types import SimpleNamespace

import pytest

from app import board, devnotes
from app.ai_runtime import AiCallRuntime
from app.db import User, get_session


def _user(admin=True):
    stamp = str(datetime.now().timestamp()).replace(".", "")
    with get_session() as db:
        user = User(email=f"devnote-{stamp}@example.invalid", username=f"dn{stamp[-8:]}", password_hash="x",
                    created_at="2026-10-01T00:00:00Z", is_admin=admin)
        db.add(user)
        db.commit()
        db.refresh(user)
        return user


def _fake_ai(monkeypatch, payload, calls=None):
    class Messages:
        def create(self, **kwargs):
            if calls is not None:
                calls.append(kwargs)
            if isinstance(payload, Exception):
                raise payload
            return SimpleNamespace(content=[SimpleNamespace(type="text", text=json.dumps(payload, ensure_ascii=False))])
    monkeypatch.setattr(devnotes, "get_ai_client", lambda: SimpleNamespace(messages=Messages()))
    runtime = AiCallRuntime(cache_ttl_seconds=0, retries=0)
    monkeypatch.setattr(devnotes, "get_ai_runtime", lambda: runtime)
    devnotes._cache.update(at=0.0, note=None)


def test_notice_becomes_the_current_dev_note_in_the_template(monkeypatch):
    calls = []
    _fake_ai(monkeypatch, {"title": "10월 업데이트", "items": [
        {"icon": "trophy", "title": "리더보드 보상", "text": "10등 안에 들면 매일 포인트를 드려요.", "link": "/leaderboard"},
        {"icon": "evil", "title": "새 기능", "text": "채팅 카드에 수익률이 보여요.", "link": "https://phish.example"},
    ]}, calls)
    post = board.create_post(_user(), "10월 업데이트 공지", "<p>리더보드 보상이 생겼어요</p><p>채팅 카드 수익률</p>",
                             body_format="html", is_notice=True)
    result = devnotes.apply_from_post(post["id"])
    assert result["status"] == "ready"
    note = devnotes.current()
    assert note["post_id"] == post["id"] and note["title"] == "10월 업데이트"
    first, second = note["items"]
    assert first == {"icon": "trophy", "title": "리더보드 보상", "text": "10등 안에 들면 매일 포인트를 드려요.",
                     "link": "/leaderboard", "link_label": "리더보드 보기"}
    assert second["icon"] == "star" and second["link"] == "" and second["link_label"] == ""  # 허용 목록 밖은 버린다
    # 뉴스 번역과 같은 AI 경로 — 용도 기록·형식 강제
    assert calls[0]["purpose"] == "devnote" and calls[0]["json_schema"]["name"] == "dev_note"
    assert "리더보드 보상이 생겼어요" in calls[0]["messages"][0]["content"]


def test_failure_keeps_the_post_and_the_previous_note(monkeypatch):
    _fake_ai(monkeypatch, {"title": "이전", "items": [{"icon": "star", "title": "이전 노트", "text": "그대로 남아요.", "link": ""}]})
    first = board.create_post(_user(), "이전 공지", "본문", is_notice=True)
    assert devnotes.apply_from_post(first["id"])["status"] == "ready"
    _fake_ai(monkeypatch, TimeoutError("provider down"))
    second = board.create_post(_user(), "새 공지", "본문", is_notice=True)
    result = devnotes.apply_from_post(second["id"])
    assert result["status"] == "failed" and "다시 시도" in result["message"]
    assert devnotes.current()["post_id"] == first["id"]


def test_only_notice_posts_can_become_dev_notes(monkeypatch):
    _fake_ai(monkeypatch, {"title": "x", "items": [{"icon": "star", "title": "a", "text": "b", "link": ""}]})
    plain = board.create_post(_user(admin=False), "일반 글", "본문")
    assert devnotes.apply_from_post(plain["id"])["status"] == "failed"


def test_empty_ai_answer_is_rejected():
    post = SimpleNamespace(id=1, created_ms=0)
    with pytest.raises(ValueError):
        devnotes.normalize({"title": "x", "items": [{"icon": "star", "title": "", "text": "", "link": ""}]}, post=post)
