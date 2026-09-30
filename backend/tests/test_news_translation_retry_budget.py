"""반복 재시도 예산 — 검증에 걸린 제목·요약을 무한히 다시 번역하지 않는다.

거절된 결과는 다음 수집 주기마다 곧장 다시 점유돼 유료 호출이 하루 수백 번
되풀이됐다. 시도 횟수를 세고 점점 미루다 포기한다.
"""
from __future__ import annotations

import hashlib

import pytest

pytest.importorskip("sqlmodel")

from sqlmodel import Session, SQLModel, create_engine

from app import community_summary_repository as summaries
from app.agent_features.position_news import repository

NOW = 1_800_000_000_000
TITLE = "Bitcoin ETF inflows hit record"
VERSION = "coin-news-title-ko-v9"


@pytest.fixture
def db(tmp_path):
    engine = create_engine(
        f"sqlite:///{tmp_path / 'retry-budget.db'}",
        connect_args={"check_same_thread": False, "timeout": 30},
    )
    SQLModel.metadata.create_all(engine)
    with Session(engine) as session:
        yield session
    engine.dispose()


def _attempt(db, *, now_ms, rejected=True, version=VERSION, succeed=False):
    """한 수집 주기를 흉내낸다 — 점유해 유료로 부르면 True.

    검증을 통과하지 못한 번역은 저장되지 않고 자리만 'error' 로 반납된다
    (`_translate_title_batch` 가 하는 일 그대로).
    """
    claim = repository.claim_title_translations(
        [TITLE],
        rejected_titles=[TITLE] if rejected else [],
        prompt_version=version,
        now_ms=now_ms,
        db=db,
    )
    if claim["claimed"] != [TITLE]:
        return False
    if succeed:
        repository.store_title_translations(
            {TITLE: "비트코인 ETF 자금 유입 사상 최대"},
            claim_token=claim["claim_token"], now_ms=now_ms + 100, db=db)
    else:
        repository.release_title_translation_claims(
            [TITLE], claim_token=claim["claim_token"], now_ms=now_ms + 100, db=db)
    return True


def test_rejected_title_retries_once_then_stops(db):
    assert _attempt(db, now_ms=NOW) is True
    # 첫 실패 뒤에는 기본 대기(5분) 안에 다시 점유되지 않는다.
    assert _attempt(db, now_ms=NOW + 299_000) is False
    assert _attempt(db, now_ms=NOW + 300_001) is True
    # 두 번째 실패 뒤에는 시간이 지나도 세 번째 유료 작업을 점유하지 않는다.
    assert _attempt(db, now_ms=NOW + 300_001 + 599_000) is False
    assert _attempt(db, now_ms=NOW + 300_001 + 600_001) is False


def test_rejected_title_is_abandoned_after_the_attempt_budget(db):
    # 수집 주기(5분)를 하루치 돌린다.
    paid = sum(_attempt(db, now_ms=NOW + cycle * 300_000) for cycle in range(288))
    assert paid == repository.TITLE_TRANSLATION_MAX_ATTEMPTS
    # 다음 날에도 더 부르지 않는다.
    assert _attempt(db, now_ms=NOW + 400 * 300_000) is False


def test_successful_translation_clears_the_attempt_budget(db):
    assert _attempt(db, now_ms=NOW) is True
    assert _attempt(db, now_ms=NOW + 300_001, succeed=True) is True
    assert repository.get_title_translations([TITLE], db=db) == {
        TITLE: "비트코인 ETF 자금 유입 사상 최대"
    }
    # 나중에 다른 이유로 거절되더라도 예산은 처음부터 다시 센다.
    paid = sum(_attempt(db, now_ms=NOW + 10_000_000 + cycle * 300_000) for cycle in range(288))
    assert paid == repository.TITLE_TRANSLATION_MAX_ATTEMPTS


def test_abandoned_title_is_retried_once_the_prompt_changes(db):
    for cycle in range(288):
        _attempt(db, now_ms=NOW + cycle * 300_000)
    assert _attempt(db, now_ms=NOW + 300 * 300_000) is False
    # 지시문을 고쳐 배포하면 포기한 제목도 다시 예산을 받는다.
    assert _attempt(db, now_ms=NOW + 301 * 300_000, version="coin-news-title-ko-v10") is True


def test_busy_release_does_not_spend_the_attempt_budget(db):
    claim = repository.claim_title_translations(
        [TITLE], prompt_version=VERSION, now_ms=NOW, db=db)
    # 제공자를 부르기 전에 로컬 혼잡으로 반납한 경우 — 유료 호출이 없었다.
    repository.release_title_translation_claims(
        [TITLE], claim_token=claim["claim_token"], retry_immediately=True,
        now_ms=NOW + 10, db=db)
    paid = sum(_attempt(db, now_ms=NOW + 20 + cycle * 300_000) for cycle in range(288))
    assert paid == repository.TITLE_TRANSLATION_MAX_ATTEMPTS


def test_provider_outage_does_not_spend_the_attempt_budget(db):
    """장애가 하루 종일 이어져도 번역 가능한 제목을 영영 버리지 않는다."""
    for cycle in range(288):
        now_ms = NOW + cycle * 300_000
        claim = repository.claim_title_translations(
            [TITLE], prompt_version=VERSION, now_ms=now_ms, db=db)
        if claim["claimed"] != [TITLE]:
            continue
        repository.release_title_translation_claims(
            [TITLE], claim_token=claim["claim_token"], provider_error=True,
            now_ms=now_ms + 100, db=db)
    # 장애가 끝나면 예산은 그대로 남아 있다.
    paid = sum(_attempt(db, now_ms=NOW + 400 * 300_000 + cycle * 300_000) for cycle in range(288))
    assert paid == repository.TITLE_TRANSLATION_MAX_ATTEMPTS


def _summary_request(post_id="123", body="public post body", version="summary-v1:model-one"):
    identity = {"post_id": post_id, "body_hash": hashlib.sha256(body.encode()).hexdigest(),
                "prompt_version": version}
    return {"summary_key": summaries.make_summary_key(**identity), **identity}


def _summary_attempt(db, request, *, now_ms):
    claim = summaries.claim_summaries(
        [request], rejected_keys=[request["summary_key"]], now_ms=now_ms, db=db)
    if claim["claimed"] != [request["summary_key"]]:
        return False
    # 검증을 통과하지 못한 요약도 저장되지 않고 자리만 반납된다.
    summaries.release_claims([request["summary_key"]], claim_token=claim["claim_token"],
                             now_ms=now_ms + 100, db=db)
    return True


def test_rejected_community_summary_is_abandoned_after_the_attempt_budget(db):
    request = _summary_request()
    paid = sum(_summary_attempt(db, request, now_ms=NOW + cycle * 300_000) for cycle in range(288))
    assert paid == summaries.MAX_ATTEMPTS
    assert _summary_attempt(db, request, now_ms=NOW + 400 * 300_000) is False


def test_rejected_community_summary_waits_longer_after_each_attempt(db):
    request = _summary_request()
    assert _summary_attempt(db, request, now_ms=NOW) is True
    assert _summary_attempt(db, request, now_ms=NOW + 299_000) is False
    assert _summary_attempt(db, request, now_ms=NOW + 300_200) is True
