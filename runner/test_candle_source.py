"""실행기가 거래소에서 직접 받는 마감봉 (2026-10-20).

서버 장애가 사용자의 매수 · 매도 시점을 좌우하지 않으려면 봉부터 실행기가 가져야 한다.
그런데 봉을 잘못 가져오면 조용히 다른 자리에서 사고판다 — 서버와 다른 경계, 진행 중인 봉,
같은 봉 두 번. 셋 다 화면에는 아무 표시가 없다. 그래서 여기서 못 박는다.
"""
import unittest
from datetime import datetime, timedelta, timezone

from candle_source import (
    BASES,
    Candle,
    CandleFetchError,
    CandleSource,
    ClosedCandleStream,
)

HOUR = 3_600_000
DAY = 24 * HOUR
KST = timezone(timedelta(hours=9))


class FakeResponse:
    def __init__(self, payload, status=200):
        self._payload = payload
        self.status_code = status

    def json(self):
        if isinstance(self._payload, Exception):
            raise self._payload
        return self._payload


class FakeSession:
    """부른 주소와 인자를 기록하고 미리 넣어 둔 응답을 돌려준다."""

    def __init__(self, *pages):
        self.pages = list(pages)
        self.calls = []

    def get(self, url, params=None, timeout=None):
        self.calls.append((url, dict(params or {})))
        if not self.pages:
            return FakeResponse([])
        page = self.pages.pop(0)
        if isinstance(page, FakeResponse):
            return page
        return FakeResponse(page)


def krw_rows(start_ms, count, interval_ms=HOUR, price=100.0):
    rows = []
    for i in range(count):
        t = start_ms + i * interval_ms
        stamp = datetime.fromtimestamp(t / 1000, timezone.utc)
        rows.append({
            "candle_date_time_utc": stamp.replace(tzinfo=None).isoformat(),
            "opening_price": price + i, "high_price": price + i + 2,
            "low_price": price + i - 1, "trade_price": price + i + 1,
            "candle_acc_trade_volume": 10.0 + i,
        })
    return list(reversed(rows))  # 거래소는 최신이 먼저다


def binance_rows(start_ms, count, interval_ms=HOUR, price=100.0):
    return [[start_ms + i * interval_ms, str(price + i), str(price + i + 2),
             str(price + i - 1), str(price + i + 1), str(10.0 + i)]
            for i in range(count)]


# --- 봉 격자 ---------------------------------------------------------------
class GridTest(unittest.TestCase):
    def test_빗썸_일봉은_KST_자정에_열린다(self):
        """빗썸만 KST 경계다. 직접 계산하면 하루가 9시간 어긋나고 아무 표시도 없다."""
        now = int(datetime(2026, 10, 20, 7, 0, tzinfo=timezone.utc).timestamp() * 1000)
        src = CandleSource("bithumb", "KRW-AZIT", "1d", now_ms=lambda: now)
        opened = src.next_close_ms() - DAY
        self.assertEqual(datetime.fromtimestamp(opened / 1000, KST).hour, 0)
        self.assertEqual(datetime.fromtimestamp(opened / 1000, timezone.utc).hour, 15)

    def test_업비트와_바이낸스_일봉은_UTC_자정에_열린다(self):
        now = int(datetime(2026, 10, 20, 7, 0, tzinfo=timezone.utc).timestamp() * 1000)
        for exchange, symbol in (("upbit", "KRW-BTC"), ("binance", "BTCUSDT")):
            src = CandleSource(exchange, symbol, "1d", now_ms=lambda: now)
            opened = src.next_close_ms() - DAY
            self.assertEqual(datetime.fromtimestamp(opened / 1000, timezone.utc).hour, 0, exchange)

    def test_빗썸_4시간봉은_03시_UTC_격자다(self):
        now = int(datetime(2026, 10, 20, 7, 30, tzinfo=timezone.utc).timestamp() * 1000)
        src = CandleSource("bithumb", "KRW-AZIT", "4h", now_ms=lambda: now)
        self.assertEqual(datetime.fromtimestamp(src.next_close_ms() / 1000, timezone.utc).hour, 11)

    def test_경계_위에_정확히_있으면_다음_경계를_준다(self):
        """같은 시각을 돌려주면 폴링 루프가 0초 대기로 돈다."""
        now = int(datetime(2026, 10, 20, 7, 0, tzinfo=timezone.utc).timestamp() * 1000)
        src = CandleSource("binance", "BTCUSDT", "1h", now_ms=lambda: now)
        self.assertEqual(src.next_close_ms(), now + HOUR)

    def test_서버와_같은_격자를_쓴다(self):
        """공유 엔진을 거치지 않고 따로 계산하면 두 쪽이 갈린다."""
        from engine.intervals import next_close_ms as shared
        now = 1_760_000_000_000
        for exchange in ("binance", "upbit", "bithumb"):
            for interval in ("5m", "1h", "4h", "1d"):
                symbol = "BTCUSDT" if exchange == "binance" else "KRW-BTC"
                src = CandleSource(exchange, symbol, interval, now_ms=lambda: now)
                self.assertEqual(src.next_close_ms(),
                                 shared(interval, now, exchange=exchange),
                                 f"{exchange} {interval}")


# --- 마감 판정 -------------------------------------------------------------
class ClosedTest(unittest.TestCase):
    def test_진행_중인_봉은_주지_않는다(self):
        """진행 중 봉으로 판단하면 같은 봉에서 샀다 팔았다 한다."""
        now = 10 * HOUR
        session = FakeSession(binance_rows(7 * HOUR, 4))  # 7,8,9,10시 — 10시는 진행 중
        src = CandleSource("binance", "BTCUSDT", "1h", session=session, now_ms=lambda: now)
        got = src.recent_closed(10)
        self.assertEqual([c.t for c in got], [7 * HOUR, 8 * HOUR, 9 * HOUR])

    def test_딱_마감된_봉은_포함한다(self):
        now = 10 * HOUR
        src = CandleSource("binance", "BTCUSDT", "1h",
                           session=FakeSession(binance_rows(9 * HOUR, 1)), now_ms=lambda: now)
        self.assertEqual([c.t for c in src.recent_closed(5)], [9 * HOUR])

    def test_마감봉이_하나도_없으면_알린다(self):
        src = CandleSource("binance", "BTCUSDT", "1h",
                           session=FakeSession(binance_rows(10 * HOUR, 1)),
                           now_ms=lambda: 10 * HOUR + 60_000)
        with self.assertRaises(CandleFetchError):
            src.recent_closed(5)

    def test_요청한_개수만큼만_준다(self):
        now = 100 * HOUR
        src = CandleSource("binance", "BTCUSDT", "1h",
                           session=FakeSession(binance_rows(50 * HOUR, 40)), now_ms=lambda: now)
        got = src.recent_closed(5)
        self.assertEqual(len(got), 5)
        self.assertEqual([c.t for c in got], [(50 + i) * HOUR for i in range(35, 40)])


# --- 국내 거래소 -----------------------------------------------------------
class DomesticTest(unittest.TestCase):
    def test_업비트_응답을_엔진_모양으로_읽는다(self):
        now = 10 * HOUR
        session = FakeSession(krw_rows(7 * HOUR, 3))
        src = CandleSource("upbit", "KRW-BTC", "1h", session=session, now_ms=lambda: now)
        got = src.recent_closed(10)
        self.assertEqual([c.t for c in got], [7 * HOUR, 8 * HOUR, 9 * HOUR])
        first = got[0]
        self.assertEqual((first.o, first.h, first.l, first.c, first.v), (100.0, 102.0, 99.0, 101.0, 10.0))
        url, params = session.calls[0]
        self.assertEqual(url, BASES["upbit"] + "/v1/candles/minutes/60")
        self.assertEqual(params["market"], "KRW-BTC")

    def test_일봉은_days_주소를_쓴다(self):
        now = 10 * DAY
        session = FakeSession(krw_rows(7 * DAY, 3, DAY))
        CandleSource("upbit", "KRW-BTC", "1d", session=session, now_ms=lambda: now).recent_closed(3)
        self.assertEqual(session.calls[0][0], BASES["upbit"] + "/v1/candles/days")

    def test_200개를_넘으면_커서로_거슬러_올라간다(self):
        now = 400 * HOUR
        session = FakeSession(krw_rows(150 * HOUR, 200), krw_rows(100 * HOUR, 50))
        src = CandleSource("upbit", "KRW-BTC", "1h", session=session, now_ms=lambda: now)
        got = src.recent_closed(240)
        self.assertEqual(len(got), 240)
        self.assertEqual(len(session.calls), 2)
        self.assertIn("to", session.calls[1][1], "두 번째 쪽은 커서를 보내야 한다")

    def test_커서가_안_밀리면_멈춘다(self):
        """같은 쪽을 계속 받으면 무한 반복이 된다 — 가진 것으로 끝낸다."""
        now = 400 * HOUR
        page = krw_rows(150 * HOUR, 200)
        session = FakeSession(page, page, page, page)
        src = CandleSource("upbit", "KRW-BTC", "1h", session=session, now_ms=lambda: now)
        got = src.recent_closed(240)
        self.assertEqual(len(got), 200)
        self.assertLessEqual(len(session.calls), 3)

    def test_빗썸은_KST_커서를_오프셋_없이_보낸다(self):
        """빗썸 문서가 그렇다 — 오프셋을 붙이면 9시간 어긋난 쪽을 받는다."""
        now = 400 * HOUR
        session = FakeSession(krw_rows(150 * HOUR, 200), krw_rows(100 * HOUR, 50))
        CandleSource("bithumb", "KRW-AZIT", "1h", session=session, now_ms=lambda: now).recent_closed(240)
        cursor = session.calls[1][1]["to"]
        self.assertNotIn("+", cursor)
        self.assertFalse(cursor.endswith("Z"))

    def test_격자에_안_맞는_봉은_거부한다(self):
        now = 10 * HOUR
        rows = krw_rows(7 * HOUR, 2)
        rows[0]["candle_date_time_utc"] = "2026-10-20T07:17:00"  # 정시가 아니다
        src = CandleSource("upbit", "KRW-BTC", "1h", session=FakeSession(rows), now_ms=lambda: now)
        with self.assertRaises(CandleFetchError):
            src.recent_closed(5)

    def test_국내는_선물_봉이_없다(self):
        with self.assertRaises(CandleFetchError):
            CandleSource("upbit", "KRW-BTC", "1h", market="futures")

    def test_매크로가_쓰는_모든_간격이_국내에서_된다(self):
        """지금 표의 간격은 전부 국내에 있다. 새 간격을 더하면 여기서 먼저 걸린다."""
        from candle_source import _krw_path
        from engine.intervals import INTERVAL_MS
        for interval in INTERVAL_MS:
            self.assertTrue(_krw_path(interval).startswith("/v1/candles/"), interval)
        with self.assertRaises(CandleFetchError):
            _krw_path("2h")  # 표에 없는 간격


# --- 오류 -------------------------------------------------------------------
class FailureTest(unittest.TestCase):
    def test_네트워크_실패는_우리_오류로_감싼다(self):
        class Boom:
            def get(self, *a, **k):
                raise OSError("연결 끊김")

        src = CandleSource("binance", "BTCUSDT", "1h", session=Boom())
        with self.assertRaises(CandleFetchError):
            src.recent_closed(5)

    def test_HTTP_오류를_알린다(self):
        src = CandleSource("binance", "BTCUSDT", "1h",
                           session=FakeSession(FakeResponse([], status=429)))
        with self.assertRaises(CandleFetchError) as caught:
            src.recent_closed(5)
        self.assertIn("429", str(caught.exception))

    def test_모르는_거래소는_만들_때_막는다(self):
        with self.assertRaises(CandleFetchError):
            CandleSource("coinbase", "BTC-USD", "1h")

    def test_모르는_간격은_만들_때_막는다(self):
        with self.assertRaises(CandleFetchError):
            CandleSource("binance", "BTCUSDT", "2h")


# --- 중복 없이 흘려보내기 ---------------------------------------------------
class StreamTest(unittest.TestCase):
    def _stream(self, now_box, pages):
        session = FakeSession(*pages)
        src = CandleSource("binance", "BTCUSDT", "1h", session=session,
                           now_ms=lambda: now_box[0])
        return ClosedCandleStream(src), session

    def test_첫_폴은_기준만_잡고_아무것도_주지_않는다(self):
        """시작하자마자 묵은 봉의 신호로 사지 않는다."""
        now = [10 * HOUR]
        stream, _ = self._stream(now, [binance_rows(7 * HOUR, 4)])
        self.assertEqual(stream.poll(), [])
        self.assertEqual(stream.last_t, 9 * HOUR)

    def test_새로_마감된_봉만_한_번씩_준다(self):
        now = [10 * HOUR]
        stream, _ = self._stream(now, [binance_rows(7 * HOUR, 4), binance_rows(8 * HOUR, 4)])
        stream.poll()                       # 기준 = 9시
        now[0] = 11 * HOUR
        self.assertEqual([c.t for c in stream.poll()], [10 * HOUR])

    def test_같은_봉을_두_번_주지_않는다(self):
        """지표는 받은 봉을 누적한다 — 중복은 조용히 결과를 바꾼다."""
        now = [10 * HOUR]
        rows = binance_rows(7 * HOUR, 3)
        stream, _ = self._stream(now, [rows, rows, rows])
        stream.poll()
        self.assertEqual(stream.poll(), [])
        self.assertEqual(stream.poll(), [])

    def test_폴을_건너뛰어도_그_사이_봉을_주워_온다(self):
        """절전 · 네트워크 끊김으로 몇 봉을 놓쳐도 순서대로 돌려준다."""
        now = [10 * HOUR]
        stream, _ = self._stream(now, [binance_rows(7 * HOUR, 4), binance_rows(7 * HOUR, 7)])
        stream.poll()                       # 기준 = 9시
        now[0] = 13 * HOUR
        self.assertEqual([c.t for c in stream.poll(lookback=5)], [10 * HOUR, 11 * HOUR, 12 * HOUR])

    def test_웜업은_과거_봉을_주고_기준을_마지막_봉으로_잡는다(self):
        now = [10 * HOUR]
        stream, _ = self._stream(now, [binance_rows(0, 11)])
        warm = stream.warmup(8)
        self.assertEqual(len(warm), 8)
        self.assertEqual(stream.last_t, warm[-1].t)
        self.assertEqual(warm[-1].t, 9 * HOUR)

    def test_웜업_뒤_첫_폴은_새_봉부터_준다(self):
        """웜업에 들어간 봉을 다시 주면 지표가 이중으로 누적된다."""
        now = [10 * HOUR]
        stream, _ = self._stream(now, [binance_rows(0, 11), binance_rows(5 * HOUR, 7)])
        stream.warmup(8)                    # ~9시까지 소비
        now[0] = 11 * HOUR
        self.assertEqual([c.t for c in stream.poll(lookback=5)], [10 * HOUR])

    def test_이어받은_기준부터_준다(self):
        """재기동 — 앞 프로세스가 어디까지 봤는지 알고 시작한다."""
        now = [12 * HOUR]
        session = FakeSession(binance_rows(7 * HOUR, 5))
        src = CandleSource("binance", "BTCUSDT", "1h", session=session, now_ms=lambda: now[0])
        stream = ClosedCandleStream(src, since_t=9 * HOUR)
        self.assertEqual([c.t for c in stream.poll(lookback=5)], [10 * HOUR, 11 * HOUR])


if __name__ == "__main__":
    unittest.main()
