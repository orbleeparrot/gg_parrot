"""백테스트 결과를 다시 읽어 내는 검증 지표 — 엔진을 부르지 않는 순수 함수.

``equity_curve`` 하나로 월별 수익 · 집중도 · 낙폭 구간 · 소르티노 · 칼마를 낸다.
거래가 없어 곡선이 비거나 짧으면 지표 대신 None 을 돌려준다(0 으로 나누지 않는다).
"""
from __future__ import annotations

import math
from datetime import datetime

_DAY_SECONDS = 86_400.0


def _points(curve) -> list[tuple[datetime, float]]:
    out = []
    for point in curve or []:
        try:
            stamp = datetime.fromisoformat(str(point.t).replace("Z", "+00:00"))
        except (TypeError, ValueError):
            continue
        out.append((stamp, float(point.equity)))
    return out


def monthly_returns(curve) -> list[dict]:
    """달력 월별 수익률(%). 각 달의 마지막 값끼리 비교한다."""
    points = _points(curve)
    if len(points) < 2:
        return []
    last_of_month: dict[str, float] = {}
    for stamp, equity in points:
        last_of_month[stamp.strftime("%Y-%m")] = equity
    months = sorted(last_of_month)
    base = points[0][1]
    rows = []
    for month in months:
        end = last_of_month[month]
        rows.append({"month": month,
                     "pct": round((end / base - 1.0) * 100.0, 2) if base > 0 else 0.0})
        base = end
    return rows


def concentration(curve) -> dict:
    """가장 많이 번 달이 전체 '번 돈' 에서 차지하는 몫.

    달마다 자산이 늘어난 금액(원 · 달러)을 구해, 가장 큰 달의 증가분을 증가한
    달들의 증가분 합으로 나눈다. 달별 수익률(%)끼리 나누면 자산이 커진 뒤의 달이
    작게 잡혀 몫이 왜곡되므로 퍼센트가 아니라 금액으로 센다.
    """
    points = _points(curve)
    if len(points) < 2:
        return {"top_month_share_pct": None, "months": 0}
    last_of_month: dict[str, float] = {}
    for stamp, equity in points:
        last_of_month[stamp.strftime("%Y-%m")] = equity
    months = sorted(last_of_month)
    base = points[0][1]
    gains = []
    for month in months:
        end = last_of_month[month]
        if end > base:
            gains.append(end - base)
        base = end
    if not gains:
        return {"top_month_share_pct": None, "months": len(months)}
    return {"top_month_share_pct": round(max(gains) / sum(gains) * 100.0, 2),
            "months": len(months)}


def drawdown_window(curve) -> dict | None:
    """가장 깊었던 낙폭의 시작 · 바닥 · 회복 시점."""
    points = _points(curve)
    if len(points) < 2:
        return None
    peak_stamp, peak = points[0]
    best = None
    for stamp, equity in points:
        if equity > peak:
            peak_stamp, peak = stamp, equity
            continue
        depth = (peak - equity) / peak * 100.0 if peak > 0 else 0.0
        if best is None or depth > best["depth_pct"]:
            best = {"start": peak_stamp, "trough": stamp, "depth_pct": round(depth, 2),
                    "peak": peak}
    if best is None or best["depth_pct"] <= 0:
        return None
    recovered = None
    for stamp, equity in points:
        if stamp > best["trough"] and equity >= best["peak"]:
            recovered = stamp
            break
    days = None if recovered is None else round(
        (recovered - best["trough"]).total_seconds() / _DAY_SECONDS)
    return {"start": best["start"].isoformat(), "trough": best["trough"].isoformat(),
            "recovered": None if recovered is None else recovered.isoformat(),
            "depth_pct": best["depth_pct"], "recovery_days": days}


def _returns(points) -> list[float]:
    out = []
    for (_, before), (_, after) in zip(points, points[1:]):
        if before > 0:
            out.append(after / before - 1.0)
    return out


def sortino(curve) -> float | None:
    """하방 변동만으로 나눈 위험조정 수익 — 샤프보다 손실에 민감하다."""
    points = _points(curve)
    rets = _returns(points)
    if len(rets) < 2:
        return None
    downside = [r for r in rets if r < 0]
    if not downside:
        return None
    deviation = math.sqrt(sum(r * r for r in downside) / len(downside))
    if deviation <= 0:
        return None
    return round((sum(rets) / len(rets)) / deviation * math.sqrt(len(rets)), 2)


# 연환산은 최소 이만큼의 기간이 있어야 의미가 있다. 몇 시간짜리 구간을 1년으로
# 늘리면 1.2 배 수익이 1.2 의 8760 제곱이 되어 값이 터지거나 무의미해진다.
_MIN_CALMAR_DAYS = 30.0


def calmar(curve, mdd_pct: float) -> float | None:
    """연수익 / 최대낙폭. 전문가가 먼저 보는 값이다.

    어떤 곡선이 와도 예외를 내지 않는다. 낙폭이 없거나, 끝 자산이 0 이하이거나,
    기간이 너무 짧거나, 결과가 유한한 수가 아니면 None 이다.
    """
    points = _points(curve)
    if len(points) < 2 or mdd_pct is None:
        return None
    try:
        mdd = float(mdd_pct)
    except (TypeError, ValueError):
        return None
    if not math.isfinite(mdd) or mdd <= 0:
        return None
    start, end = points[0][1], points[-1][1]
    if start <= 0 or end <= 0:
        return None
    days = (points[-1][0] - points[0][0]).total_seconds() / _DAY_SECONDS
    if days < _MIN_CALMAR_DAYS:
        return None
    try:
        annual = ((end / start) ** (365.0 / days) - 1.0) * 100.0
        result = annual / mdd
    except (OverflowError, ZeroDivisionError):
        return None
    if not math.isfinite(result):
        return None
    return round(result, 2)
