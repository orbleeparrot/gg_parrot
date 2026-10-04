"""해설 API 는 서버가 모은 근거만 AI 에 넘기고, 응답에 출처를 함께 내려준다.

캔들 조회·뉴스 조회·AI 는 모두 막아 둔다 — 시험은 네트워크에 붙지 않는다. explain 이 받은
payload 는 상태에 남겨, 서버가 무엇을 모델에 넘겼는지를 직접 본다.
"""
import json
import math
from datetime import timezone

import pandas as pd
import pytest
from fastapi.testclient import TestClient

from app import evidence, main, validate_explain
from app.data import NoSpotDataError, krw

SPIKE_UP, SPIKE_DOWN = 80, 100  # 합성 캔들에서 크게 튀는 두 날(색인)
BENCHMARKS = {"BTCUSDT", "KRW-BTC"}
# 두 번째 조회(기준 종목)는 코인이 오른 날(+20%) 같이 +12% 올라 '시장', 코인이 내린 날(-15%) 반대로 +5% 올라 '종목'이다.
BENCH_UP, BENCH_DOWN = 0.12, 0.05


def macro_body(symbol="BTCUSDT", exchange=None, **extra):
    body = {"symbol": symbol, "rule_type": "A", "candle_interval": "1d",
            "params": {"take_profit_pct": 5, "initial_capital": 1000000},
            "period": {"preset": "1y"}, **extra}
    if exchange:
        body["exchange"] = exchange
    return body


SUMMARY = {"final_return_pct": 142.0, "mdd_pct": 31.0, "total_trades": 14,
           "windows": [180.0, 5.0, -12.0, 3.0],
           "warnings": ["한_구간_집중", "표본_부족"]}
BODY = {"macro": macro_body(), "summary": SUMMARY}

# /api/validate 응답이 그대로 오는 모양 — 지표가 result · concentration 아래에 들어 있다.
VALIDATE_RESPONSE = {
    "result": {"final_return_pct": 142.0, "buy_hold_return_pct": 38.5, "mdd_pct": 31.0,
               "total_trades": 14, "top_trade_share_pct": 62.5, "win_rate_pct": 50.0,
               "equity_curve": [{"t": 1, "equity": 1.0}], "trades": [{"id": 1}]},
    "monthly": [{"month": "2025-10", "return_pct": 3.0}],
    "concentration": {"top_month_share_pct": 71.0, "months": 12},
    "drawdown": {"start": "2026-01-01"},
    "sortino": 0.9, "calmar": None,
    "windows": [{"index": 1, "start": "a", "end": "b", "return_pct": 180.0, "trades": 3, "error": ""},
                {"index": 2, "start": "a", "end": "b", "return_pct": None, "trades": 0, "error": "X"}],
    "warnings": ["한_구간_집중", "표본_부족"],
}


def candles(offset_hours=0, rows=120, up=SPIKE_UP, down=SPIKE_DOWN, up_move=0.2, down_move=-0.15):
    """일봉 합성 프레임. 두 날이 크게 튄다. offset_hours 가 -9 면 KST 0 시에 열리는 봉(빗썸)이다.

    up_move · down_move 는 그 두 날의 등락(비율)이다 — 기준 종목 프레임은 이것을 달리 줘서 코인과 구별한다."""
    stamps = pd.date_range("2026-01-01", periods=rows, freq="1D", tz="UTC") + pd.Timedelta(hours=offset_hours)
    close, level = [], 100.0
    for i in range(rows):
        level *= 1 + 0.004 * math.sin(i * 1.7) + (up_move if i == up else 0) + (down_move if i == down else 0)
        close.append(level)
    volume = [40.0 if i in (up, down) else 10.0 for i in range(rows)]
    return pd.DataFrame({"timestamp": stamps, "open": close, "high": close, "low": close,
                         "close": close, "volume": volume})


@pytest.fixture(autouse=True)
def fresh_limiter():
    """IP 별 호출 제한은 프로세스 안에 남는다 — 시험끼리 횟수를 나눠 쓰지 않게 비운다."""
    main._explain_limiter._events.clear()
    yield
    main._explain_limiter._events.clear()


@pytest.fixture
def client(monkeypatch):
    """캔들 · 헤드라인 · 해설을 모두 막은 클라이언트. 서버가 한 일을 state_ 에 남긴다."""
    state = {"fetched": [], "headlines": [], "payloads": [], "offset": 0, "fail": set(), "tz": []}

    def fake_fetch(macro, start_ms, end_ms):
        state["fetched"].append((macro.symbol, macro.exchange, macro.candle_interval))
        if macro.symbol in state["fail"]:
            raise NoSpotDataError("no candles")
        if len(state["fetched"]) > 1 and macro.symbol in BENCHMARKS:
            # 두 번째 조회(기준 종목)는 코인과 다른 프레임을 준다 — 서버가 엉뚱한 프레임을 넘기면 값이 달라져 드러난다.
            return candles(state["offset"], up_move=BENCH_UP, down_move=BENCH_DOWN), "synthetic"
        return candles(state["offset"]), "synthetic"

    real_market_evidence = evidence.market_evidence

    def spy_market_evidence(*args, **kwargs):
        state["tz"].append(kwargs.get("tz"))
        return real_market_evidence(*args, **kwargs)

    def fake_headlines(asset, date):
        state["headlines"].append((asset, date))
        return {"items": [{"title": f"{date} 기사", "source": "wire", "url": "https://x.test",
                           "published_ms": 1}], "found": True, "reason": ""}

    def fake_explain(payload):
        state["payloads"].append(payload)
        return {"text": "거래가 적습니다.", "source": "ai"}

    monkeypatch.setattr(main, "fetch_klines_for_macro", fake_fetch)
    monkeypatch.setattr(evidence, "headlines", fake_headlines)
    monkeypatch.setattr(evidence, "market_evidence", spy_market_evidence)
    monkeypatch.setattr(validate_explain, "explain", fake_explain)
    test_client = TestClient(main.app)
    test_client.state_ = state
    return test_client


def day(index):
    return (pd.Timestamp("2026-01-01") + pd.Timedelta(days=index)).date().isoformat()


def post(client, macro=None, summary=None):
    return client.post("/api/validate/explain",
                       json={"macro": macro or BODY["macro"], "summary": summary or SUMMARY})


def all_plain(value):
    """json.dumps 가 그대로 받는 값만 있는지 — numpy 정수 · nan 이 섞였는지 본다."""
    if isinstance(value, dict):
        return all(isinstance(k, str) and all_plain(v) for k, v in value.items())
    if isinstance(value, list):
        return all(all_plain(v) for v in value)
    if isinstance(value, bool) or value is None or isinstance(value, str):
        return True
    if type(value) is int:
        return True
    return type(value) is float and math.isfinite(value)


# --- 기본 계약 -------------------------------------------------------------------

def test_explain_returns_text_with_its_source(client):
    response = post(client)
    assert response.status_code == 200
    body = response.json()
    assert body["text"] == "거래가 적습니다."
    assert body["source"] in ("ai", "fallback")
    assert isinstance(body["evidence"], list) and len(body["evidence"]) == 2
    assert {"date", "change_pct", "volume_ratio", "btc_change_pct", "verdict", "headlines"} <= set(body["evidence"][0])


def test_a_missing_summary_is_rejected(client):
    assert client.post("/api/validate/explain", json={"macro": BODY["macro"]}).status_code == 422


def test_an_invalid_macro_gets_a_korean_422(client):
    response = post(client, macro={"symbol": "BTCUSDT", "rule_type": "ZZZ"})
    assert response.status_code == 422
    assert "매크로 설정을 확인해 주세요" in response.json()["detail"]
    assert not client.state_["payloads"]


def test_a_portfolio_macro_is_rejected(client):
    response = post(client, macro=macro_body(symbols=["BTCUSDT", "ETHUSDT"]))
    assert response.status_code == 422
    assert not client.state_["fetched"]


def test_a_bad_period_is_a_400_in_korean(client):
    response = post(client, macro=macro_body(period={"preset": "no-such-preset"}))
    assert response.status_code == 400
    assert "기간 설정" in response.json()["detail"]


def test_missing_candles_is_a_422(client):
    client.state_["fail"].add("BTCUSDT")
    response = post(client)
    assert response.status_code == 422
    assert not client.state_["payloads"]


# --- 모델에 넘기는 payload ---------------------------------------------------------

def test_a_flat_summary_reaches_the_model_with_its_numbers(client):
    post(client)
    payload = client.state_["payloads"][0]
    assert payload["final_return_pct"] == 142.0 and payload["mdd_pct"] == 31.0
    assert payload["total_trades"] == 14 and payload["windows"] == [180.0, 5.0, -12.0, 3.0]
    assert payload["warnings"] == ["한_구간_집중", "표본_부족"]
    assert len(payload["evidence"]) == 2


def test_a_validate_response_is_flattened_not_dropped(client):
    """/api/validate 응답은 지표를 result · concentration 아래에 둔다 — 그대로 넘기면 숫자가 다 사라진다."""
    assert post(client, summary=VALIDATE_RESPONSE).status_code == 200
    payload = client.state_["payloads"][0]
    assert payload["final_return_pct"] == 142.0
    assert payload["buy_hold_return_pct"] == 38.5
    assert payload["mdd_pct"] == 31.0 and payload["total_trades"] == 14
    assert payload["top_trade_share_pct"] == 62.5 and payload["top_month_share_pct"] == 71.0
    assert payload["sortino"] == 0.9
    assert payload["calmar"] is None, "못 잰 칼마는 None 으로 남는다"
    assert payload["windows"] == [180.0, None], "구간은 수익률 숫자만 넘긴다"


def test_only_judged_fields_reach_the_model(client):
    noisy = {**VALIDATE_RESPONSE, "request_id": "r-1", "created_at": "2026-10-04", "note": "마음대로 쓴 문장"}
    post(client, summary=noisy)
    allowed = {"final_return_pct", "buy_hold_return_pct", "mdd_pct", "total_trades", "calmar", "sortino",
               "top_month_share_pct", "top_trade_share_pct", "windows", "warnings", "evidence"}
    assert set(client.state_["payloads"][0]) <= allowed


def test_payload_and_response_hold_only_plain_json_values(client):
    response = post(client, summary=VALIDATE_RESPONSE)
    payload = client.state_["payloads"][0]
    assert all_plain(payload), payload
    assert all_plain(response.json())
    json.dumps(payload, allow_nan=False)
    json.dumps(response.json(), allow_nan=False)


def test_unreadable_numbers_and_unknown_warnings_are_dropped(client):
    summary = {"final_return_pct": "많이", "total_trades": True,
               "windows": ["x", 3.0], "warnings": ["한_구간_집중", "지어낸_경고", 7]}
    assert post(client, summary=summary).status_code == 200
    payload = client.state_["payloads"][0]
    assert payload.get("final_return_pct") is None and payload.get("total_trades") is None
    assert payload["windows"] == [None, 3.0]
    assert payload["warnings"] == ["한_구간_집중"]


# --- 근거: 어느 시장과 견주고, 어느 날짜로 찾는가 ----------------------------------------

def test_binance_altcoin_is_compared_with_btcusdt(client):
    post(client, macro=macro_body("ETHUSDT"))
    assert [f[0] for f in client.state_["fetched"]] == ["ETHUSDT", "BTCUSDT"]


def test_the_benchmark_frame_is_what_the_verdict_is_judged_against(client):
    """기준 종목 프레임이 실제로 쓰인다 — 엉뚱한 프레임이나 None 을 넘기면 값이 달라진다."""
    rows = {row["date"]: row for row in post(client, macro=macro_body("ETHUSDT")).json()["evidence"]}
    up, down = rows[day(SPIKE_UP)], rows[day(SPIKE_DOWN)]
    assert up["change_pct"] > 15 and 9 < up["btc_change_pct"] < 15 and up["verdict"] == "시장"
    assert down["change_pct"] < -10 and 3 < down["btc_change_pct"] < 8 and down["verdict"] == "종목"
    assert all(row["btc_change_pct"] != row["change_pct"] for row in rows.values()), "코인 자신의 프레임이 아니다"


@pytest.mark.parametrize("exchange, symbol, benchmark", [
    ("bithumb", "KRW-ETH", "KRW-BTC"), ("upbit", "KRW-ETH", "KRW-BTC"), (None, "ETHUSDT", "BTCUSDT")])
def test_the_benchmark_is_fetched_from_the_macros_own_exchange(client, exchange, symbol, benchmark):
    """국내 거래소는 모두 KRW-BTC 와 견준다 — 조건이 한 거래소만 가리키면 나머지는 USDT 시장을 받는다."""
    post(client, macro=macro_body(symbol, exchange=exchange))
    venue = exchange or "binance"
    assert client.state_["fetched"] == [(symbol, venue, "1d"), (benchmark, venue, "1d")]


@pytest.mark.parametrize("exchange, symbol, expected", [
    ("bithumb", "KRW-BTC", krw._KST), ("upbit", "KRW-BTC", timezone.utc), (None, "BTCUSDT", timezone.utc)])
def test_only_bithumb_dates_are_labelled_in_kst(client, exchange, symbol, expected):
    """interval_grid_offset: 빗썸 봉은 KST 경계, 업비트는 UTC 경계. 날짜 기준 규칙 자체를 본다."""
    post(client, macro=macro_body(symbol, exchange=exchange))
    assert client.state_["tz"] == [expected]


def test_the_benchmark_frame_is_reused_when_the_asset_is_the_benchmark(client):
    post(client)
    assert [f[0] for f in client.state_["fetched"]] == ["BTCUSDT"]


def test_an_upbit_macro_is_compared_with_krw_btc_not_btcusdt(client):
    """국내 거래소 매크로에 USDT 기준을 쓰면 검증을 거치지 않고 엉뚱한 시장을 받아 온다."""
    response = post(client, macro=macro_body("KRW-ETH", exchange="upbit"))
    assert response.status_code == 200
    assert set(response.json()) == {"text", "source", "evidence"}
    assert [f[0] for f in client.state_["fetched"]] == ["KRW-ETH", "KRW-BTC"]
    assert {f[1] for f in client.state_["fetched"]} == {"upbit"}
    assert "BTCUSDT" not in [f[0] for f in client.state_["fetched"]]


def test_an_upbit_krw_btc_macro_fetches_once_and_never_btcusdt(client):
    response = post(client, macro=macro_body("KRW-BTC", exchange="upbit"))
    assert response.status_code == 200
    assert [f[0] for f in client.state_["fetched"]] == ["KRW-BTC"]


def test_a_failing_benchmark_leaves_the_verdict_empty_not_the_request_failed(client):
    client.state_["fail"].add("BTCUSDT")
    response = post(client, macro=macro_body("ETHUSDT"))
    assert response.status_code == 200
    rows = response.json()["evidence"]
    assert rows and all(row["verdict"] == "" and row["btc_change_pct"] is None for row in rows)


def test_the_expected_benchmark_failure_is_logged_without_a_stack_trace(client, caplog):
    """기준 종목 자료가 없는 것은 기대한 실패다 — 호출마다 스택을 남기지 않고 종목 · 거래소 · 문구만 남긴다."""
    client.state_["fail"].add("BTCUSDT")
    with caplog.at_level("WARNING"):
        post(client, macro=macro_body("ETHUSDT"))
    records = [r for r in caplog.records if "benchmark candles unavailable" in r.getMessage()]
    assert records and all(r.exc_info is None for r in records)
    assert "BTCUSDT" in records[0].getMessage() and "no candles" in records[0].getMessage()


def test_evidence_is_built_from_daily_candles_even_for_an_hourly_macro(client):
    post(client, macro=macro_body("ETHUSDT", candle_interval="1h"))
    assert {f[2] for f in client.state_["fetched"]} == {"1d"}


@pytest.mark.parametrize("symbol, exchange", [("BTCUSDT", None), ("KRW-BTC", "upbit"), ("KRW-BTC", "bithumb"),
                                              ("BTCFDUSD", None)])
def test_headlines_are_looked_up_by_the_coin_not_the_market_pair(client, symbol, exchange):
    post(client, macro=macro_body(symbol, exchange=exchange))
    assert client.state_["headlines"] and {asset for asset, _ in client.state_["headlines"]} == {"BTC"}


def test_bithumb_dates_follow_kst_and_upbit_dates_follow_utc(client):
    """빗썸 일봉은 KST 0 시(= UTC 전날 15 시)에 열린다. UTC 날짜로 매기면 하루 앞서 엉뚱한 날 뉴스를 찾는다."""
    client.state_["offset"] = -9
    bithumb = post(client, macro=macro_body("KRW-BTC", exchange="bithumb")).json()["evidence"]
    assert {row["date"] for row in bithumb} == {day(SPIKE_UP), day(SPIKE_DOWN)}
    client.state_["offset"] = 0
    upbit = post(client, macro=macro_body("KRW-BTC", exchange="upbit")).json()["evidence"]
    assert {row["date"] for row in upbit} == {day(SPIKE_UP), day(SPIKE_DOWN)}


def test_the_number_of_news_lookups_is_capped(client, monkeypatch):
    monkeypatch.setattr(main, "EXPLAIN_EVIDENCE_LIMIT", 1)
    response = post(client)
    assert len(response.json()["evidence"]) == 1 and len(client.state_["headlines"]) == 1


def test_no_headline_found_is_passed_on_as_empty_not_invented(client, monkeypatch):
    monkeypatch.setattr(evidence, "headlines",
                        lambda asset, date: {"items": [], "found": False, "reason": "보존_범위_밖"})
    post(client)
    for row in client.state_["payloads"][0]["evidence"]:
        assert row["headlines"] == {"items": [], "found": False, "reason": "보존_범위_밖"}


# --- 호출 상한 ---------------------------------------------------------------------

def test_the_per_minute_ceiling_bounds_model_calls(client):
    limit = main._explain_limiter.limit
    for _ in range(limit):
        assert post(client).status_code == 200
    response = post(client)
    assert response.status_code == 429 and response.headers["Retry-After"]
    assert len(client.state_["payloads"]) == limit, "막힌 요청은 모델까지 가지 않는다"
