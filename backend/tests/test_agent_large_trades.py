from concurrent.futures import ThreadPoolExecutor
from threading import Event

from app import whales


def test_large_trades_have_correct_taker_direction_and_filter_bad_rows(monkeypatch):
    now = 1_800_000_000.0
    monkeypatch.setattr(whales.time, "time", lambda: now)
    monkeypatch.setattr(whales, "_fetch_aggregate_trades", lambda *_: [
        {"a": 11, "p": "50000", "q": "3", "T": int(now * 1000), "m": False},
        {"a": 12, "p": "50000", "q": "4", "T": int(now * 1000), "m": True},
        {"a": 13, "p": "50000", "q": ".01", "T": int(now * 1000), "m": False},
        {"a": 14, "p": "NaN", "q": "5", "T": int(now * 1000), "m": False},
        {"a": 15, "p": "50000", "q": "5", "T": int((now - 3600) * 1000), "m": False},
    ])
    result = whales.get_large_trade_activity("BTCUSDT")
    assert result["status"] == "ready"
    assert {item["id"]: item["side"] for item in result["items"]} == {
        "spot:BTCUSDT:11": "buy", "spot:BTCUSDT:12": "sell",
    }
    assert result["threshold_quote"] == 100000
    assert result["quote_asset"] == "USDT"


def test_concurrent_accounts_share_one_fetch_and_failure_has_backoff(monkeypatch):
    monkeypatch.setattr(whales, "_large_trade_cache", {})
    calls = []
    entered, release = Event(), Event()

    def fetch(*args):
        calls.append(args)
        entered.set()
        assert release.wait(2)
        raise RuntimeError("provider unavailable")

    monkeypatch.setattr(whales, "_fetch_aggregate_trades", fetch)
    with ThreadPoolExecutor(max_workers=2) as pool:
        first = pool.submit(whales.get_large_trade_activity, "SOLUSDT", "futures")
        assert entered.wait(2)
        second = pool.submit(whales.get_large_trade_activity, "SOLUSDT", "futures")
        release.set()
        assert first.result()["status"] == "unavailable"
        assert second.result()["status"] == "unavailable"
    assert whales.get_large_trade_activity("SOLUSDT", "futures")["status"] == "unavailable"
    assert len(calls) == 1


def test_non_dollar_quote_does_not_use_dollar_threshold(monkeypatch):
    monkeypatch.setattr(whales, "_fetch_aggregate_trades", lambda *_: (_ for _ in ()).throw(AssertionError("must not fetch")))
    assert whales.get_large_trade_activity("ETHBTC")["status"] == "unavailable"
