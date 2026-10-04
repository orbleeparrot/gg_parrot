"""워크포워드 — 같은 매크로를 기간 구간별로 돌려 안정성을 본다.

캔들은 호출자가 한 번만 받아 넘긴다. 구간마다 다시 받지 않는다(네트워크 비용이
구간 수만큼 늘어나고, 구간 경계가 어긋날 수 있다). 이 모듈은 DataFrame 을 시간 순으로
자를 뿐 입출력도 시계 읽기도 하지 않으므로 ``(macro, df, windows)`` 가 같으면 결과도 같다.

한 구간이 실패해도 나머지는 그대로 돌려준다 — 상장 전 구간이 섞이는 일이 흔하다.
실패한 구간도 목록에서 자기 자리를 지킨다(과최적화 경고의 후반부 판정이 마지막 원소를 본다).
"""
from __future__ import annotations

import logging
import numbers

import pandas as pd

from .backtest import run_backtest
from .schema import Macro

logger = logging.getLogger(__name__)

MIN_ROWS_PER_WINDOW = 2
DEFAULT_WINDOWS = 4

# 엔진이 읽는 열은 timestamp 이고, 저장소에서 막 읽은 프레임에는 open_time(밀리초)이 있다.
_TIME_COLUMNS = ("timestamp", "open_time")


def _time_column(df) -> str | None:
    for column in _TIME_COLUMNS:
        if column in getattr(df, "columns", ()):
            return column
    return None


def split_frame(df, windows: int) -> list:
    """시간 순으로 균등하게 자른다. 행 수가 모자라면 구간 수를 줄인다 — 빈 구간을 만들지 않는다.

    나눠떨어지지 않는 나머지 행은 마지막 구간이 받는다. 그래서 행이 빠지거나 겹치지 않고,
    마지막 구간이 사용자가 고른 기간의 끝까지 닿는다.
    """
    rows = len(df)
    count = max(1, min(int(windows), rows // MIN_ROWS_PER_WINDOW))
    if rows < MIN_ROWS_PER_WINDOW:
        return []
    column = _time_column(df)
    if column is not None:
        # 순서가 뒤섞여 들어와도 구간이 시간 순이 되도록 정렬한다(같은 시각은 입력 순서 유지).
        df = df.sort_values(column, kind="stable")
    if count <= 1:
        return [df]
    size = rows // count
    parts = [df.iloc[i * size:(i + 1) * size] for i in range(count - 1)]
    parts.append(df.iloc[(count - 1) * size:])
    return parts


def _label(value) -> str:
    """시각 한 개를 ISO 문자열로 — 밀리초 정수와 datetime 을 모두 받는다."""
    if pd.isna(value):
        return ""
    if isinstance(value, numbers.Real) and not isinstance(value, bool):
        value = pd.to_datetime(int(value), unit="ms", utc=True)
    if hasattr(value, "isoformat"):
        return value.isoformat()
    return str(value)


def _edge(part) -> tuple[str, str]:
    """구간의 첫·마지막 시각. 비었거나 시각 열이 없으면 ("", "") — UI 가 그대로 그릴 수 있다."""
    column = _time_column(part)
    if column is None or len(part) == 0:
        return "", ""
    try:
        return _label(part[column].iloc[0]), _label(part[column].iloc[-1])
    except (KeyError, IndexError, ValueError, TypeError, OverflowError):
        return "", ""


def run_windows(macro: Macro, df, windows: int = DEFAULT_WINDOWS) -> list[dict]:
    """구간별 성과. 실패한 구간은 return_pct=None 과 error 로 남는다(0.0 으로 꾸미지 않는다)."""
    rows = []
    for index, part in enumerate(split_frame(df, windows), start=1):
        start, end = _edge(part)
        try:
            result = run_backtest(macro, part)
        except Exception as exc:  # noqa: BLE001 — 한 구간 실패가 전체를 막지 않는다
            logger.warning("walk-forward window failed: index=%d reason=%s",
                           index, type(exc).__name__)
            rows.append({"index": index, "start": start, "end": end,
                         "return_pct": None, "trades": 0, "error": type(exc).__name__})
            continue
        rows.append({"index": index, "start": start, "end": end,
                     "return_pct": round(float(result.final_return_pct), 2),
                     "trades": int(result.total_trades), "error": ""})
    return rows
