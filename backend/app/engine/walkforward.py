"""워크포워드 — 같은 매크로를 기간 구간별로 돌려 안정성을 본다.

캔들은 호출자가 한 번만 받아 넘긴다. 구간마다 다시 받지 않는다(네트워크 비용이
구간 수만큼 늘어나고, 구간 경계가 어긋날 수 있다). 이 모듈은 DataFrame 을 시간 순으로
자를 뿐 입출력도 시계 읽기도 하지 않으므로 ``(macro, df, windows)`` 가 같으면 결과도 같다.

한 구간이 실패해도 나머지는 그대로 돌려준다 — 상장 전 구간이 섞이는 일이 흔하다.
실패한 구간도 목록에서 자기 자리를 지킨다(과최적화 경고의 후반부 판정이 마지막 원소를 본다).
"""
from __future__ import annotations

import logging
import math

import pandas as pd

from .backtest import run_backtest
from .schema import Macro

logger = logging.getLogger(__name__)

MIN_ROWS_PER_WINDOW = 2
DEFAULT_WINDOWS = 4

# 엔진(run_backtest)이 읽는 시각 열은 이 하나다. 저장소가 돌려주는 캔들 프레임도 이 열만 낸다.
# 정수 시각을 다른 단위로 해석해 표기하면 엔진이 보는 날짜와 어긋나므로, 값은 있는 그대로만 쓴다.
TIME_COLUMN = "timestamp"


def split_frame(df, windows: int) -> list:
    """시간 순으로 균등하게 자른다. 행 수가 모자라면 구간 수를 줄인다 — 빈 구간을 만들지 않는다.

    나눠떨어지지 않는 나머지 행은 마지막 구간이 받는다. 그래서 행이 빠지거나 겹치지 않고,
    마지막 구간이 사용자가 고른 기간의 끝까지 닿는다.

    정렬은 방어 차원이다 — 지금 호출자는 정렬된 프레임을 넘기고, 엔진도 안에서 같은 정렬을
    한다(``run_backtest``). 같은 시각 행은 입력 순서를 유지하며 입력 프레임은 바꾸지 않는다.
    """
    rows = len(df)
    if rows < MIN_ROWS_PER_WINDOW:
        return []
    count = max(1, min(int(windows), rows // MIN_ROWS_PER_WINDOW))
    if TIME_COLUMN in df.columns:
        df = df.sort_values(TIME_COLUMN, kind="stable")
    if count <= 1:
        return [df]
    size = rows // count
    parts = [df.iloc[i * size:(i + 1) * size] for i in range(count - 1)]
    parts.append(df.iloc[(count - 1) * size:])
    return parts


def _label(value) -> str:
    """시각 한 개를 ISO 문자열로. 결측은 빈 문자열."""
    if pd.isna(value):
        return ""
    if hasattr(value, "isoformat"):
        return value.isoformat()
    return str(value)


def _edge(part) -> tuple[str, str]:
    """구간의 첫·마지막 시각. 비었거나 시각 열이 없으면 ("", "") — UI 가 그대로 그릴 수 있다."""
    if TIME_COLUMN not in part.columns or len(part) == 0:
        return "", ""
    return _label(part[TIME_COLUMN].iloc[0]), _label(part[TIME_COLUMN].iloc[-1])


def _failed(index: int, start: str, end: str, reason: str) -> dict:
    return {"index": index, "start": start, "end": end,
            "return_pct": None, "trades": 0, "error": reason}


def run_windows(macro: Macro, df, windows: int = DEFAULT_WINDOWS) -> list[dict]:
    """구간별 성과. 실패한 구간은 return_pct=None 과 error 로 남는다(0.0 으로 꾸미지 않는다).

    수익률이 nan · inf 로 나온 구간도 실패로 센다 — 숫자가 아닌 값은 JSON 으로 내보낼 수 없다.
    """
    rows = []
    for index, part in enumerate(split_frame(df, windows), start=1):
        start, end = _edge(part)
        try:
            result = run_backtest(macro, part)
        except Exception as exc:  # noqa: BLE001 — 한 구간 실패가 전체를 막지 않는다
            logger.warning("walk-forward window failed: index=%d reason=%s",
                           index, type(exc).__name__)
            rows.append(_failed(index, start, end, type(exc).__name__))
            continue
        value = float(result.final_return_pct)
        if not math.isfinite(value):
            logger.warning("walk-forward window non-finite: index=%d", index)
            rows.append(_failed(index, start, end, "NonFiniteReturn"))
            continue
        rows.append({"index": index, "start": start, "end": end,
                     "return_pct": round(value, 2),
                     "trades": int(result.total_trades), "error": ""})
    return rows
