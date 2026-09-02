"""Central ticker collector contracts without Prefect, DB, AI, or network."""
from __future__ import annotations

from dataclasses import dataclass

from app.agent_features.position_news import collector


@dataclass
class FakeClaim:
    status: str
    snapshot_id: int
    claim_token: str = ""
    news_payload: dict | None = None
    had_usable_analysis: bool = False


class FakeRepository:
    def __init__(self):
        self.by_key = {}
        self.by_id = {}
        self.outcomes = []
        self.failed = []
        self.removed = 0
        self.budget_used = 0

    def claim_snapshot(self, *, snapshot_key, news_payload, **_kwargs):
        existing = self.by_key.get(snapshot_key)
        if existing is not None:
            return FakeClaim(
                "reused" if existing["completed"] else "pending",
                existing["id"],
                news_payload=existing["news"],
                had_usable_analysis=existing["completed"],
            )
        snapshot_id = len(self.by_id) + 1
        token = f"claim-{snapshot_id}"
        row = {
            "id": snapshot_id,
            "key": snapshot_key,
            "news": news_payload,
            "analysis": None,
            "completed": False,
            "claim_token": token,
        }
        self.by_key[snapshot_key] = row
        self.by_id[snapshot_id] = row
        return FakeClaim(
            "claimed",
            snapshot_id,
            claim_token=token,
            news_payload=news_payload,
        )

    def reserve_ai_budget(self, *, daily_limit, **_kwargs):
        if self.budget_used >= daily_limit:
            return False
        self.budget_used += 1
        return True

    def release_usable_claim(self, snapshot_id, claim_token):
        row = self.by_id[snapshot_id]
        if row["claim_token"] != claim_token:
            return False
        row["claim_token"] = ""
        return True

    def complete_snapshot(self, snapshot_id, analysis, *, claim_token, **_kwargs):
        row = self.by_id[snapshot_id]
        if row["claim_token"] != claim_token:
            return False
        row["analysis"] = analysis
        row["completed"] = True
        row["claim_token"] = ""
        return True

    def fail_snapshot(self, snapshot_id, error, *, claim_token, **_kwargs):
        row = self.by_id[snapshot_id]
        if row["claim_token"] != claim_token:
            return False
        self.failed.append((snapshot_id, error))
        row["completed"] = False
        row["claim_token"] = ""
        return True

    def mark_collection_outcome(self, asset, status, **kwargs):
        self.outcomes.append((asset, status, kwargs.get("error", "")))

    def discover_tracked_symbols(self):
        return []

    def prune_snapshots(self, **_kwargs):
        return self.removed


def _payload(symbol, title="현물 ETF 승인"):
    return {
        "symbol": symbol,
        "coin_name": symbol,
        "query": f"{symbol} 코인 when:7d",
        "updated_at": "2026-08-20T00:00:00Z",
        "refresh_seconds": 300,
        "items": [{
            "title": f"{symbol} {title}",
            "source": "테스트뉴스",
            "url": f"https://news.example/{symbol}",
            "published": "2026-08-20T00:00:00Z",
        }],
    }


def _analysis(items, _coin_name, *, allow_ai=True):
    return {
        "overview": "공용 헤드라인 요약",
        "items": [{
            "sentiment": "positive",
            "reason": "긍정 헤드라인",
            "confidence": "medium",
        } for _ in items],
        "analysis_status": "ready" if allow_ai else "rate_limited",
        "analysis_source": "ai" if allow_ai else "rule",
        "ai": allow_ai,
    }


def test_base_fingerprint_ignores_reordering_but_not_asset_or_headline_changes():
    first = [
        {"title": "두 번째", "source": "B"},
        {"title": "첫 번째", "source": "A"},
    ]
    reordered = list(reversed(first))
    changed = [
        {"title": "새 기사", "source": "B"},
        {"title": "첫 번째", "source": "A"},
    ]
    changed_excerpt = [
        {"title": "두 번째", "source": "B", "excerpt": "수정된 기사 설명"},
        {"title": "첫 번째", "source": "A"},
    ]

    assert collector.analysis_fingerprint("BTC", first) == (
        collector.analysis_fingerprint("BTC", reordered)
    )
    assert collector.analysis_fingerprint("BTC", first) != (
        collector.analysis_fingerprint("BTC", changed)
    )
    assert collector.analysis_fingerprint("BTC", first) != (
        collector.analysis_fingerprint("BTC", changed_excerpt)
    )
    assert collector.analysis_fingerprint("CRVUSD", first) != (
        collector.analysis_fingerprint("CRV", first)
    )


def test_same_base_ticker_fetches_and_analyzes_once_per_cycle():
    repo = FakeRepository()
    fetches = []
    analyses = []

    def fetcher(symbol):
        fetches.append(symbol)
        return _payload(symbol)

    def analyzer(items, coin_name, **kwargs):
        analyses.append((coin_name, kwargs["allow_ai"]))
        return _analysis(items, coin_name, **kwargs)

    first = collector.run_collection_cycle(
        repo=repo,
        symbols=["BTCUSDT", "BTCUSDC", "BTC"],
        fetcher=fetcher,
        analyzer=analyzer,
        max_ai_analyses=10,
    )
    second = collector.run_collection_cycle(
        repo=repo,
        symbols=["BTCBUSD"],
        fetcher=fetcher,
        analyzer=analyzer,
        max_ai_analyses=10,
    )

    assert first["ticker_count"] == 1
    assert first["stored"] == 1
    assert second["reused"] == 1
    assert fetches == ["BTC", "BTC"]
    assert len(analyses) == 1
    assert len(repo.by_key) == 1


def test_changed_headlines_create_one_new_shared_analysis():
    repo = FakeRepository()
    title = {"value": "현물 ETF 승인"}

    def fetcher(symbol):
        return _payload(symbol, title["value"])

    calls = []
    analyzer = lambda items, coin_name, **kwargs: (
        calls.append(items[0]["title"])
        or _analysis(items, coin_name, **kwargs)
    )

    collector.run_collection_cycle(
        repo=repo,
        symbols=["ETHUSDT"],
        fetcher=fetcher,
        analyzer=analyzer,
    )
    title["value"] = "거래소 점검 완료"
    collector.run_collection_cycle(
        repo=repo,
        symbols=["ETHUSDT"],
        fetcher=fetcher,
        analyzer=analyzer,
    )

    assert len(calls) == 2
    assert len(repo.by_key) == 2


def test_one_ticker_failure_does_not_stop_other_tickers():
    repo = FakeRepository()

    def fetcher(symbol):
        if symbol == "BTC":
            raise RuntimeError("rss unavailable")
        return _payload(symbol)

    summary = collector.run_collection_cycle(
        repo=repo,
        symbols=["BTCUSDT", "ETHUSDT"],
        fetcher=fetcher,
        analyzer=_analysis,
    )

    assert summary["error"] == 1
    assert summary["stored"] == 1
    assert repo.outcomes == [("BTC", "error", "rss unavailable")]
    assert len(repo.by_key) == 1


def test_cycle_applies_one_central_ai_budget_across_tickers(monkeypatch):
    monkeypatch.setenv("ANTHROPIC_API_KEY", "test-key")
    repo = FakeRepository()
    allowed = []

    def analyzer(items, coin_name, *, allow_ai):
        allowed.append((coin_name, allow_ai))
        return _analysis(items, coin_name, allow_ai=allow_ai)

    summary = collector.run_collection_cycle(
        repo=repo,
        symbols=["BTCUSDT", "ETHUSDT"],
        fetcher=lambda symbol: _payload(symbol),
        analyzer=analyzer,
        max_ai_analyses=1,
    )

    assert allowed == [("BTC", True), ("ETH", False)]
    assert summary["ai_budget_used"] == 1
    assert len(repo.by_key) == 2


def test_empty_feed_keeps_existing_state_untouched():
    repo = FakeRepository()
    result = collector.collect_ticker(
        "SOL",
        repo=repo,
        fetcher=lambda _symbol: {"symbol": "SOL", "items": []},
        analyzer=_analysis,
    )

    assert result["status"] == "empty"
    assert repo.outcomes == [("SOL", "empty", "")]
    assert repo.by_key == {}


def test_base_ticker_window_rotates_instead_of_starving_tail_symbols():
    symbols = ["AAA", "BBB", "CCC", "DDD"]

    first = collector.select_ticker_window(symbols, limit=2, now_ms=0)
    second = collector.select_ticker_window(
        symbols,
        limit=2,
        now_ms=300_000,
    )

    assert first == ["AAA", "BBB"]
    assert second == ["CCC", "DDD"]
