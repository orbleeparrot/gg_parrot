"""Public rankings use native exchange snapshots; no live HTTP or accounts."""
import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from app import hotcoins, market_trends
from app.data import krw

app = FastAPI()
app.include_router(market_trends.router)
client = TestClient(app)


def _ticker(base, change=2, turnover=100, volume=10, exchange="upbit", **extra):
    symbol = f"{base}USDT" if exchange == "binance" else f"KRW-{base}"
    return {"symbol": symbol, "exchange": exchange, "lastPrice": 1000,
            "priceChangePercent": change, "quoteVolume": turnover, "volume": volume, **extra}


def _snapshot(rows, stale=False, **extra):
    return {"items": rows, "fetched_at": 1_800_000_000, "stale": stale,
            "refresh_seconds": 30, "cache_age_seconds": 10, **extra}


@pytest.mark.parametrize("sort,expected", [
    ("turnover", ["BTC", "ETH", "XRP"]), ("gainers", ["XRP", "BTC", "ETH"]),
    ("losers", ["ETH", "BTC", "XRP"]), ("volume", ["ETH", "XRP", "BTC"]),
])
def test_rankings_sort_actual_exchange_metrics(monkeypatch, sort, expected):
    rows = [_ticker("BTC", change=2, turnover=900, volume=1),
            _ticker("ETH", change=-3, turnover=500, volume=50),
            _ticker("XRP", change=9, turnover=100, volume=10)]
    monkeypatch.setattr(krw, "get_ticker_snapshot", lambda exchange: _snapshot(rows))
    payload = client.get("/api/market/trends", params={"exchange": "upbit", "sort": sort}).json()
    assert [coin["base"] for coin in payload["coins"]] == expected
    assert [coin["rank"] for coin in payload["coins"]] == [1, 2, 3]
    assert payload["quote_currency"] == "KRW" and payload["change_basis"] == "previous_close"
    assert payload["coins"][0]["last_price"] == 1000
    assert payload["region"] == "kr" and payload["rank_basis"] == "filtered_results"


def test_search_turnover_direction_and_limit_apply_together(monkeypatch):
    rows = [_ticker("BTC", korean_name="비트코인", english_name="Bitcoin", turnover=900),
            _ticker("BCH", korean_name="비트코인캐시", english_name="Bitcoin Cash", turnover=500),
            _ticker("ETH", change=-1, turnover=900)]
    monkeypatch.setattr(krw, "get_ticker_snapshot", lambda exchange: _snapshot(rows))
    for needle in ("비트코인", "BITCOIN"):
        response = client.get("/api/market/trends", params={"exchange": "upbit", "q": needle,
                              "min_turnover": 500, "direction": "up", "limit": 1})
        assert response.status_code == 200
        assert response.json()["total"] == 2
        assert [coin["base"] for coin in response.json()["coins"]] == ["BTC"]
    empty = client.get("/api/market/trends?exchange=upbit&q=missing").json()
    assert empty["status"] == "ready" and empty["coins"] == [] and empty["total"] == 0


def test_identity_invalid_numeric_values_and_stale_source(monkeypatch):
    rows = [_ticker("BTC"), _ticker("ETH", exchange="bithumb"), _ticker("NAN", change="nan"),
            _ticker("INF", turnover="inf"), _ticker("ZERO", lastPrice=0),
            _ticker("NEG", volume=-1)]
    monkeypatch.setattr(krw, "get_ticker_snapshot", lambda exchange: _snapshot(rows, stale=True))
    result = client.get("/api/market/trends?exchange=upbit").json()
    assert [coin["base"] for coin in result["coins"]] == ["BTC", "NEG"]
    assert result["coins"][1]["volume"] is None
    assert result["stale"] and result["status"] == "stale"
    assert result["updated_at"] == "2027-01-15T08:00:00+00:00"


def test_ties_are_deterministic_and_missing_volume_sorts_last(monkeypatch):
    rows = [_ticker("XRP", volume=None), _ticker("ETH", volume=0), _ticker("BTC", volume=0)]
    monkeypatch.setattr(krw, "get_ticker_snapshot", lambda exchange: _snapshot(rows))
    result = client.get("/api/market/trends?exchange=upbit&sort=volume").json()
    assert [coin["base"] for coin in result["coins"]] == ["BTC", "ETH", "XRP"]


@pytest.mark.parametrize("query", ["exchange=unknown", "sort=bad", "direction=sideways", "limit=0",
                                   "limit=501", "min_turnover=-1", "min_turnover=nan",
                                   "min_turnover=inf", "q=" + "x" * 101])
def test_invalid_input_is_rejected_before_source_access(monkeypatch, query):
    monkeypatch.setattr(hotcoins, "get_ticker_snapshot", lambda: pytest.fail("source accessed"))
    assert client.get("/api/market/trends?" + query).status_code == 422


def test_source_failure_returns_unavailable_not_successful_empty(monkeypatch):
    def unavailable(exchange):
        raise RuntimeError("provider failed")
    monkeypatch.setattr(krw, "get_ticker_snapshot", unavailable)
    response = client.get("/api/market/trends?exchange=bithumb")
    assert response.status_code == 503
    assert "provider failed" not in response.text


def test_binance_ranking_reuses_hotcoins_fetch_and_reports_candidate_cap(monkeypatch):
    calls = []
    def fetch():
        calls.append(True)
        return [_ticker(f"COIN{i}", exchange="binance", turnover=20_000_000 + i,
                        volume=i, change=i / 10) for i in range(600)]
    monkeypatch.setattr(hotcoins, "_fetch_tickers", fetch)
    old = hotcoins.get_hot_coins()
    result = client.get("/api/market/trends?exchange=binance&sort=volume").json()
    filtered = client.get("/api/market/trends?exchange=binance&q=COIN599&direction=up").json()
    assert len(calls) == 1 and old["coins"]
    assert len(result["coins"]) == 500 and result["coins"][0]["volume"] == 599
    assert result["change_basis"] == "24h" and result["quote_currency"] == "USDT"
    assert result["coverage"] == {"source_count": 600, "candidate_count": 500,
                                     "truncated": True, "basis": "turnover_top",
                                     "returned_count": 500, "matched_count": 500,
                                     "result_truncated": False, "invalid_count": 0}
    assert result["region"] == "global"
    assert filtered["total"] == 1


def test_binance_korean_name_search_uses_memory_only(monkeypatch):
    from app import news_asset_catalog, news_exchange_names

    monkeypatch.setattr(hotcoins, "get_ticker_snapshot", lambda: _snapshot([
        _ticker("BTC", exchange="binance"), _ticker("ETH", exchange="binance")]))
    monkeypatch.setattr(news_asset_catalog, "get_asset_name", lambda *args: pytest.fail("name discovery"))
    monkeypatch.setattr(news_exchange_names, "refresh_names", lambda: pytest.fail("name refresh"))
    result = client.get("/api/market/trends", params={"exchange": "binance", "q": "비트코인"})
    assert result.status_code == 200
    assert [coin["symbol"] for coin in result.json()["coins"]] == ["BTCUSDT"]
    assert result.json()["coins"][0]["korean_name"] == "비트코인"
    assert result.json()["coins"][0]["english_name"].casefold() == "bitcoin"


def test_provided_names_take_priority_over_memory_fallback(monkeypatch):
    monkeypatch.setattr(hotcoins, "get_ticker_snapshot", lambda: _snapshot([
        _ticker("BTC", exchange="binance", korean_name="제공된 비트코인", english_name="Provided Bitcoin")]))
    coin = client.get("/api/market/trends?exchange=binance").json()["coins"][0]
    assert coin["korean_name"] == "제공된 비트코인" and coin["english_name"] == "Provided Bitcoin"


def test_main_application_registers_market_rankings(monkeypatch):
    from app.main import app as main_app

    monkeypatch.setattr(hotcoins, "_fetch_tickers", lambda: [
        _ticker("BTC", exchange="binance", turnover=100_000_000)])
    response = TestClient(main_app).get("/api/market/trends", params={"exchange": "binance", "q": "비트코인"})
    assert response.status_code == 200
    assert response.json()["exchange"] == "binance"
    assert [coin["symbol"] for coin in response.json()["coins"]] == ["BTCUSDT"]


def test_domestic_rankings_share_cache_but_never_mix_exchange_quotes(monkeypatch):
    calls = []
    def request(exchange, path, params):
        calls.append((exchange, path))
        if path == "/v1/market/all":
            return [{"market": "KRW-BTC", "korean_name": "비트코인", "english_name": "Bitcoin"}]
        return [{"market": "KRW-BTC", "trade_price": 100 if exchange == "upbit" else 200,
                 "signed_change_rate": .02, "acc_trade_price_24h": 123456789,
                 "acc_trade_volume_24h": 987}]
    monkeypatch.setattr(krw, "_request", request)
    for exchange in ("upbit", "bithumb"):
        first = client.get(f"/api/market/trends?exchange={exchange}").json()
        second = client.get(f"/api/market/trends?exchange={exchange}&q=비트&sort=gainers").json()
        assert first["updated_at"] == second["updated_at"]
        coin = first["coins"][0]
        assert coin["last_price"] == (100 if exchange == "upbit" else 200)
        assert coin["quote_volume"] == 123456789 and coin["volume"] == 987
        assert coin["change_pct"] == 2 and coin["korean_name"] == "비트코인"
    assert len(calls) == 4


def test_domestic_catalogue_stale_cannot_appear_fresh(monkeypatch):
    monkeypatch.setattr(krw, "list_symbols", lambda exchange: {"items": [{"symbol": "KRW-BTC"}],
                                                               "stale": True, "fetched_at": 10})
    monkeypatch.setattr(krw, "_ticker_rows", lambda exchange, symbols: [
        {"market": "KRW-BTC", "trade_price": 100, "signed_change_rate": 0,
         "acc_trade_price_24h": 1000}])
    result = client.get("/api/market/trends?exchange=upbit").json()
    assert result["stale"] is True and result["status"] == "stale"


def test_stale_snapshots_preserve_fetch_time_and_expire(monkeypatch):
    from app.cache_runtime import close_cache_runtime
    clock = [100.0]
    monkeypatch.setattr(krw._tickers_cache, "clock", lambda: clock[0])
    monkeypatch.setattr(krw, "list_symbols", lambda exchange: {"items": [{"symbol": "KRW-BTC"}]})
    monkeypatch.setattr(krw, "_ticker_rows", lambda exchange, symbols: [
        {"market": "KRW-BTC", "trade_price": 100, "acc_trade_price_24h": 1000}])
    first = krw.get_ticker_snapshot("upbit")
    def unavailable(*args):
        raise RuntimeError("offline")
    monkeypatch.setattr(krw, "_ticker_rows", unavailable)
    clock[0] = 131
    stale = krw.get_ticker_snapshot("upbit")
    close_cache_runtime()
    assert stale["stale"] is True and stale["fetched_at"] == first["fetched_at"]
    clock[0] = 251
    with pytest.raises(RuntimeError):
        krw.get_ticker_snapshot("upbit")


@pytest.mark.parametrize("stale,expected", [(False, "degraded"), (True, "stale")])
def test_partial_bad_provider_metrics_are_reported(monkeypatch, stale, expected):
    rows = [_ticker("BTC"), _ticker("BAD", turnover=None), "not-a-ticker"]
    monkeypatch.setattr(krw, "get_ticker_snapshot", lambda exchange: _snapshot(rows, stale=stale))
    result = market_trends.get_market_trends(exchange="upbit", q="not-listed")
    assert result["status"] == expected
    assert result["coverage"]["invalid_count"] == 2
    assert result["coverage"]["candidate_count"] == 1
    assert result["coins"] == []  # Search emptiness does not hide source degradation.


def test_limit_is_not_misrepresented_as_complete_results(monkeypatch):
    monkeypatch.setattr(krw, "get_ticker_snapshot", lambda exchange: _snapshot([
        _ticker("BTC"), _ticker("ETH"), _ticker("XRP")]))
    result = market_trends.get_market_trends(exchange="upbit", limit=1)
    assert result["coverage"]["result_truncated"] is True
    assert result["coverage"]["returned_count"] == 1
    assert result["coverage"]["matched_count"] == result["total"] == 3
    assert result["coverage"]["truncated"] is False  # No provider-side cap.


def test_asset_resolution_uses_only_supplied_prepared_snapshot(monkeypatch):
    monkeypatch.setattr(krw, "get_ticker_snapshot", lambda *args: pytest.fail("remote source"))
    monkeypatch.setattr(hotcoins, "get_ticker_snapshot", lambda: pytest.fail("remote source"))
    payload = {"coins": [
        {"base": "BTC", "symbol": "KRW-BTC", "exchange": "upbit",
         "korean_name": "비트코인", "english_name": "Bitcoin"},
        {"base": "BCH", "symbol": "KRW-BCH", "exchange": "upbit",
         "korean_name": "비트코인캐시", "english_name": "Bitcoin Cash"},
        {"base": "BTC", "symbol": "KRW-BTC", "exchange": "bithumb"},
    ]}
    assert market_trends.resolve_assets("Bitcoin", "upbit", payload) == ["BTC", "BCH"]
    assert market_trends.resolve_assets("비트코인캐시", "upbit", payload) == ["BCH"]
    assert market_trends.resolve_assets("krw-btc", "upbit", payload) == ["BTC"]
    assert market_trends.resolve_assets("btc", "upbit") == []
    assert market_trends.resolve_assets("", "upbit", payload) == ["BTC", "BCH"]


def test_asset_resolution_rejects_cross_quote_and_malformed_identities():
    payload = {"coins": [
        {"base": "BTC", "symbol": "BTCUSDT", "exchange": "upbit"},
        {"base": "X/Y", "symbol": "KRW-X/Y", "exchange": "upbit"},
        {"base": "ETH", "symbol": "KRW-ETH", "exchange": "upbit"},
        {"base": "ETH", "symbol": "KRW-ETH", "exchange": "upbit"},
    ]}
    assert market_trends.resolve_assets("", "upbit", payload) == ["ETH"]


def test_short_asset_search_is_identity_not_arbitrary_substring(monkeypatch):
    monkeypatch.setattr(hotcoins, "get_ticker_snapshot", lambda: _snapshot([
        _ticker("T", exchange="binance", english_name="Threshold"),
        _ticker("BTC", exchange="binance"), _ticker("ETH", exchange="binance")]))
    result = market_trends.get_market_trends(exchange="binance", q="T")
    assert [coin["base"] for coin in result["coins"]] == ["T"]
    assert market_trends.resolve_assets("Threshold", "binance", result) == ["T"]


def test_pure_rank_filter_preserves_source_universe_and_original(monkeypatch):
    from copy import deepcopy

    monkeypatch.setattr(krw, "get_ticker_snapshot", lambda exchange: _snapshot([
        _ticker("BTC", turnover=900, change=1, korean_name="비트코인"),
        _ticker("ORCA", turnover=2, change=-3, korean_name="오르카", english_name="Orca"),
        _ticker("ETH", turnover=500, change=2),
    ]))
    prepared = market_trends.get_market_trends(exchange="upbit", sort="turnover")
    original = deepcopy(prepared)
    monkeypatch.setattr(krw, "get_ticker_snapshot", lambda *args: pytest.fail("second remote source"))
    filtered = market_trends.filter_rankings(prepared, q="오르카", min_turnover=100, direction="up")
    assert filtered["coins"] == [] and filtered["total"] == 0
    # News/community resolution is against the prepared exchange universe, not
    # the display-only turnover/direction filters that hide ORCA's rank row.
    assert market_trends.resolve_assets("오르카", "upbit", prepared) == ["ORCA"]
    assert prepared == original

    limited = market_trends.filter_rankings(prepared, min_turnover=100, direction="up", limit=1)
    assert [coin["base"] for coin in limited["coins"]] == ["BTC"]
    assert limited["coins"][0]["rank"] == 1 and limited["total"] == 2
    assert limited["coverage"]["matched_count"] == 2
    assert limited["coverage"]["returned_count"] == 1
    assert limited["coverage"]["result_truncated"] is True
    assert limited["coverage"]["candidate_count"] == 3
    limited["coverage"]["source_count"] = -1
    limited["coins"][0]["last_price"] = -1
    assert prepared == original


@pytest.mark.parametrize("capped,expected_basis", [(False, "all"), (True, "turnover_top")])
def test_explicit_provider_cap_and_invalid_removal_are_distinct(monkeypatch, capped, expected_basis):
    monkeypatch.setattr(hotcoins, "get_ticker_snapshot", lambda: _snapshot([
        _ticker("BTC", exchange="binance"), _ticker("ETH", exchange="binance")],
        source_count=10, source_invalid_count=3, source_truncated=capped))
    result = market_trends.get_market_trends(exchange="binance")
    assert result["coverage"]["truncated"] is capped
    assert result["coverage"]["basis"] == expected_basis
    assert result["coverage"]["invalid_count"] == 3
    assert result["status"] == "degraded"


def test_today_high_low_are_paired_or_absent(monkeypatch):
    rows = [_ticker("BTC", highPrice=1100, lowPrice=900), _ticker("ETH", highPrice=900, lowPrice=1100),
            _ticker("XRP", highPrice="nan", lowPrice=900), _ticker("SOL")]
    monkeypatch.setattr(krw, "get_ticker_snapshot", lambda exchange: _snapshot(rows))
    coins = {coin["base"]: coin for coin in client.get("/api/market/trends?exchange=upbit").json()["coins"]}
    assert (coins["BTC"]["high"], coins["BTC"]["low"]) == (1100, 900)
    for base in ("ETH", "XRP", "SOL"):
        assert coins[base]["high"] is None and coins[base]["low"] is None


def test_domestic_racers_are_gainers_inside_the_turnover_pool(monkeypatch):
    monkeypatch.setattr(market_trends, "DOMESTIC_RACER_POOL", 3)
    rows = [_ticker("BTC", change=1, turnover=900), _ticker("USDT", change=5, turnover=800),
            _ticker("ETH", change=4, turnover=700), _ticker("XRP", change=-2, turnover=600),
            _ticker("DOGE", change=30, turnover=500), _ticker("BTCUP", change=50, turnover=950)]
    monkeypatch.setattr(krw, "get_ticker_snapshot", lambda exchange: _snapshot(rows))
    payload = client.get("/api/market/trends", params={"exchange": "upbit", "q": "BTC", "min_turnover": 850}).json()
    # 표시 필터(q·최소 거래대금)와 무관하게, 스테이블·레버리지를 뺀 거래대금 상위 3개 안의 상승 코인만.
    assert payload["racers"] == ["ETH", "BTC"]
    assert payload["racer_rule"] == {"pool": 3, "min_turnover": 0, "change_basis": "previous_close"}


def test_binance_racers_use_the_turnover_top_pool_without_a_dollar_floor(monkeypatch):
    # 1,000만 달러 문턱 없이 거래대금 상위 BINANCE_RACER_POOL 안의 오른 코인 — 500만 달러인 SMALL 도 들어온다.
    rows = [_ticker("TIA", change=20, turnover=4e7, exchange="binance"),
            _ticker("EUR", change=0.1, turnover=9e7, exchange="binance"),
            _ticker("U", change=0.02, turnover=8e7, exchange="binance", lastPrice=1.0006),
            _ticker("SMALL", change=90, turnover=5e6, exchange="binance"),
            _ticker("WLD", change=6, turnover=5e7, exchange="binance"),
            _ticker("DOWN", change=-3, turnover=6e7, exchange="binance")]
    monkeypatch.setattr(hotcoins, "get_ticker_snapshot", lambda: _snapshot(rows))
    payload = client.get("/api/market/trends?exchange=binance").json()
    assert payload["racers"] == ["SMALL", "TIA", "WLD"]
    assert payload["racer_rule"] == {"pool": 100, "min_turnover": 0, "change_basis": "24h"}
    # 후보는 거래대금 상위 pool 개까지만 — 그 밖의 급등 코인은 빠진다.
    monkeypatch.setattr(market_trends, "BINANCE_RACER_POOL", 3)
    assert client.get("/api/market/trends?exchange=binance").json()["racers"] == ["TIA", "WLD"]
