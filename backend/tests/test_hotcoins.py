"""Tests for the '오늘의 경주마' hot-coins aggregator (v5)."""
from __future__ import annotations

from fastapi.testclient import TestClient

from app import hotcoins as hc
from app.main import app

client = TestClient(app)


def _t(symbol, change, price, qvol):
    return {"symbol": symbol, "priceChangePercent": str(change), "lastPrice": str(price), "quoteVolume": str(qvol)}


def test_selection_filters_and_orders():
    tickers = [
        _t("XRPUSDT", 12.0, 0.63, 500_000_000),   # gainer, liquid
        _t("BTCUSDT", 1.0, 60000, 2_000_000_000),  # liquid but small change
        _t("SOLUSDT", 8.0, 150, 300_000_000),      # gainer, liquid
        _t("SCAMUSDT", 90.0, 0.0001, 5_000),       # huge pump but illiquid -> dropped
        _t("BTCUPUSDT", 30.0, 10, 400_000_000),    # leverage token -> dropped
        _t("USDCUSDT", 0.01, 1.0, 900_000_000),    # stable pair -> dropped
        _t("ETHBTC", 5.0, 0.05, 800_000_000),      # non-USDT quote -> dropped
        _t("JUPUSDT", 15.0, 1.2, 200_000_000),     # real coin ending in 'UP' -> kept
    ]
    coins = hc.select_hot_coins(tickers, limit=10, min_quote_volume=10_000_000, candidate_pool=100)
    bases = [c["base"] for c in coins]
    assert "SCAM" not in bases and "BTCUP" not in bases and "USDC" not in bases
    assert "JUP" in bases  # not mistaken for a leverage token
    # ordered by change desc among liquid candidates
    assert bases[0] == "JUP"  # 15% is highest among the liquid ones
    assert coins[0]["change_pct"] == 15.0
    assert all(c["symbol"].endswith("USDT") for c in coins)


def test_candidate_pool_prefers_liquidity_before_gainers():
    # A wild gainer with low (but above-floor) volume must be excluded when it
    # falls outside the top-volume candidate pool.
    tickers = [_t("BIGUSDT", 2.0, 1, 1_000_000_000), _t("MIDUSDT", 3.0, 1, 500_000_000)]
    tickers.append(_t("PUMPUSDT", 99.0, 1, 20_000_000))  # above floor, low volume
    coins = hc.select_hot_coins(tickers, limit=2, min_quote_volume=10_000_000, candidate_pool=2)
    bases = [c["base"] for c in coins]
    assert "PUMP" not in bases  # squeezed out of the 2-coin candidate pool
    assert set(bases) == {"BIG", "MID"}


def test_leverage_heuristic():
    assert hc._is_leverage_token("BTCUP")
    assert hc._is_leverage_token("ETHDOWN")
    assert hc._is_leverage_token("SOLBULL")
    assert not hc._is_leverage_token("JUP")  # 'J' underlying too short
    assert not hc._is_leverage_token("XRP")


def test_endpoint_uses_cache(monkeypatch):
    calls = {"n": 0}

    def fake_fetch():
        calls["n"] += 1
        return [_t("XRPUSDT", 10.0, 0.6, 500_000_000), _t("SOLUSDT", 5.0, 150, 300_000_000)]

    hc._cache.clear()
    monkeypatch.setattr(hc, "_fetch_tickers", fake_fetch)
    r1 = client.get("/api/hot-coins?limit=5").json()
    r2 = client.get("/api/hot-coins?limit=5").json()
    assert r1["coins"][0]["base"] == "XRP"
    assert calls["n"] == 1  # second call served from shared cache
    assert r2["cached"] is True


def test_endpoint_empty_on_fetch_failure(monkeypatch):
    hc._cache.clear()
    monkeypatch.setattr(hc, "_fetch_tickers", lambda: None)
    r = client.get("/api/hot-coins").json()
    assert r["coins"] == []
    assert r.get("error") == "binance"


def _t2(symbol, change, price, qvol, high, low):
    return {"symbol": symbol, "priceChangePercent": str(change), "lastPrice": str(price),
            "quoteVolume": str(qvol), "highPrice": str(high), "lowPrice": str(low)}


def test_range_pct_from_high_low():
    assert hc.ticker_range_pct(_t2("BTCUSDT", 1.0, 100, 1, 110, 100)) == 10.0
    # high/low 가 없거나 0 이면 0.0 (옛 응답·이상값 방어)
    assert hc.ticker_range_pct(_t("BTCUSDT", 1.0, 100, 1)) == 0.0
    assert hc.ticker_range_pct(_t2("BTCUSDT", 1.0, 100, 1, 110, 0)) == 0.0


def test_selection_carries_range_pct():
    coins = hc.select_hot_coins(
        [_t2("BTCUSDT", 1.0, 100, 1_000_000_000, 105, 100)],
        limit=10, min_quote_volume=10_000_000, candidate_pool=100)
    assert coins[0]["range_pct"] == 5.0


def test_get_cached_tickers_returns_none_when_load_fails(monkeypatch):
    # 캐시 자체를 비우지 않고, 캐시가 부를 로더를 실패하게 만들어 확인한다.
    monkeypatch.setattr(hc, "_load_ticker_payload", lambda: (_ for _ in ()).throw(RuntimeError("down")))
    monkeypatch.setattr(hc._cache, "get_or_load",
                        lambda *a, **k: (_ for _ in ()).throw(RuntimeError("down")))
    assert hc.get_cached_tickers() is None
