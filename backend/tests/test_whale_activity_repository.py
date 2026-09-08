"""Shared large-fill storage and work claims on an isolated real database."""
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timezone
from threading import Barrier

import pytest
from sqlalchemy import event
from sqlmodel import Session, SQLModel, create_engine, select

from app.agent_features.whale_activity import repository as repo
from app.db import RunSession, WhaleTradeState

NOW = 1_800_000_000_000


def stamp(millis=NOW):
    return datetime.fromtimestamp(millis / 1000, timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def payload(ids=(1,), *, now=NOW, threshold=100_000, symbol="BTCUSDT", market="spot"):
    return {"feature_key": "whale_activity", "symbol": symbol, "market": market,
            "status": "ready" if ids else "empty", "threshold_quote": threshold,
            "observed_at": stamp(now), "items": [
                {"id": f"{market}:{symbol}:{value}", "notional": 120_000 + value,
                 "occurred_at": stamp(now), "side": "buy", "price": 60_000, "quantity": 2}
                for value in ids]}


@pytest.fixture
def store(tmp_path, monkeypatch):
    engine = create_engine(f"sqlite:///{tmp_path / 'whales.db'}", connect_args={"check_same_thread": False})
    SQLModel.metadata.create_all(engine)
    monkeypatch.setattr(repo, "get_session", lambda: Session(engine))
    yield engine
    engine.dispose()


def test_discover_connected_running_distinct_supported_pairs_and_due_filter(store):
    with Session(store) as db:
        for symbol, market, status, age in [
            ("BTCUSDT", "spot", "running", 0), ("btcusdt", "spot", "running", 2),
            ("BTCUSDT", "futures", "running", 0), ("ETHUSDC", "spot", "running", 30),
            ("SOLUSDT", "spot", "running", 31), ("DOGEUSDT", "spot", "stopped", 0),
            ("ETHUSDT", "options", "running", 0), ("ETHBTC", "spot", "running", 0),
            ("XRPUSDT", "spot", "running", -120),
        ]:
            db.add(RunSession(user_id=1, symbol=symbol, market=market, status=status,
                              started_at=stamp(), last_heartbeat_at=stamp(NOW-age*1000)))
        db.commit()
    expected = [{"symbol": "BTCUSDT", "market": "futures"},
                {"symbol": "BTCUSDT", "market": "spot"},
                {"symbol": "ETHUSDC", "market": "spot"}]
    assert repo.discover_pairs(now_ms=NOW) == expected
    assert repo.claim_collection("BTCUSDT", "spot", now_ms=NOW)
    assert repo.discover_pairs(now_ms=NOW, due_only=True) == [expected[0], expected[2]]


def test_two_instances_only_authorize_one_network_request(store):
    barrier = Barrier(2)
    def claim():
        barrier.wait(timeout=5)
        return repo.claim_collection("BTCUSDT", "spot", now_ms=NOW)
    with ThreadPoolExecutor(max_workers=2) as pool:
        results = [future.result(timeout=10) for future in [pool.submit(claim), pool.submit(claim)]]
    assert sum(bool(token) for token in results) == 1


def test_least_recently_attempted_pairs_lead_next_time_bounded_cycle(store):
    with Session(store) as db:
        for symbol in ("BTCUSDT", "ETHUSDT", "SOLUSDT"):
            db.add(RunSession(user_id=1, symbol=symbol, market="spot", started_at=stamp(), last_heartbeat_at=stamp()))
        db.commit()
    assert [pair["symbol"] for pair in repo.discover_pairs(now_ms=NOW)] == ["BTCUSDT", "ETHUSDT", "SOLUSDT"]
    # A slow first pair consumes the cycle deadline; the next scheduled cycle
    # must advance to assets that have not yet had any network attempt.
    assert repo.claim_collection("BTCUSDT", "spot", now_ms=NOW)
    assert [pair["symbol"] for pair in repo.discover_pairs(now_ms=NOW+1)] == ["ETHUSDT", "SOLUSDT", "BTCUSDT"]
    assert repo.claim_collection("ETHUSDT", "spot", now_ms=NOW+2)
    assert [pair["symbol"] for pair in repo.discover_pairs(now_ms=NOW+3)] == ["SOLUSDT", "BTCUSDT", "ETHUSDT"]


def test_bootstrap_uses_same_state_batch_to_yield_to_recent_worker_observations(store):
    with Session(store) as db:
        for symbol in ("BTCUSDT", "ETHUSDT", "SOLUSDT"):
            db.add(RunSession(user_id=1, symbol=symbol, market="spot", started_at=stamp(), last_heartbeat_at=stamp()))
        db.commit()
    token = repo.claim_collection("BTCUSDT", "spot", now_ms=NOW-90_000)
    repo.store_result("BTCUSDT", "spot", token, payload((), now=NOW-90_000), now_ms=NOW-90_000)
    # A worker which died before storing is also given the same bounded recovery
    # grace, even after its 60-second lease expires.
    assert repo.claim_collection("ETHUSDT", "spot", now_ms=NOW-90_000)
    queries = []
    def record_query(_conn, _cursor, statement, *_args):
        if statement.startswith("SELECT"):
            queries.append(statement)
    event.listen(store, "before_cursor_execute", record_query)
    try:
        selected = repo.discover_pairs(due_only=True, bootstrap_only=True, now_ms=NOW)
    finally:
        event.remove(store, "before_cursor_execute", record_query)
    assert [pair["symbol"] for pair in selected] == ["SOLUSDT"]
    assert len(queries) == 2  # One active-session query and one shared-state batch.
    assert {pair["symbol"] for pair in repo.discover_pairs(due_only=True, bootstrap_only=True, now_ms=NOW+30_000)} == {
        "BTCUSDT", "ETHUSDT", "SOLUSDT"}


def test_claim_lease_recovery_fences_late_success_and_failure(store):
    old = repo.claim_collection("BTCUSDT", "spot", now_ms=NOW)
    assert repo.claim_collection("BTCUSDT", "spot", now_ms=NOW+repo.LEASE_MS-1) is None
    new = repo.claim_collection("BTCUSDT", "spot", now_ms=NOW+repo.LEASE_MS)
    assert new and new != old
    assert not repo.store_result("BTCUSDT", "spot", old, payload(), now_ms=NOW+repo.LEASE_MS+1)
    assert not repo.record_failure("BTCUSDT", "spot", old, error_code="429", now_ms=NOW+repo.LEASE_MS+1)
    assert repo.provider_cooldown_remaining("spot", now_ms=NOW+repo.LEASE_MS+1) == 0
    assert repo.store_result("BTCUSDT", "spot", new, payload(), now_ms=NOW+repo.LEASE_MS+2)
    assert repo.read_snapshot("BTCUSDT", "spot", now_ms=NOW+repo.LEASE_MS+3)["status"] == "ready"


def test_expired_claim_cannot_store_even_without_new_owner(store):
    token = repo.claim_collection("BTCUSDT", "spot", now_ms=NOW)
    assert not repo.store_result("BTCUSDT", "spot", token, payload(), now_ms=NOW+repo.LEASE_MS)
    assert not repo.record_failure("BTCUSDT", "spot", token, error_code="timeout", now_ms=NOW+repo.LEASE_MS)


def test_empty_observation_is_success_and_db_only_reader_preserves_stale_data(store):
    assert repo.read_snapshot("BTCUSDT", "spot", now_ms=NOW) is None
    token = repo.claim_collection("BTCUSDT", "spot", now_ms=NOW)
    assert repo.store_result("BTCUSDT", "spot", token, payload(()), now_ms=NOW+1)
    hit = repo.read_snapshot("BTCUSDT", "spot", now_ms=NOW+2)
    assert hit["items"] == [] and hit["status"] == "empty" and hit["stale"] is False
    assert hit["last_success_ms"] == NOW+1
    assert repo.claim_collection("BTCUSDT", "spot", now_ms=NOW+repo.INTERVAL_MS) is None
    assert repo.read_snapshot("BTCUSDT", "spot", now_ms=NOW+1+repo.STALE_MS)["stale"] is True


def test_success_merges_recent_stable_ids_and_rechecks_threshold(store):
    first = repo.claim_collection("BTCUSDT", "spot", now_ms=NOW)
    repo.store_result("BTCUSDT", "spot", first, payload((1, 2)), now_ms=NOW)
    second = repo.claim_collection("BTCUSDT", "spot", now_ms=NOW+repo.INTERVAL_MS)
    repo.store_result("BTCUSDT", "spot", second, payload((2, 3), now=NOW+repo.INTERVAL_MS), now_ms=NOW+repo.INTERVAL_MS)
    assert {item["id"] for item in repo.read_snapshot("BTCUSDT", "spot", now_ms=NOW+repo.INTERVAL_MS)["items"]} == {
        "spot:BTCUSDT:1", "spot:BTCUSDT:2", "spot:BTCUSDT:3"}
    third = repo.claim_collection("BTCUSDT", "spot", now_ms=NOW+2*repo.INTERVAL_MS)
    repo.store_result("BTCUSDT", "spot", third, payload((), threshold=200_000), now_ms=NOW+2*repo.INTERVAL_MS)
    assert repo.read_snapshot("BTCUSDT", "spot", now_ms=NOW+2*repo.INTERVAL_MS)["items"] == []


def test_recent_item_union_is_bounded_and_old_items_expire_on_reads(store):
    token = repo.claim_collection("BTCUSDT", "spot", now_ms=NOW)
    repo.store_result("BTCUSDT", "spot", token, payload(tuple(range(40))), now_ms=NOW)
    assert len(repo.read_snapshot("BTCUSDT", "spot", now_ms=NOW)["items"]) == 30
    assert repo.read_snapshot("BTCUSDT", "spot", now_ms=NOW+600_001)["items"] == []


def test_failure_keeps_good_payload_shared_market_backoff_and_recovery(store):
    first = repo.claim_collection("BTCUSDT", "spot", now_ms=NOW)
    repo.store_result("BTCUSDT", "spot", first, payload(), now_ms=NOW)
    second = repo.claim_collection("BTCUSDT", "spot", now_ms=NOW+repo.INTERVAL_MS)
    assert repo.record_failure("BTCUSDT", "spot", second, error="secret provider response http://private",
                               error_code="429", delay_seconds=90, now_ms=NOW+repo.INTERVAL_MS+1)
    hit = repo.read_snapshot("BTCUSDT", "spot", now_ms=NOW+repo.INTERVAL_MS+2)
    assert hit["items"] and hit["stale"] and hit["collection_status"] == "rate_limited"
    assert "secret" not in str(hit) and "private" not in str(hit)
    assert hit["last_success_ms"] == NOW
    assert repo.claim_collection("ETHUSDT", "spot", now_ms=NOW+repo.INTERVAL_MS+2) is None
    assert repo.claim_collection("ETHUSDT", "futures", now_ms=NOW+repo.INTERVAL_MS+2)
    recovery_at = NOW+repo.INTERVAL_MS+90_001
    token = repo.claim_collection("BTCUSDT", "spot", now_ms=recovery_at)
    assert token
    repo.store_result("BTCUSDT", "spot", token, payload((), now=recovery_at), now_ms=recovery_at)
    recovered = repo.read_snapshot("BTCUSDT", "spot", now_ms=recovery_at)
    assert recovered["collection_status"] == "ready" and not recovered["stale"]


@pytest.mark.parametrize("symbol, market", [("ETHBTC", "spot"), ("BTCUSDT", "options"), ("../../USDT", "spot"), ("USDT", "spot")])
def test_unsupported_pairs_never_create_work(store, symbol, market):
    assert repo.claim_collection(symbol, market, now_ms=NOW) is None
    assert repo.read_snapshot(symbol, market, now_ms=NOW) is None
    with Session(store) as db:
        assert db.exec(select(WhaleTradeState)).all() == []


def test_inactive_pruning_is_bounded_and_preserves_active_or_leased_states(store):
    old = NOW-8*86_400_000
    for symbol in ["BTCUSDT", "ETHUSDT", "SOLUSDT", "DOGEUSDT"]:
        token = repo.claim_collection(symbol, "spot", now_ms=old)
        repo.store_result(symbol, "spot", token, payload((), symbol=symbol, now=old), now_ms=old)
    with Session(store) as db:
        db.add(RunSession(user_id=1, symbol="BTCUSDT", market="spot", started_at=stamp(), last_heartbeat_at=stamp()))
        db.commit()
    assert repo.claim_collection("ETHUSDT", "spot", now_ms=NOW)
    assert repo.prune_inactive_states(now_ms=NOW, limit=1) == 1
    assert repo.prune_inactive_states(now_ms=NOW, limit=1) == 1
    assert repo.prune_inactive_states(now_ms=NOW) == 0
    assert repo.read_snapshot("BTCUSDT", "spot", now_ms=NOW)
    assert repo.read_snapshot("ETHUSDT", "spot", now_ms=NOW)


def test_configured_shared_db_cannot_authorize_local_fallback(store, monkeypatch):
    monkeypatch.setenv("DATABASE_URL", "https://invalid-shared-db.invalid")
    with pytest.raises(RuntimeError, match="shared database"):
        repo.claim_collection("BTCUSDT", "spot", now_ms=NOW)


def test_rate_limited_collector_code_blocks_all_pairs_but_does_not_expose_claims(store):
    token = repo.claim_collection("BTCUSDT", "spot", now_ms=NOW)
    assert repo.record_failure("BTCUSDT", "spot", token, error_code="rate_limited",
                               delay_seconds=300, now_ms=NOW+1)
    assert repo.provider_cooldown_remaining("spot", now_ms=NOW+2) == 299_999
    assert repo.claim_collection("ETHUSDT", "spot", now_ms=NOW+2) is None
    hit = repo.read_snapshot("BTCUSDT", "spot", now_ms=NOW+2)
    assert hit["status"] == "unavailable" and hit["collection_status"] == "rate_limited"
    assert hit["last_success_ms"] == 0
    assert "claim_token" not in hit and "claimed_ms" not in hit and "payload_json" not in hit


def test_failure_backoff_is_shared_durable_and_cleared_by_success(store):
    now = NOW
    for attempt in range(1, 4):
        token = repo.claim_collection("BTCUSDT", "spot", now_ms=now)
        assert token
        repo.record_failure("BTCUSDT", "spot", token, error_code="network_error", now_ms=now+1)
        hit = repo.read_snapshot("BTCUSDT", "spot", now_ms=now+2)
        assert hit["next_collection_ms"] == now+1+repo.INTERVAL_MS*(2**(attempt-1))
        assert repo.claim_collection("BTCUSDT", "spot", now_ms=hit["next_collection_ms"]-1) is None
        now = hit["next_collection_ms"]
    token = repo.claim_collection("BTCUSDT", "spot", now_ms=now)
    repo.store_result("BTCUSDT", "spot", token, payload((), now=now), now_ms=now+1)
    with Session(store) as db:
        row = db.get(WhaleTradeState, "spot:BTCUSDT")
        assert row.consecutive_failures == 0 and row.last_error == "" and row.error_code == ""


def test_storage_rejects_invalid_identity_and_unbounded_payload(store):
    token = repo.claim_collection("BTCUSDT", "spot", now_ms=NOW)
    with pytest.raises(ValueError, match="payload"):
        repo.store_result("BTCUSDT", "spot", token, payload(symbol="ETHUSDT"), now_ms=NOW+1)
    with pytest.raises(ValueError, match="storage limit"):
        repo.store_result("BTCUSDT", "spot", token, {**payload(), "disclaimer": "x"*128_000}, now_ms=NOW+1)
    with pytest.raises(ValueError, match="threshold"):
        repo.store_result("BTCUSDT", "spot", token, payload(threshold=float("nan")), now_ms=NOW+1)
    assert repo.store_result("BTCUSDT", "spot", token, payload(), now_ms=NOW+2)
