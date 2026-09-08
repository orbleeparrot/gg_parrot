from datetime import datetime, timezone
from email.utils import format_datetime

import httpx
import pytest

from app import whales


NOW = 1_800_000_000.0


def _trade(identity=11, **values):
    return {"a": identity, "p": "50000", "q": "3", "T": int(NOW * 1000), "m": False, **values}


@pytest.fixture(autouse=True)
def fixed_source_settings(monkeypatch):
    monkeypatch.setattr(whales.time, "time", lambda: NOW)
    monkeypatch.delenv("AGENT_LARGE_TRADE_MIN_QUOTE", raising=False)


def test_large_trades_have_correct_taker_direction_and_filter_bad_rows(monkeypatch):
    monkeypatch.setattr(whales, "_fetch_aggregate_trades", lambda *_: [
        _trade(11), _trade(12, q="4", m=True), _trade(13, q=".01"),
        _trade(14, p="NaN"), _trade(15, T=int((NOW - 3600) * 1000)),
        None, [], "invalid", {"message": "invalid trade"},
    ])
    result = whales.fetch_large_trade_activity("btcusdt")
    assert result["status"] == "ready"
    assert {item["id"]: item["side"] for item in result["items"]} == {
        "spot:BTCUSDT:11": "buy", "spot:BTCUSDT:12": "sell",
    }
    assert result["threshold_quote"] == 100000
    assert result["quote_asset"] == "USDT"
    assert result["sampled_trades"] == 9


@pytest.mark.parametrize("changes", [
    {"a": True}, {"a": False}, {"a": -1}, {"a": "-1"}, {"a": 1.5}, {"a": "1.5"},
    {"m": "false"}, {"m": 0}, {"T": True}, {"T": "1800000000000.5"},
    {"p": "Infinity"}, {"q": "Infinity"}, {"p": 0}, {"q": -1},
    {"p": True}, {"q": True}, {"T": int((NOW + 6) * 1000)},
])
def test_invalid_trade_cannot_become_a_false_large_fill(monkeypatch, changes):
    monkeypatch.setattr(whales, "_fetch_aggregate_trades", lambda *_: [_trade(**changes)])
    assert whales.fetch_large_trade_activity("BTCUSDT")["status"] == "empty"


def test_exact_ids_deduplicate_and_latest_thirty_are_returned(monkeypatch):
    rows = [_trade(str(9_007_199_254_740_992 + i), T=int(NOW * 1000) - i) for i in range(35)]
    monkeypatch.setattr(whales, "_fetch_aggregate_trades", lambda *_: rows + [rows[0]])
    result = whales.fetch_large_trade_activity("BTCUSDC", "futures")
    assert len(result["items"]) == 30
    assert result["items"][0]["id"] == "futures:BTCUSDC:9007199254740992"
    assert result["items"][1]["id"] == "futures:BTCUSDC:9007199254740993"
    assert result["quote_asset"] == "USDC"


def test_observation_time_is_after_slow_source_response(monkeypatch):
    def fetch(*_):
        monkeypatch.setattr(whales.time, "time", lambda: NOW + 8)
        return [_trade(T=int((NOW + 7) * 1000))]
    monkeypatch.setattr(whales, "_fetch_aggregate_trades", fetch)
    result = whales.fetch_large_trade_activity("BTCUSDT")
    assert result["status"] == "ready"
    assert result["observed_at"] == datetime.fromtimestamp(NOW + 8, timezone.utc).isoformat()
    assert result["window_start"] == datetime.fromtimestamp(NOW - 592, timezone.utc).isoformat()


def test_source_is_stateless_and_successful_empty_is_not_an_error(monkeypatch):
    calls = []
    monkeypatch.setattr(whales, "_fetch_aggregate_trades", lambda *args: calls.append(args) or [])
    first = whales.fetch_large_trade_activity("SOLUSDT", "futures")
    second = whales.fetch_large_trade_activity("SOLUSDT", "futures")
    assert first["status"] == second["status"] == "empty"
    assert calls == [("SOLUSDT", "futures"), ("SOLUSDT", "futures")]


@pytest.mark.parametrize("symbol,market", [("ETHBTC", "spot"), ("USDT", "spot"),
    ("BTCUSDT", "margin"), ("BTCUSDT", ""), ("", "spot")])
def test_unsupported_target_is_rejected_without_network(monkeypatch, symbol, market):
    monkeypatch.setattr(whales, "_fetch_aggregate_trades", lambda *_: pytest.fail("must not fetch"))
    with pytest.raises(ValueError):
        whales.fetch_large_trade_activity(symbol, market)


@pytest.mark.parametrize("value,expected", [("bad", 100000), ("NaN", 100000),
    ("inf", 100000), ("-inf", 100000), ("-1", 1000), ("500", 1000), ("150000", 150000)])
def test_threshold_configuration_is_finite_and_bounded(monkeypatch, value, expected):
    monkeypatch.setenv("AGENT_LARGE_TRADE_MIN_QUOTE", value)
    assert whales.base_payload("BTCUSDT")["threshold_quote"] == expected
    assert whales.configuration()["threshold_quote"] == expected


def _client(monkeypatch, handler):
    client = httpx.Client(transport=httpx.MockTransport(handler))
    monkeypatch.setattr(whales, "get_http_client", lambda: client)
    return client


@pytest.mark.parametrize("market,path,host", [
    ("spot", "/api/v3/aggTrades", "spot.invalid"),
    ("futures", "/fapi/v1/aggTrades", "futures.invalid"),
])
def test_public_source_requests_one_bounded_sample(monkeypatch, market, path, host):
    monkeypatch.setenv("BINANCE_API_BASE", "https://spot.invalid/")
    monkeypatch.setenv("BINANCE_FAPI_BASE", "https://futures.invalid/")
    requests = []
    def handler(request):
        requests.append(request)
        return httpx.Response(200, json=[_trade()])
    with _client(monkeypatch, handler):
        assert whales.fetch_large_trade_activity("BTCUSDT", market)["status"] == "ready"
    assert len(requests) == 1
    request = requests[0]
    assert request.method == "GET" and request.url.host == host and request.url.path == path
    assert dict(request.url.params) == {"symbol": "BTCUSDT", "limit": "500"}
    assert all(value == 8 for value in request.extensions["timeout"].values())


@pytest.mark.parametrize("market,host", [("spot", "data-api.binance.vision"), ("futures", "fapi.binance.com")])
def test_missing_api_override_uses_official_public_source(monkeypatch, market, host):
    monkeypatch.delenv("BINANCE_API_BASE", raising=False)
    monkeypatch.delenv("BINANCE_FAPI_BASE", raising=False)
    requested = []
    def respond(request):
        requested.append(request.url.host)
        return httpx.Response(200, json=[])
    with _client(monkeypatch, respond):
        assert whales.fetch_large_trade_activity("BTCUSDT", market)["status"] == "empty"
    assert requested == [host]


@pytest.mark.parametrize("status,header,expected", [(429, "120", 120), (418, None, 300),
    (429, "garbage", 60), (429, "999999999", 3600), (429, "0", 1),
    (429, "Infinity", 60)])
def test_rate_limit_failure_preserves_safe_retry_metadata(monkeypatch, status, header, expected):
    headers = {"Retry-After": header} if header is not None else {}
    with _client(monkeypatch, lambda request: httpx.Response(status, headers=headers, text="private upstream body")):
        with pytest.raises(whales.LargeTradeSourceError) as caught:
            whales.fetch_large_trade_activity("BTCUSDT")
    error = caught.value
    assert error.code == "rate_limited"
    assert error.http_status == status
    assert error.retry_after_seconds == error.retry_after_sec == expected
    assert "private" not in str(error) and error.__cause__ is None


def test_http_date_retry_after_uses_current_response_time(monkeypatch):
    header = format_datetime(datetime.fromtimestamp(NOW + 180, timezone.utc), usegmt=True)
    with _client(monkeypatch, lambda request: httpx.Response(429, headers={"Retry-After": header})):
        with pytest.raises(whales.LargeTradeSourceError) as caught:
            whales.fetch_large_trade_activity("BTCUSDT")
    assert caught.value.retry_after_seconds == 180


@pytest.mark.parametrize("status,body,code", [(503, "upstream private body", "http_error"),
    (200, "not JSON", "invalid_response"), (200, '{"code": "error"}', "invalid_response")])
def test_source_failure_is_observable_without_response_body(monkeypatch, status, body, code):
    with _client(monkeypatch, lambda request: httpx.Response(status, text=body)):
        with pytest.raises(whales.LargeTradeSourceError) as caught:
            whales.fetch_large_trade_activity("BTCUSDT")
    assert caught.value.code == code
    assert body not in str(caught.value)


@pytest.mark.parametrize("exception,code", [(httpx.ReadTimeout, "timeout"), (httpx.ConnectError, "network_error")])
def test_network_errors_do_not_leak_url_or_exception_details(monkeypatch, exception, code):
    def fail(request):
        raise exception("secret.invalid/private", request=request)
    with _client(monkeypatch, fail):
        with pytest.raises(whales.LargeTradeSourceError) as caught:
            whales.fetch_large_trade_activity("BTCUSDT")
    assert caught.value.code == code
    assert "secret" not in str(caught.value)
