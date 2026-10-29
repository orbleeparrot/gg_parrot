"""물어볼까 종목 선정에 쓰는 30일 일봉 공급.

``ask_candidates.build_pool`` 은 순수 함수로 남아야 한다(측정기가 과거 구간의 종목
선정을 재현할 수 있어야 한다). 그래서 일봉은 여기서 받아 주입한다.

**사용자 요청을 막지 않는다.** 재정렬할 종목이 상위 ``LIQUID_TOP``(40)개이고 국내
거래소는 요청 간격이 0.15초(``krw.REQUEST_GAP_SECONDS``)라 한 번 받는 데 최소 6초다.
캐시에 없으면 **빈 칸을 주고 백그라운드로 적립한다** — 빈 칸을 받은 ``build_pool`` 은
기존 24시간 경로를 그대로 타므로, 기능이 켜진 직후 몇 번은 예전 목록이 나온다.
의도된 동작이다.

일봉은 ``MARKET_CACHE_DIR`` 의 SQLite 캐시에도 쌓이므로(``krw.get_klines`` ·
``binance.get_klines``) 6초짜리 첫 수집은 거래소마다 하루 한 번 수준의 비용이다.
"""
from __future__ import annotations

import os
import threading
import time

from .ask_candidates import liquid_symbols
from .cache_runtime import ResponseCache
from engine.exchanges import is_domestic, normalize_exchange

# 몇 개의 일별 종가로 판단할지. ``chop_pct`` · ``range_position_pct`` 가 쓴다.
DAYS = 30
# 일봉은 하루에 한 번만 바뀌지만, 마지막 점으로 붙이는 '지금 가격' 은 티커에서 오므로
# 이 TTL 이 신선도를 좌우하지 않는다. 길게 둬서 수집을 아낀다.
CACHE_SECONDS = 1800.0
# 만료돼도 하루까지는 옛 값을 쓰면서 뒤에서 갱신한다 — 갱신 중에 빈 칸을 주지 않는다.
STALE_SECONDS = 86_400.0

_cache = ResponseCache("ask-daily", max_entries=4, max_bytes=4_000_000, retry_seconds=60)
_warming: set[str] = set()
_warming_lock = threading.Lock()


def _key(exchange: str) -> str:
    return f"daily:{normalize_exchange(exchange)}"


def _fetch_closes(symbol: str, exchange: str) -> list[float]:
    """그 종목의 최근 마감 일봉 종가들. 실패하면 던진다 — 호출자가 그 종목을 뺀다."""
    from .data import binance, krw

    end_ms = int(time.time() * 1000)
    start_ms = end_ms - (DAYS + 5) * 86_400_000
    if is_domestic(exchange):
        frame, _source = krw.get_klines(symbol, start_ms, end_ms, "1d", exchange=exchange)
    else:
        # allow_synthetic=False 가 **반드시** 필요하다. 기본값이 True 라 봉이 없으면
        # 지어낸 캔들을 돌려준다 — 갓 상장해 30일치가 없는 코인은 거래대금 상위 40에
        # 충분히 들어온다. 사실로 답하려고 만든 기능이 허구로 답하게 된다.
        # 다른 운영 호출부도 전부 False 를 넘긴다(marketdata.py · data/__init__.py).
        frame, _source = binance.get_klines(symbol, start_ms, end_ms, "1d",
                                            allow_synthetic=False)
    closes = [float(value) for value in frame["close"].tolist()]
    return closes[-DAYS:]


def _price_now(tickers: list[dict]) -> dict[str, float]:
    prices: dict[str, float] = {}
    for row in tickers or ():
        try:
            price = float(row["lastPrice"])
        except (KeyError, TypeError, ValueError):
            continue
        if price > 0:
            prices[str(row.get("symbol", ""))] = price
    return prices


def warm(tickers: list[dict], *, exchange: str = "binance", fetch=None) -> dict[str, list[float]]:
    """일봉을 실제로 받아 캐시에 넣는다. **느리다** — 백그라운드에서만 부른다.

    한 종목이 실패하면 그 종목만 뺀다. 거래소는 체결이 없는 구간의 봉을 아예 빼고
    주고(krw.py 머리글) 갓 상장한 종목은 30일치가 없다.
    """
    getter = fetch or _fetch_closes
    symbols = liquid_symbols(tickers, exchange=exchange)

    def load() -> dict[str, list[float]]:
        out: dict[str, list[float]] = {}
        for symbol in symbols:
            try:
                closes = getter(symbol, exchange)
            except Exception:
                continue
            if closes:
                out[symbol] = list(closes)
        return out

    payload, _state = _cache.get_or_load(_key(exchange), load, ttl=CACHE_SECONDS,
                                         stale_ttl=STALE_SECONDS, wait_for_refresh=True)
    return payload


def warming_enabled() -> bool:
    """백그라운드 일봉 적립을 할지. ``ASK_DAILY_WARM=false`` 로 끈다.

    운영에서 수집이 말썽일 때 배포 없이 끌 수 있어야 한다 — 끄면 30일 창도 같이
    꺼지고 기존 24시간 경로로 돌아간다(기능이 사라지는 게 아니라 예전 동작이 된다).
    시험도 이 스위치로 네트워크 접근을 막는다(tests/conftest.py).
    """
    return str(os.environ.get("ASK_DAILY_WARM", "true")).strip().lower() in {"1", "true", "yes"}


def _warm_later(tickers: list[dict], exchange: str) -> None:
    """한 거래소에 한 번만 띄운다 — 요청마다 스레드가 쌓이지 않게."""
    if not warming_enabled():
        return
    key = _key(exchange)
    with _warming_lock:
        if key in _warming:
            return
        _warming.add(key)

    def run() -> None:
        try:
            warm(tickers, exchange=exchange)
        except Exception:
            pass                       # 다음 요청이 다시 띄운다.
        finally:
            with _warming_lock:
                _warming.discard(key)

    try:
        threading.Thread(target=run, name="ask-daily-warm", daemon=True).start()
    except Exception:
        # 스레드를 못 띄웠는데 표시만 남으면 그 거래소는 프로세스가 사는 동안
        # 다시는 적립되지 않는다.
        with _warming_lock:
            _warming.discard(key)


def daily_closes(tickers: list[dict], *, exchange: str = "binance",
                 warm_later: bool = True, fetch=None) -> dict[str, list[float]]:
    """심볼 → 최근 일별 종가 + **마지막 점은 지금 가격**. 캐시에 없으면 빈 칸.

    마지막을 지금 가격으로 두는 이유: 재는 것이 "지금 고점 근처인가" 이고, 거래소
    일봉은 마감된 봉만 주므로 오늘치가 빠져 있다. 지금 가격은 티커에서 온다 —
    ``build_pool`` 이 순수 함수로 남으려면 '평가 시점의 가격' 을 호출자가 넣어야 하고,
    측정기도 그 시점의 종가를 같은 자리에 넣어 과거를 재현한다.

    ``fetch`` 는 시험용 주입구다.
    """
    cached = _cache.peek(_key(exchange))
    if cached is None:
        if warm_later:
            _warm_later(tickers, exchange)
        return {}
    closes_by_symbol, state = cached
    # **만료됐으면 옛 값을 주면서 뒤에서 갱신한다.** peek 은 만료된 값에도 "stale" 을
    # 주고 I/O 를 시작하지 않으므로, 여기서 안 띄우면 TTL 과 stale 한도 사이에서는
    # 영원히 안 받는다. 한도가 지나면 빈 칸이 되어 예전 24시간 목록으로 돌아간다 —
    # 고친 불만이 하루에 한 번씩 되살아난다.
    if warm_later and state == "stale":
        _warm_later(tickers, exchange)
    prices = _price_now(tickers)
    out: dict[str, list[float]] = {}
    for symbol, closes in (closes_by_symbol or {}).items():
        price = prices.get(symbol)
        out[symbol] = list(closes) + ([price] if price else [])
    return out


def forget() -> None:
    """시험용 — 캐시를 비운다."""
    _cache.clear()
    with _warming_lock:
        _warming.clear()
