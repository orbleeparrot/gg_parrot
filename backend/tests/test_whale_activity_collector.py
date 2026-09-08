"""Durable claims precede source calls across web bootstrap and Prefect."""
import pytest

from app.agent_features.whale_activity import collector


def test_busy_claim_never_fetches(monkeypatch):
    monkeypatch.setattr(collector.repository, "claim_collection", lambda *_: None)
    assert collector.collect_pair("BTCUSDT", "spot", fetcher=lambda *_: pytest.fail("busy"))["status"] == "skipped"


def test_empty_source_is_success_and_persisted(monkeypatch):
    calls = []
    monkeypatch.setattr(collector.repository, "claim_collection", lambda *_: "fence")
    monkeypatch.setattr(collector.repository, "store_result", lambda *args: calls.append(args) or True)
    result = collector.collect_pair("BTCUSDT", "spot", fetcher=lambda *_: {"status": "empty", "items": [], "sampled_trades": 500})
    assert result["status"] == "empty" and result["sampled_trades"] == 500
    assert calls[0][2] == "fence"


def test_source_failure_releases_claim_with_shared_backoff(monkeypatch):
    from app.whales import LargeTradeSourceError
    calls = []
    monkeypatch.setattr(collector.repository, "claim_collection", lambda *_: "fence")
    monkeypatch.setattr(collector.repository, "record_failure", lambda *args, **kwargs: calls.append(kwargs) or True)
    def fetch(*_):
        raise LargeTradeSourceError("rate_limited", http_status=429, retry_after_seconds=180)
    result = collector.collect_pair("BTCUSDT", "spot", fetcher=fetch)
    assert result["status"] == "error" and result["http_status"] == 429
    assert calls[0]["delay_seconds"] == 180


def test_bootstrap_skips_fresh_prefect_snapshot(monkeypatch):
    options = []
    monkeypatch.setattr(collector.repository, "discover_pairs", lambda **kwargs: options.append(kwargs) or [])
    result = collector.run_collection_cycle(bootstrap_only=True, collect=lambda *_: pytest.fail("fresh snapshot"))
    assert result["collected_count"] == 0
    assert options == [{"due_only": True, "bootstrap_only": True}]


def test_zero_live_pairs_do_not_call_exchange(monkeypatch):
    monkeypatch.setattr(collector.repository, "discover_pairs", lambda **_: [])
    result = collector.run_collection_cycle(collect=lambda *_: pytest.fail("no sessions"))
    assert result["eligible_pair_count"] == 0
