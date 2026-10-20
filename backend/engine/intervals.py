"""봉 격자 — 한 봉이 **언제 열리고 언제 닫히는가**. 서버와 실행기가 같은 답을 내야 한다.

판단이 실행기로 내려가면 양쪽이 각자 봉 경계를 계산한다. 경계가 한 칸만 어긋나도 같은
매크로가 다른 봉을 보고 다른 자리에서 사고판다. 그래서 이 산수는 한 곳에만 둔다.

까다로운 데가 하나 있다 — **빗썸의 봉은 KST 경계에서 시작한다.** 업비트와 바이낸스는
UTC 경계다. 그래서 빗썸 4시간봉은 03:00 UTC 에, 일봉은 15:00 UTC 에 열린다. 이걸 모르면
빗썸 일봉의 하루가 9시간 어긋난다.
"""
from __future__ import annotations

_MS_MINUTE = 60_000
_MS_HOUR = 60 * _MS_MINUTE
_MS_DAY = 24 * _MS_HOUR

#: 간격 이름 → 길이(ms). 매크로의 ``candle_interval`` 이 쓰는 값이 전부다.
INTERVAL_MS = {
    "1m": _MS_MINUTE,
    "3m": 3 * _MS_MINUTE,
    "5m": 5 * _MS_MINUTE,
    "15m": 15 * _MS_MINUTE,
    "1h": _MS_HOUR,
    "4h": 4 * _MS_HOUR,
    "1d": _MS_DAY,
}

#: 빗썸 봉이 열리는 기준 시간대(KST = UTC+9)와 UTC 의 차이.
_KST_SHIFT_MS = 9 * _MS_HOUR


def step_ms(interval: str) -> int:
    """``interval`` 한 봉의 길이(ms). 모르는 간격이면 KeyError."""
    return INTERVAL_MS[interval]


def interval_grid_offset(exchange: str, interval: str) -> int:
    """봉 격자가 UTC 0시에서 얼마나 밀려 있는가(ms).

    빗썸만 KST 경계이므로 0 이 아니다 — 4시간봉 03:00 UTC, 일봉 15:00 UTC.
    1시간 이하는 9시간이 간격의 배수라 결과가 0 이지만, 식은 그대로 둔다
    (간격이 늘어나도 저절로 맞는다).
    """
    step = INTERVAL_MS[interval]
    return (-_KST_SHIFT_MS) % step if str(exchange or "").lower() == "bithumb" else 0


def bar_open_ms(interval: str, now_ms: int, *, exchange: str = "binance") -> int:
    """``now_ms`` 가 속한 봉이 열린 시각."""
    step = INTERVAL_MS[interval]
    offset = interval_grid_offset(exchange, interval)
    return (int(now_ms) - offset) // step * step + offset


def next_close_ms(interval: str, now_ms: int, *, exchange: str = "binance") -> int:
    """``now_ms`` 다음에 봉이 닫히는 시각. 경계 위에 정확히 있으면 **다음** 경계를 준다."""
    step = INTERVAL_MS[interval]
    offset = interval_grid_offset(exchange, interval)
    return ((int(now_ms) - offset) // step + 1) * step + offset


def is_grid_aligned(t_ms: int, interval: str, *, exchange: str = "binance") -> bool:
    """거래소가 준 봉 시작 시각이 이 격자에 맞는가. 어긋나면 간격이나 거래소를 잘못 안 것이다."""
    step = INTERVAL_MS[interval]
    return (int(t_ms) - interval_grid_offset(exchange, interval)) % step == 0
