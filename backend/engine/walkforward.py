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


def _bundle_edge(part: dict) -> tuple[str, str]:
    """묶음 창의 첫 · 마지막 시각. 고르는 저울은 UTC 나노초, 표기는 **원래 값** 그대로.

    ``pd.Timestamp`` 끼리 ``min``/``max`` 를 하면 시간대가 있는 레그와 없는 레그가 섞인
    순간 ``TypeError`` 가 난다. 이 계산은 창별 ``try`` **밖**이라 그 예외가 요청 전체를
    400 으로 만든다 — 바로 옆 ``split_frames_by_time`` 은 같은 경우를 UTC 나노초로
    막으므로 같은 헬퍼(``_as_utc_ns``)를 쓴다. 표기는 레그 자기 행의 원래 값이라
    기존 라벨이 한 글자도 바뀌지 않는다.
    """
    # 함수 안에서 불러온다 — walkforward <- portfolio_backtest <- backtest 순환을 피한다.
    from .portfolio_backtest import _utc_ns

    _NAT = -(2 ** 63)       # pandas 가 NaT 를 나타내는 정수. 끝점으로 고르면 안 된다.
    lo: tuple[int, object] | None = None
    hi: tuple[int, object] | None = None
    for df in part.values():
        col = _utc_ns(df)
        if col is None or not len(col):
            continue
        # 레그마다 **두 행**만 본다 — 봉 수 × 레그 수만큼 파이썬 루프를 돌면 큰 요청에서
        # 라벨 계산이 백테스트보다 비싸진다.
        good = (col != _NAT).nonzero()[0]
        if not len(good):
            continue
        values = df[TIME_COLUMN]
        kept = col[good]
        i = int(good[int(kept.argmin())])
        j = int(good[int(kept.argmax())])
        if lo is None or int(col[i]) < lo[0]:
            lo = (int(col[i]), values.iloc[i])
        if hi is None or int(col[j]) > hi[0]:
            hi = (int(col[j]), values.iloc[j])
    if lo is None or hi is None:
        return "", ""
    return _label(lo[1]), _label(hi[1])


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


def run_bundle_windows(macro: Macro, frames: dict, windows: int = DEFAULT_WINDOWS) -> list[dict]:
    """묶음의 구간별 성과. 창은 **시각 기준**으로 자른다.

    행 수로 자르면 레그마다 봉 수가 달라 경계가 어긋난다 — 묶음에서는 모든 레그가 같은
    기간을 보아야 한도가 뜻을 가진다. 수익률은 레그 합산(``portfolio.aggregate``)이다.

    경로(동기 루프 · 레그별 완주)는 ``run_legs`` 가 고른다. 전체기간과 같은 함수를 지나야
    같은 매크로의 두 숫자가 어긋나지 않는다.
    """
    # 함수 안에서 불러온다 — walkforward <- portfolio_backtest <- backtest 순환을 피한다.
    from . import portfolio as portfolio_mod
    from .portfolio_backtest import run_legs, split_frames_by_time

    rows = []
    for index, part in enumerate(split_frames_by_time(frames, windows), start=1):
        start, end = _bundle_edge(part)
        try:
            results = run_legs(macro, part)
            agg, _per = portfolio_mod.aggregate(results, candle_interval=macro.candle_interval)
        except Exception as exc:  # noqa: BLE001 — 한 구간 실패가 전체를 막지 않는다
            logger.warning("bundle walk-forward window failed: index=%d reason=%s",
                           index, type(exc).__name__)
            rows.append(_failed(index, start, end, type(exc).__name__))
            continue
        value = float(agg.final_return_pct)
        if not math.isfinite(value):
            rows.append(_failed(index, start, end, "NonFiniteReturn"))
            continue
        rows.append({"index": index, "start": start, "end": end,
                     "return_pct": round(value, 2),
                     "trades": int(agg.total_trades), "error": ""})
    return rows
