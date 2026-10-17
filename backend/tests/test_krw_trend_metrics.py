"""Missing provider metrics must never become a genuine zero-change ranking."""
import pytest

from app import market_trends
from app.data import krw


@pytest.fixture
def ticker_source(monkeypatch):
    monkeypatch.setattr(krw, "list_symbols", lambda exchange: {
        "items": [{"symbol": "KRW-BTC", "korean_name": "비트코인"}]})
    row = {"market": "KRW-BTC", "trade_price": 100_000_000,
           "signed_change_rate": 0.02, "acc_trade_price_24h": 123_000_000,
           "acc_trade_volume_24h": 1.23}
    monkeypatch.setattr(krw, "_ticker_rows", lambda exchange, symbols: [row])
    return row


@pytest.mark.parametrize("exchange", ["upbit", "bithumb"])
@pytest.mark.parametrize("field,projection", [
    ("signed_change_rate", "priceChangePercent"),
    ("acc_trade_price_24h", "quoteVolume"),
])
@pytest.mark.parametrize("bad", [None, "", "not-a-number", "nan", "inf", True])
def test_invalid_required_metrics_remain_missing_not_zero(ticker_source, exchange, field, projection, bad):
    ticker_source[field] = bad
    row = krw.get_ticker_snapshot(exchange)["items"][0]
    assert row[projection] is None
    result = market_trends.get_market_trends(exchange=exchange)
    assert result["coins"] == []
    assert result["status"] == "error"
    assert result["coverage"]["invalid_count"] == 1


@pytest.mark.parametrize("exchange", ["upbit", "bithumb"])
def test_real_zero_metrics_are_valid(ticker_source, exchange):
    ticker_source.update(signed_change_rate=0, acc_trade_price_24h=0, acc_trade_volume_24h=0)
    result = market_trends.get_market_trends(exchange=exchange)
    assert result["status"] == "ready"
    assert result["coins"][0]["change_pct"] == 0
    assert result["coins"][0]["quote_volume"] == 0
    assert result["coins"][0]["volume"] == 0


def test_finite_provider_change_cannot_overflow_into_a_json_metric(ticker_source):
    ticker_source["signed_change_rate"] = 1e308
    row = krw.get_ticker_snapshot("upbit")["items"][0]
    assert row["priceChangePercent"] is None
    assert market_trends.get_market_trends(exchange="upbit")["status"] == "error"


def test_negative_turnover_is_not_a_valid_ranking_metric(ticker_source):
    ticker_source["acc_trade_price_24h"] = -1
    result = market_trends.get_market_trends(exchange="upbit")
    assert result["coins"] == [] and result["status"] == "error"


def test_missing_ranking_metrics_do_not_break_price_or_candle_defaults(ticker_source):
    ticker_source.update(signed_change_rate=None, acc_trade_price_24h=None,
                         high_price=None, low_price=None)
    # A valid trade price remains usable for the existing chart/price callers.
    assert krw.get_ticker_price("KRW-BTC", "upbit") == 100_000_000
    row = krw.get_all_tickers("upbit")[0]
    assert row["highPrice"] == row["lowPrice"] == row["lastPrice"] == 100_000_000
    assert row["priceChangePercent"] is None and row["quoteVolume"] is None
