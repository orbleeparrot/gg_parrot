"""물어볼까 종목 선정에 쓰는 30일 일봉 공급 (2026-10-29).

왜 캐시인가: 재정렬할 종목이 상위 40개이고 국내 거래소는 요청 간격이 0.15초
(``krw.REQUEST_GAP_SECONDS``)라 한 번 받는 데 최소 6초다. 사용자 요청 경로에서
동기로 받아서는 안 된다. **캐시에 없으면 빈 칸을 주고 백그라운드로 적립한다** —
빈 칸을 받은 ``build_pool`` 은 기존 24시간 경로를 그대로 탄다.
"""
from __future__ import annotations

import time

from app import ask_daily


def _t(symbol, qvol, price):
    return {"symbol": symbol, "priceChangePercent": "1.0", "lastPrice": str(price),
            "quoteVolume": str(qvol), "highPrice": str(price * 1.01), "lowPrice": str(price * 0.99)}


def _tickers():
    return [_t("AAAUSDT", 900_000_000, 150.0), _t("BBBUSDT", 800_000_000, 250.0)]


def setup_function():
    ask_daily.forget()


def test_a_cold_cache_gives_nothing_and_does_not_fetch():
    calls = []

    def never(symbol, exchange):
        calls.append(symbol)
        return [1.0, 2.0]

    got = ask_daily.daily_closes(_tickers(), exchange="binance", warm_later=False, fetch=never)
    assert got == {}
    assert calls == []


def test_a_warm_cache_gives_closes_with_the_price_now_last():
    # '지금 고점 근처인가' 를 재는 것이므로 마지막 점은 지금 가격이어야 한다.
    # 거래소 일봉은 **마감된** 봉만 주므로(krw.get_klines) 오늘치가 빠져 있다.
    ask_daily.warm(_tickers(), exchange="binance", fetch=lambda s, e: [100.0, 110.0, 120.0])
    got = ask_daily.daily_closes(_tickers(), exchange="binance", warm_later=False)
    assert got["AAAUSDT"] == [100.0, 110.0, 120.0, 150.0]
    assert got["BBBUSDT"] == [100.0, 110.0, 120.0, 250.0]


def test_a_symbol_whose_candles_failed_is_left_out():
    # 빠진 종목은 build_pool 에서 기존 headroom 순서로 뒤에 붙는다 — 한 종목의
    # 시세 실패가 목록 전체를 비우지 않는다.
    def only_aaa(symbol, exchange):
        if symbol != "AAAUSDT":
            raise RuntimeError("no candles")
        return [100.0, 110.0]

    ask_daily.warm(_tickers(), exchange="binance", fetch=only_aaa)
    got = ask_daily.daily_closes(_tickers(), exchange="binance", warm_later=False)
    assert "AAAUSDT" in got and "BBBUSDT" not in got


def test_the_cache_is_kept_per_exchange():
    ask_daily.warm(_tickers(), exchange="binance", fetch=lambda s, e: [100.0, 110.0])
    assert ask_daily.daily_closes(_tickers(), exchange="upbit", warm_later=False) == {}


def test_warming_asks_only_for_the_symbols_that_need_re_ranking():
    asked = []
    ask_daily.warm(_tickers(), exchange="binance",
                   fetch=lambda s, e: asked.append(s) or [100.0, 110.0])
    assert sorted(asked) == ["AAAUSDT", "BBBUSDT"]


class _Spy:
    """스레드가 떴는지만 본다 — 실제로 돌리지 않는다."""

    started = []

    def __init__(self, **kw):
        self.kw = kw

    def start(self):
        _Spy.started.append(self.kw)


def test_background_warming_can_be_switched_off(monkeypatch):
    # 운영에서 수집이 말썽이면 끌 수 있어야 하고, 시험은 이 스위치로 네트워크를 막는다.
    _Spy.started = []
    monkeypatch.setenv("ASK_DAILY_WARM", "false")
    monkeypatch.setattr(ask_daily.threading, "Thread", _Spy)
    assert ask_daily.daily_closes(_tickers(), exchange="binance") == {}
    assert _Spy.started == []


def test_background_warming_starts_once_when_switched_on(monkeypatch):
    _Spy.started = []
    monkeypatch.setenv("ASK_DAILY_WARM", "true")
    monkeypatch.setattr(ask_daily.threading, "Thread", _Spy)
    ask_daily.daily_closes(_tickers(), exchange="binance")
    ask_daily.daily_closes(_tickers(), exchange="binance")
    assert len(_Spy.started) == 1


def test_binance_candles_are_never_synthetic(monkeypatch):
    # binance.get_klines 는 allow_synthetic 기본값이 True 다 — 봉이 없으면 **지어낸다**.
    # 갓 상장해 30일치가 없는 코인은 거래대금 상위 40에 충분히 들어온다. 합성 봉이
    # 들어오면 존재하지 않는 가격으로 종목을 고르고, 카드가 그 가격을 두고 "한 달
    # 범위의 아래쪽" 이라고 말한다. 사실로 답하려고 만든 기능이 허구로 답하게 된다.
    from app.data import binance
    seen = {}

    def fake(symbol, start_ms, end_ms, interval="1d", **kw):
        seen.update(kw)
        raise RuntimeError("stop here")

    monkeypatch.setattr(binance, "get_klines", fake)
    try:
        ask_daily._fetch_closes("AAAUSDT", "binance")
    except RuntimeError:
        pass
    assert seen.get("allow_synthetic") is False


def test_a_stale_cache_is_refreshed_in_the_background(monkeypatch):
    # peek 은 만료된 값에도 "stale" 을 주고 I/O 를 시작하지 않는다. 그때 갱신을 안 띄우면
    # TTL 과 stale 한도 사이에서는 영원히 안 받고, 한도가 지나면 빈 칸이 되어 예전
    # 24시간 목록으로 돌아간다 — 고친 불만이 하루에 한 번씩 되살아난다.
    _Spy.started = []
    monkeypatch.setenv("ASK_DAILY_WARM", "true")
    monkeypatch.setattr(ask_daily, "CACHE_SECONDS", 0.02)
    ask_daily.warm(_tickers(), exchange="binance", fetch=lambda s, e: [100.0, 110.0])
    time.sleep(0.08)                      # TTL 은 지나고 stale 한도 안에 있는 상태
    monkeypatch.setattr(ask_daily.threading, "Thread", _Spy)
    got = ask_daily.daily_closes(_tickers(), exchange="binance")
    assert got, "갱신하는 동안에도 옛 값은 그대로 준다"
    assert len(_Spy.started) == 1, "뒤에서 갱신을 띄워야 한다"
