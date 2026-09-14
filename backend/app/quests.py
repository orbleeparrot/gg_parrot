"""일일 퀘스트 — 리더보드에 매크로를 올리지 않는 회원도 포인트를 벌 수 있는 길.

매일 KST 00:00 에 새로 열리고, 퀘스트마다 하루 한 번만 보상한다. 퀘스트는
서비스에 의미 있는 행동(백테스트·페이퍼·댓글)으로만 잡아 빈 글·아무 좋아요로
채워지는 어뷰징을 피하고, 하루 총합은 언락 가격(100p)의 1/3 정도로 두어
포인트 가치가 무너지지 않게 한다("3~4일 모으면 매크로 하나").

완료 판정은 기존 행동 엔드포인트에 훅으로 붙는다(:func:`complete`). 훅은
로그인 계정일 때만 돌고, 이미 받은 날이면 조용히 건너뛴다.
"""
from __future__ import annotations

from datetime import datetime, timedelta, timezone
from typing import Optional

from sqlalchemy.exc import IntegrityError
from sqlmodel import select

from . import points as points_mod
from .db import DailyQuestClaim, User

_KST = timezone(timedelta(hours=9))

# 퀘스트 목록(순서대로 화면에 보인다). key 는 원장(PointLedger.ref)에도 남으니 바꾸지 않는다.
QUESTS: list[dict] = [
    {
        "key": "backtest_run",
        "title": "백테스트 1회 돌리기",
        "hint": "직접 만들기에서 조건을 고르고 백테스트를 실행해요.",
        "reward": 10,
        "to": "/builder",
    },
    {
        "key": "paper_start",
        "title": "페이퍼 트레이딩 1회 시작하기",
        "hint": "백테스트가 괜찮으면 페이퍼 트레이딩 탭에서 실제 시세로 돌려 봐요.",
        "reward": 10,
        "to": "/builder",
    },
    {
        "key": "board_comment",
        "title": "게시판에 댓글 1개 남기기",
        "hint": "10자 이상 댓글만 인정돼요.",
        "reward": 10,
        "to": "/board",
    },
]
_BY_KEY = {q["key"]: q for q in QUESTS}
COMMENT_MIN_CHARS = 10
REASON = "quest"


def today_kst() -> str:
    return datetime.now(timezone.utc).astimezone(_KST).strftime("%Y-%m-%d")


def _now() -> tuple[str, int]:
    now = datetime.now(timezone.utc)
    return now.strftime("%Y-%m-%dT%H:%M:%SZ"), int(now.timestamp() * 1000)


def complete(db, user: Optional[User], key: str) -> Optional[dict]:
    """Mark ``key`` done today for ``user`` and pay the reward once.

    Returns the quest view (with ``reward``) when this call paid it, ``None``
    when there is no account, the quest is unknown, or it was already claimed
    today. Commits its own transaction so a hook can never leave the calling
    endpoint half-done; a concurrent duplicate claim loses on the unique index
    and is treated as already-claimed.
    """
    if user is None or user.id is None:
        return None
    quest = _BY_KEY.get(key)
    if quest is None:
        return None
    date_kst = today_kst()
    existing = db.exec(
        select(DailyQuestClaim).where(
            DailyQuestClaim.user_id == user.id,
            DailyQuestClaim.date_kst == date_kst,
            DailyQuestClaim.quest_key == key,
        )
    ).first()
    if existing is not None:
        return None
    account = db.get(User, user.id)
    if account is None or getattr(account, "is_deleted", False):
        return None
    created_at, created_ms = _now()
    db.add(
        DailyQuestClaim(
            user_id=user.id,
            date_kst=date_kst,
            quest_key=key,
            reward=quest["reward"],
            created_at=created_at,
            created_ms=created_ms,
        )
    )
    points_mod.apply(db, account, quest["reward"], REASON, f"{date_kst}:{key}")
    try:
        db.commit()
    except IntegrityError:
        db.rollback()
        return None
    # 요청 스코프 세션이 든 user 객체도 새 잔액을 보게 맞춘다.
    if user is not account:
        user.points_balance = account.points_balance
    return {**quest, "done": True, "date_kst": date_kst}


def today(db, user: User) -> dict:
    """Today's quest board for the account: each quest with ``done`` + totals."""
    date_kst = today_kst()
    claimed = {
        row.quest_key
        for row in db.exec(
            select(DailyQuestClaim).where(
                DailyQuestClaim.user_id == user.id,
                DailyQuestClaim.date_kst == date_kst,
            )
        ).all()
    }
    items = [{**q, "done": q["key"] in claimed} for q in QUESTS]
    return {
        "date_kst": date_kst,
        "quests": items,
        "earned": sum(q["reward"] for q in items if q["done"]),
        "total": sum(q["reward"] for q in items),
        "done_count": sum(1 for q in items if q["done"]),
    }
