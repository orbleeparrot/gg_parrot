"""Public holder observations must preserve balances and fail without fake deltas."""
import json

import httpx
import pytest

from app import whales


ETH_ONE = "0xAbC0000000000000000000000000000000000001"
ETH_TWO = "0xabc0000000000000000000000000000000000002"
XRP_ONE = "rMQ98K56yXJbDGv49ZSmW51sLn94Xe1mu2"
XRP_TWO = "rKveEyR1SrkWbJX214xcfH43ZsoGMb3PEw"


@pytest.fixture
def serve(monkeypatch):
    clients = []

    def install(payload=None, *, status=200, headers=None, content=None, handler=None):
        requests = []

        def respond(request):
            requests.append(request)
            if handler:
                return handler(request)
            if content is not None:
                return httpx.Response(status, headers=headers, content=content)
            return httpx.Response(status, headers=headers, json=payload)

        client = httpx.Client(transport=httpx.MockTransport(respond))
        clients.append(client)
        monkeypatch.setattr(whales, "get_http_client", lambda: client)
        return requests

    yield install
    for client in clients:
        client.close()


def blockscout(rows):
    return {"status": "1", "message": "OK", "result": rows}


def test_blockscout_exact_balances_normalization_exclusions_and_public_metadata(serve, monkeypatch):
    monkeypatch.setenv("BLOCKSCOUT_API_KEY", "private-test-key")
    requests = serve(blockscout([
        {"address": ETH_ONE, "value": "1000000000000000000000000000001"},
        {"address": ETH_TWO, "value": "1000000000000000000000000000002"},
        {"address": "0x000000000000000000000000000000000000dEaD", "value": "9999999999999999999999999999999"},
    ]))
    result = whales.fetch_holder_observation("PEPE")
    assert result["holders"] == [
        {"wallet": ETH_TWO, "balance": "1000000000000000000000000000002"},
        {"wallet": ETH_ONE.lower(), "balance": "1000000000000000000000000000001"},
    ]
    assert result["tracked_count"] == 2
    assert result["excluded_count"] == 1
    assert result["fetched_count"] == 3
    assert result["http_status"] == 200 and result["elapsed_ms"] >= 0
    assert result["source"] == "blockscout" and result["daily_source"] is False
    assert result["observed_at"] and result["source_label"]
    assert "apikey" not in result["source_url"] and "private-test-key" not in json.dumps(result)
    assert requests[0].url.params["apikey"] == "private-test-key"
    assert requests[0].url.params["offset"] == "50"
    assert requests[0].extensions["timeout"]["read"] == 12


def test_xrpscan_sorts_full_upstream_list_without_float_rounding_and_preserves_case(serve, monkeypatch):
    monkeypatch.setenv("WHALE_TOP_N", "1")
    serve([
        {"account": XRP_ONE, "balance": 9007199254740992},
        {"account": XRP_TWO, "balance": 9007199254740993},
    ])
    result = whales.fetch_holder_observation("XRP")
    assert result["holders"] == [{"wallet": XRP_TWO, "balance": "9007199254740993"}]
    assert result["fetched_count"] == 2 and result["tracked_count"] == 1
    assert result["daily_source"] is True
    assert result["source_url"] == "https://api.xrpscan.com/api/v1/balances"


@pytest.mark.parametrize("balance", [None, "", "not-a-number", "1.2", "1e9", "NaN", "Infinity", "-1", -1, True, 1.0, " 1", "+1"])
@pytest.mark.parametrize("coin", ["PEPE", "XRP"])
def test_invalid_balance_aborts_observation_instead_of_becoming_zero(serve, coin, balance):
    payload = blockscout([{"address": ETH_ONE, "value": balance}]) if coin == "PEPE" else [{"account": XRP_ONE, "balance": balance}]
    serve(payload)
    with pytest.raises(whales.OnchainSourceError) as caught:
        whales.fetch_holder_observation(coin)
    assert caught.value.code == "invalid_response"
    assert caught.value.http_status == 200


@pytest.mark.parametrize("coin,payload", [
    ("PEPE", blockscout([{"address": ETH_ONE, "value": "1"}, {"address": ETH_ONE.lower(), "value": "2"}])),
    ("XRP", [{"account": XRP_ONE, "balance": 1}, {"account": XRP_ONE, "balance": 2}]),
    ("PEPE", blockscout([{"address": "0xnot-an-address", "value": "1"}])),
    ("XRP", [{"account": "not-an-account", "balance": 1}]),
    ("PEPE", blockscout([{"address": ETH_ONE, "value": "1"}, None])),
    ("XRP", [{"account": XRP_ONE, "balance": 1}, None]),
])
def test_duplicate_or_malformed_row_aborts_whole_observation(serve, coin, payload):
    serve(payload)
    with pytest.raises(whales.OnchainSourceError, match="invalid_response"):
        whales.fetch_holder_observation(coin)


@pytest.mark.parametrize("payload", [
    {"status": "0", "message": "NOTOK", "result": []},
    {"status": "0", "message": "Max rate limit reached", "result": "private upstream body"},
    {"status": "1", "message": "NOTOK", "result": []},
    {"result": []}, [], None,
])
def test_blockscout_http_200_failure_is_not_empty_success(serve, payload):
    serve(payload)
    with pytest.raises(whales.OnchainSourceError) as caught:
        whales.fetch_holder_observation("PEPE")
    assert caught.value.http_status == 200
    assert "private upstream body" not in str(caught.value)


@pytest.mark.parametrize("coin,payload", [("PEPE", blockscout([])), ("XRP", [])])
def test_empty_upstream_response_does_not_replace_existing_baseline(serve, coin, payload):
    serve(payload)
    with pytest.raises(whales.OnchainSourceError, match="empty_response"):
        whales.fetch_holder_observation(coin)


def test_rate_limit_keeps_retry_after_and_omits_upstream_body(serve):
    serve(status=429, headers={"Retry-After": "120"}, content=b"secret query string")
    with pytest.raises(whales.OnchainSourceError) as caught:
        whales.fetch_holder_observation("XRP")
    assert caught.value.code == "rate_limited"
    assert caught.value.http_status == 429
    assert caught.value.retry_after_seconds == 120
    assert "secret" not in str(caught.value)


@pytest.mark.parametrize("retry_after,expected", [("86400", 86400), ("172800", 86400), ("bad", 300), ("-5", 1)])
def test_onchain_rate_limit_honors_day_long_retry_after_with_bounds(serve, retry_after, expected):
    serve(status=429, headers={"Retry-After": retry_after})
    with pytest.raises(whales.OnchainSourceError) as caught:
        whales.fetch_holder_observation("XRP")
    assert caught.value.retry_after_seconds == expected


def test_onchain_http_date_retry_after_honors_a_day(serve, monkeypatch):
    from email.utils import formatdate
    now = 1_800_000_000
    monkeypatch.setattr(whales.time, "time", lambda: now)
    serve(status=429, headers={"Retry-After": formatdate(now + 86400, usegmt=True)})
    with pytest.raises(whales.OnchainSourceError) as caught:
        whales.fetch_holder_observation("PEPE")
    assert caught.value.retry_after_seconds == 86400


def test_existing_aggregate_trade_retry_after_default_bound_is_preserved():
    assert whales._retry_after_seconds("86400", default=60) == 3600


@pytest.mark.parametrize("status", [301, 403, 500])
def test_http_failure_is_typed(serve, status):
    serve(status=status)
    with pytest.raises(whales.OnchainSourceError) as caught:
        whales.fetch_holder_observation("XRP")
    assert caught.value.code == "http_error" and caught.value.http_status == status


@pytest.mark.parametrize("exception,code", [(httpx.ReadTimeout, "timeout"), (httpx.ConnectError, "network_error")])
def test_http_exception_is_safe(serve, exception, code):
    def fail(request):
        raise exception("secret query string", request=request)
    serve(handler=fail)
    with pytest.raises(whales.OnchainSourceError) as caught:
        whales.fetch_holder_observation("XRP")
    assert caught.value.code == code and "secret" not in str(caught.value)


def test_invalid_json_is_typed(serve):
    serve(content=b"{broken")
    with pytest.raises(whales.OnchainSourceError, match="invalid_response"):
        whales.fetch_holder_observation("XRP")


def test_stream_limit_stops_oversized_response_before_consuming_remainder(serve, monkeypatch):
    consumed = []
    monkeypatch.setattr(whales, "ONCHAIN_MAX_RESPONSE_BYTES", 64)

    class Chunks(httpx.SyncByteStream):
        def __iter__(self):
            for i in range(20):
                consumed.append(i)
                yield b"x" * 32

    serve(handler=lambda request: httpx.Response(200, stream=Chunks()))
    with pytest.raises(whales.OnchainSourceError, match="response_too_large"):
        whales.fetch_holder_observation("XRP")
    assert len(consumed) == 3


@pytest.mark.parametrize("raw,expected", [("bad", 50), ("0", 1), ("-5", 1), ("500", 100), ("25", 25)])
def test_top_n_configuration_is_bounded_and_invalid_values_are_safe(monkeypatch, raw, expected):
    monkeypatch.setenv("WHALE_TOP_N", raw)
    config = whales.onchain_configuration()
    assert config["top_n"] == expected
    assert config["refresh_seconds"] == {"PEPE": 600, "WETH": 600, "XRP": 21600}
    assert config["ai_calls"] == 0


@pytest.mark.parametrize("symbol,expected", [
    ("PEPEUSDT", "PEPE"), ("1000PEPEUSDC", "PEPE"), ("ethusdt", "WETH"),
    ("WETHUSDC", "WETH"), ("XRPUSDT", "XRP"), ("CHIPUSDT", None),
    ("XRPBTC", None), ("NOTPEPEUSDT", None), ("", None),
])
def test_supported_pair_mapping_is_explicit(symbol, expected):
    assert whales.supported_coin_for_symbol(symbol) == expected


def test_weth_observation_explicitly_discloses_scope(serve):
    serve(blockscout([{"address": ETH_ONE, "value": "0"}]))
    result = whales.fetch_holder_observation("WETH")
    assert "WETH" in result["scope"] and "ETH" in result["scope"]
    assert result["holders"][0]["balance"] == "0"


def test_xrp_known_exchange_and_configured_addresses_keep_case_sensitive_identity(serve, monkeypatch):
    monkeypatch.setenv("WHALE_EXCLUDE_ADDRESSES", XRP_TWO)
    serve([
        {"account": "rEy8TFcrAPvhpKrwyrscNYyqBGUkE9hKaJ", "balance": 500},
        {"account": XRP_TWO, "balance": 300},
        {"account": XRP_ONE, "balance": 100},
    ])
    result = whales.fetch_holder_observation("XRP")
    assert result["holders"] == [{"wallet": XRP_ONE, "balance": "100"}]
    assert result["excluded_count"] == 2


def test_invalid_row_beyond_selected_top_n_still_aborts_observation(serve, monkeypatch):
    monkeypatch.setenv("WHALE_TOP_N", "1")
    serve([{ "account": XRP_ONE, "balance": 9007199254740993}, {"account": XRP_TWO, "balance": None}])
    with pytest.raises(whales.OnchainSourceError, match="invalid_response"):
        whales.fetch_holder_observation("XRP")


def test_content_length_rejects_oversized_response_without_reading_body(serve):
    consumed = []

    class NeverRead(httpx.SyncByteStream):
        def __iter__(self):
            consumed.append(True)
            yield b"[]"

    serve(handler=lambda request: httpx.Response(200, headers={"Content-Length": str(3 * 1024 * 1024)}, stream=NeverRead()))
    with pytest.raises(whales.OnchainSourceError, match="response_too_large"):
        whales.fetch_holder_observation("XRP")
    assert not consumed


@pytest.mark.parametrize("raw,expected", [("bad", 12), ("NaN", 12), ("inf", 12), ("-1", 1), ("60", 12)])
def test_source_timeout_setting_cannot_disable_the_bound(monkeypatch, raw, expected):
    monkeypatch.setenv("WHALE_HTTP_TIMEOUT", raw)
    assert whales.onchain_configuration()["timeout_seconds"] == expected


def test_unsupported_coin_does_not_request_any_source(serve):
    requests = serve([])
    with pytest.raises(ValueError, match="unsupported on-chain coin"):
        whales.fetch_holder_observation("CHIP")
    assert not requests
