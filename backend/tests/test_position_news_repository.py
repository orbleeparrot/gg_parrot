"""Durable central-news repository contracts (isolated temporary SQLite)."""
from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor
from threading import Barrier

import pytest

pytest.importorskip("sqlmodel")

from sqlalchemy import delete, event, update
from sqlalchemy.dialects import postgresql
from sqlmodel import Session, SQLModel, create_engine, select

from app.agent_features.position_news import repository
from app.db import (
    NewsTitleTranslation,
    RunSession,
    TickerNewsSnapshot,
    TickerNewsState,
)

@pytest.fixture
def db_engine(tmp_path):
    engine = create_engine(
        f"sqlite:///{tmp_path / 'position-news.db'}",
        connect_args={"check_same_thread": False, "timeout": 30},
    )
    SQLModel.metadata.create_all(engine)
    return engine


@pytest.fixture
def db(db_engine):
    with Session(db_engine) as session:
        yield session


def _news(symbol="BTC", title="비트코인 현물 ETF 승인"):
    return {
        "symbol": symbol,
        "coin_name": symbol,
        "query": f"{symbol} 코인 when:7d",
        "updated_at": "2026-08-20T00:00:00Z",
        "refresh_seconds": 300,
        "items": [
            {
                "title": title,
                "source": "테스트뉴스",
                "url": f"https://news.example/{symbol}",
                "published": "2026-08-20T00:00:00Z",
            }
        ],
    }


def _analysis(status="ready"):
    return {
        "overview": "공용 헤드라인 요약",
        "items": [
            {
                "sentiment": "positive",
                "reason": "긍정 헤드라인",
                "confidence": "medium",
            }
        ],
        "analysis_status": status,
        "analysis_source": "ai" if status == "ready" else "rule",
        "ai": status == "ready",
    }


def test_discovery_is_empty_without_live_sessions(db, monkeypatch):
    monkeypatch.setenv("POSITION_NEWS_PINNED_TICKERS", "00FAKE")
    monkeypatch.setenv("POSITION_NEWS_ALLOWED_ASSETS", "CRV")

    symbols = repository.discover_tracked_symbols(db)

    assert symbols == []


def test_discovery_deduplicates_live_sessions_and_ignores_inactive_sessions(
    db,
    monkeypatch,
):
    monkeypatch.setenv("POSITION_NEWS_ACTIVE_SESSION_SECONDS", "30")
    monkeypatch.setattr(repository.time, "time", lambda: 100.0)
    db.add_all([
        RunSession(
            user_id=1,
            symbol="BMTUSDT",
            position_side="long",
            status="running",
            started_at="1970-01-01T00:01:35Z",
            last_heartbeat_at="1970-01-01T00:01:35Z",
        ),
        RunSession(
            user_id=2,
            symbol="BMTUSDC",
            position_side="short",
            status="running",
            started_at="1970-01-01T00:01:35Z",
            last_heartbeat_at="1970-01-01T00:01:35Z",
        ),
        RunSession(
            user_id=3,
            symbol="ETHUSDT",
            position_side="long",
            status="running",
            started_at="1970-01-01T00:01:30Z",
            last_heartbeat_at="1970-01-01T00:01:30Z",
        ),
        RunSession(
            user_id=4,
            symbol="RUN0USDT",
            position_side="long",
            status="running",
            started_at="1970-01-01T00:00:00Z",
            last_heartbeat_at="1970-01-01T00:00:00Z",
        ),
        RunSession(
            user_id=5,
            symbol="SOLUSDT",
            position_side="long",
            status="stopped",
            started_at="1970-01-01T00:01:35Z",
            last_heartbeat_at="1970-01-01T00:01:35Z",
            stopped_at="1970-01-01T00:01:36Z",
        ),
    ])
    db.commit()

    symbols = repository.discover_tracked_symbols(db)

    assert set(symbols) == {"BMT", "ETH"}
    assert symbols.count("BMT") == 1


def test_discovery_prioritizes_oldest_active_asset(db, monkeypatch):
    monkeypatch.setattr(repository.time, "time", lambda: 1.0)
    db.add_all([
        RunSession(
            user_id=1,
            symbol="BTCUSDT",
            position_side="long",
            status="running",
            started_at="1970-01-01T00:00:00Z",
            last_heartbeat_at="1970-01-01T00:00:00Z",
        ),
        RunSession(
            user_id=2,
            symbol="ETHUSDT",
            position_side="long",
            status="running",
            started_at="1970-01-01T00:00:00Z",
            last_heartbeat_at="1970-01-01T00:00:00Z",
        ),
    ])
    db.add(TickerNewsState(asset_symbol="BTC", last_attempt_ms=500))
    db.add(TickerNewsState(asset_symbol="ETH", last_attempt_ms=100))
    db.commit()

    symbols = repository.discover_tracked_symbols(db)

    assert symbols == ["ETH", "BTC"]


def test_claim_analyze_once_and_read_latest_snapshot(db):
    payload = _news()
    first = repository.claim_snapshot(
        asset_symbol="BTC",
        snapshot_key="snapshot-one",
        news_payload=payload,
        prompt_version="v1",
        model="test-model",
        retry_incomplete=True,
        now_ms=1_000,
        db=db,
    )
    follower = repository.claim_snapshot(
        asset_symbol="BTC",
        snapshot_key="snapshot-one",
        news_payload=payload,
        prompt_version="v1",
        model="test-model",
        retry_incomplete=True,
        now_ms=2_000,
        db=db,
    )

    assert first.status == "claimed"
    assert follower.status == "pending"
    assert follower.snapshot_id == first.snapshot_id

    repository.complete_snapshot(
        first.snapshot_id,
        _analysis(),
        claim_token=first.claim_token,
        now_ms=3_000,
        db=db,
    )
    refreshed_payload = {
        **payload,
        "query": "BTC OR Bitcoin when:30d",
        "updated_at": "2026-08-20T00:05:00Z",
        "sources": [
            {
                "name": "google_news_rss",
                "status": "ready",
                "item_count": 1,
                "fetched_count": 59,
            }
        ],
    }
    reused = repository.claim_snapshot(
        asset_symbol="BTC",
        snapshot_key="snapshot-one",
        news_payload=refreshed_payload,
        prompt_version="v1",
        model="test-model",
        retry_incomplete=True,
        now_ms=4_000,
        db=db,
    )
    stored = repository.get_latest_snapshot("BTC", db)

    assert reused.status == "reused"
    assert stored["snapshot_id"] == "snapshot-one"
    assert stored["analysis"]["analysis_status"] == "ready"
    assert stored["news_payload"]["query"] == "BTC OR Bitcoin when:30d"
    assert stored["news_payload"]["sources"][0]["fetched_count"] == 59
    assert stored["collection"]["last_success_ms"] == 4_000
    assert len(db.exec(select(TickerNewsSnapshot)).all()) == 1


def test_failure_and_empty_attempt_preserve_last_good_pointer(db):
    payload = _news()
    claim = repository.claim_snapshot(
        asset_symbol="BTC",
        snapshot_key="good",
        news_payload=payload,
        prompt_version="v1",
        model="test-model",
        retry_incomplete=True,
        now_ms=1_000,
        db=db,
    )
    repository.complete_snapshot(
        claim.snapshot_id,
        _analysis(),
        claim_token=claim.claim_token,
        now_ms=2_000,
        db=db,
    )

    failed = repository.claim_snapshot(
        asset_symbol="BTC",
        snapshot_key="failed",
        news_payload=_news(title="새 헤드라인"),
        prompt_version="v1",
        model="test-model",
        retry_incomplete=True,
        now_ms=3_000,
        db=db,
    )
    repository.fail_snapshot(
        failed.snapshot_id,
        "analysis unavailable",
        claim_token=failed.claim_token,
        now_ms=4_000,
        db=db,
    )
    repository.mark_collection_outcome(
        "BTC",
        "empty",
        now_ms=5_000,
        db=db,
    )

    stored = repository.get_latest_snapshot("BTC", db)
    state = db.get(TickerNewsState, "BTC")
    assert stored["snapshot_id"] == "good"
    assert state.latest_snapshot_id == claim.snapshot_id
    assert state.collection_status == "empty"
    assert state.observation_seq == 3


def test_prune_removes_old_history_but_never_latest_pointer(db):
    first = repository.claim_snapshot(
        asset_symbol="BTC",
        snapshot_key="old",
        news_payload=_news(),
        prompt_version="v1",
        model="test-model",
        retry_incomplete=True,
        now_ms=1_000,
        db=db,
    )
    repository.complete_snapshot(
        first.snapshot_id,
        _analysis(),
        claim_token=first.claim_token,
        now_ms=2_000,
        db=db,
    )

    latest = repository.claim_snapshot(
        asset_symbol="BTC",
        snapshot_key="latest",
        news_payload=_news(title="두 번째 헤드라인"),
        prompt_version="v1",
        model="test-model",
        retry_incomplete=True,
        now_ms=3_000,
        db=db,
    )
    repository.complete_snapshot(
        latest.snapshot_id,
        _analysis(),
        claim_token=latest.claim_token,
        now_ms=4_000,
        db=db,
    )

    removed = repository.prune_snapshots(
        retention_days=1,
        now_ms=200_000_000,
        db=db,
    )

    assert removed == 1
    rows = db.exec(select(TickerNewsSnapshot)).all()
    assert [row.snapshot_key for row in rows] == ["latest"]


def test_reclaimed_lease_fences_late_owner_complete_and_fail(db):
    first = repository.claim_snapshot(
        asset_symbol="BTC",
        snapshot_key="lease",
        news_payload=_news(),
        prompt_version="v1",
        model="test-model",
        retry_incomplete=True,
        now_ms=1_000,
        db=db,
    )
    reclaimed = repository.claim_snapshot(
        asset_symbol="BTC",
        snapshot_key="lease",
        news_payload=_news(),
        prompt_version="v1",
        model="test-model",
        retry_incomplete=True,
        now_ms=400_000,
        db=db,
    )

    assert reclaimed.status == "claimed"
    assert reclaimed.claim_token != first.claim_token
    assert repository.complete_snapshot(
        reclaimed.snapshot_id,
        _analysis(),
        claim_token=reclaimed.claim_token,
        now_ms=401_000,
        db=db,
    )
    assert repository.complete_snapshot(
        first.snapshot_id,
        _analysis("degraded"),
        claim_token=first.claim_token,
        now_ms=402_000,
        db=db,
    ) is False
    assert repository.fail_snapshot(
        first.snapshot_id,
        "late owner",
        claim_token=first.claim_token,
        now_ms=403_000,
        db=db,
    ) is False
    assert repository.get_latest_snapshot("BTC", db)["analysis"]["analysis_status"] == "ready"


def test_failed_refresh_keeps_last_usable_analysis(db):
    initial = repository.claim_snapshot(
        asset_symbol="ETH",
        snapshot_key="degraded",
        news_payload=_news(symbol="ETH"),
        prompt_version="v1",
        model="test-model",
        retry_incomplete=True,
        now_ms=1_000,
        db=db,
    )
    repository.complete_snapshot(
        initial.snapshot_id,
        _analysis("degraded"),
        claim_token=initial.claim_token,
        now_ms=2_000,
        db=db,
    )
    refresh = repository.claim_snapshot(
        asset_symbol="ETH",
        snapshot_key="degraded",
        news_payload=_news(symbol="ETH"),
        prompt_version="v1",
        model="test-model",
        retry_incomplete=True,
        now_ms=400_000,
        db=db,
    )

    assert refresh.status == "claimed"
    assert refresh.had_usable_analysis is True
    assert repository.get_latest_snapshot("ETH", db) is not None
    repository.fail_snapshot(
        refresh.snapshot_id,
        "temporary AI failure",
        claim_token=refresh.claim_token,
        now_ms=401_000,
        db=db,
    )
    stored = repository.get_latest_snapshot("ETH", db)
    assert stored is not None
    assert stored["analysis"]["analysis_status"] == "degraded"


def test_late_old_snapshot_cannot_roll_latest_pointer_back(db):
    old = repository.claim_snapshot(
        asset_symbol="SOL",
        snapshot_key="old-generation",
        news_payload=_news(symbol="SOL", title="이전 기사"),
        prompt_version="v1",
        model="test-model",
        retry_incomplete=True,
        now_ms=1_000,
        db=db,
    )
    new = repository.claim_snapshot(
        asset_symbol="SOL",
        snapshot_key="new-generation",
        news_payload=_news(symbol="SOL", title="최신 기사"),
        prompt_version="v1",
        model="test-model",
        retry_incomplete=True,
        now_ms=2_000,
        db=db,
    )

    repository.complete_snapshot(
        new.snapshot_id,
        _analysis(),
        claim_token=new.claim_token,
        now_ms=3_000,
        db=db,
    )
    repository.complete_snapshot(
        old.snapshot_id,
        _analysis(),
        claim_token=old.claim_token,
        now_ms=4_000,
        db=db,
    )

    assert repository.get_latest_snapshot("SOL", db)["snapshot_id"] == "new-generation"


def test_same_millisecond_observations_keep_newer_sequence_latest(db):
    old = repository.claim_snapshot(
        asset_symbol="SOL",
        snapshot_key="same-ms-old",
        news_payload=_news(symbol="SOL", title="이전 기사"),
        prompt_version="v1",
        model="test-model",
        retry_incomplete=True,
        now_ms=1_000,
        db=db,
    )
    new = repository.claim_snapshot(
        asset_symbol="SOL",
        snapshot_key="same-ms-new",
        news_payload=_news(symbol="SOL", title="최신 기사"),
        prompt_version="v1",
        model="test-model",
        retry_incomplete=True,
        now_ms=1_000,
        db=db,
    )

    old_row = db.get(TickerNewsSnapshot, old.snapshot_id)
    new_row = db.get(TickerNewsSnapshot, new.snapshot_id)
    assert old_row.last_observation_seq < new_row.last_observation_seq

    repository.complete_snapshot(
        new.snapshot_id,
        _analysis(),
        claim_token=new.claim_token,
        now_ms=2_000,
        db=db,
    )
    repository.complete_snapshot(
        old.snapshot_id,
        _analysis(),
        claim_token=old.claim_token,
        now_ms=3_000,
        db=db,
    )

    state = db.get(TickerNewsState, "SOL")
    assert repository.get_latest_snapshot("SOL", db)["snapshot_id"] == "same-ms-new"
    assert state.latest_observation_seq == new_row.last_observation_seq


def test_prune_keeps_recently_reobserved_history(db):
    old = repository.claim_snapshot(
        asset_symbol="ADA",
        snapshot_key="recently-observed",
        news_payload=_news(symbol="ADA", title="이전 기사"),
        prompt_version="v1",
        model="test-model",
        retry_incomplete=True,
        now_ms=1_000,
        db=db,
    )
    repository.complete_snapshot(
        old.snapshot_id,
        _analysis(),
        claim_token=old.claim_token,
        now_ms=2_000,
        db=db,
    )
    latest = repository.claim_snapshot(
        asset_symbol="ADA",
        snapshot_key="current",
        news_payload=_news(symbol="ADA", title="최신 기사"),
        prompt_version="v1",
        model="test-model",
        retry_incomplete=True,
        now_ms=3_000,
        db=db,
    )
    repository.complete_snapshot(
        latest.snapshot_id,
        _analysis(),
        claim_token=latest.claim_token,
        now_ms=4_000,
        db=db,
    )
    db.exec(
        update(TickerNewsSnapshot)
        .where(TickerNewsSnapshot.id == old.snapshot_id)
        .values(last_observed_ms=199_000_000)
    )
    db.commit()

    removed = repository.prune_snapshots(
        retention_days=1,
        now_ms=200_000_000,
        db=db,
    )

    assert removed == 0
    assert db.get(TickerNewsSnapshot, old.snapshot_id) is not None


def test_reuse_never_points_at_snapshot_deleted_before_observe(
    db,
    monkeypatch,
):
    old = repository.claim_snapshot(
        asset_symbol="LINK",
        snapshot_key="prune-race",
        news_payload=_news(symbol="LINK", title="재등장 기사"),
        prompt_version="v1",
        model="test-model",
        retry_incomplete=True,
        now_ms=1_000,
        db=db,
    )
    repository.complete_snapshot(
        old.snapshot_id,
        _analysis(),
        claim_token=old.claim_token,
        now_ms=2_000,
        db=db,
    )
    latest = repository.claim_snapshot(
        asset_symbol="LINK",
        snapshot_key="current-link",
        news_payload=_news(symbol="LINK", title="현재 기사"),
        prompt_version="v1",
        model="test-model",
        retry_incomplete=True,
        now_ms=3_000,
        db=db,
    )
    repository.complete_snapshot(
        latest.snapshot_id,
        _analysis(),
        claim_token=latest.claim_token,
        now_ms=4_000,
        db=db,
    )

    original_observe = repository._observe_snapshot
    pruned = False

    def prune_before_observe(session, snapshot_id, **kwargs):
        nonlocal pruned
        if snapshot_id == old.snapshot_id and not pruned:
            result = session.exec(
                delete(TickerNewsSnapshot).where(
                    TickerNewsSnapshot.id == snapshot_id
                )
            )
            assert result.rowcount == 1
            session.commit()
            pruned = True
            return False
        return original_observe(session, snapshot_id, **kwargs)

    monkeypatch.setattr(
        repository,
        "_observe_snapshot",
        prune_before_observe,
    )
    replacement = repository.claim_snapshot(
        asset_symbol="LINK",
        snapshot_key="prune-race",
        news_payload=_news(symbol="LINK", title="재등장 기사"),
        prompt_version="v1",
        model="test-model",
        retry_incomplete=True,
        now_ms=5_000,
        db=db,
    )

    state = db.get(TickerNewsState, "LINK")
    assert replacement.status == "claimed"
    assert replacement.snapshot_id != old.snapshot_id
    assert state.latest_snapshot_id == latest.snapshot_id
    assert db.get(TickerNewsSnapshot, state.latest_snapshot_id) is not None


def test_daily_ai_budget_is_durable_and_resets_on_next_kst_day(db):
    assert repository.reserve_ai_budget(daily_limit=2, now_ms=0, db=db)
    assert repository.reserve_ai_budget(daily_limit=2, now_ms=1, db=db)
    assert repository.reserve_ai_budget(daily_limit=2, now_ms=2, db=db) is False
    assert repository.reserve_ai_budget(
        daily_limit=2,
        now_ms=86_400_000,
        db=db,
    )


def test_daily_ai_budget_namespaces_have_independent_limits(db):
    assert repository.reserve_ai_budget(
        daily_limit=1,
        namespace="position_analysis",
        now_ms=0,
        db=db,
    )
    assert repository.reserve_ai_budget(
        daily_limit=1,
        namespace="position_analysis",
        now_ms=1,
        db=db,
    ) is False
    assert repository.reserve_ai_budget(
        daily_limit=1,
        namespace="another_analysis",
        now_ms=1,
        db=db,
    )


def test_title_translations_are_persisted_and_reused_by_original_title(db):
    translations = {
        "Arbitrum token soars": "아비트럼 토큰 급등",
        "Bitcoin holds $70,000": "비트코인 $70,000 유지",
    }

    repository.store_title_translations(translations, now_ms=1_000, db=db)

    assert repository.get_title_translations(
        ["Arbitrum token soars", "missing title", "Bitcoin holds $70,000"],
        db=db,
    ) == translations


def test_title_translation_claim_prevents_a_second_worker_from_claiming(db_engine):
    title = "Arbitrum token soars"
    with Session(db_engine) as first_db:
        first = repository.claim_title_translations(
            [title],
            now_ms=1_000,
            db=first_db,
        )
    with Session(db_engine) as second_db:
        second = repository.claim_title_translations(
            [title],
            now_ms=1_001,
            db=second_db,
        )

    assert first["claimed"] == [title]
    assert first["waiting"] == []
    assert first["claim_token"]
    assert second["claimed"] == []
    assert second["waiting"] == [title]

    with Session(db_engine) as first_db:
        repository.store_title_translations(
            {title: "아비트럼 토큰 급등"},
            claim_token=first["claim_token"],
            now_ms=1_002,
            db=first_db,
        )
    with Session(db_engine) as second_db:
        assert repository.get_title_translations([title], db=second_db) == {
            title: "아비트럼 토큰 급등",
        }


def test_title_translation_claim_renewal_is_token_fenced(db):
    title = "Arbitrum token soars"
    claim = repository.claim_title_translations([title], now_ms=1_000, db=db)

    assert repository.renew_title_translation_claims(
        [title],
        claim_token="not-the-owner",
        now_ms=2_000,
        db=db,
    ) is False
    assert repository.renew_title_translation_claims(
        [title],
        claim_token=claim["claim_token"],
        now_ms=3_000,
        db=db,
    ) is True

    row = db.get(NewsTitleTranslation, repository._title_hash(title))
    assert row is not None
    assert row.claimed_ms == 3_000


def test_concurrent_stale_reclaim_grants_one_fenced_owner(db, db_engine):
    initial = repository.claim_snapshot(
        asset_symbol="XRP",
        snapshot_key="concurrent-lease",
        news_payload=_news(symbol="XRP"),
        prompt_version="v1",
        model="test-model",
        retry_incomplete=True,
        now_ms=1_000,
        db=db,
    )
    assert initial.status == "claimed"
    barrier = Barrier(2)

    def reclaim():
        with Session(db_engine) as session:
            barrier.wait()
            claim = repository.claim_snapshot(
                asset_symbol="XRP",
                snapshot_key="concurrent-lease",
                news_payload=_news(symbol="XRP"),
                prompt_version="v1",
                model="test-model",
                retry_incomplete=True,
                now_ms=400_000,
                db=session,
            )
            return claim.status, claim.claim_token

    with ThreadPoolExecutor(max_workers=2) as pool:
        results = list(pool.map(lambda _index: reclaim(), range(2)))

    assert [status for status, _token in results].count("claimed") == 1
    assert sum(bool(token) for _status, token in results) == 1


def test_concurrent_daily_budget_never_exceeds_shared_limit(db_engine):
    workers = 12
    barrier = Barrier(workers)

    def reserve():
        with Session(db_engine) as session:
            barrier.wait()
            return repository.reserve_ai_budget(
                daily_limit=4,
                now_ms=1_000,
                db=session,
            )

    with ThreadPoolExecutor(max_workers=workers) as pool:
        reservations = list(pool.map(lambda _index: reserve(), range(workers)))

    assert sum(reservations) == 4


def test_discovery_ignores_operator_assets_without_live_sessions(db, monkeypatch):
    monkeypatch.setenv("POSITION_NEWS_ALLOWED_ASSETS", "00fake,CRVUSD")

    symbols = repository.discover_tracked_symbols(db)

    assert "00FAKE" not in symbols
    assert "CRVUSD" not in symbols
    assert symbols == []


def test_late_old_failure_cannot_mark_newer_success_as_error(db):
    old = repository.claim_snapshot(
        asset_symbol="LINK",
        snapshot_key="old-failure",
        news_payload=_news(symbol="LINK", title="이전 기사"),
        prompt_version="v1",
        model="test-model",
        retry_incomplete=True,
        now_ms=1_000,
        db=db,
    )
    latest = repository.claim_snapshot(
        asset_symbol="LINK",
        snapshot_key="new-success",
        news_payload=_news(symbol="LINK", title="최신 기사"),
        prompt_version="v1",
        model="test-model",
        retry_incomplete=True,
        now_ms=2_000,
        db=db,
    )
    assert repository.complete_snapshot(
        latest.snapshot_id,
        _analysis(),
        claim_token=latest.claim_token,
        now_ms=3_000,
        db=db,
    )
    assert repository.fail_snapshot(
        old.snapshot_id,
        "late old failure",
        claim_token=old.claim_token,
        now_ms=4_000,
        db=db,
    )

    state = db.get(TickerNewsState, "LINK")
    assert (
        repository.get_latest_snapshot("LINK", db)["snapshot_id"] == "new-success"
    )
    assert state.collection_status == "ready"
    assert state.last_error == ""
    assert state.consecutive_failures == 0


def test_state_lock_compiles_to_postgres_for_update():
    state = TickerNewsState(asset_symbol="BTC")

    class Result:
        def one(self):
            return state

    class RecordingSession:
        statement = None

        def get(self, _model, _key):
            return state

        def exec(self, statement):
            self.statement = statement
            return Result()

    session = RecordingSession()
    repository._lock_state(
        session,
        asset_symbol="BTC",
        now_iso="2026-08-20T00:00:00Z",
    )

    sql = str(session.statement.compile(dialect=postgresql.dialect()))
    assert "FOR UPDATE" in sql.upper()


@pytest.mark.parametrize("operation", ["complete", "fail"])
def test_snapshot_write_paths_lock_state_before_snapshot(
    db,
    db_engine,
    operation,
):
    claim = repository.claim_snapshot(
        asset_symbol="BTC",
        snapshot_key=f"lock-order-{operation}",
        news_payload=_news(),
        prompt_version="v1",
        model="test-model",
        retry_incomplete=True,
        now_ms=1_000,
        db=db,
    )
    statements = []

    def record_sql(
        _conn,
        _cursor,
        statement,
        _parameters,
        _context,
        _executemany,
    ):
        statements.append(statement.strip().lower())

    event.listen(db_engine, "before_cursor_execute", record_sql)
    try:
        if operation == "complete":
            assert repository.complete_snapshot(
                claim.snapshot_id,
                _analysis(),
                claim_token=claim.claim_token,
                now_ms=2_000,
                db=db,
            )
        else:
            assert repository.fail_snapshot(
                claim.snapshot_id,
                "analysis failed",
                claim_token=claim.claim_token,
                now_ms=2_000,
                db=db,
            )
    finally:
        event.remove(db_engine, "before_cursor_execute", record_sql)

    state_select = next(
        index
        for index, statement in enumerate(statements)
        if statement.startswith("select") and "tickernewsstate" in statement
    )
    snapshot_update = next(
        index
        for index, statement in enumerate(statements)
        if statement.startswith("update tickernewssnapshot")
    )
    assert state_select < snapshot_update
