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


def _t3(symbol, change, price, qvol, high, low, weighted):
    """실제 바이낸스 24시간 응답 모양 — weightedAvgPrice 까지 있는 티커."""
    return {"symbol": symbol, "priceChangePercent": str(change), "lastPrice": str(price),
            "quoteVolume": str(qvol), "highPrice": str(high), "lowPrice": str(low),
            "weightedAvgPrice": str(weighted)}


def _usde():
    """실제로 후보에 올라왔던 USDE 의 숫자 — 값은 1.0 에 붙어 있는데 저가 한 번이 0.92 로 찍혔다."""
    return _t3("USDEUSDT", 0.01, 0.9999, 5e8, 1.0006, 0.9202, 0.9995)


def test_cache_stores_trimmed_payload_for_a_full_exchange_response(monkeypatch):
    # 거래소 응답 전체(2천 종목)를 통째로 캐시에 넣으면 1.6MB 라 상한에 걸려 버려지고,
    # 그러면 매 요청이 바이낸스를 그대로 때린다. 투영을 저장해 실제로 캐시되는지 본다.
    calls = {"n": 0}

    def fake_fetch():
        calls["n"] += 1
        out = []
        for i in range(2000):
            price = 0.00001234 + i
            out.append(_t3(f"SYM{i:04d}USDT", 1.23 + i % 7, f"{price:.8f}", 1e7 + i * 1e6,
                           f"{price * 1.08:.8f}", f"{price * 0.93:.8f}", f"{price * 1.01:.8f}"))
        return out

    hc._cache.clear()
    monkeypatch.setattr(hc, "_fetch_tickers", fake_fetch)
    oversized_before = hc._cache.statistics()["oversized"]
    for _ in range(5):
        assert hc.get_hot_coins(10)["coins"]
    stats = hc._cache.statistics()
    assert calls["n"] == 1                                   # 캐시 창 안에서는 한 번만 부른다
    assert stats["entries"] == 1                             # 실제로 들어갔다
    assert stats["oversized"] == oversized_before            # 상한에 걸려 버려지지 않았다
    assert stats["size_bytes"] <= hc._cache.max_bytes
    assert len(hc.get_cached_tickers()) == hc.CACHE_TOP_SYMBOLS


def test_trimmed_tickers_keep_the_fields_the_pool_needs():
    trimmed = hc.trim_tickers([_usde(), _t3("ETHBTC", 1.0, 0.05, 9e8, 0.06, 0.04, 0.05)], top=10)
    assert [t["symbol"] for t in trimmed] == ["USDEUSDT"]     # USDT 페어만 남는다
    assert set(trimmed[0]) == {"symbol", "quoteVolume", "lastPrice", "priceChangePercent",
                               "highPrice", "lowPrice", "weightedAvgPrice"}


def test_trimmed_tickers_keep_the_biggest_by_quote_volume():
    tickers = [_t3(f"S{i}USDT", 5.0, 10 + i, (i + 1) * 1e7, 11 + i, 9 + i, 10 + i) for i in range(6)]
    assert [t["symbol"] for t in hc.trim_tickers(tickers, top=2)] == ["S5USDT", "S4USDT"]


def test_pegged_asset_never_survives_selection():
    # 목록(_STABLE_BASES)에 USDE 를 넣은 것과, 목록이 낡아도 걸리는 일반 판정 — 둘 다 확인한다.
    coins = hc.select_hot_coins([_usde()], limit=10, min_quote_volume=10_000_000, candidate_pool=100)
    assert coins == []
    unlisted = dict(_usde(), symbol="NEWPEGUSDT")
    assert hc.select_hot_coins([unlisted], limit=10, min_quote_volume=10_000_000,
                               candidate_pool=100) == []


def test_range_pct_ignores_a_single_wick_but_keeps_a_real_move():
    # 저가 한 번이 0.92 로 찍힌 USDE — 원본 고저 계산이면 8%대로 잡혀 중간 순위에 끼어들었다.
    assert hc.ticker_range_pct(_usde()) < 1.0
    # 실제로 오르내린 코인은 두 반폭이 비슷해 값이 거의 줄지 않는다.
    real = _t3("DOGEUSDT", 8.0, 0.22, 5e8, 0.23, 0.19, 0.21)
    assert hc.ticker_range_pct(real) > 15.0


# 대칭 반폭만 쓰면 '한쪽으로만 간 날'도 반폭이 작아 0 에 가깝게 나온다 — 하루 등락을 하한으로
# 같이 본다. 네 가지 모양을 한자리에 못 박는다.
def _one_sided_pump():
    """갭 상승 뒤 고가 부근에서 마감 — 가중평균이 고가에 붙어 있어 좁은 쪽 반폭이 거의 0 이다."""
    return _t3("PUMPUSDT", 30, 1.29, 5e8, 1.30, 1.00, 1.295)


def _one_sided_dump():
    """반대 모양 — 급락해 저가 부근에서 마감."""
    return _t3("DUMPUSDT", -28, 1.005, 5e8, 1.30, 1.00, 1.005)


def test_range_pct_of_a_one_sided_pump_is_large():
    assert hc.ticker_range_pct(_one_sided_pump()) >= 28.0


def test_range_pct_of_a_one_sided_dump_is_large():
    assert hc.ticker_range_pct(_one_sided_dump()) >= 25.0


def test_range_pct_of_a_wick_stays_small_and_the_coin_is_dropped():
    assert hc.ticker_range_pct(_usde()) < 1.0
    assert hc.select_hot_coins([_usde()], limit=10, min_quote_volume=10_000_000,
                               candidate_pool=100) == []


def test_range_pct_of_a_two_sided_mover_does_not_collapse():
    real = _t3("DOGEUSDT", 8.0, 0.22, 5e8, 0.23, 0.19, 0.21)
    assert hc.ticker_range_pct(real) > 15.0


def test_a_one_sided_pump_never_looks_calmer_than_a_calm_coin():
    calm = _t3("CALMUSDT", 0.4, 100, 5e8, 101.5, 98.5, 100.0)
    assert hc.ticker_range_pct(_one_sided_pump()) > hc.ticker_range_pct(calm)
    assert hc.ticker_range_pct(_one_sided_dump()) > hc.ticker_range_pct(calm)
