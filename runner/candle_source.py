"""실행기가 거래소에서 **직접** 받는 마감봉.

지금은 서버가 봉을 받아 판단하고 명령만 내려보낸다. 그 구조에서는 우리 서버가 잠깐
멈추면 사용자는 자기 PC 가 멀쩡한데도 매수 · 매도 시점을 놓친다 — 원인이 전적으로 우리
쪽에 있고 사용자에게는 보이지도 않는다. 그래서 봉도 판단도 실행기가 스스로 갖는다.

여기서 지키는 것 셋:

* **마감된 봉만 준다.** 진행 중 봉으로 판단하면 같은 봉에서 샀다 팔았다 한다. 거래소마다
  '진행 중' 을 표시하는 방식이 다르므로 시각으로 가른다 — 봉이 열린 시각 + 간격 ≤ 지금.
* **봉 격자는 공유 엔진이 정한다**(``engine.intervals``). 빗썸 봉은 KST 경계에서 열리므로
  직접 계산하면 일봉이 9시간 어긋난다. 서버와 다른 경계를 쓰면 같은 매크로가 다른 봉을 본다.
* **같은 봉을 두 번 주지 않는다.** 지표는 받은 봉을 누적하므로 중복은 조용히 결과를 바꾼다.

거래소 공개 시세만 쓴다 — 키도 서명도 없다(주문은 ``brokers.py`` 가 한다).
"""
from __future__ import annotations

import time
from datetime import datetime, timedelta, timezone
from typing import Callable, List, Optional

try:  # 패키지 import(시험) / 직접 import(PyInstaller 빌드)
    from .shared_engine import bootstrap_import_path
except ImportError:
    from shared_engine import bootstrap_import_path

bootstrap_import_path()

from engine.intervals import INTERVAL_MS, interval_grid_offset, is_grid_aligned  # noqa: E402

BASES = {
    "binance": "https://api.binance.com",
    "binance_futures": "https://fapi.binance.com",
    "upbit": "https://api.upbit.com",
    "bithumb": "https://api.bithumb.com",
}
TIMEOUT = 10.0
#: 국내 거래소는 한 번에 200개까지 준다. 그보다 많이 필요하면 커서로 거슬러 올라간다.
KRW_PAGE = 200
MAX_PAGES = 10
#: 업비트 · 빗썸이 지원하는 분봉 단위. 여기 없는 간격은 애초에 매크로가 만들어지지 않는다.
KRW_MINUTES = (1, 3, 5, 10, 15, 30, 60, 240)
_KST = timezone(timedelta(hours=9))


class CandleFetchError(RuntimeError):
    """거래소에서 봉을 받지 못했다. 호출자가 다시 시도하거나 사용자에게 알린다."""


class Candle(tuple):
    """``(t, o, h, l, c, v)`` — 엔진이 받는 모양 그대로. t 는 봉이 **열린** 시각(ms, UTC)."""

    __slots__ = ()

    def __new__(cls, t, o, h, low, c, v=None):
        return super().__new__(cls, (int(t), float(o), float(h), float(low), float(c),
                                     None if v is None else float(v)))

    t = property(lambda self: self[0])
    o = property(lambda self: self[1])
    h = property(lambda self: self[2])
    l = property(lambda self: self[3])  # noqa: E741 — 엔진이 쓰는 이름 그대로
    c = property(lambda self: self[4])
    v = property(lambda self: self[5])


def _now_ms() -> int:
    return int(time.time() * 1000)


def _krw_path(interval: str) -> str:
    if interval == "1d":
        return "/v1/candles/days"
    if interval not in INTERVAL_MS:
        raise CandleFetchError("지원하지 않는 봉 간격이에요: " + str(interval))
    minutes = INTERVAL_MS[interval] // 60_000
    if minutes not in KRW_MINUTES:
        raise CandleFetchError("국내 거래소가 지원하지 않는 봉 간격이에요: " + str(interval))
    return "/v1/candles/minutes/" + str(minutes)


def _krw_cursor(stamp_ms: int, exchange: str) -> str:
    """``to`` 커서. 업비트는 UTC ISO(오프셋 포함), 빗썸은 KST 를 오프셋 없이 쓴다(문서 그대로)."""
    stamp = datetime.fromtimestamp(stamp_ms / 1000, timezone.utc)
    if exchange == "bithumb":
        stamp = stamp.astimezone(_KST).replace(tzinfo=None)
    return stamp.isoformat(timespec="milliseconds")


def _krw_candle(raw: dict, interval: str, exchange: str) -> Candle:
    # ``timestamp`` 는 마지막 체결 시각이지 봉의 시작이 아니다 — 문서화된 UTC 칸만 쓴다.
    value = raw.get("candle_date_time_utc")
    tz = timezone.utc
    if not value:
        value, tz = raw.get("candle_date_time_kst"), _KST
    if not value:
        raise CandleFetchError("거래소 봉에 시각이 없어요.")
    stamp = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
    if stamp.tzinfo is None:
        stamp = stamp.replace(tzinfo=tz)
    t = int(stamp.timestamp() * 1000)
    if not is_grid_aligned(t, interval, exchange=exchange):
        # 격자에 안 맞으면 간격이나 거래소를 잘못 안 것이다 — 그대로 쓰면 지표가 어긋난다.
        raise CandleFetchError("거래소 봉 시작 시각이 봉 간격과 맞지 않아요.")
    try:
        return Candle(t, raw["opening_price"], raw["high_price"], raw["low_price"],
                      raw["trade_price"], raw.get("candle_acc_trade_volume"))
    except (KeyError, TypeError, ValueError) as exc:
        raise CandleFetchError("거래소 봉에 값이 모자라요.") from exc


def _binance_candle(row) -> Candle:
    try:
        return Candle(row[0], row[1], row[2], row[3], row[4], row[5])
    except (IndexError, TypeError, ValueError) as exc:
        raise CandleFetchError("거래소 봉에 값이 모자라요.") from exc


class CandleSource:
    """``(거래소 · 종목 · 간격 · 시장)`` 하나의 마감봉을 가져온다.

    ``session`` 은 ``requests.Session`` 호환이면 무엇이든 된다 — 시험이 가짜를 넣는다.
    """

    def __init__(self, exchange: str, symbol: str, interval: str, *,
                 market: str = "spot", session=None,
                 now_ms: Callable[[], int] = _now_ms) -> None:
        self.exchange = str(exchange or "binance").strip().lower()
        if self.exchange not in ("binance", "upbit", "bithumb"):
            raise CandleFetchError("모르는 거래소예요: " + str(exchange))
        if interval not in INTERVAL_MS:
            raise CandleFetchError("지원하지 않는 봉 간격이에요: " + str(interval))
        if self.exchange != "binance" and market != "spot":
            raise CandleFetchError("국내 거래소는 현물 봉만 있어요.")
        if self.exchange != "binance":
            _krw_path(interval)  # 지원하지 않는 분봉이면 여기서 바로 알린다
        self.symbol = str(symbol or "").upper()
        self.interval = interval
        self.market = market
        self.step_ms = INTERVAL_MS[interval]
        self._now_ms = now_ms
        if session is None:
            import requests
            session = requests.Session()
        self.session = session

    # --- 조회 -------------------------------------------------------
    def _get(self, base: str, path: str, params: dict):
        try:
            resp = self.session.get(base + path, params=params, timeout=TIMEOUT)
        except Exception as exc:  # 네트워크 · DNS · 타임아웃
            raise CandleFetchError("봉을 받지 못했어요: " + str(exc)) from exc
        status = getattr(resp, "status_code", 200)
        if isinstance(status, int) and status >= 400:
            raise CandleFetchError("거래소가 봉 요청을 거절했어요(HTTP " + str(status) + ").")
        try:
            return resp.json()
        except Exception as exc:
            raise CandleFetchError("거래소 봉 응답을 읽지 못했어요.") from exc

    def _fetch_binance(self, limit: int) -> List[Candle]:
        futures = self.market == "futures"
        base = BASES["binance_futures" if futures else "binance"]
        path = "/fapi/v1/klines" if futures else "/api/v3/klines"
        rows = self._get(base, path, {"symbol": self.symbol, "interval": self.interval,
                                      "limit": max(1, min(int(limit) + 1, 1000))})
        if not isinstance(rows, list):
            raise CandleFetchError("거래소 봉 응답 모양이 예상과 달라요.")
        return [_binance_candle(row) for row in rows]

    def _fetch_krw(self, limit: int) -> List[Candle]:
        path, base = _krw_path(self.interval), BASES[self.exchange]
        want = max(1, int(limit) + 1)
        rows = {}
        cursor = None
        for _ in range(MAX_PAGES):
            count = min(KRW_PAGE, want - len(rows))
            params = {"market": self.symbol, "count": count}
            if cursor is not None:
                params["to"] = _krw_cursor(cursor, self.exchange)
            page = self._get(base, path, params)
            if not isinstance(page, list):
                raise CandleFetchError("거래소 봉 응답 모양이 예상과 달라요.")
            if not page:
                break
            candles = [_krw_candle(raw, self.interval, self.exchange) for raw in page]
            oldest = min(c.t for c in candles)
            if cursor is not None and oldest >= cursor:
                # 커서가 안 밀리면 같은 쪽을 맴돈다 — 무한 반복 대신 가진 것으로 끝낸다.
                break
            rows.update({c.t: c for c in candles})
            if len(rows) >= want or len(page) < count:
                break
            cursor = oldest
        return sorted(rows.values(), key=lambda c: c.t)

    def recent_closed(self, limit: int) -> List[Candle]:
        """마감된 봉을 오래된 것부터 ``limit`` 개까지. 진행 중 봉은 빼고 준다."""
        raw = self._fetch_binance(limit) if self.exchange == "binance" else self._fetch_krw(limit)
        closed = [c for c in raw if self.is_closed(c.t)]
        if not closed:
            raise CandleFetchError("마감된 봉이 아직 없어요.")
        return closed[-max(1, int(limit)):]

    # --- 시각 -------------------------------------------------------
    def is_closed(self, open_ms: int) -> bool:
        """``open_ms`` 에 열린 봉이 **다 닫혔는가**. 거래소의 표시 대신 시각으로 가른다."""
        return int(open_ms) + self.step_ms <= self._now_ms()

    def next_close_ms(self, now_ms: Optional[int] = None) -> int:
        """다음 봉이 닫히는 시각 — 폴링을 봉 경계에 맞추는 데 쓴다."""
        now = self._now_ms() if now_ms is None else int(now_ms)
        offset = interval_grid_offset(self.exchange, self.interval)
        return ((now - offset) // self.step_ms + 1) * self.step_ms + offset


class ClosedCandleStream:
    """``CandleSource`` 위에서 **새로 마감된 봉만** 한 번씩 흘려보낸다.

    ``poll()`` 을 자주 불러도 된다 — 새로 마감된 게 없으면 빈 목록이다. 같은 봉을 두 번
    주지 않는 것이 이 클래스의 전부다(지표는 받은 봉을 누적하므로 중복이 결과를 바꾼다).
    """

    def __init__(self, source: CandleSource, *, since_t: Optional[int] = None) -> None:
        self.source = source
        #: 이미 소비한 마지막 봉의 시작 시각. None 이면 첫 폴에서 기준만 잡고 아무것도 주지 않는다.
        self.last_t: Optional[int] = None if since_t is None else int(since_t)

    def warmup(self, count: int) -> List[Candle]:
        """지표를 세울 과거 봉. 받은 마지막 봉까지 소비한 것으로 친다."""
        candles = self.source.recent_closed(count)
        self.last_t = candles[-1].t
        return candles

    def poll(self, lookback: int = 3) -> List[Candle]:
        """``last_t`` 뒤에 새로 마감된 봉들(오래된 것부터).

        ``lookback`` 은 한 번에 되짚는 봉 수다 — 폴을 몇 번 건너뛰어도(절전 · 네트워크
        끊김) 그 사이 봉을 주워 온다. 기준이 없으면(첫 폴) 기준만 잡고 빈 목록을 준다:
        시작하자마자 묵은 봉의 신호로 사지 않는다.
        """
        candles = self.source.recent_closed(max(1, int(lookback)))
        if self.last_t is None:
            self.last_t = candles[-1].t
            return []
        fresh = [c for c in candles if c.t > self.last_t]
        if fresh:
            self.last_t = fresh[-1].t
        return fresh
