"""Durable claims and neutral, consecutive top-holder balance observations."""
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timezone
from threading import Barrier

import pytest
from sqlalchemy import BigInteger, event
from sqlmodel import Session, SQLModel, create_engine, select
from app import db as db_mod
from app.agent_features.whale_activity import onchain_repository as repo
from app.db import OnchainHolderState

NOW = 1_800_000_000_000
STEP = 600_000


def stamp(millis=NOW):
    return datetime.fromtimestamp(millis / 1000, timezone.utc).isoformat()


def payload(balances=None, *, coin="PEPE", now=NOW):
    balances = {"0xabc": 100, "0xdef": 200} if balances is None else balances
    return {"coin": coin, "source": "xrpscan" if coin == "XRP" else "blockscout",
            "source_label": "XRPScan" if coin == "XRP" else "Blockscout",
            "source_url": "https://api.xrpscan.com/api/v1/balances" if coin == "XRP" else "https://eth.blockscout.com/api/",
            "observed_at": stamp(now), "holders": [{"wallet": key, "balance": str(value)} for key, value in balances.items()],
            "tracked_count": len(balances), "excluded_count": 0, "fetched_count": 50,
            "daily_source": coin == "XRP", "http_status": 200, "elapsed_ms": 100,
            "scope": "Top holder balances; transfers are not trades"}


@pytest.fixture
def store(tmp_path, monkeypatch):
    engine = create_engine(f"sqlite:///{tmp_path / 'onchain.db'}", connect_args={"check_same_thread": False})
    SQLModel.metadata.create_all(engine)
    monkeypatch.setattr(repo, "get_session", lambda: Session(engine))
    yield engine
    engine.dispose()


def observe(coin="PEPE", *, now=NOW, balances=None):
    token = repo.claim_collection(coin, now_ms=now)
    assert token
    assert repo.store_result(coin, token, payload(balances, coin=coin, now=now), now_ms=now)
    return repo.read_snapshot(coin, now_ms=now)


def test_fixed_discovery_needs_no_macro_and_schedules_by_source_ttl(store):
    assert repo.discover_coins(now_ms=NOW) == ["PEPE", "WETH", "XRP"]
    observe()
    observe("XRP")
    assert repo.discover_coins(now_ms=NOW + 1) == ["WETH"]
    assert repo.discover_coins(now_ms=NOW + STEP) == ["WETH", "PEPE"]
    assert repo.discover_coins(now_ms=NOW + 21_600_000) == ["WETH", "PEPE", "XRP"]


def test_two_collectors_only_authorize_one_source_request(store):
    barrier = Barrier(2)
    def claim():
        barrier.wait(timeout=5)
        return repo.claim_collection("PEPE", now_ms=NOW)
    with ThreadPoolExecutor(max_workers=2) as pool:
        results = [future.result(timeout=10) for future in [pool.submit(claim), pool.submit(claim)]]
    assert sum(bool(token) for token in results) == 1


def test_claim_recovery_fences_late_success_and_provider_cooldown(store):
    old = repo.claim_collection("PEPE", now_ms=NOW)
    assert repo.claim_collection("PEPE", now_ms=NOW + repo.LEASE_MS - 1) is None
    new = repo.claim_collection("PEPE", now_ms=NOW + repo.LEASE_MS)
    assert new and new != old
    assert not repo.store_result("PEPE", old, payload(), now_ms=NOW + repo.LEASE_MS + 1)
    assert not repo.record_failure("PEPE", old, error_code="429", now_ms=NOW + repo.LEASE_MS + 1)
    assert repo.claim_collection("WETH", now_ms=NOW + repo.LEASE_MS + 1)
    assert repo.store_result("PEPE", new, payload(now=NOW + repo.LEASE_MS + 1), now_ms=NOW + repo.LEASE_MS + 1)


def test_expired_claim_cannot_store_without_new_owner(store):
    token = repo.claim_collection("PEPE", now_ms=NOW)
    assert not repo.store_result("PEPE", token, payload(), now_ms=NOW + repo.LEASE_MS)
    assert not repo.record_failure("PEPE", token, now_ms=NOW + repo.LEASE_MS)


def test_baseline_nochange_quiet_and_readonly_without_wallets_or_claims(store):
    assert repo.read_snapshot("PEPE", now_ms=NOW) is None
    first = observe()
    assert first["status"] == "baseline" and first["items"] == []
    second = observe(now=NOW + STEP)
    assert second["status"] == "ready" and second["items"] == []
    statements = []
    def trace(_conn, _cursor, statement, *_args):
        statements.append(statement)
    event.listen(store, "before_cursor_execute", trace)
    try:
        hit = repo.read_snapshot("PEPE", now_ms=NOW + STEP + 1)
    finally:
        event.remove(store, "before_cursor_execute", trace)
    assert all(statement.startswith("SELECT") for statement in statements)
    assert hit["tracked_count"] == 2 and hit["source_label"] == "Blockscout"
    assert not {"holders", "holders_json", "claim_token", "claimed_ms", "payload_json"}.intersection(hit)
    assert "0xabc" not in str(hit)


def test_exact_giant_integer_delta_ignores_entrants_and_departures(store):
    giant = 10**45
    observe(balances={"a": giant, "b": giant, "departed": giant})
    hit = observe(now=NOW + STEP, balances={"a": giant + 1, "b": giant - 1, "new": giant * 2})
    assert len(hit["items"]) == 1
    change = hit["items"][0]
    assert change["id"] == "onchain:PEPE:2"
    assert change["increased_count"] == 1 and change["decreased_count"] == 1
    assert change["compared_count"] == 2 and change["tracked_count"] == 3
    assert change["previous_observed_at"] == stamp()
    assert not {"buys", "sells", "mood"}.intersection(change)
    retained = observe(now=NOW + 2*STEP, balances={"a": giant + 1, "b": giant - 1, "new": giant * 2})
    assert retained["items"] == hit["items"]
    assert repo.read_snapshot("PEPE", now_ms=NOW + 3*STEP + 1)["items"] == []


def test_reentered_wallet_has_no_stale_baseline(store):
    observe(balances={"a": 100, "b": 200})
    assert observe(now=NOW + STEP, balances={"b": 200, "c": 300})["items"] == []
    assert observe(now=NOW + STEP*2, balances={"a": 999, "b": 200})["items"] == []


def test_long_gap_and_zero_intersection_reset_baseline(store):
    observe(balances={"a": 100})
    long_gap = observe(now=NOW + 3*STEP + 1, balances={"a": 999})
    assert long_gap["status"] == "baseline" and long_gap["items"] == []
    swapped = observe(now=NOW + 4*STEP + 1, balances={"b": 999})
    assert swapped["status"] == "baseline" and swapped["items"] == []


def test_xrp_wallet_case_preserved(store):
    observe("XRP", balances={"rAbC": 100, "raBc": 200})
    hit = observe("XRP", now=NOW + 21_600_000, balances={"rAbC": 101, "raBc": 199})
    assert hit["items"][0]["increased_count"] == 1
    assert hit["items"][0]["decreased_count"] == 1
    assert hit["items"][0]["daily_source"] is True


def test_failure_retains_gooddata_with_shared_provider_backoff(store):
    observe()
    changed = observe(now=NOW + STEP, balances={"0xabc": 101, "0xdef": 200})
    token = repo.claim_collection("PEPE", now_ms=NOW + STEP*2)
    assert repo.record_failure("PEPE", token, error_code="429", delay_seconds=900, now_ms=NOW + STEP*2)
    hit = repo.read_snapshot("PEPE", now_ms=NOW + STEP*2)
    assert hit["items"] == changed["items"]
    assert hit["last_success_ms"] == NOW + STEP and hit["stale"]
    assert hit["collection_status"] == "rate_limited" and hit["error_code"] == "429"
    assert repo.claim_collection("WETH", now_ms=NOW + STEP*2) is None
    assert repo.discover_coins(now_ms=NOW + STEP*2) == ["XRP"]
    assert repo.claim_collection("XRP", now_ms=NOW + STEP*2)
    assert repo.claim_collection("WETH", now_ms=NOW + STEP*2 + 900_000)


def test_shared_database_configuration_cannot_fallback_to_local(store, monkeypatch):
    monkeypatch.setenv("DATABASE_URL", "postgresql://private.invalid")
    with pytest.raises(RuntimeError, match="shared database"):
        repo.claim_collection("PEPE", now_ms=NOW)


def test_unsupported_coin_never_creates_work(store):
    assert repo.claim_collection("CHIP", now_ms=NOW) is None
    assert repo.read_snapshot("CHIP", now_ms=NOW) is None
    with Session(store) as db:
        assert db.exec(select(OnchainHolderState)).all() == []


@pytest.mark.parametrize("holders", [[{"wallet": "same", "balance": "1"}]*2,
    [{"wallet": "x", "balance": "-1"}], [{"wallet": "x", "balance": 1.0}],
    [{"wallet": "x", "balance": "1.1"}], [{"wallet": "x", "balance": "NaN"}],
    [{"wallet": str(i), "balance": "1"} for i in range(101)]])
def test_malformed_observation_cannot_destroy_baseline(store, holders):
    observe()
    token = repo.claim_collection("PEPE", now_ms=NOW + STEP)
    with pytest.raises(ValueError):
        repo.store_result("PEPE", token, {**payload(now=NOW + STEP), "holders": holders}, now_ms=NOW + STEP)
    assert repo.read_snapshot("PEPE", now_ms=NOW + STEP)["last_success_ms"] == NOW


def test_future_stale_and_wrong_source_payloads_rejected(store):
    token = repo.claim_collection("PEPE", now_ms=NOW)
    for patch in [{"coin": "XRP"}, {"source": "evil"}, {"observed_at": stamp(NOW + 60_000)},
                  {"observed_at": stamp(NOW - 120_000)}, {"source_url": "https://eth.blockscout.com/api?apikey=secret"}]:
        with pytest.raises(ValueError):
            repo.store_result("PEPE", token, {**payload(), **patch}, now_ms=NOW)


def test_schema_bigint_clocks_private_table_and_noop_migrations():
    table = OnchainHolderState.__table__
    assert table.c.state_key.primary_key
    clocks = {col.name for col in table.columns if col.name.endswith("_ms")} | {"observation_seq"}
    assert all(isinstance(table.c[name].type, BigInteger) for name in clocks)
    assert not {"user_id", "session_id"}.intersection(table.c.keys())
    assert table.name in db_mod._PG_PRIVATE_CACHE_TABLES
    state = {"tables": {table.name: True}, "columns": {(table.name, col.name): "bigint" if col.name in clocks else "text" for col in table.columns}, "indexes": set(db_mod._PG_INDEXES), "grants": set()}
    assert db_mod._pg_migration_statements(state) == []
    state["tables"][table.name] = False
    state["grants"] = {(table.name, "anon")}
    assert db_mod._pg_migration_statements(state) == ["ALTER TABLE onchainholderstate ENABLE ROW LEVEL SECURITY", "REVOKE ALL PRIVILEGES ON TABLE onchainholderstate FROM anon"]


def test_valid_all_excluded_sample_is_quiet_baseline_and_stops_old_comparison(store):
    observe()
    token = repo.claim_collection("PEPE", now_ms=NOW + STEP)
    empty = {**payload({}, now=NOW + STEP), "fetched_count": 1, "excluded_count": 1}
    assert repo.store_result("PEPE", token, empty, now_ms=NOW + STEP)
    hit = repo.read_snapshot("PEPE", now_ms=NOW + STEP)
    assert hit["status"] == "baseline" and hit["tracked_count"] == 0 and hit["items"] == []
    assert observe(now=NOW + STEP*2, balances={"0xabc": 999})["status"] == "baseline"


def test_unexplained_empty_sample_cannot_clear_existing_baseline(store):
    observe()
    token = repo.claim_collection("PEPE", now_ms=NOW + STEP)
    with pytest.raises(ValueError):
        repo.store_result("PEPE", token, payload({}, now=NOW + STEP), now_ms=NOW + STEP)


def test_safe_source_error_codes_are_preserved_and_unknown_messages_redacted(store):
    now = NOW
    for code in ["upstream_error", "empty_response", "private upstream key=secret"]:
        token = repo.claim_collection("PEPE", now_ms=now)
        assert repo.record_failure("PEPE", token, error_code=code, now_ms=now)
        hit = repo.read_snapshot("PEPE", now_ms=now)
        assert hit["error_code"] == (code if code != "private upstream key=secret" else "collector_error")
        assert "secret" not in str(hit)
        now = hit["next_collection_ms"]


def test_shared_error_backoff_resets_on_success(store):
    now = NOW
    for attempt in range(3):
        token = repo.claim_collection("PEPE", now_ms=now)
        repo.record_failure("PEPE", token, error_code="network_error", now_ms=now)
        hit = repo.read_snapshot("PEPE", now_ms=now)
        assert hit["next_collection_ms"] == now + 60_000*(2**attempt)
        assert repo.claim_collection("PEPE", now_ms=hit["next_collection_ms"]-1) is None
        now = hit["next_collection_ms"]
    observe(now=now)
    with Session(store) as db:
        row = db.get(OnchainHolderState, "PEPE")
        assert row.consecutive_failures == 0 and row.error_code == ""


def test_different_source_scope_resets_instead_of_diffing_incomparable_data(store):
    observe()
    token = repo.claim_collection("PEPE", now_ms=NOW + STEP)
    changed = {**payload({"0xabc": 999}, now=NOW + STEP), "scope": "Replacement token contract"}
    assert repo.store_result("PEPE", token, changed, now_ms=NOW + STEP)
    hit = repo.read_snapshot("PEPE", now_ms=NOW + STEP)
    assert hit["status"] == "baseline" and hit["items"] == []
