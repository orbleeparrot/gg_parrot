"""Real isolated SQL storage contracts; no external sources, models, or DBs."""
import hashlib
from concurrent.futures import ThreadPoolExecutor
from threading import Barrier

import pytest
from sqlalchemy import BigInteger
from sqlmodel import Session, SQLModel, create_engine, select

from app import community_summary_repository as repo
from app.db import CommunityPostSummary

NOW = 1_800_000_000_000


def request(post_id="123", body="public post body", version="summary-v1:model-one"):
    identity = {"post_id": post_id, "body_hash": hashlib.sha256(body.encode()).hexdigest(), "prompt_version": version}
    return {"summary_key": repo.make_summary_key(**identity), **identity}


@pytest.fixture
def store(tmp_path, monkeypatch):
    engine = create_engine(f"sqlite:///{tmp_path / 'summaries.db'}", connect_args={"check_same_thread": False})
    SQLModel.metadata.create_all(engine)
    monkeypatch.setattr(repo, "get_session", lambda: Session(engine))
    yield engine
    engine.dispose()


def test_immutable_body_and_model_identity_prevents_stale_summary_reuse(store):
    first = request()
    changed_body, changed_version = request(body="edited body"), request(version="summary-v1:model-two")
    assert len({row["summary_key"] for row in (first, changed_body, changed_version)}) == 3
    claim = repo.claim_summaries([first], now_ms=NOW)
    assert repo.store_summaries({first["summary_key"]: "작성자가 가격 흐름에 관한 의견을 제시했습니다."},
                                claim_token=claim["claim_token"], now_ms=NOW+1) == [first["summary_key"]]
    # Each repository call opens a fresh session, as after another process restart.
    assert repo.get_summaries([first, changed_body, changed_version], now_ms=NOW+2) == {
        first["summary_key"]: "작성자가 가격 흐름에 관한 의견을 제시했습니다."}
    next_claim = repo.claim_summaries([first, changed_body, changed_version], now_ms=NOW+2)
    assert next_claim["cached"][first["summary_key"]]
    assert set(next_claim["claimed"]) == {changed_body["summary_key"], changed_version["summary_key"]}
    assert "body" not in CommunityPostSummary.model_fields
    assert isinstance(CommunityPostSummary.__table__.c.claimed_ms.type, BigInteger)
    assert isinstance(CommunityPostSummary.__table__.c.updated_ms.type, BigInteger)


def test_two_instances_claim_one_batch_once_even_in_opposite_order(store):
    requests = [request("123"), request("456")]
    barrier = Barrier(2)

    def claim(batch):
        barrier.wait(timeout=5)
        return repo.claim_summaries(batch, now_ms=NOW)

    with ThreadPoolExecutor(max_workers=2) as pool:
        first = pool.submit(claim, requests)
        second = pool.submit(claim, requests[::-1])
        results = [first.result(timeout=10), second.result(timeout=10)]
    claimed = [key for result in results for key in result["claimed"]]
    assert sorted(claimed) == sorted(row["summary_key"] for row in requests)
    assert sum(len(result["waiting"]) for result in results) == 2


def test_stale_owner_cannot_store_release_or_renew_reclaimed_work(store):
    item = request()
    old = repo.claim_summaries([item], now_ms=NOW)
    active = repo.claim_summaries([item], now_ms=NOW+repo.LEASE_MS-1)
    assert active["waiting"] == [item["summary_key"]]
    new = repo.claim_summaries([item], now_ms=NOW+repo.LEASE_MS)
    assert new["claimed"] == [item["summary_key"]]
    assert not repo.renew_claims([item["summary_key"]], claim_token=old["claim_token"], now_ms=NOW+repo.LEASE_MS+1)
    assert repo.release_claims([item["summary_key"]], claim_token=old["claim_token"], now_ms=NOW+repo.LEASE_MS+1) == 0
    assert repo.store_summaries({item["summary_key"]: "오래된 요청의 요약"}, claim_token=old["claim_token"], now_ms=NOW+repo.LEASE_MS+1) == []
    assert repo.store_summaries({item["summary_key"]: "새 요청의 요약"}, claim_token=new["claim_token"], now_ms=NOW+repo.LEASE_MS+2) == [item["summary_key"]]
    assert repo.get_summaries([item], now_ms=NOW+repo.LEASE_MS+3) == {item["summary_key"]: "새 요청의 요약"}


def test_rejected_ready_cache_is_replaced_once_without_stealing_pending_claim(store):
    item = request()
    old = repo.claim_summaries([item], now_ms=NOW)
    repo.store_summaries({item["summary_key"]: "검증에서 탈락한 이전 요약"}, claim_token=old["claim_token"], now_ms=NOW+1)
    replacement = repo.claim_summaries([item], rejected_keys=[item["summary_key"]], now_ms=NOW+2)
    concurrent = repo.claim_summaries([item], rejected_keys=[item["summary_key"]], now_ms=NOW+3)
    assert replacement["claimed"] == [item["summary_key"]]
    assert concurrent["waiting"] == [item["summary_key"]]
    assert concurrent["claim_token"] == ""


def test_provider_failure_defers_but_local_busy_can_retry_without_losing_success(store):
    first, second = request(), request("456")
    claim = repo.claim_summaries([first, second], now_ms=NOW)
    repo.store_summaries({first["summary_key"]: "정상 한국어 요약"}, claim_token=claim["claim_token"], now_ms=NOW+1)
    assert repo.release_claims([first["summary_key"], second["summary_key"]], claim_token=claim["claim_token"], now_ms=NOW+2) == 1
    waiting = repo.claim_summaries([first, second], now_ms=NOW+3)
    assert waiting["cached"] == {first["summary_key"]: "정상 한국어 요약"}
    assert waiting["deferred"] == [second["summary_key"]]
    # A rejected key does not bypass an already recorded provider backoff.
    assert repo.claim_summaries([second], rejected_keys=[second["summary_key"]], now_ms=NOW+3)["deferred"] == [second["summary_key"]]
    again = repo.claim_summaries([second], now_ms=NOW+2+repo.RETRY_MS)
    assert again["claimed"] == [second["summary_key"]]
    repo.release_claims([second["summary_key"]], claim_token=again["claim_token"], retry_immediately=True, now_ms=NOW+repo.RETRY_MS+3)
    assert repo.claim_summaries([second], now_ms=NOW+repo.RETRY_MS+4)["claimed"] == [second["summary_key"]]


def test_renew_is_atomic_for_entire_owned_unstored_batch(store):
    first, second = request(), request("456")
    claim = repo.claim_summaries([first, second], now_ms=NOW)
    assert repo.renew_claims([first["summary_key"], second["summary_key"]], claim_token=claim["claim_token"], now_ms=NOW+100)
    repo.store_summaries({second["summary_key"]: "완료된 요약"}, claim_token=claim["claim_token"], now_ms=NOW+101)
    assert not repo.renew_claims([first["summary_key"], second["summary_key"]], claim_token=claim["claim_token"], now_ms=NOW+200)
    with Session(store) as db:
        assert db.get(CommunityPostSummary, first["summary_key"]).claimed_ms == NOW+100
    assert repo.renew_claims([first["summary_key"]], claim_token=claim["claim_token"], now_ms=NOW+201)


def test_store_requires_owned_claim_and_korean_output(store):
    item = request()
    assert repo.store_summaries({item["summary_key"]: "무단 저장 요약"}, claim_token="", now_ms=NOW) == []
    claim = repo.claim_summaries([item], now_ms=NOW)
    assert repo.store_summaries({item["summary_key"]: "English source body"}, claim_token=claim["claim_token"], now_ms=NOW+1) == []
    assert repo.get_summaries([item], now_ms=NOW+2) == {}


def test_expired_success_reclaims_and_pruning_is_bounded_maintenance(store):
    items = [request(str(value)) for value in range(1, 5)]
    claim = repo.claim_summaries(items, now_ms=NOW)
    repo.store_summaries({item["summary_key"]: "오래된 요약" for item in items[:3]}, claim_token=claim["claim_token"], now_ms=NOW+1)
    expiry = NOW+1+repo.CACHE_TTL_MS
    assert repo.get_summaries(items, now_ms=expiry) == {}
    assert repo.claim_summaries([items[0]], now_ms=expiry)["claimed"] == [items[0]["summary_key"]]
    assert repo.prune_summaries(retention_days=30, limit=2, now_ms=expiry+1) == 2
    with Session(store) as db:
        rows = db.exec(select(CommunityPostSummary)).all()
        assert len(rows) == 2
        assert db.get(CommunityPostSummary, items[0]["summary_key"]).processing_status == "pending"
    assert repo.prune_summaries(retention_days=30, now_ms=expiry+1) == 1


def test_configured_shared_database_cannot_silently_claim_in_local_sqlite(store, monkeypatch):
    monkeypatch.setenv("DATABASE_URL", "https://misconfigured-shared-db.invalid")
    with pytest.raises(RuntimeError, match="shared database"):
        repo.claim_summaries([request()], now_ms=NOW)
    with Session(store) as db:
        assert db.exec(select(CommunityPostSummary)).all() == []


def test_database_claim_failure_propagates_without_authorizing_paid_work(monkeypatch):
    def unavailable():
        raise RuntimeError("fixture database unavailable")
    monkeypatch.setattr(repo, "get_session", unavailable)
    with pytest.raises(RuntimeError, match="unavailable"):
        repo.claim_summaries([request()], now_ms=NOW)


def test_mismatched_identity_fails_before_database_access(monkeypatch):
    monkeypatch.setattr(repo, "get_session", lambda: pytest.fail("invalid request must not touch DB"))
    item = {**request(), "post_id": "456"}
    with pytest.raises(ValueError, match="identity"):
        repo.claim_summaries([item], now_ms=NOW)
